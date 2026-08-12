from __future__ import annotations

"""Canonical-only Portfolio Manager preflight for v3.7-alpha3.

The module is pure domain logic.  It freezes one immutable canonical snapshot,
authorizes Risk and Execution from one immutable schema-2 revision. Legacy
projections are optional test/rollback diagnostics and never authorize orders.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping

from .portfolio_model import (
    OwnershipStatus,
    PortfolioState,
    PositionOrigin,
    ReconciliationStatus,
    SnapshotFreshness,
)
from .portfolio_repository import PortfolioRepository, PortfolioRepositoryError


class PortfolioPreflightStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


class PortfolioDualReadStatus(StrEnum):
    DISABLED = "DISABLED"
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True, slots=True)
class LegacyPortfolioView:
    """Safety-relevant projection of the accepted v3.6 runtime state."""

    account_id: str
    mode: str
    instrument_id: str
    actual_lots: int | None
    target_lots: int | None
    owner_strategy_id: str | None = None
    owner_config_hash: str | None = None
    pending_order_ids: tuple[str, ...] = ()
    uncertain_order_ids: tuple[str, ...] = ()
    external_activity: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", str(self.account_id).strip())
        object.__setattr__(self, "mode", str(self.mode).strip().upper())
        object.__setattr__(self, "instrument_id", str(self.instrument_id).strip())
        object.__setattr__(
            self,
            "actual_lots",
            None if self.actual_lots is None else int(self.actual_lots),
        )
        object.__setattr__(
            self,
            "target_lots",
            None if self.target_lots is None else int(self.target_lots),
        )
        object.__setattr__(
            self,
            "pending_order_ids",
            tuple(sorted({str(item) for item in self.pending_order_ids if str(item)})),
        )
        object.__setattr__(
            self,
            "uncertain_order_ids",
            tuple(sorted({str(item) for item in self.uncertain_order_ids if str(item)})),
        )

    @property
    def pending(self) -> bool:
        return bool(self.pending_order_ids or self.uncertain_order_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "mode": self.mode,
            "instrument_id": self.instrument_id,
            "actual_lots": self.actual_lots,
            "target_lots": self.target_lots,
            "owner_strategy_id": self.owner_strategy_id,
            "owner_config_hash": self.owner_config_hash,
            "pending_order_ids": list(self.pending_order_ids),
            "uncertain_order_ids": list(self.uncertain_order_ids),
            "external_activity": self.external_activity,
        }


@dataclass(frozen=True, slots=True)
class PortfolioSnapshotLease:
    """Immutable authorization lease over one canonical snapshot revision."""

    state: PortfolioState
    revision: int
    decision_checksum: str
    document_checksum: str
    leased_at: str

    @classmethod
    def from_state(
        cls,
        state: PortfolioState,
        *,
        leased_at: str | None = None,
    ) -> "PortfolioSnapshotLease":
        document = json.dumps(
            state.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            state=state,
            revision=int(state.revision),
            decision_checksum=state.decision_sha256,
            document_checksum=hashlib.sha256(document).hexdigest(),
            leased_at=leased_at or datetime.now(timezone.utc).isoformat(),
        )

    @property
    def account_id(self) -> str:
        return self.state.account_id

    def to_dict(self, *, include_state: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "account_id": self.account_id,
            "revision": self.revision,
            "decision_checksum": self.decision_checksum,
            "document_checksum": self.document_checksum,
            "snapshot_at": self.state.snapshot_at,
            "leased_at": self.leased_at,
            "freshness": self.state.freshness.value,
            "state_status": self.state.state_status,
            "blocking": self.state.blocking,
        }
        if include_state:
            result["state"] = self.state.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class PortfolioDualReadResult:
    status: PortfolioDualReadStatus
    reasons: tuple[str, ...]
    canonical_actual_lots: int
    legacy_actual_lots: int | None
    canonical_target_lots: int | None
    legacy_target_lots: int | None
    canonical_owner_strategy_id: str | None
    legacy_owner_strategy_id: str | None
    canonical_pending_order_ids: tuple[str, ...]
    legacy_pending_order_ids: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return self.status is not PortfolioDualReadStatus.MISMATCH

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "matched": self.matched,
            "reasons": list(self.reasons),
            "canonical_actual_lots": self.canonical_actual_lots,
            "legacy_actual_lots": self.legacy_actual_lots,
            "canonical_target_lots": self.canonical_target_lots,
            "legacy_target_lots": self.legacy_target_lots,
            "canonical_owner_strategy_id": self.canonical_owner_strategy_id,
            "legacy_owner_strategy_id": self.legacy_owner_strategy_id,
            "canonical_pending_order_ids": list(self.canonical_pending_order_ids),
            "legacy_pending_order_ids": list(self.legacy_pending_order_ids),
        }


@dataclass(frozen=True, slots=True)
class PortfolioPreflightContext:
    account_id: str
    mode: str
    instrument_id: str
    snapshot_revision: int
    snapshot_decision_checksum: str
    snapshot_document_checksum: str
    snapshot_at: str
    freshness: SnapshotFreshness
    state_status: str
    portfolio_source: str
    migration_status: str
    legacy_read_path_enabled: bool
    actual_lots: int
    canonical_target_lots: int | None
    proposed_target_lots: int
    ownership_status: OwnershipStatus
    origin: PositionOrigin
    reconciliation_status: ReconciliationStatus
    pending_order_ids: tuple[str, ...]
    uncertain_order_ids: tuple[str, ...]
    dual_read: PortfolioDualReadResult
    blocking_reasons: tuple[str, ...]
    evaluated_at: str

    @property
    def blocking(self) -> bool:
        return bool(self.blocking_reasons)

    @property
    def active_pending_order_ids(self) -> tuple[str, ...]:
        return tuple(sorted({*self.pending_order_ids, *self.uncertain_order_ids}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "mode": self.mode,
            "instrument_id": self.instrument_id,
            "snapshot_revision": self.snapshot_revision,
            "snapshot_decision_checksum": self.snapshot_decision_checksum,
            "snapshot_document_checksum": self.snapshot_document_checksum,
            "snapshot_at": self.snapshot_at,
            "freshness": self.freshness.value,
            "state_status": self.state_status,
            "portfolio_source": self.portfolio_source,
            "migration_status": self.migration_status,
            "legacy_read_path_enabled": self.legacy_read_path_enabled,
            "actual_lots": self.actual_lots,
            "canonical_target_lots": self.canonical_target_lots,
            "proposed_target_lots": self.proposed_target_lots,
            "ownership_status": self.ownership_status.value,
            "origin": self.origin.value,
            "reconciliation_status": self.reconciliation_status.value,
            "pending_order_ids": list(self.pending_order_ids),
            "uncertain_order_ids": list(self.uncertain_order_ids),
            "dual_read": self.dual_read.to_dict(),
            "blocking_reasons": list(self.blocking_reasons),
            "blocking": self.blocking,
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True, slots=True)
class PortfolioPreflightDecision:
    status: PortfolioPreflightStatus
    context: PortfolioPreflightContext

    @property
    def allowed(self) -> bool:
        return self.status is PortfolioPreflightStatus.PASS

    @property
    def passed(self) -> bool:
        """Compatibility/readability alias used by Risk and Execution."""
        return self.allowed

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.context.blocking_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "context": self.context.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PortfolioRevisionCheck:
    unchanged: bool
    expected_revision: int
    current_revision: int | None
    expected_decision_checksum: str
    current_decision_checksum: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "unchanged": self.unchanged,
            "expected_revision": self.expected_revision,
            "current_revision": self.current_revision,
            "expected_decision_checksum": self.expected_decision_checksum,
            "current_decision_checksum": self.current_decision_checksum,
            "reason": self.reason,
        }


class PortfolioDualReadComparator:
    """Compare canonical state with the accepted legacy runtime projection."""

    @staticmethod
    def compare(
        *,
        canonical_actual_lots: int,
        canonical_target_lots: int | None,
        canonical_owner_strategy_id: str | None,
        canonical_owner_config_hash: str | None,
        canonical_pending_order_ids: tuple[str, ...],
        canonical_uncertain_order_ids: tuple[str, ...],
        legacy: LegacyPortfolioView,
    ) -> PortfolioDualReadResult:
        reasons: list[str] = []
        if legacy.actual_lots is not None and int(legacy.actual_lots) != int(
            canonical_actual_lots
        ):
            reasons.append(
                "Canonical and legacy actual lots differ: "
                f"{canonical_actual_lots} != {legacy.actual_lots}."
            )
        if not _targets_equivalent(
            canonical_target_lots,
            legacy.target_lots,
            actual_lots=canonical_actual_lots,
        ):
            reasons.append(
                "Canonical and legacy effective targets differ: "
                f"{canonical_target_lots!r} != {legacy.target_lots!r}."
            )
        if int(canonical_actual_lots) != 0:
            if (canonical_owner_strategy_id or None) != (
                legacy.owner_strategy_id or None
            ):
                reasons.append(
                    "Canonical and legacy owner strategies differ: "
                    f"{canonical_owner_strategy_id!r} != "
                    f"{legacy.owner_strategy_id!r}."
                )
            if (
                canonical_owner_config_hash
                and legacy.owner_config_hash
                and canonical_owner_config_hash != legacy.owner_config_hash
            ):
                reasons.append("Canonical and legacy owner config hashes differ.")
        canonical_pending = tuple(
            sorted(
                {
                    *canonical_pending_order_ids,
                    *canonical_uncertain_order_ids,
                }
            )
        )
        legacy_pending = tuple(
            sorted({*legacy.pending_order_ids, *legacy.uncertain_order_ids})
        )
        if canonical_pending != legacy_pending:
            reasons.append(
                "Canonical and legacy pending-order IDs differ: "
                f"{canonical_pending!r} != {legacy_pending!r}."
            )
        if legacy.external_activity:
            reasons.append("Legacy runtime reports unresolved external activity.")
        return PortfolioDualReadResult(
            status=(
                PortfolioDualReadStatus.MISMATCH
                if reasons
                else PortfolioDualReadStatus.MATCHED
            ),
            reasons=tuple(reasons),
            canonical_actual_lots=int(canonical_actual_lots),
            legacy_actual_lots=legacy.actual_lots,
            canonical_target_lots=canonical_target_lots,
            legacy_target_lots=legacy.target_lots,
            canonical_owner_strategy_id=canonical_owner_strategy_id,
            legacy_owner_strategy_id=legacy.owner_strategy_id,
            canonical_pending_order_ids=canonical_pending,
            legacy_pending_order_ids=legacy_pending,
        )


class PortfolioPreflightGate:
    """Produce the authoritative alpha2 fail-closed order preflight."""

    def evaluate(
        self,
        lease: PortfolioSnapshotLease,
        *,
        account_id: str,
        mode: str,
        instrument_id: str,
        proposed_target_lots: int,
        legacy: LegacyPortfolioView | None = None,
        require_dual_read: bool = False,
    ) -> PortfolioPreflightDecision:
        state = lease.state
        selected_account = str(account_id).strip()
        selected_instrument = str(instrument_id).strip()
        normalized_mode = str(mode).strip().upper()
        reasons: list[str] = []

        if not selected_account or state.account_id != selected_account:
            reasons.append("Canonical account scope does not match the selected account.")
        if require_dual_read:
            if legacy is None:
                reasons.append("Legacy dual-read was requested but no projection was supplied.")
            else:
                if legacy.account_id != selected_account:
                    reasons.append("Legacy account scope does not match the selected account.")
                if legacy.instrument_id != selected_instrument:
                    reasons.append("Legacy instrument scope does not match preflight scope.")
                if legacy.mode != normalized_mode:
                    reasons.append("Legacy mode does not match preflight mode.")
        if state.portfolio_source != "CANONICAL":
            reasons.append(f"Portfolio source is {state.portfolio_source}, not CANONICAL.")
        if not state.migration.complete:
            reasons.append(
                f"Canonical migration is {state.migration.status.value}; cutover is incomplete."
            )
        if state.migration.legacy_read_path_enabled:
            reasons.append("Legacy portfolio read path is still enabled.")
        if normalized_mode not in {"SANDBOX_EXECUTION", "DRY_RUN"}:
            reasons.append(f"Unsupported preflight mode {normalized_mode!r}.")
        if state.freshness is not SnapshotFreshness.FRESH:
            reasons.append(
                f"Canonical broker snapshot is {state.freshness.value}, not FRESH."
            )
        if state.state_status in {"BLOCKED", "MANUAL_REVIEW_REQUIRED"}:
            reasons.append(f"Canonical state status is {state.state_status}.")
        if state.blocking:
            reasons.append("Canonical portfolio contains a blocking condition.")

        position = state.position(selected_instrument)
        if position is None:
            actual_lots = 0
            canonical_target = None
            ownership_status = OwnershipStatus.FLAT
            origin = PositionOrigin.UNKNOWN
            reconciliation_status = ReconciliationStatus.MATCHED
            owner_strategy_id = None
            owner_config_hash = None
            pending_order_ids: tuple[str, ...] = ()
            uncertain_order_ids: tuple[str, ...] = ()
        else:
            actual_lots = int(position.actual_lots)
            canonical_target = position.target_lots
            ownership_status = position.ownership_status
            origin = position.origin
            reconciliation_status = position.reconciliation.status
            owner_strategy_id = (
                position.ownership.strategy_id if position.ownership else None
            )
            owner_config_hash = (
                position.ownership.config_hash if position.ownership else None
            )
            pending_order_ids = tuple(
                sorted(
                    item.order_request_id
                    for item in position.pending_orders
                    if item.active and not item.uncertain
                )
            )
            uncertain_order_ids = tuple(
                sorted(
                    item.order_request_id
                    for item in position.pending_orders
                    if item.uncertain
                )
            )
            if reconciliation_status is not ReconciliationStatus.MATCHED:
                reasons.append(
                    "Instrument reconciliation is "
                    f"{reconciliation_status.value}, not MATCHED."
                )
            if uncertain_order_ids:
                reasons.append("An uncertain broker/local order exists.")
            elif pending_order_ids:
                reasons.append("An active pending order exists.")
            if actual_lots != 0 and ownership_status is not OwnershipStatus.ATTRIBUTED:
                reasons.append("Open position ownership is not ATTRIBUTED to PRIMARY.")
            if actual_lots != 0 and origin is not PositionOrigin.STRATEGY:
                reasons.append(
                    "Open position origin is not STRATEGY; manual review is required."
                )

        if require_dual_read and legacy is not None:
            dual = PortfolioDualReadComparator.compare(
                canonical_actual_lots=actual_lots,
                canonical_target_lots=canonical_target,
                canonical_owner_strategy_id=owner_strategy_id,
                canonical_owner_config_hash=owner_config_hash,
                canonical_pending_order_ids=pending_order_ids,
                canonical_uncertain_order_ids=uncertain_order_ids,
                legacy=legacy,
            )
            reasons.extend(dual.reasons)
        else:
            dual = PortfolioDualReadResult(
                status=PortfolioDualReadStatus.DISABLED,
                reasons=(),
                canonical_actual_lots=actual_lots,
                legacy_actual_lots=None,
                canonical_target_lots=canonical_target,
                legacy_target_lots=None,
                canonical_owner_strategy_id=owner_strategy_id,
                legacy_owner_strategy_id=None,
                canonical_pending_order_ids=tuple(
                    sorted({*pending_order_ids, *uncertain_order_ids})
                ),
                legacy_pending_order_ids=(),
            )

        context = PortfolioPreflightContext(
            account_id=selected_account,
            mode=normalized_mode,
            instrument_id=selected_instrument,
            snapshot_revision=lease.revision,
            snapshot_decision_checksum=lease.decision_checksum,
            snapshot_document_checksum=lease.document_checksum,
            snapshot_at=state.snapshot_at,
            freshness=state.freshness,
            state_status=state.state_status,
            portfolio_source=state.portfolio_source,
            migration_status=state.migration.status.value,
            legacy_read_path_enabled=state.migration.legacy_read_path_enabled,
            actual_lots=actual_lots,
            canonical_target_lots=canonical_target,
            proposed_target_lots=int(proposed_target_lots),
            ownership_status=ownership_status,
            origin=origin,
            reconciliation_status=reconciliation_status,
            pending_order_ids=pending_order_ids,
            uncertain_order_ids=uncertain_order_ids,
            dual_read=dual,
            blocking_reasons=tuple(dict.fromkeys(reasons)),
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )
        return PortfolioPreflightDecision(
            status=(
                PortfolioPreflightStatus.BLOCKED
                if context.blocking
                else PortfolioPreflightStatus.PASS
            ),
            context=context,
        )

    @staticmethod
    def recheck(
        repository: PortfolioRepository,
        lease: PortfolioSnapshotLease,
        *,
        expected_account_id: str,
    ) -> PortfolioRevisionCheck:
        try:
            current = repository.load(expected_account_id=expected_account_id)
        except PortfolioRepositoryError as exc:
            return PortfolioRevisionCheck(
                unchanged=False,
                expected_revision=lease.revision,
                current_revision=None,
                expected_decision_checksum=lease.decision_checksum,
                current_decision_checksum=None,
                reason=f"Cannot reload canonical portfolio state: {exc}",
            )
        current_lease = PortfolioSnapshotLease.from_state(current)
        unchanged = (
            current_lease.revision == lease.revision
            and current_lease.decision_checksum == lease.decision_checksum
        )
        return PortfolioRevisionCheck(
            unchanged=unchanged,
            expected_revision=lease.revision,
            current_revision=current_lease.revision,
            expected_decision_checksum=lease.decision_checksum,
            current_decision_checksum=current_lease.decision_checksum,
            reason=(
                "Canonical portfolio revision is unchanged."
                if unchanged
                else "Canonical portfolio revision/decision checksum changed before broker POST."
            ),
        )


def portfolio_risk_mapping(state: PortfolioState) -> Mapping[str, Any]:
    """Return the minimal T-Invest-like totals consumed by RiskRuntimeAdapter."""

    rub = state.account.cash("rub")
    cash_rub = rub.total if rub is not None else 0.0
    return {
        "totalAmountPortfolio": state.account.total_value,
        "totalAmountCurrencies": cash_rub,
        "totalAmountShares": state.account.securities_value,
        "totalAmountBonds": 0.0,
        "totalAmountEtf": 0.0,
        "totalAmountFutures": 0.0,
        "totalAmountOptions": 0.0,
        "totalAmountSp": 0.0,
        "expectedYield": state.account.expected_yield,
    }


def _targets_equivalent(
    canonical: int | None,
    legacy: int | None,
    *,
    actual_lots: int,
) -> bool:
    if canonical == legacy:
        return True
    if int(actual_lots) == 0 and canonical in {None, 0} and legacy in {None, 0}:
        return True
    return False
