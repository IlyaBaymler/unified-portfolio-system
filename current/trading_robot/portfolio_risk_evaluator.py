from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from .portfolio_risk_model import (
    PortfolioRiskAssessment,
    PortfolioRiskDecision,
    PortfolioRiskExposureShare,
    PortfolioRiskInput,
    PortfolioRiskInputError,
    PortfolioRiskMetrics,
    PortfolioRiskPolicy,
    PortfolioRiskRuleCap,
    PositionRiskInput,
    ProposedPortfolioChange,
    canonical_sha256,
)
from .portfolio_risk_sizing import (
    combine_lot_caps,
    lot_cap_for_incremental_budget,
    lot_cap_for_total_value,
)

_FUTURE_TOLERANCE = timedelta(seconds=5)
_UNKNOWN_GROUP = "UNKNOWN"


def _fraction(value: float | None, denominator: float | None) -> float | None:
    if value is None or denominator is None or denominator <= 0:
        return None
    return value / denominator


def _exposure_rows(
    values: dict[str, float],
    nav_rub: float | None,
) -> tuple[PortfolioRiskExposureShare, ...]:
    return tuple(
        PortfolioRiskExposureShare(
            key=key,
            value_rub=value,
            fraction=_fraction(value, nav_rub),
        )
        for key, value in sorted(values.items())
    )


def _metrics(
    risk_input: PortfolioRiskInput,
    positions: tuple[PositionRiskInput, ...],
    *,
    additional_reservation_rub: float,
    additional_turnover_rub: float,
) -> PortfolioRiskMetrics:
    exposure: dict[str, tuple[float, str, str]] = {}
    open_instruments: set[str] = set()
    unknown: set[str] = set()

    for position in positions:
        if position.actual_lots <= 0:
            continue
        open_instruments.add(position.instrument_id)
        value = position.market_value_rub
        if value is None:
            unknown.add(position.instrument_id)
            continue
        exposure[position.instrument_id] = (
            value,
            position.strategy_id or _UNKNOWN_GROUP,
            position.asset_class or _UNKNOWN_GROUP,
        )

    for reservation in risk_input.reservations:
        projected = reservation.projected_market_value_rub
        if projected is None:
            continue
        existing = exposure.get(reservation.instrument_id)
        if projected == 0:
            exposure.pop(reservation.instrument_id, None)
            unknown.discard(reservation.instrument_id)
            open_instruments.discard(reservation.instrument_id)
            continue
        open_instruments.add(reservation.instrument_id)
        unknown.discard(reservation.instrument_id)
        exposure[reservation.instrument_id] = (
            projected,
            reservation.strategy_id
            or (existing[1] if existing is not None else _UNKNOWN_GROUP),
            reservation.asset_class
            or (existing[2] if existing is not None else _UNKNOWN_GROUP),
        )

    instrument_values = {
        instrument_id: item[0]
        for instrument_id, item in exposure.items()
    }
    strategy_values: defaultdict[str, float] = defaultdict(float)
    asset_values: defaultdict[str, float] = defaultdict(float)
    for value, strategy_id, asset_class in exposure.values():
        strategy_values[strategy_id] += value
        asset_values[asset_class] += value

    all_open_values_known = not unknown
    gross = sum(instrument_values.values()) if all_open_values_known else None
    net = gross  # v3.9 is long-only; this field preserves a future short-aware API.
    reserved = risk_input.reserved_cash_rub + additional_reservation_rub
    free_cash = (
        None
        if risk_input.cash_available_rub is None
        else risk_input.cash_available_rub - reserved
    )
    turnover = risk_input.daily_turnover_rub + additional_turnover_rub
    nav = risk_input.nav_rub
    instrument_rows = _exposure_rows(instrument_values, nav)
    hhi = (
        None
        if not all_open_values_known
        else (
            0.0
            if gross == 0
            else sum((value / gross) ** 2 for value in instrument_values.values())
        )
    )
    daily_pnl = (
        None
        if nav is None or risk_input.daily_start_equity_rub is None
        else nav - risk_input.daily_start_equity_rub
    )
    weekly_pnl = (
        None
        if nav is None or risk_input.weekly_start_equity_rub is None
        else nav - risk_input.weekly_start_equity_rub
    )
    drawdown = (
        None
        if nav is None or risk_input.high_watermark_equity_rub is None
        else max(
            0.0,
            (risk_input.high_watermark_equity_rub - nav)
            / risk_input.high_watermark_equity_rub,
        )
    )
    return PortfolioRiskMetrics(
        nav_rub=nav,
        gross_exposure_rub=gross,
        gross_exposure_fraction=_fraction(gross, nav),
        net_exposure_rub=net,
        net_exposure_fraction=_fraction(net, nav),
        cash_available_rub=risk_input.cash_available_rub,
        cash_reserved_rub=reserved,
        free_cash_rub=free_cash,
        free_cash_fraction=_fraction(free_cash, nav),
        open_positions=len(open_instruments),
        hhi=hhi,
        daily_turnover_rub=turnover,
        daily_turnover_fraction=_fraction(
            turnover,
            risk_input.daily_start_equity_rub,
        ),
        daily_pnl_rub=daily_pnl,
        daily_return=_fraction(daily_pnl, risk_input.daily_start_equity_rub),
        weekly_pnl_rub=weekly_pnl,
        weekly_return=_fraction(weekly_pnl, risk_input.weekly_start_equity_rub),
        drawdown_fraction=drawdown,
        instrument_exposure=instrument_rows,
        strategy_exposure=_exposure_rows(dict(strategy_values), nav),
        asset_class_exposure=_exposure_rows(dict(asset_values), nav),
        unknown_instruments=tuple(sorted(unknown)),
    )


