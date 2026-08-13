from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .central_order_manager import (
    CentralOrderConflictError,
    CentralOrderError,
    CentralOrderIntent,
    CentralOrderManager,
)
from .market_idle import MarketAvailability, classify_market_status
from .orders import (
    executed_lots,
    executed_order_price,
    is_terminal_order_status,
    normalize_execution_status,
)
from .portfolio_repository import PortfolioRepository
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

    def get_order_state(
        self,
        account_id: str,
        order_id: str,
        *,
        by_request_id: bool = True,
    ) -> dict[str, Any]: ...


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
    ) -> None:
        if policy.account_id != manager.account_id:
            raise ValueError("Sandbox execution policy account scope mismatch.")
        self.transport = transport
        self.manager = manager
        self.policy = policy

    def dispatch_next(
        self,
        portfolio_repository: PortfolioRepository,
        *,
        expected_intent_id: str | None = None,
    ) -> SandboxDispatchResult:
        state = self.manager.state()
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
        if not self.policy.armed:
            return SandboxDispatchResult(
                status="DISARMED",
                intent_id=intent.intent_id,
            )

        market_result = self._market_precheck(intent)
        if isinstance(market_result, SandboxDispatchResult):
            return market_result
        market = market_result

        try:
            preparation = self.manager.prepare_next(
                portfolio_repository,
                expected_intent_id=intent.intent_id,
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
        broker_order_id = str(
            payload.get("orderId") or request_id or ""
        ).strip()
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


def _reconciliation_outcome(provider_status: str | None) -> str | None:
    return {
        "FILL": "FILLED",
        "PARTIALLYFILL": "PARTIALLY_FILLED",
        "REJECTED": "REJECTED",
        "CANCELLED": "CANCELLED",
    }.get(provider_status)
