from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest

from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.journal import EventJournal


class FakeSandboxAPI:
    def __init__(self) -> None:
        self.post_count = 0
        self.order_ids: list[str] = []
        self.trading_available = True
        self.order_status = "EXECUTION_REPORT_STATUS_FILL"
        self.executed_lots = 3
        self.current_lots = 0
        self.candle_calls: list[tuple[tuple, dict]] = []

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
        self.candle_calls.append((args, kwargs))
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
        if self.current_lots <= 0:
            return {"positions": []}
        return {
            "positions": [
                {
                    "instrumentUid": "instrument-uid",
                    "quantityLots": {
                        "units": str(self.current_lots),
                        "nano": 0,
                    },
                }
            ]
        }

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions", [])
        if not positions:
            return 0
        return int(positions[0]["quantityLots"]["units"])

    def get_trading_status(self, instrument_id: str):
        return {
            "apiTradeAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "marketOrderAvailableFlag": True,
        }

    def get_max_lots(self, account_id: str, instrument_id: str, price=None):
        return {"buyLimits": {"buyMaxMarketLots": "100"}}

    @staticmethod
    def max_buy_lots(response):
        return int(response["buyLimits"]["buyMaxMarketLots"])

    def best_price_available(self, status):
        return self.trading_available

    def market_order_available(self, status):
        return self.trading_available

    def post_order(self, *args, order_id: str, **kwargs):
        self.post_count += 1
        self.order_ids.append(order_id)
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
        }

    def get_order_state(self, account_id: str, order_id: str, by_request_id=True):
        return {
            "orderId": order_id,
            "executionReportStatus": self.order_status,
            "lotsExecuted": str(self.executed_lots),
        }


def make_config(state_file: Path, *, dry_run: bool) -> BotConfig:
    return BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=3,
        dry_run=dry_run,
        state_file=str(state_file),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
    )


def test_same_completed_candle_is_not_processed_twice(tmp_path: Path):
    api = FakeSandboxAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=True),
        allow_execution=False,
    )
    first = bot.run_once()
    second = bot.run_once()
    assert first["status"] == "processed"
    assert second["status"] == "already_processed"
    assert api.post_count == 0


def test_order_id_is_deterministic_and_restart_safe(tmp_path: Path):
    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    config = make_config(state_file, dry_run=False)
    first_bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )
    first = first_bot.run_once()

    second_bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )
    second = second_bot.run_once()

    assert first["executed"] is True
    assert second["status"] == "already_processed"
    assert api.post_count == 1
    assert len(api.order_ids[0]) == 36


def test_temporarily_blocked_execution_does_not_consume_candle(tmp_path: Path):
    api = FakeSandboxAPI()
    api.trading_available = False
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=False),
        allow_execution=True,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "execution_blocked"
    assert second["status"] == "execution_blocked"
    assert api.post_count == 0


def test_nonterminal_order_is_reconciled_without_duplicate_submit(tmp_path: Path):
    api = FakeSandboxAPI()
    api.order_status = "EXECUTION_REPORT_STATUS_NEW"
    api.executed_lots = 0
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=False),
        allow_execution=True,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "order_pending"
    assert second["status"] == "order_pending"
    assert api.post_count == 1


def test_partial_fill_is_reported_as_execution_for_fill_and_kill(tmp_path: Path):
    api = FakeSandboxAPI()
    api.order_status = "EXECUTION_REPORT_STATUS_PARTIALLYFILL"
    api.executed_lots = 1
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=False),
        allow_execution=True,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "processed"
    assert first["accepted"] is True
    assert first["executed"] is True
    assert first["executed_lots"] == 1
    assert second["status"] == "already_processed"
    assert api.post_count == 1


def test_daily_interval_expands_warmup_and_avoids_limit_source_conflict(
    tmp_path: Path,
):
    api = FakeSandboxAPI()
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_DAY",
        fast_window=20,
        slow_window=50,
        lookback_days=30,
        max_order_lots=1,
        dry_run=True,
        state_file=str(tmp_path / "daily-state.json"),
    )
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=False,
    )

    result = bot.run_once()
    args, kwargs = api.candle_calls[0]
    requested_span = args[2] - args[1]

    assert requested_span.days >= 100
    assert kwargs["limit"] is None
    assert result["effective_lookback_days"] >= 100
    assert result["candles_used"] == 500


