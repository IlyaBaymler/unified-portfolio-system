from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_robot.risk import (
    ExecutionRecord,
    RiskEngine,
    RiskInputError,
    RiskPolicy,
    RiskSnapshot,
    RiskState,
    average_true_range,
    portfolio_risk_inputs,
)
from trading_robot.risk_persistence import (
    RiskPersistenceError,
    RiskProfileStore,
    RiskStateStore,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def snapshot(
    *,
    target: int = 1,
    current: int = 0,
    price: float | None = 250.0,
    lot_size: int = 10,
    equity: float | None = 50_000.0,
    cash: float | None = 50_000.0,
    atr: float | None = 5.0,
    stop_distance: float | None = None,
    now: datetime = NOW,
    snapshot_at: datetime | None = NOW,
    reconciled: bool | None = True,
    pending: bool | None = False,
) -> RiskSnapshot:
    return RiskSnapshot(
        now=now,
        strategy_target_lots=target,
        current_lots=current,
        price_rub=price,
        lot_size=lot_size,
        portfolio_equity_rub=equity,
        cash_rub=cash,
        securities_value_rub=(
            None if equity is None or cash is None else equity - cash
        ),
        atr_rub=atr,
        stop_distance_rub=stop_distance,
        snapshot_at=snapshot_at,
        position_reconciled=reconciled,
        pending_order=pending,
        mode="SANDBOX_EXECUTION",
    )


def permissive_policy(**overrides) -> RiskPolicy:
    values = {
        "max_position_lots": 100,
        "max_position_value_rub": 1_000_000.0,
        "max_position_share_of_equity": 1.0,
        "max_order_value_rub": 1_000_000.0,
        "cash_reserve_rub": 0.0,
        "commission_buffer_fraction": 0.0,
        "risk_per_trade_rub": None,
        "risk_per_trade_fraction": None,
        "daily_loss_limit_rub": None,
        "daily_loss_limit_fraction": None,
        "weekly_loss_limit_rub": None,
        "weekly_loss_limit_fraction": None,
        "max_drawdown_fraction": None,
        "max_daily_turnover_rub": None,
        "max_orders_per_day": None,
        "max_snapshot_age_seconds": 300,
    }
    values.update(overrides)
    return RiskPolicy(**values)


def q(value: float) -> dict[str, int]:
    units = int(value)
    nano = round((value - units) * 1_000_000_000)
    return {"units": units, "nano": nano}


def test_policy_hash_is_stable_and_sensitive():
    a = RiskPolicy()
    b = RiskPolicy()
    c = replace(a, cash_reserve_rub=a.cash_reserve_rub + 1)
    assert a.policy_hash == b.policy_hash
    assert a.policy_hash != c.policy_hash
    assert len(a.policy_hash) == 64


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_position_lots": -1},
        {"max_position_value_rub": 0},
        {"max_position_share_of_equity": 1.1},
        {"max_cash_usage_fraction": 0},
        {"commission_buffer_fraction": 1},
        {"atr_multiplier": 0},
        {"max_orders_per_day": 0},
        {"max_snapshot_age_seconds": -1},
    ],
)
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        RiskPolicy(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"strategy_target_lots": -1},
        {"current_lots": -1},
        {"lot_size": 0},
        {"price_rub": 0},
        {"atr_rub": -1},
        {"stop_distance_rub": 0},
    ],
)
def test_snapshot_validation(kwargs):
    values = asdict(snapshot())
    values.update(kwargs)
    with pytest.raises(RiskInputError):
        RiskSnapshot(**values)


def test_state_round_trip():
    state = RiskState(
        daily_date="2026-07-22",
        daily_turnover_rub=123.45,
        daily_order_count=2,
        recorded_execution_ids=("A", "B"),
    )
    assert RiskState.from_dict(state.to_dict()) == state


