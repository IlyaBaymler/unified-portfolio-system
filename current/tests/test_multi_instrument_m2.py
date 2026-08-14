from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from trading_robot.bot import BotConfig
from trading_robot.candle_policy import (
    candle_interval_policy,
    strategy_lookback_days,
)
from trading_robot.config_persistence import bot_config_to_profile
from trading_robot.dashboard_view import (
    build_multi_instrument_dashboard,
    load_multi_instrument_dashboard,
)
from trading_robot.global_scheduler import GlobalScheduler
from trading_robot.instrument_runtime import (
    InstrumentRuntime,
    InstrumentRuntimeConflictError,
    InstrumentRuntimeStore,
)
from trading_robot.multi_instrument_config import (
    MultiInstrumentConfigError,
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)
from trading_robot.multi_instrument_strategy import (
    MultiInstrumentStrategyAdapter,
    MultiInstrumentStrategyError,
    StrategyCandleLoader,
)
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.runtime_bootstrap import validate_runtime_files

UTC = timezone.utc
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def make_profile(
    ticker: str,
    interval: str,
    *,
    instrument_id: str | None = None,
    version: int = 1,
) -> MultiInstrumentProfile:
    config = BotConfig(
        ticker=ticker,
        class_code="TQBR",
        candle_interval=interval,
        primary_strategy="sma",
        shadow_strategies=(),
        fast_window=2,
        slow_window=5,
        volatility_window=5,
        lookback_days=5,
        max_order_lots=1,
    )
    return MultiInstrumentProfile(
        instrument_id=instrument_id or f"uid-{ticker.lower()}",
        strategy_profile=bot_config_to_profile(
            config,
            connect_timeout_seconds=8,
            read_timeout_seconds=25,
        ),
        configuration_version=version,
        scheduler_cadence_seconds=1,
        decision_cadence_seconds=1,
        risk_refresh_cadence_seconds=30,
        reconciliation_cadence_seconds=60,
        market_status_cadence_seconds=15,
    )


def three_profiles() -> tuple[MultiInstrumentProfile, ...]:
    return (
        make_profile("SBER", "CANDLE_INTERVAL_HOUR"),
        make_profile("LKOH", "CANDLE_INTERVAL_30_MIN"),
        make_profile("YDEX", "CANDLE_INTERVAL_15_MIN"),
    )


def frame_ending(end: datetime, frequency: str) -> pd.DataFrame:
    index = pd.date_range(end=end, periods=120, freq=frequency, tz="UTC")
    close = [100.0 + index / 10.0 for index in range(len(index))]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value + 0.5 for value in close],
            "low": [value - 0.5 for value in close],
            "close": close,
            "volume": [1000] * len(index),
            "is_complete": [True] * len(index),
        },
        index=index,
    )


class FakeCandleAPI:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[dict[str, object]] = []

    def get_candles(
        self,
        instrument_id: str,
        from_time: datetime,
        to_time: datetime,
        *,
        interval: str,
        limit: int | None,
    ) -> pd.DataFrame:
        self.calls.append(
            {
                "instrument_id": instrument_id,
                "from": from_time,
                "to": to_time,
                "interval": interval,
                "limit": limit,
            }
        )
        return self.frames[instrument_id].copy()


class BrokerPostTrapAPI(FakeCandleAPI):
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        super().__init__(frames)
        self.post_order_calls = 0

    def post_order(self, *args, **kwargs):
        self.post_order_calls += 1
        raise AssertionError("read-only v3.8 adapter attempted broker POST")


class ReadOnlyServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def refresh_market_status(self, runtime, now):
        self.calls.append(("market", runtime.config.ticker))

    def refresh_risk(self, runtime, now):
        self.calls.append(("risk", runtime.config.ticker))

    def reconcile_portfolio(self, runtime, now):
        self.calls.append(("reconcile", runtime.config.ticker))


def save_profiles_and_registry(
    root: Path,
    *,
    profiles: tuple[MultiInstrumentProfile, ...] | None = None,
):
    selected = profiles or three_profiles()
    profile_store = MultiInstrumentProfileStore(
        root / "multi_instrument_profiles.json"
    )
    profile_store.save_mode("DRY_RUN", selected)
    runtime_store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    runtimes = profile_store.bootstrap_runtime_registry(
        mode="DRY_RUN",
        account_id="sandbox-account-1",
        runtime_store=runtime_store,
    )
    return profile_store, runtime_store, runtimes


