from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from .bot import BotConfig
from .locking import InterProcessFileLock
from .state_persistence import atomic_write_json


ProfileMode = Literal["DRY_RUN", "SANDBOX_EXECUTION"]
PROFILE_MODES: tuple[ProfileMode, ...] = ("DRY_RUN", "SANDBOX_EXECUTION")


class StrategyProfileError(RuntimeError):
    """Raised when the profile file is missing, invalid or unsafe to use."""


_PROFILE_FIELDS: tuple[str, ...] = (
    "ticker",
    "class_code",
    "candle_interval",
    "primary_strategy",
    "shadow_strategies",
    "fast_window",
    "slow_window",
    "sma_hysteresis_percent",
    "donchian_entry_window",
    "donchian_exit_window",
    "donchian_atr_window",
    "donchian_trailing_stop_atr",
    "ensemble_sma_fast",
    "ensemble_sma_slow",
    "ensemble_momentum_window",
    "ensemble_breakout_window",
    "ensemble_vote_threshold",
    "annual_target_volatility",
    "volatility_window",
    "max_strategy_weight",
    "lookback_days",
    "poll_seconds",
    "max_order_lots",
    "order_type",
    "time_in_force",
    "check_trading_status",
    "failure_threshold",
    "circuit_open_seconds",
    "circuit_max_open_seconds",
    "max_signal_age_seconds",
    "reconcile_attempts",
    "reconcile_delay_seconds",
    "portfolio_reconcile_interval_seconds",
)

_ALLOWED_PROFILE_FIELDS = frozenset(
    (*_PROFILE_FIELDS, "connect_timeout_seconds", "read_timeout_seconds")
)
_FORBIDDEN_PROFILE_KEYS = frozenset(
    {
        "token",
        "api_token",
        "account_id",
        "authorization",
        "tbank_sandbox_token",
        "tbank_sandbox_account_id",
        "password",
        "secret",
    }
)


