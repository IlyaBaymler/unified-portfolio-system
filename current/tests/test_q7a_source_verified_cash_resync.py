"""STEP32: authentic selected-store chain; no real provider or trading authority."""
from __future__ import annotations

import importlib
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_financial_readers import read_case
from test_q7a_source_versioned_operational_store import op_case, _operation
from test_q7a_source_versioned_cutover import cut_case
from test_q7a_source_selected_sync import selected_case, _inputs, _run, _view
from test_q7a_source_selected_owner_refresh_rebuilt import owner_case, refresh
from test_q7a_source_settlement_closure import _snapshot, _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State


def module():
    return importlib.import_module('trading_robot.versioned_risk_resync')


def prepare(c):
    return module().prepare_cash_flow_resync(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_authority_sha256=c.a.cash_authority_manager.status().sha256)


def confirm(c,p,**kwargs):
    return module().confirm_cash_flow_resync(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_plan_sha256=p.plan_sha256,
        confirmation=p.confirmation,**kwargs)


def recover(c,p,**kwargs):
    return module().recover_cash_flow_resync(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_plan_sha256=p.plan_sha256,**kwargs)


def test_core_verified_deposit_retains_fee_loss(owner_case,request):
    c=owner_case;refresh(c)
    risk0=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert risk0.risk_resync_required
    before=_snapshot(c.x.c.y.x);old_a=c.a.cash_authority_manager.status()
    with _view(c) as (_,v):cash=v.export_bytes()
    proof=prepare(c)
    assert _snapshot(c.x.c.y.x)==before
    result=confirm(c,proof)
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert not risk.risk_resync_required
    assert risk.daily_start_equity_rub==risk.weekly_start_equity_rub==1_000_100
    assert risk.high_watermark_equity_rub==1_000_100
    assert risk.last_equity_rub-risk.daily_start_equity_rub==pytest.approx(-.2,abs=1e-8)
    assert risk.recorded_execution_ids==risk0.recorded_execution_ids
    assert risk.daily_order_count==risk0.daily_order_count==1
    assert risk.daily_turnover_rub==risk0.daily_turnover_rub
    after=_snapshot(c.x.c.y.x)
    for name in ('portfolio','central'):assert after[0][name]==before[0][name]
    assert after[1:]==before[1:]
    assert c.a.cash_authority_manager.status().ledger_revision==old_a.ledger_revision
    assert c.a.cash_authority_manager.status().post_attempt_count==1
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    with _view(c) as (_,v):assert v.export_bytes()==cash
    assert recover(c,proof).replay and _snapshot(c.x.c.y.x)==after
    assert not result.public_summary()['order_admission_performed']
    assert c.x.c.y.x.p.order_calls==1
    request.node.user_properties.extend([('baseline_after_rub',risk.daily_start_equity_rub),
        ('daily_pnl_rub',risk.last_equity_rub-risk.daily_start_equity_rub),('new_money_transactions',0),
        ('new_risk_executions',0),('synthetic_post_total',1)])


@pytest.mark.parametrize('point',['resync.after_hold','resync.after_risk','resync.after_result',
                                 'resync.after_audit','resync.after_authority'])
def test_recovery_after_confirmed_cut(owner_case,monkeypatch,point):
    c=owner_case;refresh(c);proof=prepare(c)
    def fail(at):
        if at==point:raise RuntimeError('injected')
    with pytest.raises(RuntimeError,match='injected'):confirm(c,proof,fault_injector=fail)
    for name in ('get_positions','get_portfolio','get_orders','get_order_state','get_max_lots','get_operations_by_cursor_once'):
        monkeypatch.setattr(c.x.c.y.x.p,name,lambda *a,**k:pytest.fail('provider reached in recovery'))
    recover(c,proof)
    state=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert not state.risk_resync_required and state.daily_start_equity_rub==1000100
    saved=_snapshot(c.x.c.y.x)
    assert recover(c,proof).replay and _snapshot(c.x.c.y.x)==saved
    assert c.x.c.y.x.p.order_calls==1


def test_plan_alone_does_not_authorize_resync(owner_case):
    c=owner_case;refresh(c);proof=prepare(c);before=_snapshot(c.x.c.y.x)
    with pytest.raises(module().VerifiedCashFlowError,match='EXPLICIT_CONFIRMATION_REQUIRED'):
        recover(c,proof)
    with pytest.raises(module().VerifiedCashFlowError,match='CONFIRMATION_REQUIRED'):
        confirm(c,replace(proof,confirmation='yes'))
    assert _snapshot(c.x.c.y.x)==before


