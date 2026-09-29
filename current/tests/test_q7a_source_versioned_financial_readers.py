"""STEP25 versioned financial readers. All provider IO is synthetic."""
from __future__ import annotations

import importlib
import json
from dataclasses import fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_fee_consumer import _capture, _register_and_create
from test_q7a_source_settlement_closure import _snapshot
from trading_robot import cash_availability as cl5
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import reporting_risk_cash_context as cl6
from trading_robot.broker_read_adapters import BrokerEnvironment
from trading_robot.portfolio_preflight import PortfolioSnapshotLease


def _modules():
    return (importlib.import_module('trading_robot.versioned_financial_readers'),
            importlib.import_module('trading_robot.versioned_financial_capture'))


@pytest.fixture
def read_case(exact_case, request):
    from test_q7a_source_fee_replacement import _setup
    from test_q7a_source_cash_components import _export
    from trading_robot import cash_observation_versions as versions
    from hashlib import sha256
    x=exact_case
    intent, raw, trade, fee, proof, target=_setup(x, amount=getattr(request,'param','0.2'))
    fee['id']=x.old_fee['id'];fee['cursor']=x.old_fee['cursor']
    # Select a same-day fixture BEFORE review migration/capture. Source owner
    # timestamps remain the actual closure timestamps, never rewritten.
    x.p.clock_at-=timedelta(days=1)
    a=x.c.execution_adapter
    common=dict(codec_registry=tuple(a.cl7_ledger_store._registry.values()),
        identity_key=a.cl7_identity_key,identity_key_id=a.cl7_identity_key_id,
        account_scope_sha256=a.cash_authority_manager.status().account_scope_sha256)
    root=a.manager.store.path.parent/'version-review'
    versions.copy_v1_for_revision_review(a.cl7_ledger_store.root,root,**common,
        expected_source_export_sha256=sha256(_export(x)).hexdigest(),
        created_at=stamp(x.p.clock_at-timedelta(seconds=1)))
    review=versions.ObservationVersionStore.open(root,**common)
    y=SimpleNamespace(x=x,a=a,intent=intent,raw=raw,trade=trade,fee=fee,proof=proof,
        target=target,common=common,review=review,root=root,recovery=x.c.cycle_source.portfolio_recovery)
    try:
        c = _capture(y)
        store, args = _register_and_create(y, c)
        try:
            store.append_verified_fee_revision(c.capture, **args)
            yield SimpleNamespace(y=y, store=store, captured=c)
        finally:
            store.close()
    finally:
        y.review.close()


def _pins(case):
    m, _ = _modules()
    s = case.store.snapshot()
    return m.VersionedReadPins(**{f.name: getattr(s, f.name) for f in fields(m.VersionedReadPins)})


def _args(case, *, positions=None, budgets=None, state=None, time=None, portfolio_state=None):
    y = case.y; a = y.a; x = y.x
    now = time or a.cl7_clock()
    common = dict(identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        account_scope_sha256=a.cash_authority_manager.status().account_scope_sha256,
        environment=BrokerEnvironment.SANDBOX)
    raw = positions if positions is not None else x.p.get_positions(a.policy.account_id)
    cash = cl4.build_broker_rub_position_cash_proof(raw, raw_account_id=a.policy.account_id,
        as_of=now, evaluated_at=now, response_complete=True, **common)
    policy = a.cash_authority_manager.buying_budget_policy
    provider = x.p
    if budgets is not None:
        names = sorted(policy.instruments)
        provider = SimpleNamespace(get_max_lots=lambda account, uid, price=None:
            {'currency':'rub', 'buyLimits':{'buyMoneyAmount':money(Decimal(budgets[names.index(uid)])/10**9)},
             'buyMarginLimits':{'buyMoneyAmount':money(10_000_000)}})
    own = policy.acquire(provider, account_scope_sha256=common['account_scope_sha256'],
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        clock=lambda: now, monotonic_ns=lambda: 1)
    reservations = cl5.project_central_reservations(state or a.manager.state(), evaluated_at=now, **common)
    p = portfolio_state or y.recovery.manager.repository.load(expected_account_id=a.policy.account_id)
    port = cl6.build_portfolio_identity_evidence(PortfolioSnapshotLease.from_state(p, leased_at=datetime.fromisoformat(now.replace("Z", "+00:00")).isoformat()),
        evaluated_at=now, **common)
    risk = cl6.build_risk_guard_evidence(y.recovery.risk.profile_store.load_profile(y.recovery.risk.mode)['policy'],
        y.recovery.risk.state_store.load_account(a.policy.account_id), raw_account_id=a.policy.account_id,
        captured_at=now, evaluated_at=now, **common)
    return dict(pins=_pins(case), codec_registry=y.common['codec_registry'],
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        account_scope_sha256=common['account_scope_sha256'], broker_cash=cash, own_buying=own,
        reservations=reservations, portfolio=port, risk_guard=risk,
        buying_scope_sha256=policy.scope_sha256, evaluated_at=now)


