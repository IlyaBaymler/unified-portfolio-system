from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from trading_robot.portfolio_risk_evaluator import (
    PortfolioRiskEvaluator,
    build_portfolio_risk_metrics,
)
from trading_robot.portfolio_risk_model import (
    AssetClassConcentrationLimit,
    PortfolioRiskDecision,
    PortfolioRiskInput,
    PortfolioRiskInputError,
    PortfolioRiskPolicy,
    PortfolioRiskReservation,
    PositionRiskInput,
    ProposedPortfolioChange,
)
from trading_robot.portfolio_risk_sizing import (
    combine_lot_caps,
    lot_cap_for_incremental_budget,
    lot_cap_for_total_value,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def position(
    instrument_id: str = "figi-sber",
    *,
    lots: int = 1,
    price: float | None = 1_000.0,
    strategy: str | None = "primary",
    asset_class: str | None = "stock",
    price_at: datetime | None = NOW,
    price_source: str | None = "moex",
    reconciled: bool | None = True,
    lot_size: int = 10,
    currency: str = "rub",
) -> PositionRiskInput:
    return PositionRiskInput(
        instrument_id=instrument_id,
        ticker=instrument_id.removeprefix("figi-"),
        strategy_id=strategy,
        asset_class=asset_class,
        actual_lots=lots,
        lot_price_rub=price,
        price_at=price_at,
        price_source=price_source,
        reconciled=reconciled,
        lot_size=lot_size,
        currency=currency,
    )


def risk_input(
    *,
    positions: tuple[PositionRiskInput, ...] | None = None,
    nav: float | None = 100_000.0,
    cash: float | None = 50_000.0,
    reservations: tuple[PortfolioRiskReservation, ...] = (),
    snapshot_at: datetime | None = NOW,
    turnover: float = 0.0,
    daily_start: float | None = 100_000.0,
    weekly_start: float | None = 100_000.0,
    high_watermark: float | None = 100_000.0,
    global_kill: bool = False,
    instrument_kills: tuple[str, ...] = (),
    risk_resync_required: bool = False,
    blocking_orders: tuple[str, ...] = (),
    data_quality_flags: tuple[str, ...] = (),
) -> PortfolioRiskInput:
    return PortfolioRiskInput(
        account_id="sandbox-account",
        snapshot_revision=7,
        snapshot_checksum="a" * 64,
        snapshot_at=snapshot_at,
        evaluated_at=NOW,
        central_order_revision=3,
        reservation_projection_hash="b" * 64,
        risk_state_guard_hash="c" * 64,
        nav_rub=nav,
        cash_available_rub=cash,
        positions=(position(),) if positions is None else positions,
        reservations=reservations,
        daily_turnover_rub=turnover,
        daily_start_equity_rub=daily_start,
        weekly_start_equity_rub=weekly_start,
        high_watermark_equity_rub=high_watermark,
        global_kill_switch=global_kill,
        instrument_kill_switches=instrument_kills,
        risk_resync_required=risk_resync_required,
        blocking_order_ids=blocking_orders,
        data_quality_flags=data_quality_flags,
    )


def change(
    *,
    instrument_id: str = "figi-sber",
    current: int = 1,
    target: int = 2,
    price: float = 1_000.0,
    reservation_price: float | None = None,
    strategy: str | None = "primary",
    asset_class: str | None = "stock",
    price_at: datetime = NOW,
    price_source: str | None = "moex",
    lot_size: int = 10,
    currency: str = "rub",
) -> ProposedPortfolioChange:
    return ProposedPortfolioChange(
        instrument_id=instrument_id,
        ticker=instrument_id.removeprefix("figi-"),
        current_lots=current,
        requested_target_lots=target,
        price_per_lot_rub=price,
        reservation_per_lot_rub=reservation_price,
        strategy_id=strategy,
        asset_class=asset_class,
        price_at=price_at,
        price_source=price_source,
        lot_size=lot_size,
        currency=currency,
    )


def evaluate(
    policy: PortfolioRiskPolicy | None = None,
    *,
    snapshot: PortfolioRiskInput | None = None,
    proposal: ProposedPortfolioChange | None = None,
):
    return (
        PortfolioRiskEvaluator(policy or PortfolioRiskPolicy())
        .evaluate(
            snapshot or risk_input(),
            proposal or change(),
        )
        .decision
    )


def test_model_normalizes_taxonomy_and_is_order_independent() -> None:
    sber = position("figi-sber", asset_class=" stock ", price_source=" moex ")
    gazp = position("figi-gazp", lots=2, price=500.0, strategy="secondary")
    first = risk_input(positions=(sber, gazp))
    second = risk_input(positions=(gazp, sber))

    assert first.positions[1].asset_class == "STOCK"
    assert first.positions[1].price_source == "MOEX"
    assert first.positions[1].currency == "RUB"
    assert first.input_hash == second.input_hash


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: position(price_at=datetime.fromisoformat("2026-08-13T12:00:00")),
            "timezone-aware",
        ),
        (lambda: position(reconciled=1), "true, false or null"),
        (
            lambda: PortfolioRiskPolicy(max_gross_exposure_fraction=1.1),
            "must be in",
        ),
        (
            lambda: PortfolioRiskPolicy(max_daily_turnover_fraction=20),
            "must be in",
        ),
    ],
)
def test_model_rejects_ambiguous_values(factory, message: str) -> None:
    with pytest.raises(PortfolioRiskInputError, match=message):
        factory()


