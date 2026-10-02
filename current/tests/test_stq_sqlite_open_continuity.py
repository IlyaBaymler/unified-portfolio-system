"""STQ-C1D-001: real rename schedules, kernel continuity and retained semantics."""
from __future__ import annotations

import os
import sqlite3
import struct
from pathlib import Path

import pytest

from test_v3_10_cash_ledger_persistence import descriptor, fixture, vectors  # noqa: F401
from trading_robot import cash_ledger_persistence as cl2
from trading_robot import sqlite_open_custody as continuity


def _seed(root, descriptor):
    cl2.CashLedgerStore.create(root, [descriptor]).close()


def _close_without_checkpoint(store):
    # A failed-path probe must not request a product checkpoint for cleanup.
    try:
        store._connection.close()
    finally:
        store._custody.close()
        store._closed = True


@pytest.mark.skipif(os.name != "posix", reason="Actual POSIX open-file rename schedule")
@pytest.mark.parametrize("level", ["database", "parent"])
@pytest.mark.parametrize("different_bytes", [False, True])
def test_aba_same_or_different_valid_database_is_rejected(tmp_path, monkeypatch, descriptor,
                                                         vectors, level, different_bytes):
    a, b = tmp_path / "a", tmp_path / "b"
    _seed(a, descriptor); _seed(b, descriptor)
    if different_bytes:
        with cl2.CashLedgerStore.open(b, [descriptor]) as donor:
            obs = cl2.InboxObservation.from_canonical_bytes(
                vectors["observation-original"]["canonical_json_ascii"], [descriptor])
            donor.append_observation(obs, expected_store_revision=0)
    database = a / "store.sqlite3"
    before = database.read_bytes()
    real_connect = cl2._connect
    hits = []
    captured = []
    real_custody = cl2._open_database_custody

    def custody(*args):
        c = real_custody(*args); captured.append((c, c._continuity)); return c

    def connect(path, timeout):
        left = database if level == "database" else a
        right = b / "store.sqlite3" if level == "database" else b
        spare = tmp_path / "displaced"
        os.replace(left, spare); os.replace(right, left)
        try:
            conn = real_connect(path, timeout)
            hits.append(conn.execute("SELECT store_revision FROM cl2_meta").fetchone()[0])
        finally:
            os.replace(left, right); os.replace(spare, left)
        return conn

    monkeypatch.setattr(cl2, "_open_database_custody", custody)
    monkeypatch.setattr(cl2, "_connect", connect)
    with pytest.raises(cl2.PersistenceError, match="^PATH_INVALID$"):
        cl2.CashLedgerStore.open(a, [descriptor])
    assert hits == [int(different_bytes)]
    assert database.read_bytes() == before
    assert len(captured) == 1 and captured[0][0].closed and captured[0][1]._closed


@pytest.mark.skipif(os.name != "posix", reason="Actual POSIX open-file rename schedule")
def test_readonly_probe_does_not_end_writable_open_guard(tmp_path, descriptor):
    a, b = tmp_path / "a", tmp_path / "b"
    _seed(a, descriptor); _seed(b, descriptor)
    path, identity = cl2._validate_live_root_identity(a)
    custody = cl2._open_database_custody(path, identity)
    ro = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        cl2._validate_open_root(a, ro, custody)
    finally:
        ro.close()
    assert custody._continuity is not None
    displaced = tmp_path / "displaced"
    os.replace(path, displaced); os.replace(b / "store.sqlite3", path)
    conn = cl2._connect(path, 0)
    conn.execute("SELECT store_revision FROM cl2_meta").fetchone()
    os.replace(path, b / "store.sqlite3"); os.replace(displaced, path)
    try:
        with pytest.raises(cl2.PersistenceError, match="^PATH_INVALID$"):
            cl2._validate_open_root(a, conn, custody)
        with pytest.raises(cl2.PersistenceError, match="^PATH_INVALID$"):
            custody.seal_open(a, conn)
    finally:
        conn.close(); custody.close()