def test_mode_separated_multi_instrument_profiles_round_trip(tmp_path: Path):
    store = MultiInstrumentProfileStore(tmp_path / "profiles.json")
    dry = three_profiles()
    execution = (make_profile("SBER", "CANDLE_INTERVAL_30_MIN"),)

    store.save_mode("DRY_RUN", dry)
    store.save_mode("SANDBOX_EXECUTION", execution)

    assert store.load_mode("DRY_RUN") == tuple(
        sorted(dry, key=lambda item: item.instrument_id)
    )
    assert store.load_mode("SANDBOX_EXECUTION") == execution


def test_multi_instrument_profile_set_is_limited_to_three(tmp_path: Path):
    profiles = (*three_profiles(), make_profile("MOEX", "CANDLE_INTERVAL_HOUR"))

    with pytest.raises(MultiInstrumentConfigError, match="at most 3"):
        MultiInstrumentProfileStore(tmp_path / "profiles.json").save_mode(
            "DRY_RUN", profiles
        )


def test_multi_instrument_profile_rejects_duplicate_instrument(tmp_path: Path):
    first = make_profile("SBER", "CANDLE_INTERVAL_HOUR")
    duplicate = make_profile(
        "SBER2",
        "CANDLE_INTERVAL_15_MIN",
        instrument_id=first.instrument_id,
    )

    with pytest.raises(MultiInstrumentConfigError, match="Duplicate instrument_id"):
        MultiInstrumentProfileStore(tmp_path / "profiles.json").save_mode(
            "DRY_RUN", (first, duplicate)
        )


def test_multi_instrument_profile_rejects_secret_fields():
    profile = dict(make_profile("SBER", "CANDLE_INTERVAL_HOUR").strategy_profile)
    profile["token"] = "must-not-persist"

    with pytest.raises(MultiInstrumentConfigError, match="forbidden"):
        MultiInstrumentProfile("uid-sber", profile)


def test_multi_instrument_profile_checksum_tampering_is_fail_closed(
    tmp_path: Path,
):
    path = tmp_path / "profiles.json"
    store = MultiInstrumentProfileStore(path)
    store.save_mode("DRY_RUN", three_profiles())
    document = json.loads(path.read_text(encoding="utf-8"))
    document["profiles"]["DRY_RUN"]["instruments"][0]["strategy_profile"][
        "candle_interval"
    ] = "CANDLE_INTERVAL_DAY"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + "\n",
        encoding="ascii",
    )

    with pytest.raises(MultiInstrumentConfigError, match="checksum mismatch"):
        store.load_mode("DRY_RUN")


def test_registry_bootstrap_creates_stopped_runtimes_and_is_idempotent(
    tmp_path: Path,
):
    profile_store, runtime_store, first = save_profiles_and_registry(tmp_path)
    second = profile_store.bootstrap_runtime_registry(
        mode="DRY_RUN",
        account_id="sandbox-account-1",
        runtime_store=runtime_store,
    )

    assert len(first) == 3
    assert first == second
    assert all(runtime.status == "STOPPED" for runtime in second)
    assert {
        (runtime.config.ticker, runtime.config.candle_interval)
        for runtime in second
    } == {
        ("SBER", "CANDLE_INTERVAL_HOUR"),
        ("LKOH", "CANDLE_INTERVAL_30_MIN"),
        ("YDEX", "CANDLE_INTERVAL_15_MIN"),
    }


def test_registry_bootstrap_never_applies_implicit_timeframe_change(
    tmp_path: Path,
):
    profile_store, runtime_store, _ = save_profiles_and_registry(tmp_path)
    changed = (
        make_profile("SBER", "CANDLE_INTERVAL_15_MIN", version=2),
        make_profile("LKOH", "CANDLE_INTERVAL_30_MIN"),
        make_profile("YDEX", "CANDLE_INTERVAL_15_MIN"),
    )
    profile_store.save_mode("DRY_RUN", changed)

    with pytest.raises(InstrumentRuntimeConflictError, match="explicit"):
        profile_store.bootstrap_runtime_registry(
            mode="DRY_RUN",
            account_id="sandbox-account-1",
            runtime_store=runtime_store,
        )


