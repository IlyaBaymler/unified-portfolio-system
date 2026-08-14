from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_central_order_coordinator_v3_8 import ACCOUNT, NOW
from test_v3_9_configure_shadow_runtimes_tool import (
    args as configure_args,
)
from test_v3_9_configure_shadow_runtimes_tool import (
    prepare_ready_runtime,
    secure_probe,
)
from test_v3_9_start_shadow_runtimes_tool import (
    FakeClient,
    secret_provider_factory,
)

from tools import v3_9_ack_external_close as acknowledge
from tools import v3_9_configure_shadow_runtimes as configure
from tools import v3_9_start_shadow_runtimes as start
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.portfolio_model import (
    OwnershipStatus,
    PortfolioTarget,
    PositionOrigin,
    PositionOwnership,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk_persistence import RiskStateStore
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.runtime_integrity import sha256_file


def args(root: Path, action: str, *, confirm: str = ""):
    return acknowledge.parse_args(
        [action, "--runtime-dir", str(root), "--confirm", confirm]
    )


def ack_ready_runtime(tmp_path: Path) -> Path:
    root = prepare_ready_runtime(tmp_path)
    runtime_store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    runtimes = runtime_store.load(expected_account_id=ACCOUNT)
    lkoh_runtime = next(item for item in runtimes if item.config.ticker == "LKOH")
    portfolio_store = PortfolioRepository(root / "portfolio_state.json")
    portfolio = portfolio_store.load(expected_account_id=ACCOUNT)
    position = PositionState(
        instrument_id=lkoh_runtime.config.instrument_id,
        figi="figi-lkoh",
        ticker="LKOH",
        class_code="TQBR",
        asset_type="share",
        currency="rub",
        quantity=10.0,
        actual_lots=1,
        average_price=7_000.0,
        current_price=7_000.0,
        market_value=70_000.0,
        expected_yield=0.0,
        target=PortfolioTarget(
            instrument_id=lkoh_runtime.config.instrument_id,
            target_lots=1,
            strategy_id=lkoh_runtime.config.strategy_id,
            config_hash=lkoh_runtime.config.strategy_config_hash,
            candle_time=NOW,
        ),
        ownership=PositionOwnership(
            strategy_id=lkoh_runtime.config.strategy_id,
            config_hash=lkoh_runtime.config.strategy_config_hash,
            candle_interval=lkoh_runtime.config.candle_interval,
            source="CANONICAL",
            attributed_at=NOW,
        ),
        ownership_status=OwnershipStatus.ATTRIBUTED,
        pending_orders=(),
        reconciliation=ReconciliationResult(
            instrument_id=lkoh_runtime.config.instrument_id,
            status=ReconciliationStatus.MATCHED,
            blocking=False,
            reasons=("Broker lots, target and ownership are consistent.",),
            actual_lots=1,
            target_lots=1,
            checked_at=NOW,
        ),
        origin=PositionOrigin.STRATEGY,
        last_candle_time=NOW,
    )
    portfolio_store.save(
        replace(
            portfolio,
            positions=(position,),
            state_status="READY",
            blocking=False,
            warnings=(),
        ),
        expected_revision=portfolio.revision,
    )
    runtime_store.save(
        tuple(
            item.with_execution_state(current_lots=1)
            if item.config.ticker == "LKOH"
            else item
            for item in runtimes
        )
    )
    configure.run(
        configure_args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )
    return root


def test_ack_preview_is_read_only_and_redacted(tmp_path: Path) -> None:
    root = ack_ready_runtime(tmp_path)
    tracked = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    }

    result = acknowledge.run(
        args(root, "preview"),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )

    assert result["status"] == "PREVIEW"
    assert result["provider_lots"] == 0
    assert result["canonical_lots_after"] == 1
    assert result["runtime_lots_after"] == 1
    assert result["broker_mutation_authorized"] is False
    assert result["broker_order_submit_called"] is False
    assert result["writes_performed"] is False
    assert ACCOUNT not in json.dumps(result)
    assert {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    } == tracked