def test_stale_confirmation_writes_nothing(owner_case):
    c=owner_case;refresh(c);proof=prepare(c);before=_snapshot(c.x.c.y.x)
    c.x.c.y.x.p.clock_at+=timedelta(seconds=6)
    with pytest.raises(module().VerifiedCashFlowError,match='STALE'):confirm(c,proof)
    assert _snapshot(c.x.c.y.x)==before


def test_real_locks_and_changed_risk_recovery_fail_closed(owner_case):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock,LockUnavailableError
    c=owner_case;refresh(c);proof=prepare(c)
    def fail(at):
        if at=='resync.after_risk':
            for path in (c.r.manager.repository.lock_path,c.r.risk.state_store.lock_path,c.a.manager.store.lock_path):
                with pytest.raises(LockUnavailableError):
                    with InterProcessFileLock(path,timeout_seconds=0):pass
            db=sqlite3.connect(c.root/'ledger/store.sqlite3',timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError):db.execute('BEGIN IMMEDIATE')
            finally:db.close()
            raise RuntimeError('cut')
    with pytest.raises(RuntimeError):confirm(c,proof,fault_injector=fail)
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    c.r.risk.state_store.save_account(c.a.policy.account_id,replace(risk,daily_order_count=2))
    with pytest.raises(module().VerifiedCashFlowError,match='PREFIX_CONFLICT'):recover(c,proof)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING


def test_resync_recompose_and_next_cash_refresh(owner_case,desktop_case,monkeypatch):
    c=owner_case;refresh(c);proof=prepare(c)
    def fail(at):
        if at=='resync.after_risk':raise RuntimeError('cut')
    with pytest.raises(RuntimeError):confirm(c,proof,fault_injector=fail)
    _recompose(c.x.c.y.x,desktop_case)
    c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
    recover(c,proof)
    p=c.x.c.y.x.p;p.clock_at+=timedelta(seconds=1)
    rows=c.rows+[_operation(c.x,name='next-withdrawal',kind='OUTPUT',amount='-25',at=stamp(p.clock_at-timedelta(seconds=.5)))]
    _inputs(c,monkeypatch,rows=rows,amount='75');c.cash-=25*10**9
    _run(c);p.clock_at+=timedelta(seconds=1);refresh(c)
    proof2=prepare(c);confirm(c,proof2)
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert not risk.risk_resync_required and risk.daily_start_equity_rub==1000075
    assert risk.high_watermark_equity_rub==1000075
    assert risk.last_equity_rub-risk.daily_start_equity_rub==pytest.approx(-.2,abs=1e-8)
    assert p.order_calls==1


@pytest.mark.parametrize('damage',['amount','after_risk','missing_transaction'])
def test_correctly_signed_but_economically_false_plan_is_rejected(owner_case,damage):
    c=owner_case;refresh(c);proof=prepare(c);before=_snapshot(c.x.c.y.x);m=module()
    path=c.root/'cash_flow_resync/plans'/(proof.plan_sha256+'.json')
    body,digest=m._read(path,c.a.cl7_identity_key,m.PLAN_FIELDS)
    if damage=='amount':body['explanation']['external_flow_nano']='200000000000'
    elif damage=='after_risk':body['after_risk']['daily_start_equity_rub']=body['after_risk']['last_equity_rub']
    else:body['explanation']['transaction_sha256s']=[]
    digest=m._sha(m._sealed(body,c.a.cl7_identity_key))
    m._write(path.with_name(digest+'.json'),body,c.a.cl7_identity_key,m.PLAN_FIELDS)
    false_proof=replace(proof,plan_sha256=digest,confirmation='ACKNOWLEDGE VERIFIED CASH FLOW '+digest)
    with pytest.raises(m.VerifiedCashFlowError,match='SEMANTICS_INVALID'):confirm(c,false_proof)
    assert _snapshot(c.x.c.y.x)==before


def test_gui_blocked_and_deadline_covers_derived_proof(owner_case,monkeypatch):
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    c=owner_case;refresh(c);proof=prepare(c);before=_snapshot(c.x.c.y.x);m=module();old=m._plan
    def slow(*args,**kwargs):
        value=old(*args,**kwargs);c.x.c.y.x.p.clock_at+=timedelta(seconds=6);return value
    monkeypatch.setattr(m,'_plan',slow)
    with pytest.raises(m.VerifiedCashFlowError):confirm(c,proof)
    assert _snapshot(c.x.c.y.x)==before
    monkeypatch.setattr(m,'_plan',old)
    x=c.x.c.y.x;x.c.set_connected(True);x.c.set_market_state('OPEN')
    calls=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    with pytest.raises(GuiRuntimeBlockedError,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):x.c.run_cycle()
    assert calls==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
