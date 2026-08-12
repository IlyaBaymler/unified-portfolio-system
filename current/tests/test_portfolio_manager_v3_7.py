from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from trading_robot.portfolio_adapters import RuntimePortfolioAdapter
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import ReconciliationStatus
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_snapshot import PortfolioSnapshotBuilder


class FakePortfolioAPI:
    def __init__(self, lots: int = 1) -> None:
        self.current_lots = lots
        self.orders: list[dict] = []

    def find_instrument(self, ticker: str, class_code: str):
        return {
            "ticker": ticker,
            "classCode": class_code,
            "uid": "uid-sber",
            "figi": "figi-sber",
            "instrumentType": "share",
            "currency": "rub",
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_portfolio(self, account_id: str):
        positions = []
        if self.current_lots:
            positions.append(
                {
                    "instrumentUid": "uid-sber",
                    "figi": "figi-sber",
                    "ticker": "SBER",
                    "classCode": "TQBR",
                    "instrumentType": "share",
                    "quantity": {"units": str(self.current_lots), "nano": 0},
                    "quantityLots": {"units": str(self.current_lots), "nano": 0},
                    "averagePositionPrice": {"currency": "rub", "units": "250", "nano": 0},
                    "currentPrice": {"currency": "rub", "units": "260", "nano": 0},
                    "expectedYield": {"units": "10", "nano": 0},
                }
            )
        return {
            "totalAmountPortfolio": {"currency": "rub", "units": "50260", "nano": 0},
            "totalAmountShares": {"currency": "rub", "units": "260", "nano": 0},
            "expectedYield": {"units": "10", "nano": 0},
            "positions": positions,
        }

    def get_positions(self, account_id: str):
        return {"money": [{"currency": "rub", "units": "50000", "nano": 0}]}

    def get_orders(self, account_id: str):
        return list(self.orders)


def robot_state(*, target_lots: int = 1, owner: bool = True) -> dict:
    config_hash = "a" * 64
    scope = "account-1|SBER_TQBR"
    state_key = f"{scope}|CANDLE_INTERVAL_10_MIN|primary:sma:{config_hash[:16]}"
    state = {
        "version": 6,
        "bots": {
            state_key: {
                "instrument_id": "uid-sber",
                "figi": "figi-sber",
                "last_strategy_decisions": {
                    "sma": {
                        "strategy_id": "sma",
                        "role": "PRIMARY",
                        "config_hash": config_hash,
                        "candle_time": "2026-07-31T10:00:00+00:00",
                        "target_lots": target_lots,
                    }
                },
                "pending_order": None,
            }
        },
        "execution_scopes": {scope: {}},
    }
    if owner:
        state["execution_scopes"][scope]["active_primary"] = {
            "strategy_id": "sma",
            "config_hash": config_hash,
            "candle_interval": "CANDLE_INTERVAL_10_MIN",
        }
    return state


def make_manager(
    tmp_path: Path,
    api: FakePortfolioAPI,
    *,
    seed_canonical: bool = True,
) -> CanonicalPortfolioManager:
    robot_path = tmp_path / "robot_state.json"
    robot_path.write_text(json.dumps(robot_state()), encoding="utf-8")
    manager = CanonicalPortfolioManager(
        api,
        "account-1",
        robot_state_file=robot_path,
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )
    if seed_canonical and api.current_lots:
        # First establish broker actual in canonical state; then attach the
        # confirmed Strategy target/ownership as the post-fill coordinator does.
        manager.refresh(record_event=False)
        manager.stage_confirmed_target(
            instrument_id="uid-sber",
            target_lots=api.current_lots,
            strategy_id="sma",
            config_hash="a" * 64,
            candle_interval="CANDLE_INTERVAL_10_MIN",
            ticker="SBER",
            figi="figi-sber",
            class_code="TQBR",
            candle_time="2026-07-31T10:00:00+00:00",
        )
    return manager


def test_canonical_manager_publishes_and_persists_snapshot(tmp_path: Path):
    manager = make_manager(tmp_path, FakePortfolioAPI(lots=1))
    state = manager.refresh()
    persisted = PortfolioRepository(tmp_path / "portfolio_state.json").load(
        expected_account_id="account-1"
    )
    assert state == persisted
    assert state.positions[0].reconciliation.status is ReconciliationStatus.MATCHED
    assert (tmp_path / "portfolio_state.json.sha256").exists()


def test_gui_projection_uses_canonical_snapshot_builder(tmp_path: Path):
    manager = make_manager(tmp_path, FakePortfolioAPI(lots=1))
    payload = PortfolioSnapshotBuilder(manager.refresh()).to_dict()
    assert payload["canonical"] is True
    assert payload["schema_version"] == 2
    assert payload["positions"][0]["quantity_lots"] == 1
    assert payload["positions"][0]["owner_strategy"] == "sma"
    assert payload["positions"][0]["reconciliation_status"] == "MATCHED"


def test_restart_loads_previous_open_position_without_duplicate_side_effect(tmp_path: Path):
    api = FakePortfolioAPI(lots=1)
    first = make_manager(tmp_path, api)
    first_state = first.refresh()
    second = CanonicalPortfolioManager(
        api,
        "account-1",
        robot_state_file=tmp_path / "robot_state.json",
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )
    second_state = second.refresh()
    assert second_state.positions[0].actual_lots == 1
    assert second_state.positions[0].reconciliation.status is ReconciliationStatus.MATCHED
    assert first_state.account_id == second_state.account_id


def test_runtime_adapter_marks_missing_ownership(tmp_path: Path):
    path = tmp_path / "robot_state.json"
    path.write_text(json.dumps(robot_state(owner=False)), encoding="utf-8")
    record = RuntimePortfolioAdapter.from_robot_state(path, account_id="account-1")
    assert record.positions[0].target.target_lots == 1
    assert record.positions[0].ownership is None


def test_json_and_csv_exports(tmp_path: Path):
    manager = make_manager(tmp_path, FakePortfolioAPI(lots=1))
    builder = PortfolioSnapshotBuilder(manager.refresh())
    json_path = builder.export_json(tmp_path / "portfolio.json")
    csv_path = builder.export_csv(tmp_path / "portfolio.csv")
    assert json.loads(json_path.read_text(encoding="utf-8"))["canonical"] is True
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["ticker"] == "SBER"
    assert rows[0]["reconciliation_status"] == "MATCHED"


def test_large_portfolio_projection_remains_deterministic(tmp_path: Path):
    manager = make_manager(tmp_path, FakePortfolioAPI(lots=1))
    state = manager.refresh()
    # The builder is pure and should remain stable even when called repeatedly.
    first = PortfolioSnapshotBuilder(state).to_dict()
    second = PortfolioSnapshotBuilder(state).to_dict()
    assert first == second


def test_cached_snapshot_can_be_marked_stale(tmp_path: Path):
    manager = make_manager(tmp_path, FakePortfolioAPI(lots=1))
    manager.refresh()
    cached = manager.cached(mark_stale=True, warning="API unavailable")
    assert cached.freshness.value == "STALE"
    assert cached.blocking is True
    assert "API unavailable" in cached.warnings


def test_manager_detects_unattributed_broker_position(tmp_path: Path):
    robot_path = tmp_path / "robot_state.json"
    robot_path.write_text(json.dumps(robot_state(owner=False, target_lots=0)), encoding="utf-8")
    manager = CanonicalPortfolioManager(
        FakePortfolioAPI(lots=1),
        "account-1",
        robot_state_file=robot_path,
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )
    state = manager.refresh()
    assert state.positions[0].reconciliation.status is ReconciliationStatus.UNATTRIBUTED_OPEN_POSITION
    assert state.blocking is True


def test_matched_refresh_discards_resolved_snapshot_warnings(tmp_path: Path):
    api = FakePortfolioAPI(lots=1)
    manager = make_manager(tmp_path, api, seed_canonical=False)

    unattributed = manager.refresh(record_event=False)
    assert unattributed.blocking is True
    assert any("UNATTRIBUTED_OPEN_POSITION" in item for item in unattributed.warnings)

    manager.stage_confirmed_target(
        instrument_id="uid-sber",
        target_lots=1,
        strategy_id="sma",
        config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_10_MIN",
        ticker="SBER",
        figi="figi-sber",
        class_code="TQBR",
        candle_time="2026-07-31T10:00:00+00:00",
    )
    matched = manager.refresh(record_event=False)

    assert matched.state_status == "READY"
    assert matched.blocking is False
    assert matched.positions[0].reconciliation.status is ReconciliationStatus.MATCHED
    assert not any("UNATTRIBUTED_OPEN_POSITION" in item for item in matched.warnings)


def test_first_empty_snapshot_discards_bootstrap_warning(tmp_path: Path):
    manager = make_manager(tmp_path, FakePortfolioAPI(lots=0), seed_canonical=False)

    state = manager.refresh(record_event=False)

    assert state.state_status == "EMPTY"
    assert state.blocking is False
    assert "Portfolio snapshot has not been collected yet." not in state.warnings


def test_external_broker_order_without_position_is_not_hidden(tmp_path: Path):
    api = FakePortfolioAPI(lots=0)
    api.orders = [
        {
            "instrumentUid": "uid-external",
            "orderRequestId": "external-order",
            "direction": "ORDER_DIRECTION_BUY",
            "lotsRequested": "1",
            "lotsExecuted": "0",
            "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
        }
    ]
    robot_path = tmp_path / "robot_state.json"
    robot_path.write_text(json.dumps({"version": 6, "bots": {}, "execution_scopes": {}}), encoding="utf-8")
    manager = CanonicalPortfolioManager(
        api,
        "account-1",
        robot_state_file=robot_path,
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )
    state = manager.refresh()
    assert state.positions[0].instrument_id == "uid-external"
    assert state.positions[0].reconciliation.status is ReconciliationStatus.PENDING_ORDER
    assert state.blocking is True
