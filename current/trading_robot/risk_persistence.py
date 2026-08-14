from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .locking import InterProcessFileLock
from .risk import RiskPolicy, RiskState
from .state_persistence import atomic_write_json

RiskMode = Literal["DRY_RUN", "SANDBOX_EXECUTION"]
RISK_MODES: tuple[RiskMode, ...] = ("DRY_RUN", "SANDBOX_EXECUTION")


class RiskPersistenceError(RuntimeError):
    """Raised when a risk profile/state cannot be trusted or persisted."""


_FORBIDDEN_KEYS = frozenset(
    {
        "token",
        "api_token",
        "account_id",
        "authorization",
        "password",
        "secret",
        "tbank_sandbox_token",
        "tbank_sandbox_account_id",
    }
)
_POLICY_FIELDS = frozenset(field.name for field in fields(RiskPolicy))
_PORTFOLIO_POLICY_FIELDS = frozenset(
    {
        "portfolio_policy_configured",
        "portfolio_policy_mode",
        "max_gross_exposure_rub",
        "max_gross_exposure_fraction",
        "max_net_exposure_fraction",
        "max_instrument_concentration_fraction",
        "max_strategy_concentration_fraction",
        "max_asset_class_concentration_fraction",
        "asset_class_concentration_limits",
        "max_open_positions",
        "min_cash_reserve_fraction",
        "max_daily_turnover_fraction",
        "max_price_age_seconds",
        "portfolio_warning_utilization_fraction",
    }
)
def normalize_risk_mode(value: str) -> RiskMode:
    normalized = str(value).strip().upper()
    if normalized not in RISK_MODES:
        raise RiskPersistenceError(f"Unsupported risk mode: {value!r}.")
    return normalized  # type: ignore[return-value]


