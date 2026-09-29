"""STEP27: real SQLite lease and request-bound integration prerequisite.

Synthetic provider / temporary stores. Candidate construction below is a
structured financial-binding test, NOT a newly admitted/posted v4 trade.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_financial_readers import read_case, _args
from test_q7a_source_versioned_operational_store import op_case, _operation, _sync, _request, _later
from test_q7a_source_settlement_closure import _snapshot
from trading_robot.central_order_manager import CentralOrderIntent
from trading_robot import versioned_operational_store as v4
from trading_robot.runtime_cash_authority import CL7RuntimeError, LockedDispatchProof


def module():
    return importlib.import_module('trading_robot.versioned_runtime_adapter')


def adapter(x):
    return module().VersionedRuntimeStoreAdapter(x.store, x.store.snapshot().pins)


def queued_inputs(x, direction='BUY', reserve=106050):
    """Real domain objects/evidence; the candidate is explicitly test input."""
    old = x.c.y.intent
    c = replace(old.candidate, candle_time=_later(x.end), created_at=_later(x.end),
                current_lots=0 if direction=='BUY' else 1,
                target_lots=1 if direction=='BUY' else 0)
    auth = replace(old.authorization, authorized_target_lots=c.target_lots,
        portfolio_risk=replace(old.authorization.portfolio_risk, approved_target_lots=c.target_lots))
    i = CentralOrderIntent.create(c, auth, queue_sequence=2,
        reserved_cash_kopecks=reserve if direction=='BUY' else 0, created_at=_later(x.end))
    state = replace(x.c.y.a.manager.state(), revision=10, next_sequence=3, intents=(i,))
    cash = x.store.snapshot().cash_nano
    positions = x.c.y.x.p.get_positions(c.account_id)
    positions['money']=[money(Decimal(cash)/10**9)]
    def limits(account, uid, price=None):
        return {'currency':'rub','buyLimits': {'buyMoneyAmount':money(Decimal(cash)/10**9),
                                               'buyMaxMarketLots':'100'},
                'sellLimits':{'sellMaxLots':'100'}}
    provider = SimpleNamespace(get_positions=lambda _:positions, get_max_lots=limits)
    now = _later(x.end)
    policy = x.c.y.a.cl7_own_funds_policy
    funds = policy.acquire(provider, i, state, clock=lambda:now, monotonic_ns=lambda:1)
    data = _args(x.c, positions=positions, budgets=(cash,cash), state=state, time=now)
    # Align the structured test authorization with real current evidence.
    # This is not execution of a fresh Risk admission; that is outside STEP27.
    port, risk = data['portfolio'], data['risk_guard']
    auth = replace(auth, portfolio_revision=port.portfolio_revision,
        portfolio_document_checksum=port.portfolio_document_checksum,
        portfolio_decision_checksum=port.portfolio_decision_checksum,
        risk_policy_hash=risk.risk_policy_hash, risk_state_guard_hash=risk.risk_state_guard_hash,
        authorized_at=now, portfolio_risk=replace(auth.portfolio_risk,
            snapshot_revision=port.portfolio_revision, snapshot_checksum=port.portfolio_decision_checksum,
            risk_state_guard_hash=risk.risk_state_guard_hash))
    i = replace(i, authorization=auth)
    state = replace(state, intents=(i,))
    inputs = {k:data[k] for k in ['broker_cash','own_buying','portfolio','risk_guard','buying_scope_sha256','evaluated_at']}
    inputs.update(intent=i, central_state=state,
        expected_intent_sha256=sha256(json.dumps(i.to_dict(),sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')).hexdigest(),
        own_policy=policy, own_funds=funds)
    return inputs


@pytest.mark.parametrize('direction',['BUY','SELL'])
def test_core_request_binding_uses_corrected_cash_under_real_cl7_guard(op_case,direction,request):
    x=op_case;m=module();a=adapter(x);args=queued_inputs(x,direction)
    before=_snapshot(x.c.y.x),x.store.export_bytes()
    authority=x.c.y.a.cash_authority_manager
    with authority.ledger_guard(a) as view:
        assert type(view) is v4.LockedOperationalView
        conn=sqlite3.connect(x.root/'store.sqlite3',timeout=0,isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError):conn.execute('BEGIN IMMEDIATE')
        finally:conn.close()
        binding=a.build_request_binding(view,**args)
        assert a.validate_request_binding(binding,view,now=args['evaluated_at'],**args)==binding
        body=json.loads(binding.payload_bytes)
        assert body['pins']==a.pins.to_dict()
        assert body['source_export_version']==4 and body['runtime_authority_granted'] is False
        assert body['risk_admission_performed'] is False
        context=json.loads(body['context_json_ascii'])['payload']
        assert context['expected_cash_nano']=='998948565432110'
        assert context['queued_reserved_nano']==str(args['intent'].reserved_cash_kopecks*10**7)
        expect=998948565432110-args['intent'].reserved_cash_kopecks*10**7
        assert body['free_cash_nano']==str(expect)
        with pytest.raises(m.VersionedRuntimeBindingError,match='CUTOVER'):
            a.require_runtime_authority(binding)
        with pytest.raises(CL7RuntimeError):
            authority.build_locked_dispatch_proof(context=binding,authority_record=authority.status(),
                raw_intent_id=args['intent'].intent_id,identity_key=x.c.y.a.cl7_identity_key,
                reserved_cash=__import__('trading_robot.cash_ledger_domain',fromlist=['Money']).Money('RUB',0),
                current_lots=0,target_lots=1,direction='BUY',evaluated_at=args['evaluated_at'])
        with pytest.raises(Exception):LockedDispatchProof.from_canonical_dict(json.loads(binding.canonical_bytes))
    assert (_snapshot(x.c.y.x),x.store.export_bytes())==before
    assert x.c.y.x.p.order_calls==1
    request.node.user_properties.extend([('direction',direction),('corrected_cash_nano',998948565432110),
        ('free_cash_nano',expect),('writer_lock_verified',True),('runtime_cutover',False),('fake_posts',1)])


def test_sync_then_new_explicit_pins_and_request_binding(op_case,request):
    x=op_case;m=module();a=adapter(x)
    old=a.pins;row=_operation(x)
    def cash(_):
        p=x.c.y.x.p.get_positions(x.c.y.a.policy.account_id)
        p['money']=[money(Decimal(998948565432110+100*10**9)/10**9)];return p
    result=a.sync_tbank_operations(_request(x,x.old_rows+[row]),recorded_at=_later(x.end),read_rub_positions=cash)
    assert a.pins==old
    with pytest.raises(v4.OperationalStoreError):a.snapshot()
    # In production the caller must first persist trusted pins separately.
    next_adapter=m.VersionedRuntimeStoreAdapter(x.store,result.pins)
    args=queued_inputs(x)
    with next_adapter.locked_snapshot() as view:
        binding=next_adapter.build_request_binding(view,**args)
        assert json.loads(binding.payload_bytes)['pins']==result.pins.to_dict()
        assert view.project_cash().expected_cash_nano==999048565432110
    assert x.c.y.x.p.order_calls==1
    request.node.user_properties.extend([('after_input_cash_nano',999048565432110),('silent_pin_adoption',False)])


def test_lease_expired_after_exit_and_not_reusable_in_new_guard(op_case):
    x=op_case;m=module();a=adapter(x);args=queued_inputs(x)
    with a.locked_snapshot() as view:binding=a.build_request_binding(view,**args)
    for f in [view.snapshot,view.export_bytes,view.project_cash]:
        with pytest.raises(v4.OperationalStoreError,match='VIEW_NOT_ACTIVE'):f()
    with pytest.raises(m.VersionedRuntimeBindingError):a.build_request_binding(view,**args)
    with a.locked_snapshot() as other:
        with pytest.raises(m.VersionedRuntimeBindingError):
            a.validate_request_binding(binding,other,now=args['evaluated_at'],**args)


@pytest.mark.parametrize('field',['export_sha256','seed_export_sha256','batch_head_sha256','store_revision','ledger_revision','ledger_head_sha256'])
def test_independent_pins_all_checked_before_lease(op_case,field):
    x=op_case;a=adapter(x);p=a.pins
    bad=replace(p,**{field:'a'*64 if field.endswith('sha256') else getattr(p,field)+1})
    before=x.store.export_bytes()
    with pytest.raises(Exception):
        with x.store.locked_snapshot(expected_pins=bad):pytest.fail('must not yield')
    assert x.store.export_bytes()==before


def test_reader_does_not_freeze_writers_but_live_view_does(op_case):
    x=op_case;a=adapter(x);rows=x.old_rows+[_operation(x)]
    raw=a.export_bytes()
    with a.locked_snapshot() as view:
        assert view.export_bytes()==raw
        with pytest.raises(v4.OperationalStoreError):_sync(x,rows)
        assert view.export_bytes()==raw
        with pytest.raises(v4.OperationalStoreError):
            with a.locked_snapshot():pytest.fail('nested writer must fail')
    assert _sync(x,rows).appended_transactions==1
    with pytest.raises(Exception):a.export_bytes()


def test_body_exception_releases_writer_without_changes(op_case):
    x=op_case;a=adapter(x);raw=a.export_bytes()
    with pytest.raises(RuntimeError,match='test failure'):
        with a.locked_snapshot() as view:raise RuntimeError('test failure')
    with pytest.raises(v4.OperationalStoreError):view.snapshot()
    assert a.export_bytes()==raw
    assert _sync(x,x.old_rows+[_operation(x)]).appended_transactions==1


@pytest.mark.parametrize('tick',[True,-1,5_000_000_001])
def test_lease_monotonic_expiry_and_backwards_clock(op_case,tick):
    x=op_case;a=adapter(x);clock=[0];raw=a.export_bytes()
    with pytest.raises(v4.OperationalStoreError):
        with a.locked_snapshot(monotonic_ns=lambda:clock[0]) as view:
            clock[0]=tick;view.export_bytes()
    assert a.export_bytes()==raw


def test_different_thread_cannot_use_live_view(op_case):
    from concurrent.futures import ThreadPoolExecutor
    x=op_case;a=adapter(x)
    with a.locked_snapshot() as view:
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(v4.OperationalStoreError,match='VIEW_NOT_ACTIVE'):
                pool.submit(view.export_bytes).result()


def test_direct_v4_store_not_silently_accepted_by_legacy_cl7_guard(op_case):
    x=op_case
    with pytest.raises(CL7RuntimeError,match='VERSIONED_PINNED_ADAPTER_REQUIRED'):
        with x.c.y.a.cash_authority_manager.ledger_guard(x.store):pytest.fail('raw store not admitted')


@pytest.mark.parametrize('damage',['wrong_intent_pin','changed_request','wrong_reservation','wrong_metadata',
    'own_cash','blocked_cash','own_request_hash','own_lot_size','own_direction','own_estimated_price',
    'blocked_central','wrong_queue_head','wrong_scope','stale_owner','frozen_cash','wrong_key','bool_evidence',
    'authorization_time','authorization_document','authorization_risk','positions_response','bestprice'])
def test_binding_rejects_mismatched_request_cash_and_owner_inputs(op_case,damage):
    x=op_case;m=module();a=adapter(x);d=queued_inputs(x)
    if damage=='wrong_intent_pin':d['expected_intent_sha256']='a'*64
    elif damage=='changed_request':
        c=replace(d['intent'].candidate,estimated_price_kopecks=d['intent'].candidate.estimated_price_kopecks+1)
        d['intent']=CentralOrderIntent.create(c,d['intent'].authorization,queue_sequence=2,
            reserved_cash_kopecks=d['intent'].reserved_cash_kopecks,created_at=_later(x.end))
    elif damage=='wrong_reservation':d['own_funds']=replace(d['own_funds'],all_local_reservations_nano=d['own_funds'].all_local_reservations_nano+1)
    elif damage=='wrong_metadata':
        row=d['own_policy'].instruments[d['intent'].candidate.instrument_id]
        rows=dict(d['own_policy'].instruments);rows[row.instrument_id]=replace(row,lot_size=row.lot_size+1)
        d['own_policy']=replace(d['own_policy'],instruments=rows)
    elif damage=='own_cash':d['own_funds']=replace(d['own_funds'],rub_position_nano=d['own_funds'].rub_position_nano+1)
    elif damage=='blocked_cash':d['own_funds']=replace(d['own_funds'],broker_blocked_nano=1)
    elif damage=='own_request_hash':d['own_funds']=replace(d['own_funds'],request_sha256='a'*64)
    elif damage=='own_lot_size':d['own_funds']=replace(d['own_funds'],lot_size=11)
    elif damage=='own_direction':
        d['own_funds']=replace(d['own_funds'],direction='SELL',own_reservation_nano=0)
    elif damage=='own_estimated_price':d['own_funds']=replace(d['own_funds'],estimated_price_kopecks=99)
    elif damage=='blocked_central':
        i=d['intent'].transition('IN_FLIGHT',detail='synthetic attempt',at=_later(x.end,2))
        d['central_state']=replace(d['central_state'],intents=(i,))
    elif damage=='wrong_queue_head':d['central_state']=replace(d['central_state'],intents=())
    elif damage=='wrong_scope':d['buying_scope_sha256']='a'*64
    elif damage=='stale_owner':d['evaluated_at']=_later(d['evaluated_at'],6)
    elif damage=='frozen_cash':
        cash=x.c.y.x.p.get_positions(x.c.y.a.policy.account_id);cash['money']=[money(Decimal(998948641975321)/10**9)]
        d['broker_cash']=_args(x.c,positions=cash,time=d['evaluated_at'])['broker_cash']
    elif damage=='wrong_key':d['broker_cash']=replace(d['broker_cash'],proof_identity_sha256='a'*64)
    elif damage=='authorization_time':
        d['intent']=replace(d['intent'],authorization=replace(d['intent'].authorization,authorized_at=_later(x.end,-20)))
    elif damage=='authorization_document':
        d['intent']=replace(d['intent'],authorization=replace(d['intent'].authorization,portfolio_document_checksum='a'*64))
    elif damage=='authorization_risk':
        auth=d['intent'].authorization
        d['intent']=replace(d['intent'],authorization=replace(auth,risk_state_guard_hash='a'*64,
            portfolio_risk=replace(auth.portfolio_risk,risk_state_guard_hash='a'*64)))
    elif damage=='bestprice':
        c=replace(d['intent'].candidate,order_type='BESTPRICE')
        d['intent']=CentralOrderIntent.create(c,d['intent'].authorization,queue_sequence=2,
            reserved_cash_kopecks=d['intent'].reserved_cash_kopecks,created_at=_later(x.end))
        d['expected_intent_sha256']=m.intent_fingerprint(d['intent'])
        d['central_state']=replace(d['central_state'],intents=(d['intent'],))
    elif damage=='positions_response':d['own_funds']=replace(d['own_funds'],positions_sha256='a'*64)
    else:d['own_funds']=True
    if damage.startswith('authorization_'):
        d['expected_intent_sha256']=m.intent_fingerprint(d['intent'])
        d['central_state']=replace(d['central_state'],intents=(d['intent'],))
    raw=a.export_bytes()
    with a.locked_snapshot() as view:
        with pytest.raises(m.VersionedRuntimeBindingError):a.build_request_binding(view,**d)
    assert a.export_bytes()==raw and x.c.y.x.p.order_calls==1


@pytest.mark.parametrize('mutation',['flag','cash','pins','lease','signature','context'])
def test_proof_tampering_and_resigning_do_not_bypass_semantic_validation(op_case,mutation):
    import hmac,hashlib
    from trading_robot.versioned_fee_evidence import _canonical
    x=op_case;m=module();a=adapter(x);d=queued_inputs(x)
    with a.locked_snapshot() as view:
        b=a.build_request_binding(view,**d);body=json.loads(b.payload_bytes)
        if mutation=='flag':body['runtime_authority_granted']=True
        elif mutation=='cash':body['free_cash_nano']=str(int(body['free_cash_nano'])+1)
        elif mutation=='pins':body['pins']['ledger_revision']+=1
        elif mutation=='lease':body['lease_id']='a'*64
        elif mutation=='context':body['context_json_ascii']='{}'
        raw=_canonical(body)
        signature='a'*64 if mutation=='signature' else hmac.new(x.store._key,raw,hashlib.sha256).hexdigest()
        tampered=m.VersionedRequestBinding(raw,signature)
        with pytest.raises(m.VersionedRuntimeBindingError):
            a.validate_request_binding(tampered,view,now=d['evaluated_at'],**d)


def test_expiry_does_not_renew_underlying_evidence(op_case):
    x=op_case;m=module();a=adapter(x);d=queued_inputs(x)
    with a.locked_snapshot() as view:
        b=a.build_request_binding(view,**d)
        with pytest.raises(m.VersionedRuntimeBindingError):
            a.validate_request_binding(b,view,now=_later(d['evaluated_at'],6),**d)
        with pytest.raises(m.VersionedRuntimeBindingError):
            a.validate_request_binding(b,view,now=_later(d['evaluated_at'],-1),**d)
        with pytest.raises(m.VersionedRuntimeBindingError,match='TIME_REBIND'):
            a.validate_request_binding(b,view,now=_later(d['evaluated_at'],1),**dict(d,evaluated_at=_later(d['evaluated_at'],1)))


def test_owned_view_is_readonly_even_for_cooperative_private_mutation(op_case):
    x=op_case;a=adapter(x);raw=a.export_bytes()
    with pytest.raises(v4.OperationalStoreError):
        with a.locked_snapshot() as view:
            # Test a caller with private connection access. Schema/prefix drift
            # is detected; rollback removes this uncommitted table.
            view._connection.execute('CREATE TABLE unexpected_table(x)')
    assert a.export_bytes()==raw


def test_escaped_connection_commit_invalidates_view(op_case):
    x=op_case;a=adapter(x);raw=a.export_bytes()
    with pytest.raises(v4.OperationalStoreError,match='TRANSACTION_LOST'):
        with a.locked_snapshot() as view:
            view._connection.execute('COMMIT');view.snapshot()
    assert a.export_bytes()==raw


def test_all_queued_reserves_are_counted_once_not_just_selected_request(op_case):
    x=op_case;a=adapter(x);d=queued_inputs(x)
    first=d['intent'];c=replace(first.candidate,candle_time=_later(x.end,-1))
    second=CentralOrderIntent.create(c,first.authorization,queue_sequence=3,
        reserved_cash_kopecks=20000,created_at=_later(x.end))
    d['central_state']=replace(d['central_state'],intents=(first,second),next_sequence=4)
    d['own_funds']=replace(d['own_funds'],all_local_reservations_nano=(106050+20000)*10**7)
    with a.locked_snapshot() as view:
        binding=a.build_request_binding(view,**d);body=json.loads(binding.payload_bytes)
        context=json.loads(body['context_json_ascii'])['payload']
        assert context['queued_reserved_nano']==str((106050+20000)*10**7)
        assert body['free_cash_nano']==str(998948565432110-(106050+20000)*10**7)


def test_specific_own_money_caps_cash_without_a_second_reserve_subtraction(op_case):
    x=op_case;a=adapter(x);d=queued_inputs(x)
    d['own_funds']=replace(d['own_funds'],own_money_nano=1100*10**9)
    with a.locked_snapshot() as view:
        binding=a.build_request_binding(view,**d);body=json.loads(binding.payload_bytes)
        assert body['free_cash_nano']==str(39_500_000_000)
        assert body['reserved_cash_nano']==str(1060_500_000_000)
