from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools import v3_8_prepare_runtime as prepare
from trading_robot.bot import BotConfig
from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.config_persistence import (
    StrategyProfileStore,
    bot_config_to_profile,
)
from trading_robot.portfolio_model import PortfolioState
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.runtime_backup import RuntimeBackupManager

ACCOUNT = "sandbox-account-v3-8"


def make_source(root: Path, *, risk_scope: str | None = None) -> None:
    PortfolioRepository(root / "portfolio_state.json").save(
        PortfolioState.empty(account_id=ACCOUNT)
    )
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
    RiskProfileStore(root / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope=risk_scope,
        source="ACCEPTED_V3_7_BASELINE",
    )
    (root / ".env").write_text("TBANK_SANDBOX_TOKEN=must-not-copy\n")


def args(source: Path, target: Path, action: str, *, confirm: str = ""):
    return prepare.parse_args(
        [
            action,
            "--source-runtime-dir",
            str(source),
            "--runtime-dir",
            str(target),
            "--confirm",
            confirm,
        ]
    )


def test_preview_reports_unscoped_upgrade_without_writes(tmp_path: Path):
    source = tmp_path / "accepted"
    target = tmp_path / "isolated"
    make_source(source)

    result = prepare.run(args(source, target, "preview"))

    assert result["status"] == "PREVIEW"
    assert result["source_risk_scope"] == "UNSCOPED"
    assert result["writes_performed"] is False
    assert result["secrets_copied"] is False
    assert result["target_existed_before"] is False
    assert result["target_exists"] is False
    assert ACCOUNT not in json.dumps(result)
    assert not target.exists()


def test_apply_requires_exact_confirmation_without_target_write(tmp_path: Path):
    source = tmp_path / "accepted"
    target = tmp_path / "isolated"
    make_source(source)

    with pytest.raises(RuntimeError, match="exact isolated-runtime"):
        prepare.run(args(source, target, "apply", confirm="WRONG"))

    assert not target.exists()


def test_apply_creates_isolated_scoped_clean_runtime(tmp_path: Path):
    source = tmp_path / "accepted"
    target = tmp_path / "isolated"
    make_source(source)
    source_portfolio = (source / "portfolio_state.json").read_bytes()
    source_risk = (source / "risk_profiles.json").read_bytes()

    result = prepare.run(
        args(
            source,
            target,
            "apply",
            confirm=prepare.APPLY_CONFIRMATION,
        )
    )

    assert result["status"] == "PREPARED"
    assert result["writes_performed"] is True
    assert result["target_existed_before"] is False
    assert result["target_exists"] is True
    assert not (target / ".env").exists()
    assert not (target / "trading_events.db").exists()
    assert not (target / "robot_state.json").exists()
    assert (source / "portfolio_state.json").read_bytes() == source_portfolio
    assert (source / "risk_profiles.json").read_bytes() == source_risk
    PortfolioRepository(target / "portfolio_state.json").load(
        expected_account_id=ACCOUNT
    )
    assert StrategyProfileStore(target / "strategy_profiles.json").load_profile(
        "SANDBOX_EXECUTION"
    )
    risk = RiskProfileStore(target / "risk_profiles.json").require_profile(
        "SANDBOX_EXECUTION"
    )
    assert risk["account_scope"] == ACCOUNT
    assert RiskStateStore(target / "risk_state.json").load_account(
        ACCOUNT
    ) == RiskState()
    assert not CentralOrderStore(target / "central_order_state.json").load(
        expected_account_id=ACCOUNT
    ).intents
    for name in (
        "portfolio_state.json",
        "central_order_state.json",
        prepare.MANIFEST_NAME,
    ):
        assert (target / f"{name}.sha256").is_file()


def test_apply_refuses_overwrite(tmp_path: Path):
    source = tmp_path / "accepted"
    target = tmp_path / "isolated"
    make_source(source)
    target.mkdir()

    with pytest.raises(RuntimeError, match="overwrite refused"):
        prepare.run(
            args(
                source,
                target,
                "apply",
                confirm=prepare.APPLY_CONFIRMATION,
            )
        )


def test_verified_backup_includes_seed_manifest(tmp_path: Path):
    source = tmp_path / "accepted"
    target = tmp_path / "isolated"
    make_source(source)
    prepare.run(
        args(
            source,
            target,
            "apply",
            confirm=prepare.APPLY_CONFIRMATION,
        )
    )
    manager = RuntimeBackupManager(target, app_version="v3.8-dev")

    backup = manager.create_backup(tmp_path / "runtime.zip")
    verification = manager.verify_backup(backup)

    assert verification.valid
    assert verification.manifest is not None
    names = {item["name"] for item in verification.manifest["entries"]}
    assert prepare.MANIFEST_NAME in names


def test_preview_rejects_risk_scope_for_another_account(tmp_path: Path):
    source = tmp_path / "accepted"
    target = tmp_path / "isolated"
    make_source(source, risk_scope="different-account")

    with pytest.raises(RuntimeError, match="belongs to another account"):
        prepare.run(args(source, target, "preview"))


def test_preview_rejects_overlapping_source_and_target(tmp_path: Path):
    source = tmp_path / "accepted"
    make_source(source)

    with pytest.raises(RuntimeError, match="must not overlap"):
        prepare.run(args(source, source / "nested", "preview"))