def test_sealed_connection_cannot_be_replaced_by_another_object(tmp_path, descriptor):
    root = tmp_path / "a"
    with cl2.CashLedgerStore.create(root, [descriptor]) as store:
        assert store._custody._continuity is None
        assert store._custody._bound_connection is store._connection
        other = sqlite3.connect(root / "store.sqlite3")
        try:
            with pytest.raises(cl2.PersistenceError, match="^PATH_INVALID$"):
                cl2._validate_open_root(root, other, store._custody)
        finally:
            other.close()
        assert store.validate().store_revision == 0


def test_unavailable_continuity_stops_before_sqlite_open(tmp_path, monkeypatch, descriptor):
    root = tmp_path / "a"; _seed(root, descriptor)
    hits = []
    def fail(*args):
        raise continuity.ContinuityError("SQLITE_PATH_CONTINUITY_UNAVAILABLE")
    def should_not_open(*args):
        hits.append(1); pytest.fail("unprotected SQLite open")
    monkeypatch.setattr(cl2, "PathContinuityGuard", fail)
    monkeypatch.setattr(cl2, "_connect", should_not_open)
    with pytest.raises(cl2.PersistenceError, match="^PATH_INVALID$"):
        cl2.CashLedgerStore.open(root, [descriptor])
    assert hits == []


@pytest.mark.skipif(os.name != "posix", reason="Linux inotify queue contract")
@pytest.mark.parametrize("damage", ["queue_overflow", "watch_removed", "fd_closed"])
def test_notification_loss_is_sticky_refusal(tmp_path, monkeypatch, descriptor, damage):
    root = tmp_path / "a"; _seed(root, descriptor)
    path, identity = cl2._validate_live_root_identity(root)
    custody = cl2._open_database_custody(path, identity)
    guard = custody._continuity
    assert guard is not None and guard._notify is not None
    fd = guard._notify.fileno()
    read = os.read
    if damage == "fd_closed":
        guard._notify.close()
    else:
        mask = 0x4000 if damage == "queue_overflow" else 0x8000
        monkeypatch.setattr(os, "read", lambda f, n: struct.pack("iIII", -1, mask, 0, 0)
                            if f == fd else read(f, n))
    try:
        with pytest.raises(cl2.PersistenceError, match="^PATH_INVALID$"):
            custody.validate(path)
        monkeypatch.setattr(os, "read", read)
        with pytest.raises(cl2.PersistenceError, match="^PATH_INVALID$"):
            custody.validate(path)
    finally:
        custody.close()


@pytest.mark.skipif(os.name != "posix", reason="Linux fd inventory")
def test_repeated_open_close_has_no_watch_descriptor_leak(tmp_path, descriptor):
    root = tmp_path / "a"; _seed(root, descriptor)
    before = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(12):
        with cl2.CashLedgerStore.open(root, [descriptor]) as store:
            assert store.validate().store_revision == 0
    assert len(list(Path("/proc/self/fd").iterdir())) == before


@pytest.mark.skipif(os.name != "nt", reason="Native Windows rename-denial proof")
@pytest.mark.parametrize("level", ["database", "parent"])
def test_windows_lease_denies_rename_during_actual_open(tmp_path, monkeypatch, descriptor, level):
    root = tmp_path / "a"; _seed(root, descriptor)
    real_connect = cl2._connect; hits = []
    def connect(path, timeout):
        target = path if level == "database" else root
        with pytest.raises(PermissionError) as caught:
            os.replace(target, tmp_path / "displaced")
        assert caught.value.winerror in {5, 32}
        hits.append(1)
        return real_connect(path, timeout)
    monkeypatch.setattr(cl2, "_connect", connect)
    with cl2.CashLedgerStore.open(root, [descriptor]) as store:
        assert store.validate().store_revision == 0
    assert hits == [1]
