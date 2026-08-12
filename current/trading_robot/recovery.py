from __future__ import annotations

"""Startup recovery planning for interrupted Sandbox order lifecycles.

The coordinator is intentionally pure: it does not call the broker and does not
mutate files.  It converts a persisted pending-order snapshot into one explicit,
auditable next action.  The trading bot then executes only that action.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .orders import OrderLifecycle


class RecoveryAction(StrEnum):
    NONE = "NONE"
    CANCEL_INTENT_AND_REEVALUATE = "CANCEL_INTENT_AND_REEVALUATE"
    LOOKUP_BROKER_ORDER = "LOOKUP_BROKER_ORDER"
    WAIT_FOR_TERMINAL_STATUS = "WAIT_FOR_TERMINAL_STATUS"
    RECONCILE_ONLY = "RECONCILE_ONLY"
    RISK_ACCOUNT_ONLY = "RISK_ACCOUNT_ONLY"
    FINALIZE_ACCOUNTED = "FINALIZE_ACCOUNTED"
    FINALIZE_FAILED = "FINALIZE_FAILED"
    BLOCK_MANUAL_REVIEW = "BLOCK_MANUAL_REVIEW"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    recovery_id: str
    assessed_at: str
    order_id: str | None
    lifecycle_state: str | None
    action: RecoveryAction
    safe_to_continue: bool
    broker_lookup_required: bool
    broker_resubmit_allowed: bool
    reason: str
    expected_missing_phase: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["action"] = str(self.action)
        return payload


class StartupRecoveryCoordinator:
    """Choose the only safe continuation for a persisted pending operation."""

    _PRE_SUBMISSION = {
        str(OrderLifecycle.DECISION_CREATED),
        str(OrderLifecycle.PRECHECK_PASSED),
        str(OrderLifecycle.INTENT_SAVED),
    }
    _LOOKUP_REQUIRED = {
        str(OrderLifecycle.ORDER_SUBMITTED),
        str(OrderLifecycle.ORDER_ACCEPTED),
        str(OrderLifecycle.NEW),
        str(OrderLifecycle.PARTIALLY_FILLED),
    }
    _RECONCILE_ONLY = {
        str(OrderLifecycle.FILLED),
        str(OrderLifecycle.RECONCILIATION_REQUIRED),
    }
    _RISK_ONLY = {
        str(OrderLifecycle.PORTFOLIO_RECONCILED),
        str(OrderLifecycle.RISK_ACCOUNTING_REQUIRED),
    }
    _FAILED = {
        str(OrderLifecycle.SUBMISSION_FAILED),
        str(OrderLifecycle.REJECTED),
        str(OrderLifecycle.CANCELLED),
    }

    def assess(
        self,
        pending: Mapping[str, Any] | None,
        *,
        allow_execution: bool,
    ) -> RecoveryDecision:
        assessed_at = datetime.now(timezone.utc).isoformat()
        recovery_id = str(uuid4())
        if not pending:
            return RecoveryDecision(
                recovery_id=recovery_id,
                assessed_at=assessed_at,
                order_id=None,
                lifecycle_state=None,
                action=RecoveryAction.NONE,
                safe_to_continue=True,
                broker_lookup_required=False,
                broker_resubmit_allowed=False,
                reason="No persisted pending order exists.",
            )

        order_id = str(pending.get("order_id") or "").strip() or None
        lifecycle = str(pending.get("lifecycle_state") or "").strip() or None
        if order_id is None:
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.BLOCK_MANUAL_REVIEW,
                False,
                False,
                "Pending order has no order_id; broker correlation is impossible.",
            )
        if lifecycle is None:
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.BLOCK_MANUAL_REVIEW,
                False,
                False,
                "Pending order has no lifecycle_state.",
            )

        if lifecycle == str(OrderLifecycle.UNKNOWN_SUBMIT_STATE):
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.BLOCK_MANUAL_REVIEW,
                False,
                False,
                "Broker lookup could not prove whether the submitted request exists. "
                "Automatic replay is forbidden; inspect the broker account and resolve "
                "the uncertain operation explicitly.",
                expected_missing_phase="OPERATOR_RESOLUTION",
            )

        if lifecycle in self._PRE_SUBMISSION:
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.CANCEL_INTENT_AND_REEVALUATE,
                True,
                False,
                "The intent was durably saved but no submission phase was persisted; "
                "discard it and evaluate a fresh completed candle.",
                expected_missing_phase="ORDER_SUBMITTED",
            )

        if lifecycle == str(OrderLifecycle.ORDER_SUBMITTED):
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.LOOKUP_BROKER_ORDER,
                bool(allow_execution),
                True,
                "Submission may have reached the broker. Lookup by orderRequestId is "
                "mandatory; automatic POST replay is forbidden.",
                expected_missing_phase="ORDER_ACCEPTED_OR_TERMINAL",
            )

        if lifecycle in self._LOOKUP_REQUIRED:
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.LOOKUP_BROKER_ORDER,
                bool(allow_execution),
                True,
                "The broker lifecycle is incomplete; refresh the existing order only.",
                expected_missing_phase="TERMINAL_STATUS",
            )

        if lifecycle in self._RECONCILE_ONLY:
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.RECONCILE_ONLY,
                True,
                False,
                "Execution is already persisted; perform portfolio reconciliation only.",
                expected_missing_phase="PORTFOLIO_RECONCILED",
            )

        if lifecycle in self._RISK_ONLY:
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.RISK_ACCOUNT_ONLY,
                True,
                False,
                "Portfolio reconciliation proof exists; perform idempotent Risk accounting only.",
                expected_missing_phase="RISK_ACCOUNTED",
            )

        if lifecycle == str(OrderLifecycle.RISK_ACCOUNTED):
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.FINALIZE_ACCOUNTED,
                True,
                False,
                "All economic phases are complete; clear the persisted pending marker only.",
            )

        if lifecycle in self._FAILED:
            return self._decision(
                recovery_id,
                assessed_at,
                order_id,
                lifecycle,
                RecoveryAction.FINALIZE_FAILED,
                True,
                False,
                "The order reached a terminal non-fill state; finalize without resubmission.",
            )

        return self._decision(
            recovery_id,
            assessed_at,
            order_id,
            lifecycle,
            RecoveryAction.BLOCK_MANUAL_REVIEW,
            False,
            False,
            f"Unsupported lifecycle state {lifecycle!r}; fail-closed manual review is required.",
        )

    @staticmethod
    def _decision(
        recovery_id: str,
        assessed_at: str,
        order_id: str | None,
        lifecycle: str | None,
        action: RecoveryAction,
        safe_to_continue: bool,
        broker_lookup_required: bool,
        reason: str,
        expected_missing_phase: str | None = None,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            recovery_id=recovery_id,
            assessed_at=assessed_at,
            order_id=order_id,
            lifecycle_state=lifecycle,
            action=action,
            safe_to_continue=safe_to_continue,
            broker_lookup_required=broker_lookup_required,
            broker_resubmit_allowed=False,
            reason=reason,
            expected_missing_phase=expected_missing_phase,
        )

# Compatibility/diagnostic API used by the RC crash matrix.  The trading bot
# continues to use StartupRecoveryCoordinator; this facade exposes more
# granular action names for tests, reports and future adapters.
RecoveryAction.REEVALUATE_INTENT = RecoveryAction.CANCEL_INTENT_AND_REEVALUATE  # type: ignore[attr-defined]
RecoveryAction.LOOKUP_SUBMISSION = RecoveryAction.LOOKUP_BROKER_ORDER  # type: ignore[attr-defined]
RecoveryAction.POLL_ORDER_STATE = RecoveryAction.WAIT_FOR_TERMINAL_STATUS  # type: ignore[attr-defined]
RecoveryAction.RECONCILE_POSITION = RecoveryAction.RECONCILE_ONLY  # type: ignore[attr-defined]
RecoveryAction.ACCOUNT_RISK_ONLY = RecoveryAction.RISK_ACCOUNT_ONLY  # type: ignore[attr-defined]
RecoveryAction.FINALIZE_TERMINAL = RecoveryAction.FINALIZE_ACCOUNTED  # type: ignore[attr-defined]
RecoveryAction.BLOCK_AMBIGUOUS = RecoveryAction.BLOCK_MANUAL_REVIEW  # type: ignore[attr-defined]


class RecoveryCoordinator:
    """Granular pure recovery planner used by the RC crash matrix."""

    def plan(self, pending: Mapping[str, Any] | None) -> RecoveryDecision:
        # Start with the production coordinator and refine accepted/order states
        # for audit readability without granting resubmission rights.
        base = StartupRecoveryCoordinator().assess(pending, allow_execution=True)
        lifecycle = str((pending or {}).get("lifecycle_state") or "")
        if not pending:
            return base
        required = ("order_id", "action", "lots", "current_lots_before", "target_lots")
        if any((pending or {}).get(name) is None for name in required):
            return RecoveryDecision(
                **{
                    **base.to_dict(),
                    "action": RecoveryAction.BLOCK_MANUAL_REVIEW,
                    "safe_to_continue": False,
                    "broker_lookup_required": False,
                    "broker_resubmit_allowed": False,
                    "reason": "Pending order is incomplete; manual review is required.",
                }
            )
        if lifecycle == str(OrderLifecycle.UNKNOWN_SUBMIT_STATE):
            return RecoveryDecision(
                **{
                    **base.to_dict(),
                    "action": RecoveryAction.LOOKUP_BROKER_ORDER,
                    "safe_to_continue": True,
                    "broker_lookup_required": True,
                    "broker_resubmit_allowed": False,
                    "reason": "Repeat broker lookup only; a second POST is forbidden.",
                }
            )
        if lifecycle in {
            str(OrderLifecycle.ORDER_ACCEPTED),
            str(OrderLifecycle.NEW),
            str(OrderLifecycle.PARTIALLY_FILLED),
        }:
            return RecoveryDecision(
                **{
                    **base.to_dict(),
                    "action": RecoveryAction.WAIT_FOR_TERMINAL_STATUS,
                    "safe_to_continue": True,
                    "broker_lookup_required": True,
                    "broker_resubmit_allowed": False,
                }
            )
        return base


class InjectedCrash(BaseException):
    pass


class CrashInjector:
    """Persistent one-shot crash injector for manual crash-matrix scripts."""

    def __init__(
        self,
        *,
        phase: str | None = None,
        mode: str = "exception",
        marker_path: str | Path | None = None,
    ) -> None:
        from pathlib import Path as _Path
        self.phase = str(phase or "").strip().upper() or None
        self.mode = str(mode).strip().lower()
        self.marker_path = _Path(marker_path) if marker_path is not None else None

    def maybe_crash(self, phase: str, context: Mapping[str, Any] | None = None) -> None:
        normalized = str(phase).strip().upper()
        if not self.phase or normalized != self.phase:
            return
        if self.marker_path is not None and self.marker_path.exists():
            return
        if self.marker_path is not None:
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            self.marker_path.write_text(
                __import__("json").dumps(
                    {"phase": normalized, "context": dict(context or {})},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        if self.mode == "exit":
            raise SystemExit(86)
        raise InjectedCrash(f"Injected crash after {normalized}")