def test_ten_minute_interval_clamps_lookback_to_safe_api_span(tmp_path: Path):
    api = FakeSandboxAPI()
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_10_MIN",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=1,
        dry_run=True,
        state_file=str(tmp_path / "ten-minute-state.json"),
    )
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=False,
    )

    result = bot.run_once()
    args, kwargs = api.candle_calls[0]
    requested_span = args[2] - args[1]

    assert requested_span.days == 6
    assert kwargs["limit"] is None
    assert result["effective_lookback_days"] == 6


@pytest.mark.parametrize(
    "interval",
    ["CANDLE_INTERVAL_15_MIN", "CANDLE_INTERVAL_30_MIN"],
)
def test_v3_8_intraday_intervals_use_safe_three_week_lookback(
    tmp_path: Path,
    interval: str,
):
    api = FakeSandboxAPI()
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        candle_interval=interval,
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=1,
        dry_run=True,
        state_file=str(tmp_path / f"{interval}.json"),
    )
    bot = SandboxTradingBot(api, "account-1", config, allow_execution=False)

    result = bot.run_once()
    args, kwargs = api.candle_calls[0]
    requested_span = args[2] - args[1]

    assert requested_span.days == 20
    assert kwargs["interval"] == interval
    assert kwargs["limit"] is None
    assert result["effective_lookback_days"] == 20


def test_transient_errors_open_persistent_circuit_breaker(tmp_path: Path):
    from trading_robot.tbank_sandbox import TBankAPIError

    class FailingAPI(FakeSandboxAPI):
        def __init__(self):
            super().__init__()
            self.fail_calls = 0

        def get_candles(self, *args, **kwargs):
            self.fail_calls += 1
            raise TBankAPIError(
                "temporary timeout",
                transient=True,
                service="MarketDataService",
                method="GetCandles",
            )

    api = FailingAPI()
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=1,
        dry_run=True,
        state_file=str(tmp_path / "state.json"),
        failure_threshold=2,
        circuit_open_seconds=60,
    )
    bot = SandboxTradingBot(api, "account-1", config)

    first = bot.run_once()
    second = bot.run_once()
    third = bot.run_once()

    assert first["status"] == "api_degraded"
    assert second["status"] == "circuit_open"
    assert third["status"] == "circuit_open"
    assert api.fail_calls == 2
    assert second["consecutive_failures"] == 2


def test_reconciliation_mismatch_keeps_pending_without_duplicate(tmp_path: Path):
    class MismatchAPI(FakeSandboxAPI):
        def post_order(self, *args, order_id: str, **kwargs):
            self.post_count += 1
            self.order_ids.append(order_id)
            # Provider says it filled, but portfolio intentionally remains stale.
            return {
                "orderId": order_id,
                "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                "lotsExecuted": "3",
            }

    api = MismatchAPI()
    config = make_config(tmp_path / "state.json", dry_run=False)
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "order_pending"
    assert first["position_reconciled"] is False
    assert second["status"] == "order_pending"
    assert api.post_count == 1
    assert second["pending_order"]["lifecycle_state"] == (
        "RECONCILIATION_REQUIRED"
    )


def test_state_exposes_seen_dry_run_and_consumed_candle(tmp_path: Path):
    api = FakeSandboxAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=True),
        allow_execution=False,
    )
    result = bot.run_once()
    snapshot = bot.status_snapshot()

    assert result["status"] == "processed"
    assert snapshot["last_seen_candle"] is not None
    assert snapshot["last_consumed_candle"] == snapshot["last_seen_candle"]
    assert snapshot["last_dry_run_candle"] == snapshot["last_seen_candle"]


