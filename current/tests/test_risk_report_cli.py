from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from risk_report import main
from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore


def test_risk_report_cli_generates_all_outputs(tmp_path: Path):
    account_id = "ACC"
    profile_path = tmp_path / "profiles.json"
    state_path = tmp_path / "state.json"
    journal_path = tmp_path / "events.db"
    output_dir = tmp_path / "out"

    RiskProfileStore(profile_path).save_profile("SANDBOX_EXECUTION", RiskPolicy())
    RiskStateStore(state_path).save_account(
        account_id,
        RiskState(
            daily_date="2026-07-24",
            weekly_key="2026-W30",
            daily_start_equity_rub=50_000.0,
            weekly_start_equity_rub=50_000.0,
            high_watermark_equity_rub=50_000.0,
            last_equity_rub=50_000.0,
            last_cash_rub=50_000.0,
            last_snapshot_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    EventJournal(journal_path).record(
        JournalEvent(
            category="risk",
            event_type="RISK_EVALUATED",
            account_id=account_id,
            payload={
                "risk_decision": {
                    "status": "PASS",
                    "current_lots": 0,
                    "requested_target_lots": 0,
                    "approved_target_lots": 0,
                    "approved_delta_lots": 0,
                    "breaches": [],
                    "reasons": [],
                    "metrics": {"price_per_lot_rub": 250.0},
                }
            },
        )
    )

    result = main(
        [
            "all",
            "--account-id",
            account_id,
            "--profile-file",
            str(profile_path),
            "--state-file",
            str(state_path),
            "--journal-file",
            str(journal_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert result == 0
    assert (output_dir / "risk_dashboard_snapshot.json").exists()
    assert (output_dir / "risk_burn_in_report.json").exists()
    assert (output_dir / "risk_burn_in_checks.csv").exists()
    assert (output_dir / "risk_burn_in_metrics.csv").exists()
