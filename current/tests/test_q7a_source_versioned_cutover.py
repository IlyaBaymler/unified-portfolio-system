"""STEP29 source selection with real financial owners and synthetic provider only."""
from __future__ import annotations

import importlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_financial_readers import read_case, _pins
from test_q7a_source_versioned_operational_store import op_case
from test_q7a_source_settlement_closure import _snapshot
from trading_robot import cash_ledger_persistence as cl2
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State


def _module():
    return importlib.import_module('trading_robot.versioned_runtime_cutover')


@pytest.fixture
def cut_case(op_case):
    x = op_case
    m = _module()
    return SimpleNamespace(x=x, a=x.c.y.a, r=x.c.y.recovery, m=m,
        root=x.root.with_name('selected-v4'), journal=x.c.store)


def _prepare(c, **kwargs):
    c.prepared = c.m.prepare_versioned_cutover(c.a, recovery=c.r, candidate=c.x.store,
        candidate_pins=c.x.store.snapshot().pins, journal_store=c.journal,
        journal_pins=_pins(c.x.c), target_root=c.root, **kwargs)
    return c.prepared


def _confirm(c, **kwargs):
    return c.m.confirm_versioned_cutover(c.a, recovery=c.r, target_root=c.root,
        expected_plan_sha256=c.prepared.plan_sha256,
        confirmation=c.prepared.confirmation, journal_store=c.journal, **kwargs)


def test_core_select_copy_disarmed_preserves_money_and_execution(cut_case,request):
    c=cut_case
    before=_snapshot(c.x.c.y.x)
    source=c.a.cl7_ledger_store.export_bytes(); candidate=c.x.store.export_bytes()
    old=c.a.cash_authority_manager.status()
    p=_prepare(c)
    assert _snapshot(c.x.c.y.x)==before
    result=_confirm(c)
    after=_snapshot(c.x.c.y.x)
    assert c.a.cl7_ledger_store.export_bytes()==source and c.x.store.export_bytes()==candidate
    for name in ['portfolio','risk','central']:assert after[0][name]==before[0][name]
    selected=c.a.cash_authority_manager.status()
    assert selected.state is State.EXACT_CASH_VERSIONED_DISARMED
    assert selected.post_attempt_count==old.post_attempt_count==1
    assert selected.ledger_revision==old.ledger_revision+1==4
    assert selected.activation_context_sha256==p.plan_sha256
    with c.m.locked_selected_source(c.a,recovery=c.r,target_root=c.root,
            expected_plan_sha256=p.plan_sha256) as (adapter,view):
        assert view.snapshot().cash_nano==998948565432110
        assert view.pins==result.pins
        with pytest.raises(Exception):adapter.require_runtime_authority()
    assert c.x.c.y.x.p.order_calls==1
    again=_confirm(c)
    assert again.replay and _snapshot(c.x.c.y.x)==after
    request.node.user_properties.extend([('selected_cash_nano',998948565432110),
        ('initial_cash_nano',998948641975321),('new_money_transactions',0),
        ('fake_post_count',1),('selected_state',selected.state.value),('ledger_revision',selected.ledger_revision)])


def test_core_resume_after_hold_without_second_post(cut_case):
    c=cut_case;_prepare(c)
    def cut(point):
        if point=='confirm.after_hold':raise RuntimeError('cut-hold')
    with pytest.raises(RuntimeError,match='cut-hold'):_confirm(c,fault_injector=cut)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_SOURCE_CUTOVER_PENDING
    result=_confirm(c)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    assert c.x.c.y.x.p.order_calls==1 and not result.replay


@pytest.mark.parametrize('confirmation',['','SELECT VERSIONED CASH DISARMED','SELECT VERSIONED CASH DISARMED '+'0'*64])
def test_confirmation_is_exact_and_never_selects_implicitly(cut_case,confirmation):
    c=cut_case;_prepare(c);before=_snapshot(c.x.c.y.x)
    with pytest.raises(c.m.VersionedCutoverError,match='CONFIRMATION'):
        c.m.confirm_versioned_cutover(c.a,recovery=c.r,target_root=c.root,
            expected_plan_sha256=c.prepared.plan_sha256,confirmation=confirmation,journal_store=c.journal)
    assert _snapshot(c.x.c.y.x)==before and not (c.root/'commit.json').exists()


