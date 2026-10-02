"""Kernel-backed pathname continuity for the SQLite trusted-open interval.

A second descriptor is NOT the SQLite descriptor.  This guard instead makes
name replacement observable (Linux inotify), or prevents it (Windows sharing
leases), for the database AND every ancestor.  The guard is installed before
SQLite opens the name and retained until its binding is sealed.  Ordinary
content writes are not identity changes.  No SQLite, WAL or SHM bytes are read.

Supported profile: local Linux filesystems listed below, and native Windows.
Network/FUSE filesystems, other kernels and missing notification support fail
closed.  Privileged mount-namespace/raw-device manipulation and arbitrary
in-process code replacement are outside this pathname-replacement boundary.
No I/O, native-library loading or watch registration occurs on module import.
"""
from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path
from typing import BinaryIO


class ContinuityError(OSError):
    """Finite refusal; never includes a private path."""


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


class PathContinuityGuard:
    """Sticky continuity proof, not a snapshot of a pathname or file contents."""

    def __init__(self, database: Path, retained: BinaryIO) -> None:
        self._closed = False
        self._invalid = False
        self._notify: BinaryIO | None = None
        self._anchors: list[tuple[Path, tuple[int, int]]] = []
        self._win_handles: list[int] = []
        self._win_api = None
        try:
            if os.name == "nt":
                self._start_windows(database)
            elif os.name == "posix" and os.uname().sysname == "Linux":
                self._start_linux(database, retained)
            else:
                raise ContinuityError("SQLITE_PATH_CONTINUITY_UNSUPPORTED")
            if _identity(os.fstat(retained.fileno())) != _identity(database.stat()):
                raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
            self.validate()
        except (AttributeError, NotImplementedError):
            self.close()
            raise ContinuityError("SQLITE_PATH_CONTINUITY_UNSUPPORTED") from None
        except BaseException:
            self.close()
            raise

    def _start_linux(self, database: Path, retained: BinaryIO) -> None:
        # Only local filesystems with in-kernel inode notifications are admitted.
        # f_type is the first native long of Linux struct statfs; reserve more
        # than the ABI structure needs. No unversioned CPython/SQLite offsets.
        if ctypes.sizeof(ctypes.c_long) != 8:
            raise ContinuityError("SQLITE_PATH_CONTINUITY_UNSUPPORTED")
        lib = ctypes.CDLL(None, use_errno=True)
        lib.inotify_init1.argtypes = [ctypes.c_int]
        lib.inotify_init1.restype = ctypes.c_int
        lib.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        lib.inotify_add_watch.restype = ctypes.c_int
        lib.fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
        lib.fstatfs.restype = ctypes.c_int
        local_types = {0xEF53, 0x58465342, 0x9123683E, 0x01021994, 0x794C7630}
        fd = lib.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            raise ContinuityError("SQLITE_PATH_CONTINUITY_UNAVAILABLE")
        self._notify = os.fdopen(fd, "rb", buffering=0)

        def watch(path: Path, handle_fd: int, *, main: bool) -> None:
            buf = ctypes.create_string_buffer(256)
            if lib.fstatfs(handle_fd, buf) != 0:
                raise ContinuityError("SQLITE_PATH_CONTINUITY_UNAVAILABLE")
            if ctypes.c_long.from_buffer(buf).value & 0xFFFFFFFF not in local_types:
                raise ContinuityError("SQLITE_PATH_CONTINUITY_FILESYSTEM_UNSUPPORTED")
            value = os.fstat(handle_fd)
            if main:
                if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                    raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
            elif not stat.S_ISDIR(value.st_mode):
                raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
            # Attach to the held object, NOT to a pathname that could be swapped
            # while installing the watch. Do not specify IN_DONT_FOLLOW here:
            # /proc/self/fd is the kernel's descriptor reference, not user input.
            mask = 0x800 | 0x400 | 0x2000  # MOVE_SELF, DELETE_SELF, UNMOUNT
            if main:
                mask |= 0x4  # ATTRIB: includes transient hardlink changes
            proc_name = f"/proc/self/fd/{handle_fd}".encode("ascii")
            if lib.inotify_add_watch(fd, proc_name, mask) < 0:
                raise ContinuityError("SQLITE_PATH_CONTINUITY_UNAVAILABLE")
            self._anchors.append((path, _identity(value)))

        # Root-to-leaf coverage detects replacement of a parent directory too.
        for directory in reversed(database.parents):
            anchor = os.open(directory, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                watch(directory, anchor, main=False)
            finally:
                os.close(anchor)  # O_PATH descriptor, not a SQLite lock-bearing fd
        watch(database, retained.fileno(), main=True)

    def _start_windows(self, database: Path) -> None:
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        api.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                               wintypes.DWORD, wintypes.DWORD]
        api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        api.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        api.GetDriveTypeW.restype = wintypes.UINT
        # Remote filesystems may have out-of-band namespace writers.
        if api.GetDriveTypeW(database.anchor) not in {3, 6}:  # fixed / RAM disk
            raise ContinuityError("SQLITE_PATH_CONTINUITY_FILESYSTEM_UNSUPPORTED")
        self._win_api = api
        invalid = ctypes.c_void_p(-1).value
        # Root-to-leaf leases protect the complete path, not merely the leaf.
        for path in (*reversed(database.parents), database):
            # FILE_READ_ATTRIBUTES; SHARE_READ | SHARE_WRITE, explicitly NOT
            # SHARE_DELETE. OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
            handle = api.CreateFileW(str(path), 0x80, 0x3, None, 3, 0x02200000, None)
            if handle in (None, invalid):
                raise ContinuityError("SQLITE_PATH_CONTINUITY_UNAVAILABLE")
            self._win_handles.append(handle)
            value = path.lstat()
            if (stat.S_ISLNK(value.st_mode)
                    or getattr(value, "st_file_attributes", 0) & 0x400):
                raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
            if path == database and (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1):
                raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
            if path != database and not stat.S_ISDIR(value.st_mode):
                raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
            self._anchors.append((path, _identity(value)))
            self._check_windows_name(handle, path)

    def _check_windows_name(self, handle: int, path: Path) -> None:
        assert self._win_api is not None
        buf = ctypes.create_unicode_buffer(32768)
        n = self._win_api.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
        if not 0 < n < len(buf):
            raise ContinuityError("SQLITE_PATH_CONTINUITY_UNAVAILABLE")
        name = buf.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
        if os.path.normcase(os.path.normpath(name)) != os.path.normcase(os.path.normpath(str(path))):
            raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")

    def validate(self) -> None:
        if self._closed or self._invalid:
            raise ContinuityError("SQLITE_PATH_CONTINUITY_LOST")
        try:
            if self._notify is not None:
                if self._notify.closed:
                    raise ContinuityError("SQLITE_PATH_CONTINUITY_LOST")
                try:
                    event = os.read(self._notify.fileno(), 65536)
                except BlockingIOError:
                    event = None  # live queue, no rename notifications
                # Every subscribed event invalidates the proof. OVERFLOW and
                # IGNORED are delivered by the kernel automatically: fail closed
                # too. Never drain/re-baseline a queue after an ABA event.
                if event is not None:
                    raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
            for i, (path, identity) in enumerate(self._anchors):
                value = path.lstat()
                if (stat.S_ISLNK(value.st_mode) or _identity(value) != identity
                        or getattr(value, "st_file_attributes", 0) & 0x400):
                    raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
                if self._win_api is not None:
                    self._check_windows_name(self._win_handles[i], path)
            if self._notify is not None:
                try:
                    os.read(self._notify.fileno(), 65536)
                except BlockingIOError:
                    pass
                else:
                    raise ContinuityError("SQLITE_PATH_CONTINUITY_CHANGED")
        except (OSError, ValueError):
            self._invalid = True
            raise ContinuityError("SQLITE_PATH_CONTINUITY_LOST") from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._notify is not None:
            self._notify.close()
        if self._win_api is not None:
            for handle in reversed(self._win_handles):
                self._win_api.CloseHandle(handle)
            self._win_handles.clear()