def test_unreadable_state_file_stops_robot_safely(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{broken-json", encoding="utf-8")
    bot = SandboxTradingBot(
        FakeSandboxAPI(),
        "account-1",
        make_config(state_file, dry_run=True),
        allow_execution=False,
    )

    import pytest

    with pytest.raises(RuntimeError, match="robot_state.json"):
        bot.run_once()


def test_nontransient_submission_rejection_is_not_retried(tmp_path: Path):
    from trading_robot.tbank_sandbox import TBankAPIError

    class RejectingAPI(FakeSandboxAPI):
        def post_order(self, *args, order_id: str, **kwargs):
            self.post_count += 1
            self.order_ids.append(order_id)
            raise TBankAPIError(
                "invalid order",
                status_code=400,
                details={"code": 3},
                transient=False,
                service="SandboxService",
                method="PostSandboxOrder",
                tracking_id="tracking-reject",
            )

    api = RejectingAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=False),
        allow_execution=True,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "submission_failed"
    assert first["accepted"] is False
    assert first["pending_order"] is None
    assert second["status"] == "already_processed"
    assert api.post_count == 1


def test_lost_submission_response_recovers_by_same_order_id(tmp_path: Path):
    from trading_robot.tbank_sandbox import TBankAPIError

    class LostResponseAPI(FakeSandboxAPI):
        def __init__(self):
            super().__init__()
            self.accepted_orders: dict[str, dict] = {}

        def post_order(self, *args, order_id: str, **kwargs):
            self.post_count += 1
            self.order_ids.append(order_id)
            direction = str(args[3]).upper()
            self.current_lots += self.executed_lots if direction == "BUY" else -self.executed_lots
            self.accepted_orders[order_id] = {
                "orderId": order_id,
                "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                "lotsExecuted": str(self.executed_lots),
            }
            raise TBankAPIError(
                "response lost after submission",
                transient=True,
                service="SandboxService",
                method="PostSandboxOrder",
            )

        def get_order_state(self, account_id: str, order_id: str, by_request_id=True):
            return self.accepted_orders[order_id]

    api = LostResponseAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=False),
        allow_execution=True,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "api_degraded"
    assert first["pending_order"]["order_id"] == api.order_ids[0]
    assert second["status"] == "order_recovered"
    assert second["position_reconciled"] is True
    assert api.post_count == 1


def test_locked_state_file_blocks_second_robot_process(tmp_path: Path):
    from trading_robot.locking import InterProcessFileLock

    state_file = tmp_path / "state.json"
    bot = SandboxTradingBot(
        FakeSandboxAPI(),
        "account-1",
        make_config(state_file, dry_run=True),
        allow_execution=False,
    )
    lock = InterProcessFileLock(Path(str(state_file) + ".lock"))
    lock.acquire()
    try:
        result = bot.run_once()
    finally:
        lock.release()

    assert result["status"] == "state_locked"
    assert result["api_state"] == "NOT_CALLED"


def test_shadow_strategies_are_journalled_but_primary_alone_executes(tmp_path: Path):
    from trading_robot.journal import EventJournal

    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    journal_file = tmp_path / "events.db"
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        primary_strategy="sma",
        shadow_strategies=("donchian",),
        fast_window=5,
        slow_window=20,
        donchian_entry_window=30,
        donchian_exit_window=10,
        donchian_atr_window=14,
        lookback_days=30,
        max_order_lots=3,
        dry_run=False,
        state_file=str(state_file),
        journal_file=str(journal_file),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
    )
    bot = SandboxTradingBot(api, "account-1", config, allow_execution=True)

    result = bot.run_once()
    rows = EventJournal(journal_file).recent(limit=100)

    assert result["status"] == "processed"
    assert result["primary_strategy"] == "sma"
    assert set(result["strategy_decisions"]) == {"sma", "donchian"}
    assert result["strategy_decisions"]["sma"]["role"] == "PRIMARY"
    assert result["strategy_decisions"]["donchian"]["role"] == "SHADOW"
    assert api.post_count == 1
    assert any(
        row["category"] == "strategy"
        and row["event_type"] == "SHADOW_DECISION"
        for row in rows
    )
    order_rows = [row for row in rows if row["category"] == "order"]
    assert all(
        row["payload"].get("strategy_id") in {None, "sma"}
        for row in order_rows
    )


def test_dry_run_returns_primary_shadow_comparison_without_orders(tmp_path: Path):
    api = FakeSandboxAPI()
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        primary_strategy="donchian",
        shadow_strategies=("sma",),
        fast_window=5,
        slow_window=20,
        donchian_entry_window=30,
        donchian_exit_window=10,
        donchian_atr_window=14,
        lookback_days=30,
        max_order_lots=3,
        dry_run=True,
        state_file=str(tmp_path / "state.json"),
        max_signal_age_seconds=10**10,
    )
    bot = SandboxTradingBot(api, "account-1", config, allow_execution=False)

    result = bot.run_once()

    assert result["primary_strategy"] == "donchian"
    assert result["strategy_comparison"]["enabled_count"] == 2
    assert set(result["strategy_decisions"]) == {"donchian", "sma"}
    assert api.post_count == 0


