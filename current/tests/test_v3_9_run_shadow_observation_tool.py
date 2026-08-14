from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest
from test_central_order_coordinator_v3_8 import ACCOUNT
from test_v3_9_start_shadow_runtimes_tool import (
    FakeClient as StartFakeClient,
)
from test_v3_9_start_shadow_runtimes_tool import (
    args as start_args,
)
from test_v3_9_start_shadow_runtimes_tool import (
    configured_runtime,
    secret_provider_factory,
)

from tools import v3_9_run_shadow_observation as observe
from tools import v3_9_start_shadow_runtimes as start
from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal


class FakeClient(StartFakeClient):
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self, token: str, **kwargs) -> None:
        super().__init__(token, **kwargs)
        self.__class__.instances.append(self)

    def get_trading_status(self, instrument_id: str):
        self.calls.append(f"get_trading_status:{instrument_id}")
        return {
            "instrumentUid": instrument_id,
            "tradingStatus": "SECURITY_TRADING_STATUS_NORMAL_TRADING",
        }

    def get_last_prices(self, instrument_ids: list[str]):
        self.calls.append("get_last_prices")
        return [
            {
                "instrumentUid": instrument_id,
                "price": {"units": "100", "nano": 0},
                "time": datetime.now(timezone.utc).isoformat(),
            }
            for instrument_id in instrument_ids
        ]

    def get_candles(
        self,
        instrument_id: str,
        from_time: datetime,
        to_time: datetime,
        *,
        interval: str,
        limit: int | None,
    ) -> pd.DataFrame:
        del from_time, interval, limit
        self.calls.append(f"get_candles:{instrument_id}")
        end = to_time.astimezone(timezone.utc).replace(second=0, microsecond=0)
        index = pd.date_range(end=end - timedelta(minutes=1), periods=20, freq="h")
        close = [100.0 + number for number in range(20)]
        return pd.DataFrame(
            {
                "open": close,
                "high": [value + 1.0 for value in close],
                "low": [value - 1.0 for value in close],
                "close": close,
                "volume": [100] * 20,
                "is_complete": [True] * 20,
                "last_trade_at": index,
            },
            index=index,
        )

    def post_order(self, *args, **kwargs):
        raise AssertionError("M3 observation must not submit a broker order")


def args(root: Path, action: str, *, confirm: str = ""):
    return observe.parse_args(
        [action, "--runtime-dir", str(root), "--confirm", confirm]
    )


def started_runtime(tmp_path: Path) -> Path:
    root = configured_runtime(tmp_path)
    start.run(
        start_args(root, "apply", confirm=start.APPLY_CONFIRMATION),
        client_factory=StartFakeClient,
        secret_provider_factory=secret_provider_factory,
    )
    return root


def test_preview_evaluates_natural_closed_candles_without_writes(tmp_path: Path) -> None:
    root = started_runtime(tmp_path)
    tracked = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    }

    result = observe.run(
        args(root, "preview"),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )

    assert result["status"] == "PREVIEW"
    assert result["runtime_status"] == "ACTIVE"
    assert len(result["natural_proposals"]) == 2
    assert {item["ticker"] for item in result["natural_proposals"]} == {
        "LKOH",
        "SBER",
    }
    assert all(
        item["execution_authorized"] is False
        and item["persisted"] is False
        and item["candidate_price_at"] is not None
        and item["candidate_price_source"] == "TBANK_LAST_PRICE_EXCHANGE"
        for item in result["natural_proposals"]
    )
    assert result["central_mutation_authorized"] is False
    assert result["broker_order_submit_called"] is False
    assert result["writes_performed"] is False
    assert ACCOUNT not in json.dumps(result)
    assert {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    } == tracked


def test_apply_requires_exact_confirmation_before_writes(tmp_path: Path) -> None:
    root = started_runtime(tmp_path)
    journal_before = (root / "trading_events.db").read_bytes()

    with pytest.raises(RuntimeError, match="exact v3.9 observation confirmation"):
        observe.run(
            args(root, "apply", confirm="WRONG"),
            client_factory=FakeClient,
            secret_provider_factory=secret_provider_factory,
        )

    assert (root / "trading_events.db").read_bytes() == journal_before


def test_apply_records_shadow_and_never_changes_central_or_calls_post(
    tmp_path: Path,
) -> None:
    root = started_runtime(tmp_path)
    central_before = (root / "central_order_state.json").read_bytes()

    result = observe.run(
        args(root, "apply", confirm=observe.APPLY_CONFIRMATION),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )

    assert result["status"] == "OBSERVED"
    assert result["broker_order_submit_called"] is False
    assert result["central_mutation_authorized"] is False
    assert result["shadow_report"]["status"] == "PASS"
    assert result["shadow_report"]["coverage_fraction"] == pytest.approx(1.0)
    assert result["shadow_report"]["unavailable"] == 0
    assert result["shadow_report"]["unexplained_drift"] == 0
    assert len(result["observations"]) == 2
    assert all(item["execution_authorized"] is False for item in result["observations"])
    assert all(
        "SNAPSHOT_FROM_FUTURE" not in item["reason_codes"]
        for item in result["observations"]
    )
    assert all(
        "CANDIDATE_PRICE_STALE" not in item["reason_codes"]
        for item in result["observations"]
    )
    assert ACCOUNT not in json.dumps(result)
    assert all(
        "runtime_key" not in item
        for item in result["scheduler"]["actions"]
    )
    assert (root / "central_order_state.json").read_bytes() == central_before
    central = CentralOrderStore(root / "central_order_state.json").load(
        expected_account_id=ACCOUNT
    )
    assert central.intents == ()
    assert central.reserved_cash_kopecks == 0
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=ACCOUNT
    )
    assert all(item.last_processed_candle is not None for item in runtimes)
    assert EventJournal(root / "trading_events.db", read_only=True).count(
        category="portfolio_risk_shadow"
    ) == 2

    tracked = {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    }
    repeated = observe.run(
        args(root, "apply", confirm=observe.APPLY_CONFIRMATION),
        client_factory=FakeClient,
        secret_provider_factory=secret_provider_factory,
    )
    assert repeated["status"] == "ALREADY_OBSERVED"
    assert repeated["writes_performed"] is False
    assert ACCOUNT not in json.dumps(repeated)
    assert {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    } == tracked
