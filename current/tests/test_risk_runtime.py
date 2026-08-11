from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.journal import EventJournal
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def q(value: float) -> dict[str, int]:
    units = int(value)
    nano = int(round((value - units) * 1_000_000_000))
    return {"units": units, "nano": nano}


def candles(periods: int = 500) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=periods, freq="h", tz="UTC")
    close = np.linspace(100.0, 200.0, periods)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000,
            "is_complete": True,
        },
        index=index,
    )


class RiskAwareFakeAPI:
    def __init__(self) -> None:
        self.post_count = 0
        self.current_lots = 0

    def find_instrument(self, query: str, class_code: str):
        return {
            "ticker": query,
            "classCode": class_code,
            "uid": "instrument-uid",
            "lot": 10,
            "apiTradeAvailableFlag": True,
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_candles(self, *args, **kwargs):
        return candles()

    def get_portfolio(self, account_id: str):
        positions = []
        if self.current_lots:
            positions.append(
                {
                    "instrumentUid": "instrument-uid",
                    "quantityLots": {"units": str(self.current_lots), "nano": 0},
                }
            )
        return {
            "positions": positions,
            "totalAmountPortfolio": q(50_000.0),
            "totalAmountCurrencies": q(50_000.0),
            "totalAmountShares": q(0.0),
            "totalAmountBonds": q(0.0),
            "totalAmountEtf": q(0.0),
            "totalAmountFutures": q(0.0),
            "totalAmountOptions": q(0.0),
            "totalAmountSp": q(0.0),
        }

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions") or []
        return int(positions[0]["quantityLots"]["units"]) if positions else 0

    def get_trading_status(self, instrument_id: str):
        return {
            "apiTradeAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "marketOrderAvailableFlag": True,
            "bestpriceOrderAvailableFlag": True,
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
        return 100

    def post_order(self, *args, **kwargs):
        self.post_count += 1
        raise AssertionError("Dry-run must never submit an order")


def make_runtime(tmp_path: Path, *, policy: RiskPolicy | None = None):
    profiles = RiskProfileStore(tmp_path / "risk_profiles.json")
    if policy is not None:
        profiles.save_profile("DRY_RUN", policy)
    return RiskRuntimeAdapter(
        account_id="account-1",
        mode="DRY_RUN",
        profile_store=profiles,
        state_store=RiskStateStore(tmp_path / "risk_state.json"),
    )


def make_config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=3,
        dry_run=True,
        state_file=str(tmp_path / "robot_state.json"),
        journal_file=str(tmp_path / "events.db"),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
    )


def permissive_policy(**overrides) -> RiskPolicy:
    values = dict(
        max_position_lots=100,
        max_position_value_rub=1_000_000.0,
        max_position_share_of_equity=1.0,
        max_order_value_rub=1_000_000.0,
        cash_reserve_rub=0.0,
        commission_buffer_fraction=0.0,
        risk_per_trade_rub=None,
        risk_per_trade_fraction=None,
        daily_loss_limit_rub=None,
        daily_loss_limit_fraction=None,
        weekly_loss_limit_rub=None,
        weekly_loss_limit_fraction=None,
        max_drawdown_fraction=None,
        max_daily_turnover_rub=None,
        max_orders_per_day=None,
        max_snapshot_age_seconds=300,
    )
    values.update(overrides)
    return RiskPolicy(**values)


def test_runtime_auto_creates_default_profile_and_persists_state(tmp_path: Path):
    runtime = make_runtime(tmp_path)
    outcome = runtime.evaluate(
        now=NOW,
        strategy_target_lots=1,
        current_lots=0,
        price_rub=100.0,
        lot_size=10,
        portfolio=RiskAwareFakeAPI().get_portfolio("account-1"),
        candles=candles(),
        atr_window=20,
        stop_level=None,
        snapshot_at=NOW,
    )

    assert outcome.error is None
    assert outcome.profile_auto_created is True
    assert outcome.status == "PASS"
    assert outcome.approved_target_lots == 1
    assert (tmp_path / "risk_profiles.json").exists()
    state = RiskStateStore(tmp_path / "risk_state.json").load_account("account-1")
    assert state.daily_start_equity_rub == pytest.approx(50_000.0)
    assert state.last_evaluated_at is not None


def test_runtime_corrupt_profile_fails_closed(tmp_path: Path):
    (tmp_path / "risk_profiles.json").write_text("{broken", encoding="utf-8")
    runtime = make_runtime(tmp_path)
    outcome = runtime.evaluate(
        now=NOW,
        strategy_target_lots=3,
        current_lots=1,
        price_rub=100.0,
        lot_size=10,
        portfolio=RiskAwareFakeAPI().get_portfolio("account-1"),
        candles=candles(),
        atr_window=20,
        stop_level=None,
        snapshot_at=NOW,
    )

    assert outcome.status == "RUNTIME_ERROR"
    assert outcome.approved_target_lots == 1
    assert outcome.error


def test_alpha3_sandbox_runtime_missing_profile_fails_closed(tmp_path: Path):
    runtime = RiskRuntimeAdapter(
        account_id="account-1",
        mode="SANDBOX_EXECUTION",
        profile_store=RiskProfileStore(tmp_path / "profiles.json"),
        state_store=RiskStateStore(tmp_path / "state.json"),
    )
    outcome = runtime.evaluate(
        now=NOW,
        strategy_target_lots=1,
        current_lots=0,
        price_rub=100.0,
        lot_size=10,
        portfolio=RiskAwareFakeAPI().get_portfolio("account-1"),
        candles=candles(),
        atr_window=20,
        stop_level=None,
        snapshot_at=NOW,
        decision_context="test-candle",
    )

    assert outcome.status == "RUNTIME_ERROR"
    assert outcome.approved_target_lots == 0
    assert "SANDBOX_EXECUTION" in str(outcome.error)


def test_bot_dry_run_uses_risk_approved_target(tmp_path: Path):
    policy = permissive_policy(max_position_lots=1)
    runtime = make_runtime(tmp_path, policy=policy)
    api = RiskAwareFakeAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path),
        allow_execution=False,
        risk_runtime=runtime,
    )

    result = bot.run_once()

    assert result["status"] == "processed"
    assert result["strategy_target_lots"] == 3
    assert result["target_lots"] == 1
    assert result["risk_status"] == "ADJUSTED"
    assert result["action"] == "WOULD_BUY"
    assert result["lots"] == 1
    assert result["order_was_sent"] is False
    assert api.post_count == 0

    risk_events = EventJournal(tmp_path / "events.db").recent(
        limit=20,
        category="risk",
    )
    assert any(event["event_type"] == "RISK_EVALUATED" for event in risk_events)
    state_doc = json.loads((tmp_path / "robot_state.json").read_text(encoding="utf-8"))
    stored = next(iter(state_doc["bots"].values()))["last_risk_decision"]
    assert stored["status"] == "ADJUSTED"


