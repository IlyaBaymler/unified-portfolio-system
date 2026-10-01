"""Rebuilt STEP31. Actual owners/SQLite; provider and clocks synthetic."""
from __future__ import annotations

import importlib
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_financial_readers import read_case
from test_q7a_source_versioned_operational_store import op_case, _operation
from test_q7a_source_versioned_cutover import cut_case
from test_q7a_source_selected_sync import selected_case, _inputs, _run, _view
from test_q7a_source_settlement_closure import _snapshot, _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State


@pytest.fixture
def owner_case(selected_case,monkeypatch):
    c=selected_case
    c.m31=importlib.import_module('trading_robot.versioned_owner_refresh')
    c.rows,c.read_calls=_inputs(c,monkeypatch)
    c.synced=_run(c)
    p=c.x.c.y.x.p;p.clock_at+=timedelta(seconds=1)
    c.cash=999048565432110
    old_portfolio=p.get_portfolio;old_limits=p.get_max_lots
    def portfolio(account):
        raw=deepcopy(old_portfolio(account))
        raw['totalAmountCurrencies']=money(Decimal(c.cash)/10**9)
        raw['totalAmountPortfolio']=money(Decimal(c.cash)/10**9+Decimal('1051.23456789'))
        return raw
    def limits(account,uid,*args,**kwargs):
        raw=deepcopy(old_limits(account,uid,*args,**kwargs))
        raw['buyLimits']['buyMoneyAmount']=money(Decimal(c.cash)/10**9)
        return raw
    monkeypatch.setattr(p,'get_portfolio',portfolio)
    monkeypatch.setattr(p,'get_max_lots',limits)
    return c


def refresh(c,**kwargs):
    return c.m31.refresh_selected_owners(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_authority_sha256=c.a.cash_authority_manager.status().sha256,**kwargs)


def plan(c):
    paths=sorted((c.root/'owner_refresh/plans').glob('*.json'))
    assert len(paths)==1
    return paths[0].stem


def recover(c,**kwargs):
    return c.m31.recover_owner_refresh(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_refresh_plan_sha256=plan(c),**kwargs)


def test_core_refresh_after_cash_sync_updates_owners_without_money(owner_case,request):
    c=owner_case;before=_snapshot(c.x.c.y.x);old=c.a.cash_authority_manager.status()
    with _view(c) as (_,v):source=v.export_bytes()
    result=refresh(c)
    after=_snapshot(c.x.c.y.x)
    state=c.r.manager.repository.load();risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert state.account.cash('rub').available == pytest.approx(999048.56, rel=0, abs=1e-8)
    assert Decimal(str(state.account.cash('rub').available)) <= Decimal('999048.56')
    assert risk.risk_resync_required and risk.recorded_execution_ids==tuple(before[0]['risk']['recorded_execution_ids'])
    assert [(p.instrument_id,p.actual_lots,p.target_lots,p.ownership) for p in state.positions]==[
        (p.instrument_id,p.actual_lots,p.target_lots,p.ownership) for p in
        c.m31.PortfolioState.from_dict(before[0]['portfolio']).positions]
    assert after[0]['central']==before[0]['central'] and after[1:]==before[1:]
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    assert c.a.cash_authority_manager.status().post_attempt_count==old.post_attempt_count==1
    with _view(c) as (_,v): assert v.export_bytes()==source and v.snapshot().cash_nano==c.cash
    assert recover(c).replay and _snapshot(c.x.c.y.x)==after
    assert not result.public_summary()['runtime_authority_granted'] and c.x.c.y.x.p.order_calls==1
    request.node.user_properties.extend([('ledger_cash_nano',c.cash),('portfolio_available_rub',repr(state.account.cash('rub').available)),
        ('risk_resync_required',risk.risk_resync_required),('new_money_transactions',0),('fake_post_count',1)])


