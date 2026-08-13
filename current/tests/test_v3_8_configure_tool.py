from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from tools import v3_8_configure as configure
from trading_robot.bot import BotConfig
from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.config_persistence import (
    StrategyProfileStore,
    bot_config_to_profile,
)
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_model import PortfolioState
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore

ACCOUNT = "sandbox-account-1"


def prepare_runtime(root: Path) -> None:
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        shadow_strategies=(),
        fast_window=2,
        slow_window=5,
        volatility_window=5,
        lookback_days=5,
        max_order_lots=1,
    )
    StrategyProfileStore(root / "strategy_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        bot_config_to_profile(
            config,
            connect_timeout_seconds=8,
            read_timeout_seconds=25,
        ),
    )
    CentralOrderStore(root / "central_order_state.json").initialize(ACCOUNT)
    PortfolioRepository(root / "portfolio_state.json").save(
        PortfolioState.empty(account_id=ACCOUNT)
    )
    RiskProfileStore(root / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope=ACCOUNT,
        source="V3_8_CONFIG_TEST",
    )


class FakeClient:
    IDS: ClassVar[dict[tuple[str, str], str]] = {
        ("SBER", "TQBR"): "uid-sber",
        ("LKOH", "TQBR"): "uid-lkoh",
        ("YDEX", "TQBR"): "uid-ydex",
    }

    def __init__(self, token, **kwargs):
        self.token = token
        self.post_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def find_instrument(self, ticker: str, class_code: str):
        return {
            "uid": self.IDS[(ticker, class_code)],
            "ticker": ticker,
            "classCode": class_code,
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]


def args(
    root: Path,
    action: str,
    *,
    confirmation: str = "",
    max_order_lots: int | None = None,
):
    values = [
        action,
        "--runtime-dir",
        str(root),
        "--account-id",
        ACCOUNT,
        "--instrument",
        "SBER,TQBR,CANDLE_INTERVAL_HOUR",
        "--instrument",
        "LKOH,TQBR,CANDLE_INTERVAL_30_MIN",
        "--confirm",
        confirmation,
    ]
    if max_order_lots is not None:
        values.extend(("--max-order-lots", str(max_order_lots)))
    return configure.parse_args(values)


