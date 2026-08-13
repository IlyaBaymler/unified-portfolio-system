from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from tools import v3_8_sandbox_acceptance as acceptance
from trading_robot.bot import BotConfig
from trading_robot.central_order_manager import CentralOrderManager, CentralOrderStore
from trading_robot.config_persistence import bot_config_to_profile
from trading_robot.instrument_runtime import InstrumentRuntime, InstrumentRuntimeStore
from trading_robot.multi_instrument_config import (
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)
from trading_robot.portfolio_model import PortfolioState
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore

ACCOUNT = "sandbox-account-1"
INSTRUMENT = "uid-sber"


def quotation(value: float) -> dict[str, int]:
    units = int(value)
    return {
        "units": units,
        "nano": round((value - units) * 1_000_000_000),
    }


def permissive_policy(*, max_position_lots: int = 100) -> RiskPolicy:
    return RiskPolicy(
        max_position_lots=max_position_lots,
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


def prepare_runtime(
    root: Path,
    *,
    max_order_lots: int = 1,
    max_position_lots: int = 100,
    annual_target_volatility: float | None = None,
) -> None:
    config = BotConfig(
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        shadow_strategies=(),
        fast_window=2,
        slow_window=5,
        sma_hysteresis_percent=0.0,
        volatility_window=5,
        annual_target_volatility=annual_target_volatility,
        lookback_days=5,
        max_order_lots=max_order_lots,
    )
    profile = MultiInstrumentProfile(
        instrument_id=INSTRUMENT,
        strategy_profile=bot_config_to_profile(
            config,
            connect_timeout_seconds=8,
            read_timeout_seconds=25,
        ),
        scheduler_cadence_seconds=1,
        decision_cadence_seconds=1,
        risk_refresh_cadence_seconds=30,
        reconciliation_cadence_seconds=60,
        market_status_cadence_seconds=15,
    )
    MultiInstrumentProfileStore(
        root / "multi_instrument_profiles.json"
    ).save_mode("SANDBOX_EXECUTION", (profile,))
    InstrumentRuntimeStore(root / "instrument_runtimes.json").save(
        (InstrumentRuntime(profile.to_runtime_config(ACCOUNT)),)
    )
    PortfolioRepository(root / "portfolio_state.json").save(
        PortfolioState.empty(account_id=ACCOUNT)
    )
    CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    RiskProfileStore(root / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        permissive_policy(max_position_lots=max_position_lots),
        account_scope=ACCOUNT,
        source="V3_8_ACCEPTANCE_FLOW_TEST",
    )


class FakeSandboxClient:
    def __init__(self) -> None:
        self.position_lots = 0
        self.post_count = 0
        self.order_id: str | None = None
        self.broker_order_id: str | None = None
        self.requested_lots = 0
        self.executed_lots = 0
        self.direction = "BUY"
        self.trend_up = True
        self.trend_step = 0.1
        self.volatility_amplitude = 0.0
        self.next_executed_lots: int | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def find_instrument(self, query: str, class_code: str):
        assert (query, class_code) == ("SBER", "TQBR")
        return {
            "uid": INSTRUMENT,
            "figi": "BBG004730N88",
            "ticker": "SBER",
            "classCode": "TQBR",
            "lot": 10,
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_candles(
        self,
        instrument_id: str,
        from_time: datetime,
        to_time: datetime,
        *,
        interval: str,
        limit: int | None,
    ) -> pd.DataFrame:
        assert instrument_id == INSTRUMENT
        assert interval == "CANDLE_INTERVAL_HOUR"
        index = pd.date_range(
            end=to_time.replace(minute=0, second=0, microsecond=0),
            periods=120,
            freq="h",
        )
        close = [
            (
                100.0
                + number * self.trend_step
                + self.volatility_amplitude * (1 if number % 2 else -1)
            )
            if self.trend_up
            else 120.0 - number * 0.1
            for number in range(len(index))
        ]
        return pd.DataFrame(
            {
                "open": close,
                "high": [value + 0.5 for value in close],
                "low": [value - 0.5 for value in close],
                "close": close,
                "volume": [1000] * len(index),
                "is_complete": [True] * len(index),
            },
            index=index,
        )

    def get_portfolio(self, account_id: str):
        assert account_id == ACCOUNT
        positions = []
        if self.position_lots:
            positions.append(
                {
                    "instrumentUid": INSTRUMENT,
                    "figi": "BBG004730N88",
                    "instrumentType": "share",
                    "quantityLots": quotation(float(self.position_lots)),
                    "quantity": quotation(float(self.position_lots * 10)),
                    "averagePositionPrice": quotation(100.0),
                    "currentPrice": quotation(100.0),
                    "expectedYield": quotation(0.0),
                }
            )
        cash = 1_000_000.0 - self.position_lots * 1_000.0
        return {
            "positions": positions,
            "totalAmountPortfolio": quotation(1_000_000.0),
            "totalAmountCurrencies": quotation(cash),
            "totalAmountShares": quotation(self.position_lots * 1_000.0),
            "expectedYield": quotation(0.0),
        }

    def get_orders(self, account_id: str):
        assert account_id == ACCOUNT
        return []

    def get_trading_status(self, instrument_id: str):
        assert instrument_id == INSTRUMENT
        return {
            "apiTradeAvailableFlag": True,
            "bestpriceOrderAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "marketOrderAvailableFlag": True,
        }

    def post_order(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        *,
        order_id: str,
        order_type: str,
        time_in_force: str,
    ):
        assert account_id == ACCOUNT
        assert instrument_id == INSTRUMENT
        assert lots > 0
        assert direction in {"BUY", "SELL"}
        assert order_type == "BESTPRICE"
        assert time_in_force == "FILL_AND_KILL"
        self.post_count += 1
        self.requested_lots = lots
        self.executed_lots = (
            lots if self.next_executed_lots is None else self.next_executed_lots
        )
        assert 0 < self.executed_lots <= lots
        self.direction = direction
        delta = self.executed_lots if direction == "BUY" else -self.executed_lots
        self.position_lots += delta
        assert self.position_lots >= 0
        self.order_id = order_id
        self.broker_order_id = f"broker-order-{self.post_count}"
        self.next_executed_lots = None
        return self._terminal_order()

    def get_order_state(
        self,
        account_id: str,
        order_id: str,
        *,
        by_request_id: bool = True,
    ):
        assert account_id == ACCOUNT
        assert by_request_id is True
        assert order_id == self.order_id
        return self._terminal_order()

    def _terminal_order(self):
        status = (
            "EXECUTION_REPORT_STATUS_FILL"
            if self.executed_lots == self.requested_lots
            else "EXECUTION_REPORT_STATUS_PARTIALLYFILL"
        )
        return {
            "orderId": self.broker_order_id,
            "orderRequestId": self.order_id,
            "executionReportStatus": status,
            "lotsRequested": str(self.requested_lots),
            "lotsExecuted": str(self.executed_lots),
            "executedOrderPrice": quotation(100.0),
        }


def cli_args(root: Path, action: str, *extra: str):
    return acceptance.parse_args(
        [
            action,
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            *extra,
        ]
    )


def test_operator_prepare_dispatch_reconcile_flow_is_single_submit_and_accounted(
    tmp_path: Path,
):
    prepare_runtime(tmp_path)
    client = FakeSandboxClient()
    client_factory = lambda *args, **kwargs: client
    credential = {acceptance.TOKEN_KEY: "secret-canary"}

    prepared = acceptance.run(
        cli_args(
            tmp_path,
            "prepare-one",
            "--instrument-id",
            INSTRUMENT,
            "--confirm",
            acceptance.PREPARE_CONFIRMATION,
        ),
        environ=credential,
        client_factory=client_factory,
    )
    intent_id = prepared["intent_id"]
    dispatched = acceptance.run(
        cli_args(
            tmp_path,
            "dispatch-one",
            "--intent-id",
            intent_id,
            "--confirm",
            "ENABLE V3.8 SANDBOX EXECUTION",
        ),
        environ={
            **credential,
            acceptance.ARM_ENV_NAME: "YES",
        },
        client_factory=client_factory,
    )
    reconciled = acceptance.run(
        cli_args(
            tmp_path,
            "reconcile",
            "--intent-id",
            intent_id,
            "--confirm",
            acceptance.RECONCILIATION_CONFIRMATION,
        ),
        environ=credential,
        client_factory=client_factory,
    )

    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    ).state()
    runtime = InstrumentRuntimeStore(
        tmp_path / "instrument_runtimes.json"
    ).load(expected_account_id=ACCOUNT)[0]
    risk = RiskStateStore(tmp_path / "risk_state.json").load_account(ACCOUNT)
    portfolio = PortfolioRepository(tmp_path / "portfolio_state.json").load(
        expected_account_id=ACCOUNT
    )

    assert prepared["status"] == "QUEUED"
    assert prepared["runtime_sync_warning"] is None
    assert dispatched["status"] == "SUBMITTED"
    assert reconciled["status"] == "RECONCILED"
    assert reconciled["risk_execution_status"] == "RECORDED"
    assert reconciled["runtime_sync_warning"] is None
    assert client.post_count == 1
    assert central.blocking_intent is None
    assert central.queued == ()
    assert runtime.status == "ACTIVE"
    assert runtime.current_lots == 1
    assert runtime.pending_order_ids == ()
    assert risk.daily_order_count == 1
    assert risk.daily_turnover_rub == 1_000.0
    assert risk.recorded_execution_ids == (intent_id,)
    assert portfolio.position(INSTRUMENT).actual_lots == 1
    assert "secret-canary" not in str((prepared, dispatched, reconciled))


def save_risk_limit(root: Path, max_position_lots: int) -> None:
    RiskProfileStore(root / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        permissive_policy(max_position_lots=max_position_lots),
        account_scope=ACCOUNT,
        source="V3_8_MULTI_LOT_ACCEPTANCE_TEST",
    )


def execute_full_transition(
    root: Path,
    client: FakeSandboxClient,
    *,
    current_lots: int,
    target_lots: int,
) -> tuple[dict, dict, dict, dict]:
    credential = {acceptance.TOKEN_KEY: "secret-canary"}
    posts_before = client.post_count
    prepared = acceptance.run(
        cli_args(
            root,
            "prepare-one",
            "--instrument-id",
            INSTRUMENT,
            "--confirm",
            acceptance.PREPARE_CONFIRMATION,
        ),
        environ=credential,
        client_factory=lambda *args, **kwargs: client,
    )
    intent_id = prepared["intent_id"]
    central = CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=ACCOUNT,
    ).state()
    assert prepared["status"] == "QUEUED", prepared
    queued = central.queued[0]
    assert prepared["current_lots"] == current_lots
    assert prepared["approved_target_lots"] == target_lots
    assert queued.intent_id == intent_id
    assert queued.candidate.current_lots == current_lots
    assert queued.candidate.target_lots == target_lots
    assert queued.candidate.requested_lots == abs(target_lots - current_lots)
    if target_lots > current_lots:
        assert central.reserved_cash_kopecks > 0
    else:
        assert central.reserved_cash_kopecks == 0

    dispatched = acceptance.run(
        cli_args(
            root,
            "dispatch-one",
            "--intent-id",
            intent_id,
            "--confirm",
            acceptance.SANDBOX_EXECUTION_CONFIRMATION,
        ),
        environ={**credential, acceptance.ARM_ENV_NAME: "YES"},
        client_factory=lambda *args, **kwargs: client,
    )
    assert dispatched["status"] == "SUBMITTED"
    assert dispatched["executed_lots"] == abs(target_lots - current_lots)
    assert client.post_count == posts_before + 1

    restarted = CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    assert restarted.recover_after_restart() is None
    assert restarted.state().blocking_intent.intent_id == intent_id
    inspected = acceptance.run(
        cli_args(root, "inspect"),
        environ=credential,
        client_factory=lambda *args, **kwargs: client,
    )
    assert inspected["status"] == "ORDER_OBSERVED"
    assert inspected["terminal"] is True
    assert inspected["suggested_reconciliation_outcome"] == "FILLED"
    assert client.post_count == posts_before + 1

    reconciled = acceptance.run(
        cli_args(
            root,
            "reconcile",
            "--intent-id",
            intent_id,
            "--confirm",
            acceptance.RECONCILIATION_CONFIRMATION,
        ),
        environ=credential,
        client_factory=lambda *args, **kwargs: client,
    )
    assert reconciled["status"] == "RECONCILED"
    assert reconciled["outcome"] == "FILLED"
    assert reconciled["executed_lots"] == abs(target_lots - current_lots)
    assert reconciled["risk_execution_status"] == "RECORDED"
    assert reconciled["runtime_sync_warning"] is None
    assert client.position_lots == target_lots
    assert client.post_count == posts_before + 1
    return prepared, dispatched, inspected, reconciled


