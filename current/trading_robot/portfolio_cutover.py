from __future__ import annotations

"""Safe PortfolioState schema 1 -> 2 canonical cutover for v3.7-alpha3."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from .journal import EventJournal, JournalEvent
from .portfolio_model import (
    LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION,
    PORTFOLIO_STATE_SCHEMA_VERSION,
    CompatibilityShadowStatus,
    PortfolioMigrationMetadata,
    PortfolioState,
    ReconciliationStatus,
)
from .portfolio_repository import PortfolioRepository, PortfolioRepositoryError
from .portfolio_transactions import PortfolioTransactionCoordinator
from .state_persistence import atomic_write_json


@dataclass(frozen=True, slots=True)
class PortfolioCutoverPreview:
    allowed: bool
    already_migrated: bool
    source_schema: int | None
    target_schema: int
    account_id: str | None
    revision: int | None
    blocking_reasons: tuple[str, ...]
    position_count: int
    active_pending_order_ids: tuple[str, ...]
    uncertain_order_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "already_migrated": self.already_migrated,
            "source_schema": self.source_schema,
            "target_schema": self.target_schema,
            "account_id": self.account_id,
            "revision": self.revision,
            "blocking_reasons": list(self.blocking_reasons),
            "position_count": self.position_count,
            "active_pending_order_ids": list(self.active_pending_order_ids),
            "uncertain_order_ids": list(self.uncertain_order_ids),
        }


@dataclass(frozen=True, slots=True)
class PortfolioCutoverResult:
    success: bool
    migration_id: str | None
    status: str
    preview: PortfolioCutoverPreview
    backup_path: str | None
    report_path: str
    old_revision: int | None
    new_revision: int | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "migration_id": self.migration_id,
            "status": self.status,
            "preview": self.preview.to_dict(),
            "backup_path": self.backup_path,
            "report_path": self.report_path,
            "old_revision": self.old_revision,
            "new_revision": self.new_revision,
            "detail": self.detail,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


class PortfolioCutoverManager:
    CONFIRMATION = "CUTOVER PORTFOLIO STATE 2"

    def __init__(
        self,
        repository: PortfolioRepository,
        *,
        journal: EventJournal | None = None,
        report_path: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.journal = journal
        self.report_path = Path(report_path or repository.path.with_name("canonical_migration_report.json"))

    def preview(
        self,
        *,
        expected_account_id: str | None = None,
        execution_active: bool = False,
    ) -> PortfolioCutoverPreview:
        source_schema = self.repository.raw_schema_version()
        if source_schema is None:
            return PortfolioCutoverPreview(
                allowed=True,
                already_migrated=False,
                source_schema=None,
                target_schema=PORTFOLIO_STATE_SCHEMA_VERSION,
                account_id=expected_account_id,
                revision=None,
                blocking_reasons=(),
                position_count=0,
                active_pending_order_ids=(),
                uncertain_order_ids=(),
            )
        # Load without expected scope so preview can report an account mismatch
        # as a controlled block instead of raising before the report is built.
        state = self.repository.load()
        if source_schema == PORTFOLIO_STATE_SCHEMA_VERSION and state.migration.complete:
            return PortfolioCutoverPreview(
                allowed=True,
                already_migrated=True,
                source_schema=source_schema,
                target_schema=PORTFOLIO_STATE_SCHEMA_VERSION,
                account_id=state.account_id or None,
                revision=state.revision,
                blocking_reasons=(),
                position_count=len(state.positions),
                active_pending_order_ids=(),
                uncertain_order_ids=(),
            )
        reasons: list[str] = []
        active_ids: list[str] = []
        uncertain_ids: list[str] = []
        if execution_active:
            reasons.append("Execution is active; stop Dry-run/Sandbox before cutover.")
        if source_schema != LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION:
            reasons.append(f"Unsupported source schema {source_schema}.")
        if expected_account_id and state.account_id and state.account_id != expected_account_id:
            reasons.append("Portfolio account does not match selected account.")
        if state.freshness.value not in {"FRESH", "UNAVAILABLE"}:
            reasons.append(f"Portfolio snapshot freshness is {state.freshness.value}.")
        if state.state_status in {"BLOCKED", "MANUAL_REVIEW_REQUIRED"} or state.blocking:
            reasons.append(f"Portfolio state is {state.state_status} / blocking.")
        for position in state.positions:
            if position.reconciliation.status is not ReconciliationStatus.MATCHED:
                reasons.append(
                    f"{position.instrument_id}: reconciliation={position.reconciliation.status.value}."
                )
            for order in position.pending_orders:
                if order.uncertain:
                    uncertain_ids.append(order.order_request_id)
                elif order.active:
                    active_ids.append(order.order_request_id)
        if active_ids:
            reasons.append("Active pending order exists.")
        if uncertain_ids:
            reasons.append("Uncertain submit/order exists.")
        return PortfolioCutoverPreview(
            allowed=not reasons,
            already_migrated=False,
            source_schema=source_schema,
            target_schema=PORTFOLIO_STATE_SCHEMA_VERSION,
            account_id=state.account_id or None,
            revision=state.revision,
            blocking_reasons=tuple(dict.fromkeys(reasons)),
            position_count=len(state.positions),
            active_pending_order_ids=tuple(sorted(set(active_ids))),
            uncertain_order_ids=tuple(sorted(set(uncertain_ids))),
        )

    def execute(
        self,
        *,
        confirmation: str,
        expected_account_id: str | None = None,
        execution_active: bool = False,
    ) -> PortfolioCutoverResult:
        preview = self.preview(
            expected_account_id=expected_account_id,
            execution_active=execution_active,
        )
        if preview.already_migrated:
            result = PortfolioCutoverResult(
                success=True,
                migration_id=None,
                status="ALREADY_MIGRATED",
                preview=preview,
                backup_path=None,
                report_path=str(self.report_path),
                old_revision=preview.revision,
                new_revision=preview.revision,
                detail="PortfolioState schema 2 is already active.",
            )
            self._write_report(result)
            return result
        if confirmation != self.CONFIRMATION:
            result = self._blocked_result(preview, "Exact cutover confirmation is required.")
            self._record("CANONICAL_MIGRATION_BLOCKED", result)
            self._write_report(result)
            return result
        if not preview.allowed:
            result = self._blocked_result(preview, "; ".join(preview.blocking_reasons))
            self._record("CANONICAL_MIGRATION_BLOCKED", result)
            self._write_report(result)
            return result
        migration_id = str(uuid4())
        self._record_raw(
            "CANONICAL_MIGRATION_STARTED",
            account_id=preview.account_id,
            status="started",
            payload={"migration_id": migration_id, **preview.to_dict()},
        )
        self._record_raw(
            "CANONICAL_MIGRATION_PREVIEWED",
            account_id=preview.account_id,
            status="allowed",
            payload={"migration_id": migration_id, **preview.to_dict()},
        )
        backup_path: Path | None = None
        try:
            if self.repository.path.exists():
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = self.repository.path.with_name(
                    f"{self.repository.path.stem}.schema1.{timestamp}.backup.json"
                )
                shutil.copy2(self.repository.path, backup_path)
                digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
                backup_path.with_name(backup_path.name + ".sha256").write_text(
                    f"{digest}  {backup_path.name}\n", encoding="ascii"
                )
            state = self.repository.load(expected_account_id=expected_account_id)
            migrated_at = datetime.now(timezone.utc).isoformat()
            coordinator = PortfolioTransactionCoordinator(
                self.repository,
                journal=self.journal,
            )
            transaction = coordinator.commit(
                "CANONICAL_CUTOVER",
                lambda current: replace(
                    current,
                    version=PORTFOLIO_STATE_SCHEMA_VERSION,
                    portfolio_source="CANONICAL",
                    migration=PortfolioMigrationMetadata.completed(
                        source_schema=LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION,
                        migration_id=migration_id,
                        migrated_at=migrated_at,
                        shadow_status=CompatibilityShadowStatus.NOT_CONFIGURED,
                        detail="Schema 1 -> 2 cutover completed; legacy reads disabled.",
                    ),
                    generated_at=migrated_at,
                    state_status=("EMPTY" if not current.positions else current.state_status),
                ),
                expected_revision=state.revision,
                transaction_id=f"cutover:{migration_id}",
                account_id=state.account_id or expected_account_id,
            )
            result = PortfolioCutoverResult(
                success=True,
                migration_id=migration_id,
                status="COMPLETED",
                preview=preview,
                backup_path=str(backup_path) if backup_path else None,
                report_path=str(self.report_path),
                old_revision=transaction.old_revision,
                new_revision=transaction.new_revision,
                detail="Canonical PortfolioState schema 2 cutover completed.",
            )
            self._record("CANONICAL_CUTOVER_COMPLETED", result)
            self._write_report(result)
            return result
        except Exception as exc:
            result = PortfolioCutoverResult(
                success=False,
                migration_id=migration_id,
                status="FAILED",
                preview=preview,
                backup_path=str(backup_path) if backup_path else None,
                report_path=str(self.report_path),
                old_revision=preview.revision,
                new_revision=None,
                detail=f"Canonical cutover failed: {type(exc).__name__}: {exc}",
            )
            self._record("CANONICAL_MIGRATION_BLOCKED", result, severity="ERROR")
            self._write_report(result)
            return result

    def _blocked_result(self, preview: PortfolioCutoverPreview, detail: str) -> PortfolioCutoverResult:
        return PortfolioCutoverResult(
            success=False,
            migration_id=None,
            status="BLOCKED",
            preview=preview,
            backup_path=None,
            report_path=str(self.report_path),
            old_revision=preview.revision,
            new_revision=None,
            detail=detail,
        )

    def _write_report(self, result: PortfolioCutoverResult) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self.report_path,
            result.to_dict(),
            write_checksum=True,
            keep_last_good=True,
            validate_roundtrip=True,
        )

    def _record(self, event_type: str, result: PortfolioCutoverResult, *, severity: str = "INFO") -> None:
        self._record_raw(
            event_type,
            account_id=result.preview.account_id,
            status=result.status.lower(),
            severity=severity,
            payload=result.to_dict(),
        )

    def _record_raw(
        self,
        event_type: str,
        *,
        account_id: str | None,
        status: str,
        payload: dict[str, Any],
        severity: str = "INFO",
    ) -> None:
        if self.journal is None:
            return
        self.journal.record(
            JournalEvent(
                category="portfolio_migration",
                event_type=event_type,
                severity=severity,
                account_id=account_id,
                status=status,
                payload=payload,
            )
        )
