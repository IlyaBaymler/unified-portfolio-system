"""STEP18 terminal partial-cancel settlement. Provider and time are synthetic.

All owners, locks, proof builders, ledger and GUI recovery are real. The fixture
explicitly configures a three-lot strategy/risk cap BEFORE constructing runtime;
it never rewrites an admitted candidate or bypasses a financial authorization.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money, position, stage
from test_q7a_source_exact_receipt import _pending, _raw
from test_q7a_source_exact_recovery_tick import _state
from test_q7a_source_cash_components import _export, _cash
from test_q7a_source_settlement_closure import _close, _recompose
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.risk_persistence import RiskProfileStore
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState


@pytest.fixture
def desktop_case(base_desktop_case):
    x = base_desktop_case
    x.execution_order_type = "MARKET"
    profiles = MultiInstrumentProfileStore(x.root / "multi_instrument_profiles.json")
    values = profiles.load_mode("SANDBOX_EXECUTION")
    profiles.save_mode("SANDBOX_EXECUTION", tuple(replace(p,
        strategy_profile={**p.strategy_profile, "max_order_lots": 3}) for p in values))
    runtimes = InstrumentRuntimeStore(x.root / "instrument_runtimes.json")
    # New disposable configuration, before any actual runtime/proof/intent exists.
    runtimes.path.unlink()
    values = profiles.bootstrap_runtime_registry(mode="SANDBOX_EXECUTION", account_id=ACCOUNT,
                                                  runtime_store=runtimes)
    runtimes.save(tuple(replace(r, status="ACTIVE") for r in values))
    risk = RiskProfileStore(x.root / "risk_profiles.json")
    saved = risk.require_profile("SANDBOX_EXECUTION")
    risk.confirm_portfolio_policy("SANDBOX_EXECUTION", replace(saved['policy'], max_position_lots=3),
        confirmation="CONFIRM PORTFOLIO RISK POLICY", account_scope=saved['account_scope'])
    return x


def _partial(x, *, side="BUY", done=1, fee="0.123456789", status="CANCELLED", intent=None):
    if side == "SELL":
        x.p.payload['positions'] = [position(3)]
        x.p.payload['totalAmountShares'] = money(3150)
        x.p.payload['totalAmountPortfolio'] = money(1_003_150)
        stage(x, 3, 'synthetic-initial-three-lot-long')
        x.refresh()
        x.p.target = 0
    if intent is None:
        intent = _pending(x)
    assert intent.candidate.requested_lots == 3, intent.candidate
    assert intent.candidate.direction == side
    raw = _raw(x, intent, status=status, executed=done, commission=fee)
    if intent.broker_order_id is None:
        raw["orderId"] = "exchange-" + intent.intent_id
    qty = done * 10
    gross = Decimal('105.123456789') * qty
    trade = {'id': 'SYNTHETIC_PARTIAL_OPERATION', 'brokerAccountId': ACCOUNT, 'cursor': 'SYNTHETIC_CURSOR',
        'date': stamp(x.p.clock_at), 'type': 'OPERATION_TYPE_' + side,
        'state': 'OPERATION_STATE_EXECUTED', 'quantity': '30', 'quantityDone': str(qty),
        'quantityRest': str(30 - qty), 'payment': money(-gross if side == 'BUY' else gross),
        'commission': money(fee), 'childOperations': [], 'instrumentUid': UID, 'instrumentType': 'share',
        'tradesInfo': {'trades': [{'num': raw['stages'][0]['tradeId'], 'quantity': str(qty),
                                  'price': raw['stages'][0]['price'], 'date': stamp(x.p.clock_at)}]}}
    x.operations[:] = [trade]
    if Decimal(fee):
        x.operations.append({'id': 'SYNTHETIC_PARTIAL_FEE', 'brokerAccountId': ACCOUNT,
            'cursor': 'SYNTHETIC_FEE_CURSOR', 'date': stamp(x.p.clock_at),
            'type': 'OPERATION_TYPE_BROKER_FEE', 'state': 'OPERATION_STATE_EXECUTED',
            'quantity': '0', 'quantityDone': '0', 'quantityRest': '0',
            'payment': money(-Decimal(fee)), 'commission': money(0), 'childOperations': [],
            'parentOperationId': trade['id'], 'instrumentUid': UID})
    delta = (-gross if side == 'BUY' else gross) - Decimal(fee)
    x.p.wallet_rub = Decimal('1000000') + delta
    actual = done if side == 'BUY' else 3 - done
    x.p.payload['positions'] = [position(actual)]
    x.p.payload['totalAmountShares'] = money(1050 * actual)
    x.p.payload['totalAmountCurrencies'] = money(x.p.wallet_rub)
    x.p.payload['totalAmountPortfolio'] = money(x.p.wallet_rub + 1050 * actual)
    x.c.set_connected(True)
    x.c.set_market_state('OPEN')
    x.advance()
    return intent, raw, trade


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_terminal_partial_gui_books_only_execution_and_releases_residual(exact_case, side, request):
    x = exact_case
    intent, raw, trade = _partial(x, side=side)
    a = x.c.execution_adapter
    requested = deepcopy(intent.candidate.to_dict())
    counts = x.p.order_calls, x.p.candle_calls, x.p.quote_calls
    before_authority = a.cash_authority_manager.status()
    result = x.c.run_cycle()
    assert result.actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED', result.actions[0]
    assert counts == (x.p.order_calls, x.p.candle_calls, x.p.quote_calls)
    final = next(i for i in a.manager.state().intents if i.intent_id == intent.intent_id)
    p = x.manager.repository.load(expected_account_id=ACCOUNT).position(UID)
    actual = 1 if side == 'BUY' else 2
    assert p.actual_lots == p.target_lots == actual
    assert p.ownership.strategy_id == intent.candidate.strategy_id
    assert final.status == 'RECONCILED' and final.outcome == 'PARTIALLY_FILLED'
    assert final.executed_lots == 1 and final.candidate.to_dict() == requested
    assert final.reserved_cash_kopecks == intent.reserved_cash_kopecks
    assert a.manager.state().blocking_intent is None and not a.manager.state().queued
    assert a.manager.state().reserved_cash_kopecks == 0
    risk = a.risk_runtime.state_store.load_account(ACCOUNT)
    assert risk.daily_order_count == 1 and list(risk.recorded_execution_ids).count(intent.intent_id) == 1
    assert risk.daily_turnover_rub == pytest.approx(1051.23456789)
    authority = a.cash_authority_manager.status()
    assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert authority.post_attempt_count == before_authority.post_attempt_count == 1
    assert authority.pending_dispatch_proof_sha256 is None
    expected = 998948641975321 if side == 'BUY' else 1001051111111101
    assert _cash(_export(x)) == expected
    assert len(json.loads(_export(x))['transactions']) == 3
    assert x.p.order_calls == 1
    after = _state(x)
    repeat = _close(x, intent.cl7_locked_dispatch_proof_sha256)
    assert repeat.replay and _state(x) == after
    request.node.user_properties.extend([('side', side), ('requested_lots', 3), ('executed_lots', 1),
        ('actual_target_lots', actual), ('cash_nano', str(expected)), ('fake_posts', x.p.order_calls),
        ('risk_count', risk.daily_order_count), ('final_outcome', final.outcome)])


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('done', [1, 2])
@pytest.mark.parametrize('fee', ['0', '0.123456789'])
def test_supported_counts_and_zero_fee_no_fictitious_posting(exact_case, side, done, fee):
    x = exact_case
    intent, _, _ = _partial(x, side=side, done=done, fee=fee)
    a = x.c.execution_adapter
    reserved = a.manager.state().reserved_cash_kopecks
    assert reserved == intent.reserved_cash_kopecks
    assert reserved > 0 if side == 'BUY' else reserved == 0
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    state = a.manager.state()
    assert state.reserved_cash_kopecks == 0
    assert state.intents[-1].reserved_cash_kopecks == reserved
    assert state.intents[-1].executed_lots == done and state.intents[-1].outcome == 'PARTIALLY_FILLED'
    assert len(json.loads(_export(x))['transactions']) == (3 if Decimal(fee) else 2)
    gross = 1051234567890 * done
    expected = 10**15 + (-gross if side == 'BUY' else gross) - int(Decimal(fee) * 10**9)
    assert _cash(_export(x)) == expected and x.p.order_calls == 1


@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('done', [1, 2])
def test_active_partial_retains_every_owner_and_reserve_until_terminal(exact_case, side, done):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    intent, raw, _ = _partial(x, side=side, done=done, status='PARTIALLYFILL')
    a = x.c.execution_adapter
    before = _state(x)
    reserved = a.manager.state().reserved_cash_kopecks
    for _ in range(2):
        with pytest.raises(ExactRecoveryTickError):
            x.c.run_cycle()
        assert _state(x) == before and a.manager.state().reserved_cash_kopecks == reserved
        assert a.manager.state().blocking_intent == intent
        assert not list((a.manager.store.path.parent/'exact_cash_components').glob('*.json'))
    raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_CANCELLED'
    x.advance()
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert a.manager.state().reserved_cash_kopecks == 0 and x.p.order_calls == 1


@pytest.mark.parametrize('damage', [
    'active', 'zero', 'rejected_positive', 'cancel_all', 'new_positive', 'receipt_units',
    'receipt_requested', 'receipt_duplicate', 'receipt_price', 'receipt_trade', 'receipt_exchange',
    'requested_units', 'quantity_lots', 'quantity_done', 'quantity_rest', 'executed_only_profile',
    'operation_canceled', 'operation_progress', 'missing_trade', 'trade_units', 'trade_time',
    'wrong_direction', 'wrong_account', 'wrong_instrument', 'trade_payment_full', 'trade_payment_net',
    'fee_missing', 'fee_parent', 'fee_amount', 'fee_sign', 'fee_duplicate', 'commission_missing',
    'service_missing', 'service_nonzero', 'broker_cash', 'broker_blocked', 'pagination',
])
def test_invalid_or_incomplete_terminal_evidence_keeps_money_and_reserve(exact_case, damage):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    intent, raw, trade = _partial(x)
    a = x.c.execution_adapter
    if damage == 'active': raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_PARTIALLYFILL'
    elif damage == 'zero':
        raw.update(lotsExecuted='0', stages=[], executedCommission=money(0))
    elif damage == 'rejected_positive': raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_REJECTED'
    elif damage == 'cancel_all': raw['lotsExecuted'] = '3'; raw['stages'][0]['quantity'] = '3'
    elif damage == 'new_positive': raw['executionReportStatus'] = 'EXECUTION_REPORT_STATUS_NEW'
    elif damage == 'receipt_units': raw['lotsExecuted'] = '10'
    elif damage == 'receipt_requested': raw['lotsRequested'] = '4'
    elif damage == 'receipt_duplicate': raw['stages'] *= 2
    elif damage == 'receipt_price': raw['stages'][0]['price'] = raw['averagePositionPrice'] = money(106)
    elif damage == 'receipt_trade': raw['stages'][0]['tradeId'] = 'OTHER'
    elif damage == 'receipt_exchange': raw['orderId'] = 'OTHER'
    elif damage == 'requested_units': trade['quantity'] = '40'; trade['quantityRest'] = '30'
    elif damage == 'quantity_lots': trade.update(quantity='3', quantityDone='1', quantityRest='2')
    elif damage == 'quantity_done': trade.update(quantityDone='20', quantityRest='10')
    elif damage == 'quantity_rest': trade['quantityRest'] = '0'
    elif damage == 'executed_only_profile': trade.update(quantity='10', quantityRest='0')
    elif damage == 'operation_canceled': trade['state'] = 'OPERATION_STATE_CANCELED'
    elif damage == 'operation_progress': trade['state'] = 'OPERATION_STATE_PROGRESS'
    elif damage == 'missing_trade': trade.pop('tradesInfo')
    elif damage == 'trade_units': trade['tradesInfo']['trades'][0]['quantity'] = '1'
    elif damage == 'trade_time': trade['tradesInfo']['trades'][0]['date'] = stamp(x.p.clock_at)
    elif damage == 'wrong_direction': trade['type'] = 'OPERATION_TYPE_SELL'
    elif damage == 'wrong_account': trade['brokerAccountId'] = 'OTHER'
    elif damage == 'wrong_instrument': trade['instrumentUid'] = 'OTHER'
    elif damage == 'trade_payment_full': trade['payment'] = money('-3153.703703367')
    elif damage == 'trade_payment_net': trade['payment'] = money('-1051.358024679')
    elif damage == 'fee_missing': x.operations.pop()
    elif damage == 'fee_parent': x.operations[-1]['parentOperationId'] = 'OTHER'
    elif damage == 'fee_amount': x.operations[-1]['payment'] = money('-0.5')
    elif damage == 'fee_sign': x.operations[-1]['payment'] = money('0.123456789')
    elif damage == 'fee_duplicate': x.operations.append(deepcopy(x.operations[-1]))
    elif damage == 'commission_missing': raw.pop('executedCommission')
    elif damage == 'service_missing': raw.pop('serviceCommission')
    elif damage == 'service_nonzero': raw['serviceCommission'] = money(1)
    elif damage == 'broker_cash': x.p.wallet_rub -= 1
    elif damage == 'broker_blocked': x.p.cash_blocked_rub = 1
    elif damage == 'pagination': x.operations_damage = 'invalid'
    before = _state(x)
    counts = x.p.order_calls, x.p.candle_calls, x.p.quote_calls
    with pytest.raises(ExactRecoveryTickError):
        x.c.run_cycle()
    assert _state(x) == before
    assert counts == (x.p.order_calls, x.p.candle_calls, x.p.quote_calls)
    assert a.manager.state().blocking_intent == intent
    assert a.manager.state().reserved_cash_kopecks == intent.reserved_cash_kopecks
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    assert not list((a.manager.store.path.parent/'exact_cash_components').glob('*.json'))


@pytest.mark.parametrize('damage', ['position_requested', 'position_zero', 'open_order', 'ownership'])
def test_cash_record_does_not_override_invalid_holdings_or_ownership(exact_case, damage):
    from trading_robot.exact_settlement_closure import ExactSettlementClosureError
    from test_q7a_source_provider_refresh import order
    x = exact_case
    intent, raw, _ = _partial(x, side='SELL' if damage == 'ownership' else 'BUY')
    a = x.c.execution_adapter
    cash = a.record_exact_cash_components()
    if damage == 'position_requested': x.p.payload['positions'] = [position(3)]
    elif damage == 'position_zero': x.p.payload['positions'] = []
    elif damage == 'open_order': x.p.orders = [order()]
    else:
        before = x.manager.repository.load(expected_account_id=ACCOUNT)
        pos = before.position(UID)
        changed = replace(pos, ownership=replace(pos.ownership, strategy_id='OTHER'))
        x.manager.repository.save(replace(before, positions=(changed,)),
            expected_revision=before.revision, allow_equal_revision=True)
    before = _state(x)
    with pytest.raises(ExactSettlementClosureError):
        _close(x, cash.proof_sha256)
    assert _state(x) == before and x.p.order_calls == 1
    assert a.manager.state().blocking_intent == intent
    assert len(json.loads(_export(x))['transactions']) == 3


@pytest.mark.parametrize('cut', ['cash_trade', 'cash_fee', 'plan', 'portfolio', 'risk', 'central', 'audit', 'authority'])
@pytest.mark.parametrize('recompose', [False, True])
def test_partial_cancel_committed_prefix_recovers_without_double_charge(exact_case, desktop_case, monkeypatch, cut, recompose):
    from trading_robot import exact_settlement_closure as mod
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    intent, _, _ = _partial(x)
    a = x.c.execution_adapter
    fired = []
    with monkeypatch.context() as mp:
        if cut.startswith('cash'): target, name = a.cl7_ledger_store, 'append_transaction'
        elif cut == 'plan': target, name = mod._ClosureStore, 'create'
        elif cut == 'portfolio': target, name = x.manager.transaction_coordinator, 'commit'
        elif cut == 'risk': target, name = a.risk_runtime.state_store, 'save_account_while_locked'
        elif cut == 'central': target, name = a.manager.store, '_save_unlocked'
        elif cut == 'audit': target, name = x.manager.transaction_coordinator, '_record'
        else: target, name = a.cash_authority_manager.store, '_commit_unlocked'
        original = getattr(target, name)
        calls = [0]
        def fail(*args, **kwargs):
            result = original(*args, **kwargs)
            calls[0] += 1
            wanted = ((cut != 'audit' or args[0] == 'EXACT_SETTLEMENT_ACCOUNTED')
                      and (cut != 'cash_fee' or calls[0] == 2))
            if wanted and not fired:
                fired.append(True)
                raise RuntimeError('SYNTHETIC_AFTER_PARTIAL_COMMIT')
            return result
        mp.setattr(target, name, fail)
        with pytest.raises(ExactRecoveryTickError):
            x.c.run_cycle()
    assert fired and x.p.order_calls == 1
    done = a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert done == (cut == 'authority')
    if recompose:
        _recompose(x, desktop_case)
        x.c.set_connected(True)
        x.c.set_market_state('OPEN')
    a = x.c.execution_adapter
    if not done:
        assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    # Also re-read cash/closure through the explicit idempotent endpoint, including
    # the already-DISARMED cut; never re-arm or run another ordinary trading tick.
    before = _state(x)
    assert _close(x, intent.cl7_locked_dispatch_proof_sha256).replay
    assert _state(x) == before
    assert a.manager.state().intents[-1].outcome == 'PARTIALLY_FILLED'
    assert a.manager.state().reserved_cash_kopecks == 0
    assert a.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == (intent.intent_id,)
    assert _cash(_export(x)) == 998948641975321
    assert len(json.loads(_export(x))['transactions']) == 3 and x.p.order_calls == 1


def test_late_fee_arrival_after_partial_cancel_is_not_a_zero_fee(exact_case):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x = exact_case
    intent, _, _ = _partial(x)
    fee = x.operations.pop()
    before = _state(x)
    with pytest.raises(ExactRecoveryTickError):
        x.c.run_cycle()
    assert _state(x) == before
    x.operations.append(fee)
    x.advance()
    assert x.c.run_cycle().actions[0].status == 'EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert x.p.order_calls == 1 and x.c.execution_adapter.manager.state().intents[-1].outcome == 'PARTIALLY_FILLED'


@pytest.mark.parametrize('surface', ['run_cycle', 'service_tick', 'cli'])
def test_partial_cancel_after_lost_ack_preserves_request_and_attempt(exact_case, monkeypatch, surface, capsys):
    from datetime import timedelta
    from test_q7a_source_inflight_recovery import _lost_ack
    x = exact_case
    intent = _lost_ack(x, monkeypatch)
    intent, raw, _ = _partial(x, intent=intent)
    x.p.on_portfolio = lambda: setattr(x.p, 'clock_at', x.p.clock_at + timedelta(microseconds=1))
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
        assert ACCOUNT not in payload and intent.intent_id not in payload and raw['orderId'] not in payload
    a = x.c.execution_adapter
    final = a.manager.state().intents[-1]
    assert final.intent_id == intent.intent_id and final.candidate == intent.candidate
    assert final.outcome == 'PARTIALLY_FILLED' and final.executed_lots == 1
    assert final.broker_order_id == raw['orderId']
    assert [t.status for t in final.transitions] == ['QUEUED', 'IN_FLIGHT', 'SUBMITTED', 'RECONCILED']
    assert a.cash_authority_manager.status().post_attempt_count == 1
    assert counts == (x.p.order_calls, x.p.candle_calls, x.p.quote_calls)
    assert _cash(_export(x)) == 998948641975321