def _build(case, **kwargs):
    m,_ = _modules()
    return m.build_versioned_cash_context(case.store.export_bytes(), **_args(case,**kwargs))


@pytest.mark.parametrize('read_case',['0.2','0.001'],indirect=True)
def test_corrected_projection_not_frozen_cash(read_case, request):
    m,_ = _modules(); c=read_case; a=c.y.a
    before=_snapshot(c.y.x); base_export=c.y.review.export_bytes()
    p=m.project_versioned_cash(c.store.export_bytes(), pins=_pins(c), **c.y.common)
    assert p.baseline_cash_nano==998948641975321
    assert p.expected_cash_nano==c.store.snapshot().cash_nano
    assert p.correction_delta_nano==123456789-int(Decimal(c.y.raw['executedCommission']['units'])*10**9+Decimal(c.y.raw['executedCommission']['nano']))
    report=_build(c); body=json.loads(report.payload_bytes)
    assert body['cl4_status']=='MATCHED'
    assert body['status']=='CONSISTENT_REVIEW_ONLY', body
    assert body['free_cash_nano']==str(p.expected_cash_nano)
    assert body['runtime_authority_granted'] is False
    assert body['cl7_status']=='BLOCKED_CUTOVER_REQUIRED'
    assert _snapshot(c.y.x)==before and c.y.review.export_bytes()==base_export
    request.node.user_properties.extend([('corrected_cash_nano',p.expected_cash_nano),
        ('frozen_cash_nano',p.baseline_cash_nano),('free_cash_nano',body['free_cash_nano']),
        ('runtime_unchanged',True),('fake_posts',c.y.x.p.order_calls)])


def test_real_capture_rechecks_economics_and_own_money_without_writes(read_case):
    m,bridge=_modules();c=read_case;y=c.y
    before=_snapshot(y.x); raw=c.store.export_bytes(); reads=len(y.x.p.cash_read_calls)
    result=bridge.capture_versioned_financial_review(y.a,recovery=y.recovery,journal_store=c.store,pins=_pins(c))
    body=json.loads(result.context.payload_bytes)
    assert body['status']=='CONSISTENT_REVIEW_ONLY',body
    assert body['current_economic_evidence_sha256']==result.primary_evidence_sha256
    assert result.projection.expected_cash_nano==998948565432110
    assert _snapshot(y.x)==before and c.store.export_bytes()==raw and y.x.p.order_calls==1
    assert y.x.p.cash_read_calls[reads:]==[('positions', y.a.policy.account_id),
        ('max_lots',y.a.policy.account_id,'uid-lkoh'),('max_lots',y.a.policy.account_id,'uid-sber')]
    with pytest.raises(m.VersionedFinancialReadError,match='CUTOVER'):
        m.require_versioned_runtime_authority(result.context)


@pytest.mark.parametrize('field', ['export_sha256','review_export_sha256','observed_version_head_sha256',
    'correction_head_sha256','ledger_head_sha256','ledger_revision','store_revision'])