def test_primary_strategy_switch_is_blocked_while_position_is_open(tmp_path: Path):
    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    first = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=3,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    ).run_once()
    assert first["executed"] is True
    assert api.current_lots == 3

    switched_bot = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            primary_strategy="donchian",
            donchian_entry_window=30,
            donchian_exit_window=10,
            donchian_atr_window=14,
            max_order_lots=3,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    )
    switched = switched_bot.run_once()

    assert switched["status"] == "strategy_switch_blocked"
    assert switched["retryable_block"] is True
    assert api.post_count == 1


def test_unattributed_open_position_blocks_execution(tmp_path: Path):
    api = FakeSandboxAPI()
    api.current_lots = 1
    bot = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=3,
            dry_run=False,
            state_file=str(tmp_path / "state.json"),
            max_signal_age_seconds=10**10,
        ),
        allow_execution=True,
    )

    result = bot.run_once()

    assert result["status"] == "strategy_switch_blocked"
    assert "ownership" in result["execution_block_reason"]
    assert api.post_count == 0


def test_primary_ownership_is_shared_across_timeframes(tmp_path: Path):
    """DAY and HOUR must not own the same broker position independently."""
    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"

    hourly = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            candle_interval="CANDLE_INTERVAL_HOUR",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=3,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    )
    first = hourly.run_once()
    assert first["executed"] is True
    assert api.current_lots == 3

    daily = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            candle_interval="CANDLE_INTERVAL_DAY",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=3,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    )
    switched = daily.run_once()

    assert switched["status"] == "strategy_switch_blocked"
    assert api.post_count == 1


def test_pending_order_blocks_other_timeframe_in_same_instrument(tmp_path: Path):
    api = FakeSandboxAPI()
    api.order_status = "EXECUTION_REPORT_STATUS_NEW"
    api.executed_lots = 0
    state_file = tmp_path / "state.json"

    hour_bot = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            candle_interval="CANDLE_INTERVAL_HOUR",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=3,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    )
    first = hour_bot.run_once()
    assert first["status"] == "order_pending"

    api.order_status = "EXECUTION_REPORT_STATUS_FILL"
    api.executed_lots = 3
    day_bot = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            candle_interval="CANDLE_INTERVAL_DAY",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=3,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    )
    result = day_bot.run_once()

    assert result["status"] == "strategy_switch_blocked"
    assert result["foreign_pending_order"] is not None
    assert api.post_count == 1


def test_position_limit_change_is_blocked_while_position_is_open(tmp_path: Path):
    api = FakeSandboxAPI()
    api.executed_lots = 1
    state_file = tmp_path / "state.json"
    first_bot = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=1,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    )
    first = first_bot.run_once()
    assert first["executed"] is True
    assert api.current_lots == 1

    resized_bot = SandboxTradingBot(
        api,
        "account-1",
        BotConfig(
            ticker="SBER",
            class_code="TQBR",
            primary_strategy="sma",
            fast_window=5,
            slow_window=20,
            max_order_lots=3,
            dry_run=False,
            state_file=str(state_file),
            max_signal_age_seconds=10**10,
            reconcile_delay_seconds=0,
        ),
        allow_execution=True,
    )
    result = resized_bot.run_once()

    assert result["status"] == "strategy_switch_blocked"
    assert api.post_count == 1


