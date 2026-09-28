"""STEP12: CL5 own-buying evidence, real desktop/CL2-CL7, synthetic broker only."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from datetime import timedelta
import pytest

from test_q7a_source_exact_dispatch import exact_case, desktop_case, base_desktop_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_desktop_flow import _desktop_cycle, _observed
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState, CL7RuntimeError


def test_paired_zero_withdrawal_does_not_block_own_buying(exact_case, record_property):
    x = exact_case
    x.withdraw['money'] = []
    before = len(x.exact_calls)
    outcome = _desktop_cycle(x.c)
    obs = _observed(x.c)
    record_property('coordination', outcome.status)
    record_property('execution', obs.execution_status)
    record_property('fake_posts', x.p.order_calls)
    record_property('withdraw_reads', x.exact_calls[before:].count('withdraw'))
    assert outcome.status == 'QUEUED'
    assert obs.execution_status == 'SUBMITTED'
    assert x.p.order_calls == 1
    assert 'withdraw' not in x.exact_calls[before:]


def test_paired_unavailable_withdrawal_not_called(exact_case, monkeypatch, record_property):
    x = exact_case
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('SYNTHETIC_PRIVATE_WITHDRAW_UNAVAILABLE')
    monkeypatch.setattr(x.p, 'get_withdraw_limits', unavailable)
    _desktop_cycle(x.c)
    obs = _observed(x.c)
    record_property('execution', obs.execution_status)
    record_property('fake_posts', x.p.order_calls)
    record_property('withdraw_reads', len(calls))
    assert obs.execution_status == 'SUBMITTED'
    assert calls == [] and x.p.order_calls == 1


def test_paired_foreign_withdrawal_ignored_not_converted(exact_case):
    x = exact_case
    x.withdraw = {'money': [money(0), money(500, 'USD')], 'blocked': [], 'blockedGuarantee': []}
    _desktop_cycle(x.c)
    assert _observed(x.c).execution_status == 'SUBMITTED'
    assert x.p.order_calls == 1

# Pure tests use the real CL4 store and Central reservation projection. Synthetic
# metadata/provider values never grant runtime or broker authority.
from types import SimpleNamespace
import hashlib
from test_q7a_source_primary_rub_cash import (
    KEY, KEY_ID, AT, LATER, account_hash, positions, proof as accounting_proof,
    old_proof, store_at, accept,
)
from test_v3_10_cash_availability import _state as central_state
from trading_robot import cash_availability as legacy_cl5
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import reporting_risk_cash_context as cl6
from trading_robot.broker_read_adapters import BrokerEnvironment
from trading_robot.portfolio_risk_adapter import PortfolioRiskInstrumentMetadata
from trading_robot.cash_ledger_domain import Money


def module():
    # Keep collection possible on STEP11; missing API is a case failure, not a
    # falsely passing skip or a test-module-wide collection error.
    from trading_robot import cash_buying_availability
    return cash_buying_availability


def policy(count=2, guard=lambda: None, order_type='MARKET'):
    meta = {f'uid-{i}': PortfolioRiskInstrumentMetadata(f'uid-{i}', 10, 'SHARE', 'RUB')
            for i in range(count)}
    return module().OwnBuyingBudgetPolicy(ACCOUNT, meta, guard, order_type)


class LimitsProvider:
    def __init__(self, amounts=(100, 80)):
        self.amounts = amounts
        self.calls = []
        self.damage = lambda raw, uid: None
        self.after = lambda: None
        self.responses = []
    def get_max_lots(self, account, uid, price=None):
        self.calls.append((account, uid, price))
        raw = {'currency': 'rub', 'buyLimits': {'buyMoneyAmount': money(self.amounts[int(uid[-1])])},
               'buyMarginLimits': {'buyMoneyAmount': money(999999)},
               'accountId': account, 'instrumentId': uid}
        self.damage(raw, uid)
        self.responses.append(deepcopy(raw))
        self.after()
        return raw


def acquire(p=None, provider=None, clock=lambda: AT, monotonic=lambda: 1, **changes):
    args = dict(account_scope_sha256=account_hash(), identity_key=KEY,
                identity_key_id=KEY_ID, clock=clock, monotonic_ns=monotonic)
    args.update(changes)
    return (policy() if p is None else p).acquire(LimitsProvider() if provider is None else provider, **args)


@pytest.mark.parametrize('count,amounts,expected', [(2, (100, 80),80), (3,(100,90,70),70),
                                                  (2,(0,100),0), (2,('0.000000001',100),0.000000001)])
def test_own_proof_uses_minimum_and_raw_response_identity(count, amounts, expected):
    m=module(); p=policy(count); broker=LimitsProvider(amounts)
    got=acquire(p,broker)
    assert got.available_rub.minor_units == money(expected)['nano'] + int(money(expected)['units'])*10**9
    assert len(broker.calls)==count and all(call[0]==ACCOUNT and call[2] is None for call in broker.calls)
    assert got.response_sha256 == tuple(hashlib.sha256(legacy_cl5._canonical_bytes(raw)).hexdigest()
                                        for raw in broker.responses)
    assert m.validate_own_buying_proof(got,KEY,buying_scope_sha256=p.scope_sha256)==got
    assert got.version==3 and b'withdraw' not in got.canonical_bytes.lower()
    assert ACCOUNT.encode() not in got.canonical_bytes and b'uid-0' not in got.canonical_bytes
    assert got.available_rub.minor_units != 999999*10**9


@pytest.mark.parametrize('damage', [
    'missing_own','only_margin','missing_amount','amount_null','amount_float','amount_bool',
    'units_bool','units_float','units_leading_zero','nano_bool','nano_range','negative','cap',
    'foreign_currency','currency_list','amount_foreign','amount_extra','account','instrument',
    'loading','loading_int','nan_extra','duplicate_alias','too_deep','oversize',
])
def test_invalid_own_sources_never_fall_back(damage):
    m=module(); p=LimitsProvider()
    def mutate(raw, uid):
        amount=raw['buyLimits']['buyMoneyAmount']
        if damage=='missing_own': del raw['buyLimits']
        elif damage=='only_margin': raw.pop('buyLimits')
        elif damage=='missing_amount': raw['buyLimits']={}
        elif damage=='amount_null': raw['buyLimits']['buyMoneyAmount']=None
        elif damage=='amount_float': raw['buyLimits']['buyMoneyAmount']=1.0
        elif damage=='amount_bool': raw['buyLimits']['buyMoneyAmount']=True
        elif damage=='units_bool': amount['units']=True
        elif damage=='units_float': amount['units']=1.0
        elif damage=='units_leading_zero': amount['units']='01'
        elif damage=='nano_bool': amount['nano']=True
        elif damage=='nano_range': amount['nano']=10**9
        elif damage=='negative': amount['units']='-1'
        elif damage=='cap': amount['units']=str(10**12+1)
        elif damage=='foreign_currency': raw['currency']='usd'
        elif damage=='currency_list': raw['currency']=['SECRET_CANARY']
        elif damage=='amount_foreign': amount['currency']='usd'
        elif damage=='amount_extra': amount['secret']='SECRET_CANARY'
        elif damage=='account': raw['accountId']='SECRET_CANARY'
        elif damage=='instrument': raw['instrumentId']='SECRET_CANARY'
        elif damage=='loading': raw['limitsLoadingInProgress']=True
        elif damage=='loading_int': raw['limitsLoadingInProgress']=0
        elif damage=='nan_extra': raw['secret']=float('nan')
        elif damage=='duplicate_alias': raw['alias']=raw['buyLimits']
        elif damage=='too_deep':
            here=raw
            for _ in range(18): here['nested']={}; here=here['nested']
        elif damage=='oversize': raw['secret']='X'*1048577
    p.damage=mutate
    with pytest.raises(m.CL5Error) as caught: acquire(provider=p)
    assert 'SECRET_CANARY' not in str(caught.value)
    assert len(p.calls)<=1


@pytest.mark.parametrize('form',['quotation','money','omitted_zero'])
def test_present_zero_amount_protobuf_shape(form):
    p=LimitsProvider((0,0))
    def mutate(raw, uid):
        if form=='quotation': raw['buyLimits']['buyMoneyAmount'].pop('currency')
        elif form=='omitted_zero': raw['buyLimits']['buyMoneyAmount']={}
    p.damage=mutate
    assert acquire(provider=p).available_rub.minor_units==0


@pytest.mark.parametrize('damage',['utc_forward','utc_backward','mono_forward','mono_backward','mono_bool','guard','scope'])
def test_acquisition_time_and_binding_drift_are_rejected(damage):
    m=module(); c=SimpleNamespace(utc=AT,mono=100,checks=0)
    def guard():
        c.checks+=1
        if damage=='guard' and c.checks>2: raise m.CL5Error(m.CL5Reason.PROOF_IDENTITY_INVALID)
    p=policy(guard=guard); provider=LimitsProvider()
    def after():
        if damage=='utc_forward': c.utc='2027-01-01T12:00:06.000000000Z'
        elif damage=='utc_backward': c.utc='2027-01-01T11:59:59.000000000Z'
        elif damage=='mono_forward': c.mono=6_000_000_100
        elif damage=='mono_backward': c.mono=99
        elif damage=='mono_bool': c.mono=True
        elif damage=='scope': object.__setattr__(p,'execution_order_type','BESTPRICE')
    provider.after=after
    with pytest.raises(m.CL5Error): acquire(p,provider,clock=lambda:c.utc,monotonic=lambda:c.mono)
    assert len(provider.calls)<=1


def test_provider_failure_is_finite_and_no_retry():
    m=module(); p=LimitsProvider()
    def broken(raw,uid): raise RuntimeError('PRIVATE_PROVIDER_CANARY')
    p.damage=broken
    with pytest.raises(m.CL5Error) as caught: acquire(provider=p)
    assert 'PRIVATE_PROVIDER_CANARY' not in str(caught.value) and len(p.calls)==1
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize('field,value', [('own_money_nano',(10**9,2*10**9)),
    ('buying_scope_sha256','a'*64),('account_scope_sha256','a'*64),
    ('request_sha256',('a'*64,'b'*64)),('response_sha256',('a'*64,'b'*64)),
    ('as_of','2027-01-01T11:59:59.000000000Z'),('identity_key_id','ANOTHER'),
    ('proof_identity_sha256','a'*64),('version',2)])
def test_tampered_or_downgraded_own_proof_fails_hmac(field,value):
    m=module(); good=acquire()
    with pytest.raises(m.CL5Error): m.validate_own_buying_proof(replace(good,**{field:value}),KEY)


def test_wrong_key_and_configured_scope_rejected():
    m=module(); good=acquire()
    with pytest.raises(m.CL5Error): m.validate_own_buying_proof(good,b'x'*32)
    with pytest.raises(m.CL5Error): m.validate_own_buying_proof(good,KEY,buying_scope_sha256='b'*64)
    with pytest.raises(m.CL5Error): acquire(account_scope_sha256='b'*64)
    assert policy(order_type='BESTPRICE').scope_sha256 != policy().scope_sha256


@pytest.fixture
def cash_input(tmp_path):
    source=accounting_proof(positions(100))
    store=store_at(tmp_path/'ledger'); accept(store,source)
    export=store.export_bytes()
    reconciliation=cl4.reconcile_shadow_cash(export,source,evaluated_at=LATER,identity_key=KEY)
    yield SimpleNamespace(store=store,export=export,reconciliation=reconciliation)
    store.close()


def reservations(status=None, kopecks=3000, at=LATER):
    return legacy_cl5.project_central_reservations(central_state(status,reserved_kopecks=kopecks,account=ACCOUNT),
        account_scope_sha256=account_hash(),environment=BrokerEnvironment.SANDBOX,
        evaluated_at=at,identity_key=KEY,identity_key_id=KEY_ID)


def available(cash_input, amounts=(100,80), status=None, kopecks=3000, **kw):
    return module().build_own_cash_availability(cash_input.export,cash_input.reconciliation,
        acquire(provider=LimitsProvider(amounts)),reservations(status,kopecks),
        evaluated_at=kw.pop('at',LATER),identity_key=KEY,**kw)


@pytest.mark.parametrize('status,kopecks,expected,reason', [
    (None,0,80,'READY'),('QUEUED',3000,50,'READY'),('QUEUED',8000,0,'READY'),
    ('QUEUED',8001,None,'INSUFFICIENT_AFTER_RESERVATIONS'),
    ('IN_FLIGHT',3000,None,'CENTRAL_PROVIDER_OVERLAP_UNKNOWN'),
    ('SUBMITTED',3000,None,'CENTRAL_PROVIDER_OVERLAP_UNKNOWN'),
    ('UNCERTAIN',3000,None,'CENTRAL_PROVIDER_OVERLAP_UNKNOWN'),
    ('SUBMITTED',0,None,'CENTRAL_PROVIDER_OVERLAP_UNKNOWN'),('FAILED',3000,80,'READY'),
])
def test_reservations_once_and_ambiguous_even_when_cash_zero(cash_input,status,kopecks,expected,reason):
    result=available(cash_input,status=status,kopecks=kopecks)
    assert result.availability_reason.value==reason
    assert (None if result.free_investable_cash is None else result.free_investable_cash.minor_units)==(
        None if expected is None else expected*10**9)
    assert b'withdraw' not in result.canonical_bytes.lower()
    assert result.version==3 and cash_input.store.export_bytes()==cash_input.export


def test_own_budget_smaller_is_not_accounting_mismatch(cash_input):
    result=available(cash_input,amounts=(70,60))
    assert result.status.value=='READY' and result.broker_total_cash.minor_units==100*10**9
    assert result.free_investable_cash.minor_units==60*10**9


def test_limit_larger_than_accounting_is_inconsistent(cash_input):
    result=available(cash_input,amounts=(101,80))
    assert result.availability_reason.value=='BROKER_VIEW_MISMATCH'
    assert result.free_investable_cash is None


def test_stale_source_blocks_and_future_raises(cash_input):
    m=module()
    result=available(cash_input,at='2027-01-01T12:00:06.000000000Z')
    assert result.availability_reason.value=='BROKER_PROOF_STALE'
    with pytest.raises(m.CL5Error): available(cash_input,at='2027-01-01T11:59:59.000000000Z')


def test_legacy_opening_cannot_be_mixed_with_own_availability(tmp_path):
    m=module(); p=old_proof(100); s=store_at(tmp_path/'old'); accept(s,p)
    r=cl4.reconcile_shadow_cash(s.export_bytes(),p,evaluated_at=LATER,identity_key=KEY)
    with pytest.raises(m.CL5Error):
        m.build_own_cash_availability(s.export_bytes(),r,acquire(),reservations(),evaluated_at=LATER,identity_key=KEY)
    s.close()


@pytest.mark.parametrize('change',[{'free_investable_cash':Money('RUB',100*10**9)},
    {'version':2},{'central_ambiguous_count':True},{'central_ambiguous_reserved_cash':Money('RUB',1),
    'central_total_reserved_cash':Money('RUB',1)}])
def test_snapshot_constructor_does_not_allow_inconsistent_values(cash_input,change):
    with pytest.raises(module().CL5Error): replace(available(cash_input),**change)


def test_cl6_recomputes_new_snapshot_and_rejects_tamper(cash_input):
    m=module(); own=acquire(); reserve=reservations(); snapshot=available(cash_input)
    rebuilt=cl6._rebuild_availability(cash_input.export,cash_input.reconciliation,own,reserve,snapshot,KEY)
    assert rebuilt==snapshot
    bad=replace(snapshot,buying_scope_sha256='a'*64)
    with pytest.raises(cl6.CL6Error):
        cl6._rebuild_availability(cash_input.export,cash_input.reconciliation,own,reserve,bad,KEY)


def test_v3_context_and_availability_are_separate_from_withdrawal(exact_case):
    x=exact_case; a=x.c.execution_adapter; manager=a.cash_authority_manager
    # Capture the actual generated context, not a fabricated context object.
    seen=[]; original=manager.build_runtime_context
    def observe(**kw):
        result=original(**kw); seen.append(result); return result
    manager.build_runtime_context=observe
    assert 'withdraw' not in x.exact_calls  # includes prepare, confirm, activate, arm
    _desktop_cycle(x.c)
    assert _observed(x.c).execution_status=='SUBMITTED'
    assert seen and all(e.broker_withdraw_limits_proof is None for e in seen)
    for e in seen:
        assert e.broker_own_buying_cash_proof.version==3 and e.availability.version==3
        assert e.context.version==3
        assert b'withdraw' not in e.context.canonical_bytes.lower()
        assert e.context.buying_scope_sha256==manager.buying_budget_policy.scope_sha256


@pytest.mark.parametrize('amount',[0,1])
def test_insufficient_own_budget_blocks_before_attempt(exact_case,amount):
    x=exact_case
    x.p.cash_limits_override={'currency':'rub','buyLimits':{'buyMoneyAmount':money(amount),
        'buyMaxMarketLots':'1000000'},'buyMarginLimits':{'buyMoneyAmount':money(99999999)}}
    _desktop_cycle(x.c)
    assert _observed(x.c).execution_status=='CL7_CONTEXT_BLOCKED'
    state=x.c.execution_adapter.cash_authority_manager.status()
    assert x.p.order_calls==0 and state.post_attempt_count==0
    assert state.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED


def test_final_locked_gate_still_checks_late_lot_cap(exact_case):
    x=exact_case; n=[]
    def at_positions():
        n.append(1)
        if len(n)==2:
            x.p.cash_limits_override={'currency':'rub','buyLimits':{'buyMoneyAmount':money(1_000_000),
                'buyMaxMarketLots':'0'},'sellLimits':{'sellMaxLots':'1000000'}}
    x.p.on_cash_positions=at_positions
    _desktop_cycle(x.c)
    assert _observed(x.c).execution_status=='CL7_OWN_FUNDS_BLOCKED'
    assert x.p.order_calls==0 and x.c.execution_adapter.cash_authority_manager.status().post_attempt_count==0


def test_selected_common_scope_and_prelock_read_budget(exact_case):
    x=exact_case; before=len(x.p.cash_read_calls)
    _desktop_cycle(x.c)
    assert _observed(x.c).execution_status=='SUBMITTED'
    calls=x.p.cash_read_calls[before:]
    assert calls==[('positions',ACCOUNT),('max_lots',ACCOUNT,'uid-lkoh'),
        ('max_lots',ACCOUNT,UID),('positions',ACCOUNT),('max_lots',ACCOUNT,UID)]
    assert len(x.c.execution_adapter.cash_authority_manager.buying_budget_policy.instruments)==2


def test_removing_buying_policy_from_factory_fails_binding_guard(exact_case):
    x=exact_case
    x.c.execution_adapter.cash_authority_manager._buying_budget_policy=None
    with pytest.raises(RuntimeError): _desktop_cycle(x.c)
    assert x.p.order_calls==0


def test_manager_rejects_different_requested_account(exact_case):
    x=exact_case; a=x.c.execution_adapter
    with pytest.raises(CL7RuntimeError) as caught:
        a.cash_authority_manager.read_availability_cash(x.p,'OTHER',account_scope_sha256='a'*64,
            identity_key=a.cl7_identity_key,identity_key_id=a.cl7_identity_key_id,
            clock=a.cl7_clock,monotonic_ns=a.cl7_monotonic_ns)
    assert caught.value.reason.value=='ACCOUNT_SCOPE_INVALID' and x.p.order_calls==0


def capture_context(x):
    manager=x.c.execution_adapter.cash_authority_manager
    seen=[]; original=manager.build_runtime_context
    def observe(**kw):
        result=original(**kw); seen.append(result); return result
    manager.build_runtime_context=observe
    _desktop_cycle(x.c)
    assert _observed(x.c).execution_status=='SUBMITTED'
    return seen[-1]


def rebuild_context(x,e,**overrides):
    args=dict(evaluated_at=e.context.evaluated_at,
        identity_key=x.c.execution_adapter.cl7_identity_key,
        identity_key_id=x.c.execution_adapter.cl7_identity_key_id)
    args.update(overrides)
    return cl6.build_portfolio_risk_cash_context(e.ledger_export_bytes,e.reconciliation,
        e.broker_own_buying_cash_proof,e.reservations,e.availability,e.portfolio,e.risk_guard,**args)


def test_cl6_reevaluation_does_not_renew_five_second_budget(exact_case):
    x=exact_case; e=capture_context(x)
    later=stamp(x.p.clock_at+timedelta(seconds=6))
    got=rebuild_context(x,e,evaluated_at=later)
    assert got.status.value=='BLOCKED' and got.reason.value=='CASH_AVAILABILITY_STALE'
    assert got.free_investable_cash is None


@pytest.mark.parametrize('change',[{'version':2},{'buying_scope_sha256':None},
    {'broker_withdraw_limits_as_of':AT}])
def test_v3_context_cannot_be_relabelled_or_mixed(exact_case,change):
    e=capture_context(exact_case)
    with pytest.raises(cl6.CL6Error): replace(e.context,**change)


def test_legacy_proof_with_own_snapshot_rejected_as_different_schema(exact_case):
    x=exact_case; e=capture_context(x)
    from test_v3_10_cash_availability import _withdraw
    with pytest.raises(cl6.CL6Error) as caught:
        cl6.build_portfolio_risk_cash_context(e.ledger_export_bytes,e.reconciliation,
            _withdraw(),e.reservations,e.availability,e.portfolio,e.risk_guard,
            evaluated_at=e.context.evaluated_at,identity_key=x.c.execution_adapter.cl7_identity_key,
            identity_key_id=x.c.execution_adapter.cl7_identity_key_id)
    assert caught.value.reason.value=='CASH_AVAILABILITY_INVALID'


def test_scope_is_in_context_identity_and_cannot_reuse_signature(exact_case):
    x=exact_case; e=capture_context(x)
    changed=replace(e.context,buying_scope_sha256='a'*64)
    assert changed.canonical_bytes!=e.context.canonical_bytes
    assert cl6._context_identity(changed,x.c.execution_adapter.cl7_identity_key)!=cl6._context_identity(
        e.context,x.c.execution_adapter.cl7_identity_key)


def test_two_queued_reservations_each_subtracted_once(cash_input):
    from test_v3_10_cash_availability import _candidate, _authorization, _intent, CENTRAL_TS
    from trading_robot.central_order_manager import CentralOrderIntent, CentralOrderState
    first=_intent(account=ACCOUNT,reserved_kopecks=3000)
    second=CentralOrderIntent.create(replace(_candidate(account=ACCOUNT),instrument_id='uid-lkoh',
        ticker='LKOH',runtime_key='runtime-lkoh'),
        replace(_authorization(account=ACCOUNT),instrument_id='uid-lkoh'),
        queue_sequence=2,reserved_cash_kopecks=2000,created_at=CENTRAL_TS)
    state=CentralOrderState(ACCOUNT,1,3,(first,second),CENTRAL_TS,CENTRAL_TS)
    proj=legacy_cl5.project_central_reservations(state,account_scope_sha256=account_hash(),
        environment=BrokerEnvironment.SANDBOX,evaluated_at=LATER,identity_key=KEY,identity_key_id=KEY_ID)
    snap=module().build_own_cash_availability(cash_input.export,cash_input.reconciliation,acquire(),proj,
        evaluated_at=LATER,identity_key=KEY)
    assert snap.central_queued_reserved_cash.minor_units==50*10**9
    assert snap.free_investable_cash.minor_units==30*10**9
    assert state.intents[0].reserved_cash_kopecks==3000 and state.intents[1].reserved_cash_kopecks==2000


def test_submitted_sell_with_zero_cash_reserve_still_blocks(cash_input):
    from test_v3_10_cash_availability import _candidate, _authorization, CENTRAL_TS
    from trading_robot.central_order_manager import CentralOrderIntent, CentralOrderState
    intent=CentralOrderIntent.create(replace(_candidate(account=ACCOUNT),current_lots=1,target_lots=0),
        replace(_authorization(account=ACCOUNT),authorized_target_lots=0),queue_sequence=1,
        reserved_cash_kopecks=0,created_at=CENTRAL_TS)
    intent=intent.transition('IN_FLIGHT',detail='synthetic',at=CENTRAL_TS).transition(
        'SUBMITTED',detail='synthetic',at=CENTRAL_TS,broker_order_id='synthetic-sell')
    state=CentralOrderState(ACCOUNT,1,2,(intent,),CENTRAL_TS,CENTRAL_TS)
    proj=legacy_cl5.project_central_reservations(state,account_scope_sha256=account_hash(),
        environment=BrokerEnvironment.SANDBOX,evaluated_at=LATER,identity_key=KEY,identity_key_id=KEY_ID)
    snap=module().build_own_cash_availability(cash_input.export,cash_input.reconciliation,acquire(),proj,
        evaluated_at=LATER,identity_key=KEY)
    assert proj.ambiguous_count==1 and proj.total_reserved_cash.minor_units==0
    assert snap.status.value=='MANUAL_REVIEW_REQUIRED' and snap.free_investable_cash is None