def _find_forbidden_profile_keys(value: Any, *, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}" if prefix else key
            if key.strip().lower() in _FORBIDDEN_PROFILE_KEYS:
                found.append(path)
            found.extend(_find_forbidden_profile_keys(nested, prefix=path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            found.extend(_find_forbidden_profile_keys(nested, prefix=path))
    return found


def _validate_profile_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(config)
    leaked = _find_forbidden_profile_keys(clean)
    if leaked:
        raise StrategyProfileError(
            "Strategy profile contains forbidden secret/account fields: "
            + ", ".join(sorted(set(leaked)))
        )
    unknown = sorted(set(clean) - _ALLOWED_PROFILE_FIELDS)
    if unknown:
        raise StrategyProfileError(
            "Strategy profile contains unsupported fields: "
            + ", ".join(unknown)
        )
    return clean


def validate_strategy_profile(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated copy of a secret-free strategy profile."""

    return _validate_profile_payload(config)


def normalize_profile_mode(value: str) -> ProfileMode:
    normalized = str(value).strip().upper()
    if normalized not in PROFILE_MODES:
        raise StrategyProfileError(
            f"Unsupported strategy profile mode: {value!r}."
        )
    return normalized  # type: ignore[return-value]


def canonical_profile_hash(profile: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(profile),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def bot_config_to_profile(
    config: BotConfig,
    *,
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
) -> dict[str, Any]:
    source = asdict(config)
    profile: dict[str, Any] = {
        field: source[field]
        for field in _PROFILE_FIELDS
        if field in source
    }
    profile["shadow_strategies"] = list(config.shadow_strategies)
    profile["connect_timeout_seconds"] = float(connect_timeout_seconds)
    profile["read_timeout_seconds"] = float(read_timeout_seconds)
    return profile


def profile_to_bot_config(
    profile: Mapping[str, Any],
    *,
    mode: ProfileMode,
    state_file: str,
    journal_file: str,
    heartbeat_log_seconds: int = 1800,
    slow_cycle_seconds: float = 10.0,
) -> BotConfig:
    normalized_mode = normalize_profile_mode(mode)
    raw = dict(profile)
    raw.pop("connect_timeout_seconds", None)
    raw.pop("read_timeout_seconds", None)
    raw["shadow_strategies"] = tuple(raw.get("shadow_strategies") or ())
    raw["dry_run"] = normalized_mode == "DRY_RUN"
    raw["state_file"] = state_file
    raw["journal_file"] = journal_file
    raw["heartbeat_log_seconds"] = int(heartbeat_log_seconds)
    raw["slow_cycle_seconds"] = float(slow_cycle_seconds)
    try:
        return BotConfig(**raw)
    except (TypeError, ValueError) as exc:
        raise StrategyProfileError(
            f"Invalid {normalized_mode} strategy profile: {exc}"
        ) from exc


class StrategyProfileStore:
    """Atomic, mode-separated persistence for GUI trading configurations.

    Secrets and account identifiers deliberately do not belong in this file.
    The store contains only strategy/runtime parameters for DRY_RUN and
    SANDBOX_EXECUTION. A corrupt file is rejected rather than silently replaced,
    because an unnoticed fallback could switch the executing PRIMARY strategy.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def load_document(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": self.SCHEMA_VERSION,
                "last_selected_mode": "DRY_RUN",
                "profiles": {},
            }
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StrategyProfileError(
                "Файл strategy_profiles.json существует, но не читается. "
                "Sandbox Execution заблокирован до восстановления или явного "
                "сброса профиля."
            ) from exc
        if not isinstance(document, dict):
            raise StrategyProfileError("Strategy profile root must be an object.")
        profiles = document.get("profiles")
        if profiles is None:
            profiles = {}
        if not isinstance(profiles, dict):
            raise StrategyProfileError("Strategy profile 'profiles' must be an object.")
        version = int(document.get("version", 0) or 0)
        if version > self.SCHEMA_VERSION:
            raise StrategyProfileError(
                f"Strategy profile schema {version} is newer than supported "
                f"schema {self.SCHEMA_VERSION}."
            )
        document["version"] = self.SCHEMA_VERSION
        document["profiles"] = profiles
        raw_last_mode = document.get("last_selected_mode")
        if raw_last_mode in (None, ""):
            document["last_selected_mode"] = "DRY_RUN"
        else:
            try:
                document["last_selected_mode"] = normalize_profile_mode(
                    str(raw_last_mode)
                )
            except StrategyProfileError as exc:
                raise StrategyProfileError(
                    "Strategy profile contains an invalid last_selected_mode. "
                    "Execution is blocked instead of silently switching the "
                    "active Dry-run/Sandbox profile."
                ) from exc
        return document

    def load_profile(self, mode: ProfileMode) -> dict[str, Any] | None:
        normalized_mode = normalize_profile_mode(mode)
        document = self.load_document()
        raw = document["profiles"].get(normalized_mode)
        if raw is None:
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("config"), dict):
            raise StrategyProfileError(
                f"Profile {normalized_mode} has an invalid structure."
            )
        config = _validate_profile_payload(raw["config"])
        expected_hash = str(raw.get("config_hash") or "").strip()
        actual_hash = canonical_profile_hash(config)
        if not expected_hash:
            raise StrategyProfileError(
                f"Profile {normalized_mode} has no checksum. Execution is blocked."
            )
        if expected_hash != actual_hash:
            raise StrategyProfileError(
                f"Profile {normalized_mode} checksum mismatch. Execution is blocked."
            )
        return {
            "mode": normalized_mode,
            "config": config,
            "config_hash": actual_hash,
            "updated_at": raw.get("updated_at"),
        }

    def save_profile(
        self,
        mode: ProfileMode,
        config: Mapping[str, Any],
        *,
        select: bool = True,
    ) -> dict[str, Any]:
        normalized_mode = normalize_profile_mode(mode)
        clean = _validate_profile_payload(config)
        config_hash = canonical_profile_hash(clean)
        updated_at = datetime.now(timezone.utc).isoformat()
        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            document = self.load_document()
            document["profiles"][normalized_mode] = {
                "config": clean,
                "config_hash": config_hash,
                "updated_at": updated_at,
            }
            if select:
                document["last_selected_mode"] = normalized_mode
            document["version"] = self.SCHEMA_VERSION
            atomic_write_json(self.path, document, backup_existing=True)
        return {
            "mode": normalized_mode,
            "config": clean,
            "config_hash": config_hash,
            "updated_at": updated_at,
        }

    def set_last_selected_mode(self, mode: ProfileMode) -> None:
        normalized_mode = normalize_profile_mode(mode)
        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            document = self.load_document()
            document["last_selected_mode"] = normalized_mode
            atomic_write_json(self.path, document, backup_existing=True)

    def reset_profile(
        self,
        mode: ProfileMode,
        *,
        force_recreate_document: bool = False,
    ) -> bool:
        normalized_mode = normalize_profile_mode(mode)
        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            try:
                document = self.load_document()
            except StrategyProfileError:
                if not force_recreate_document:
                    raise
                document = {
                    "version": self.SCHEMA_VERSION,
                    "last_selected_mode": normalized_mode,
                    "profiles": {},
                }
            existed = normalized_mode in document["profiles"]
            document["profiles"].pop(normalized_mode, None)
            if document.get("last_selected_mode") == normalized_mode:
                document["last_selected_mode"] = "DRY_RUN"
            atomic_write_json(
                self.path,
                document,
                backup_existing=not force_recreate_document,
            )
            return existed

    def last_selected_mode(self) -> ProfileMode:
        return normalize_profile_mode(
            str(self.load_document().get("last_selected_mode") or "DRY_RUN")
        )
