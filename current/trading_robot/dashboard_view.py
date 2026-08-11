from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class KillSwitchBanner:
    """Presentation-only state for the Risk Dashboard kill-switch banner."""

    state: str
    title: str
    detail: str
    background: str
    foreground: str


def full_account_id(value: Any) -> str:
    """Return the unabridged identifier used by GUI, exports and reports."""

    return str(value or "").strip()


def _latest_control_event(
    recent_events: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for row in recent_events:
        if str(row.get("event_type") or "") in {
            "KILL_SWITCH_ENGAGED",
            "KILL_SWITCH_CLEARED",
        }:
            return row
    return None


def build_kill_switch_banner(
    summary: Mapping[str, Any] | None,
    recent_events: Iterable[Mapping[str, Any]] = (),
) -> KillSwitchBanner:
    """Build a persistent, explicit ON/OFF/UNKNOWN dashboard status."""

    if summary is None:
        return KillSwitchBanner(
            state="UNKNOWN",
            title="KILL SWITCH: UNKNOWN",
            detail="RiskState ещё не загружен или не прошёл проверку.",
            background="#666666",
            foreground="#ffffff",
        )

    event = _latest_control_event(recent_events)
    raw_payload = event.get("payload") if isinstance(event, Mapping) else None
    payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    active = bool(summary.get("kill_switch_active"))
    reason = str(
        summary.get("kill_switch_reason")
        or payload.get("reason")
        or ("Причина не указана" if active else "—")
    )
    changed_at = str(
        summary.get("kill_switch_set_at")
        or payload.get("changed_at")
        or (event or {}).get("timestamp_utc")
        or "—"
    )
    actor = str(summary.get("kill_switch_actor") or payload.get("actor") or "—")
    source = str(summary.get("kill_switch_source") or payload.get("source") or "—")

    if active:
        title = "KILL SWITCH: ON — НОВЫЕ ВХОДЫ ЗАБЛОКИРОВАНЫ"
        effect = (
            "Новые BUY и увеличение позиции заблокированы. Reduce-only: "
            + (
                "разрешён при однозначной reconciled-позиции."
                if summary.get("kill_switch_reduce_only_allowed")
                else "заблокирован."
            )
        )
        background = "#8b1a1a"
    else:
        title = "KILL SWITCH: OFF"
        effect = (
            "Kill switch не блокирует операции; продолжают действовать остальные "
            "лимиты и fail-closed проверки."
        )
        background = "#1f6b3a"

    return KillSwitchBanner(
        state="ON" if active else "OFF",
        title=title,
        detail=(
            f"Причина: {reason} | Время: {changed_at} | "
            f"Actor: {actor} | Источник: {source}\n{effect}"
        ),
        background=background,
        foreground="#ffffff",
    )


__all__ = ["KillSwitchBanner", "build_kill_switch_banner", "full_account_id"]
