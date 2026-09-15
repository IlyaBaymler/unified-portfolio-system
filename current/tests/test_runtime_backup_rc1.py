from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.runtime_backup import RuntimeBackupError, RuntimeBackupManager
from trading_robot.runtime_cash_authority import (
    RuntimeCashAuthorityManager,
    RuntimeCashAuthorityState,
    RuntimeCashAuthorityStore,
)
from trading_robot.runtime_integrity import (
    inspect_json_file,
    inspect_runtime_cash_authority,
    inspect_sqlite_file,
)
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
    assert next(
        item for item in preview if item.name == "trading_events.db"
    ).action in {"UNCHANGED", "REPLACE"}


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
    assert any(
        item.name == "robot_state.json" and item.action == "REPLACE" for item in preview
    )
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


def test_runtime_backup_rejects_manifest_consistent_corrupt_member(tmp_path: Path):
    create_runtime(tmp_path)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    backup = manager.create_backup(tmp_path / "runtime.zip")
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(backup, "r") as source:
        manifest = json.loads(source.read("manifest.json").decode("utf-8"))
        members = {name: source.read(name) for name in source.namelist()}
    members["robot_state.json"] = b"{broken"
    entry = next(
        item for item in manifest["entries"] if item["name"] == "robot_state.json"
    )
    entry["size_bytes"] = len(members["robot_state.json"])
    entry["sha256"] = hashlib.sha256(members["robot_state.json"]).hexdigest()
    members["manifest.json"] = json.dumps(manifest).encode("utf-8")
    with zipfile.ZipFile(corrupt, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)

    verification = manager.verify_backup(corrupt)

    assert not verification.valid
    assert any("content validation" in item for item in verification.errors)


def test_runtime_backup_refuses_corrupt_source(tmp_path: Path):
    create_runtime(tmp_path)
    (tmp_path / "risk_state.json").write_text("{broken", encoding="utf-8")
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.6")
    with pytest.raises(RuntimeBackupError, match="Cannot back up risk_state.json"):
        manager.create_backup(tmp_path / "runtime.zip")


def test_runtime_backup_refuses_checksum_mismatch_in_managed_v3_9_source(
    tmp_path: Path,
):
    create_runtime(tmp_path)
    manifest = tmp_path / "v3_9_enforced_runtime_manifest.json"
    atomic_write_json(
        manifest,
        {"version": 1, "activation_status": "ACTIVE"},
        write_checksum=True,
    )
    manifest.write_text(
        json.dumps({"version": 1, "activation_status": "TAMPERED"}),
        encoding="utf-8",
    )

    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    with pytest.raises(RuntimeBackupError, match="CHECKSUM_MISMATCH"):
        manager.create_backup(tmp_path / "runtime.zip")


def test_runtime_backup_preserves_m5_2_seed_manifest(tmp_path: Path):
    create_runtime(tmp_path)
    seed = tmp_path / "v3_9_m5_2_runtime_seed_manifest.json"
    atomic_write_json(
        seed,
        {"version": 1, "account_fingerprint": "sha256:test"},
        write_checksum=True,
    )

    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    backup = manager.create_backup(tmp_path / "runtime.zip")
    verification = manager.verify_backup(backup)

    assert verification.valid
    assert verification.manifest is not None
    assert "v3_9_m5_2_runtime_seed_manifest.json" in {
        entry["name"] for entry in verification.manifest["entries"]
    }


def test_restore_refreshes_checksum_and_lastgood_to_restored_state(
    tmp_path: Path,
):
    create_runtime(tmp_path)
    state = tmp_path / "multi_instrument_profiles.json"
    atomic_write_json(
        state,
        {"version": 1, "marker": "backup"},
        write_checksum=True,
    )
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    atomic_write_json(
        state,
        {"version": 1, "marker": "intermediate"},
        write_checksum=True,
        keep_last_good=True,
    )
    atomic_write_json(
        state,
        {"version": 1, "marker": "current"},
        write_checksum=True,
        keep_last_good=True,
    )

    manager.restore_backup(backup, confirmation="RESTORE RUNTIME")

    lastgood = state.with_name(state.name + ".lastgood")
    assert json.loads(state.read_text(encoding="utf-8"))["marker"] == "backup"
    assert lastgood.read_bytes() == state.read_bytes()
    assert inspect_json_file(state, require_checksum=True).valid
    assert inspect_json_file(lastgood, require_checksum=True).valid


def test_restore_repairs_missing_lastgood_for_unchanged_managed_entry(
    tmp_path: Path,
):
    create_runtime(tmp_path)
    state = tmp_path / "multi_instrument_profiles.json"
    atomic_write_json(
        state,
        {"version": 1, "marker": "accepted"},
        write_checksum=True,
    )
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    original = state.read_bytes()

    preview = manager.restore_backup(backup, confirmation="RESTORE RUNTIME")

    item = next(item for item in preview if item.name == state.name)
    lastgood = state.with_name(state.name + ".lastgood")
    assert item.action == "UNCHANGED"
    assert state.read_bytes() == original
    assert lastgood.read_bytes() == original
    assert inspect_json_file(state, require_checksum=True).valid
    assert inspect_json_file(lastgood, require_checksum=True).valid
    assert not list(tmp_path.glob(f"{state.name}.pre_restore_*.bak"))