def test_runtime_validation_recognizes_optional_v3_8_pair(tmp_path: Path):
    save_profiles_and_registry(tmp_path)

    report = validate_runtime_files(tmp_path)
    actions = {item.name: item.action for item in report.items}

    assert actions["multi_instrument_profiles.json"] == "VALIDATED"
    assert actions["instrument_runtimes.json"] == "VALIDATED"
    assert not any("v3.8 runtime registry" in error for error in report.errors)


def test_read_only_dashboard_joins_profiles_and_runtimes_without_writes(
    tmp_path: Path,
):
    save_profiles_and_registry(tmp_path)
    profile_path = tmp_path / "multi_instrument_profiles.json"
    runtime_path = tmp_path / "instrument_runtimes.json"
    managed_paths = (
        profile_path,
        profile_path.with_name(profile_path.name + ".sha256"),
        runtime_path,
        runtime_path.with_name(runtime_path.name + ".sha256"),
    )
    before = {path.name: path.read_bytes() for path in managed_paths}

    snapshot = load_multi_instrument_dashboard(
        profile_path,
        runtime_path,
        mode="DRY_RUN",
    )

    assert snapshot.state == "READY"
    assert snapshot.account_id == "sandbox-account-1"
    assert {row.ticker for row in snapshot.rows} == {"SBER", "LKOH", "YDEX"}
    assert {row.identity_status for row in snapshot.rows} == {"MATCHED"}
    assert all(row.runtime_status == "STOPPED" for row in snapshot.rows)
    assert before == {path.name: path.read_bytes() for path in managed_paths}


def test_read_only_dashboard_surfaces_identity_mismatch():
    profiles = three_profiles()
    runtimes = tuple(
        InstrumentRuntime(profile.to_runtime_config("sandbox-account-1"))
        for profile in profiles
    )
    changed = (
        make_profile("SBER", "CANDLE_INTERVAL_15_MIN", version=2),
        *profiles[1:],
    )

    snapshot = build_multi_instrument_dashboard(
        changed,
        runtimes,
        mode="DRY_RUN",
    )

    sber = next(row for row in snapshot.rows if row.ticker == "SBER")
    assert snapshot.state == "ATTENTION"
    assert sber.identity_status == "MISMATCH"
    assert "candle_interval" in sber.detail


def test_backup_includes_v3_8_profiles_and_runtime_registry(tmp_path: Path):
    save_profiles_and_registry(tmp_path)
    backup = RuntimeBackupManager(tmp_path, app_version="0.3.8a1").create_backup(
        tmp_path / "backups" / "runtime.zip"
    )
    verification = RuntimeBackupManager(
        tmp_path, app_version="0.3.8a1"
    ).verify_backup(backup)
    names = {entry["name"] for entry in verification.manifest["entries"]}

    assert verification.valid
    assert "multi_instrument_profiles.json" in names
    assert "instrument_runtimes.json" in names