def test_flat_position_clears_legacy_scope_migration_conflict(tmp_path: Path):
    """A historical HOUR/DAY conflict is harmless once the broker is flat."""
    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        fast_window=5,
        slow_window=20,
        max_order_lots=3,
        dry_run=False,
        state_file=str(state_file),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
    )
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )
    legacy_owner = {
        "active_primary": {
            "strategy_id": "sma",
            "config_hash": "legacy",
            "candle_interval": "CANDLE_INTERVAL_HOUR",
        }
    }
    state_file.write_text(
        json.dumps(
            {
                "version": 3,
                "bots": {},
                "execution_scopes": {
                    bot.execution_scope_key + "|CANDLE_INTERVAL_HOUR": legacy_owner,
                    bot.execution_scope_key + "|CANDLE_INTERVAL_DAY": {
                        "active_primary": {
                            **legacy_owner["active_primary"],
                            "candle_interval": "CANDLE_INTERVAL_DAY",
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = bot.run_once()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    scope = saved["execution_scopes"][bot.execution_scope_key]

    assert result["status"] == "processed"
    assert result["executed"] is True
    assert "migration_conflict" not in scope
    assert scope["active_primary"]["config_hash"] == bot.primary_config_hash
    assert scope.get("migration_conflict_resolved_at")


def test_v351_observability_separates_decision_from_order(tmp_path: Path):
    from trading_robot.journal import EventJournal

    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    config = make_config(state_file, dry_run=True)
    bot = SandboxTradingBot(api, "account-1", config, allow_execution=False)
    result = bot.run_once()
    bot.end_session("test_complete")

    assert result["session_id"]
    assert result["cycle_duration_seconds"] >= 0
    assert "candle_load" in result["timings"]
    assert "strategy_evaluation" in result["timings"]

    journal = EventJournal(tmp_path / "trading_events.db")
    rows = journal.recent(limit=100)
    categories = {row["category"] for row in rows}
    assert {"session", "cycle", "decision", "strategy"}.issubset(categories)
    assert not any(
        row["category"] == "order" and row["event_type"] == "DECISION_CREATED"
        for row in rows
    )
    cycle = next(row for row in rows if row["category"] == "cycle")
    assert cycle["session_id"] == result["session_id"]
    assert cycle["duration_seconds"] is not None
    assert cycle["status"] == result["status"]


def test_dry_run_uses_explicit_would_buy_label(tmp_path: Path):
    api = FakeSandboxAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=True),
        allow_execution=False,
    )

    result = bot.run_once()

    assert result["action"] == "WOULD_BUY"
    assert result["intended_action"] == "BUY"
    assert result["order_was_sent"] is False
    assert api.post_count == 0


def test_executed_order_keeps_buy_label_and_marks_order_sent(tmp_path: Path):
    api = FakeSandboxAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=False),
        allow_execution=True,
    )

    result = bot.run_once()

    assert result["action"] == "BUY"
    assert result["intended_action"] == "BUY"
    assert result["order_was_sent"] is True
    assert api.post_count == 1


def test_state_save_failure_returns_safe_result_and_does_not_submit(
    tmp_path: Path,
    monkeypatch,
):
    from trading_robot.state_persistence import StatePersistenceError

    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"

    def fail_save(path, state, **_kwargs):
        raise StatePersistenceError(
            path,
            phase="replace",
            attempts=8,
            last_error=PermissionError("temporarily locked"),
        )

    monkeypatch.setattr("trading_robot.bot.atomic_write_json", fail_save)
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(state_file, dry_run=False),
        allow_execution=True,
    )

    result = bot.run_once()

    assert result["status"] == "state_save_failed"
    assert result["new_orders_blocked"] is True
    assert result["order_was_sent"] is False
    assert result["api_state"] == "LOCAL_STATE_ERROR"
    assert api.post_count == 0


def test_api_degraded_separates_current_and_last_known_decisions(tmp_path: Path):
    from trading_robot.tbank_sandbox import TBankAPIError

    class FailAfterSuccessAPI(FakeSandboxAPI):
        def __init__(self):
            super().__init__()
            self.fail = False

        def get_candles(self, *args, **kwargs):
            if self.fail:
                raise TBankAPIError(
                    "temporary outage",
                    transient=True,
                    service="MarketDataService",
                    method="GetCandles",
                )
            return super().get_candles(*args, **kwargs)

    api = FailAfterSuccessAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=True),
        allow_execution=False,
    )
    first = bot.run_once()
    assert first["status"] == "processed"

    api.fail = True
    degraded = bot.run_once()

    assert degraded["status"] == "api_degraded"
    assert degraded["strategy_decisions"] is None
    assert degraded["strategy_comparison"] is None
    assert isinstance(degraded["last_known_strategy_decisions"], dict)
    assert degraded["last_known_strategy_decisions"]["sma"]["signal"] == 1


