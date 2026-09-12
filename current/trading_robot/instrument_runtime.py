from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .locking import InterProcessFileLock, LockUnavailableError
from .state_persistence import (
    StatePersistenceError,
    StateSaveResult,
    atomic_write_json,
    read_json_verified,
)

INSTRUMENT_RUNTIME_SCHEMA_VERSION = 1
RUNTIME_STATUSES = frozenset({"STOPPED", "ACTIVE", "BLOCKED"})


class InstrumentRuntimeError(RuntimeError):
    """Base error for v3.8 per-instrument runtime state."""


class InstrumentRuntimeConflictError(InstrumentRuntimeError):
    """Raised when a runtime identity or lifecycle transition is unsafe."""


class InstrumentRuntimeStateError(InstrumentRuntimeError):
    """Raised when persisted runtime state is invalid or cannot be read."""


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InstrumentRuntimeError(f"{field} must not be empty.")
    return normalized


def _positive_seconds(value: Any, field: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise InstrumentRuntimeError(f"{field} must be an integer.") from exc
    if normalized < 1:
        raise InstrumentRuntimeError(f"{field} must be positive.")
    return normalized


def _parse_timestamp(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstrumentRuntimeStateError(
            f"{field} must be an ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InstrumentRuntimeStateError(f"{field} must be timezone-aware.")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise InstrumentRuntimeStateError("Runtime timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class InstrumentRuntimeConfig:
    """Versioned v3.8 identity and independent service cadences.

    ``candle_interval`` belongs to one instrument runtime. Scheduler, Risk,
    reconciliation and market-status cadences are deliberately separate and do
    not participate in the strategy/runtime identity hash.
    """

    account_id: str
    instrument_id: str
    ticker: str
    class_code: str
    candle_interval: str
    strategy_id: str
    strategy_config_hash: str
    configuration_version: int = 1
    decision_cadence_seconds: int = 60
    scheduler_cadence_seconds: int = 15
    risk_refresh_cadence_seconds: int = 60
    reconciliation_cadence_seconds: int = 300
    market_status_cadence_seconds: int = 60

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "account_id", _required_text(self.account_id, "account_id")
        )
        object.__setattr__(
            self,
            "instrument_id",
            _required_text(self.instrument_id, "instrument_id"),
        )
        object.__setattr__(
            self, "ticker", _required_text(self.ticker, "ticker").upper()
        )
        object.__setattr__(
            self,
            "class_code",
            _required_text(self.class_code, "class_code").upper(),
        )
        interval = _required_text(self.candle_interval, "candle_interval").upper()
        if not interval.startswith("CANDLE_INTERVAL_"):
            raise InstrumentRuntimeError(
                "candle_interval must use a CANDLE_INTERVAL_* broker constant."
            )
        object.__setattr__(self, "candle_interval", interval)
        object.__setattr__(
            self,
            "strategy_id",
            _required_text(self.strategy_id, "strategy_id").lower(),
        )
        config_hash = _required_text(
            self.strategy_config_hash, "strategy_config_hash"
        ).lower()
        if len(config_hash) != 64 or any(
            character not in "0123456789abcdef" for character in config_hash
        ):
            raise InstrumentRuntimeError(
                "strategy_config_hash must be a 64-character SHA-256 hex digest."
            )
        object.__setattr__(self, "strategy_config_hash", config_hash)
        version = _positive_seconds(
            self.configuration_version, "configuration_version"
        )
        object.__setattr__(self, "configuration_version", version)
        for field in (
            "decision_cadence_seconds",
            "scheduler_cadence_seconds",
            "risk_refresh_cadence_seconds",
            "reconciliation_cadence_seconds",
            "market_status_cadence_seconds",
        ):
            object.__setattr__(
                self,
                field,
                _positive_seconds(getattr(self, field), field),
            )

    @property
    def execution_scope_key(self) -> str:
        """Account/instrument scope; timeframe never creates a second owner."""

        return f"{self.account_id}|{self.instrument_id}"

    @property
    def runtime_config_hash(self) -> str:
        payload = {
            "schema_version": INSTRUMENT_RUNTIME_SCHEMA_VERSION,
            "configuration_version": self.configuration_version,
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "class_code": self.class_code,
            "candle_interval": self.candle_interval,
            "strategy_id": self.strategy_id,
            "strategy_config_hash": self.strategy_config_hash,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def runtime_key(self) -> str:
        return "|".join(
            (
                self.account_id,
                self.instrument_id,
                self.candle_interval,
                self.strategy_id,
                self.runtime_config_hash[:16],
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "class_code": self.class_code,
            "candle_interval": self.candle_interval,
            "strategy_id": self.strategy_id,
            "strategy_config_hash": self.strategy_config_hash,
            "configuration_version": self.configuration_version,
            "runtime_config_hash": self.runtime_config_hash,
            "runtime_key": self.runtime_key,
            "cadences": {
                "decision_seconds": self.decision_cadence_seconds,
                "scheduler_seconds": self.scheduler_cadence_seconds,
                "risk_refresh_seconds": self.risk_refresh_cadence_seconds,
                "reconciliation_seconds": self.reconciliation_cadence_seconds,
                "market_status_seconds": self.market_status_cadence_seconds,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> InstrumentRuntimeConfig:
        cadences = raw.get("cadences") or {}
        if not isinstance(cadences, Mapping):
            raise InstrumentRuntimeStateError("Runtime cadences must be an object.")
        config = cls(
            account_id=raw.get("account_id", ""),
            instrument_id=raw.get("instrument_id", ""),
            ticker=raw.get("ticker", ""),
            class_code=raw.get("class_code", ""),
            candle_interval=raw.get("candle_interval", ""),
            strategy_id=raw.get("strategy_id", ""),
            strategy_config_hash=raw.get("strategy_config_hash", ""),
            configuration_version=raw.get("configuration_version", 0),
            decision_cadence_seconds=cadences.get("decision_seconds", 0),
            scheduler_cadence_seconds=cadences.get("scheduler_seconds", 0),
            risk_refresh_cadence_seconds=cadences.get("risk_refresh_seconds", 0),
            reconciliation_cadence_seconds=cadences.get(
                "reconciliation_seconds", 0
            ),
            market_status_cadence_seconds=cadences.get("market_status_seconds", 0),
        )
        expected_hash = _required_text(
            raw.get("runtime_config_hash"), "runtime_config_hash"
        )
        expected_key = _required_text(raw.get("runtime_key"), "runtime_key")
        if expected_hash != config.runtime_config_hash:
            raise InstrumentRuntimeStateError(
                "Instrument runtime config hash mismatch."
            )
        if expected_key != config.runtime_key:
            raise InstrumentRuntimeStateError("Instrument runtime key mismatch.")
        return config


@dataclass(frozen=True, slots=True)
class InstrumentRuntime:
    config: InstrumentRuntimeConfig
    status: str = "STOPPED"
    last_scheduler_service_at: datetime | None = None
    last_processed_candle: datetime | None = None
    last_decision_check_at: datetime | None = None
    last_risk_refresh_at: datetime | None = None
    last_reconciliation_at: datetime | None = None
    last_market_status_at: datetime | None = None
    current_lots: int = 0
    pending_order_ids: tuple[str, ...] = ()
    revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.config, InstrumentRuntimeConfig):
            raise InstrumentRuntimeError("config must be InstrumentRuntimeConfig.")
        status = str(self.status or "").strip().upper()
        if status not in RUNTIME_STATUSES:
            raise InstrumentRuntimeStateError(
                f"Unsupported instrument runtime status: {self.status!r}."
            )
        object.__setattr__(self, "status", status)
        for field in (
            "last_scheduler_service_at",
            "last_processed_candle",
            "last_decision_check_at",
            "last_risk_refresh_at",
            "last_reconciliation_at",
            "last_market_status_at",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _parse_timestamp(value, field))
        try:
            lots = int(self.current_lots)
            revision = int(self.revision)
        except (TypeError, ValueError) as exc:
            raise InstrumentRuntimeStateError(
                "current_lots and revision must be integers."
            ) from exc
        if revision < 0:
            raise InstrumentRuntimeStateError("revision must not be negative.")
        object.__setattr__(self, "current_lots", lots)
        object.__setattr__(self, "revision", revision)
        pending: list[str] = []
        for raw_order_id in self.pending_order_ids:
            order_id = _required_text(raw_order_id, "pending_order_id")
            if order_id not in pending:
                pending.append(order_id)
        object.__setattr__(self, "pending_order_ids", tuple(pending))

    @property
    def runtime_key(self) -> str:
        return self.config.runtime_key

    @property
    def can_replace_configuration(self) -> bool:
        return (
            self.status == "STOPPED"
            and self.current_lots == 0
            and not self.pending_order_ids
        )

    def start(self) -> InstrumentRuntime:
        if self.status == "BLOCKED":
            raise InstrumentRuntimeConflictError(
                "A blocked InstrumentRuntime cannot start until it is repaired."
            )
        if self.status == "ACTIVE":
            return self
        return replace(self, status="ACTIVE", revision=self.revision + 1)

    def stop(self) -> InstrumentRuntime:
        if self.status == "STOPPED":
            return self
        return replace(self, status="STOPPED", revision=self.revision + 1)

    def block(self) -> InstrumentRuntime:
        if self.status == "BLOCKED":
            return self
        return replace(self, status="BLOCKED", revision=self.revision + 1)

    def with_execution_state(
        self,
        *,
        current_lots: int,
        pending_order_ids: Iterable[str] = (),
    ) -> InstrumentRuntime:
        return replace(
            self,
            current_lots=int(current_lots),
            pending_order_ids=tuple(pending_order_ids),
            revision=self.revision + 1,
        )

    def replace_configuration(
        self,
        config: InstrumentRuntimeConfig,
    ) -> InstrumentRuntime:
        """Explicitly create a new temporal identity while stopped and flat."""

        if not self.can_replace_configuration:
            raise InstrumentRuntimeConflictError(
                "InstrumentRuntime configuration can change only while STOPPED, "
                "flat and without pending orders."
            )
        if config.execution_scope_key != self.config.execution_scope_key:
            raise InstrumentRuntimeConflictError(
                "Configuration replacement cannot change account/instrument scope."
            )
        if config.runtime_config_hash == self.config.runtime_config_hash:
            raise InstrumentRuntimeConflictError(
                "Replacement must create a new versioned runtime identity."
            )
        return InstrumentRuntime(config=config, revision=self.revision + 1)

    def mark_decision_check(self, checked_at: datetime) -> InstrumentRuntime:
        return self._mark_timestamp("last_decision_check_at", checked_at)

    def mark_scheduler_service(self, serviced_at: datetime) -> InstrumentRuntime:
        return self._mark_timestamp("last_scheduler_service_at", serviced_at)

    def mark_candle_processed(self, candle_time: datetime) -> InstrumentRuntime:
        normalized = _parse_timestamp(candle_time, "last_processed_candle")
        assert normalized is not None
        if (
            self.last_processed_candle is not None
            and normalized <= self.last_processed_candle
        ):
            raise InstrumentRuntimeConflictError(
                "last_processed_candle must advance monotonically."
            )
        return replace(
            self,
            last_processed_candle=normalized,
            revision=self.revision + 1,
        )

    def mark_risk_refresh(self, refreshed_at: datetime) -> InstrumentRuntime:
        return self._mark_timestamp("last_risk_refresh_at", refreshed_at)

    def mark_reconciliation(self, reconciled_at: datetime) -> InstrumentRuntime:
        return self._mark_timestamp("last_reconciliation_at", reconciled_at)

    def mark_market_status(self, checked_at: datetime) -> InstrumentRuntime:
        return self._mark_timestamp("last_market_status_at", checked_at)

    def _mark_timestamp(self, field: str, value: datetime) -> InstrumentRuntime:
        normalized = _parse_timestamp(value, field)
        assert normalized is not None
        previous = getattr(self, field)
        if previous is not None and normalized < previous:
            raise InstrumentRuntimeConflictError(
                f"{field} must not move backwards."
            )
        if previous == normalized:
            return self
        return replace(self, **{field: normalized, "revision": self.revision + 1})

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "status": self.status,
            "last_scheduler_service_at": _timestamp_text(
                self.last_scheduler_service_at
            ),
            "last_processed_candle": _timestamp_text(self.last_processed_candle),
            "last_decision_check_at": _timestamp_text(self.last_decision_check_at),
            "last_risk_refresh_at": _timestamp_text(self.last_risk_refresh_at),
            "last_reconciliation_at": _timestamp_text(self.last_reconciliation_at),
            "last_market_status_at": _timestamp_text(self.last_market_status_at),
            "current_lots": self.current_lots,
            "pending_order_ids": list(self.pending_order_ids),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> InstrumentRuntime:
        config = raw.get("config")
        if not isinstance(config, Mapping):
            raise InstrumentRuntimeStateError("Runtime config must be an object.")
        pending = raw.get("pending_order_ids") or []
        if not isinstance(pending, list):
            raise InstrumentRuntimeStateError(
                "pending_order_ids must be an array."
            )
        return cls(
            config=InstrumentRuntimeConfig.from_dict(config),
            status=raw.get("status", ""),
            last_scheduler_service_at=_parse_timestamp(
                raw.get("last_scheduler_service_at"),
                "last_scheduler_service_at",
            ),
            last_processed_candle=_parse_timestamp(
                raw.get("last_processed_candle"), "last_processed_candle"
            ),
            last_decision_check_at=_parse_timestamp(
                raw.get("last_decision_check_at"), "last_decision_check_at"
            ),
            last_risk_refresh_at=_parse_timestamp(
                raw.get("last_risk_refresh_at"), "last_risk_refresh_at"
            ),
            last_reconciliation_at=_parse_timestamp(
                raw.get("last_reconciliation_at"), "last_reconciliation_at"
            ),
            last_market_status_at=_parse_timestamp(
                raw.get("last_market_status_at"), "last_market_status_at"
            ),
            current_lots=raw.get("current_lots", 0),
            pending_order_ids=tuple(str(item) for item in pending),
            revision=raw.get("revision", -1),
        )


class InstrumentRuntimeStore:
    """Checksum-managed account-wide persistence for v3.8 runtime state."""

    SCHEMA_VERSION = INSTRUMENT_RUNTIME_SCHEMA_VERSION

    def __init__(
        self,
        path: str | Path,
        *,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.last_good_path = self.path.with_name(self.path.name + ".lastgood")
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))

    def save(self, runtimes: Iterable[InstrumentRuntime]) -> StateSaveResult:
        normalized = self._validate_collection(tuple(runtimes))
        document = self._document(normalized)
        try:
            with InterProcessFileLock(
                self.lock_path, timeout_seconds=self.lock_timeout_seconds
            ):
                return self._write_unlocked(document)
        except (LockUnavailableError, StatePersistenceError) as exc:
            raise InstrumentRuntimeStateError(str(exc)) from exc

    def compare_and_swap_all(
        self,
        *,
        expected: Iterable[InstrumentRuntime],
        successor: Iterable[InstrumentRuntime],
        expected_account_id: str,
    ) -> tuple[InstrumentRuntime, ...]:
        """Atomically replace the complete registry when its exact value matches.

        One file lock covers verified load, full-document comparison, write and
        verified read-back.  This is the account-level lifecycle boundary used
        by the v3.10 GUI; it never attempts a partial runtime transition.
        """

        account_id = _required_text(expected_account_id, "expected_account_id")
        normalized_expected = self._validate_collection(tuple(expected))
        normalized_successor = self._validate_collection(tuple(successor))
        for label, values in (
            ("expected", normalized_expected),
            ("successor", normalized_successor),
        ):
            actual_account = values[0].config.account_id if values else None
            if actual_account != account_id:
                raise InstrumentRuntimeConflictError(
                    f"GROUP_ACCOUNT_SCOPE_MISMATCH: {label} registry"
                )

        expected_document = self._document(normalized_expected)
        successor_document = self._document(normalized_successor)
        try:
            with InterProcessFileLock(
                self.lock_path, timeout_seconds=self.lock_timeout_seconds
            ):
                current = self._load_unlocked(expected_account_id=account_id)
                current_document = self._document(current)
                if self._canonical_document_bytes(current_document) != (
                    self._canonical_document_bytes(expected_document)
                ):
                    raise InstrumentRuntimeConflictError("GROUP_CAS_MISMATCH")
                try:
                    self._write_unlocked(successor_document)
                except StatePersistenceError as exc:
                    raise InstrumentRuntimeStateError(
                        f"GROUP_COMMIT_FAILED: {exc}"
                    ) from exc
                try:
                    committed = self._load_unlocked(expected_account_id=account_id)
                except InstrumentRuntimeError as exc:
                    raise InstrumentRuntimeStateError(
                        f"GROUP_POSTCONDITION_FAILED: {exc}"
                    ) from exc
                if self._canonical_document_bytes(self._document(committed)) != (
                    self._canonical_document_bytes(successor_document)
                ):
                    raise InstrumentRuntimeStateError(
                        "GROUP_POSTCONDITION_FAILED"
                    )
                return committed
        except InstrumentRuntimeError:
            raise
        except LockUnavailableError as exc:
            raise InstrumentRuntimeStateError(
                f"GROUP_COMMIT_FAILED: {exc}"
            ) from exc

    def load(
        self,
        *,
        expected_account_id: str | None = None,
    ) -> tuple[InstrumentRuntime, ...]:
        if not self.path.exists():
            raise InstrumentRuntimeStateError(
                f"Instrument runtime state is missing: {self.path}"
            )
        return self._load_unlocked(expected_account_id=expected_account_id)

    def load_optional(
        self,
        *,
        expected_account_id: str | None = None,
    ) -> tuple[InstrumentRuntime, ...]:
        if not self.path.exists():
            return ()
        return self.load(expected_account_id=expected_account_id)

    def load_last_good(
        self,
        *,
        expected_account_id: str | None = None,
    ) -> tuple[InstrumentRuntime, ...]:
        store = InstrumentRuntimeStore(
            self.last_good_path,
            lock_timeout_seconds=self.lock_timeout_seconds,
        )
        return store.load(expected_account_id=expected_account_id)

    def _load_document(
        self,
        document: Mapping[str, Any],
        *,
        expected_account_id: str | None,
    ) -> tuple[InstrumentRuntime, ...]:
        raw_runtimes = document.get("runtimes")
        if not isinstance(raw_runtimes, list):
            raise InstrumentRuntimeStateError("runtimes must be an array.")
        runtimes: list[InstrumentRuntime] = []
        for raw in raw_runtimes:
            if not isinstance(raw, Mapping):
                raise InstrumentRuntimeStateError(
                    "Each instrument runtime must be an object."
                )
            runtimes.append(InstrumentRuntime.from_dict(raw))
        normalized = self._validate_collection(tuple(runtimes))
        document_account = document.get("account_id")
        actual_account = normalized[0].config.account_id if normalized else None
        if document_account != actual_account:
            raise InstrumentRuntimeStateError(
                "Instrument runtime document account scope mismatch."
            )
        if expected_account_id is not None and actual_account != str(
            expected_account_id
        ):
            raise InstrumentRuntimeStateError(
                "Instrument runtime state belongs to a different account."
            )
        return normalized

    def _load_unlocked(
        self,
        *,
        expected_account_id: str | None,
    ) -> tuple[InstrumentRuntime, ...]:
        try:
            document = read_json_verified(
                self.path,
                supported_versions={self.SCHEMA_VERSION},
            )
        except StatePersistenceError as exc:
            raise InstrumentRuntimeStateError(str(exc)) from exc
        return self._load_document(
            document,
            expected_account_id=expected_account_id,
        )

    def _document(
        self,
        runtimes: tuple[InstrumentRuntime, ...],
    ) -> dict[str, Any]:
        account_id = runtimes[0].config.account_id if runtimes else None
        return {
            "version": self.SCHEMA_VERSION,
            "account_id": account_id,
            "runtimes": [runtime.to_dict() for runtime in runtimes],
        }

    @staticmethod
    def _canonical_document_bytes(document: Mapping[str, Any]) -> bytes:
        return json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _write_unlocked(self, document: Mapping[str, Any]) -> StateSaveResult:
        return atomic_write_json(
            self.path,
            document,
            write_checksum=True,
            keep_last_good=True,
            validate_roundtrip=True,
        )

    @staticmethod
    def _validate_collection(
        runtimes: tuple[InstrumentRuntime, ...],
    ) -> tuple[InstrumentRuntime, ...]:
        keys: set[str] = set()
        execution_scopes: set[str] = set()
        accounts: set[str] = set()
        for runtime in runtimes:
            if not isinstance(runtime, InstrumentRuntime):
                raise InstrumentRuntimeStateError(
                    "All persisted values must be InstrumentRuntime objects."
                )
            if runtime.runtime_key in keys:
                raise InstrumentRuntimeConflictError(
                    f"Duplicate runtime key: {runtime.runtime_key}"
                )
            if runtime.config.execution_scope_key in execution_scopes:
                raise InstrumentRuntimeConflictError(
                    "v3.8 permits one InstrumentRuntime per account/instrument; "
                    "per-strategy timeframe belongs to v4.x."
                )
            keys.add(runtime.runtime_key)
            execution_scopes.add(runtime.config.execution_scope_key)
            accounts.add(runtime.config.account_id)
        if len(accounts) > 1:
            raise InstrumentRuntimeConflictError(
                "One InstrumentRuntimeStore cannot mix account scopes."
            )
        return tuple(sorted(runtimes, key=lambda item: item.runtime_key))
