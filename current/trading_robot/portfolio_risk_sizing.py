from __future__ import annotations

from math import floor, isfinite

from .portfolio_risk_model import PortfolioRiskInputError, PortfolioRiskRuleCap


def _finite_non_negative(value: float, field: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioRiskInputError(
            f"{field} must be finite and non-negative."
        ) from exc
    if not isfinite(normalized) or normalized < 0:
        raise PortfolioRiskInputError(f"{field} must be finite and non-negative.")
    return normalized


def _positive(value: float, field: str) -> float:
    normalized = _finite_non_negative(value, field)
    if normalized <= 0:
        raise PortfolioRiskInputError(f"{field} must be positive.")
    return normalized


def _lots(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PortfolioRiskInputError(f"{field} must be a non-negative integer.")
    return value


def lot_cap_for_total_value(
    *,
    rule: str,
    current_lots: int,
    unit_value_rub: float,
    other_value_rub: float,
    limit_rub: float,
    detail: str,
) -> PortfolioRiskRuleCap:
    """Return a long-only target cap while never forcing an implicit sell."""

    current = _lots(current_lots, "current_lots")
    unit = _positive(unit_value_rub, "unit_value_rub")
    other = _finite_non_negative(other_value_rub, "other_value_rub")
    limit = _finite_non_negative(limit_rub, "limit_rub")
    raw_cap = max(0, floor((limit - other + 1e-9) / unit))
    return PortfolioRiskRuleCap(
        rule=rule,
        max_target_lots=max(current, raw_cap),
        limit_value=limit,
        detail=detail,
    )


def lot_cap_for_incremental_budget(
    *,
    rule: str,
    current_lots: int,
    unit_cost_rub: float,
    budget_rub: float,
    limit_value: float,
    detail: str,
) -> PortfolioRiskRuleCap:
    """Convert a remaining cash/turnover budget to a maximum target."""

    current = _lots(current_lots, "current_lots")
    unit = _positive(unit_cost_rub, "unit_cost_rub")
    budget = _finite_non_negative(budget_rub, "budget_rub")
    return PortfolioRiskRuleCap(
        rule=rule,
        max_target_lots=current + floor((budget + 1e-9) / unit),
        limit_value=_finite_non_negative(limit_value, "limit_value"),
        detail=detail,
    )


def combine_lot_caps(
    *,
    current_lots: int,
    requested_target_lots: int,
    caps: tuple[PortfolioRiskRuleCap, ...],
) -> int:
    """Select the tightest cap without increasing or auto-reducing the request."""

    current = _lots(current_lots, "current_lots")
    requested = _lots(requested_target_lots, "requested_target_lots")
    if requested <= current:
        return requested
    cap_values = [requested]
    for cap in caps:
        if not isinstance(cap, PortfolioRiskRuleCap):
            raise PortfolioRiskInputError(
                "caps must contain PortfolioRiskRuleCap values."
            )
        cap_values.append(cap.max_target_lots)
    return max(current, min(cap_values))