def _project_positions(
    risk_input: PortfolioRiskInput,
    change: ProposedPortfolioChange,
    target_lots: int,
) -> tuple[PositionRiskInput, ...]:
    existing = risk_input.position(change.instrument_id)
    positions = [
        item
        for item in risk_input.positions
        if item.instrument_id != change.instrument_id
    ]
    if target_lots > 0:
        positions.append(
            PositionRiskInput(
                instrument_id=change.instrument_id,
                ticker=change.ticker or (existing.ticker if existing else None),
                strategy_id=(
                    change.strategy_id or (existing.strategy_id if existing else None)
                ),
                asset_class=(
                    change.asset_class or (existing.asset_class if existing else None)
                ),
                actual_lots=target_lots,
                lot_price_rub=change.price_per_lot_rub,
                price_at=change.price_at,
                price_source=change.price_source,
                reconciled=existing.reconciled if existing else True,
                lot_size=change.lot_size,
                currency=change.currency,
            )
        )
    return tuple(sorted(positions, key=lambda item: item.instrument_id))


def build_portfolio_risk_metrics(
    risk_input: PortfolioRiskInput,
    change: ProposedPortfolioChange | None = None,
    *,
    target_lots: int | None = None,
) -> PortfolioRiskMetrics:
    """Build deterministic current or candidate metrics without external I/O."""

    if change is None:
        if target_lots is not None:
            raise ValueError("target_lots requires a proposed change.")
        return _metrics(
            risk_input,
            risk_input.positions,
            additional_reservation_rub=0.0,
            additional_turnover_rub=0.0,
        )
    selected_target = (
        change.requested_target_lots if target_lots is None else target_lots
    )
    if (
        isinstance(selected_target, bool)
        or not isinstance(selected_target, int)
        or selected_target < 0
    ):
        raise PortfolioRiskInputError("target_lots must be a non-negative integer.")
    positions = _project_positions(risk_input, change, selected_target)
    return _metrics(
        risk_input,
        positions,
        additional_reservation_rub=change.reservation_for_target(selected_target),
        additional_turnover_rub=(
            abs(selected_target - change.current_lots) * change.price_per_lot_rub
        ),
    )


def _value(rows: tuple[PortfolioRiskExposureShare, ...], key: str) -> float:
    selected = next((item.value_rub for item in rows if item.key == key), None)
    return 0.0 if selected is None else selected


