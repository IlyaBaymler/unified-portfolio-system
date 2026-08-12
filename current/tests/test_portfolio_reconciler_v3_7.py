from __future__ import annotations

from datetime import datetime, timezone

from trading_robot.portfolio_adapters import (
    BrokerPortfolioRecord,
    BrokerPositionRecord,
    RuntimePortfolioRecord,
    RuntimePositionRecord,
)
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    PendingOrderState,
    PendingOrderStatus,
    PortfolioState,
    PortfolioTarget,
    PositionOwnership,
    ReconciliationStatus,
    SnapshotFreshness,
)
from trading_robot.portfolio_reconciler import (
    PortfolioReconciler,
    ReconciliationContext,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def account(account_id: str = "account-1") -> AccountState:
    return AccountState(
        account_id=account_id,
        total_value=50_000,
        securities_value=275,
        expected_yield=1,
        cash_balances=(CashBalance("rub", 49_725),),
    )


def broker_position(*, lots: int = 1, pending=()) -> BrokerPositionRecord:
    return BrokerPositionRecord(
        instrument_id="uid-sber",
        figi="figi-sber",
        ticker="SBER",
        class_code="TQBR",
        asset_type="share",
        currency="rub",
        quantity=float(lots),
        actual_lots=lots,
        average_price=270,
        current_price=275,
        market_value=275 * lots,
        expected_yield=5,
        pending_orders=tuple(pending),
    )


def broker(*, positions=(), account_id: str = "account-1") -> BrokerPortfolioRecord:
    return BrokerPortfolioRecord(
        account=account(account_id),
        snapshot_at=now(),
        positions=tuple(positions),
        source_status="OK",
        warnings=(),
    )


def runtime_position(
    *,
    target_lots: int | None = 1,
    owner: bool = True,
    pending=(),
) -> RuntimePositionRecord:
    owner_state = (
        PositionOwnership(
            strategy_id="sma",
            config_hash="a" * 64,
            candle_interval="CANDLE_INTERVAL_10_MIN",
        )
        if owner
        else None
    )
    target = (
        PortfolioTarget(
            instrument_id="uid-sber",
            target_lots=target_lots,
            strategy_id="sma",
            config_hash="a" * 64,
            candle_time=now(),
        )
        if target_lots is not None
        else None
    )
    return RuntimePositionRecord(
        instrument_id="uid-sber",
        figi="figi-sber",
        ticker="SBER",
        class_code="TQBR",
        target=target,
        ownership=owner_state,
        pending_orders=tuple(pending),
        last_candle_time=now(),
        state_key="account-1|SBER_TQBR|CANDLE_INTERVAL_10_MIN|primary:sma:a",
    )


def runtime(*, positions=(), account_id: str = "account-1") -> RuntimePortfolioRecord:
    return RuntimePortfolioRecord(
        account_id=account_id,
        positions=tuple(positions),
        warnings=(),
        state_status="OK",
    )


def status(state: PortfolioState) -> ReconciliationStatus:
    return state.positions[0].reconciliation.status


def test_empty_portfolio_is_ready_and_non_blocking():
    state = PortfolioReconciler().reconcile(broker(), runtime())
    assert state.positions == ()
    assert state.state_status == "EMPTY"
    assert state.blocking is False


def test_matched_position_is_ready():
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(),)),
        runtime(positions=(runtime_position(),)),
    )
    assert status(state) is ReconciliationStatus.MATCHED
    assert state.blocking is False
    assert state.positions[0].ownership.strategy_id == "sma"


def test_multiple_positions_are_supported_read_only():
    second_broker = BrokerPositionRecord(
        instrument_id="uid-bond",
        figi="figi-bond",
        ticker="BOND1",
        class_code="TQOB",
        asset_type="bond",
        currency="rub",
        quantity=2,
        actual_lots=2,
        average_price=980,
        current_price=990,
        market_value=1980,
        expected_yield=20,
        pending_orders=(),
    )
    second_runtime = RuntimePositionRecord(
        instrument_id="uid-bond",
        figi="figi-bond",
        ticker="BOND1",
        class_code="TQOB",
        target=PortfolioTarget("uid-bond", 2),
        ownership=PositionOwnership("manual-model", "b" * 64, "CANDLE_INTERVAL_HOUR"),
        pending_orders=(),
        last_candle_time=now(),
        state_key="bond",
    )
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(), second_broker)),
        runtime(positions=(runtime_position(), second_runtime)),
    )
    assert {item.ticker for item in state.positions} == {"SBER", "BOND1"}
    assert all(item.reconciliation.status is ReconciliationStatus.MATCHED for item in state.positions)