def test_preview_resolves_profiles_without_writing_v3_8_runtime(tmp_path: Path):
    prepare_runtime(tmp_path)

    result = configure.run(
        args(tmp_path, "preview"),
        environ={configure.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )

    assert result["status"] == "PREVIEW"
    assert result["writes_performed"] is False
    assert {item["instrument_id"] for item in result["instruments"]} == {
        "uid-sber",
        "uid-lkoh",
    }
    assert {item["max_order_lots"] for item in result["instruments"]} == {1}
    assert not (tmp_path / "multi_instrument_profiles.json").exists()
    assert not (tmp_path / "instrument_runtimes.json").exists()
    assert ACCOUNT not in json.dumps(result)
    assert result["account_fingerprint"] == configure._fingerprint(ACCOUNT)
    assert "secret-canary" not in str(result)


def test_apply_requires_exact_confirmation_before_secret_or_network(
    monkeypatch,
    tmp_path: Path,
):
    prepare_runtime(tmp_path)
    monkeypatch.setattr(
        configure,
        "preferred_secret_provider",
        lambda _path: pytest.fail("secret provider must not be read"),
    )

    with pytest.raises(RuntimeError, match="exact v3.8 configuration"):
        configure.run(
            args(tmp_path, "apply", confirmation="WRONG"),
            environ={},
            client_factory=lambda *a, **k: pytest.fail("network client created"),
        )


def test_apply_creates_stopped_checksummed_profiles_and_runtimes(tmp_path: Path):
    prepare_runtime(tmp_path)

    result = configure.run(
        args(
            tmp_path,
            "apply",
            confirmation=configure.APPLY_CONFIRMATION,
        ),
        environ={configure.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )

    profiles = MultiInstrumentProfileStore(
        tmp_path / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    runtimes = InstrumentRuntimeStore(
        tmp_path / "instrument_runtimes.json"
    ).load(expected_account_id=ACCOUNT)
    assert result["status"] == "CONFIGURED"
    assert result["writes_performed"] is True
    assert {item.ticker for item in profiles} == {"SBER", "LKOH"}
    assert {item.candle_interval for item in profiles} == {
        "CANDLE_INTERVAL_HOUR",
        "CANDLE_INTERVAL_30_MIN",
    }
    assert {item.status for item in runtimes} == {"STOPPED"}
    assert (tmp_path / "multi_instrument_profiles.json.sha256").is_file()
    assert (tmp_path / "instrument_runtimes.json.sha256").is_file()
    assert ACCOUNT not in json.dumps(result)
    assert all(
        len(item["runtime_key_fingerprint"]) == 12
        for item in result["runtime_statuses"]
    )
    assert "secret-canary" not in str(result)


def test_apply_can_create_explicit_static_multi_lot_profiles(tmp_path: Path):
    prepare_runtime(tmp_path)

    preview = configure.run(
        args(tmp_path, "preview", max_order_lots=5),
        environ={configure.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )
    applied = configure.run(
        args(
            tmp_path,
            "apply",
            confirmation=configure.APPLY_CONFIRMATION,
            max_order_lots=5,
        ),
        environ={configure.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )

    profiles = MultiInstrumentProfileStore(
        tmp_path / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    assert {item["max_order_lots"] for item in preview["instruments"]} == {5}
    assert {item["max_order_lots"] for item in applied["instruments"]} == {5}
    assert {
        item.to_bot_config(
            mode="SANDBOX_EXECUTION",
            state_file="robot_state.json",
            journal_file="trading_events.db",
        ).max_order_lots
        for item in profiles
    } == {5}


def test_explicit_multi_lot_limit_is_validated_before_secret_or_network(
    monkeypatch,
    tmp_path: Path,
):
    prepare_runtime(tmp_path)
    monkeypatch.setattr(
        configure,
        "preferred_secret_provider",
        lambda _path: pytest.fail("secret provider must not be read"),
    )

    with pytest.raises(RuntimeError, match="between 1 and 100"):
        configure.run(
            args(tmp_path, "preview", max_order_lots=0),
            environ={},
            client_factory=lambda *a, **k: pytest.fail("network client created"),
        )


def test_apply_refuses_to_overwrite_existing_v3_8_files(tmp_path: Path):
    prepare_runtime(tmp_path)
    configure.run(
        args(
            tmp_path,
            "apply",
            confirmation=configure.APPLY_CONFIRMATION,
        ),
        environ={configure.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )

    with pytest.raises(RuntimeError, match="refuses to overwrite"):
        configure.run(
            args(
                tmp_path,
                "apply",
                confirmation=configure.APPLY_CONFIRMATION,
            ),
            environ={configure.TOKEN_KEY: "secret-canary"},
            client_factory=FakeClient,
        )


def test_apply_recovers_matching_profile_when_runtime_write_was_missing(
    tmp_path: Path,
):
    prepare_runtime(tmp_path)
    configure.run(
        args(
            tmp_path,
            "apply",
            confirmation=configure.APPLY_CONFIRMATION,
        ),
        environ={configure.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )
    runtime_path = tmp_path / "instrument_runtimes.json"
    runtime_path.unlink()
    runtime_path.with_name(runtime_path.name + ".sha256").unlink()

    recovered = configure.run(
        args(
            tmp_path,
            "apply",
            confirmation=configure.APPLY_CONFIRMATION,
        ),
        environ={configure.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )

    assert recovered["status"] == "CONFIGURED"
    assert recovered["recovered_matching_profile"] is True
    assert InstrumentRuntimeStore(runtime_path).load(
        expected_account_id=ACCOUNT
    )
