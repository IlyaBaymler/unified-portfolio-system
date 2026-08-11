from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.journal import EventJournal
from trading_robot.market_idle import classify_market_status


class MarketAwareFakeAPI:
    def __init__(self) -> None:
        self.market_open = True
        self.ambiguous_status = False
        self.candle_calls = 0
        self.status_calls = 0
        self.portfolio_calls = 0
        self.post_count = 0
        self.current_lots = 0
        self.order_status = "EXECUTION_REPORT_STATUS_FILL"
        self.executed_lots = 1

    def find_instrument(self, query: str, class_code: str):
        return {
            "ticker": query,
            "classCode": class_code,
            "uid": "instrument-uid",
            "apiTradeAvailableFlag": True,
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_trading_status(self, instrument_id: str):
        self.status_calls += 1
        if self.ambiguous_status:
            return {"tradingStatus": "UNKNOWN"}
        return {
            "tradingStatus": (
                "SECURITY_TRADING_STATUS_NORMAL_TRADING"
                if self.market_open
                else "SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING"
            ),
            "apiTradeAvailableFlag": self.market_open,
            "limitOrderAvailableFlag": self.market_open,
            "marketOrderAvailableFlag": self.market_open,
            "bestpriceOrderAvailableFlag": self.market_open,
        }

    def get_candles(self, *args, **kwargs):
        self.candle_calls += 1
        index = pd.date_range("2024-01-01", periods=500, freq="h", tz="UTC")
        close = np.linspace(100, 200, len(index))
        return pd.DataFrame(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 100_000,
                "is_complete": True,
            },
            index=index,
        )

    def get_portfolio(self, account_id: str):
        self.portfolio_calls += 1
        if self.current_lots <= 0:
            return {"positions": []}
        return {
            "positions": [
                {
                    "instrumentUid": "instrument-uid",
                    "quantityLots": {"units": str(self.current_lots), "nano": 0},
                }
            ]
        }

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions", [])
        return int(positions[0]["quantityLots"]["units"]) if positions else 0

    def get_max_lots(self, account_id: str, instrument_id: str, price=None):
        return {"buyLimits": {"buyMaxMarketLots": "100"}}

    @staticmethod
    def max_buy_lots(response):
        return int(response["buyLimits"]["buyMaxMarketLots"])

    @staticmethod
    def best_price_available(status):
        return bool(
            status.get("apiTradeAvailableFlag", True)
            and status.get("limitOrderAvailableFlag", True)
        )

    @staticmethod
    def market_order_available(status):
        return bool(
            status.get("apiTradeAvailableFlag", True)
            and status.get("marketOrderAvailableFlag", True)
        )

    def post_order(self, *args, order_id: str, **kwargs):
        self.post_count += 1
        direction = str(args[3]).upper()
        if self.order_status in {
            "EXECUTION_REPORT_STATUS_FILL",
            "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
        }:
            delta = self.executed_lots if direction == "BUY" else -self.executed_lots
            self.current_lots = max(0, self.current_lots + delta)
        return {
            "orderId": order_id,
            "executionReportStatus": self.order_status,
            "lotsExecuted": str(self.executed_lots),
            "executedOrderPrice": {"units": "200", "nano": 0},
        }

    def get_order_state(self, account_id: str, order_id: str, by_request_id=True):
        return {
            "orderId": order_id,
            "executionReportStatus": self.order_status,
            "lotsExecuted": str(self.executed_lots),
            "executedOrderPrice": {"units": "200", "nano": 0},
        }


def make_config(
    tmp_path: Path,
    *,
    dry_run: bool = True,
    idle_reconcile: int = 1800,
) -> BotConfig:
    return BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        poll_seconds=60,
        max_order_lots=1,
        dry_run=dry_run,
        state_file=str(tmp_path / "robot_state.json"),
        journal_file=str(tmp_path / "trading_events.db"),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
        portfolio_reconcile_interval_seconds=900,
        market_idle_enabled=True,
        market_status_check_seconds=30,
        market_idle_poll_seconds=30,
        market_idle_reconcile_seconds=idle_reconcile,
        market_idle_heartbeat_seconds=30,
    )