def test_backup_restore_recreates_v3_8_checksum_sidecars(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    save_profiles_and_registry(source)
    backup = RuntimeBackupManager(
        source, app_version="0.3.8a1"
    ).create_backup(tmp_path / "runtime.zip")
    restored = tmp_path / "restored"

    RuntimeBackupManager(restored, app_version="0.3.8a1").restore_backup(
        backup,
        confirmation="RESTORE RUNTIME",
    )

    profiles = MultiInstrumentProfileStore(
        restored / "multi_instrument_profiles.json"
    ).load_mode("DRY_RUN")
    runtimes = InstrumentRuntimeStore(
        restored / "instrument_runtimes.json"
    ).load(expected_account_id="sandbox-account-1")
    assert len(profiles) == 3
    assert len(runtimes) == 3


@pytest.mark.parametrize(
    "interval,span_days,lookback_days",
    [
        ("CANDLE_INTERVAL_15_MIN", 21, 20),
        ("CANDLE_INTERVAL_30_MIN", 21, 20),
        ("CANDLE_INTERVAL_HOUR", 90, 89),
    ],
)
def test_candle_policy_covers_v3_8_intervals(
    interval: str,
    span_days: int,
    lookback_days: int,
):
    policy = candle_interval_policy(interval)

    assert policy.maximum_request_span.days == span_days
    assert policy.maximum_lookback_days == lookback_days
    assert strategy_lookback_days(
        interval,
        required_bars=10_000,
        requested_days=999,
    ) == lookback_days


def test_strategy_adapter_emits_read_only_proposals_for_three_timeframes(
    tmp_path: Path,
):
    profile_store, runtime_store, runtimes = save_profiles_and_registry(tmp_path)
    active = tuple(runtime.start() for runtime in runtimes)
    runtime_store.save(active)
    frames = {
        "uid-sber": frame_ending(NOW - timedelta(hours=1), "1h"),
        "uid-lkoh": frame_ending(NOW - timedelta(minutes=30), "30min"),
        "uid-ydex": frame_ending(NOW - timedelta(minutes=15), "15min"),
    }
    api = FakeCandleAPI(frames)
    services = ReadOnlyServices()
    adapter = MultiInstrumentStrategyAdapter(
        profile_store.load_mode("DRY_RUN"),
        candle_loader=StrategyCandleLoader(api),
        services=services,
    )
    scheduler = GlobalScheduler(active, store=runtime_store)

    result = scheduler.tick(
        now=NOW,
        latest_closed_candles={
            runtime.runtime_key: frames[runtime.config.instrument_id].index[-1]
            for runtime in active
        },
        hooks=adapter,
    )

    assert not result.failures
    assert len(adapter.proposals) == 3
    assert {proposal.candle_interval for proposal in adapter.proposals} == {
        "CANDLE_INTERVAL_HOUR",
        "CANDLE_INTERVAL_30_MIN",
        "CANDLE_INTERVAL_15_MIN",
    }
    assert all(not proposal.execution_authorized for proposal in adapter.proposals)
    assert all(
        proposal.to_dict()["next_gate"]
        == "PORTFOLIO_POLICY_RISK_PREFLIGHT_EXECUTION"
        for proposal in adapter.proposals
    )
    assert not hasattr(adapter, "post_order")
    assert all(call["limit"] is None for call in api.calls)


def test_strategy_adapter_preserves_no_cross_talk_for_single_new_candle(
    tmp_path: Path,
):
    profile_store, _, runtimes = save_profiles_and_registry(tmp_path)
    active = tuple(runtime.start() for runtime in runtimes)
    sber = next(runtime for runtime in active if runtime.config.ticker == "SBER")
    frames = {
        runtime.config.instrument_id: frame_ending(
            NOW - timedelta(hours=1), "1h"
        )
        for runtime in active
    }
    adapter = MultiInstrumentStrategyAdapter(
        profile_store.load_mode("DRY_RUN"),
        candle_loader=StrategyCandleLoader(FakeCandleAPI(frames)),
        services=ReadOnlyServices(),
    )

    GlobalScheduler(active).tick(
        now=NOW,
        latest_closed_candles={
            sber.runtime_key: frames[sber.config.instrument_id].index[-1]
        },
        hooks=adapter,
    )

    assert [proposal.ticker for proposal in adapter.proposals] == ["SBER"]


def test_strategy_adapter_rejects_runtime_profile_identity_mismatch(
    tmp_path: Path,
):
    _, _, runtimes = save_profiles_and_registry(tmp_path)
    sber = next(runtime for runtime in runtimes if runtime.config.ticker == "SBER")
    changed_profile = make_profile("SBER", "CANDLE_INTERVAL_15_MIN", version=2)
    frame = frame_ending(NOW - timedelta(hours=1), "1h")
    adapter = MultiInstrumentStrategyAdapter(
        (changed_profile,),
        candle_loader=StrategyCandleLoader(FakeCandleAPI({"uid-sber": frame})),
        services=ReadOnlyServices(),
    )

    with pytest.raises(MultiInstrumentStrategyError, match="identity mismatch"):
        adapter.evaluate_closed_candle(
            sber,
            frame.index[-1].to_pydatetime(),
            NOW,
        )


def test_strategy_adapter_never_calls_broker_post(tmp_path: Path):
    profile_store, _, runtimes = save_profiles_and_registry(tmp_path)
    sber = next(
        runtime.start()
        for runtime in runtimes
        if runtime.config.ticker == "SBER"
    )
    api = BrokerPostTrapAPI(
        {"uid-sber": frame_ending(NOW - timedelta(hours=1), "1h")}
    )
    adapter = MultiInstrumentStrategyAdapter(
        profile_store.load_mode("DRY_RUN"),
        candle_loader=StrategyCandleLoader(api),
        services=ReadOnlyServices(),
    )

    adapter.evaluate_closed_candle(
        sber,
        NOW - timedelta(hours=1),
        NOW,
    )

    assert api.post_order_calls == 0
