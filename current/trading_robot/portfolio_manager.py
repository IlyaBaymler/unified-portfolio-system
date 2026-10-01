from __future__ import annotations

"""Canonical-only Portfolio Manager orchestration for v3.7-alpha3."""

from dataclasses import replace
from datetime import datetime, timezone
import logging
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .journal import EventJournal, JournalEvent
from .portfolio_adapters import (
    BrokerPortfolioAdapter,
    BrokerPortfolioRecord,
    RuntimePortfolioAdapter,
    RuntimePortfolioRecord,
)
from .portfolio_cutover import PortfolioCutoverManager
from .portfolio import ExternalCloseAcknowledgementRequest, OwnershipRecoveryRequest
from .portfolio_model import (
    OwnershipStatus,
    PortfolioMigrationStatus,
    PortfolioState,
    PortfolioTarget,
    PositionOrigin,
    PositionOwnership,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
    SnapshotFreshness,
)
from .portfolio_reconciler import PortfolioReconciler, ReconciliationContext
from .portfolio_repository import (
    PortfolioRepository, PortfolioRepositoryError, portfolio_document_checksum,
)
from .portfolio_snapshot import PortfolioSnapshotBuilder
from .portfolio_observation import PortfolioObservationPolicy
from .portfolio_cash_observation import (
    DesktopOwnCashPolicy, PortfolioCashObservation, PortfolioCashObservationError,
)
from .portfolio_transactions import (
    LegacyPortfolioShadowWriter,
    PortfolioTransactionCoordinator,
)
from .tbank_sandbox import TBankSandboxClient


logger = logging.getLogger(__name__)


