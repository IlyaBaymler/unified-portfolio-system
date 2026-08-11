from __future__ import annotations

"""Mandatory canonical Portfolio Manager reconciliation after a confirmed fill."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .portfolio_manager import CanonicalPortfolioManager
from .portfolio_model import ReconciliationStatus, SnapshotFreshness
from .portfolio_preflight import PortfolioSnapshotLease


@dataclass(frozen=True, slots=True)
class PostFillPortfolioResult:
    success: bool
    expected_lots: int
    actual_lots: int | None
    revision: int | None
    decision_checksum: str | None
    reconciliation_status: str | None
    snapshot_at: str | None
    reason: str
    state_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "expected_lots": self.expected_lots,
            "actual_lots": self.actual_lots,
            "revision": self.revision,
            "decision_checksum": self.decision_checksum,
            "reconciliation_status": self.reconciliation_status,
            "snapshot_at": self.snapshot_at,
            "state_status": self.state_status,
            "reason": self.reason,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


class PostFillPortfolioCoordinator:
    def __init__(self, manager: CanonicalPortfolioManager) -> None:
        self.manager = manager

    def reconcile_from_api_portfolio(
        self,
        portfolio: Mapping[str, Any],
        *,
        instrument_id: str,
        expected_lots: int,
        instrument_metadata: Mapping[str, Any] | None = None,
        broker_orders: Iterable[Mapping[str, Any]] = (),
        snapshot_at: str | None = None,
    ) -> PostFillPortfolioResult:
        try:
            state = self.manager.refresh_from_api_portfolio(
                portfolio,
                instrument_metadata=instrument_metadata,
                broker_orders=broker_orders,
                record_event=True,
                snapshot_at=snapshot_at,
            )
        except Exception as exc:
            return PostFillPortfolioResult(
                success=False,
                expected_lots=int(expected_lots),
                actual_lots=None,
                revision=None,
                decision_checksum=None,
                reconciliation_status=None,
                snapshot_at=None,
                state_status=None,
                reason=f"Canonical refresh failed after fill: {exc}",
            )
        return self._evaluate_state(
            state,
            instrument_id=instrument_id,
            expected_lots=expected_lots,
        )

    def reconcile(
        self,
        *,
        instrument_id: str,
        expected_lots: int,
    ) -> PostFillPortfolioResult:
        try:
            state = self.manager.refresh(record_event=True)
        except Exception as exc:
            return PostFillPortfolioResult(
                success=False,
                expected_lots=int(expected_lots),
                actual_lots=None,
                revision=None,
                decision_checksum=None,
                reconciliation_status=None,
                snapshot_at=None,
                state_status=None,
                reason=f"Canonical refresh failed after fill: {exc}",
            )
        return self._evaluate_state(
            state,
            instrument_id=instrument_id,
            expected_lots=expected_lots,
        )

    @staticmethod
    def _evaluate_state(
        state,
        *,
        instrument_id: str,
        expected_lots: int,
    ) -> PostFillPortfolioResult:
        lease = PortfolioSnapshotLease.from_state(state)
        position = state.position(str(instrument_id))
        actual = int(position.actual_lots) if position is not None else 0
        status = (
            position.reconciliation.status
            if position is not None
            else ReconciliationStatus.MATCHED
        )
        reasons: list[str] = []
        if state.freshness is not SnapshotFreshness.FRESH:
            reasons.append(f"freshness={state.freshness.value}")
        if state.blocking:
            reasons.append(f"state_status={state.state_status}")
        if status is not ReconciliationStatus.MATCHED:
            reasons.append(f"reconciliation={status.value}")
        if actual != int(expected_lots):
            reasons.append(f"actual_lots={actual}, expected_lots={expected_lots}")
        return PostFillPortfolioResult(
            success=not reasons,
            expected_lots=int(expected_lots),
            actual_lots=actual,
            revision=lease.revision,
            decision_checksum=lease.decision_checksum,
            reconciliation_status=status.value,
            snapshot_at=state.snapshot_at,
            state_status=state.state_status,
            reason=(
                "Canonical portfolio reconciled after fill."
                if not reasons
                else "Canonical post-fill reconciliation failed: " + "; ".join(reasons)
            ),
        )
