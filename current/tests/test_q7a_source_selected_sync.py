"""STEP30 selected cash sync. Real owners and SQLite; synthetic provider only."""
from __future__ import annotations

import importlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_financial_readers import read_case
from test_q7a_source_versioned_operational_store import op_case, _operation
from test_q7a_source_versioned_cutover import cut_case, _prepare, _confirm
from test_q7a_source_settlement_closure import _snapshot, _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State


@pytest.fixture
def selected_case(cut_case):
    c=cut_case
    c.m30=importlib.import_module('trading_robot.versioned_selected_sync')
    _prepare(c); c.selected=_confirm(c)
    c.x.c.y.x.p.clock_at += timedelta(seconds=1)
    return c


def _inputs(c, monkeypatch, *, kind='INPUT', amount='100', name='cash-1', rows=None):
    x=c.x; provider=x.c.y.x.p
    op=_operation(x,name=name,kind=kind,amount=amount,at=x.end)
    rows=deepcopy(x.old_rows+[op] if rows is None else rows)
    cash=x.c.store.snapshot().cash_nano+int(Decimal(amount)*10**9)
    original=provider.get_positions
    calls={'operations':0,'cash':0}
    def read_ops(payload,timeout):
        calls['operations']+=1
        return {'items':deepcopy(rows),'hasNext':False,'nextCursor':''}
    def positions(account):
        calls['cash']+=1
        data=original(account);data['money']=[money(Decimal(cash)/10**9)];return data
    monkeypatch.setattr(provider,'get_operations_by_cursor_once',read_ops)
    monkeypatch.setattr(provider,'get_positions',positions)
    return rows,calls


def _run(c, **kw):
    return c.m30.sync_selected_cash(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_authority_sha256=c.a.cash_authority_manager.status().sha256,**kw)


def _plan(c):
    plans=list((c.root/'selected_sync/plans').glob('*.json'))
    assert len(plans)==1
    return plans[0].stem


def _recover(c, **kw):
    return c.m30.recover_selected_sync(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_sync_plan_sha256=_plan(c),**kw)


def _view(c):
    return c.m30.locked_current_selected_source(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_authority_sha256=c.a.cash_authority_manager.status().sha256)


def test_core_sync_cash_registry_and_authority(selected_case,monkeypatch,request):
    c=selected_case; before=_snapshot(c.x.c.y.x); old=c.a.cash_authority_manager.status()
    _,calls=_inputs(c,monkeypatch)
    result=_run(c)
    assert result.outcome=='COMMITTED' and not result.replay
    current=c.a.cash_authority_manager.status()
    assert current.state is State.EXACT_CASH_VERSIONED_DISARMED
    assert current.ledger_revision==result.pins.ledger_revision==5
    assert current.ledger_head_sha256==result.pins.ledger_head_sha256
    assert current.post_attempt_count==old.post_attempt_count==1
    assert current.activation_context_sha256!=c.prepared.plan_sha256
    assert result.registry_checkpoint.sequence==2
    with _view(c) as (adapter,view):
        assert view.snapshot().cash_nano==999048565432110
        assert view.snapshot().transaction_count==6
        with pytest.raises(Exception):adapter.require_runtime_authority()
    snap=_snapshot(c.x.c.y.x)
    for name in ('portfolio','risk','central'):assert snap[0][name]==before[0][name]
    assert snap[1:]==before[1:]
    assert c.x.c.y.x.p.order_calls==1 and calls=={'operations':1,'cash':1}
    again=_recover(c)
    assert again.replay and again.pins==result.pins and calls=={'operations':1,'cash':1}
    assert _snapshot(c.x.c.y.x)==snap
    request.node.user_properties.extend([('selected_cash_after_nano',999048565432110),
        ('ledger_revision',current.ledger_revision),('registry_sequence',2),('new_cash_transactions',1),
        ('risk_unchanged',True),('fake_post_count',1),('authority_state',current.state.value)])


def test_core_recover_source_commit_to_authority_gap(selected_case,monkeypatch,request):
    c=selected_case;before=_snapshot(c.x.c.y.x);_,calls=_inputs(c,monkeypatch)
    def cut(point):
        if point=='sync.after_source_commit':raise RuntimeError('cut-gap')
    with pytest.raises(Exception):_run(c,fault_injector=cut)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_SYNC_PENDING
    reads=dict(calls)
    result=_recover(c)
    assert result.outcome=='COMMITTED' and result.registry_checkpoint.sequence==2
    assert calls==reads
    with _view(c) as (_,view):assert view.snapshot().cash_nano==999048565432110
    snap=_snapshot(c.x.c.y.x)
    for name in ('portfolio','risk','central'):assert snap[0][name]==before[0][name]
    assert snap[1:]==before[1:]
    assert c.x.c.y.x.p.order_calls==1
    request.node.user_properties.extend([('gap_recovery','COMMITTED'),('recovery_provider_calls',0),
        ('recovery_money_writes',0),('new_cash_transactions',1),('fake_post_count',1)])