class CanonicalPortfolioManager:
    """Publish the only authoritative portfolio state used by Risk/Execution."""

    def __init__(
        self,
        api: TBankSandboxClient,
        account_id: str,
        *,
        robot_state_file: str | Path,
        portfolio_state_file: str | Path,
        journal_file: str | Path,
    ) -> None:
        self.api = api
        self.account_id = str(account_id)
        # Retained for rollback metadata only. Alpha3 never reads portfolio
        # actual/target/ownership/pending values from this file.
        self.robot_state_path = Path(robot_state_file)
        self.repository = PortfolioRepository(portfolio_state_file)
        self.journal = EventJournal(journal_file)
        self.reconciler = PortfolioReconciler()
        shadow_path = self.repository.path.with_name("portfolio_legacy_shadow.json")
        self.transaction_coordinator = PortfolioTransactionCoordinator(
            self.repository,
            journal=self.journal,
            shadow_writer=LegacyPortfolioShadowWriter(shadow_path),
        )
        self.cutover = PortfolioCutoverManager(
            self.repository,
            journal=self.journal,
            report_path=self.repository.path.with_name("canonical_migration_report.json"),
        )
        self._ensure_canonical_repository()

    def _ensure_canonical_repository(self) -> None:
        if not self.repository.exists():
            self.repository.save(PortfolioState.empty(account_id=self.account_id))
            return
        source_schema = self.repository.raw_schema_version()
        if source_schema == 1:
            raise PortfolioRepositoryError(
                "PortfolioState schema 1 requires an explicit canonical cutover. "
                "Run 'run_portfolio_cutover.bat preview --account-id <ACCOUNT_ID>' "
                "and then confirm with 'CUTOVER PORTFOLIO STATE 2' while execution is stopped."
            )
        state = self.repository.load(expected_account_id=self.account_id)
        if not state.migration.complete or state.portfolio_source != "CANONICAL":
            raise PortfolioRepositoryError(
                "Canonical PortfolioState cutover is incomplete; trading is blocked."
            )

    def refresh(
        self,
        *,
        record_event: bool = True,
        _stage_observer: Callable[[str], None] | None = None,
        observation_policy: PortfolioObservationPolicy | None = None,
        cash_observation_policy: DesktopOwnCashPolicy | None = None,
    ) -> PortfolioState:
        """Refresh observations; the desktop policy pins revision before reads.

        Legacy callers retain their frozen read/observer sequence. The strict
        desktop path is opt-in and does not expand controlled Q7 read budgets.
        """

        if cash_observation_policy is not None and (
            type(cash_observation_policy) is not DesktopOwnCashPolicy
            or observation_policy is None
            or cash_observation_policy.account_id != self.account_id
            or cash_observation_policy.instruments != observation_policy.instruments
        ):
            raise PortfolioCashObservationError("PORTFOLIO_CASH_SCOPE_MISMATCH")
        previous = None
        if observation_policy is not None:
            if _stage_observer is not None:
                _stage_observer("LOCAL_PRESTATE")
            previous = self.repository.load(expected_account_id=self.account_id)
            if observation_policy.account_id != self.account_id:
                raise PortfolioRepositoryError("PORTFOLIO_OBSERVATION_ACCOUNT_MISMATCH")
            started = time.monotonic()
            portfolio, broker_orders = observation_policy.acquire(
                self.api, previous, _stage_observer,
            )
            cash_observation = None
            if cash_observation_policy is not None:
                def checkpoint(stage: str) -> None:
                    if not 0 <= time.monotonic() - started <= observation_policy.max_acquisition_seconds:
                        raise PortfolioCashObservationError("PORTFOLIO_CASH_OBSERVATION_EXPIRED")
                    if _stage_observer is not None:
                        _stage_observer(stage)
                cash_observation = cash_observation_policy.acquire(self.api, checkpoint)
                if observation_policy.binding_guard is not None:
                    observation_policy.binding_guard()
                checkpoint("CASH_PUBLICATION_READY")
            return self.refresh_from_api_portfolio(
                portfolio, broker_orders=broker_orders, record_event=record_event,
                _stage_observer=_stage_observer, _expected_previous=previous,
                _cash_observation=cash_observation,
            )
        if _stage_observer is not None:
            _stage_observer("PROVIDER_PORTFOLIO")
        portfolio = self.api.get_portfolio(self.account_id)
        broker_orders: Iterable[Mapping[str, Any]] = ()
        if _stage_observer is not None:
            _stage_observer("PROVIDER_ORDERS")
        if hasattr(self.api, "get_orders"):
            broker_orders = self.api.get_orders(self.account_id)
        return self.refresh_from_api_portfolio(
            portfolio,
            broker_orders=broker_orders,
            record_event=record_event,
            _stage_observer=_stage_observer,
            _expected_previous=previous,
        )

    def refresh_from_api_portfolio(
        self,
        portfolio: Mapping[str, Any],
        *,
        instrument_metadata: Mapping[str, Any] | None = None,
        broker_orders: Iterable[Mapping[str, Any]] = (),
        runtime_state: Mapping[str, Any] | None = None,
        record_event: bool = True,
        snapshot_at: str | None = None,
        _stage_observer: Callable[[str], None] | None = None,
        _expected_previous: PortfolioState | None = None,
        _cash_observation: PortfolioCashObservation | None = None,
    ) -> PortfolioState:
        """Refresh using one exact broker observation.

        ``runtime_state`` remains in the signature for source compatibility but
        is deliberately ignored. Portfolio plans are read only from canonical
        PortfolioState schema 2.
        """

        del runtime_state
        if _stage_observer is not None and _expected_previous is None:
            _stage_observer("LOCAL_PRESTATE")
        previous = _expected_previous or self.repository.load(expected_account_id=self.account_id)
        if previous.account_id != self.account_id:
            raise PortfolioRepositoryError("PORTFOLIO_OBSERVATION_ACCOUNT_MISMATCH")
        if _stage_observer is not None:
            _stage_observer("ADAPTER")
        broker = BrokerPortfolioAdapter.from_api_portfolio(
            portfolio,
            account_id=self.account_id,
            instrument_metadata=instrument_metadata,
            broker_orders=broker_orders,
            snapshot_at=snapshot_at,
        )
        if _cash_observation is not None:
            if type(_cash_observation) is not PortfolioCashObservation:
                raise PortfolioCashObservationError("PORTFOLIO_CASH_OBSERVATION_INVALID")
            broker = _cash_observation.apply(broker)
        runtime = RuntimePortfolioAdapter.from_portfolio_state(previous)
        return self._reconcile_and_publish(
            broker,
            runtime,
            previous=previous,
            record_event=record_event,
            _stage_observer=_stage_observer,
        )

    def publish_from_records(
        self,
        broker: BrokerPortfolioRecord,
        runtime: RuntimePortfolioRecord | None = None,
        *,
        record_event: bool = True,
    ) -> PortfolioState:
        previous = self.repository.load(expected_account_id=self.account_id)
        canonical_runtime = runtime or RuntimePortfolioAdapter.from_portfolio_state(previous)
        return self._reconcile_and_publish(
            broker,
            canonical_runtime,
            previous=previous,
            record_event=record_event,
        )

    def stage_confirmed_target(
        self,
        *,
        instrument_id: str,
        target_lots: int,
        strategy_id: str,
        config_hash: str,
        candle_interval: str,
        ticker: str = "",
        figi: str = "",
        class_code: str = "",
        candle_time: str | None = None,
        transaction_id: str | None = None,
    ) -> PortfolioState:
        """Commit a broker-confirmed Strategy target before post-fill refresh."""

        token = str(instrument_id)
        now = datetime.now(timezone.utc).isoformat()

        def transform(current: PortfolioState) -> PortfolioState:
            positions = list(current.positions)
            index = next(
                (idx for idx, item in enumerate(positions) if item.instrument_id == token),
                None,
            )
            target = PortfolioTarget(
                instrument_id=token,
                target_lots=int(target_lots),
                strategy_id=strategy_id,
                config_hash=config_hash,
                candle_time=candle_time,
            )
            owner = PositionOwnership(
                strategy_id=strategy_id,
                config_hash=config_hash,
                candle_interval=candle_interval,
                source="CANONICAL_TRANSACTION",
                attributed_at=now,
            )
            if index is None:
                actual = 0
                status = (
                    ReconciliationStatus.MATCHED
                    if int(target_lots) == 0
                    else ReconciliationStatus.TARGET_MISMATCH
                )
                position = PositionState(
                    instrument_id=token,
                    figi=figi,
                    ticker=ticker or token[:12],
                    class_code=class_code,
                    asset_type="unknown",
                    currency="",
                    quantity=0.0,
                    actual_lots=actual,
                    average_price=None,
                    current_price=None,
                    market_value=None,
                    expected_yield=None,
                    target=target,
                    ownership=(owner if int(target_lots) != 0 else None),
                    ownership_status=(
                        OwnershipStatus.ATTRIBUTED
                        if int(target_lots) != 0
                        else OwnershipStatus.FLAT
                    ),
                    pending_orders=(),
                    reconciliation=ReconciliationResult(
                        instrument_id=token,
                        status=status,
                        blocking=status.blocking,
                        reasons=("Confirmed target staged before canonical broker refresh.",),
                        actual_lots=actual,
                        target_lots=int(target_lots),
                        pending_order_ids=(),
                        checked_at=now,
                    ),
                    origin=PositionOrigin.STRATEGY,
                    last_candle_time=candle_time,
                )
                positions.append(position)
            else:
                existing = positions[index]
                actual = int(existing.actual_lots)
                status = (
                    ReconciliationStatus.MATCHED
                    if actual == int(target_lots)
                    else ReconciliationStatus.TARGET_MISMATCH
                )
                positions[index] = replace(
                    existing,
                    target=target,
                    ownership=(owner if actual != 0 or int(target_lots) != 0 else None),
                    ownership_status=(
                        OwnershipStatus.ATTRIBUTED
                        if actual != 0 or int(target_lots) != 0
                        else OwnershipStatus.FLAT
                    ),
                    reconciliation=replace(
                        existing.reconciliation,
                        status=status,
                        blocking=status.blocking,
                        reasons=("Confirmed target staged before canonical broker refresh.",),
                        actual_lots=actual,
                        target_lots=int(target_lots),
                        checked_at=now,
                    ),
                    origin=PositionOrigin.STRATEGY,
                    last_candle_time=candle_time or existing.last_candle_time,
                )
            blocking = any(item.reconciliation.blocking for item in positions)
            return replace(
                current,
                positions=tuple(sorted(positions, key=lambda item: (item.ticker, item.instrument_id))),
                generated_at=now,
                state_status="BLOCKED" if blocking else ("EMPTY" if not positions else "READY"),
                blocking=blocking,
            )

        result = self.transaction_coordinator.commit(
            "STAGE_CONFIRMED_STRATEGY_TARGET",
            transform,
            transaction_id=transaction_id,
            account_id=self.account_id,
            instrument_id=token,
            mode="SANDBOX_EXECUTION",
        )
        return result.state

    def recover_ownership(self, request: OwnershipRecoveryRequest) -> dict[str, Any]:
        """Attribute a fresh unattributed broker position in canonical state.

        This is an operator action. It does not submit an order and never reads
        legacy portfolio fields from ``robot_state.json``.
        """

        if str(request.account_id) != self.account_id:
            raise ValueError("Ownership recovery account does not match manager account.")
        if int(request.expected_lots) <= 0:
            raise ValueError("expected_lots must be positive.")
        if request.confirmation_text.strip().upper() != request.expected_phrase:
            raise ValueError(f"Confirmation mismatch; expected {request.expected_phrase}.")

        instrument = self.api.find_instrument(request.ticker, request.class_code)
        instrument_id = self.api.instrument_id(instrument)
        figi = str(instrument.get("figi") or "") if isinstance(instrument, Mapping) else ""
        state = self.refresh(record_event=False)
        position = self._find_position(
            state,
            instrument_id=instrument_id,
            ticker=request.ticker,
            figi=figi,
        )
        reasons: list[str] = []
        if state.freshness is not SnapshotFreshness.FRESH:
            reasons.append(f"Canonical snapshot is {state.freshness.value}.")
        if position is None:
            reasons.append("Broker position is absent from canonical snapshot.")
        else:
            if int(position.actual_lots) != int(request.expected_lots):
                reasons.append(
                    f"Broker position is {position.actual_lots} lot(s), expected {request.expected_lots}."
                )
            if position.ownership is not None:
                reasons.append("Position already has canonical PRIMARY ownership.")
            if any(order.active or order.uncertain for order in position.pending_orders):
                reasons.append("Pending or uncertain order exists for the position.")
            if position.reconciliation.status not in {
                ReconciliationStatus.UNATTRIBUTED_OPEN_POSITION,
                ReconciliationStatus.OWNERSHIP_MISSING,
            }:
                reasons.append(
                    "Position is not in an ownership-recovery state "
                    f"({position.reconciliation.status.value})."
                )
        precheck = {
            "status": "ready" if not reasons else "blocked",
            "account_id": self.account_id,
            "instrument_id": instrument_id,
            "ticker": request.ticker.upper(),
            "current_lots": position.actual_lots if position else None,
            "expected_lots": int(request.expected_lots),
            "block_reasons": reasons,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self.journal.record(
            JournalEvent(
                category="portfolio",
                event_type="OWNERSHIP_RECOVERY_PRECHECK",
                severity="INFO" if not reasons else "ERROR",
                account_id=self.account_id,
                instrument_id=instrument_id,
                ticker=request.ticker.upper(),
                status=precheck["status"],
                payload=precheck,
            )
        )
        if reasons or position is None:
            raise RuntimeError("; ".join(reasons or ["Ownership recovery precheck failed."]))

        now = datetime.now(timezone.utc).isoformat()
        owner = PositionOwnership(
            strategy_id=request.primary_strategy,
            config_hash=request.primary_config_hash,
            candle_interval=request.candle_interval,
            source="GUI_OPERATOR_CANONICAL",
            attributed_at=now,
        )
        target = PortfolioTarget(
            instrument_id=position.instrument_id,
            target_lots=int(position.actual_lots),
            strategy_id=request.primary_strategy,
            config_hash=request.primary_config_hash,
            candle_time=position.last_candle_time,
        )

        def transform(current: PortfolioState) -> PortfolioState:
            positions: list[PositionState] = []
            found = False
            for item in current.positions:
                if item.instrument_id != position.instrument_id:
                    positions.append(item)
                    continue
                found = True
                positions.append(
                    replace(
                        item,
                        target=target,
                        ownership=owner,
                        ownership_status=OwnershipStatus.ATTRIBUTED,
                        origin=PositionOrigin.STRATEGY,
                        reconciliation=replace(
                            item.reconciliation,
                            status=ReconciliationStatus.MATCHED,
                            blocking=False,
                            reasons=("Canonical ownership recovered by explicit operator action.",),
                            actual_lots=item.actual_lots,
                            target_lots=item.actual_lots,
                            checked_at=now,
                        ),
                    )
                )
            if not found:
                raise RuntimeError("Canonical position disappeared before ownership commit.")
            blocking = any(item.reconciliation.blocking for item in positions)
            return replace(
                current,
                positions=tuple(positions),
                generated_at=now,
                blocking=blocking,
                state_status="BLOCKED" if blocking else ("EMPTY" if not positions else "READY"),
            )

        transaction_id = f"recover-ownership:{self.account_id}:{instrument_id}:{request.primary_config_hash}"
        result = self.transaction_coordinator.commit(
            "RECOVER_POSITION_OWNERSHIP",
            transform,
            transaction_id=transaction_id,
            account_id=self.account_id,
            instrument_id=instrument_id,
            mode="SANDBOX_EXECUTION",
        )
        payload = {
            **precheck,
            "status": "ownership_recovered",
            "owner_strategy": request.primary_strategy,
            "owner_config_hash": request.primary_config_hash,
            "strategy_suite_hash": request.strategy_suite_hash,
            "shadow_strategies": list(request.shadow_strategies),
            "old_revision": result.old_revision,
            "new_revision": result.new_revision,
            "transaction_id": result.transaction_id,
            "recovered_at": now,
        }
        self.journal.record(
            JournalEvent(
                category="portfolio",
                event_type="OWNERSHIP_RECOVERED",
                severity="WARNING",
                account_id=self.account_id,
                instrument_id=instrument_id,
                ticker=request.ticker.upper(),
                strategy_id=request.primary_strategy,
                config_hash=request.primary_config_hash,
                status="completed",
                payload=payload,
            )
        )
        return payload

    def acknowledge_external_close(
        self,
        request: ExternalCloseAcknowledgementRequest,
    ) -> dict[str, Any]:
        """Clear a stale canonical target after a proven external flat close."""

        if str(request.account_id) != self.account_id:
            raise ValueError("Acknowledgement account does not match manager account.")
        if int(request.expected_target_lots) == 0:
            raise ValueError("expected_target_lots must describe a non-flat stale target.")
        if request.confirmation_text.strip().upper() != request.expected_phrase:
            raise ValueError(f"Confirmation mismatch; expected {request.expected_phrase}.")

        instrument = self.api.find_instrument(request.ticker, request.class_code)
        instrument_id = self.api.instrument_id(instrument)
        figi = str(instrument.get("figi") or "") if isinstance(instrument, Mapping) else ""
        state = self.refresh(record_event=False)
        position = self._find_position(
            state,
            instrument_id=instrument_id,
            ticker=request.ticker,
            figi=figi,
        )
        lifecycle = self._latest_execution_lifecycle(instrument_id)
        reasons: list[str] = []
        if state.freshness is not SnapshotFreshness.FRESH:
            reasons.append(f"Canonical snapshot is {state.freshness.value}.")
        if position is None:
            reasons.append("Canonical position/target is absent.")
        else:
            if int(position.actual_lots) != 0:
                reasons.append(f"Fresh broker snapshot is not flat ({position.actual_lots} lot(s)).")
            if position.target_lots != int(request.expected_target_lots):
                reasons.append(
                    "Canonical target does not match operator expectation "
                    f"({position.target_lots!r} != {request.expected_target_lots})."
                )
            if position.ownership is None:
                reasons.append("No canonical PRIMARY ownership exists to clear.")
            if any(order.active or order.uncertain for order in position.pending_orders):
                reasons.append("Pending or uncertain order exists for the position.")
            if position.reconciliation.status is not ReconciliationStatus.TARGET_MISMATCH:
                reasons.append(
                    "Acknowledgement requires TARGET_MISMATCH, got "
                    f"{position.reconciliation.status.value}."
                )
        if not lifecycle.get("fully_accounted"):
            reasons.append("Latest FILLED execution is not fully reconciled and risk-accounted.")
        precheck = {
            "status": "ready" if not reasons else "blocked",
            "account_id": self.account_id,
            "instrument_id": instrument_id,
            "ticker": request.ticker.upper(),
            "current_lots": position.actual_lots if position else None,
            "expected_target_lots": int(request.expected_target_lots),
            "latest_execution_lifecycle": lifecycle,
            "block_reasons": reasons,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self.journal.record(
            JournalEvent(
                category="portfolio",
                event_type="EXTERNAL_CLOSE_ACK_PRECHECK",
                severity="INFO" if not reasons else "ERROR",
                account_id=self.account_id,
                instrument_id=instrument_id,
                ticker=request.ticker.upper(),
                status=precheck["status"],
                payload=precheck,
            )
        )
        if reasons or position is None:
            raise RuntimeError("; ".join(reasons or ["External close acknowledgement failed."]))

        acknowledgement_id = str(__import__("uuid").uuid4())
        now = datetime.now(timezone.utc).isoformat()
        previous_owner = position.ownership.to_dict() if position.ownership else None

        def transform(current: PortfolioState) -> PortfolioState:
            positions: list[PositionState] = []
            found = False
            for item in current.positions:
                if item.instrument_id != position.instrument_id:
                    positions.append(item)
                    continue
                found = True
                positions.append(
                    replace(
                        item,
                        target=PortfolioTarget(
                            instrument_id=item.instrument_id,
                            target_lots=0,
                            strategy_id=None,
                            config_hash=None,
                            candle_time=item.last_candle_time,
                        ),
                        ownership=None,
                        ownership_status=OwnershipStatus.FLAT,
                        origin=PositionOrigin.EXTERNAL,
                        reconciliation=replace(
                            item.reconciliation,
                            status=ReconciliationStatus.MATCHED,
                            blocking=False,
                            reasons=("External flat close acknowledged by operator.",),
                            actual_lots=0,
                            target_lots=0,
                            checked_at=now,
                        ),
                    )
                )
            if not found:
                raise RuntimeError("Canonical target disappeared before acknowledgement commit.")
            blocking = any(item.reconciliation.blocking for item in positions)
            return replace(
                current,
                positions=tuple(positions),
                generated_at=now,
                blocking=blocking,
                state_status="BLOCKED" if blocking else ("EMPTY" if not positions else "READY"),
            )

        result = self.transaction_coordinator.commit(
            "ACKNOWLEDGE_EXTERNAL_CLOSE",
            transform,
            transaction_id=f"external-close:{self.account_id}:{instrument_id}:{acknowledgement_id}",
            account_id=self.account_id,
            instrument_id=instrument_id,
            mode="SANDBOX_EXECUTION",
        )
        payload = {
            **precheck,
            "status": "external_close_acknowledged",
            "acknowledgement_id": acknowledgement_id,
            "previous_active_primary": previous_owner,
            "old_target_lots": int(request.expected_target_lots),
            "new_target_lots": 0,
            "risk_state_changed": False,
            "execution_history_changed": False,
            "old_revision": result.old_revision,
            "new_revision": result.new_revision,
            "transaction_id": result.transaction_id,
            "acknowledged_at": now,
        }
        common = {
            "acknowledgement_id": acknowledgement_id,
            "old_target_lots": int(request.expected_target_lots),
            "new_target_lots": 0,
            "broker_actual_lots": 0,
            "previous_active_primary": previous_owner,
            "risk_state_changed": False,
            "execution_history_changed": False,
            "canonical_revision": result.new_revision,
        }
        for event_type in ("PORTFOLIO_TARGET_CLEARED", "EXTERNAL_CLOSE_ACKNOWLEDGED"):
            self.journal.record(
                JournalEvent(
                    category="portfolio",
                    event_type=event_type,
                    severity="WARNING",
                    account_id=self.account_id,
                    instrument_id=instrument_id,
                    ticker=request.ticker.upper(),
                    status="completed",
                    payload=common,
                )
            )
        return payload

    @staticmethod
    def _find_position(
        state: PortfolioState,
        *,
        instrument_id: str,
        ticker: str = "",
        figi: str = "",
    ) -> PositionState | None:
        tokens = {str(instrument_id), str(figi), str(ticker).upper()}
        for position in state.positions:
            if {
                position.instrument_id,
                position.figi,
                position.ticker.upper(),
            } & tokens:
                return position
        return None

    def _latest_execution_lifecycle(self, instrument_id: str) -> dict[str, Any]:
        rows = self.journal.recent(limit=5_000, account_id=self.account_id)
        filled = next(
            (
                row
                for row in rows
                if str(row.get("instrument_id") or "") == instrument_id
                and str(row.get("event_type") or "").upper() == "FILLED"
                and str(row.get("order_id") or "")
            ),
            None,
        )
        if filled is None:
            return {"fully_accounted": False, "reason": "No FILLED event found."}
        order_id = str(filled.get("order_id") or "")
        events = {
            str(row.get("event_type") or "").upper()
            for row in rows
            if str(row.get("order_id") or "") == order_id
        }
        required = {"FILLED", "PORTFOLIO_RECONCILED", "EXECUTION_RECORDED", "RISK_ACCOUNTED"}
        missing = sorted(required - events)
        return {
            "fully_accounted": not missing,
            "order_id": order_id,
            "filled_at": filled.get("timestamp_utc"),
            "events": sorted(events),
            "missing_events": missing,
        }

    def cached(
        self,
        *,
        mark_stale: bool = False,
        warning: str | None = None,
    ) -> PortfolioState:
        state = self.repository.load(expected_account_id=self.account_id)
        if mark_stale:
            return state.with_freshness(
                SnapshotFreshness.STALE,
                warning=warning or "Cached portfolio snapshot is stale.",
            )
        return state

    def refresh_or_cached(self) -> PortfolioState:
        try:
            return self.refresh()
        except Exception as exc:
            logger.warning("Portfolio refresh failed; returning cached state: %s", exc)
            return self.cached(mark_stale=True, warning=str(exc))

    def snapshot_dict(self) -> dict[str, Any]:
        return PortfolioSnapshotBuilder(self.refresh()).to_dict()

    def _reconcile_and_publish(
        self,
        broker: BrokerPortfolioRecord,
        runtime: RuntimePortfolioRecord,
        *,
        previous: PortfolioState,
        record_event: bool,
        _stage_observer: Callable[[str], None] | None = None,
    ) -> PortfolioState:
        if _stage_observer is not None:
            _stage_observer("RECONCILIATION")
        evidence = self._journal_confirmed_instruments(since=previous.snapshot_at)
        origins = self._journal_position_origins()
        candidate = self.reconciler.reconcile(
            broker,
            runtime,
            previous=previous,
            context=ReconciliationContext(
                freshness=SnapshotFreshness.FRESH,
                expected_account_id=self.account_id,
                journal_confirmed_instruments=frozenset(evidence),
                position_origins=origins,
            ),
        )
        candidate = self._normalize_flat_positions(candidate)
        if _stage_observer is not None:
            _stage_observer("PUBLISH")
        result = self.transaction_coordinator.commit(
            "BROKER_SNAPSHOT_REFRESH",
            lambda current: replace(
                candidate,
                migration=current.migration,
                portfolio_source="CANONICAL",
            ),
            expected_revision=previous.revision,
            expected_document_checksum=portfolio_document_checksum(previous),
            account_id=self.account_id,
        )
        state = result.state
        if record_event:
            self._record_snapshot(state)
        return state

    @staticmethod
    def _normalize_flat_positions(state: PortfolioState) -> PortfolioState:
        positions: list[PositionState] = []
        for item in state.positions:
            if (
                item.actual_lots == 0
                and item.target_lots in {None, 0}
                and not any(order.active for order in item.pending_orders)
            ):
                item = replace(
                    item,
                    ownership=None,
                    ownership_status=OwnershipStatus.FLAT,
                )
            positions.append(item)
        blocking = any(item.reconciliation.blocking for item in positions)
        return replace(
            state,
            positions=tuple(positions),
            blocking=blocking,
            state_status="BLOCKED" if blocking else ("EMPTY" if not positions else "READY"),
        )

    def _journal_confirmed_instruments(self, *, since: str | None) -> set[str]:
        confirmed: set[str] = set()
        since_dt = _parse_timestamp(since) if since else None
        for event in self.journal.recent(limit=10_000, account_id=self.account_id):
            event_type = str(event.get("event_type") or "")
            if event_type == "CANONICAL_TRANSACTION_COMMITTED":
                payload = event.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                if str(payload.get("operation") or "") not in {
                    "STAGE_CONFIRMED_STRATEGY_TARGET",
                    "RECOVER_POSITION_OWNERSHIP",
                    "ACKNOWLEDGE_EXTERNAL_CLOSE",
                }:
                    continue
            elif event_type not in {
                "PORTFOLIO_RECONCILED",
                "BROKER_POSITION_RECONCILED",
                "POST_FILL_CANONICAL_RECONCILED",
                "EXECUTION_RECORDED",
                "RISK_ACCOUNTED",
                "ORDER_RECOVERY_COMPLETED",
            }:
                continue
            if since_dt is not None:
                timestamp = _parse_timestamp(str(event.get("timestamp_utc") or ""))
                if timestamp is None or timestamp <= since_dt:
                    continue
            instrument_id = str(event.get("instrument_id") or "").strip()
            if instrument_id:
                confirmed.add(instrument_id)
        return confirmed

    def _journal_position_origins(self) -> dict[str, PositionOrigin]:
        origins: dict[str, PositionOrigin] = {}
        for event in self.journal.recent(limit=10_000, account_id=self.account_id):
            instrument_id = str(event.get("instrument_id") or "").strip()
            if not instrument_id or instrument_id in origins:
                continue
            event_type = str(event.get("event_type") or "").upper()
            payload = event.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if event_type == "EXTERNAL_CLOSE_ACKNOWLEDGED":
                origins[instrument_id] = PositionOrigin.EXTERNAL
                continue
            if event_type != "EXECUTION_RECORDED":
                continue
            source = str(payload.get("execution_source") or "").strip().upper()
            if source == "STRATEGY":
                origins[instrument_id] = PositionOrigin.STRATEGY
            elif source == "DIAGNOSTIC":
                origins[instrument_id] = PositionOrigin.DIAGNOSTIC
            elif source in {"EXTERNAL", "MANUAL"}:
                origins[instrument_id] = PositionOrigin.EXTERNAL
            else:
                origins[instrument_id] = PositionOrigin.UNKNOWN
        return origins

    def _record_snapshot(self, state: PortfolioState) -> None:
        counts: dict[str, int] = {}
        for result in state.reconciliation_results:
            counts[result.status.value] = counts.get(result.status.value, 0) + 1
        self.journal.record(
            JournalEvent(
                category="portfolio",
                event_type="PORTFOLIO_STATE_PUBLISHED",
                severity="WARNING" if state.blocking else "INFO",
                account_id=self.account_id,
                status=state.state_status,
                payload={
                    "schema_version": state.version,
                    "portfolio_source": state.portfolio_source,
                    "migration_status": state.migration.status.value,
                    "legacy_read_path_enabled": state.migration.legacy_read_path_enabled,
                    "compatibility_shadow_status": state.migration.compatibility_shadow_status.value,
                    "revision": state.revision,
                    "decision_checksum": state.decision_sha256,
                    "snapshot_at": state.snapshot_at,
                    "freshness": state.freshness.value,
                    "position_count": len(state.positions),
                    "blocking": state.blocking,
                    "reconciliation_counts": counts,
                    "warnings": list(state.warnings),
                },
            )
        )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
