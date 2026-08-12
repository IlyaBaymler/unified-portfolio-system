from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_robot.bot import BotConfig
from trading_robot.config_persistence import (
    StrategyProfileError,
    StrategyProfileStore,
    bot_config_to_profile,
    canonical_profile_hash,
    profile_to_bot_config,
)


def make_profile(*, primary: str, interval: str) -> dict:
    config = BotConfig(
        primary_strategy=primary,
        shadow_strategies=("donchian", "ensemble"),
        candle_interval=interval,
        portfolio_reconcile_interval_seconds=900,
    )
    return bot_config_to_profile(
        config,
        connect_timeout_seconds=8,
        read_timeout_seconds=25,
    )


def test_dry_run_and_execution_profiles_are_independent(tmp_path: Path):
    store = StrategyProfileStore(tmp_path / "strategy_profiles.json")
    dry = make_profile(primary="sma", interval="CANDLE_INTERVAL_HOUR")
    execution = make_profile(
        primary="donchian",
        interval="CANDLE_INTERVAL_10_MIN",
    )

    store.save_profile("DRY_RUN", dry)
    store.save_profile("SANDBOX_EXECUTION", execution)

    loaded_dry = store.load_profile("DRY_RUN")
    loaded_execution = store.load_profile("SANDBOX_EXECUTION")
    assert loaded_dry is not None
    assert loaded_execution is not None
    assert loaded_dry["config"]["primary_strategy"] == "sma"
    assert loaded_execution["config"]["primary_strategy"] == "donchian"
    assert loaded_dry["config_hash"] != loaded_execution["config_hash"]


def test_profile_round_trip_recreates_bot_config(tmp_path: Path):
    store = StrategyProfileStore(tmp_path / "profiles.json")
    profile = make_profile(primary="ensemble", interval="CANDLE_INTERVAL_DAY")
    saved = store.save_profile("SANDBOX_EXECUTION", profile)
    loaded = store.load_profile("SANDBOX_EXECUTION")
    assert loaded is not None
    config = profile_to_bot_config(
        loaded["config"],
        mode="SANDBOX_EXECUTION",
        state_file=str(tmp_path / "state.json"),
        journal_file=str(tmp_path / "events.db"),
    )
    assert config.primary_strategy == "ensemble"
    assert config.candle_interval == "CANDLE_INTERVAL_DAY"
    assert config.dry_run is False
    assert saved["config_hash"] == canonical_profile_hash(profile)


def test_corrupt_profile_file_is_fail_closed(tmp_path: Path):
    path = tmp_path / "profiles.json"
    path.write_text("{broken", encoding="utf-8")
    store = StrategyProfileStore(path)
    with pytest.raises(StrategyProfileError):
        store.load_document()


def test_profile_checksum_mismatch_is_rejected(tmp_path: Path):
    path = tmp_path / "profiles.json"
    profile = make_profile(primary="sma", interval="CANDLE_INTERVAL_HOUR")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_selected_mode": "DRY_RUN",
                "profiles": {
                    "DRY_RUN": {
                        "config": profile,
                        "config_hash": "wrong",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StrategyProfileError):
        StrategyProfileStore(path).load_profile("DRY_RUN")



def test_profile_without_checksum_is_rejected(tmp_path: Path):
    path = tmp_path / "profiles.json"
    profile = make_profile(primary="sma", interval="CANDLE_INTERVAL_HOUR")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_selected_mode": "DRY_RUN",
                "profiles": {
                    "DRY_RUN": {
                        "config": profile,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StrategyProfileError, match="no checksum"):
        StrategyProfileStore(path).load_profile("DRY_RUN")

def test_profile_rejects_secret_fields(tmp_path: Path):
    store = StrategyProfileStore(tmp_path / "profiles.json")
    with pytest.raises(StrategyProfileError):
        store.save_profile(
            "DRY_RUN",
            {"primary_strategy": "sma", "token": "secret"},
        )


def test_corrupt_profile_can_only_be_recreated_explicitly(tmp_path: Path):
    path = tmp_path / "profiles.json"
    path.write_text("{broken", encoding="utf-8")
    store = StrategyProfileStore(path)

    with pytest.raises(StrategyProfileError):
        store.reset_profile("SANDBOX_EXECUTION")

    store.reset_profile(
        "SANDBOX_EXECUTION",
        force_recreate_document=True,
    )
    document = store.load_document()
    assert document["profiles"] == {}
    assert document["last_selected_mode"] == "DRY_RUN"


def test_profile_load_rejects_nested_secret_even_with_valid_checksum(tmp_path: Path):
    path = tmp_path / "profiles.json"
    profile = make_profile(primary="sma", interval="CANDLE_INTERVAL_HOUR")
    profile["metadata"] = {"token": "must-not-be-here"}
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_selected_mode": "DRY_RUN",
                "profiles": {
                    "DRY_RUN": {
                        "config": profile,
                        "config_hash": canonical_profile_hash(profile),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StrategyProfileError, match="forbidden"):
        StrategyProfileStore(path).load_profile("DRY_RUN")


def test_invalid_last_selected_mode_is_fail_closed(tmp_path: Path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_selected_mode": "UNKNOWN_MODE",
                "profiles": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(StrategyProfileError, match="last_selected_mode"):
        StrategyProfileStore(path).load_document()