def test_bot_risk_runtime_error_is_retryable_and_not_consumed(tmp_path: Path):
    (tmp_path / "risk_profiles.json").write_text("{broken", encoding="utf-8")
    runtime = make_runtime(tmp_path)
    api = RiskAwareFakeAPI()
    bot = SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path),
        allow_execution=False,
        risk_runtime=runtime,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "risk_blocked"
    assert second["status"] == "risk_blocked"
    assert first["action"] == "RISK_BLOCKED"
    assert first["strategy_target_lots"] == 3
    assert first["target_lots"] == 0
    assert first["new_orders_blocked"] is True
    assert api.post_count == 0


def test_bot_rejects_risk_runtime_mode_mismatch(tmp_path: Path):
    runtime = make_runtime(tmp_path, policy=permissive_policy())
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=3,
        dry_run=False,
        state_file=str(tmp_path / "robot_state.json"),
        journal_file=str(tmp_path / "events.db"),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
    )
    with pytest.raises(ValueError, match="mode mismatch"):
        SandboxTradingBot(
            RiskAwareFakeAPI(),
            "account-1",
            config,
            allow_execution=True,
            risk_runtime=runtime,
        )


def test_same_candle_reuses_saved_risk_decision_without_duplicate_event(tmp_path: Path):
    runtime = make_runtime(tmp_path, policy=permissive_policy(max_position_lots=1))
    bot = SandboxTradingBot(
        RiskAwareFakeAPI(),
        "account-1",
        make_config(tmp_path),
        allow_execution=False,
        risk_runtime=runtime,
    )

    first = bot.run_once()
    second = bot.run_once()

    assert first["risk_status"] == "ADJUSTED"
    assert second["status"] == "already_processed"
    assert second["target_lots"] == 1
    assert second["risk_status"] == "ADJUSTED"
    risk_events = EventJournal(tmp_path / "events.db").recent(
        limit=50,
        category="risk",
    )
    evaluated = [event for event in risk_events if event["event_type"] == "RISK_EVALUATED"]
    assert len(evaluated) == 1