def test_metrics_cover_exposure_hhi_cash_turnover_and_pnl() -> None:
    snapshot = risk_input(
        positions=(
            position("figi-sber", lots=2, price=1_000.0),
            position(
                "figi-gazp",
                lots=1,
                price=3_000.0,
                strategy="secondary",
                asset_class="bond",
            ),
        ),
        nav=10_000.0,
        cash=6_000.0,
        reservations=(PortfolioRiskReservation("r1", "figi-lkoh", 500.0),),
        turnover=1_000.0,
        daily_start=9_000.0,
        weekly_start=8_000.0,
        high_watermark=12_500.0,
    )

    metrics = build_portfolio_risk_metrics(snapshot)

    assert metrics.gross_exposure_rub == 5_000.0
    assert metrics.gross_exposure_fraction == pytest.approx(0.5)
    assert metrics.hhi == pytest.approx(0.52)
    assert metrics.free_cash_rub == 5_500.0
    assert metrics.daily_turnover_fraction == pytest.approx(1 / 9)
    assert metrics.daily_pnl_rub == 1_000.0
    assert metrics.weekly_return == pytest.approx(0.25)
    assert metrics.drawdown_fraction == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("positions", "expected_count", "expected_gross"),
    [
        ((), 0, 0.0),
        ((position(),), 1, 1_000.0),
        (
            (
                position("figi-sber", price=1_000.0),
                position("figi-gazp", price=2_000.0),
                position("figi-lkoh", price=3_000.0),
            ),
            3,
            6_000.0,
        ),
    ],
)
def test_synthetic_zero_one_three_position_fixtures(
    positions: tuple[PositionRiskInput, ...],
    expected_count: int,
    expected_gross: float,
) -> None:
    metrics = build_portfolio_risk_metrics(risk_input(positions=positions))

    assert metrics.open_positions == expected_count
    assert metrics.gross_exposure_rub == expected_gross


def test_projected_metrics_include_candidate_reservation_and_turnover() -> None:
    snapshot = risk_input(
        reservations=(PortfolioRiskReservation("r1", "figi-gazp", 500.0),),
        turnover=700.0,
    )
    proposal = change(target=3, reservation_price=1_100.0)

    metrics = build_portfolio_risk_metrics(snapshot, proposal)

    assert metrics.gross_exposure_rub == 3_000.0
    assert metrics.cash_reserved_rub == 2_700.0
    assert metrics.free_cash_rub == 47_300.0
    assert metrics.daily_turnover_rub == 2_700.0


def test_sizing_helpers_never_force_implicit_reduction() -> None:
    total = lot_cap_for_total_value(
        rule="TOTAL",
        current_lots=3,
        unit_value_rub=1_000.0,
        other_value_rub=5_000.0,
        limit_rub=4_000.0,
        detail="test",
    )
    budget = lot_cap_for_incremental_budget(
        rule="BUDGET",
        current_lots=3,
        unit_cost_rub=1_000.0,
        budget_rub=2_999.0,
        limit_value=2_999.0,
        detail="test",
    )

    assert total.max_target_lots == 3
    assert budget.max_target_lots == 5
    assert (
        combine_lot_caps(
            current_lots=3,
            requested_target_lots=10,
            caps=(total, budget),
        )
        == 3
    )


def test_pass_is_deterministic_and_never_authorizes_execution() -> None:
    first = evaluate()
    second = evaluate()

    assert first.status == "PASS"
    assert first.approved_target_lots == 2
    assert first.execution_authorized is False
    assert first.decision_id == second.decision_id
    assert first.to_dict() == second.to_dict()


