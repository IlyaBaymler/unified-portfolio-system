from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from .central_order_manager import (
    ACCOUNT_BLOCKING_STATUSES,
    RESERVATION_STATUSES,
    TERMINAL_STATUSES,
    CentralOrderState,
    central_reservation_projection_hash,
)
from .portfolio_model import (
    OwnershipStatus,
    PortfolioState,
    ReconciliationStatus,
    SnapshotFreshness,
)
from .portfolio_risk_evaluator import build_portfolio_risk_metrics
from .portfolio_risk_model import (
    AssetClassConcentrationLimit,
    PortfolioRiskInput,
    PortfolioRiskMetrics,
    PortfolioRiskPolicy,
    PortfolioRiskReservation,
    PositionRiskInput,
)
from .risk import RiskPolicy, RiskState
from .risk_runtime import risk_state_guard_hash


class PortfolioRiskAdapterError(RuntimeError):
    """Raised when v3.8 state cannot form an unambiguous read-only input."""


def _positive_finite_number(value: object) -> bool:
    return type(value) in {int, float} and isfinite(value) and value > 0


def _clean_empty_portfolio(
    portfolio: PortfolioState,
    central_orders: CentralOrderState,
    *,
    excluded_reservation_ids: tuple[str, ...] = (),
) -> bool:
    """Admit financial EMPTY with retired history or one replaceable BUY.

    Exclusions are the existing Portfolio Risk input mechanism, not new order
    authority: Central checks the exact same-instrument exclusion under its
    admission lock, and dispatch replays the finalized proof. Never exclude an
    account blocker or an unrelated active reservation to make EMPTY pass.
    """

    rub_cash = portfolio.account.cash("rub")
    if (
        portfolio.portfolio_source != "CANONICAL"
        or portfolio.state_status != "EMPTY"
        or portfolio.freshness is not SnapshotFreshness.FRESH
        or portfolio.migration.complete is not True
        or portfolio.blocking is not False
        or portfolio.positions != ()
        or portfolio.account_id != central_orders.account_id
        or not _positive_finite_number(portfolio.account.total_value)
        or rub_cash is None
        or not _positive_finite_number(rub_cash.available)
    ):
        return False

    # A single exclusion can represent the currently replaceable reservation
    # or its now-terminal predecessor when dispatch replays a replacement.
    # Terminal records retain their original cash field as history; only the
    # existing RESERVATION_STATUSES contribute to the live aggregate.
    if len(excluded_reservation_ids) > 1:
        return False
    for intent in central_orders.intents:
        if intent.status in TERMINAL_STATUSES:
            continue
        if (
            intent.status != "QUEUED"
            or intent.intent_id not in excluded_reservation_ids
            or intent.candidate.current_lots != 0
            or intent.candidate.direction != "BUY"
        ):
            return False

    expected_reserved = sum(
        intent.reserved_cash_kopecks
        for intent in central_orders.intents
        if intent.status in RESERVATION_STATUSES
    )
    # Build a separate reference with only the excluded identity retained.
    # Its post-exclusion projection is empty but remains bound to the account,
    # revision and excluded IDs. No persisted intent or reserve is mutated.
    reference = replace(
        central_orders,
        intents=tuple(
            intent
            for intent in central_orders.intents
            if intent.intent_id in excluded_reservation_ids
        ),
    )
    return (
        central_orders.reserved_cash_kopecks == expected_reserved
        and central_reservation_projection_hash(
            central_orders, excluded_reservation_ids=excluded_reservation_ids
        )
        == central_reservation_projection_hash(
            reference, excluded_reservation_ids=excluded_reservation_ids
        )
    )


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise PortfolioRiskAdapterError(
                "Portfolio Risk timestamp must be ISO-8601."
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PortfolioRiskAdapterError(
            "Portfolio Risk timestamp must be timezone-aware."
        )
    return parsed.astimezone(timezone.utc)


def _optional_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if isfinite(normalized) and normalized > 0 else None


def _lot_size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return normalized if normalized > 0 else None


@dataclass(frozen=True, slots=True)
class PortfolioRiskInstrumentMetadata:
    instrument_id: str
    lot_size: int
    asset_class: str | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        instrument_id = str(self.instrument_id or "").strip()
        lot_size = _lot_size(self.lot_size)
        if not instrument_id or lot_size is None:
            raise PortfolioRiskAdapterError(
                "Instrument metadata requires instrument_id and positive lot_size."
            )
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "lot_size", lot_size)
        object.__setattr__(
            self,
            "asset_class",
            str(self.asset_class or "").strip().upper() or None,
        )
        object.__setattr__(
            self,
            "currency",
            str(self.currency or "").strip().upper() or None,
        )