@pytest.mark.parametrize('point',['confirm.after_plan','confirm.after_hold','confirm.after_audit','confirm.after_selection'])
@pytest.mark.parametrize('recompose',[False,True])
def test_committed_prefix_recovery_requires_same_plan(cut_case,desktop_case,point,recompose):
    from test_q7a_source_settlement_closure import _recompose
    c=cut_case;_prepare(c)
    before=_snapshot(c.x.c.y.x)
    def die(actual):
        if actual==point:raise RuntimeError('injected-cut')
    with pytest.raises(RuntimeError,match='injected-cut'):_confirm(c,fault_injector=die)
    if recompose:
        _recompose(c.x.c.y.x,desktop_case)
        c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
    after=_confirm(c)
    assert after.replay==(point=='confirm.after_selection')
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    snap=_snapshot(c.x.c.y.x)
    for k in ('portfolio','risk','central'):assert snap[0][k]==before[0][k]
    assert snap[1:]==before[1:]


@pytest.mark.parametrize('point',['prepare.after_copy','prepare.after_registry','prepare.after_plan'])
def test_prepare_failure_does_not_change_authority(cut_case,point):
    c=cut_case;before=_snapshot(c.x.c.y.x)
    def die(actual):
        if actual==point:raise RuntimeError('injected-prepare')
    with pytest.raises(RuntimeError,match='injected-prepare'):_prepare(c,fault_injector=die)
    assert _snapshot(c.x.c.y.x)==before
    assert c.root.exists()==(point=='prepare.after_plan')


@pytest.mark.parametrize('damage',['receipt_fee','missing_fee','operation_alias','cash_one_nano','blocked_cash','stale_portfolio'])
@pytest.mark.parametrize('after_hold',[False,True])
def test_invalid_fresh_evidence_never_selects(cut_case,monkeypatch,damage,after_hold):
    from datetime import timedelta
    c=cut_case;_prepare(c)
    if after_hold:
        def die(actual):
            if actual=='confirm.after_hold':raise RuntimeError('stop')
        with pytest.raises(RuntimeError):_confirm(c,fault_injector=die)
    before=_snapshot(c.x.c.y.x)
    y=c.x.c.y
    if damage=='receipt_fee':y.raw['executedCommission']=money('0.3')
    elif damage=='missing_fee':y.raw.pop('executedCommission')
    elif damage=='operation_alias':y.fee['id']='foreign-fee-id'
    elif damage in ('cash_one_nano','blocked_cash'):
        original=y.x.p.get_positions
        def changed(account):
            raw=original(account)
            if damage=='cash_one_nano':raw['money']=[money(Decimal(998948565432111)/10**9)]
            else:raw['blocked']=[money(1)]
            return raw
        monkeypatch.setattr(y.x.p,'get_positions',changed)
    else:y.x.p.clock_at+=timedelta(days=1)
    with pytest.raises(Exception):_confirm(c)
    assert _snapshot(y.x)==before
    assert c.a.cash_authority_manager.status().state is (
        State.EXACT_CASH_SOURCE_CUTOVER_PENDING if after_hold else State.EXACT_CASH_DISARMED)


@pytest.mark.parametrize('damage',['plan_hash','plan_content','missing_plan','target_identity','target_batch','registry_missing','runtime_graph'])
def test_plan_target_and_graph_fail_closed(cut_case,monkeypatch,damage):
    from trading_robot import versioned_operational_store as v4
    from test_q7a_source_versioned_operational_store import _sync
    import shutil
    c=cut_case;_prepare(c);before=_snapshot(c.x.c.y.x)
    if damage=='plan_hash':c.prepared=replace(c.prepared,plan_sha256='0'*64)
    elif damage=='plan_content':
        p=c.root/'prepared.json';doc=json.loads(p.read_bytes());doc['payload']['cash_nano']='1';p.write_text(json.dumps(doc))
    elif damage=='missing_plan':(c.root/'prepared.json').unlink()
    elif damage=='target_identity':
        p=c.root/'ledger/store.sqlite3'; q=p.with_suffix('.copy');shutil.copyfile(p,q);q.replace(p)
    elif damage=='target_batch':
        target=v4.VersionedOperationalStore.open(c.root/'ledger',**c.x.c.y.common)
        original=c.x.store;c.x.store=target
        try:_sync(c.x,c.x.old_rows)
        finally:c.x.store=original;target.close()
    elif damage=='registry_missing':(c.root/'pins/store.sqlite3').unlink()
    else:monkeypatch.setattr(c.r,'central',object())
    with pytest.raises(Exception):_confirm(c)
    if damage!='runtime_graph':assert _snapshot(c.x.c.y.x)==before
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_DISARMED