def _data_guards(
    risk_input: PortfolioRiskInput,
    change: ProposedPortfolioChange,
    policy: PortfolioRiskPolicy,
) -> list[str]:
    blocks: list[str] = []
    existing = risk_input.position(change.instrument_id)
    actual_lots = existing.actual_lots if existing else 0
    if actual_lots != change.current_lots:
        blocks.append("CURRENT_LOTS_MISMATCH")
    if risk_input.snapshot_at is None:
        blocks.append("SNAPSHOT_TIME_UNKNOWN")
    else:
        snapshot_age = risk_input.evaluated_at - risk_input.snapshot_at
        if snapshot_age < -_FUTURE_TOLERANCE:
            blocks.append("SNAPSHOT_FROM_FUTURE")
        if (
            policy.max_snapshot_age_seconds is not None
            and snapshot_age.total_seconds() > policy.max_snapshot_age_seconds
        ):
            blocks.append("STALE_CANONICAL_SNAPSHOT")
    if risk_input.nav_rub is None:
        blocks.append("NAV_UNKNOWN")
    elif risk_input.nav_rub <= 0:
        blocks.append("NAV_NOT_POSITIVE")
    if risk_input.cash_available_rub is None:
        blocks.append("CASH_UNKNOWN")
    if risk_input.blocking_order_ids:
        blocks.append("BLOCKING_ORDER_ACTIVE")
    if risk_input.risk_resync_required:
        blocks.append("RISK_RESYNC_REQUIRED")
    blocks.extend(f"DATA_QUALITY:{flag}" for flag in risk_input.data_quality_flags)

    relevant = [item for item in risk_input.positions if item.actual_lots > 0]
    for position in relevant:
        if position.reconciled is not True:
            suffix = "UNKNOWN" if position.reconciled is None else "FALSE"
            blocks.append(f"POSITION_RECONCILED_{suffix}:{position.instrument_id}")
        if position.lot_price_rub is None:
            blocks.append(f"PRICE_UNKNOWN:{position.instrument_id}")
        if position.price_at is None:
            blocks.append(f"PRICE_TIME_UNKNOWN:{position.instrument_id}")
        else:
            age = risk_input.evaluated_at - position.price_at
            if age < -_FUTURE_TOLERANCE:
                blocks.append(f"PRICE_FROM_FUTURE:{position.instrument_id}")
            if (
                policy.max_price_age_seconds is not None
                and age.total_seconds() > policy.max_price_age_seconds
            ):
                blocks.append(f"STALE_PRICE:{position.instrument_id}")
        if position.price_source is None:
            blocks.append(f"PRICE_SOURCE_UNKNOWN:{position.instrument_id}")

    candidate_age = risk_input.evaluated_at - change.price_at
    if candidate_age < -_FUTURE_TOLERANCE:
        blocks.append("CANDIDATE_PRICE_FROM_FUTURE")
    if (
        policy.max_price_age_seconds is not None
        and candidate_age.total_seconds() > policy.max_price_age_seconds
    ):
        blocks.append("CANDIDATE_PRICE_STALE")
    if change.price_source is None:
        blocks.append("CANDIDATE_PRICE_SOURCE_UNKNOWN")
    if existing is not None:
        if existing.lot_size is None:
            blocks.append("LOT_SIZE_UNKNOWN")
        if existing.lot_size != change.lot_size:
            blocks.append("LOT_SIZE_MISMATCH")
        if existing.currency is None:
            blocks.append("CURRENCY_UNKNOWN")
        if existing.currency != change.currency:
            blocks.append("CURRENCY_MISMATCH")
        if (
            change.strategy_id is not None
            and existing.strategy_id is not None
            and change.strategy_id != existing.strategy_id
        ):
            blocks.append("STRATEGY_OWNERSHIP_MISMATCH")
        if (
            change.asset_class is not None
            and existing.asset_class is not None
            and change.asset_class != existing.asset_class
        ):
            blocks.append("ASSET_CLASS_MISMATCH")
    if change.currency != "RUB":
        blocks.append("UNSUPPORTED_CURRENCY")
    blocks.extend(
        f"UNSUPPORTED_CURRENCY:{item.instrument_id}"
        for item in relevant
        if item.currency != "RUB"
    )
    return blocks