def portfolio_policy_from_risk_policy(policy: RiskPolicy) -> PortfolioRiskPolicy:
    if not isinstance(policy, RiskPolicy):
        raise TypeError("policy must be RiskPolicy.")
    if policy.cash_reserve_rub < 0:
        raise PortfolioRiskAdapterError("Risk cash reserve must not be negative.")
    return PortfolioRiskPolicy(
        mode=policy.portfolio_policy_mode,
        max_gross_exposure_rub=policy.max_gross_exposure_rub,
        max_gross_exposure_fraction=policy.max_gross_exposure_fraction,
        max_net_exposure_fraction=policy.max_net_exposure_fraction,
        max_instrument_concentration_fraction=(
            policy.max_instrument_concentration_fraction
        ),
        max_strategy_concentration_fraction=(
            policy.max_strategy_concentration_fraction
        ),
        max_asset_class_concentration_fraction=(
            policy.max_asset_class_concentration_fraction
        ),
        asset_class_limits=tuple(
            AssetClassConcentrationLimit(asset_class, fraction)
            for asset_class, fraction in policy.asset_class_concentration_limits
        ),
        max_open_positions=policy.max_open_positions,
        min_cash_reserve_rub=(policy.cash_reserve_rub or None),
        min_cash_reserve_fraction=policy.min_cash_reserve_fraction,
        max_daily_turnover_rub=policy.max_daily_turnover_rub,
        max_daily_turnover_fraction=policy.max_daily_turnover_fraction,
        daily_loss_limit_rub=policy.daily_loss_limit_rub,
        daily_loss_limit_fraction=policy.daily_loss_limit_fraction,
        weekly_loss_limit_rub=policy.weekly_loss_limit_rub,
        weekly_loss_limit_fraction=policy.weekly_loss_limit_fraction,
        max_drawdown_fraction=policy.max_drawdown_fraction,
        max_snapshot_age_seconds=policy.max_snapshot_age_seconds,
        max_price_age_seconds=policy.max_price_age_seconds,
        warning_utilization_fraction=(policy.portfolio_warning_utilization_fraction),
        allow_risk_reducing_orders_during_halt=(
            policy.allow_risk_reducing_orders_during_halt
        ),
    )


