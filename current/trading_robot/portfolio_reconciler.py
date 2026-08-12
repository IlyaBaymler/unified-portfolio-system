from __future__ import annotations

"""Pure reconciliation between broker, runtime and persisted portfolio state."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from .portfolio_adapters import (
    BrokerPortfolioRecord,
    BrokerPositionRecord,
    RuntimePortfolioRecord,
    RuntimePositionRecord,
)
from .portfolio_model import (
    OwnershipStatus,
    PositionOrigin,
    PendingOrderState,
    PortfolioState,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
    SnapshotFreshness,
    PORTFOLIO_STATE_SCHEMA_VERSION,
)


@dataclass(frozen=True, slots=True)
class ReconciliationContext:
    freshness: SnapshotFreshness = SnapshotFreshness.FRESH
    expected_account_id: str | None = None
    journal_confirmed_instruments: frozenset[str] = frozenset()
    position_origins: Mapping[str, PositionOrigin | str] | None = None
    generated_at: str | None = None


class PortfolioReconciler:
    """Build one canonical state without mutating broker/runtime sources."""

    def reconcile(
        self,
        broker: BrokerPortfolioRecord,
        runtime: RuntimePortfolioRecord,
        *,
        previous: PortfolioState | None = None,
        context: ReconciliationContext | None = None,
    ) -> PortfolioState:
        ctx = context or ReconciliationContext()
        generated_at = ctx.generated_at or datetime.now(timezone.utc).isoformat()
        expected_account = str(ctx.expected_account_id or broker.account.account_id)
        account_mismatch = bool(
            expected_account
            and broker.account.account_id
            and expected_account != broker.account.account_id
        ) or bool(
            runtime.account_id
            and broker.account.account_id
            and runtime.account_id != broker.account.account_id
        )

        broker_index = {item.instrument_id: item for item in broker.positions}
        runtime_index = {item.instrument_id: item for item in runtime.positions}
        previous_index = (
            {item.instrument_id: item for item in previous.positions}
            if previous is not None
            else {}
        )
        all_ids = sorted(set(broker_index) | set(runtime_index))

        positions: list[PositionState] = []
        warnings: list[str] = [*broker.warnings, *runtime.warnings]
        for instrument_id in all_ids:
            broker_position = broker_index.get(instrument_id)
            runtime_position = runtime_index.get(instrument_id)
            previous_position = previous_index.get(instrument_id)
            result = self._reconcile_position(
                instrument_id,
                broker_position=broker_position,
                runtime_position=runtime_position,
                previous_position=previous_position,
                freshness=ctx.freshness,
                account_mismatch=account_mismatch,
                journal_confirmed=(
                    instrument_id in ctx.journal_confirmed_instruments
                ),
                checked_at=generated_at,
            )
            if result.status is not ReconciliationStatus.MATCHED:
                warnings.append(
                    f"{instrument_id}: {result.status.value} — "
                    + "; ".join(result.reasons)
                )
            positions.append(
                self._build_position(
                    instrument_id,
                    broker_position=broker_position,
                    runtime_position=runtime_position,
                    previous_position=previous_position,
                    result=result,
                    origin_hint=(ctx.position_origins or {}).get(instrument_id),
                )
            )

        blocking = account_mismatch or any(
            item.reconciliation.blocking for item in positions
        )
        if account_mismatch:
            warnings.append(
                "Selected, broker and runtime account scopes are inconsistent."
            )
        state_status = "BLOCKED" if blocking else ("EMPTY" if not positions else "READY")
        return PortfolioState(
            version=PORTFOLIO_STATE_SCHEMA_VERSION,
            account=broker.account,
            snapshot_at=broker.snapshot_at,
            generated_at=generated_at,
            freshness=ctx.freshness,
            source="PORTFOLIO_MANAGER",
            positions=tuple(positions),
            warnings=tuple(dict.fromkeys(warnings)),
            state_status=state_status,
            blocking=blocking,
        )

    def _reconcile_position(
        self,
        instrument_id: str,
        *,
        broker_position: BrokerPositionRecord | None,
        runtime_position: RuntimePositionRecord | None,
        previous_position: PositionState | None,
        freshness: SnapshotFreshness,
        account_mismatch: bool,
        journal_confirmed: bool,
        checked_at: str,
    ) -> ReconciliationResult:
        actual_lots = broker_position.actual_lots if broker_position else 0
        target = runtime_position.target if runtime_position else None
        target_lots = target.target_lots if target else None
        ownership = runtime_position.ownership if runtime_position else None
        pending = _merge_pending(
            broker_position.pending_orders if broker_position else (),
            runtime_position.pending_orders if runtime_position else (),
        )
        pending_ids = tuple(item.order_request_id for item in pending)

        status = ReconciliationStatus.MATCHED
        reasons: list[str] = []

        if account_mismatch:
            status = ReconciliationStatus.ACCOUNT_MISMATCH
            reasons.append("Account IDs do not match the selected execution scope.")
        elif freshness is not SnapshotFreshness.FRESH:
            status = ReconciliationStatus.BROKER_SNAPSHOT_STALE
            reasons.append(f"Broker snapshot freshness is {freshness.value}.")
        elif any(item.partial_fill and item.active for item in pending):
            status = ReconciliationStatus.PARTIAL_FILL
            reasons.append("At least one active order is partially filled.")
        elif any(item.uncertain or item.status.value == "UNKNOWN" for item in pending):
            status = ReconciliationStatus.PENDING_ORDER_UNCERTAIN
            reasons.append("Pending order state is uncertain.")
        elif any(item.active for item in pending):
            status = ReconciliationStatus.PENDING_ORDER
            reasons.append("An active pending order exists.")
        elif actual_lots != 0 and ownership is None:
            if target_lots is None:
                status = ReconciliationStatus.UNATTRIBUTED_OPEN_POSITION
                reasons.append("Broker reports an open position without local ownership or target.")
            else:
                status = ReconciliationStatus.OWNERSHIP_MISSING
                reasons.append("Open broker position has a target but no PRIMARY owner.")
        elif (
            previous_position is not None
            and previous_position.actual_lots != actual_lots
            and not journal_confirmed
        ):
            status = ReconciliationStatus.EXTERNAL_ACTIVITY_DETECTED
            reasons.append(
                "Broker quantity changed since the persisted snapshot without matching journal evidence."
            )
        elif target_lots is None:
            if actual_lots == 0:
                status = ReconciliationStatus.MATCHED
                reasons.append("No open position and no target.")
            else:  # defensive; ownership branch should already handle this
                status = ReconciliationStatus.MANUAL_REVIEW_REQUIRED
                reasons.append("Open position has no canonical target.")
        elif target_lots != actual_lots:
            status = ReconciliationStatus.TARGET_MISMATCH
            reasons.append(
                f"Broker lots {actual_lots} differ from target lots {target_lots}."
            )
        else:
            status = ReconciliationStatus.MATCHED
            reasons.append("Broker lots, target and ownership are consistent.")

        return ReconciliationResult(
            instrument_id=instrument_id,
            status=status,
            blocking=status.blocking,
            reasons=tuple(reasons),
            actual_lots=actual_lots,
            target_lots=target_lots,
            pending_order_ids=pending_ids,
            checked_at=checked_at,
        )

    @staticmethod
    def _build_position(
        instrument_id: str,
        *,
        broker_position: BrokerPositionRecord | None,
        runtime_position: RuntimePositionRecord | None,
        previous_position: PositionState | None,
        result: ReconciliationResult,
        origin_hint: PositionOrigin | str | None,
    ) -> PositionState:
        ownership = runtime_position.ownership if runtime_position else None
        actual_lots = broker_position.actual_lots if broker_position else 0
        if actual_lots != 0 and ownership is None:
            ownership_status = OwnershipStatus.UNATTRIBUTED
        elif ownership is not None:
            ownership_status = OwnershipStatus.ATTRIBUTED
        elif actual_lots == 0:
            ownership_status = OwnershipStatus.FLAT
        else:
            ownership_status = OwnershipStatus.UNKNOWN

        pending = _merge_pending(
            broker_position.pending_orders if broker_position else (),
            runtime_position.pending_orders if runtime_position else (),
        )
        if runtime_position is not None and (
            runtime_position.ownership is not None or runtime_position.target is not None
        ):
            origin = PositionOrigin.STRATEGY
        elif result.status is ReconciliationStatus.EXTERNAL_ACTIVITY_DETECTED:
            origin = PositionOrigin.EXTERNAL
        elif origin_hint is not None:
            try:
                origin = PositionOrigin(str(origin_hint))
            except ValueError:
                origin = PositionOrigin.UNKNOWN
        elif previous_position is not None and previous_position.origin is not PositionOrigin.UNKNOWN:
            origin = previous_position.origin
        else:
            origin = PositionOrigin.UNKNOWN
        return PositionState(
            instrument_id=instrument_id,
            figi=(
                broker_position.figi
                if broker_position
                else (runtime_position.figi if runtime_position else "")
            ),
            ticker=(
                broker_position.ticker
                if broker_position
                else (runtime_position.ticker if runtime_position else instrument_id[:12])
            ),
            class_code=(
                broker_position.class_code
                if broker_position
                else (runtime_position.class_code if runtime_position else "")
            ),
            asset_type=broker_position.asset_type if broker_position else "unknown",
            currency=broker_position.currency if broker_position else "",
            quantity=broker_position.quantity if broker_position else 0.0,
            actual_lots=actual_lots,
            average_price=broker_position.average_price if broker_position else None,
            current_price=broker_position.current_price if broker_position else None,
            market_value=broker_position.market_value if broker_position else None,
            expected_yield=broker_position.expected_yield if broker_position else None,
            target=runtime_position.target if runtime_position else None,
            ownership=ownership,
            ownership_status=ownership_status,
            pending_orders=pending,
            reconciliation=result,
            origin=origin,
            last_candle_time=(runtime_position.last_candle_time if runtime_position else None),
        )


def _merge_pending(
    *collections: Iterable[PendingOrderState],
) -> tuple[PendingOrderState, ...]:
    result: list[PendingOrderState] = []
    seen: set[str] = set()
    for collection in collections:
        for item in collection:
            if item.order_request_id in seen:
                continue
            seen.add(item.order_request_id)
            result.append(item)
    return tuple(result)