def _policy_halts(
    risk_input: PortfolioRiskInput,
    change: ProposedPortfolioChange,
    policy: PortfolioRiskPolicy,
    metrics: PortfolioRiskMetrics,
) -> list[str]:
    halts: list[str] = []
    if risk_input.global_kill_switch:
        halts.append("GLOBAL_KILL_SWITCH")
    if change.instrument_id in risk_input.instrument_kill_switches:
        halts.append("INSTRUMENT_KILL_SWITCH")
    if (
        policy.max_gross_exposure_rub is not None
        and metrics.gross_exposure_rub is not None
        and metrics.gross_exposure_rub > policy.max_gross_exposure_rub
    ):
        halts.append("GROSS_EXPOSURE_LIMIT_BREACH")
    if (
        policy.max_gross_exposure_fraction is not None
        and metrics.gross_exposure_fraction is not None
        and metrics.gross_exposure_fraction > policy.max_gross_exposure_fraction
    ):
        halts.append("GROSS_EXPOSURE_FRACTION_BREACH")
    if (
        policy.max_net_exposure_fraction is not None
        and metrics.net_exposure_fraction is not None
        and metrics.net_exposure_fraction > policy.max_net_exposure_fraction
    ):
        halts.append("NET_EXPOSURE_FRACTION_BREACH")
    if policy.max_instrument_concentration_fraction is not None and any(
        item.fraction is not None
        and item.fraction > policy.max_instrument_concentration_fraction
        for item in metrics.instrument_exposure
    ):
        halts.append("INSTRUMENT_CONCENTRATION_BREACH")
    if policy.max_strategy_concentration_fraction is not None and any(
        item.fraction is not None
        and item.fraction > policy.max_strategy_concentration_fraction
        for item in metrics.strategy_exposure
    ):
        halts.append("STRATEGY_CONCENTRATION_BREACH")
    for item in metrics.asset_class_exposure:
        limit = policy.asset_class_limit(
            None if item.key == _UNKNOWN_GROUP else item.key
        )
        if limit is not None and item.fraction is not None and item.fraction > limit:
            halts.append(f"ASSET_CLASS_CONCENTRATION_BREACH:{item.key}")
    if (
        policy.max_open_positions is not None
        and metrics.open_positions > policy.max_open_positions
    ):
        halts.append("OPEN_POSITION_LIMIT_BREACH")
    required_cash_reserve = policy.min_cash_reserve_rub or 0.0
    if policy.min_cash_reserve_fraction is not None and metrics.nav_rub is not None:
        required_cash_reserve = max(
            required_cash_reserve,
            policy.min_cash_reserve_fraction * metrics.nav_rub,
        )
    if (
        metrics.free_cash_rub is not None
        and metrics.free_cash_rub < required_cash_reserve
    ):
        halts.append("CASH_RESERVE_BREACH")
    if policy.daily_loss_limit_rub is not None and metrics.daily_pnl_rub is None:
        halts.append("DAILY_PNL_BASELINE_UNKNOWN")
    elif (
        policy.daily_loss_limit_rub is not None
        and metrics.daily_pnl_rub <= -policy.daily_loss_limit_rub
    ):
        halts.append("DAILY_LOSS_LIMIT")
    if policy.daily_loss_limit_fraction is not None and metrics.daily_return is None:
        halts.append("DAILY_RETURN_BASELINE_UNKNOWN")
    elif (
        policy.daily_loss_limit_fraction is not None
        and metrics.daily_return <= -policy.daily_loss_limit_fraction
    ):
        halts.append("DAILY_LOSS_FRACTION_LIMIT")
    if policy.weekly_loss_limit_rub is not None and metrics.weekly_pnl_rub is None:
        halts.append("WEEKLY_PNL_BASELINE_UNKNOWN")
    elif (
        policy.weekly_loss_limit_rub is not None
        and metrics.weekly_pnl_rub <= -policy.weekly_loss_limit_rub
    ):
        halts.append("WEEKLY_LOSS_LIMIT")
    if policy.weekly_loss_limit_fraction is not None and metrics.weekly_return is None:
        halts.append("WEEKLY_RETURN_BASELINE_UNKNOWN")
    elif (
        policy.weekly_loss_limit_fraction is not None
        and metrics.weekly_return <= -policy.weekly_loss_limit_fraction
    ):
        halts.append("WEEKLY_LOSS_FRACTION_LIMIT")
    if policy.max_drawdown_fraction is not None and metrics.drawdown_fraction is None:
        halts.append("DRAWDOWN_BASELINE_UNKNOWN")
    elif (
        policy.max_drawdown_fraction is not None
        and metrics.drawdown_fraction >= policy.max_drawdown_fraction
    ):
        halts.append("DRAWDOWN_LIMIT")
    if (
        policy.max_daily_turnover_rub is not None
        and metrics.daily_turnover_rub >= policy.max_daily_turnover_rub
    ):
        halts.append("DAILY_TURNOVER_LIMIT")
    if policy.max_daily_turnover_fraction is not None:
        if metrics.daily_turnover_fraction is None:
            halts.append("TURNOVER_BASELINE_UNKNOWN")
        elif metrics.daily_turnover_fraction >= policy.max_daily_turnover_fraction:
            halts.append("DAILY_TURNOVER_FRACTION_LIMIT")
    return halts