def test_source_risk_change_rejects_prepared_plan(cut_case):
    c=cut_case;_prepare(c)
    store=c.r.risk.state_store;account=c.a.policy.account_id
    state=store.load_account(account)
    state=replace(state,daily_order_count=state.daily_order_count+1)
    store.save_account(account,state)
    before=_snapshot(c.x.c.y.x)
    with pytest.raises(Exception):_confirm(c)
    assert _snapshot(c.x.c.y.x)==before


def test_source_metadata_change_rejects_prepared_plan(cut_case,monkeypatch):
    c=cut_case;_prepare(c);before=_snapshot(c.x.c.y.x)
    def invalid():raise RuntimeError('metadata-drift')
    monkeypatch.setattr(c.a,'cl7_own_funds_policy',replace(c.a.cl7_own_funds_policy,binding_guard=invalid))
    with pytest.raises(Exception):_confirm(c)
    assert _snapshot(c.x.c.y.x)==before


def test_existing_target_never_removed(cut_case):
    c=cut_case;c.root.mkdir();marker=c.root/'user-data';marker.write_text('keep')
    with pytest.raises(Exception):_prepare(c)
    assert marker.read_text()=='keep'


def test_racing_target_creation_never_removed(cut_case,monkeypatch):
    from pathlib import Path
    c=cut_case;original=Path.mkdir
    def racing(path,*args,**kwargs):
        if path==c.root:
            original(path,*args,**kwargs);(path/'foreign').write_text('keep')
            raise FileExistsError('racing foreign target')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'mkdir',racing)
    with pytest.raises(FileExistsError):_prepare(c)
    assert (c.root/'foreign').read_text()=='keep'


def test_candidate_with_ordinary_batches_requires_later_cutover_profile(cut_case):
    from test_q7a_source_versioned_operational_store import _sync,_operation
    c=cut_case;_sync(c.x,c.x.old_rows+[_operation(c.x)])
    before=_snapshot(c.x.c.y.x)
    with pytest.raises(c.m.VersionedCutoverError,match='NONEMPTY'):_prepare(c)
    assert _snapshot(c.x.c.y.x)==before and not c.root.exists()


def test_audit_failure_leaves_hold_and_can_resume(cut_case,monkeypatch):
    c=cut_case;_prepare(c);j=c.r.manager.journal;old=j.record
    def failed(event):
        if event.event_type=='VERSIONED_SOURCE_SELECTION_VERIFIED':raise RuntimeError('audit-disk')
        return old(event)
    with monkeypatch.context() as mp:
        mp.setattr(j,'record',failed)
        with pytest.raises(RuntimeError,match='audit-disk'):_confirm(c)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_SOURCE_CUTOVER_PENDING
    _confirm(c)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED


def test_deadline_after_hold_never_selects(cut_case):
    from datetime import timedelta
    c=cut_case;_prepare(c)
    def pause(point):
        if point=='confirm.after_hold':c.x.c.y.x.p.clock_at+=timedelta(seconds=6)
    with pytest.raises(c.m.VersionedCutoverError,match='STALE'):_confirm(c,fault_injector=pause)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_SOURCE_CUTOVER_PENDING


