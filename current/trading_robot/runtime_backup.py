from __future__ import annotations

"""Validated runtime backup, preview and transactional restore for v3.7-alpha3."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Iterable, Mapping
from uuid import uuid4
import zipfile

from .journal import EventJournal
from .locking import InterProcessFileLock
from .portfolio_model import PORTFOLIO_STATE_SCHEMA_VERSION, validate_portfolio_document
from .runtime_integrity import (
    FileIntegrityReport,
    inspect_json_file,
    inspect_sqlite_file,
    sha256_file,
)


DEFAULT_RUNTIME_FILES: tuple[str, ...] = (
    "v3_8_runtime_seed_manifest.json",
    "strategy_profiles.json",
    "multi_instrument_profiles.json",
    "instrument_runtimes.json",
    "central_order_state.json",
    "risk_profiles.json",
    "risk_state.json",
    "robot_state.json",
    "portfolio_state.json",
    "canonical_migration_report.json",
    "portfolio_legacy_shadow.json",
    "sandbox_diagnostic_state.json",
    "trading_events.db",
)

_FORBIDDEN_BACKUP_NAMES = {
    ".env",
    "runtime_bootstrap_report.json",
    "robot_gui.log",
    "robot_debug.log",
}

_CHECKSUM_MANAGED_JSON_NAMES = {
    "portfolio_state.json",
    "multi_instrument_profiles.json",
    "instrument_runtimes.json",
    "central_order_state.json",
}


@dataclass(frozen=True, slots=True)
class BackupEntry:
    name: str
    size_bytes: int
    sha256: str
    kind: str
    schema_version: int | str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format_version: int
    created_at: str
    app_version: str
    source_directory: str
    token_included: bool
    entries: tuple[BackupEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "created_at": self.created_at,
            "app_version": self.app_version,
            "source_directory": self.source_directory,
            "token_included": self.token_included,
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class BackupVerification:
    path: str
    valid: bool
    manifest: dict[str, Any] | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RestorePreviewItem:
    name: str
    action: str
    current_sha256: str | None
    backup_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeBackupError(RuntimeError):
    pass


def format_backup_verification_summary(result: BackupVerification) -> str:
    """Return a compact operator-facing verification report.

    The raw manifest remains available to tooling, but the GUI should not dump
    a multi-page JSON object into a message box.
    """

    manifest = result.manifest if isinstance(result.manifest, dict) else {}
    entries = manifest.get("entries")
    entries = entries if isinstance(entries, list) else []
    status = "VALID — backup можно использовать" if result.valid else "INVALID — восстановление запрещено"
    lines = [
        f"Статус: {status}",
        f"Архив: {result.path}",
        f"Создан: {manifest.get('created_at') or '—'}",
        f"Версия программы: {manifest.get('app_version') or '—'}",
        f"Исходный runtime: {manifest.get('source_directory') or '—'}",
        f"Файлов: {len(entries)}",
        f"Токен включён: {'ДА' if manifest.get('token_included') else 'нет'}",
        f"Ошибок: {len(result.errors)}; предупреждений: {len(result.warnings)}",
    ]
    if entries:
        lines.append("")
        lines.append("Содержимое:")
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            size = int(raw.get("size_bytes") or 0)
            size_text = f"{size / 1024:.1f} KiB" if size >= 1024 else f"{size} B"
            schema = raw.get("schema_version")
            schema_text = f", schema {schema}" if schema is not None else ""
            digest = str(raw.get("sha256") or "")
            digest_text = digest[:12] + "…" if len(digest) > 12 else digest
            lines.append(
                f"• {raw.get('name') or '—'} — {raw.get('kind') or 'unknown'}"
                f"{schema_text}, {size_text}, SHA-256 {digest_text}"
            )
    if result.errors:
        lines.append("")
        lines.append("Ошибки:")
        lines.extend(f"• {item}" for item in result.errors)
    if result.warnings:
        lines.append("")
        lines.append("Предупреждения:")
        lines.extend(f"• {item}" for item in result.warnings)
    return "\n".join(lines)


class RuntimeBackupManager:
    FORMAT_VERSION = 1

    def __init__(
        self,
        runtime_dir: str | Path,
        *,
        app_version: str,
        runtime_files: Iterable[str] = DEFAULT_RUNTIME_FILES,
    ) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()
        self.app_version = str(app_version)
        self.runtime_files = tuple(dict.fromkeys(str(name) for name in runtime_files))
        self.lock_path = self.runtime_dir / "runtime_backup.lock"
        for name in self.runtime_files:
            if Path(name).name != name or name in _FORBIDDEN_BACKUP_NAMES:
                raise ValueError(f"Unsafe runtime backup member name: {name!r}")

    def create_backup(self, output: str | Path) -> Path:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() != ".zip":
            target = target.with_suffix(".zip")
        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            with tempfile.TemporaryDirectory(prefix="moex-runtime-backup-") as temp_name:
                staging = Path(temp_name)
                entries: list[BackupEntry] = []
                for name in self.runtime_files:
                    source = self.runtime_dir / name
                    if not source.exists():
                        continue
                    staged = staging / name
                    if name == "trading_events.db":
                        journal = EventJournal(source)
                        checkpoint = journal.checkpoint_wal("PASSIVE")
                        if int(checkpoint.get("busy", 0)) != 0:
                            raise RuntimeBackupError(
                                "SQLite WAL checkpoint is busy; stop active writers and retry."
                            )
                        journal.backup_to(staged)
                    else:
                        report = inspect_json_file(source)
                        if not report.valid:
                            raise RuntimeBackupError(
                                f"Cannot back up {name}: {report.status} — {report.detail}"
                            )
                        shutil.copy2(source, staged)
                    report = self._inspect_member(staged, name)
                    if not report.valid:
                        raise RuntimeBackupError(
                            f"Staged backup entry {name} failed integrity check: {report.detail}"
                        )
                    entries.append(
                        BackupEntry(
                            name=name,
                            size_bytes=int(staged.stat().st_size),
                            sha256=sha256_file(staged),
                            kind=report.kind,
                            schema_version=report.schema_version,
                        )
                    )
                if not entries:
                    raise RuntimeBackupError("No valid runtime files were found to back up.")
                manifest = BackupManifest(
                    format_version=self.FORMAT_VERSION,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    app_version=self.app_version,
                    source_directory=str(self.runtime_dir),
                    token_included=False,
                    entries=tuple(entries),
                )
                (staging / "manifest.json").write_text(
                    json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary = target.with_name(target.name + f".{uuid4().hex}.tmp")
                try:
                    with zipfile.ZipFile(
                        temporary,
                        "w",
                        compression=zipfile.ZIP_DEFLATED,
                        compresslevel=9,
                    ) as archive:
                        archive.write(staging / "manifest.json", arcname="manifest.json")
                        for entry in sorted(entries, key=lambda item: item.name):
                            archive.write(staging / entry.name, arcname=entry.name)
                    verification = self.verify_backup(temporary)
                    if not verification.valid:
                        raise RuntimeBackupError(
                            "Backup verification failed: " + "; ".join(verification.errors)
                        )
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        return target

    def verify_backup(self, path: str | Path) -> BackupVerification:
        target = Path(path)
        errors: list[str] = []
        warnings: list[str] = []
        manifest: dict[str, Any] | None = None
        if not target.exists():
            return BackupVerification(str(target), False, None, ("File not found.",), ())
        try:
            with zipfile.ZipFile(target, "r") as archive:
                listed_names = archive.namelist()
                names = set(listed_names)
                if len(names) != len(listed_names):
                    errors.append("Archive contains duplicate member names.")
                for name in listed_names:
                    if not self._safe_member_name(name):
                        errors.append(f"Unsafe archive member path: {name}")
                if "manifest.json" not in names:
                    errors.append("manifest.json is missing.")
                else:
                    manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                if not isinstance(manifest, dict):
                    errors.append("Manifest root is invalid.")
                else:
                    if manifest.get("format_version") != self.FORMAT_VERSION:
                        errors.append("Unsupported backup format version.")
                    if manifest.get("token_included") is not False:
                        errors.append("Backup manifest indicates that a token is included.")
                    entries = manifest.get("entries")
                    expected_names = {"manifest.json"}
                    if not isinstance(entries, list) or not entries:
                        errors.append("Backup manifest has no entries.")
                    else:
                        seen_entries: set[str] = set()
                        for raw in entries:
                            if not isinstance(raw, dict):
                                errors.append("Invalid manifest entry.")
                                continue
                            name = str(raw.get("name") or "")
                            if name in seen_entries:
                                errors.append(f"Duplicate manifest entry: {name}")
                            seen_entries.add(name)
                            expected_names.add(name)
                            if name not in self.runtime_files:
                                errors.append(f"Unexpected runtime member: {name}")
                            if not self._safe_member_name(name):
                                errors.append(f"Unsafe manifest member: {name}")
                            if name not in names:
                                errors.append(f"Missing archive member: {name}")
                                continue
                            data = archive.read(name)
                            digest = hashlib.sha256(data).hexdigest()
                            if digest != str(raw.get("sha256") or ""):
                                errors.append(f"Checksum mismatch: {name}")
                            try:
                                expected_size = int(raw.get("size_bytes"))
                            except (TypeError, ValueError):
                                expected_size = -1
                            if len(data) != expected_size:
                                errors.append(f"Size mismatch: {name}")
                            if name in _FORBIDDEN_BACKUP_NAMES or "token" in name.lower():
                                errors.append(f"Forbidden secret file in backup: {name}")
                    unexpected = sorted(names - expected_names)
                    if unexpected:
                        errors.append(
                            "Unexpected archive members: " + ", ".join(unexpected)
                        )
        except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
        return BackupVerification(
            path=str(target),
            valid=not errors,
            manifest=manifest,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def preview_restore(self, path: str | Path) -> tuple[RestorePreviewItem, ...]:
        verification = self.verify_backup(path)
        if not verification.valid or verification.manifest is None:
            raise RuntimeBackupError("Backup is invalid: " + "; ".join(verification.errors))
        items: list[RestorePreviewItem] = []
        for raw in verification.manifest.get("entries", []):
            name = str(raw["name"])
            current = self.runtime_dir / name
            current_hash = sha256_file(current) if current.exists() else None
            backup_hash = str(raw["sha256"])
            action = (
                "UNCHANGED"
                if current_hash == backup_hash
                else ("REPLACE" if current.exists() else "CREATE")
            )
            items.append(
                RestorePreviewItem(
                    name=name,
                    action=action,
                    current_sha256=current_hash,
                    backup_sha256=backup_hash,
                )
            )
        return tuple(items)

    def restore_backup(
        self,
        path: str | Path,
        *,
        confirmation: str,
    ) -> tuple[RestorePreviewItem, ...]:
        if confirmation.strip() != "RESTORE RUNTIME":
            raise RuntimeBackupError("Confirmation must be exactly RESTORE RUNTIME.")
        verification = self.verify_backup(path)
        if not verification.valid or verification.manifest is None:
            raise RuntimeBackupError("Backup is invalid: " + "; ".join(verification.errors))
        preview = self.preview_restore(path)
        target = Path(path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        with InterProcessFileLock(self.lock_path, timeout_seconds=5.0):
            with tempfile.TemporaryDirectory(prefix="moex-runtime-restore-") as temp_name:
                workspace = Path(temp_name)
                staging = workspace / "staging"
                rollback = workspace / "rollback"
                staging.mkdir()
                rollback.mkdir()
                entry_names = [str(raw["name"]) for raw in verification.manifest["entries"]]
                with zipfile.ZipFile(target, "r") as archive:
                    for name in entry_names:
                        (staging / name).write_bytes(archive.read(name))

                # Validate every candidate before changing a single runtime file.
                for name in entry_names:
                    report = self._inspect_member(staging / name, name)
                    if not report.valid:
                        raise RuntimeBackupError(
                            f"Staged restore file failed validation: {name}: {report.detail}"
                        )

                changed = [item for item in preview if item.action != "UNCHANGED"]
                existing_names: set[str] = set()
                for item in changed:
                    destination = self.runtime_dir / item.name
                    if not destination.exists():
                        continue
                    existing_names.add(item.name)
                    if item.name == "trading_events.db":
                        # A byte copy can omit uncheckpointed WAL pages and Windows
                        # may refuse to replace a database that the GUI/OneDrive has
                        # open.  Use SQLite's online backup API for a consistent
                        # rollback image instead.
                        self._sqlite_backup(destination, rollback / item.name)
                    else:
                        shutil.copy2(destination, rollback / item.name)

                applied: list[str] = []
                try:
                    for item in changed:
                        source = staging / item.name
                        destination = self.runtime_dir / item.name
                        if item.name == "trading_events.db":
                            # Do not os.replace() a live SQLite file on Windows.
                            # The SQLite backup API safely replaces database pages
                            # while preserving the path used by existing GUI objects.
                            self._restore_sqlite(source, destination)
                        else:
                            temporary = destination.with_name(
                                destination.name + f".{uuid4().hex}.restore.tmp"
                            )
                            try:
                                shutil.copy2(source, temporary)
                                self._replace_with_retry(temporary, destination)
                            finally:
                                temporary.unlink(missing_ok=True)
                            self._refresh_json_checksum_sidecar(destination)
                        applied.append(item.name)

                    # Post-commit validation detects filesystem or antivirus corruption.
                    for item in changed:
                        destination = self.runtime_dir / item.name
                        report = self._inspect_member(destination, item.name)
                        if item.name == "trading_events.db":
                            source_digest = self._sqlite_content_digest(staging / item.name)
                            destination_digest = self._sqlite_content_digest(destination)
                            content_matches = source_digest == destination_digest
                        else:
                            content_matches = sha256_file(destination) == item.backup_sha256
                        if not report.valid or not content_matches:
                            raise RuntimeBackupError(
                                f"Restored file failed post-commit validation: {item.name}: "
                                f"{report.detail}"
                            )
                except Exception as exc:
                    rollback_errors: list[str] = []
                    for name in reversed(applied):
                        destination = self.runtime_dir / name
                        try:
                            if name in existing_names:
                                if name == "trading_events.db":
                                    self._restore_sqlite(rollback / name, destination)
                                else:
                                    self._replace_with_retry(rollback / name, destination)
                                    self._refresh_json_checksum_sidecar(destination)
                            else:
                                destination.unlink(missing_ok=True)
                                if name == "trading_events.db":
                                    destination.with_name(destination.name + "-wal").unlink(missing_ok=True)
                                    destination.with_name(destination.name + "-shm").unlink(missing_ok=True)
                        except OSError as rollback_exc:
                            rollback_errors.append(f"{name}: {rollback_exc}")
                    detail = str(exc)
                    if rollback_errors:
                        detail += "; rollback errors: " + "; ".join(rollback_errors)
                    raise RuntimeBackupError(detail) from exc

                # Keep operator-visible point-in-time copies only after a successful
                # transactional restore. They are excluded from release archives.
                for name in existing_names:
                    source = rollback / name
                    if source.exists():
                        destination = self.runtime_dir / name
                        audit_copy = destination.with_name(
                            destination.name + f".pre_restore_{timestamp}.bak"
                        )
                        shutil.copy2(source, audit_copy)
        return preview

    @staticmethod
    def _refresh_json_checksum_sidecar(destination: Path) -> None:
        """Keep checksum-managed JSON consistent after transactional restore."""

        sidecar = destination.with_name(destination.name + ".sha256")
        if (
            destination.name not in _CHECKSUM_MANAGED_JSON_NAMES
            and not sidecar.exists()
        ):
            return
        temporary = sidecar.with_name(sidecar.name + f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(sha256_file(destination) + "\n", encoding="ascii")
            os.replace(temporary, sidecar)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace_with_retry(source: Path, destination: Path) -> None:
        """Replace a non-SQLite file with short Windows sharing retries."""

        delays = (0.0, 0.10, 0.25, 0.50, 1.00)
        last_error: OSError | None = None
        for delay in delays:
            if delay:
                time.sleep(delay)
            try:
                os.replace(source, destination)
                return
            except PermissionError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    @staticmethod
    def _sqlite_backup(source: Path, destination: Path) -> None:
        """Create a transactionally consistent SQLite rollback copy."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        source_connection = sqlite3.connect(source, timeout=10.0)
        target_connection = sqlite3.connect(destination, timeout=10.0)
        try:
            source_connection.execute("PRAGMA busy_timeout = 10000")
            target_connection.execute("PRAGMA busy_timeout = 10000")
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
            source_connection.close()
        report = inspect_sqlite_file(destination)
        if not report.valid:
            destination.unlink(missing_ok=True)
            raise RuntimeBackupError(
                f"SQLite rollback backup failed integrity check: {report.detail}"
            )

    @staticmethod
    def _sqlite_content_digest(path: Path) -> str:
        """Hash SQLite schema and rows, independent of page/WAL layout."""

        digest = hashlib.sha256()
        connection = sqlite3.connect(path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            schema_rows = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
            for row in schema_rows:
                digest.update(
                    json.dumps(
                        dict(row),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                )
                digest.update(b"\n")

            table_names = [
                str(row["name"])
                for row in schema_rows
                if str(row["type"]) == "table"
            ]
            for table in table_names:
                quoted = table.replace('"', '""')
                columns = connection.execute(
                    f'PRAGMA table_info("{quoted}")'
                ).fetchall()
                pk_columns = [
                    str(row[1])
                    for row in sorted(columns, key=lambda item: int(item[5] or 0))
                    if int(row[5] or 0) > 0
                ]
                if pk_columns:
                    order_clause = ", ".join(
                        '"' + name.replace('"', '""') + '"'
                        for name in pk_columns
                    )
                else:
                    order_clause = "rowid"
                try:
                    rows = connection.execute(
                        f'SELECT * FROM "{quoted}" ORDER BY {order_clause}'
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = connection.execute(
                        f'SELECT * FROM "{quoted}"'
                    ).fetchall()
                digest.update(f"TABLE:{table}\n".encode("utf-8"))
                for row in rows:
                    normalized = {
                        key: (value.hex() if isinstance(value, bytes) else value)
                        for key, value in dict(row).items()
                    }
                    digest.update(
                        json.dumps(
                            normalized,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ).encode("utf-8")
                    )
                    digest.update(b"\n")
        finally:
            connection.close()
        return digest.hexdigest()

    @staticmethod
    def _restore_sqlite(source: Path, destination: Path) -> None:
        """Restore SQLite through its backup API instead of file replacement.

        On Windows, os.replace(trading_events.db) fails with WinError 5 while
        SQLite, antivirus, or OneDrive temporarily holds the destination open.
        SQLite's online backup API is the supported way to update a live path.
        """

        destination.parent.mkdir(parents=True, exist_ok=True)
        report = inspect_sqlite_file(source)
        if not report.valid:
            raise RuntimeBackupError(
                f"SQLite restore source failed integrity check: {report.detail}"
            )
        last_error: sqlite3.Error | OSError | None = None
        for delay in (0.0, 0.10, 0.25, 0.50, 1.00):
            if delay:
                time.sleep(delay)
            source_connection: sqlite3.Connection | None = None
            target_connection: sqlite3.Connection | None = None
            try:
                source_connection = sqlite3.connect(source, timeout=10.0)
                target_connection = sqlite3.connect(destination, timeout=10.0)
                source_connection.execute("PRAGMA busy_timeout = 10000")
                target_connection.execute("PRAGMA busy_timeout = 10000")
                source_connection.backup(target_connection)
                target_connection.commit()
                checkpoint = target_connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if checkpoint and int(checkpoint[0]) != 0:
                    raise sqlite3.OperationalError(
                        "SQLite WAL checkpoint remained busy after restore."
                    )
                return
            except (sqlite3.Error, OSError) as exc:
                last_error = exc
            finally:
                if target_connection is not None:
                    target_connection.close()
                if source_connection is not None:
                    source_connection.close()
        assert last_error is not None
        raise RuntimeBackupError(
            "SQLite restore could not obtain a safe write window. "
            "Close other program copies and pause OneDrive/antivirus scanning, "
            f"then retry: {last_error}"
        ) from last_error

    @staticmethod
    def _safe_member_name(name: str) -> bool:
        path = Path(name)
        return (
            bool(name)
            and path.name == name
            and not path.is_absolute()
            and ".." not in path.parts
            and "\\" not in name
            and "/" not in name
        )

    @staticmethod
    def _inspect_member(path: Path, name: str) -> FileIntegrityReport:
        if name == "trading_events.db":
            return inspect_sqlite_file(path)
        if name == "portfolio_state.json":
            return inspect_json_file(
                path,
                expected_versions={PORTFOLIO_STATE_SCHEMA_VERSION},
                validator=validate_portfolio_document,
            )
        return inspect_json_file(path)
