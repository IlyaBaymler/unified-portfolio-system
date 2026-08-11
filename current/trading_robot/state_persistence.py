from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import random
import time
from typing import Any
from uuid import uuid4


StateEventCallback = Callable[[str, dict[str, Any]], None]


DEFAULT_RETRY_DELAYS: tuple[float, ...] = (
    0.05,
    0.10,
    0.20,
    0.40,
    0.80,
    1.60,
    3.20,
)

_TRANSIENT_ERRNOS = {
    errno.EACCES,
    errno.EBUSY,
    errno.EPERM,
}
_TRANSIENT_WINERRORS = {
    5,   # ERROR_ACCESS_DENIED
    32,  # ERROR_SHARING_VIOLATION
    33,  # ERROR_LOCK_VIOLATION
}


@dataclass(frozen=True, slots=True)
class StateSaveResult:
    path: str
    attempt_count: int
    retry_count: int
    duration_seconds: float
    backup_path: str | None = None


class StatePersistenceError(RuntimeError):
    """Raised when a local state file cannot be durably replaced.

    A failure is treated as safety-critical because the state file carries
    pending-order and idempotency information.  Callers must stop the current
    trading action and reconcile on a later cycle rather than continuing with
    an uncertain local state.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        phase: str,
        attempts: int,
        last_error: BaseException,
    ) -> None:
        self.path = Path(path)
        self.phase = str(phase)
        self.attempts = int(attempts)
        self.last_error = last_error
        super().__init__(
            "Не удалось надёжно сохранить локальное состояние "
            f"{self.path} (этап: {self.phase}, попыток: {self.attempts}): "
            f"{last_error}"
        )


def atomic_write_json(
    path: str | Path,
    state: Mapping[str, Any],
    *,
    retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
    jitter_fraction: float = 0.15,
    event_callback: StateEventCallback | None = None,
    backup_existing: bool = False,
    backup_suffix: str = ".bak",
    validate_roundtrip: bool = True,
    write_checksum: bool = False,
    keep_last_good: bool = False,
) -> StateSaveResult:
    """Durably and atomically replace a JSON state file.

    The temporary file is unique and resides in the destination directory so
    ``os.replace`` stays atomic.  On Windows, antivirus/indexing/cloud-sync
    software can briefly hold the destination file open; transient sharing or
    access errors are retried with exponential delays and jitter.
    """

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Once a state file is checksum-managed, every subsequent writer must
    # preserve that invariant even when older call sites omit the flag.
    effective_write_checksum = bool(
        write_checksum or destination.with_name(destination.name + ".sha256").exists()
    )
    started = time.perf_counter()
    payload = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    if validate_roundtrip:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:  # defensive; json.dumps should not fail
            raise StatePersistenceError(
                destination,
                phase="validate_payload",
                attempts=1,
                last_error=exc,
            ) from exc
        if not isinstance(decoded, dict):
            exc = ValueError("JSON state root must be an object.")
            raise StatePersistenceError(
                destination,
                phase="validate_payload",
                attempts=1,
                last_error=exc,
            ) from exc
    temporary = destination.with_name(
        f"{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    backup_path: Path | None = None

    try:
        try:
            with temporary.open(
                "x",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if validate_roundtrip:
                persisted = json.loads(temporary.read_text(encoding="utf-8"))
                if not isinstance(persisted, dict):
                    raise ValueError("Temporary JSON root must be an object.")
        except Exception as exc:
            details = _event_payload(
                destination=destination,
                temporary=temporary,
                phase="write_temp",
                attempt=1,
                max_attempts=1,
                error=exc,
                delay_seconds=0.0,
            )
            _emit(event_callback, "STATE_SAVE_FAILED", details)
            raise StatePersistenceError(
                destination,
                phase="write_temp",
                attempts=1,
                last_error=exc,
            ) from exc

        if (backup_existing or keep_last_good) and destination.exists():
            try:
                existing_text = destination.read_text(encoding="utf-8")
                existing = json.loads(existing_text)
                if not isinstance(existing, dict):
                    raise ValueError("Existing JSON root must be an object.")
                selected_suffix = ".lastgood" if keep_last_good else backup_suffix
                backup_path = destination.with_name(destination.name + selected_suffix)
                backup_temporary = backup_path.with_name(
                    f"{backup_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
                )
                try:
                    with backup_temporary.open("x", encoding="utf-8", newline="\n") as handle:
                        handle.write(
                            json.dumps(existing, ensure_ascii=False, indent=2, default=str)
                        )
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(backup_temporary, backup_path)
                    if keep_last_good:
                        _write_checksum_sidecar(
                            backup_path,
                            hashlib.sha256(backup_path.read_bytes()).hexdigest(),
                        )
                    _sync_parent_directory_best_effort(destination.parent)
                finally:
                    backup_temporary.unlink(missing_ok=True)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                details = _event_payload(
                    destination=destination,
                    temporary=temporary,
                    phase="backup_existing",
                    attempt=1,
                    max_attempts=1,
                    error=exc,
                    delay_seconds=0.0,
                )
                _emit(event_callback, "STATE_BACKUP_SKIPPED", details)
                # Never replace a known-corrupt destination silently.  The caller
                # must inspect/restore it explicitly instead of resetting state.
                raise StatePersistenceError(
                    destination,
                    phase="backup_existing",
                    attempts=1,
                    last_error=exc,
                ) from exc

        delays = tuple(max(0.0, float(delay)) for delay in retry_delays)
        max_attempts = len(delays) + 1
        last_error: BaseException | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                os.replace(temporary, destination)
                if effective_write_checksum:
                    _write_checksum_sidecar(
                        destination,
                        hashlib.sha256(destination.read_bytes()).hexdigest(),
                    )
                _sync_parent_directory_best_effort(destination.parent)
                duration = max(0.0, time.perf_counter() - started)
                if attempt > 1:
                    _emit(
                        event_callback,
                        "STATE_SAVE_RECOVERED",
                        {
                            "path": str(destination),
                            "attempt_count": attempt,
                            "retry_count": attempt - 1,
                            "duration_seconds": round(duration, 6),
                        },
                    )
                return StateSaveResult(
                    path=str(destination),
                    attempt_count=attempt,
                    retry_count=attempt - 1,
                    duration_seconds=duration,
                    backup_path=(str(backup_path) if backup_path else None),
                )
            except OSError as exc:
                last_error = exc
                if not _is_transient_replace_error(exc) or attempt >= max_attempts:
                    break

                base_delay = delays[attempt - 1]
                jitter = (
                    random.uniform(0.0, base_delay * jitter_fraction)
                    if base_delay > 0 and jitter_fraction > 0
                    else 0.0
                )
                delay = base_delay + jitter
                _emit(
                    event_callback,
                    "STATE_SAVE_RETRY",
                    _event_payload(
                        destination=destination,
                        temporary=temporary,
                        phase="replace",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error=exc,
                        delay_seconds=delay,
                    ),
                )
                time.sleep(delay)

        assert last_error is not None
        duration = max(0.0, time.perf_counter() - started)
        attempts_used = attempt
        details = _event_payload(
            destination=destination,
            temporary=temporary,
            phase="replace",
            attempt=attempts_used,
            max_attempts=max_attempts,
            error=last_error,
            delay_seconds=0.0,
        )
        details["duration_seconds"] = round(duration, 6)
        _emit(event_callback, "STATE_SAVE_FAILED", details)
        raise StatePersistenceError(
            destination,
            phase="replace",
            attempts=attempts_used,
            last_error=last_error,
        ) from last_error
    finally:
        # After a successful replace the temporary path no longer exists.
        # After a failed replace it is safe to remove because no order action
        # is allowed to continue from the caller.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass



@dataclass(frozen=True, slots=True)
class JsonIntegrityInspection:
    path: str
    status: str
    valid: bool
    version: int | str | None = None
    detail: str = ""


def _checksum_path(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _write_checksum_sidecar(path: Path, digest: str) -> None:
    sidecar = _checksum_path(path)
    temporary = sidecar.with_name(f"{sidecar.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(str(digest).strip().lower() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, sidecar)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_json_file(
    path: str | Path,
    *,
    supported_versions: set[int | str] | None = None,
) -> JsonIntegrityInspection:
    target = Path(path)
    if not target.exists():
        return JsonIntegrityInspection(str(target), "MISSING", False, detail="File not found.")
    try:
        raw = target.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return JsonIntegrityInspection(str(target), "CORRUPT", False, detail=str(exc))
    if not isinstance(value, dict):
        return JsonIntegrityInspection(str(target), "CORRUPT", False, detail="Root is not an object.")
    sidecar = _checksum_path(target)
    if sidecar.exists():
        try:
            expected = sidecar.read_text(encoding="ascii").strip().lower()
        except (OSError, UnicodeError) as exc:
            return JsonIntegrityInspection(str(target), "CHECKSUM_UNREADABLE", False, value.get("version"), str(exc))
        actual = hashlib.sha256(raw).hexdigest()
        if expected != actual:
            return JsonIntegrityInspection(str(target), "CHECKSUM_MISMATCH", False, value.get("version"), "SHA-256 does not match sidecar.")
    version = value.get("version")
    if supported_versions is not None and version not in supported_versions:
        return JsonIntegrityInspection(str(target), "INCOMPATIBLE", False, version, f"Unsupported version: {version!r}")
    return JsonIntegrityInspection(str(target), "VALID", True, version, "JSON and checksum are valid.")


def read_json_verified(
    path: str | Path,
    *,
    supported_versions: set[int | str] | None = None,
) -> dict[str, Any]:
    target = Path(path)
    inspection = inspect_json_file(target, supported_versions=supported_versions)
    if not inspection.valid:
        error = ValueError(f"{inspection.status}: {inspection.detail}")
        raise StatePersistenceError(
            target, phase="verify_read", attempts=1, last_error=error
        ) from error
    value = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _is_transient_replace_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS:
        return True
    return getattr(exc, "errno", None) in _TRANSIENT_ERRNOS


def _event_payload(
    *,
    destination: Path,
    temporary: Path,
    phase: str,
    attempt: int,
    max_attempts: int,
    error: BaseException,
    delay_seconds: float,
) -> dict[str, Any]:
    return {
        "path": str(destination),
        "temporary_path": str(temporary),
        "phase": phase,
        "attempt": int(attempt),
        "max_attempts": int(max_attempts),
        "delay_seconds": round(float(delay_seconds), 6),
        "error_type": type(error).__name__,
        "error": str(error),
        "errno": getattr(error, "errno", None),
        "winerror": getattr(error, "winerror", None),
    }


def _emit(
    callback: StateEventCallback | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(event_type, dict(payload))
    except Exception:
        # State persistence must not depend on logging availability.
        pass


def _sync_parent_directory_best_effort(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
