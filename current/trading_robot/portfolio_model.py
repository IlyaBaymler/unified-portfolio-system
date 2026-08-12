from __future__ import annotations

"""Canonical, broker-agnostic portfolio domain model for v3.7-alpha3.

The classes in this module deliberately do not depend on Tkinter, T-Invest
payloads, or the execution engine.  They form the immutable boundary shared by
Portfolio Manager, GUI projections, Risk Engine integration and exports.
"""

from dataclasses import dataclass, field
import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence


LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION = 1
PORTFOLIO_STATE_SCHEMA_VERSION = 2


class PortfolioModelError(ValueError):
    """Raised when a portfolio document violates the canonical schema."""




class PortfolioMigrationStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class CompatibilityShadowStatus(StrEnum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    # Source-compatible alias for alpha3 callers. Persisted beta1 documents use
    # DISABLED, while the loader below still accepts legacy NOT_CONFIGURED.
    NOT_CONFIGURED = "DISABLED"


def _compatibility_shadow_status(value: Any) -> CompatibilityShadowStatus:
    token = str(value or "DISABLED").upper()
    if token == "NOT_CONFIGURED":
        token = "DISABLED"
    return _enum(CompatibilityShadowStatus, token)


@dataclass(frozen=True, slots=True)
class PortfolioMigrationMetadata:
    status: PortfolioMigrationStatus = PortfolioMigrationStatus.COMPLETED
    source_schema: int = PORTFOLIO_STATE_SCHEMA_VERSION
    target_schema: int = PORTFOLIO_STATE_SCHEMA_VERSION
    migration_id: str | None = None
    migrated_at: str | None = None
    legacy_read_path_enabled: bool = False
    compatibility_shadow_status: CompatibilityShadowStatus = (
        CompatibilityShadowStatus.DISABLED
    )
    detail: str = "Canonical schema is active."

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(PortfolioMigrationStatus, self.status))
        object.__setattr__(self, "source_schema", int(self.source_schema))
        object.__setattr__(self, "target_schema", int(self.target_schema))
        object.__setattr__(
            self,
            "compatibility_shadow_status",
            _compatibility_shadow_status(self.compatibility_shadow_status),
        )
        if self.migrated_at:
            _parse_timestamp(self.migrated_at, "migrated_at")

    @classmethod
    def completed(
        cls,
        *,
        source_schema: int = PORTFOLIO_STATE_SCHEMA_VERSION,
        migration_id: str | None = None,
        migrated_at: str | None = None,
        shadow_status: CompatibilityShadowStatus = CompatibilityShadowStatus.DISABLED,
        detail: str = "Canonical schema is active.",
    ) -> "PortfolioMigrationMetadata":
        return cls(
            status=PortfolioMigrationStatus.COMPLETED,
            source_schema=source_schema,
            target_schema=PORTFOLIO_STATE_SCHEMA_VERSION,
            migration_id=migration_id,
            migrated_at=migrated_at,
            legacy_read_path_enabled=False,
            compatibility_shadow_status=shadow_status,
            detail=detail,
        )

    @classmethod
    def pending_from_v1(cls) -> "PortfolioMigrationMetadata":
        return cls(
            status=PortfolioMigrationStatus.PENDING,
            source_schema=LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION,
            target_schema=PORTFOLIO_STATE_SCHEMA_VERSION,
            legacy_read_path_enabled=True,
            detail="Schema 1 document requires canonical cutover.",
        )

    @property
    def complete(self) -> bool:
        return (
            self.status is PortfolioMigrationStatus.COMPLETED
            and not self.legacy_read_path_enabled
            and self.target_schema == PORTFOLIO_STATE_SCHEMA_VERSION
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "source_schema": self.source_schema,
            "target_schema": self.target_schema,
            "migration_id": self.migration_id,
            "migrated_at": self.migrated_at,
            "legacy_read_path_enabled": self.legacy_read_path_enabled,
            "compatibility_shadow_status": self.compatibility_shadow_status.value,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PortfolioMigrationMetadata":
        return cls(
            status=_enum(PortfolioMigrationStatus, raw.get("status") or "COMPLETED"),
            source_schema=_integer(
                raw.get("source_schema"), default=PORTFOLIO_STATE_SCHEMA_VERSION
            ),
            target_schema=_integer(
                raw.get("target_schema"), default=PORTFOLIO_STATE_SCHEMA_VERSION
            ),
            migration_id=_optional_text(raw.get("migration_id")),
            migrated_at=_optional_text(raw.get("migrated_at")),
            legacy_read_path_enabled=bool(
                raw.get("legacy_read_path_enabled", False)
            ),
            compatibility_shadow_status=_compatibility_shadow_status(
                raw.get("compatibility_shadow_status")
            ),
            detail=str(raw.get("detail") or ""),
        )


class SnapshotFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class OwnershipStatus(StrEnum):
    ATTRIBUTED = "ATTRIBUTED"
    UNATTRIBUTED = "UNATTRIBUTED"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"


class PositionOrigin(StrEnum):
    STRATEGY = "STRATEGY"
    DIAGNOSTIC = "DIAGNOSTIC"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


class PendingOrderStatus(StrEnum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class ReconciliationStatus(StrEnum):
    MATCHED = "MATCHED"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    OWNERSHIP_MISSING = "OWNERSHIP_MISSING"
    UNATTRIBUTED_OPEN_POSITION = "UNATTRIBUTED_OPEN_POSITION"
    PENDING_ORDER = "PENDING_ORDER"
    PENDING_ORDER_UNCERTAIN = "PENDING_ORDER_UNCERTAIN"
    PARTIAL_FILL = "PARTIAL_FILL"
    BROKER_SNAPSHOT_STALE = "BROKER_SNAPSHOT_STALE"
    EXTERNAL_ACTIVITY_DETECTED = "EXTERNAL_ACTIVITY_DETECTED"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

    @property
    def blocking(self) -> bool:
        return self is not ReconciliationStatus.MATCHED


@dataclass(frozen=True, slots=True)
class CashBalance:
    currency: str
    available: float
    blocked: float = 0.0

    @property
    def total(self) -> float:
        return float(self.available) + float(self.blocked)

    def __post_init__(self) -> None:
        currency = str(self.currency).strip().lower()
        if not currency:
            raise PortfolioModelError("CashBalance.currency must not be empty.")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "available", float(self.available))
        object.__setattr__(self, "blocked", float(self.blocked))

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "available": self.available,
            "blocked": self.blocked,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CashBalance":
        return cls(
            currency=_required_text(raw, "currency"),
            available=_number(raw.get("available"), default=0.0),
            blocked=_number(raw.get("blocked"), default=0.0),
        )


@dataclass(frozen=True, slots=True)
class PositionOwnership:
    strategy_id: str
    config_hash: str
    candle_interval: str
    source: str = "RUNTIME"
    attributed_at: str | None = None

    def __post_init__(self) -> None:
        strategy_id = str(self.strategy_id).strip()
        config_hash = str(self.config_hash).strip()
        candle_interval = str(self.candle_interval).strip()
        if not strategy_id or not config_hash or not candle_interval:
            raise PortfolioModelError(
                "PositionOwnership requires strategy_id, config_hash and candle_interval."
            )
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "candle_interval", candle_interval)
        object.__setattr__(self, "source", str(self.source or "RUNTIME").strip().upper())
        if self.attributed_at:
            _parse_timestamp(self.attributed_at, "attributed_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "config_hash": self.config_hash,
            "candle_interval": self.candle_interval,
            "source": self.source,
            "attributed_at": self.attributed_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PositionOwnership":
        return cls(
            strategy_id=_required_text(raw, "strategy_id"),
            config_hash=_required_text(raw, "config_hash"),
            candle_interval=_required_text(raw, "candle_interval"),
            source=str(raw.get("source") or "RUNTIME"),
            attributed_at=_optional_text(raw.get("attributed_at")),
        )


@dataclass(frozen=True, slots=True)
class PortfolioTarget:
    instrument_id: str
    target_lots: int
    strategy_id: str | None = None
    config_hash: str | None = None
    candle_time: str | None = None

    def __post_init__(self) -> None:
        instrument_id = str(self.instrument_id).strip()
        if not instrument_id:
            raise PortfolioModelError("PortfolioTarget.instrument_id must not be empty.")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "target_lots", int(self.target_lots))
        if self.candle_time:
            _parse_timestamp(self.candle_time, "candle_time")

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "target_lots": self.target_lots,
            "strategy_id": self.strategy_id,
            "config_hash": self.config_hash,
            "candle_time": self.candle_time,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PortfolioTarget":
        return cls(
            instrument_id=_required_text(raw, "instrument_id"),
            target_lots=_integer(raw.get("target_lots"), default=0),
            strategy_id=_optional_text(raw.get("strategy_id")),
            config_hash=_optional_text(raw.get("config_hash")),
            candle_time=_optional_text(raw.get("candle_time")),
        )


