from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest

from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.journal import EventJournal
from trading_robot.risk import RiskEngine, RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def q(value: float) -> dict[str, int | str]:
    units = int(value)
    nano = int(round((value - units) * 1_000_000_000))
    return {"units": str(units), "nano": nano}


def rising_candles(periods: int = 500) -> pd.DataFrame:
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


class SandboxRiskAPI:
    def __init__(self) -> None:
        self.post_count = 0
        self.order_ids: list[str] = []
        self.current_lots = 0
        self.executed_lots = 1
        self.executed_price = 201.5
        self.order_status = "EXECUTION_REPORT_STATUS_FILL"

    def find_instrument(self, query: str, class_code: str):
        return {
            "ticker": query,
            "classCode": class_code,
            "uid": "instrument-uid",
            "figi": "figi-1",
            "lot": 10,
            "apiTradeAvailableFlag": True,
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_candles(self, *args, **kwargs):
        return rising_candles()

    def get_portfolio(self, account_id: str):
        positions = []
        if self.current_lots:
            positions.append(
                {
                    "instrumentUid": "instrument-uid",
                    "quantityLots": q(float(self.current_lots)),
                }
            )
        securities = self.current_lots * self.executed_price * 10
        return {
            "positions": positions,
            "totalAmountPortfolio": q(50_000.0),
            "totalAmountCurrencies": q(50_000.0 - securities),
            "totalAmountShares": q(securities),
            "totalAmountBonds": q(0.0),
            "totalAmountEtf": q(0.0),
            "totalAmountFutures": q(0.0),
            "totalAmountOptions": q(0.0),
            "totalAmountSp": q(0.0),
        }

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions") or []
        if not positions:
            return 0
        return int(float(positions[0]["quantityLots"]["units"]))

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

    def post_order(self, account_id, instrument_id, lots, action, *, order_id, **kwargs):
        self.post_count += 1
        self.order_ids.append(order_id)
        signed = int(lots) if str(action).upper() == "BUY" else -int(lots)
        self.current_lots = max(0, self.current_lots + signed)
        return self._order_payload(order_id)

    def get_order_state(self, account_id: str, order_id: str, by_request_id=True):
        return self._order_payload(order_id)

    def _order_payload(self, order_id: str):
        return {
            "orderId": order_id,
            "executionReportStatus": self.order_status,
            "lotsExecuted": str(self.executed_lots),
            "executedOrderPrice": q(self.executed_price),
        }


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


def make_config(tmp_path: Path, *, max_lots: int = 1) -> BotConfig:
    return BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=5,
        slow_window=20,
        lookback_days=30,
        max_order_lots=max_lots,
        dry_run=False,
        state_file=str(tmp_path / "robot_state.json"),
        journal_file=str(tmp_path / "events.db"),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
    )


def make_runtime(tmp_path: Path, policy: RiskPolicy) -> RiskRuntimeAdapter:
    profile_store = RiskProfileStore(tmp_path / "risk_profiles.json")
    profile_store.save_profile("SANDBOX_EXECUTION", policy)
    return RiskRuntimeAdapter(
        account_id="account-1",
        mode="SANDBOX_EXECUTION",
        profile_store=profile_store,
        state_store=RiskStateStore(tmp_path / "risk_state.json"),
        auto_create_dry_run_profile=False,
    )


def make_bot(
    tmp_path: Path,
    api: SandboxRiskAPI,
    runtime: RiskRuntimeAdapter,
    *,
    max_lots: int = 1,
) -> SandboxTradingBot:
    return SandboxTradingBot(
        api,
        "account-1",
        make_config(tmp_path, max_lots=max_lots),
        allow_execution=True,
        risk_runtime=runtime,
        require_risk_runtime_for_execution=True,
    )


