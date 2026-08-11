from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.runtime_backup import (
    RuntimeBackupManager,
    format_backup_verification_summary,
)
from trading_robot.state_persistence import atomic_write_json


def _runtime(root: Path) -> None:
    payloads = {
        "strategy_profiles.json": {"version": 2, "profiles": {}},
        "risk_profiles.json": {"version": 1, "profiles": {}},
        "risk_state.json": {"version": 2, "accounts": {}},
        "robot_state.json": {"version": 6, "bots": {}},
        "sandbox_diagnostic_state.json": {"version": 1, "accounts": {}},
    }
    for name, payload in payloads.items():
        atomic_write_json(root / name, payload)
    EventJournal(root / "trading_events.db").record(
        JournalEvent(category="test", event_type="BACKUP_BASELINE")
    )


def test_sqlite_restore_uses_online_backup_and_restores_rows(tmp_path: Path):
    _runtime(tmp_path)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    journal = EventJournal(tmp_path / "trading_events.db")
    journal.record(JournalEvent(category="test", event_type="AFTER_BACKUP"))
    assert journal.count() == 2

    # Keep a reader open to model Windows GUI/OneDrive contention.  Restoring
    # via os.replace would be fragile; SQLite's backup API remains valid.
    reader = sqlite3.connect(tmp_path / "trading_events.db")
    reader.execute("SELECT COUNT(*) FROM events").fetchone()
    try:
        manager.restore_backup(backup, confirmation="RESTORE RUNTIME")
    finally:
        reader.close()

    assert EventJournal(tmp_path / "trading_events.db").count() == 1
    assert list(tmp_path.glob("trading_events.db.pre_restore_*.bak"))


def test_sqlite_member_never_uses_file_replace_path(tmp_path: Path, monkeypatch):
    _runtime(tmp_path)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    backup = manager.create_backup(tmp_path / "runtime.zip")
    EventJournal(tmp_path / "trading_events.db").record(
        JournalEvent(category="test", event_type="CHANGED")
    )
    calls: list[str] = []
    original = manager._replace_with_retry

    def guarded(source: Path, destination: Path) -> None:
        calls.append(destination.name)
        assert destination.name != "trading_events.db"
        original(source, destination)

    monkeypatch.setattr(manager, "_replace_with_retry", guarded)
    manager.restore_backup(backup, confirmation="RESTORE RUNTIME")
    assert "trading_events.db" not in calls


def test_backup_verification_summary_is_operator_readable(tmp_path: Path):
    _runtime(tmp_path)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    backup = manager.create_backup(tmp_path / "runtime.zip")
    result = manager.verify_backup(backup)
    text = format_backup_verification_summary(result)
    assert "VALID — backup можно использовать" in text
    assert "Ошибок: 0; предупреждений: 0" in text
    assert "trading_events.db" in text
    assert "token_included" not in text


def test_sqlite_restore_in_onedrive_style_path_with_spaces(tmp_path: Path):
    runtime = tmp_path / "OneDrive Desktop" / "MOEX Robot" / "runtime"
    runtime.mkdir(parents=True)
    _runtime(runtime)
    manager = RuntimeBackupManager(runtime, app_version="0.3.6")
    backup = manager.create_backup(runtime / "backups" / "runtime backup.zip")
    EventJournal(runtime / "trading_events.db").record(
        JournalEvent(category="test", event_type="AFTER_BACKUP")
    )
    manager.restore_backup(backup, confirmation="RESTORE RUNTIME")
    assert EventJournal(runtime / "trading_events.db").count() == 1
