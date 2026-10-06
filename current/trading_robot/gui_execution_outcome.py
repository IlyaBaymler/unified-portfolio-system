"""Sanitized GUI observation of two distinct owners: Central and execution.

No result here grants authority or retries an order.  'order_was_sent' is the
adapter's report of a submission attempt, not confirmation of a trade/fill.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from dataclasses import asdict, dataclass, replace
from hashlib import sha256

from .runtime_cash_authority import CL7RuntimeReason
from .sandbox_execution_adapter import SandboxDispatchResult

COORDINATION_STATUSES = frozenset({
    "QUEUED", "INTENT_QUEUED", "AUTHORIZED", "REAUTHORIZED", "REPLACED",
    "ACCOUNT_BLOCKED", "CANONICAL_UNAVAILABLE", "PREFLIGHT_BLOCKED", "RISK_BLOCKED",
    "NO_POSITION_CHANGE", "CANCELLED_NO_POSITION_CHANGE", "AUTHORIZATION_BLOCKED",
    "CANONICAL_CHANGED", "ALREADY_PROCESSED", "PORTFOLIO_RISK_ADMISSION_UNAVAILABLE",
    "PORTFOLIO_RISK_PRICE_UNAVAILABLE", "PORTFOLIO_RISK_BLOCKED",
    "PORTFOLIO_RISK_NO_POSITION_CHANGE", "PORTFOLIO_RISK_CURRENCY_UNKNOWN",
    "PORTFOLIO_RISK_ACCOUNT_MISMATCH", "PORTFOLIO_RISK_METADATA_MISMATCH",
    "PORTFOLIO_RISK_NOT_ENFORCED", "PORTFOLIO_RISK_POLICY_CHANGED",
    "PORTFOLIO_RISK_STATE_CHANGED", "PORTFOLIO_RISK_CANONICAL_CHANGED",
    "PORTFOLIO_RISK_RESERVATION_CHANGED", "PORTFOLIO_RISK_QUEUE_CHANGED",
    "PORTFOLIO_RISK_TIMESTAMP_INVALID", "PORTFOLIO_RISK_REAUTHORIZATION_REQUIRED",
    "PORTFOLIO_RISK_PROOF_MISMATCH", "UNKNOWN",
})
EXECUTION_STATUSES = frozenset({
    "NOT_DISPATCHED", "METADATA_BINDING_BLOCKED", "DISPATCH_EXCEPTION",
    "DISPATCH_RESULT_INVALID", "EXECUTION_AUDIT_UNAVAILABLE", "IDLE",
    "CL7_RECOVERY_BLOCKED", "CL7_DISPATCH_PENDING", "CL7_CONTEXT_UNAVAILABLE",
    "CL7_TRANSPORT_UNAVAILABLE", "CL7_LOCKED_REVALIDATION_BLOCKED",
    "ACCOUNT_BLOCKED", "OPERATOR_INTENT_MISMATCH", "OPERATOR_INTENT_REQUIRED",
    "DISARMED", "SUBMITTED", "SUBMISSION_REJECTED", "SUBMISSION_UNCERTAIN",
    "STATE_COMMIT_UNCERTAIN", "RISK_AUTHORIZATION_UNAVAILABLE",
    "PORTFOLIO_RISK_AUTHORIZATION_UNAVAILABLE", "CANONICAL_AUTHORIZATION_UNAVAILABLE",
    "CANONICAL_PREFLIGHT_BLOCKED", "LOCAL_VALIDATION_FAILED",
    "MARKET_STATUS_UNAVAILABLE", "MARKET_STATUS_UNCERTAIN", "MARKET_IDLE",
    "RISK_POLICY_CHANGED", "RISK_STATE_CHANGED", "RISK_ACCOUNT_MISMATCH",
    "RISK_INSTRUMENT_KILL_SWITCH_ACTIVE", "RISK_MODE_MISMATCH",
    "RISK_REAUTHORIZATION_REQUIRED", "RISK_RESYNC_REQUIRED",
    "PORTFOLIO_RISK_ACCOUNT_MISMATCH", "PORTFOLIO_RISK_NO_POSITION_CHANGE",
    "RISK_PROFILE_UNAVAILABLE", "RISK_POLICY_UNAVAILABLE", "RISK_KILL_SWITCH_ACTIVE",
    "PORTFOLIO_RISK_NOT_ENFORCED", "PORTFOLIO_RISK_POLICY_CHANGED",
    "PORTFOLIO_RISK_STATE_CHANGED", "PORTFOLIO_RISK_CANONICAL_CHANGED",
    "PORTFOLIO_RISK_RESERVATION_CHANGED", "PORTFOLIO_RISK_QUEUE_CHANGED",
    "PORTFOLIO_RISK_TIMESTAMP_INVALID", "PORTFOLIO_RISK_REAUTHORIZATION_REQUIRED",
    "PORTFOLIO_RISK_PROOF_MISMATCH", "PORTFOLIO_RISK_METADATA_MISMATCH",
    "PORTFOLIO_RISK_CURRENCY_UNKNOWN", "PORTFOLIO_RISK_BLOCKED",
} | {"CL7_" + reason.value for reason in CL7RuntimeReason})


def intent_digest(account_scope: str, intent_id: object) -> str | None:
    if (type(intent_id) is not str or not intent_id
            or intent_id != intent_id.strip() or len(intent_id) > 128):
        return None
    return sha256(("GUI_INTENT_OBSERVATION_V1\0" + account_scope + "\0" + intent_id).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class GuiCycleOutcome:
    coordination_status: str
    execution_status: str
    action: str
    intent_sha256: str | None
    dispatch_invoked: bool = False
    intent_binding: str = "NOT_ATTEMPTED"
    order_was_sent: bool | None = False
    order_may_have_been_sent: bool | None = False
    recovery_required: bool = False
    audit_persisted: bool = False
    automatic_retry: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def observe_gui_cycle(
    coordination: object,
    *,
    proposed_target: object,
    account_scope: str,
    dispatch_invoked: bool = False,
    dispatch_result: object = None,
    exception: bool = False,
    metadata_blocked: bool = False,
) -> GuiCycleOutcome:
    raw_status = getattr(coordination, "status", None)
    status = raw_status if type(raw_status) is str and raw_status in COORDINATION_STATUSES else "UNKNOWN"
    current = getattr(coordination, "current_lots", None)
    action = "UNDETERMINED"
    if type(current) is int and type(proposed_target) is int:
        action = "BUY" if proposed_target > current else "SELL" if proposed_target < current else "HOLD"
    expected_id = getattr(coordination, "intent_id", None)
    digest = intent_digest(account_scope, expected_id)
    base = GuiCycleOutcome(status, "NOT_DISPATCHED", action, digest,
                           recovery_required=status == "ACCOUNT_BLOCKED")
    if metadata_blocked:
        return replace(base, execution_status="METADATA_BINDING_BLOCKED")
    if not dispatch_invoked:
        return base
    unobserved = replace(
        base, execution_status="DISPATCH_EXCEPTION" if exception else "DISPATCH_RESULT_INVALID",
        dispatch_invoked=True, intent_binding="UNVERIFIED", order_was_sent=None,
        order_may_have_been_sent=True, recovery_required=True,
    )
    if exception or type(dispatch_result) is not SandboxDispatchResult:
        return unobserved
    result = dispatch_result
    if (type(result.status) is not str or result.status not in EXECUTION_STATUSES
            or result.status in {"NOT_DISPATCHED", "METADATA_BINDING_BLOCKED", "DISPATCH_EXCEPTION",
                                 "DISPATCH_RESULT_INVALID", "EXECUTION_AUDIT_UNAVAILABLE"}
            or type(result.order_was_sent) is not bool
            or type(result.order_may_have_been_sent) is not bool
            or type(result.retryable) is not bool
            or type(result.terminal) is not bool
            or type(result.executed_lots) is not int or result.executed_lots < 0):
        return unobserved
    binding = "MATCHED" if digest and result.intent_id == expected_id else (
        "NOT_REPORTED" if result.intent_id is None else "OTHER_INTENT"
    )
    possible = result.order_was_sent or result.order_may_have_been_sent
    if (possible and binding != "MATCHED") or (
        result.status in {"SUBMITTED", "SUBMISSION_REJECTED"}
        and (not result.order_was_sent or result.order_may_have_been_sent)
    ) or (result.status in {"SUBMISSION_UNCERTAIN", "STATE_COMMIT_UNCERTAIN"}
          and not result.order_may_have_been_sent):
        return unobserved
    if possible and result.status not in {
        "SUBMITTED", "SUBMISSION_REJECTED", "SUBMISSION_UNCERTAIN", "STATE_COMMIT_UNCERTAIN"
    }:
        return unobserved
    recovery = base.recovery_required or result.order_may_have_been_sent or result.status in {
        "ACCOUNT_BLOCKED",
        "CL7_RECOVERY_BLOCKED", "CL7_DISPATCH_PENDING", "CL7_RECOVERY_REQUIRED",
    }
    return replace(
        base, execution_status=result.status, dispatch_invoked=True,
        intent_binding=binding, order_was_sent=result.order_was_sent,
        order_may_have_been_sent=result.order_may_have_been_sent, recovery_required=recovery,
    )


def valid_gui_execution_payload(payload: object) -> bool:
    """Validate only the explicit read model, never arbitrary journal payloads."""
    if not isinstance(payload, Mapping):
        return False
    required = set(GuiCycleOutcome.__dataclass_fields__) | {"account_scope_sha256"}
    if set(payload) != required:
        return False
    coord, status = payload["coordination_status"], payload["execution_status"]
    binding, digest = payload["intent_binding"], payload["intent_sha256"]
    if (type(coord) is not str or coord not in COORDINATION_STATUSES
            or type(status) is not str or status not in EXECUTION_STATUSES
            or type(binding) is not str or binding not in {
                "NOT_ATTEMPTED", "MATCHED", "NOT_REPORTED", "OTHER_INTENT", "UNVERIFIED"}
            or (digest is not None and (type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None))
            or payload["automatic_retry"] is not False
            or payload["audit_persisted"] is not True
            or type(payload["dispatch_invoked"]) is not bool
            or type(payload["recovery_required"]) is not bool
            or type(payload["action"]) is not str
            or payload["action"] not in {"BUY", "HOLD", "SELL", "UNDETERMINED"}):
        return False
    sent, possible = payload["order_was_sent"], payload["order_may_have_been_sent"]
    if (sent is not None and type(sent) is not bool
            or possible is not None and type(possible) is not bool):
        return False
    if not payload["dispatch_invoked"]:
        return (status in {"NOT_DISPATCHED", "METADATA_BINDING_BLOCKED"}
                and binding == "NOT_ATTEMPTED" and sent is False and possible is False)
    if status in {"NOT_DISPATCHED", "METADATA_BINDING_BLOCKED"}:
        return False
    if status in {"DISPATCH_EXCEPTION", "DISPATCH_RESULT_INVALID"}:
        return (sent is None and possible is True and binding == "UNVERIFIED"
                and payload["recovery_required"] is True)
    if sent is None or possible is None:
        return False
    if binding == "MATCHED" and digest is None:
        return False
    if (sent or possible) and (binding != "MATCHED" or status not in {
            "SUBMITTED", "SUBMISSION_REJECTED", "SUBMISSION_UNCERTAIN", "STATE_COMMIT_UNCERTAIN"}):
        return False
    if status in {"SUBMITTED", "SUBMISSION_REJECTED"}:
        return sent is True and possible is False
    if status in {"SUBMISSION_UNCERTAIN", "STATE_COMMIT_UNCERTAIN"}:
        return possible is True and payload["recovery_required"] is True
    return not sent and not possible