def test_portfolio_input_parser():
    result = portfolio_risk_inputs(
        {
            "totalAmountPortfolio": q(50_000.25),
            "totalAmountCurrencies": q(40_000.10),
            "totalAmountShares": q(7_000.05),
            "totalAmountBonds": q(3_000.10),
        }
    )
    assert result["equity_rub"] == pytest.approx(50_000.25)
    assert result["cash_rub"] == pytest.approx(40_000.10)
    assert result["securities_value_rub"] == pytest.approx(10_000.15)


def test_average_true_range_known_series():
    index = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
    candles = pd.DataFrame(
        {
            "high": [101 + i for i in range(30)],
            "low": [99 + i for i in range(30)],
            "close": [100 + i for i in range(30)],
        },
        index=index,
    )
    assert average_true_range(candles, 20) == pytest.approx(2.0)


def test_passes_permissive_policy():
    assessment = RiskEngine(permissive_policy()).evaluate(snapshot(target=3))
    assert assessment.decision.status == "PASS"
    assert assessment.decision.approved_target_lots == 3
    assert assessment.decision.approved_action == "BUY"


def test_disabled_policy_passes_strategy_target_through():
    policy = replace(permissive_policy(), enabled=False, max_position_lots=0)
    result = RiskEngine(policy).evaluate(snapshot(target=7))
    assert result.decision.status == "DISABLED"
    assert result.decision.approved_target_lots == 7


def test_max_position_lots_adjusts_target():
    policy = permissive_policy(max_position_lots=2)
    result = RiskEngine(policy).evaluate(snapshot(target=5))
    assert result.decision.status == "ADJUSTED"
    assert result.decision.approved_target_lots == 2
    assert "MAX_POSITION_LOTS" in result.decision.breaches


def test_position_value_and_share_caps():
    policy = permissive_policy(
        max_position_value_rub=7_500,
        max_position_share_of_equity=0.10,
    )
    result = RiskEngine(policy).evaluate(snapshot(target=10, equity=50_000))
    # One lot is 2,500 RUB; value cap = 3, share cap = 2.
    assert result.decision.lot_caps["MAX_POSITION_VALUE"] == 3
    assert result.decision.lot_caps["MAX_POSITION_SHARE"] == 2
    assert result.decision.approved_target_lots == 2


def test_cash_reserve_is_incremental_for_existing_position():
    policy = permissive_policy(cash_reserve_rub=47_000)
    result = RiskEngine(policy).evaluate(
        snapshot(target=4, current=1, cash=50_000)
    )
    # Spendable cash buys one additional 2,500 RUB lot.
    assert result.decision.lot_caps["CASH_RESERVE"] == 2
    assert result.decision.approved_target_lots == 2


def test_order_value_is_incremental_for_existing_position():
    policy = permissive_policy(max_order_value_rub=2_500)
    result = RiskEngine(policy).evaluate(snapshot(target=5, current=2))
    assert result.decision.lot_caps["MAX_ORDER_VALUE"] == 3
    assert result.decision.approved_target_lots == 3


def test_atr_risk_budget_caps_lots():
    policy = permissive_policy(risk_per_trade_rub=1_000)
    result = RiskEngine(policy).evaluate(snapshot(target=20, price=100, atr=5))
    # ATR 5 * 3 * lot size 10 = 150 RUB risk per lot.
    assert result.decision.lot_caps["RISK_PER_TRADE"] == 6
    assert result.decision.approved_target_lots == 6
    assert result.decision.metrics["risk_distance_source"] == "ATR"


def test_explicit_stop_distance_has_priority_over_atr():
    policy = permissive_policy(risk_per_trade_rub=1_000)
    result = RiskEngine(policy).evaluate(
        snapshot(target=20, atr=100, stop_distance=2)
    )
    assert result.decision.lot_caps["RISK_PER_TRADE"] == 50
    assert result.decision.metrics["risk_distance_source"] == "STOP_DISTANCE"


def test_fractional_risk_budget_uses_equity():
    policy = permissive_policy(
        risk_per_trade_rub=None,
        risk_per_trade_fraction=0.01,
    )
    result = RiskEngine(policy).evaluate(snapshot(target=20, equity=50_000, atr=5))
    # 500 RUB budget / 150 RUB per lot = 3 lots.
    assert result.decision.lot_caps["RISK_PER_TRADE"] == 3


