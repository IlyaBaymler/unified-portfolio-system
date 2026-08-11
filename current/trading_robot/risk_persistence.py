from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

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


def _policy_from_payload(payload: Mapping[str, Any]) -> RiskPolicy:
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
    if missing:
        raise RiskPersistenceError(
            "Risk profile is incomplete; missing fields: " + ", ".join(missing)
        )
    try:
        return RiskPolicy(**clean)
    except (TypeError, ValueError) as exc:
        raise RiskPersistenceError(f"Invalid risk profile: {exc}") from exc


class RiskProfileStore:
    """Checksummed, mode-separated persistence for risk policies.

    DRY_RUN and SANDBOX_EXECUTION are independent. A missing/corrupt Sandbox
    profile must be treated as fail-closed by the future execution adapter.
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
            raise RiskPersistenceError(
                "risk_profiles.json exists but cannot be read; risk execution "
                "must remain blocked."
            ) from exc
        if not isinstance(document, dict):
            raise RiskPersistenceError("Risk profile root must be an object.")
        version = int(document.get("version", 0) or 0)
        if version != self.SCHEMA_VERSION:
            raise RiskPersistenceError(
                f"Unsupported risk profile schema {version}; expected "
                f"{self.SCHEMA_VERSION}."
            )
        profiles = document.get("profiles")
        if not isinstance(profiles, dict):
            raise RiskPersistenceError("Risk profile 'profiles' must be an object.")
        raw_mode = document.get("last_selected_mode", "DRY_RUN")
        document["last_selected_mode"] = normalize_risk_mode(str(raw_mode))
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
            raise RiskPersistenceError(
                f"Risk profile {normalized} has no checksum."
            )
        if expected_hash != policy.policy_hash:
            raise RiskPersistenceError(
                f"Risk profile {normalized} checksum mismatch."
            )
        account_scope = str(raw.get("account_scope") or "").strip() or None
        source = str(raw.get("source") or "").strip() or None
        return {
            "mode": normalized,
            "policy": policy,
            "policy_hash": policy.policy_hash,
            "updated_at": raw.get("updated_at"),
            "account_scope": account_scope,
            "source": source,
        }

    def require_profile(self, mode: RiskMode) -> dict[str, Any]:
        loaded = self.load_profile(mode)
        if loaded is None:
            raise RiskPersistenceError(
                f"Risk profile {normalize_risk_mode(mode)} is not saved."
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
    ) -> dict[str, Any]:
        normalized = normalize_risk_mode(mode)
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
        }

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
    """Atomic per-account persistence for RiskState with v1 migration."""

    SCHEMA_VERSION = 2

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
        if version not in {1, self.SCHEMA_VERSION}:
            raise RiskPersistenceError(
                f"Unsupported risk state schema {version}; supported schemas "
                f"are 1 and {self.SCHEMA_VERSION}."
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
