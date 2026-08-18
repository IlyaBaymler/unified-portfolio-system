from __future__ import annotations

"""Command-line maintenance tool for v3.9.0 runtime operations."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from trading_robot import __version__
from trading_robot.paths import resolve_app_paths
from trading_robot.readiness import ProductionReadinessEvaluator
from trading_robot.runtime_backup import RuntimeBackupManager, RuntimeBackupError
from trading_robot.secret_provider import preferred_secret_provider, probe_secret_provider
from trading_robot.support_bundle import SupportBundleBuilder, SupportBundleError


def _paths():
    paths = resolve_app_paths(__file__)
    paths.ensure_directories()
    return paths


def _json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="v3.9.0 runtime maintenance tool")
    sub = parser.add_subparsers(dest="command", required=True)

    readiness = sub.add_parser("readiness", help="Evaluate Sandbox production-readiness gate.")
    readiness.add_argument("--account-id", default="")
    readiness.add_argument("--api-authenticated", choices=("yes", "no", "unknown"), default="unknown")
    readiness.add_argument("--api-available", choices=("yes", "no", "unknown"), default="unknown")

    backup = sub.add_parser("backup", help="Create a verified runtime backup.")
    backup.add_argument("--output", default="")

    verify = sub.add_parser("verify-backup", help="Verify a runtime backup ZIP.")
    verify.add_argument("path")

    preview = sub.add_parser("preview-restore", help="Preview runtime restore changes.")
    preview.add_argument("path")

    restore = sub.add_parser("restore", help="Restore a verified runtime backup.")
    restore.add_argument("path")
    restore.add_argument("--confirmation", required=True)

    support = sub.add_parser("support-bundle", help="Create a redacted support bundle.")
    support.add_argument("--output", default="")
    support.add_argument("--account-id", default="")

    return parser.parse_args(argv)


def _tri(value: str) -> bool | None:
    return {"yes": True, "no": False, "unknown": None}[value]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = _paths()
    manager = RuntimeBackupManager(paths.runtime_dir, app_version=__version__)
    try:
        if args.command == "readiness":
            provider = preferred_secret_provider(paths.runtime_dir)
            probe = probe_secret_provider(paths.runtime_dir, provider=provider)
            api_status = {
                "authenticated": _tri(args.api_authenticated),
                "available": _tri(args.api_available),
                **probe.to_dict(),
            }
            report = ProductionReadinessEvaluator(
                paths.runtime_dir,
                app_version=__version__,
                backups_dir=paths.backups_dir,
            ).evaluate(account_id=args.account_id or None, api_status=api_status)
            _json(report.to_dict())
            return 0 if str(report.status) != "BLOCKED" else 2

        if args.command == "backup":
            output = Path(args.output) if args.output else paths.backups_dir / (
                "runtime_backup_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".zip"
            )
            created = manager.create_backup(output)
            _json({"status": "CREATED", "path": str(created), "verification": manager.verify_backup(created).to_dict()})
            return 0

        if args.command == "verify-backup":
            result = manager.verify_backup(args.path)
            _json(result.to_dict())
            return 0 if result.valid else 2

        if args.command == "preview-restore":
            _json([item.to_dict() for item in manager.preview_restore(args.path)])
            return 0

        if args.command == "restore":
            preview = manager.restore_backup(args.path, confirmation=args.confirmation)
            _json({"status": "RESTORED", "items": [item.to_dict() for item in preview]})
            return 0

        if args.command == "support-bundle":
            output = Path(args.output) if args.output else paths.support_dir / (
                "support_bundle_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".zip"
            )
            provider = preferred_secret_provider(paths.runtime_dir)
            token = provider.get("TBANK_SANDBOX_TOKEN") or ""
            result = SupportBundleBuilder(
                paths.runtime_dir,
                app_version=__version__,
            ).build(
                output,
                account_id=args.account_id or None,
                known_secrets=(token,),
            )
            _json(result.to_dict())
            return 0
    except (RuntimeBackupError, SupportBundleError, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