@pytest.mark.parametrize('kind,amount',[('OUTPUT','-25'),('BROKER_FEE','-0.010000001')])
def test_nontrading_cash_changes_preserve_owners(selected_case,monkeypatch,kind,amount):
    c=selected_case;before=_snapshot(c.x.c.y.x)
    _inputs(c,monkeypatch,kind=kind,amount=amount)
    result=_run(c)
    with _view(c) as (_,view):assert view.snapshot().cash_nano==998948565432110+int(Decimal(amount)*10**9)
    assert result.pins.ledger_revision==5
    after=_snapshot(c.x.c.y.x)
    for name in ('portfolio','risk','central'):assert after[0][name]==before[0][name]
    assert after[1:]==before[1:]


def test_rescan_and_replay_keep_ledger_head(selected_case,monkeypatch):
    c=selected_case;_inputs(c,monkeypatch,rows=c.x.old_rows,amount='0')
    result=_run(c)
    assert result.outcome=='COMMITTED' and result.pins.ledger_revision==4
    assert result.pins.ledger_head_sha256==c.selected.pins.ledger_head_sha256
    before=_snapshot(c.x.c.y.x)
    files={str(p):p.read_bytes() for p in (c.root/'selected_sync').rglob('*.json')}
    again=_run(c)
    assert again.outcome=='NO_CHANGE' and again.replay
    assert again.authority_sha256==result.authority_sha256 and again.pins==result.pins
    assert _snapshot(c.x.c.y.x)==before
    assert {str(p):p.read_bytes() for p in (c.root/'selected_sync').rglob('*.json')}==files


def test_two_consecutive_cash_rounds_follow_authority_lineage(selected_case,monkeypatch):
    c=selected_case
    rows,_=_inputs(c,monkeypatch)
    first=_run(c)
    c.x.c.y.x.p.clock_at+=timedelta(seconds=1)
    rows.append(_operation(c.x,name='cash-2',kind='OUTPUT',amount='-25',at=stamp(c.x.c.y.x.p.clock_at-timedelta(seconds=0.5))))
    _inputs(c,monkeypatch,rows=rows,amount='75')
    second=_run(c)
    assert second.pins.ledger_revision==6 and second.registry_checkpoint.sequence==4
    assert second.authority_sha256!=first.authority_sha256
    with _view(c) as (_,view):assert view.snapshot().cash_nano==999023565432110
    with pytest.raises(Exception):
        c.m.locked_selected_source # old resolver refuses advanced pins, not silently follows them
        with c.m.locked_selected_source(c.a,recovery=c.r,target_root=c.root,
            expected_plan_sha256=c.prepared.plan_sha256):pass
    assert c.x.c.y.x.p.order_calls==1


_CUTS=['sync.after_plan','sync.after_hold','registry.after_commit','sync.after_prepare',
       'source.append.before_insert','source.append.after_commit','sync.after_source_commit',
       'resolution.after_record','sync.after_registry_commit','finish.after_result',
       'finish.after_audit','finish.after_authority']
@pytest.mark.parametrize('recompose',[False,True])
@pytest.mark.parametrize('where',_CUTS)
def test_exact_prefix_recovery_never_replays_cash(selected_case,monkeypatch,desktop_case,where,recompose):
    from trading_robot import versioned_operational_store as v4
    c=selected_case;before=_snapshot(c.x.c.y.x);_,calls=_inputs(c,monkeypatch)
    def die(point):
        if point==where:raise RuntimeError('injected-cut')
    with pytest.raises(Exception):_run(c,fault_injector=die)
    source=v4.VersionedOperationalStore.open(c.root/'ledger',**c.x.c.y.common)
    try:
        raw=source.export_bytes();committed=source.snapshot().pins.ledger_revision==5
    finally:source.close()
    if recompose:
        _recompose(c.x.c.y.x,desktop_case)
        c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
    prior=dict(calls)
    # Recovered source must not be written even if the next transport would fail.
    monkeypatch.setattr(c.x.c.y.x.p,'get_operations_by_cursor_once',lambda *_:pytest.fail('provider in recovery'))
    monkeypatch.setattr(c.x.c.y.x.p,'get_positions',lambda *_:pytest.fail('cash read in recovery'))
    result=_recover(c)
    assert result.outcome==('COMMITTED' if committed else 'ABORTED')
    assert result.pins.ledger_revision==(5 if committed else 4)
    assert calls==prior
    source=v4.VersionedOperationalStore.open(c.root/'ledger',**c.x.c.y.common)
    try:assert source.export_bytes()==raw
    finally:source.close()
    after=_snapshot(c.x.c.y.x)
    for name in ('portfolio','risk','central'):assert after[0][name]==before[0][name]
    assert after[1:]==before[1:] and c.x.c.y.x.p.order_calls==1
    with _view(c) as (_,view):assert view.snapshot().cash_nano==(999048565432110 if committed else 998948565432110)