def test_sandbox_execution_requires_risk_runtime(tmp_path: Path):
    with pytest.raises(ValueError, match="requires a RiskRuntimeAdapter"):
        SandboxTradingBot(
            SandboxRiskAPI(),
            "account-1",
            make_config(tmp_path),
            allow_execution=True,
            require_risk_runtime_for_execution=True,
        )


def test_sandbox_pass_records_risk_before_and_after_order(tmp_path: Path):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(max_position_lots=1))
    result = make_bot(tmp_path, api, runtime).run_once()

    assert result["status"] == "processed"
    assert result["risk_status"] == "PASS"
    assert result["strategy_target_lots"] == 1
    assert result["target_lots"] == 1
    assert result["executed_lots"] == 1
    assert result["risk_decision_id"]
    assert result["risk_execution_status"] == "RECORDED"
    assert result["pending_order"] is None
    assert api.post_count == 1

    state = RiskStateStore(tmp_path / "risk_state.json").load_account("account-1")
    assert state.daily_order_count == 1
    assert state.daily_turnover_rub == pytest.approx(201.5 * 10)
    assert len(state.recorded_execution_ids) == 1

    order_events = EventJournal(tmp_path / "events.db").recent(
        limit=50,
        category="order",
    )
    intent_event = next(
        event for event in order_events if event["event_type"] == "INTENT_SAVED"
    )
    assert intent_event["payload"]["risk_decision_id"] == result["risk_decision_id"]
    assert intent_event["payload"]["risk_policy_hash"] == result["risk_policy_hash"]
    assert any(event["event_type"] == "RISK_ACCOUNTED" for event in order_events)

    ordered = list(
        reversed(
            EventJournal(tmp_path / "events.db").recent(
                limit=100, order_id=result["order_id"]
            )
        )
    )
    names = [(event["category"], event["event_type"]) for event in ordered]
    filled_index = names.index(("order", "FILLED"))
    reconciled_index = names.index(("order", "PORTFOLIO_RECONCILED"))
    execution_index = names.index(("risk", "EXECUTION_RECORDED"))
    accounted_index = names.index(("order", "RISK_ACCOUNTED"))
    assert filled_index < reconciled_index < execution_index < accounted_index
    risk_payload = ordered[execution_index]["payload"]
    assert risk_payload["execution_source"] == "STRATEGY"
    assert risk_payload["reconciliation_proof"]["position_reconciled"] is True


def test_sandbox_adjusted_submits_only_approved_lots(tmp_path: Path):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(max_position_lots=1))
    result = make_bot(tmp_path, api, runtime, max_lots=3).run_once()

    assert result["risk_status"] == "ADJUSTED"
    assert result["strategy_target_lots"] == 3
    assert result["target_lots"] == 1
    assert result["lots"] == 1
    assert result["executed_lots"] == 1
    assert api.current_lots == 1
    assert api.post_count == 1


def test_sandbox_blocked_creates_no_intent_or_order(tmp_path: Path):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(max_position_lots=0))
    bot = make_bot(tmp_path, api, runtime, max_lots=3)

    first = bot.run_once()
    second = bot.run_once()

    assert first["status"] == "risk_blocked"
    assert second["status"] == "risk_blocked_heartbeat"
    assert second["risk_block_repeated"] is True
    assert first["action"] == "RISK_BLOCKED"
    assert first["target_lots"] == 0
    assert api.post_count == 0
    journal = EventJournal(tmp_path / "events.db")
    order_events = journal.recent(limit=50, category="order")
    assert not any(event["event_type"] == "INTENT_SAVED" for event in order_events)
    risk_events = journal.recent(limit=50, category="risk")
    assert sum(
        event["event_type"] == "RISK_EVALUATED" for event in risk_events
    ) == 1
    cycle_events = journal.recent(limit=50, category="cycle")
    assert sum(event["event_type"] == "risk_blocked" for event in cycle_events) == 1
    assert not any(
        event["event_type"] == "risk_blocked_heartbeat"
        for event in cycle_events
    )


