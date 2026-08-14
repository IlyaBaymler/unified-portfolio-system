from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from test_central_order_coordinator_v3_8 import ACCOUNT
from test_v3_9_configure_shadow_runtimes_tool import (
    args as configure_args,
)
from test_v3_9_configure_shadow_runtimes_tool import (
    prepare_ready_runtime,
    secure_probe,
)

from tools import v3_9_configure_shadow_runtimes as configure
from tools import v3_9_start_shadow_runtimes as start
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk_persistence import RiskStateStore
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.secret_provider import SecretProvider


class TestSecretProvider:
    name = "TEST SECURE STORE"
    secure = True

    def get(self, key: str) -> str | None:
        return "test-token" if key == start.TOKEN_KEY else None

    def set(self, key: str, value: str) -> None:
        raise AssertionError("start gate must not write a secret")

    def delete(self, key: str) -> None:
        raise AssertionError("start gate must not delete a secret")


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self, token: str, **kwargs) -> None:
        assert token == "test-token"
        self.calls: list[str] = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @staticmethod
    def instrument_id(instrument: dict) -> str:
        return str(instrument["uid"])

    def get_accounts(self):
        self.calls.append("get_accounts")
        return [{"id": ACCOUNT}]

    def find_instrument(self, ticker: str, class_code: str):
        self.calls.append(f"find_instrument:{ticker}")
        return {
            "uid": f"uid-{ticker.lower()}",
            "figi": f"figi-{ticker.lower()}",
            "ticker": ticker,
            "classCode": class_code,
            "lot": 10,
        }

    def get_portfolio(self, account_id: str):
        assert account_id == ACCOUNT
        self.calls.append("get_portfolio")
        return {
            "totalAmountPortfolio": {"units": 100_000, "nano": 0},
            "totalAmountCurrencies": {"units": 100_000, "nano": 0},
            "totalAmountShares": {"units": 0, "nano": 0},
            "expectedYield": {"units": 0, "nano": 0},
            "positions": [],
        }

    def get_orders(self, account_id: str):
        assert account_id == ACCOUNT
        self.calls.append("get_orders")
        return []


def secret_provider_factory(_root: str | Path) -> SecretProvider:
    return TestSecretProvider()


def args(root: Path, action: str, *, confirm: str = ""):
    return start.parse_args(
        [action, "--runtime-dir", str(root), "--confirm", confirm]
    )


def configured_runtime(tmp_path: Path) -> Path:
    root = prepare_ready_runtime(tmp_path)
    configure.run(
        configure_args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )
    return root


def test_preview_performs_provider_preflight_without_writes(tmp_path: Path) -> None:
    root = configured_runtime(tmp_path)
    tracked = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    }

    result = start.run(
        args(root, "preview"),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )

    assert result["status"] == "PREVIEW"
    assert result["runtime_status"] == "STOPPED"
    assert result["risk_baseline_status"] == "UNINITIALIZED"
    assert result["broker_mutation_authorized"] is False
    assert result["broker_order_submit_called"] is False
    assert result["strategy_proposal_created"] is False
    assert result["central_intent_created"] is False
    assert result["writes_performed"] is False
    assert ACCOUNT not in json.dumps(result)
    assert not (root / start.MANIFEST_NAME).exists()
    assert {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    } == tracked
    assert FakeClient.instances[-1].calls == [
        "get_accounts",
        "find_instrument:LKOH",
        "find_instrument:SBER",
        "get_portfolio",
        "get_orders",
    ]


def test_apply_requires_exact_confirmation_before_local_writes(tmp_path: Path) -> None:
    root = configured_runtime(tmp_path)

    with pytest.raises(RuntimeError, match="exact v3.9 start confirmation"):
        start.run(
            args(root, "apply", confirm="WRONG"),
            client_factory=FakeClient,
            secret_provider_factory=secret_provider_factory,
        )

    assert not (root / start.MANIFEST_NAME).exists()
    assert EventJournal(root / "trading_events.db", read_only=True).count() == 0


def test_apply_starts_all_runtimes_and_initializes_baselines(tmp_path: Path) -> None:
    root = configured_runtime(tmp_path)

    result = start.run(
        args(root, "apply", confirm=start.APPLY_CONFIRMATION),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )

    assert result["status"] == "STARTED"
    assert result["runtime_status"] == "ACTIVE"
    assert result["risk_baseline_status"] == "READY"
    assert result["canonical_freshness"] == "FRESH"
    assert result["broker_order_submit_called"] is False
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=ACCOUNT
    )
    assert {item.status for item in runtimes} == {"ACTIVE"}
    state = RiskStateStore(root / "risk_state.json").load_account(ACCOUNT)
    assert state.daily_start_equity_rub == pytest.approx(100_000)
    assert state.last_cash_rub == pytest.approx(100_000)
    portfolio = PortfolioRepository(root / "portfolio_state.json").load(
        expected_account_id=ACCOUNT
    )
    assert portfolio.freshness.value == "FRESH"
    assert not portfolio.blocking
    assert (root / f"{start.MANIFEST_NAME}.sha256").is_file()
    assert not (root / "trading_events.db-wal").exists()
    assert not (root / "trading_events.db-shm").exists()
    assert EventJournal(root / "trading_events.db", read_only=True).count() >= 6

    repeated = start.run(
        args(root, "apply", confirm=start.APPLY_CONFIRMATION),
        client_factory=lambda *a, **k: pytest.fail("idempotent retry used provider"),
        secret_provider_factory=secret_provider_factory,
    )
    assert repeated["status"] == "ALREADY_STARTED"
    assert repeated["writes_performed"] is False

    InstrumentRuntimeStore(root / "instrument_runtimes.json").save(
        tuple(item.stop() for item in runtimes)
    )
    with pytest.raises(RuntimeError, match="not ACTIVE"):
        start.run(
            args(root, "apply", confirm=start.APPLY_CONFIRMATION),
            client_factory=lambda *a, **k: pytest.fail("recovery used provider"),
            secret_provider_factory=secret_provider_factory,
        )


def test_start_refuses_active_provider_order(tmp_path: Path) -> None:
    root = configured_runtime(tmp_path)

    class PendingOrderClient(FakeClient):
        def get_orders(self, account_id: str):
            return [
                {
                    "orderId": "broker-order-1",
                    "instrumentUid": "uid-sber",
                    "lotsRequested": 1,
                    "lotsExecuted": 0,
                    "direction": "ORDER_DIRECTION_BUY",
                    "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
                }
            ]

    with pytest.raises(RuntimeError, match="active or uncertain broker order"):
        start.run(
            args(root, "preview"),
            client_factory=PendingOrderClient,
            secret_provider_factory=secret_provider_factory,
        )


def test_backup_contains_start_manifest(tmp_path: Path) -> None:
    root = configured_runtime(tmp_path)
    start.run(
        args(root, "apply", confirm=start.APPLY_CONFIRMATION),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )
    manager = RuntimeBackupManager(root, app_version="v3.9-m3")
    backup = manager.create_backup(tmp_path / "started-runtime.zip")
    verification = manager.verify_backup(backup)

    assert verification.valid
    assert verification.manifest is not None
    names = {entry["name"] for entry in verification.manifest["entries"]}
    assert start.MANIFEST_NAME in names
