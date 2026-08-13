from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_robot.global_scheduler import GlobalScheduler
from trading_robot.instrument_runtime import (
    InstrumentRuntime,
    InstrumentRuntimeConfig,
    InstrumentRuntimeConflictError,
    InstrumentRuntimeError,
    InstrumentRuntimeStateError,
    InstrumentRuntimeStore,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
STRATEGY_HASH = "a" * 64


def make_config(
    ticker: str,
    interval: str,
    *,
    instrument_id: str | None = None,
    strategy_hash: str = STRATEGY_HASH,
    configuration_version: int = 1,
    scheduler_seconds: int = 1,
    decision_seconds: int = 1,
    risk_seconds: int = 30,
    reconciliation_seconds: int = 60,
    market_seconds: int = 15,
) -> InstrumentRuntimeConfig:
    return InstrumentRuntimeConfig(
        account_id="sandbox-account-1",
        instrument_id=instrument_id or f"uid-{ticker.lower()}",
        ticker=ticker,
        class_code="TQBR",
        candle_interval=interval,
        strategy_id="sma",
        strategy_config_hash=strategy_hash,
        configuration_version=configuration_version,
        scheduler_cadence_seconds=scheduler_seconds,
        decision_cadence_seconds=decision_seconds,
        risk_refresh_cadence_seconds=risk_seconds,
        reconciliation_cadence_seconds=reconciliation_seconds,
        market_status_cadence_seconds=market_seconds,
    )


def make_runtimes() -> tuple[InstrumentRuntime, ...]:
    return (
        InstrumentRuntime(make_config("SBER", "CANDLE_INTERVAL_HOUR")).start(),
        InstrumentRuntime(make_config("LKOH", "CANDLE_INTERVAL_30_MIN")).start(),
        InstrumentRuntime(make_config("YDEX", "CANDLE_INTERVAL_15_MIN")).start(),
    )


class RecordingHooks:
    def __init__(self, *, fail_risk_for: str | None = None) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.fail_risk_for = fail_risk_for

    def refresh_market_status(self, runtime: InstrumentRuntime, now: datetime):
        self.calls.append(("market", runtime.config.ticker, None))

    def refresh_risk(self, runtime: InstrumentRuntime, now: datetime):
        self.calls.append(("risk", runtime.config.ticker, None))
        if runtime.config.ticker == self.fail_risk_for:
            raise RuntimeError("risk probe failed")

    def reconcile_portfolio(self, runtime: InstrumentRuntime, now: datetime):
        self.calls.append(("reconcile", runtime.config.ticker, None))

    def evaluate_closed_candle(
        self,
        runtime: InstrumentRuntime,
        candle_time: datetime,
        now: datetime,
    ):
        self.calls.append(("decision", runtime.config.ticker, candle_time.isoformat()))


def calls_of(hooks: RecordingHooks, action: str) -> list[tuple[str, str, str | None]]:
    return [item for item in hooks.calls if item[0] == action]


def test_runtime_identity_includes_fixed_candle_interval():
    hourly = make_config("SBER", "CANDLE_INTERVAL_HOUR")
    fifteen = make_config("SBER", "CANDLE_INTERVAL_15_MIN")

    assert hourly.runtime_config_hash != fifteen.runtime_config_hash
    assert hourly.runtime_key != fifteen.runtime_key
    assert hourly.execution_scope_key == fifteen.execution_scope_key


def test_service_cadences_do_not_change_strategy_runtime_identity():
    baseline = make_config("SBER", "CANDLE_INTERVAL_HOUR")
    faster_services = replace(
        baseline,
        scheduler_cadence_seconds=5,
        decision_cadence_seconds=10,
        risk_refresh_cadence_seconds=15,
        reconciliation_cadence_seconds=20,
        market_status_cadence_seconds=25,
    )

    assert baseline.runtime_config_hash == faster_services.runtime_config_hash
    assert baseline.runtime_key == faster_services.runtime_key


@pytest.mark.parametrize(
    "field,value",
    [
        ("candle_interval", "HOUR"),
        ("strategy_config_hash", "not-a-sha"),
        ("scheduler_cadence_seconds", 0),
        ("risk_refresh_cadence_seconds", -1),
    ],
)
def test_runtime_config_rejects_invalid_identity_or_cadence(field: str, value):
    values = make_config("SBER", "CANDLE_INTERVAL_HOUR").to_dict()
    cadences = values.pop("cadences")
    values.pop("runtime_config_hash")
    values.pop("runtime_key")
    constructor = {
        **values,
        "decision_cadence_seconds": cadences["decision_seconds"],
        "scheduler_cadence_seconds": cadences["scheduler_seconds"],
        "risk_refresh_cadence_seconds": cadences["risk_refresh_seconds"],
        "reconciliation_cadence_seconds": cadences["reconciliation_seconds"],
        "market_status_cadence_seconds": cadences["market_status_seconds"],
        field: value,
    }
    with pytest.raises(InstrumentRuntimeError):
        InstrumentRuntimeConfig(**constructor)


def test_scheduler_accepts_three_instruments_with_different_timeframes():
    scheduler = GlobalScheduler(make_runtimes())

    assert {
        (item.config.ticker, item.config.candle_interval)
        for item in scheduler.runtimes
    } == {
        ("SBER", "CANDLE_INTERVAL_HOUR"),
        ("LKOH", "CANDLE_INTERVAL_30_MIN"),
        ("YDEX", "CANDLE_INTERVAL_15_MIN"),
    }


def test_scheduler_rejects_second_runtime_for_same_instrument():
    first = InstrumentRuntime(make_config("SBER", "CANDLE_INTERVAL_HOUR"))
    second = InstrumentRuntime(make_config("SBER", "CANDLE_INTERVAL_15_MIN"))

    with pytest.raises(InstrumentRuntimeConflictError, match="v3.8 permits one"):
        GlobalScheduler((first, second))


def test_new_candle_for_one_runtime_has_no_decision_cross_talk():
    scheduler = GlobalScheduler(make_runtimes())
    hooks = RecordingHooks()
    sber = next(item for item in scheduler.runtimes if item.config.ticker == "SBER")
    candle = T0 - timedelta(hours=1)

    scheduler.tick(
        now=T0,
        latest_closed_candles={sber.runtime_key: candle},
        hooks=hooks,
    )

    assert calls_of(hooks, "decision") == [
        ("decision", "SBER", candle.isoformat())
    ]
    assert {item[1] for item in calls_of(hooks, "risk")} == {
        "SBER",
        "LKOH",
        "YDEX",
    }
    assert {item[1] for item in calls_of(hooks, "reconcile")} == {
        "SBER",
        "LKOH",
        "YDEX",
    }


def test_same_closed_candle_is_evaluated_at_most_once():
    scheduler = GlobalScheduler(make_runtimes())
    hooks = RecordingHooks()
    sber = next(item for item in scheduler.runtimes if item.config.ticker == "SBER")
    candle = T0 - timedelta(hours=1)

    scheduler.tick(
        now=T0,
        latest_closed_candles={sber.runtime_key: candle},
        hooks=hooks,
    )
    scheduler.tick(
        now=T0 + timedelta(seconds=1),
        latest_closed_candles={sber.runtime_key: candle},
        hooks=hooks,
    )

    assert calls_of(hooks, "decision") == [
        ("decision", "SBER", candle.isoformat())
    ]
    assert scheduler.get(sber.runtime_key).last_processed_candle == candle


def test_risk_and_reconciliation_cadences_run_without_new_candle():
    scheduler = GlobalScheduler(make_runtimes())
    hooks = RecordingHooks()

    scheduler.tick(now=T0, latest_closed_candles={}, hooks=hooks)
    hooks.calls.clear()
    scheduler.tick(
        now=T0 + timedelta(seconds=30),
        latest_closed_candles={},
        hooks=hooks,
    )
    assert {item[1] for item in calls_of(hooks, "risk")} == {
        "SBER",
        "LKOH",
        "YDEX",
    }
    assert calls_of(hooks, "reconcile") == []
    assert calls_of(hooks, "decision") == []

    hooks.calls.clear()
    scheduler.tick(
        now=T0 + timedelta(seconds=60),
        latest_closed_candles={},
        hooks=hooks,
    )
    assert {item[1] for item in calls_of(hooks, "risk")} == {
        "SBER",
        "LKOH",
        "YDEX",
    }
    assert {item[1] for item in calls_of(hooks, "reconcile")} == {
        "SBER",
        "LKOH",
        "YDEX",
    }


def test_scheduler_cadence_is_independent_from_candle_interval():
    scheduler = GlobalScheduler(make_runtimes())
    hooks = RecordingHooks()

    first = scheduler.tick(now=T0, latest_closed_candles={}, hooks=hooks)
    second = scheduler.tick(
        now=T0 + timedelta(milliseconds=500),
        latest_closed_candles={},
        hooks=hooks,
    )

    assert not first.failures
    assert sum(item.action == "SCHEDULER" for item in second.actions) == 3
    assert all(
        item.status == "NOT_DUE"
        for item in second.actions
        if item.action == "SCHEDULER"
    )


def test_runtime_failure_isolated_from_other_instruments():
    scheduler = GlobalScheduler(make_runtimes())
    hooks = RecordingHooks(fail_risk_for="SBER")

    result = scheduler.tick(now=T0, latest_closed_candles={}, hooks=hooks)

    assert len(result.failures) == 1
    assert result.failures[0].ticker == "SBER"
    assert {item[1] for item in calls_of(hooks, "reconcile")} == {
        "SBER",
        "LKOH",
        "YDEX",
    }
    assert {item[1] for item in calls_of(hooks, "risk")} == {
        "SBER",
        "LKOH",
        "YDEX",
    }


def test_timeframe_change_is_blocked_while_runtime_is_active():
    runtime = InstrumentRuntime(
        make_config("SBER", "CANDLE_INTERVAL_HOUR")
    ).start()
    replacement = make_config(
        "SBER",
        "CANDLE_INTERVAL_15_MIN",
        configuration_version=2,
    )

    with pytest.raises(InstrumentRuntimeConflictError, match="STOPPED"):
        runtime.replace_configuration(replacement)


@pytest.mark.parametrize(
    "current_lots,pending",
    [(1, ()), (0, ("order-1",))],
)
def test_timeframe_change_is_blocked_with_position_or_pending_order(
    current_lots: int,
    pending: tuple[str, ...],
):
    runtime = InstrumentRuntime(
        make_config("SBER", "CANDLE_INTERVAL_HOUR")
    ).with_execution_state(
        current_lots=current_lots,
        pending_order_ids=pending,
    )
    replacement = make_config(
        "SBER",
        "CANDLE_INTERVAL_15_MIN",
        configuration_version=2,
    )

    with pytest.raises(InstrumentRuntimeConflictError, match="flat"):
        runtime.replace_configuration(replacement)


def test_explicit_stopped_flat_timeframe_change_creates_new_identity():
    old = (
        InstrumentRuntime(make_config("SBER", "CANDLE_INTERVAL_HOUR"))
        .mark_candle_processed(T0 - timedelta(hours=1))
    )
    replacement = make_config(
        "SBER",
        "CANDLE_INTERVAL_15_MIN",
        configuration_version=2,
    )

    new = old.replace_configuration(replacement)

    assert new.runtime_key != old.runtime_key
    assert new.config.candle_interval == "CANDLE_INTERVAL_15_MIN"
    assert new.last_processed_candle is None
    assert new.status == "STOPPED"


def test_runtime_store_round_trip_restores_per_instrument_temporal_state(
    tmp_path: Path,
):
    path = tmp_path / "instrument_runtimes.json"
    store = InstrumentRuntimeStore(path)
    runtimes = tuple(
        runtime.mark_candle_processed(T0 - timedelta(minutes=index + 1))
        for index, runtime in enumerate(make_runtimes())
    )

    store.save(runtimes)
    restored = store.load(expected_account_id="sandbox-account-1")

    assert path.with_name(path.name + ".sha256").is_file()
    assert [item.runtime_key for item in restored] == sorted(
        item.runtime_key for item in runtimes
    )
    assert {
        item.config.ticker: item.last_processed_candle for item in restored
    } == {
        item.config.ticker: item.last_processed_candle for item in runtimes
    }


def test_runtime_store_fails_closed_on_checksum_mismatch(tmp_path: Path):
    path = tmp_path / "instrument_runtimes.json"
    store = InstrumentRuntimeStore(path)
    store.save(make_runtimes())
    document = json.loads(path.read_text(encoding="utf-8"))
    document["runtimes"][0]["status"] = "BLOCKED"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(InstrumentRuntimeStateError, match="CHECKSUM_MISMATCH"):
        store.load()


def test_scheduler_restart_does_not_repeat_processed_candle(tmp_path: Path):
    store = InstrumentRuntimeStore(tmp_path / "instrument_runtimes.json")
    store.save(make_runtimes())
    scheduler = GlobalScheduler.restore(
        store,
        expected_account_id="sandbox-account-1",
    )
    hooks = RecordingHooks()
    sber = next(item for item in scheduler.runtimes if item.config.ticker == "SBER")
    candle = T0 - timedelta(hours=1)
    scheduler.tick(
        now=T0,
        latest_closed_candles={sber.runtime_key: candle},
        hooks=hooks,
    )

    restarted = GlobalScheduler.restore(
        store,
        expected_account_id="sandbox-account-1",
    )
    restarted_hooks = RecordingHooks()
    restarted.tick(
        now=T0 + timedelta(seconds=1),
        latest_closed_candles={sber.runtime_key: candle},
        hooks=restarted_hooks,
    )

    assert calls_of(restarted_hooks, "decision") == []
    assert restarted.get(sber.runtime_key).last_processed_candle == candle


def test_persisted_runtime_identity_tampering_is_rejected(tmp_path: Path):
    path = tmp_path / "instrument_runtimes.json"
    store = InstrumentRuntimeStore(path)
    store.save(make_runtimes())
    document = json.loads(path.read_text(encoding="utf-8"))
    document["runtimes"][0]["config"]["runtime_key"] = "forged"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(digest + "\n", encoding="ascii")

    with pytest.raises(InstrumentRuntimeStateError, match="key mismatch"):
        store.load()
