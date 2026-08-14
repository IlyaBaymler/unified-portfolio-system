from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .central_order_manager import (
    RESERVATION_STATUSES,
    CentralOrderCandidate,
    CentralOrderIntent,
    CentralOrderManager,
    CentralOrderState,
    EnqueueResult,
    ExecutionAuthorization,
    PortfolioRiskAuthorizationProof,
    central_reservation_projection_hash,
)
from .locking import InterProcessFileLock
from .portfolio_model import PortfolioState
from .portfolio_preflight import PortfolioSnapshotLease
from .portfolio_repository import PortfolioRepository
from .portfolio_risk_adapter import (
    PortfolioRiskInputAdapter,
    PortfolioRiskInstrumentMetadata,
    PortfolioRiskReadOnlyReport,
    build_portfolio_risk_read_only_report,
    portfolio_policy_from_risk_policy,
)
from .portfolio_risk_evaluator import PortfolioRiskEvaluator
from .portfolio_risk_model import (
    PortfolioRiskDecision,
    ProposedPortfolioChange,
)
from .risk import RiskPolicy
from .risk_persistence import RiskProfileStore, RiskStateStore
from .risk_runtime import risk_state_guard_hash


class PortfolioRiskAuthorizationError(RuntimeError):
    """Fail-closed M4 admission or dispatch authorization failure."""

    def __init__(
        self,
        status: str,
        message: str,
        *,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status = str(status or "").strip().upper()
        self.retryable = bool(retryable)


@dataclass(frozen=True, slots=True)
class PortfolioRiskAdmissionResult:
    enqueue: EnqueueResult
    decision: PortfolioRiskDecision


class PortfolioRiskRuntime:
    """Authoritative M4 bridge for Central admission and final dispatch.

    Lock order for every multi-store operation is fixed and must not be
    inverted: canonical portfolio -> Risk profile -> Risk state -> Central.
    The broker adapter keeps the first three locks while Central performs its
    atomic queue transition; no provider POST is possible before all proofs
    have been revalidated.
    """

    def __init__(
        self,
        *,
        account_id: str,
        profile_store: RiskProfileStore,
        state_store: RiskStateStore,
        instrument_metadata: Mapping[
            str,
            PortfolioRiskInstrumentMetadata,
        ]
        | None = None,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.account_id = str(account_id or "").strip()
        if not self.account_id:
            raise ValueError("account_id must not be empty.")
        self.profile_store = profile_store
        self.state_store = state_store
        self.instrument_metadata = dict(instrument_metadata or {})
        if any(
            str(key) != value.instrument_id
            for key, value in self.instrument_metadata.items()
        ):
            raise ValueError("Portfolio Risk metadata key/identity mismatch.")
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        account_id: str,
        instrument_metadata: Mapping[
            str,
            PortfolioRiskInstrumentMetadata,
        ]
        | None = None,
    ) -> PortfolioRiskRuntime:
        root = Path(directory)
        return cls(
            account_id=account_id,
            profile_store=RiskProfileStore(root / "risk_profiles.json"),
            state_store=RiskStateStore(root / "risk_state.json"),
            instrument_metadata=instrument_metadata,
        )

    def admit(
        self,
        manager: CentralOrderManager,
        portfolio_repository: PortfolioRepository,
        candidate: CentralOrderCandidate,
        authorization: ExecutionAuthorization,
        *,
        price_at: datetime,
        price_source: str,
        evaluated_at: datetime,
        cash_buffer_bps: int = 100,
    ) -> PortfolioRiskAdmissionResult:
        """Evaluate and persist one candidate as a single atomic admission."""

        self._validate_scope(manager, candidate, authorization)
        captured_decision: dict[str, PortfolioRiskDecision] = {}
        with portfolio_repository.locked_snapshot(
            expected_account_id=self.account_id
        ) as portfolio:
            self._validate_canonical_authorization(portfolio, authorization)
            with (
                InterProcessFileLock(
                    self.profile_store.lock_path,
                    timeout_seconds=self.lock_timeout_seconds,
                ),
                InterProcessFileLock(
                    self.state_store.lock_path,
                    timeout_seconds=self.lock_timeout_seconds,
                ),
            ):
                policy = self._load_enforced_policy(
                    expected_profile_hash=authorization.risk_policy_hash
                )
                risk_state = self.state_store.load_account(self.account_id)
                expected_guard = str(authorization.risk_state_guard_hash or "")
                if risk_state_guard_hash(risk_state) != expected_guard:
                    raise PortfolioRiskAuthorizationError(
                        "PORTFOLIO_RISK_STATE_CHANGED",
                        "RiskState changed before authoritative Portfolio Risk admission.",
                    )

                def builder(
                    central: CentralOrderState,
                ) -> tuple[CentralOrderCandidate, ExecutionAuthorization]:
                    active_same = next(
                        (
                            item
                            for item in central.intents
                            if item.status in RESERVATION_STATUSES
                            and item.candidate.instrument_id
                            == candidate.instrument_id
                        ),
                        None,
                    )
                    excluded = (
                        (active_same.intent_id,) if active_same is not None else ()
                    )
                    risk_input = PortfolioRiskInputAdapter().build(
                        portfolio=portfolio,
                        central_orders=central,
                        risk_state=risk_state,
                        evaluated_at=self._aware(evaluated_at),
                        instrument_metadata=self._metadata_for(
                            portfolio,
                            candidate,
                        ),
                        excluded_reservation_ids=excluded,
                    )
                    change = self._change(
                        portfolio,
                        candidate,
                        requested_target_lots=candidate.target_lots,
                        price_at=price_at,
                        price_source=price_source,
                        cash_buffer_bps=cash_buffer_bps,
                    )
                    decision = PortfolioRiskEvaluator(policy).evaluate(
                        risk_input,
                        change,
                    ).decision
                    self._require_authorizing_decision(
                        decision,
                        current_lots=candidate.current_lots,
                    )
                    self.state_store.save_account_while_locked(
                        self.account_id,
                        replace(
                            risk_state,
                            last_portfolio_risk_decision_id=decision.decision_id,
                            last_portfolio_risk_input_hash=decision.input_hash,
                            last_portfolio_risk_evaluated_at=(
                                decision.evaluated_at.isoformat()
                            ),
                        ),
                    )
                    final_candidate = replace(
                        candidate,
                        target_lots=decision.approved_target_lots,
                    )
                    proof = PortfolioRiskAuthorizationProof(
                        decision_id=decision.decision_id,
                        input_hash=decision.input_hash,
                        policy_hash=decision.policy_hash,
                        status=decision.status,
                        single_risk_approved_target_lots=(
                            authorization.authorized_target_lots
                        ),
                        approved_target_lots=decision.approved_target_lots,
                        snapshot_revision=decision.snapshot_revision,
                        snapshot_checksum=decision.snapshot_checksum,
                        central_order_revision=decision.central_order_revision,
                        reservation_projection_hash=(
                            decision.reservation_projection_hash
                        ),
                        risk_state_guard_hash=decision.risk_state_guard_hash,
                        evaluated_at=decision.evaluated_at.isoformat(),
                        candidate_price_at=self._aware(price_at).isoformat(),
                        candidate_price_source=price_source,
                        cash_buffer_bps=cash_buffer_bps,
                        excluded_reservation_ids=excluded,
                    )
                    captured_decision["value"] = decision
                    return final_candidate, replace(
                        authorization,
                        authorized_target_lots=decision.approved_target_lots,
                        portfolio_risk=proof,
                    )

                enqueue = manager.admit_portfolio(
                    builder,
                    cash_buffer_bps=cash_buffer_bps,
                )
                decision = captured_decision["value"]
        return PortfolioRiskAdmissionResult(enqueue=enqueue, decision=decision)

    def validate_dispatch(
        self,
        *,
        portfolio: PortfolioState,
        central_orders: CentralOrderState,
        intent: CentralOrderIntent,
        evaluated_at: datetime | None = None,
    ) -> PortfolioRiskDecision:
        """Reconstruct the saved decision and repeat it with current time."""

        proof = intent.authorization.portfolio_risk
        if proof is None or not proof.finalized:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_REAUTHORIZATION_REQUIRED",
                "Queued intent has no finalized authoritative Portfolio Risk proof.",
            )
        self._validate_canonical_authorization(portfolio, intent.authorization)
        if central_orders.revision != proof.admission_central_revision:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_QUEUE_CHANGED",
                "Central queue revision changed after Portfolio Risk admission.",
            )
        current_projection = central_reservation_projection_hash(central_orders)
        if current_projection != proof.admission_reservation_projection_hash:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_RESERVATION_CHANGED",
                "Central reservation projection changed after admission.",
            )
        policy = self._load_enforced_policy(
            expected_profile_hash=intent.authorization.risk_policy_hash
        )
        risk_state = self.state_store.load_account(self.account_id)
        if risk_state_guard_hash(risk_state) != proof.risk_state_guard_hash:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_STATE_CHANGED",
                "RiskState changed after Portfolio Risk admission.",
            )
        pre_admission = replace(
            central_orders,
            revision=proof.central_order_revision,
            intents=tuple(
                item
                for item in central_orders.intents
                if item.intent_id != intent.intent_id
                or intent.intent_id in proof.excluded_reservation_ids
            ),
        )
        metadata = self._metadata_for(portfolio, intent.candidate)
        saved_input = PortfolioRiskInputAdapter().build(
            portfolio=portfolio,
            central_orders=pre_admission,
            risk_state=risk_state,
            evaluated_at=self._parse_timestamp(proof.evaluated_at),
            instrument_metadata=metadata,
            excluded_reservation_ids=proof.excluded_reservation_ids,
        )
        change = self._change(
            portfolio,
            intent.candidate,
            requested_target_lots=proof.single_risk_approved_target_lots,
            price_at=self._parse_timestamp(proof.candidate_price_at),
            price_source=proof.candidate_price_source,
            cash_buffer_bps=proof.cash_buffer_bps,
        )
        saved = PortfolioRiskEvaluator(policy).evaluate(
            saved_input,
            change,
        ).decision
        expected = {
            "decision_id": proof.decision_id,
            "input_hash": proof.input_hash,
            "policy_hash": proof.policy_hash,
            "status": proof.status,
            "approved_target_lots": proof.approved_target_lots,
            "snapshot_revision": proof.snapshot_revision,
            "snapshot_checksum": proof.snapshot_checksum,
            "central_order_revision": proof.central_order_revision,
            "reservation_projection_hash": proof.reservation_projection_hash,
            "risk_state_guard_hash": proof.risk_state_guard_hash,
        }
        actual = {
            key: getattr(saved, key)
            for key in expected
        }
        if actual != expected:
            mismatched = ", ".join(
                key for key in expected if actual[key] != expected[key]
            )
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_PROOF_MISMATCH",
                f"Saved Portfolio Risk decision cannot be reproduced: {mismatched}.",
                retryable=False,
            )

        live_input = replace(
            saved_input,
            evaluated_at=self._aware(evaluated_at or datetime.now(timezone.utc)),
        )
        live = PortfolioRiskEvaluator(policy).evaluate(
            live_input,
            change,
        ).decision
        self._require_authorizing_decision(
            live,
            current_lots=intent.candidate.current_lots,
        )
        if live.approved_target_lots != intent.candidate.target_lots:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_REAUTHORIZATION_REQUIRED",
                "Current Portfolio Risk target differs from the queued target.",
            )
        return live

    def recalculate_current(
        self,
        manager: CentralOrderManager,
        portfolio_repository: PortfolioRepository,
        *,
        evaluated_at: datetime | None = None,
    ) -> PortfolioRiskReadOnlyReport:
        """Recalculate actual portfolio metrics after canonical reconciliation."""

        if manager.account_id != self.account_id:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_ACCOUNT_MISMATCH",
                "Central manager account scope mismatch.",
                retryable=False,
            )
        with (
            portfolio_repository.locked_snapshot(
                expected_account_id=self.account_id
            ) as portfolio,
            InterProcessFileLock(
                self.profile_store.lock_path,
                timeout_seconds=self.lock_timeout_seconds,
            ),
            InterProcessFileLock(
                self.state_store.lock_path,
                timeout_seconds=self.lock_timeout_seconds,
            ),
        ):
            self._load_enforced_policy(expected_profile_hash=None)
            source_policy = self.profile_store.require_portfolio_policy(
                "SANDBOX_EXECUTION"
            )["policy"]
            risk_state = self.state_store.load_account(self.account_id)

            def build(central: CentralOrderState) -> PortfolioRiskReadOnlyReport:
                risk_input = PortfolioRiskInputAdapter().build(
                    portfolio=portfolio,
                    central_orders=central,
                    risk_state=risk_state,
                    evaluated_at=self._aware(
                        evaluated_at or datetime.now(timezone.utc)
                    ),
                    instrument_metadata=self.instrument_metadata,
                )
                return build_portfolio_risk_read_only_report(
                    risk_input=risk_input,
                    policy=source_policy,
                    mode="SANDBOX_EXECUTION",
                )

            return manager.inspect_locked(build)

    def _load_enforced_policy(
        self,
        *,
        expected_profile_hash: str | None,
    ) -> Any:
        loaded = self.profile_store.require_portfolio_policy("SANDBOX_EXECUTION")
        scope = str(loaded.get("account_scope") or "").strip()
        if scope and scope != self.account_id:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_ACCOUNT_MISMATCH",
                "Portfolio Risk profile belongs to a different account.",
                retryable=False,
            )
        if expected_profile_hash is not None and (
            str(loaded["policy_hash"]) != str(expected_profile_hash)
        ):
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_POLICY_CHANGED",
                "Risk profile changed after single-order authorization.",
            )
        policy = loaded["policy"]
        computational = portfolio_policy_from_risk_policy(policy)
        if computational.mode != "ENFORCED":
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_NOT_ENFORCED",
                "Portfolio Risk policy is not explicitly ENFORCED.",
                retryable=False,
            )
        return computational

    def _metadata_for(
        self,
        portfolio: PortfolioState,
        candidate: CentralOrderCandidate,
    ) -> dict[str, PortfolioRiskInstrumentMetadata]:
        metadata = dict(self.instrument_metadata)
        configured = metadata.get(candidate.instrument_id)
        if configured is not None and configured.lot_size != candidate.lot_size:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_METADATA_MISMATCH",
                "Candidate lot size differs from Portfolio Risk metadata.",
                retryable=False,
            )
        position = portfolio.position(candidate.instrument_id)
        metadata[candidate.instrument_id] = PortfolioRiskInstrumentMetadata(
            instrument_id=candidate.instrument_id,
            lot_size=candidate.lot_size,
            asset_class=(
                position.asset_type
                if position is not None and position.asset_type.upper() != "UNKNOWN"
                else (configured.asset_class if configured is not None else None)
            ),
            currency=(
                self._candidate_currency(portfolio, candidate)
            ),
        )
        return metadata

    def _candidate_currency(
        self,
        portfolio: PortfolioState,
        candidate: CentralOrderCandidate,
    ) -> str | None:
        position = portfolio.position(candidate.instrument_id)
        configured = self.instrument_metadata.get(candidate.instrument_id)
        currency = (
            position.currency
            if position is not None and position.currency
            else (configured.currency if configured is not None else None)
        )
        normalized = str(currency or "").strip().upper()
        return normalized or None

    def _change(
        self,
        portfolio: PortfolioState,
        candidate: CentralOrderCandidate,
        *,
        requested_target_lots: int,
        price_at: datetime,
        price_source: str,
        cash_buffer_bps: int,
    ) -> ProposedPortfolioChange:
        position = portfolio.position(candidate.instrument_id)
        configured = self.instrument_metadata.get(candidate.instrument_id)
        asset_class = (
            position.asset_type
            if position is not None and position.asset_type.upper() != "UNKNOWN"
            else (configured.asset_class if configured is not None else None)
        )
        currency = self._candidate_currency(portfolio, candidate)
        if currency is None:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_CURRENCY_UNKNOWN",
                "Authoritative Portfolio Risk requires explicit instrument currency metadata.",
                retryable=False,
            )
        price_per_lot = (
            candidate.estimated_price_kopecks
            / 100.0
            * candidate.lot_size
        )
        return ProposedPortfolioChange(
            instrument_id=candidate.instrument_id,
            ticker=candidate.ticker,
            strategy_id=candidate.strategy_id,
            asset_class=asset_class,
            current_lots=candidate.current_lots,
            requested_target_lots=requested_target_lots,
            lot_size=candidate.lot_size,
            currency=currency,
            price_per_lot_rub=price_per_lot,
            reservation_per_lot_rub=(
                price_per_lot * (10_000 + int(cash_buffer_bps)) / 10_000
            ),
            price_at=self._aware(price_at),
            price_source=price_source,
        )

    @staticmethod
    def _require_authorizing_decision(
        decision: PortfolioRiskDecision,
        *,
        current_lots: int,
    ) -> None:
        if decision.status not in {"PASS", "ADJUSTED", "REDUCTION_ALLOWED"}:
            reasons = (
                *decision.hard_blocks,
                *decision.policy_halts,
                *decision.adjustments,
            )
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_BLOCKED",
                "; ".join(reasons) or "Portfolio Risk blocked the candidate.",
                retryable=False,
            )
        if decision.approved_target_lots == int(current_lots):
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_NO_POSITION_CHANGE",
                "Portfolio Risk approved no position change.",
                retryable=False,
            )

    def _validate_scope(
        self,
        manager: CentralOrderManager,
        candidate: CentralOrderCandidate,
        authorization: ExecutionAuthorization,
    ) -> None:
        scopes = {
            self.account_id,
            manager.account_id,
            candidate.account_id,
            authorization.account_id,
        }
        if len(scopes) != 1:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_ACCOUNT_MISMATCH",
                "Portfolio Risk admission account scopes differ.",
                retryable=False,
            )

    def _validate_canonical_authorization(
        self,
        portfolio: PortfolioState,
        authorization: ExecutionAuthorization,
    ) -> None:
        if portfolio.account_id != self.account_id:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_ACCOUNT_MISMATCH",
                "Canonical portfolio belongs to a different account.",
                retryable=False,
            )
        lease = PortfolioSnapshotLease.from_state(portfolio)
        if (
            lease.revision != authorization.portfolio_revision
            or lease.decision_checksum
            != authorization.portfolio_decision_checksum
            or lease.document_checksum
            != authorization.portfolio_document_checksum
        ):
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_CANONICAL_CHANGED",
                "Canonical portfolio changed after single-order authorization.",
            )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_TIMESTAMP_INVALID",
                "Portfolio Risk timestamps must be timezone-aware.",
                retryable=False,
            )
        return value.astimezone(timezone.utc)

    @classmethod
    def _parse_timestamp(cls, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise PortfolioRiskAuthorizationError(
                "PORTFOLIO_RISK_TIMESTAMP_INVALID",
                "Portfolio Risk proof timestamp is invalid.",
                retryable=False,
            ) from exc
        return cls._aware(parsed)


def portfolio_risk_runtime_from_risk_adapter(
    risk_runtime: Any,
    *,
    instrument_metadata: Mapping[str, PortfolioRiskInstrumentMetadata] | None = None,
) -> PortfolioRiskRuntime | None:
    """Auto-wire M4 only for an explicitly ENFORCED confirmed profile."""

    profile_store = getattr(risk_runtime, "profile_store", None)
    state_store = getattr(risk_runtime, "state_store", None)
    account_id = str(getattr(risk_runtime, "account_id", "") or "").strip()
    mode = str(getattr(risk_runtime, "mode", "") or "").strip().upper()
    if (
        not account_id
        or mode != "SANDBOX_EXECUTION"
        or not isinstance(profile_store, RiskProfileStore)
        or not isinstance(state_store, RiskStateStore)
    ):
        return None
    loaded = profile_store.load_profile("SANDBOX_EXECUTION")
    if loaded is None:
        return None
    policy: RiskPolicy = loaded["policy"]
    if (
        not policy.portfolio_policy_configured
        or policy.portfolio_policy_mode != "ENFORCED"
    ):
        return None
    return PortfolioRiskRuntime(
        account_id=account_id,
        profile_store=profile_store,
        state_store=state_store,
        instrument_metadata=instrument_metadata,
    )
