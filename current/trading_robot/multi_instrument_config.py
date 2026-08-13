from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .bot import BotConfig
from .config_persistence import (
    PROFILE_MODES,
    ProfileMode,
    StrategyProfileError,
    canonical_profile_hash,
    normalize_profile_mode,
    profile_to_bot_config,
    validate_strategy_profile,
)
from .instrument_runtime import (
    InstrumentRuntime,
    InstrumentRuntimeConfig,
    InstrumentRuntimeConflictError,
    InstrumentRuntimeStore,
)
from .locking import InterProcessFileLock, LockUnavailableError
from .state_persistence import (
    StatePersistenceError,
    atomic_write_json,
    read_json_verified,
)

MULTI_INSTRUMENT_PROFILE_SCHEMA_VERSION = 1
MAX_V3_8_INSTRUMENTS = 3


class MultiInstrumentConfigError(RuntimeError):
    """Raised when a v3.8 instrument profile set is invalid or unsafe."""


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MultiInstrumentConfigError(f"{field} must not be empty.")
    return normalized


def _positive_int(value: Any, field: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise MultiInstrumentConfigError(f"{field} must be an integer.") from exc
    if normalized < 1:
        raise MultiInstrumentConfigError(f"{field} must be positive.")
    return normalized


@dataclass(frozen=True, slots=True)
class MultiInstrumentProfile:
    """One secret-free Strategy/Bot profile bound to one instrument runtime."""

    instrument_id: str
    strategy_profile: Mapping[str, Any]
    configuration_version: int = 1
    decision_cadence_seconds: int = 60
    scheduler_cadence_seconds: int = 15
    risk_refresh_cadence_seconds: int = 60
    reconciliation_cadence_seconds: int = 300
    market_status_cadence_seconds: int = 60

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "instrument_id",
            _required_text(self.instrument_id, "instrument_id"),
        )
        try:
            profile = validate_strategy_profile(self.strategy_profile)
            # BotConfig provides the canonical type/range validation already
            # used by the single-instrument profile store.
            profile_to_bot_config(
                profile,
                mode="DRY_RUN",
                state_file="robot_state.json",
                journal_file="trading_events.db",
            )
        except (StrategyProfileError, TypeError, ValueError) as exc:
            raise MultiInstrumentConfigError(str(exc)) from exc
        object.__setattr__(self, "strategy_profile", profile)
        for field in (
            "configuration_version",
            "decision_cadence_seconds",
            "scheduler_cadence_seconds",
            "risk_refresh_cadence_seconds",
            "reconciliation_cadence_seconds",
            "market_status_cadence_seconds",
        ):
            object.__setattr__(
                self,
                field,
                _positive_int(getattr(self, field), field),
            )

    @property
    def ticker(self) -> str:
        return _required_text(self.strategy_profile.get("ticker"), "ticker").upper()

    @property
    def class_code(self) -> str:
        return _required_text(
            self.strategy_profile.get("class_code"), "class_code"
        ).upper()

    @property
    def candle_interval(self) -> str:
        return _required_text(
            self.strategy_profile.get("candle_interval"), "candle_interval"
        ).upper()

    @property
    def strategy_id(self) -> str:
        return _required_text(
            self.strategy_profile.get("primary_strategy"), "primary_strategy"
        ).lower()

    @property
    def strategy_profile_hash(self) -> str:
        return canonical_profile_hash(self.strategy_profile)

    @property
    def profile_identity_hash(self) -> str:
        payload = {
            "schema_version": MULTI_INSTRUMENT_PROFILE_SCHEMA_VERSION,
            "configuration_version": self.configuration_version,
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "class_code": self.class_code,
            "candle_interval": self.candle_interval,
            "strategy_id": self.strategy_id,
            "strategy_profile_hash": self.strategy_profile_hash,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def to_runtime_config(self, account_id: str) -> InstrumentRuntimeConfig:
        return InstrumentRuntimeConfig(
            account_id=account_id,
            instrument_id=self.instrument_id,
            ticker=self.ticker,
            class_code=self.class_code,
            candle_interval=self.candle_interval,
            strategy_id=self.strategy_id,
            strategy_config_hash=self.strategy_profile_hash,
            configuration_version=self.configuration_version,
            decision_cadence_seconds=self.decision_cadence_seconds,
            scheduler_cadence_seconds=self.scheduler_cadence_seconds,
            risk_refresh_cadence_seconds=self.risk_refresh_cadence_seconds,
            reconciliation_cadence_seconds=self.reconciliation_cadence_seconds,
            market_status_cadence_seconds=self.market_status_cadence_seconds,
        )

    def to_bot_config(
        self,
        *,
        mode: ProfileMode,
        state_file: str,
        journal_file: str,
    ) -> BotConfig:
        return profile_to_bot_config(
            self.strategy_profile,
            mode=mode,
            state_file=state_file,
            journal_file=journal_file,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "configuration_version": self.configuration_version,
            "strategy_profile": dict(self.strategy_profile),
            "strategy_profile_hash": self.strategy_profile_hash,
            "profile_identity_hash": self.profile_identity_hash,
            "cadences": {
                "decision_seconds": self.decision_cadence_seconds,
                "scheduler_seconds": self.scheduler_cadence_seconds,
                "risk_refresh_seconds": self.risk_refresh_cadence_seconds,
                "reconciliation_seconds": self.reconciliation_cadence_seconds,
                "market_status_seconds": self.market_status_cadence_seconds,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> MultiInstrumentProfile:
        strategy_profile = raw.get("strategy_profile")
        cadences = raw.get("cadences") or {}
        if not isinstance(strategy_profile, Mapping):
            raise MultiInstrumentConfigError("strategy_profile must be an object.")
        if not isinstance(cadences, Mapping):
            raise MultiInstrumentConfigError("cadences must be an object.")
        profile = cls(
            instrument_id=raw.get("instrument_id", ""),
            strategy_profile=strategy_profile,
            configuration_version=raw.get("configuration_version", 0),
            decision_cadence_seconds=cadences.get("decision_seconds", 0),
            scheduler_cadence_seconds=cadences.get("scheduler_seconds", 0),
            risk_refresh_cadence_seconds=cadences.get("risk_refresh_seconds", 0),
            reconciliation_cadence_seconds=cadences.get(
                "reconciliation_seconds", 0
            ),
            market_status_cadence_seconds=cadences.get("market_status_seconds", 0),
        )
        if raw.get("strategy_profile_hash") != profile.strategy_profile_hash:
            raise MultiInstrumentConfigError("Strategy profile checksum mismatch.")
        if raw.get("profile_identity_hash") != profile.profile_identity_hash:
            raise MultiInstrumentConfigError("Instrument profile identity mismatch.")
        return profile


class MultiInstrumentProfileStore:
    SCHEMA_VERSION = MULTI_INSTRUMENT_PROFILE_SCHEMA_VERSION

    def __init__(
        self,
        path: str | Path,
        *,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.checksum_path = self.path.with_name(self.path.name + ".sha256")
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))

    def load_document(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.SCHEMA_VERSION, "profiles": {}}
        if not self.checksum_path.exists():
            raise MultiInstrumentConfigError(
                "Multi-instrument profile checksum is missing."
            )
        try:
            document = read_json_verified(
                self.path,
                supported_versions={self.SCHEMA_VERSION},
            )
        except StatePersistenceError as exc:
            raise MultiInstrumentConfigError(str(exc)) from exc
        profiles = document.get("profiles")
        if not isinstance(profiles, dict):
            raise MultiInstrumentConfigError("profiles must be an object.")
        unknown_modes = sorted(set(profiles) - set(PROFILE_MODES))
        if unknown_modes:
            raise MultiInstrumentConfigError(
                "Unsupported profile modes: " + ", ".join(unknown_modes)
            )
        return document

    def load_mode(self, mode: ProfileMode) -> tuple[MultiInstrumentProfile, ...]:
        normalized_mode = normalize_profile_mode(mode)
        document = self.load_document()
        raw_mode = document["profiles"].get(normalized_mode)
        if raw_mode is None:
            return ()
        if not isinstance(raw_mode, Mapping):
            raise MultiInstrumentConfigError(
                f"Profile mode {normalized_mode} must be an object."
            )
        raw_instruments = raw_mode.get("instruments")
        if not isinstance(raw_instruments, list):
            raise MultiInstrumentConfigError("instruments must be an array.")
        profiles = self._validate_collection(
            MultiInstrumentProfile.from_dict(raw) if isinstance(raw, Mapping)
            else self._invalid_profile_item()
            for raw in raw_instruments
        )
        expected_hash = str(raw_mode.get("profile_set_hash") or "")
        actual_hash = self._profile_set_hash(profiles)
        if expected_hash != actual_hash:
            raise MultiInstrumentConfigError("Profile set checksum mismatch.")
        return profiles

    def save_mode(
        self,
        mode: ProfileMode,
        profiles: Iterable[MultiInstrumentProfile],
    ) -> tuple[MultiInstrumentProfile, ...]:
        normalized_mode = normalize_profile_mode(mode)
        normalized = self._validate_collection(profiles)
        try:
            with InterProcessFileLock(
                self.lock_path, timeout_seconds=self.lock_timeout_seconds
            ):
                document = self.load_document()
                document["version"] = self.SCHEMA_VERSION
                document["profiles"][normalized_mode] = {
                    "instruments": [profile.to_dict() for profile in normalized],
                    "profile_set_hash": self._profile_set_hash(normalized),
                }
                atomic_write_json(
                    self.path,
                    document,
                    write_checksum=True,
                    keep_last_good=True,
                    validate_roundtrip=True,
                )
        except (LockUnavailableError, StatePersistenceError) as exc:
            raise MultiInstrumentConfigError(str(exc)) from exc
        return normalized

    def bootstrap_runtime_registry(
        self,
        *,
        mode: ProfileMode,
        account_id: str,
        runtime_store: InstrumentRuntimeStore,
    ) -> tuple[InstrumentRuntime, ...]:
        """Create missing stopped runtimes without implicit config transitions."""

        profiles = self.load_mode(mode)
        if not profiles:
            raise MultiInstrumentConfigError(
                f"No multi-instrument profiles are configured for {mode}."
            )
        existing = runtime_store.load_optional(expected_account_id=account_id)
        by_scope = {
            runtime.config.execution_scope_key: runtime for runtime in existing
        }
        desired_scopes: set[str] = set()
        result: list[InstrumentRuntime] = []
        for profile in profiles:
            config = profile.to_runtime_config(account_id)
            desired_scopes.add(config.execution_scope_key)
            current = by_scope.get(config.execution_scope_key)
            if current is None:
                result.append(InstrumentRuntime(config=config))
                continue
            if current.config.runtime_config_hash != config.runtime_config_hash:
                raise InstrumentRuntimeConflictError(
                    f"Runtime config transition for {profile.ticker} requires an "
                    "explicit stopped+flat operator action."
                )
            if current.config.to_dict() != config.to_dict():
                raise InstrumentRuntimeConflictError(
                    f"Runtime cadence transition for {profile.ticker} requires "
                    "explicit review."
                )
            result.append(current)
        orphaned = [
            runtime.config.ticker
            for runtime in existing
            if runtime.config.execution_scope_key not in desired_scopes
        ]
        if orphaned:
            raise InstrumentRuntimeConflictError(
                "Runtime removal requires explicit review: " + ", ".join(orphaned)
            )
        runtime_store.save(result)
        return tuple(sorted(result, key=lambda item: item.runtime_key))

    @staticmethod
    def _invalid_profile_item() -> MultiInstrumentProfile:
        raise MultiInstrumentConfigError(
            "Each multi-instrument profile must be an object."
        )

    @staticmethod
    def _validate_collection(
        profiles: Iterable[MultiInstrumentProfile],
    ) -> tuple[MultiInstrumentProfile, ...]:
        normalized = tuple(profiles)
        if len(normalized) > MAX_V3_8_INSTRUMENTS:
            raise MultiInstrumentConfigError(
                f"v3.8 supports at most {MAX_V3_8_INSTRUMENTS} instruments."
            )
        instrument_ids: set[str] = set()
        symbols: set[tuple[str, str]] = set()
        for profile in normalized:
            if not isinstance(profile, MultiInstrumentProfile):
                raise MultiInstrumentConfigError(
                    "profiles must contain MultiInstrumentProfile objects."
                )
            if profile.instrument_id in instrument_ids:
                raise MultiInstrumentConfigError(
                    f"Duplicate instrument_id: {profile.instrument_id}"
                )
            symbol = (profile.ticker, profile.class_code)
            if symbol in symbols:
                raise MultiInstrumentConfigError(
                    f"Duplicate instrument symbol: {profile.ticker}_{profile.class_code}"
                )
            instrument_ids.add(profile.instrument_id)
            symbols.add(symbol)
        return tuple(sorted(normalized, key=lambda item: item.instrument_id))

    @staticmethod
    def _profile_set_hash(
        profiles: Iterable[MultiInstrumentProfile],
    ) -> str:
        payload = [profile.to_dict() for profile in profiles]
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()
