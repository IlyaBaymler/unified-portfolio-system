"""STEP28: durable independent pins, synthetic IO, real SQLite and CL3.

No user runtime, real provider, cutover, arm or v4 PostOrder is executed.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_financial_readers import read_case
from test_q7a_source_versioned_operational_store import op_case, _operation, _request, _later, _sync
from test_q7a_source_versioned_runtime_adapter import queued_inputs
from test_q7a_source_settlement_closure import _snapshot
from trading_robot import versioned_operational_store as v4
from trading_robot.locking import InterProcessFileLock


def _module():
    return importlib.import_module('trading_robot.versioned_source_binding')


@pytest.fixture
def bound_case(op_case):
    x = op_case; m = _module()
    # Independently supplied test identity, not a claim that registry locks P/R/C.
    runtime = sha256(b'synthetic-runtime-identity-v1').hexdigest()
    owners = sha256(json.dumps(_snapshot(x.c.y.x)[0], sort_keys=True, default=str).encode()).hexdigest()
    r = m.DurableSourceBinding.create(x.root.with_name('source-binding'), source=x.store,
        expected_pins=x.store.snapshot().pins, runtime_scope_sha256=runtime,
        owner_binding_sha256=owners, created_at=x.end)
    return SimpleNamespace(x=x, m=m, r=r, initial=r.snapshot(), runtime=runtime, owners=owners)


def _reopen(b, *, reopen_source=True, checkpoint=None):
    if reopen_source:
        b.x.store.close()
        b.x.store = v4.VersionedOperationalStore.open(b.x.root, **b.x.c.y.common)
    b.r = b.m.DurableSourceBinding.open(b.r.root, source=b.x.store,
        checkpoint=checkpoint or b.initial.checkpoint,
        runtime_scope_sha256=b.runtime, owner_binding_sha256=b.owners)
    return b.r


def _run(b, rows=None, *, end=None, transform=None, cash_override=None, clock=None, checkpoint=None):
    x = b.x
    rows = x.old_rows + [_operation(x)] if rows is None else rows
    old_ids = {r['id'] for r in x.old_rows}
    amount = x.c.store.snapshot().cash_nano + sum(
        int(Decimal(r['payment']['units']) * 10**9) + int(r['payment']['nano'])
        for r in rows if r['id'] not in old_ids)
    def positions(account):
        raw = x.c.y.x.p.get_positions(account)
        raw['money'] = [money(Decimal(amount if cash_override is None else cash_override) / 10**9)]
        return raw
    return b.r.sync_tbank_operations(_request(x, rows, end=end, transform=transform, clock=clock),
        expected_checkpoint=checkpoint or b.r.snapshot().checkpoint,
        recorded_at=end or _later(x.end), read_rub_positions=positions)


def _die(point):
    def fail(actual):
        if actual == point:
            raise RuntimeError('injected-' + point)
    return fail


def test_core_commit_then_pins_persists_corrected_cash(bound_case, request):
    b=bound_case; x=b.x
    owners = _snapshot(x.c.y.x); original=x.c.store.export_bytes()
    result, state = _run(b)
    assert state.pins == x.store.snapshot().pins
    assert state.pins.ledger_revision == b.initial.pins.ledger_revision+1
    assert state.pins.store_revision == b.initial.pins.store_revision+1
    assert state.checkpoint.sequence == 2 and state.pending_plan_sha256 is None
    assert result.appended_transactions == 1 and result.cash_delta_nano == 100*10**9
    with b.r.locked_binding() as (adapter, view):
        assert adapter.pins == state.pins and view.pins == state.pins
        assert view.project_cash().expected_cash_nano == 999048565432110
        args=queued_inputs(x)
        binding=adapter.build_request_binding(view, **args)
        assert binding.public_summary()['runtime_authority_granted'] is False
    assert _snapshot(x.c.y.x)==owners and x.c.store.export_bytes()==original
    root_export=b.r.export_bytes(); raw=x.store.export_bytes()
    result2,state2=_run(b)
    assert result2.replay and result2.appended_transactions==0 and state2==state
    assert b.r.export_bytes()==root_export and x.store.export_bytes()==raw
    _reopen(b, checkpoint=state.checkpoint)
    assert b.r.snapshot()==state
    with b.r.locked_binding() as (_,view):assert view.snapshot().cash_nano==999048565432110
    request.node.user_properties.extend([('cash_after_nano',999048565432110),
        ('new_transactions',1),('binding_events',2),('source_runtime_unchanged',True),
        ('v4_post_order_calls',0),('initial_fake_posts',x.c.y.x.p.order_calls)])


def test_core_recover_commit_to_pin_gap_without_second_write(bound_case, request):
    b=bound_case; x=b.x
    owners=_snapshot(x.c.y.x)
    b.r._fault=_die('sync.after_source_commit')
    with pytest.raises(RuntimeError,match='injected'): _run(b)
    old=b.r.snapshot()
    assert old.pins==b.initial.pins and old.pending_plan_sha256
    assert x.store.snapshot().pins != old.pins
    assert x.store.snapshot().cash_nano==999048565432110
    raw=x.store.export_bytes()
    _reopen(b)
    with pytest.raises(b.m.SourceBindingError,match='PENDING'):
        with b.r.locked_binding():pass
    # Recovery must not recollect, append, or replay a monetary write.
    x.store._injector=lambda _:pytest.fail('recovery must not call monetary writer')
    result,done=b.r.recover_pending(expected_checkpoint=b.r.snapshot().checkpoint)
    assert result=='COMMITTED' and done.pending_plan_sha256 is None
    assert done.pins==x.store.snapshot().pins and x.store.export_bytes()==raw
    assert _snapshot(x.c.y.x)==owners
    saved=b.r.export_bytes()
    again,same=b.r.recover_pending(expected_checkpoint=done.checkpoint)
    assert again=='NO_PENDING' and same==done and b.r.export_bytes()==saved
    request.node.user_properties.extend([('commit_gap_recovered',True),
        ('cash_after_nano',999048565432110),('recovery_source_writes',0),
        ('recovery_provider_calls',0),('recovery_risk_writes',0),('second_post',False)])


@pytest.mark.parametrize('reopen',[False,True])
@pytest.mark.parametrize('where',['registry.after_commit','sync.after_prepare',
    'source.append.before_insert','source.append.after_insert','source.append.before_commit',
    'source.append.after_commit','sync.after_source_commit','resolution.before_record','resolution.after_record'])
def test_ordered_failure_cuts_resolve_exact_prefix_only(bound_case,where,reopen):
    b=bound_case; x=b.x; owners=_snapshot(x.c.y.x)
    if where.startswith('source.'):
        x.store._injector=_die(where.removeprefix('source.'))
    else:b.r._fault=_die(where)
    with pytest.raises(Exception):_run(b)
    state=b.r.snapshot(); raw=x.store.export_bytes(); committed= x.store.snapshot().pins!=b.initial.pins
    if reopen:_reopen(b)
    b.r._fault=None; x.store._injector=None
    outcome,end=b.r.recover_pending(expected_checkpoint=b.r.snapshot().checkpoint)
    assert outcome==('NO_PENDING' if state.pending_plan_sha256 is None else 'COMMITTED' if committed else 'ABORTED')
    assert end.pending_plan_sha256 is None and end.pins==x.store.snapshot().pins
    assert x.store.export_bytes()==raw and _snapshot(x.c.y.x)==owners
    if not committed:
        assert end.pins==b.initial.pins
        result,end=_run(b)
        assert result.appended_transactions==1
    else:
        result,_=_run(b)
        assert result.replay
    assert x.store.snapshot().cash_nano==999048565432110
    assert x.store.snapshot().transaction_count==6 and x.c.y.x.p.order_calls==1


@pytest.mark.parametrize('point',['registry.before_insert','registry.after_insert','registry.before_commit'])
def test_registry_prepare_rollback_never_calls_source_insert(bound_case,point):
    b=bound_case; before=b.r.export_bytes(),b.x.store.export_bytes()
    b.r._fault=_die(point)
    with pytest.raises(Exception):_run(b)
    assert (b.r.export_bytes(),b.x.store.export_bytes())==before
    assert b.r.snapshot().pending_plan_sha256 is None


@pytest.mark.parametrize('point',['registry.before_insert','registry.after_insert','registry.before_commit','registry.after_commit'])
def test_pin_commit_fault_after_source_commit_is_recoverable(bound_case,point):
    b=bound_case; seen=0
    def fail(actual):
        nonlocal seen
        if actual==point:
            seen+=1
            if seen==2:raise RuntimeError('resolution-write-cut')
    b.r._fault=fail
    with pytest.raises(Exception):_run(b)
    raw=b.x.store.export_bytes()
    _reopen(b)
    outcome,done=b.r.recover_pending(expected_checkpoint=b.r.snapshot().checkpoint)
    assert outcome==('NO_PENDING' if point.endswith('after_commit') else 'COMMITTED')
    assert done.pending_plan_sha256 is None and done.pins==b.x.store.snapshot().pins
    assert b.x.store.export_bytes()==raw and b.x.store.snapshot().transaction_count==6


@pytest.mark.parametrize('bad',['missing_old','same_id_changed','wrong_rub','timeout','clock'])
def test_bad_collection_does_not_prepare_or_change_pins(bound_case,bad):
    b=bound_case; rows=deepcopy(b.x.old_rows)+[_operation(b.x)]
    before=b.r.export_bytes(),b.x.store.export_bytes()
    kw={}
    if bad=='missing_old':rows.pop(0)
    elif bad=='same_id_changed':rows[1]['payment']=money('-0.333')
    elif bad=='wrong_rub':kw['cash_override']=998948565432110
    elif bad=='timeout':kw['transform']=lambda *args:(_ for _ in ()).throw(TimeoutError())
    else:kw['clock']=lambda:6_000_000_000
    with pytest.raises(Exception):_run(b,rows,**kw)
    assert (b.r.export_bytes(),b.x.store.export_bytes())==before


def test_pending_never_invokes_new_reads_on_repeated_sync(bound_case):
    b=bound_case;b.r._fault=_die('sync.after_prepare')
    with pytest.raises(Exception):_run(b)
    b.r._fault=None;before=b.r.export_bytes(),b.x.store.export_bytes()
    with pytest.raises(b.m.SourceBindingError,match='PENDING'):
        _run(b,transform=lambda *args:pytest.fail('unexpected read'))
    assert (b.r.export_bytes(),b.x.store.export_bytes())==before


def test_foreign_commit_after_prepared_blocks_recovery(bound_case):
    b=bound_case;b.r._fault=_die('sync.after_prepare')
    with pytest.raises(Exception):_run(b)
    b.r._fault=None
    _sync(b.x,b.x.old_rows+[_operation(b.x,'other','INPUT','200')])
    before=b.r.export_bytes(),b.x.store.export_bytes()
    with pytest.raises(b.m.SourceBindingError,match='DIVERGED'):
        b.r.recover_pending(expected_checkpoint=b.r.snapshot().checkpoint)
    assert (b.r.export_bytes(),b.x.store.export_bytes())==before
    assert b.r.snapshot().pending_plan_sha256 is not None


def test_more_than_one_committed_batch_not_blindly_adopted(bound_case):
    b=bound_case;b.r._fault=_die('sync.after_source_commit')
    with pytest.raises(Exception):_run(b)
    b.r._fault=None
    rows=b.x.old_rows+[_operation(b.x),_operation(b.x,'second','INPUT','200',at=_later(b.x.end))]
    _sync(b.x,rows,end=_later(b.x.end,2))
    before=b.r.export_bytes(),b.x.store.export_bytes()
    with pytest.raises(b.m.SourceBindingError,match='DIVERGED'):
        b.r.recover_pending(expected_checkpoint=b.r.snapshot().checkpoint)
    assert (b.r.export_bytes(),b.x.store.export_bytes())==before


def test_unregistered_source_write_makes_binding_stale(bound_case):
    b=bound_case;_sync(b.x,b.x.old_rows+[_operation(b.x)])
    with pytest.raises(Exception):
        with b.r.locked_binding():pass
    with pytest.raises(Exception):b.r.recover_pending(expected_checkpoint=b.initial.checkpoint)
    assert b.r.snapshot().pins==b.initial.pins


def test_new_rescan_records_window_but_no_money(bound_case):
    b=bound_case;_run(b)
    one=b.r.snapshot();before=b.x.store.snapshot()
    result,two=_run(b,end=_later(b.x.end,2))
    assert result.appended_transactions==result.appended_observations==0
    assert two.checkpoint.sequence==4 and two.pins.store_revision==one.pins.store_revision+1
    assert two.pins.ledger_revision==one.pins.ledger_revision
    assert b.x.store.snapshot().cash_nano==before.cash_nano


def test_two_successive_batches_and_reopen_keep_chain(bound_case):
    b=bound_case;_run(b);cp=b.r.snapshot().checkpoint;_reopen(b,checkpoint=cp)
    rows=b.x.old_rows+[_operation(b.x),_operation(b.x,'out','OUTPUT','-25',at=_later(b.x.end))]
    result,state=_run(b,rows,end=_later(b.x.end,2))
    assert result.cash_delta_nano==-25*10**9 and state.checkpoint.sequence==4
    assert b.x.store.snapshot().cash_nano==999023565432110
    assert b.x.store.snapshot().transaction_count==7


@pytest.mark.parametrize('target',['runtime','owner','root','source_copy','key'])
def test_reopen_rejects_identity_substitution(bound_case,target,tmp_path):
    b=bound_case;args=dict(source=b.x.store,checkpoint=b.initial.checkpoint,
        runtime_scope_sha256=b.runtime,owner_binding_sha256=b.owners)
    if target=='runtime':args['runtime_scope_sha256']='a'*64
    elif target=='owner':args['owner_binding_sha256']='b'*64
    elif target=='root':args['checkpoint']=replace(b.initial.checkpoint,root_sha256='c'*64)
    elif target=='key':b.x.store._key=b'OTHER-TEST-KEY'*3
    else:
        path=tmp_path/'other-source'
        v4.restore_operational_export(b.x.store.export_bytes(),path,pins=b.initial.pins,**b.x.c.y.common)
        args['source']=v4.VersionedOperationalStore.open(path,**b.x.c.y.common)
    try:
        with pytest.raises(Exception):b.m.DurableSourceBinding.open(b.r.root,**args)
    finally:
        if target=='source_copy':args['source'].close()
        if target=='key':b.x.store._key=b.x.c.y.common['identity_key']


def test_root_and_registry_are_immutable_and_tampering_rejected(bound_case):
    b=bound_case;_run(b)
    db=b.r.root/'store.sqlite3'
    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.DatabaseError):conn.execute('UPDATE binding_root SET canonical=?',(b'{}',))
        with pytest.raises(sqlite3.DatabaseError):conn.execute('DELETE FROM binding_event')
        conn.execute('DROP TRIGGER event_no_update')
    with pytest.raises(b.m.SourceBindingError,match='SCHEMA'):b.r.snapshot()


def test_re_signed_wrong_prepared_pins_rejected(bound_case):
    b=bound_case;b.r._fault=_die('sync.after_prepare')
    with pytest.raises(Exception):_run(b)
    from trading_robot.versioned_fee_evidence import _sealed, _sha
    with sqlite3.connect(b.r.root/'store.sqlite3') as conn:
        body=json.loads(bytes(conn.execute('SELECT canonical FROM binding_event').fetchone()[0]))['payload']
        body['after_pins']['ledger_revision']+=1
        raw=_sealed(body,b.x.store._key)
        conn.execute('DROP TRIGGER event_no_update')
        conn.execute('UPDATE binding_event SET canonical=?,sha256=?',(raw,_sha(raw)))
        conn.execute(next(s for s in b.m._SQL if s.startswith('CREATE TRIGGER event_no_update')))
    with pytest.raises(Exception):b.r.snapshot()
    assert b.x.store.snapshot().pins==b.initial.pins


def test_exact_expected_registry_checkpoint_is_cas(bound_case):
    b=bound_case;_run(b);before=b.r.export_bytes(),b.x.store.export_bytes()
    with pytest.raises(b.m.SourceBindingError,match='CAS'):_run(b,checkpoint=b.initial.checkpoint)
    with pytest.raises(b.m.SourceBindingError,match='CAS'):b.r.recover_pending(expected_checkpoint=b.initial.checkpoint)
    assert (b.r.export_bytes(),b.x.store.export_bytes())==before


def test_external_checkpoint_detects_whole_registry_rollback(bound_case,tmp_path):
    b=bound_case;old=tmp_path/'old.sqlite3'
    with sqlite3.connect(b.r.root/'store.sqlite3') as src,sqlite3.connect(old) as dst:src.backup(dst)
    _run(b);latest=b.r.snapshot().checkpoint
    with sqlite3.connect(old) as src,sqlite3.connect(b.r.root/'store.sqlite3') as dst:src.backup(dst)
    with pytest.raises(b.m.SourceBindingError,match='ROLLBACK'):
        _reopen(b,reopen_source=False,checkpoint=latest)


def test_pending_lock_and_live_view_real_sqlite(bound_case):
    b=bound_case
    with b.r.locked_binding() as (a,view):
        with sqlite3.connect(b.x.root/'store.sqlite3',timeout=0,isolation_level=None) as conn:
            with pytest.raises(sqlite3.OperationalError):conn.execute('BEGIN IMMEDIATE')
        other=b.m.DurableSourceBinding(b.r.root,b.x.store,checkpoint=b.initial.checkpoint,
            runtime_scope_sha256=b.runtime,owner_binding_sha256=b.owners)
        with pytest.raises(b.m.SourceBindingError,match='LOCKED'):other.snapshot()
        with pytest.raises(b.m.SourceBindingError,match='REENTRANT'):b.r.snapshot()
        assert a.pins==b.initial.pins
    with pytest.raises(v4.OperationalStoreError):view.assert_active()
    assert b.r.snapshot()==b.initial


def test_resolution_pins_written_while_source_writer_locked(bound_case):
    b=bound_case;points=[]
    def check(where):
        if where=='resolution.before_record':
            with sqlite3.connect(b.x.root/'store.sqlite3',timeout=0,isolation_level=None) as conn:
                with pytest.raises(sqlite3.OperationalError):conn.execute('BEGIN IMMEDIATE')
            points.append(where)
    b.r._fault=check;_run(b)
    assert points==['resolution.before_record']


@pytest.mark.parametrize('bad',['existing','overlap','symlink','missing'])
def test_registry_path_no_overwrite_or_implicit_create(bound_case,bad,tmp_path):
    b=bound_case;root=tmp_path/'new-registry'
    if bad=='existing':root=b.r.root
    elif bad=='overlap':root=b.x.root/'nested'
    elif bad=='symlink':root.symlink_to(b.r.root,target_is_directory=True)
    if bad=='missing':
        with pytest.raises(Exception):b.m.DurableSourceBinding.open(root,source=b.x.store,
            checkpoint=b.initial.checkpoint,runtime_scope_sha256=b.runtime,owner_binding_sha256=b.owners)
        assert not root.exists()
    else:
        with pytest.raises(Exception):b.m.DurableSourceBinding.create(root,source=b.x.store,
            expected_pins=b.initial.pins,runtime_scope_sha256=b.runtime,owner_binding_sha256=b.owners,created_at=b.x.end)
    assert b.r.snapshot()==b.initial


@pytest.mark.parametrize('point',['create.before_commit','create.after_commit','create.before_promote','create.after_promote'])
def test_create_failure_does_not_modify_source(bound_case,point,tmp_path):
    b=bound_case;root=tmp_path/'new-registry';source=b.x.store.export_bytes()
    with pytest.raises(RuntimeError):b.m.DurableSourceBinding.create(root,source=b.x.store,
        expected_pins=b.initial.pins,runtime_scope_sha256=b.runtime,owner_binding_sha256=b.owners,
        created_at=b.x.end,fault_injector=_die(point))
    assert b.x.store.export_bytes()==source
    assert root.exists()==(point=='create.after_promote')
    assert not root.with_name(root.name+'.binding-staging').exists()


def test_public_summary_and_no_runtime_authority(bound_case):
    b=bound_case;summary=b.r.snapshot().public_summary();text=json.dumps(summary)
    assert b.x.c.y.a.policy.account_id not in text and str(b.x.root) not in text
    assert 'cash_nano' not in text and summary['runtime_authority_granted'] is False
    with pytest.raises(b.m.SourceBindingError,match='CUTOVER'):b.r.require_runtime_authority()


@pytest.mark.parametrize('action',['sync','recover'])
def test_checkpoint_cannot_be_spoofed_by_equality_object(bound_case,action):
    b=bound_case
    class AlwaysEqual:
        def __eq__(self, other):return True
    before=b.r.export_bytes(),b.x.store.export_bytes()
    with pytest.raises(b.m.SourceBindingError,match='CHECKPOINT_INVALID'):
        if action=='sync':_run(b,checkpoint=AlwaysEqual())
        else:b.r.recover_pending(expected_checkpoint=AlwaysEqual())
    assert (b.r.export_bytes(),b.x.store.export_bytes())==before
