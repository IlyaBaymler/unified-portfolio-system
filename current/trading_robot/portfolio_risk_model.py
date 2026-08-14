from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from typing import Any


class PortfolioRiskInputError(ValueError):
    """Raised when a pure Portfolio Risk input is ambiguous or malformed."""


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise PortfolioRiskInputError(f"{field} must not be empty.")
    return normalized


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PortfolioRiskInputError(f"{field} must be a non-negative integer.")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioRiskInputError(
            f"{field} must be a non-negative integer."
        ) from exc
    if normalized != value or normalized < 0:
        raise PortfolioRiskInputError(f"{field} must be a non-negative integer.")
    return normalized


def _positive_int(value: Any, field: str) -> int:
    normalized = _non_negative_int(value, field)
    if normalized < 1:
        raise PortfolioRiskInputError(f"{field} must be a positive integer.")
    return normalized


def _finite_number(
    value: Any,
    field: str,
    *,
    allow_none: bool = False,
    positive: bool = False,
) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise PortfolioRiskInputError(f"{field} must be finite.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioRiskInputError(f"{field} must be finite.") from exc
    if not isfinite(normalized):
        raise PortfolioRiskInputError(f"{field} must be finite.")
    if normalized < 0 or (positive and normalized <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise PortfolioRiskInputError(f"{field} must be {qualifier}.")
    return normalized


def _optional_positive(value: Any, field: str) -> float | None:
    return _finite_number(value, field, allow_none=True, positive=True)


def _optional_limit(value: Any, field: str) -> float | None:
    return _finite_number(value, field, allow_none=True, positive=True)


def _optional_non_negative(value: Any, field: str) -> float | None:
    return _finite_number(value, field, allow_none=True)


def _optional_fraction(value: Any, field: str) -> float | None:
    normalized = _finite_number(value, field, allow_none=True, positive=True)
    if normalized is not None and normalized > 1:
        raise PortfolioRiskInputError(f"{field} must be in (0, 1].")
    return normalized


def _aware_datetime(
    value: datetime | None,
    field: str,
    *,
    allow_none: bool = False,
) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PortfolioRiskInputError(f"{field} must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _sha256_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise PortfolioRiskInputError(f"{field} must be a SHA-256 hex digest.")
    return normalized


def _sorted_unique_text(values: Any, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise PortfolioRiskInputError(f"{field} must be an array.")
    try:
        normalized = tuple(sorted({_required_text(value, field) for value in values}))
    except TypeError as exc:
        raise PortfolioRiskInputError(f"{field} must be an array.") from exc
    return normalized


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _boolean(value: Any, field: str) -> bool:
    if value is not True and value is not False:
        raise PortfolioRiskInputError(f"{field} must be true or false.")
    return value


def canonical_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PositionRiskInput:
    instrument_id: str
    actual_lots: int
    lot_price_rub: float | None
    price_at: datetime | None
    lot_size: int | None
    currency: str | None
    ticker: str | None = None
    strategy_id: str | None = None
    asset_class: str | None = None
    price_source: str | None = None
    reconciled: bool | None = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "instrument_id",
            _required_text(self.instrument_id, "position.instrument_id"),
        )
        object.__setattr__(
            self,
            "actual_lots",
            _non_negative_int(self.actual_lots, "position.actual_lots"),
        )
        object.__setattr__(
            self,
            "lot_price_rub",
            _optional_positive(self.lot_price_rub, "position.lot_price_rub"),
        )
        object.__setattr__(
            self,
            "price_at",
            _aware_datetime(self.price_at, "position.price_at", allow_none=True),
        )
        object.__setattr__(
            self,
            "ticker",
            _optional_text(self.ticker).upper()
            if _optional_text(self.ticker)
            else None,
        )
        object.__setattr__(self, "strategy_id", _optional_text(self.strategy_id))
        object.__setattr__(
            self,
            "asset_class",
            _optional_text(self.asset_class).upper()
            if _optional_text(self.asset_class)
            else None,
        )
        object.__setattr__(
            self,
            "price_source",
            _optional_text(self.price_source).upper()
            if _optional_text(self.price_source)
            else None,
        )
        object.__setattr__(
            self,
            "lot_size",
            None
            if self.lot_size is None
            else _positive_int(self.lot_size, "position.lot_size"),
        )
        currency = _optional_text(self.currency)
        object.__setattr__(
            self,
            "currency",
            currency.upper() if currency else None,
        )
        if (
            self.reconciled is not True
            and self.reconciled is not False
            and self.reconciled is not None
        ):
            raise PortfolioRiskInputError(
                "position.reconciled must be true, false or null."
            )

    @property
    def market_value_rub(self) -> float | None:
        if self.lot_price_rub is None:
            return None
        return self.actual_lots * self.lot_price_rub

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "strategy_id": self.strategy_id,
            "asset_class": self.asset_class,
            "actual_lots": self.actual_lots,
            "lot_size": self.lot_size,
            "currency": self.currency,
            "lot_price_rub": self.lot_price_rub,
            "market_value_rub": self.market_value_rub,
            "price_at": _iso(self.price_at),
            "price_source": self.price_source,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskReservation:
    reservation_id: str
    instrument_id: str
    amount_rub: float
    projected_market_value_rub: float | None = None
    strategy_id: str | None = None
    asset_class: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reservation_id",
            _required_text(self.reservation_id, "reservation.reservation_id"),
        )
        object.__setattr__(
            self,
            "instrument_id",
            _required_text(self.instrument_id, "reservation.instrument_id"),
        )
        object.__setattr__(
            self,
            "amount_rub",
            _finite_number(self.amount_rub, "reservation.amount_rub"),
        )
        object.__setattr__(
            self,
            "projected_market_value_rub",
            _optional_non_negative(
                self.projected_market_value_rub,
                "reservation.projected_market_value_rub",
            ),
        )
        object.__setattr__(
            self,
            "strategy_id",
            _optional_text(self.strategy_id),
        )
        object.__setattr__(
            self,
            "asset_class",
            (
                _optional_text(self.asset_class).upper()
                if _optional_text(self.asset_class)
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "instrument_id": self.instrument_id,
            "amount_rub": self.amount_rub,
            "projected_market_value_rub": self.projected_market_value_rub,
            "strategy_id": self.strategy_id,
            "asset_class": self.asset_class,
        }


@dataclass(frozen=True, slots=True)
class ProposedPortfolioChange:
    instrument_id: str
    current_lots: int
    requested_target_lots: int
    price_per_lot_rub: float
    price_at: datetime
    strategy_id: str | None
    asset_class: str | None
    lot_size: int
    currency: str
    reservation_per_lot_rub: float | None = None
    ticker: str | None = None
    price_source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "instrument_id",
            _required_text(self.instrument_id, "change.instrument_id"),
        )
        object.__setattr__(
            self,
            "current_lots",
            _non_negative_int(self.current_lots, "change.current_lots"),
        )
        object.__setattr__(
            self,
            "requested_target_lots",
            _non_negative_int(
                self.requested_target_lots,
                "change.requested_target_lots",
            ),
        )
        price = _finite_number(
            self.price_per_lot_rub,
            "change.price_per_lot_rub",
            positive=True,
        )
        object.__setattr__(self, "price_per_lot_rub", price)
        object.__setattr__(
            self,
            "price_at",
            _aware_datetime(self.price_at, "change.price_at"),
        )
        reservation_price = (
            price
            if self.reservation_per_lot_rub is None
            else _finite_number(
                self.reservation_per_lot_rub,
                "change.reservation_per_lot_rub",
                positive=True,
            )
        )
        if reservation_price < price:
            raise PortfolioRiskInputError(
                "change.reservation_per_lot_rub must cover price_per_lot_rub."
            )
        object.__setattr__(self, "reservation_per_lot_rub", reservation_price)
        object.__setattr__(
            self,
            "ticker",
            _optional_text(self.ticker).upper()
            if _optional_text(self.ticker)
            else None,
        )
        object.__setattr__(self, "strategy_id", _optional_text(self.strategy_id))
        object.__setattr__(
            self,
            "asset_class",
            _optional_text(self.asset_class).upper()
            if _optional_text(self.asset_class)
            else None,
        )
        object.__setattr__(
            self,
            "price_source",
            _optional_text(self.price_source).upper()
            if _optional_text(self.price_source)
            else None,
        )
        object.__setattr__(
            self,
            "lot_size",
            _positive_int(self.lot_size, "change.lot_size"),
        )
        currency = _required_text(self.currency, "change.currency").upper()
        object.__setattr__(self, "currency", currency)

    @property
    def requested_delta_lots(self) -> int:
        return self.requested_target_lots - self.current_lots

    @property
    def risk_increasing(self) -> bool:
        return self.requested_delta_lots > 0

    @property
    def risk_reducing(self) -> bool:
        return self.requested_delta_lots < 0

    def reservation_for_target(self, target_lots: int) -> float:
        delta = max(0, target_lots - self.current_lots)
        return delta * self.reservation_per_lot_rub

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "strategy_id": self.strategy_id,
            "asset_class": self.asset_class,
            "current_lots": self.current_lots,
            "requested_target_lots": self.requested_target_lots,
            "requested_delta_lots": self.requested_delta_lots,
            "lot_size": self.lot_size,
            "currency": self.currency,
            "price_per_lot_rub": self.price_per_lot_rub,
            "reservation_per_lot_rub": self.reservation_per_lot_rub,
            "price_at": _iso(self.price_at),
            "price_source": self.price_source,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskInput:
    account_id: str
    snapshot_revision: int
    snapshot_checksum: str
    snapshot_at: datetime | None
    evaluated_at: datetime
    central_order_revision: int
    reservation_projection_hash: str
    risk_state_guard_hash: str
    nav_rub: float | None
    cash_available_rub: float | None
    positions: tuple[PositionRiskInput, ...]
    reservations: tuple[PortfolioRiskReservation, ...] = ()
    daily_turnover_rub: float = 0.0
    daily_start_equity_rub: float | None = None
    weekly_start_equity_rub: float | None = None
    high_watermark_equity_rub: float | None = None
    global_kill_switch: bool = False
    instrument_kill_switches: tuple[str, ...] = ()
    risk_resync_required: bool = False
    blocking_order_ids: tuple[str, ...] = ()
    data_quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "account_id",
            _required_text(self.account_id, "input.account_id"),
        )
        object.__setattr__(
            self,
            "snapshot_revision",
            _non_negative_int(self.snapshot_revision, "input.snapshot_revision"),
        )
        object.__setattr__(
            self,
            "central_order_revision",
            _non_negative_int(
                self.central_order_revision,
                "input.central_order_revision",
            ),
        )
        object.__setattr__(
            self,
            "snapshot_checksum",
            _sha256_text(self.snapshot_checksum, "input.snapshot_checksum"),
        )
        object.__setattr__(
            self,
            "reservation_projection_hash",
            _sha256_text(
                self.reservation_projection_hash,
                "input.reservation_projection_hash",
            ),
        )
        object.__setattr__(
            self,
            "risk_state_guard_hash",
            _sha256_text(
                self.risk_state_guard_hash,
                "input.risk_state_guard_hash",
            ),
        )
        object.__setattr__(
            self,
            "snapshot_at",
            _aware_datetime(self.snapshot_at, "input.snapshot_at", allow_none=True),
        )
        object.__setattr__(
            self,
            "evaluated_at",
            _aware_datetime(self.evaluated_at, "input.evaluated_at"),
        )
        object.__setattr__(
            self,
            "nav_rub",
            _finite_number(self.nav_rub, "input.nav_rub", allow_none=True),
        )
        object.__setattr__(
            self,
            "cash_available_rub",
            _finite_number(
                self.cash_available_rub,
                "input.cash_available_rub",
                allow_none=True,
            ),
        )
        positions = tuple(self.positions)
        if any(not isinstance(item, PositionRiskInput) for item in positions):
            raise PortfolioRiskInputError(
                "input.positions must contain PositionRiskInput values."
            )
        position_ids = [item.instrument_id for item in positions]
        if len(position_ids) != len(set(position_ids)):
            raise PortfolioRiskInputError("Duplicate position instrument_id.")
        object.__setattr__(
            self,
            "positions",
            tuple(sorted(positions, key=lambda item: item.instrument_id)),
        )
        reservations = tuple(self.reservations)
        if any(not isinstance(item, PortfolioRiskReservation) for item in reservations):
            raise PortfolioRiskInputError(
                "input.reservations must contain PortfolioRiskReservation values."
            )
        reservation_ids = [item.reservation_id for item in reservations]
        if len(reservation_ids) != len(set(reservation_ids)):
            raise PortfolioRiskInputError("Duplicate reservation_id.")
        object.__setattr__(
            self,
            "reservations",
            tuple(sorted(reservations, key=lambda item: item.reservation_id)),
        )
        object.__setattr__(
            self,
            "daily_turnover_rub",
            _finite_number(
                self.daily_turnover_rub,
                "input.daily_turnover_rub",
            ),
        )
        for field in (
            "daily_start_equity_rub",
            "weekly_start_equity_rub",
            "high_watermark_equity_rub",
        ):
            object.__setattr__(
                self,
                field,
                _finite_number(
                    getattr(self, field),
                    f"input.{field}",
                    allow_none=True,
                    positive=True,
                ),
            )
        for field in (
            "instrument_kill_switches",
            "blocking_order_ids",
            "data_quality_flags",
        ):
            object.__setattr__(
                self,
                field,
                _sorted_unique_text(getattr(self, field), f"input.{field}"),
            )
        for field in ("global_kill_switch", "risk_resync_required"):
            object.__setattr__(
                self,
                field,
                _boolean(getattr(self, field), f"input.{field}"),
            )

    @property
    def reserved_cash_rub(self) -> float:
        return sum(item.amount_rub for item in self.reservations)

    def position(self, instrument_id: str) -> PositionRiskInput | None:
        selected = str(instrument_id)
        return next(
            (item for item in self.positions if item.instrument_id == selected),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "snapshot_revision": self.snapshot_revision,
            "snapshot_checksum": self.snapshot_checksum,
            "snapshot_at": _iso(self.snapshot_at),
            "evaluated_at": _iso(self.evaluated_at),
            "central_order_revision": self.central_order_revision,
            "reservation_projection_hash": self.reservation_projection_hash,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "nav_rub": self.nav_rub,
            "cash_available_rub": self.cash_available_rub,
            "positions": [item.to_dict() for item in self.positions],
            "reservations": [item.to_dict() for item in self.reservations],
            "reserved_cash_rub": self.reserved_cash_rub,
            "daily_turnover_rub": self.daily_turnover_rub,
            "daily_start_equity_rub": self.daily_start_equity_rub,
            "weekly_start_equity_rub": self.weekly_start_equity_rub,
            "high_watermark_equity_rub": self.high_watermark_equity_rub,
            "global_kill_switch": self.global_kill_switch,
            "instrument_kill_switches": list(self.instrument_kill_switches),
            "risk_resync_required": self.risk_resync_required,
            "blocking_order_ids": list(self.blocking_order_ids),
            "data_quality_flags": list(self.data_quality_flags),
        }

    @property
    def input_hash(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class AssetClassConcentrationLimit:
    asset_class: str
    fraction: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "asset_class",
            _required_text(
                self.asset_class,
                "asset_class_limit.asset_class",
            ).upper(),
        )
        object.__setattr__(
            self,
            "fraction",
            _optional_fraction(
                self.fraction,
                "asset_class_limit.fraction",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"asset_class": self.asset_class, "fraction": self.fraction}


@dataclass(frozen=True, slots=True)
class PortfolioRiskPolicy:
    mode: str = "OBSERVE_ONLY"
    max_gross_exposure_rub: float | None = None
    max_gross_exposure_fraction: float | None = None
    max_net_exposure_fraction: float | None = None
    max_instrument_concentration_fraction: float | None = None
    max_strategy_concentration_fraction: float | None = None
    max_asset_class_concentration_fraction: float | None = None
    asset_class_limits: tuple[AssetClassConcentrationLimit, ...] = ()
    max_open_positions: int | None = None
    min_cash_reserve_rub: float | None = None
    min_cash_reserve_fraction: float | None = None
    max_daily_turnover_rub: float | None = None
    max_daily_turnover_fraction: float | None = None
    daily_loss_limit_rub: float | None = None
    daily_loss_limit_fraction: float | None = None
    weekly_loss_limit_rub: float | None = None
    weekly_loss_limit_fraction: float | None = None
    max_drawdown_fraction: float | None = None
    max_snapshot_age_seconds: float | None = 300.0
    max_price_age_seconds: float | None = 300.0
    warning_utilization_fraction: float = 0.8
    allow_risk_reducing_orders_during_halt: bool = True

    def __post_init__(self) -> None:
        mode = str(self.mode or "").strip().upper()
        if mode not in {"OBSERVE_ONLY", "ENFORCED"}:
            raise PortfolioRiskInputError(
                "policy.mode must be OBSERVE_ONLY or ENFORCED."
            )
        object.__setattr__(self, "mode", mode)
        for field in (
            "max_gross_exposure_rub",
            "max_daily_turnover_rub",
            "daily_loss_limit_rub",
            "weekly_loss_limit_rub",
        ):
            object.__setattr__(
                self,
                field,
                _optional_limit(getattr(self, field), f"policy.{field}"),
            )
        object.__setattr__(
            self,
            "min_cash_reserve_rub",
            _optional_non_negative(
                self.min_cash_reserve_rub,
                "policy.min_cash_reserve_rub",
            ),
        )
        for field in ("max_snapshot_age_seconds", "max_price_age_seconds"):
            object.__setattr__(
                self,
                field,
                _optional_non_negative(getattr(self, field), f"policy.{field}"),
            )
        for field in (
            "max_gross_exposure_fraction",
            "max_net_exposure_fraction",
            "max_instrument_concentration_fraction",
            "max_strategy_concentration_fraction",
            "max_asset_class_concentration_fraction",
            "min_cash_reserve_fraction",
            "max_daily_turnover_fraction",
            "daily_loss_limit_fraction",
            "weekly_loss_limit_fraction",
            "max_drawdown_fraction",
        ):
            object.__setattr__(
                self,
                field,
                _optional_fraction(getattr(self, field), f"policy.{field}"),
            )
        if self.max_open_positions is not None:
            normalized_positions = _non_negative_int(
                self.max_open_positions,
                "policy.max_open_positions",
            )
            if normalized_positions < 1:
                raise PortfolioRiskInputError(
                    "policy.max_open_positions must be positive."
                )
            object.__setattr__(self, "max_open_positions", normalized_positions)
        warning = _optional_fraction(
            self.warning_utilization_fraction,
            "policy.warning_utilization_fraction",
        )
        object.__setattr__(self, "warning_utilization_fraction", warning)
        object.__setattr__(
            self,
            "allow_risk_reducing_orders_during_halt",
            _boolean(
                self.allow_risk_reducing_orders_during_halt,
                "policy.allow_risk_reducing_orders_during_halt",
            ),
        )
        limits = tuple(self.asset_class_limits)
        if any(not isinstance(item, AssetClassConcentrationLimit) for item in limits):
            raise PortfolioRiskInputError(
                "policy.asset_class_limits must contain AssetClassConcentrationLimit values."
            )
        names = [item.asset_class for item in limits]
        if len(names) != len(set(names)):
            raise PortfolioRiskInputError("Duplicate asset-class policy limit.")
        object.__setattr__(
            self,
            "asset_class_limits",
            tuple(sorted(limits, key=lambda item: item.asset_class)),
        )

    def asset_class_limit(self, asset_class: str | None) -> float | None:
        selected = str(asset_class or "")
        override = next(
            (
                item.fraction
                for item in self.asset_class_limits
                if item.asset_class == selected
            ),
            None,
        )
        return (
            override
            if override is not None
            else self.max_asset_class_concentration_fraction
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "max_gross_exposure_rub": self.max_gross_exposure_rub,
            "max_gross_exposure_fraction": self.max_gross_exposure_fraction,
            "max_net_exposure_fraction": self.max_net_exposure_fraction,
            "max_instrument_concentration_fraction": (
                self.max_instrument_concentration_fraction
            ),
            "max_strategy_concentration_fraction": (
                self.max_strategy_concentration_fraction
            ),
            "max_asset_class_concentration_fraction": (
                self.max_asset_class_concentration_fraction
            ),
            "asset_class_limits": [item.to_dict() for item in self.asset_class_limits],
            "max_open_positions": self.max_open_positions,
            "min_cash_reserve_rub": self.min_cash_reserve_rub,
            "min_cash_reserve_fraction": self.min_cash_reserve_fraction,
            "max_daily_turnover_rub": self.max_daily_turnover_rub,
            "max_daily_turnover_fraction": self.max_daily_turnover_fraction,
            "daily_loss_limit_rub": self.daily_loss_limit_rub,
            "daily_loss_limit_fraction": self.daily_loss_limit_fraction,
            "weekly_loss_limit_rub": self.weekly_loss_limit_rub,
            "weekly_loss_limit_fraction": self.weekly_loss_limit_fraction,
            "max_drawdown_fraction": self.max_drawdown_fraction,
            "max_snapshot_age_seconds": self.max_snapshot_age_seconds,
            "max_price_age_seconds": self.max_price_age_seconds,
            "warning_utilization_fraction": self.warning_utilization_fraction,
            "allow_risk_reducing_orders_during_halt": (
                self.allow_risk_reducing_orders_during_halt
            ),
        }

    @property
    def policy_hash(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class PortfolioRiskExposureShare:
    key: str
    value_rub: float
    fraction: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _required_text(self.key, "exposure.key"))
        object.__setattr__(
            self,
            "value_rub",
            _finite_number(self.value_rub, "exposure.value_rub"),
        )
        object.__setattr__(
            self,
            "fraction",
            _finite_number(
                self.fraction,
                "exposure.fraction",
                allow_none=True,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value_rub": self.value_rub,
            "fraction": self.fraction,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskMetrics:
    nav_rub: float | None
    gross_exposure_rub: float | None
    gross_exposure_fraction: float | None
    net_exposure_rub: float | None
    net_exposure_fraction: float | None
    cash_available_rub: float | None
    cash_reserved_rub: float
    free_cash_rub: float | None
    free_cash_fraction: float | None
    open_positions: int
    hhi: float | None
    daily_turnover_rub: float
    daily_turnover_fraction: float | None
    daily_pnl_rub: float | None
    daily_return: float | None
    weekly_pnl_rub: float | None
    weekly_return: float | None
    drawdown_fraction: float | None
    instrument_exposure: tuple[PortfolioRiskExposureShare, ...]
    strategy_exposure: tuple[PortfolioRiskExposureShare, ...]
    asset_class_exposure: tuple[PortfolioRiskExposureShare, ...]
    unknown_instruments: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "nav_rub": self.nav_rub,
            "gross_exposure_rub": self.gross_exposure_rub,
            "gross_exposure_fraction": self.gross_exposure_fraction,
            "net_exposure_rub": self.net_exposure_rub,
            "net_exposure_fraction": self.net_exposure_fraction,
            "cash_available_rub": self.cash_available_rub,
            "cash_reserved_rub": self.cash_reserved_rub,
            "free_cash_rub": self.free_cash_rub,
            "free_cash_fraction": self.free_cash_fraction,
            "open_positions": self.open_positions,
            "hhi": self.hhi,
            "daily_turnover_rub": self.daily_turnover_rub,
            "daily_turnover_fraction": self.daily_turnover_fraction,
            "daily_pnl_rub": self.daily_pnl_rub,
            "daily_return": self.daily_return,
            "weekly_pnl_rub": self.weekly_pnl_rub,
            "weekly_return": self.weekly_return,
            "drawdown_fraction": self.drawdown_fraction,
            "instrument_exposure": [
                item.to_dict() for item in self.instrument_exposure
            ],
            "strategy_exposure": [item.to_dict() for item in self.strategy_exposure],
            "asset_class_exposure": [
                item.to_dict() for item in self.asset_class_exposure
            ],
            "unknown_instruments": list(self.unknown_instruments),
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskRuleCap:
    rule: str
    max_target_lots: int
    limit_value: float | int
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule", _required_text(self.rule, "cap.rule"))
        object.__setattr__(
            self,
            "max_target_lots",
            _non_negative_int(self.max_target_lots, "cap.max_target_lots"),
        )
        object.__setattr__(
            self,
            "limit_value",
            _finite_number(self.limit_value, "cap.limit_value"),
        )
        object.__setattr__(self, "detail", _required_text(self.detail, "cap.detail"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "max_target_lots": self.max_target_lots,
            "limit_value": self.limit_value,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskDecision:
    decision_id: str
    input_hash: str
    policy_hash: str
    mode: str
    status: str
    current_lots: int
    requested_target_lots: int
    approved_target_lots: int
    hard_blocks: tuple[str, ...]
    policy_halts: tuple[str, ...]
    adjustments: tuple[str, ...]
    warnings: tuple[str, ...]
    lot_caps: tuple[PortfolioRiskRuleCap, ...]
    current_metrics: PortfolioRiskMetrics
    requested_metrics: PortfolioRiskMetrics
    projected_metrics: PortfolioRiskMetrics
    snapshot_revision: int
    snapshot_checksum: str
    central_order_revision: int
    reservation_projection_hash: str
    risk_state_guard_hash: str
    evaluated_at: datetime
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        status = str(self.status).strip().upper()
        if status not in {
            "PASS",
            "ADJUSTED",
            "BLOCKED",
            "REDUCTION_ALLOWED",
            "HALTED",
        }:
            raise PortfolioRiskInputError(f"Unsupported decision status: {status}.")
        object.__setattr__(self, "status", status)
        mode = str(self.mode).strip().upper()
        if mode not in {"OBSERVE_ONLY", "ENFORCED"}:
            raise PortfolioRiskInputError(f"Unsupported decision mode: {mode}.")
        object.__setattr__(self, "mode", mode)
        for field in ("decision_id", "input_hash", "policy_hash"):
            object.__setattr__(
                self,
                field,
                _sha256_text(getattr(self, field), f"decision.{field}"),
            )
        current = _non_negative_int(self.current_lots, "decision.current_lots")
        requested = _non_negative_int(
            self.requested_target_lots,
            "decision.requested_target_lots",
        )
        approved = _non_negative_int(
            self.approved_target_lots,
            "decision.approved_target_lots",
        )
        lower, upper = sorted((current, requested))
        if not lower <= approved <= upper:
            raise PortfolioRiskInputError(
                "Approved target must remain between current and requested target."
            )
        object.__setattr__(self, "current_lots", current)
        object.__setattr__(self, "requested_target_lots", requested)
        object.__setattr__(self, "approved_target_lots", approved)
        if self.execution_authorized is not False:
            raise PortfolioRiskInputError(
                "Pure Portfolio Risk decision cannot authorize execution."
            )
        for field in ("hard_blocks", "policy_halts", "adjustments", "warnings"):
            object.__setattr__(
                self,
                field,
                _sorted_unique_text(getattr(self, field), f"decision.{field}"),
            )
        caps = tuple(self.lot_caps)
        if any(not isinstance(item, PortfolioRiskRuleCap) for item in caps):
            raise PortfolioRiskInputError(
                "decision.lot_caps must contain PortfolioRiskRuleCap values."
            )
        object.__setattr__(self, "lot_caps", tuple(sorted(caps, key=lambda x: x.rule)))
        for field in ("current_metrics", "requested_metrics", "projected_metrics"):
            if not isinstance(getattr(self, field), PortfolioRiskMetrics):
                raise PortfolioRiskInputError(
                    f"decision.{field} must be PortfolioRiskMetrics."
                )
        object.__setattr__(
            self,
            "snapshot_revision",
            _non_negative_int(self.snapshot_revision, "decision.snapshot_revision"),
        )
        object.__setattr__(
            self,
            "central_order_revision",
            _non_negative_int(
                self.central_order_revision,
                "decision.central_order_revision",
            ),
        )
        for field in (
            "snapshot_checksum",
            "reservation_projection_hash",
            "risk_state_guard_hash",
        ):
            object.__setattr__(
                self,
                field,
                _sha256_text(getattr(self, field), f"decision.{field}"),
            )
        object.__setattr__(
            self,
            "evaluated_at",
            _aware_datetime(self.evaluated_at, "decision.evaluated_at"),
        )

    @property
    def requested_delta_lots(self) -> int:
        return self.requested_target_lots - self.current_lots

    @property
    def approved_delta_lots(self) -> int:
        return self.approved_target_lots - self.current_lots

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "input_hash": self.input_hash,
            "policy_hash": self.policy_hash,
            "mode": self.mode,
            "status": self.status,
            "current_lots": self.current_lots,
            "requested_target_lots": self.requested_target_lots,
            "requested_delta_lots": self.requested_delta_lots,
            "approved_target_lots": self.approved_target_lots,
            "approved_delta_lots": self.approved_delta_lots,
            "hard_blocks": list(self.hard_blocks),
            "policy_halts": list(self.policy_halts),
            "adjustments": list(self.adjustments),
            "warnings": list(self.warnings),
            "lot_caps": [item.to_dict() for item in self.lot_caps],
            "current_metrics": self.current_metrics.to_dict(),
            "requested_metrics": self.requested_metrics.to_dict(),
            "projected_metrics": self.projected_metrics.to_dict(),
            "snapshot_revision": self.snapshot_revision,
            "snapshot_checksum": self.snapshot_checksum,
            "central_order_revision": self.central_order_revision,
            "reservation_projection_hash": self.reservation_projection_hash,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "evaluated_at": _iso(self.evaluated_at),
            "execution_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskAssessment:
    decision: PortfolioRiskDecision

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision.to_dict()}
