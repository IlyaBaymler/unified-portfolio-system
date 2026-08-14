from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_central_order_coordinator_v3_8 import ACCOUNT, NOW, profile

from tools import v3_9_prepare_shadow_runtime as prepare
from trading_robot.central_order_manager import (
    CentralOrderCandidate,
    CentralOrderManager,
    CentralOrderStore,
    ExecutionAuthorization,
)
from trading_robot.config_persistence import StrategyProfileStore
from trading_robot.instrument_runtime import InstrumentRuntime, InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_model import PortfolioState
from trading_robot.portfolio_preflight import PortfolioSnapshotLease
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.state_persistence import atomic_write_json


def make_source(
    root: Path,
    *,
    explicit_currency_metadata: bool = False,
) -> None:
    portfolio = PortfolioState.empty(account_id=ACCOUNT)
    PortfolioRepository(root / "portfolio_state.json").save(portfolio)
    selected = (
        profile("SBER", "CANDLE_INTERVAL_HOUR"),
        profile("LKOH", "CANDLE_INTERVAL_30_MIN"),
    )
    StrategyProfileStore(root / "strategy_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        selected[0].strategy_profile,
    )
    RiskProfileStore(root / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope=ACCOUNT,
        source="ACCEPTED_V3_8_TEST_RUNTIME",
    )
    MultiInstrumentProfileStore(
        root / "multi_instrument_profiles.json"
    ).save_mode("SANDBOX_EXECUTION", selected)
    InstrumentRuntimeStore(root / "instrument_runtimes.json").save(
        tuple(
            InstrumentRuntime(config=item.to_runtime_config(ACCOUNT), status="ACTIVE")
            for item in selected
        )
    )
    central = CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    for item in selected:
        lease = PortfolioSnapshotLease.from_state(portfolio, leased_at=NOW)
        requested = CentralOrderCandidate(
            account_id=ACCOUNT,
            instrument_id=item.instrument_id,
            ticker=item.ticker,
            runtime_key=f"runtime-{item.instrument_id}",
            runtime_config_hash="a" * 64,
            candle_interval=item.candle_interval,
            candle_time=NOW,
            strategy_id="sma",
            strategy_profile_hash="c" * 64,
            current_lots=0,
            target_lots=1,
            estimated_price_kopecks=10_000,
            lot_size=10,
            created_at=NOW,
        )
        approved = ExecutionAuthorization(
            account_id=ACCOUNT,
            instrument_id=item.instrument_id,
            authorized_target_lots=1,
            portfolio_revision=lease.revision,
            portfolio_decision_checksum=lease.decision_checksum,
            portfolio_document_checksum=lease.document_checksum,
            available_cash_kopecks=100_000_000,
            preflight_status="PASS",
            pending_order_ids=(),
            uncertain_order_ids=(),
            risk_status="PASS",
            risk_decision_id=f"risk-{item.instrument_id}",
            risk_policy_hash="b" * 64,
            risk_order_allowed=True,
            authorized_at=NOW,
        )
        queued = central.enqueue(
            requested,
            approved,
        )
        central.cancel_queued(queued.intent.intent_id, reason="test lot evidence")
    if explicit_currency_metadata:
        atomic_write_json(
            root / prepare.METADATA_NAME,
            {
                "version": 1,
                "instruments": [
                    {
                        "instrument_id": item.instrument_id,
                        "lot_size": 10,
                        "asset_class": "SHARE",
                        "currency": "RUB",
                    }
                    for item in selected
                ],
            },
            write_checksum=True,
        )
    (root / ".env").write_text("TBANK_SANDBOX_TOKEN=must-not-copy\n", encoding="utf-8")
    (root / "robot_debug.log").write_text("must-not-copy\n", encoding="utf-8")


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


def test_preview_is_read_only_and_redacts_account(tmp_path: Path) -> None:
    source = tmp_path / "accepted-v3-8"
    target = tmp_path / "isolated-v3-9"
    make_source(source)

    result = prepare.run(args(source, target, "preview"))

    assert result["status"] == "PREVIEW"
    assert result["instrument_count"] == 2
    assert result["instruments"] == ["LKOH", "SBER"]
    assert result["portfolio_policy_status"] == "CONFIGURATION_REQUIRED"
    assert result["writes_performed"] is False
    assert result["secrets_copied"] is False
    assert ACCOUNT not in json.dumps(result)
    assert not target.exists()