def test_ack_apply_requires_exact_confirmation_without_writes(tmp_path: Path) -> None:
    root = ack_ready_runtime(tmp_path)

    with pytest.raises(RuntimeError, match="exact external-close confirmation"):
        acknowledge.run(
            args(root, "apply", confirm="WRONG"),
            client_factory=FakeClient,
            secret_provider_factory=secret_provider_factory,
        )

    assert EventJournal(root / "trading_events.db", read_only=True).count() == 0
    assert not (root / acknowledge.MANIFEST_NAME).exists()


def test_ack_apply_clears_only_lkoh_and_enables_start(tmp_path: Path) -> None:
    root = ack_ready_runtime(tmp_path)
    risk_hash = sha256_file(root / "risk_state.json")
    central_hash = sha256_file(root / "central_order_state.json")

    result = acknowledge.run(
        args(root, "apply", confirm=acknowledge.APPLY_CONFIRMATION),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )

    assert result["status"] == "ACKNOWLEDGED"
    portfolio = PortfolioRepository(root / "portfolio_state.json").load(
        expected_account_id=ACCOUNT
    )
    lkoh = portfolio.position("uid-lkoh")
    assert lkoh is not None
    assert lkoh.actual_lots == 0
    assert lkoh.target_lots == 0
    assert lkoh.ownership is None
    assert lkoh.origin is PositionOrigin.EXTERNAL
    assert lkoh.reconciliation.status is ReconciliationStatus.MATCHED
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=ACCOUNT
    )
    assert {item.config.ticker: item.current_lots for item in runtimes} == {
        "LKOH": 0,
        "SBER": 0,
    }
    assert {item.status for item in runtimes} == {"STOPPED"}
    assert sha256_file(root / "risk_state.json") == risk_hash
    assert sha256_file(root / "central_order_state.json") == central_hash
    risk = RiskStateStore(root / "risk_state.json").load_account(ACCOUNT)
    assert risk.daily_date is None
    assert (root / f"{acknowledge.MANIFEST_NAME}.sha256").is_file()
    rows = EventJournal(root / "trading_events.db", read_only=True).recent(
        limit=100,
        account_id=ACCOUNT,
    )
    assert sum(row["event_type"] == "EXTERNAL_CLOSE_ACKNOWLEDGED" for row in rows) == 1

    repeated = acknowledge.run(
        args(root, "apply", confirm=acknowledge.APPLY_CONFIRMATION),
        client_factory=lambda *a, **k: pytest.fail("idempotent ACK used provider"),
        secret_provider_factory=secret_provider_factory,
    )
    assert repeated["status"] == "ALREADY_ACKNOWLEDGED"
    started = start.run(
        start.parse_args(
            [
                "apply",
                "--runtime-dir",
                str(root),
                "--confirm",
                start.APPLY_CONFIRMATION,
            ]
        ),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )
    assert started["status"] == "STARTED"
    assert started["runtime_status"] == "ACTIVE"


def test_ack_refuses_nonflat_provider_or_active_order(tmp_path: Path) -> None:
    root = ack_ready_runtime(tmp_path)

    class NonflatClient(FakeClient):
        def get_portfolio(self, account_id: str):
            payload = super().get_portfolio(account_id)
            payload["positions"] = [
                {
                    "instrumentUid": "uid-lkoh",
                    "figi": "figi-lkoh",
                    "instrumentType": "share",
                    "quantity": {"units": 10, "nano": 0},
                    "quantityLots": {"units": 1, "nano": 0},
                }
            ]
            return payload

    with pytest.raises(RuntimeError, match="provider=1, expected=0"):
        acknowledge.run(
            args(root, "preview"),
            client_factory=NonflatClient,
            secret_provider_factory=secret_provider_factory,
        )


def test_backup_contains_external_close_ack_manifest(tmp_path: Path) -> None:
    root = ack_ready_runtime(tmp_path)
    acknowledge.run(
        args(root, "apply", confirm=acknowledge.APPLY_CONFIRMATION),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )
    manager = RuntimeBackupManager(root, app_version="v3.9-m3")
    backup = manager.create_backup(tmp_path / "acknowledged-runtime.zip")
    verification = manager.verify_backup(backup)

    assert verification.valid
    assert verification.manifest is not None
    names = {entry["name"] for entry in verification.manifest["entries"]}
    assert acknowledge.MANIFEST_NAME in names