def test_every_external_pin_is_required(read_case,field):
    m,_=_modules();c=read_case;p=_pins(c)
    bad=replace(p,**{field:'a'*64 if field.endswith('sha256') else getattr(p,field)+1})
    before=_snapshot(c.y.x),c.store.export_bytes()
    with pytest.raises(m.VersionedFinancialReadError):
        m.project_versioned_cash(c.store.export_bytes(),pins=bad,**c.y.common)
    assert (_snapshot(c.y.x),c.store.export_bytes())==before


@pytest.mark.parametrize('damage',['frozen_v1','review_v2','wrong_key','wrong_account','uncommitted'])
def test_unknown_or_unqualified_projection_is_not_accepted(read_case,damage):
    from hashlib import sha256
    m,_=_modules();c=read_case;raw=c.store.export_bytes();p=_pins(c);common=dict(c.y.common)
    if damage=='frozen_v1': raw=c.y.x.prior_export
    elif damage=='review_v2': raw=c.y.review.export_bytes()
    elif damage=='wrong_key': common['identity_key']=b'z'*32
    elif damage=='wrong_account': common['account_scope_sha256']='b'*64
    else:
        d=json.loads(raw);d['correction_record_json_ascii']=None
        raw=json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    p=replace(p,export_sha256=sha256(raw).hexdigest())
    with pytest.raises(m.VersionedFinancialReadError):m.project_versioned_cash(raw,pins=p,**common)


@pytest.mark.parametrize('change',[-1,1,'frozen'])
def test_balance_must_match_corrected_cash_to_one_nano(read_case,change):
    c=read_case;raw=c.y.x.p.get_positions(c.y.a.policy.account_id)
    amount=998948641975321 if change=='frozen' else c.store.snapshot().cash_nano+change
    raw['money']=[money(Decimal(amount)/10**9)]
    report=_build(c,positions=raw,budgets=(100*10**9,100*10**9));b=json.loads(report.payload_bytes)
    assert b['status']=='BLOCKED' and b['reason']=='VERSIONED_CASH_MISMATCH'
    assert b['free_cash_nano'] is None and b['cl4_status']=='MISMATCHED'


@pytest.mark.parametrize('amounts,expected',[(('80','60'),'60'),(('0','100'),'0'),
    (('0.000000001','100'),'0.000000001')])
def test_lower_own_limits_and_zero_are_not_ledger_errors(read_case,amounts,expected):
    c=read_case;ns=tuple(int(Decimal(n)*10**9) for n in amounts)
    b=json.loads(_build(c,budgets=ns).payload_bytes)
    assert b['status']=='CONSISTENT_REVIEW_ONLY' and b['cl4_status']=='MATCHED'
    assert int(b['free_cash_nano'])==int(Decimal(expected)*10**9)
    assert b['runtime_authority_granted'] is False


def test_own_limit_above_corrected_accounting_is_not_hidden_by_minimum(read_case):
    c=read_case
    b=json.loads(_build(c,budgets=(c.store.snapshot().cash_nano+1,10**9)).payload_bytes)
    assert b['reason']=='OWN_BUDGET_EXCEEDS_ACCOUNTING' and b['free_cash_nano'] is None


@pytest.mark.parametrize('status,reserved_kopecks,reason',[
    ('QUEUED',3000,'VALIDATED_FOR_REVIEW_ONLY'),('QUEUED',9000,'INSUFFICIENT_AFTER_RESERVATIONS'),
    ('IN_FLIGHT',0,'CENTRAL_PROVIDER_OVERLAP_UNKNOWN'),
    ('SUBMITTED',0,'CENTRAL_PROVIDER_OVERLAP_UNKNOWN'),('UNCERTAIN',0,'CENTRAL_PROVIDER_OVERLAP_UNKNOWN')])
