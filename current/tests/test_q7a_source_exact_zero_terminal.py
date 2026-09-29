"""STEP19: bounded zero-terminal recovery, synthetic provider and real owners.

No live IO. Positive fees here are an explicit synthetic exchange-linked
BROKER_FEE profile, not a claim that any broker routinely charges zero fills.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money, position, stage
from test_q7a_source_exact_receipt import _pending, _raw
from test_q7a_source_cash_components import _export, _cash, _record
from test_q7a_source_settlement_closure import _close, _recompose
from test_q7a_source_exact_recovery_tick import _state
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState


def _zero(x, *, side='BUY', status='CANCELLED', fee='0', intent=None):
    if side == 'SELL':
        x.p.payload['positions'] = [position()]
        x.p.payload['totalAmountShares'] = money(1050)
        x.p.payload['totalAmountPortfolio'] = money(1_001_050)
        stage(x, 1, 'synthetic-preowned-long')
        x.refresh()
        x.p.target = 0
    if intent is None:
        intent = _pending(x)
    assert intent.candidate.direction == side
    raw = _raw(x, intent, status=status, executed=0, commission=fee)
    if intent.broker_order_id is None:
        raw['orderId'] = 'exchange-' + intent.intent_id
    raw['executedOrderPrice'] = money(0)
    raw['averagePositionPrice'] = money(0)
    raw['orderDate'] = next(t.at for t in intent.transitions if t.status == 'IN_FLIGHT')
    x.operations[:] = []
    if Decimal(fee):
        x.operations.append({'id': 'SYNTHETIC_ZERO_FEE', 'brokerAccountId': ACCOUNT,
            'cursor': 'SYNTHETIC_ZERO_FEE_CURSOR', 'date': stamp(x.p.clock_at),
            'type': 'OPERATION_TYPE_BROKER_FEE', 'state': 'OPERATION_STATE_EXECUTED',
            'quantity': '0', 'quantityDone': '0', 'quantityRest': '0',
            'payment': money(-Decimal(fee)), 'commission': money(0), 'childOperations': [],
            'parentOperationId': raw['orderId'], 'instrumentUid': UID})
    x.p.wallet_rub = Decimal('1000000') - Decimal(fee)
    x.p.payload['totalAmountCurrencies'] = money(x.p.wallet_rub)
    x.p.payload['totalAmountPortfolio'] = money(x.p.wallet_rub + (1050 if side == 'SELL' else 0))
    x.c.set_connected(True)
    x.c.set_market_state('OPEN')
    x.advance()
    return intent, raw


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('status', ['CANCELLED', 'REJECTED'])
@pytest.mark.parametrize('fee', ['0', '0.123456789'])
def test_zero_terminal_gui_closes_without_execution(exact_case, side, status, fee, monkeypatch, request):
    x = exact_case
    intent, raw = _zero(x, side=side, status=status, fee=fee)
    a = x.c.execution_adapter
    p_before = x.manager.repository.load(expected_account_id=ACCOUNT).position(UID)
    risk_bytes = a.risk_runtime.state_store.path.read_bytes()
    export = _export(x)
    counts = x.p.order_calls, x.p.candle_calls, x.p.quote_calls
    from trading_robot import exact_settlement_closure as mod
    def forbidden(*args, **kwargs):
        pytest.fail('Zero terminal must not write Risk or record an execution')
    monkeypatch.setattr(mod, '_risk_after', forbidden)
    monkeypatch.setattr(a.risk_runtime.state_store, 'save_account_while_locked', forbidden)
    result = x.c.run_cycle()
    assert result.actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED', result.actions[0]
    final = a.manager.state().intents[-1]
    assert final.status == 'RECONCILED' and final.outcome == status
    assert final.executed_lots == 0 and final.risk_execution_id is None
    assert final.risk_execution_status == 'NOT_REQUIRED'
    assert final.candidate == intent.candidate and final.intent_id == intent.intent_id
    assert final.cl7_locked_dispatch_proof == intent.cl7_locked_dispatch_proof
    assert final.reserved_cash_kopecks == intent.reserved_cash_kopecks
    assert a.manager.state().reserved_cash_kopecks == 0 and a.manager.state().blocking_intent is None
    assert risk_bytes == a.risk_runtime.state_store.path.read_bytes()
    actual = x.manager.repository.load(expected_account_id=ACCOUNT).position(UID)
    expected_lots = 1 if side == 'SELL' else 0
    assert actual.actual_lots == actual.target_lots == expected_lots
    assert actual.ownership == (p_before.ownership if p_before else None)
    cash = _export(x)
    assert _cash(cash) == 10**15 - int(Decimal(fee) * 10**9)
    assert len(json.loads(cash)['transactions']) == (2 if Decimal(fee) else 1)
    if not Decimal(fee):
        assert cash == export  # No observation, transaction, revision or head change.
    authority = a.cash_authority_manager.status()
    assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert authority.pending_dispatch_proof_sha256 is None and authority.post_attempt_count == 1
    assert counts == (x.p.order_calls, x.p.candle_calls, x.p.quote_calls) and x.p.order_calls == 1
    after = _state(x)
    x.advance()
    assert _close(x, intent.cl7_locked_dispatch_proof_sha256).replay
    assert _state(x) == after
    request.node.user_properties.extend([('side', side), ('terminal', status), ('executed_lots', 0),
        ('fee_nano', str(int(Decimal(fee) * 10**9))), ('cash_nano', str(_cash(cash))),
        ('risk_unchanged', True), ('fake_posts', x.p.order_calls),
        ('ledger_transactions', len(json.loads(cash)['transactions'])),
        ('final_authority', authority.state.value)])


@pytest.mark.parametrize('fee', ['0', '0.123456789'])
def test_cash_result_is_not_owner_completion_and_replay_is_exact(exact_case, fee, monkeypatch):
    x = exact_case
    intent, _ = _zero(x, fee=fee)
    a = x.c.execution_adapter
    old = _state(x)
    if not Decimal(fee):
        def forbidden(*args, **kwargs):
            pytest.fail('No zero CL2 posting/observation is allowed')
        monkeypatch.setattr(a.cl7_ledger_store, 'append_transaction', forbidden)
        monkeypatch.setattr(a.cl7_ledger_store, 'append_observation', forbidden)
    result = _record(x)
    assert result.gross_nano == 0 and result.cash_delta_nano == -int(Decimal(fee) * 10**9)
    assert result.appended_transactions == (1 if Decimal(fee) else 0)
    assert not result.replay
    assert result.to_canonical_dict()['authority_clear_allowed'] is False
    assert _state(x)[:4] == old[:4]
    snapshot = _state(x)
    assert _record(x).replay
    assert _state(x) == snapshot and x.p.order_calls == 1


@pytest.mark.parametrize('damage', [
    'missing_commission', 'missing_service', 'nonzero_service', 'negative_commission',
    'fee_absent', 'fee_duplicate', 'fee_parent', 'fee_parent_request_id', 'fee_instrument',
    'fee_account', 'fee_quantity', 'fee_done', 'fee_rest', 'fee_has_trade', 'fee_child',
    'fee_summary', 'fee_payment', 'fee_currency', 'fee_canceled', 'fee_progress',
    'fee_type', 'fee_date_before_attempt', 'zero_fee_with_operation', 'gross_trade',
    'pagination', 'cash_mismatch', 'broker_blocked', '404', 'timeout',
    'receipt_request', 'receipt_order', 'receipt_has_trade', 'receipt_executed',
    'active_new', 'fake_fill', 'next_utc_day', 'next_moscow_day',
])
def test_invalid_zero_evidence_preserves_all_owners(exact_case, damage):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    from trading_robot.tbank_sandbox import TBankAPIError
    x = exact_case
    intent, raw = _zero(x, fee='0.123456789')
    fee = x.operations[0]
    if damage == 'missing_commission': raw.pop('executedCommission')
    elif damage == 'missing_service': raw.pop('serviceCommission')
    elif damage == 'nonzero_service': raw['serviceCommission'] = money(1)
    elif damage == 'negative_commission': raw['executedCommission'] = money(-1)
    elif damage == 'fee_absent': x.operations.clear()
    elif damage == 'fee_duplicate': x.operations.append(deepcopy(fee))
    elif damage == 'fee_parent': fee['parentOperationId'] = 'OTHER_EXCHANGE'
    elif damage == 'fee_parent_request_id': fee['parentOperationId'] = intent.intent_id
    elif damage == 'fee_instrument': fee['instrumentUid'] = 'OTHER_INSTRUMENT'
    elif damage == 'fee_account': fee['brokerAccountId'] = 'OTHER_ACCOUNT'
    elif damage == 'fee_quantity': fee['quantity'] = '10'
    elif damage == 'fee_done': fee['quantityDone'] = '10'
    elif damage == 'fee_rest': fee['quantityRest'] = '10'
    elif damage == 'fee_has_trade': fee['tradesInfo'] = {'trades': [{'num': 'MADE_UP'}]}
    elif damage == 'fee_child': fee['childOperations'] = [{'payment': money(-1)}]
    elif damage == 'fee_summary': fee['commission'] = money(1)
    elif damage == 'fee_payment': fee['payment'] = money(-1)
    elif damage == 'fee_currency': fee['payment']['currency'] = 'USD'
    elif damage == 'fee_canceled': fee['state'] = 'OPERATION_STATE_CANCELED'
    elif damage == 'fee_progress': fee['state'] = 'OPERATION_STATE_PROGRESS'
    elif damage == 'fee_type': fee['type'] = 'OPERATION_TYPE_OUTPUT'
    elif damage == 'fee_date_before_attempt':
        # Before the attempt but still included by the complete cash window.
        fee['date'] = x.c.execution_adapter.cash_authority_manager.status().operations_complete_through
        # Move the stored synthetic observation before the request when needed.
        from datetime import datetime
        start = datetime.fromisoformat(fee['date'].replace('Z', '+00:00'))
        fee['date'] = stamp(start - timedelta(microseconds=1))
    elif damage == 'zero_fee_with_operation': raw['executedCommission'] = money(0)
    elif damage == 'gross_trade':
        fee.update(type='OPERATION_TYPE_BUY', quantity='10', quantityDone='10', quantityRest='0')
    elif damage == 'pagination': x.operations_damage = 'invalid'
    elif damage == 'cash_mismatch': x.p.wallet_rub -= 1
    elif damage == 'broker_blocked': x.p.cash_blocked_rub = 1
    elif damage == '404': x.p.receipts[intent.intent_id] = TBankAPIError('PRIVATE_404', status_code=404, transient=False)
    elif damage == 'timeout': x.p.receipts[intent.intent_id] = TimeoutError('PRIVATE_TIMEOUT')
    elif damage == 'receipt_request': raw['orderRequestId'] = 'OTHER_REQUEST'
    elif damage == 'receipt_order': raw['orderId'] = 'OTHER_EXCHANGE'
    elif damage == 'receipt_has_trade':
        raw['stages'] = [{'quantity': '1', 'price': money(105), 'tradeId': 'OTHER_TRADE', 'executionTime': stamp(x.p.clock_at)}]
    elif damage == 'receipt_executed': raw['lotsExecuted'] = '1'
    elif damage == 'active_new': raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_NEW'
    elif damage == 'fake_fill': raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_FILL'
    elif damage == 'next_utc_day': x.p.clock_at += timedelta(days=1)
    elif damage == 'next_moscow_day': x.p.clock_at = x.p.clock_at.replace(hour=21, minute=0)
    before = _state(x)
    counts = x.p.order_calls, x.p.candle_calls, x.p.quote_calls
    with pytest.raises(ExactRecoveryTickError):
        x.c.run_cycle()
    assert _state(x) == before
    assert counts == (x.p.order_calls, x.p.candle_calls, x.p.quote_calls)
    assert not list(x.root.glob('exact_cash_components/*.json'))


@pytest.mark.parametrize('field', ['executedCommission', 'serviceCommission'])
def test_unknown_fee_can_become_explicit_zero_on_later_tick(exact_case, field):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    intent, raw = _zero(x)
    raw.pop(field)
    before = _state(x)
    with pytest.raises(ExactRecoveryTickError): x.c.run_cycle()
    assert _state(x) == before
    raw[field] = money(0)
    x.advance()
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert x.p.order_calls == 1


def test_positive_fee_must_arrive_as_an_operation_not_be_inferred(exact_case):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    _zero(x, fee='0.123456789')
    fee = x.operations.pop()
    before = _state(x)
    with pytest.raises(ExactRecoveryTickError): x.c.run_cycle()
    assert _state(x) == before
    x.operations.append(fee)
    x.advance()
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert _cash(_export(x)) == 999999876543211 and x.p.order_calls == 1


@pytest.mark.parametrize('cut', ['cash_plan', 'fee', 'closure_plan', 'portfolio', 'central', 'audit', 'authority'])
@pytest.mark.parametrize('recompose', [False, True])
def test_zero_fee_only_committed_prefix_recovers_once(exact_case, desktop_case, monkeypatch, cut, recompose):
    from trading_robot import exact_cash_settlement as cash, exact_settlement_closure as closure
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    intent, _ = _zero(x, status='REJECTED', fee='0.123456789')
    a = x.c.execution_adapter
    risk_before = a.risk_runtime.state_store.path.read_bytes()
    fired = []
    with monkeypatch.context() as mp:
        if cut == 'cash_plan': target, name = cash._PlanStore, 'create'
        elif cut == 'fee': target, name = a.cl7_ledger_store, 'append_transaction'
        elif cut == 'closure_plan': target, name = closure._ClosureStore, 'create'
        elif cut == 'portfolio': target, name = x.manager.transaction_coordinator, 'commit'
        elif cut == 'central': target, name = a.manager.store, '_save_unlocked'
        elif cut == 'audit': target, name = x.manager.transaction_coordinator, '_record'
        else: target, name = a.cash_authority_manager.store, '_commit_unlocked'
        original = getattr(target, name)
        def fail(*args, **kwargs):
            result = original(*args, **kwargs)
            wanted = cut != 'audit' or args[0] == 'EXACT_SETTLEMENT_ACCOUNTED'
            if wanted and not fired:
                fired.append(True)
                raise RuntimeError('SYNTHETIC_ZERO_COMMITTED_CUT')
            return result
        mp.setattr(target, name, fail)
        with pytest.raises(ExactRecoveryTickError): x.c.run_cycle()
    assert fired and x.p.order_calls == 1
    done = a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert done == (cut == 'authority')
    if recompose:
        _recompose(x, desktop_case)
        x.c.set_connected(True); x.c.set_market_state('OPEN')
    a = x.c.execution_adapter
    if not done:
        assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert risk_before == a.risk_runtime.state_store.path.read_bytes()
    assert _cash(_export(x)) == 999999876543211
    assert len(json.loads(_export(x))['transactions']) == 2
    assert a.manager.state().intents[-1].outcome == 'REJECTED'
    assert a.manager.state().intents[-1].risk_execution_status == 'NOT_REQUIRED'
    saved = _state(x)
    assert _close(x, intent.cl7_locked_dispatch_proof_sha256).replay
    assert _state(x) == saved and x.p.order_calls == 1


@pytest.mark.parametrize('status', ['CANCELLED', 'REJECTED'])
@pytest.mark.parametrize('surface', ['run_cycle', 'service_tick', 'cli'])
def test_zero_after_lost_ack_closes_without_repost(exact_case, monkeypatch, status, surface, capsys):
    from test_q7a_source_inflight_recovery import _lost_ack
    x = exact_case
    intent = _lost_ack(x, monkeypatch)
    intent, raw = _zero(x, status=status, intent=intent)
    x.p.on_portfolio = lambda: setattr(x.p, 'clock_at', x.p.clock_at + timedelta(microseconds=1))
    old_ledger = _export(x)
    risk_bytes = x.c.execution_adapter.risk_runtime.state_store.path.read_bytes()
    counts = x.p.order_calls, x.p.candle_calls, x.p.quote_calls
    if surface == 'run_cycle':
        assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    elif surface == 'service_tick':
        assert x.c.service_tick(now=x.p.clock_at, latest_closed_candles={}, hooks=object()).actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    else:
        from tools.v3_10_exact_settlement_recover import main
        assert main(['--runtime-dir', str(x.root), '--execution-order-type', 'MARKET']) == 0
        payload = capsys.readouterr().out
        assert json.loads(payload)['status'] == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
        assert all(private not in payload for private in (ACCOUNT, intent.intent_id, raw['orderId']))
    a = x.c.execution_adapter
    final = a.manager.state().intents[-1]
    assert final.outcome == status and final.intent_id == intent.intent_id
    assert final.candidate == intent.candidate and final.executed_lots == 0
    assert final.risk_execution_id is None and final.risk_execution_status == 'NOT_REQUIRED'
    assert [t.status for t in final.transitions] == ['QUEUED', 'IN_FLIGHT', 'SUBMITTED', 'RECONCILED']
    assert _export(x) == old_ledger and risk_bytes == a.risk_runtime.state_store.path.read_bytes()
    assert counts == (x.p.order_calls, x.p.candle_calls, x.p.quote_calls)
    assert a.cash_authority_manager.status().post_attempt_count == 1


@pytest.mark.parametrize('damage', ['rearm', 'legacy', 'reset_attempt', 'revision_only', 'head_only',
    'two_transactions', 'older_revision', 'watermark_same', 'watermark_future', 'opening', 'account'])
def test_zero_authority_transition_is_bounded(exact_case, damage):
    from dataclasses import replace
    from trading_robot.runtime_cash_authority import _transition_pair, CL7RuntimeError
    x = exact_case
    _zero(x)
    a = x.c.execution_adapter
    pending = a.cash_authority_manager.status()
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    closed = a.cash_authority_manager.status()
    assert closed.transition_kind == 'EXACT_ZERO_TERMINAL_CLOSED_DISARMED'
    assert closed.ledger_revision == pending.ledger_revision
    assert closed.ledger_head_sha256 == pending.ledger_head_sha256
    _transition_pair(pending, closed)
    changes = {}
    if damage == 'rearm': changes['state'] = RuntimeCashAuthorityState.EXACT_CASH_ARMED
    elif damage == 'legacy': changes['state'] = RuntimeCashAuthorityState.LEGACY_ACTIVE
    elif damage == 'reset_attempt': changes['post_attempt_count'] = 0
    elif damage == 'revision_only': changes['ledger_revision'] = closed.ledger_revision + 1
    elif damage == 'head_only': changes['ledger_head_sha256'] = 'a'*64
    elif damage == 'two_transactions': changes.update(ledger_revision=closed.ledger_revision+2, ledger_head_sha256='a'*64)
    elif damage == 'older_revision': changes['ledger_revision'] = closed.ledger_revision - 1
    elif damage == 'watermark_same': changes['operations_complete_through'] = pending.operations_complete_through
    elif damage == 'watermark_future': changes['operations_complete_through'] = stamp(x.p.clock_at+timedelta(seconds=1))
    elif damage == 'opening': changes['opening_record_sha256'] = 'b'*64
    elif damage == 'account': changes['account_scope_sha256'] = 'b'*64
    with pytest.raises(CL7RuntimeError):
        _transition_pair(pending, replace(closed, **changes))


@pytest.mark.parametrize('cut', ['cash_plan', 'closure_plan', 'central'])
@pytest.mark.parametrize('recompose', [False, True])
def test_zero_effect_plan_prefix_recovery_leaves_ledger_and_risk_bytes_unchanged(
    exact_case, desktop_case, monkeypatch, cut, recompose,
):
    from trading_robot import exact_cash_settlement as cash, exact_settlement_closure as closure
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    intent, _ = _zero(x)
    a = x.c.execution_adapter
    ledger, risk = _export(x), a.risk_runtime.state_store.path.read_bytes()
    fired = []
    with monkeypatch.context() as mp:
        target, name = ((cash._PlanStore, 'create') if cut == 'cash_plan' else
                        (closure._ClosureStore, 'create') if cut == 'closure_plan' else
                        (a.manager.store, '_save_unlocked'))
        original = getattr(target, name)
        def fail(*args, **kwargs):
            result = original(*args, **kwargs)
            if not fired:
                fired.append(True)
                raise RuntimeError('SYNTHETIC_ZERO_EFFECT_PREFIX')
            return result
        mp.setattr(target, name, fail)
        with pytest.raises(ExactRecoveryTickError): x.c.run_cycle()
    assert fired and _export(x) == ledger and a.risk_runtime.state_store.path.read_bytes() == risk
    if recompose:
        _recompose(x, desktop_case)
        x.c.set_connected(True); x.c.set_market_state('OPEN')
    a = x.c.execution_adapter
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert _export(x) == ledger and a.risk_runtime.state_store.path.read_bytes() == risk
    assert a.manager.state().intents[-1].risk_execution_id is None and x.p.order_calls == 1


from test_q7a_source_exact_recovery_tick import roundtrip_case


@pytest.mark.parametrize('status', ['CANCELLED', 'REJECTED'])
def test_same_candle_is_not_reissued_even_after_explicit_rearm(roundtrip_case, status):
    x = roundtrip_case
    a = x.c.execution_adapter
    x.c.run_cycle()
    intent = a.manager.state().blocking_intent
    assert intent is not None and intent.candidate.direction == 'BUY'
    _zero(x, status=status, intent=intent)
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    # No clock/candle advance, no change of Strategy signal. The operator arm
    # is separate from recovery and cannot erase the processed candle identity.
    a.cash_authority_manager.arm(raw_account_id=ACCOUNT, identity_key=a.cl7_identity_key,
        identity_key_id=a.cl7_identity_key_id, confirmation=a.cash_authority_manager.ARM_PHRASE,
        transition_at=a.cl7_clock())
    x.c.run_cycle()
    assert x.p.order_calls == 1 and len(a.manager.state().intents) == 1
    assert a.manager.state().blocking_intent is None


def test_zero_closure_preserves_existing_risk_halt_and_counters(exact_case):
    from dataclasses import replace
    x = exact_case
    _zero(x)
    a = x.c.execution_adapter
    store = a.risk_runtime.state_store
    risk = store.load_account(ACCOUNT)
    store.save_account(ACCOUNT, replace(risk, daily_order_count=2, daily_turnover_rub=125.5,
        recorded_execution_ids=('synthetic-previous-execution',), kill_switch_active=True,
        kill_switch_reason='synthetic halt'))
    before = store.path.read_bytes()
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert store.path.read_bytes() == before


def test_zero_close_still_holds_real_final_locks(exact_case, monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock, LockUnavailableError
    x = exact_case
    _zero(x)
    a = x.c.execution_adapter
    r = x.c.cycle_source.portfolio_recovery
    checked = []
    for target, name, label in [(a.manager.store, '_save_unlocked', 'central'),
                               (a.cash_authority_manager.store, '_commit_unlocked', 'authority')]:
        original = getattr(target, name)
        def wrapped(*args, _fn=original, _label=label, **kwargs):
            con = sqlite3.connect(a.cl7_ledger_store.root / 'store.sqlite3', timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    con.execute('BEGIN IMMEDIATE')
            finally:
                con.close()
            for path in [r.manager.repository.lock_path, r.risk.profile_store.lock_path,
                         r.risk.state_store.lock_path, a.manager.store.lock_path,
                         a.cash_authority_manager.store.lock_path, r.profiles.lock_path, r.runtimes.lock_path]:
                with pytest.raises(LockUnavailableError):
                    with InterProcessFileLock(path, timeout_seconds=0): pass
            checked.append(_label)
            return _fn(*args, **kwargs)
        monkeypatch.setattr(target, name, wrapped)
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert checked == ['central', 'authority']


def test_empty_stage_window_does_not_skip_same_day_check(exact_case):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    _zero(x)
    x.p.clock_at = x.p.clock_at.replace(hour=21)
    before = _state(x)
    with pytest.raises(ExactRecoveryTickError): x.c.run_cycle()
    assert _state(x) == before and x.p.order_calls == 1
