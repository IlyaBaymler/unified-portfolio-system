from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import floor, isfinite
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
RISK_STATE_VERSION = 3
_MAX_RECORDED_EXECUTION_IDS = 512


class RiskInputError(ValueError):
    """Raised when a risk snapshot or execution record is structurally invalid."""


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive_optional(name: str, value: float | None) -> None:
    if value is not None and (not isfinite(float(value)) or float(value) <= 0):
        raise ValueError(f"{name} must be positive or None.")


def _fraction_optional(name: str, value: float | None) -> None:
    if value is not None and (not isfinite(float(value)) or not 0 < float(value) <= 1):
        raise ValueError(f"{name} must be in (0, 1] or None.")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Account-level, strategy-agnostic risk limits.

    The policy may preserve or reduce a PRIMARY target, but it must never
    increase exposure above the strategy request. Limits apply to long-only
    positions in v3.7-alpha3. The module is independent from the broker and GUI.
    """

    enabled: bool = True
    max_position_lots: int = 1
    max_position_value_rub: float | None = 10_000.0
    max_position_share_of_equity: float | None = 0.20
    max_order_value_rub: float | None = 10_000.0
    cash_reserve_rub: float = 10_000.0
    max_cash_usage_fraction: float = 1.0
    commission_buffer_fraction: float = 0.002

    risk_per_trade_rub: float | None = 500.0
    risk_per_trade_fraction: float | None = None
    atr_multiplier: float = 3.0

    daily_loss_limit_rub: float | None = 500.0
    daily_loss_limit_fraction: float | None = 0.01
    weekly_loss_limit_rub: float | None = 1_500.0
    weekly_loss_limit_fraction: float | None = None
    max_drawdown_fraction: float | None = 0.05

    max_daily_turnover_rub: float | None = 20_000.0
    max_orders_per_day: int | None = 4
    max_snapshot_age_seconds: int | None = 120

    block_on_unknown_equity: bool = True
    block_on_unknown_cash: bool = True
    block_on_unknown_price: bool = True
    block_on_unknown_risk_distance: bool = True
    block_on_stale_snapshot: bool = True
    block_on_unreconciled_position: bool = True
    block_on_pending_order: bool = True

    allow_risk_reducing_orders_during_halt: bool = True

    # v3.9 Portfolio Risk fields are additive to the existing single-order
    # policy. Legacy profiles load with configuration disabled, so an upgrade
    # cannot silently activate new Sandbox limits.
    portfolio_policy_configured: bool = False
    portfolio_policy_mode: str = "OBSERVE_ONLY"
    max_gross_exposure_rub: float | None = None
    max_gross_exposure_fraction: float | None = None
    max_net_exposure_fraction: float | None = None
    max_instrument_concentration_fraction: float | None = None
    max_strategy_concentration_fraction: float | None = None
    max_asset_class_concentration_fraction: float | None = None
    asset_class_concentration_limits: tuple[tuple[str, float], ...] = ()
    max_open_positions: int | None = None
    min_cash_reserve_fraction: float | None = None
    max_daily_turnover_fraction: float | None = None
    max_price_age_seconds: int | None = 300
    portfolio_warning_utilization_fraction: float = 0.8

    def __post_init__(self) -> None:
        if self.max_position_lots < 0:
            raise ValueError("max_position_lots must not be negative.")
        for name, value in (
            ("max_position_value_rub", self.max_position_value_rub),
            ("max_order_value_rub", self.max_order_value_rub),
            ("risk_per_trade_rub", self.risk_per_trade_rub),
            ("daily_loss_limit_rub", self.daily_loss_limit_rub),
            ("weekly_loss_limit_rub", self.weekly_loss_limit_rub),
            ("max_daily_turnover_rub", self.max_daily_turnover_rub),
            ("max_gross_exposure_rub", self.max_gross_exposure_rub),
        ):
            _positive_optional(name, value)
        for name, value in (
            ("max_position_share_of_equity", self.max_position_share_of_equity),
            ("risk_per_trade_fraction", self.risk_per_trade_fraction),
            ("daily_loss_limit_fraction", self.daily_loss_limit_fraction),
            ("weekly_loss_limit_fraction", self.weekly_loss_limit_fraction),
            ("max_drawdown_fraction", self.max_drawdown_fraction),
            ("max_gross_exposure_fraction", self.max_gross_exposure_fraction),
            ("max_net_exposure_fraction", self.max_net_exposure_fraction),
            (
                "max_instrument_concentration_fraction",
                self.max_instrument_concentration_fraction,
            ),
            (
                "max_strategy_concentration_fraction",
                self.max_strategy_concentration_fraction,
            ),
            (
                "max_asset_class_concentration_fraction",
                self.max_asset_class_concentration_fraction,
            ),
            ("min_cash_reserve_fraction", self.min_cash_reserve_fraction),
            ("max_daily_turnover_fraction", self.max_daily_turnover_fraction),
        ):
            _fraction_optional(name, value)
        if not isfinite(float(self.cash_reserve_rub)) or self.cash_reserve_rub < 0:
            raise ValueError("cash_reserve_rub must be finite and non-negative.")
        if not 0 < self.max_cash_usage_fraction <= 1:
            raise ValueError("max_cash_usage_fraction must be in (0, 1].")
        if not 0 <= self.commission_buffer_fraction < 1:
            raise ValueError("commission_buffer_fraction must be in [0, 1).")
        if not isfinite(float(self.atr_multiplier)) or self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be positive.")
        if self.max_orders_per_day is not None and self.max_orders_per_day < 1:
            raise ValueError("max_orders_per_day must be positive or None.")
        if (
            self.max_snapshot_age_seconds is not None
            and self.max_snapshot_age_seconds < 0
        ):
            raise ValueError("max_snapshot_age_seconds must be non-negative or None.")
        if self.max_price_age_seconds is not None and self.max_price_age_seconds < 0:
            raise ValueError("max_price_age_seconds must be non-negative or None.")
        if self.max_open_positions is not None and self.max_open_positions < 1:
            raise ValueError("max_open_positions must be positive or None.")
        if not isinstance(self.portfolio_policy_configured, bool):
            raise TypeError("portfolio_policy_configured must be boolean.")
        portfolio_mode = str(self.portfolio_policy_mode or "").strip().upper()
        if portfolio_mode not in {"OBSERVE_ONLY", "ENFORCED"}:
            raise ValueError("portfolio_policy_mode must be OBSERVE_ONLY or ENFORCED.")
        object.__setattr__(self, "portfolio_policy_mode", portfolio_mode)
        _fraction_optional(
            "portfolio_warning_utilization_fraction",
            self.portfolio_warning_utilization_fraction,
        )
        normalized_asset_limits: list[tuple[str, float]] = []
        for raw in self.asset_class_concentration_limits:
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                raise ValueError(
                    "asset_class_concentration_limits entries must be "
                    "(asset_class, fraction) pairs."
                )
            asset_class = str(raw[0] or "").strip().upper()
            if not asset_class:
                raise ValueError("Asset-class limit name must not be empty.")
            fraction = float(raw[1])
            _fraction_optional(
                f"asset_class_concentration_limits.{asset_class}",
                fraction,
            )
            normalized_asset_limits.append((asset_class, fraction))
        names = [item[0] for item in normalized_asset_limits]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate asset-class concentration limit.")
        object.__setattr__(
            self,
            "asset_class_concentration_limits",
            tuple(sorted(normalized_asset_limits)),
        )

    @property
    def policy_hash(self) -> str:
        payload = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """All information required for one deterministic pre-trade evaluation."""

    now: datetime
    strategy_target_lots: int
    current_lots: int
    price_rub: float | None
    lot_size: int
    portfolio_equity_rub: float | None
    cash_rub: float | None
    securities_value_rub: float | None = None
    atr_rub: float | None = None
    stop_distance_rub: float | None = None
    snapshot_at: datetime | None = None
    position_reconciled: bool | None = True
    pending_order: bool | None = False
    mode: str = "DRY_RUN"

    def __post_init__(self) -> None:
        if not isinstance(self.now, datetime):
            raise RiskInputError("now must be a datetime.")
        if self.snapshot_at is not None and not isinstance(self.snapshot_at, datetime):
            raise RiskInputError("snapshot_at must be a datetime or None.")
        for name, value in (
            ("strategy_target_lots", self.strategy_target_lots),
            ("current_lots", self.current_lots),
        ):
            if isinstance(value, bool) or int(value) != value or int(value) < 0:
                raise RiskInputError(f"{name} must be a non-negative integer.")
        if isinstance(self.lot_size, bool) or int(self.lot_size) != self.lot_size:
            raise RiskInputError("lot_size must be a positive integer.")
        if int(self.lot_size) < 1:
            raise RiskInputError("lot_size must be a positive integer.")
        for name, value in (
            ("price_rub", self.price_rub),
            ("atr_rub", self.atr_rub),
            ("stop_distance_rub", self.stop_distance_rub),
        ):
            if value is not None and (
                _finite_or_none(value) is None or float(value) <= 0
            ):
                raise RiskInputError(f"{name} must be positive or None.")
        for name, value in (
            ("portfolio_equity_rub", self.portfolio_equity_rub),
            ("cash_rub", self.cash_rub),
            ("securities_value_rub", self.securities_value_rub),
        ):
            if value is not None and _finite_or_none(value) is None:
                raise RiskInputError(f"{name} must be finite or None.")


@dataclass(frozen=True, slots=True)
class InstrumentRiskHalt:
    instrument_id: str
    reason: str
    source: str
    set_at: str
    operator_ref: str | None = None

    def __post_init__(self) -> None:
        instrument_id = str(self.instrument_id or "").strip()
        reason = str(self.reason or "").strip()
        source = str(self.source or "").strip().upper()
        if not instrument_id or not reason or not source:
            raise RiskInputError(
                "Instrument risk halt requires instrument_id, reason and source."
            )
        try:
            parsed = datetime.fromisoformat(str(self.set_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise RiskInputError(
                "Instrument risk halt set_at must be ISO-8601."
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RiskInputError("Instrument risk halt set_at must be timezone-aware.")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "source", source)
        object.__setattr__(
            self,
            "set_at",
            parsed.astimezone(timezone.utc).isoformat(),
        )
        object.__setattr__(
            self,
            "operator_ref",
            str(self.operator_ref or "").strip() or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> InstrumentRiskHalt:
        return cls(
            instrument_id=value.get("instrument_id", ""),
            reason=value.get("reason", ""),
            source=value.get("source", ""),
            set_at=value.get("set_at", ""),
            operator_ref=value.get("operator_ref"),
        )


@dataclass(frozen=True, slots=True)
class RiskState:
    """Persistable account-level state used by loss and turnover limits."""

    version: int = RISK_STATE_VERSION
    daily_date: str | None = None
    weekly_key: str | None = None
    daily_start_equity_rub: float | None = None
    weekly_start_equity_rub: float | None = None
    high_watermark_equity_rub: float | None = None
    daily_turnover_rub: float = 0.0
    daily_order_count: int = 0
    kill_switch_active: bool = False
    kill_switch_reason: str | None = None
    kill_switch_set_at: str | None = None
    kill_switch_source: str | None = None
    kill_switch_operator_ref: str | None = None
    instrument_kill_switches: tuple[InstrumentRiskHalt, ...] = ()
    risk_resync_required: bool = False
    risk_resync_reason: str | None = None
    risk_resync_set_at: str | None = None
    last_equity_rub: float | None = None
    last_cash_rub: float | None = None
    last_snapshot_at: str | None = None
    last_evaluated_at: str | None = None
    last_execution_at: str | None = None
    recorded_execution_ids: tuple[str, ...] = ()
    last_portfolio_risk_decision_id: str | None = None
    last_portfolio_risk_input_hash: str | None = None
    last_portfolio_risk_evaluated_at: str | None = None

    def __post_init__(self) -> None:
        if self.version > RISK_STATE_VERSION:
            raise RiskInputError(
                f"Risk state version {self.version} is newer than supported "
                f"version {RISK_STATE_VERSION}."
            )
        if self.daily_turnover_rub < 0 or not isfinite(float(self.daily_turnover_rub)):
            raise RiskInputError("daily_turnover_rub must be finite and non-negative.")
        if self.daily_order_count < 0:
            raise RiskInputError("daily_order_count must not be negative.")
        halts = tuple(self.instrument_kill_switches)
        if any(not isinstance(item, InstrumentRiskHalt) for item in halts):
            raise RiskInputError(
                "instrument_kill_switches must contain InstrumentRiskHalt values."
            )
        ids = [item.instrument_id for item in halts]
        if len(ids) != len(set(ids)):
            raise RiskInputError("Duplicate instrument risk halt.")
        object.__setattr__(
            self,
            "instrument_kill_switches",
            tuple(sorted(halts, key=lambda item: item.instrument_id)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["version"] = RISK_STATE_VERSION
        payload["recorded_execution_ids"] = list(self.recorded_execution_ids)
        payload["instrument_kill_switches"] = [
            item.to_dict() for item in self.instrument_kill_switches
        ]
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> RiskState:
        if value is None:
            return cls()
        payload = dict(value)
        try:
            source_version = int(payload.get("version", 1) or 1)
        except (TypeError, ValueError) as exc:
            raise RiskInputError("Invalid risk state version.") from exc
        if source_version > RISK_STATE_VERSION:
            raise RiskInputError(
                f"Risk state version {source_version} is newer than supported "
                f"version {RISK_STATE_VERSION}."
            )
        # v1 -> v3 and v2 -> v3 are additive. Missing fields take safe defaults and
        # the state is rewritten with the current version on the next save.
        payload["version"] = RISK_STATE_VERSION
        payload.setdefault("risk_resync_required", False)
        payload.setdefault("risk_resync_reason", None)
        payload.setdefault("risk_resync_set_at", None)
        payload.setdefault("kill_switch_source", None)
        payload.setdefault("kill_switch_operator_ref", None)
        payload.setdefault("instrument_kill_switches", ())
        payload.setdefault("last_portfolio_risk_decision_id", None)
        payload.setdefault("last_portfolio_risk_input_hash", None)
        payload.setdefault("last_portfolio_risk_evaluated_at", None)
        raw_halts = payload.get("instrument_kill_switches") or ()
        if not isinstance(raw_halts, (list, tuple)):
            raise RiskInputError("instrument_kill_switches must be an array.")
        payload["instrument_kill_switches"] = tuple(
            item
            if isinstance(item, InstrumentRiskHalt)
            else InstrumentRiskHalt.from_dict(item)
            for item in raw_halts
        )
        payload["recorded_execution_ids"] = tuple(
            str(item) for item in payload.get("recorded_execution_ids") or ()
        )
        try:
            return cls(**payload)
        except (TypeError, ValueError) as exc:
            raise RiskInputError(f"Invalid risk state: {exc}") from exc


@dataclass(frozen=True, slots=True)
class RiskEvent:
    event_type: str
    severity: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "severity": self.severity,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class RiskDecision:
    status: str
    policy_hash: str
    requested_target_lots: int
    approved_target_lots: int
    current_lots: int
    requested_delta_lots: int
    approved_delta_lots: int
    requested_action: str
    approved_action: str
    risk_increasing: bool
    risk_reducing: bool
    reduce_only: bool
    order_allowed: bool
    risk_halted: bool
    absolute_block: bool
    reasons: tuple[str, ...]
    breaches: tuple[str, ...]
    lot_caps: dict[str, int]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "policy_hash": self.policy_hash,
            "requested_target_lots": self.requested_target_lots,
            "approved_target_lots": self.approved_target_lots,
            "current_lots": self.current_lots,
            "requested_delta_lots": self.requested_delta_lots,
            "approved_delta_lots": self.approved_delta_lots,
            "requested_action": self.requested_action,
            "approved_action": self.approved_action,
            "risk_increasing": self.risk_increasing,
            "risk_reducing": self.risk_reducing,
            "reduce_only": self.reduce_only,
            "order_allowed": self.order_allowed,
            "risk_halted": self.risk_halted,
            "absolute_block": self.absolute_block,
            "reasons": list(self.reasons),
            "breaches": list(self.breaches),
            "lot_caps": dict(self.lot_caps),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    decision: RiskDecision
    state: RiskState
    events: tuple[RiskEvent, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "state": self.state.to_dict(),
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_id: str
    executed_at: datetime
    signed_lots: int
    price_rub: float
    lot_size: int
    portfolio_equity_rub: float | None = None
    execution_source: str = "STRATEGY"

    def __post_init__(self) -> None:
        if not str(self.execution_id).strip():
            raise RiskInputError("execution_id must not be empty.")
        if not isinstance(self.executed_at, datetime):
            raise RiskInputError("executed_at must be a datetime.")
        if (
            isinstance(self.signed_lots, bool)
            or int(self.signed_lots) != self.signed_lots
        ):
            raise RiskInputError("signed_lots must be an integer.")
        if int(self.signed_lots) == 0:
            raise RiskInputError("signed_lots must not be zero.")
        if _finite_or_none(self.price_rub) is None or self.price_rub <= 0:
            raise RiskInputError("price_rub must be positive.")
        if isinstance(self.lot_size, bool) or int(self.lot_size) != self.lot_size:
            raise RiskInputError("lot_size must be a positive integer.")
        if int(self.lot_size) < 1:
            raise RiskInputError("lot_size must be a positive integer.")
        if (
            self.portfolio_equity_rub is not None
            and _finite_or_none(self.portfolio_equity_rub) is None
        ):
            raise RiskInputError("portfolio_equity_rub must be finite or None.")
        normalized_source = str(self.execution_source).strip().upper()
        if not normalized_source:
            raise RiskInputError("execution_source must not be empty.")


@dataclass(frozen=True, slots=True)
class ExecutionRegistration:
    state: RiskState
    events: tuple[RiskEvent, ...]
    duplicate: bool
    turnover_added_rub: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "duplicate": self.duplicate,
            "turnover_added_rub": self.turnover_added_rub,
        }


def _action(delta: int) -> str:
    if delta > 0:
        return "BUY"
    if delta < 0:
        return "SELL"
    return "HOLD"


def _date_key(now: datetime) -> str:
    return _utc(now).astimezone(MOSCOW_TZ).date().isoformat()


def _week_key(now: datetime) -> str:
    iso = _utc(now).astimezone(MOSCOW_TZ).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _make_event(event_type: str, severity: str = "INFO", **details: Any) -> RiskEvent:
    return RiskEvent(event_type=event_type, severity=severity, details=details)


def _roll_state_periods(
    state: RiskState,
    *,
    now: datetime,
    equity_rub: float | None,
) -> tuple[RiskState, tuple[RiskEvent, ...]]:
    events: list[RiskEvent] = []
    date_key = _date_key(now)
    week_key = _week_key(now)
    updated = state

    if state.daily_date != date_key:
        updated = replace(
            updated,
            daily_date=date_key,
            daily_start_equity_rub=equity_rub,
            daily_turnover_rub=0.0,
            daily_order_count=0,
        )
        events.append(
            _make_event(
                "DAILY_BASELINE_INITIALIZED",
                daily_date=date_key,
                equity_rub=equity_rub,
            )
        )
    elif state.daily_start_equity_rub is None and equity_rub is not None:
        updated = replace(updated, daily_start_equity_rub=equity_rub)
        events.append(
            _make_event(
                "DAILY_BASELINE_INITIALIZED",
                daily_date=date_key,
                equity_rub=equity_rub,
            )
        )

    if state.weekly_key != week_key:
        updated = replace(
            updated,
            weekly_key=week_key,
            weekly_start_equity_rub=equity_rub,
        )
        events.append(
            _make_event(
                "WEEKLY_BASELINE_INITIALIZED",
                weekly_key=week_key,
                equity_rub=equity_rub,
            )
        )
    elif state.weekly_start_equity_rub is None and equity_rub is not None:
        updated = replace(updated, weekly_start_equity_rub=equity_rub)
        events.append(
            _make_event(
                "WEEKLY_BASELINE_INITIALIZED",
                weekly_key=week_key,
                equity_rub=equity_rub,
            )
        )

    high = updated.high_watermark_equity_rub
    if equity_rub is not None and (high is None or equity_rub > high):
        updated = replace(updated, high_watermark_equity_rub=equity_rub)
        events.append(_make_event("HIGH_WATERMARK_UPDATED", equity_rub=equity_rub))
    return updated, tuple(events)


def portfolio_risk_inputs(portfolio: Mapping[str, Any]) -> dict[str, float | None]:
    """Extract ruble-equivalent totals from a T-Invest portfolio response."""

    def quotation(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return _finite_or_none(value)
        if not isinstance(value, Mapping):
            return None
        try:
            units = float(value.get("units", 0) or 0)
            nano = float(value.get("nano", 0) or 0)
        except (TypeError, ValueError):
            return None
        return _finite_or_none(units + nano / 1_000_000_000)

    components = (
        "totalAmountShares",
        "totalAmountBonds",
        "totalAmountEtf",
        "totalAmountFutures",
        "totalAmountOptions",
        "totalAmountSp",
    )
    securities = 0.0
    for key in components:
        securities += quotation(portfolio.get(key)) or 0.0
    return {
        "equity_rub": quotation(portfolio.get("totalAmountPortfolio")),
        "cash_rub": quotation(portfolio.get("totalAmountCurrencies")),
        "securities_value_rub": securities,
        "expected_yield_rub": quotation(portfolio.get("expectedYield")),
    }


def average_true_range(candles: pd.DataFrame, window: int) -> float | None:
    """Return the latest simple ATR for a completed OHLC frame."""

    if window < 2 or candles is None or len(candles) < window + 1:
        return None
    required = {"high", "low", "close"}
    if not required.issubset(candles.columns):
        return None
    high = pd.to_numeric(candles["high"], errors="coerce")
    low = pd.to_numeric(candles["low"], errors="coerce")
    close = pd.to_numeric(candles["close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = true_range.rolling(window).mean().iloc[-1]
    return _finite_or_none(value)


class RiskEngine:
    """Deterministic risk evaluator with explicit persistent state transitions."""

    def __init__(self, policy: RiskPolicy) -> None:
        self.policy = policy

    def engage_kill_switch(
        self,
        state: RiskState,
        *,
        now: datetime,
        reason: str,
        source: str = "OPERATOR",
        operator_ref: str | None = None,
    ) -> tuple[RiskState, RiskEvent]:
        normalized_reason = str(reason).strip() or "Manual kill switch"
        normalized_source = str(source or "").strip().upper() or "OPERATOR"
        normalized_ref = str(operator_ref or "").strip() or None
        idempotent = (
            state.kill_switch_active
            and state.kill_switch_reason == normalized_reason
            and state.kill_switch_source == normalized_source
            and state.kill_switch_operator_ref == normalized_ref
        )
        if idempotent:
            return state, _make_event(
                "KILL_SWITCH_ENABLED",
                "WARNING",
                reason=normalized_reason,
                source=normalized_source,
                operator_ref=normalized_ref,
                set_at=state.kill_switch_set_at,
                idempotent=True,
            )
        updated = replace(
            state,
            kill_switch_active=True,
            kill_switch_reason=normalized_reason,
            kill_switch_set_at=_utc(now).isoformat(),
            kill_switch_source=normalized_source,
            kill_switch_operator_ref=normalized_ref,
        )
        return updated, _make_event(
            "KILL_SWITCH_ENABLED",
            "WARNING",
            reason=normalized_reason,
            source=normalized_source,
            operator_ref=normalized_ref,
            set_at=updated.kill_switch_set_at,
            idempotent=False,
        )

    def clear_kill_switch(
        self,
        state: RiskState,
        *,
        now: datetime,
        confirmation: str,
    ) -> tuple[RiskState, RiskEvent]:
        if str(confirmation).strip().upper() != "CLEAR RISK HALT":
            raise RiskInputError(
                "Kill switch confirmation must be exactly 'CLEAR RISK HALT'."
            )
        updated = replace(
            state,
            kill_switch_active=False,
            kill_switch_reason=None,
            kill_switch_set_at=None,
            kill_switch_source=None,
            kill_switch_operator_ref=None,
        )
        return updated, _make_event(
            "KILL_SWITCH_DISABLED",
            "WARNING",
            cleared_at=_utc(now).isoformat(),
        )

    def engage_instrument_kill_switch(
        self,
        state: RiskState,
        *,
        instrument_id: str,
        now: datetime,
        reason: str,
        source: str = "OPERATOR",
        operator_ref: str | None = None,
    ) -> tuple[RiskState, RiskEvent]:
        halt = InstrumentRiskHalt(
            instrument_id=instrument_id,
            reason=str(reason).strip() or "Manual instrument kill switch",
            source=source,
            operator_ref=operator_ref,
            set_at=_utc(now).isoformat(),
        )
        existing = next(
            (
                item
                for item in state.instrument_kill_switches
                if item.instrument_id == halt.instrument_id
            ),
            None,
        )
        if (
            existing is not None
            and existing.reason == halt.reason
            and existing.source == halt.source
            and existing.operator_ref == halt.operator_ref
        ):
            return state, _make_event(
                "INSTRUMENT_KILL_SWITCH_ENABLED",
                "WARNING",
                **existing.to_dict(),
                idempotent=True,
            )
        remaining = tuple(
            item
            for item in state.instrument_kill_switches
            if item.instrument_id != halt.instrument_id
        )
        updated = replace(
            state,
            instrument_kill_switches=(*remaining, halt),
        )
        return updated, _make_event(
            "INSTRUMENT_KILL_SWITCH_ENABLED",
            "WARNING",
            **halt.to_dict(),
            idempotent=False,
        )

    def clear_instrument_kill_switch(
        self,
        state: RiskState,
        *,
        instrument_id: str,
        now: datetime,
        confirmation: str,
    ) -> tuple[RiskState, RiskEvent]:
        selected = str(instrument_id or "").strip()
        expected = f"CLEAR INSTRUMENT RISK HALT {selected}".upper()
        if not selected or str(confirmation).strip().upper() != expected:
            raise RiskInputError(
                f"Instrument kill switch confirmation must be exactly '{expected}'."
            )
        remaining = tuple(
            item
            for item in state.instrument_kill_switches
            if item.instrument_id != selected
        )
        idempotent = len(remaining) == len(state.instrument_kill_switches)
        updated = replace(state, instrument_kill_switches=remaining)
        return updated, _make_event(
            "INSTRUMENT_KILL_SWITCH_DISABLED",
            "WARNING",
            instrument_id=selected,
            cleared_at=_utc(now).isoformat(),
            idempotent=idempotent,
        )

    def mark_external_activity(
        self,
        state: RiskState,
        *,
        now: datetime,
        reason: str,
        source: str,
    ) -> tuple[RiskState, RiskEvent]:
        normalized_reason = str(reason).strip() or "External account activity"
        normalized_source = str(source).strip().upper() or "UNKNOWN"
        set_at = _utc(now).isoformat()
        updated = replace(
            state,
            risk_resync_required=True,
            risk_resync_reason=normalized_reason,
            risk_resync_set_at=set_at,
        )
        return updated, _make_event(
            "EXTERNAL_ACTIVITY_DETECTED",
            "ERROR",
            reason=normalized_reason,
            source=normalized_source,
            risk_resync_required=True,
            set_at=set_at,
        )

    def reset_baselines(
        self,
        state: RiskState,
        *,
        now: datetime,
        equity_rub: float | None,
        confirmation: str,
    ) -> tuple[RiskState, tuple[RiskEvent, ...]]:
        if str(confirmation).strip().upper() != "RESET RISK BASELINES":
            raise RiskInputError(
                "Baseline reset confirmation must be exactly 'RESET RISK BASELINES'."
            )
        normalized_equity = _finite_or_none(equity_rub)
        resync_was_required = bool(state.risk_resync_required)
        updated = replace(
            state,
            daily_date=_date_key(now),
            weekly_key=_week_key(now),
            daily_start_equity_rub=normalized_equity,
            weekly_start_equity_rub=normalized_equity,
            high_watermark_equity_rub=normalized_equity,
            daily_turnover_rub=0.0,
            daily_order_count=0,
            risk_resync_required=False,
            risk_resync_reason=None,
            risk_resync_set_at=None,
            last_equity_rub=normalized_equity,
            last_evaluated_at=_utc(now).isoformat(),
        )
        events = [
            _make_event(
                "RISK_BASELINES_RESET",
                "WARNING",
                equity_rub=normalized_equity,
                reset_at=_utc(now).isoformat(),
            )
        ]
        if resync_was_required:
            events.append(
                _make_event(
                    "RISK_RESYNC_COMPLETED",
                    "WARNING",
                    equity_rub=normalized_equity,
                    completed_at=_utc(now).isoformat(),
                )
            )
        return updated, tuple(events)

    def initialize_pristine_baselines(
        self,
        state: RiskState,
        *,
        now: datetime,
        equity_rub: float,
        cash_rub: float,
        snapshot_at: datetime,
    ) -> tuple[RiskState, RiskEvent]:
        """Initialize a never-used account state from one fresh broker snapshot.

        This is deliberately narrower than ``reset_baselines``: it refuses any
        prior baseline, execution accounting, halt or resynchronization state.
        It therefore cannot erase history and needs no reset confirmation.
        """

        normalized_equity = _finite_or_none(equity_rub)
        normalized_cash = _finite_or_none(cash_rub)
        if normalized_equity is None or normalized_equity < 0:
            raise RiskInputError("Initial equity must be finite and non-negative.")
        if normalized_cash is None or normalized_cash < 0:
            raise RiskInputError("Initial cash must be finite and non-negative.")
        if not isinstance(snapshot_at, datetime):
            raise RiskInputError("Initial snapshot_at must be a datetime.")
        baseline_fields = (
            state.daily_date,
            state.weekly_key,
            state.daily_start_equity_rub,
            state.weekly_start_equity_rub,
            state.high_watermark_equity_rub,
            state.last_equity_rub,
            state.last_cash_rub,
            state.last_snapshot_at,
            state.last_evaluated_at,
            state.last_execution_at,
            state.last_portfolio_risk_decision_id,
            state.last_portfolio_risk_input_hash,
            state.last_portfolio_risk_evaluated_at,
        )
        if (
            any(value is not None for value in baseline_fields)
            or state.daily_turnover_rub != 0
            or state.daily_order_count != 0
            or state.recorded_execution_ids
            or state.kill_switch_active
            or state.instrument_kill_switches
            or state.risk_resync_required
        ):
            raise RiskInputError(
                "Risk baselines can be initialized only from a pristine state."
            )
        evaluated_at = _utc(now).isoformat()
        source_snapshot_at = _utc(snapshot_at).isoformat()
        updated = replace(
            state,
            daily_date=_date_key(now),
            weekly_key=_week_key(now),
            daily_start_equity_rub=normalized_equity,
            weekly_start_equity_rub=normalized_equity,
            high_watermark_equity_rub=normalized_equity,
            last_equity_rub=normalized_equity,
            last_cash_rub=normalized_cash,
            last_snapshot_at=source_snapshot_at,
            last_evaluated_at=evaluated_at,
        )
        return updated, _make_event(
            "RISK_BASELINES_INITIALIZED",
            equity_rub=normalized_equity,
            cash_rub=normalized_cash,
            snapshot_at=source_snapshot_at,
            initialized_at=evaluated_at,
        )

    def evaluate(
        self,
        snapshot: RiskSnapshot,
        state: RiskState | None = None,
    ) -> RiskAssessment:
        state = state or RiskState()
        now = _utc(snapshot.now)
        equity = _finite_or_none(snapshot.portfolio_equity_rub)
        cash = _finite_or_none(snapshot.cash_rub)
        price = _finite_or_none(snapshot.price_rub)
        atr = _finite_or_none(snapshot.atr_rub)
        stop_distance = _finite_or_none(snapshot.stop_distance_rub)

        rolled, period_events = _roll_state_periods(
            state,
            now=now,
            equity_rub=equity,
        )
        events: list[RiskEvent] = list(period_events)

        high = rolled.high_watermark_equity_rub
        daily_start = rolled.daily_start_equity_rub
        weekly_start = rolled.weekly_start_equity_rub
        daily_pnl = (
            None if equity is None or daily_start is None else equity - daily_start
        )
        weekly_pnl = (
            None if equity is None or weekly_start is None else equity - weekly_start
        )
        daily_return = (
            None
            if daily_pnl is None or daily_start in (None, 0)
            else daily_pnl / daily_start
        )
        weekly_return = (
            None
            if weekly_pnl is None or weekly_start in (None, 0)
            else weekly_pnl / weekly_start
        )
        drawdown = (
            None if equity is None or high in (None, 0) else (equity - high) / high
        )

        snapshot_time_missing = snapshot.snapshot_at is None
        snapshot_at = _utc(snapshot.snapshot_at) if snapshot.snapshot_at else None
        snapshot_age = (
            None
            if snapshot_at is None
            else max(0.0, (now - snapshot_at).total_seconds())
        )
        snapshot_from_future = (
            snapshot_at is not None and snapshot_at > now + timedelta(seconds=5)
        )

        requested_target = int(snapshot.strategy_target_lots)
        current = int(snapshot.current_lots)
        requested_delta = requested_target - current
        requested_action = _action(requested_delta)
        risk_increasing = requested_delta > 0
        risk_reducing = requested_delta < 0

        metrics: dict[str, Any] = {
            "mode": str(snapshot.mode),
            "evaluated_at": now.isoformat(),
            "snapshot_at": snapshot_at.isoformat() if snapshot_at else None,
            "snapshot_age_seconds": snapshot_age,
            "equity_rub": equity,
            "cash_rub": cash,
            "securities_value_rub": _finite_or_none(snapshot.securities_value_rub),
            "price_rub": price,
            "lot_size": int(snapshot.lot_size),
            "atr_rub": atr,
            "explicit_stop_distance_rub": stop_distance,
            "daily_start_equity_rub": daily_start,
            "weekly_start_equity_rub": weekly_start,
            "high_watermark_equity_rub": high,
            "daily_pnl_rub": daily_pnl,
            "weekly_pnl_rub": weekly_pnl,
            "daily_return": daily_return,
            "weekly_return": weekly_return,
            "drawdown": drawdown,
            "daily_turnover_rub": rolled.daily_turnover_rub,
            "daily_order_count": rolled.daily_order_count,
            "max_daily_turnover_rub": self.policy.max_daily_turnover_rub,
            "max_orders_per_day": self.policy.max_orders_per_day,
            "position_reconciled": snapshot.position_reconciled,
            "pending_order": snapshot.pending_order,
            "kill_switch_active": rolled.kill_switch_active,
            "risk_resync_required": rolled.risk_resync_required,
            "risk_resync_reason": rolled.risk_resync_reason,
            "risk_resync_set_at": rolled.risk_resync_set_at,
        }

        if not self.policy.enabled:
            approved_target = requested_target
            approved_delta = approved_target - current
            decision = RiskDecision(
                status="DISABLED",
                policy_hash=self.policy.policy_hash,
                requested_target_lots=requested_target,
                approved_target_lots=approved_target,
                current_lots=current,
                requested_delta_lots=requested_delta,
                approved_delta_lots=approved_delta,
                requested_action=requested_action,
                approved_action=_action(approved_delta),
                risk_increasing=risk_increasing,
                risk_reducing=risk_reducing,
                reduce_only=risk_reducing,
                order_allowed=approved_delta != 0,
                risk_halted=False,
                absolute_block=False,
                reasons=("Risk Engine is disabled; strategy target passed through.",),
                breaches=(),
                lot_caps={},
                metrics=metrics,
            )
            updated = replace(
                rolled,
                last_equity_rub=equity,
                last_cash_rub=cash,
                last_snapshot_at=(snapshot_at.isoformat() if snapshot_at else None),
                last_evaluated_at=now.isoformat(),
            )
            return RiskAssessment(decision, updated, tuple(events))

        absolute_breaches: list[str] = []
        increase_breaches: list[str] = []
        reasons: list[str] = []

        if self.policy.block_on_stale_snapshot and snapshot_time_missing:
            absolute_breaches.append("SNAPSHOT_TIME_UNKNOWN")
        if snapshot_from_future:
            absolute_breaches.append("SNAPSHOT_FROM_FUTURE")
        if (
            self.policy.block_on_stale_snapshot
            and self.policy.max_snapshot_age_seconds is not None
            and snapshot_age is not None
            and snapshot_age > self.policy.max_snapshot_age_seconds
        ):
            absolute_breaches.append("STALE_PORTFOLIO_SNAPSHOT")
        if (
            self.policy.block_on_unreconciled_position
            and snapshot.position_reconciled is not True
        ):
            absolute_breaches.append("POSITION_NOT_RECONCILED")
        if self.policy.block_on_pending_order:
            if snapshot.pending_order is True:
                absolute_breaches.append("PENDING_ORDER_ACTIVE")
            elif snapshot.pending_order is None:
                absolute_breaches.append("PENDING_ORDER_STATE_UNKNOWN")
        if price is None and self.policy.block_on_unknown_price:
            absolute_breaches.append("UNKNOWN_PRICE")
        if rolled.risk_resync_required:
            absolute_breaches.append("RISK_RESYNC_REQUIRED")

        if rolled.kill_switch_active:
            increase_breaches.append("KILL_SWITCH")
        if equity is None and self.policy.block_on_unknown_equity:
            increase_breaches.append("UNKNOWN_EQUITY")
        if cash is None and self.policy.block_on_unknown_cash:
            increase_breaches.append("UNKNOWN_CASH")

        if (
            self.policy.daily_loss_limit_rub is not None
            and daily_pnl is not None
            and daily_pnl <= -self.policy.daily_loss_limit_rub
        ):
            increase_breaches.append("DAILY_LOSS_LIMIT_RUB")
        if (
            self.policy.daily_loss_limit_fraction is not None
            and daily_return is not None
            and daily_return <= -self.policy.daily_loss_limit_fraction
        ):
            increase_breaches.append("DAILY_LOSS_LIMIT_FRACTION")
        if (
            self.policy.weekly_loss_limit_rub is not None
            and weekly_pnl is not None
            and weekly_pnl <= -self.policy.weekly_loss_limit_rub
        ):
            increase_breaches.append("WEEKLY_LOSS_LIMIT_RUB")
        if (
            self.policy.weekly_loss_limit_fraction is not None
            and weekly_return is not None
            and weekly_return <= -self.policy.weekly_loss_limit_fraction
        ):
            increase_breaches.append("WEEKLY_LOSS_LIMIT_FRACTION")
        if (
            self.policy.max_drawdown_fraction is not None
            and drawdown is not None
            and drawdown <= -self.policy.max_drawdown_fraction
        ):
            increase_breaches.append("MAX_DRAWDOWN")
        if (
            self.policy.max_orders_per_day is not None
            and rolled.daily_order_count >= self.policy.max_orders_per_day
        ):
            increase_breaches.append("MAX_ORDERS_PER_DAY")
            reasons.append(
                "Daily order limit reached: "
                f"{rolled.daily_order_count}/{self.policy.max_orders_per_day}; "
                "new exposure is blocked until the next Moscow trading day."
            )
        if (
            self.policy.max_daily_turnover_rub is not None
            and rolled.daily_turnover_rub >= self.policy.max_daily_turnover_rub
        ):
            increase_breaches.append("MAX_DAILY_TURNOVER")
            reasons.append(
                "Daily turnover limit reached: "
                f"{rolled.daily_turnover_rub:.2f}/"
                f"{self.policy.max_daily_turnover_rub:.2f} RUB; "
                "new exposure is blocked until the next Moscow trading day."
            )

        lot_caps: dict[str, int] = {
            "MAX_POSITION_LOTS": max(0, self.policy.max_position_lots)
        }
        price_per_lot = None
        if price is not None:
            price_per_lot = price * int(snapshot.lot_size)
            metrics["price_per_lot_rub"] = price_per_lot
            if self.policy.max_position_value_rub is not None:
                lot_caps["MAX_POSITION_VALUE"] = max(
                    0,
                    floor(self.policy.max_position_value_rub / price_per_lot),
                )
            if (
                self.policy.max_position_share_of_equity is not None
                and equity is not None
                and equity > 0
            ):
                lot_caps["MAX_POSITION_SHARE"] = max(
                    0,
                    floor(
                        equity
                        * self.policy.max_position_share_of_equity
                        / price_per_lot
                    ),
                )
            if cash is not None:
                spendable = max(0.0, cash - self.policy.cash_reserve_rub)
                spendable *= self.policy.max_cash_usage_fraction
                buffered_cost = price_per_lot * (
                    1.0 + self.policy.commission_buffer_fraction
                )
                lot_caps["CASH_RESERVE"] = current + max(
                    0,
                    floor(spendable / buffered_cost),
                )
            if self.policy.max_order_value_rub is not None:
                lot_caps["MAX_ORDER_VALUE"] = current + max(
                    0,
                    floor(self.policy.max_order_value_rub / price_per_lot),
                )
            if self.policy.max_daily_turnover_rub is not None:
                turnover_remaining = max(
                    0.0,
                    self.policy.max_daily_turnover_rub - rolled.daily_turnover_rub,
                )
                lot_caps["DAILY_TURNOVER_REMAINING"] = current + max(
                    0,
                    floor(turnover_remaining / price_per_lot),
                )

        risk_budget = self.policy.risk_per_trade_rub
        if (
            risk_budget is None
            and self.policy.risk_per_trade_fraction is not None
            and equity is not None
            and equity > 0
        ):
            risk_budget = equity * self.policy.risk_per_trade_fraction
        risk_distance = stop_distance
        risk_distance_source = "STOP_DISTANCE"
        if risk_distance is None and atr is not None:
            risk_distance = atr * self.policy.atr_multiplier
            risk_distance_source = "ATR"
        risk_sizing_requested = (
            self.policy.risk_per_trade_rub is not None
            or self.policy.risk_per_trade_fraction is not None
        )
        if risk_sizing_requested:
            if risk_budget is not None and risk_distance is not None:
                risk_per_lot = risk_distance * int(snapshot.lot_size)
                metrics["risk_budget_rub"] = risk_budget
                metrics["risk_distance_rub"] = risk_distance
                metrics["risk_distance_source"] = risk_distance_source
                metrics["risk_per_lot_rub"] = risk_per_lot
                lot_caps["RISK_PER_TRADE"] = max(
                    0,
                    floor(risk_budget / risk_per_lot),
                )
            elif self.policy.block_on_unknown_risk_distance:
                increase_breaches.append("UNKNOWN_RISK_DISTANCE")
                reasons.append(
                    "Risk-per-trade sizing requested, but equity/risk distance "
                    "is unavailable."
                )

        effective_cap = min(lot_caps.values()) if lot_caps else requested_target
        metrics["effective_lot_cap"] = effective_cap

        absolute_block = bool(absolute_breaches)
        increase_halted = bool(increase_breaches)

        reduction_blocked = False
        if risk_reducing:
            if absolute_block:
                approved_target = current
                reduction_blocked = True
                reasons.append(
                    "Risk-reducing order blocked because execution state is "
                    "ambiguous or portfolio data is unsafe."
                )
            elif (
                increase_halted
                and not self.policy.allow_risk_reducing_orders_during_halt
            ):
                approved_target = current
                reduction_blocked = True
                reasons.append("Risk-reducing orders are disabled by policy.")
            else:
                approved_target = requested_target
                if increase_halted:
                    reasons.append(
                        "New exposure is halted; requested risk reduction remains allowed."
                    )
        elif risk_increasing:
            if absolute_block or increase_halted:
                approved_target = current
                reasons.append("New exposure blocked by Risk Engine.")
            else:
                approved_target = min(
                    requested_target,
                    max(current, effective_cap),
                )
                if approved_target < requested_target:
                    reasons.append(
                        f"PRIMARY target reduced from {requested_target} to "
                        f"{approved_target} lots."
                    )
                    for name, value in lot_caps.items():
                        if value == effective_cap:
                            increase_breaches.append(name)
        else:
            approved_target = current
            if absolute_block or increase_halted:
                reasons.append(
                    "No order requested; Risk Engine remains halted for new exposure."
                )

        approved_target = max(
            0, min(approved_target, requested_target if risk_increasing else current)
        )
        approved_delta = approved_target - current
        approved_action = _action(approved_delta)

        all_breaches = tuple(dict.fromkeys([*absolute_breaches, *increase_breaches]))
        if (
            reduction_blocked
            or absolute_block
            and requested_delta != 0
            or risk_increasing
            and approved_target == current
        ):
            status = "BLOCKED"
        elif risk_increasing and approved_target < requested_target:
            status = "ADJUSTED"
        elif risk_reducing and approved_target < current and increase_halted:
            status = "REDUCTION_ALLOWED"
        elif requested_delta == 0 and (absolute_block or increase_halted):
            status = "HALTED"
        else:
            status = "PASS"

        if not reasons:
            reasons.append("Risk policy passed without target adjustment.")

        decision = RiskDecision(
            status=status,
            policy_hash=self.policy.policy_hash,
            requested_target_lots=requested_target,
            approved_target_lots=approved_target,
            current_lots=current,
            requested_delta_lots=requested_delta,
            approved_delta_lots=approved_delta,
            requested_action=requested_action,
            approved_action=approved_action,
            risk_increasing=risk_increasing,
            risk_reducing=risk_reducing,
            reduce_only=risk_reducing and approved_delta < 0,
            order_allowed=approved_delta != 0,
            risk_halted=absolute_block or increase_halted,
            absolute_block=absolute_block,
            reasons=tuple(reasons),
            breaches=all_breaches,
            lot_caps=lot_caps,
            metrics=metrics,
        )

        updated = replace(
            rolled,
            last_equity_rub=equity,
            last_cash_rub=cash,
            last_snapshot_at=(snapshot_at.isoformat() if snapshot_at else None),
            last_evaluated_at=now.isoformat(),
        )
        # A deterministic policy/safety decision is not a runtime failure.
        # Actual loading, calculation and persistence failures are emitted by
        # RiskRuntimeAdapter as the separate RISK_RUNTIME_ERROR event.
        severity = (
            "WARNING"
            if status in {"BLOCKED", "ADJUSTED", "HALTED", "REDUCTION_ALLOWED"}
            else "INFO"
        )
        block_kind = (
            "SAFETY_GATE"
            if status == "BLOCKED" and absolute_block
            else "POLICY_LIMIT"
            if status == "BLOCKED"
            else None
        )
        events.append(
            _make_event(
                "RISK_EVALUATED",
                severity,
                status=status,
                block_kind=block_kind,
                expected_policy_block=(status == "BLOCKED" and not absolute_block),
                requested_target_lots=requested_target,
                approved_target_lots=approved_target,
                breaches=list(all_breaches),
                policy_hash=self.policy.policy_hash,
            )
        )
        return RiskAssessment(decision, updated, tuple(events))

    def record_execution(
        self,
        state: RiskState,
        record: ExecutionRecord,
    ) -> ExecutionRegistration:
        execution_id = str(record.execution_id).strip()
        if execution_id in state.recorded_execution_ids:
            return ExecutionRegistration(
                state=state,
                events=(
                    _make_event(
                        "DUPLICATE_EXECUTION_IGNORED",
                        "WARNING",
                        execution_id=execution_id,
                        execution_source=(str(record.execution_source).strip().upper()),
                    ),
                ),
                duplicate=True,
                turnover_added_rub=0.0,
            )

        equity = _finite_or_none(record.portfolio_equity_rub)
        rolled, period_events = _roll_state_periods(
            state,
            now=record.executed_at,
            equity_rub=equity,
        )
        turnover = (
            abs(int(record.signed_lots))
            * float(record.price_rub)
            * int(record.lot_size)
        )
        ids = (*rolled.recorded_execution_ids, execution_id)
        if len(ids) > _MAX_RECORDED_EXECUTION_IDS:
            ids = ids[-_MAX_RECORDED_EXECUTION_IDS:]
        updated = replace(
            rolled,
            daily_turnover_rub=rolled.daily_turnover_rub + turnover,
            daily_order_count=rolled.daily_order_count + 1,
            last_execution_at=_utc(record.executed_at).isoformat(),
            last_equity_rub=equity if equity is not None else rolled.last_equity_rub,
            recorded_execution_ids=tuple(ids),
        )
        events = [*period_events]
        events.append(
            _make_event(
                "EXECUTION_RECORDED",
                execution_id=execution_id,
                signed_lots=int(record.signed_lots),
                turnover_added_rub=turnover,
                daily_turnover_rub=updated.daily_turnover_rub,
                daily_order_count=updated.daily_order_count,
                execution_source=str(record.execution_source).strip().upper(),
            )
        )
        return ExecutionRegistration(
            state=updated,
            events=tuple(events),
            duplicate=False,
            turnover_added_rub=turnover,
        )
