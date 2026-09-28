"""STEP7: real desktop recovery hook, real refresher/owners, synthetic transport.

No test calls stage_confirmed_target or Central.mark_reconciled to finish a fill.
Negative cases must leave the persisted blocker, ownership and accounting intact.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest
from test_q7a_source_desktop_flow import desktop_case, _desktop_cycle, _observed
from test_q7a_source_provider_refresh import refresh_case, money, position, order
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.gui_runtime_controller import _CoordinatingHooks


def _reconcile(x):
    x.advance()
    now, latest, hooks = x.c.cycle_source()
    runtime = next(r for r in x.c.runtime_store.load(expected_account_id=ACCOUNT)
                   if r.config.instrument_id == UID)
    return _CoordinatingHooks(x.c, hooks).reconcile_portfolio(runtime, now)


def _pending(x, *, observed=False, uncertain=False):
    x.refresh()
    x.p.post_behavior = "ambiguous" if uncertain else "submitted"
    result = _desktop_cycle(x.c)
    intent = x.c.central_order_coordinator.manager.state().blocking_intent
    assert result.intent_id == intent.intent_id
    assert intent.status == ("UNCERTAIN" if uncertain else "SUBMITTED")
    if observed:
        x.p.orders = [order(intent.intent_id, intent.broker_order_id)]
        x.refresh()
        x.p.orders = []
    return intent


def _fill(x, intent):
    c = intent.candidate
    x.advance()
    raw = order(intent.intent_id, intent.broker_order_id or "uncertain-exchange",
        direction=c.direction, requested=c.requested_lots, executed=c.requested_lots, status="FILL")
    raw["stages"] = [{"tradeId": "trade-" + intent.intent_id,
        "quantity": str(c.requested_lots), "price": money(105),
        "executionTime": x.p.clock_at.isoformat()}]
    x.p.receipts[intent.intent_id] = raw
    x.p.receipts[raw["orderId"]] = raw
    x.p.orders = []
    x.p.payload["positions"] = [position(c.target_lots)] if c.target_lots else []
    x.p.wallet_rub = 1_000_000 - 1050 * c.target_lots
    x.p.payload["totalAmountCurrencies"] = money(x.p.wallet_rub)
    x.p.payload["totalAmountShares"] = money(1050 * c.target_lots)
    return raw


def _snapshot(x):
    return (x.manager.repository.path.read_bytes(),
        x.c.central_order_coordinator.manager.store.path.read_bytes(),
        x.c.central_order_coordinator.risk_runtime.state_store.path.read_bytes(), x.p.order_calls)


@pytest.mark.parametrize("observed", [False, True], ids=["fast-fill", "cached-active"])
def test_real_recovery_hook_finishes_same_buy_without_test_economic_calls(refresh_case, observed):
    x = refresh_case
    intent = _pending(x, observed=observed)
    _fill(x, intent)
    state = _reconcile(x)
    final = next(i for i in x.c.central_order_coordinator.manager.state().intents if i.intent_id == intent.intent_id)
    assert final.status == "RECONCILED"
    assert final.risk_execution_status == "RECORDED"
    assert state.position(UID).actual_lots == state.position(UID).target_lots == 1
    assert not state.blocking
    risk = x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT)
    assert risk.daily_turnover_rub == 1050 and risk.daily_order_count == 1
    assert risk.recorded_execution_ids == (intent.intent_id,)
    assert x.p.order_calls == 1
    assert x.p.read_calls.count(("state", intent.intent_id, True)) == 1
    assert x.p.read_calls.count(("state", intent.broker_order_id, False)) == 0
    assert state.position(UID).pending_orders[-1].order_request_id == intent.intent_id


def test_uncertain_resolved_by_bound_full_receipt_never_reposts(refresh_case):
    x = refresh_case
    intent = _pending(x, uncertain=True)
    _fill(x, intent)
    state = _reconcile(x)
    assert not state.blocking
    assert x.c.central_order_coordinator.manager.state().blocking_intent is None
    assert x.p.order_calls == 1
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == (intent.intent_id,)


def test_automatic_full_cycle_fast_buy_fast_sell_flat_reentry(refresh_case):
    x = refresh_case
    x.refresh()
    central = x.c.central_order_coordinator.manager
    risk = x.c.central_order_coordinator.risk_runtime
    ids = []
    for target in (1, 0):
        x.p.target = target
        x.p.clock_at += timedelta(hours=1)
        x.advance()
        result = _desktop_cycle(x.c)
        intent = central.state().blocking_intent
        assert result.status == "QUEUED" and intent.status == "SUBMITTED"
        ids.append(intent.intent_id)
        _fill(x, intent)
        state = _reconcile(x)
        assert central.state().blocking_intent is None
        assert not state.blocking
        assert state.position(UID).actual_lots == state.position(UID).target_lots == target
        saved = risk.state_store.load_account(ACCOUNT)
        assert _desktop_cycle(x.c).status == "NO_POSITION_CHANGE"
        for _ in range(2):
            _reconcile(x)
        now = risk.state_store.load_account(ACCOUNT)
        assert now.recorded_execution_ids == saved.recorded_execution_ids
        assert now.daily_turnover_rub == saved.daily_turnover_rub
        assert now.daily_order_count == saved.daily_order_count
        assert x.p.order_calls == len(ids)
    final = x.manager.repository.load(expected_account_id=ACCOUNT)
    assert final.position(UID).ownership is None
    assert final.account.cash("rub").available == 1_000_000
    assert risk.state_store.load_account(ACCOUNT).daily_turnover_rub == 2100
    x.p.target = 1
    x.p.clock_at += timedelta(hours=1)
    x.advance()
    assert _desktop_cycle(x.c).status == "QUEUED"
    ids.append(central.state().blocking_intent.intent_id)
    assert len(set(ids)) == x.p.order_calls == 3
    assert tuple(i.status for i in central.state().intents) == ("RECONCILED", "RECONCILED", "SUBMITTED")


@pytest.mark.parametrize("damage", ["missing", "timeout", "wrong_request", "wrong_exchange", "wrong_instrument",
    "wrong_account", "direction", "requested", "executed", "bool", "status", "missing_stages",
    "empty_stages", "duplicate_trade", "stage_quantity", "stage_fraction", "stage_bool", "coverage",
    "negative_price", "nan_price", "price_currency", "price_mismatch", "early_trade", "future_trade",
    "naive_trade", "missing_trade_id", "order_currency", "order_type", "too_many_stages"])
def test_invalid_receipt_keeps_same_blocker_and_never_adopts_position(refresh_case, damage):
    x = refresh_case
    intent = _pending(x)
    raw = _fill(x, intent)
    if damage == "missing": x.p.receipts.pop(intent.intent_id)
    elif damage == "timeout": x.p.receipts[intent.intent_id] = TimeoutError("PRIVATE_PROVIDER_SECRET")
    elif damage == "wrong_request": raw["orderRequestId"] = "PRIVATE_OTHER"
    elif damage == "wrong_exchange": raw["orderId"] = "PRIVATE_OTHER"
    elif damage == "wrong_instrument": raw["instrumentUid"] = "uid-lkoh"
    elif damage == "wrong_account": raw["accountId"] = "PRIVATE_OTHER"
    elif damage == "direction": raw["direction"] = "ORDER_DIRECTION_SELL"
    elif damage == "requested": raw["lotsRequested"] = "2"
    elif damage == "executed": raw["lotsExecuted"] = "2"
    elif damage == "bool": raw["lotsExecuted"] = True
    elif damage == "status": raw["executionReportStatus"] = "PRIVATE_BOGUS"
    elif damage == "missing_stages": raw.pop("stages")
    elif damage == "empty_stages": raw["stages"] = []
    elif damage == "duplicate_trade": raw["stages"] *= 2
    elif damage == "stage_quantity": raw["stages"][0]["quantity"] = "2"
    elif damage == "stage_fraction": raw["stages"][0]["quantity"] = "0.5"
    elif damage == "stage_bool": raw["stages"][0]["quantity"] = True
    elif damage == "coverage": raw["stages"][0]["quantity"] = "0"
    elif damage == "negative_price": raw["stages"][0]["price"] = money(-1)
    elif damage == "nan_price": raw["stages"][0]["price"]["units"] = "NaN"
    elif damage == "price_currency": raw["stages"][0]["price"] = money(105, "usd")
    elif damage == "price_mismatch": raw["averagePositionPrice"] = money(106)
    elif damage == "early_trade": raw["stages"][0]["executionTime"] = "2001-01-01T00:00:00Z"
    elif damage == "future_trade": raw["stages"][0]["executionTime"] = (x.p.clock_at + timedelta(hours=1)).isoformat()
    elif damage == "naive_trade": raw["stages"][0]["executionTime"] = "2026-08-13T12:00:00"
    elif damage == "missing_trade_id": raw["stages"][0].pop("tradeId")
    elif damage == "order_currency": raw["currency"] = "usd"
    elif damage == "order_type": raw["orderType"] = "ORDER_TYPE_LIMIT"
    elif damage == "too_many_stages": raw["stages"] *= 129
    before = _snapshot(x)
    with pytest.raises(RuntimeError, match="DESKTOP_FILL_RECOVERY_") as exc:
        _reconcile(x)
    assert "PRIVATE" not in str(exc.value)
    assert _snapshot(x) == before


@pytest.mark.parametrize("damage", ["actual_zero", "actual_extra", "foreign_order",
    "central_drift", "portfolio_drift", "metadata_drift", "cash_failure", "contradictory_active",
    "risk_drift", "timeout"])
def test_observation_and_owner_guards_do_not_release_blocker(refresh_case, monkeypatch, damage):
    x = refresh_case
    intent = _pending(x)
    raw = _fill(x, intent)
    if damage == "actual_zero": x.p.payload["positions"] = []
    elif damage == "actual_extra": x.p.payload["positions"] = [position(2)]
    elif damage == "foreign_order": x.p.orders = [order("other", "foreign")]
    elif damage == "central_drift":
        x.p.on_state = lambda: x.c.central_order_coordinator.manager.mark_uncertain(intent.intent_id, reason="synthetic drift")
    elif damage == "portfolio_drift":
        def drift():
            current = x.manager.repository.load(expected_account_id=ACCOUNT)
            x.manager.repository.save(replace(current, revision=current.revision + 1))
        x.p.on_portfolio = drift
    elif damage == "metadata_drift":
        x.p.on_orders = lambda: (x.root / "portfolio_risk_metadata.json").write_bytes(b"changed")
    elif damage == "cash_failure": x.p.cash_limits_override = {}
    elif damage == "contradictory_active": x.p.orders = [order(intent.intent_id, intent.broker_order_id)]
    elif damage == "risk_drift":
        def risk_drift():
            store = x.c.central_order_coordinator.risk_runtime.state_store
            store.update_account(ACCOUNT, lambda s: replace(s, kill_switch_active=True))
        x.p.on_state = risk_drift
    elif damage == "timeout":
        import trading_robot.desktop_fill_recovery as module
        ticks = [0.0]
        monkeypatch.setattr(module.time, "monotonic", lambda: ticks[0])
        x.p.on_state = lambda: ticks.__setitem__(0, 31.0)
    with pytest.raises(RuntimeError):
        _reconcile(x)
    assert x.c.central_order_coordinator.manager.state().blocking_intent is not None
    assert x.p.order_calls == 1
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == ()
    saved = x.manager.repository.load(expected_account_id=ACCOUNT)
    assert saved.position(UID) is None or saved.position(UID).ownership is None


def test_nonterminal_receipt_does_not_record_execution(refresh_case):
    x = refresh_case
    intent = _pending(x)
    raw = order(intent.intent_id, intent.broker_order_id)
    x.p.receipts[intent.intent_id] = raw
    x.p.orders = [raw]
    state = _reconcile(x)
    assert state.blocking
    assert x.c.central_order_coordinator.manager.state().blocking_intent.intent_id == intent.intent_id
    assert x.p.order_calls == 1
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == ()


def test_real_run_cycle_routes_pending_to_recovery_before_candles_or_strategy(refresh_case, monkeypatch):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    x.advance()
    runtime_bytes = x.c.runtime_store.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("Recovery tick must not read candles or quote or dispatch")
    monkeypatch.setattr(x.p, "get_candles", forbidden)
    monkeypatch.setattr(x.p, "get_last_prices", forbidden)
    monkeypatch.setattr(x.c.execution_adapter, "dispatch_next", forbidden)
    tick = x.c.run_cycle()
    assert len(tick.actions) == 1
    assert tick.actions[0].action == "FULL_FILL_RECOVERY"
    assert tick.actions[0].status == "RECONCILED_FULL_FILL"
    assert x.c.central_order_coordinator.manager.state().blocking_intent is None
    assert x.c.runtime_store.path.read_bytes() == runtime_bytes
    assert x.p.order_calls == 1


@pytest.mark.parametrize("point", ["after_canonical", "after_risk", "canonical_journal"])
@pytest.mark.parametrize("restart", [False, True], ids=["same-process", "recompose"])
def test_interrupted_accounting_resumes_same_intent_once(refresh_case, desktop_case, monkeypatch, point, restart):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    central = x.c.central_order_coordinator.manager
    if point == "after_canonical":
        owner, name = central, "mark_reconciled"
        original = owner.mark_reconciled
        def fail(*args, **kwargs):
            raise OSError("SYNTHETIC_STOP")
    elif point == "after_risk":
        owner, name = central.store, "mutate"
        original = owner.mutate
        def fail(*args, **kwargs):
            raise OSError("SYNTHETIC_STOP")
    else:
        owner, name = x.manager.transaction_coordinator, "_record"
        original = owner._record
        def fail(event, *args, **kwargs):
            if event == "CANONICAL_TRANSACTION_COMMITTED":
                raise OSError("SYNTHETIC_STOP")
            return original(event, *args, **kwargs)
    monkeypatch.setattr(owner, name, fail)
    with pytest.raises(RuntimeError):
        _reconcile(x)
    assert central.state().blocking_intent.intent_id == intent.intent_id
    state = x.manager.repository.load(expected_account_id=ACCOUNT)
    assert state.position(UID).actual_lots == state.position(UID).target_lots == 1
    assert state.last_transaction_id.startswith("desktop-full-fill:")
    partial_risk = x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT)
    assert partial_risk.daily_order_count == int(point == "after_risk")
    monkeypatch.setattr(owner, name, original)
    if restart:
        import desktop_gui
        from test_q7a_source_provider_refresh import _manager
        monkeypatch.setattr(desktop_gui, "_PRODUCTION_COMPOSITION", None)
        x.c = desktop_case.compose()
        x.manager = _manager(x.c)
    state = _reconcile(x)
    assert not state.blocking
    assert x.c.central_order_coordinator.manager.state().blocking_intent is None
    risk = x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT)
    assert risk.daily_turnover_rub == 1050
    assert risk.daily_order_count == 1 and risk.recorded_execution_ids == (intent.intent_id,)
    assert x.p.order_calls == 1
    for _ in range(2): _reconcile(x)
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).daily_order_count == 1


def test_recovery_does_not_attribute_unrelated_observed_long(refresh_case):
    x = refresh_case
    x.p.payload["positions"] = [position()]
    before_risk = x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids
    state = _reconcile(x)
    assert state.blocking and state.position(UID).ownership is None
    assert state.position(UID).target_lots is None
    assert x.c.central_order_coordinator.manager.state().intents == ()
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == before_risk
    assert not any(isinstance(call, tuple) and call[0] == "state" for call in x.p.read_calls)
    assert x.p.order_calls == 0


@pytest.mark.parametrize("change", ["price", "stage_id", "lost_checkpoint"])
def test_replay_requires_same_receipt_and_canonical_checkpoint(refresh_case, monkeypatch, change):
    x = refresh_case
    intent = _pending(x)
    raw = _fill(x, intent)
    central = x.c.central_order_coordinator.manager
    original = central.mark_reconciled
    def fail(*args, **kwargs): raise OSError("synthetic stop")
    monkeypatch.setattr(central, "mark_reconciled", fail)
    with pytest.raises(RuntimeError): _reconcile(x)
    monkeypatch.setattr(central, "mark_reconciled", original)
    if change == "price":
        raw["stages"][0]["price"] = money(106)
        raw["averagePositionPrice"] = money(106)
    elif change == "stage_id": raw["stages"][0]["tradeId"] = "different-trade"
    else:
        current = x.manager.repository.load(expected_account_id=ACCOUNT)
        x.manager.repository.save(replace(current, last_transaction_id="external-refresh"))
    before = _snapshot(x)
    with pytest.raises(RuntimeError): _reconcile(x)
    assert _snapshot(x) == before


@pytest.mark.parametrize("status", ["CANCELLED", "REJECTED"])
def test_zero_fill_terminal_reconciles_without_claiming_execution(refresh_case, status):
    x = refresh_case
    intent = _pending(x)
    raw = order(intent.intent_id, intent.broker_order_id, status=status)
    x.p.receipts[intent.intent_id] = raw
    _reconcile(x)
    central = x.c.central_order_coordinator.manager.state()
    assert central.blocking_intent is None
    final = next(i for i in central.intents if i.intent_id == intent.intent_id)
    assert final.status == "RECONCILED" and final.outcome == status
    assert final.executed_lots == 0 and final.risk_execution_status == "NOT_REQUIRED"
    assert final.risk_execution_id is None
    assert central.reserved_cash_kopecks == 0
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == ()
    assert x.p.order_calls == 1


def _multilot(x, desktop_case, monkeypatch):
    import desktop_gui
    profiles = x.c.profile_store.load_mode("SANDBOX_EXECUTION")
    profiles = x.c.profile_store.save_mode("SANDBOX_EXECUTION", tuple(
        replace(p, strategy_profile={**p.strategy_profile, "max_order_lots": 3}) for p in profiles))
    mapping = {p.instrument_id: p for p in profiles}
    runtimes = x.c.runtime_store.load(expected_account_id=ACCOUNT)
    x.c.runtime_store.save(tuple(replace(r, config=mapping[r.config.instrument_id].to_runtime_config(ACCOUNT))
                                 for r in runtimes))
    monkeypatch.setattr(desktop_gui, "_PRODUCTION_COMPOSITION", None)
    x.c = desktop_case.compose()
    # Explicit synthetic policy increase; production limits are not bypassed.
    risk = x.c.central_order_coordinator.risk_runtime
    policy = risk.profile_store.load_profile("SANDBOX_EXECUTION")["policy"]
    risk.profile_store.confirm_portfolio_policy(
        "SANDBOX_EXECUTION", replace(policy, max_position_lots=3),
        confirmation="CONFIRM PORTFOLIO RISK POLICY", account_scope=ACCOUNT)
    from test_q7a_source_provider_refresh import _manager
    x.manager = _manager(x.c)
    def refreshed():
        x.advance()
        return x.c.cycle_source.portfolio_refresher()
    x.refresh = refreshed


def test_multilot_stage_vwap_is_per_security_not_order_valuation(refresh_case, desktop_case, monkeypatch):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    assert intent.candidate.requested_lots == 3
    raw = _fill(x, intent)
    raw["stages"] = [{"tradeId": "a", "quantity": "1", "price": money(101), "executionTime": x.p.clock_at.isoformat()},
                     {"tradeId": "b", "quantity": "2", "price": money(107), "executionTime": x.p.clock_at.isoformat()}]
    raw["executedOrderPrice"] = money(3150)  # NEVER used as unit price.
    state = _reconcile(x)
    assert state.position(UID).actual_lots == state.position(UID).target_lots == 3
    saved = x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT)
    assert saved.daily_turnover_rub == 3150 and saved.daily_order_count == 1
    assert x.p.order_calls == 1


@pytest.mark.parametrize("status", ["PARTIALLYFILL", "CANCELLED"])
def test_partial_execution_keeps_owner_and_accounting_blocked(refresh_case, desktop_case, monkeypatch, status):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    raw = order(intent.intent_id, intent.broker_order_id, requested=3, executed=1, status=status)
    x.p.receipts[intent.intent_id] = raw
    x.p.orders = [raw] if status == "PARTIALLYFILL" else []
    x.p.payload["positions"] = [position(1)]
    state = _reconcile(x)
    assert state.blocking and state.position(UID).ownership is None
    assert x.c.central_order_coordinator.manager.state().blocking_intent.intent_id == intent.intent_id
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == ()
    assert x.p.order_calls == 1


def test_new_recovery_event_failure_stops_following_controller_cycles(refresh_case, monkeypatch):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    x.advance()
    original = x.c.journal.record
    def failing(event):
        if event.event_type == "GUI_FILL_RECOVERY": raise OSError("PRIVATE_JOURNAL_FAILURE")
        return original(event)
    monkeypatch.setattr(x.c.journal, "record", failing)
    with pytest.raises(RuntimeError, match="RECOVERY_AUDIT_UNAVAILABLE"):
        x.c.run_cycle()
    assert x.c.central_order_coordinator.manager.state().blocking_intent is None
    before = (list(x.p.read_calls), x.p.candle_calls, x.p.order_calls)
    with pytest.raises(RuntimeError): x.c.run_cycle()
    assert before == (x.p.read_calls, x.p.candle_calls, x.p.order_calls)


def test_recovery_event_has_finite_status_no_private_ids(refresh_case):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    x.advance()
    x.c.run_cycle()
    events = x.c.journal.recent(event_type="GUI_FILL_RECOVERY")
    assert len(events) == 1
    text = json.dumps(events[0]["payload"])
    assert ACCOUNT not in text and intent.intent_id not in text and intent.broker_order_id not in text
    assert events[0]["payload"]["provider_post_attempts"] == 0
    assert events[0]["payload"]["strategy_evaluated"] is False


@pytest.mark.parametrize("guard", ["portfolio", "intent"])
def test_exact_reconciliation_guard_checks_again_under_existing_owner_lease(refresh_case, monkeypatch, guard):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    central = x.c.central_order_coordinator.manager
    original = central.mark_reconciled
    def changed(*args, **kwargs):
        if guard == "portfolio":
            saved = x.manager.repository.load(expected_account_id=ACCOUNT)
            x.manager.repository.save(replace(saved, revision=saved.revision + 1))
        else:
            central.mark_uncertain(intent.intent_id, reason="synthetic concurrent change")
        return original(*args, **kwargs)
    monkeypatch.setattr(central, "mark_reconciled", changed)
    with pytest.raises(RuntimeError): _reconcile(x)
    assert central.state().blocking_intent is not None
    assert x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids == ()
    assert x.p.order_calls == 1


def test_exact_cash_intent_is_not_claimed_by_legacy_worker(refresh_case, monkeypatch):
    from types import SimpleNamespace
    from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    authority_store = x.c.cash_authority.store
    monkeypatch.setattr(authority_store, "load", lambda: SimpleNamespace(state=RuntimeCashAuthorityState.EXACT_CASH_ARMED))
    calls = list(x.p.read_calls)
    with pytest.raises(RuntimeError, match="EXACT_CASH_OUT_OF_SCOPE"): _reconcile(x)
    assert x.p.read_calls == calls
    assert x.c.central_order_coordinator.manager.state().blocking_intent.intent_id == intent.intent_id
    assert x.p.order_calls == 1


def test_queued_only_run_cycle_has_no_recovery_read_or_new_post(refresh_case):
    x = refresh_case
    x.refresh()
    x.p.market_open = False
    _desktop_cycle(x.c)
    assert x.c.central_order_coordinator.manager.state().queued
    calls = (list(x.p.read_calls), x.p.candle_calls, x.p.order_calls)
    with pytest.raises(RuntimeError, match="RECOVERY_REQUIRED"): x.c.run_cycle()
    assert (x.p.read_calls, x.p.candle_calls, x.p.order_calls) == calls


def test_own_cached_local_unknown_is_not_deleted_as_broker_fill(refresh_case):
    from trading_robot.portfolio_model import PendingOrderStatus
    x = refresh_case
    intent = _pending(x, observed=True)
    state = x.manager.repository.load(expected_account_id=ACCOUNT)
    pos = state.position(UID)
    pending = replace(pos.pending_orders[0], source="LOCAL", status=PendingOrderStatus.UNKNOWN, uncertain=True)
    x.manager.repository.save(replace(state, positions=(replace(pos, pending_orders=(pending,)),)))
    _fill(x, intent)
    before = _snapshot(x)
    with pytest.raises(RuntimeError, match="OTHER_PENDING_ORDER"): _reconcile(x)
    assert _snapshot(x) == before


@pytest.mark.parametrize("delay", [timedelta(days=1), timedelta(hours=12)])
def test_cross_accounting_day_fill_retains_blocker_instead_of_misdating_turnover(refresh_case, delay):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    x.p.clock_at += delay
    before = _snapshot(x)
    with pytest.raises(RuntimeError, match="ACCOUNTING_PERIOD_OUT_OF_SCOPE"):
        _reconcile(x)
    assert _snapshot(x) == before


def test_controller_recovery_ticks_close_buy_sell_without_candle_or_post_in_recovery(refresh_case):
    x = refresh_case
    x.refresh()
    central = x.c.central_order_coordinator.manager
    for target in (1, 0):
        x.p.target = target
        x.p.clock_at += timedelta(hours=1)
        x.advance()
        assert _desktop_cycle(x.c).status == "QUEUED"
        intent = central.state().blocking_intent
        _fill(x, intent)
        x.advance()
        counts = (x.p.candle_calls, x.p.order_calls)
        tick = x.c.run_cycle()
        assert len(tick.actions) == 1
        assert tick.actions[0].action == "FULL_FILL_RECOVERY"
        assert tick.actions[0].status == "RECONCILED_FULL_FILL"
        assert (x.p.candle_calls, x.p.order_calls) == counts
        assert central.state().blocking_intent is None
        assert _desktop_cycle(x.c).status == "NO_POSITION_CHANGE"
    assert x.p.order_calls == 2
    risk = x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT)
    assert risk.daily_order_count == 2 and risk.daily_turnover_rub == 2100


def test_service_tick_uses_bound_recovery_not_supplied_strategy_hook(refresh_case):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    x.advance()
    class NoStrategy:
        def __getattr__(self, name):
            raise AssertionError("Strategy hook must be unreachable during recovery")
    before = (x.p.candle_calls, x.p.order_calls)
    tick = x.c.service_tick(now=x.p.clock_at, latest_closed_candles={}, hooks=NoStrategy())
    assert tick.actions[0].status == "RECONCILED_FULL_FILL"
    assert before == (x.p.candle_calls, x.p.order_calls)


@pytest.mark.parametrize("when", ["before", "during"])
def test_persisted_stop_cannot_be_hidden_by_stale_active_scheduler(refresh_case, when):
    x = refresh_case
    intent = _pending(x)
    _fill(x, intent)
    def stopped():
        runtimes = x.c.runtime_store.load(expected_account_id=ACCOUNT)
        x.c.runtime_store.save(tuple(r.stop() for r in runtimes))
    if when == "before": stopped()
    else: x.p.on_state = stopped
    before = _snapshot(x)
    with pytest.raises(RuntimeError, match="CONFIGURED_SET_"):
        x.c.run_cycle()
    assert _snapshot(x) == before
