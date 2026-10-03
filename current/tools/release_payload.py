"""Read release payloads once through pinned, non-link filesystem objects.

This boundary is independent of SQLite custody. POSIX reads use directory-fd
relative opens; Windows holds read/list sharing leases (no write or delete
sharing) on the ancestry and file. Unsupported primitives fail closed. The
returned immutable bytes, not a later pathname read, are the release payload.
The output directory is a separate, caller-controlled trusted destination.
"""
from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


def _identity(value: os.stat_result) -> tuple[int, int]:
    if value.st_ino == 0:
        raise RuntimeError("Release source file identity is unavailable")
    return value.st_dev, value.st_ino


def _fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (*_identity(value), value.st_mode, value.st_nlink, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _require_type(value: os.stat_result, directory: bool) -> None:
    valid = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if (not valid or getattr(value, "st_file_attributes", 0) & 0x400
            or (not directory and value.st_nlink != 1)):
        raise RuntimeError("Release source is a link, reparse point or special file")


def _read_buffer(stream: BinaryIO, size: int) -> bytes:
    # Bound the read to the original size; growth is an error, not an unbounded
    # stream. fstat before/after also rejects concurrent mutation of that inode.
    chunks = []
    remaining = size + 1
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) != size:
        raise RuntimeError("Release source size changed while reading")
    return raw


