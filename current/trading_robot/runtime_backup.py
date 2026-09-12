from __future__ import annotations

"""Validated runtime backup, preview and transactional restore for v3.7-alpha3."""

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Iterable
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .journal import EventJournal
from .locking import InterProcessFileLock
from .portfolio_model import PORTFOLIO_STATE_SCHEMA_VERSION, validate_portfolio_document
from .runtime_cash_authority import RuntimeCashAuthorityStore
from .runtime_integrity import (
    FileIntegrityReport,
    IntegrityStatus,
    inspect_cash_ledger_store,
    inspect_json_file,
    inspect_runtime_cash_authority,
    inspect_sqlite_file,
    sha256_file,
)

_CASH_LEDGER_ROOT_NAME = "cash_ledger_v3_10.sqlite3"
_CASH_LEDGER_DATABASE_MEMBER = "cash_ledger_v3_10.sqlite3/store.sqlite3"
_AUTHORITY_ACTIVE_NAME = "runtime_cash_authority.json"
_AUTHORITY_CHECKSUM_NAME = "runtime_cash_authority.json.sha256"
_AUTHORITY_LASTGOOD_NAME = "runtime_cash_authority.json.lastgood"

DEFAULT_RUNTIME_FILES: tuple[str, ...] = (
    "v3_8_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_config_manifest.json",
    "v3_9_enforced_runtime_manifest.json",
    "v3_9_m5_2_runtime_seed_manifest.json",
    "v3_9_external_close_ack_manifest.json",
    "v3_9_shadow_runtime_start_manifest.json",
    "portfolio_risk_metadata.json",
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
    _AUTHORITY_ACTIVE_NAME,
    _AUTHORITY_CHECKSUM_NAME,
    _AUTHORITY_LASTGOOD_NAME,
    _CASH_LEDGER_ROOT_NAME,
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
    "portfolio_risk_metadata.json",
    "canonical_migration_report.json",
    "portfolio_legacy_shadow.json",
    "v3_8_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_config_manifest.json",
    "v3_9_enforced_runtime_manifest.json",
    "v3_9_m5_2_runtime_seed_manifest.json",
    "v3_9_external_close_ack_manifest.json",
    "v3_9_shadow_runtime_start_manifest.json",
}

_LAST_GOOD_MANAGED_JSON_NAMES = {
    "portfolio_state.json",
    "multi_instrument_profiles.json",
    "instrument_runtimes.json",
    "central_order_state.json",
    "canonical_migration_report.json",
    "portfolio_legacy_shadow.json",
}

