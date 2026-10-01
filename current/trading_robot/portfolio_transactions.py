from __future__ import annotations

"""Single-writer canonical portfolio transactions for v3.7-alpha3."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .journal import EventJournal, JournalEvent
from .portfolio_model import (
    CompatibilityShadowStatus,
    PortfolioMigrationMetadata,
    PortfolioState,
)
from .portfolio_repository import (
    PortfolioRepository,
    PortfolioRepositoryError,
    PortfolioRevisionConflictError,
    portfolio_document_checksum,
)
from .state_persistence import atomic_write_json


class PortfolioTransactionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioTransactionResult:
    transaction_id: str
    operation: str
    committed: bool
    idempotent: bool
    old_revision: int
    new_revision: int
    state: PortfolioState
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "operation": self.operation,
            "committed": self.committed,
            "idempotent": self.idempotent,
            "old_revision": self.old_revision,
            "new_revision": self.new_revision,
            "detail": self.detail,
        }


class LegacyPortfolioShadowWriter:
    """Write-only rollback projection; never used to authorize an order."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, state: PortfolioState) -> CompatibilityShadowStatus:
        try:
            payload = {
                "version": 1,
                "source": "CANONICAL_WRITE_ONLY_SHADOW",
                "canonical_revision": state.revision,
                "account_id": state.account_id,
                "positions": [
                    {
                        "instrument_id": item.instrument_id,
                        "actual_lots": item.actual_lots,
                        "target_lots": item.target_lots,
                        "ownership": item.ownership.to_dict() if item.ownership else None,
                        "origin": item.origin.value,
                        "pending_order_ids": [
                            order.order_request_id for order in item.pending_orders
                        ],
                    }
                    for item in state.positions
                ],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_write_json(
                self.path,
                payload,
                write_checksum=True,
                keep_last_good=True,
                validate_roundtrip=True,
            )
            return CompatibilityShadowStatus.OK
        except Exception:
            return CompatibilityShadowStatus.DEGRADED


class PortfolioTransactionCoordinator:
    """The only supported writer for PortfolioState schema 2."""

    def __init__(
        self,
        repository: PortfolioRepository,
        *,
        journal: EventJournal | None = None,
        shadow_writer: LegacyPortfolioShadowWriter | None = None,
    ) -> None:
        self.repository = repository
        self.journal = journal
        self.shadow_writer = shadow_writer

    def commit(
        self,
        operation: str,
        transform: Callable[[PortfolioState], PortfolioState],
        *,
        expected_revision: int | None = None,
        expected_document_checksum: str | None = None,
        transaction_id: str | None = None,
        account_id: str | None = None,
        instrument_id: str | None = None,
        mode: str | None = None,
    ) -> PortfolioTransactionResult:
        tx_id = transaction_id or str(uuid4())
        current = self.repository.load(expected_account_id=account_id)
        old_revision = int(current.revision)
        if (
            current.last_transaction_id == tx_id
            and current.last_transaction_status == "COMMITTED"
        ):
            return PortfolioTransactionResult(
                transaction_id=tx_id,
                operation=operation,
                committed=True,
                idempotent=True,
                old_revision=old_revision,
                new_revision=old_revision,
                state=current,
                detail="Transaction was already committed.",
            )
        if expected_revision is not None and old_revision != int(expected_revision):
            raise PortfolioRevisionConflictError(
                f"Portfolio revision conflict: expected {expected_revision}, current {old_revision}."
            )
        if (expected_document_checksum is not None
                and portfolio_document_checksum(current) != expected_document_checksum):
            raise PortfolioRevisionConflictError("Portfolio document checksum conflict.")
        self._record(
            "CANONICAL_TRANSACTION_STARTED",
            account_id=account_id or current.account_id,
            instrument_id=instrument_id,
            mode=mode,
            status="started",
            payload={
                "transaction_id": tx_id,
                "operation": operation,
                "old_revision": old_revision,
            },
        )
        try:
            candidate = transform(current)
            if not isinstance(candidate, PortfolioState):
                raise PortfolioTransactionError("Transform must return PortfolioState.")
            if candidate.account_id and current.account_id and candidate.account_id != current.account_id:
                raise PortfolioTransactionError("Transaction cannot change account scope.")
            business_changed = candidate.decision_sha256 != current.decision_sha256
            new_revision = old_revision + 1 if business_changed else old_revision
            shadow_status = current.migration.compatibility_shadow_status
            candidate = replace(
                candidate,
                revision=new_revision,
                portfolio_source="CANONICAL",
                migration=PortfolioMigrationMetadata.completed(
                    source_schema=current.migration.source_schema,
                    migration_id=current.migration.migration_id,
                    migrated_at=current.migration.migrated_at,
                    shadow_status=shadow_status,
                    detail="Canonical-only read path is active.",
                ),
                last_transaction_id=tx_id,
                last_transaction_status="COMMITTED",
            )
            self.repository.save(
                candidate,
                expected_revision=old_revision,
                expected_document_checksum=portfolio_document_checksum(current),
                allow_equal_revision=True,
            )
            if self.shadow_writer is not None:
                shadow_status = self.shadow_writer.write(candidate)
                if shadow_status != candidate.migration.compatibility_shadow_status:
                    saved_checksum = portfolio_document_checksum(candidate)
                    candidate = replace(
                        candidate,
                        migration=PortfolioMigrationMetadata.completed(
                            source_schema=candidate.migration.source_schema,
                            migration_id=candidate.migration.migration_id,
                            migrated_at=candidate.migration.migrated_at,
                            shadow_status=shadow_status,
                            detail="Canonical-only read path is active.",
                        ),
                    )
                    self.repository.save(
                        candidate,
                        expected_revision=new_revision,
                        expected_document_checksum=saved_checksum,
                        allow_equal_revision=True,
                    )
                    self._record(
                        "LEGACY_SHADOW_UPDATED"
                        if shadow_status is CompatibilityShadowStatus.OK
                        else "LEGACY_SHADOW_WRITE_FAILED",
                        account_id=account_id or candidate.account_id,
                        instrument_id=instrument_id,
                        mode=mode,
                        status=shadow_status.value.lower(),
                        severity=(
                            "INFO"
                            if shadow_status is CompatibilityShadowStatus.OK
                            else "WARNING"
                        ),
                        payload={
                            "transaction_id": tx_id,
                            "canonical_revision": new_revision,
                            "shadow_status": shadow_status.value,
                        },
                    )
            result = PortfolioTransactionResult(
                transaction_id=tx_id,
                operation=operation,
                committed=True,
                idempotent=False,
                old_revision=old_revision,
                new_revision=new_revision,
                state=candidate,
                detail="Canonical transaction committed.",
            )
            self._record(
                "CANONICAL_TRANSACTION_COMMITTED",
                account_id=account_id or candidate.account_id,
                instrument_id=instrument_id,
                mode=mode,
                status="committed",
                payload=result.to_dict(),
            )
            return result
        except Exception as exc:
            self._record(
                "CANONICAL_TRANSACTION_FAILED",
                account_id=account_id or current.account_id,
                instrument_id=instrument_id,
                mode=mode,
                status="failed",
                severity="ERROR",
                payload={
                    "transaction_id": tx_id,
                    "operation": operation,
                    "old_revision": old_revision,
                    "error_class": type(exc).__name__,
                    "detail": str(exc),
                },
            )
            if isinstance(exc, PortfolioRepositoryError):
                raise
            raise PortfolioTransactionError(str(exc)) from exc

    def _record(
        self,
        event_type: str,
        *,
        account_id: str | None,
        instrument_id: str | None,
        mode: str | None,
        status: str,
        payload: dict[str, Any],
        severity: str = "INFO",
    ) -> None:
        if self.journal is None:
            return
        self.journal.record(
            JournalEvent(
                category="portfolio_transaction",
                event_type=event_type,
                severity=severity,
                account_id=account_id,
                instrument_id=instrument_id,
                mode=mode,
                status=status,
                payload=payload,
            )
        )