def test_actual_reservation_projection_counts_queued_once_and_blocks_unknown(read_case,status,reserved_kopecks,reason):
    from test_v3_10_cash_availability import _state
    c=read_case;state=_state(status,reserved_kopecks=reserved_kopecks,account=c.y.a.policy.account_id)
    b=json.loads(_build(c,budgets=(80*10**9,100*10**9),state=state).payload_bytes)
    assert b['reason']==reason
    if status=='QUEUED' and reason=='VALIDATED_FOR_REVIEW_ONLY':
        assert b['free_cash_nano']==str(50*10**9)
    else: assert b['free_cash_nano'] is None


def test_two_queued_reservations_are_counted_once_each(read_case):
    from test_v3_10_cash_availability import _candidate,_authorization,_intent
    from trading_robot.central_order_manager import CentralOrderIntent,CentralOrderState
    c=read_case;account=c.y.a.policy.account_id
    first=_intent(account=account,reserved_kopecks=3000)
    second=CentralOrderIntent.create(replace(_candidate(account=account),instrument_id='uid-lkoh'),
        replace(_authorization(account=account),instrument_id='uid-lkoh'),queue_sequence=2,
        reserved_cash_kopecks=2000,created_at=first.created_at)
    state=CentralOrderState(account_id=account,revision=1,next_sequence=3,
        intents=(first,second),created_at=first.created_at,updated_at=first.created_at)
    b=json.loads(_build(c,budgets=(80*10**9,100*10**9),state=state).payload_bytes)
    assert b['queued_reserved_nano']==str(50*10**9) and b['free_cash_nano']==str(30*10**9)


@pytest.mark.parametrize('field', ['broker_cash','own_buying','reservations','portfolio','risk_guard','scope','account','key'])
def test_hmac_and_dependency_bindings_are_revalidated(read_case,field):
    m,_=_modules();c=read_case;args=_args(c)
    if field=='broker_cash': args[field]=replace(args[field],response_canonical_sha256='b'*64)
    elif field=='own_buying': args[field]=replace(args[field],own_money_nano=(1,1))
    elif field=='reservations': args[field]=replace(args[field],projection_identity_sha256='b'*64)
    elif field=='portfolio': args[field]=replace(args[field],portfolio_document_checksum='b'*64)
    elif field=='risk_guard': args[field]=replace(args[field],risk_state_guard_hash='b'*64)
    elif field=='scope': args['buying_scope_sha256']='b'*64
    elif field=='account': args['account_scope_sha256']='b'*64
    elif field=='key': args['identity_key']=b'z'*32
    with pytest.raises(m.VersionedFinancialReadError):m.build_versioned_cash_context(c.store.export_bytes(),**args)


def test_valid_proof_with_stale_timestamps_does_not_renew_itself(read_case):
    m,_=_modules();c=read_case;args=_args(c)
    report=m.build_versioned_cash_context(c.store.export_bytes(),**args)
    assert m.validate_versioned_cash_context(report,c.store.export_bytes(),**args)==report
    args['evaluated_at']=stamp(c.y.x.p.clock_at+timedelta(seconds=6))
    fresh=m.build_versioned_cash_context(c.store.export_bytes(),**args)
    assert json.loads(fresh.payload_bytes)['reason']=='EVIDENCE_STALE'
    with pytest.raises(m.VersionedFinancialReadError):m.validate_versioned_cash_context(report,c.store.export_bytes(),**args)


@pytest.mark.parametrize('field,value',[('free_cash_nano','999999999999999999999'),
    ('financial_ready',True),('runtime_authority_granted',True),('version',2),
    ('cl7_status','READY_FOR_POST')])
def test_resigned_false_context_is_rejected_not_just_hmac_checked(read_case,field,value):
    import hashlib,hmac
    m,_=_modules();c=read_case;args=_args(c)
    report=m.build_versioned_cash_context(c.store.export_bytes(),**args)
    d=json.loads(report.payload_bytes);d[field]=value
    raw=json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    forged=m.VersionedFinancialContext(raw,hmac.new(args['identity_key'],raw,hashlib.sha256).hexdigest())
    with pytest.raises(m.VersionedFinancialReadError):m.validate_versioned_cash_context(forged,c.store.export_bytes(),**args)
    with pytest.raises(m.VersionedFinancialReadError,match='CUTOVER'):m.require_versioned_runtime_authority(forged)