def test_unattributed_open_position_is_blocking():
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(),)),
        runtime(),
    )
    assert status(state) is ReconciliationStatus.UNATTRIBUTED_OPEN_POSITION
    assert state.blocking is True


def test_target_without_owner_is_ownership_missing():
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(),)),
        runtime(positions=(runtime_position(owner=False),)),
    )
    assert status(state) is ReconciliationStatus.OWNERSHIP_MISSING


def test_target_mismatch_is_blocking():
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(lots=1),)),
        runtime(positions=(runtime_position(target_lots=0),)),
    )
    assert status(state) is ReconciliationStatus.TARGET_MISMATCH
    assert state.positions[0].reconciliation.actual_lots == 1
    assert state.positions[0].reconciliation.target_lots == 0


def test_active_pending_order_is_reported():
    order = PendingOrderState(
        order_request_id="order-1",
        instrument_id="uid-sber",
        direction="BUY",
        requested_lots=1,
        status=PendingOrderStatus.NEW,
    )
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(),)),
        runtime(positions=(runtime_position(pending=(order,)),)),
    )
    assert status(state) is ReconciliationStatus.PENDING_ORDER


def test_uncertain_pending_order_has_priority():
    order = PendingOrderState(
        order_request_id="order-1",
        instrument_id="uid-sber",
        direction="BUY",
        requested_lots=1,
        status=PendingOrderStatus.UNKNOWN,
        uncertain=True,
    )
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(),)),
        runtime(positions=(runtime_position(pending=(order,)),)),
    )
    assert status(state) is ReconciliationStatus.PENDING_ORDER_UNCERTAIN


def test_partial_fill_is_explicit():
    order = PendingOrderState(
        order_request_id="order-1",
        instrument_id="uid-sber",
        direction="BUY",
        requested_lots=2,
        executed_lots=1,
        status=PendingOrderStatus.PARTIALLY_FILLED,
    )
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(pending=(order,)),)),
        runtime(positions=(runtime_position(),)),
    )
    assert status(state) is ReconciliationStatus.PARTIAL_FILL


def test_stale_snapshot_blocks_even_matched_position():
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(),)),
        runtime(positions=(runtime_position(),)),
        context=ReconciliationContext(freshness=SnapshotFreshness.STALE),
    )
    assert status(state) is ReconciliationStatus.BROKER_SNAPSHOT_STALE


def test_account_mismatch_is_fail_closed():
    state = PortfolioReconciler().reconcile(
        broker(positions=(broker_position(),), account_id="broker-account"),
        runtime(positions=(runtime_position(),), account_id="runtime-account"),
        context=ReconciliationContext(expected_account_id="selected-account"),
    )
    assert status(state) is ReconciliationStatus.ACCOUNT_MISMATCH
    assert state.blocking is True


def test_external_manual_trade_is_detected_without_journal_evidence():
    reconciler = PortfolioReconciler()
    previous = reconciler.reconcile(
        broker(positions=(broker_position(lots=1),)),
        runtime(positions=(runtime_position(target_lots=1),)),
    )
    current = reconciler.reconcile(
        broker(positions=(broker_position(lots=2),)),
        runtime(positions=(runtime_position(target_lots=2),)),
        previous=previous,
    )
    assert status(current) is ReconciliationStatus.EXTERNAL_ACTIVITY_DETECTED


def test_journal_evidence_prevents_false_external_activity():
    reconciler = PortfolioReconciler()
    previous = reconciler.reconcile(
        broker(positions=(broker_position(lots=1),)),
        runtime(positions=(runtime_position(target_lots=1),)),
    )
    current = reconciler.reconcile(
        broker(positions=(broker_position(lots=2),)),
        runtime(positions=(runtime_position(target_lots=2),)),
        previous=previous,
        context=ReconciliationContext(
            journal_confirmed_instruments=frozenset({"uid-sber"})
        ),
    )
    assert status(current) is ReconciliationStatus.MATCHED


def test_runtime_only_nonzero_target_is_visible_as_mismatch():
    state = PortfolioReconciler().reconcile(
        broker(),
        runtime(positions=(runtime_position(target_lots=1),)),
    )
    assert state.positions[0].actual_lots == 0
    assert status(state) is ReconciliationStatus.TARGET_MISMATCH
