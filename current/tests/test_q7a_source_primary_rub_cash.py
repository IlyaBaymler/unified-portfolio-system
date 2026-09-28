"""STEP11: versioned CL4 accounting from RUB positions, not trade authority.

The paired desktop scenarios use the same genuine factory/owners on both source
versions. Only broker-shaped data, credentials and clocks are synthetic. Unit
cases for newly versioned APIs may fail to set up on their predecessor. Nonzero
blocked balances, CL5 withdrawal and exact recovery are explicitly NOT migrated.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import hmac
import json

import pytest

from test_q7a_source_exact_dispatch import exact_case, desktop_case, stamp, operation
from test_q7a_source_desktop_flow import desktop_case as base_desktop_case, _desktop_cycle, _observed
from test_q7a_source_provider_refresh import refresh_case as base_refresh_case, money
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import cash_ledger_persistence as persistence
from trading_robot import broker_read_adapters as broker
from trading_robot.cash_ledger_domain import Money
from trading_robot.runtime_cash_authority import (
    RuntimeCashAuthorityManager, RuntimeCashAuthorityState, LockedDispatchProof,
)

KEY = bytes(range(32))
KEY_ID = 'PRIMARY_RUB_TEST'
AT = '2027-01-01T12:00:00.000000000Z'
LATER = '2027-01-01T12:00:01.000000000Z'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def account_hash(raw=ACCOUNT, key=KEY):
    return hmac.new(key, canonical({'account_id': raw, 'domain': 'v3.10-cl3-account-scope',
        'environment': 'SANDBOX', 'identity_key_id': KEY_ID, 'provider': 'TBANK', 'version': 1}),
        hashlib.sha256).hexdigest()


def positions(amount='123.500000001', currency='rub'):
    return {'accountId': ACCOUNT, 'money': [money(amount, currency)], 'blocked': [],
            'limitsLoadingInProgress': False, 'securities': [], 'futures': [], 'options': []}


def proof(raw=None, **overrides):
    args = dict(raw_account_id=ACCOUNT, account_scope_sha256=account_hash(),
        environment=broker.BrokerEnvironment.SANDBOX, as_of=AT, evaluated_at=LATER,
        response_complete=True, identity_key=KEY, identity_key_id=KEY_ID)
    args.update(overrides)
    return cl4.build_broker_rub_position_cash_proof(positions() if raw is None else raw, **args)


def old_proof(amount='123.500000001'):
    return cl4.build_broker_cash_proof({'totalAmountCurrencies': money(amount)},
        account_scope_sha256=account_hash(), environment=broker.BrokerEnvironment.SANDBOX,
        as_of=AT, evaluated_at=LATER, response_complete=True, identity_key=KEY, identity_key_id=KEY_ID)


def store_at(path):
    return persistence.CashLedgerStore.create(path, (
        cl4.CL4_OPENING_CODEC, cl4.CL4_RUB_POSITION_OPENING_CODEC, broker.TBANK_OPERATION_CODEC))


def accept(store, source):
    plan = cl4.prepare_from_now_opening(store.export_bytes(), source,
                                      evaluated_at=LATER, identity_key=KEY)
    result = cl4.accept_from_now_opening(store, plan,
        confirmation=f'ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}',
        evaluated_at=LATER, identity_key=KEY)
    return plan, result


@pytest.fixture
def refresh_case(base_refresh_case, request):
    x = base_refresh_case
    if 'mixed_opening' in request.node.name:
        x.p.payload['totalAmountCurrencies'] = money(2_000_000)
        x.p.payload['totalAmountPortfolio'] = money(2_000_000)
        x.p.payload['positions'] = [{
            'instrumentType': 'currency', 'instrumentUid': 'synthetic-usd',
            'quantity': money(100, 'usd'), 'currentPrice': money(10_000),
        }]
        x.p.cash_positions_override = positions(1_000_000)
        x.p.cash_positions_override['money'].append(money(100, 'usd'))
    return x


def record(x, request, **extra):
    authority = x.c.execution_adapter.cash_authority_manager.status()
    request.node.user_properties.extend({
        'fake_post_count': x.p.order_calls, 'post_attempt_count': authority.post_attempt_count,
        'authority': authority.state.value,
        'cash_source_version': getattr(x.c.execution_adapter.cash_authority_manager,
                                      'cash_source_version', 2), **extra,
    }.items())


@pytest.mark.parametrize('currency', ['rub', 'RUB'])
def test_positions_decimal_money_and_raw_identity(currency):
    raw = positions(currency=currency)
    raw['money'].append(money('98765.4321', 'usd'))
    raw['totalAmountCurrencies'] = money(999_999_999)  # Never used for arithmetic.
    original = deepcopy(raw)
    p = proof(raw)
    assert p.cash == Money('RUB', 123_500_000_001)
    assert p.version == 3 and p.response_canonical_sha256 == hashlib.sha256(canonical(raw)).hexdigest()
    assert p.to_canonical_dict()['rpc'].endswith('/GetSandboxPositions')
    assert p.to_canonical_dict()['cash_field'].startswith('money[RUB]')
    assert raw == original


def test_unrelated_foreign_amount_changes_hash_but_not_rub_cash():
    raw = positions(); first = proof(raw)
    raw['money'].append(money(100, 'eur')); second = proof(raw)
    assert first.cash == second.cash
    assert first.response_canonical_sha256 != second.response_canonical_sha256
    assert first.proof_identity_sha256 != second.proof_identity_sha256


def test_equivalent_wire_spellings_do_not_rewrite_provider_response():
    lower, upper = proof(positions(currency='rub')), proof(positions(currency='RUB'))
    assert lower.cash == upper.cash and lower.canonical_bytes != upper.canonical_bytes
    assert lower.proof_identity_sha256 != upper.proof_identity_sha256


@pytest.mark.parametrize('form', ['absent_money', 'empty', 'foreign_only', 'omitted_zero_scalars'])
def test_no_rub_is_zero_not_valuation(form):
    raw = positions()
    if form == 'absent_money': del raw['money']
    elif form == 'empty': raw['money'] = []
    elif form == 'foreign_only': raw['money'] = [money(100, 'usd')]
    else: raw['money'] = [{'currency': 'rub'}]
    raw['totalAmountCurrencies'] = money(1_000_000)
    assert proof(raw).cash == Money('RUB', 0)


@pytest.mark.parametrize('damage', [
    'missing_account', 'other_account', 'account_bool', 'loading', 'loading_int',
    'money_not_list', 'blocked_not_list', 'too_many_currencies', 'duplicate_rub',
    'duplicate_foreign', 'missing_currency', 'mixed_currency', 'spaces_currency',
    'unknown_row_key', 'row_not_dict', 'units_bool', 'units_float', 'units_leading_zero',
    'nano_bool', 'nano_range', 'negative_rub', 'negative_foreign', 'amount_cap',
    'nonzero_blocked_rub', 'nonzero_blocked_foreign', 'negative_blocked',
    'futures', 'options', 'bad_securities', 'nan_extra', 'surrogate_extra', 'nested_deep',
    'top_list', 'unsupported_int',
])
def test_invalid_or_unsupported_positions_are_closed_and_private(damage):
    raw = positions(); row = raw['money'][0]
    if damage == 'missing_account': del raw['accountId']
    elif damage == 'other_account': raw['accountId'] = 'PRIVATE_ACCOUNT_CANARY'
    elif damage == 'account_bool': raw['accountId'] = True
    elif damage == 'loading': raw['limitsLoadingInProgress'] = True
    elif damage == 'loading_int': raw['limitsLoadingInProgress'] = 0
    elif damage == 'money_not_list': raw['money'] = {}
    elif damage == 'blocked_not_list': raw['blocked'] = None
    elif damage == 'too_many_currencies': raw['money'] = [money(0)] * 33
    elif damage == 'duplicate_rub': raw['money'].append(money(0, 'RUB'))
    elif damage == 'duplicate_foreign': raw['money'] += [money(0, 'usd'), money(1, 'USD')]
    elif damage == 'missing_currency': del row['currency']
    elif damage == 'mixed_currency': row['currency'] = 'Rub'
    elif damage == 'spaces_currency': row['currency'] = ' rub'
    elif damage == 'unknown_row_key': row['private'] = 'PRIVATE_ACCOUNT_CANARY'
    elif damage == 'row_not_dict': raw['money'] = [0]
    elif damage == 'units_bool': row['units'] = True
    elif damage == 'units_float': row['units'] = 1.0
    elif damage == 'units_leading_zero': row['units'] = '01'
    elif damage == 'nano_bool': row['nano'] = True
    elif damage == 'nano_range': row['nano'] = 10**9
    elif damage == 'negative_rub': raw['money'] = [money(-1)]
    elif damage == 'negative_foreign': raw['money'].append(money(-1, 'usd'))
    elif damage == 'amount_cap': raw['money'] = [money(10**12 + 1)]
    elif damage == 'nonzero_blocked_rub': raw['blocked'] = [money(1)]
    elif damage == 'nonzero_blocked_foreign': raw['blocked'] = [money(1, 'usd')]
    elif damage == 'negative_blocked': raw['blocked'] = [money(-1)]
    elif damage in {'futures', 'options'}: raw[damage] = [{}]
    elif damage == 'bad_securities': raw['securities'] = 'PRIVATE_ACCOUNT_CANARY'
    elif damage == 'nan_extra': raw['extra'] = float('nan')
    elif damage == 'surrogate_extra': raw['extra'] = '\ud800'
    elif damage == 'nested_deep':
        nested = {}; raw['extra'] = nested
        for _ in range(18): nested['next'] = {}; nested = nested['next']
    elif damage == 'top_list': raw = []
    elif damage == 'unsupported_int': raw['extra'] = 2**64
    with pytest.raises(cl4.CL4Error) as caught:
        proof(raw)
    assert 'PRIVATE_ACCOUNT_CANARY' not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize('args', [
    {'raw_account_id': 'other'}, {'raw_account_id': True}, {'raw_account_id': ' bad '},
    {'account_scope_sha256': 'a'*64}, {'identity_key': b'x'*32},
    {'identity_key_id': 'OTHER_KEY'}, {'response_complete': False},
    {'response_complete': 1}, {'as_of': '2027-01-01T12:00:02.000000000Z'},
    {'evaluated_at': '2027-01-01T12:02:01.000000000Z'},
    {'as_of': '2027-02-30T12:00:00.000000000Z'},
    {'environment': 'SANDBOX'},
])
def test_scope_key_time_and_completeness_are_checked(args):
    with pytest.raises(cl4.CL4Error): proof(**args)


def test_versioned_opening_projection_reconciliation_and_idempotency(tmp_path):
    store = store_at(tmp_path/'v3')
    p = proof(); plan, accepted = accept(store, p)
    assert plan.version == accepted.record.version == 3
    assert plan.observation.descriptor.codec_id == 'CL4_FROM_NOW_RUB_POSITION_OPENING_V3'
    assert json.loads(plan.observation.content_json_ascii)['contract_version'] == 3
    once = store.export_bytes()
    repeated = cl4.accept_from_now_opening(store, plan,
        confirmation=f'ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}', evaluated_at=LATER, identity_key=KEY)
    assert repeated.disposition == 'OPENING_ALREADY_PRESENT' and store.export_bytes() == once
    recon = cl4.reconcile_shadow_cash(once, p, evaluated_at=LATER, identity_key=KEY)
    assert recon.version == recon.projection.version == 3
    assert recon.status is cl4.ReconciliationStatus.MATCHED and recon.delta_minor_units == 0
    candidate = cl4.build_adoption_candidate(recon, ledger_export_bytes=once, identity_key=KEY)
    assert candidate.automatic_adoption is False
    reopened = persistence.CashLedgerStore.open(store.root, (
        cl4.CL4_OPENING_CODEC, cl4.CL4_RUB_POSITION_OPENING_CODEC, broker.TBANK_OPERATION_CODEC))
    assert reopened.export_bytes() == once


@pytest.mark.parametrize('version', [2, 3])
def test_same_amount_different_opening_basis_never_silently_migrates(tmp_path, version):
    store = store_at(tmp_path/'mixed')
    p = old_proof() if version == 2 else proof()
    _, accepted = accept(store, p)
    other = proof() if version == 2 else old_proof()
    assert p.cash == other.cash and p.proof_identity_sha256 != other.proof_identity_sha256
    before = store.export_bytes()
    with pytest.raises(cl4.CL4Error) as caught:
        cl4.reconcile_shadow_cash(before, other, evaluated_at=LATER, identity_key=KEY)
    assert caught.value.reason.value == 'CASH_SOURCE_MISMATCH'
    with pytest.raises(cl4.CL4Error): accept(store, other)
    assert store.export_bytes() == before and accepted.record.version == version


@pytest.mark.parametrize('field,value', [('version', 2), ('cash', Money('RUB', 99)),
    ('account_scope_sha256', 'a'*64), ('response_canonical_sha256', 'f'*64),
    ('identity_key_id', 'OTHER_KEY'), ('as_of', LATER)])
def test_proof_downgrade_or_tamper_cannot_open_ledger(tmp_path, field, value):
    store = store_at(tmp_path/'tamper')
    before = store.export_bytes()
    with pytest.raises(cl4.CL4Error): accept(store, replace(proof(), **{field:value}))
    assert store.export_bytes() == before


def test_zero_accounting_cash_is_observable_but_no_zero_opening_posting(tmp_path):
    store = store_at(tmp_path/'zero')
    p = proof(positions(0)); before = store.export_bytes()
    assert p.cash.minor_units == 0
    with pytest.raises(cl4.CL4Error) as caught: accept(store, p)
    assert caught.value.reason is cl4.CL4Reason.OPENING_AMOUNT_UNSUPPORTED
    assert store.export_bytes() == before


def test_positive_cash_movement_is_a_real_reconciliation_difference(tmp_path):
    store = store_at(tmp_path/'delta')
    accept(store, proof(positions(1000)))
    r = cl4.reconcile_shadow_cash(store.export_bytes(), proof(positions(1010)),
                                  evaluated_at=LATER, identity_key=KEY)
    assert r.version == 3 and r.delta_minor_units == 10*10**9
    assert r.status is cl4.ReconciliationStatus.DISCREPANCY


def test_paired_mixed_opening_uses_rub_not_currency_valuation(exact_case, request):
    x = exact_case; a = x.c.execution_adapter
    projection = cl4.project_shadow_cash(a.cl7_ledger_store.export_bytes(),
        account_scope_sha256=a.cash_authority_manager.status().account_scope_sha256,
        environment=broker.BrokerEnvironment.SANDBOX, as_of=stamp(x.p.clock_at),
        identity_key=a.cl7_identity_key)
    record(x, request, ledger_cash_nano=projection.expected_cash.minor_units,
           currency_valuation_rub=2_000_000, rub_position=1_000_000)
    assert projection.expected_cash == Money('RUB', 1_000_000*10**9)
    assert projection.version == 3
    assert _desktop_cycle(x.c).status == 'QUEUED'
    assert _observed(x.c).execution_status == 'SUBMITTED' and x.p.order_calls == 1


def test_paired_foreign_revaluation_does_not_change_rub_or_block_dispatch(exact_case, request):
    x = exact_case; a = x.c.execution_adapter
    before = a.cl7_ledger_store.export_bytes()
    x.p.payload['totalAmountCurrencies'] = money(2_000_000)
    x.p.cash_positions_override = positions(1_000_000)
    x.p.cash_positions_override['money'].append(money(100, 'usd'))
    assert _desktop_cycle(x.c).status == 'QUEUED'
    status = _observed(x.c).execution_status
    record(x, request, execution_status=status)
    assert status == 'SUBMITTED' and x.p.order_calls == 1
    assert a.cl7_ledger_store.export_bytes() == before


def test_paired_actual_rub_drift_is_not_hidden_by_unchanged_valuation(exact_case, request):
    x = exact_case; a = x.c.execution_adapter
    before = a.cl7_ledger_store.export_bytes()
    x.p.wallet_rub -= 100
    assert _desktop_cycle(x.c).status == 'QUEUED'
    status = _observed(x.c).execution_status
    record(x, request, execution_status=status)
    assert status != 'SUBMITTED' and x.p.order_calls == 0
    assert a.cash_authority_manager.status().post_attempt_count == 0
    assert a.cl7_ledger_store.export_bytes() == before


def test_dispatch_does_not_reacquire_unused_getportfolio_accounting(exact_case, monkeypatch):
    x = exact_case
    def forbidden(*a, **kw): raise RuntimeError('UNUSED_VALUATION_RPC')
    monkeypatch.setattr(x.p, 'get_portfolio', forbidden)
    assert _desktop_cycle(x.c).status == 'QUEUED'
    assert _observed(x.c).execution_status == 'SUBMITTED' and x.p.order_calls == 1


def test_primary_cash_read_no_fallback_when_getpositions_fails(exact_case, monkeypatch):
    x = exact_case; calls = []
    def forbidden(*a, **kw):
        calls.append(True); raise RuntimeError('PRIVATE_BROKER_TEXT')
    monkeypatch.setattr(x.p, 'get_positions', forbidden)
    count = len(x.p.read_calls)
    assert _desktop_cycle(x.c).status == 'QUEUED'
    outcome = _observed(x.c)
    assert outcome.execution_status == 'CL7_BROKER_READ_FAILED'
    assert calls == [True] and 'PRIVATE_BROKER_TEXT' not in str(outcome)
    assert len(x.p.read_calls) == count and x.p.order_calls == 0


def test_broker_blocked_basis_is_explicitly_unsupported(exact_case):
    x = exact_case; x.p.cash_blocked_rub = 1
    assert _desktop_cycle(x.c).status == 'QUEUED'
    assert _observed(x.c).execution_status == 'CL7_RECONCILIATION_BLOCKED'
    assert x.p.order_calls == 0
    assert x.c.execution_adapter.cash_authority_manager.status().post_attempt_count == 0


def test_source_selection_tamper_does_not_downgrade_desktop(exact_case):
    x = exact_case
    x.c.execution_adapter.cash_authority_manager._cash_source_version = 2
    with pytest.raises(RuntimeError): _desktop_cycle(x.c)
    assert x.p.order_calls == 0


def test_cl5_own_budget_works_independently_of_withdrawal(exact_case):
    """STEP12 regression: own buying evidence is independent of withdrawal."""
    x = exact_case; x.withdraw['money'] = []
    assert _desktop_cycle(x.c).status == 'QUEUED'
    assert _observed(x.c).execution_status == 'SUBMITTED'
    assert x.p.order_calls == 1


def test_new_basis_ingests_actual_rub_movement_before_locked_proof(exact_case):
    x = exact_case; a = x.c.execution_adapter; old = a.cl7_ledger_store.snapshot()
    x.operations = [operation(x, 'INPUT', 100)]
    x.p.wallet_rub += 100
    x.withdraw['money'] = [money(x.p.wallet_rub)]
    # Valuation is deliberately not changed; new primary RUB is independent.
    x.advance()
    assert _desktop_cycle(x.c).status == 'QUEUED'
    assert _observed(x.c).execution_status == 'SUBMITTED'
    intent = x.c.central_order_coordinator.manager.state().blocking_intent
    dispatch_proof = LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof)
    assert dispatch_proof.ledger_revision > old.ledger_revision
    assert dispatch_proof.ledger_head_sha256 == a.cl7_ledger_store.snapshot().ledger_head_sha256
    assert len(json.loads(a.cl7_ledger_store.export_bytes())['transactions']) == 2
    assert x.p.order_calls == 1


@pytest.mark.parametrize('damage', ['plan_version', 'codec', 'proof_version'])
def test_opening_source_cannot_be_relabelled_after_plan_construction(tmp_path, damage):
    store = store_at(tmp_path/'plan-tamper')
    plan = cl4.prepare_from_now_opening(store.export_bytes(), proof(), evaluated_at=LATER, identity_key=KEY)
    before = store.export_bytes()
    with pytest.raises((cl4.CL4Error, persistence.PersistenceError)):
        if damage == 'plan_version': plan = replace(plan, version=2)
        elif damage == 'codec': plan = replace(plan, observation=replace(plan.observation, descriptor=cl4.CL4_OPENING_CODEC))
        else: plan = replace(plan, proof=replace(plan.proof, version=2))
        cl4.accept_from_now_opening(store, plan,
            confirmation=f'ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}',
            evaluated_at=LATER, identity_key=KEY)
    assert store.export_bytes() == before


@pytest.mark.parametrize('value', [True, 3.0, None, 1, 4])
def test_manager_source_version_is_exactly_typed(exact_case, value):
    manager = exact_case.c.execution_adapter.cash_authority_manager
    from trading_robot.runtime_cash_authority import CL7RuntimeError
    with pytest.raises(CL7RuntimeError):
        RuntimeCashAuthorityManager(manager.store, cash_source_version=value)
    assert exact_case.p.order_calls == 0


def test_final_own_funds_gate_still_runs_after_primary_rub_reconciliation(exact_case):
    x = exact_case
    raw = {'currency': 'rub', 'buyLimits': {
        'buyMoneyAmount': money(0), 'buyMaxMarketLots': '0'},
        'buyMarginLimits': {'buyMoneyAmount': money(10_000_000), 'buyMaxMarketLots': '1000000'}}
    # STEP12: leave the new preliminary CL5 scope valid, damage final locked read.
    from test_q7a_source_locked_own_funds import inject_at_final_own_funds_read
    inject_at_final_own_funds_read(x, raw)
    assert _desktop_cycle(x.c).status == 'QUEUED'
    assert _observed(x.c).execution_status == 'CL7_OWN_FUNDS_BLOCKED'
    authority = x.c.execution_adapter.cash_authority_manager.status()
    assert authority.post_attempt_count == 0 and x.p.order_calls == 0


def test_no_policy_manager_remains_explicit_legacy_source(exact_case):
    manager = RuntimeCashAuthorityManager(exact_case.c.execution_adapter.cash_authority_manager.store)
    assert manager.cash_source_version == 2
    # This is NOT permission to attach it to the guarded desktop composition.
    exact_case.c.execution_adapter.cash_authority_manager = manager
    with pytest.raises(RuntimeError): _desktop_cycle(exact_case.c)
    assert exact_case.p.order_calls == 0
