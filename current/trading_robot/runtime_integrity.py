from __future__ import annotations

"""Runtime integrity inspection shared by recovery, backup and readiness UI."""

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .cash_ledger_persistence import (
    CASH_LEDGER_STORE_SCHEMA_VERSION,
    CodecDescriptor,
    PersistenceError,
    _validate_connection,
    normalize_codec_registry,
)
from .runtime_cash_authority import RuntimeCashAuthorityRecord, _transition_pair


class IntegrityStatus(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNREADABLE = "UNREADABLE"
    EMPTY = "EMPTY"
    CHECKSUM_MISSING = "CHECKSUM_MISSING"
    CHECKSUM_UNREADABLE = "CHECKSUM_UNREADABLE"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"


@dataclass(frozen=True, slots=True)
class FileIntegrityReport:
    path: str
    status: IntegrityStatus
    kind: str
    size_bytes: int | None = None
    sha256: str | None = None
    schema_version: int | str | None = None
    detail: str = ""

    @property
    def valid(self) -> bool:
        return self.status == IntegrityStatus.VALID

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = str(self.status)
        payload["valid"] = self.valid
        return payload


JsonValidator = Callable[[Mapping[str, Any]], None]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_json_file(
    path: str | Path,
    *,
    expected_versions: set[int | str] | None = None,
    validator: JsonValidator | None = None,
    require_checksum: bool = False,
) -> FileIntegrityReport:
    target = Path(path)
    if not target.exists():
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.MISSING,
            kind="json",
            detail="File does not exist.",
        )
    try:
        size = target.stat().st_size
    except OSError as exc:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.UNREADABLE,
            kind="json",
            detail=str(exc),
        )
    if size <= 0:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.EMPTY,
            kind="json",
            size_bytes=size,
            detail="File is empty.",
        )
    try:
        raw = target.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.UNREADABLE,
            kind="json",
            size_bytes=size,
            detail=str(exc),
        )
    except json.JSONDecodeError as exc:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.CORRUPT,
            kind="json",
            size_bytes=size,
            detail=f"Invalid JSON at line {exc.lineno}, column {exc.colno}.",
        )
    if not isinstance(document, dict):
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.CORRUPT,
            kind="json",
            size_bytes=size,
            detail="JSON root must be an object.",
        )
    version = document.get("version")
    digest = hashlib.sha256(raw).hexdigest()
    checksum_path = target.with_name(target.name + ".sha256")
    if require_checksum or checksum_path.exists():
        if not checksum_path.is_file():
            return FileIntegrityReport(
                path=str(target),
                status=IntegrityStatus.CHECKSUM_MISSING,
                kind="json",
                size_bytes=size,
                sha256=digest,
                schema_version=version,
                detail=f"Checksum sidecar is missing: {checksum_path.name}",
            )
        try:
            expected_checksum = (
                checksum_path.read_text(encoding="ascii").strip().lower()
            )
        except (OSError, UnicodeError) as exc:
            return FileIntegrityReport(
                path=str(target),
                status=IntegrityStatus.CHECKSUM_UNREADABLE,
                kind="json",
                size_bytes=size,
                sha256=digest,
                schema_version=version,
                detail=str(exc),
            )
        if expected_checksum != digest:
            return FileIntegrityReport(
                path=str(target),
                status=IntegrityStatus.CHECKSUM_MISMATCH,
                kind="json",
                size_bytes=size,
                sha256=digest,
                schema_version=version,
                detail="SHA-256 does not match sidecar.",
            )
    if expected_versions is not None and version not in expected_versions:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.INCOMPATIBLE,
            kind="json",
            size_bytes=size,
            sha256=digest,
            schema_version=version,
            detail=(
                f"Unsupported schema version {version!r}; expected one of "
                f"{sorted(str(item) for item in expected_versions)}."
            ),
        )
    if validator is not None:
        try:
            validator(document)
        except Exception as exc:  # noqa: BLE001 - validator is an extension boundary
            return FileIntegrityReport(
                path=str(target),
                status=IntegrityStatus.CORRUPT,
                kind="json",
                size_bytes=size,
                sha256=digest,
                schema_version=version,
                detail=str(exc),
            )
    return FileIntegrityReport(
        path=str(target),
        status=IntegrityStatus.VALID,
        kind="json",
        size_bytes=size,
        sha256=digest,
        schema_version=version,
        detail="JSON parsed successfully.",
    )