def test_proof_hash_changes_with_canonical_central_policy_and_risk_state() -> None:
    baseline = evaluate()
    changed_snapshot = replace(risk_input(), snapshot_checksum="d" * 64)
    changed_central = replace(
        risk_input(),
        reservation_projection_hash="e" * 64,
    )
    changed_state = replace(risk_input(), risk_state_guard_hash="f" * 64)

    decisions = (
        evaluate(snapshot=changed_snapshot),
        evaluate(snapshot=changed_central),
        evaluate(snapshot=changed_state),
        evaluate(PortfolioRiskPolicy(max_gross_exposure_rub=10_000.0)),
    )

    assert all(item.input_hash != baseline.input_hash for item in decisions[:3])
    assert decisions[3].policy_hash != baseline.policy_hash
    assert len({baseline.decision_id, *(item.decision_id for item in decisions)}) == 5


def test_gross_limit_adjusts_to_whole_lot_cap() -> None:
    decision = evaluate(
        PortfolioRiskPolicy(max_gross_exposure_rub=2_500.0),
        proposal=change(target=5),
    )

    assert decision.status == "ADJUSTED"
    assert decision.approved_target_lots == 2
    assert decision.adjustments == ("MAX_GROSS_EXPOSURE_RUB",)
    assert decision.requested_metrics.gross_exposure_rub == 5_000.0
    assert decision.projected_metrics.gross_exposure_rub == 2_000.0


def test_cash_and_existing_reservations_cap_incremental_buy() -> None:
    snapshot = risk_input(
        cash=4_500.0,
        reservations=(PortfolioRiskReservation("active", "figi-gazp", 500.0),),
    )
    decision = evaluate(
        PortfolioRiskPolicy(min_cash_reserve_rub=2_000.0),
        snapshot=snapshot,
        proposal=change(target=5),
    )

    assert decision.status == "ADJUSTED"
    assert decision.approved_target_lots == 3
    assert decision.projected_metrics.free_cash_rub == 2_000.0


def test_available_cash_caps_buy_even_without_configured_reserve() -> None:
    decision = evaluate(
        snapshot=risk_input(cash=1_500.0),
        proposal=change(target=5),
    )

    assert decision.status == "ADJUSTED"
    assert decision.approved_target_lots == 2
    assert decision.adjustments == ("AVAILABLE_CASH_AND_RESERVE",)


def test_daily_turnover_caps_candidate() -> None:
    decision = evaluate(
        PortfolioRiskPolicy(max_daily_turnover_rub=3_500.0),
        snapshot=risk_input(turnover=1_500.0),
        proposal=change(target=5),
    )

    assert decision.status == "ADJUSTED"
    assert decision.approved_target_lots == 3
    assert decision.projected_metrics.daily_turnover_rub == 3_500.0


def test_strategy_and_asset_limits_use_whole_portfolio_groups() -> None:
    snapshot = risk_input(
        positions=(
            position("figi-sber", lots=1, price=1_000.0),
            position("figi-gazp", lots=2, price=1_000.0),
            position(
                "figi-ofz",
                lots=1,
                price=1_000.0,
                strategy="secondary",
                asset_class="bond",
            ),
        ),
        nav=10_000.0,
    )
    policy = PortfolioRiskPolicy(
        max_strategy_concentration_fraction=0.4,
        asset_class_limits=(AssetClassConcentrationLimit("stock", 0.45),),
    )

    decision = evaluate(policy, snapshot=snapshot, proposal=change(target=5))

    assert decision.status == "ADJUSTED"
    assert decision.approved_target_lots == 2
    assert decision.adjustments == (
        "MAX_ASSET_CLASS_CONCENTRATION",
        "MAX_STRATEGY_CONCENTRATION",
    )


@pytest.mark.parametrize(
    ("policy", "positions", "reason"),
    [
        (
            PortfolioRiskPolicy(max_strategy_concentration_fraction=0.5),
            (position(strategy=None),),
            "PORTFOLIO_STRATEGY_OWNERSHIP_UNKNOWN",
        ),
        (
            PortfolioRiskPolicy(max_asset_class_concentration_fraction=0.5),
            (position(asset_class=None),),
            "PORTFOLIO_ASSET_CLASS_UNKNOWN",
        ),
    ],
)
def test_required_group_metadata_is_fail_closed(
    policy: PortfolioRiskPolicy,
    positions: tuple[PositionRiskInput, ...],
    reason: str,
) -> None:
    decision = evaluate(policy, snapshot=risk_input(positions=positions))

    assert decision.status == "BLOCKED"
    assert reason in decision.hard_blocks


def test_max_open_positions_blocks_new_instrument() -> None:
    snapshot = risk_input(
        positions=(
            position("figi-sber"),
            position("figi-gazp"),
        )
    )
    proposal = change(instrument_id="figi-lkoh", current=0, target=1)

    decision = evaluate(
        PortfolioRiskPolicy(max_open_positions=2),
        snapshot=snapshot,
        proposal=proposal,
    )

    assert decision.status == "BLOCKED"
    assert decision.approved_target_lots == 0
    assert decision.adjustments == ("MAX_OPEN_POSITIONS",)


