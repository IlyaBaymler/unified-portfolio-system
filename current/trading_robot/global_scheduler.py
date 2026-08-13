from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from .instrument_runtime import (
    InstrumentRuntime,
    InstrumentRuntimeConfig,
    InstrumentRuntimeConflictError,
    InstrumentRuntimeStore,
)

logger = logging.getLogger(__name__)


class InstrumentRuntimeHooks(Protocol):
    """Side-effect boundary serviced by ``GlobalScheduler``.

    ``evaluate_closed_candle`` produces a strategy decision/proposal. It must
    not call the broker order endpoint directly. Broker POST remains behind the
    existing canonical preflight, Risk and Execution Engine lifecycle.
    """

    def refresh_market_status(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any: ...

    def refresh_risk(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any: ...

    def reconcile_portfolio(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any: ...

    def evaluate_closed_candle(
        self,
        runtime: InstrumentRuntime,
        candle_time: datetime,
        now: datetime,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class SchedulerAuditEvent:
    """Transport-neutral audit record emitted after persisted transitions."""

    event_type: str
    occurred_at: datetime
    session_id: str
    account_id: str | None
    status: str
    severity: str = "INFO"
    runtime_key: str | None = None
    instrument_id: str | None = None
    ticker: str | None = None
    action: str | None = None
    candle_time: str | None = None
    config_hash: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


class SchedulerEventSink(Protocol):
    """Best-effort audit boundary; sinks never control scheduler progress."""

    def record_scheduler_event(self, event: SchedulerAuditEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class SchedulerActionResult:
    runtime_key: str
    ticker: str
    action: str
    status: str
    detail: str = ""
    candle_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_key": self.runtime_key,
            "ticker": self.ticker,
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
            "candle_time": self.candle_time,
        }


@dataclass(frozen=True, slots=True)
class SchedulerTickResult:
    serviced_at: datetime
    actions: tuple[SchedulerActionResult, ...]

    @property
    def failures(self) -> tuple[SchedulerActionResult, ...]:
        return tuple(item for item in self.actions if item.status == "FAILED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "serviced_at": self.serviced_at.isoformat(),
            "actions": [item.to_dict() for item in self.actions],
            "failure_count": len(self.failures),
        }


class GlobalScheduler:
    """Deterministic sequential scheduler for fixed per-instrument timeframe.

    The scheduler is intentionally execution-agnostic. It separates temporal
    service cadences, prevents one instrument's candle from triggering another
    instrument, and persists a successful candle evaluation before that candle
    can be planned again after restart.
    """

    def __init__(
        self,
        runtimes: Sequence[InstrumentRuntime] = (),
        *,
        store: InstrumentRuntimeStore | None = None,
        event_sink: SchedulerEventSink | None = None,
        session_id: str | None = None,
        startup_event_type: str = "SCHEDULER_STARTED",
    ) -> None:
        self.store = store
        self.event_sink = event_sink
        self.session_id = str(session_id or uuid4())
        self._runtimes: dict[str, InstrumentRuntime] = {}
        for runtime in runtimes:
            self._insert(runtime)
        if startup_event_type not in {
            "SCHEDULER_STARTED",
            "SCHEDULER_RESTORED",
        }:
            raise ValueError("Unsupported scheduler startup event type.")
        self._emit_scheduler_startup(startup_event_type)

    @classmethod
    def restore(
        cls,
        store: InstrumentRuntimeStore,
        *,
        expected_account_id: str | None = None,
        event_sink: SchedulerEventSink | None = None,
        session_id: str | None = None,
    ) -> GlobalScheduler:
        return cls(
            store.load(expected_account_id=expected_account_id),
            store=store,
            event_sink=event_sink,
            session_id=session_id,
            startup_event_type="SCHEDULER_RESTORED",
        )

    @property
    def runtimes(self) -> tuple[InstrumentRuntime, ...]:
        return tuple(
            self._runtimes[key]
            for key in sorted(self._runtimes)
        )

    def get(self, runtime_key: str) -> InstrumentRuntime:
        try:
            return self._runtimes[str(runtime_key)]
        except KeyError as exc:
            raise InstrumentRuntimeConflictError(
                f"Unknown InstrumentRuntime: {runtime_key}"
            ) from exc

    def add_runtime(self, runtime: InstrumentRuntime) -> None:
        self._insert(runtime)
        try:
            self._persist_all()
        except Exception:
            self._runtimes.pop(runtime.runtime_key, None)
            raise
        self._emit_runtime_lifecycle("RUNTIME_ADDED", runtime)

    def start_runtime(self, runtime_key: str) -> InstrumentRuntime:
        runtime = self._commit(self.get(runtime_key).start())
        self._emit_runtime_lifecycle("RUNTIME_STARTED", runtime)
        return runtime

    def stop_runtime(self, runtime_key: str) -> InstrumentRuntime:
        runtime = self._commit(self.get(runtime_key).stop())
        self._emit_runtime_lifecycle("RUNTIME_STOPPED", runtime)
        return runtime

    def update_execution_state(
        self,
        runtime_key: str,
        *,
        current_lots: int,
        pending_order_ids: Sequence[str] = (),
    ) -> InstrumentRuntime:
        runtime = self._commit(
            self.get(runtime_key).with_execution_state(
                current_lots=current_lots,
                pending_order_ids=pending_order_ids,
            )
        )
        self._emit_runtime_lifecycle(
            "RUNTIME_EXECUTION_STATE_UPDATED",
            runtime,
            payload={
                "current_lots": runtime.current_lots,
                "pending_order_count": len(runtime.pending_order_ids),
            },
        )
        return runtime

    def replace_runtime_configuration(
        self,
        runtime_key: str,
        config: InstrumentRuntimeConfig,
    ) -> InstrumentRuntime:
        current = self.get(runtime_key)
        replacement = current.replace_configuration(config)
        if replacement.runtime_key in self._runtimes:
            raise InstrumentRuntimeConflictError(
                f"Runtime identity already exists: {replacement.runtime_key}"
            )
        updated = dict(self._runtimes)
        updated.pop(current.runtime_key)
        self._validate_execution_scope(replacement, updated.values())
        updated[replacement.runtime_key] = replacement
        if self.store is not None:
            self.store.save(updated.values())
        self._runtimes = updated
        self._emit_runtime_lifecycle(
            "RUNTIME_CONFIGURATION_REPLACED",
            replacement,
            payload={"previous_runtime_key": current.runtime_key},
        )
        return replacement

    def tick(
        self,
        *,
        now: datetime,
        latest_closed_candles: Mapping[str, datetime | str | None],
        hooks: InstrumentRuntimeHooks,
    ) -> SchedulerTickResult:
        now = _aware_utc(now, "now")
        actions: list[SchedulerActionResult] = []
        for runtime_key in tuple(sorted(self._runtimes)):
            runtime = self._runtimes[runtime_key]
            if runtime.status != "ACTIVE":
                actions.append(
                    self._result(
                        runtime,
                        "SCHEDULER",
                        "SKIPPED",
                        f"runtime status is {runtime.status}",
                    )
                )
                continue
            if not _is_due(
                runtime.last_scheduler_service_at,
                runtime.config.scheduler_cadence_seconds,
                now,
            ):
                actions.append(
                    self._result(
                        runtime,
                        "SCHEDULER",
                        "NOT_DUE",
                        "scheduler cadence has not elapsed",
                    )
                )
                continue
            runtime = self._commit(runtime.mark_scheduler_service(now))
            runtime, result = self._service_action(
                runtime,
                action="MARKET_STATUS",
                due=_is_due(
                    runtime.last_market_status_at,
                    runtime.config.market_status_cadence_seconds,
                    now,
                ),
                callback=lambda runtime=runtime: hooks.refresh_market_status(
                    runtime, now
                ),
                mark=lambda item: item.mark_market_status(now),
            )
            actions.append(result)
            runtime, result = self._service_action(
                runtime,
                action="RISK_REFRESH",
                due=_is_due(
                    runtime.last_risk_refresh_at,
                    runtime.config.risk_refresh_cadence_seconds,
                    now,
                ),
                callback=lambda runtime=runtime: hooks.refresh_risk(runtime, now),
                mark=lambda item: item.mark_risk_refresh(now),
            )
            actions.append(result)
            runtime, result = self._service_action(
                runtime,
                action="RECONCILIATION",
                due=_is_due(
                    runtime.last_reconciliation_at,
                    runtime.config.reconciliation_cadence_seconds,
                    now,
                ),
                callback=lambda runtime=runtime: hooks.reconcile_portfolio(
                    runtime, now
                ),
                mark=lambda item: item.mark_reconciliation(now),
            )
            actions.append(result)
            runtime, decision_results = self._service_decision(
                runtime,
                now=now,
                raw_candle=latest_closed_candles.get(runtime.runtime_key),
                hooks=hooks,
            )
            actions.extend(decision_results)
        for result in actions:
            self._emit_action_result(result, occurred_at=now)
        return SchedulerTickResult(serviced_at=now, actions=tuple(actions))

    def _service_action(
        self,
        runtime: InstrumentRuntime,
        *,
        action: str,
        due: bool,
        callback: Any,
        mark: Any,
    ) -> tuple[InstrumentRuntime, SchedulerActionResult]:
        if not due:
            return runtime, self._result(
                runtime,
                action,
                "NOT_DUE",
                "independent cadence has not elapsed",
            )
        try:
            callback()
        # Hook failures are an isolation boundary: one instrument must not stop
        # scheduler service for the remaining runtimes.
        except Exception as exc:  # noqa: BLE001
            return runtime, self._result(
                runtime,
                action,
                "FAILED",
                f"{type(exc).__name__}: {exc}",
            )
        updated = self._commit(mark(runtime))
        return updated, self._result(updated, action, "COMPLETED")

    def _service_decision(
        self,
        runtime: InstrumentRuntime,
        *,
        now: datetime,
        raw_candle: datetime | str | None,
        hooks: InstrumentRuntimeHooks,
    ) -> tuple[InstrumentRuntime, tuple[SchedulerActionResult, ...]]:
        if not _is_due(
            runtime.last_decision_check_at,
            runtime.config.decision_cadence_seconds,
            now,
        ):
            return runtime, (
                self._result(
                    runtime,
                    "DECISION_CHECK",
                    "NOT_DUE",
                    "decision cadence has not elapsed",
                ),
            )
        runtime = self._commit(runtime.mark_decision_check(now))
        if raw_candle in (None, ""):
            return runtime, (
                self._result(
                    runtime,
                    "DECISION_CHECK",
                    "NO_NEW_CANDLE",
                    "no closed candle is available for this runtime",
                ),
            )
        try:
            candle_time = _aware_utc(raw_candle, "candle_time")
        except (TypeError, ValueError) as exc:
            return runtime, (
                self._result(
                    runtime,
                    "DECISION_CHECK",
                    "FAILED",
                    f"{type(exc).__name__}: {exc}",
                ),
            )
        if (
            runtime.last_processed_candle is not None
            and candle_time <= runtime.last_processed_candle
        ):
            return runtime, (
                self._result(
                    runtime,
                    "DECISION_CHECK",
                    "ALREADY_PROCESSED",
                    "closed candle did not advance for this runtime",
                    candle_time=candle_time,
                ),
            )
        try:
            hooks.evaluate_closed_candle(runtime, candle_time, now)
        # Strategy adapters are isolated per instrument for the same reason.
        except Exception as exc:  # noqa: BLE001
            return runtime, (
                self._result(
                    runtime,
                    "DECISION_EVALUATION",
                    "FAILED",
                    f"{type(exc).__name__}: {exc}",
                    candle_time=candle_time,
                ),
            )
        updated = self._commit(runtime.mark_candle_processed(candle_time))
        return updated, (
            self._result(
                updated,
                "DECISION_EVALUATION",
                "COMPLETED",
                candle_time=candle_time,
            ),
        )

    def _insert(self, runtime: InstrumentRuntime) -> None:
        if not isinstance(runtime, InstrumentRuntime):
            raise TypeError("runtime must be InstrumentRuntime")
        if runtime.runtime_key in self._runtimes:
            raise InstrumentRuntimeConflictError(
                f"Duplicate runtime key: {runtime.runtime_key}"
            )
        self._validate_execution_scope(runtime, self._runtimes.values())
        accounts = {item.config.account_id for item in self._runtimes.values()}
        if accounts and runtime.config.account_id not in accounts:
            raise InstrumentRuntimeConflictError(
                "One GlobalScheduler cannot mix account scopes."
            )
        self._runtimes[runtime.runtime_key] = runtime

    @staticmethod
    def _validate_execution_scope(
        runtime: InstrumentRuntime,
        existing: Any,
    ) -> None:
        if any(
            item.config.execution_scope_key == runtime.config.execution_scope_key
            for item in existing
        ):
            raise InstrumentRuntimeConflictError(
                "v3.8 permits one InstrumentRuntime per account/instrument; "
                "per-strategy timeframe belongs to v4.x."
            )

    def _commit(self, runtime: InstrumentRuntime) -> InstrumentRuntime:
        if runtime.runtime_key not in self._runtimes:
            raise InstrumentRuntimeConflictError(
                f"Cannot commit unknown runtime: {runtime.runtime_key}"
            )
        updated = dict(self._runtimes)
        updated[runtime.runtime_key] = runtime
        if self.store is not None:
            self.store.save(updated.values())
        self._runtimes = updated
        return runtime

    def _persist_all(self) -> None:
        if self.store is not None:
            self.store.save(self._runtimes.values())

    def _emit_scheduler_startup(self, event_type: str) -> None:
        runtimes = self.runtimes
        accounts = sorted({item.config.account_id for item in runtimes})
        account_id = accounts[0] if len(accounts) == 1 else None
        self._emit(
            SchedulerAuditEvent(
                event_type=event_type,
                occurred_at=datetime.now(timezone.utc),
                session_id=self.session_id,
                account_id=account_id,
                status="restored" if event_type == "SCHEDULER_RESTORED" else "started",
                payload={
                    "runtime_count": len(runtimes),
                    "runtime_keys": [item.runtime_key for item in runtimes],
                    "last_processed_candles": {
                        item.runtime_key: (
                            item.last_processed_candle.isoformat()
                            if item.last_processed_candle
                            else None
                        )
                        for item in runtimes
                    },
                },
            )
        )

    def _emit_runtime_lifecycle(
        self,
        event_type: str,
        runtime: InstrumentRuntime,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self._emit(
            SchedulerAuditEvent(
                event_type=event_type,
                occurred_at=datetime.now(timezone.utc),
                session_id=self.session_id,
                account_id=runtime.config.account_id,
                runtime_key=runtime.runtime_key,
                instrument_id=runtime.config.instrument_id,
                ticker=runtime.config.ticker,
                status=runtime.status.lower(),
                config_hash=runtime.config.runtime_config_hash,
                payload={
                    "revision": runtime.revision,
                    "candle_interval": runtime.config.candle_interval,
                    "strategy_id": runtime.config.strategy_id,
                    "last_processed_candle": (
                        runtime.last_processed_candle.isoformat()
                        if runtime.last_processed_candle
                        else None
                    ),
                    **dict(payload or {}),
                },
            )
        )

    def _emit_action_result(
        self,
        result: SchedulerActionResult,
        *,
        occurred_at: datetime,
    ) -> None:
        if result.status not in {"COMPLETED", "FAILED"}:
            return
        runtime = self._runtimes.get(result.runtime_key)
        if runtime is None:
            return
        failed = result.status == "FAILED"
        self._emit(
            SchedulerAuditEvent(
                event_type=(
                    "SCHEDULER_ACTION_FAILED"
                    if failed
                    else "SCHEDULER_ACTION_COMPLETED"
                ),
                occurred_at=occurred_at,
                session_id=self.session_id,
                account_id=runtime.config.account_id,
                runtime_key=runtime.runtime_key,
                instrument_id=runtime.config.instrument_id,
                ticker=runtime.config.ticker,
                status=result.status.lower(),
                severity="ERROR" if failed else "INFO",
                action=result.action,
                candle_time=result.candle_time,
                config_hash=runtime.config.runtime_config_hash,
                payload={
                    "detail": result.detail,
                    "revision": runtime.revision,
                    "candle_interval": runtime.config.candle_interval,
                },
            )
        )

    def _emit(self, event: SchedulerAuditEvent) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink.record_scheduler_event(event)
        except Exception:
            logger.exception(
                "Scheduler audit sink failed: event_type=%s runtime=%s",
                event.event_type,
                event.runtime_key,
            )

    @staticmethod
    def _result(
        runtime: InstrumentRuntime,
        action: str,
        status: str,
        detail: str = "",
        *,
        candle_time: datetime | None = None,
    ) -> SchedulerActionResult:
        return SchedulerActionResult(
            runtime_key=runtime.runtime_key,
            ticker=runtime.config.ticker,
            action=action,
            status=status,
            detail=detail,
            candle_time=candle_time.isoformat() if candle_time else None,
        )


def _aware_utc(value: datetime | str, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware.")
    return parsed.astimezone(timezone.utc)


def _is_due(
    last_at: datetime | None,
    cadence_seconds: int,
    now: datetime,
) -> bool:
    if last_at is None:
        return True
    return (now - last_at).total_seconds() >= int(cadence_seconds)
