from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .central_order_manager import (
    CentralOrderConflictError,
    CentralOrderError,
    CentralOrderIntent,
    CentralOrderManager,
    CentralOrderState,
)
from .market_idle import MarketAvailability, classify_market_status
from .orders import (
    executed_lots,
    executed_order_price,
    is_terminal_order_status,
    normalize_execution_status,
)
from .portfolio_preflight import PortfolioSnapshotLease
from .portfolio_repository import PortfolioRepository, PortfolioRepositoryError
from .portfolio_risk_runtime import (
    PortfolioRiskAuthorizationError,
    PortfolioRiskRuntime,
)
from .risk_runtime import RiskDispatchAuthorizationError
from .runtime_cash_authority import (
    CL7RuntimeError,
    CL7RuntimeReason,
    LockedDispatchProof,
    RuntimeCashAuthorityManager,
    RuntimeCashAuthorityRecord,
    RuntimeCashAuthorityState,
)
from .tbank_sandbox import TBankAPIError

SANDBOX_EXECUTION_CONFIRMATION = "ENABLE V3.8 SANDBOX EXECUTION"
_AMBIGUOUS_HTTP_STATUSES = frozenset({408, 409, 425, 429})


class SandboxExecutionTransport(Protocol):
    def get_trading_status(self, instrument_id: str) -> dict[str, Any]: ...

    def post_order(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        *,
        order_id: str,
        order_type: str,
        time_in_force: str,
    ) -> dict[str, Any]: ...

    def post_order_once(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        *,
        order_id: str,
        order_type: str,
        time_in_force: str,
    ) -> dict[str, Any]: ...

    def get_order_state(
        self,
        account_id: str,
        order_id: str,
        *,
        by_request_id: bool = True,
    ) -> dict[str, Any]: ...

    def get_operations_by_cursor_once(
        self, payload: dict[str, Any], timeout_ns: int
    ) -> dict[str, Any]: ...

    def get_portfolio(self, account_id: str) -> dict[str, Any]: ...

    def get_positions(self, account_id: str) -> dict[str, Any]: ...


class SandboxRiskAuthorizationGate(Protocol):
    account_id: str
    mode: str

    def dispatch_authorization_guard(
        self,
        *,
        expected_policy_hash: str,
        expected_state_guard_hash: str | None,
        instrument_id: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class SandboxExecutionPolicy:
    account_id: str
    enabled: bool = False
    confirmation: str = ""

    def __post_init__(self) -> None:
        account_id = str(self.account_id or "").strip()
        if not account_id:
            raise ValueError("Sandbox execution policy requires account_id.")
        if not isinstance(self.enabled, bool):
            raise TypeError("Sandbox execution policy enabled must be boolean.")
        confirmation = str(self.confirmation or "").strip()
        if self.enabled and confirmation != SANDBOX_EXECUTION_CONFIRMATION:
            raise ValueError(
                "Sandbox execution requires the exact arming confirmation."
            )
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "confirmation", confirmation)

    @property
    def armed(self) -> bool:
        return self.enabled and self.confirmation == SANDBOX_EXECUTION_CONFIRMATION


@dataclass(frozen=True, slots=True)
class SandboxDispatchResult:
    status: str
    intent_id: str | None = None
    order_was_sent: bool = False
    order_may_have_been_sent: bool = False
    retryable: bool = False
    market: MarketAvailability | None = None
    broker_order_id: str | None = None
    provider_status: str | None = None
    executed_lots: int = 0
    terminal: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxInspectionResult:
    status: str
    intent_id: str | None = None
    broker_order_id: str | None = None
    provider_status: str | None = None
    executed_lots: int = 0
    terminal: bool = False
    suggested_reconciliation_outcome: str | None = None
    execution_price_rub: float | None = None
    execution_price_source: str | None = None
    retryable: bool = False
    error: str | None = None