def test_unknown_risk_distance_fails_closed_for_new_exposure():
    policy = permissive_policy(
        risk_per_trade_rub=500,
        block_on_unknown_risk_distance=True,
    )
    result = RiskEngine(policy).evaluate(snapshot(target=1, atr=None))
    assert result.decision.status == "BLOCKED"
    assert "UNKNOWN_RISK_DISTANCE" in result.decision.breaches


def test_missing_snapshot_time_fails_closed():
    policy = permissive_policy(max_snapshot_age_seconds=60)
    result = RiskEngine(policy).evaluate(snapshot(target=1, snapshot_at=None))
    assert result.decision.status == "BLOCKED"
    assert "SNAPSHOT_TIME_UNKNOWN" in result.decision.breaches


def test_stale_snapshot_is_absolute_block():
    policy = permissive_policy(max_snapshot_age_seconds=60)
    stale = snapshot(
        target=0,
        current=1,
        now=NOW,
        snapshot_at=NOW - timedelta(seconds=61),
    )
    result = RiskEngine(policy).evaluate(stale)
    assert result.decision.status == "BLOCKED"
    assert result.decision.absolute_block is True
    assert result.decision.approved_target_lots == 1


@pytest.mark.parametrize(
    ("reconciled", "pending", "expected"),
    [
        (False, False, "POSITION_NOT_RECONCILED"),
        (None, False, "POSITION_NOT_RECONCILED"),
        (True, True, "PENDING_ORDER_ACTIVE"),
        (True, None, "PENDING_ORDER_STATE_UNKNOWN"),
    ],
)
def test_execution_ambiguity_blocks_all_orders(reconciled, pending, expected):
    policy = permissive_policy()
    result = RiskEngine(policy).evaluate(
        snapshot(
            target=0,
            current=1,
            reconciled=reconciled,
            pending=pending,
        )
    )
    assert result.decision.status == "BLOCKED"
    assert expected in result.decision.breaches
    assert result.decision.order_allowed is False


def test_kill_switch_blocks_buy_but_allows_reduce_only_sell():
    engine = RiskEngine(permissive_policy())
    halted, _ = engine.engage_kill_switch(
        RiskState(),
        now=NOW,
        reason="operator test",
    )
    buy = engine.evaluate(snapshot(target=1), halted)
    assert buy.decision.status == "BLOCKED"
    assert "KILL_SWITCH" in buy.decision.breaches

    sell = engine.evaluate(snapshot(target=0, current=1), buy.state)
    assert sell.decision.status == "REDUCTION_ALLOWED"
    assert sell.decision.approved_target_lots == 0
    assert sell.decision.reduce_only is True


def test_policy_can_disable_reduce_only_during_halt():
    policy = permissive_policy(allow_risk_reducing_orders_during_halt=False)
    engine = RiskEngine(policy)
    halted, _ = engine.engage_kill_switch(RiskState(), now=NOW, reason="test")
    sell = engine.evaluate(snapshot(target=0, current=1), halted)
    assert sell.decision.status == "BLOCKED"
    assert sell.decision.approved_target_lots == 1


def test_kill_switch_clear_requires_exact_confirmation():
    engine = RiskEngine(permissive_policy())
    halted, _ = engine.engage_kill_switch(RiskState(), now=NOW, reason="test")
    with pytest.raises(RiskInputError):
        engine.clear_kill_switch(halted, now=NOW, confirmation="clear")
    active, event = engine.clear_kill_switch(
        halted,
        now=NOW,
        confirmation="CLEAR RISK HALT",
    )
    assert active.kill_switch_active is False
    assert event.event_type == "KILL_SWITCH_DISABLED"


def test_daily_loss_limit_blocks_new_exposure():
    policy = permissive_policy(daily_loss_limit_rub=500)
    engine = RiskEngine(policy)
    first = engine.evaluate(snapshot(target=0, equity=50_000))
    second = engine.evaluate(
        snapshot(target=1, equity=49_400, now=NOW + timedelta(hours=1), snapshot_at=NOW + timedelta(hours=1)),
        first.state,
    )
    assert second.decision.metrics["daily_pnl_rub"] == pytest.approx(-600)
    assert second.decision.status == "BLOCKED"
    assert "DAILY_LOSS_LIMIT_RUB" in second.decision.breaches


