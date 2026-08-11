from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.runtime_backup import RuntimeBackupError, RuntimeBackupManager
from trading_robot.runtime_integrity import inspect_json_file, inspect_sqlite_file
from trading_robot.state_persistence import StatePersistenceError, atomic_write_json


def create_runtime(root: Path) -> None:
    payloads = {
        "strategy_profiles.json": {"version": 2, "profiles": {}},
        "risk_profiles.json": {"version": 1, "profiles": {}},
        "risk_state.json": {"version": 2, "accounts": {}},
        "robot_state.json": {"version": 5, "bots": {}},
        "sandbox_diagnostic_state.json": {"version": 4, "accounts": {}},
    }
    for name, payload in payloads.items():
        atomic_write_json(root / name, payload)
    journal = EventJournal(root / "trading_events.db")
    journal.record(JournalEvent(category="session", event_type="STARTED"))


def test_atomic_json_creates_last_valid_backup(tmp_path: Path):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"version": 1, "value": "first"})
    result = atomic_write_json(
        target,
        {"version": 1, "value": "second"},
        backup_existing=True,
    )
    backup = tmp_path / "state.json.bak"
    assert result.backup_path == str(backup)
    assert json.loads(backup.read_text(encoding="utf-8"))["value"] == "first"
    assert json.loads(target.read_text(encoding="utf-8"))["value"] == "second"


def test_atomic_json_refuses_to_replace_corrupt_existing_state(tmp_path: Path):
    target = tmp_path / "state.json"
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(StatePersistenceError, match="backup_existing"):
        atomic_write_json(target, {"version": 1}, backup_existing=True)
    assert target.read_text(encoding="utf-8") == "{broken"


def test_journal_integrity_checkpoint_and_backup(tmp_path: Path):
    source = EventJournal(tmp_path / "events.db")
    source.record(JournalEvent(category="test", event_type="A"))
    assert source.integrity_report().valid
    checkpoint = source.checkpoint_wal("PASSIVE")
    assert checkpoint["mode"] == "PASSIVE"
    backup = source.backup_to(tmp_path / "copy.db")
    assert backup.exists()
    assert inspect_sqlite_file(backup).valid
    assert EventJournal(backup).count() == 1


def test_runtime_backup_create_verify_and_preview(tmp_path: Path):
    create_runtime(tmp_path)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    path = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    verification = manager.verify_backup(path)
    assert verification.valid
    assert verification.manifest is not None
    assert verification.manifest["token_included"] is False
    with zipfile.ZipFile(path) as archive:
        assert ".env" not in archive.namelist()
        assert "manifest.json" in archive.namelist()
    preview = manager.preview_restore(path)
    assert preview
    assert all(
        item.action == "UNCHANGED"
        for item in preview
        if item.name != "trading_events.db"
    )
    assert next(item for item in preview if item.name == "trading_events.db").action in {
        "UNCHANGED", "REPLACE"
    }


def test_runtime_backup_restore_requires_confirmation_and_preserves_pre_restore(
    tmp_path: Path,
):
    create_runtime(tmp_path)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    state_path = tmp_path / "robot_state.json"
    state_path.write_text(
        json.dumps({"version": 5, "bots": {"changed": {}}}), encoding="utf-8"
    )
    with pytest.raises(RuntimeBackupError, match="RESTORE RUNTIME"):
        manager.restore_backup(backup, confirmation="yes")
    preview = manager.restore_backup(backup, confirmation="RESTORE RUNTIME")
    assert any(item.name == "robot_state.json" and item.action == "REPLACE" for item in preview)
    restored = json.loads(state_path.read_text(encoding="utf-8"))
    assert restored == {"version": 5, "bots": {}}
    assert list(tmp_path.glob("robot_state.json.pre_restore_*.bak"))


def test_runtime_backup_detects_checksum_tampering(tmp_path: Path):
    create_runtime(tmp_path)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    backup = manager.create_backup(tmp_path / "runtime.zip")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(backup, "r") as source, zipfile.ZipFile(tampered, "w") as out:
        for name in source.namelist():
            data = source.read(name)
            if name == "robot_state.json":
                data += b"x"
            out.writestr(name, data)
    verification = manager.verify_backup(tampered)
    assert not verification.valid
    assert any("Checksum mismatch" in item for item in verification.errors)


def test_runtime_backup_refuses_corrupt_source(tmp_path: Path):
    create_runtime(tmp_path)
    (tmp_path / "risk_state.json").write_text("{broken", encoding="utf-8")
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    with pytest.raises(RuntimeBackupError, match="Cannot back up risk_state.json"):
        manager.create_backup(tmp_path / "runtime.zip")


def test_integrity_inspectors_distinguish_missing_corrupt_and_valid(tmp_path: Path):
    missing = inspect_json_file(tmp_path / "missing.json")
    assert str(missing.status) == "MISSING"
    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    assert str(inspect_json_file(bad).status) == "CORRUPT"
    good = tmp_path / "good.json"
    good.write_text('{"version": 1}', encoding="utf-8")
    assert inspect_json_file(good).valid

    bad_db = tmp_path / "bad.db"
    bad_db.write_bytes(b"not sqlite")
    assert not inspect_sqlite_file(bad_db).valid