def _find_forbidden(value: Any, *, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}" if prefix else key
            if key.strip().lower() in _FORBIDDEN_KEYS:
                found.add(path)
            found.update(_find_forbidden(nested, prefix=path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            found.update(_find_forbidden(nested, prefix=path))
    return found


def _policy_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()


def _policy_from_payload(
    payload: Mapping[str, Any],
    *,
    allow_legacy: bool = False,
) -> RiskPolicy:
    clean = dict(payload)
    forbidden = _find_forbidden(clean)
    if forbidden:
        raise RiskPersistenceError(
            "Risk profile contains forbidden secret/account fields: "
            + ", ".join(sorted(forbidden))
        )
    unknown = sorted(set(clean) - _POLICY_FIELDS)
    missing = sorted(_POLICY_FIELDS - set(clean))
    if unknown:
        raise RiskPersistenceError(
            "Risk profile contains unsupported fields: " + ", ".join(unknown)
        )
    if missing and not (
        allow_legacy and set(missing).issubset(_PORTFOLIO_POLICY_FIELDS)
    ):
        raise RiskPersistenceError(
            "Risk profile is incomplete; missing fields: " + ", ".join(missing)
        )
    if allow_legacy:
        defaults = asdict(RiskPolicy())
        for field_name in _PORTFOLIO_POLICY_FIELDS:
            clean.setdefault(field_name, defaults[field_name])
    try:
        return RiskPolicy(**clean)
    except (TypeError, ValueError) as exc:
        raise RiskPersistenceError(f"Invalid risk profile: {exc}") from exc


def _portfolio_policy_status(mode: RiskMode, policy: RiskPolicy) -> str:
    if policy.portfolio_policy_configured:
        return "READY"
    if mode == "SANDBOX_EXECUTION":
        return "CONFIGURATION_REQUIRED"
    return "OBSERVE_ONLY_UNCONFIGURED"


class RiskProfileStore:
    """Checksummed, mode-separated persistence for risk policies.

    DRY_RUN and SANDBOX_EXECUTION are independent. A missing/corrupt Sandbox
    profile must be treated as fail-closed by the future execution adapter.
    """

    SCHEMA_VERSION = 2

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
            raise RiskPersistenceError(
                "risk_profiles.json exists but cannot be read; risk execution "
                "must remain blocked."
            ) from exc
        if not isinstance(document, dict):
            raise RiskPersistenceError("Risk profile root must be an object.")
        version = int(document.get("version", 0) or 0)
        if version not in {1, self.SCHEMA_VERSION}:
            raise RiskPersistenceError(
                f"Unsupported risk profile schema {version}; supported "
                f"schemas are 1 and {self.SCHEMA_VERSION}."
            )
        profiles = document.get("profiles")
        if not isinstance(profiles, dict):
            raise RiskPersistenceError("Risk profile 'profiles' must be an object.")
        raw_mode = document.get("last_selected_mode", "DRY_RUN")
        document["last_selected_mode"] = normalize_risk_mode(str(raw_mode))
        if version == 1:
            for raw_mode_name, raw_profile in profiles.items():
                mode = normalize_risk_mode(str(raw_mode_name))
                if not isinstance(raw_profile, dict) or not isinstance(
                    raw_profile.get("policy"), dict
                ):
                    raise RiskPersistenceError(
                        f"Risk profile {mode} has invalid structure."
                    )
                raw_policy = dict(raw_profile["policy"])
                expected_hash = str(raw_profile.get("policy_hash") or "").strip()
                if not expected_hash or expected_hash != _policy_payload_hash(
                    raw_policy
                ):
                    raise RiskPersistenceError(
                        f"Risk profile {mode} checksum mismatch."
                    )
                policy = _policy_from_payload(raw_policy, allow_legacy=True)
                raw_profile["policy"] = asdict(policy)
                raw_profile["policy_hash"] = policy.policy_hash
                raw_profile["portfolio_policy_status"] = _portfolio_policy_status(
                    mode,
                    policy,
                )
                raw_profile["migrated_from_schema"] = 1
        document["profiles"] = profiles
        document["version"] = self.SCHEMA_VERSION
        return document

    def load_profile(self, mode: RiskMode) -> dict[str, Any] | None:
        normalized = normalize_risk_mode(mode)
        document = self.load_document()
        raw = document["profiles"].get(normalized)
        if raw is None:
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("policy"), dict):
            raise RiskPersistenceError(
                f"Risk profile {normalized} has invalid structure."
            )
        policy = _policy_from_payload(raw["policy"])
        expected_hash = str(raw.get("policy_hash") or "").strip()
        if not expected_hash:
            raise RiskPersistenceError(f"Risk profile {normalized} has no checksum.")
        if expected_hash != policy.policy_hash:
            raise RiskPersistenceError(f"Risk profile {normalized} checksum mismatch.")
        account_scope = str(raw.get("account_scope") or "").strip() or None
        source = str(raw.get("source") or "").strip() or None
        portfolio_policy_status = _portfolio_policy_status(normalized, policy)
        persisted_status = str(raw.get("portfolio_policy_status") or "").strip().upper()
        if persisted_status and persisted_status != portfolio_policy_status:
            raise RiskPersistenceError(
                f"Risk profile {normalized} has inconsistent Portfolio Risk status."
            )
        return {
            "mode": normalized,
            "policy": policy,
            "policy_hash": policy.policy_hash,
            "updated_at": raw.get("updated_at"),
            "account_scope": account_scope,
            "source": source,
            "portfolio_policy_status": portfolio_policy_status,
            "migrated_from_schema": raw.get("migrated_from_schema"),
        }

    def require_profile(self, mode: RiskMode) -> dict[str, Any]:
        loaded = self.load_profile(mode)
        if loaded is None:
            raise RiskPersistenceError(
                f"Risk profile {normalize_risk_mode(mode)} is not saved."
            )
        return loaded

    def require_portfolio_policy(self, mode: RiskMode) -> dict[str, Any]:
        loaded = self.require_profile(mode)
        if loaded["portfolio_policy_status"] != "READY":
            raise RiskPersistenceError(
                f"Risk profile {normalize_risk_mode(mode)} requires explicit "
                "Portfolio Risk configuration."
            )
        return loaded

    def save_profile(
        self,
        mode: RiskMode,
        policy: RiskPolicy,
        *,
        select: bool = True,
        account_scope: str | None = None,
        source: str | None = None,
        _portfolio_confirmation: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_risk_mode(mode)
        if (
            normalized == "SANDBOX_EXECUTION"
            and policy.portfolio_policy_configured
            and not _portfolio_confirmation
        ):
            raise RiskPersistenceError(
                "Configured Sandbox Portfolio Risk policy must be saved through "
                "explicit confirmation 'CONFIRM PORTFOLIO RISK POLICY'."
            )
        payload = asdict(policy)
        forbidden = _find_forbidden(payload)
        if forbidden:
            raise RiskPersistenceError(
                "Risk profile contains forbidden secret/account fields: "
                + ", ".join(sorted(forbidden))
            )
        updated_at = datetime.now(timezone.utc).isoformat()
        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            document = self.load_document()
            profile_entry = {
                "policy": payload,
                "policy_hash": policy.policy_hash,
                "updated_at": updated_at,
                "portfolio_policy_status": _portfolio_policy_status(
                    normalized,
                    policy,
                ),
            }
            normalized_scope = str(account_scope or "").strip()
            if normalized_scope:
                profile_entry["account_scope"] = normalized_scope
            normalized_source = str(source or "").strip().upper()
            if normalized_source:
                profile_entry["source"] = normalized_source
            document["profiles"][normalized] = profile_entry
            if select:
                document["last_selected_mode"] = normalized
            atomic_write_json(self.path, document, backup_existing=True)
        return {
            "mode": normalized,
            "policy": policy,
            "policy_hash": policy.policy_hash,
            "updated_at": updated_at,
            "account_scope": normalized_scope or None,
            "source": normalized_source or None,
            "portfolio_policy_status": profile_entry["portfolio_policy_status"],
        }

    def confirm_portfolio_policy(
        self,
        mode: RiskMode,
        policy: RiskPolicy,
        *,
        confirmation: str,
        select: bool = True,
        account_scope: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        if str(confirmation).strip().upper() != "CONFIRM PORTFOLIO RISK POLICY":
            raise RiskPersistenceError(
                "Portfolio Risk confirmation must be exactly "
                "'CONFIRM PORTFOLIO RISK POLICY'."
            )
        if not policy.portfolio_policy_configured:
            raise RiskPersistenceError(
                "Confirmed Portfolio Risk policy must set "
                "portfolio_policy_configured=true."
            )
        return self.save_profile(
            mode,
            policy,
            select=select,
            account_scope=account_scope,
            source=source,
            _portfolio_confirmation=True,
        )

    def create_default_dry_run(self) -> dict[str, Any]:
        loaded = self.load_profile("DRY_RUN")
        if loaded is not None:
            return loaded
        return self.save_profile("DRY_RUN", RiskPolicy(), select=True)

    def reset_profile(self, mode: RiskMode) -> bool:
        normalized = normalize_risk_mode(mode)
        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            document = self.load_document()
            removed = document["profiles"].pop(normalized, None) is not None
            if document.get("last_selected_mode") == normalized:
                document["last_selected_mode"] = "DRY_RUN"
            atomic_write_json(self.path, document, backup_existing=True)
        return removed


class RiskStateStore:
    """Atomic per-account persistence for additive RiskState migration."""

    SCHEMA_VERSION = 4

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def load_document(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.SCHEMA_VERSION, "accounts": {}}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RiskPersistenceError(
                "risk_state.json exists but cannot be read; new exposure must "
                "remain blocked until state is recovered."
            ) from exc
        if not isinstance(document, dict):
            raise RiskPersistenceError("Risk state root must be an object.")
        version = int(document.get("version", 0) or 0)
        if version not in {1, 2, 3, self.SCHEMA_VERSION}:
            raise RiskPersistenceError(
                f"Unsupported risk state schema {version}; supported schemas "
                f"are 1, 2, 3 and {self.SCHEMA_VERSION}."
            )
        accounts = document.get("accounts")
        if not isinstance(accounts, dict):
            raise RiskPersistenceError("Risk state accounts must be an object.")
        return {"version": self.SCHEMA_VERSION, "accounts": accounts}

    def load_account(self, account_id: str) -> RiskState:
        normalized = str(account_id).strip()
        if not normalized:
            raise RiskPersistenceError("account_id must not be empty.")
        document = self.load_document()
        raw = document["accounts"].get(normalized)
        if raw is None:
            return RiskState()
        if not isinstance(raw, dict):
            raise RiskPersistenceError(
                f"Risk state for account {normalized} must be an object."
            )
        try:
            return RiskState.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise RiskPersistenceError(
                f"Invalid risk state for account {normalized}: {exc}"
            ) from exc

    def save_account(self, account_id: str, state: RiskState) -> None:
        self.update_account(account_id, lambda _current: state)

    def save_account_while_locked(self, account_id: str, state: RiskState) -> None:
        """Persist while the caller owns ``lock_path`` in the M4 lock order."""

        normalized = str(account_id).strip()
        if not normalized:
            raise RiskPersistenceError("account_id must not be empty.")
        if not isinstance(state, RiskState):
            raise TypeError("state must be RiskState.")
        document = self.load_document()
        raw = document["accounts"].get(normalized)
        if raw is not None and not isinstance(raw, dict):
            raise RiskPersistenceError(
                f"Risk state for account {normalized} must be an object."
            )
        document["accounts"][normalized] = state.to_dict()
        atomic_write_json(self.path, document, backup_existing=True)

    def update_account(
        self,
        account_id: str,
        updater: Callable[[RiskState], RiskState],
    ) -> RiskState:
        normalized = str(account_id).strip()
        if not normalized:
            raise RiskPersistenceError("account_id must not be empty.")
        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            document = self.load_document()
            raw = document["accounts"].get(normalized)
            if raw is not None and not isinstance(raw, dict):
                raise RiskPersistenceError(
                    f"Risk state for account {normalized} must be an object."
                )
            current = RiskState.from_dict(raw)
            updated = updater(current)
            if not isinstance(updated, RiskState):
                raise TypeError("Risk state updater must return RiskState.")
            document["accounts"][normalized] = updated.to_dict()
            atomic_write_json(self.path, document, backup_existing=True)
            return updated