class SandboxExecutionAdapter:
    """The only v3.8 boundary allowed to call Sandbox ``post_order``.

    The adapter is intentionally not wired to ``GlobalScheduler``. It performs
    one market precheck and one idempotent submission handoff. Ambiguous
    outcomes become account-wide ``UNCERTAIN`` blockers and are never
    resubmitted by this component.
    """

    def __init__(
        self,
        transport: SandboxExecutionTransport,
        manager: CentralOrderManager,
        policy: SandboxExecutionPolicy,
        *,
        risk_runtime: SandboxRiskAuthorizationGate | None = None,
        portfolio_risk_runtime: PortfolioRiskRuntime | None = None,
        cash_authority_manager: RuntimeCashAuthorityManager | None = None,
        cl7_identity_key: bytes | None = None,
        cl7_identity_key_id: str | None = None,
        cl7_ledger_store: Any | None = None,
        cl7_proof_builder: Callable[
            [RuntimeCashAuthorityRecord, CentralOrderState, CentralOrderIntent],
            LockedDispatchProof,
        ]
        | None = None,
        cl7_clock: Callable[[], str] | None = None,
        cl7_monotonic_ns: Callable[[], int] | None = None,
        cl7_wait_ns: Callable[[int], object] | None = None,
    ) -> None:
        if policy.account_id != manager.account_id:
            raise ValueError("Sandbox execution policy account scope mismatch.")
        if risk_runtime is not None and (
            str(getattr(risk_runtime, "account_id", "")).strip() != manager.account_id
            or str(getattr(risk_runtime, "mode", "")).strip().upper()
            != "SANDBOX_EXECUTION"
        ):
            raise ValueError("Sandbox Risk authorization scope mismatch.")
        self.transport = transport
        self.manager = manager
        self.policy = policy
        self.risk_runtime = risk_runtime
        self.portfolio_risk_runtime = portfolio_risk_runtime
        self.cash_authority_manager = cash_authority_manager
        self.cl7_identity_key = cl7_identity_key
        self.cl7_identity_key_id = cl7_identity_key_id
        self.cl7_ledger_store = cl7_ledger_store
        self.cl7_proof_builder = cl7_proof_builder
        self.cl7_clock = cl7_clock or (
            lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
            + "000Z"
        )
        self.cl7_monotonic_ns = cl7_monotonic_ns or time.monotonic_ns
        self.cl7_wait_ns = cl7_wait_ns or (
            lambda duration: time.sleep(duration / 1_000_000_000)
        )
        if self.portfolio_risk_runtime is not None and (
            self.portfolio_risk_runtime.account_id != manager.account_id
        ):
            raise ValueError("Sandbox Portfolio Risk authorization scope mismatch.")

    def dispatch_next(
        self,
        portfolio_repository: PortfolioRepository,
        *,
        expected_intent_id: str | None = None,
    ) -> SandboxDispatchResult:
        state = self.manager.state()
        authority = None
        if self.cash_authority_manager is not None:
            try:
                authority = self.cash_authority_manager.status()
            except CL7RuntimeError as exc:
                return SandboxDispatchResult(
                    status="CL7_RECOVERY_BLOCKED",
                    error=exc.reason.value,
                )
            if (
                authority.state
                is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
            ):
                return SandboxDispatchResult(
                    status="CL7_DISPATCH_PENDING",
                    error="DISPATCH_PENDING",
                )
        blocker = state.blocking_intent
        if blocker is not None:
            return SandboxDispatchResult(
                status="ACCOUNT_BLOCKED",
                intent_id=blocker.intent_id,
            )
        if not state.queued:
            return SandboxDispatchResult(status="IDLE")
        intent = state.queued[0]
        expected = str(expected_intent_id or "").strip() or None
        if expected is not None and expected != intent.intent_id:
            return SandboxDispatchResult(
                status="OPERATOR_INTENT_MISMATCH",
                intent_id=intent.intent_id,
                error="Selected intent is not the current account-wide queue head.",
            )
        if (
            authority is not None
            and authority.state is not RuntimeCashAuthorityState.LEGACY_ACTIVE
        ):
            return self._dispatch_exact(
                intent,
                portfolio_repository,
                expected_intent_id=expected,
            )
        if not self.policy.armed:
            return SandboxDispatchResult(
                status="DISARMED",
                intent_id=intent.intent_id,
            )
        return self._dispatch_legacy_authorized(intent, portfolio_repository)

    def _dispatch_exact(
        self,
        intent: CentralOrderIntent,
        portfolio_repository: PortfolioRepository,
        *,
        expected_intent_id: str | None,
    ) -> SandboxDispatchResult:
        if expected_intent_id is None:
            return SandboxDispatchResult(
                status="OPERATOR_INTENT_REQUIRED",
                error="CENTRAL_CHANGED",
            )
        if (
            type(self.cl7_identity_key) is not bytes
            or type(self.cl7_identity_key_id) is not str
            or self.cl7_ledger_store is None
        ):
            return SandboxDispatchResult(
                status="CL7_CONTEXT_UNAVAILABLE",
                error="CONTEXT_BLOCKED",
            )
        post_once = getattr(self.transport, "post_order_once", None)
        if not callable(post_once):
            return SandboxDispatchResult(
                status="CL7_TRANSPORT_UNAVAILABLE",
                error="INTERNAL_BOUNDARY_FAILED",
            )
        market_result = self._market_precheck(intent)
        if isinstance(market_result, SandboxDispatchResult):
            return market_result
        market = market_result
        authority_manager = self.cash_authority_manager
        assert authority_manager is not None
        try:
            with authority_manager.store.locked():
                authority = authority_manager.store._load_unlocked(
                    allow_missing_legacy=False
                )
                if authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING:
                    raise CL7RuntimeError(CL7RuntimeReason.DISPATCH_PENDING)
                if authority.state is not RuntimeCashAuthorityState.EXACT_CASH_ARMED:
                    raise CL7RuntimeError(CL7RuntimeReason.DISPATCH_NOT_ARMED)
                authority_manager._account(
                    authority,
                    self.policy.account_id,
                    self.cl7_identity_key,
                    self.cl7_identity_key_id,
                )
                sync_to = self.cl7_clock()
                from .broker_read_adapters import RetryPolicy

                authority, _batch = authority_manager.synchronize_operations_locked(
                    authority,
                    ledger_store=self.cl7_ledger_store,
                    raw_account_id=self.policy.account_id,
                    identity_key=self.cl7_identity_key,
                    identity_key_id=self.cl7_identity_key_id,
                    sync_to_exclusive=sync_to,
                    transport=self.transport.get_operations_by_cursor_once,
                    monotonic_ns=self.cl7_monotonic_ns,
                    wait_ns=self.cl7_wait_ns,
                    absolute_deadline_ns=self.cl7_monotonic_ns()
                    + 60_000_000_000,
                    retry_policy=RetryPolicy(
                        max_attempts=3,
                        per_attempt_timeout_ns=10_000_000_000,
                        backoff_ns=(100_000_000, 500_000_000),
                    ),
                    transition_at=self.cl7_clock(),
                )
                try:
                    portfolio_response = self.transport.get_portfolio(
                        self.policy.account_id
                    )
                    positions_response = self.transport.get_positions(
                        self.policy.account_id
                    )
                    provider_as_of = self.cl7_clock()
                except Exception:  # noqa: BLE001 - provider trust boundary
                    raise CL7RuntimeError(
                        CL7RuntimeReason.BROKER_READ_FAILED,
                        stage="CURRENT_CASH_POSITIONS",
                        retryable=True,
                    ) from None
                with portfolio_repository.locked_snapshot(
                    expected_account_id=self.policy.account_id
                ) as locked_portfolio:
                    risk_guard = (
                        self.risk_runtime.dispatch_authorization_guard(
                            expected_policy_hash=intent.authorization.risk_policy_hash,
                            expected_state_guard_hash=(
                                intent.authorization.risk_state_guard_hash
                            ),
                            instrument_id=intent.candidate.instrument_id,
                        )
                        if self.risk_runtime is not None
                        else None
                    )
                    if risk_guard is None:
                        raise CL7RuntimeError(CL7RuntimeReason.RISK_CHANGED)
                    with risk_guard:
                        try:
                            risk_policy, _created = self.risk_runtime._load_policy()
                            risk_state = self.risk_runtime.state_store.load_account(
                                self.policy.account_id
                            )
                        except Exception:  # noqa: BLE001 - risk custody boundary
                            raise CL7RuntimeError(
                                CL7RuntimeReason.RISK_CHANGED
                            ) from None
                        evaluated_at = self.cl7_clock()
                        portfolio_lease = PortfolioSnapshotLease.from_state(
                            locked_portfolio,
                            leased_at=_cl7_iso_timestamp(evaluated_at),
                        )
                        with authority_manager.ledger_guard(self.cl7_ledger_store):
                            def validate(
                                central: CentralOrderState,
                                queued: CentralOrderIntent,
                            ) -> LockedDispatchProof:
                                if self.portfolio_risk_runtime is not None:
                                    self.portfolio_risk_runtime.validate_dispatch(
                                        portfolio=locked_portfolio,
                                        central_orders=central,
                                        intent=queued,
                                    )
                                evidence = authority_manager.build_runtime_context(
                                    current=authority,
                                    ledger_store=self.cl7_ledger_store,
                                    portfolio_response=portfolio_response,
                                    positions_response=positions_response,
                                    broker_cash_as_of=provider_as_of,
                                    broker_positions_as_of=provider_as_of,
                                    central_state=central,
                                    portfolio_lease=portfolio_lease,
                                    risk_policy=risk_policy,
                                    risk_state=risk_state,
                                    raw_account_id=self.policy.account_id,
                                    identity_key=self.cl7_identity_key,
                                    identity_key_id=self.cl7_identity_key_id,
                                    evaluated_at=evaluated_at,
                                    require_ready=True,
                                )
                                if self.cl7_proof_builder is None:
                                    proof = authority_manager.build_locked_dispatch_proof(
                                        context=evidence.context,
                                        authority_record=authority,
                                        raw_intent_id=queued.intent_id,
                                        identity_key=self.cl7_identity_key,
                                        reserved_cash=_cl7_reserved_cash(queued),
                                        current_lots=queued.candidate.current_lots,
                                        target_lots=queued.candidate.target_lots,
                                        direction=queued.candidate.direction,
                                        evaluated_at=evaluated_at,
                                    )
                                else:
                                    proof = self.cl7_proof_builder(
                                        authority,
                                        central,
                                        queued,
                                    )
                                if not isinstance(proof, LockedDispatchProof):
                                    raise CL7RuntimeError(
                                        CL7RuntimeReason.DISPATCH_PROOF_INVALID
                                    )
                                proof.verify_identity(
                                    raw_intent_id=queued.intent_id,
                                    identity_key=self.cl7_identity_key,
                                )
                                _require_cl7_proof_fresh(proof, self.cl7_clock())
                                return proof

                            with self.manager.locked_dispatch_lease(
                                portfolio_repository,
                                expected_intent_id=expected_intent_id,
                                locked_portfolio_state=locked_portfolio,
                                validator=validate,
                            ) as lease:
                                try:
                                    pending = authority_manager.record_dispatch_attempt_locked(
                                        authority,
                                        lease.proof,
                                        transition_at=self.cl7_clock(),
                                    )
                                except CL7RuntimeError as exc:
                                    if exc.reason is CL7RuntimeReason.ATTEMPT_RECORD_FAILED:
                                        lease.mark_pre_submit_failed(
                                            reason="CL7_ATTEMPT_RECORD_FAILED"
                                        )
                                    raise
                                try:
                                    response = self.transport.post_order_once(
                                        self.policy.account_id,
                                        lease.intent.candidate.instrument_id,
                                        lease.intent.candidate.requested_lots,
                                        lease.intent.candidate.direction,
                                        order_id=lease.intent.intent_id,
                                        order_type=lease.intent.candidate.order_type,
                                        time_in_force=lease.intent.candidate.time_in_force,
                                    )
                                except TBankAPIError as exc:
                                    if _exact_provider_rejection(exc):
                                        lease.mark_submission_rejected(
                                            reason="CL7_PROVIDER_EXPLICIT_REJECTION"
                                        )
                                        authority_manager.clear_dispatch_locked(
                                            pending,
                                            proof=lease.proof,
                                            central_intent=lease.intent,
                                            transition_at=self.cl7_clock(),
                                        )
                                        return SandboxDispatchResult(
                                            status="SUBMISSION_REJECTED",
                                            intent_id=lease.intent.intent_id,
                                            order_was_sent=True,
                                            market=market,
                                            error="PROVIDER_REJECTED",
                                        )
                                    lease.mark_uncertain(
                                        reason="CL7_PROVIDER_OUTCOME_UNCERTAIN"
                                    )
                                    return SandboxDispatchResult(
                                        status="SUBMISSION_UNCERTAIN",
                                        intent_id=lease.intent.intent_id,
                                        order_was_sent=True,
                                        order_may_have_been_sent=True,
                                        market=market,
                                        error="PROVIDER_OUTCOME_UNCERTAIN",
                                    )
                                except Exception:  # noqa: BLE001 - post outcome boundary
                                    lease.mark_uncertain(
                                        reason="CL7_PROVIDER_OUTCOME_UNCERTAIN"
                                    )
                                    return SandboxDispatchResult(
                                        status="SUBMISSION_UNCERTAIN",
                                        intent_id=lease.intent.intent_id,
                                        order_was_sent=True,
                                        order_may_have_been_sent=True,
                                        market=market,
                                        error="PROVIDER_OUTCOME_UNCERTAIN",
                                    )
                                if not isinstance(response, Mapping):
                                    lease.mark_uncertain(
                                        reason="CL7_PROVIDER_CORRELATION_INVALID"
                                    )
                                    return SandboxDispatchResult(
                                        status="SUBMISSION_UNCERTAIN",
                                        intent_id=lease.intent.intent_id,
                                        order_was_sent=True,
                                        order_may_have_been_sent=True,
                                        market=market,
                                        error="PROVIDER_OUTCOME_UNCERTAIN",
                                    )
                                request_id = response.get("orderRequestId")
                                broker_order_id = response.get("orderId")
                                if (
                                    type(request_id) is not str
                                    or request_id != lease.intent.intent_id
                                    or type(broker_order_id) is not str
                                    or not broker_order_id.strip()
                                    or len(broker_order_id) > 128
                                ):
                                    lease.mark_uncertain(
                                        reason="CL7_PROVIDER_CORRELATION_INVALID"
                                    )
                                    return SandboxDispatchResult(
                                        status="SUBMISSION_UNCERTAIN",
                                        intent_id=lease.intent.intent_id,
                                        order_was_sent=True,
                                        order_may_have_been_sent=True,
                                        market=market,
                                        error="PROVIDER_OUTCOME_UNCERTAIN",
                                    )
                                broker_order_id = broker_order_id.strip()
                                try:
                                    lease.mark_submitted(
                                        broker_order_id=broker_order_id
                                    )
                                except CentralOrderError:
                                    return SandboxDispatchResult(
                                        status="STATE_COMMIT_UNCERTAIN",
                                        intent_id=lease.intent.intent_id,
                                        order_was_sent=True,
                                        order_may_have_been_sent=True,
                                        market=market,
                                        error="PROVIDER_OUTCOME_UNCERTAIN",
                                    )
                                return SandboxDispatchResult(
                                    status="SUBMITTED",
                                    intent_id=lease.intent.intent_id,
                                    order_was_sent=True,
                                    market=market,
                                    broker_order_id=broker_order_id,
                                    provider_status=normalize_execution_status(response),
                                    executed_lots=executed_lots(response),
                                    terminal=is_terminal_order_status(
                                        normalize_execution_status(response),
                                        lease.intent.candidate.time_in_force,
                                    ),
                                )
        except CL7RuntimeError as exc:
            return SandboxDispatchResult(
                status="CL7_" + exc.reason.value,
                intent_id=intent.intent_id,
                retryable=exc.retryable,
                market=market,
                error=exc.reason.value,
            )
        except (PortfolioRepositoryError, RiskDispatchAuthorizationError) as exc:
            return SandboxDispatchResult(
                status="CL7_LOCKED_REVALIDATION_BLOCKED",
                intent_id=intent.intent_id,
                retryable=True,
                market=market,
                error=type(exc).__name__.upper(),
            )
        except Exception:  # noqa: BLE001 - finite dispatch boundary
            return SandboxDispatchResult(
                status="CL7_INTERNAL_BOUNDARY_FAILED",
                intent_id=intent.intent_id,
                market=market,
                error="INTERNAL_BOUNDARY_FAILED",
            )

    def _dispatch_legacy_authorized(
        self,
        intent: CentralOrderIntent,
        portfolio_repository: PortfolioRepository,
    ) -> SandboxDispatchResult:
        if self.risk_runtime is None:
            return SandboxDispatchResult(
                status="RISK_AUTHORIZATION_UNAVAILABLE",
                intent_id=intent.intent_id,
                retryable=True,
                error="Dispatch-time Risk authorization gate is unavailable.",
            )
        if (
            intent.authorization.portfolio_risk is not None
            and self.portfolio_risk_runtime is None
        ):
            return SandboxDispatchResult(
                status="PORTFOLIO_RISK_AUTHORIZATION_UNAVAILABLE",
                intent_id=intent.intent_id,
                retryable=True,
                error="Dispatch-time Portfolio Risk authorization gate is unavailable.",
            )
        if self.portfolio_risk_runtime is not None:
            try:
                with (
                    portfolio_repository.locked_snapshot(
                        expected_account_id=self.policy.account_id
                    ) as locked_portfolio,
                    self.risk_runtime.dispatch_authorization_guard(
                        expected_policy_hash=intent.authorization.risk_policy_hash,
                        expected_state_guard_hash=(
                            intent.authorization.risk_state_guard_hash
                        ),
                        instrument_id=intent.candidate.instrument_id,
                    ),
                ):
                    return self._dispatch_with_current_risk(
                        intent,
                        portfolio_repository,
                        locked_portfolio_state=locked_portfolio,
                    )
            except PortfolioRiskAuthorizationError as exc:
                return SandboxDispatchResult(
                    status=exc.status,
                    intent_id=intent.intent_id,
                    retryable=exc.retryable,
                    error=str(exc),
                )
            except PortfolioRepositoryError as exc:
                return SandboxDispatchResult(
                    status="CANONICAL_AUTHORIZATION_UNAVAILABLE",
                    intent_id=intent.intent_id,
                    retryable=True,
                    error=str(exc),
                )
            except RiskDispatchAuthorizationError as exc:
                return SandboxDispatchResult(
                    status=exc.status,
                    intent_id=intent.intent_id,
                    retryable=exc.retryable,
                    error=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 - all M4 gates fail closed
                return SandboxDispatchResult(
                    status="PORTFOLIO_RISK_AUTHORIZATION_UNAVAILABLE",
                    intent_id=intent.intent_id,
                    retryable=True,
                    error=f"Dispatch-time Portfolio Risk authorization failed: {exc}",
                )
        try:
            with self.risk_runtime.dispatch_authorization_guard(
                expected_policy_hash=intent.authorization.risk_policy_hash,
                expected_state_guard_hash=(intent.authorization.risk_state_guard_hash),
                instrument_id=intent.candidate.instrument_id,
            ):
                return self._dispatch_with_current_risk(
                    intent,
                    portfolio_repository,
                )
        except RiskDispatchAuthorizationError as exc:
            return SandboxDispatchResult(
                status=exc.status,
                intent_id=intent.intent_id,
                retryable=exc.retryable,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - Risk gate is fail-closed
            return SandboxDispatchResult(
                status="RISK_AUTHORIZATION_UNAVAILABLE",
                intent_id=intent.intent_id,
                retryable=True,
                error=f"Dispatch-time Risk authorization failed: {exc}",
            )

    def _dispatch_with_current_risk(
        self,
        intent: CentralOrderIntent,
        portfolio_repository: PortfolioRepository,
        *,
        locked_portfolio_state: Any | None = None,
    ) -> SandboxDispatchResult:
        """Submit only while the persisted Risk authorization guard is held."""

        market_result = self._market_precheck(intent)
        if isinstance(market_result, SandboxDispatchResult):
            return market_result
        market = market_result

        try:
            preparation = self.manager.prepare_next(
                portfolio_repository,
                expected_intent_id=intent.intent_id,
                locked_portfolio_state=locked_portfolio_state,
                portfolio_risk_validator=(
                    (
                        lambda portfolio, central, queued: (
                            self.portfolio_risk_runtime.validate_dispatch(
                                portfolio=portfolio,
                                central_orders=central,
                                intent=queued,
                            )
                        )
                    )
                    if self.portfolio_risk_runtime is not None
                    else None
                ),
            )
        except PortfolioRiskAuthorizationError as exc:
            return SandboxDispatchResult(
                status=exc.status,
                intent_id=intent.intent_id,
                retryable=exc.retryable,
                market=market,
                error=str(exc),
            )
        except CentralOrderConflictError as exc:
            return SandboxDispatchResult(
                status="CANONICAL_PREFLIGHT_BLOCKED",
                intent_id=intent.intent_id,
                retryable=True,
                market=market,
                error=str(exc),
            )
        if preparation is None:
            return SandboxDispatchResult(status="IDLE", market=market)
        prepared = preparation.intent

        try:
            response = self.transport.post_order(
                self.policy.account_id,
                prepared.candidate.instrument_id,
                prepared.candidate.requested_lots,
                prepared.candidate.direction,
                order_id=prepared.intent_id,
                order_type=prepared.candidate.order_type,
                time_in_force=prepared.candidate.time_in_force,
            )
        except TBankAPIError as exc:
            if _ambiguous_api_error(exc):
                return self._submission_uncertain(prepared, exc, market=market)
            self.manager.mark_submission_rejected(
                prepared.intent_id,
                reason=f"Provider explicitly rejected Sandbox order: {exc}",
            )
            return SandboxDispatchResult(
                status="SUBMISSION_REJECTED",
                intent_id=prepared.intent_id,
                order_was_sent=True,
                market=market,
                error=str(exc),
            )
        except ValueError as exc:
            self.manager.mark_pre_submit_failed(
                prepared.intent_id,
                reason=f"Local Sandbox order validation failed: {exc}",
            )
            return SandboxDispatchResult(
                status="LOCAL_VALIDATION_FAILED",
                intent_id=prepared.intent_id,
                market=market,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - unknown post outcome is unsafe
            return self._submission_uncertain(prepared, exc, market=market)

        if not isinstance(response, Mapping):
            return self._submission_uncertain(
                prepared,
                RuntimeError("Sandbox submission returned a non-object response."),
                market=market,
            )
        payload = dict(response)
        request_id = str(payload.get("orderRequestId") or "").strip()
        if request_id and request_id != prepared.intent_id:
            return self._submission_uncertain(
                prepared,
                RuntimeError("Sandbox response request ID does not match intent ID."),
                market=market,
            )
        broker_order_id = str(payload.get("orderId") or request_id or "").strip()
        if not broker_order_id:
            return self._submission_uncertain(
                prepared,
                RuntimeError("Sandbox response contains no order correlation ID."),
                market=market,
            )
        try:
            self.manager.mark_submitted(
                prepared.intent_id,
                broker_order_id=broker_order_id,
            )
        except CentralOrderError as exc:
            return self._submission_uncertain(
                prepared,
                RuntimeError(
                    "Provider accepted the order but local SUBMITTED persistence "
                    f"failed: {exc}"
                ),
                market=market,
            )

        provider_status = normalize_execution_status(payload)
        filled = executed_lots(payload)
        return SandboxDispatchResult(
            status="SUBMITTED",
            intent_id=prepared.intent_id,
            order_was_sent=True,
            market=market,
            broker_order_id=broker_order_id,
            provider_status=provider_status,
            executed_lots=filled,
            terminal=is_terminal_order_status(
                provider_status,
                prepared.candidate.time_in_force,
            ),
        )

    def inspect_blocking_order(self) -> SandboxInspectionResult:
        blocker = self.manager.state().blocking_intent
        if blocker is None:
            return SandboxInspectionResult(status="IDLE")
        try:
            response = self.transport.get_order_state(
                self.policy.account_id,
                blocker.intent_id,
                by_request_id=True,
            )
        except TBankAPIError as exc:
            if exc.status_code == 404 and not exc.transient:
                return SandboxInspectionResult(
                    status="NOT_FOUND_UNCERTAIN",
                    intent_id=blocker.intent_id,
                    retryable=True,
                    error=str(exc),
                )
            return SandboxInspectionResult(
                status="INSPECTION_UNAVAILABLE",
                intent_id=blocker.intent_id,
                retryable=True,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary is fail-closed
            return SandboxInspectionResult(
                status="INSPECTION_UNAVAILABLE",
                intent_id=blocker.intent_id,
                retryable=True,
                error=str(exc),
            )
        if not isinstance(response, Mapping):
            return SandboxInspectionResult(
                status="INSPECTION_UNCERTAIN",
                intent_id=blocker.intent_id,
                retryable=True,
                error="Sandbox order inspection returned a non-object response.",
            )
        payload = dict(response)
        broker_order_id = payload.get("orderId")
        if (
            type(broker_order_id) is not str
            or not broker_order_id.strip()
            or len(broker_order_id.strip()) > 128
        ):
            return SandboxInspectionResult(
                status="INSPECTION_UNCERTAIN",
                intent_id=blocker.intent_id,
                retryable=True,
                error="Sandbox order inspection has no bounded broker order ID.",
            )
        provider_status = normalize_execution_status(payload)
        filled = executed_lots(payload)
        price, price_source = executed_order_price(payload)
        terminal = is_terminal_order_status(
            provider_status,
            blocker.candidate.time_in_force,
        )
        return SandboxInspectionResult(
            status="ORDER_OBSERVED",
            intent_id=blocker.intent_id,
            broker_order_id=broker_order_id.strip(),
            provider_status=provider_status,
            executed_lots=filled,
            terminal=terminal,
            suggested_reconciliation_outcome=(
                _reconciliation_outcome(provider_status) if terminal else None
            ),
            execution_price_rub=price,
            execution_price_source=price_source,
        )

    def _market_precheck(
        self,
        intent: CentralOrderIntent,
    ) -> MarketAvailability | SandboxDispatchResult:
        try:
            raw_status = self.transport.get_trading_status(
                intent.candidate.instrument_id
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary is fail-closed
            return SandboxDispatchResult(
                status="MARKET_STATUS_UNAVAILABLE",
                intent_id=intent.intent_id,
                retryable=True,
                error=str(exc),
            )
        if not isinstance(raw_status, Mapping):
            return SandboxDispatchResult(
                status="MARKET_STATUS_UNCERTAIN",
                intent_id=intent.intent_id,
                retryable=True,
                error="Sandbox market status is not an object.",
            )
        market = classify_market_status(
            raw_status,
            order_type=intent.candidate.order_type,
        )
        if market.executable is False:
            return SandboxDispatchResult(
                status="MARKET_IDLE",
                intent_id=intent.intent_id,
                retryable=True,
                market=market,
            )
        if market.executable is not True:
            return SandboxDispatchResult(
                status="MARKET_STATUS_UNCERTAIN",
                intent_id=intent.intent_id,
                retryable=True,
                market=market,
            )
        return market

    def _submission_uncertain(
        self,
        intent: CentralOrderIntent,
        exc: BaseException,
        *,
        market: MarketAvailability,
    ) -> SandboxDispatchResult:
        error = str(exc)
        try:
            self.manager.mark_uncertain(
                intent.intent_id,
                reason=(
                    "Sandbox submission outcome is ambiguous; automatic "
                    f"resubmit is forbidden. {error}"
                ),
            )
            status = "SUBMISSION_UNCERTAIN"
        except CentralOrderError as transition_exc:
            status = "STATE_COMMIT_UNCERTAIN"
            error = f"{error}; local uncertainty persistence failed: {transition_exc}"
        return SandboxDispatchResult(
            status=status,
            intent_id=intent.intent_id,
            order_was_sent=True,
            order_may_have_been_sent=True,
            retryable=False,
            market=market,
            error=error,
        )


def _ambiguous_api_error(exc: TBankAPIError) -> bool:
    status = exc.status_code
    return bool(
        exc.transient
        or status is None
        or status < 400
        or status >= 500
        or status in _AMBIGUOUS_HTTP_STATUSES
    )


def _exact_provider_rejection(exc: TBankAPIError) -> bool:
    status = exc.status_code
    return bool(
        exc.service == "SandboxService"
        and exc.method == "PostSandboxOrder"
        and type(status) is int
        and 400 <= status <= 499
        and status not in _AMBIGUOUS_HTTP_STATUSES
        and exc.transient is False
        and getattr(exc, "direct_response", False) is True
        and getattr(exc, "redirect_followed", False) is False
    )


def _cl7_timestamp_ns(value: str) -> int:
    if not isinstance(value, str) or len(value) != 30 or not value.endswith("Z"):
        raise CL7RuntimeError(CL7RuntimeReason.TIMESTAMP_INVALID)
    try:
        base = datetime.fromisoformat(value[:19] + "+00:00")
        fraction = int(value[20:29])
    except (TypeError, ValueError):
        raise CL7RuntimeError(CL7RuntimeReason.TIMESTAMP_INVALID) from None
    return int(base.timestamp()) * 1_000_000_000 + fraction


def _cl7_iso_timestamp(value: str) -> str:
    """Convert canonical CL1 nanoseconds to predecessor Portfolio ISO UTC."""

    _cl7_timestamp_ns(value)
    return value[:26] + "+00:00"


def _cl7_reserved_cash(intent: CentralOrderIntent):
    from .cash_ledger_domain import Money

    try:
        value = intent.reserved_cash_kopecks * 10_000_000
        return Money(currency="RUB", minor_units=value)
    except Exception:  # noqa: BLE001 - finite CL7 reason mapping
        raise CL7RuntimeError(CL7RuntimeReason.CENTRAL_CHANGED) from None


def _require_cl7_proof_fresh(proof: LockedDispatchProof, now: str) -> None:
    evaluated = _cl7_timestamp_ns(proof.evaluated_at)
    current = _cl7_timestamp_ns(now)
    if evaluated > current or current - evaluated > 10_000_000_000:
        raise CL7RuntimeError(CL7RuntimeReason.CONTEXT_STALE)


def _reconciliation_outcome(provider_status: str | None) -> str | None:
    return {
        "FILL": "FILLED",
        "PARTIALLYFILL": "PARTIALLY_FILLED",
        "REJECTED": "REJECTED",
        "CANCELLED": "CANCELLED",
    }.get(provider_status)