class PortfolioRiskInputAdapter:
    """The only v3.8-aware boundary for immutable Portfolio Risk inputs."""

    def build(
        self,
        *,
        portfolio: PortfolioState,
        central_orders: CentralOrderState,
        risk_state: RiskState,
        evaluated_at: datetime,
        instrument_metadata: Mapping[str, PortfolioRiskInstrumentMetadata]
        | None = None,
        excluded_reservation_ids: tuple[str, ...] = (),
    ) -> PortfolioRiskInput:
        if not isinstance(portfolio, PortfolioState):
            raise TypeError("portfolio must be PortfolioState.")
        if not isinstance(central_orders, CentralOrderState):
            raise TypeError("central_orders must be CentralOrderState.")
        if not isinstance(risk_state, RiskState):
            raise TypeError("risk_state must be RiskState.")
        if portfolio.account_id != central_orders.account_id:
            raise PortfolioRiskAdapterError(
                "Portfolio and Central order account scopes differ."
            )
        now = _aware(evaluated_at)
        snapshot_at = _aware(portfolio.snapshot_at)
        metadata = dict(instrument_metadata or {})
        if any(
            not isinstance(item, PortfolioRiskInstrumentMetadata)
            for item in metadata.values()
        ):
            raise PortfolioRiskAdapterError(
                "instrument_metadata must contain PortfolioRiskInstrumentMetadata."
            )
        mismatched_metadata = sorted(
            str(key) for key, item in metadata.items() if str(key) != item.instrument_id
        )
        if mismatched_metadata:
            raise PortfolioRiskAdapterError(
                "Instrument metadata key/identity mismatch: "
                + ", ".join(mismatched_metadata)
            )
        excluded = tuple(
            sorted({str(item or "").strip() for item in excluded_reservation_ids})
        )
        if any(not item for item in excluded):
            raise PortfolioRiskAdapterError(
                "excluded_reservation_ids must contain non-empty identifiers."
            )
        known_intent_ids = {item.intent_id for item in central_orders.intents}
        unknown_exclusions = sorted(set(excluded) - known_intent_ids)
        if unknown_exclusions:
            raise PortfolioRiskAdapterError(
                "Excluded reservation does not exist in Central state: "
                + ", ".join(unknown_exclusions)
            )

        flags: set[str] = set()
        if portfolio.portfolio_source != "CANONICAL":
            flags.add("PORTFOLIO_SOURCE_NOT_CANONICAL")
        if not portfolio.migration.complete:
            flags.add("CANONICAL_MIGRATION_INCOMPLETE")
        if portfolio.freshness is not SnapshotFreshness.FRESH:
            flags.add(f"PORTFOLIO_FRESHNESS_{portfolio.freshness.value}")
        if portfolio.blocking:
            flags.add("PORTFOLIO_BLOCKING")
        if portfolio.state_status not in {"READY", "ACTIVE"} and not (
            portfolio.state_status == "EMPTY"
            and _clean_empty_portfolio(
                portfolio, central_orders, excluded_reservation_ids=excluded
            )
        ):
            flags.add(f"PORTFOLIO_STATUS_{portfolio.state_status}")

        positions: list[PositionRiskInput] = []
        for source in portfolio.positions:
            item_metadata = metadata.get(source.instrument_id)
            lot_size = item_metadata.lot_size if item_metadata else None
            currency = (
                item_metadata.currency
                if item_metadata and item_metadata.currency
                else source.currency or None
            )
            asset_class = (
                item_metadata.asset_class
                if item_metadata and item_metadata.asset_class
                else source.asset_type or None
            )
            if str(asset_class or "").strip().upper() in {"", "UNKNOWN"}:
                asset_class = None
            price = _optional_price(source.current_price)
            lot_price = (
                price * lot_size if price is not None and lot_size is not None else None
            )
            owner = source.ownership
            reconciled = source.reconciliation.status is ReconciliationStatus.MATCHED
            if (
                source.actual_lots > 0
                and source.ownership_status is not OwnershipStatus.ATTRIBUTED
            ):
                flags.add(
                    f"OWNERSHIP_{source.ownership_status.value}:{source.instrument_id}"
                )
            if lot_size is None:
                flags.add(f"LOT_SIZE_UNKNOWN:{source.instrument_id}")
            if currency is None:
                flags.add(f"CURRENCY_UNKNOWN:{source.instrument_id}")
            positions.append(
                PositionRiskInput(
                    instrument_id=source.instrument_id,
                    actual_lots=source.actual_lots,
                    lot_price_rub=lot_price,
                    price_at=snapshot_at if price is not None else None,
                    lot_size=lot_size,
                    currency=currency,
                    ticker=source.ticker,
                    strategy_id=owner.strategy_id if owner else None,
                    asset_class=asset_class,
                    price_source=(
                        f"CANONICAL_{portfolio.source}" if price is not None else None
                    ),
                    reconciled=reconciled,
                )
            )

        reservations: list[PortfolioRiskReservation] = []
        for intent in central_orders.intents:
            if (
                intent.status not in RESERVATION_STATUSES
                or intent.intent_id in excluded
            ):
                continue
            candidate = intent.candidate
            source_position = portfolio.position(candidate.instrument_id)
            item_metadata = metadata.get(candidate.instrument_id)
            asset_class = (
                item_metadata.asset_class
                if item_metadata is not None and item_metadata.asset_class
                else (
                    source_position.asset_type
                    if source_position is not None
                    and source_position.asset_type.upper() != "UNKNOWN"
                    else None
                )
            )
            reservations.append(
                PortfolioRiskReservation(
                    reservation_id=intent.intent_id,
                    instrument_id=candidate.instrument_id,
                    amount_rub=intent.reserved_cash_kopecks / 100.0,
                    projected_market_value_rub=(
                        candidate.target_lots
                        * candidate.estimated_price_kopecks
                        / 100.0
                        * candidate.lot_size
                    ),
                    strategy_id=candidate.strategy_id,
                    asset_class=asset_class,
                )
            )
        reservation_projection_hash = central_reservation_projection_hash(
            central_orders,
            excluded_reservation_ids=excluded,
        )
        rub_cash = portfolio.account.cash("rub")
        blocking_ids = tuple(
            intent.intent_id
            for intent in central_orders.intents
            if intent.status in ACCOUNT_BLOCKING_STATUSES
        )
        return PortfolioRiskInput(
            account_id=portfolio.account_id,
            snapshot_revision=portfolio.revision,
            snapshot_checksum=portfolio.decision_sha256,
            snapshot_at=snapshot_at,
            evaluated_at=now,
            central_order_revision=central_orders.revision,
            reservation_projection_hash=reservation_projection_hash,
            risk_state_guard_hash=risk_state_guard_hash(risk_state),
            nav_rub=portfolio.account.total_value,
            cash_available_rub=rub_cash.available if rub_cash else None,
            positions=tuple(positions),
            reservations=tuple(reservations),
            daily_turnover_rub=risk_state.daily_turnover_rub,
            daily_start_equity_rub=risk_state.daily_start_equity_rub,
            weekly_start_equity_rub=risk_state.weekly_start_equity_rub,
            high_watermark_equity_rub=risk_state.high_watermark_equity_rub,
            global_kill_switch=risk_state.kill_switch_active,
            instrument_kill_switches=tuple(
                item.instrument_id for item in risk_state.instrument_kill_switches
            ),
            risk_resync_required=risk_state.risk_resync_required,
            blocking_order_ids=blocking_ids,
            data_quality_flags=tuple(flags),
        )


