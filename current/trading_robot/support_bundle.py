from __future__ import annotations

"""Create a redacted support bundle that is safe to share for diagnostics."""

import json
import os
import platform
import re
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import EventJournal
from .logging_setup import redact_sensitive_text
from .portfolio_model import PORTFOLIO_STATE_SCHEMA_VERSION, validate_portfolio_document
from .risk_persistence import RiskProfileStore, RiskStateStore
from .risk_reporting import load_risk_dashboard_snapshot
from .runtime_integrity import inspect_json_file, inspect_sqlite_file, sha256_file

_SECRET_KEY_PARTS = (
    "token",
    "authorization",
    "password",
    "secret",
    "credential",
    "api_key",
    "account_id",
)
_SECRET_LIKE_RE = re.compile(
    r"(?i)(?:Bearer\s+[A-Za-z0-9._~+\-/=]{12,}|"
    r"TBANK_(?:SANDBOX_)?TOKEN\s*[=:]\s*[^\s,;]+)"
)

_RUNTIME_INTEGRITY_JSON_NAMES = (
    "v3_8_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_config_manifest.json",
    "v3_9_enforced_runtime_manifest.json",
    "v3_9_m5_2_runtime_seed_manifest.json",
    "v3_9_external_close_ack_manifest.json",
    "v3_9_shadow_runtime_start_manifest.json",
    "portfolio_risk_metadata.json",
    "strategy_profiles.json",
    "multi_instrument_profiles.json",
    "instrument_runtimes.json",
    "central_order_state.json",
    "risk_profiles.json",
    "risk_state.json",
    "robot_state.json",
    "portfolio_state.json",
    "canonical_migration_report.json",
    "portfolio_legacy_shadow.json",
    "sandbox_diagnostic_state.json",
)

_CHECKSUM_REQUIRED_JSON_NAMES = {
    "v3_8_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_config_manifest.json",
    "v3_9_enforced_runtime_manifest.json",
    "v3_9_m5_2_runtime_seed_manifest.json",
    "v3_9_external_close_ack_manifest.json",
    "v3_9_shadow_runtime_start_manifest.json",
    "portfolio_risk_metadata.json",
    "multi_instrument_profiles.json",
    "instrument_runtimes.json",
    "central_order_state.json",
    "portfolio_state.json",
    "canonical_migration_report.json",
    "portfolio_legacy_shadow.json",
}


def _normalise_known_values(values: Iterable[str]) -> tuple[str, ...]:
    normalised = {
        str(value).strip()
        for value in values
        if value is not None and str(value).strip()
    }
    return tuple(sorted(normalised, key=len, reverse=True))


def _redact_text(text: str, known_values: tuple[str, ...]) -> str:
    redacted = redact_sensitive_text(text)
    for value in known_values:
        redacted = redacted.replace(value, "<REDACTED>")
    return redacted


def redact_object(
    value: Any,
    *,
    known_values: Iterable[str] = (),
) -> Any:
    return _redact_object(value, _normalise_known_values(known_values))


def _redact_object(value: Any, known_values: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = str(raw_key)
            if any(part in key.lower() for part in _SECRET_KEY_PARTS):
                result[key] = "<REDACTED>"
            else:
                result[key] = _redact_object(nested, known_values)
        return result
    if isinstance(value, list):
        return [_redact_object(item, known_values) for item in value]
    if isinstance(value, tuple):
        return [_redact_object(item, known_values) for item in value]
    if isinstance(value, str):
        return _redact_text(value, known_values)
    return value


@dataclass(frozen=True, slots=True)
class SecretScanResult:
    clean: bool
    findings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_text_for_secrets(
    text: str,
    *,
    known_secrets: Iterable[str] = (),
) -> SecretScanResult:
    findings: list[str] = []
    if _SECRET_LIKE_RE.search(text):
        findings.append("secret-like token pattern")
    for secret in known_secrets:
        normalized = str(secret).strip()
        if normalized and normalized in text:
            findings.append("known secret value")
    return SecretScanResult(clean=not findings, findings=tuple(sorted(set(findings))))


@dataclass(frozen=True, slots=True)
class SupportBundleResult:
    path: str
    members: tuple[str, ...]
    secret_scan: SecretScanResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "members": list(self.members),
            "secret_scan": self.secret_scan.to_dict(),
        }


