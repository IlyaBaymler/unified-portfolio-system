from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from trading_robot import __version__
from trading_robot.paths import resolve_app_paths
from trading_robot.readiness import ProductionReadinessEvaluator
from trading_robot.runtime_backup import RuntimeBackupError, RuntimeBackupManager
from trading_robot.support_bundle import SupportBundleBuilder, SupportBundleError


def runtime_dir() -> Path:
    configured = os.getenv("MOEX_ROBOT_RUNTIME_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path.cwd()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Sandbox readiness and recovery tool")
    root.add_argument("--runtime-dir", default=None)
    commands = root.add_subparsers(dest="command", required=True)

    readiness = commands.add_parser("readiness")
    readiness.add_argument("--account-id", default=None)
    readiness.add_argument("--api-available", action="store_true")

    backup = commands.add_parser("backup-create")
    backup.add_argument("--output", default=None)

    verify = commands.add_parser("backup-verify")
    verify.add_argument("path")

    preview = commands.add_parser("restore-preview")
    preview.add_argument("path")

    restore = commands.add_parser("restore")
    restore.add_argument("path")
    restore.add_argument("--confirmation", required=True)

    isolated = commands.add_parser("restore-isolated")
    isolated.add_argument("path")
    isolated.add_argument("destination")

    support = commands.add_parser("support-bundle")
    support.add_argument("--output", default=None)
    support.add_argument("--account-id", default=None)

    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = Path(args.runtime_dir).resolve() if args.runtime_dir else runtime_dir()
    root.mkdir(parents=True, exist_ok=True)
    manager = RuntimeBackupManager(root, app_version=__version__)

    try:
        if args.command == "readiness":
            app_paths = resolve_app_paths(__file__)
            local_manifest = root / "build_manifest.json"
            report = ProductionReadinessEvaluator(
                root,
                app_version=__version__,
                backups_dir=root / "backups",
                build_manifest_path=(
                    local_manifest
                    if local_manifest.exists()
                    else app_paths.app_dir / "build_manifest.json"
                ),
            ).evaluate(
                account_id=args.account_id,
                api_status=(
                    {"authenticated": True, "available": True}
                    if args.api_available
                    else None
                ),
            )
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            return 0 if str(report.status) != "BLOCKED" else 2

        if args.command == "backup-create":
            output = args.output or str(
                root
                / "backups"
                / datetime.now(timezone.utc).strftime(
                    "runtime_backup_%Y%m%d_%H%M%S.zip"
                )
            )
            path = manager.create_backup(output)
            print(path)
            return 0

        if args.command == "backup-verify":
            result = manager.verify_backup(args.path)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0 if result.valid else 2

        if args.command == "restore-preview":
            items = manager.preview_restore(args.path)
            print(
                json.dumps(
                    [item.to_dict() for item in items],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "restore":
            items = manager.restore_backup(
                args.path,
                confirmation=args.confirmation,
            )
            print(
                json.dumps(
                    [item.to_dict() for item in items],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "restore-isolated":
            destination = manager.restore_backup_isolated(
                args.path,
                args.destination,
            )
            print(json.dumps({"status": "RESTORED_ISOLATED", "path": str(destination)}))
            return 0

        if args.command == "support-bundle":
            output = args.output or str(
                root
                / "reports"
                / datetime.now(timezone.utc).strftime(
                    "support_bundle_%Y%m%d_%H%M%S.zip"
                )
            )
            result = SupportBundleBuilder(
                root,
                app_version=__version__,
            ).build(output, account_id=args.account_id)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0
    except (RuntimeBackupError, SupportBundleError, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