def test_core_recovery_after_portfolio_commit(owner_case,request):
    c=owner_case;before=_snapshot(c.x.c.y.x)
    def fail(point):
        if point=='refresh.after_portfolio':raise RuntimeError('injected')
    with pytest.raises(RuntimeError,match='injected'):refresh(c,fault_injector=fail)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING
    assert _snapshot(c.x.c.y.x)[0]['risk']==before[0]['risk']
    reads=dict(c.read_calls);result=recover(c)
    assert result.risk_resync_required and c.read_calls==reads
    assert c.x.c.y.x.p.order_calls==1
    request.node.user_properties.extend([('recovery_provider_calls',0),('recovery_money_writes',0)])


@pytest.mark.parametrize('point',['refresh.after_plan','refresh.after_hold','refresh.after_portfolio',
                                  'refresh.after_risk','refresh.after_audit','refresh.after_authority'])
@pytest.mark.parametrize('restart',[False,True])
def test_ordered_recovery_cut_points(owner_case,desktop_case,monkeypatch,point,restart):
    c=owner_case
    def fail(at):
        if at==point:raise RuntimeError('cut')
    with pytest.raises(RuntimeError,match='cut'):refresh(c,fault_injector=fail)
    if restart:
        _recompose(c.x.c.y.x,desktop_case)
        c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
    p=c.x.c.y.x.p
    def forbidden(*args,**kwargs):pytest.fail('provider called in recovery')
    for method in ['get_portfolio','get_orders','get_order_state','get_positions','get_max_lots']:
        monkeypatch.setattr(p,method,forbidden)
    result=recover(c)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    assert result.risk_resync_required and p.order_calls==1
    after=_snapshot(c.x.c.y.x)
    assert recover(c).replay and _snapshot(c.x.c.y.x)==after


def test_refresh_sync_refresh_chain(owner_case,monkeypatch):
    c=owner_case;refresh(c);p=c.x.c.y.x.p
    p.clock_at+=timedelta(seconds=1)
    rows=c.rows+[_operation(c.x,name='second-cash',kind='OUTPUT',amount='-25',at=stamp(p.clock_at-timedelta(seconds=.5)))]
    _inputs(c,monkeypatch,rows=rows,amount='75');c.cash-=25*10**9
    second=_run(c)
    assert second.pins.ledger_revision==6
    p.clock_at+=timedelta(seconds=1)
    refresh(c)
    assert c.r.manager.repository.load().account.cash('rub').available == pytest.approx(999023.56, rel=0, abs=1e-8)
    with _view(c) as (_,v):assert v.snapshot().cash_nano==c.cash


@pytest.mark.parametrize('damage',['cash','position','orders','portfolio_account','metadata','risk_during_read','timeout'])
def test_invalid_capture_writes_no_owners(owner_case,monkeypatch,damage):
    c=owner_case;p=c.x.c.y.x.p;before=_snapshot(c.x.c.y.x)
    if damage=='cash':
        old=p.get_positions
        def read(account):
            raw=deepcopy(old(account));raw['money']=[money(Decimal(c.cash+1)/10**9)];return raw
        monkeypatch.setattr(p,'get_positions',read)
    elif damage in ('position','portfolio_account'):
        old=p.get_portfolio
        def read(account):
            raw=deepcopy(old(account))
            if damage=='portfolio_account':raw['accountId']='other'
            else:raw['positions']=[]
            return raw
        monkeypatch.setattr(p,'get_portfolio',read)
    elif damage=='orders':monkeypatch.setattr(p,'get_orders',lambda account:None)
    elif damage=='metadata':
        monkeypatch.setattr(c.a,'cl7_own_funds_policy',replace(c.a.cl7_own_funds_policy,
            binding_guard=lambda:(_ for _ in ()).throw(RuntimeError('changed'))))
    elif damage=='risk_during_read':
        old=p.get_portfolio
        def read(account):
            state=c.r.risk.state_store.load_account(c.a.policy.account_id)
            c.r.risk.state_store.save_account(c.a.policy.account_id,replace(state,kill_switch_active=True,kill_switch_reason='external'))
            return old(account)
        monkeypatch.setattr(p,'get_portfolio',read)
    else:
        old=p.get_positions
        def read(account):
            raw=old(account);p.clock_at+=timedelta(seconds=6);return raw
        monkeypatch.setattr(p,'get_positions',read)
    with pytest.raises(Exception):refresh(c)
    after=_snapshot(c.x.c.y.x)
    for k in ['portfolio','central','authority']:assert after[0][k]==before[0][k]
    if damage!='risk_during_read':assert after[0]['risk']==before[0]['risk']
    assert not list((c.root/'owner_refresh/plans').glob('*.json'))


