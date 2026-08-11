from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time

import numpy as np
import pandas as pd

from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.journal import EventJournal
from trading_robot.logging_setup import redact_sensitive_text


class ObservabilityFakeAPI:
    def __init__(self, *, emit_retry: bool = False) -> None:
        self.emit_retry = emit_retry
        self.telemetry_callback = None
        self._last_response_meta = {}

    @property
    def last_response_meta(self):
        return dict(self._last_response_meta)

    def set_telemetry_callback(self, callback):
        self.telemetry_callback = callback

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

    def get_candles(self, *args, **kwargs):
        if self.emit_retry and self.telemetry_callback:
            self.telemetry_callback(
                {
                    "event_type": "API_RETRY_SCHEDULED",
                    "service": "MarketDataService",
                    "method": "GetCandles",
                    "attempt": 1,
                    "max_attempts": 2,
                    "delay_seconds": 0.5,
                    "reason": "ConnectTimeout",
                }
            )
            recovered = {
                "event_type": "API_RETRY_RECOVERED",
                "service": "MarketDataService",
                "method": "GetCandles",
                "request_started_at": "2026-07-20T10:00:00+00:00",
                "request_completed_at": "2026-07-20T10:00:01+00:00",
                "request_duration_seconds": 1.0,
                "attempt_count": 2,
                "retry_count": 1,
                "retry_delay_total_seconds": 0.5,
                "status_code": 200,
                "tracking_id": "retry-id",
            }
            self._last_response_meta = recovered
            self.telemetry_callback(recovered)

        index = pd.date_range("2024-01-01", periods=500, freq="h", tz="UTC")
        close = np.linspace(100.0, 200.0, len(index))
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
        return {"positions": []}

    @staticmethod
    def position_lots(portfolio, instrument):
        return 0

    def get_trading_status(self, instrument_id: str):
        return {
            "apiTradeAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "marketOrderAvailableFlag": True,
        }

    @staticmethod
    def best_price_available(status):
        return True

    @staticmethod
    def market_order_available(status):
        return True

    def get_max_lots(self, account_id: str, instrument_id: str, price=None):
        return {"buyLimits": {"buyMaxMarketLots": "100"}}

    @staticmethod
    def max_buy_lots(response):
        return int(response["buyLimits"]["buyMaxMarketLots"])


def _config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=1,
        dry_run=True,
        state_file=str(tmp_path / "state.json"),
        journal_file=str(tmp_path / "events.db"),
        max_signal_age_seconds=10**10,
        heartbeat_log_seconds=3600,
        slow_cycle_seconds=60.0,
    )


def test_cycle_has_timing_session_and_decision_is_not_an_order(tmp_path: Path):
    bot = SandboxTradingBot(
        ObservabilityFakeAPI(),
        "account-1",
        _config(tmp_path),
        allow_execution=False,
    )
    result = bot.run_once()

    assert result["status"] == "processed"
    assert result["cycle_started_at"] <= result["cycle_finished_at"]
    assert result["cycle_duration_seconds"] >= 0
    assert result["session_id"] == bot.session_id
    assert result["api_request_count"] == 0
    assert result["action"] == "WOULD_BUY"
    assert result["intended_action"] == "BUY"
    assert result["order_was_sent"] is False

    journal = EventJournal(tmp_path / "events.db")
    decision_rows = journal.recent(category="decision")
    order_rows = journal.recent(category="order")
    cycle_rows = journal.recent(category="cycle")

    assert len(decision_rows) == 1
    assert decision_rows[0]["event_type"] == "TRADE_DECISION"
    assert decision_rows[0]["action"] == "WOULD_BUY"
    assert order_rows == []
    assert cycle_rows[0]["duration_seconds"] is not None
    assert "strategy_decisions" not in cycle_rows[0]["payload"]
    session_rows = journal.recent(session_id=bot.session_id, limit=20)
    assert {row["category"] for row in session_rows}.issuperset(
        {"session", "strategy", "strategy_comparison", "decision", "cycle"}
    )

    bot.end_session("test_complete")
    sessions = journal.recent(category="session")
    assert {row["event_type"] for row in sessions} == {"STARTED", "STOPPED"}
    assert all(row["payload"]["software_version"] == "0.3.7a3" for row in sessions)


def test_recovered_retry_is_structured_and_aggregated_into_cycle(tmp_path: Path):
    bot = SandboxTradingBot(
        ObservabilityFakeAPI(emit_retry=True),
        "account-1",
        _config(tmp_path),
        allow_execution=False,
    )
    result = bot.run_once()

    assert result["api_retry_count"] == 1
    assert result["api_request_count"] == 1
    assert result["api_attempt_count"] == 2
    assert result["api_recovered_retry_count"] == 1
    assert result["api_total_duration_seconds"] == 1.0

    journal = EventJournal(tmp_path / "events.db")
    api_events = journal.recent(category="api")
    assert {row["event_type"] for row in api_events} == {
        "API_RETRY_SCHEDULED",
        "API_RETRY_RECOVERED",
    }


def test_sensitive_text_is_redacted_before_logging():
    raw = (
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456 "
        "TBANK_SANDBOX_TOKEN=secret-token-value "
        "{'token': 'another-secret-value'}"
    )
    redacted = redact_sensitive_text(raw)
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "secret-token-value" not in redacted
    assert "another-secret-value" not in redacted
    assert redacted.count("<REDACTED>") == 3


def test_cycle_without_api_call_does_not_reuse_stale_request_metadata(tmp_path: Path):
    api = ObservabilityFakeAPI()
    api._last_response_meta = {
        "event_type": "API_REQUEST_SUCCEEDED",
        "service": "OldService",
        "method": "OldMethod",
        "request_completed_at": "2026-07-20T09:00:00+00:00",
        "request_duration_seconds": 99.0,
        "attempt_count": 7,
    }
    bot = SandboxTradingBot(
        api,
        "account-1",
        _config(tmp_path),
        allow_execution=False,
    )
    bot._cycle_api_events = []
    result = {"status": "state_locked", "api_state": "NOT_CALLED"}
    bot._finalize_cycle_observability(
        result,
        cycle_started_at=datetime.now(timezone.utc),
        cycle_started_perf=time.perf_counter(),
    )

    assert result["api_request_count"] == 0
    assert result["api_attempt_count"] == 0
    assert "api_last_service" not in result
    assert "api_completed_at" not in result
