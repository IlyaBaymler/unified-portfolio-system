from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from trading_robot.strategy import generate_strategy_signals
from trading_robot.strategy_runtime import (
    STRATEGY_VERSIONS,
    StrategyDecision,
    StrategySuiteConfig,
    compare_strategy_decisions,
    evaluate_strategy_decision,
    evaluate_strategy_suite,
)


def make_candles(periods: int = 320) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=periods, freq="h", tz="UTC")
    trend = np.linspace(100.0, 220.0, periods)
    wave = 2.5 * np.sin(np.arange(periods) / 7.0)
    close = trend + wave
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 100_000,
        },
        index=index,
    )


def test_strategy_config_hash_is_stable_and_parameter_sensitive():
    suite = StrategySuiteConfig(primary_strategy="sma")
    same = StrategySuiteConfig(primary_strategy="sma")
    changed = replace(suite, sma_slow_window=60)
    resized = replace(suite, position_limit_lots=3)

    assert suite.config_hash("sma") == same.config_hash("sma")
    assert suite.suite_hash() == same.suite_hash()
    assert suite.config_hash("sma") != changed.config_hash("sma")
    assert suite.config_hash("sma") != resized.config_hash("sma")
    assert suite.suite_hash() != changed.suite_hash()
    assert suite.suite_hash() != resized.suite_hash()


def test_runtime_decision_matches_research_signal_for_every_strategy():
    candles = make_candles()
    suite = StrategySuiteConfig(
        primary_strategy="sma",
        shadow_strategies=("donchian", "ensemble"),
        sma_fast_window=10,
        sma_slow_window=30,
        donchian_entry_window=30,
        donchian_exit_window=10,
        donchian_atr_window=14,
        ensemble_sma_fast=20,
        ensemble_sma_slow=60,
        ensemble_momentum_window=40,
        ensemble_breakout_window=30,
        ensemble_vote_threshold=3,
    )

    decisions = evaluate_strategy_suite(candles, suite)
    assert set(decisions) == {"sma", "donchian", "ensemble"}

    for strategy, decision in decisions.items():
        evaluation_candles = candles.tail(suite.evaluation_bars(strategy))
        research_config = suite.build_strategy_config(
            strategy, evaluation_candles
        )
        frame = generate_strategy_signals(
            evaluation_candles, strategy, research_config
        )
        latest = frame.iloc[-1]
        assert decision.signal == int(latest["signal"])
        assert np.isclose(decision.target_weight, float(latest["target_weight"]))
        assert decision.config_hash == suite.config_hash(strategy)
        assert decision.strategy_version == STRATEGY_VERSIONS[strategy]


def test_only_selected_strategy_has_primary_role():
    suite = StrategySuiteConfig(
        primary_strategy="donchian",
        shadow_strategies=("sma", "ensemble", "donchian"),
        donchian_entry_window=30,
        donchian_exit_window=10,
    )
    decisions = evaluate_strategy_suite(make_candles(), suite)

    assert decisions["donchian"].role == "PRIMARY"
    assert decisions["sma"].role == "SHADOW"
    assert decisions["ensemble"].role == "SHADOW"
    assert suite.enabled_strategies == ("donchian", "sma", "ensemble")


def test_comparison_detects_divergence_from_primary():
    common = {
        "candle_time": "2026-01-01T10:00:00+00:00",
        "strategy_version": "1.0",
        "config_hash": "a" * 64,
        "target_weight": 1.0,
        "target_lots": 1,
        "reason": "test",
        "indicators": {},
        "bars_used": 100,
        "required_bars": 50,
        "stop_level": None,
    }
    decisions = {
        "sma": StrategyDecision(
            strategy_id="sma", role="PRIMARY", signal=1, **common
        ),
        "donchian": StrategyDecision(
            strategy_id="donchian", role="SHADOW", signal=0, **common
        ),
    }

    result = compare_strategy_decisions(decisions, "sma")
    assert result["event_type"] == "DIVERGENCE"
    assert result["all_agree"] is False
    assert result["disagreeing_strategies"] == ["donchian"]


def test_volatility_target_scales_lots_but_never_exceeds_limit():
    candles = make_candles()
    suite = StrategySuiteConfig(
        primary_strategy="sma",
        sma_fast_window=5,
        sma_slow_window=20,
        annual_target_volatility=0.05,
        volatility_window=10,
        max_weight=1.0,
        position_limit_lots=10,
    )
    decision = evaluate_strategy_decision(
        candles, suite, "sma"
    )

    assert 0 <= decision.target_lots <= 10
    assert 0.0 <= decision.target_weight <= 1.0
    if decision.signal == 1:
        assert decision.target_lots >= 1


def test_shadow_selection_does_not_change_primary_history_window_or_decision():
    candles = make_candles(periods=500)
    primary_only = StrategySuiteConfig(
        primary_strategy="sma",
        sma_fast_window=10,
        sma_slow_window=30,
    )
    with_shadow = StrategySuiteConfig(
        primary_strategy="sma",
        shadow_strategies=("ensemble",),
        sma_fast_window=10,
        sma_slow_window=30,
        ensemble_sma_fast=30,
        ensemble_sma_slow=200,
        ensemble_momentum_window=126,
        ensemble_breakout_window=100,
    )

    first = evaluate_strategy_decision(
        candles, primary_only, "sma"
    )
    second = evaluate_strategy_decision(
        candles, with_shadow, "sma"
    )

    assert first.signal == second.signal
    assert first.target_lots == second.target_lots
    assert first.bars_used == second.bars_used
    assert first.config_hash == second.config_hash


def test_runtime_decision_reports_reconstruction_window():
    candles = make_candles(periods=500)
    suite = StrategySuiteConfig(
        primary_strategy="donchian",
        donchian_entry_window=30,
        donchian_exit_window=10,
        donchian_atr_window=14,
    )
    decision = evaluate_strategy_decision(
        candles, suite, "donchian"
    )

    assert decision.required_bars == suite.required_bars("donchian")
    assert decision.bars_used == suite.evaluation_bars("donchian")
    assert decision.bars_used > decision.required_bars