def test_changed_risk_prefix_blocks_recovery(owner_case):
    c=owner_case
    def fail(point):
        if point=='refresh.after_portfolio':raise RuntimeError('cut')
    with pytest.raises(RuntimeError):refresh(c,fault_injector=fail)
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    c.r.risk.state_store.save_account(c.a.policy.account_id,replace(risk,daily_order_count=risk.daily_order_count+1))
    with pytest.raises(c.m31.OwnerRefreshError,match='PREFIX'):recover(c)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING


def test_gui_stays_blocked_after_refresh(owner_case):
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    c=owner_case;refresh(c);x=c.x.c.y.x
    x.c.set_connected(True);x.c.set_market_state('OPEN')
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    with pytest.raises(GuiRuntimeBlockedError,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):x.c.run_cycle()
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)


def test_global_deadline_includes_reads_and_final_writes(owner_case,monkeypatch):
    c=owner_case;p=c.x.c.y.x.p;old=p.get_positions
    def slow(account):
        raw=old(account);p.clock_at+=timedelta(seconds=4);return raw
    monkeypatch.setattr(p,'get_positions',slow)
    def delay(point):
        if point=='refresh.after_risk':p.clock_at+=timedelta(seconds=2)
    with pytest.raises(c.m31.OwnerRefreshError,match='STALE'):refresh(c,fault_injector=delay)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING
    recover(c)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED


def test_finishing_holds_real_owner_and_sqlite_locks(owner_case):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock, LockUnavailableError
    c=owner_case;checked=[]
    def check(point):
        if point not in {'refresh.after_risk','refresh.after_audit'}:return
        for lock in [c.r.manager.repository.lock_path,c.r.risk.state_store.lock_path,c.a.manager.store.lock_path]:
            with pytest.raises(LockUnavailableError):
                with InterProcessFileLock(lock,timeout_seconds=0):pass
        db=sqlite3.connect(c.root/'ledger/store.sqlite3',timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError):db.execute('BEGIN IMMEDIATE')
        finally:db.close()
        checked.append(point)
    refresh(c,fault_injector=check)
    assert checked==['refresh.after_risk','refresh.after_audit']


def test_corrupt_plan_never_repairs_owners(owner_case):
    c=owner_case
    def cut(point):
        if point=='refresh.after_hold':raise RuntimeError('cut')
    with pytest.raises(RuntimeError):refresh(c,fault_injector=cut)
    path=next((c.root/'owner_refresh/plans').glob('*.json'))
    data=path.read_text();path.write_text(data.replace('PREPARED','CORRUPTED'))
    before=_snapshot(c.x.c.y.x)
    with pytest.raises(c.m31.OwnerRefreshError):recover(c)
    assert _snapshot(c.x.c.y.x)==before


def test_stale_authority_pin_rejected_before_reads(owner_case,monkeypatch):
    c=owner_case;p=c.x.c.y.x.p
    monkeypatch.setattr(p,'get_portfolio',lambda *args:pytest.fail('unexpected provider read'))
    with pytest.raises(c.m31.OwnerRefreshError,match='CAS'):
        c.m31.refresh_selected_owners(c.a,recovery=c.r,target_root=c.root,
            expected_selection_sha256=c.prepared.plan_sha256,expected_authority_sha256='0'*64)


def test_old_operations_window_requires_sync_before_owner_refresh(owner_case,monkeypatch):
    c=owner_case;p=c.x.c.y.x.p;before=_snapshot(c.x.c.y.x)
    p.clock_at+=timedelta(seconds=6)
    monkeypatch.setattr(p,'get_portfolio',lambda *_:pytest.fail('stale window must stop before new reads'))
    with pytest.raises(c.m31.OwnerRefreshError,match='OPERATIONS_WINDOW_STALE'):refresh(c)
    assert _snapshot(c.x.c.y.x)==before
    assert not list((c.root/'owner_refresh/plans').glob('*.json'))
