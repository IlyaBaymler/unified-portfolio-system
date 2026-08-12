from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.crash_injection import CrashInjector, SimulatedProcessCrash
from trading_robot.orders import OrderLifecycle
from trading_robot.recovery import (
    RecoveryAction,
    StartupRecoveryCoordinator,
)
from trading_robot.state_persistence import atomic_write_json, read_json_verified
from trading_robot.tbank_sandbox import TBankAPIError


class RecoveryAPI:
    def __init__(self) -> None:
        self.post_count = 0
        self.lookup_count = 0
        self.lookup_404 = False
        self.current_lots = 0
        self.order_status = "EXECUTION_REPORT_STATUS_FILL"
        self.executed_lots = 1

    def find_instrument(self, query: str, class_code: str):
        return {
            "ticker": query,
            "classCode": class_code,
            "uid": "instrument-uid",
            "lot": 1,
            "apiTradeAvailableFlag": True,
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_candles(self, *args, **kwargs):
        index = pd.date_range("2026-01-01", periods=500, freq="h", tz="UTC")
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
        if not self.current_lots:
            return {
                "positions": [],
                "totalAmountPortfolio": 50_000,
                "totalAmountCurrencies": 50_000,
                "totalAmountShares": 0,
            }
        return {
            "positions": [
                {
                    "instrumentUid": "instrument-uid",
                    "quantityLots": {"units": str(self.current_lots), "nano": 0},
                }
            ],
            "totalAmountPortfolio": 50_000,
            "totalAmountCurrencies": 49_800,
            "totalAmountShares": 200,
        }

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions", [])
        return int(positions[0]["quantityLots"]["units"]) if positions else 0

    def get_trading_status(self, instrument_id: str):
        return {
            "apiTradeAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "marketOrderAvailableFlag": True,
        }

    def get_max_lots(self, account_id: str, instrument_id: str, price=None):
        return {"buyLimits": {"buyMaxMarketLots": "10"}}

    @staticmethod
    def max_buy_lots(response):
        return int(response["buyLimits"]["buyMaxMarketLots"])

    @staticmethod
    def best_price_available(status):
        return True

    @staticmethod
    def market_order_available(status):
        return True

    def post_order(self, *args, order_id: str, **kwargs):
        self.post_count += 1
        return {
            "orderId": order_id,
            "executionReportStatus": self.order_status,
            "lotsExecuted": str(self.executed_lots),
        }

    def get_order_state(self, account_id: str, order_id: str, by_request_id=True):
        self.lookup_count += 1
        if self.lookup_404:
            raise TBankAPIError(
                "not found",
                status_code=404,
                transient=False,
                service="SandboxService",
                method="GetSandboxOrderState",
            )
        return {
            "orderId": order_id,
            "executionReportStatus": self.order_status,
            "lotsExecuted": str(self.executed_lots),
        }


def config(path: Path) -> BotConfig:
    return BotConfig(
        ticker="SBER",
        class_code="TQBR",
        fast_window=3,
        slow_window=8,
        max_order_lots=1,
        dry_run=False,
        state_file=str(path),
        max_signal_age_seconds=10**10,
        reconcile_delay_seconds=0,
    )


def pending(lifecycle: OrderLifecycle | str) -> dict:
    return {
        "order_id": "11111111-1111-5111-8111-111111111111",
        "lifecycle_state": str(lifecycle),
        "action": "BUY",
        "lots": 1,
        "current_lots_before": 0,
        "target_lots": 1,
        "expected_lots_after": 1,
        "executed_lots": 1,
        "last_known_status": "FILL",
        "time_in_force": "FILL_AND_KILL",
        "candle_time": "2026-01-01T10:00:00+00:00",
        "risk_enforced": False,
    }


@pytest.mark.parametrize(
    ("lifecycle", "action", "broker_lookup"),
    [
        (OrderLifecycle.INTENT_SAVED, RecoveryAction.CANCEL_INTENT_AND_REEVALUATE, False),
        (OrderLifecycle.ORDER_SUBMITTED, RecoveryAction.LOOKUP_BROKER_ORDER, True),
        (OrderLifecycle.UNKNOWN_SUBMIT_STATE, RecoveryAction.BLOCK_MANUAL_REVIEW, False),
        (OrderLifecycle.ORDER_ACCEPTED, RecoveryAction.LOOKUP_BROKER_ORDER, True),
        (OrderLifecycle.PARTIALLY_FILLED, RecoveryAction.LOOKUP_BROKER_ORDER, True),
        (OrderLifecycle.FILLED, RecoveryAction.RECONCILE_ONLY, False),
        (OrderLifecycle.RECONCILIATION_REQUIRED, RecoveryAction.RECONCILE_ONLY, False),
        (OrderLifecycle.PORTFOLIO_RECONCILED, RecoveryAction.RISK_ACCOUNT_ONLY, False),
        (OrderLifecycle.RISK_ACCOUNTING_REQUIRED, RecoveryAction.RISK_ACCOUNT_ONLY, False),
        (OrderLifecycle.RISK_ACCOUNTED, RecoveryAction.FINALIZE_ACCOUNTED, False),
        (OrderLifecycle.REJECTED, RecoveryAction.FINALIZE_FAILED, False),
    ],
)
def test_recovery_coordinator_matrix(lifecycle, action, broker_lookup):
    decision = StartupRecoveryCoordinator().assess(
        pending(lifecycle), allow_execution=True
    )
    assert decision.action == action
    assert decision.broker_lookup_required is broker_lookup
    assert decision.broker_resubmit_allowed is False
    assert bool(decision.recovery_id)


def test_recovery_blocks_incomplete_or_unknown_state():
    item = pending("ALIEN_STATE")
    assert (
        StartupRecoveryCoordinator().assess(item, allow_execution=True).action
        == RecoveryAction.BLOCK_MANUAL_REVIEW
    )
    item.pop("order_id")
    decision = StartupRecoveryCoordinator().assess(item, allow_execution=True)
    assert decision.action == RecoveryAction.BLOCK_MANUAL_REVIEW
    assert decision.safe_to_continue is False


def test_order_submitted_is_not_safe_to_continue_when_execution_is_disabled():
    decision = StartupRecoveryCoordinator().assess(
        pending(OrderLifecycle.ORDER_SUBMITTED), allow_execution=False
    )
    assert decision.action == RecoveryAction.LOOKUP_BROKER_ORDER
    assert decision.safe_to_continue is False
    assert decision.broker_resubmit_allowed is False


def test_ambiguous_submit_404_never_resubmits(tmp_path: Path):
    api = RecoveryAPI()
    api.lookup_404 = True
    state_path = tmp_path / "robot_state.json"
    bot = SandboxTradingBot(api, "account-1", config(state_path), allow_execution=True)
    root = {
        "version": 5,
        "bots": {bot.state_key: {"pending_order": pending(OrderLifecycle.ORDER_SUBMITTED)}},
        "strategy_states": {},
        "strategy_configs": {},
        "execution_scopes": {},
    }
    atomic_write_json(state_path, root, keep_last_good=True, write_checksum=True)

    result = bot.run_once()

    assert result["status"] == "unknown_submit_state"
    assert result["resubmitted"] is False
    assert api.lookup_count == 1
    assert api.post_count == 0
    saved = read_json_verified(state_path, supported_versions={5, 6})
    saved_pending = saved["bots"][bot.state_key]["pending_order"]
    assert saved_pending["lifecycle_state"] == str(OrderLifecycle.UNKNOWN_SUBMIT_STATE)


def test_pre_submit_intent_is_cleared_for_fresh_reevaluation(tmp_path: Path):
    api = RecoveryAPI()
    state_path = tmp_path / "robot_state.json"
    bot = SandboxTradingBot(api, "account-1", config(state_path), allow_execution=True)
    root = {
        "version": 5,
        "bots": {bot.state_key: {"pending_order": pending(OrderLifecycle.INTENT_SAVED)}},
        "strategy_states": {},
        "strategy_configs": {},
        "execution_scopes": {},
    }
    atomic_write_json(state_path, root, keep_last_good=True, write_checksum=True)

    result = bot.run_once()

    assert result["status"] == "intent_reevaluation_required"
    assert result["resubmitted"] is False
    assert api.post_count == 0
    saved = read_json_verified(state_path, supported_versions={5, 6})
    assert "pending_order" not in saved["bots"][bot.state_key]


def test_crash_injector_is_one_shot():
    injector = CrashInjector(phase="ORDER_SUBMITTED", once=True, enabled=True)
    with pytest.raises(SimulatedProcessCrash, match="ORDER_SUBMITTED") as caught:
        injector.checkpoint("ORDER_SUBMITTED", context={"order_id": "A"})
    assert caught.value.context["order_id"] == "A"
    injector.checkpoint("ORDER_SUBMITTED", context={"order_id": "A"})


def test_environment_crash_hook_requires_explicit_arm(monkeypatch):
    monkeypatch.setenv("MOEX_TEST_CRASH_AFTER_PHASE", "FILLED")
    monkeypatch.delenv("MOEX_ENABLE_TEST_CRASH_INJECTION", raising=False)
    safe = CrashInjector.from_environment()
    safe.checkpoint("FILLED")
    monkeypatch.setenv("MOEX_ENABLE_TEST_CRASH_INJECTION", "YES")
    armed = CrashInjector.from_environment()
    with pytest.raises(SimulatedProcessCrash, match="FILLED"):
        armed.checkpoint("FILLED")