@pytest.mark.parametrize('damage',['cash_one_nano','blocked','fee_changed','missing_old','alias',
                                  'buy','sell','incomplete','timeout','cash_failure'])
def test_invalid_capture_writes_no_plan_or_source(selected_case,monkeypatch,damage):
    from trading_robot import versioned_operational_store as v4
    c=selected_case;rows,_=_inputs(c,monkeypatch)
    provider=c.x.c.y.x.p
    if damage=='fee_changed':rows[1]['payment']=money('-0.3')
    elif damage=='missing_old':rows.pop(0)
    elif damage=='alias':
        x=deepcopy(rows[1]);x['id']='alias-id';rows.append(x)
    elif damage in ('buy','sell'):
        rows[-1]=_operation(c.x,name='position-op',kind=damage.upper(),amount='-100' if damage=='buy' else '100',qty='10')
    if damage in ('fee_changed','missing_old','alias','buy','sell'):
        _inputs(c,monkeypatch,rows=rows,amount='-100' if damage=='buy' else '100')
    if damage in ('cash_one_nano','blocked'):
        original=provider.get_positions
        def wrong(account):
            raw=original(account)
            if damage=='cash_one_nano':raw['money']=[money(Decimal(999048565432111)/10**9)]
            else:raw['blocked']=[money(1)]
            return raw
        monkeypatch.setattr(provider,'get_positions',wrong)
    if damage=='incomplete':monkeypatch.setattr(provider,'get_operations_by_cursor_once',lambda *_:{'items':rows,'hasNext':True,'nextCursor':''})
    if damage=='timeout':monkeypatch.setattr(provider,'get_operations_by_cursor_once',lambda *_:(_ for _ in ()).throw(TimeoutError()))
    if damage=='cash_failure':monkeypatch.setattr(provider,'get_positions',lambda *_:(_ for _ in ()).throw(RuntimeError('offline')))
    before=_snapshot(c.x.c.y.x)
    source=v4.VersionedOperationalStore.open(c.root/'ledger',**c.x.c.y.common)
    try:raw=source.export_bytes()
    finally:source.close()
    with pytest.raises(Exception):_run(c)
    assert _snapshot(c.x.c.y.x)==before
    assert not (c.root/'selected_sync/plans').exists()
    source=v4.VersionedOperationalStore.open(c.root/'ledger',**c.x.c.y.common)
    try:assert source.export_bytes()==raw
    finally:source.close()


@pytest.mark.parametrize('damage',['risk','central','metadata','stopped'])
def test_owner_drift_during_capture_is_not_adopted(selected_case,monkeypatch,damage):
    c=selected_case;_,calls=_inputs(c,monkeypatch);p=c.x.c.y.x.p;original=p.get_positions
    def drift(account):
        raw=original(account)
        if damage=='risk':
            store=c.r.risk.state_store;s=store.load_account(account)
            store.save_account(account,replace(s,daily_order_count=s.daily_order_count+1))
        elif damage=='central':
            monkeypatch.setattr(c.a.manager,'state',lambda:(_ for _ in ()).throw(RuntimeError('changed-central')))
        elif damage=='metadata':
            monkeypatch.setattr(c.a,'cl7_own_funds_policy',replace(c.a.cl7_own_funds_policy,
                binding_guard=lambda:(_ for _ in ()).throw(RuntimeError('metadata-changed'))))
        else:monkeypatch.setattr(c.r.runtimes,'load',lambda **_:[])
        return raw
    monkeypatch.setattr(p,'get_positions',drift)
    authority=c.a.cash_authority_manager.status()
    with pytest.raises(Exception):_run(c)
    assert c.a.cash_authority_manager.status()==authority
    assert not (c.root/'selected_sync/plans').exists()


@pytest.mark.parametrize('where',['capture','after_hold','after_registry'])
def test_one_deadline_spans_capture_and_final_writes(selected_case,monkeypatch,where):
    c=selected_case;_inputs(c,monkeypatch);p=c.x.c.y.x.p;old=p.get_positions
    clock=[1]
    monkeypatch.setattr(c.a,'cl7_monotonic_ns',lambda:clock[0])
    def cash(account):
        raw=old(account);clock[0]+=4_000_000_000
        if where=='capture':clock[0]+=2_000_000_000
        return raw
    monkeypatch.setattr(p,'get_positions',cash)
    def delayed(point):
        if point==('sync.after_hold' if where=='after_hold' else 'sync.after_registry_commit'):
            clock[0]+=2_000_000_000
    with pytest.raises(Exception):_run(c,fault_injector=delayed)
    if where=='capture':
        assert not (c.root/'selected_sync/plans').exists()
        assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    else:
        assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_SYNC_PENDING
        result=_recover(c)
        assert result.outcome==('ABORTED' if where=='after_hold' else 'COMMITTED')


