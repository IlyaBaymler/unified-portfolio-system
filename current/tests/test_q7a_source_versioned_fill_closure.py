"""STEP36: cash-backed full-FILL owners; real factory, synthetic provider only."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import importlib
import json

import pytest

from test_q7a_source_versioned_fill_cash import (
    base_desktop_case, desktop_case, exact_case, refresh_case, read_case, op_case,
    cut_case, selected_case, owner_case, admission_case, admit, refresh, _snapshot,
    money, setup_fill, record, state,
)
from test_q7a_source_settlement_closure import _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State


def module():
    return importlib.import_module('trading_robot.versioned_fill_closure')


def setup_closure(c, monkeypatch, *, direction='BUY', fee='0.123456789', ack='SUBMITTED'):
    setup_fill(c, monkeypatch, direction=direction, fee=fee, ack=ack)
    c.cash_result = record(c)
    c.intent = c.a.manager.state().blocking_intent
    c.cash = state(c)[0].cash_nano
    # Broker observations change, never canonical Portfolio or old owner files.
    old_portfolio = c.provider.get_portfolio
    c.portfolio_after = deepcopy(old_portfolio(c.a.policy.account_id))
    uid = c.intent.candidate.instrument_id
    rows = [v for v in c.portfolio_after['positions'] if v['instrumentUid'] != uid]
    if c.intent.candidate.target_lots:
        rows.append({'instrumentUid': uid, 'figi': 'figi-'+c.intent.candidate.ticker.lower(),
            'ticker': c.intent.candidate.ticker, 'classCode': 'TQBR', 'instrumentType': 'share',
            'quantity': money(c.intent.candidate.target_lots * c.intent.candidate.lot_size),
            'quantityLots': money(c.intent.candidate.target_lots),
            'averagePositionPrice': money('105.123456789'), 'currentPrice': money('105.123456789')})
    c.portfolio_after['positions'] = rows
    shares = sum((Decimal(v['quantity']['units'])+Decimal(v['quantity']['nano'])/10**9)*
                 (Decimal(v['currentPrice']['units'])+Decimal(v['currentPrice']['nano'])/10**9) for v in rows)
    c.portfolio_after['totalAmountCurrencies'] = money(Decimal(c.cash)/10**9)
    c.portfolio_after['totalAmountShares'] = money(shares)
    c.portfolio_after['totalAmountPortfolio'] = money(Decimal(c.cash)/10**9 + shares)
    def portfolio(account):
        assert account == c.a.policy.account_id
        return deepcopy(c.portfolio_after)
    monkeypatch.setattr(c.provider, 'get_portfolio', portfolio)
    c.provider.clock_at += timedelta(seconds=1)
    return c


def close(c, **kwargs):
    return module().close_selected_full_fill(c.a, recovery=c.r, target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_dispatch_plan_sha256=c.sent.plan_sha256,
        expected_cash_plan_sha256=c.cash_result.cash_plan_sha256, **kwargs)


def plan(c):
    path = c.root/'versioned_fill_closure'/c.sent.plan_sha256/'plan.json'
    raw = path.read_bytes()
    from trading_robot.versioned_fee_evidence import _canonical, _sha
    return _sha(_canonical(json.loads(raw)))


def recover(c, **kwargs):
    return module().recover_selected_full_fill(c.a, recovery=c.r, target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_dispatch_plan_sha256=c.sent.plan_sha256,
        expected_closure_plan_sha256=plan(c), **kwargs)


@pytest.mark.parametrize('direction,fee', [('BUY','0.123456789'), ('SELL','0')])
def test_core_cash_backed_owners_close_once_disarmed(admission_case, monkeypatch, request, direction, fee):
    c = setup_closure(admission_case, monkeypatch, direction=direction, fee=fee)
    before = _snapshot(c.x.c.y.x)
    source = state(c)[1]
    result = close(c)
    final = _snapshot(c.x.c.y.x)
    p = c.r.manager.repository.load()
    pos = p.position(c.intent.candidate.instrument_id)
    assert (pos.actual_lots if pos else 0) == c.intent.candidate.target_lots
    if direction == 'BUY':
        assert pos.target_lots == 1 and pos.ownership.strategy_id == c.intent.candidate.strategy_id
    else:
        assert pos is None or (pos.target_lots in (None,0) and pos.ownership is None)
    risk = c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert risk.recorded_execution_ids == (*before[0]['risk']['recorded_execution_ids'], c.intent.intent_id)
    assert risk.daily_order_count == before[0]['risk']['daily_order_count'] + 1
    central = c.a.manager.state()
    intent = next(i for i in central.intents if i.intent_id == c.intent.intent_id)
    assert intent.status == 'RECONCILED' and intent.outcome == 'FILLED' and intent.executed_lots == 1
    assert intent.risk_execution_id == c.intent.intent_id and intent.risk_execution_status == 'RECORDED'
    assert intent.candidate == c.intent.candidate and intent.reserved_cash_kopecks == c.intent.reserved_cash_kopecks
    assert central.blocking_intent is None and not central.queued
    authority = c.a.cash_authority_manager.status()
    assert authority.state is State.EXACT_CASH_VERSIONED_DISARMED
    assert authority.post_attempt_count == 2 and authority.pending_dispatch_proof_sha256 is None
    assert authority.ledger_head_sha256 == state(c)[0].pins.ledger_head_sha256
    assert authority.ledger_revision == state(c)[0].pins.ledger_revision
    assert state(c)[1] == source and final[1:] == before[1:]
    assert c.provider.order_calls == 2
    reads = dict(c.reads)
    again = recover(c)
    assert again.replay and _snapshot(c.x.c.y.x) == final and state(c)[1] == source and c.reads == reads
    assert result.public_summary()['new_money_transactions'] == 0
    from trading_robot.versioned_selected_sync import locked_current_selected_source
    with locked_current_selected_source(c.a, recovery=c.r, target_root=c.root,
            expected_selection_sha256=c.prepared.plan_sha256,
            expected_authority_sha256=authority.sha256) as (_, view):
        assert view.snapshot().cash_nano == c.cash
    request.node.user_properties.extend([('direction',direction), ('cash_nano',c.cash),
        ('ledger_revision',authority.ledger_revision),('new_money_transactions',0),('new_risk_executions',1),
        ('post_order_total',2),('central',intent.status),('authority',authority.state.value)])


@pytest.mark.parametrize('ack',['IN_FLIGHT','UNCERTAIN'])
def test_lost_ack_closes_only_same_cash_proven_intent(admission_case,monkeypatch,ack):
    c=setup_closure(admission_case,monkeypatch,ack=ack)
    old=c.a.manager.state(); source=state(c)[1]
    close(c)
    now=c.a.manager.state(); closed=next(i for i in now.intents if i.intent_id==c.intent.intent_id)
    assert closed.status=='RECONCILED' and closed.outcome=='FILLED' and closed.broker_order_id==c.fill['orderId']
    assert now.revision-old.revision==(2 if ack=='IN_FLIGHT' else 1)
    if ack=='UNCERTAIN':assert closed.transitions[-2].status=='UNCERTAIN'
    else:assert [s.status for s in closed.transitions[-2:]]==['SUBMITTED','RECONCILED']
    assert state(c)[1]==source and c.provider.order_calls==2
    assert recover(c).replay


@pytest.mark.parametrize('point',['closure.after_plan','closure.after_portfolio','closure.after_risk',
    'closure.after_central','closure.after_result_file','closure.after_result','closure.after_audit','closure.after_authority'])
def test_saved_prefixes_recover_without_reads_reposts_or_money(admission_case,desktop_case,monkeypatch,point):
    c=setup_closure(admission_case,monkeypatch)
    source=state(c)[1];before=_snapshot(c.x.c.y.x);seen=[]
    def cut(name):
        if name==point:
            seen.append(name);raise RuntimeError('synthetic closure cut')
    with pytest.raises(RuntimeError,match='synthetic closure cut'):close(c,fault_injector=cut)
    assert seen==[point]
    if point in {'closure.after_portfolio','closure.after_risk','closure.after_central'}:
        _recompose(c.x.c.y.x,desktop_case)
        c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
    def forbidden(*args,**kwargs):pytest.fail('provider used during offline recovery')
    for name in ('get_portfolio','get_orders','get_order_state','get_positions','get_max_lots','get_operations_by_cursor_once'):
        monkeypatch.setattr(c.provider,name,forbidden)
    result=recover(c)
    assert state(c)[1]==source and c.provider.order_calls==2
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert risk.recorded_execution_ids==(*before[0]['risk']['recorded_execution_ids'],c.intent.intent_id)
    assert risk.daily_order_count==before[0]['risk']['daily_order_count']+1
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    assert c.a.manager.state().blocking_intent is None
    final=_snapshot(c.x.c.y.x)
    assert recover(c).replay and _snapshot(c.x.c.y.x)==final


def test_fresh_evidence_contradictions_never_change_owners(admission_case,monkeypatch):
    c=setup_closure(admission_case,monkeypatch)
    before=_snapshot(c.x.c.y.x);source=state(c)[1]
    originals=deepcopy(c.fill),deepcopy(c.fill_rows),deepcopy(c.portfolio_after)
    for damage in ('receipt_fee','exchange','quantity','missing_operation','altered_old_fee','position','unknown_position'):
        c.fill,c.fill_rows,c.portfolio_after=deepcopy(originals)
        if damage=='receipt_fee':c.fill['executedCommission']=money('0.123456788')
        elif damage=='exchange':c.fill['orderId']='foreign-order'
        elif damage=='quantity':c.fill['lotsExecuted']='2'
        elif damage=='missing_operation':c.fill_rows.pop()
        elif damage=='altered_old_fee':
            row=next(r for r in c.fill_rows if r['type']=='OPERATION_TYPE_BROKER_FEE');row['payment']=money('-0.999')
        elif damage=='position':c.portfolio_after['positions']=[p for p in c.portfolio_after['positions'] if p['instrumentUid']!=c.intent.candidate.instrument_id]
        else:
            pos=deepcopy(c.portfolio_after['positions'][0]);pos['instrumentUid']='unknown';c.portfolio_after['positions'].append(pos)
        with pytest.raises(Exception) as caught:close(c)
        assert not isinstance(caught.value,(AttributeError,KeyError,TypeError)),(damage,caught.value)
        assert _snapshot(c.x.c.y.x)==before and state(c)[1]==source,damage
        assert not (c.root/'versioned_fill_closure'/c.sent.plan_sha256/'plan.json').exists()
    assert c.provider.order_calls==2


def test_post_cash_one_nano_drift_and_blocked_cash_do_not_close(admission_case,monkeypatch):
    c=setup_closure(admission_case,monkeypatch)
    before=_snapshot(c.x.c.y.x);source=state(c)[1];old=c.provider.get_positions
    for damage in ('one_nano','blocked','new_order'):
        def read(account):
            v=deepcopy(old(account))
            if damage=='one_nano':v['money']=[money(Decimal(c.cash+1)/10**9)]
            if damage=='blocked':v['blocked']=[money('0.01')]
            return v
        monkeypatch.setattr(c.provider,'get_positions',read)
        if damage=='new_order':
            row=deepcopy(c.fill);row.update(orderId='unknown',orderRequestId='other',executionReportStatus='EXECUTION_REPORT_STATUS_NEW',lotsExecuted='0',stages=[])
            c.provider.orders=[row]
        with pytest.raises(Exception) as caught:close(c)
        assert not isinstance(caught.value,(AttributeError,KeyError,TypeError)),caught.value
        assert _snapshot(c.x.c.y.x)==before and state(c)[1]==source
    assert c.provider.order_calls==2


def test_signed_false_plan_is_not_settlement_permission(admission_case,monkeypatch):
    c=setup_closure(admission_case,monkeypatch)
    before=_snapshot(c.x.c.y.x);source=state(c)[1]
    def cut(name):
        if name=='closure.after_plan':raise RuntimeError('plan saved')
    with pytest.raises(RuntimeError,match='plan saved'):close(c,fault_injector=cut)
    path=c.root/'versioned_fill_closure'/c.sent.plan_sha256/'plan.json'
    raw=path.read_bytes(); original=json.loads(raw)['payload']
    from trading_robot.versioned_fee_evidence import _sealed
    for damage in ('risk','position','cash_head','receipt','watermark'):
        payload=deepcopy(original)
        if damage=='risk':payload['after_owners']['risk']['daily_order_count']+=1
        elif damage=='position':payload['portfolio_candidate']['positions'][-1]['actual_lots']+=1
        elif damage=='cash_head':payload['pins']['ledger_head_sha256']='9'*64
        elif damage=='receipt':payload['reads'][0]['response']['executedCommission']=money('0.01')
        else:payload['covered_until']=payload['before_authority']['operations_complete_through']
        path.write_bytes(_sealed(payload,c.a.cl7_identity_key))
        with pytest.raises(Exception) as caught:recover(c)
        assert not isinstance(caught.value,(AttributeError,KeyError,TypeError)),caught.value
        assert _snapshot(c.x.c.y.x)==before and state(c)[1]==source
    path.write_bytes(raw)
    assert recover(c).public_summary()['bounded_settlement_verified']


def test_foreign_owner_after_portfolio_commit_remains_pending(admission_case,monkeypatch):
    c=setup_closure(admission_case,monkeypatch);source=state(c)[1]
    def cut(name):
        if name=='closure.after_portfolio':raise RuntimeError('portfolio saved')
    with pytest.raises(RuntimeError,match='portfolio saved'):close(c,fault_injector=cut)
    from dataclasses import replace
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    c.r.risk.state_store.save_account(c.a.policy.account_id,replace(risk,risk_resync_required=True))
    before=_snapshot(c.x.c.y.x)
    with pytest.raises(Exception,match='PREFIX'):recover(c)
    assert _snapshot(c.x.c.y.x)==before and state(c)[1]==source
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING


def test_locks_cover_risk_central_and_last_authority_commit(admission_case,monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock,LockUnavailableError
    c=setup_closure(admission_case,monkeypatch);checked=[]
    def locked(name):
        for root in (c.root/'ledger',c.a.cl7_ledger_store.root):
            con=sqlite3.connect(root/'store.sqlite3',timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError):con.execute('BEGIN IMMEDIATE')
            finally:con.close()
        for path in (c.a.manager.store.lock_path,c.r.risk.state_store.lock_path,c.r.manager.repository.lock_path):
            with pytest.raises(LockUnavailableError):
                with InterProcessFileLock(path,timeout_seconds=0):pass
        checked.append(name)
    def fault(name):
        if name in ('closure.after_risk','closure.after_central','closure.after_audit','closure.after_authority'):locked(name)
    close(c,fault_injector=fault)
    assert len(checked)==4
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    c.x.c.y.x.c.set_connected(True);c.x.c.y.x.c.set_market_state('OPEN')
    with pytest.raises(GuiRuntimeBlockedError,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):c.x.c.y.x.c.run_cycle()
    assert c.provider.order_calls==2


def test_clock_budget_includes_new_reads(admission_case,monkeypatch):
    c=setup_closure(admission_case,monkeypatch);before=_snapshot(c.x.c.y.x);old=c.provider.get_portfolio
    def slow(account):
        out=old(account);c.provider.clock_at+=timedelta(seconds=6);return out
    monkeypatch.setattr(c.provider,'get_portfolio',slow)
    with pytest.raises(Exception) as caught:close(c)
    assert not isinstance(caught.value,(AttributeError,KeyError,TypeError)),caught.value
    assert _snapshot(c.x.c.y.x)==before
    assert not (c.root/'versioned_fill_closure'/c.sent.plan_sha256/'plan.json').exists()


def test_real_monotonic_budget_with_synthetic_reads(admission_case,monkeypatch,request):
    import time
    c=setup_closure(admission_case,monkeypatch)
    monkeypatch.setattr(c.a,'cl7_monotonic_ns',time.monotonic_ns)
    start=[];old=c.provider.get_order_state
    def read(*args,**kwargs):
        start.append(time.monotonic());return old(*args,**kwargs)
    monkeypatch.setattr(c.provider,'get_order_state',read)
    result=close(c)
    elapsed=time.monotonic()-start[0]
    assert elapsed < 5 and result.public_summary()['bounded_settlement_verified']
    request.node.user_properties.append(('closure_capture_to_return_real_seconds',elapsed))
