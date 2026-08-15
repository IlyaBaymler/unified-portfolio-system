from __future__ import annotations

"""Read-only M5.3 persistence, support and standalone qualification."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.verify_standalone_layout import verify_layout
from trading_robot.central_order_manager import (
    ACCOUNT_BLOCKING_STATUSES,
    CentralOrderStore,
)
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.portfolio_model import (
    PORTFOLIO_STATE_SCHEMA_VERSION,
    validate_portfolio_document,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.runtime_integrity import (
    inspect_json_file,
    inspect_sqlite_file,
)
from trading_robot.support_bundle import scan_text_for_secrets

_REQUIRED_JSON: dict[str, bool] = {
    "v3_9_enforced_runtime_manifest.json": True,
    "portfolio_risk_metadata.json": True,
    "multi_instrument_profiles.json": True,
    "instrument_runtimes.json": True,
    "central_order_state.json": True,
    "risk_profiles.json": False,
    "risk_state.json": False,
    "portfolio_state.json": True,
}

_REQUIRED_SUPPORT_MEMBERS = {
    "manifest.json",
    "runtime_integrity.json",
    "risk_dashboard_snapshot.json",
    "recent_events.json",
    "sha256_manifest.json",
}

_INSPECTION_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)


def _absolute_without_symlink_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_within(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _validate_output_boundary(args: argparse.Namespace) -> None:
    output = getattr(args, "output", None)
    if output is None:
        return
    output_path = Path(output)
    lexical_output = _absolute_without_symlink_resolution(output_path)
    resolved_output = output_path.resolve(strict=False)
    protected_roots = [Path(args.runtime_dir)]
    standalone_root = getattr(args, "standalone_root", None)
    if standalone_root is not None:
        protected_roots.append(Path(standalone_root))
    for root in protected_roots:
        lexical_root = _absolute_without_symlink_resolution(root)
        resolved_root = root.resolve(strict=False)
        if _is_within(lexical_output, lexical_root) or _is_within(
            resolved_output,
            resolved_root,
        ):
            raise ValueError(
                "--output must be outside all qualified runtime/package roots."
            )


def _account_reference(account_id: str) -> dict[str, str]:
    normalized = str(account_id or "").strip()
    if not normalized:
        raise ValueError("account_id must not be empty.")
    return {
        "masked": f"<REDACTED_ACCOUNT:{normalized[-4:]}>",
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _safety_fields() -> dict[str, bool]:
    return {
        "writes_performed": False,
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "dispatch_authorized": False,
        "resubmit_authorized": False,
        "provider_post_authorized": False,
    }


def _safe_failure(prefix: str, error: Exception, account_id: str) -> str:
    # Runtime exceptions can contain an account discovered from persisted
    # state, which is not necessarily the caller-supplied account_id. Keep
    # evidence diagnostic without serializing arbitrary exception text.
    del account_id
    return prefix + ":" + type(error).__name__


def _redact_account(value: Any, account_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_account(nested, account_id)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_account(item, account_id) for item in value]
    if isinstance(value, tuple):
        return [_redact_account(item, account_id) for item in value]
    if isinstance(value, str) and account_id:
        return value.replace(account_id, f"<REDACTED_ACCOUNT:{account_id[-4:]}>")
    return value


def _runtime_report(root: Path, account_id: str) -> tuple[dict[str, Any], list[str]]:
    integrity: dict[str, Any] = {}
    failures: list[str] = []
    for name, require_checksum in _REQUIRED_JSON.items():
        path = root / name
        if name == "portfolio_state.json":
            report = inspect_json_file(
                path,
                expected_versions={PORTFOLIO_STATE_SCHEMA_VERSION},
                validator=validate_portfolio_document,
                require_checksum=require_checksum,
            )
        else:
            report = inspect_json_file(path, require_checksum=require_checksum)
        integrity[name] = report.to_dict()
        if not report.valid:
            failures.append(f"INTEGRITY_{name}:{report.status}")

    journal_path = root / "trading_events.db"
    journal_sidecars: dict[str, int | None] = {}
    for suffix in ("-wal", "-shm"):
        sidecar = journal_path.with_name(journal_path.name + suffix)
        try:
            journal_sidecars[suffix] = (
                sidecar.stat().st_size if sidecar.exists() else None
            )
        except OSError:
            journal_sidecars[suffix] = None
    if (journal_sidecars.get("-wal") or 0) > 0:
        failures.append("EVENT_JOURNAL_WAL_NOT_EMPTY")
        integrity["trading_events.db"] = {
            "path": str(journal_path),
            "status": "WAL_NOT_EMPTY",
            "kind": "sqlite",
            "size_bytes": journal_path.stat().st_size,
            "sha256": None,
            "schema_version": None,
            "detail": "Checkpoint the stopped EventJournal before final review.",
            "valid": False,
        }
        journal_integrity_valid = False
    else:
        journal_integrity = inspect_sqlite_file(journal_path)
        integrity["trading_events.db"] = journal_integrity.to_dict()
        journal_integrity_valid = journal_integrity.valid
        if not journal_integrity.valid:
            failures.append(
                f"INTEGRITY_trading_events.db:{journal_integrity.status}"
            )

    runtime_summary: dict[str, Any] = {
        "integrity": integrity,
        "portfolio": None,
        "central": None,
        "instrument_runtimes": None,
        "risk": None,
        "journal": {"sidecar_sizes": journal_sidecars},
    }
    try:
        portfolio = PortfolioRepository(root / "portfolio_state.json").load(
            expected_account_id=account_id
        )
        runtime_summary["portfolio"] = {
            "revision": portfolio.revision,
            "freshness": str(portfolio.freshness),
            "state_status": portfolio.state_status,
            "blocking": portfolio.blocking,
            "position_count": len(portfolio.positions),
        }
        if portfolio.blocking:
            failures.append("CANONICAL_PORTFOLIO_BLOCKING")
    except _INSPECTION_ERRORS as exc:
        failures.append(
            _safe_failure("CANONICAL_PORTFOLIO_UNAVAILABLE", exc, account_id)
        )

    try:
        central = CentralOrderStore(root / "central_order_state.json").load(
            expected_account_id=account_id
        )
        blockers = [
            item.intent_id
            for item in central.intents
            if item.status in ACCOUNT_BLOCKING_STATUSES
        ]
        runtime_summary["central"] = {
            "revision": central.revision,
            "intent_count": len(central.intents),
            "blocking_intent_count": len(blockers),
            "queued_count": len(central.queued),
            "reserved_cash_kopecks": central.reserved_cash_kopecks,
        }
        if blockers or central.queued or central.reserved_cash_kopecks:
            failures.append("CENTRAL_ORDER_STATE_NOT_QUIESCENT")
    except _INSPECTION_ERRORS as exc:
        failures.append(
            _safe_failure("CENTRAL_ORDER_STATE_UNAVAILABLE", exc, account_id)
        )

    try:
        runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
            expected_account_id=account_id
        )
        statuses: dict[str, int] = {}
        pending_count = 0
        for runtime in runtimes:
            statuses[runtime.status] = statuses.get(runtime.status, 0) + 1
            pending_count += len(runtime.pending_order_ids)
        runtime_summary["instrument_runtimes"] = {
            "count": len(runtimes),
            "statuses": statuses,
            "pending_order_count": pending_count,
        }
        if any(item.status != "STOPPED" for item in runtimes) or pending_count:
            failures.append("INSTRUMENT_RUNTIMES_NOT_STOPPED")
    except _INSPECTION_ERRORS as exc:
        failures.append(
            _safe_failure("INSTRUMENT_RUNTIMES_UNAVAILABLE", exc, account_id)
        )

    try:
        profile = RiskProfileStore(root / "risk_profiles.json").require_profile(
            "SANDBOX_EXECUTION"
        )
        scope = str(profile.get("account_scope") or "").strip()
        state = RiskStateStore(root / "risk_state.json").load_account(account_id)
        runtime_summary["risk"] = {
            "policy_status": profile["portfolio_policy_status"],
            "policy_mode": profile["policy"].portfolio_policy_mode,
            "policy_hash": profile["policy_hash"],
            "account_scope_matches": scope == account_id,
            "global_kill_switch_active": state.kill_switch_active,
            "instrument_kill_switch_count": len(state.instrument_kill_switches),
            "risk_resync_required": state.risk_resync_required,
            "risk_resync_source": state.risk_resync_source,
        }
        if (
            profile["portfolio_policy_status"] != "READY"
            or profile["policy"].portfolio_policy_mode != "ENFORCED"
            or scope != account_id
        ):
            failures.append("PORTFOLIO_RISK_POLICY_NOT_ENFORCED")
        if state.kill_switch_active or state.instrument_kill_switches:
            failures.append("RISK_KILL_SWITCH_ACTIVE")
        if state.risk_resync_required:
            failures.append("RISK_RESYNC_REQUIRED")
    except _INSPECTION_ERRORS as exc:
        failures.append(
            _safe_failure("PORTFOLIO_RISK_STATE_UNAVAILABLE", exc, account_id)
        )

    try:
        metadata = load_portfolio_risk_metadata(root / "portfolio_risk_metadata.json")
        runtime_summary["portfolio_risk_metadata_count"] = len(metadata)
    except _INSPECTION_ERRORS as exc:
        failures.append(
            _safe_failure(
                "PORTFOLIO_RISK_METADATA_UNAVAILABLE",
                exc,
                account_id,
            )
        )

    if journal_integrity_valid:
        try:
            journal = EventJournal(journal_path, read_only=True)
            runtime_summary["journal"] = {
                "event_count": journal.count(account_id=account_id),
                "categories": journal.grouped_counts("category"),
                "sidecar_sizes": journal_sidecars,
            }
        except _INSPECTION_ERRORS as exc:
            failures.append(_safe_failure("EVENT_JOURNAL_UNAVAILABLE", exc, account_id))
    return runtime_summary, failures


def _backup_report(
    root: Path,
    backup_path: Path,
    *,
    app_version: str,
) -> tuple[dict[str, Any], list[str]]:
    verification = RuntimeBackupManager(
        root,
        app_version=app_version,
    ).verify_backup(backup_path)
    failures: list[str] = []
    entries = (
        verification.manifest.get("entries", [])
        if isinstance(verification.manifest, dict)
        else []
    )
    names = {str(item.get("name") or "") for item in entries if isinstance(item, dict)}
    missing = sorted((set(_REQUIRED_JSON) | {"trading_events.db"}) - names)
    observed_app_version = (
        str(verification.manifest.get("app_version") or "")
        if isinstance(verification.manifest, dict)
        else ""
    )
    app_version_matches = observed_app_version == app_version
    if not verification.valid:
        failures.append("BACKUP_INVALID")
    if missing:
        failures.append("BACKUP_REQUIRED_MEMBERS_MISSING")
    if not app_version_matches:
        failures.append("BACKUP_APP_VERSION_MISMATCH")
    return {
        "path": str(backup_path.resolve()),
        "valid": verification.valid and app_version_matches,
        "errors": list(verification.errors),
        "warnings": list(verification.warnings),
        "entry_count": len(entries),
        "missing_required_members": missing,
        "expected_app_version": app_version,
        "observed_app_version": observed_app_version,
        "app_version_matches": app_version_matches,
    }, failures


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
    )


def _support_report(
    support_path: Path,
    *,
    account_id: str,
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    members: list[str] = []
    hash_mismatches: list[str] = []
    hash_coverage_errors: list[str] = []
    account_leaks: list[str] = []
    secret_findings: list[str] = []
    integrity_failures: list[str] = []
    missing: list[str] = []
    try:
        with zipfile.ZipFile(support_path, "r") as archive:
            members = archive.namelist()
            if len(members) != len(set(members)) or any(
                not _safe_archive_name(name) for name in members
            ):
                failures.append("SUPPORT_BUNDLE_UNSAFE_MEMBERS")
            missing = sorted(_REQUIRED_SUPPORT_MEMBERS - set(members))
            if missing:
                failures.append("SUPPORT_BUNDLE_REQUIRED_MEMBERS_MISSING")
            hashes = json.loads(archive.read("sha256_manifest.json").decode("utf-8"))
            if not isinstance(hashes, dict):
                raise TypeError("Support checksum manifest root is invalid.")
            expected_hashed_members = set(members) - {"sha256_manifest.json"}
            hash_names = {str(name) for name in hashes}
            for name in sorted(expected_hashed_members - hash_names):
                hash_coverage_errors.append(f"UNHASHED:{name}")
            for name in sorted(hash_names - expected_hashed_members):
                hash_coverage_errors.append(f"UNEXPECTED_HASH:{name}")
            for name, expected in hashes.items():
                if name not in members:
                    hash_mismatches.append(str(name))
                    continue
                actual = hashlib.sha256(archive.read(name)).hexdigest()
                if actual != str(expected):
                    hash_mismatches.append(str(name))
            for name in members:
                raw = archive.read(name)
                if account_id.encode("utf-8") in raw:
                    account_leaks.append(name)
                decoded = raw.decode("utf-8", errors="replace")
                scan = scan_text_for_secrets(decoded)
                secret_findings.extend(f"{name}:{finding}" for finding in scan.findings)
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            expected_mask = f"<REDACTED_ACCOUNT:{account_id[-4:]}>"
            expected_hash = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
            if (
                not isinstance(manifest, dict)
                or manifest.get("token_included") is not False
                or manifest.get("account_id") != expected_mask
                or manifest.get("account_id_sha256") != expected_hash
            ):
                failures.append("SUPPORT_BUNDLE_MANIFEST_INVALID")
            runtime_integrity = json.loads(
                archive.read("runtime_integrity.json").decode("utf-8")
            )
            required_integrity = set(_REQUIRED_JSON) | {"trading_events.db"}
            if not isinstance(runtime_integrity, dict):
                failures.append("SUPPORT_BUNDLE_INTEGRITY_INVALID")
            else:
                for name in sorted(required_integrity):
                    raw_report = runtime_integrity.get(name)
                    if (
                        not isinstance(raw_report, dict)
                        or raw_report.get("valid") is not True
                    ):
                        integrity_failures.append(name)
                for name, raw_report in runtime_integrity.items():
                    if (
                        name not in required_integrity
                        and isinstance(raw_report, dict)
                        and raw_report.get("status") != "MISSING"
                        and raw_report.get("valid") is not True
                    ):
                        integrity_failures.append(str(name))
            if hash_mismatches:
                failures.append("SUPPORT_BUNDLE_CHECKSUM_MISMATCH")
            if hash_coverage_errors:
                failures.append("SUPPORT_BUNDLE_CHECKSUM_COVERAGE_INVALID")
            if account_leaks:
                failures.append("SUPPORT_BUNDLE_ACCOUNT_ID_LEAK")
            if secret_findings:
                failures.append("SUPPORT_BUNDLE_SECRET_PATTERN")
            if integrity_failures:
                failures.append("SUPPORT_BUNDLE_RUNTIME_INTEGRITY_FAILED")
    except (
        OSError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        failures.append(_safe_failure("SUPPORT_BUNDLE_INVALID", exc, account_id))
    return {
        "path": str(support_path.resolve()),
        "member_count": len(members),
        "missing_required_members": missing,
        "checksum_mismatches": sorted(hash_mismatches),
        "checksum_coverage_errors": sorted(hash_coverage_errors),
        "account_id_leaks": sorted(account_leaks),
        "secret_findings": sorted(secret_findings),
        "runtime_integrity_failures": sorted(integrity_failures),
    }, failures


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.runtime_dir.resolve()
    account_id = str(args.account_id or "").strip()
    account = _account_reference(account_id)
    runtime, failures = _runtime_report(root, account_id)
    report: dict[str, Any] = {
        "version": "v3.9-beta1-m5.3",
        "action": args.action,
        "status": "PASS",
        "account": account,
        "runtime": runtime,
        "backup": None,
        "support_bundle": None,
        "standalone": None,
        **_safety_fields(),
    }
    if args.backup is not None:
        backup, backup_failures = _backup_report(
            root,
            args.backup,
            app_version=args.app_version,
        )
        report["backup"] = backup
        failures.extend(backup_failures)
    if args.support_bundle is not None:
        support, support_failures = _support_report(
            args.support_bundle,
            account_id=account_id,
        )
        report["support_bundle"] = support
        failures.extend(support_failures)
    if args.standalone_root is not None:
        standalone_errors = verify_layout(
            args.standalone_root,
            expected_version=args.expected_version,
            expected_channel=args.expected_channel,
            minimum_risk_state_schema=args.minimum_risk_state_schema,
            allow_structural_fixture=args.allow_structural_fixture,
        )
        manifest_path = args.standalone_root / "app" / "build_manifest.json"
        try:
            standalone_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            standalone_manifest = {}
        artifact_kind = (
            str(standalone_manifest.get("artifact_kind") or "EXECUTABLE")
            if isinstance(standalone_manifest, dict)
            else "UNKNOWN"
        )
        report["standalone"] = {
            "root": str(args.standalone_root.resolve()),
            "valid": not standalone_errors,
            "errors": standalone_errors,
            "artifact_kind": artifact_kind,
            "structural_only": artifact_kind == "STRUCTURAL_LAYOUT_FIXTURE",
            "executable_launch_verified": False,
        }
        if standalone_errors:
            failures.append("STANDALONE_LAYOUT_INVALID")
    if args.action == "final-review" and any(
        value is None
        for value in (args.backup, args.support_bundle, args.standalone_root)
    ):
        failures.append("FINAL_REVIEW_ARTIFACTS_INCOMPLETE")
    report["failures"] = sorted(set(failures))
    if failures:
        report["status"] = "FAIL"
    return _redact_account(report, account_id)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only v3.9 M5.3 persistence and standalone qualification. "
            "The tool cannot restore state, create intents or call a provider."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("inspect", "final-review"):
        command = subparsers.add_parser(action)
        command.add_argument("--runtime-dir", type=Path, required=True)
        command.add_argument("--account-id", required=True)
        command.add_argument("--backup", type=Path)
        command.add_argument("--support-bundle", type=Path)
        command.add_argument("--standalone-root", type=Path)
        command.add_argument("--app-version", default="0.3.9b1")
        command.add_argument("--expected-version", default="0.3.9b1")
        command.add_argument("--expected-channel", default="beta")
        command.add_argument("--minimum-risk-state-schema", type=int, default=4)
        command.add_argument("--allow-structural-fixture", action="store_true")
        command.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _redacted_error(args: argparse.Namespace, error: Exception) -> dict[str, Any]:
    account_id = str(getattr(args, "account_id", "") or "").strip()
    account = (
        _account_reference(account_id)
        if account_id
        else {"masked": "<REDACTED_ACCOUNT>", "sha256": None}
    )
    return {
        "version": "v3.9-beta1-m5.3",
        "action": getattr(args, "action", None),
        "status": "ERROR",
        "account": account,
        "error": {
            "type": type(error).__name__,
            "message": "Qualification failed safely before completion.",
        },
        **_safety_fields(),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _validate_output_boundary(args)
        payload = run(args)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0 if payload["status"] == "PASS" else 2
    except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        print(
            json.dumps(
                _redacted_error(args, exc),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
