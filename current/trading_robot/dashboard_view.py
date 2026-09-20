from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
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
    account_scope_sha256: str = "UNKNOWN"
    runtime_revision: int | str = "UNKNOWN"
    actual_lots: int | str = "UNKNOWN"
    target_lots: int | str = "UNKNOWN"
    portfolio_revision: int | str = "UNKNOWN"
    reconciliation_status: str = "UNKNOWN"
    ownership_status: str = "UNKNOWN"
    central_revision: int | str = "UNKNOWN"
    queued_reserved_cash: int | str = "UNKNOWN"
    central_blocking_status: str = "UNKNOWN"
    pending_status: str = "UNKNOWN"
    in_flight_status: str = "UNKNOWN"
    submitted_status: str = "UNKNOWN"
    uncertain_status: str = "UNKNOWN"
    risk_policy_hash: str = "UNKNOWN"
    risk_state_revision: int | str = "UNKNOWN"
    risk_readiness: str = "UNKNOWN"
    portfolio_risk_status: str = "UNKNOWN"
    kill_switch_status: str = "UNKNOWN"
    resync_status: str = "UNKNOWN"
    cl7_authority_revision: int | str = "UNKNOWN"
    cl7_authority_mode: str = "UNKNOWN"
    cash_actionability_status: str = "UNKNOWN"
    source_status: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class MultiInstrumentDashboard:
    """Immutable read model; it deliberately exposes no lifecycle commands."""

    mode: ProfileMode
    state: str
    account_id: str
    detail: str
    rows: tuple[InstrumentRuntimeView, ...]


@dataclass(frozen=True, slots=True)
class SandboxDecisionDisplay:
    """One sanitized journal outcome; this is never an order-status owner."""

    action: str
    status: str
    at_utc: str


_DECISION_EVENT_TYPES = frozenset(
    {"PRIMARY_STRATEGY_DECISION", "CENTRAL_COORDINATION_RESULT"}
)
_DECISION_ACTIONS = frozenset(
    {"LONG", "FLAT", "BUY", "HOLD", "SELL", "UNDETERMINED"}
)
_SAFE_DECISION_STATUS = re.compile(r"[A-Z0-9_]{1,80}\Z")