@dataclass(frozen=True, slots=True)
class PortfolioRiskReadOnlyReport:
    status: str
    generated_at: str
    account_id: str
    portfolio_policy_status: str
    input_hash: str
    policy_hash: str
    metrics: PortfolioRiskMetrics
    warnings: tuple[str, ...]
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.execution_authorized is not False:
            raise PortfolioRiskAdapterError(
                "Read-only Portfolio Risk report cannot authorize execution."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "generated_at": self.generated_at,
            "account_id": self.account_id,
            "portfolio_policy_status": self.portfolio_policy_status,
            "input_hash": self.input_hash,
            "policy_hash": self.policy_hash,
            "metrics": self.metrics.to_dict(),
            "warnings": list(self.warnings),
            "execution_authorized": False,
        }


def build_portfolio_risk_read_only_report(
    *,
    risk_input: PortfolioRiskInput,
    policy: RiskPolicy,
    mode: str,
) -> PortfolioRiskReadOnlyReport:
    normalized_mode = str(mode or "").strip().upper()
    configured = policy.portfolio_policy_configured
    policy_status = (
        "READY"
        if configured
        else (
            "CONFIGURATION_REQUIRED"
            if normalized_mode == "SANDBOX_EXECUTION"
            else "OBSERVE_ONLY_UNCONFIGURED"
        )
    )
    metrics = build_portfolio_risk_metrics(risk_input)
    warnings = list(risk_input.data_quality_flags)
    if risk_input.nav_rub is None or risk_input.nav_rub <= 0:
        warnings.append("NAV_UNKNOWN_OR_NOT_POSITIVE")
    if risk_input.cash_available_rub is None:
        warnings.append("CASH_UNKNOWN")
    if (
        policy.daily_loss_limit_rub is not None
        or policy.daily_loss_limit_fraction is not None
    ) and risk_input.daily_start_equity_rub is None:
        warnings.append("DAILY_BASELINE_UNKNOWN")
    if (
        policy.weekly_loss_limit_rub is not None
        or policy.weekly_loss_limit_fraction is not None
    ) and risk_input.weekly_start_equity_rub is None:
        warnings.append("WEEKLY_BASELINE_UNKNOWN")
    if (
        policy.max_drawdown_fraction is not None
        and risk_input.high_watermark_equity_rub is None
    ):
        warnings.append("HIGH_WATERMARK_UNKNOWN")
    if risk_input.global_kill_switch:
        warnings.append("GLOBAL_KILL_SWITCH_ACTIVE")
    if risk_input.risk_resync_required:
        warnings.append("RISK_RESYNC_REQUIRED")
    warnings.extend(
        f"INSTRUMENT_KILL_SWITCH_ACTIVE:{instrument_id}"
        for instrument_id in risk_input.instrument_kill_switches
    )
    warnings.extend(
        f"BLOCKING_ORDER_ACTIVE:{order_id}"
        for order_id in risk_input.blocking_order_ids
    )
    warnings.extend(
        f"VALUATION_UNKNOWN:{instrument_id}"
        for instrument_id in metrics.unknown_instruments
    )
    if not configured:
        warnings.append("PORTFOLIO_POLICY_CONFIGURATION_REQUIRED")
    return PortfolioRiskReadOnlyReport(
        status=(
            "BLOCKED"
            if warnings
            and any(
                item != "PORTFOLIO_POLICY_CONFIGURATION_REQUIRED" for item in warnings
            )
            else (
                "CONFIGURATION_REQUIRED"
                if policy_status == "CONFIGURATION_REQUIRED"
                else policy_status
            )
        ),
        generated_at=risk_input.evaluated_at.isoformat(),
        account_id=risk_input.account_id,
        portfolio_policy_status=policy_status,
        input_hash=risk_input.input_hash,
        policy_hash=policy.policy_hash,
        metrics=metrics,
        warnings=tuple(sorted(set(warnings))),
    )