def test_execution_accounting_requires_reconciliation_proof(tmp_path: Path):
    runtime = make_runtime(tmp_path, policy=permissive_policy())
    outcome = runtime.record_execution(
        execution_id="exec-without-proof",
        executed_at=NOW,
        signed_lots=1,
        price_rub=100.0,
        lot_size=10,
        portfolio=RiskAwareFakeAPI().get_portfolio("account-1"),
        decision_id="decision-1",
        expected_policy_hash=permissive_policy().policy_hash,
        execution_source="STRATEGY",
    )

    assert outcome.status == "RUNTIME_ERROR"
    assert "reconciliation" in str(outcome.error).lower()
    state = RiskStateStore(tmp_path / "risk_state.json").load_account("account-1")
    assert state.daily_order_count == 0
    assert state.daily_turnover_rub == 0.0
    assert state.recorded_execution_ids == ()


def test_external_execution_is_idempotent_and_preserves_source(tmp_path: Path):
    runtime = make_runtime(tmp_path, policy=permissive_policy())
    proof = {
        "position_reconciled": True,
        "expected_lots_after": 0,
        "actual_lots_after": 0,
        "account_id": "account-1",
        "instrument_id": "instrument-uid",
    }
    kwargs = dict(
        execution_id="diagnostic-sell-1",
        executed_at=NOW,
        signed_lots=-1,
        price_rub=101.0,
        lot_size=10,
        portfolio=RiskAwareFakeAPI().get_portfolio("account-1"),
        expected_policy_hash=permissive_policy().policy_hash,
        price_source="executedOrderPrice",
        execution_source="DIAGNOSTIC",
        reconciliation_confirmed_at=NOW.isoformat(),
        reconciliation_proof=proof,
    )

    first = runtime.record_execution(**kwargs)
    second = runtime.record_execution(**kwargs)

    assert first.status == "RECORDED"
    assert first.execution_source == "DIAGNOSTIC"
    assert first.reconciliation_proof == proof
    assert second.status == "DUPLICATE"
    state = RiskStateStore(tmp_path / "risk_state.json").load_account("account-1")
    assert state.daily_order_count == 1
    assert state.daily_turnover_rub == pytest.approx(1010.0)
    assert state.recorded_execution_ids == ("diagnostic-sell-1",)


def test_normal_local_snapshot_capture_delay_does_not_trigger_future_block(tmp_path: Path):
    runtime = make_runtime(
        tmp_path,
        policy=permissive_policy(max_snapshot_age_seconds=60),
    )
    outcome = runtime.evaluate(
        now=NOW,
        strategy_target_lots=1,
        current_lots=0,
        price_rub=200.0,
        lot_size=10,
        portfolio=RiskAwareFakeAPI().get_portfolio("account-1"),
        candles=candles(),
        atr_window=20,
        stop_level=None,
        snapshot_at=NOW + timedelta(seconds=5),
        decision_context="normal-api-latency",
    )
    assert outcome.status == "PASS"
    assert outcome.assessment is not None
    assert "SNAPSHOT_FROM_FUTURE" not in outcome.assessment.decision.breaches
    assert outcome.assessment.decision.metrics["snapshot_age_seconds"] == 0.0


def test_large_snapshot_clock_skew_still_fails_closed(tmp_path: Path):
    runtime = make_runtime(
        tmp_path,
        policy=permissive_policy(max_snapshot_age_seconds=60),
    )
    outcome = runtime.evaluate(
        now=NOW,
        strategy_target_lots=1,
        current_lots=0,
        price_rub=200.0,
        lot_size=10,
        portfolio=RiskAwareFakeAPI().get_portfolio("account-1"),
        candles=candles(),
        atr_window=20,
        stop_level=None,
        snapshot_at=NOW + timedelta(seconds=120),
        decision_context="real-clock-skew",
    )
    assert outcome.status == "BLOCKED"
    assert outcome.assessment is not None
    assert "SNAPSHOT_FROM_FUTURE" in outcome.assessment.decision.breaches