def test_apply_creates_clean_isolated_shadow_runtime(tmp_path: Path) -> None:
    source = tmp_path / "accepted-v3-8"
    target = tmp_path / "isolated-v3-9"
    make_source(source)
    source_bytes = {
        item.name: item.read_bytes()
        for item in source.iterdir()
        if item.is_file()
    }

    result = prepare.run(
        args(source, target, "apply", confirm=prepare.APPLY_CONFIRMATION)
    )

    assert result["status"] == "PREPARED"
    assert result["target_exists"] is True
    assert source_bytes == {
        item.name: item.read_bytes()
        for item in source.iterdir()
        if item.is_file()
    }
    for forbidden in (".env", "robot_gui.log", "robot_debug.log"):
        assert not (target / forbidden).exists()
    assert EventJournal(target / "trading_events.db", read_only=True).count() == 0
    assert not CentralOrderStore(target / "central_order_state.json").load(
        expected_account_id=ACCOUNT
    ).intents
    assert RiskStateStore(target / "risk_state.json").load_account(ACCOUNT) == RiskState()
    loaded_risk = RiskProfileStore(target / "risk_profiles.json").require_profile(
        "SANDBOX_EXECUTION"
    )
    assert loaded_risk["portfolio_policy_status"] == "CONFIGURATION_REQUIRED"
    assert loaded_risk["policy"].portfolio_policy_mode == "OBSERVE_ONLY"
    runtimes = InstrumentRuntimeStore(target / "instrument_runtimes.json").load(
        expected_account_id=ACCOUNT
    )
    assert len(runtimes) == 2
    assert all(item.status == "STOPPED" for item in runtimes)
    assert all(item.current_lots == 0 for item in runtimes)
    metadata = load_portfolio_risk_metadata(target / prepare.METADATA_NAME)
    assert set(metadata) == {"uid-sber", "uid-lkoh"}
    assert {item.lot_size for item in metadata.values()} == {10}
    assert {item.currency for item in metadata.values()} == {None}
    manifest = json.loads((target / prepare.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["central_order_history_empty"] is True
    assert manifest["broker_order_history_copied"] is False
    for name in (prepare.METADATA_NAME, prepare.MANIFEST_NAME):
        assert (target / f"{name}.sha256").is_file()


def test_apply_requires_confirmation_and_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "accepted-v3-8"
    target = tmp_path / "isolated-v3-9"
    make_source(source)

    with pytest.raises(RuntimeError, match="exact v3.9 shadow confirmation"):
        prepare.run(args(source, target, "apply", confirm="WRONG"))
    assert not target.exists()
    target.mkdir()
    with pytest.raises(RuntimeError, match="overwrite refused"):
        prepare.run(
            args(source, target, "apply", confirm=prepare.APPLY_CONFIRMATION)
        )


def test_preview_rejects_missing_lot_size_evidence(tmp_path: Path) -> None:
    source = tmp_path / "accepted-v3-8"
    target = tmp_path / "isolated-v3-9"
    make_source(source)
    central_path = source / "central_order_state.json"
    central_path.unlink()
    central_path.with_name(central_path.name + ".sha256").unlink()
    CentralOrderStore(central_path).initialize(ACCOUNT)

    with pytest.raises(RuntimeError, match="no verified lot-size evidence"):
        prepare.run(args(source, target, "preview"))


def test_apply_preserves_checksummed_portfolio_risk_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "accepted-v3-9-m3"
    target = tmp_path / "isolated-v3-9-m4"
    make_source(source)
    central_path = source / "central_order_state.json"
    central_path.unlink()
    central_path.with_name(central_path.name + ".sha256").unlink()
    CentralOrderStore(central_path).initialize(ACCOUNT)
    atomic_write_json(
        source / prepare.METADATA_NAME,
        {
            "version": 1,
            "instruments": [
                {
                    "instrument_id": "uid-sber",
                    "lot_size": 10,
                    "asset_class": "SHARE",
                    "currency": "RUB",
                },
                {
                    "instrument_id": "uid-lkoh",
                    "lot_size": 10,
                    "asset_class": "SHARE",
                    "currency": "RUB",
                },
            ],
        },
        write_checksum=True,
    )

    result = prepare.run(
        args(source, target, "apply", confirm=prepare.APPLY_CONFIRMATION)
    )

    assert result["status"] == "PREPARED"
    assert result["instrument_count"] == 2
    metadata = load_portfolio_risk_metadata(target / prepare.METADATA_NAME)
    assert {item.lot_size for item in metadata.values()} == {10}
    assert {item.asset_class for item in metadata.values()} == {"SHARE"}
    assert {item.currency for item in metadata.values()} == {"RUB"}


def test_verified_backup_restores_checksummed_shadow_metadata(tmp_path: Path) -> None:
    source = tmp_path / "accepted-v3-8"
    target = tmp_path / "isolated-v3-9"
    restored = tmp_path / "restored-v3-9"
    make_source(source)
    prepare.run(
        args(source, target, "apply", confirm=prepare.APPLY_CONFIRMATION)
    )
    backup_manager = RuntimeBackupManager(target, app_version="v3.9-m3")
    backup = backup_manager.create_backup(tmp_path / "runtime.zip")
    verification = backup_manager.verify_backup(backup)

    assert verification.valid
    assert verification.manifest is not None
    names = {item["name"] for item in verification.manifest["entries"]}
    assert {prepare.METADATA_NAME, prepare.MANIFEST_NAME} <= names
    RuntimeBackupManager(restored, app_version="v3.9-m3").restore_backup(
        backup,
        confirmation="RESTORE RUNTIME",
    )
    assert set(load_portfolio_risk_metadata(restored / prepare.METADATA_NAME)) == {
        "uid-sber",
        "uid-lkoh",
    }
    assert (restored / f"{prepare.MANIFEST_NAME}.sha256").is_file()


def test_direct_cli_preview_works_without_pythonpath(tmp_path: Path) -> None:
    source = tmp_path / "accepted-v3-8"
    target = tmp_path / "isolated-v3-9"
    make_source(source)
    script = Path(prepare.__file__).resolve()

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "preview",
            "--source-runtime-dir",
            str(source),
            "--runtime-dir",
            str(target),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "PREVIEW"
    assert not target.exists()