def test_drawdown_limit_uses_high_watermark():
    policy = permissive_policy(max_drawdown_fraction=0.05)
    engine = RiskEngine(policy)
    first = engine.evaluate(snapshot(target=0, equity=50_000))
    high = engine.evaluate(
        snapshot(target=0, equity=60_000, now=NOW + timedelta(hours=1), snapshot_at=NOW + timedelta(hours=1)),
        first.state,
    )
    down = engine.evaluate(
        snapshot(target=1, equity=56_000, now=NOW + timedelta(hours=2), snapshot_at=NOW + timedelta(hours=2)),
        high.state,
    )
    assert down.decision.metrics["drawdown"] == pytest.approx(-4_000 / 60_000)
    assert "MAX_DRAWDOWN" in down.decision.breaches


def test_max_orders_per_day_blocks_new_entry_but_not_exit():
    policy = permissive_policy(max_orders_per_day=2)
    state = RiskState(
        daily_date="2026-07-22",
        daily_start_equity_rub=50_000,
        weekly_key="2026-W30",
        weekly_start_equity_rub=50_000,
        high_watermark_equity_rub=50_000,
        daily_order_count=2,
    )
    engine = RiskEngine(policy)
    buy = engine.evaluate(snapshot(target=1), state)
    assert buy.decision.status == "BLOCKED"
    assert "MAX_ORDERS_PER_DAY" in buy.decision.breaches
    sell = engine.evaluate(snapshot(target=0, current=1), state)
    assert sell.decision.status == "REDUCTION_ALLOWED"


def test_daily_turnover_remaining_caps_increment():
    policy = permissive_policy(max_daily_turnover_rub=10_000)
    state = RiskState(
        daily_date="2026-07-22",
        daily_start_equity_rub=50_000,
        weekly_key="2026-W30",
        weekly_start_equity_rub=50_000,
        high_watermark_equity_rub=50_000,
        daily_turnover_rub=7_500,
    )
    result = RiskEngine(policy).evaluate(snapshot(target=5), state)
    assert result.decision.lot_caps["DAILY_TURNOVER_REMAINING"] == 1
    assert result.decision.approved_target_lots == 1


def test_hold_above_cap_does_not_force_liquidation():
    policy = permissive_policy(max_position_lots=1)
    result = RiskEngine(policy).evaluate(snapshot(target=3, current=3))
    assert result.decision.approved_target_lots == 3
    assert result.decision.approved_action == "HOLD"


def test_engine_never_increases_strategy_target():
    policy = permissive_policy(max_position_lots=100)
    for target in range(5):
        result = RiskEngine(policy).evaluate(
            snapshot(target=target, current=min(target, 2))
        )
        assert result.decision.approved_target_lots <= max(target, result.decision.current_lots)
        if target >= result.decision.current_lots:
            assert result.decision.approved_target_lots <= target


def test_moscow_day_rollover_resets_turnover_and_order_count():
    policy = permissive_policy()
    engine = RiskEngine(policy)
    state = RiskState(
        daily_date="2026-07-22",
        weekly_key="2026-W30",
        daily_start_equity_rub=50_000,
        weekly_start_equity_rub=50_000,
        high_watermark_equity_rub=50_000,
        daily_turnover_rub=9_000,
        daily_order_count=3,
    )
    # 22:00 UTC is 01:00 Moscow on 23 July.
    next_day = datetime(2026, 7, 22, 22, tzinfo=timezone.utc)
    result = engine.evaluate(
        snapshot(
            target=0,
            now=next_day,
            snapshot_at=next_day,
            equity=49_000,
            cash=49_000,
        ),
        state,
    )
    assert result.state.daily_date == "2026-07-23"
    assert result.state.daily_turnover_rub == 0
    assert result.state.daily_order_count == 0
    assert result.decision.metrics["daily_pnl_rub"] == pytest.approx(0)
    assert any(e.event_type == "DAILY_BASELINE_INITIALIZED" for e in result.events)