@pytest.mark.parametrize(
    ("snapshot", "proposal", "reason"),
    [
        (
            risk_input(snapshot_at=NOW - timedelta(seconds=301)),
            change(),
            "STALE_CANONICAL_SNAPSHOT",
        ),
        (
            risk_input(),
            change(price_at=NOW - timedelta(seconds=301)),
            "CANDIDATE_PRICE_STALE",
        ),
        (risk_input(nav=None), change(), "NAV_UNKNOWN"),
        (risk_input(cash=None), change(), "CASH_UNKNOWN"),
        (risk_input(), change(price_source=None), "CANDIDATE_PRICE_SOURCE_UNKNOWN"),
        (risk_input(blocking_orders=("order-1",)), change(), "BLOCKING_ORDER_ACTIVE"),
        (risk_input(risk_resync_required=True), change(), "RISK_RESYNC_REQUIRED"),
        (risk_input(), change(current=0), "CURRENT_LOTS_MISMATCH"),
        (risk_input(), change(lot_size=1), "LOT_SIZE_MISMATCH"),
        (risk_input(), change(currency="usd"), "CURRENCY_MISMATCH"),
    ],
)
def test_data_quality_guards_fail_closed(snapshot, proposal, reason: str) -> None:
    decision = evaluate(snapshot=snapshot, proposal=proposal)

    assert decision.status == "BLOCKED"
    assert decision.approved_target_lots == proposal.current_lots
    assert reason in decision.hard_blocks


def test_kill_switch_blocks_increase_but_allows_fresh_reduction() -> None:
    snapshot = risk_input(global_kill=True)

    increased = evaluate(snapshot=snapshot, proposal=change(target=2))
    reduced = evaluate(snapshot=snapshot, proposal=change(current=1, target=0))

    assert increased.status == "BLOCKED"
    assert "GLOBAL_KILL_SWITCH" in increased.policy_halts
    assert reduced.status == "REDUCTION_ALLOWED"
    assert reduced.approved_target_lots == 0


def test_stale_data_blocks_even_a_reduction() -> None:
    decision = evaluate(
        snapshot=risk_input(snapshot_at=NOW - timedelta(seconds=301)),
        proposal=change(target=0),
    )

    assert decision.status == "BLOCKED"
    assert decision.approved_target_lots == 1


def test_loss_halt_allows_only_reduction() -> None:
    snapshot = risk_input(nav=90_000.0, daily_start=100_000.0)
    policy = PortfolioRiskPolicy(daily_loss_limit_rub=5_000.0)

    increased = evaluate(policy, snapshot=snapshot, proposal=change(target=2))
    reduced = evaluate(policy, snapshot=snapshot, proposal=change(target=0))

    assert increased.status == "BLOCKED"
    assert "DAILY_LOSS_LIMIT" in increased.policy_halts
    assert reduced.status == "REDUCTION_ALLOWED"


def test_existing_portfolio_limit_breach_allows_only_reduction() -> None:
    snapshot = risk_input(positions=(position(lots=3),), nav=10_000.0)
    policy = PortfolioRiskPolicy(max_gross_exposure_rub=2_000.0)

    increased = evaluate(
        policy,
        snapshot=snapshot,
        proposal=change(current=3, target=4),
    )
    reduced = evaluate(
        policy,
        snapshot=snapshot,
        proposal=change(current=3, target=2),
    )

    assert increased.status == "BLOCKED"
    assert "GROSS_EXPOSURE_LIMIT_BREACH" in increased.policy_halts
    assert reduced.status == "REDUCTION_ALLOWED"
    assert reduced.approved_target_lots == 2


def test_observe_only_reports_policy_result_without_execution_authority() -> None:
    decision = evaluate(
        PortfolioRiskPolicy(
            mode="observe_only",
            max_gross_exposure_rub=1_000.0,
        ),
        proposal=change(target=2),
    )

    assert decision.status == "BLOCKED"
    assert decision.mode == "OBSERVE_ONLY"
    assert decision.execution_authorized is False
    assert "OBSERVE_ONLY" in decision.warnings


def test_decision_rejects_execution_authority() -> None:
    decision = evaluate()
    payload = {
        field: getattr(decision, field) for field in decision.__dataclass_fields__
    }
    payload["execution_authorized"] = True

    with pytest.raises(PortfolioRiskInputError, match="cannot authorize"):
        PortfolioRiskDecision(**payload)
