from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from risk_control_tool import main
from trading_robot.journal import EventJournal
from trading_robot.risk import RiskEngine, RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore


def test_risk_control_tool_engage_and_clear_are_audited(tmp_path: Path):
    state_path = tmp_path / "risk_state.json"
    profile_path = tmp_path / "risk_profiles.json"
    journal_path = tmp_path / "events.db"
    RiskProfileStore(profile_path).save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
    )
    common = [
        "--file",
        str(state_path),
        "--profile-file",
        str(profile_path),
        "--journal-file",
        str(journal_path),
        "--account-id",
        "account-1",
        "--actor",
        "test-operator",
    ]

    assert main(["engage", *common, "--reason", "test halt"]) == 0
    state = RiskStateStore(state_path).load_account("account-1")
    assert state.kill_switch_active is True
    assert state.kill_switch_reason == "test halt"

    assert main(
        [
            "clear",
            *common,
            "--confirmation",
            "CLEAR RISK HALT",
        ]
    ) == 0
    state = RiskStateStore(state_path).load_account("account-1")
    assert state.kill_switch_active is False

    rows = list(
        reversed(
            EventJournal(journal_path).recent(
                limit=10,
                category="risk_control",
            )
        )
    )
    assert [row["event_type"] for row in rows] == [
        "KILL_SWITCH_ENGAGED",
        "KILL_SWITCH_CLEARED",
    ]
    engaged, cleared = rows
    assert engaged["payload"]["actor"] == "test-operator"
    assert engaged["payload"]["source"] == "risk_control_tool"
    assert engaged["payload"]["reason"] == "test halt"
    assert engaged["payload"]["previous_state"]["kill_switch_active"] is False
    assert engaged["payload"]["new_state"]["kill_switch_active"] is True
    assert engaged["config_hash"]
    assert cleared["payload"]["confirmation_validated"] is True
    assert "CLEAR RISK HALT" not in str(cleared["payload"])
    assert cleared["payload"]["previous_state"]["kill_switch_active"] is True
    assert cleared["payload"]["new_state"]["kill_switch_active"] is False


def test_reset_baselines_clears_resync_and_audits_both_events(tmp_path: Path):
    state_path = tmp_path / "risk_state.json"
    profile_path = tmp_path / "risk_profiles.json"
    journal_path = tmp_path / "events.db"
    RiskProfileStore(profile_path).save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
    )
    store = RiskStateStore(state_path)
    marked, _event = RiskEngine(RiskPolicy()).mark_external_activity(
        store.load_account("account-1"),
        now=datetime.now(timezone.utc),
        reason="manual drift",
        source="BROKER_POSITION_DRIFT",
    )
    store.save_account("account-1", marked)

    assert main(
        [
            "reset-baselines",
            "--file",
            str(state_path),
            "--profile-file",
            str(profile_path),
            "--journal-file",
            str(journal_path),
            "--account-id",
            "account-1",
            "--actor",
            "test-operator",
            "--equity-rub",
            "50000",
            "--confirmation",
            "RESET RISK BASELINES",
        ]
    ) == 0

    state = store.load_account("account-1")
    assert state.risk_resync_required is False
    rows = list(
        reversed(
            EventJournal(journal_path).recent(
                limit=10,
                category="risk_control",
            )
        )
    )
    assert [row["event_type"] for row in rows] == [
        "RISK_BASELINES_RESET",
        "RISK_RESYNC_COMPLETED",
    ]
    assert all(row["payload"]["confirmation_validated"] for row in rows)
    assert rows[0]["payload"]["previous_state"]["risk_resync_required"] is True
    assert rows[-1]["payload"]["new_state"]["risk_resync_required"] is False
    assert rows[-1]["payload"]["new_state"]["daily_start_equity_rub"] == 50000
    assert "RESET RISK BASELINES" not in str(rows[-1]["payload"])