def test_record_execution_is_idempotent_and_updates_turnover():
    engine = RiskEngine(permissive_policy())
    record = ExecutionRecord(
        execution_id="order-1-fill-1",
        executed_at=NOW,
        signed_lots=1,
        price_rub=250,
        lot_size=10,
        portfolio_equity_rub=50_000,
    )
    first = engine.record_execution(RiskState(), record)
    assert first.duplicate is False
    assert first.turnover_added_rub == pytest.approx(2_500)
    assert first.state.daily_order_count == 1
    assert first.state.daily_turnover_rub == pytest.approx(2_500)

    second = engine.record_execution(first.state, record)
    assert second.duplicate is True
    assert second.state == first.state
    assert second.turnover_added_rub == 0


def test_baseline_reset_requires_confirmation():
    engine = RiskEngine(permissive_policy())
    with pytest.raises(RiskInputError):
        engine.reset_baselines(
            RiskState(),
            now=NOW,
            equity_rub=50_000,
            confirmation="RESET",
        )
    reset, events = engine.reset_baselines(
        RiskState(daily_turnover_rub=10_000, daily_order_count=4),
        now=NOW,
        equity_rub=50_000,
        confirmation="RESET RISK BASELINES",
    )
    assert reset.daily_turnover_rub == 0
    assert reset.daily_order_count == 0
    assert events[0].event_type == "RISK_BASELINES_RESET"


def test_pristine_baseline_initialization_uses_fresh_equity_and_cash():
    initialized, event = RiskEngine(permissive_policy()).initialize_pristine_baselines(
        RiskState(),
        now=NOW,
        equity_rub=50_000,
        cash_rub=15_000,
        snapshot_at=NOW,
    )

    assert initialized.daily_date == "2026-07-22"
    assert initialized.daily_start_equity_rub == pytest.approx(50_000)
    assert initialized.weekly_start_equity_rub == pytest.approx(50_000)
    assert initialized.high_watermark_equity_rub == pytest.approx(50_000)
    assert initialized.last_cash_rub == pytest.approx(15_000)
    assert initialized.daily_turnover_rub == 0
    assert initialized.daily_order_count == 0
    assert event.event_type == "RISK_BASELINES_INITIALIZED"


def test_pristine_baseline_initialization_refuses_prior_state():
    engine = RiskEngine(permissive_policy())

    with pytest.raises(RiskInputError, match="pristine state"):
        engine.initialize_pristine_baselines(
            RiskState(daily_turnover_rub=1),
            now=NOW,
            equity_rub=50_000,
            cash_rub=15_000,
            snapshot_at=NOW,
        )


def test_profile_store_separates_modes_and_checks_checksum(tmp_path):
    store = RiskProfileStore(tmp_path / "risk_profiles.json")
    dry = RiskPolicy(max_position_lots=1)
    sandbox = RiskPolicy(max_position_lots=2)
    store.save_profile("DRY_RUN", dry)
    store.save_profile("SANDBOX_EXECUTION", sandbox)
    assert store.require_profile("DRY_RUN")["policy"] == dry
    assert store.require_profile("SANDBOX_EXECUTION")["policy"] == sandbox

    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["profiles"]["SANDBOX_EXECUTION"]["policy"]["max_position_lots"] = 9
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RiskPersistenceError, match="checksum"):
        store.load_profile("SANDBOX_EXECUTION")


def test_profile_store_rejects_missing_fields(tmp_path):
    store = RiskProfileStore(tmp_path / "risk_profiles.json")
    store.save_profile("DRY_RUN", RiskPolicy())
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    del raw["profiles"]["DRY_RUN"]["policy"]["max_position_lots"]
    # Recompute is intentionally omitted: either completeness or checksum must fail.
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RiskPersistenceError, match="incomplete"):
        store.load_profile("DRY_RUN")