@dataclass(frozen=True, slots=True)
class PendingOrderState:
    order_request_id: str
    instrument_id: str
    direction: str
    requested_lots: int
    executed_lots: int = 0
    status: PendingOrderStatus = PendingOrderStatus.UNKNOWN
    broker_order_id: str | None = None
    uncertain: bool = False
    source: str = "LOCAL"

    def __post_init__(self) -> None:
        order_request_id = str(self.order_request_id).strip()
        instrument_id = str(self.instrument_id).strip()
        if not order_request_id or not instrument_id:
            raise PortfolioModelError(
                "PendingOrderState requires order_request_id and instrument_id."
            )
        requested_lots = int(self.requested_lots)
        executed_lots = int(self.executed_lots)
        if requested_lots < 0 or executed_lots < 0 or executed_lots > requested_lots:
            raise PortfolioModelError("Invalid pending-order lot counters.")
        object.__setattr__(self, "order_request_id", order_request_id)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "direction", str(self.direction or "UNKNOWN").strip().upper())
        object.__setattr__(self, "requested_lots", requested_lots)
        object.__setattr__(self, "executed_lots", executed_lots)
        object.__setattr__(self, "status", _enum(PendingOrderStatus, self.status))
        object.__setattr__(self, "source", str(self.source or "LOCAL").strip().upper())

    @property
    def partial_fill(self) -> bool:
        return 0 < self.executed_lots < self.requested_lots

    @property
    def active(self) -> bool:
        return self.status not in {
            PendingOrderStatus.FILLED,
            PendingOrderStatus.CANCELLED,
            PendingOrderStatus.REJECTED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_request_id": self.order_request_id,
            "broker_order_id": self.broker_order_id,
            "instrument_id": self.instrument_id,
            "direction": self.direction,
            "requested_lots": self.requested_lots,
            "executed_lots": self.executed_lots,
            "status": str(self.status),
            "uncertain": bool(self.uncertain),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PendingOrderState":
        return cls(
            order_request_id=_required_text(raw, "order_request_id"),
            broker_order_id=_optional_text(raw.get("broker_order_id")),
            instrument_id=_required_text(raw, "instrument_id"),
            direction=str(raw.get("direction") or "UNKNOWN"),
            requested_lots=_integer(raw.get("requested_lots"), default=0),
            executed_lots=_integer(raw.get("executed_lots"), default=0),
            status=_enum(PendingOrderStatus, raw.get("status")),
            uncertain=bool(raw.get("uncertain", False)),
            source=str(raw.get("source") or "LOCAL"),
        )


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    instrument_id: str
    status: ReconciliationStatus
    blocking: bool
    reasons: tuple[str, ...]
    actual_lots: int
    target_lots: int | None
    pending_order_ids: tuple[str, ...] = ()
    checked_at: str | None = None

    def __post_init__(self) -> None:
        instrument_id = str(self.instrument_id).strip()
        if not instrument_id:
            raise PortfolioModelError(
                "ReconciliationResult.instrument_id must not be empty."
            )
        status = _enum(ReconciliationStatus, self.status)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "blocking", bool(self.blocking or status.blocking))
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "actual_lots", int(self.actual_lots))
        object.__setattr__(
            self,
            "target_lots",
            None if self.target_lots is None else int(self.target_lots),
        )
        object.__setattr__(
            self,
            "pending_order_ids",
            tuple(str(item) for item in self.pending_order_ids if str(item)),
        )
        if self.checked_at:
            _parse_timestamp(self.checked_at, "checked_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "status": str(self.status),
            "blocking": self.blocking,
            "reasons": list(self.reasons),
            "actual_lots": self.actual_lots,
            "target_lots": self.target_lots,
            "pending_order_ids": list(self.pending_order_ids),
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReconciliationResult":
        return cls(
            instrument_id=_required_text(raw, "instrument_id"),
            status=_enum(ReconciliationStatus, raw.get("status")),
            blocking=bool(raw.get("blocking", False)),
            reasons=_text_tuple(raw.get("reasons")),
            actual_lots=_integer(raw.get("actual_lots"), default=0),
            target_lots=_optional_integer(raw.get("target_lots")),
            pending_order_ids=_text_tuple(raw.get("pending_order_ids")),
            checked_at=_optional_text(raw.get("checked_at")),
        )


@dataclass(frozen=True, slots=True)
class PositionState:
    instrument_id: str
    figi: str
    ticker: str
    class_code: str
    asset_type: str
    currency: str
    quantity: float
    actual_lots: int
    average_price: float | None
    current_price: float | None
    market_value: float | None
    expected_yield: float | None
    target: PortfolioTarget | None
    ownership: PositionOwnership | None
    ownership_status: OwnershipStatus
    pending_orders: tuple[PendingOrderState, ...]
    reconciliation: ReconciliationResult
    origin: PositionOrigin = PositionOrigin.UNKNOWN
    last_candle_time: str | None = None

    def __post_init__(self) -> None:
        instrument_id = str(self.instrument_id).strip()
        if not instrument_id:
            raise PortfolioModelError("PositionState.instrument_id must not be empty.")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "figi", str(self.figi or "").strip())
        object.__setattr__(self, "ticker", str(self.ticker or instrument_id[:12]).strip())
        object.__setattr__(self, "class_code", str(self.class_code or "").strip())
        object.__setattr__(self, "asset_type", str(self.asset_type or "unknown").lower())
        object.__setattr__(self, "currency", str(self.currency or "").lower())
        object.__setattr__(self, "quantity", float(self.quantity))
        object.__setattr__(self, "actual_lots", int(self.actual_lots))
        object.__setattr__(self, "ownership_status", _enum(OwnershipStatus, self.ownership_status))
        object.__setattr__(self, "origin", _enum(PositionOrigin, self.origin))
        object.__setattr__(self, "pending_orders", tuple(self.pending_orders))
        if self.reconciliation.instrument_id != instrument_id:
            raise PortfolioModelError("Position and reconciliation instrument IDs differ.")
        if self.target is not None and self.target.instrument_id != instrument_id:
            raise PortfolioModelError("Position and target instrument IDs differ.")
        if self.last_candle_time:
            _parse_timestamp(self.last_candle_time, "last_candle_time")

    @property
    def target_lots(self) -> int | None:
        return self.target.target_lots if self.target is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "figi": self.figi,
            "ticker": self.ticker,
            "class_code": self.class_code,
            "asset_type": self.asset_type,
            "currency": self.currency,
            "quantity": self.quantity,
            "actual_lots": self.actual_lots,
            "average_price": self.average_price,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "expected_yield": self.expected_yield,
            "target": self.target.to_dict() if self.target else None,
            "ownership": self.ownership.to_dict() if self.ownership else None,
            "ownership_status": str(self.ownership_status),
            "pending_orders": [item.to_dict() for item in self.pending_orders],
            "reconciliation": self.reconciliation.to_dict(),
            "origin": str(self.origin),
            "last_candle_time": self.last_candle_time,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PositionState":
        target_raw = raw.get("target")
        owner_raw = raw.get("ownership")
        reconciliation_raw = _mapping(raw.get("reconciliation"), "reconciliation")
        return cls(
            instrument_id=_required_text(raw, "instrument_id"),
            figi=str(raw.get("figi") or ""),
            ticker=str(raw.get("ticker") or ""),
            class_code=str(raw.get("class_code") or ""),
            asset_type=str(raw.get("asset_type") or "unknown"),
            currency=str(raw.get("currency") or ""),
            quantity=_number(raw.get("quantity"), default=0.0),
            actual_lots=_integer(raw.get("actual_lots"), default=0),
            average_price=_optional_number(raw.get("average_price")),
            current_price=_optional_number(raw.get("current_price")),
            market_value=_optional_number(raw.get("market_value")),
            expected_yield=_optional_number(raw.get("expected_yield")),
            target=(
                PortfolioTarget.from_dict(_mapping(target_raw, "target"))
                if target_raw is not None
                else None
            ),
            ownership=(
                PositionOwnership.from_dict(_mapping(owner_raw, "ownership"))
                if owner_raw is not None
                else None
            ),
            ownership_status=_enum(OwnershipStatus, raw.get("ownership_status")),
            pending_orders=tuple(
                PendingOrderState.from_dict(_mapping(item, "pending_order"))
                for item in _sequence(raw.get("pending_orders"), "pending_orders")
            ),
            reconciliation=ReconciliationResult.from_dict(reconciliation_raw),
            origin=_enum(PositionOrigin, raw.get("origin") or PositionOrigin.UNKNOWN),
            last_candle_time=_optional_text(raw.get("last_candle_time")),
        )