def test_corrupt_sandbox_profile_fails_closed_before_order(tmp_path: Path):
    api = SandboxRiskAPI()
    profile_path = tmp_path / "risk_profiles.json"
    profile_path.write_text("{broken", encoding="utf-8")
    runtime = RiskRuntimeAdapter(
        account_id="account-1",
        mode="SANDBOX_EXECUTION",
        profile_store=RiskProfileStore(profile_path),
        state_store=RiskStateStore(tmp_path / "risk_state.json"),
        auto_create_dry_run_profile=False,
    )
    result = make_bot(tmp_path, api, runtime).run_once()

    assert result["status"] == "risk_blocked"
    assert result["risk_status"] == "RUNTIME_ERROR"
    assert result["target_lots"] == 0
    assert api.post_count == 0


def test_risk_accounting_failure_keeps_pending_and_recovery_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(max_position_lots=1))
    original_update = runtime.state_store.update_account
    calls = {"count": 0}

    def fail_execution_accounting_once(account_id, updater):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated risk state write failure")
        return original_update(account_id, updater)

    monkeypatch.setattr(
        runtime.state_store,
        "update_account",
        fail_execution_accounting_once,
    )
    bot = make_bot(tmp_path, api, runtime)

    first = bot.run_once()
    assert first["status"] == "risk_accounting_pending"
    assert first["position_reconciled"] is True
    assert first["pending_order"] is not None
    assert api.post_count == 1
    state_before = RiskStateStore(tmp_path / "risk_state.json").load_account(
        "account-1"
    )
    assert state_before.daily_order_count == 0

    second = bot.run_once()
    assert second["status"] == "order_recovered"
    assert second["pending_order"] is None
    assert second["position_reconciled"] is True
    assert api.post_count == 1
    state_after = RiskStateStore(tmp_path / "risk_state.json").load_account(
        "account-1"
    )
    assert state_after.daily_order_count == 1
    assert len(state_after.recorded_execution_ids) == 1

    # A direct replay of the same broker execution remains idempotent.
    duplicate = runtime.record_execution(
        execution_id=api.order_ids[0],
        executed_at=NOW,
        signed_lots=1,
        price_rub=api.executed_price,
        lot_size=10,
        portfolio=api.get_portfolio("account-1"),
        decision_id=first["risk_decision_id"],
        expected_policy_hash=first["risk_policy_hash"],
        price_source="test",
        execution_source="STRATEGY",
        reconciliation_confirmed_at=NOW.isoformat(),
        reconciliation_proof={
            "position_reconciled": True,
            "account_id": "account-1",
            "instrument_id": "instrument-uid",
            "expected_lots_after": 1,
            "actual_lots_after": 1,
            "confirmed_at": NOW.isoformat(),
        },
    )
    assert duplicate.status == "DUPLICATE"
    final_state = RiskStateStore(tmp_path / "risk_state.json").load_account(
        "account-1"
    )
    assert final_state.daily_order_count == 1


def test_disabled_sandbox_policy_is_fail_closed(tmp_path: Path):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(enabled=False))
    result = make_bot(tmp_path, api, runtime).run_once()

    assert result["status"] == "risk_blocked"
    assert result["risk_status"] == "RUNTIME_ERROR"
    assert "cannot disable" in " ".join(result["risk_reasons"])
    assert api.post_count == 0


def test_kill_switch_blocks_new_sandbox_buy(tmp_path: Path):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(max_position_lots=1))
    state_store = RiskStateStore(tmp_path / "risk_state.json")
    state = state_store.load_account("account-1")
    halted, _event = RiskEngine(permissive_policy()).engage_kill_switch(
        state,
        now=NOW,
        reason="integration test",
    )
    state_store.save_account("account-1", halted)

    result = make_bot(tmp_path, api, runtime).run_once()

    assert result["status"] == "risk_blocked"
    assert result["risk_status"] == "BLOCKED"
    assert "KILL_SWITCH" in result["risk_breaches"]
    assert api.post_count == 0