def test_state_store_is_per_account_and_atomic_shape(tmp_path):
    store = RiskStateStore(tmp_path / "risk_state.json")
    store.save_account("A", RiskState(daily_order_count=1))
    store.save_account("B", RiskState(daily_order_count=2))
    assert store.load_account("A").daily_order_count == 1
    assert store.load_account("B").daily_order_count == 2
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["version"] == RiskStateStore.SCHEMA_VERSION
    assert set(raw["accounts"]) == {"A", "B"}


def test_corrupt_state_store_fails_closed(tmp_path):
    path = tmp_path / "risk_state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RiskPersistenceError, match="cannot be read"):
        RiskStateStore(path).load_account("A")


def test_state_store_migrates_alpha3_v1_document(tmp_path):
    path = tmp_path / "risk_state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {
                    "A": {
                        "version": 1,
                        "daily_order_count": 2,
                        "daily_turnover_rub": 123.45,
                        "recorded_execution_ids": ["legacy-exec"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store = RiskStateStore(path)
    state = store.load_account("A")
    assert state.version == 3
    assert state.daily_order_count == 2
    assert state.recorded_execution_ids == ("legacy-exec",)
    assert state.risk_resync_required is False

    store.save_account("A", state)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == RiskStateStore.SCHEMA_VERSION
    assert migrated["accounts"]["A"]["version"] == 3
    assert migrated["accounts"]["A"]["risk_resync_required"] is False


def test_external_activity_requires_explicit_risk_resync():
    engine = RiskEngine(permissive_policy())
    marked, event = engine.mark_external_activity(
        RiskState(),
        now=NOW,
        reason="manual broker drift",
        source="BROKER_POSITION_DRIFT",
    )
    assert marked.risk_resync_required is True
    assert event.event_type == "EXTERNAL_ACTIVITY_DETECTED"

    assessment = engine.evaluate(
        snapshot(target=0, current=1),
        marked,
    )
    assert assessment.decision.status == "BLOCKED"
    assert "RISK_RESYNC_REQUIRED" in assessment.decision.breaches

    reset, events = engine.reset_baselines(
        marked,
        now=NOW,
        equity_rub=50_000.0,
        confirmation="RESET RISK BASELINES",
    )
    assert reset.risk_resync_required is False
    assert [item.event_type for item in events] == [
        "RISK_BASELINES_RESET",
        "RISK_RESYNC_COMPLETED",
    ]


def test_future_snapshot_remains_absolute_block_and_policy_block_is_warning():
    policy = permissive_policy(max_snapshot_age_seconds=60)
    future = snapshot(
        target=1,
        now=NOW,
        snapshot_at=NOW + timedelta(seconds=120),
    )
    assessment = RiskEngine(policy).evaluate(future)
    assert assessment.decision.status == "BLOCKED"
    assert assessment.decision.absolute_block is True
    assert "SNAPSHOT_FROM_FUTURE" in assessment.decision.breaches
    evaluated = [
        event for event in assessment.events if event.event_type == "RISK_EVALUATED"
    ][-1]
    assert evaluated.severity == "WARNING"
    assert evaluated.details["block_kind"] == "SAFETY_GATE"
    assert evaluated.details["expected_policy_block"] is False


def test_daily_order_limit_is_expected_policy_block_not_runtime_error():
    policy = permissive_policy(max_orders_per_day=1)
    state = replace(
        RiskState(daily_date="2026-07-22", weekly_key="2026-W30"),
        daily_order_count=1,
    )
    assessment = RiskEngine(policy).evaluate(snapshot(target=1), state)
    assert assessment.decision.status == "BLOCKED"
    assert assessment.decision.absolute_block is False
    assert "MAX_ORDERS_PER_DAY" in assessment.decision.breaches
    evaluated = [
        event for event in assessment.events if event.event_type == "RISK_EVALUATED"
    ][-1]
    assert evaluated.severity == "WARNING"
    assert evaluated.details["block_kind"] == "POLICY_LIMIT"
    assert evaluated.details["expected_policy_block"] is True
    assert assessment.decision.metrics["max_orders_per_day"] == 1