def test_restore_revision_zero_authority_removes_newer_lastgood(
    tmp_path: Path,
):
    create_runtime(tmp_path)
    store = RuntimeCashAuthorityStore(tmp_path)
    initial = store.bootstrap(transition_at="2026-09-13T00:00:00.000000000Z")
    backup_manager = RuntimeBackupManager(tmp_path, app_version="0.3.10")
    backup = backup_manager.create_backup(tmp_path / "backups" / "b0.zip")
    authority_manager = RuntimeCashAuthorityManager(store)
    with store.locked():
        prepared = authority_manager._change(
            store._load_unlocked(allow_missing_legacy=False),
            at="2026-09-13T00:00:01.000000000Z",
            kind="PREPARE_CUTOVER",
            state=RuntimeCashAuthorityState.CUTOVER_PREPARED,
            cutover_generation=1,
            account_scope_sha256="1" * 64,
            identity_key_id="TEST_RESTORE_KEY_V1",
        )
        store._commit_unlocked(
            prepared,
            expected_revision=initial.record_revision,
            expected_sha256=initial.sha256,
        )
    cancelled = authority_manager.cancel(transition_at="2026-09-13T00:00:02.000000000Z")
    assert cancelled.record_revision == 2
    assert store.lastgood_path.exists()

    backup_manager.restore_backup(backup, confirmation="RESTORE RUNTIME")

    restored = store.load(allow_missing_legacy=False)
    assert restored.canonical_bytes == initial.canonical_bytes
    assert not store.lastgood_path.exists()
    assert inspect_runtime_cash_authority(tmp_path).valid


def test_failed_revision_zero_restore_rolls_back_newer_authority_custody(
    tmp_path: Path,
    monkeypatch,
):
    create_runtime(tmp_path)
    store = RuntimeCashAuthorityStore(tmp_path)
    initial = store.bootstrap(transition_at="2026-09-13T00:00:00.000000000Z")
    backup_manager = RuntimeBackupManager(tmp_path, app_version="0.3.10")
    backup = backup_manager.create_backup(tmp_path / "backups" / "b0.zip")
    authority_manager = RuntimeCashAuthorityManager(store)
    with store.locked():
        prepared = authority_manager._change(
            store._load_unlocked(allow_missing_legacy=False),
            at="2026-09-13T00:00:01.000000000Z",
            kind="PREPARE_CUTOVER",
            state=RuntimeCashAuthorityState.CUTOVER_PREPARED,
            cutover_generation=1,
            account_scope_sha256="1" * 64,
            identity_key_id="TEST_RESTORE_KEY_V1",
        )
        store._commit_unlocked(
            prepared,
            expected_revision=initial.record_revision,
            expected_sha256=initial.sha256,
        )
    cancelled = authority_manager.cancel(transition_at="2026-09-13T00:00:02.000000000Z")
    before = {
        path.name: path.read_bytes()
        for path in (store.path, store.checksum_path, store.lastgood_path)
    }
    risk_state = tmp_path / "risk_state.json"
    risk_state.write_text(
        json.dumps({"version": 2, "accounts": {"changed": {}}}),
        encoding="utf-8",
    )
    inspect_member = backup_manager._inspect_member

    def fail_late_postcondition(path: Path, name: str):
        if path == risk_state:
            raise RuntimeBackupError("synthetic late postcondition failure")
        return inspect_member(path, name)

    monkeypatch.setattr(backup_manager, "_inspect_member", fail_late_postcondition)

    with pytest.raises(RuntimeBackupError, match="late postcondition failure"):
        backup_manager.restore_backup(backup, confirmation="RESTORE RUNTIME")

    restored = store.load(allow_missing_legacy=False)
    assert restored.canonical_bytes == cancelled.canonical_bytes
    assert before == {
        path.name: path.read_bytes()
        for path in (store.path, store.checksum_path, store.lastgood_path)
    }
    assert inspect_runtime_cash_authority(tmp_path).valid


def test_failed_unchanged_recovery_maintenance_rolls_back_companions(
    tmp_path: Path,
    monkeypatch,
):
    create_runtime(tmp_path)
    state = tmp_path / "multi_instrument_profiles.json"
    atomic_write_json(
        state,
        {"version": 1, "marker": "accepted"},
        write_checksum=True,
    )
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    checksum = state.with_name(state.name + ".sha256")
    original = {state.name: state.read_bytes(), checksum.name: checksum.read_bytes()}
    refresh_recovery_files = manager._refresh_json_recovery_files

    def fail_recovery_refresh(destination: Path) -> None:
        refresh_recovery_files(destination)
        if destination == state:
            raise RuntimeBackupError("synthetic unchanged recovery failure")

    monkeypatch.setattr(
        manager,
        "_refresh_json_recovery_files",
        fail_recovery_refresh,
    )

    with pytest.raises(RuntimeBackupError, match="unchanged recovery failure"):
        manager.restore_backup(backup, confirmation="RESTORE RUNTIME")

    assert {
        state.name: state.read_bytes(),
        checksum.name: checksum.read_bytes(),
    } == original
    assert not state.with_name(state.name + ".lastgood").exists()
    assert not state.with_name(state.name + ".lastgood.sha256").exists()
    assert not list(tmp_path.glob(f"{state.name}.pre_restore_*.bak"))