def test_multi_lot_target_sequence_survives_restart_and_reconciles_each_step(
    tmp_path: Path,
):
    prepare_runtime(
        tmp_path,
        max_order_lots=5,
        max_position_lots=3,
        annual_target_volatility=0.1,
    )
    client = FakeSandboxClient()
    evidence = []

    evidence.append(
        execute_full_transition(
            tmp_path,
            client,
            current_lots=0,
            target_lots=3,
        )
    )
    save_risk_limit(tmp_path, 5)
    evidence.append(
        execute_full_transition(
            tmp_path,
            client,
            current_lots=3,
            target_lots=5,
        )
    )
    client.trend_step = 0.2
    client.volatility_amplitude = 0.2
    evidence.append(
        execute_full_transition(
            tmp_path,
            client,
            current_lots=5,
            target_lots=2,
        )
    )
    client.trend_up = False
    evidence.append(
        execute_full_transition(
            tmp_path,
            client,
            current_lots=2,
            target_lots=0,
        )
    )

    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    ).state()
    runtime = InstrumentRuntimeStore(
        tmp_path / "instrument_runtimes.json"
    ).load(expected_account_id=ACCOUNT)[0]
    risk = RiskStateStore(tmp_path / "risk_state.json").load_account(ACCOUNT)
    portfolio = PortfolioRepository(tmp_path / "portfolio_state.json").load(
        expected_account_id=ACCOUNT
    )

    assert client.post_count == 4
    assert len(central.intents) == 4
    assert len({item.intent_id for item in central.intents}) == 4
    assert {item.status for item in central.intents} == {"RECONCILED"}
    assert {item.outcome for item in central.intents} == {"FILLED"}
    assert central.blocking_intent is None
    assert central.queued == ()
    assert central.reserved_cash_kopecks == 0
    assert runtime.status == "ACTIVE"
    assert runtime.current_lots == 0
    assert runtime.pending_order_ids == ()
    assert risk.daily_order_count == 4
    assert risk.daily_turnover_rub == 10_000.0
    assert len(risk.recorded_execution_ids) == 4
    assert portfolio.position(INSTRUMENT).actual_lots == 0
    assert portfolio.position(INSTRUMENT).target_lots == 0
    assert portfolio.state_status == "READY"
    assert "secret-canary" not in str(evidence)