def test_external_position_drift_sets_persistent_risk_resync_gate(tmp_path: Path):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(max_position_lots=1))
    config = make_config(tmp_path)
    config = replace(
        config,
        portfolio_reconcile_interval_seconds=60,
    )
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
        risk_runtime=runtime,
        require_risk_runtime_for_execution=True,
    )

    first = bot.run_once()
    api.current_lots = 0
    second = bot.run_once()

    assert first["executed"] is True
    assert second["status"] == "position_mismatch"
    risk_state = RiskStateStore(tmp_path / "risk_state.json").load_account(
        "account-1"
    )
    assert risk_state.risk_resync_required is True
    events = EventJournal(tmp_path / "events.db").recent(
        limit=20,
        category="risk",
        event_type="EXTERNAL_ACTIVITY_DETECTED",
    )
    assert len(events) == 1
    assert events[0]["payload"]["risk_resync_persisted"] is True

    blocked = runtime.evaluate(
        now=NOW,
        strategy_target_lots=0,
        current_lots=0,
        price_rub=200.0,
        lot_size=10,
        portfolio=api.get_portfolio("account-1"),
        candles=rising_candles(),
        atr_window=20,
        stop_level=None,
        position_reconciled=True,
        pending_order=False,
        snapshot_at=NOW,
        decision_context="post-drift",
    )
    assert blocked.status == "HALTED"
    assert "RISK_RESYNC_REQUIRED" in blocked.assessment.decision.breaches


def test_external_activity_clears_only_after_explicit_risk_resync(tmp_path: Path):
    api = SandboxRiskAPI()
    runtime = make_runtime(tmp_path, permissive_policy(max_position_lots=1))
    config = replace(
        make_config(tmp_path),
        portfolio_reconcile_interval_seconds=60,
    )
    bot = SandboxTradingBot(
        api,
        "account-1",
        config,
        allow_execution=True,
        risk_runtime=runtime,
        require_risk_runtime_for_execution=True,
    )

    assert bot.run_once()["executed"] is True
    api.current_lots = 0
    assert bot.run_once()["status"] == "position_mismatch"

    def make_periodic_due() -> None:
        path = tmp_path / "robot_state.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        for state in document["bots"].values():
            state["last_periodic_reconcile_at"] = "2000-01-01T00:00:00+00:00"
        path.write_text(json.dumps(document), encoding="utf-8")

    api.current_lots = 1
    make_periodic_due()
    reconciled = bot.run_once()
    assert reconciled["status"] == "already_processed"
    assert reconciled["periodic_reconciliation"]["position_reconciled"] is True
    assert reconciled["periodic_reconciliation"]["external_activity_active"] is True
    assert runtime.risk_resync_required() is True

    state_store = RiskStateStore(tmp_path / "risk_state.json")
    reset, events = RiskEngine(permissive_policy()).reset_baselines(
        state_store.load_account("account-1"),
        now=NOW,
        equity_rub=50_000.0,
        confirmation="RESET RISK BASELINES",
    )
    state_store.save_account("account-1", reset)
    assert [event.event_type for event in events][-1] == "RISK_RESYNC_COMPLETED"

    make_periodic_due()
    cleared = bot.run_once()
    assert cleared["periodic_reconciliation"]["position_reconciled"] is True
    assert cleared["periodic_reconciliation"]["external_activity_active"] is False
    event_names = [
        row["event_type"]
        for row in reversed(
            EventJournal(tmp_path / "events.db").recent(
                limit=50,
                category="risk",
            )
        )
    ]
    assert "EXTERNAL_ACTIVITY_DETECTED" in event_names
    assert "EXTERNAL_POSITION_RECONCILED" in event_names
    assert "EXTERNAL_ACTIVITY_CLEARED" in event_names
