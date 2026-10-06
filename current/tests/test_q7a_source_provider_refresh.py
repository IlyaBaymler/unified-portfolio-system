"""STEP5: real desktop factory + provider-shaped refresh, no snapshot injection.

Synthetic broker observations only; explicit existing post-fill target/Central
calls qualify those boundaries, NOT automatic desktop recovery or exact cash.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_q7a_source_desktop_flow import desktop_case, _desktop_cycle, _observed
from test_q7a_source_natural_cycle import ACCOUNT, NOW, UID
from trading_robot.portfolio_model import PendingOrderStatus, SnapshotFreshness


def money(value, currency="rub"):
    number = Decimal(str(value))
    units = int(number)
    return {"currency": currency, "units": str(units), "nano": int((number - units) * 10**9)}


def position(lots=1, *, include_lots=True):
    row = {"instrumentUid": UID, "figi": "figi-sber", "instrumentType": "share",
           "ticker": "SBER", "classCode": "TQBR", "quantity": money(lots * 10),
           "averagePositionPrice": money(105), "currentPrice": money(105)}
    if include_lots:
        row["quantityLots"] = money(lots)
    return row


def order(request="refresh-request-1", exchange="refresh-exchange-1", *, requested=1,
          executed=0, status="NEW", direction="BUY"):
    return {"orderRequestId": request, "orderId": exchange, "accountId": ACCOUNT,
            "instrumentUid": UID, "direction": "ORDER_DIRECTION_" + direction,
            "lotsRequested": str(requested), "lotsExecuted": str(executed),
            "executionReportStatus": "EXECUTION_REPORT_STATUS_" + status,
            "averagePositionPrice": money(105), "executedOrderPrice": money(105),
            "currency": "rub"}


def _manager(controller):
    reader = controller.cycle_source.portfolio_refresher
    return getattr(reader, "func", reader).__self__


@pytest.fixture
def refresh_case(desktop_case, monkeypatch):
    p = desktop_case.provider
    p.payload = {"accountId": ACCOUNT, "positions": [],
                 "totalAmountPortfolio": money(1_000_000),
                 "totalAmountCurrencies": money(1_000_000),
                 "totalAmountShares": money(0)}
    p.orders = []
    p.receipts = {}
    p.read_calls = []
    # Independent synthetic cash observations, never inferred from valuation.
    p.cash_read_calls = []
    p.wallet_rub = 1_000_000
    p.cash_blocked_rub = 0
    p.cash_positions_override = None
    p.cash_limits_override = None
    p.on_cash_positions = None
    p.on_cash_limits = None
    p.on_portfolio = None
    p.on_orders = None
    p.on_state = None
    p.market_open, p.post_behavior = True, "submitted"
    p.clock_at = NOW

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return p.clock_at if tz is not None else p.clock_at.replace(tzinfo=None)
    import trading_robot.portfolio_manager as pm
    import trading_robot.portfolio_adapters as pa
    import trading_robot.portfolio_reconciler as pr
    import trading_robot.central_order_manager as central
    import trading_robot.journal as journal
    monkeypatch.setattr(pm, "datetime", Clock)
    monkeypatch.setattr(pa, "datetime", Clock)
    monkeypatch.setattr(pr, "datetime", Clock)
    monkeypatch.setattr(journal, "datetime", Clock)
    monkeypatch.setattr(central, "_now", lambda: p.clock_at.isoformat())

    def get_portfolio(account):
        assert account == ACCOUNT
        p.read_calls.append("portfolio")
        raw = deepcopy(p.payload)
        if p.on_portfolio:
            p.on_portfolio()
        return raw
    def get_orders(account):
        assert account == ACCOUNT
        p.read_calls.append("orders")
        if p.on_orders:
            p.on_orders()
        return deepcopy(p.orders)
    def get_order_state(account, key, *, by_request_id=False):
        assert account == ACCOUNT
        p.read_calls.append(("state", key, by_request_id))
        if p.on_state:
            p.on_state()
        raw = p.receipts.get(key)
        if isinstance(raw, Exception):
            raise raw
        return deepcopy(raw)
    def get_positions(account):
        assert account == ACCOUNT
        p.cash_read_calls.append(("positions", account))
        raw = deepcopy(p.cash_positions_override) if p.cash_positions_override is not None else {
            "accountId": ACCOUNT, "money": [money(p.wallet_rub)],
            "blocked": [money(p.cash_blocked_rub)], "limitsLoadingInProgress": False,
            "securities": [], "futures": [], "options": [],
        }
        if p.on_cash_positions:
            p.on_cash_positions()
        return raw
    def get_max_lots(account, instrument_id, price=None):
        assert account == ACCOUNT and instrument_id in {UID, "uid-lkoh"}
        assert price is None
        p.cash_read_calls.append(("max_lots", account, instrument_id))
        raw = deepcopy(p.cash_limits_override) if p.cash_limits_override is not None else {
            "currency": "rub", "buyLimits": {"buyMoneyAmount": money(p.wallet_rub),
                "buyMaxLots": "1000000", "buyMaxMarketLots": "1000000"},
            "sellLimits": {"sellMaxLots": "1000000"},
            "buyMarginLimits": {"buyMoneyAmount": money(10_000_000)},
        }
        if p.on_cash_limits:
            p.on_cash_limits(instrument_id)
        return raw
    original_candles = p.get_candles
    def candles(*args, **kwargs):
        frame = original_candles(*args, **kwargs)
        frame.index += timedelta(hours=int((p.clock_at - NOW).total_seconds() // 3600))
        return frame
    monkeypatch.setattr(p, "get_candles", candles)
    original_post = p.post_order
    def post(*args, **kwargs):
        raw = original_post(*args, **kwargs)
        raw["orderId"] = "exchange-" + kwargs["order_id"]
        return raw
    for name, fn in (("get_portfolio", get_portfolio), ("get_orders", get_orders),
                     ("get_order_state", get_order_state), ("post_order", post),
                     ("get_positions", get_positions), ("get_max_lots", get_max_lots)):
        monkeypatch.setattr(p, name, fn, raising=False)
    c = desktop_case.compose()
    case = SimpleNamespace(c=c, p=p, manager=_manager(c), root=desktop_case.root)
    def advance():
        p.clock_at += timedelta(seconds=1)
        p.quote_at = p.clock_at
    def refresh():
        advance()
        return c.cycle_source.portfolio_refresher()
    case.advance, case.refresh = advance, refresh
    return case


def stage(case, lots, transaction=None):
    profile = next(p for p in case.c.profile_store.load_mode("SANDBOX_EXECUTION") if p.instrument_id == UID)
    case.advance()
    return case.manager.stage_confirmed_target(instrument_id=UID, target_lots=lots,
        strategy_id="sma", config_hash=profile.strategy_profile_hash,
        candle_interval="CANDLE_INTERVAL_HOUR", ticker="SBER", transaction_id=transaction)


def seed_pending(case, *, requested=1, executed=0, status="NEW"):
    case.p.orders = [order(requested=requested, executed=executed, status=status)]
    if executed:
        case.p.payload["positions"] = [position(executed)]
    return case.refresh()


def test_refresh_retires_cached_broker_pending_only_from_bound_terminal_receipt(refresh_case):
    x = refresh_case
    first = seed_pending(x)
    assert first.blocking
    x.p.orders = []
    x.p.payload["positions"] = [position(1)]
    x.p.receipts["refresh-exchange-1"] = order(executed=1, status="FILL")
    stage(x, 1)
    second = x.refresh()
    assert not second.blocking, second.to_dict()
    assert second.position(UID).actual_lots == 1
    assert second.position(UID).pending_orders[0].status is PendingOrderStatus.FILLED
    assert x.p.read_calls.count(("state", "refresh-exchange-1", False)) == 1
    assert x.p.order_calls == 0
    again = x.refresh()
    assert not again.blocking
    assert x.p.read_calls.count(("state", "refresh-exchange-1", False)) == 1


def test_refresh_derives_deprecated_lots_from_verified_metadata_not_share_count(refresh_case):
    x = refresh_case
    x.p.payload["positions"] = [position(1, include_lots=False)]
    state = x.refresh()
    assert state.position(UID).actual_lots == 1
    assert state.position(UID).quantity == 10
    assert state.blocking  # An observed long is NOT silently attributed to Strategy.


def test_refresh_rejects_concurrent_canonical_change_instead_of_rebasing_old_observation(refresh_case):
    x = refresh_case
    before = x.manager.repository.load(expected_account_id=ACCOUNT)
    def concurrent():
        x.manager.stage_confirmed_target(instrument_id=UID, target_lots=1, strategy_id="sma",
            config_hash="a" * 64, candle_interval="CANDLE_INTERVAL_HOUR")
    x.p.on_portfolio = concurrent
    from trading_robot.portfolio_repository import PortfolioRevisionConflictError
    with pytest.raises(PortfolioRevisionConflictError):
        x.refresh()
    after = x.manager.repository.load(expected_account_id=ACCOUNT)
    assert after.revision == before.revision + 1
    assert after.position(UID).target_lots == 1


@pytest.mark.parametrize("damage", ["missing_account", "wrong_account", "bad_positions", "duplicate", "uid",
    "fraction", "lots_mismatch", "quantity_bool", "quantity_nan", "currency", "price", "total_currency"])
def test_bad_observation_is_not_published(refresh_case, damage):
    x = refresh_case
    row = position()
    x.p.payload["positions"] = [row]
    if damage == "missing_account": x.p.payload.pop("accountId")
    elif damage == "wrong_account": x.p.payload["accountId"] = "PRIVATE_OTHER_ACCOUNT"
    elif damage == "bad_positions": x.p.payload["positions"] = {}
    elif damage == "duplicate": x.p.payload["positions"].append(deepcopy(row))
    elif damage == "uid": row["instrumentUid"] = "PRIVATE_OTHER_INSTRUMENT"
    elif damage == "fraction": row["quantity"] = money(5); row["quantityLots"] = money(.5)
    elif damage == "lots_mismatch": row["quantityLots"] = money(2)
    elif damage == "quantity_bool": row["quantity"]["units"] = True
    elif damage == "quantity_nan": row["quantity"]["units"] = "NaN"
    elif damage == "currency": row["averagePositionPrice"]["currency"] = "usd"
    elif damage == "price": row["currentPrice"]["nano"] = 10**9
    elif damage == "total_currency": x.p.payload["totalAmountPortfolio"]["currency"] = "usd"
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_") as exc:
        x.refresh()
    assert "PRIVATE" not in str(exc.value)
    assert x.manager.repository.path.read_bytes() == before
    assert x.p.order_calls == 0


@pytest.mark.parametrize("damage", ["missing", "timeout", "wrong_request", "wrong_exchange", "wrong_instrument", "wrong_account",
    "direction", "requested", "executed", "bool", "unknown_status", "fill_incomplete"])
def test_missing_active_order_requires_matching_state_not_absence(refresh_case, damage):
    x = refresh_case
    seed_pending(x)
    x.p.orders = []
    raw = order(executed=1, status="FILL")
    if damage == "missing": raw = None
    elif damage == "timeout": raw = TimeoutError("PRIVATE_PROVIDER_FAILURE")
    elif damage == "wrong_request": raw["orderRequestId"] = "PRIVATE_OTHER_REQUEST"
    elif damage == "wrong_exchange": raw["orderId"] = "PRIVATE_OTHER_ORDER"
    elif damage == "wrong_instrument": raw["instrumentUid"] = "uid-lkoh"
    elif damage == "wrong_account": raw["accountId"] = "PRIVATE_OTHER_ACCOUNT"
    elif damage == "direction": raw["direction"] = "ORDER_DIRECTION_SELL"
    elif damage == "requested": raw["lotsRequested"] = "2"
    elif damage == "executed": raw["lotsExecuted"] = "2"
    elif damage == "bool": raw["lotsExecuted"] = True
    elif damage == "unknown_status": raw["executionReportStatus"] = "PRIVATE_STATUS"
    elif damage == "fill_incomplete": raw["lotsExecuted"] = "0"
    x.p.receipts["refresh-exchange-1"] = raw
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_") as exc:
        x.refresh()
    assert "PRIVATE" not in str(exc.value)
    assert x.manager.repository.path.read_bytes() == before
    assert x.manager.repository.load(expected_account_id=ACCOUNT).blocking
    assert x.p.order_calls == 0


@pytest.mark.parametrize("terminal,executed", [("CANCELLED", 0), ("REJECTED", 0), ("CANCELLED", 1)])
def test_terminal_cancel_reject_partial_retains_history_not_execution_permission(refresh_case, terminal, executed):
    x = refresh_case
    seed_pending(x, requested=2)
    x.p.orders = []
    x.p.receipts["refresh-exchange-1"] = order(requested=2, executed=executed, status=terminal)
    if executed:
        x.p.payload["positions"] = [position(executed)]
    state = x.refresh()
    pending = state.position(UID).pending_orders[0]
    assert pending.executed_lots == executed
    assert not pending.active
    assert state.blocking is bool(executed)  # Partial long has no authorized owner yet.
    assert x.c.central_order_coordinator.manager.state().intents == ()
    assert x.p.order_calls == 0


@pytest.mark.parametrize("still_active", ["NEW", "PARTIALLYFILL"])
def test_order_state_still_active_keeps_blocker(refresh_case, still_active):
    x = refresh_case
    seed_pending(x, requested=2)
    x.p.orders = []
    x.p.receipts["refresh-exchange-1"] = order(requested=2, executed=int(still_active == "PARTIALLYFILL"), status=still_active)
    state = x.refresh()
    assert state.blocking
    assert state.position(UID).pending_orders[0].active


def test_duplicate_orders_are_not_silently_deduplicated(refresh_case):
    x = refresh_case
    x.p.orders = [order(), order()]
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError): x.refresh()
    assert x.manager.repository.path.read_bytes() == before


def test_metadata_drift_during_reads_does_not_publish(refresh_case):
    x = refresh_case
    path = x.root / "portfolio_risk_metadata.json"
    x.p.on_orders = lambda: path.write_bytes(b"changed")
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError): x.refresh()
    assert x.manager.repository.path.read_bytes() == before


def test_explicit_refresh_fill_hold_sell_flat_reentry_without_injected_snapshots(refresh_case):
    x = refresh_case
    initial = x.refresh()
    assert initial.state_status == "EMPTY" and initial.freshness is SnapshotFreshness.FRESH
    central = x.c.central_order_coordinator.manager
    risk = x.c.central_order_coordinator.risk_runtime
    for side in ("BUY", "SELL"):
        x.p.clock_at += timedelta(hours=1)
        x.p.target = int(side == "BUY")
        x.advance()
        outcome = _desktop_cycle(x.c)
        assert outcome.status == "QUEUED", outcome.to_dict()
        assert _observed(x.c).execution_status == "SUBMITTED"
        observed = next(i for i in central.state().intents if i.intent_id == outcome.intent_id)
        raw = order(outcome.intent_id, observed.broker_order_id, direction=side)
        x.p.orders = [raw]
        active = x.refresh()
        assert active.blocking
        assert _desktop_cycle(x.c).status == "ACCOUNT_BLOCKED"
        # External provider observations change, never repository.save(snapshot).
        x.p.orders = []
        x.p.payload["positions"] = [position()] if side == "BUY" else []
        x.p.payload["totalAmountCurrencies"] = money(1_000_000 - 1050 if side == "BUY" else 1_000_000)
        x.p.payload["totalAmountShares"] = money(1050 if side == "BUY" else 0)
        x.p.wallet_rub = 1_000_000 - 1050 if side == "BUY" else 1_000_000
        raw = dict(raw, lotsExecuted="1", executionReportStatus="EXECUTION_REPORT_STATUS_FILL")
        x.p.receipts[observed.broker_order_id] = raw
        # Existing explicit post-fill protocol. Refresh alone grants no ownership.
        stage(x, int(side == "BUY"), f"explicit-test-fill:{outcome.intent_id}")
        state = x.refresh()
        assert not state.blocking, state.to_dict()
        final = central.mark_reconciled(outcome.intent_id,
            portfolio_repository=x.manager.repository, outcome="FILLED", executed_lots=1,
            risk_runtime=risk, execution_price_rub=105.0, execution_price_source="SYNTHETIC_PROVIDER_PRICE")
        assert final.risk_execution_status == "RECORDED"
        # Repeated reads and HOLD do not replay accounting or submit another order.
        x.refresh()
        before_risk = risk.state_store.load_account(ACCOUNT).to_dict()
        assert _desktop_cycle(x.c).status == "NO_POSITION_CHANGE"
        after_risk = risk.state_store.load_account(ACCOUNT).to_dict()
        # HOLD performs a fresh Risk evaluation. Only evaluation/snapshot times
        # may advance; execution IDs, turnover and order count must not change.
        for field in ("last_evaluated_at", "last_snapshot_at"):
            before_risk.pop(field)
            after_risk.pop(field)
        assert after_risk == before_risk
    state = x.manager.repository.load(expected_account_id=ACCOUNT)
    assert state.position(UID).actual_lots == 0 and state.position(UID).target_lots == 0
    assert len(state.position(UID).pending_orders) == 2  # Terminal broker history retained.
    accounted = risk.state_store.load_account(ACCOUNT)
    assert accounted.daily_order_count == 2 and accounted.daily_turnover_rub == 2100.0
    assert len(accounted.recorded_execution_ids) == 2
    assert x.p.order_calls == 2
    x.p.target = 1
    x.p.clock_at += timedelta(hours=1)
    x.advance()
    assert _desktop_cycle(x.c).status == "QUEUED"
    assert x.p.order_calls == 3 and len(set(x.p.sent_ids)) == 3


def test_resolution_budget_checked_before_first_state_read(refresh_case):
    x = refresh_case
    x.p.orders = [order(f"r-{i}", f"b-{i}") for i in range(4)]
    x.refresh()
    x.p.orders = []
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_READ_BUDGET_EXCEEDED"):
        x.refresh()
    assert not any(isinstance(call, tuple) for call in x.p.read_calls)
    assert x.manager.repository.path.read_bytes() == before


def test_local_uncertain_pending_is_not_deleted_or_queried_as_broker_history(refresh_case):
    x = refresh_case
    seed_pending(x)
    repo = x.manager.repository
    state = repo.load(expected_account_id=ACCOUNT)
    position_state = state.position(UID)
    pending = replace(position_state.pending_orders[0], source="LOCAL", uncertain=True,
                      status=PendingOrderStatus.UNKNOWN)
    # Isolated safety corruption fixture, not part of the provider full-cycle.
    repo.save(replace(state, positions=(replace(position_state, pending_orders=(pending,)),)))
    x.p.orders = []
    result = x.refresh()
    assert result.blocking and result.position(UID).pending_orders[0].uncertain
    assert not any(isinstance(call, tuple) for call in x.p.read_calls)


def test_expired_acquisition_not_stamped_fresh(refresh_case, monkeypatch):
    import time
    x = refresh_case
    wall = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: wall[0])
    x.p.on_orders = lambda: wall.__setitem__(0, 131.0)
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_EXPIRED"):
        x.refresh()
    assert x.manager.repository.path.read_bytes() == before


@pytest.mark.parametrize("stage_name", ["portfolio", "orders"])
def test_provider_exception_cannot_publish_or_leak(refresh_case, monkeypatch, stage_name):
    x = refresh_case
    def failed(account):
        raise RuntimeError("PRIVATE_PROVIDER_FAILURE_ACCOUNT_RAW_PAYLOAD")
    monkeypatch.setattr(x.p, "get_" + stage_name, failed)
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_PROVIDER_UNAVAILABLE") as caught:
        x.refresh()
    assert "PRIVATE" not in str(caught.value)
    assert x.manager.repository.path.read_bytes() == before


@pytest.mark.parametrize("damage", ["wrong_binding", "decreased_execution", "bool_count", "unknown_status", "empty_order_id"])
def test_active_list_updates_are_validated_against_cached_identity(refresh_case, damage):
    x = refresh_case
    seed_pending(x, requested=3, executed=1, status="PARTIALLYFILL")
    raw = order(requested=3, executed=1, status="PARTIALLYFILL")
    if damage == "wrong_binding": raw["instrumentUid"] = "uid-lkoh"
    elif damage == "decreased_execution": raw["lotsExecuted"] = "0"; raw["executionReportStatus"] = "EXECUTION_REPORT_STATUS_NEW"
    elif damage == "bool_count": raw["lotsRequested"] = True
    elif damage == "unknown_status": raw["executionReportStatus"] = "PRIVATE_STATUS"
    elif damage == "empty_order_id": raw["orderId"] = ""
    x.p.orders = [raw]
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_"):
        x.refresh()
    assert x.manager.repository.path.read_bytes() == before


def test_request_only_cached_identity_uses_request_query(refresh_case):
    x = refresh_case
    seed_pending(x)
    repo = x.manager.repository
    state = repo.load(expected_account_id=ACCOUNT)
    pos = state.position(UID)
    repo.save(replace(state, positions=(replace(pos,
        pending_orders=(replace(pos.pending_orders[0], broker_order_id=None),)),)))
    x.p.orders = []
    x.p.receipts["refresh-request-1"] = order(status="CANCELLED")
    result = x.refresh()
    assert not result.blocking
    assert ("state", "refresh-request-1", True) in x.p.read_calls


def test_exchange_only_cached_identity_does_not_rekey_history(refresh_case):
    x = refresh_case
    raw = order()
    raw.pop("orderRequestId")
    x.p.orders = [raw]
    x.refresh()
    x.p.orders = []
    x.p.receipts["refresh-exchange-1"] = order(status="CANCELLED")
    result = x.refresh()
    assert not result.blocking
    assert len(result.position(UID).pending_orders) == 1
    assert result.position(UID).pending_orders[0].order_request_id == "refresh-exchange-1"


@pytest.mark.parametrize("flag", ["blocked", "blockedLots"])
def test_explicit_security_block_cannot_be_ignored(refresh_case, flag):
    x = refresh_case
    row = position()
    row[flag] = True if flag == "blocked" else money(10)
    x.p.payload["positions"] = [row]
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_SECURITY_BLOCKED"):
        x.refresh()
    assert x.manager.repository.path.read_bytes() == before


def test_refresh_without_confirmed_target_does_not_steal_external_position(refresh_case):
    x = refresh_case
    x.p.payload["positions"] = [position()]
    state = x.refresh()
    assert state.blocking and state.position(UID).ownership is None
    assert state.position(UID).reconciliation.status.value == "UNATTRIBUTED_OPEN_POSITION"
    assert _desktop_cycle(x.c).status not in {"QUEUED", "REAUTHORIZED", "REPLACED"}
    assert x.p.order_calls == 0


@pytest.mark.parametrize("duplicate_key", ["request", "exchange"])
def test_duplicate_cached_identity_is_not_collapsed(refresh_case, duplicate_key):
    x = refresh_case
    seed_pending(x)
    repo = x.manager.repository
    state = repo.load(expected_account_id=ACCOUNT)
    pos = state.position(UID)
    original = pos.pending_orders[0]
    duplicate = replace(original,
        order_request_id=(original.order_request_id if duplicate_key == "request" else "another-request"),
        broker_order_id=(original.broker_order_id if duplicate_key == "exchange" else "another-exchange"))
    # Isolated malformed-cache fixture: no fabricated snapshots in full cycle.
    repo.save(replace(state, positions=(replace(pos, pending_orders=(original, duplicate)),)))
    before = repo.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_OBSERVATION_DUPLICATE_CACHED_ORDER"):
        x.refresh()
    assert repo.path.read_bytes() == before
    assert not any(isinstance(call, tuple) for call in x.p.read_calls)