def test_strategy_decision_is_linked_to_each_new_session(tmp_path: Path):
    from trading_robot.journal import EventJournal

    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    journal_file = tmp_path / "events.db"
    from dataclasses import replace

    config = replace(
        make_config(state_file, dry_run=True),
        journal_file=str(journal_file),
    )

    first_bot = SandboxTradingBot(
        api, "account-1", config, allow_execution=False
    )
    first_bot.run_once()
    first_bot.end_session("test")

    second_bot = SandboxTradingBot(
        api, "account-1", config, allow_execution=False
    )
    second = second_bot.run_once()
    second_bot.end_session("test")

    assert second["status"] == "already_processed"
    rows = EventJournal(journal_file).recent(category="strategy", limit=20)
    primary_rows = [row for row in rows if row["event_type"] == "PRIMARY_DECISION"]
    assert len(primary_rows) == 2
    assert {row["session_id"] for row in primary_rows} == {
        first_bot.session_id,
        second_bot.session_id,
    }


def test_state_save_failure_after_submission_recovers_without_duplicate(
    tmp_path: Path,
    monkeypatch,
):
    from trading_robot.state_persistence import (
        StatePersistenceError,
        atomic_write_json as real_atomic_write_json,
    )

    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    call_count = 0

    def fail_after_provider_acceptance(path, state, **kwargs):
        nonlocal call_count
        call_count += 1
        # Save #1: INTENT_SAVED. Save #2: ORDER_SUBMITTED. The provider
        # accepts the order next; save #3 persists ORDER_ACCEPTED and fails.
        if call_count == 3:
            raise StatePersistenceError(
                path,
                phase="replace",
                attempts=8,
                last_error=PermissionError("temporarily locked"),
            )
        return real_atomic_write_json(path, state, **kwargs)

    monkeypatch.setattr(
        "trading_robot.bot.atomic_write_json",
        fail_after_provider_acceptance,
    )
    config = make_config(state_file, dry_run=False)
    first_bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )
    first = first_bot.run_once()

    assert first["status"] == "state_save_failed"
    assert first["order_was_sent"] is True
    assert first["order_may_have_been_sent"] is True
    assert api.post_count == 1

    # Restore persistence and restart. The disk still contains the previously
    # saved ORDER_SUBMITTED intent, so recovery uses the same deterministic ID.
    monkeypatch.setattr(
        "trading_robot.bot.atomic_write_json",
        real_atomic_write_json,
    )
    second_bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )
    recovered = second_bot.run_once()

    assert recovered["status"] == "order_recovered"
    assert recovered["position_reconciled"] is True
    assert api.post_count == 1


def test_state_save_retry_and_recovery_are_journalled(
    tmp_path: Path,
    monkeypatch,
):
    import os

    from trading_robot import state_persistence
    from trading_robot.journal import EventJournal

    real_replace = os.replace
    calls = 0

    def fail_once(source, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(13, "temporary lock", str(target))
        return real_replace(source, target)

    monkeypatch.setattr(state_persistence.os, "replace", fail_once)
    monkeypatch.setattr(state_persistence.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(state_persistence.random, "uniform", lambda _a, _b: 0.0)

    journal_file = tmp_path / "events.db"
    from dataclasses import replace

    config = replace(
        make_config(tmp_path / "state.json", dry_run=True),
        journal_file=str(journal_file),
    )
    bot = SandboxTradingBot(
        FakeSandboxAPI(),
        "account-1",
        config,
        allow_execution=False,
    )

    result = bot.run_once()

    assert result["status"] == "processed"
    events = EventJournal(journal_file).recent(category="state", limit=20)
    assert {row["event_type"] for row in events} == {
        "STATE_SAVE_RETRY",
        "STATE_SAVE_RECOVERED",
    }
    assert all(row["session_id"] == bot.session_id for row in events)


def test_state_failure_before_provider_call_blocks_order_but_marks_ambiguity(
    tmp_path: Path,
    monkeypatch,
):
    from trading_robot.state_persistence import (
        StatePersistenceError,
        atomic_write_json as real_atomic_write_json,
    )

    api = FakeSandboxAPI()
    call_count = 0

    def fail_second_save(path, state, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise StatePersistenceError(
                path,
                phase="replace",
                attempts=8,
                last_error=PermissionError("temporarily locked"),
            )
        return real_atomic_write_json(path, state, **kwargs)

    monkeypatch.setattr("trading_robot.bot.atomic_write_json", fail_second_save)
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path / "state.json", dry_run=False),
        allow_execution=True,
    )

    result = bot.run_once()

    assert result["status"] == "state_save_failed"
    assert result["order_was_sent"] is False
    assert result["order_may_have_been_sent"] is True
    assert api.post_count == 0


def test_already_processed_cycle_performs_periodic_portfolio_reconciliation(
    tmp_path: Path,
):
    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=3,
        dry_run=False,
        state_file=str(state_file),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
        portfolio_reconcile_interval_seconds=60,
    )
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )
    first = bot.run_once()
    second = bot.run_once()
    assert first["status"] == "processed"
    assert second["status"] == "already_processed"
    assert second["periodic_reconciliation"]["performed"] is True
    assert second["periodic_reconciliation"]["position_reconciled"] is True


