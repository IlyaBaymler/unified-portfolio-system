from __future__ import annotations

from pathlib import Path

import pytest

import run_bot
from trading_robot.bot import BotConfig
from trading_robot.config_persistence import (
    StrategyProfileStore,
    bot_config_to_profile,
)


def _set_common_paths(monkeypatch, tmp_path: Path) -> Path:
    profile_path = tmp_path / "strategy_profiles.json"
    monkeypatch.setenv("ROBOT_STRATEGY_PROFILE_FILE", str(profile_path))
    monkeypatch.setenv("ROBOT_STATE_FILE", str(tmp_path / "robot_state.json"))
    monkeypatch.setenv("ROBOT_JOURNAL_FILE", str(tmp_path / "events.db"))
    return profile_path


def test_cli_dry_run_migrates_legacy_env_once(monkeypatch, tmp_path: Path):
    profile_path = _set_common_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROBOT_PRIMARY_STRATEGY", "donchian")
    monkeypatch.setenv("ROBOT_SHADOW_STRATEGIES", "sma,ensemble")
    monkeypatch.setenv("ROBOT_CANDLE_INTERVAL", "CANDLE_INTERVAL_10_MIN")
    monkeypatch.setenv("TBANK_CONNECT_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("TBANK_READ_TIMEOUT_SECONDS", "31")

    config, connect_timeout, read_timeout, config_hash = (
        run_bot.load_cli_bot_config(execute=False)
    )

    assert config.primary_strategy == "donchian"
    assert config.shadow_strategies == ("sma", "ensemble")
    assert config.candle_interval == "CANDLE_INTERVAL_10_MIN"
    assert config.dry_run is True
    assert connect_timeout == 7
    assert read_timeout == 31
    assert len(config_hash) == 64
    store = StrategyProfileStore(profile_path)
    assert store.load_profile("DRY_RUN") is not None
    assert store.load_profile("SANDBOX_EXECUTION") is None


def test_cli_execution_does_not_fall_back_without_saved_profile(
    monkeypatch,
    tmp_path: Path,
):
    _set_common_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROBOT_PRIMARY_STRATEGY", "sma")

    with pytest.raises(SystemExit, match="required for Sandbox execution"):
        run_bot.load_cli_bot_config(execute=True)


def test_cli_execution_uses_saved_profile_not_legacy_env(
    monkeypatch,
    tmp_path: Path,
):
    profile_path = _set_common_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ROBOT_PRIMARY_STRATEGY", "sma")
    saved_config = BotConfig(
        primary_strategy="ensemble",
        shadow_strategies=("donchian",),
        candle_interval="CANDLE_INTERVAL_10_MIN",
        order_type="BESTPRICE",
        time_in_force="FILL_AND_KILL",
    )
    store = StrategyProfileStore(profile_path)
    store.save_profile(
        "SANDBOX_EXECUTION",
        bot_config_to_profile(
            saved_config,
            connect_timeout_seconds=6.5,
            read_timeout_seconds=29.0,
        ),
    )

    config, connect_timeout, read_timeout, config_hash = (
        run_bot.load_cli_bot_config(execute=True)
    )

    assert config.primary_strategy == "ensemble"
    assert config.shadow_strategies == ("donchian",)
    assert config.candle_interval == "CANDLE_INTERVAL_10_MIN"
    assert config.dry_run is False
    assert config.order_type == "BESTPRICE"
    assert config.time_in_force == "FILL_AND_KILL"
    assert connect_timeout == 6.5
    assert read_timeout == 29.0
    assert len(config_hash) == 64