@pytest.mark.parametrize('damage',['receipt_fee','fee_id','missing_fee','trade_price','same_balance_different_fee',
    'blocked','positions_account','only_margin','timeout'])
def test_capture_primary_mismatch_does_not_fall_back_or_write(read_case,damage,monkeypatch):
    from copy import deepcopy
    m,bridge=_modules();c=read_case;y=c.y
    if damage=='receipt_fee': y.raw['executedCommission']=money('0.21')
    elif damage=='fee_id': y.fee['id']='DIFFERENT_PROVIDER_ID'
    elif damage=='missing_fee': y.x.operations.remove(y.fee)
    elif damage=='trade_price': y.trade['tradesInfo']['trades'][0]['price']=money(1)
    elif damage=='same_balance_different_fee':
        y.raw['executedCommission']=money('0.21');y.fee['payment']=money('-0.21')
    elif damage in {'blocked','positions_account'}:
        original=y.x.p.get_positions
        def bad(account):
            value=original(account)
            if damage=='blocked':value['blocked']=[money('0.1')]
            else:value['accountId']='PRIVATE_OTHER_ACCOUNT'
            return value
        monkeypatch.setattr(y.x.p,'get_positions',bad)
    elif damage=='only_margin':
        monkeypatch.setattr(y.x.p,'get_max_lots',lambda *a,**kw:{'currency':'rub',
            'buyMarginLimits':{'buyMoneyAmount':money(999999999)}})
    else:
        def bad(*a,**kw):raise TimeoutError('PRIVATE_PROVIDER_EXCEPTION')
        monkeypatch.setattr(y.x.p,'get_order_state',bad)
    before=_snapshot(y.x),c.store.export_bytes()
    with pytest.raises(m.VersionedFinancialReadError) as caught:
        bridge.capture_versioned_financial_review(y.a,recovery=y.recovery,journal_store=c.store,pins=_pins(c))
    assert 'PRIVATE_' not in str(caught.value)
    assert (_snapshot(y.x),c.store.export_bytes())==before and y.x.p.order_calls==1


@pytest.mark.parametrize('damage',['wall_forward','wall_back','mono_forward','key','policy'])
def test_capture_detects_drift_during_actual_provider_reads(read_case,damage):
    m,bridge=_modules();c=read_case;y=c.y
    def drift(*args):
        if damage=='wall_forward': y.x.p.clock_at+=timedelta(seconds=6)
        elif damage=='wall_back': y.x.p.clock_at-=timedelta(seconds=1)
        elif damage=='mono_forward': y.a.cl7_monotonic_ns=lambda:6_000_000_001
        elif damage=='key': y.a.cl7_identity_key=b'x'*32
        elif damage=='policy': object.__setattr__(y.a.cash_authority_manager.buying_budget_policy,'execution_order_type','BESTPRICE')
    y.x.p.on_cash_limits=drift
    # Private adapter fields may be changed by the injected competing actor;
    # compare actual financial files, not this intentionally mutated graph.
    files={p:p.read_bytes() for p in y.a.manager.store.path.parent.glob('*.json')}
    with pytest.raises(m.VersionedFinancialReadError):
        bridge.capture_versioned_financial_review(y.a,recovery=y.recovery,journal_store=c.store,pins=_pins(c))
    assert all(p.read_bytes()==b for p,b in files.items()) and y.x.p.order_calls==1


def test_day_old_canonical_is_not_relabelled_as_fresh_by_capture(read_case):
    _,bridge=_modules();c=read_case;y=c.y
    y.x.p.clock_at+=timedelta(days=1)
    before=_snapshot(y.x)
    result=bridge.capture_versioned_financial_review(y.a,recovery=y.recovery,journal_store=c.store,pins=_pins(c))
    b=json.loads(result.context.payload_bytes)
    assert b['cl4_status']=='MATCHED' and b['reason']=='EVIDENCE_STALE'
    assert b['free_cash_nano'] is None and _snapshot(y.x)==before


