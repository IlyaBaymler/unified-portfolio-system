from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_robot.state_persistence import (
    StatePersistenceError,
    atomic_write_json,
    inspect_json_file,
    read_json_verified,
)


def test_atomic_json_writes_checksum_and_last_good(tmp_path: Path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"version": 1, "value": 1}, write_checksum=True, keep_last_good=True)
    atomic_write_json(path, {"version": 1, "value": 2}, write_checksum=True, keep_last_good=True)

    assert inspect_json_file(path, supported_versions={1}).valid
    backup = path.with_name(path.name + ".lastgood")
    assert backup.exists()
    assert backup.with_name(backup.name + ".sha256").exists()
    assert json.loads(backup.read_text(encoding="utf-8"))["value"] == 1


def test_checksum_mismatch_is_fail_closed(tmp_path: Path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"version": 1, "value": 1}, write_checksum=True)
    path.write_text('{"version": 1, "value": 9}', encoding="utf-8")
    inspection = inspect_json_file(path, supported_versions={1})
    assert inspection.status == "CHECKSUM_MISMATCH"
    with pytest.raises(StatePersistenceError, match="CHECKSUM_MISMATCH"):
        read_json_verified(path, supported_versions={1})
