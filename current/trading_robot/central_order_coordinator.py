from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .central_order_manager import (
    CentralOrderCandidate,
    CentralOrderConflictError,
    CentralOrderIntent,
    CentralOrderManager,
    ExecutionAuthorization,
)
from .instrument_runtime import InstrumentRuntime
from .journal import EventJournal
from .multi_instrument_config import MultiInstrumentProfile
from .multi_instrument_strategy import StrategyProposal
from .portfolio_preflight import (
    PortfolioPreflightGate,
    PortfolioSnapshotLease,
    portfolio_risk_mapping,
)
from .portfolio_repository import PortfolioRepository, PortfolioRepositoryError
from .portfolio_risk_read_service import load_portfolio_risk_metadata
from .portfolio_risk_runtime import (
    PortfolioRiskAuthorizationError,
    PortfolioRiskRuntime,
)
from .portfolio_risk_shadow import (
    PortfolioRiskCandidateQuote,
    PortfolioRiskShadowObserver,
    PortfolioRiskShadowResult,
)
from .risk_runtime import RiskRuntimeAdapter


class CentralOrderCoordinationError(RuntimeError):
    """Raised when coordinator inputs violate the v3.8 execution contract."""


@dataclass(frozen=True, slots=True)
class CentralOrderCoordinationResult:
    """Fail-closed outcome of Strategy -> Risk -> central queue coordination.

    This result never authorizes a provider call. Only SandboxExecutionAdapter
    may cross the broker POST boundary after its separate operator arming gate.
    """

    status: str
    instrument_id: str
    ticker: str
    portfolio_revision: int | None
    current_lots: int | None
    proposed_target_lots: int
    approved_target_lots: int | None = None
    intent_id: str | None = None
    cancelled_intent_id: str | None = None
    preflight_status: str | None = None
    risk_status: str | None = None
    portfolio_risk_status: str | None = None
    portfolio_risk_decision_id: str | None = None
    reason: str | None = None
    portfolio_risk_shadow: PortfolioRiskShadowResult | None = None
    broker_execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.broker_execution_authorized:
            raise CentralOrderCoordinationError(
                "Central coordinator cannot authorize broker execution."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "portfolio_revision": self.portfolio_revision,
            "current_lots": self.current_lots,
            "proposed_target_lots": self.proposed_target_lots,
            "approved_target_lots": self.approved_target_lots,
            "intent_id": self.intent_id,
            "cancelled_intent_id": self.cancelled_intent_id,
            "preflight_status": self.preflight_status,
            "risk_status": self.risk_status,
            "portfolio_risk_status": self.portfolio_risk_status,
            "portfolio_risk_decision_id": self.portfolio_risk_decision_id,
            "reason": self.reason,
            "portfolio_risk_shadow": (
                self.portfolio_risk_shadow.to_dict()
                if self.portfolio_risk_shadow is not None
                else None
            ),
            "broker_execution_authorized": False,
        }


