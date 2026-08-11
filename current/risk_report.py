from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from trading_robot import __version__
from trading_robot.journal import EventJournal
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_reporting import (
    RiskReportError,
    load_risk_burn_in_report,
    load_risk_dashboard_snapshot,
    write_burn_in_report,
    write_dashboard_snapshot,
)


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _account_id(raw: str | None) -> str:
    value = str(raw or os.getenv("TBANK_SANDBOX_ACCOUNT_ID", "")).strip()
    if not value:
        raise RiskReportError(
            "Sandbox account id is required. Set TBANK_SANDBOX_ACCOUNT_ID "
            "in .env or pass --account-id."
        )
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a read-only Risk Dashboard snapshot and/or a Sandbox "
            "burn-in audit report. The tool never connects to the broker."
        )
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=("snapshot", "burn-in", "all"),
    )
    parser.add_argument("--account-id")
    parser.add_argument("--mode", default="SANDBOX_EXECUTION")
    parser.add_argument("--profile-file", default="risk_profiles.json")
    parser.add_argument("--state-file", default="risk_state.json")
    parser.add_argument("--journal-file", default="trading_events.db")
    parser.add_argument("--output-dir", default="risk_beta_output")
    parser.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Limit burn-in report to the most recent N hours.",
    )
    args = parser.parse_args(argv)

    app_dir = Path(__file__).resolve().parent
    load_dotenv(app_dir / ".env")
    try:
        account_id = _account_id(args.account_id)
        profile_path = _resolve(app_dir, args.profile_file)
        state_path = _resolve(app_dir, args.state_file)
        journal_path = _resolve(app_dir, args.journal_file)
        output_dir = _resolve(app_dir, args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        generated: dict[str, str] = {}

        if args.command in {"snapshot", "all"}:
            snapshot = load_risk_dashboard_snapshot(
                profile_store=RiskProfileStore(profile_path),
                state_store=RiskStateStore(state_path),
                journal=EventJournal(journal_path),
                account_id=account_id,
                mode=args.mode,
                now=datetime.now(timezone.utc),
            )
            snapshot_path = write_dashboard_snapshot(
                snapshot,
                output_dir / "risk_dashboard_snapshot.json",
            )
            generated["snapshot"] = str(snapshot_path)
            print(
                "Risk Dashboard:",
                snapshot.engine_status,
                "round_trip_ready=",
                snapshot.summary.get("round_trip_ready"),
            )

        if args.command in {"burn-in", "all"}:
            report = load_risk_burn_in_report(
                EventJournal(journal_path),
                account_id=account_id,
                hours=args.hours,
                software_version=__version__,
            )
            paths = write_burn_in_report(report, output_dir)
            generated.update(
                {f"burn_in_{key}": str(value) for key, value in paths.items()}
            )
            print(
                "Risk burn-in:",
                report.overall_status,
                "events=",
                report.metrics.get("events_total"),
            )

        print(json.dumps(generated, ensure_ascii=False, indent=2))
        return 0
    except (RiskReportError, OSError, ValueError, TypeError) as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