@dataclass(frozen=True, slots=True)
class AccountState:
    account_id: str
    total_value: float | None
    securities_value: float | None
    expected_yield: float | None
    cash_balances: tuple[CashBalance, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", str(self.account_id or "").strip())
        object.__setattr__(self, "cash_balances", tuple(self.cash_balances))
        currencies = [item.currency for item in self.cash_balances]
        if len(currencies) != len(set(currencies)):
            raise PortfolioModelError("Duplicate cash currency in AccountState.")

    def cash(self, currency: str) -> CashBalance | None:
        normalized = str(currency).lower()
        return next((item for item in self.cash_balances if item.currency == normalized), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "total_value": self.total_value,
            "securities_value": self.securities_value,
            "expected_yield": self.expected_yield,
            "cash_balances": [item.to_dict() for item in self.cash_balances],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AccountState":
        return cls(
            account_id=str(raw.get("account_id") or ""),
            total_value=_optional_number(raw.get("total_value")),
            securities_value=_optional_number(raw.get("securities_value")),
            expected_yield=_optional_number(raw.get("expected_yield")),
            cash_balances=tuple(
                CashBalance.from_dict(_mapping(item, "cash_balance"))
                for item in _sequence(raw.get("cash_balances"), "cash_balances")
            ),
        )


@dataclass(frozen=True, slots=True)
class PortfolioState:
    version: int
    account: AccountState
    snapshot_at: str
    generated_at: str
    freshness: SnapshotFreshness
    source: str
    positions: tuple[PositionState, ...]
    warnings: tuple[str, ...]
    state_status: str
    blocking: bool
    revision: int = 0
    portfolio_source: str = "CANONICAL"
    migration: PortfolioMigrationMetadata = field(
        default_factory=PortfolioMigrationMetadata.completed
    )
    last_transaction_id: str | None = None
    last_transaction_status: str = "NONE"

    def __post_init__(self) -> None:
        raw_version = int(self.version)
        if raw_version not in {
            LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION,
            PORTFOLIO_STATE_SCHEMA_VERSION,
        }:
            raise PortfolioModelError(
                f"Unsupported portfolio schema {self.version!r}; expected "
                f"{LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION} or "
                f"{PORTFOLIO_STATE_SCHEMA_VERSION}."
            )
        # Direct construction with schema 1 is retained for test/client
        # compatibility and normalized in-memory to schema 2. Raw schema 1
        # documents loaded through from_dict receive PENDING migration metadata.
        object.__setattr__(self, "version", PORTFOLIO_STATE_SCHEMA_VERSION)
        revision = int(self.revision)
        if revision < 0:
            raise PortfolioModelError("PortfolioState.revision must not be negative.")
        object.__setattr__(self, "revision", revision)
        _parse_timestamp(self.snapshot_at, "snapshot_at")
        _parse_timestamp(self.generated_at, "generated_at")
        object.__setattr__(self, "freshness", _enum(SnapshotFreshness, self.freshness))
        object.__setattr__(self, "source", str(self.source or "UNKNOWN").strip().upper())
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))
        object.__setattr__(self, "state_status", str(self.state_status or "UNKNOWN").strip().upper())
        object.__setattr__(
            self, "portfolio_source", str(self.portfolio_source or "CANONICAL").strip().upper()
        )
        if not isinstance(self.migration, PortfolioMigrationMetadata):
            object.__setattr__(
                self,
                "migration",
                PortfolioMigrationMetadata.from_dict(
                    _mapping(self.migration, "migration")
                ),
            )
        object.__setattr__(
            self,
            "last_transaction_status",
            str(self.last_transaction_status or "NONE").strip().upper(),
        )
        position_ids = [item.instrument_id for item in self.positions]
        if len(position_ids) != len(set(position_ids)):
            raise PortfolioModelError("Duplicate instrument_id in PortfolioState.")
        inferred_blocking = any(item.reconciliation.blocking for item in self.positions)
        object.__setattr__(self, "blocking", bool(self.blocking or inferred_blocking))

    @property
    def account_id(self) -> str:
        return self.account.account_id

    @property
    def reconciliation_results(self) -> tuple[ReconciliationResult, ...]:
        return tuple(item.reconciliation for item in self.positions)

    def position(self, instrument_id: str) -> PositionState | None:
        token = str(instrument_id)
        return next((item for item in self.positions if item.instrument_id == token), None)

    def decision_material(self) -> dict[str, Any]:
        """Return fields that may change order authorization.

        Capture timestamps, display warnings, current market price and expected
        yield are deliberately excluded.  This keeps the revision stable when a
        GUI refresh observes the same economic position/ownership/pending state.
        """

        return {
            "version": self.version,
            "portfolio_source": self.portfolio_source,
            "migration_status": self.migration.status.value,
            "legacy_read_path_enabled": self.migration.legacy_read_path_enabled,
            "account_id": self.account.account_id,
            "cash_balances": [
                {
                    "currency": item.currency,
                    "available": item.available,
                    "blocked": item.blocked,
                }
                for item in self.account.cash_balances
            ],
            "freshness": str(self.freshness),
            "state_status": self.state_status,
            "blocking": self.blocking,
            "positions": [
                {
                    "instrument_id": item.instrument_id,
                    "actual_lots": item.actual_lots,
                    "target_lots": item.target_lots,
                    "ownership": item.ownership.to_dict() if item.ownership else None,
                    "ownership_status": str(item.ownership_status),
                    "origin": str(item.origin),
                    "pending_orders": [
                        {
                            "order_request_id": order.order_request_id,
                            "status": str(order.status),
                            "requested_lots": order.requested_lots,
                            "executed_lots": order.executed_lots,
                            "uncertain": order.uncertain,
                        }
                        for order in item.pending_orders
                    ],
                    "reconciliation": {
                        "status": str(item.reconciliation.status),
                        "blocking": item.reconciliation.blocking,
                        "actual_lots": item.reconciliation.actual_lots,
                        "target_lots": item.reconciliation.target_lots,
                    },
                }
                for item in self.positions
            ],
        }

    @property
    def decision_sha256(self) -> str:
        payload = json.dumps(
            self.decision_material(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "account": self.account.to_dict(),
            "snapshot_at": self.snapshot_at,
            "generated_at": self.generated_at,
            "freshness": str(self.freshness),
            "source": self.source,
            "positions": [item.to_dict() for item in self.positions],
            "warnings": list(self.warnings),
            "state_status": self.state_status,
            "blocking": self.blocking,
            "revision": self.revision,
            "portfolio_source": self.portfolio_source,
            "migration": self.migration.to_dict(),
            "last_transaction_id": self.last_transaction_id,
            "last_transaction_status": self.last_transaction_status,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PortfolioState":
        raw_version = _integer(raw.get("version"), default=-1)
        if raw_version not in {
            LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION,
            PORTFOLIO_STATE_SCHEMA_VERSION,
        }:
            raise PortfolioModelError(
                f"Unsupported portfolio schema {raw_version!r}; expected "
                f"{LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION} or "
                f"{PORTFOLIO_STATE_SCHEMA_VERSION}."
            )
        account = AccountState.from_dict(_mapping(raw.get("account"), "account"))
        migration_raw = raw.get("migration")
        if raw_version == LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION:
            migration = PortfolioMigrationMetadata.pending_from_v1()
            portfolio_source = "MIGRATION_PENDING"
        else:
            migration = (
                PortfolioMigrationMetadata.from_dict(
                    _mapping(migration_raw, "migration")
                )
                if migration_raw is not None
                else PortfolioMigrationMetadata.completed()
            )
            portfolio_source = str(raw.get("portfolio_source") or "CANONICAL")
        return cls(
            version=PORTFOLIO_STATE_SCHEMA_VERSION,
            account=account,
            snapshot_at=_required_text(raw, "snapshot_at"),
            generated_at=_required_text(raw, "generated_at"),
            freshness=_enum(SnapshotFreshness, raw.get("freshness")),
            source=str(raw.get("source") or "UNKNOWN"),
            positions=tuple(
                PositionState.from_dict(_mapping(item, "position"))
                for item in _sequence(raw.get("positions"), "positions")
            ),
            warnings=_text_tuple(raw.get("warnings")),
            state_status=str(raw.get("state_status") or "UNKNOWN"),
            blocking=bool(raw.get("blocking", False)),
            revision=_integer(raw.get("revision"), default=0),
            portfolio_source=portfolio_source,
            migration=migration,
            last_transaction_id=_optional_text(raw.get("last_transaction_id")),
            last_transaction_status=str(
                raw.get("last_transaction_status") or "NONE"
            ),
        )

    @classmethod
    def empty(cls, *, account_id: str = "", now: str | None = None) -> "PortfolioState":
        timestamp = now or datetime.now(timezone.utc).isoformat()
        return cls(
            version=PORTFOLIO_STATE_SCHEMA_VERSION,
            account=AccountState(
                account_id=account_id,
                total_value=None,
                securities_value=None,
                expected_yield=None,
                cash_balances=(),
            ),
            snapshot_at=timestamp,
            generated_at=timestamp,
            freshness=SnapshotFreshness.UNAVAILABLE,
            source="BOOTSTRAP",
            positions=(),
            warnings=("Portfolio snapshot has not been collected yet.",),
            state_status="EMPTY",
            blocking=False,
            revision=0,
            portfolio_source="CANONICAL",
            migration=PortfolioMigrationMetadata.completed(),
            last_transaction_id=None,
            last_transaction_status="NONE",
        )

    def with_freshness(
        self,
        freshness: SnapshotFreshness,
        *,
        warning: str | None = None,
        generated_at: str | None = None,
    ) -> "PortfolioState":
        warnings = self.warnings + ((warning,) if warning else ())
        return PortfolioState(
            version=self.version,
            account=self.account,
            snapshot_at=self.snapshot_at,
            generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
            freshness=freshness,
            source=self.source,
            positions=self.positions,
            warnings=warnings,
            state_status="BLOCKED" if freshness is not SnapshotFreshness.FRESH else self.state_status,
            blocking=(self.blocking or freshness is not SnapshotFreshness.FRESH),
            revision=self.revision,
            portfolio_source=self.portfolio_source,
            migration=self.migration,
            last_transaction_id=self.last_transaction_id,
            last_transaction_status=self.last_transaction_status,
        )


def validate_portfolio_document(raw: Mapping[str, Any]) -> None:
    PortfolioState.from_dict(raw)


def _enum(enum_type: type[StrEnum], value: Any) -> Any:
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise PortfolioModelError(
            f"Invalid {enum_type.__name__} value {value!r}; expected one of {allowed}."
        ) from exc


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PortfolioModelError(f"{name} must be an object.")
    return value


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PortfolioModelError(f"{name} must be an array.")
    return value


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise PortfolioModelError(f"{key} must not be empty.")
    return value


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _text_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value, "text array"))


def _integer(value: Any, *, default: int) -> int:
    if value is None:
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioModelError(f"Expected integer, got {value!r}.") from exc


def _optional_integer(value: Any) -> int | None:
    if value is None:
        return None
    return _integer(value, default=0)


def _number(value: Any, *, default: float) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioModelError(f"Expected number, got {value!r}.") from exc


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return _number(value, default=0.0)


def _parse_timestamp(value: str, name: str) -> datetime:
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PortfolioModelError(f"{name} must be ISO-8601, got {value!r}.") from exc
    if parsed.tzinfo is None:
        raise PortfolioModelError(f"{name} must include a timezone.")
    return parsed
