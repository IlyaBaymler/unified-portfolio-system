"""STEP9A: genuine CL2-CL7 owners, synthetic transport and disposable stores.

This qualifies wire decoding and the locked *sending* boundary only.  It does
not authorize broker IO or remove DesktopFillRecovery's exact-authority guard.
The test clock is explicit and shared; no evidence/proof builder is stubbed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_desktop_flow import desktop_case as base_desktop_case, _desktop_cycle, _observed
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot import broker_read_adapters as cl3
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot.cash_ledger_domain import Money
from trading_robot.runtime_cash_authority import (
    CL7RuntimeError, LockedDispatchProof, RuntimeCashAuthorityState,
)
from trading_robot.tbank_sandbox import TBankAPIError, TBankSandboxClient


@pytest.fixture
def desktop_case(base_desktop_case, request):
    # STEP10: exact positives now make an explicit MARKET choice. Historical
    # BESTPRICE defaults are tested separately and are never silently converted.
    if "test_default_bestprice" not in request.node.name:
        base_desktop_case.execution_order_type = "MARKET"
    return base_desktop_case


def stamp(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.%f') + '000Z'


@pytest.fixture
def exact_case(refresh_case, monkeypatch, request):
    x = refresh_case
    a = x.c.execution_adapter
    m = a.cash_authority_manager
    # Ledger stores are created by the real fixture. Their actual creation time
    # precedes this controlled clock (also used in the repository's CL7 suite).
    x.p.clock_at = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)
    x.p.quote_at = x.p.clock_at

    class ClockMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return x.p.clock_at if tz is not None else x.p.clock_at.replace(tzinfo=None)

    import trading_robot.portfolio_risk_runtime as risk_module
    monkeypatch.setattr(risk_module, 'datetime', Clock)
    x.wire_currency = getattr(request, 'param', 'rub')
    x.exact_calls, x.at_post, x.operations = [], [], []
    x.post_mode = 'accepted'
    x.operations_damage = None
    x.withdraw = {'money': [money(x.p.wallet_rub, x.wire_currency)],
                  'blocked': [], 'blockedGuarantee': []}
    original_portfolio = x.p.get_portfolio
    x.exact_reads = False

    def portfolio(account):
        body = original_portfolio(account)
        if x.exact_reads:
            body['totalAmountCurrencies']['currency'] = x.wire_currency
        return body

    def withdrawal(account):
        assert account == ACCOUNT
        x.exact_calls.append('withdraw')

        class StaticTransport:
            @staticmethod
            def _post(service, method, payload):
                assert service == 'SandboxService'
                assert method == 'GetSandboxWithdrawLimits'
                assert payload == {'accountId': ACCOUNT}
                return deepcopy(x.withdraw)

        return TBankSandboxClient.get_withdraw_limits(StaticTransport(), account)

    def operations(payload, timeout):
        x.exact_calls.append('operations')
        assert payload['accountId'] == ACCOUNT
        assert timeout > 0
        rows = [deepcopy(row) for row in x.operations
                if payload['from'] <= row['date'] < payload['to']]
        if x.operations_damage == 'invalid':
            return {'hasNext': False, 'items': 'NOT_A_LIST', 'nextCursor': ''}
        return {'hasNext': False, 'items': rows, 'nextCursor': ''}

    def post_once(*args, **kwargs):
        assert args[0] == ACCOUNT
        x.exact_calls.append('post_once')
        current = m.store._load_unlocked(allow_missing_legacy=False)
        assert current.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
        central = x.c.central_order_coordinator.manager.store._load_unlocked(
            expected_account_id=ACCOUNT)
        intent = central.blocking_intent
        assert intent.status == 'IN_FLIGHT'
        assert kwargs['order_id'] == intent.intent_id
        proof = LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof)
        proof.verify_identity(raw_intent_id=intent.intent_id, identity_key=a.cl7_identity_key)
        assert current.pending_dispatch_proof_sha256 == proof.sha256
        assert current.post_attempt_count == 1
        # A second genuine SQLite writer is excluded during the external call.
        con = sqlite3.connect(a.cl7_ledger_store.root / 'store.sqlite3', timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                con.execute('BEGIN IMMEDIATE')
        finally:
            con.close()
        x.at_post.append({'attempts': current.post_attempt_count,
                          'central_status': intent.status,
                          'authority_status': current.state.value,
                          'proof_sha256': proof.sha256,
                          'reserved_nano': proof.reserved_cash.minor_units,
                          'free_nano': proof.free_investable_cash.minor_units})
        if x.post_mode == 'rejected':
            x.p.order_calls += 1
            raise TBankAPIError('SYNTHETIC_PRIVATE_PROVIDER', status_code=400,
                transient=False, service='SandboxService', method='PostSandboxOrder',
                direct_response=True, redirect_followed=False)
        if x.post_mode == 'timeout':
            x.p.order_calls += 1
            raise TimeoutError('SYNTHETIC_PRIVATE_PROVIDER')
        result = x.p.post_order(*args, **kwargs)
        if x.post_mode == 'mismatch':
            result['orderRequestId'] = 'different-request'
        return result

    monkeypatch.setattr(x.p, 'get_portfolio', portfolio)
    monkeypatch.setattr(x.p, 'get_operations_by_cursor_once', operations, raising=False)
    monkeypatch.setattr(x.p, 'get_withdraw_limits', withdrawal, raising=False)
    monkeypatch.setattr(x.p, 'post_order_once', post_once, raising=False)
    a.cl7_clock = lambda: stamp(x.p.clock_at)
    a.cl7_monotonic_ns = lambda: 1
    a.cl7_wait_ns = lambda _: None
    x.refresh()
    x.exact_reads = True
    x.inputs = dict(ledger_store=a.cl7_ledger_store,
        portfolio_repository=x.c.portfolio_repository,
        risk_profile_store=x.c.central_order_coordinator.risk_runtime.profile_store,
        risk_state_store=x.c.central_order_coordinator.risk_runtime.state_store,
        central_manager=x.c.central_order_coordinator.manager,
        provider=x.p, raw_account_id=ACCOUNT, identity_key=a.cl7_identity_key,
        identity_key_id=a.cl7_identity_key_id, clock=a.cl7_clock,
        monotonic_ns=a.cl7_monotonic_ns, wait_ns=a.cl7_wait_ns)
    x.advance()
    prepared, preview = m.prepare_runtime(confirmation=m.PREPARE_PHRASE, **x.inputs)
    assert preview.context.status.value == 'READY_FOR_LOCKED_REVALIDATION'
    x.advance()
    m.confirm_runtime(confirmation=m.CONFIRM_PHRASE, runtime_dir=x.root, **x.inputs)
    x.advance()
    m.activate_runtime(confirmation=m.ACTIVATE_PHRASE, runtime_dir=x.root, **x.inputs)
    x.advance()
    m.arm(raw_account_id=ACCOUNT, identity_key=a.cl7_identity_key,
          identity_key_id=a.cl7_identity_key_id, confirmation=m.ARM_PHRASE,
          transition_at=a.cl7_clock())
    x.advance()
    x.calls_before_dispatch = len(x.exact_calls)
    yield x


@pytest.mark.parametrize('exact_case', ['rub', 'RUB'], indirect=True)
def test_real_cl2_to_cl7_dispatch_without_fake_proof(exact_case):
    x = exact_case
    a = x.c.execution_adapter
    assert a.cl7_proof_builder is None
    result = _desktop_cycle(x.c)
    observed = _observed(x.c)
    assert result.status == 'QUEUED'
    assert observed.execution_status == 'SUBMITTED'
    assert x.p.order_calls == 1
    assert x.exact_calls[x.calls_before_dispatch:] == ['operations', 'post_once']  # STEP12: CL5 never reads withdrawal
    assert x.at_post[0]['central_status'] == 'IN_FLIGHT'
    assert x.at_post[0]['reserved_nano'] > 0
    assert x.at_post[0]['free_nano'] >= 0
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    intent = x.c.central_order_coordinator.manager.state().blocking_intent
    assert intent.status == 'SUBMITTED'
    proof = LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof)
    snapshot = a.cl7_ledger_store.snapshot()
    assert proof.ledger_revision == snapshot.ledger_revision
    assert proof.ledger_head_sha256 == snapshot.ledger_head_sha256
    assert proof.portfolio_revision == x.c.portfolio_repository.load(expected_account_id=ACCOUNT).revision
    before = len(x.exact_calls)
    second = a.dispatch_next(x.c.portfolio_repository, expected_intent_id=intent.intent_id)
    assert second.status == 'CL7_DISPATCH_PENDING'
    assert x.p.order_calls == 1 and len(x.exact_calls) == before


@pytest.mark.parametrize('post_mode,status,central_status', [
    ('timeout', 'SUBMISSION_UNCERTAIN', 'UNCERTAIN'),
    ('mismatch', 'SUBMISSION_UNCERTAIN', 'UNCERTAIN'),
    ('rejected', 'SUBMISSION_REJECTED', 'FAILED'),
])
def test_real_chain_classifies_external_outcome_without_second_post(exact_case, post_mode, status, central_status):
    x = exact_case
    x.post_mode = post_mode
    result = _desktop_cycle(x.c)
    assert result.status == 'QUEUED'
    assert _observed(x.c).execution_status == status
    central = x.c.central_order_coordinator.manager.state()
    intent = central.intents[-1]
    assert intent.status == central_status
    assert x.p.order_calls == 1
    a = x.c.execution_adapter
    count = len(x.exact_calls)
    follow = a.dispatch_next(x.c.portfolio_repository, expected_intent_id=intent.intent_id)
    assert follow.status in {'IDLE', 'CL7_DISPATCH_PENDING'}
    assert len(x.exact_calls) == count and x.p.order_calls == 1
    assert a.cash_authority_manager.status().post_attempt_count == 1
    assert a.cash_authority_manager.status().state is (
        RuntimeCashAuthorityState.EXACT_CASH_ARMED if post_mode == 'rejected'
        else RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING)


@pytest.mark.parametrize('damage', [
    'own_zero', 'own_insufficient', 'own_foreign', 'primary_negative_blocked',
    'bad_operation_page', 'primary_cash_account', 'primary_cash_nano',
    'authority_disarmed', 'metadata', 'wrong_intent',
])
def test_real_chain_refuses_bad_cash_or_custody_before_attempt(exact_case, damage):
    x = exact_case
    a = x.c.execution_adapter
    # STEP12: damage the selected buying source, not obsolete withdrawal data.
    if damage in {'own_zero', 'own_insufficient', 'own_foreign'}:
        x.p.cash_limits_override = {'currency': 'usd' if damage == 'own_foreign' else 'rub',
            'buyLimits': {'buyMoneyAmount': money(0 if damage == 'own_zero' else 1),
                          'buyMaxMarketLots': '1000000'}}
    elif damage == 'primary_negative_blocked': x.p.cash_blocked_rub = -1
    elif damage == 'bad_operation_page': x.operations_damage = 'invalid'
    elif damage in {'primary_cash_account', 'primary_cash_nano'}:
        # STEP11: invalid data in the actual primary cash RPC, not unused valuation.
        x.p.cash_positions_override = {'accountId': ACCOUNT, 'money': [money(x.p.wallet_rub)], 'blocked': []}
        if damage == 'primary_cash_account': x.p.cash_positions_override['accountId'] = 'other'
        else: x.p.cash_positions_override['money'][0]['nano'] = True
    elif damage == 'authority_disarmed': a.cash_authority_manager.disarm(transition_at=a.cl7_clock())
    elif damage == 'metadata':
        (x.root / 'portfolio_risk_metadata.json').write_bytes(b'INVALID')
        with pytest.raises(RuntimeError): _desktop_cycle(x.c)
    if damage != 'metadata':
        if damage == 'wrong_intent':
            x.p.market_open = False
            assert _desktop_cycle(x.c).status == 'QUEUED'
            x.p.market_open = True
            assert a.dispatch_next(x.c.portfolio_repository,
                expected_intent_id='different').status == 'OPERATOR_INTENT_MISMATCH'
        else:
            assert _desktop_cycle(x.c).status == 'QUEUED'
            assert _observed(x.c).execution_status != 'SUBMITTED'
    assert x.p.order_calls == 0 and not x.at_post
    authority = a.cash_authority_manager.status()
    assert authority.post_attempt_count == 0 and authority.pending_dispatch_proof_sha256 is None


def operation(x, name, payment, quantity='0', commission=0):
    return {'brokerAccountId': ACCOUNT, 'childOperations': [],
            'commission': money(commission, x.wire_currency),
            'cursor': 'cursor-' + name, 'date': stamp(x.p.clock_at), 'id': 'operation-' + name,
            'payment': money(payment, x.wire_currency), 'quantity': quantity,
            'quantityDone': quantity, 'quantityRest': '0',
            'state': 'OPERATION_STATE_EXECUTED', 'type': 'OPERATION_TYPE_' + name}


def synchronize(x, *, commit=False):
    a = x.c.execution_adapter
    return a.cash_authority_manager.synchronize_operations(
        ledger_store=a.cl7_ledger_store, raw_account_id=ACCOUNT,
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        sync_to_exclusive=stamp(x.p.clock_at), transport=x.p.get_operations_by_cursor_once,
        monotonic_ns=lambda: 1, wait_ns=lambda _: None, absolute_deadline_ns=1000,
        retry_policy=cl3.RetryPolicy(1, 100, ()), transition_at=stamp(x.p.clock_at),
        commit_authority=commit)


@pytest.mark.parametrize('exact_case', ['rub', 'RUB'], indirect=True)
def test_real_cl3_cl2_signed_effects_replay_without_duplicate_ledger_transactions(exact_case):
    x = exact_case
    a = x.c.execution_adapter
    x.operations = [operation(x, 'INPUT', 1000), operation(x, 'OUTPUT', -250),
                    operation(x, 'BUY', -1050, '10'), operation(x, 'SELL', 1050, '10'),
                    operation(x, 'BROKER_FEE', '-2.1')]
    x.advance()
    before_authority = a.cash_authority_manager.status().canonical_bytes
    _, batch = synchronize(x, commit=False)
    assert all(d.kind.value == 'TRANSACTION_PROPOSED' for d in batch.decisions)
    once = a.cl7_ledger_store.export_bytes()
    synchronize(x, commit=False)
    assert a.cl7_ledger_store.export_bytes() == once
    assert a.cash_authority_manager.status().canonical_bytes == before_authority
    synchronize(x, commit=True)
    assert a.cl7_ledger_store.export_bytes() == once
    projection = cl4.project_shadow_cash(once,
        account_scope_sha256=a.cash_authority_manager.status().account_scope_sha256,
        environment=cl3.BrokerEnvironment.SANDBOX, as_of=stamp(x.p.clock_at),
        identity_key=a.cl7_identity_key)
    assert projection.complete
    assert projection.expected_cash == Money('RUB', 1_000_747_900_000_000)
    assert len(json.loads(once)['transactions']) == 6  # Opening + five independent operations.
    assert x.p.order_calls == 0


@pytest.mark.parametrize('damage', ['currency', 'nano', 'account', 'commission', 'partial'])
def test_operations_keep_rejection_or_review_not_fabricated_cash(exact_case, damage):
    x = exact_case
    row = operation(x, 'BUY', -1050, '10')
    if damage == 'currency': row['payment']['currency'] = 'usd'
    elif damage == 'nano': row['payment']['nano'] = True
    elif damage == 'account': row['brokerAccountId'] = 'other'
    elif damage == 'commission': row['commission'] = money('-2.1', 'rub')
    elif damage == 'partial': row['quantityDone'] = '5'; row['quantityRest'] = '5'
    x.operations = [row]
    x.advance()
    ledger = x.c.execution_adapter.cl7_ledger_store
    before = ledger.export_bytes()
    if damage in {'currency', 'nano', 'account'}:
        with pytest.raises(CL7RuntimeError): synchronize(x)
        assert ledger.export_bytes() == before
    else:
        _, batch = synchronize(x)
        assert batch.decisions[0].kind.value == 'REVIEW_REQUIRED'
        assert len(json.loads(ledger.export_bytes())['transactions']) == 1
    assert x.p.order_calls == 0


def test_exact_recovery_guard_is_not_removed_by_wire_correction(exact_case):
    x = exact_case
    assert _desktop_cycle(x.c).status == 'QUEUED'
    intent = x.c.central_order_coordinator.manager.state().blocking_intent
    from test_q7a_source_fill_recovery import _fill, _reconcile
    _fill(x, intent)
    before = x.c.central_order_coordinator.manager.state()
    with pytest.raises(RuntimeError):
        _reconcile(x)
    assert x.c.central_order_coordinator.manager.state() == before
    assert x.p.order_calls == 1
    assert x.c.execution_adapter.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING


def test_withdraw_available_is_not_double_debited_by_informational_blocked(exact_case):
    """Characterize CL5 semantics: money is an available field, not gross cash."""
    x = exact_case
    x.withdraw['blocked'] = [money(700)]
    assert _desktop_cycle(x.c).status == 'QUEUED'
    assert _observed(x.c).execution_status == 'SUBMITTED'
    at_post = x.at_post[0]
    assert at_post['free_nano'] == x.p.wallet_rub * 10**9 - at_post['reserved_nano']
    assert x.p.order_calls == 1


def test_equivalent_wire_case_replay_is_not_second_economic_operation(exact_case):
    x = exact_case
    x.operations = [operation(x, 'INPUT', 100)]
    x.advance()
    synchronize(x, commit=False)
    once = x.c.execution_adapter.cl7_ledger_store.export_bytes()
    for row in x.operations:
        row['payment']['currency'] = 'RUB'
        row['commission']['currency'] = 'RUB'
    synchronize(x, commit=False)
    assert x.c.execution_adapter.cl7_ledger_store.export_bytes() == once
    assert len(json.loads(once)['transactions']) == 2
    assert x.p.order_calls == 0


def test_locked_dispatch_consumes_real_nonempty_cl3_sync(exact_case):
    x = exact_case
    ledger = x.c.execution_adapter.cl7_ledger_store
    before = ledger.snapshot()
    x.operations = [operation(x, 'INPUT', 100)]
    x.p.wallet_rub += 100
    x.p.payload['totalAmountCurrencies'] = money(x.p.wallet_rub)
    x.p.payload['totalAmountPortfolio'] = money(x.p.wallet_rub)
    x.withdraw['money'] = [money(x.p.wallet_rub)]
    x.refresh()  # Genuine canonical/cash policies see the same synthetic deposit.
    result = _desktop_cycle(x.c)
    assert result.status == 'QUEUED'
    assert _observed(x.c).execution_status == 'SUBMITTED'
    intent = x.c.central_order_coordinator.manager.state().blocking_intent
    proof = LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof)
    assert proof.ledger_revision > before.ledger_revision
    assert proof.ledger_head_sha256 != before.ledger_head_sha256
    assert proof.ledger_head_sha256 == ledger.snapshot().ledger_head_sha256
    assert len(json.loads(ledger.export_bytes())['transactions']) == 2
    assert x.at_post[0]['free_nano'] == x.p.wallet_rub * 10**9 - x.at_post[0]['reserved_nano']
    assert x.p.order_calls == 1
