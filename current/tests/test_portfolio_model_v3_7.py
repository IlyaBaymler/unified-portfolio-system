from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    OwnershipStatus,
    PendingOrderState,
    PendingOrderStatus,
    PortfolioModelError,
    PortfolioState,
    PortfolioTarget,
    PositionOwnership,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
    SnapshotFreshness,
)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sample_state() -> PortfolioState:
    checked = timestamp()
    target = PortfolioTarget(
        instrument_id="uid-sber",
        target_lots=1,
        strategy_id="sma",
        config_hash="a" * 64,
        candle_time=checked,
    )
    owner = PositionOwnership(
        strategy_id="sma",
        config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_10_MIN",
    )
    result = ReconciliationResult(
        instrument_id="uid-sber",
        status=ReconciliationStatus.MATCHED,
        blocking=False,
        reasons=("ok",),
        actual_lots=1,
        target_lots=1,
        checked_at=checked,
    )
    return PortfolioState(
        version=1,
        account=AccountState(
            account_id="account-1",
            total_value=50_000,
            securities_value=275,
            expected_yield=1.2,
            cash_balances=(CashBalance("RUB", 49_725, 0),),
        ),
        snapshot_at=checked,
        generated_at=checked,
        freshness=SnapshotFreshness.FRESH,
        source="test",
        positions=(
            PositionState(
                instrument_id="uid-sber",
                figi="figi-sber",
                ticker="SBER",
                class_code="TQBR",
                asset_type="share",
                currency="rub",
                quantity=1,
                actual_lots=1,
                average_price=270,
                current_price=275,
                market_value=275,
                expected_yield=5,
                target=target,
                ownership=owner,
                ownership_status=OwnershipStatus.ATTRIBUTED,
                pending_orders=(),
                reconciliation=result,
                last_candle_time=checked,
            ),
        ),
        warnings=(),
        state_status="READY",
        blocking=False,
    )


def test_portfolio_state_round_trip_is_lossless():
    state = sample_state()
    restored = PortfolioState.from_dict(state.to_dict())
    assert restored == state
    assert restored.account.cash("rub").available == 49_725
    assert restored.position("uid-sber").target_lots == 1


def test_domain_objects_are_immutable():
    state = sample_state()
    with pytest.raises(FrozenInstanceError):
        state.blocking = True  # type: ignore[misc]


def test_empty_state_is_valid_bootstrap_document():
    state = PortfolioState.empty(account_id="")
    assert state.version == 2
    assert state.positions == ()
    assert state.freshness is SnapshotFreshness.UNAVAILABLE
    assert state.state_status == "EMPTY"


def test_invalid_schema_is_rejected():
    raw = sample_state().to_dict()
    raw["version"] = 99
    with pytest.raises(PortfolioModelError, match="Unsupported portfolio schema"):
        PortfolioState.from_dict(raw)


def test_timezone_is_required_for_snapshot_timestamp():
    raw = sample_state().to_dict()
    raw["snapshot_at"] = "2026-07-31T10:00:00"
    with pytest.raises(PortfolioModelError, match="timezone"):
        PortfolioState.from_dict(raw)


def test_duplicate_position_ids_are_rejected():
    state = sample_state()
    with pytest.raises(PortfolioModelError, match="Duplicate instrument_id"):
        PortfolioState(
            version=1,
            account=state.account,
            snapshot_at=state.snapshot_at,
            generated_at=state.generated_at,
            freshness=state.freshness,
            source=state.source,
            positions=(state.positions[0], state.positions[0]),
            warnings=(),
            state_status="READY",
            blocking=False,
        )


def test_pending_order_rejects_impossible_lot_counters():
    with pytest.raises(PortfolioModelError, match="lot counters"):
        PendingOrderState(
            order_request_id="order-1",
            instrument_id="uid-sber",
            direction="BUY",
            requested_lots=1,
            executed_lots=2,
            status=PendingOrderStatus.PARTIALLY_FILLED,
        )


def test_partial_fill_property_is_derived():
    order = PendingOrderState(
        order_request_id="order-1",
        instrument_id="uid-sber",
        direction="BUY",
        requested_lots=2,
        executed_lots=1,
        status=PendingOrderStatus.PARTIALLY_FILLED,
    )
    assert order.partial_fill is True
    assert order.active is True


def test_reconciliation_status_enforces_blocking():
    result = ReconciliationResult(
        instrument_id="uid",
        status=ReconciliationStatus.TARGET_MISMATCH,
        blocking=False,
        reasons=("mismatch",),
        actual_lots=1,
        target_lots=0,
        checked_at=timestamp(),
    )
    assert result.blocking is True


def test_cash_currency_is_normalized_and_unique():
    account = AccountState(
        account_id="a",
        total_value=1,
        securities_value=0,
        expected_yield=0,
        cash_balances=(CashBalance("RUB", 1),),
    )
    assert account.cash_balances[0].currency == "rub"
    with pytest.raises(PortfolioModelError, match="Duplicate cash currency"):
        AccountState(
            account_id="a",
            total_value=1,
            securities_value=0,
            expected_yield=0,
            cash_balances=(CashBalance("RUB", 1), CashBalance("rub", 2)),
        )
