from __future__ import annotations

import json
from pathlib import Path

from rc_tool import main
from trading_robot.journal import EventJournal
from trading_robot.state_persistence import atomic_write_json


def _runtime(root: Path) -> None:
    atomic_write_json(root / "strategy_profiles.json", {"version": 2, "profiles": {}})
    atomic_write_json(root / "risk_profiles.json", {"version": 1, "profiles": {}})
    atomic_write_json(root / "risk_state.json", {"version": 2, "accounts": {}})
    atomic_write_json(root / "robot_state.json", {"version": 6, "bots": {}})
    atomic_write_json(root / "sandbox_diagnostic_state.json", {"version": 4, "accounts": {}})
    EventJournal(root / "trading_events.db").count()


def test_rc_tool_backup_verify_and_support_bundle(tmp_path: Path, capsys):
    _runtime(tmp_path)
    backup = tmp_path / "backups" / "runtime.zip"
    assert main(["--runtime-dir", str(tmp_path), "backup-create", "--output", str(backup)]) == 0
    assert backup.exists()
    assert main(["--runtime-dir", str(tmp_path), "backup-verify", str(backup)]) == 0
    support = tmp_path / "reports" / "support.zip"
    assert main(["--runtime-dir", str(tmp_path), "support-bundle", "--output", str(support)]) == 0
    assert support.exists()
    output = capsys.readouterr().out
    assert "token_included" in output or str(backup) in output


def test_rc_tool_readiness_is_fail_closed_without_account(tmp_path: Path, capsys):
    _runtime(tmp_path)
    (tmp_path / "build_manifest.json").write_text(
        json.dumps({"software_version": "0.3.6"}), encoding="utf-8"
    )
    code = main(["--runtime-dir", str(tmp_path), "readiness"])
    assert code == 2
    assert "BLOCKED" in capsys.readouterr().out