class CentralOrderCoordinator:
    """Create or refresh central intents without access to broker POST.

    Every authorization is derived from one immutable canonical portfolio
    lease. A queued intent is explicitly reauthorized after any portfolio
    revision change. At most one live intent may own an instrument scope.
    """

    def __init__(
        self,
        manager: CentralOrderManager,
        portfolio_repository: PortfolioRepository,
        risk_runtime: Any,
        *,
        preflight_gate: PortfolioPreflightGate | None = None,
        portfolio_risk_shadow: PortfolioRiskShadowObserver | None = None,
        portfolio_risk_runtime: PortfolioRiskRuntime | None = None,
    ) -> None:
        if str(getattr(risk_runtime, "account_id", "")).strip() != (
            manager.account_id
        ):
            raise CentralOrderCoordinationError(
                "Risk runtime account scope does not match Central Order Manager."
            )
        if str(getattr(risk_runtime, "mode", "")).strip().upper() != (
            "SANDBOX_EXECUTION"
        ):
            raise CentralOrderCoordinationError(
                "Central coordinator requires SANDBOX_EXECUTION Risk runtime."
            )
        self.manager = manager
        self.portfolio_repository = portfolio_repository
        self.risk_runtime = risk_runtime
        self.preflight_gate = preflight_gate or PortfolioPreflightGate()
        self.portfolio_risk_shadow = portfolio_risk_shadow
        self.portfolio_risk_runtime = portfolio_risk_runtime
        metadata = None
        metadata_path = manager.store.path.parent / "portfolio_risk_metadata.json"
        if metadata_path.is_file():
            try:
                metadata = load_portfolio_risk_metadata(metadata_path)
            except (OSError, RuntimeError, TypeError, ValueError):
                metadata = None
        if self.portfolio_risk_runtime is not None and (
            self.portfolio_risk_runtime.account_id != manager.account_id
        ):
            raise CentralOrderCoordinationError(
                "Portfolio Risk runtime account scope does not match Central."
            )
        if self.portfolio_risk_shadow is None and isinstance(
            risk_runtime,
            RiskRuntimeAdapter,
        ):
            try:
                runtime_root = manager.store.path.parent
                self.portfolio_risk_shadow = PortfolioRiskShadowObserver(
                    account_id=manager.account_id,
                    mode="SANDBOX_EXECUTION",
                    profile_store=risk_runtime.profile_store,
                    journal=EventJournal(runtime_root / "trading_events.db"),
                    instrument_metadata=(
                        metadata
                    ),
                )
            except (OSError, RuntimeError, sqlite3.Error):
                self.portfolio_risk_shadow = None

    def coordinate(
        self,
        proposal: StrategyProposal,
        runtime: InstrumentRuntime,
        profile: MultiInstrumentProfile,
        *,
        candles: pd.DataFrame,
        lot_size: int,
        now: datetime,
        cash_buffer_bps: int = 100,
        observe_only: bool = False,
        portfolio_risk_candidate_quote: PortfolioRiskCandidateQuote | None = None,
    ) -> CentralOrderCoordinationResult:
        self._validate_inputs(
            proposal,
            runtime,
            profile,
            candles=candles,
            lot_size=lot_size,
            now=now,
        )
        initial_central = self.manager.state()
        if initial_central.blocking_intent is not None:
            blocker = initial_central.blocking_intent
            return self._result(
                proposal,
                status="ACCOUNT_BLOCKED",
                reason=f"{blocker.intent_id} is {blocker.status}.",
            )

        try:
            portfolio_state = self.portfolio_repository.load(
                expected_account_id=self.manager.account_id
            )
        except PortfolioRepositoryError as exc:
            return self._result(
                proposal,
                status="CANONICAL_UNAVAILABLE",
                reason=str(exc),
            )
        lease = PortfolioSnapshotLease.from_state(portfolio_state)
        position = portfolio_state.position(proposal.instrument_id)
        current_lots = int(position.actual_lots) if position is not None else 0
        existing = self._queued_for_instrument(
            initial_central.queued,
            proposal.instrument_id,
        )

        preflight = self.preflight_gate.evaluate(
            lease,
            account_id=self.manager.account_id,
            mode="SANDBOX_EXECUTION",
            instrument_id=proposal.instrument_id,
            proposed_target_lots=proposal.primary_target_lots,
            legacy=None,
            require_dual_read=False,
        )
        if not preflight.allowed:
            return self._result(
                proposal,
                status="PREFLIGHT_BLOCKED",
                lease=lease,
                current_lots=current_lots,
                preflight_status=preflight.status.value,
                reason="; ".join(preflight.reasons),
            )

        primary = proposal.decisions[proposal.primary_strategy]
        config = profile.to_bot_config(
            mode="SANDBOX_EXECUTION",
            state_file="robot_state.json",
            journal_file="trading_events.db",
        )
        price = float(primary.indicators["close"])
        risk = self.risk_runtime.evaluate(
            now=_utc(now),
            strategy_target_lots=int(proposal.primary_target_lots),
            current_lots=current_lots,
            price_rub=price,
            lot_size=int(lot_size),
            portfolio=portfolio_risk_mapping(lease.state),
            candles=candles,
            atr_window=int(config.donchian_atr_window),
            stop_level=primary.stop_level,
            position_reconciled=True,
            pending_order=False,
            snapshot_at=_parse_timestamp(lease.state.snapshot_at),
            decision_context=(
                f"{runtime.runtime_key}|{proposal.candle_time}|"
                f"{proposal.primary_strategy}|{proposal.strategy_profile_hash}|"
                f"{lease.decision_checksum}"
            ),
            portfolio_preflight=preflight.context.to_dict(),
        )
        risk_status = str(getattr(risk, "status", "")).strip().upper()
        assessment = getattr(risk, "assessment", None)
        decision = getattr(assessment, "decision", None)
        approved_target = int(getattr(risk, "approved_target_lots", current_lots))
        assessment_failed = (
            not bool(getattr(risk, "enforced", False))
            or str(getattr(risk, "mode", "")).strip().upper()
            != "SANDBOX_EXECUTION"
            or getattr(risk, "error", None)
            or decision is None
        )
        portfolio_shadow = None
        if self.portfolio_risk_shadow is not None and decision is not None:
            try:
                shadow_quote = portfolio_risk_candidate_quote
                if (
                    shadow_quote is None
                    and not observe_only
                    and self.portfolio_risk_runtime is None
                ):
                    shadow_quote = PortfolioRiskCandidateQuote(
                        unit_price_rub=price,
                        price_at=proposal.candle_time,
                        source="LEGACY_STRATEGY_CLOSED_CANDLE",
                    )
                portfolio_shadow = self.portfolio_risk_shadow.observe(
                    proposal=proposal,
                    portfolio=portfolio_state,
                    central_orders=initial_central,
                    risk_state=assessment.state,
                    actual_approved_target_lots=approved_target,
                    actual_risk_decision_id=str(risk.decision_id or ""),
                    actual_risk_status=risk_status or None,
                    actual_risk_policy_hash=risk.policy_hash,
                    actual_reason_codes=tuple(
                        str(item)
                        for item in (
                            *(getattr(decision, "reasons", ()) or ()),
                            *(getattr(decision, "breaches", ()) or ()),
                        )
                    ),
                    lot_size=lot_size,
                    candidate_quote=shadow_quote,
                    evaluated_at=_utc(now),
                    cash_buffer_bps=cash_buffer_bps,
                    excluded_reservation_ids=(
                        (existing.intent_id,) if existing is not None else ()
                    ),
                )
            except (
                ArithmeticError,
                LookupError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                sqlite3.Error,
            ):
                # M3 observability must never mutate the accepted v3.8 path.
                portfolio_shadow = None
        if observe_only:
            return self._result(
                proposal,
                status=(
                    "SHADOW_OBSERVED"
                    if portfolio_shadow is not None
                    else "SHADOW_UNAVAILABLE"
                ),
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                preflight_status=preflight.status.value,
                risk_status=risk_status or None,
                portfolio_risk_shadow=portfolio_shadow,
                reason=(
                    None
                    if portfolio_shadow is not None
                    else (
                        str(getattr(risk, "error", "") or "").strip()
                        or "Portfolio Risk shadow observation is unavailable."
                    )
                ),
            )
        no_position_change = approved_target == current_lots
        no_change_is_safe = (
            not assessment_failed
            and no_position_change
            and risk_status in {"PASS", "ADJUSTED", "REDUCTION_ALLOWED"}
        )
        if no_change_is_safe:
            cancelled = None
            if existing is not None:
                cancelled = self.manager.cancel_queued(
                    existing.intent_id,
                    reason=(
                        "Canonical position already matches the latest "
                        "Risk-approved target."
                    ),
                ).intent_id
            return self._result(
                proposal,
                status=(
                    "CANCELLED_NO_POSITION_CHANGE"
                    if cancelled is not None
                    else "NO_POSITION_CHANGE"
                ),
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                cancelled_intent_id=cancelled,
                preflight_status=preflight.status.value,
                risk_status=risk_status,
                portfolio_risk_shadow=portfolio_shadow,
            )
        if assessment_failed or not bool(
            getattr(decision, "order_allowed", False)
        ):
            return self._result(
                proposal,
                status="RISK_BLOCKED",
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                preflight_status=preflight.status.value,
                risk_status=risk_status or None,
                portfolio_risk_shadow=portfolio_shadow,
                reason=(
                    str(getattr(risk, "error", "") or "").strip()
                    or "; ".join(getattr(decision, "reasons", ()) or ())
                    or "Risk did not authorize a position change."
                ),
            )

        try:
            authoritative_quote = portfolio_risk_candidate_quote
            if (
                self.portfolio_risk_runtime is not None
                and authoritative_quote is None
            ):
                raise PortfolioRiskAuthorizationError(
                    "PORTFOLIO_RISK_PRICE_UNAVAILABLE",
                    "Authoritative Portfolio Risk requires a current candidate quote.",
                    retryable=True,
                )
            authorization = ExecutionAuthorization.from_gate_results(
                preflight,
                risk,
                lease=lease,
                authorized_at=_utc(now).isoformat(),
            )
            candidate = CentralOrderCandidate.from_strategy_proposal(
                proposal,
                account_id=self.manager.account_id,
                runtime_config_hash=runtime.config.runtime_config_hash,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                estimated_price_rub=(
                    authoritative_quote.unit_price_rub
                    if authoritative_quote is not None
                    else price
                ),
                lot_size=lot_size,
            )
        except (
            CentralOrderConflictError,
            PortfolioRiskAuthorizationError,
            TypeError,
            ValueError,
        ) as exc:
            return self._result(
                proposal,
                status=getattr(exc, "status", "AUTHORIZATION_BLOCKED"),
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                preflight_status=preflight.status.value,
                risk_status=risk_status or None,
                portfolio_risk_shadow=portfolio_shadow,
                reason=str(exc),
            )

        if self.portfolio_risk_runtime is not None:
            assert authoritative_quote is not None
            try:
                admitted = self.portfolio_risk_runtime.admit(
                    self.manager,
                    self.portfolio_repository,
                    candidate,
                    authorization,
                    price_at=authoritative_quote.price_at,
                    price_source=authoritative_quote.source,
                    evaluated_at=_utc(now),
                    cash_buffer_bps=cash_buffer_bps,
                )
            except PortfolioRiskAuthorizationError as exc:
                return self._result(
                    proposal,
                    status=exc.status,
                    lease=lease,
                    current_lots=current_lots,
                    approved_target_lots=current_lots,
                    preflight_status=preflight.status.value,
                    risk_status=risk_status,
                    portfolio_risk_shadow=portfolio_shadow,
                    reason=str(exc),
                )
            except (CentralOrderConflictError, OSError, RuntimeError) as exc:
                return self._result(
                    proposal,
                    status="PORTFOLIO_RISK_ADMISSION_UNAVAILABLE",
                    lease=lease,
                    current_lots=current_lots,
                    approved_target_lots=current_lots,
                    preflight_status=preflight.status.value,
                    risk_status=risk_status,
                    portfolio_risk_shadow=portfolio_shadow,
                    reason=str(exc),
                )
            enqueue = admitted.enqueue
            final_target = admitted.decision.approved_target_lots
            if enqueue.reauthorized:
                final_status = "REAUTHORIZED"
            elif enqueue.replaced_intent_id is not None:
                final_status = "REPLACED"
            elif enqueue.idempotent:
                final_status = "ALREADY_PROCESSED"
            else:
                final_status = "QUEUED"
            return self._result(
                proposal,
                status=final_status,
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=final_target,
                intent_id=enqueue.intent.intent_id,
                cancelled_intent_id=enqueue.replaced_intent_id,
                preflight_status=preflight.status.value,
                risk_status=risk_status,
                portfolio_risk_status=admitted.decision.status,
                portfolio_risk_decision_id=admitted.decision.decision_id,
                portfolio_risk_shadow=portfolio_shadow,
            )

        revision_check = self.preflight_gate.recheck(
            self.portfolio_repository,
            lease,
            expected_account_id=self.manager.account_id,
        )
        if not revision_check.unchanged:
            return self._result(
                proposal,
                status="CANONICAL_CHANGED",
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                preflight_status=preflight.status.value,
                risk_status=risk_status,
                portfolio_risk_shadow=portfolio_shadow,
                reason=revision_check.reason,
            )

        latest_central = self.manager.state()
        if latest_central.blocking_intent is not None:
            blocker = latest_central.blocking_intent
            return self._result(
                proposal,
                status="ACCOUNT_BLOCKED",
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                preflight_status=preflight.status.value,
                risk_status=risk_status,
                portfolio_risk_shadow=portfolio_shadow,
                reason=f"{blocker.intent_id} is {blocker.status}.",
            )

        duplicate = next(
            (
                item
                for item in latest_central.intents
                if item.idempotency_key == candidate.idempotency_key()
            ),
            None,
        )
        if duplicate is not None and duplicate.status != "QUEUED":
            return self._result(
                proposal,
                status="ALREADY_PROCESSED",
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                intent_id=duplicate.intent_id,
                preflight_status=preflight.status.value,
                risk_status=risk_status,
                portfolio_risk_shadow=portfolio_shadow,
                reason=f"Existing intent is {duplicate.status}; resubmit is forbidden.",
            )

        queued = self._queued_for_instrument(
            latest_central.queued,
            proposal.instrument_id,
        )
        if queued is not None and queued.idempotency_key == candidate.idempotency_key():
            refreshed = self.manager.reauthorize_queued(
                queued.intent_id,
                authorization,
            )
            return self._result(
                proposal,
                status="REAUTHORIZED",
                lease=lease,
                current_lots=current_lots,
                approved_target_lots=approved_target,
                intent_id=refreshed.intent_id,
                preflight_status=preflight.status.value,
                risk_status=risk_status,
                portfolio_risk_shadow=portfolio_shadow,
            )

        cancelled = None
        if queued is not None:
            cancelled = self.manager.cancel_queued(
                queued.intent_id,
                reason="Superseded by a newer canonical Risk-authorized intent.",
            ).intent_id
        enqueued = self.manager.enqueue(
            candidate,
            authorization,
            cash_buffer_bps=cash_buffer_bps,
        )
        return self._result(
            proposal,
            status=("REPLACED" if cancelled is not None else "QUEUED"),
            lease=lease,
            current_lots=current_lots,
            approved_target_lots=approved_target,
            intent_id=enqueued.intent.intent_id,
            cancelled_intent_id=cancelled,
            preflight_status=preflight.status.value,
            risk_status=risk_status,
            portfolio_risk_shadow=portfolio_shadow,
        )

    def _validate_inputs(
        self,
        proposal: StrategyProposal,
        runtime: InstrumentRuntime,
        profile: MultiInstrumentProfile,
        *,
        candles: pd.DataFrame,
        lot_size: int,
        now: datetime,
    ) -> None:
        if runtime.config.account_id != self.manager.account_id:
            raise CentralOrderCoordinationError(
                "Instrument runtime account scope mismatch."
            )
        if runtime.status != "ACTIVE":
            raise CentralOrderCoordinationError(
                "Only an ACTIVE InstrumentRuntime may create central intents."
            )
        expected = profile.to_runtime_config(self.manager.account_id)
        if expected.to_dict() != runtime.config.to_dict():
            raise CentralOrderCoordinationError(
                "Instrument runtime does not match the selected profile."
            )
        if (
            proposal.execution_authorized
            or proposal.runtime_key != runtime.runtime_key
            or proposal.instrument_id != runtime.config.instrument_id
            or proposal.ticker.upper() != runtime.config.ticker
            or proposal.candle_interval.upper() != runtime.config.candle_interval
            or proposal.primary_strategy.lower() != runtime.config.strategy_id
            or proposal.strategy_profile_hash != profile.strategy_profile_hash
        ):
            raise CentralOrderCoordinationError(
                "Strategy proposal/runtime/profile identity mismatch."
            )
        try:
            primary = proposal.decisions[proposal.primary_strategy]
        except KeyError as exc:
            raise CentralOrderCoordinationError(
                "Strategy proposal has no PRIMARY decision."
            ) from exc
        if (
            str(primary.candle_time) != proposal.candle_time
            or int(primary.target_lots) != int(proposal.primary_target_lots)
        ):
            raise CentralOrderCoordinationError(
                "Strategy proposal summary does not match its PRIMARY decision."
            )
        try:
            price = float(primary.indicators["close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CentralOrderCoordinationError(
                "PRIMARY decision has no usable close price."
            ) from exc
        if not math.isfinite(price) or price <= 0:
            raise CentralOrderCoordinationError(
                "PRIMARY decision close price must be finite and positive."
            )
        if not isinstance(candles, pd.DataFrame) or candles.empty:
            raise CentralOrderCoordinationError(
                "Risk authorization requires a non-empty candle frame."
            )
        latest_candle = pd.Timestamp(candles.index[-1])
        if latest_candle.tzinfo is None:
            latest_candle = latest_candle.tz_localize("UTC")
        else:
            latest_candle = latest_candle.tz_convert("UTC")
        if latest_candle.to_pydatetime() != _parse_timestamp(
            proposal.candle_time
        ):
            raise CentralOrderCoordinationError(
                "Risk candle frame does not end at the proposal candle."
            )
        if isinstance(lot_size, bool) or int(lot_size) != lot_size or int(lot_size) < 1:
            raise CentralOrderCoordinationError("lot_size must be a positive integer.")
        _utc(now)

    @staticmethod
    def _queued_for_instrument(
        queued: tuple[CentralOrderIntent, ...],
        instrument_id: str,
    ) -> CentralOrderIntent | None:
        matches = [
            item
            for item in queued
            if item.candidate.instrument_id == instrument_id
        ]
        if len(matches) > 1:
            raise CentralOrderCoordinationError(
                "More than one queued intent owns the same instrument scope."
            )
        return matches[0] if matches else None

    @staticmethod
    def _result(
        proposal: StrategyProposal,
        *,
        status: str,
        lease: PortfolioSnapshotLease | None = None,
        current_lots: int | None = None,
        approved_target_lots: int | None = None,
        intent_id: str | None = None,
        cancelled_intent_id: str | None = None,
        preflight_status: str | None = None,
        risk_status: str | None = None,
        portfolio_risk_status: str | None = None,
        portfolio_risk_decision_id: str | None = None,
        portfolio_risk_shadow: PortfolioRiskShadowResult | None = None,
        reason: str | None = None,
    ) -> CentralOrderCoordinationResult:
        return CentralOrderCoordinationResult(
            status=status,
            instrument_id=proposal.instrument_id,
            ticker=proposal.ticker,
            portfolio_revision=(lease.revision if lease is not None else None),
            current_lots=current_lots,
            proposed_target_lots=int(proposal.primary_target_lots),
            approved_target_lots=approved_target_lots,
            intent_id=intent_id,
            cancelled_intent_id=cancelled_intent_id,
            preflight_status=preflight_status,
            risk_status=risk_status,
            portfolio_risk_status=portfolio_risk_status,
            portfolio_risk_decision_id=portfolio_risk_decision_id,
            portfolio_risk_shadow=portfolio_risk_shadow,
            reason=reason,
        )


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CentralOrderCoordinationError(
            "Canonical snapshot timestamp is invalid."
        ) from exc
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CentralOrderCoordinationError(
            "Coordinator timestamps must be timezone-aware."
        )
    return value.astimezone(timezone.utc)
