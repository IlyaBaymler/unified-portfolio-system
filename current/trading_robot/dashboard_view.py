from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_persistence import ProfileMode, normalize_profile_mode
from .instrument_runtime import InstrumentRuntime, InstrumentRuntimeStore
from .multi_instrument_config import (
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)


@dataclass(frozen=True, slots=True)
class KillSwitchBanner:
    """Presentation-only state for the Risk Dashboard kill-switch banner."""

    state: str
    title: str
    detail: str
    background: str
    foreground: str


@dataclass(frozen=True, slots=True)
class InstrumentRuntimeView:
    """Presentation-only row for the read-only v3.8 runtime dashboard."""

    instrument_id: str
    ticker: str
    candle_interval: str
    strategy_id: str
    runtime_status: str
    identity_status: str
    current_lots: int
    pending_orders: int
    last_processed_candle: str
    runtime_key: str
    runtime_config_hash: str
    detail: str


@dataclass(frozen=True, slots=True)
class MultiInstrumentDashboard:
    """Immutable read model; it deliberately exposes no lifecycle commands."""

    mode: ProfileMode
    state: str
    account_id: str
    detail: str
    rows: tuple[InstrumentRuntimeView, ...]


def _runtime_profile_mismatches(
    profile: MultiInstrumentProfile,
    runtime: InstrumentRuntime,
) -> tuple[str, ...]:
    config = runtime.config
    expected = {
        "instrument_id": profile.instrument_id,
        "ticker": profile.ticker,
        "class_code": profile.class_code,
        "candle_interval": profile.candle_interval,
        "strategy_id": profile.strategy_id,
        "strategy_config_hash": profile.strategy_profile_hash,
        "configuration_version": profile.configuration_version,
        "decision_cadence_seconds": profile.decision_cadence_seconds,
        "scheduler_cadence_seconds": profile.scheduler_cadence_seconds,
        "risk_refresh_cadence_seconds": profile.risk_refresh_cadence_seconds,
        "reconciliation_cadence_seconds": (
            profile.reconciliation_cadence_seconds
        ),
        "market_status_cadence_seconds": (
            profile.market_status_cadence_seconds
        ),
    }
    return tuple(
        field
        for field, expected_value in expected.items()
        if getattr(config, field) != expected_value
    )


def build_multi_instrument_dashboard(
    profiles: Iterable[MultiInstrumentProfile],
    runtimes: Iterable[InstrumentRuntime],
    *,
    mode: ProfileMode,
) -> MultiInstrumentDashboard:
    """Join stored profiles and runtimes without mutating either collection."""

    normalized_mode = normalize_profile_mode(mode)
    selected_profiles = tuple(profiles)
    selected_runtimes = tuple(runtimes)
    runtime_by_instrument = {
        runtime.config.instrument_id: runtime for runtime in selected_runtimes
    }
    rows: list[InstrumentRuntimeView] = []
    attention = False

    for profile in sorted(
        selected_profiles,
        key=lambda item: (item.ticker, item.instrument_id),
    ):
        runtime = runtime_by_instrument.pop(profile.instrument_id, None)
        if runtime is None:
            attention = True
            rows.append(
                InstrumentRuntimeView(
                    instrument_id=profile.instrument_id,
                    ticker=profile.ticker,
                    candle_interval=profile.candle_interval,
                    strategy_id=profile.strategy_id,
                    runtime_status="NOT_INITIALIZED",
                    identity_status="PROFILE_ONLY",
                    current_lots=0,
                    pending_orders=0,
                    last_processed_candle="",
                    runtime_key="",
                    runtime_config_hash="",
                    detail="Runtime registry entry is missing.",
                )
            )
            continue
        mismatches = _runtime_profile_mismatches(profile, runtime)
        identity_status = "MATCHED" if not mismatches else "MISMATCH"
        if mismatches:
            attention = True
        rows.append(
            InstrumentRuntimeView(
                instrument_id=profile.instrument_id,
                ticker=profile.ticker,
                candle_interval=profile.candle_interval,
                strategy_id=profile.strategy_id,
                runtime_status=runtime.status,
                identity_status=identity_status,
                current_lots=runtime.current_lots,
                pending_orders=len(runtime.pending_order_ids),
                last_processed_candle=(
                    runtime.last_processed_candle.isoformat()
                    if runtime.last_processed_candle
                    else ""
                ),
                runtime_key=runtime.runtime_key,
                runtime_config_hash=runtime.config.runtime_config_hash,
                detail=(
                    ""
                    if not mismatches
                    else "Profile/runtime mismatch: " + ", ".join(mismatches)
                ),
            )
        )

    for runtime in sorted(
        runtime_by_instrument.values(),
        key=lambda item: (item.config.ticker, item.config.instrument_id),
    ):
        attention = True
        rows.append(
            InstrumentRuntimeView(
                instrument_id=runtime.config.instrument_id,
                ticker=runtime.config.ticker,
                candle_interval=runtime.config.candle_interval,
                strategy_id=runtime.config.strategy_id,
                runtime_status=runtime.status,
                identity_status="RUNTIME_ONLY",
                current_lots=runtime.current_lots,
                pending_orders=len(runtime.pending_order_ids),
                last_processed_candle=(
                    runtime.last_processed_candle.isoformat()
                    if runtime.last_processed_candle
                    else ""
                ),
                runtime_key=runtime.runtime_key,
                runtime_config_hash=runtime.config.runtime_config_hash,
                detail=f"No {normalized_mode} profile owns this runtime.",
            )
        )

    account_ids = sorted(
        {runtime.config.account_id for runtime in selected_runtimes}
    )
    account_id = account_ids[0] if len(account_ids) == 1 else ""
    if len(account_ids) > 1:
        attention = True
    if not selected_profiles and not selected_runtimes:
        state = "NOT_CONFIGURED"
        detail = f"No v3.8 profiles or runtimes are configured for {normalized_mode}."
    elif attention:
        state = "ATTENTION"
        detail = "Profile/runtime registry requires operator review."
    else:
        state = "READY"
        detail = f"{len(rows)} runtime(s) match {normalized_mode} profiles."
    return MultiInstrumentDashboard(
        mode=normalized_mode,
        state=state,
        account_id=account_id,
        detail=detail,
        rows=tuple(rows),
    )


def load_multi_instrument_dashboard(
    profile_path: str | Path,
    runtime_path: str | Path,
    *,
    mode: ProfileMode,
) -> MultiInstrumentDashboard:
    """Read the two checksum-managed stores for the desktop dashboard."""

    profiles = MultiInstrumentProfileStore(profile_path).load_mode(mode)
    runtimes = InstrumentRuntimeStore(runtime_path).load_optional()
    return build_multi_instrument_dashboard(profiles, runtimes, mode=mode)


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


__all__ = [
    "InstrumentRuntimeView",
    "KillSwitchBanner",
    "MultiInstrumentDashboard",
    "build_kill_switch_banner",
    "build_multi_instrument_dashboard",
    "full_account_id",
    "load_multi_instrument_dashboard",
]