def test_capture_never_reads_withdrawal_or_calls_writers(read_case,monkeypatch):
    _,bridge=_modules();c=read_case;y=c.y
    def prohibited(*a,**k):raise AssertionError('must not be called')
    for name in ('post_order','post_order_once','get_withdraw_limits'):
        monkeypatch.setattr(y.x.p,name,prohibited)
    monkeypatch.setattr(y.a.cl7_ledger_store,'append_transaction',prohibited)
    monkeypatch.setattr(y.recovery.risk.state_store,'save_account_while_locked',prohibited)
    monkeypatch.setattr(y.a.cash_authority_manager,'arm',prohibited)
    result=bridge.capture_versioned_financial_review(y.a,recovery=y.recovery,journal_store=c.store,pins=_pins(c))
    assert result.public_summary()['runtime_authority_granted'] is False
    summary=json.dumps(result.public_summary())
    assert y.a.policy.account_id not in summary and y.intent.intent_id not in summary


def test_source_locks_must_be_obtained_before_accepting_capture(read_case):
    from trading_robot.locking import InterProcessFileLock
    m,bridge=_modules();c=read_case;y=c.y;before=_snapshot(y.x)
    with InterProcessFileLock(y.recovery.risk.state_store.lock_path,timeout_seconds=0.1):
        with pytest.raises(m.VersionedFinancialReadError):
            bridge.capture_versioned_financial_review(y.a,recovery=y.recovery,journal_store=c.store,pins=_pins(c))
    assert _snapshot(y.x)==before


def test_old_readers_cannot_accept_new_context_or_choose_frozen_base(read_case):
    from trading_robot.runtime_cash_authority import LockedDispatchProof
    m,_=_modules();c=read_case;context=_build(c);d=json.loads(context.payload_bytes)
    with pytest.raises(Exception):LockedDispatchProof.from_canonical_dict(d)
    with pytest.raises(Exception):cl4.project_shadow_cash(c.store.export_bytes(),
        account_scope_sha256=c.y.common['account_scope_sha256'],environment=BrokerEnvironment.SANDBOX,
        as_of=c.y.a.cl7_clock(),identity_key=c.y.a.cl7_identity_key)
    with pytest.raises(m.VersionedFinancialReadError):
        m.require_versioned_runtime_authority(context)


@pytest.mark.parametrize('damage',['capture_fee','provenance','forged_flag'])
def test_full_journal_semantics_not_just_export_hash_are_validated(read_case,damage):
    from hashlib import sha256
    import hmac
    m,_=_modules();c=read_case;raw=c.store.export_bytes();d=json.loads(raw);p=_pins(c)
    canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    if damage=='forged_flag':d['financial_ready']=True
    else:
        e=json.loads(d['correction_record_json_ascii']);b=e['payload']
        if damage=='capture_fee':
            capture=json.loads(b['capture_json_ascii']);capture['order_state']['executedCommission']=money('0.21')
            b['capture_json_ascii']=canonical(capture).decode()
        else:b['provenance']['observation_sha256']='e'*64
        e['hmac_sha256']=hmac.new(c.y.a.cl7_identity_key,canonical(b),'sha256').hexdigest()
        d['correction_record_json_ascii']=canonical(e).decode()
        p=replace(p,correction_head_sha256=sha256(canonical(e)).hexdigest())
    raw=canonical(d);p=replace(p,export_sha256=sha256(raw).hexdigest())
    with pytest.raises(m.VersionedFinancialReadError):m.project_versioned_cash(raw,pins=p,**c.y.common)


def test_current_economic_binding_cannot_be_supplied_to_pure_builder(read_case):
    m,_=_modules();c=read_case;args=_args(c);args['current_economic_evidence_sha256']='a'*64
    with pytest.raises(m.VersionedFinancialReadError,match='INTERNAL_ONLY'):
        m.build_versioned_cash_context(c.store.export_bytes(),**args)
