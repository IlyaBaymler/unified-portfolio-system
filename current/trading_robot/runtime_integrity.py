from __future__ import annotations

"""Runtime integrity inspection shared by recovery, backup and readiness UI."""

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping


class IntegrityStatus(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNREADABLE = "UNREADABLE"
    EMPTY = "EMPTY"


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
        raw = target.read_text(encoding="utf-8")
        document = json.loads(raw)
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
    if expected_versions is not None and version not in expected_versions:
        return FileIntegrityReport(
            path=str(target),
            status=IntegrityStatus.INCOMPATIBLE,
            kind="json",
            size_bytes=size,
            sha256=sha256_file(target),
            schema_version=version,
            detail=(
                f"Unsupported schema version {version!r}; expected one of "
                f"{sorted(str(item) for item in expected_versions)}."
            ),
        )
    if validator is not None:
        try:
            validator(document)
        except Exception as exc:
            return FileIntegrityReport(
                path=str(target),
                status=IntegrityStatus.CORRUPT,
                kind="json",
                size_bytes=size,
                sha256=sha256_file(target),
                schema_version=version,
                detail=str(exc),
            )
    return FileIntegrityReport(
        path=str(target),
        status=IntegrityStatus.VALID,
        kind="json",
        size_bytes=size,
        sha256=sha256_file(target),
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
        connection = sqlite3.connect(
            f"file:{target.resolve().as_posix()}?mode=ro",
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
            detail=(
                f"quick_check={quick_values}; integrity_check={integrity_values}"
            ),
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
