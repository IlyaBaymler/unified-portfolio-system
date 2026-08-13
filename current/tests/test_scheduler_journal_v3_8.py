from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_robot.global_scheduler import GlobalScheduler, SchedulerAuditEvent
from trading_robot.instrument_runtime import (
    InstrumentRuntime,
    InstrumentRuntimeConfig,
    InstrumentRuntimeStore,
)
from trading_robot.journal import EventJournal
from trading_robot.scheduler_journal import EventJournalSchedulerSink

UTC = timezone.utc
T0 = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def make_runtime(*, active: bool = False) -> InstrumentRuntime:
    runtime = InstrumentRuntime(
        InstrumentRuntimeConfig(
            account_id="sandbox-account-1",
            instrument_id="uid-sber",
            ticker="SBER",
            class_code="TQBR",
            candle_interval="CANDLE_INTERVAL_HOUR",
            strategy_id="sma",
            strategy_config_hash="a" * 64,
            scheduler_cadence_seconds=1,
            decision_cadence_seconds=1,
            risk_refresh_cadence_seconds=1,
            reconciliation_cadence_seconds=1,
            market_status_cadence_seconds=1,
        )
    )
    return runtime.start() if active else runtime


class RecordingHooks:
    def __init__(self) -> None:
        self.decisions: list[tuple[str, datetime]] = []

    def refresh_market_status(self, runtime, now):
        return None

    def refresh_risk(self, runtime, now):
        return None

    def reconcile_portfolio(self, runtime, now):
        return None

    def evaluate_closed_candle(self, runtime, candle_time, now):
        self.decisions.append((runtime.config.ticker, candle_time))


class FailingAuditSink:
    def record_scheduler_event(self, event: SchedulerAuditEvent) -> None:
        raise OSError("audit storage unavailable")


def make_scheduler(
    root: Path,
    *,
    active: bool,
    session_id: str,
) -> tuple[GlobalScheduler, InstrumentRuntimeStore, EventJournal]:
    store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    store.save((make_runtime(active=active),))
    journal = EventJournal(root / "trading_events.db")
    scheduler = GlobalScheduler.restore(
        store,
        expected_account_id="sandbox-account-1",
        event_sink=EventJournalSchedulerSink(journal, mode="DRY_RUN"),
        session_id=session_id,
    )
    return scheduler, store, journal


def test_scheduler_journal_records_persisted_lifecycle_and_actions(
    tmp_path: Path,
):
    scheduler, _, journal = make_scheduler(
        tmp_path,
        active=False,
        session_id="scheduler-session-1",
    )
    runtime = scheduler.runtimes[0]
    runtime = scheduler.start_runtime(runtime.runtime_key)
    candle = T0 - timedelta(hours=1)

    scheduler.tick(
        now=T0,
        latest_closed_candles={runtime.runtime_key: candle},
        hooks=RecordingHooks(),
    )
    scheduler.stop_runtime(runtime.runtime_key)

    restored = journal.recent(event_type="SCHEDULER_RESTORED")
    completed = journal.recent(event_type="SCHEDULER_ACTION_COMPLETED")
    lifecycle = journal.recent(category="runtime")
    assert len(restored) == 1
    assert restored[0]["session_id"] == "scheduler-session-1"
    assert restored[0]["payload"]["runtime_count"] == 1
    assert {row["action"] for row in completed} == {
        "MARKET_STATUS",
        "RISK_REFRESH",
        "RECONCILIATION",
        "DECISION_EVALUATION",
    }
    assert {row["event_type"] for row in lifecycle} >= {
        "RUNTIME_STARTED",
        "RUNTIME_STOPPED",
    }
    assert all(row["account_id"] == "sandbox-account-1" for row in completed)
    assert all(row["config_hash"] for row in completed)


def test_scheduler_restart_journal_does_not_duplicate_decision(
    tmp_path: Path,
):
    scheduler, store, journal = make_scheduler(
        tmp_path,
        active=True,
        session_id="scheduler-session-1",
    )
    runtime = scheduler.runtimes[0]
    candle = T0 - timedelta(hours=1)
    first_hooks = RecordingHooks()
    scheduler.tick(
        now=T0,
        latest_closed_candles={runtime.runtime_key: candle},
        hooks=first_hooks,
    )

    restarted = GlobalScheduler.restore(
        store,
        expected_account_id="sandbox-account-1",
        event_sink=EventJournalSchedulerSink(journal, mode="DRY_RUN"),
        session_id="scheduler-session-2",
    )
    second_hooks = RecordingHooks()
    restarted.tick(
        now=T0 + timedelta(seconds=2),
        latest_closed_candles={runtime.runtime_key: candle},
        hooks=second_hooks,
    )

    completed = journal.recent(event_type="SCHEDULER_ACTION_COMPLETED")
    decision_events = [
        row for row in completed if row["action"] == "DECISION_EVALUATION"
    ]
    restored = journal.recent(event_type="SCHEDULER_RESTORED")
    second_restore = next(
        row for row in restored if row["session_id"] == "scheduler-session-2"
    )
    assert first_hooks.decisions == [("SBER", candle)]
    assert second_hooks.decisions == []
    assert len(decision_events) == 1
    assert candle.isoformat() in second_restore["payload"][
        "last_processed_candles"
    ].values()


def test_scheduler_audit_failure_never_blocks_temporal_persistence(
    tmp_path: Path,
):
    store = InstrumentRuntimeStore(tmp_path / "instrument_runtimes.json")
    runtime = make_runtime(active=True)
    store.save((runtime,))
    scheduler = GlobalScheduler.restore(
        store,
        expected_account_id="sandbox-account-1",
        event_sink=FailingAuditSink(),
        session_id="scheduler-session-failing-audit",
    )
    candle = T0 - timedelta(hours=1)

    result = scheduler.tick(
        now=T0,
        latest_closed_candles={runtime.runtime_key: candle},
        hooks=RecordingHooks(),
    )

    persisted = store.load(expected_account_id="sandbox-account-1")[0]
    assert not result.failures
    assert persisted.last_processed_candle == candle