def test_real_locks_and_no_provider_under_owner_locks(selected_case,monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock
    c=selected_case;_inputs(c,monkeypatch);p=c.x.c.y.x.p;old=p.get_positions
    def cash(account):
        # Network IO occurs before final owners are locked.
        with InterProcessFileLock(c.r.risk.state_store.lock_path,timeout_seconds=0):pass
        return old(account)
    monkeypatch.setattr(p,'get_positions',cash)
    checked=[]
    def verify(point):
        if point=='finish.after_audit':
            with pytest.raises(Exception):
                with InterProcessFileLock(c.r.risk.state_store.lock_path,timeout_seconds=0):pass
            conn=sqlite3.connect(c.root/'ledger/store.sqlite3',timeout=0,isolation_level=None)
            try:
                with pytest.raises(sqlite3.OperationalError):conn.execute('BEGIN IMMEDIATE')
            finally:conn.close()
            checked.append(True)
    _run(c,fault_injector=verify)
    assert checked==[True]


def test_pending_blocks_sync_gui_and_both_readers(selected_case,monkeypatch):
    c=selected_case;_,calls=_inputs(c,monkeypatch)
    def cut(point):
        if point=='sync.after_hold':raise RuntimeError('hold')
    with pytest.raises(Exception):_run(c,fault_injector=cut)
    count=dict(calls)
    with pytest.raises(Exception):_run(c)
    with pytest.raises(Exception):
        with _view(c):pass
    with pytest.raises(Exception,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):
        c.x.c.y.x.c._recovery_required()
    assert calls==count and c.x.c.y.x.p.order_calls==1


def test_expected_authority_cas_is_required(selected_case,monkeypatch):
    c=selected_case;_,calls=_inputs(c,monkeypatch)
    with pytest.raises(Exception):
        c.m30.sync_selected_cash(c.a,recovery=c.r,target_root=c.root,
            expected_selection_sha256=c.prepared.plan_sha256,expected_authority_sha256='0'*64)
    assert calls=={'operations':0,'cash':0}


@pytest.mark.parametrize('damage',['missing_plan','bad_signature','bad_result','source_replaced','extra_registry'])
def test_corruption_or_unplanned_prefix_never_adopts(selected_case,monkeypatch,damage):
    import shutil
    from trading_robot import versioned_operational_store as v4
    from trading_robot import versioned_source_binding as reg
    from test_q7a_source_durable_source_binding import _run as run_binding
    c=selected_case;_inputs(c,monkeypatch)
    def cut(point):
        if point=='finish.after_result':raise RuntimeError('pending')
    with pytest.raises(Exception):_run(c,fault_injector=cut)
    digest=_plan(c);state=c.a.cash_authority_manager.status()
    if damage=='missing_plan':(c.root/'selected_sync/plans'/f'{digest}.json').unlink()
    elif damage=='bad_signature':
        path=c.root/'selected_sync/plans'/f'{digest}.json';d=json.loads(path.read_bytes());d['payload']['sequence']=99;path.write_text(json.dumps(d))
    elif damage=='bad_result':
        path=c.root/'selected_sync/resolutions'/f'{digest}.json';d=json.loads(path.read_bytes());d['payload']['outcome']='ABORTED';path.write_text(json.dumps(d))
    elif damage=='source_replaced':
        path=c.root/'ledger/store.sqlite3';q=path.with_suffix('.other');shutil.copyfile(path,q);q.replace(path)
    else:
        source=v4.VersionedOperationalStore.open(c.root/'ledger',**c.x.c.y.common)
        p=json.loads((c.root/'prepared.json').read_bytes())['payload']
        binding=reg.DurableSourceBinding.open(c.root/'pins',source=source,
            checkpoint=reg.BindingCheckpoint(**p['registry_checkpoint']),runtime_scope_sha256=p['runtime_scope_sha256'],
            owner_binding_sha256=p['owner_binding_sha256'])
        x=SimpleNamespace(**vars(c.x));x.store=source
        b=SimpleNamespace(x=x,r=binding)
        # A distinct later rescan commits extra registry events beyond the plan.
        from test_q7a_source_versioned_operational_store import _later
        try:run_binding(b,rows=x.old_rows+[_operation(x,name='cash-1')],end=_later(x.end,3))
        finally:source.close()
    with pytest.raises(Exception):
        c.m30.recover_selected_sync(c.a,recovery=c.r,target_root=c.root,
            expected_selection_sha256=c.prepared.plan_sha256,expected_sync_plan_sha256=digest)
    assert c.a.cash_authority_manager.status()==state