class SupportBundleError(RuntimeError):
    pass


class SupportBundleBuilder:
    def __init__(
        self,
        app_dir: str | Path,
        *,
        app_version: str,
        journal_name: str = "trading_events.db",
    ) -> None:
        self.app_dir = Path(app_dir).resolve()
        self.app_version = str(app_version)
        self.journal_path = self.app_dir / journal_name

    def build(
        self,
        output: str | Path,
        *,
        account_id: str | None = None,
        known_secrets: Iterable[str] = (),
        recent_event_limit: int = 2000,
    ) -> SupportBundleResult:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() != ".zip":
            target = target.with_suffix(".zip")
        canonical_account_id = self._portfolio_account_id()
        explicit_account_id = str(account_id).strip() if account_id else None
        effective_account_id = explicit_account_id or canonical_account_id
        scan_secrets = _normalise_known_values(
            tuple(known_secrets)
            + ((canonical_account_id,) if canonical_account_id else ())
            + ((explicit_account_id,) if explicit_account_id else ())
        )
        with tempfile.TemporaryDirectory(prefix="moex-support-") as temp_name:
            staging = Path(temp_name)
            portfolio_observability = self._portfolio_observability()
            manifest = {
                "format_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "app_version": self.app_version,
                "account_id": (
                    None
                    if not effective_account_id
                    else "<REDACTED_ACCOUNT:" + effective_account_id[-4:] + ">"
                ),
                "account_id_sha256": (
                    __import__("hashlib").sha256(
                        effective_account_id.encode("utf-8")
                    ).hexdigest()
                    if effective_account_id
                    else None
                ),
                "token_included": False,
                "portfolio_observability": portfolio_observability,
                "platform": {
                    "system": platform.system(),
                    "release": platform.release(),
                    "version": platform.version(),
                    "machine": platform.machine(),
                    "python": platform.python_version(),
                },
                "process": {"pid": os.getpid()},
            }
            # The manifest is assembled only from non-sensitive metadata and
            # irreversible/masked account references. A second generic pass
            # would erase those safe references because their keys contain
            # ``account_id``.
            self._write_json(staging / "manifest.json", manifest, redact=False)

            integrity: dict[str, Any] = {}
            for name in _RUNTIME_INTEGRITY_JSON_NAMES:
                if name == "portfolio_state.json":
                    report = inspect_json_file(
                        self.app_dir / name,
                        expected_versions={PORTFOLIO_STATE_SCHEMA_VERSION},
                        validator=validate_portfolio_document,
                        require_checksum=True,
                    )
                else:
                    report = inspect_json_file(
                        self.app_dir / name,
                        require_checksum=name in _CHECKSUM_REQUIRED_JSON_NAMES,
                    )
                integrity[name] = report.to_dict()
                integrity[name]["path"] = name
            journal_integrity = inspect_sqlite_file(self.journal_path).to_dict()
            journal_integrity["path"] = "trading_events.db"
            integrity["trading_events.db"] = journal_integrity
            self._write_json(
                staging / "runtime_integrity.json",
                redact_object(integrity, known_values=scan_secrets),
            )

            for name in ("runtime_bootstrap_report.json",):
                path = self.app_dir / name
                if not path.exists():
                    continue
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                self._write_json(
                    staging / name,
                    redact_object(document, known_values=scan_secrets),
                )

            dashboard_destination = staging / "risk_dashboard_snapshot.json"
            if effective_account_id and self.journal_path.is_file():
                try:
                    dashboard = load_risk_dashboard_snapshot(
                        profile_store=RiskProfileStore(
                            self.app_dir / "risk_profiles.json"
                        ),
                        state_store=RiskStateStore(self.app_dir / "risk_state.json"),
                        journal=EventJournal(self.journal_path, read_only=True),
                        account_id=effective_account_id,
                    ).to_dict()
                except (
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                    sqlite3.Error,
                ) as exc:
                    dashboard = {
                        "status": "UNAVAILABLE",
                        "error_type": type(exc).__name__,
                        "detail": "Risk dashboard generation failed safely.",
                    }
                self._write_json(
                    dashboard_destination,
                    redact_object(dashboard, known_values=scan_secrets),
                )
            elif (self.app_dir / "risk_dashboard_snapshot.json").is_file():
                try:
                    dashboard = json.loads(
                        (self.app_dir / "risk_dashboard_snapshot.json").read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, UnicodeError, json.JSONDecodeError):
                    dashboard = None
                if dashboard is not None:
                    self._write_json(
                        dashboard_destination,
                        redact_object(dashboard, known_values=scan_secrets),
                    )

            for name in ("robot_gui.log", "robot_debug.log"):
                source = self.app_dir / name
                if not source.exists():
                    continue
                try:
                    text = source.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                (staging / name).write_text(
                    _redact_text(text, scan_secrets), encoding="utf-8"
                )

            if self.journal_path.exists():
                journal = EventJournal(self.journal_path, read_only=True)
                events = journal.recent(
                    limit=max(1, int(recent_event_limit)),
                    account_id=effective_account_id,
                )
                self._write_json(
                    staging / "recent_events.json",
                    redact_object(events, known_values=scan_secrets),
                )

            hashes = {
                path.name: sha256_file(path)
                for path in staging.iterdir()
                if path.is_file()
            }
            self._write_json(staging / "sha256_manifest.json", hashes)

            findings: list[str] = []
            for path in staging.iterdir():
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                scan = scan_text_for_secrets(text, known_secrets=scan_secrets)
                findings.extend(f"{path.name}: {item}" for item in scan.findings)
            if findings:
                raise SupportBundleError(
                    "Support bundle failed secret scan: " + "; ".join(findings)
                )

            temporary = target.with_name(target.name + ".tmp")
            temporary.unlink(missing_ok=True)
            members: list[str] = []
            try:
                with zipfile.ZipFile(
                    temporary,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                ) as archive:
                    for path in sorted(staging.iterdir(), key=lambda p: p.name):
                        archive.write(path, arcname=path.name)
                        members.append(path.name)
                temporary.replace(target)
                # Re-open the completed archive and scan every textual member.
                with zipfile.ZipFile(target, "r") as completed:
                    archive_findings: list[str] = []
                    for member in completed.namelist():
                        raw = completed.read(member)
                        decoded = raw.decode("utf-8", errors="replace")
                        scan = scan_text_for_secrets(decoded, known_secrets=scan_secrets)
                        archive_findings.extend(
                            f"{member}: {item}" for item in scan.findings
                        )
                    if archive_findings:
                        target.unlink(missing_ok=True)
                        raise SupportBundleError(
                            "Completed support bundle failed secret scan: "
                            + "; ".join(archive_findings)
                        )
            finally:
                temporary.unlink(missing_ok=True)
        return SupportBundleResult(
            path=str(target),
            members=tuple(members),
            secret_scan=SecretScanResult(clean=True, findings=()),
        )

    def _portfolio_account_id(self) -> str | None:
        path = self.app_dir / "portfolio_state.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(document, Mapping):
            return None
        account = document.get("account")
        if not isinstance(account, Mapping):
            return None
        account_id = str(account.get("account_id") or "").strip()
        return account_id or None

    def _portfolio_observability(self) -> dict[str, Any]:
        path = self.app_dir / "portfolio_state.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {
                "schema_version": None,
                "portfolio_source": None,
                "canonical_blocking": None,
                "compatibility_shadow_status": "DISABLED",
            }
        migration = document.get("migration")
        migration = migration if isinstance(migration, Mapping) else {}
        shadow_status = str(
            migration.get("compatibility_shadow_status") or "DISABLED"
        ).upper()
        if shadow_status == "NOT_CONFIGURED":
            shadow_status = "DISABLED"
        if shadow_status not in {"OK", "DEGRADED", "DISABLED"}:
            shadow_status = "DEGRADED"
        return {
            "schema_version": document.get("version"),
            "portfolio_source": document.get("portfolio_source"),
            "canonical_blocking": bool(document.get("blocking")),
            "compatibility_shadow_status": shadow_status,
        }

    @staticmethod
    def _write_json(path: Path, value: Any, *, redact: bool = True) -> None:
        document = redact_object(value) if redact else value
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
