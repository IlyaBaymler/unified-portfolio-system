from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class OrderLifecycle(StrEnum):
    DECISION_CREATED = "DECISION_CREATED"
    PRECHECK_PASSED = "PRECHECK_PASSED"
    INTENT_SAVED = "INTENT_SAVED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    UNKNOWN_SUBMIT_STATE = "UNKNOWN_SUBMIT_STATE"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RISK_ACCOUNTING_REQUIRED = "RISK_ACCOUNTING_REQUIRED"
    RISK_ACCOUNTED = "RISK_ACCOUNTED"
    PORTFOLIO_RECONCILED = "PORTFOLIO_RECONCILED"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_execution_status(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    raw = payload.get("executionReportStatus")
    if raw is None:
        raw = payload.get("execution_report_status")
    if raw is None:
        return None
    status = str(raw).strip().upper()
    prefix = "EXECUTION_REPORT_STATUS_"
    return status[len(prefix) :] if status.startswith(prefix) else status


def extract_int(
    payload: dict[str, Any] | None,
    *keys: str,
    default: int = 0,
) -> int:
    if not payload:
        return default
    for key in keys:
        if key not in payload:
            continue
        try:
            return int(payload[key])
        except (TypeError, ValueError):
            continue
    return default


def executed_lots(payload: dict[str, Any] | None) -> int:
    return max(
        0,
        extract_int(payload, "lotsExecuted", "lots_executed", default=0),
    )




def _quotation_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number > 0 else None
    if not isinstance(value, dict):
        return None
    try:
        units = float(value.get("units", 0) or 0)
        nano = float(value.get("nano", 0) or 0)
        number = units + nano / 1_000_000_000
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def executed_order_price(
    payload: dict[str, Any] | None,
    *,
    fallback: float | None = None,
) -> tuple[float | None, str | None]:
    """Extract the confirmed average execution price from an order payload.

    T-Invest Sandbox responses normally expose `executedOrderPrice`.  Older
    mocks and some failure states may not.  The caller may provide the saved
    decision price as a conservative audit fallback; the source is returned so
    journals can distinguish confirmed and approximate accounting.
    """

    if payload:
        for key in (
            "executedOrderPrice",
            "executed_order_price",
            "averagePositionPrice",
            "average_position_price",
            "initialOrderPrice",
            "initial_order_price",
        ):
            if key not in payload:
                continue
            value = _quotation_to_float(payload.get(key))
            if value is not None:
                return value, key
    fallback_value = _quotation_to_float(fallback)
    if fallback_value is not None:
        return fallback_value, "decision_price_fallback"
    return None, None

def requested_lots(payload: dict[str, Any] | None) -> int:
    return max(
        0,
        extract_int(
            payload,
            "lotsRequested",
            "lots_requested",
            "quantity",
            default=0,
        ),
    )


def is_terminal_order_status(
    status: str | None,
    time_in_force: str,
) -> bool:
    if status in {"FILL", "REJECTED", "CANCELLED"}:
        return True
    return (
        status == "PARTIALLYFILL"
        and time_in_force.upper() in {"FILL_AND_KILL", "FILL_OR_KILL"}
    )


def lifecycle_for_status(status: str | None) -> OrderLifecycle:
    mapping = {
        "NEW": OrderLifecycle.NEW,
        "PARTIALLYFILL": OrderLifecycle.PARTIALLY_FILLED,
        "FILL": OrderLifecycle.FILLED,
        "REJECTED": OrderLifecycle.REJECTED,
        "CANCELLED": OrderLifecycle.CANCELLED,
    }
    return mapping.get(status, OrderLifecycle.ORDER_ACCEPTED)


def signed_lot_delta(direction: str, lots: int) -> int:
    direction = direction.upper()
    if direction == "BUY":
        return int(lots)
    if direction == "SELL":
        return -int(lots)
    raise ValueError("direction must be BUY or SELL")


def transition_intent(
    intent: dict[str, Any],
    state: OrderLifecycle | str,
    *,
    details: dict[str, Any] | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    state_value = str(state)
    timestamp = at or utc_now_iso()
    intent["lifecycle_state"] = state_value
    intent["last_transition_at"] = timestamp
    history = intent.setdefault("history", [])
    entry: dict[str, Any] = {
        "state": state_value,
        "at": timestamp,
    }
    if details:
        entry["details"] = details
        intent.update(details)
    history.append(entry)
    # Avoid unbounded state files if a provider keeps returning the same status.
    if len(history) > 100:
        del history[:-100]
    return intent
