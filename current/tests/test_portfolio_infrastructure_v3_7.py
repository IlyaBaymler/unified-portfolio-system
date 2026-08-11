from __future__ import annotations

import json
from pathlib import Path
import time
import zipfile

from trading_robot.journal import EventJournal
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    OwnershipStatus,
    PortfolioState,
    PortfolioTarget,
    PositionOwnership,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
    SnapshotFreshness,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_snapshot import PortfolioSnapshotBuilder
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.runtime_bootstrap import bootstrap_runtime_files
from trading_robot.support_bundle import SupportBundleBuilder


def test_bootstrap_creates_and_validates_portfolio_state(tmp_path: Path):
    first = bootstrap_runtime_files(tmp_path, create_missing=True)
    assert first.ok
    actions = {item.name: item.action for item in first.items}
    assert actions["portfolio_state.json"] == "CREATED"
    assert PortfolioRepository(tmp_path / "portfolio_state.json").load().version == 2

    second = bootstrap_runtime_files(tmp_path, create_missing=True)
    assert second.ok
    actions = {item.name: item.action for item in second.items}
    assert actions["portfolio_state.json"] == "VALIDATED"


def test_backup_verify_restore_includes_portfolio_state(tmp_path: Path):
    report = bootstrap_runtime_files(tmp_path, create_missing=True)
    assert report.ok
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    original = PortfolioState.empty(account_id="account-1")
    repository.save(original)
    manager = RuntimeBackupManager(tmp_path, app_version="0.3.7a2")
    archive = manager.create_backup(tmp_path / "backup.zip")
    verification = manager.verify_backup(archive)
    assert verification.valid
    names = {item["name"] for item in verification.manifest["entries"]}
    assert "portfolio_state.json" in names

    changed = original.with_freshness(
        SnapshotFreshness.STALE,
        warning="changed",
    )
    repository.save(changed)
    preview = {item.name: item.action for item in manager.preview_restore(archive)}
    assert preview["portfolio_state.json"] == "REPLACE"
    manager.restore_backup(archive, confirmation="RESTORE RUNTIME")
    restored = repository.load(expected_account_id="account-1")
    assert restored == original


def test_support_bundle_reports_portfolio_integrity_without_raw_state(tmp_path: Path):
    assert bootstrap_runtime_files(tmp_path, create_missing=True).ok
    EventJournal(tmp_path / "trading_events.db")
    result = SupportBundleBuilder(tmp_path, app_version="0.3.7a2").build(
        tmp_path / "support.zip",
        account_id=None,
    )
    with zipfile.ZipFile(result.path) as archive:
        integrity = json.loads(archive.read("runtime_integrity.json"))
        names = set(archive.namelist())
    assert integrity["portfolio_state.json"]["valid"] is True
    assert "portfolio_state.json" not in names


def test_large_portfolio_export_is_linear_and_complete(tmp_path: Path):
    checked = "2026-07-31T10:00:00+00:00"
    positions = []
    for index in range(1_000):
        instrument_id = f"uid-{index:04d}"
        result = ReconciliationResult(
            instrument_id=instrument_id,
            status=ReconciliationStatus.MATCHED,
            blocking=False,
            reasons=("ok",),
            actual_lots=1,
            target_lots=1,
            checked_at=checked,
        )
        positions.append(
            PositionState(
                instrument_id=instrument_id,
                figi=f"figi-{index:04d}",
                ticker=f"T{index:04d}",
                class_code="TQBR",
                asset_type="share",
                currency="rub",
                quantity=1,
                actual_lots=1,
                average_price=100,
                current_price=101,
                market_value=101,
                expected_yield=1,
                target=PortfolioTarget(instrument_id, 1),
                ownership=PositionOwnership(
                    "sma", f"{index:064x}"[-64:], "CANDLE_INTERVAL_HOUR"
                ),
                ownership_status=OwnershipStatus.ATTRIBUTED,
                pending_orders=(),
                reconciliation=result,
                last_candle_time=checked,
            )
        )
    state = PortfolioState(
        version=1,
        account=AccountState(
            account_id="account-1",
            total_value=101_000,
            securities_value=101_000,
            expected_yield=1_000,
            cash_balances=(CashBalance("rub", 0),),
        ),
        snapshot_at=checked,
        generated_at=checked,
        freshness=SnapshotFreshness.FRESH,
        source="TEST",
        positions=tuple(positions),
        warnings=(),
        state_status="READY",
        blocking=False,
    )
    started = time.perf_counter()
    payload = PortfolioSnapshotBuilder(state).to_dict()
    elapsed = time.perf_counter() - started
    assert len(payload["positions"]) == 1_000
    assert elapsed < 2.0


def test_gui_source_uses_background_canonical_manager():
    source = Path("desktop_gui.py").read_text(encoding="utf-8")
    block = source[source.index("def _refresh_portfolio"):source.index("def _format_money")]
    assert "CanonicalPortfolioManager" in block
    assert "PortfolioSnapshotBuilder" in block
    assert "self._run_background" in block
    assert "api.get_portfolio" not in block
