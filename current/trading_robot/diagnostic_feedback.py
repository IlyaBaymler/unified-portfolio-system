from __future__ import annotations

"""Operator-facing feedback for explicitly initiated diagnostic orders.

The module is independent from Tkinter so success/warning wording can be unit
-tested without opening a GUI.  It never changes portfolio or risk state.
"""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class DiagnosticFeedback:
    title: str
    message: str
    success: bool
    requires_attention: bool
    reconciliation_status: str


def _matching_position(
    snapshot: Mapping[str, Any] | None,
    *,
    ticker: str,
) -> Mapping[str, Any] | None:
    if not isinstance(snapshot, Mapping):
        return None
    wanted = str(ticker or "").strip().upper()
    for row in snapshot.get("positions") or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("ticker") or "").strip().upper() == wanted:
            return row
    return None


def build_diagnostic_feedback(
    result: Mapping[str, Any],
    *,
    direction: str,
    ticker: str,
) -> DiagnosticFeedback:
    """Build explicit, safe operator feedback for one diagnostic order."""

    action = str(direction or result.get("direction") or "").strip().upper()
    symbol = str(ticker or result.get("ticker") or "").strip().upper() or "—"
    status = str(result.get("status") or "unknown").strip()
    executed = int(result.get("executed_lots") or 0)
    actual_after = result.get("actual_lots_after")
    order_id = str(result.get("order_id") or "—")
    reconciled = bool(result.get("position_reconciled"))
    risk_status = str(
        result.get("risk_execution_status")
        or ((result.get("risk_execution") or {}).get("status") if isinstance(result.get("risk_execution"), Mapping) else "")
        or "—"
    )

    snapshot = result.get("canonical_portfolio_snapshot")
    position = _matching_position(
        snapshot if isinstance(snapshot, Mapping) else None,
        ticker=symbol,
    )
    if position is not None:
        reconciliation_status = str(
            position.get("reconciliation_status") or "UNKNOWN"
        )
    elif isinstance(snapshot, Mapping) and not bool(snapshot.get("blocking")):
        reconciliation_status = "MATCHED"
    elif isinstance(snapshot, Mapping):
        reconciliation_status = str(snapshot.get("state_status") or "BLOCKED")
    else:
        reconciliation_status = "REFRESH_UNAVAILABLE"

    success = status == "processed" and executed > 0 and reconciled
    mismatch = reconciliation_status in {
        "TARGET_MISMATCH",
        "OWNERSHIP_MISSING",
        "UNATTRIBUTED_OPEN_POSITION",
        "EXTERNAL_ACTIVITY_DETECTED",
        "MANUAL_REVIEW_REQUIRED",
        "BLOCKED",
    }
    requires_attention = mismatch or not success

    verb = "Покупка" if action == "BUY" else "Продажа" if action == "SELL" else action
    title = (
        f"Диагностическая {verb.lower()} исполнена"
        if success and not mismatch
        else f"Диагностическая {verb.lower()} исполнена — требуется внимание"
        if success
        else "Диагностическая заявка не завершена"
    )

    lines = [
        f"Операция: {action or '—'} {symbol}",
        f"Исполнено лотов: {executed}",
        f"Order ID: {order_id}",
        f"Фактическая позиция после операции: {actual_after if actual_after is not None else '—'}",
        f"Broker reconciliation: {'PASS' if reconciled else 'NOT CONFIRMED'}",
        f"Portfolio Manager: {reconciliation_status}",
        f"Risk accounting: {risk_status}",
    ]
    if mismatch:
        lines.extend(
            [
                "",
                "Фактическая позиция изменилась вне обычного Strategy Engine.",
                "Strategy target/ownership может оставаться активным, поэтому новые входы блокируются.",
                "Обновите портфель и примените безопасное acknowledgement либо дождитесь новой PRIMARY decision.",
            ]
        )
    refresh_error = result.get("canonical_portfolio_refresh_error")
    if refresh_error:
        lines.extend(
            [
                "",
                "Канонический снимок не удалось обновить сразу; выполните ручное обновление портфеля.",
                f"Класс ошибки: {refresh_error}",
            ]
        )
    return DiagnosticFeedback(
        title=title,
        message="\n".join(lines),
        success=success,
        requires_attention=requires_attention,
        reconciliation_status=reconciliation_status,
    )