def test_final_writes_hold_actual_file_and_both_sqlite_locks(cut_case,monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock
    c=cut_case;_prepare(c);manager=c.a.cash_authority_manager;real=manager.store._commit_unlocked;seen=[]
    def checked(next_record,**kwargs):
        for path in (manager.store.lock_path,c.r.profiles.lock_path,c.r.runtimes.lock_path,
                     c.r.manager.repository.lock_path,c.r.risk.profile_store.lock_path,
                     c.r.risk.state_store.lock_path,c.a.manager.store.lock_path,
                     c.root/'pins.binding.lock'):
            with pytest.raises(Exception):
                with InterProcessFileLock(path,timeout_seconds=0):pass
        for db in (c.a.cl7_ledger_store.root/'store.sqlite3',c.root/'ledger/store.sqlite3'):
            other=sqlite3.connect(db,isolation_level=None,timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError):other.execute('BEGIN IMMEDIATE')
            finally:other.close()
        seen.append(next_record.state)
        return real(next_record,**kwargs)
    monkeypatch.setattr(manager.store,'_commit_unlocked',checked)
    _confirm(c)
    assert seen==[State.EXACT_CASH_SOURCE_CUTOVER_PENDING,State.EXACT_CASH_VERSIONED_DISARMED]


@pytest.mark.parametrize('held',[False,True])
def test_old_arm_and_dispatch_have_no_fallback(cut_case,held):
    c=cut_case;_prepare(c)
    if held:
        def die(p):
            if p=='confirm.after_hold':raise RuntimeError('hold')
        with pytest.raises(RuntimeError):_confirm(c,fault_injector=die)
    else:_confirm(c)
    before=_snapshot(c.x.c.y.x);m=c.a.cash_authority_manager
    with pytest.raises(Exception):m.arm(raw_account_id=c.a.policy.account_id,identity_key=c.a.cl7_identity_key,
        identity_key_id=c.a.cl7_identity_key_id,confirmation=m.ARM_PHRASE,transition_at=c.a.cl7_clock())
    with pytest.raises(Exception):m.disarm(transition_at=c.a.cl7_clock())
    with pytest.raises(Exception):m.cancel(transition_at=c.a.cl7_clock())
    # Actual adapter dispatch on an empty terminal queue cannot create a POST.
    c.a.dispatch_next(c.r.manager.repository)
    assert _snapshot(c.x.c.y.x)==before


def test_selected_view_is_scoped_to_exact_source_and_live_lease(cut_case):
    c=cut_case;_prepare(c);_confirm(c)
    with c.m.locked_selected_source(c.a,recovery=c.r,target_root=c.root,
            expected_plan_sha256=c.prepared.plan_sha256) as (_,view):assert view.snapshot().cash_nano==998948565432110
    with pytest.raises(Exception):view.snapshot()
    with pytest.raises(Exception):
        with c.m.locked_selected_source(c.a,recovery=c.r,target_root=c.root,
                expected_plan_sha256='0'*64):pass


def test_public_summary_has_no_private_paths_accounts_or_cash(cut_case):
    c=cut_case;p=_prepare(c);r=_confirm(c)
    text=json.dumps([p.public_summary(),r.public_summary()])
    for value in (str(c.root),c.a.policy.account_id,'998948565432110','998948641975321'):
        assert value not in text
    assert r.public_summary()['source_selection_performed'] is True
    assert r.public_summary()['trading_cutover_complete'] is False


def test_illegal_source_transition_cannot_arm_or_change_attempt(cut_case):
    from trading_robot.runtime_cash_authority import _transition_pair
    c=cut_case;p=_prepare(c);_confirm(c);m=c.a.cash_authority_manager;current=m.status()
    for kwargs in ({'state':State.EXACT_CASH_ARMED},{'post_attempt_count':0},
                   {'state':State.LEGACY_ACTIVE},{'opening_record_sha256':'0'*64}):
        with pytest.raises(Exception):
            bad=m._change(current,at=c.a.cl7_clock(),kind='ARM_EXACT',**kwargs)
            _transition_pair(current,bad)


def test_selection_does_not_repair_partial_authority_file_commit(cut_case,monkeypatch):
    c=cut_case;_prepare(c);store=c.a.cash_authority_manager.store;real=store._replace_prepared
    def cut(temp,path):
        real(temp,path)
        if path==store.path:raise RuntimeError('cut-active-before-checksum')
    before_ledger=c.a.cl7_ledger_store.export_bytes()
    with monkeypatch.context() as mp:
        mp.setattr(store,'_replace_prepared',cut)
        with pytest.raises(RuntimeError):_confirm(c)
    broken=store.path.read_bytes(),store.checksum_path.read_bytes()
    with pytest.raises(Exception):_confirm(c)
    assert (store.path.read_bytes(),store.checksum_path.read_bytes())==broken
    assert c.a.cl7_ledger_store.export_bytes()==before_ledger and c.x.c.y.x.p.order_calls==1


@pytest.mark.parametrize('held',[False,True])
@pytest.mark.parametrize('route',['run_cycle','service_tick'])
def test_default_gui_blocks_before_refresh_strategy_or_candles(cut_case,held,route):
    c=cut_case;_prepare(c)
    if held:
        def stop(point):
            if point=='confirm.after_hold':raise RuntimeError('hold')
        with pytest.raises(RuntimeError):_confirm(c,fault_injector=stop)
    else:_confirm(c)
    x=c.x.c.y.x;counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls,len(x.p.cash_read_calls)
    before=_snapshot(x)
    with pytest.raises(Exception,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):
        if route=='run_cycle':x.c.run_cycle()
        else:x.c.service_tick(now=x.p.clock_at,latest_closed_candles={},hooks=object())
    assert (x.p.candle_calls,x.p.quote_calls,x.p.order_calls,len(x.p.cash_read_calls))==counts
    assert _snapshot(x)==before


def test_total_capture_and_commit_deadline_is_not_restarted(cut_case,monkeypatch):
    c=cut_case;_prepare(c);real=c.a.transport.get_order_state
    elapsed=[0]
    # Independent wall and monotonic clocks: local work must not restart the
    # monotonic budget just because the signed wall-clock timestamps are equal.
    monkeypatch.setattr(c.a,'cl7_monotonic_ns',lambda:elapsed[0])
    def slow(*args,**kwargs):
        elapsed[0]+=4_000_000_000
        return real(*args,**kwargs)
    monkeypatch.setattr(c.a.transport,'get_order_state',slow)
    def late(point):
        if point=='confirm.after_hold':elapsed[0]+=2_000_000_000
    with pytest.raises(c.m.VersionedCutoverError,match='STALE'):_confirm(c,fault_injector=late)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_SOURCE_CUTOVER_PENDING
    assert c.x.c.y.x.p.order_calls==1


def test_cutover_open_store_identities_never_raw_read_live_shm(cut_case, monkeypatch):
    c = cut_case
    source_shared_memory = c.a.cl7_ledger_store.root / "store.sqlite3-shm"
    live_shared_memories = {
        source_shared_memory,
        c.x.store.root / "store.sqlite3-shm",
        c.journal.root / "store.sqlite3-shm",
        c.root / "ledger" / "store.sqlite3-shm",
    }
    original_read_bytes = Path.read_bytes
    attempted_shm_reads = []

    def reject_live_shm(path):
        if path in live_shared_memories and path.exists():
            attempted_shm_reads.append(path)
            raise PermissionError("simulated Windows live SHM denial")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_live_shm)
    prepared = _prepare(c)
    assert prepared.plan_sha256
    assert attempted_shm_reads == []
    with pytest.raises(
        cl2.PersistenceError,
        match=f"^{cl2.PersistenceReason.WAL_SIDECAR_INCONSISTENT.value}$",
    ):
        cl2._validate_live_root(c.a.cl7_ledger_store.root)
    assert attempted_shm_reads == [source_shared_memory]


def test_cutover_physical_identity_uses_retained_custody_token(cut_case):
    c = cut_case
    store = c.journal
    original = store._custody._identity
    physical = c.m._physical(store.root, store._connection, store._custody)
    assert physical["device"] == str(original.device)
    assert physical["inode"] == str(original.inode)
    store._custody._identity = cl2._DatabaseIdentity(original.device, original.inode + 1)
    try:
        with pytest.raises(
            cl2.PersistenceError,
            match=f"^{cl2.PersistenceReason.PATH_INVALID.value}$",
        ):
            c.m._physical(store.root, store._connection, store._custody)
    finally:
        store._custody._identity = original
