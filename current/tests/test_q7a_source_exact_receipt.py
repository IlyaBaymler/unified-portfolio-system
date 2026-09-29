"""STEP13: exact receipt arithmetic + no settlement-free Central/authority clear.

Integration fixtures use genuine CL2/CL7/Central/Risk/desktop owners and one
synthetic dispatch. No test authorizes provider access or a real account.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case, _desktop_cycle
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money, order
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.runtime_cash_authority import CL7RuntimeError, RuntimeCashAuthorityState


def _pending(x):
    assert _desktop_cycle(x.c).status == 'QUEUED'
    intent = x.c.central_order_coordinator.manager.state().blocking_intent
    assert intent.status == 'SUBMITTED' and x.p.order_calls == 1
    x.advance()
    return intent


def _raw(x, intent, *, price='105.123456789', commission='0.123456789', service=0,
         status='FILL', executed=None):
    c = intent.candidate
    count = c.requested_lots if executed is None else executed
    raw = order(intent.intent_id, intent.broker_order_id, direction=c.direction,
        requested=c.requested_lots, executed=count, status=status)
    raw['orderType'] = 'ORDER_TYPE_' + c.order_type
    raw['averagePositionPrice'] = money(price)
    # Deliberately non-unit-valued field; it must never outrank actual stages.
    raw['executedOrderPrice'] = money('9999.999999999')
    raw['initialOrderPrice'] = money(5555)
    raw['stages'] = [] if not count else [{
        'tradeId': 'PRIVATE_SYNTHETIC_TRADE', 'quantity': str(count),
        'price': money(price), 'executionTime': stamp(x.p.clock_at),
    }]
    raw['executedCommission'] = money(commission)
    raw['serviceCommission'] = money(service)
    x.p.receipts[intent.intent_id] = raw
    return raw


def _snapshot(x):
    a = x.c.execution_adapter
    central = x.c.central_order_coordinator.manager
    risk = x.c.central_order_coordinator.risk_runtime
    return (x.manager.repository.path.read_bytes(), central.store.path.read_bytes(),
        risk.state_store.path.read_bytes(), a.cash_authority_manager.status().canonical_bytes,
        a.cl7_ledger_store.snapshot(), x.p.order_calls)


def _inspect(x):
    return x.c.execution_adapter.inspect_blocking_order()


def test_exact_inspection_uses_stage_unit_price_not_order_aggregate(exact_case, request):
    x = exact_case
    intent = _pending(x)
    raw = _raw(x, intent)
    before = _snapshot(x)
    untouched = deepcopy(raw)
    result = _inspect(x)
    request.node.user_properties.extend([
        ("observed_unit_price_rub", str(result.execution_price_rub)),
        ("price_source", str(result.execution_price_source)),
        ("suggested_reconciliation_outcome", str(result.suggested_reconciliation_outcome)),
        ("synthetic_post_count", x.p.order_calls),
    ])
    assert result.status == 'ORDER_OBSERVED', result.error
    assert result.execution_price_rub == pytest.approx(105.123456789, abs=1e-12)
    assert result.execution_price_source == 'GET_ORDER_STATE_STAGES'
    receipt = getattr(result, 'exact_receipt', None)
    assert receipt is not None
    assert receipt.gross_nano == 1_051_234_567_890
    assert receipt.average_price_rub == Fraction(105_123_456_789, 10**9)
    assert receipt.executed_commission_nano == 123_456_789
    assert receipt.expected_cash_delta_nano == -1_051_358_024_679
    assert result.suggested_reconciliation_outcome is None
    assert result.reconciliation_block_reason == 'EXACT_SETTLEMENT_REQUIRED'
    assert receipt.to_canonical_dict()['authority_clear_allowed'] is False
    assert _snapshot(x) == before and raw == untouched


def test_bound_full_fill_remains_pending_without_operations_and_ledger(exact_case):
    x = exact_case
    intent = _pending(x)
    _raw(x, intent, commission=0)
    before = _snapshot(x)
    first, second = _inspect(x), _inspect(x)
    assert first.status == second.status == 'ORDER_OBSERVED'
    assert first.suggested_reconciliation_outcome is None
    assert first.exact_receipt.receipt_identity_sha256 == second.exact_receipt.receipt_identity_sha256
    assert first.exact_receipt.to_canonical_dict()['settlement_verified'] is False
    assert _snapshot(x) == before
    with pytest.raises(CL7RuntimeError, match='RECOVERY_REQUIRED'):
        x.c.execution_adapter.cash_authority_manager.recover_runtime(
            central_manager=x.c.central_order_coordinator.manager,
            raw_account_id=ACCOUNT, identity_key=x.c.execution_adapter.cl7_identity_key,
            identity_key_id=x.c.execution_adapter.cl7_identity_key_id,
            transition_at=stamp(x.p.clock_at))
    assert _snapshot(x) == before


@pytest.mark.parametrize('damage', [
    'request', 'exchange', 'instrument', 'account', 'direction', 'currency', 'order_type',
    'missing_request', 'missing_exchange', 'missing_type', 'requested', 'executed',
    'bool_quantity', 'float_quantity', 'leading_zero', 'negative_quantity', 'status',
    'fill_zero', 'new_nonzero', 'partial_equals_total', 'missing_stages', 'empty_stages',
    'duplicate_trade', 'stage_quantity', 'stage_bool', 'stage_fraction', 'stage_zero',
    'stage_price_currency', 'stage_price_negative', 'stage_price_bool', 'stage_price_nano',
    'average', 'trade_future', 'trade_early', 'trade_time_invalid', 'trade_missing_id',
    'commission_currency', 'commission_negative', 'commission_bool', 'commission_null',
    'service_currency', 'huge_payload', 'nan_payload', 'not_object',
])
def test_exact_invalid_receipt_keeps_all_owners_and_never_suggests_close(exact_case, damage):
    x = exact_case
    intent = _pending(x)
    raw = _raw(x, intent)
    fields = {'request': 'orderRequestId', 'exchange': 'orderId', 'instrument': 'instrumentUid',
              'account': 'accountId', 'direction': 'direction', 'currency': 'currency',
              'order_type': 'orderType'}
    if damage in fields:
        raw[fields[damage]] = 'PRIVATE_WRONG_ID_OR_ENUM'
    elif damage == 'missing_request':
        raw.pop('orderRequestId')
    elif damage == 'missing_exchange':
        raw.pop('orderId')
    elif damage == 'missing_type':
        raw.pop('orderType')
    elif damage == 'requested':
        raw['lotsRequested'] = '2'
    elif damage == 'executed':
        raw['lotsExecuted'] = '2'
    elif damage == 'bool_quantity':
        raw['lotsExecuted'] = True
    elif damage == 'float_quantity':
        raw['lotsExecuted'] = 1.0
    elif damage == 'leading_zero':
        raw['lotsExecuted'] = '01'
    elif damage == 'negative_quantity':
        raw['lotsExecuted'] = '-1'
    elif damage == 'status':
        raw['executionReportStatus'] = 'PRIVATE_STATUS'
    elif damage == 'fill_zero':
        raw['lotsExecuted'] = '0'
    elif damage == 'new_nonzero':
        raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_NEW'
    elif damage == 'partial_equals_total':
        raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_PARTIALLYFILL'
    elif damage == 'missing_stages':
        raw.pop('stages')
    elif damage == 'empty_stages':
        raw['stages'] = []
    elif damage == 'duplicate_trade':
        raw['stages'] *= 2
    elif damage == 'stage_quantity':
        raw['stages'][0]['quantity'] = '2'
    elif damage == 'stage_bool':
        raw['stages'][0]['quantity'] = True
    elif damage == 'stage_fraction':
        raw['stages'][0]['quantity'] = '0.5'
    elif damage == 'stage_zero':
        raw['stages'][0]['quantity'] = '0'
    elif damage == 'stage_price_currency':
        raw['stages'][0]['price']['currency'] = 'usd'
    elif damage == 'stage_price_negative':
        raw['stages'][0]['price'] = money(-1)
    elif damage == 'stage_price_bool':
        raw['stages'][0]['price']['units'] = True
    elif damage == 'stage_price_nano':
        raw['stages'][0]['price']['nano'] = 10**9
    elif damage == 'average':
        raw['averagePositionPrice'] = money(100)
    elif damage == 'trade_future':
        raw['stages'][0]['executionTime'] = stamp(x.p.clock_at + timedelta(seconds=1))
    elif damage == 'trade_early':
        raw['stages'][0]['executionTime'] = '2001-01-01T00:00:00Z'
    elif damage == 'trade_time_invalid':
        raw['stages'][0]['executionTime'] = '2027-02-30T12:00:00Z'
    elif damage == 'trade_missing_id':
        raw['stages'][0].pop('tradeId')
    elif damage == 'commission_currency':
        raw['executedCommission']['currency'] = 'usd'
    elif damage == 'commission_negative':
        raw['executedCommission'] = money(-1)
    elif damage == 'commission_bool':
        raw['executedCommission']['nano'] = True
    elif damage == 'commission_null':
        raw['executedCommission'] = None
    elif damage == 'service_currency':
        raw['serviceCommission']['currency'] = 'usd'
    elif damage == 'huge_payload':
        raw['unknown'] = 'X' * 262_145
    elif damage == 'nan_payload':
        raw['executedCommission']['units'] = float('nan')
    elif damage == 'not_object':
        x.p.receipts[intent.intent_id] = []
    else: raise AssertionError(damage)
    before = _snapshot(x)
    result = _inspect(x)
    assert result.status == 'INSPECTION_UNCERTAIN'
    assert result.suggested_reconciliation_outcome is None
    assert getattr(result, 'exact_receipt', None) is None
    assert result.execution_price_rub is None
    assert 'PRIVATE' not in str(result.error)
    assert _snapshot(x) == before


@pytest.mark.parametrize('field,status', [
    ('executedCommission', 'EXECUTED_COMMISSION_UNKNOWN'),
    ('serviceCommission', 'SERVICE_COMMISSION_UNKNOWN'),
])
def test_absent_fee_is_unknown_not_initial_fee_or_zero(exact_case, field, status):
    x = exact_case
    intent = _pending(x)
    raw = _raw(x, intent)
    raw.pop(field)
    raw['initialCommission'] = money(0)
    before = _snapshot(x)
    result = _inspect(x)
    assert result.status == 'ORDER_OBSERVED', result.error
    receipt = getattr(result, 'exact_receipt', None)
    assert receipt is not None and receipt.fee_status == status
    assert receipt.expected_cash_delta_nano is None
    assert result.suggested_reconciliation_outcome is None
    assert _snapshot(x) == before


def test_service_fee_is_not_silently_added_or_double_counted(exact_case):
    x = exact_case
    intent = _pending(x)
    _raw(x, intent, commission=1, service=1)
    result = _inspect(x)
    assert result.status == 'ORDER_OBSERVED', result.error
    receipt = getattr(result, 'exact_receipt', None)
    assert receipt is not None
    assert receipt.fee_status == 'SERVICE_COMMISSION_OUT_OF_SCOPE'
    assert receipt.expected_cash_delta_nano is None
    assert result.suggested_reconciliation_outcome is None


@pytest.mark.parametrize('status', ['NEW', 'CANCELLED', 'REJECTED'])
def test_zero_fill_receipts_do_not_invent_price_fee_or_clear_permission(exact_case, status):
    x = exact_case
    intent = _pending(x)
    _raw(x, intent, executed=0, status=status, commission=0)
    before = _snapshot(x)
    result = _inspect(x)
    assert result.status == 'ORDER_OBSERVED', result.error
    assert result.execution_price_rub is None
    assert result.terminal is (status != 'NEW')
    assert result.suggested_reconciliation_outcome is None
    assert result.exact_receipt.gross_nano == 0
    assert result.exact_receipt.expected_cash_delta_nano == 0
    assert _snapshot(x) == before


@pytest.mark.parametrize('failure', ['not_found', 'timeout', 'private_provider_error'])
def test_read_failures_are_private_and_never_resubmit(exact_case, failure):
    from trading_robot.tbank_sandbox import TBankAPIError
    x = exact_case
    intent = _pending(x)
    if failure == 'not_found':
        error = TBankAPIError('PRIVATE_PROVIDER_ACCOUNT', status_code=404, transient=False)
    elif failure == 'private_provider_error':
        error = TBankAPIError('PRIVATE_PROVIDER_ACCOUNT', status_code=500, transient=True)
    else: error = TimeoutError('PRIVATE_PROVIDER_ACCOUNT')
    x.p.receipts[intent.intent_id] = error
    before = _snapshot(x)
    result = _inspect(x)
    assert result.status == ('NOT_FOUND_UNCERTAIN' if failure == 'not_found' else 'INSPECTION_UNAVAILABLE')
    assert result.suggested_reconciliation_outcome is None
    assert 'PRIVATE' not in str(result.error)
    assert _snapshot(x) == before


@pytest.mark.parametrize('damage', ['missing_policy', 'metadata_lot', 'wrong_key', 'wrong_key_id'])
def test_binding_failure_is_rejected_before_receipt_read(exact_case, damage):
    x = exact_case
    intent = _pending(x)
    _raw(x, intent)
    a = x.c.execution_adapter
    if damage == 'missing_policy':
        a.cl7_own_funds_policy = None
    elif damage == 'metadata_lot':
        policy = a.cl7_own_funds_policy
        rows = dict(policy.instruments)
        rows[UID] = replace(rows[UID], lot_size=1)
        a.cl7_own_funds_policy = replace(policy, instruments=rows)
    elif damage == 'wrong_key':
        a.cl7_identity_key = b'X' * 32
    elif damage == 'wrong_key_id':
        a.cl7_identity_key_id = 'DIFFERENT_KEY'
    before, reads = _snapshot(x), len(x.p.read_calls)
    result = _inspect(x)
    assert result.status == 'INSPECTION_UNCERTAIN'
    assert result.suggested_reconciliation_outcome is None
    assert len(x.p.read_calls) == reads
    assert _snapshot(x) == before


@pytest.mark.parametrize('damage', ['wall_forward', 'wall_backward', 'monotonic_forward', 'monotonic_backward', 'central_change'])
def test_read_time_and_custody_drift_cannot_become_a_settlement(exact_case, damage):
    x = exact_case
    intent = _pending(x)
    _raw(x, intent)
    a = x.c.execution_adapter
    after_hook = []
    def hook():
        if damage == 'wall_forward':
            x.p.clock_at += timedelta(seconds=6)
        elif damage == 'wall_backward':
            x.p.clock_at -= timedelta(seconds=1)
        elif damage == 'monotonic_forward':
            a.cl7_monotonic_ns = lambda: 6_000_000_002
        elif damage == 'monotonic_backward':
            a.cl7_monotonic_ns = lambda: 0
        elif damage == 'central_change':
            x.c.central_order_coordinator.manager.mark_uncertain(intent.intent_id, reason='external update')
        after_hook.append(_snapshot(x))
    x.p.on_state = hook
    result = _inspect(x)
    assert result.status == 'INSPECTION_UNCERTAIN'
    assert result.suggested_reconciliation_outcome is None
    assert _snapshot(x) == after_hook[0]


def test_generic_central_reconciliation_refuses_exact_before_risk_or_release(exact_case):
    x = exact_case
    intent = _pending(x)
    x.refresh()
    central = x.c.central_order_coordinator.manager
    before = _snapshot(x)
    with pytest.raises(RuntimeError, match='EXACT_SETTLEMENT_REQUIRED'):
        central.mark_reconciled(intent.intent_id, portfolio_repository=x.manager.repository,
            outcome='CANCELLED', executed_lots=0,
            risk_runtime=x.c.central_order_coordinator.risk_runtime)
    assert _snapshot(x) == before


def _legacy_terminal_record(x, intent):
    """Simulate a previously persisted position-only completion, not valid settlement."""
    central = x.c.central_order_coordinator.manager
    portfolio = x.manager.repository.load(expected_account_id=ACCOUNT)
    terminal = intent.transition('RECONCILED', detail='synthetic historical position-only completion',
        outcome='CANCELLED', executed_lots=0,
        reconciled_portfolio_revision=portfolio.revision,
        reconciled_portfolio_decision_checksum=portfolio.decision_sha256,
        reconciled_portfolio_snapshot_at=portfolio.snapshot_at,
        risk_execution_status='NOT_REQUIRED', risk_execution_id=None)
    central.store.mutate(ACCOUNT, lambda state: (state.replace_intent(terminal), terminal))
    return terminal


@pytest.mark.parametrize('path', ['restart_clear', 'same_process_clear'])
def test_preexisting_reconciled_exact_intent_is_not_cash_settlement(exact_case, path):
    from trading_robot.runtime_cash_authority import LockedDispatchProof
    x = exact_case
    intent = _pending(x)
    terminal = _legacy_terminal_record(x, intent)
    a = x.c.execution_adapter
    m = a.cash_authority_manager
    before = _snapshot(x)
    with pytest.raises(CL7RuntimeError, match='EXACT_SETTLEMENT_REQUIRED'):
        if path == 'restart_clear':
            m.recover_runtime(central_manager=x.c.central_order_coordinator.manager,
                raw_account_id=ACCOUNT, identity_key=a.cl7_identity_key,
                identity_key_id=a.cl7_identity_key_id, transition_at=stamp(x.p.clock_at))
        else:
            with m.store.locked():
                current = m.store._load_unlocked(allow_missing_legacy=False)
                m._clear_dispatch_locked(current,
                    proof=LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof),
                    central_intent=terminal, transition_at=stamp(x.p.clock_at))
    assert _snapshot(x) == before
    assert m.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    assert m.status().post_attempt_count == 1


def test_safe_inspection_does_not_export_money_or_raw_identifiers(exact_case):
    from tools.v3_10_runtime_cash_cutover import _safe_inspection
    x = exact_case
    intent = _pending(x)
    _raw(x, intent)
    result = _inspect(x)
    payload = _safe_inspection(result)
    assert payload.get('receipt_binding_verified') is True
    assert payload['settlement_verified'] is False
    assert payload['authority_clear_allowed'] is False
    serialized = json.dumps(payload)
    assert not any(secret in serialized for secret in
                   (ACCOUNT, UID, intent.intent_id, intent.broker_order_id, 'PRIVATE_SYNTHETIC_TRADE', '105.123'))
    assert set(payload) == {'executed_lots', 'provider_status', 'retryable', 'status', 'terminal',
        'reconciliation_block_reason', 'receipt_binding_verified', 'settlement_verified',
        'authority_clear_allowed', 'fee_status'}


@pytest.mark.parametrize("crash_cut", [False, True], ids=["submitted", "in_flight"])
def test_cli_recover_does_not_mutate_before_exact_settlement(exact_case, monkeypatch, capsys, crash_cut):
    from types import SimpleNamespace
    from tools import v3_10_runtime_cash_cutover as cli
    x = exact_case
    original = _pending(x)
    _raw(x, original, commission=0)
    central = x.c.central_order_coordinator.manager
    if crash_cut:
        # Exact saved pre-submitted crash cut; no provider replay or new POST.
        transitions = original.transitions[:-1]
        pending = replace(original, status="IN_FLIGHT", broker_order_id=None,
            updated_at=transitions[-1].at, transitions=transitions)
        central.store.mutate(ACCOUNT, lambda state: (state.replace_intent(pending), pending))
    a = x.c.execution_adapter
    closed = []
    runtime = SimpleNamespace(root=x.root, authority=a.cash_authority_manager,
        central=central, portfolio=x.manager.repository, raw_account=ACCOUNT,
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        adapter=lambda: a, ledger=SimpleNamespace(close=lambda: closed.append(True)), provider=None)
    # Only environment/secret bootstrap is replaced. Recovery, stores, Central,
    # authority, decoder and synthetic transport execute their real code paths.
    monkeypatch.setattr(cli, "_open_runtime", lambda *args, **kwargs: runtime)
    before = _snapshot(x)
    code = cli.main(["recover", "--runtime-dir", str(x.root)])
    payload = json.loads(capsys.readouterr().out)
    assert _snapshot(x) == before
    assert code == 2 and payload["status"] == "BLOCKED"
    assert "EXACT_SETTLEMENT_REQUIRED" in json.dumps(payload)
    assert closed == [True]
    assert central.state().blocking_intent.status == ("IN_FLIGHT" if crash_cut else "SUBMITTED")


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("lot_size", [1, 10, 100])
def test_receipt_value_object_keeps_multistage_arithmetic_rational(exact_case, direction, lot_size):
    # Pure value-object arithmetic, not a second authorized dispatch. The valid
    # one-lot integration receipt provides the shape; this does NOT attest a
    # broker execution or ledger posting for the substituted test economics.
    x = exact_case
    intent = _pending(x)
    _raw(x, intent)
    result = _inspect(x)
    assert result.status == "ORDER_OBSERVED"
    receipt = getattr(result, "exact_receipt", None)
    assert receipt is not None
    s = receipt.stages[0]
    stages = (replace(s, lots=1, price_nano=100_000_000_001),
              replace(s, trade_scope_sha256="b" * 64, lots=2, price_nano=100_000_000_002))
    receipt = replace(receipt, direction=direction, stages=stages, lot_size=lot_size,
                      requested_lots=3, executed_lots=3, executed_commission_nano=1)
    gross = 300_000_000_005 * lot_size
    assert receipt.gross_nano == gross
    assert receipt.average_price_rub == Fraction(300_000_000_005, 3 * 10**9)
    assert receipt.expected_cash_delta_nano == (-gross if direction == "BUY" else gross) - 1
    data = receipt.to_canonical_dict()
    assert data["average_price"] == {"weighted_nano": "300000000005", "lots_denominator": "3"}
    assert data["settlement_verified"] is False


def test_final_metadata_recheck_cannot_extend_receipt_freshness(exact_case):
    x = exact_case
    intent = _pending(x)
    _raw(x, intent)
    a = x.c.execution_adapter
    policy = a.cl7_own_funds_policy
    calls = []
    def guard():
        policy.binding_guard()
        calls.append(True)
        if len(calls) == 2:
            x.p.clock_at += timedelta(seconds=6)
    a.cl7_own_funds_policy = replace(policy, binding_guard=guard)
    before = _snapshot(x)
    result = _inspect(x)
    assert result.status == "INSPECTION_UNCERTAIN"
    assert result.exact_receipt is None and result.suggested_reconciliation_outcome is None
    assert _snapshot(x) == before