class _PosixSource:
    def __init__(self, root: Path, stack: ExitStack) -> None:
        required = (os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
                    and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"))
        if not required:
            raise RuntimeError("Checked release reads require directory-fd/no-follow support")
        self._flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        self._chain: list[tuple[int, str, int]] = []
        anchor = os.open(root.anchor, self._flags)
        stack.callback(os.close, anchor)
        fd = anchor
        for name in root.parts[1:]:
            child = self._directory(fd, name, stack)
            self._chain.append((fd, name, child))
            fd = child
        self._root_fd = fd

    def _directory(self, parent: int, name: str, stack: ExitStack) -> int:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require_type(before, True)
        fd = os.open(name, self._flags, dir_fd=parent)
        stack.callback(os.close, fd)
        actual = os.fstat(fd)
        _require_type(actual, True)
        if _identity(actual) != _identity(before):
            raise RuntimeError("Release directory changed while opening")
        return fd

    def validate(self) -> None:
        for parent, name, fd in self._chain:
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _require_type(named, True)
            if _identity(named) != _identity(os.fstat(fd)):
                raise RuntimeError("Release source ancestry changed")

    def read(self, parts: tuple[str, ...]) -> bytes:
        self.validate()
        with ExitStack() as stack:
            parent = self._root_fd
            chain = []
            for name in parts[:-1]:
                fd = self._directory(parent, name, stack)
                chain.append((parent, name, fd))
                parent = fd
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            _require_type(before, False)
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
            # Register ownership before fdopen so even fdopen failure closes fd.
            stack.callback(os.close, fd)
            actual = os.fstat(fd)
            _require_type(actual, False)
            if _fingerprint(actual) != _fingerprint(before):
                raise RuntimeError("Release source changed while opening")
            with os.fdopen(fd, "rb", buffering=0, closefd=False) as stream:
                raw = _read_buffer(stream, actual.st_size)
            after = os.fstat(fd)
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            _require_type(named, False)
            if _fingerprint(after) != _fingerprint(actual) or _fingerprint(named) != _fingerprint(actual):
                raise RuntimeError("Release source changed while reading")
            for owner, name, child in chain:
                current = os.stat(name, dir_fd=owner, follow_symlinks=False)
                _require_type(current, True)
                if _identity(current) != _identity(os.fstat(child)):
                    raise RuntimeError("Release file ancestry changed")
            self.validate()
            return raw


@dataclass(frozen=True)
class _WindowsInfo:
    identity: tuple[int, int]
    attributes: int
    links: int
    size: int
    creation: int
    write: int
    change: int


class _WindowsSource:
    """Native sharing leases; no metadata-only or unchecked-read fallback."""
    def __init__(self, root: Path, stack: ExitStack) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self._api.CreateFileW.restype = wintypes.HANDLE
        self._api.CloseHandle.argtypes = [wintypes.HANDLE]
        self._api.CloseHandle.restype = wintypes.BOOL
        self._api.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                       wintypes.DWORD, wintypes.DWORD]
        self._api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self._api.GetFileType.argtypes = [wintypes.HANDLE]
        self._api.GetFileType.restype = wintypes.DWORD
        self._api.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        self._api.GetFileInformationByHandleEx.restype = wintypes.BOOL

        class BasicInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("creation", "access", "write", "change")] + [
                            ("attributes", wintypes.DWORD)]

        class StandardInfo(ctypes.Structure):
            _fields_ = [("allocation", ctypes.c_longlong),
                        ("size", ctypes.c_longlong), ("links", wintypes.DWORD),
                        ("delete_pending", ctypes.c_ubyte),
                        ("directory", ctypes.c_ubyte)]

        class IdInfo(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong),
                        ("file_id", ctypes.c_ubyte * 16)]

        self._basic_info, self._standard_info, self._id_info = BasicInfo, StandardInfo, IdInfo
        self._root = root
        self._held: list[tuple[Path, int, tuple[int, int]]] = []
        for path in (*reversed(root.parents), root):
            handle, value = self._open(path, True)
            stack.callback(self._api.CloseHandle, handle)
            self._held.append((path, handle, value.identity))

    def _metadata(self, handle: int, directory: bool) -> _WindowsInfo:
        # Use the SAME native representation for named metadata controls and
        # the actual CRT-owned read handle. CPython lstat/fstat mode/ctime are
        # intentionally not mixed here (suffix bits and birth/change time).
        if self._api.GetFileType(handle) != 1:  # FILE_TYPE_DISK only
            raise RuntimeError("Release source handle is not a disk file")
        basic, standard, identity = self._basic_info(), self._standard_info(), self._id_info()
        for kind, value in [(0, basic), (1, standard), (18, identity)]:
            if not self._api.GetFileInformationByHandleEx(
                handle, kind, self._ctypes.byref(value), self._ctypes.sizeof(value),
            ):
                raise RuntimeError("Native release source metadata is unavailable")
        file_id = int.from_bytes(bytes(identity.file_id), "little")
        if (not file_id or basic.attributes & 0x400
                or bool(basic.attributes & 0x10) != directory
                or bool(standard.directory) != directory
                or standard.delete_pending or standard.size < 0
                or (not directory and standard.links != 1)):
            raise RuntimeError("Release source is a link, reparse point or special file")
        return _WindowsInfo((int(identity.volume), file_id), int(basic.attributes),
                            int(standard.links), int(standard.size), int(basic.creation),
                            int(basic.write), int(basic.change))

    def _handle(self, path: Path, directory: bool, access: int, share: int) -> int:
        handle = self._api.CreateFileW(str(path), access, share, None, 3,
                                      0x00200000 | (0x02000000 if directory else 0), None)
        if handle == self._ctypes.c_void_p(-1).value:
            raise RuntimeError("Cannot acquire release source read lease")
        return handle

    def _snapshot(self, path: Path, directory: bool) -> _WindowsInfo:
        # Metadata-only control, NOT a payload read or fallback. OPEN_REPARSE
        # and actual type checks apply before any file content can be read.
        handle = self._handle(path, directory, 0x80, 0x7)
        try:
            value = self._metadata(handle, directory)
            if self._final_path(handle) != os.path.normcase(os.path.normpath(str(path))):
                raise RuntimeError("Release source control pathname changed")
            return value
        finally:
            self._api.CloseHandle(handle)

    def _final_path(self, handle: int) -> str:
        buffer = self._ctypes.create_unicode_buffer(32768)
        count = self._api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not count or count >= len(buffer):
            raise RuntimeError("Release source handle pathname unavailable")
        name = buffer.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
        return os.path.normcase(os.path.normpath(name))

    def _open(self, path: Path, directory: bool) -> tuple[int, _WindowsInfo]:
        before = self._snapshot(path, directory)
        # FILE_READ_DATA / FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES. Share
        # READ only: no write/delete/reparse mutation while this lease is held.
        handle = self._handle(path, directory, 0x81, 0x1)
        try:
            after = self._metadata(handle, directory)
            if ((before.identity != after.identity if directory else before != after)
                    or self._final_path(handle) != os.path.normcase(os.path.normpath(str(path)))):
                raise RuntimeError("Release source pathname changed while opening")
            return handle, after
        except BaseException:
            self._api.CloseHandle(handle)
            raise

    def validate(self) -> None:
        for path, handle, identity in self._held:
            value = self._snapshot(path, True)
            if (value.identity != identity or self._metadata(handle, True).identity != identity
                    or self._final_path(handle) != os.path.normcase(os.path.normpath(str(path)))):
                raise RuntimeError("Release source ancestry changed")

    def read(self, parts: tuple[str, ...]) -> bytes:
        import msvcrt

        self.validate()
        with ExitStack() as stack:
            path = self._root
            held = []
            for name in parts[:-1]:
                path /= name
                handle, value = self._open(path, True)
                stack.callback(self._api.CloseHandle, handle)
                held.append((path, handle, value.identity))
            path /= parts[-1]
            handle, before = self._open(path, False)
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                self._api.CloseHandle(handle)
                raise
            stack.callback(os.close, fd)  # CRT now owns the native handle.
            owned_handle = msvcrt.get_osfhandle(fd)
            if owned_handle != handle:
                self._api.CloseHandle(handle)
                raise RuntimeError("Release file handle ownership changed")
            actual = self._metadata(owned_handle, False)
            if actual != before:
                raise RuntimeError("Release file identity disagrees with read handle")
            with os.fdopen(fd, "rb", buffering=0, closefd=False) as stream:
                raw = _read_buffer(stream, actual.size)
            if (self._metadata(owned_handle, False) != actual
                    or self._snapshot(path, False) != actual):
                raise RuntimeError("Release file changed while reading")
            for named, handle, identity in held:
                if (self._snapshot(named, True).identity != identity
                        or self._metadata(handle, True).identity != identity
                        or self._final_path(handle) != os.path.normcase(os.path.normpath(str(named)))):
                    raise RuntimeError("Release file ancestry changed")
            self.validate()
            return raw


class ReleaseSource:
    """One pinned root for a build; read failures never authorise an archive."""
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if ".." in self.root.parts:
            raise RuntimeError("Release root must not contain parent traversal")
        self._stack = ExitStack()
        try:
            if os.name == "nt":
                self._reader = _WindowsSource(self.root, self._stack)
            elif os.name == "posix":
                self._reader = _PosixSource(self.root, self._stack)
            else:
                raise RuntimeError("Unsupported checked release-read platform")
        except BaseException:
            self._stack.close()
            raise
        self._closed = False

    def __enter__(self) -> ReleaseSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._stack.close()

    def read(self, path: Path) -> bytes:
        if self._closed:
            raise RuntimeError("Release source is closed")
        try:
            relative = path.absolute().relative_to(self.root)
            if not relative.parts or ".." in relative.parts:
                raise RuntimeError("Release input escapes pinned source")
            return self._reader.read(relative.parts)
        except (OSError, ValueError) as exc:
            raise RuntimeError("Release source read failed closed") from exc