def _limits_for_change(
    risk_input: PortfolioRiskInput,
    change: ProposedPortfolioChange,
    policy: PortfolioRiskPolicy,
    current: PortfolioRiskMetrics,
) -> tuple[list[PortfolioRiskRuleCap], list[str]]:
    caps: list[PortfolioRiskRuleCap] = []
    blocks: list[str] = []
    current_instrument_value = _value(
        current.instrument_exposure,
        change.instrument_id,
    )
    known_gross = current.gross_exposure_rub
    if known_gross is None:
        blocks.append("GROSS_EXPOSURE_UNKNOWN")
        known_gross = 0.0

    total_limits: list[tuple[str, float | None]] = [
        ("MAX_GROSS_EXPOSURE_RUB", policy.max_gross_exposure_rub),
        (
            "MAX_GROSS_EXPOSURE_FRACTION",
            None
            if policy.max_gross_exposure_fraction is None or risk_input.nav_rub is None
            else policy.max_gross_exposure_fraction * risk_input.nav_rub,
        ),
        (
            "MAX_NET_EXPOSURE_FRACTION",
            None
            if policy.max_net_exposure_fraction is None or risk_input.nav_rub is None
            else policy.max_net_exposure_fraction * risk_input.nav_rub,
        ),
    ]
    for rule, limit in total_limits:
        configured = {
            "MAX_GROSS_EXPOSURE_RUB": policy.max_gross_exposure_rub,
            "MAX_GROSS_EXPOSURE_FRACTION": policy.max_gross_exposure_fraction,
            "MAX_NET_EXPOSURE_FRACTION": policy.max_net_exposure_fraction,
        }[rule]
        if configured is not None and limit is None:
            blocks.append(f"{rule}_BASE_UNKNOWN")
        elif limit is not None:
            caps.append(
                lot_cap_for_total_value(
                    rule=rule,
                    current_lots=change.current_lots,
                    unit_value_rub=change.price_per_lot_rub,
                    other_value_rub=max(0.0, known_gross - current_instrument_value),
                    limit_rub=limit,
                    detail="Whole-portfolio long-only exposure cap.",
                )
            )

    nav = risk_input.nav_rub
    if policy.max_instrument_concentration_fraction is not None:
        if nav is None:
            blocks.append("INSTRUMENT_CONCENTRATION_BASE_UNKNOWN")
        else:
            caps.append(
                lot_cap_for_total_value(
                    rule="MAX_INSTRUMENT_CONCENTRATION",
                    current_lots=change.current_lots,
                    unit_value_rub=change.price_per_lot_rub,
                    other_value_rub=0.0,
                    limit_rub=nav * policy.max_instrument_concentration_fraction,
                    detail="Candidate instrument share of canonical equity.",
                )
            )

    existing = risk_input.position(change.instrument_id)
    strategy_id = change.strategy_id or (existing.strategy_id if existing else None)
    if policy.max_strategy_concentration_fraction is not None:
        if any(
            item.actual_lots > 0 and item.strategy_id is None
            for item in risk_input.positions
        ):
            blocks.append("PORTFOLIO_STRATEGY_OWNERSHIP_UNKNOWN")
        if strategy_id is None:
            blocks.append("STRATEGY_OWNERSHIP_UNKNOWN")
        elif nav is None:
            blocks.append("STRATEGY_CONCENTRATION_BASE_UNKNOWN")
        else:
            group_value = _value(current.strategy_exposure, strategy_id)
            caps.append(
                lot_cap_for_total_value(
                    rule="MAX_STRATEGY_CONCENTRATION",
                    current_lots=change.current_lots,
                    unit_value_rub=change.price_per_lot_rub,
                    other_value_rub=max(0.0, group_value - current_instrument_value),
                    limit_rub=nav * policy.max_strategy_concentration_fraction,
                    detail=f"Canonical strategy ownership: {strategy_id}.",
                )
            )

    asset_class = change.asset_class or (existing.asset_class if existing else None)
    asset_fraction = policy.asset_class_limit(asset_class)
    if asset_fraction is not None:
        if any(
            item.actual_lots > 0 and item.asset_class is None
            for item in risk_input.positions
        ):
            blocks.append("PORTFOLIO_ASSET_CLASS_UNKNOWN")
        if asset_class is None:
            blocks.append("ASSET_CLASS_UNKNOWN")
        elif nav is None:
            blocks.append("ASSET_CONCENTRATION_BASE_UNKNOWN")
        else:
            group_value = _value(current.asset_class_exposure, asset_class)
            caps.append(
                lot_cap_for_total_value(
                    rule="MAX_ASSET_CLASS_CONCENTRATION",
                    current_lots=change.current_lots,
                    unit_value_rub=change.price_per_lot_rub,
                    other_value_rub=max(0.0, group_value - current_instrument_value),
                    limit_rub=nav * asset_fraction,
                    detail=f"Normalized asset class: {asset_class}.",
                )
            )

    if (
        policy.max_open_positions is not None
        and change.current_lots == 0
        and current.open_positions >= policy.max_open_positions
    ):
        caps.append(
            PortfolioRiskRuleCap(
                rule="MAX_OPEN_POSITIONS",
                max_target_lots=0,
                limit_value=policy.max_open_positions,
                detail="A new instrument consumes one open-position slot.",
            )
        )

    cash_reserve_candidates = [0.0]
    if policy.min_cash_reserve_rub is not None:
        cash_reserve_candidates.append(policy.min_cash_reserve_rub)
    if policy.min_cash_reserve_fraction is not None:
        if nav is None:
            blocks.append("CASH_RESERVE_BASE_UNKNOWN")
        else:
            cash_reserve_candidates.append(policy.min_cash_reserve_fraction * nav)
    if risk_input.cash_available_rub is None:
        blocks.append("CASH_AVAILABLE_UNKNOWN")
    else:
        required = max(cash_reserve_candidates)
        available_budget = max(
            0.0,
            risk_input.cash_available_rub - risk_input.reserved_cash_rub - required,
        )
        caps.append(
            lot_cap_for_incremental_budget(
                rule="AVAILABLE_CASH_AND_RESERVE",
                current_lots=change.current_lots,
                unit_cost_rub=change.reservation_per_lot_rub,
                budget_rub=available_budget,
                limit_value=required,
                detail="Canonical available cash less active Central reservations.",
            )
        )

    turnover_limits: list[tuple[str, float]] = []
    if policy.max_daily_turnover_rub is not None:
        turnover_limits.append(
            ("MAX_DAILY_TURNOVER_RUB", policy.max_daily_turnover_rub)
        )
    if policy.max_daily_turnover_fraction is not None:
        if risk_input.daily_start_equity_rub is None:
            blocks.append("TURNOVER_BASELINE_UNKNOWN")
        else:
            turnover_limits.append(
                (
                    "MAX_DAILY_TURNOVER_FRACTION",
                    policy.max_daily_turnover_fraction
                    * risk_input.daily_start_equity_rub,
                )
            )
    for rule, limit in turnover_limits:
        caps.append(
            lot_cap_for_incremental_budget(
                rule=rule,
                current_lots=change.current_lots,
                unit_cost_rub=change.price_per_lot_rub,
                budget_rub=max(0.0, limit - risk_input.daily_turnover_rub),
                limit_value=limit,
                detail="Account-wide turnover remaining before candidate.",
            )
        )
    return caps, blocks