_JSON_RECOVERY_SUFFIXES = (".sha256", ".lastgood", ".lastgood.sha256")


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
    sqlite_sidecar_dispositions: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "created_at": self.created_at,
            "app_version": self.app_version,
            "source_directory": self.source_directory,
            "token_included": self.token_included,
            "entries": [entry.to_dict() for entry in self.entries],
            "sqlite_sidecar_dispositions": self.sqlite_sidecar_dispositions,
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
    status = (
        "VALID — backup можно использовать"
        if result.valid
        else "INVALID — восстановление запрещено"
    )
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
        with self._backup_locks():  # noqa: SIM117 - lock covers complete snapshot
            with tempfile.TemporaryDirectory(
                prefix="moex-runtime-backup-"
            ) as temp_name:
                staging = Path(temp_name)
                entries: list[BackupEntry] = []
                sidecar_dispositions: dict[str, str] = {}
                for name in self.runtime_files:
                    source = self.runtime_dir / name
                    if not source.exists():
                        continue
                    staged = staging / name
                    if name == _CASH_LEDGER_ROOT_NAME:
                        entry, dispositions = self._stage_cash_ledger(source, staged)
                        entries.append(entry)
                        sidecar_dispositions.update(dispositions)
                        continue
                    if name == "trading_events.db":
                        journal = EventJournal(source)
                        checkpoint = journal.checkpoint_wal("PASSIVE")
                        if int(checkpoint.get("busy", 0)) != 0:
                            raise RuntimeBackupError(
                                "SQLite WAL checkpoint is busy; stop active writers and retry."
                            )
                        journal.backup_to(staged)
                    else:
                        report = self._inspect_source_member(source, name)
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
                    for companion_name in self._managed_recovery_names(name):
                        companion_source = self.runtime_dir / companion_name
                        companion_staged = staging / companion_name
                        protected_name = companion_name.removesuffix(".sha256")
                        protected_source = self.runtime_dir / protected_name
                        if companion_name.endswith(
                            ".sha256"
                        ) and not protected_source.is_file():
                            protected = staging / protected_name
                            if not protected.is_file():
                                raise RuntimeBackupError(
                                    "Managed recovery checksum has no protected "
                                    f"member: {companion_name}"
                                )
                            companion_staged.write_bytes(
                                (sha256_file(protected) + "\n").encode("ascii")
                            )
                        elif companion_source.is_file():
                            shutil.copy2(companion_source, companion_staged)
                        elif companion_name.endswith(".lastgood"):
                            shutil.copy2(staged, companion_staged)
                        elif companion_name.endswith(".sha256"):
                            protected = staging / protected_name
                            if not protected.is_file():
                                raise RuntimeBackupError(
                                    "Managed recovery checksum has no protected "
                                    f"member: {companion_name}"
                                )
                            companion_staged.write_bytes(
                                (sha256_file(protected) + "\n").encode("ascii")
                            )
                        else:  # pragma: no cover - closed helper output
                            raise RuntimeBackupError(
                                f"Unsupported recovery member: {companion_name}"
                            )
                        companion_report = self._inspect_member(
                            companion_staged,
                            companion_name,
                        )
                        if not companion_report.valid:
                            raise RuntimeBackupError(
                                "Managed recovery member failed validation: "
                                f"{companion_name}: {companion_report.detail}"
                            )
                        entries.append(
                            BackupEntry(
                                name=companion_name,
                                size_bytes=int(companion_staged.stat().st_size),
                                sha256=sha256_file(companion_staged),
                                kind=companion_report.kind,
                                schema_version=companion_report.schema_version,
                            )
                        )
                if not entries:
                    raise RuntimeBackupError(
                        "No valid runtime files were found to back up."
                    )
                manifest = BackupManifest(
                    format_version=self.FORMAT_VERSION,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    app_version=self.app_version,
                    source_directory=str(self.runtime_dir),
                    token_included=False,
                    entries=tuple(entries),
                    sqlite_sidecar_dispositions=(
                        dict(sorted(sidecar_dispositions.items()))
                        if sidecar_dispositions
                        else None
                    ),
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
                        for entry in sorted(entries, key=lambda item: item.name):
                            archive.write(staging / entry.name, arcname=entry.name)
                        archive.write(
                            staging / "manifest.json", arcname="manifest.json"
                        )
                    verification = self.verify_backup(temporary)
                    if not verification.valid:
                        raise RuntimeBackupError(
                            "Backup verification failed: "
                            + "; ".join(verification.errors)
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
            return BackupVerification(
                str(target), False, None, ("File not found.",), ()
            )
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
                elif not listed_names or listed_names[-1] != "manifest.json":
                    errors.append("manifest.json must be the final archive member.")
                else:
                    manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                if not isinstance(manifest, dict):
                    errors.append("Manifest root is invalid.")
                else:
                    if manifest.get("format_version") != self.FORMAT_VERSION:
                        errors.append("Unsupported backup format version.")
                    if manifest.get("token_included") is not False:
                        errors.append(
                            "Backup manifest indicates that a token is included."
                        )
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
                            if not self._allowed_archive_member(name):
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
                            if (
                                name in _FORBIDDEN_BACKUP_NAMES
                                or "token" in name.lower()
                            ):
                                errors.append(
                                    f"Forbidden secret file in backup: {name}"
                                )
                            if self._safe_member_name(name):
                                with tempfile.TemporaryDirectory(
                                    prefix="moex-runtime-verify-member-"
                                ) as temp_name:
                                    staged = Path(temp_name) / name
                                    staged.parent.mkdir(parents=True, exist_ok=True)
                                    staged.write_bytes(data)
                                    report = self._inspect_member(staged, name)
                                if not report.valid:
                                    errors.append(
                                        "Runtime member failed content validation: "
                                        f"{name}: {report.status} — {report.detail}"
                                    )
                                if str(raw.get("kind") or "") != report.kind:
                                    errors.append(f"Kind mismatch: {name}")
                                if raw.get("schema_version") != report.schema_version:
                                    errors.append(f"Schema version mismatch: {name}")
                    unexpected = sorted(names - expected_names)
                    if unexpected:
                        errors.append(
                            "Unexpected archive members: " + ", ".join(unexpected)
                        )
                    if _AUTHORITY_ACTIVE_NAME in names:
                        with tempfile.TemporaryDirectory(
                            prefix="moex-runtime-verify-authority-"
                        ) as temp_name:
                            staged_root = Path(temp_name)
                            for name in (
                                _AUTHORITY_ACTIVE_NAME,
                                _AUTHORITY_CHECKSUM_NAME,
                                _AUTHORITY_LASTGOOD_NAME,
                            ):
                                if name in names:
                                    (staged_root / name).write_bytes(archive.read(name))
                            authority_report = inspect_runtime_cash_authority(
                                staged_root
                            )
                        if not authority_report.valid:
                            errors.append(
                                "Runtime member failed custody validation: "
                                + _AUTHORITY_ACTIVE_NAME
                            )
                    for primary_name in sorted(
                        names & (_CHECKSUM_MANAGED_JSON_NAMES | _LAST_GOOD_MANAGED_JSON_NAMES)
                    ):
                        for companion_name in self._managed_recovery_names(primary_name):
                            if companion_name not in names:
                                errors.append(
                                    "Managed recovery member is missing: "
                                    + companion_name
                                )
                                continue
                            protected_name = companion_name.removesuffix(".sha256")
                            if companion_name.endswith(".sha256"):
                                expected = hashlib.sha256(
                                    archive.read(protected_name)
                                ).hexdigest() + "\n"
                                try:
                                    actual = archive.read(companion_name).decode("ascii")
                                except UnicodeDecodeError:
                                    actual = ""
                                if not hmac.compare_digest(actual, expected):
                                    errors.append(
                                        "Managed recovery checksum mismatch: "
                                        + companion_name
                                    )
                    dispositions = manifest.get("sqlite_sidecar_dispositions")
                    if _CASH_LEDGER_DATABASE_MEMBER in names:
                        expected_disposition_keys = {
                            "cash_ledger_v3_10.sqlite3-wal",
                            "cash_ledger_v3_10.sqlite3-shm",
                        }
                        if (
                            not isinstance(dispositions, dict)
                            or set(dispositions) != expected_disposition_keys
                            or any(
                                value not in {"ABSENT", "SNAPSHOTTED_INTO_DATABASE"}
                                for value in dispositions.values()
                            )
                        ):
                            errors.append("CL2 SQLite sidecar disposition is invalid.")
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
            raise RuntimeBackupError(
                "Backup is invalid: " + "; ".join(verification.errors)
            )
        items: list[RestorePreviewItem] = []
        for raw in verification.manifest.get("entries", []):
            name = str(raw["name"])
            current = self.runtime_dir / name
            current_hash = sha256_file(current) if current.is_file() else None
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
            raise RuntimeBackupError(
                "Backup is invalid: " + "; ".join(verification.errors)
            )
        preview = self.preview_restore(path)
        target = Path(path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        with InterProcessFileLock(  # noqa: SIM117 - lock covers temp cleanup
            self.lock_path, timeout_seconds=5.0
        ):
            with tempfile.TemporaryDirectory(
                prefix="moex-runtime-restore-"
            ) as temp_name:
                workspace = Path(temp_name)
                staging = workspace / "staging"
                rollback = workspace / "rollback"
                staging.mkdir()
                rollback.mkdir()
                entry_names = [
                    str(raw["name"]) for raw in verification.manifest["entries"]
                ]
                with zipfile.ZipFile(target, "r") as archive:
                    for name in entry_names:
                        destination = staging / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(archive.read(name))

                # Validate every candidate before changing a single runtime file.
                for name in entry_names:
                    report = self._inspect_member(staging / name, name)
                    if not report.valid:
                        raise RuntimeBackupError(
                            f"Staged restore file failed validation: {name}: {report.detail}"
                        )

                changed = [item for item in preview if item.action != "UNCHANGED"]
                recovery_maintenance = [
                    item
                    for item in preview
                    if item.action == "UNCHANGED"
                    and self._is_recovery_managed_primary(item.name)
                    and not self._json_recovery_files_current(
                        self.runtime_dir / item.name
                    )
                ]
                transactional_items = [*changed, *recovery_maintenance]
                existing_names: set[str] = set()
                audit_names: set[str] = set()
                for item in transactional_items:
                    destination = self.runtime_dir / item.name
                    if self._is_recovery_managed_primary(item.name):
                        # Recovery companions can outlive a missing/corrupt
                        # primary. Preserve that exact pre-restore state too.
                        self._snapshot_json_recovery_files(destination, rollback)
                    if not destination.exists():
                        continue
                    existing_names.add(item.name)
                    if item.action != "UNCHANGED":
                        audit_names.add(item.name)
                    if self._is_sqlite_member(item.name):
                        # A byte copy can omit uncheckpointed WAL pages and Windows
                        # may refuse to replace a database that the GUI/OneDrive has
                        # open.  Use SQLite's online backup API for a consistent
                        # rollback image instead.
                        self._sqlite_backup(destination, rollback / item.name)
                    else:
                        shutil.copy2(destination, rollback / item.name)

                applied: list[str] = []
                try:
                    for item in transactional_items:
                        source = staging / item.name
                        destination = self.runtime_dir / item.name
                        if item.action == "UNCHANGED":
                            # An unchanged primary can still have missing or stale
                            # checksum/last-good companions. Repair those inside
                            # the same rollback boundary without rewriting the
                            # accepted primary or creating a misleading audit copy.
                            applied.append(item.name)
                            if self._is_recovery_managed_primary(item.name):
                                self._refresh_json_recovery_files(destination)
                            continue
                        if self._is_sqlite_member(item.name):
                            # Do not os.replace() a live SQLite file on Windows.
                            # The SQLite backup API safely replaces database pages
                            # while preserving the path used by existing GUI objects.
                            self._restore_sqlite(source, destination)
                            applied.append(item.name)
                        else:
                            temporary = destination.with_name(
                                destination.name + f".{uuid4().hex}.restore.tmp"
                            )
                            try:
                                shutil.copy2(source, temporary)
                                self._replace_with_retry(temporary, destination)
                            finally:
                                temporary.unlink(missing_ok=True)
                            applied.append(item.name)
                            self._refresh_json_recovery_files(destination)

                    # Post-commit validation detects filesystem or antivirus corruption.
                    for item in transactional_items:
                        destination = self.runtime_dir / item.name
                        report = self._inspect_member(destination, item.name)
                        if self._is_sqlite_member(item.name):
                            source_digest = self._sqlite_content_digest(
                                staging / item.name
                            )
                            destination_digest = self._sqlite_content_digest(
                                destination
                            )
                            content_matches = source_digest == destination_digest
                        else:
                            content_matches = (
                                sha256_file(destination) == item.backup_sha256
                            )
                        recovery_current = (
                            not self._is_recovery_managed_primary(item.name)
                            or self._json_recovery_files_current(destination)
                        )
                        if (
                            not report.valid
                            or not content_matches
                            or not recovery_current
                        ):
                            raise RuntimeBackupError(
                                f"Restored file failed post-commit validation: {item.name}: "
                                f"{report.detail}; recovery_current={recovery_current}"
                            )
                except Exception as exc:
                    rollback_errors: list[str] = []
                    for name in reversed(applied):
                        destination = self.runtime_dir / name
                        try:
                            if name in existing_names:
                                if self._is_sqlite_member(name):
                                    self._restore_sqlite(rollback / name, destination)
                                else:
                                    self._replace_with_retry(
                                        rollback / name, destination
                                    )
                                    if self._is_recovery_managed_primary(name):
                                        self._restore_json_recovery_snapshot(
                                            destination,
                                            rollback,
                                        )
                            else:
                                destination.unlink(missing_ok=True)
                                if self._is_sqlite_member(name):
                                    destination.with_name(
                                        destination.name + "-wal"
                                    ).unlink(missing_ok=True)
                                    destination.with_name(
                                        destination.name + "-shm"
                                    ).unlink(missing_ok=True)
                                else:
                                    self._restore_json_recovery_snapshot(
                                        destination,
                                        rollback,
                                    )
                        except (OSError, RuntimeBackupError) as rollback_exc:
                            rollback_errors.append(f"{name}: {rollback_exc}")
                    detail = str(exc)
                    if rollback_errors:
                        detail += "; rollback errors: " + "; ".join(rollback_errors)
                    raise RuntimeBackupError(detail) from exc

                # Keep operator-visible point-in-time copies only after a successful
                # transactional restore. They are excluded from release archives.
                for name in audit_names:
                    source = rollback / name
                    if source.exists():
                        destination = self.runtime_dir / name
                        audit_copy = destination.with_name(
                            destination.name + f".pre_restore_{timestamp}.bak"
                        )
                        shutil.copy2(source, audit_copy)
        return preview

    def restore_backup_isolated(
        self,
        path: str | Path,
        destination: str | Path,
    ) -> Path:
        """Restore a verified backup into a new empty directory only."""

        verification = self.verify_backup(path)
        if not verification.valid or verification.manifest is None:
            raise RuntimeBackupError(
                "Backup is invalid: " + "; ".join(verification.errors)
            )
        target = Path(destination).resolve()
        runtime_root = self.runtime_dir.resolve()
        if (
            target == runtime_root
            or runtime_root in target.parents
            or target.is_symlink()
        ):
            raise RuntimeBackupError("Isolated restore target is unsafe.")
        if target.exists():
            if not target.is_dir() or any(target.iterdir()):
                raise RuntimeBackupError(
                    "Isolated restore target must be new and empty."
                )
            target.rmdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(
            tempfile.mkdtemp(
                prefix=f".{target.name}.cl8-restore-",
                dir=target.parent,
            )
        )
        try:
            entry_names = [
                str(raw["name"]) for raw in verification.manifest.get("entries", [])
            ]
            with zipfile.ZipFile(path, "r") as archive:
                for name in entry_names:
                    if not self._safe_member_name(name):
                        raise RuntimeBackupError("Backup contains an unsafe member.")
                    restored = workspace / name
                    restored.parent.mkdir(parents=True, exist_ok=True)
                    restored.write_bytes(archive.read(name))
            for name in entry_names:
                report = self._inspect_member(workspace / name, name)
                if not report.valid:
                    raise RuntimeBackupError(
                        f"Isolated restore validation failed: {name}: {report.detail}"
                    )
            if _AUTHORITY_ACTIVE_NAME in entry_names:
                authority_report = inspect_runtime_cash_authority(workspace)
                if not authority_report.valid:
                    raise RuntimeBackupError("Isolated CL7 custody validation failed.")
            if _CASH_LEDGER_DATABASE_MEMBER in entry_names:
                ledger_report = inspect_cash_ledger_store(
                    workspace / _CASH_LEDGER_ROOT_NAME
                )
                if not ledger_report.valid:
                    raise RuntimeBackupError("Isolated CL2 custody validation failed.")
            if target.exists():
                raise RuntimeBackupError(
                    "Isolated restore target appeared during restore."
                )
            os.replace(workspace, target)
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise
        return target

    @staticmethod
    def _refresh_json_recovery_files(destination: Path) -> None:
        """Keep checksums and last-good recovery state aligned after restore."""

        if destination.name == _AUTHORITY_ACTIVE_NAME:
            # CL7 lastgood is the previous record in a CAS chain, not a copy of
            # active.  Its three custody members are restored explicitly.
            return

        sidecar = destination.with_name(destination.name + ".sha256")
        if (
            destination.name not in _CHECKSUM_MANAGED_JSON_NAMES
            and not sidecar.exists()
        ):
            return
        temporary = sidecar.with_name(sidecar.name + f".{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(
                (sha256_file(destination) + "\n").encode("ascii")
            )
            os.replace(temporary, sidecar)
        finally:
            temporary.unlink(missing_ok=True)

        if destination.name not in _LAST_GOOD_MANAGED_JSON_NAMES:
            return
        last_good = destination.with_name(destination.name + ".lastgood")
        last_good_temporary = last_good.with_name(
            last_good.name + f".{uuid4().hex}.tmp"
        )
        try:
            shutil.copy2(destination, last_good_temporary)
            os.replace(last_good_temporary, last_good)
        finally:
            last_good_temporary.unlink(missing_ok=True)
        last_good_sidecar = last_good.with_name(last_good.name + ".sha256")
        last_good_checksum_temporary = last_good_sidecar.with_name(
            last_good_sidecar.name + f".{uuid4().hex}.tmp"
        )
        try:
            last_good_checksum_temporary.write_bytes(
                (sha256_file(last_good) + "\n").encode("ascii")
            )
            os.replace(last_good_checksum_temporary, last_good_sidecar)
        finally:
            last_good_checksum_temporary.unlink(missing_ok=True)

    @staticmethod
    def _json_recovery_files_current(destination: Path) -> bool:
        """Return whether managed checksum/last-good companions match primary."""

        if destination.name == _AUTHORITY_ACTIVE_NAME:
            return inspect_runtime_cash_authority(destination).valid

        sidecar = destination.with_name(destination.name + ".sha256")
        checksum_required = (
            destination.name in _CHECKSUM_MANAGED_JSON_NAMES or sidecar.exists()
        )
        if (
            checksum_required
            and not inspect_json_file(
                destination,
                require_checksum=True,
            ).valid
        ):
            return False
        if destination.name not in _LAST_GOOD_MANAGED_JSON_NAMES:
            return True
        last_good = destination.with_name(destination.name + ".lastgood")
        if not last_good.is_file():
            return False
        return (
            RuntimeBackupManager._inspect_member(
                last_good,
                destination.name,
            ).valid
            and inspect_json_file(last_good, require_checksum=True).valid
        )

    @staticmethod
    def _snapshot_json_recovery_files(destination: Path, rollback: Path) -> None:
        for suffix in _JSON_RECOVERY_SUFFIXES:
            companion = destination.with_name(destination.name + suffix)
            if companion.is_file():
                shutil.copy2(companion, rollback / companion.name)

    @staticmethod
    def _restore_json_recovery_snapshot(destination: Path, rollback: Path) -> None:
        for suffix in _JSON_RECOVERY_SUFFIXES:
            companion = destination.with_name(destination.name + suffix)
            saved = rollback / companion.name
            if saved.is_file():
                temporary = companion.with_name(
                    companion.name + f".{uuid4().hex}.rollback.tmp"
                )
                try:
                    shutil.copy2(saved, temporary)
                    os.replace(temporary, companion)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                companion.unlink(missing_ok=True)

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
        report = (
            inspect_cash_ledger_store(destination.parent)
            if RuntimeBackupManager._is_cash_ledger_database_path(source)
            else inspect_sqlite_file(destination)
        )
        if not report.valid:
            destination.unlink(missing_ok=True)
            raise RuntimeBackupError(
                f"SQLite rollback backup failed integrity check: {report.detail}"
            )

    @staticmethod
    def _stage_cash_ledger(
        source: Path,
        destination: Path,
    ) -> tuple[BackupEntry, dict[str, str]]:
        source_wal_present = (source / "store.sqlite3-wal").exists()
        source_shm_present = (source / "store.sqlite3-shm").exists()
        report = inspect_cash_ledger_store(source)
        if not report.valid:
            raise RuntimeBackupError(
                "Cannot back up cash_ledger_v3_10.sqlite3: " + report.detail
            )
        source_database = source / "store.sqlite3"
        staged_database = destination / "store.sqlite3"
        destination.mkdir(parents=True, exist_ok=True)
        source_connection = sqlite3.connect(source_database, timeout=10.0)
        target_connection = sqlite3.connect(staged_database, timeout=10.0)
        try:
            source_connection.execute("PRAGMA busy_timeout=10000")
            target_connection.execute("PRAGMA busy_timeout=10000")
            source_connection.backup(target_connection)
            target_connection.commit()
            checkpoint = target_connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            if checkpoint and int(checkpoint[0]) != 0:
                raise RuntimeBackupError("CL2 backup checkpoint remained busy.")
        except sqlite3.Error as exc:
            raise RuntimeBackupError("CL2 SQLite backup failed safely.") from exc
        finally:
            target_connection.close()
            source_connection.close()
        staged_report = inspect_cash_ledger_store(destination)
        if not staged_report.valid:
            raise RuntimeBackupError(
                "Staged CL2 backup failed integrity validation: " + staged_report.detail
            )
        entry = BackupEntry(
            name=_CASH_LEDGER_DATABASE_MEMBER,
            size_bytes=staged_database.stat().st_size,
            sha256=sha256_file(staged_database),
            kind=staged_report.kind,
            schema_version=staged_report.schema_version,
        )
        dispositions = {
            "cash_ledger_v3_10.sqlite3-wal": (
                "SNAPSHOTTED_INTO_DATABASE" if source_wal_present else "ABSENT"
            ),
            "cash_ledger_v3_10.sqlite3-shm": (
                "SNAPSHOTTED_INTO_DATABASE" if source_shm_present else "ABSENT"
            ),
        }
        return entry, dispositions

    @contextmanager
    def _backup_locks(self):
        """Freeze the accepted JSON stores in the CL7 global lock order."""

        authority_store = RuntimeCashAuthorityStore(self.runtime_dir)
        with ExitStack() as stack:
            if authority_store.custody_exists():
                stack.enter_context(authority_store.locked())
            for name in (
                "portfolio_state.json",
                "risk_profiles.json",
                "risk_state.json",
                "central_order_state.json",
            ):
                stack.enter_context(
                    InterProcessFileLock(
                        self.runtime_dir / f"{name}.lock",
                        timeout_seconds=5.0,
                    )
                )
            stack.enter_context(
                InterProcessFileLock(self.lock_path, timeout_seconds=5.0)
            )
            yield

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
                str(row["name"]) for row in schema_rows if str(row["type"]) == "table"
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
                        '"' + name.replace('"', '""') + '"' for name in pk_columns
                    )
                else:
                    order_clause = "rowid"
                try:
                    rows = connection.execute(
                        f'SELECT * FROM "{quoted}" ORDER BY {order_clause}'
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = connection.execute(f'SELECT * FROM "{quoted}"').fetchall()
                digest.update(f"TABLE:{table}\n".encode())
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
        report = (
            inspect_cash_ledger_store(source.parent)
            if RuntimeBackupManager._is_cash_ledger_database_path(source)
            else inspect_sqlite_file(source)
        )
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
        normalized = name.replace("\\", "/")
        parts = tuple(part for part in normalized.split("/") if part)
        return (
            bool(name)
            and bool(parts)
            and normalized == "/".join(parts)
            and not path.is_absolute()
            and ".." not in path.parts
            and "\\" not in name
            and ":" not in name
        )

    def _allowed_archive_member(self, name: str) -> bool:
        return (
            name in self.runtime_files
            or any(
                primary in self.runtime_files
                and name in self._managed_recovery_names(primary)
                for primary in (_CHECKSUM_MANAGED_JSON_NAMES | _LAST_GOOD_MANAGED_JSON_NAMES)
            )
            or (
            _CASH_LEDGER_ROOT_NAME in self.runtime_files
            and name == _CASH_LEDGER_DATABASE_MEMBER
            )
        )

    @staticmethod
    def _managed_recovery_names(primary_name: str) -> tuple[str, ...]:
        names: list[str] = []
        if primary_name in _CHECKSUM_MANAGED_JSON_NAMES:
            names.append(primary_name + ".sha256")
        if primary_name in _LAST_GOOD_MANAGED_JSON_NAMES:
            names.extend(
                (
                    primary_name + ".lastgood",
                    primary_name + ".lastgood.sha256",
                )
            )
        return tuple(names)

    @staticmethod
    def _is_sqlite_member(name: str) -> bool:
        return name in {"trading_events.db", _CASH_LEDGER_DATABASE_MEMBER}

    @staticmethod
    def _is_recovery_managed_primary(name: str) -> bool:
        return name in (
            _CHECKSUM_MANAGED_JSON_NAMES | _LAST_GOOD_MANAGED_JSON_NAMES
        )

    @staticmethod
    def _is_cash_ledger_database_path(path: Path) -> bool:
        return (
            path.name == "store.sqlite3" and path.parent.name == _CASH_LEDGER_ROOT_NAME
        )

    @staticmethod
    def _inspect_source_member(path: Path, name: str) -> FileIntegrityReport:
        if name == _AUTHORITY_ACTIVE_NAME:
            return inspect_runtime_cash_authority(path)
        if name == _AUTHORITY_CHECKSUM_NAME:
            return RuntimeBackupManager._inspect_checksum_member(path, name)
        if name == _AUTHORITY_LASTGOOD_NAME:
            return RuntimeBackupManager._inspect_authority_json_member(path, name)
        require_checksum = name in _CHECKSUM_MANAGED_JSON_NAMES
        if name == "portfolio_state.json":
            return inspect_json_file(
                path,
                expected_versions={PORTFOLIO_STATE_SCHEMA_VERSION},
                validator=validate_portfolio_document,
                require_checksum=require_checksum,
            )
        return inspect_json_file(path, require_checksum=require_checksum)

    @staticmethod
    def _inspect_member(path: Path, name: str) -> FileIntegrityReport:
        if name == "trading_events.db":
            return inspect_sqlite_file(path)
        if name == _CASH_LEDGER_DATABASE_MEMBER:
            return inspect_cash_ledger_store(path.parent)
        if name in {_AUTHORITY_ACTIVE_NAME, _AUTHORITY_LASTGOOD_NAME}:
            return RuntimeBackupManager._inspect_authority_json_member(path, name)
        if name == _AUTHORITY_CHECKSUM_NAME:
            return RuntimeBackupManager._inspect_checksum_member(path, name)
        if name.endswith(".sha256"):
            return RuntimeBackupManager._inspect_checksum_member(path, name)
        if name.endswith(".lastgood"):
            return RuntimeBackupManager._inspect_member(
                path,
                name.removesuffix(".lastgood"),
            )
        if name == "portfolio_state.json":
            return inspect_json_file(
                path,
                expected_versions={PORTFOLIO_STATE_SCHEMA_VERSION},
                validator=validate_portfolio_document,
            )
        return inspect_json_file(path)

    @staticmethod
    def _inspect_checksum_member(path: Path, name: str) -> FileIntegrityReport:
        try:
            raw = path.read_bytes()
        except OSError:
            return FileIntegrityReport(
                path=str(path),
                status=IntegrityStatus.UNREADABLE,
                kind="sha256",
                detail="Checksum sidecar is unreadable.",
            )
        valid = (
            len(raw) == 65
            and raw.endswith(b"\n")
            and all(byte in b"0123456789abcdef" for byte in raw[:-1])
        )
        return FileIntegrityReport(
            path=str(path),
            status=IntegrityStatus.VALID if valid else IntegrityStatus.CORRUPT,
            kind="sha256",
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            detail=(
                "Checksum sidecar is canonical."
                if valid
                else "Checksum sidecar is non-canonical."
            ),
        )

    @staticmethod
    def _inspect_authority_json_member(path: Path, name: str) -> FileIntegrityReport:
        try:
            from .runtime_cash_authority import RuntimeCashAuthorityRecord

            raw = path.read_bytes()
            record = RuntimeCashAuthorityRecord.from_canonical_bytes(raw)
        except (OSError, RuntimeError, TypeError, ValueError):
            return FileIntegrityReport(
                path=str(path),
                status=IntegrityStatus.CORRUPT,
                kind="runtime_cash_authority",
                detail="CL7 authority member is unreadable or non-canonical.",
            )
        return FileIntegrityReport(
            path=str(path),
            status=IntegrityStatus.VALID,
            kind="runtime_cash_authority",
            size_bytes=len(raw),
            sha256=record.sha256,
            schema_version=record.version,
            detail=f"CL7 authority member is canonical: {name}.",
        )