def latest_sandbox_decisions(
    events: Iterable[Mapping[str, Any]],
    *,
    session_id: str,
    account_scope_sha256: str,
    instrument_ids: Iterable[str],
) -> dict[str, SandboxDecisionDisplay]:
    """Project the latest current-session decision without inferring an order.

    EventJournal rows contain private identifiers and payloads.  Only finite
    action/status/time values leave this function, and cross-session/account
    rows cannot become GUI evidence for the configured set.
    """

    configured = {item for item in instrument_ids if type(item) is str}
    latest: dict[str, tuple[int, SandboxDecisionDisplay]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("event_type")
        event_session = event.get("session_id")
        event_mode = event.get("mode")
        if (
            type(event_type) is not str
            or event_type not in _DECISION_EVENT_TYPES
            or type(event_session) is not str
            or event_session != session_id
            or type(event_mode) is not str
            or event_mode != "SANDBOX_EXECUTION"
        ):
            continue
        instrument_id = event.get("instrument_id")
        if type(instrument_id) is not str or instrument_id not in configured:
            continue
        payload = event.get("payload")
        if type(payload) is not dict:
            continue
        scope = payload.get("account_scope_sha256")
        if type(scope) is not str or scope != account_scope_sha256:
            continue
        event_id = event.get("id")
        if type(event_id) is not int or event_id < 1:
            continue
        if instrument_id in latest and event_id <= latest[instrument_id][0]:
            continue

        action = event.get("action")
        status = event.get("status")
        timestamp = event.get("timestamp_utc")
        valid = (
            type(action) is str
            and action in _DECISION_ACTIONS
            and type(status) is str
            and _SAFE_DECISION_STATUS.fullmatch(status) is not None
            and type(timestamp) is str
        )
        at_utc = "—"
        if valid:
            try:
                parsed = datetime.fromisoformat(timestamp)
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    valid = False
                else:
                    at_utc = parsed.astimezone(timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%SZ"
                    )
            except ValueError:
                valid = False
        latest[instrument_id] = (
            event_id,
            SandboxDecisionDisplay(
                action=action if valid else "AUDIT_INVALID",
                status=status if valid else "AUDIT_INVALID",
                at_utc=at_utc if valid else "—",
            ),
        )
    return {instrument_id: value for instrument_id, (_, value) in latest.items()}


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
    portfolio_state: Any | None = None,
    central_state: Any | None = None,
    risk_snapshot: Mapping[str, Any] | None = None,
    portfolio_risk_snapshot: Mapping[str, Any] | None = None,
    authority_record: Any | None = None,
    cash_actionability_status: str | None = None,
    account_scope_sha256: str | None = None,
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
    owner_evidence_requested = any(
        value is not None
        for value in (
            portfolio_state,
            central_state,
            risk_snapshot,
            portfolio_risk_snapshot,
            authority_record,
            cash_actionability_status,
            account_scope_sha256,
        )
    )

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
        owner_fields = _owner_fields(
            profile.instrument_id,
            expected_account_id=runtime.config.account_id,
            portfolio_state=portfolio_state,
            central_state=central_state,
            risk_snapshot=risk_snapshot,
            portfolio_risk_snapshot=portfolio_risk_snapshot,
            authority_record=authority_record,
            cash_actionability_status=cash_actionability_status,
            account_scope_sha256=account_scope_sha256,
        )
        if (
            owner_evidence_requested
            and owner_fields.get("source_status") != "READY"
        ):
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
                runtime_revision=runtime.revision,
                detail="; ".join(
                    item
                    for item in (
                        (
                            ""
                            if not mismatches
                            else "Profile/runtime mismatch: " + ", ".join(mismatches)
                        ),
                        owner_fields.pop("detail", ""),
                    )
                    if item
                ),
                **owner_fields,
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


def _status_text(value: Any, default: str = "UNKNOWN") -> str:
    if value is None:
        return default
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip().upper()
    return normalized or default


def _owner_fields(
    instrument_id: str,
    *,
    expected_account_id: str,
    portfolio_state: Any | None,
    central_state: Any | None,
    risk_snapshot: Mapping[str, Any] | None,
    portfolio_risk_snapshot: Mapping[str, Any] | None,
    authority_record: Any | None,
    cash_actionability_status: str | None,
    account_scope_sha256: str | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "account_scope_sha256": str(account_scope_sha256 or "UNKNOWN"),
        "cash_actionability_status": _status_text(cash_actionability_status),
        "source_status": "READY",
    }
    missing: list[str] = []
    mismatched: list[str] = []
    if not account_scope_sha256:
        missing.append("ACCOUNT_SCOPE")
    if cash_actionability_status is None:
        missing.append("CASH_ACTIONABILITY")

    position = None
    if portfolio_state is not None:
        if str(getattr(portfolio_state, "account_id", expected_account_id)) != (
            expected_account_id
        ):
            mismatched.append("PORTFOLIO_ACCOUNT")
        finder = getattr(portfolio_state, "position", None)
        position = finder(instrument_id) if callable(finder) else None
    if position is None:
        missing.append("PORTFOLIO")
    else:
        reconciliation = getattr(position, "reconciliation", None)
        values.update(
            actual_lots=int(position.actual_lots),
            target_lots=(
                int(position.target_lots)
                if position.target_lots is not None
                else "UNKNOWN"
            ),
            portfolio_revision=int(getattr(portfolio_state, "revision", 0)),
            reconciliation_status=_status_text(
                getattr(reconciliation, "status", None)
            ),
            ownership_status=_status_text(position.ownership_status),
        )

    if central_state is None:
        missing.append("CENTRAL")
    else:
        if str(getattr(central_state, "account_id", expected_account_id)) != (
            expected_account_id
        ):
            mismatched.append("CENTRAL_ACCOUNT")
        intents = tuple(
            item
            for item in central_state.intents
            if str(getattr(getattr(item, "candidate", None), "instrument_id", ""))
            == instrument_id
        )
        statuses = {_status_text(getattr(item, "status", None)) for item in intents}
        queued = tuple(item for item in intents if _status_text(item.status) == "QUEUED")
        values.update(
            central_revision=int(central_state.revision),
            queued_reserved_cash=(
                int(queued[0].reserved_cash_kopecks)
                if len(queued) == 1
                else (0 if not queued else "BLOCKED")
            ),
            central_blocking_status=(
                _status_text(central_state.blocking_intent.status)
                if central_state.blocking_intent is not None
                else "CLEAR"
            ),
            pending_status="PRESENT" if intents else "CLEAR",
            in_flight_status="PRESENT" if "IN_FLIGHT" in statuses else "CLEAR",
            submitted_status="PRESENT" if "SUBMITTED" in statuses else "CLEAR",
            uncertain_status="PRESENT" if "UNCERTAIN" in statuses else "CLEAR",
        )

    risk = risk_snapshot or {}
    if not risk:
        missing.append("RISK")
    else:
        values.update(
            risk_policy_hash=str(risk.get("risk_policy_hash") or "UNKNOWN"),
            risk_state_revision=risk.get("risk_state_revision", "UNKNOWN"),
            risk_readiness=_status_text(risk.get("risk_readiness")),
            kill_switch_status=(
                "ON" if bool(risk.get("kill_switch_active")) else "OFF"
            ),
            resync_status=(
                "REQUIRED" if bool(risk.get("risk_resync_required")) else "CLEAR"
            ),
        )
    portfolio_risk_value = (
        portfolio_risk_snapshot.get(instrument_id)
        if portfolio_risk_snapshot is not None
        else None
    )
    values["portfolio_risk_status"] = _status_text(portfolio_risk_value)
    if portfolio_risk_value is None:
        missing.append("PORTFOLIO_RISK")

    if authority_record is None:
        missing.append("CL7")
    else:
        values.update(
            cl7_authority_revision=int(authority_record.record_revision),
            cl7_authority_mode=_status_text(authority_record.state),
        )
        authority_scope = str(
            getattr(authority_record, "account_scope_sha256", "") or ""
        )
        if authority_scope != str(account_scope_sha256 or ""):
            mismatched.append("CL7_ACCOUNT_SCOPE")
    if mismatched:
        values["source_status"] = "MISMATCH"
        values["detail"] = "Mismatched owner evidence: " + ", ".join(mismatched)
    elif missing:
        values["source_status"] = "UNKNOWN"
        values["detail"] = "Missing owner evidence: " + ", ".join(missing)
    return values


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