def inspect_sqlite_file(path: str | Path) -> FileIntegrityReport:
    target = Path(path)
    if not target.exists():
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.MISSING,
            kind="sqlite",
            detail="Database does not exist.",
        )
    try:
        size = target.stat().st_size
    except OSError as exc:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.UNREADABLE,
            kind="sqlite",
            detail=str(exc),
        )
    if size <= 0:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.EMPTY,
            kind="sqlite",
            size_bytes=size,
            detail="Database file is empty.",
        )
    try:
        wal_path = target.with_name(target.name + "-wal")
        use_immutable = not wal_path.exists() or wal_path.stat().st_size == 0
        uri = f"file:{target.resolve().as_posix()}?mode=ro"
        if use_immutable:
            uri += "&immutable=1"
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=5.0,
        )
        try:
            quick = connection.execute("PRAGMA quick_check").fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            schema_row = connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.CORRUPT,
            kind="sqlite",
            size_bytes=size,
            detail=str(exc),
        )
    except OSError as exc:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.UNREADABLE,
            kind="sqlite",
            size_bytes=size,
            detail=str(exc),
        )
    quick_values = [str(row[0]) for row in quick]
    integrity_values = [str(row[0]) for row in integrity]
    if quick_values != ["ok"] or integrity_values != ["ok"]:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.CORRUPT,
            kind="sqlite",
            size_bytes=size,
            sha256=sha256_file(target),
            schema_version=(schema_row[0] if schema_row else None),
            detail=(f"quick_check={quick_values}; integrity_check={integrity_values}"),
        )
    return FileIntegrityReport(
        path=str(target),
        status=IntegrityStatus.VALID,
        kind="sqlite",
        size_bytes=size,
        sha256=sha256_file(target),
        schema_version=(schema_row[0] if schema_row else None),
        detail="SQLite quick_check and integrity_check returned ok.",
    )


