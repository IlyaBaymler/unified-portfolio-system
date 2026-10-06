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


class _RecordedWindowsCall:
    """Signature-compatible API stub; it does NOT simulate a native OS test."""

    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


def test_windows_lease_access_is_subject_to_sharing_checks(tmp_path, monkeypatch):
    # Execute the product's actual argument construction, without claiming
    # Windows execution. The MS-FSA access predicate is an independent oracle.
    from types import SimpleNamespace

    root = tmp_path / "a"
    root.mkdir()
    database = root / "store.sqlite3"
    database.write_bytes(b"argument-contract-only")
    opened, closed = [], []

    def create(path, access, share, security, disposition, flags, template):
        opened.append((path, access, share, disposition, flags))
        return len(opened)

    api = SimpleNamespace(
        CreateFileW=_RecordedWindowsCall(create),
        CloseHandle=_RecordedWindowsCall(lambda handle: closed.append(handle) or 1),
        GetFinalPathNameByHandleW=_RecordedWindowsCall(lambda *args: 0),
        GetDriveTypeW=_RecordedWindowsCall(lambda *args: 3),
    )
    monkeypatch.setattr(continuity.ctypes, "WinDLL", lambda *a, **k: api, raising=False)
    monkeypatch.setattr(continuity.PathContinuityGuard, "_check_windows_name",
                        lambda *a: None)
    guard = continuity.PathContinuityGuard.__new__(continuity.PathContinuityGuard)
    guard._closed, guard._invalid = False, False
    guard._notify, guard._win_api = None, None
    guard._anchors, guard._win_handles = [], []
    try:
        guard._start_windows(database)
        assert [row[0] for row in opened] == [
            str(p) for p in (*reversed(database.parents), database)
        ]
        share_checked_rights = 0x1 | 0x2 | 0x4 | 0x20 | 0x10000
        for _, access, share, disposition, flags in opened:
            assert access & share_checked_rights, "metadata-only lease is ignored"
            assert access == 0x81 and share == 0x3
            assert not access & (0x2 | 0x4 | 0x10000), "unnecessary write/delete access"
            assert disposition == 3 and flags == 0x02200000
    finally:
        guard.close()
    assert closed == list(range(len(opened), 0, -1))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows metadata-only control")
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_native_windows_metadata_control_and_real_deny_delete(tmp_path, kind):
    # No SQLite, child handle or retained rb handle can accidentally protect
    # these objects. First prove the old metadata-only flags do not protect it,
    # then prove the product's new rights deny rename until CloseHandle.
    import ctypes
    from ctypes import wintypes

    target = tmp_path / "lease_target"
    if kind == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"synthetic-only")
    renamed = tmp_path / "renamed"
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                               wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    for access in (0x80, continuity._WINDOWS_LEASE_ACCESS):
        handle = api.CreateFileW(str(target), access, 0x3, None, 3, 0x02200000, None)
        assert handle not in (None, invalid), ctypes.get_last_error()
        try:
            if access == 0x80:
                os.replace(target, renamed)
                os.replace(renamed, target)
            else:
                with pytest.raises(PermissionError) as error:
                    os.replace(target, renamed)
                assert error.value.winerror in {5, 32}
        finally:
            assert api.CloseHandle(handle)
    os.replace(target, renamed)
    os.replace(renamed, target)
    assert target.exists() and not renamed.exists()