def test_periodic_reconciliation_detects_external_position_drift(tmp_path: Path):
    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=3,
        dry_run=False,
        state_file=str(state_file),
        journal_file=str(tmp_path / "events.db"),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
        portfolio_reconcile_interval_seconds=60,
    )
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
    )
    first = bot.run_once()
    api.current_lots = 0  # external/manual drift after confirmed BUY
    second = bot.run_once()
    assert first["executed"] is True
    assert second["status"] == "position_mismatch"
    assert second["new_orders_blocked"] is True
    assert second["periodic_reconciliation"]["current_lots"] == 0
    assert api.post_count == 1
    risk_events = EventJournal(tmp_path / "events.db").recent(
        limit=20,
        category="risk",
        event_type="EXTERNAL_ACTIVITY_DETECTED",
    )
    assert len(risk_events) == 1
    assert risk_events[0]["payload"]["risk_resync_required"] is True
    assert risk_events[0]["payload"]["delta_lots"] == -first["actual_lots_after"]


def test_periodic_position_mismatch_remains_blocked_on_later_heartbeat(tmp_path: Path):
    api = FakeSandboxAPI()
    state_file = tmp_path / "state.json"
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=3,
        dry_run=False,
        state_file=str(state_file),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
        portfolio_reconcile_interval_seconds=60,
    )
    bot = SandboxTradingBot(api, "account-1", config, allow_execution=True)
    first = bot.run_once()
    api.current_lots = 0
    second = bot.run_once()

    # Force the next heartbeat to be due without changing the model candle.
    state = json.loads(state_file.read_text(encoding="utf-8"))
    bot_state = state["bots"][bot.state_key]
    bot_state["last_periodic_reconcile_at"] = "2000-01-01T00:00:00+00:00"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    third = bot.run_once()

    assert first["executed"] is True
    assert second["status"] == "position_mismatch"
    assert third["status"] == "position_mismatch"
    assert third["periodic_reconciliation"]["target_lots"] == first["target_lots"]
    assert api.post_count == 1


def test_circuit_transition_events_include_half_open_and_closed(tmp_path: Path):
    from datetime import datetime, timedelta, timezone
    import json

    from trading_robot.journal import EventJournal
    from trading_robot.tbank_sandbox import TBankAPIError

    class RecoveringAPI(FakeSandboxAPI):
        def __init__(self):
            super().__init__()
            self.failures_remaining = 2

        def get_candles(self, *args, **kwargs):
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise TBankAPIError(
                    "temporary timeout",
                    transient=True,
                    service="MarketDataService",
                    method="GetCandles",
                )
            return super().get_candles(*args, **kwargs)

    state_file = tmp_path / "state.json"
    journal_file = tmp_path / "events.db"
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        dry_run=True,
        state_file=str(state_file),
        journal_file=str(journal_file),
        failure_threshold=2,
        circuit_open_seconds=60,
    )
    bot = SandboxTradingBot(RecoveringAPI(), "account-1", config)

    assert bot.run_once()["status"] == "api_degraded"
    assert bot.run_once()["status"] == "circuit_open"

    state = json.loads(state_file.read_text(encoding="utf-8"))
    bot_state = next(iter(state["bots"].values()))
    bot_state["health"]["open_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    state_file.write_text(json.dumps(state), encoding="utf-8")

    recovered = bot.run_once()
    assert recovered["status"] == "processed"
    assert recovered["circuit_state"] == "CLOSED"

    events = EventJournal(journal_file).recent(
        limit=100,
        category="resilience",
    )
    event_types = {item["event_type"] for item in events}
    assert {"CIRCUIT_OPEN", "CIRCUIT_HALF_OPEN", "CIRCUIT_CLOSED"}.issubset(
        event_types
    )