def inspect_runtime_cash_authority(path: str | Path) -> FileIntegrityReport:
    """Validate CL7 active/checksum/lastgood custody without creating a lock."""

    selected = Path(path)
    active = (
        selected / "runtime_cash_authority.json"
        if selected.suffix.lower() != ".json"
        else selected
    )
    checksum = active.with_name(active.name + ".sha256")
    lastgood = active.with_name(active.name + ".lastgood")
    existing = tuple(item.exists() for item in (active, checksum, lastgood))
    if not any(existing):
        return FileIntegrityReport(
            path=str(active),
            status=IntegrityStatus.MISSING,
            kind="runtime_cash_authority",
            detail="CL7 authority custody does not exist.",
        )
    if not active.is_file() or not checksum.is_file():
        return FileIntegrityReport(
            path=str(active),
            status=IntegrityStatus.CORRUPT,
            kind="runtime_cash_authority",
            detail="CL7 active/checksum custody is incomplete.",
        )
    try:
        active_bytes = active.read_bytes()
        checksum_bytes = checksum.read_bytes()
        record = RuntimeCashAuthorityRecord.from_canonical_bytes(active_bytes)
    except (OSError, RuntimeError, TypeError, ValueError):
        return FileIntegrityReport(
            path=str(active),
            status=IntegrityStatus.CORRUPT,
            kind="runtime_cash_authority",
            detail="CL7 active record is unreadable or non-canonical.",
        )
    expected_checksum = (record.sha256 + "\n").encode("ascii")
    if not hmac.compare_digest(checksum_bytes, expected_checksum):
        return FileIntegrityReport(
            path=str(active),
            status=IntegrityStatus.CHECKSUM_MISMATCH,
            kind="runtime_cash_authority",
            size_bytes=len(active_bytes),
            sha256=record.sha256,
            schema_version=record.version,
            detail="CL7 active checksum does not match.",
        )
    if record.record_revision == 0:
        lastgood_valid = not lastgood.exists()
    elif not lastgood.is_file():
        lastgood_valid = False
    else:
        try:
            previous = RuntimeCashAuthorityRecord.from_canonical_bytes(
                lastgood.read_bytes()
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            lastgood_valid = False
        else:
            try:
                _transition_pair(previous, record)
            except (RuntimeError, TypeError, ValueError):
                lastgood_valid = False
            else:
                lastgood_valid = True
    if not lastgood_valid:
        return FileIntegrityReport(
            path=str(active),
            status=IntegrityStatus.CORRUPT,
            kind="runtime_cash_authority",
            size_bytes=len(active_bytes),
            sha256=record.sha256,
            schema_version=record.version,
            detail="CL7 lastgood revision/hash chain is invalid.",
        )
    return FileIntegrityReport(
        path=str(active),
        status=IntegrityStatus.VALID,
        kind="runtime_cash_authority",
        size_bytes=len(active_bytes),
        sha256=record.sha256,
        schema_version=record.version,
        detail=(
            "CL7 active/checksum/lastgood custody is valid; "
            f"state={record.state.value}; revision={record.record_revision}."
        ),
    )


def inspect_cash_ledger_store(path: str | Path) -> FileIntegrityReport:
    """Inspect the CL2 directory store and its WAL/SHM custody read-only."""

    root = Path(path)
    if not root.exists():
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.MISSING,
            kind="cash_ledger_store",
            detail="CL2 CashLedger store does not exist.",
        )
    if not root.is_dir() or root.is_symlink():
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.INCOMPATIBLE,
            kind="cash_ledger_store",
            detail="CL2 CashLedger root is not a regular directory.",
        )
    database = root / "store.sqlite3"
    wal = root / "store.sqlite3-wal"
    shared_memory = root / "store.sqlite3-shm"
    allowed = {
        "store.sqlite3",
        "store.sqlite3-wal",
        "store.sqlite3-shm",
    }
    try:
        children = {item.name for item in root.iterdir()}
    except OSError:
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.UNREADABLE,
            kind="cash_ledger_store",
            detail="CL2 CashLedger directory cannot be enumerated.",
        )
    if not database.is_file() or not children.issubset(allowed):
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.CORRUPT,
            kind="cash_ledger_store",
            detail="CL2 CashLedger layout is incomplete or contains unknown members.",
        )
    if wal.exists() != shared_memory.exists():
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.CORRUPT,
            kind="cash_ledger_store",
            detail="CL2 CashLedger WAL/SHM sidecar pair is inconsistent.",
        )
    if any(
        item.exists() and (not item.is_file() or item.is_symlink())
        for item in (wal, shared_memory)
    ):
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.CORRUPT,
            kind="cash_ledger_store",
            detail="CL2 CashLedger sidecar type is invalid.",
        )
    try:
        header = database.read_bytes()[:16]
        if header != b"SQLite format 3\x00":
            raise sqlite3.DatabaseError("invalid header")
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            descriptor_rows = connection.execute(
                "SELECT canonical FROM cl2_codec ORDER BY codec_id, codec_version"
            ).fetchall()
            descriptors = [
                CodecDescriptor.from_canonical_bytes(bytes(row[0]))
                for row in descriptor_rows
            ]
            registry = normalize_codec_registry(descriptors)
            _validate_connection(connection, registry)
            row = connection.execute(
                "SELECT schema_version, store_revision, ledger_revision "
                "FROM cl2_meta WHERE singleton=1"
            ).fetchone()
        finally:
            connection.close()
    except (OSError, PersistenceError, sqlite3.Error, TypeError, ValueError):
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.CORRUPT,
            kind="cash_ledger_store",
            detail="CL2 CashLedger SQLite structure or metadata is invalid.",
        )
    if (
        row is None
        or int(row[0]) != CASH_LEDGER_STORE_SCHEMA_VERSION
        or int(row[1]) < 0
        or int(row[2]) < 0
    ):
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.CORRUPT,
            kind="cash_ledger_store",
            detail="CL2 CashLedger integrity or revision metadata is invalid.",
        )
    digest = hashlib.sha256()
    size = 0
    try:
        for member in sorted(root.iterdir(), key=lambda item: item.name):
            raw_digest = sha256_file(member)
            member_size = member.stat().st_size
            size += member_size
            digest.update(member.name.encode("ascii"))
            digest.update(b"\x00")
            digest.update(raw_digest.encode("ascii"))
            digest.update(b"\n")
    except (OSError, UnicodeError):
        return FileIntegrityReport(
            path=str(root),
            status=IntegrityStatus.UNREADABLE,
            kind="cash_ledger_store",
            detail="CL2 CashLedger identity could not be calculated.",
        )
    return FileIntegrityReport(
        path=str(root),
        status=IntegrityStatus.VALID,
        kind="cash_ledger_store",
        size_bytes=size,
        sha256=digest.hexdigest(),
        schema_version=int(row[0]),
        detail=(
            "CL2 CashLedger integrity is valid; "
            f"store_revision={int(row[1])}; ledger_revision={int(row[2])}."
        ),
    )


def inspect_v3_10_cash_custody(root: str | Path) -> dict[str, FileIntegrityReport]:
    selected = Path(root)
    return {
        "runtime_cash_authority.json": inspect_runtime_cash_authority(selected),
        "cash_ledger_v3_10.sqlite3": inspect_cash_ledger_store(
            selected / "cash_ledger_v3_10.sqlite3"
        ),
    }


__all__ = [
    "FileIntegrityReport",
    "IntegrityStatus",
    "inspect_cash_ledger_store",
    "inspect_json_file",
    "inspect_runtime_cash_authority",
    "inspect_sqlite_file",
    "inspect_v3_10_cash_custody",
    "sha256_file",
]