def age_market_monitor(state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    bot_state = next(iter(state["bots"].values()))
    monitor = bot_state["market_monitor"]
    monitor["last_status_check_at"] = "2020-01-01T00:00:00+00:00"
    if monitor.get("idle"):
        monitor["idle"]["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
        monitor["idle"]["last_reconcile_at"] = "2020-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_closed_market_enters_idle_without_loading_candles(tmp_path: Path):
    api = MarketAwareFakeAPI()
    api.market_open = False
    bot = SandboxTradingBot(api, "account-1", make_config(tmp_path))

    result = bot.run_once()

    assert result["status"] == "market_idle"
    assert result["market_idle_transition"] == "ENTERED"
    assert result["market_state"] == "IDLE"
    assert result["recommended_wait_seconds"] == 30
    assert api.candle_calls == 0
    assert api.post_count == 0
    events = EventJournal(tmp_path / "trading_events.db").recent(
        limit=20, category="market"
    )
    assert [item["event_type"] for item in events].count("MARKET_IDLE_ENTERED") == 1


def test_market_idle_heartbeat_is_deduplicated(tmp_path: Path):
    api = MarketAwareFakeAPI()
    api.market_open = False
    state_path = tmp_path / "robot_state.json"
    bot = SandboxTradingBot(api, "account-1", make_config(tmp_path))
    bot.run_once()
    age_market_monitor(state_path)

    second = bot.run_once()

    assert second["status"] == "market_idle"
    assert second["market_idle_transition"] == "NONE"
    assert api.candle_calls == 0
    events = EventJournal(tmp_path / "trading_events.db").recent(
        limit=20, category="market"
    )
    names = [item["event_type"] for item in events]
    assert names.count("MARKET_IDLE_ENTERED") == 1
    assert names.count("MARKET_IDLE_HEARTBEAT") == 1


def test_explicit_open_status_exits_idle_and_resumes_strategy(tmp_path: Path):
    api = MarketAwareFakeAPI()
    api.market_open = False
    state_path = tmp_path / "robot_state.json"
    bot = SandboxTradingBot(api, "account-1", make_config(tmp_path))
    bot.run_once()
    age_market_monitor(state_path)
    api.market_open = True

    resumed = bot.run_once()

    assert resumed["status"] == "processed"
    assert resumed["market_idle_transition"] == "EXITED"
    assert resumed["market_state"] == "OPEN"
    assert api.candle_calls == 1
    events = EventJournal(tmp_path / "trading_events.db").recent(
        limit=20, category="market"
    )
    assert any(item["event_type"] == "MARKET_IDLE_EXITED" for item in events)


def test_ambiguous_status_does_not_resume_existing_idle(tmp_path: Path):
    api = MarketAwareFakeAPI()
    api.market_open = False
    state_path = tmp_path / "robot_state.json"
    bot = SandboxTradingBot(api, "account-1", make_config(tmp_path))
    bot.run_once()
    age_market_monitor(state_path)
    api.ambiguous_status = True

    result = bot.run_once()

    assert result["status"] == "market_idle"
    assert result["market_idle_reason"] == "STATUS_UNCERTAIN_WHILE_IDLE"
    assert api.candle_calls == 0


def test_ambiguous_status_on_startup_does_not_invent_market_close(tmp_path: Path):
    api = MarketAwareFakeAPI()
    api.ambiguous_status = True
    bot = SandboxTradingBot(api, "account-1", make_config(tmp_path))

    result = bot.run_once()

    assert result["status"] == "processed"
    assert result["market_state"] == "UNKNOWN"
    assert api.candle_calls == 1
    assert classify_market_status(
        {"apiTradeAvailableFlag": True}, order_type="BESTPRICE"
    ).executable is None
    assert classify_market_status(
        {"limitOrderAvailableFlag": True}, order_type="BESTPRICE"
    ).executable is None


def test_pending_order_recovery_has_priority_over_market_idle(tmp_path: Path):
    api = MarketAwareFakeAPI()
    api.order_status = "EXECUTION_REPORT_STATUS_NEW"
    api.executed_lots = 0
    config = make_config(tmp_path, dry_run=False)
    bot = SandboxTradingBot(api, "account-1", config, allow_execution=True)

    first = bot.run_once()
    assert first["status"] == "order_pending"
    assert api.post_count == 1
    api.market_open = False
    age_market_monitor(tmp_path / "robot_state.json")

    second = bot.run_once()

    assert second["status"] == "order_pending"
    assert second.get("market_idle_active") is not True
    assert api.post_count == 1


def test_market_idle_keeps_periodic_portfolio_reconciliation(tmp_path: Path):
    api = MarketAwareFakeAPI()
    config = make_config(tmp_path, dry_run=False, idle_reconcile=60)
    bot = SandboxTradingBot(api, "account-1", config, allow_execution=True)
    first = bot.run_once()
    assert first["executed"] is True
    assert api.current_lots == 1
    age_market_monitor(tmp_path / "robot_state.json")
    api.market_open = False

    idle = bot.run_once()

    assert idle["status"] == "market_idle"
    assert idle["periodic_reconciliation"]["performed"] is True
    assert idle["periodic_reconciliation"]["position_reconciled"] is True
    assert idle["current_lots"] == 1


def test_market_idle_config_rejects_unsafe_intervals():
    with pytest.raises(ValueError, match="market_idle_poll_seconds"):
        BotConfig(market_idle_poll_seconds=10)
    with pytest.raises(ValueError, match="market_idle_reconcile_seconds"):
        BotConfig(market_idle_reconcile_seconds=30)
