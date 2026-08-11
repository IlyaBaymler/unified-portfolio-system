from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import BinaryIO


class LockUnavailableError(RuntimeError):
    """Raised when another process owns an inter-process lock."""


@dataclass(slots=True)
class InterProcessFileLock:
    """Small cross-platform advisory lock backed by an open file handle.

    Windows uses ``msvcrt.locking`` and POSIX systems use ``fcntl.flock``.
    The lock is automatically released by the operating system if the process
    terminates unexpectedly. The tiny lock file may remain on disk; that is
    harmless because ownership is attached to the open handle, not the file's
    existence.
    """

    path: str | Path
    timeout_seconds: float = 0.0
    poll_seconds: float = 0.05
    _handle: BinaryIO | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative.")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive.")

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> "InterProcessFileLock":
        if self._handle is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        last_error: OSError | None = None

        while True:
            handle = self._open_handle()
            try:
                self._lock_handle(handle)
            except OSError as exc:
                last_error = exc
                handle.close()
                if time.monotonic() >= deadline:
                    raise LockUnavailableError(
                        f"Ресурс уже используется другим процессом: {self.path}"
                    ) from last_error
                time.sleep(self.poll_seconds)
                continue

            self._handle = handle
            self._write_owner_metadata()
            return self

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._unlock_handle(handle)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "InterProcessFileLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def _open_handle(self) -> BinaryIO:
        try:
            handle = self.path.open("r+b")
        except FileNotFoundError:
            handle = self.path.open("w+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() < 1:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        return handle

    @staticmethod
    def _lock_handle(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: BinaryIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass

    def _write_owner_metadata(self) -> None:
        assert self._handle is not None
        payload = f"pid={os.getpid()} acquired={time.time():.6f}\n".encode("ascii")
        self._handle.seek(0)
        self._handle.write(payload)
        self._handle.truncate()
        self._handle.flush()