def test_partial_fill_is_accounted_then_remaining_multi_lot_target_is_safe(
    tmp_path: Path,
):
    prepare_runtime(
        tmp_path,
        max_order_lots=5,
        max_position_lots=5,
    )
    client = FakeSandboxClient()
    client.next_executed_lots = 3
    credential = {acceptance.TOKEN_KEY: "secret-canary"}

    prepared = acceptance.run(
        cli_args(
            tmp_path,
            "prepare-one",
            "--instrument-id",
            INSTRUMENT,
            "--confirm",
            acceptance.PREPARE_CONFIRMATION,
        ),
        environ=credential,
        client_factory=lambda *args, **kwargs: client,
    )
    intent_id = prepared["intent_id"]
    dispatched = acceptance.run(
        cli_args(
            tmp_path,
            "dispatch-one",
            "--intent-id",
            intent_id,
            "--confirm",
            acceptance.SANDBOX_EXECUTION_CONFIRMATION,
        ),
        environ={**credential, acceptance.ARM_ENV_NAME: "YES"},
        client_factory=lambda *args, **kwargs: client,
    )
    inspected = acceptance.run(
        cli_args(tmp_path, "inspect"),
        environ=credential,
        client_factory=lambda *args, **kwargs: client,
    )
    reconciled = acceptance.run(
        cli_args(
            tmp_path,
            "reconcile",
            "--intent-id",
            intent_id,
            "--confirm",
            acceptance.RECONCILIATION_CONFIRMATION,
        ),
        environ=credential,
        client_factory=lambda *args, **kwargs: client,
    )

    assert prepared["approved_target_lots"] == 5
    assert dispatched["executed_lots"] == 3
    assert inspected["suggested_reconciliation_outcome"] == "PARTIALLY_FILLED"
    assert reconciled["outcome"] == "PARTIALLY_FILLED"
    assert reconciled["executed_lots"] == 3
    assert client.position_lots == 3
    execute_full_transition(
        tmp_path,
        client,
        current_lots=3,
        target_lots=5,
    )

    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    ).state()
    risk = RiskStateStore(tmp_path / "risk_state.json").load_account(ACCOUNT)
    assert [item.outcome for item in central.intents] == [
        "PARTIALLY_FILLED",
        "FILLED",
    ]
    assert risk.daily_order_count == 2
    assert risk.daily_turnover_rub == 5_000.0
    assert client.post_count == 2