def test_failed_restore_preserves_original_checksum_and_lastgood(
    tmp_path: Path,
    monkeypatch,
):
    create_runtime(tmp_path)
    state = tmp_path / "multi_instrument_profiles.json"
    atomic_write_json(
        state,
        {"version": 1, "marker": "backup"},
        write_checksum=True,
    )
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    atomic_write_json(
        state,
        {"version": 1, "marker": "intermediate"},
        write_checksum=True,
        keep_last_good=True,
    )
    atomic_write_json(
        state,
        {"version": 1, "marker": "current"},
        write_checksum=True,
        keep_last_good=True,
    )
    original = {
        path.name: path.read_bytes()
        for path in (
            state,
            state.with_name(state.name + ".sha256"),
            state.with_name(state.name + ".lastgood"),
            state.with_name(state.name + ".lastgood.sha256"),
        )
    }
    refresh_recovery_files = manager._refresh_json_recovery_files

    def fail_recovery_refresh(destination: Path) -> None:
        refresh_recovery_files(destination)
        if destination == state:
            raise RuntimeBackupError("synthetic post-commit failure")

    monkeypatch.setattr(
        manager,
        "_refresh_json_recovery_files",
        fail_recovery_refresh,
    )

    with pytest.raises(RuntimeBackupError, match="synthetic post-commit failure"):
        manager.restore_backup(backup, confirmation="RESTORE RUNTIME")

    assert {
        path.name: path.read_bytes()
        for path in (
            state,
            state.with_name(state.name + ".sha256"),
            state.with_name(state.name + ".lastgood"),
            state.with_name(state.name + ".lastgood.sha256"),
        )
    } == original


def test_failed_restore_preserves_orphan_recovery_companions(
    tmp_path: Path,
    monkeypatch,
):
    create_runtime(tmp_path)
    state = tmp_path / "multi_instrument_profiles.json"
    atomic_write_json(
        state,
        {"version": 1, "marker": "backup"},
        write_checksum=True,
    )
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    state.unlink()
    companions = (
        state.with_name(state.name + ".sha256"),
        state.with_name(state.name + ".lastgood"),
        state.with_name(state.name + ".lastgood.sha256"),
    )
    companions[0].write_bytes(b"orphan-primary-checksum")
    companions[1].write_bytes(b'{"version": 1, "marker": "orphan-lastgood"}')
    companions[2].write_bytes(b"orphan-lastgood-checksum")
    original = {path.name: path.read_bytes() for path in companions}
    refresh_recovery_files = manager._refresh_json_recovery_files

    def fail_recovery_refresh(destination: Path) -> None:
        refresh_recovery_files(destination)
        if destination == state:
            raise RuntimeBackupError("synthetic orphan rollback failure")

    monkeypatch.setattr(
        manager,
        "_refresh_json_recovery_files",
        fail_recovery_refresh,
    )

    with pytest.raises(RuntimeBackupError, match="synthetic orphan rollback failure"):
        manager.restore_backup(backup, confirmation="RESTORE RUNTIME")

    assert not state.exists()
    assert {path.name: path.read_bytes() for path in companions} == original


def test_restore_reports_runtime_error_from_failed_rollback(
    tmp_path: Path,
    monkeypatch,
):
    create_runtime(tmp_path)
    state = tmp_path / "risk_state.json"
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.9b1")
    backup = manager.create_backup(tmp_path / "backups" / "runtime.zip")
    state.write_text(
        json.dumps({"version": 2, "accounts": {"changed": {}}}),
        encoding="utf-8",
    )
    replace_with_retry = manager._replace_with_retry
    replace_calls = 0

    def fail_rollback_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls > 1:
            raise RuntimeBackupError("synthetic rollback replacement failure")
        replace_with_retry(source, destination)

    def fail_recovery_refresh(_destination: Path) -> None:
        raise RuntimeBackupError("synthetic post-commit failure")

    monkeypatch.setattr(manager, "_replace_with_retry", fail_rollback_replace)
    monkeypatch.setattr(
        manager,
        "_refresh_json_recovery_files",
        fail_recovery_refresh,
    )

    with pytest.raises(
        RuntimeBackupError,
        match="rollback errors: risk_state.json: synthetic rollback replacement failure",
    ):
        manager.restore_backup(backup, confirmation="RESTORE RUNTIME")


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