def _warnings(
    policy: PortfolioRiskPolicy,
    metrics: PortfolioRiskMetrics,
) -> list[str]:
    warnings: list[str] = []
    threshold = policy.warning_utilization_fraction
    checks = (
        (
            "GROSS_EXPOSURE_NEAR_LIMIT",
            metrics.gross_exposure_rub,
            policy.max_gross_exposure_rub,
        ),
        (
            "DAILY_TURNOVER_NEAR_LIMIT",
            metrics.daily_turnover_rub,
            policy.max_daily_turnover_rub,
        ),
    )
    for code, value, limit in checks:
        if value is not None and limit is not None and value >= threshold * limit:
            warnings.append(code)
    if policy.mode == "OBSERVE_ONLY":
        warnings.append("OBSERVE_ONLY")
    return warnings


class PortfolioRiskEvaluator:
    """Pure, deterministic account-level evaluator for the v3.9 M1 boundary."""

    def __init__(self, policy: PortfolioRiskPolicy) -> None:
        if not isinstance(policy, PortfolioRiskPolicy):
            raise TypeError("policy must be PortfolioRiskPolicy.")
        self.policy = policy

    def evaluate(
        self,
        risk_input: PortfolioRiskInput,
        change: ProposedPortfolioChange,
    ) -> PortfolioRiskAssessment:
        current = build_portfolio_risk_metrics(risk_input)
        requested = build_portfolio_risk_metrics(risk_input, change)
        data_blocks = _data_guards(risk_input, change, self.policy)
        halts = _policy_halts(risk_input, change, self.policy, current)
        caps, sizing_blocks = _limits_for_change(
            risk_input,
            change,
            self.policy,
            current,
        )
        data_blocks.extend(sizing_blocks)
        all_reasons = sorted(set(data_blocks + halts))

        requested_delta = change.requested_delta_lots
        approved = change.current_lots
        adjustments: list[str] = []
        if requested_delta == 0:
            status = "HALTED" if all_reasons else "PASS"
            approved = change.current_lots
        elif data_blocks:
            status = "BLOCKED"
        elif requested_delta < 0:
            if halts and not self.policy.allow_risk_reducing_orders_during_halt:
                status = "BLOCKED"
            else:
                approved = change.requested_target_lots
                status = "REDUCTION_ALLOWED" if halts else "PASS"
        elif halts:
            status = "BLOCKED"
        else:
            approved = combine_lot_caps(
                current_lots=change.current_lots,
                requested_target_lots=change.requested_target_lots,
                caps=tuple(caps),
            )
            if approved < change.requested_target_lots:
                status = "ADJUSTED" if approved > change.current_lots else "BLOCKED"
                adjustments.extend(
                    cap.rule for cap in caps if cap.max_target_lots == approved
                )
            else:
                status = "PASS"

        projected = build_portfolio_risk_metrics(
            risk_input,
            change,
            target_lots=approved,
        )
        warnings = _warnings(self.policy, projected)
        decision_payload = {
            "input_hash": risk_input.input_hash,
            "policy_hash": self.policy.policy_hash,
            "change": change.to_dict(),
            "status": status,
            "approved_target_lots": approved,
            "hard_blocks": sorted(set(data_blocks)),
            "policy_halts": sorted(set(halts)),
            "adjustments": sorted(set(adjustments)),
            "lot_caps": [item.to_dict() for item in sorted(caps, key=lambda x: x.rule)],
        }
        decision = PortfolioRiskDecision(
            decision_id=canonical_sha256(decision_payload),
            input_hash=risk_input.input_hash,
            policy_hash=self.policy.policy_hash,
            mode=self.policy.mode,
            status=status,
            current_lots=change.current_lots,
            requested_target_lots=change.requested_target_lots,
            approved_target_lots=approved,
            hard_blocks=tuple(data_blocks),
            policy_halts=tuple(halts),
            adjustments=tuple(adjustments),
            warnings=tuple(warnings),
            lot_caps=tuple(caps),
            current_metrics=current,
            requested_metrics=requested,
            projected_metrics=projected,
            snapshot_revision=risk_input.snapshot_revision,
            snapshot_checksum=risk_input.snapshot_checksum,
            central_order_revision=risk_input.central_order_revision,
            reservation_projection_hash=risk_input.reservation_projection_hash,
            risk_state_guard_hash=risk_input.risk_state_guard_hash,
            evaluated_at=risk_input.evaluated_at,
        )
        return PortfolioRiskAssessment(decision=decision)
