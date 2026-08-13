from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from trading_robot.bot import BotConfig
from trading_robot.central_order_coordinator import CentralOrderCoordinator
from trading_robot.central_order_manager import CentralOrderManager, CentralOrderStore
from trading_robot.config_persistence import bot_config_to_profile
from trading_robot.instrument_runtime import InstrumentRuntime
from trading_robot.multi_instrument_config import MultiInstrumentProfile
from trading_robot.multi_instrument_strategy import StrategyProposal
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    PortfolioState,
    SnapshotFreshness,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter, RiskRuntimeOutcome
from trading_robot.strategy_runtime import StrategyDecision

ACCOUNT = "sandbox-account-1"
NOW_DT = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
NOW = NOW_DT.isoformat()


def profile(ticker: str, interval: str) -> MultiInstrumentProfile:
    config = BotConfig(
        ticker=ticker,
        class_code="TQBR",
        candle_interval=interval,
        primary_strategy="sma",
        shadow_strategies=(),
        fast_window=2,
        slow_window=5,
        volatility_window=5,
        lookback_days=5,
        max_order_lots=3,
    )
    return MultiInstrumentProfile(
        instrument_id=f"uid-{ticker.lower()}",
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


def runtime(selected: MultiInstrumentProfile) -> InstrumentRuntime:
    return InstrumentRuntime(
        config=selected.to_runtime_config(ACCOUNT),
        status="ACTIVE",
    )


def proposal(
    selected: MultiInstrumentProfile,
    selected_runtime: InstrumentRuntime,
    *,
    target_lots: int = 1,
    close: float = 100.0,
) -> StrategyProposal:
    decision = StrategyDecision(
        candle_time=NOW,
        strategy_id="sma",
        strategy_version="1",
        role="PRIMARY",
        config_hash=selected.strategy_profile_hash,
        signal=1 if target_lots else 0,
        target_weight=1.0 if target_lots else 0.0,
        target_lots=target_lots,
        reason="test",
        indicators={"close": close},
        bars_used=10,
        required_bars=5,
    )
    return StrategyProposal(
        runtime_key=selected_runtime.runtime_key,
        instrument_id=selected.instrument_id,
        ticker=selected.ticker,
        candle_interval=selected.candle_interval,
        candle_time=NOW,
        strategy_profile_hash=selected.strategy_profile_hash,
        primary_strategy="sma",
        primary_target_lots=target_lots,
        decisions={"sma": decision},
        comparison={},
        generated_at=NOW,
    )


def candles() -> pd.DataFrame:
    index = pd.date_range(end=NOW_DT, periods=20, freq="h")
    close = [100.0 + number for number in range(len(index))]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value + 1.0 for value in close],
            "low": [value - 1.0 for value in close],
            "close": close,
        },
        index=index,
    )


def portfolio_state(
    *,
    revision: int = 0,
    cash: float = 1_000_000.0,
    freshness: SnapshotFreshness = SnapshotFreshness.FRESH,
) -> PortfolioState:
    snapshot = (NOW_DT.replace(day=13 + revision)).isoformat()
    return PortfolioState(
        version=2,
        account=AccountState(
            account_id=ACCOUNT,
            total_value=cash,
            securities_value=0.0,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", cash),),
        ),
        snapshot_at=snapshot,
        generated_at=snapshot,
        freshness=freshness,
        source="PORTFOLIO_MANAGER",
        positions=(),
        warnings=(),
        state_status="READY",
        blocking=False,
        revision=revision,
    )


def repository(root: Path, state: PortfolioState | None = None) -> PortfolioRepository:
    result = PortfolioRepository(root / "portfolio_state.json")
    result.save(state or portfolio_state())
    return result


def manager(root: Path) -> CentralOrderManager:
    return CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=ACCOUNT,
    )


class FakeRiskRuntime:
    account_id = ACCOUNT
    mode = "SANDBOX_EXECUTION"

    def __init__(self, *, approved_target_lots: int | None = None) -> None:
        self.approved_target_lots = approved_target_lots
        self.calls: list[dict] = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        approved = (
            int(kwargs["strategy_target_lots"])
            if self.approved_target_lots is None
            else self.approved_target_lots
        )
        current = int(kwargs["current_lots"])
        status = (
            "ADJUSTED"
            if approved != int(kwargs["strategy_target_lots"])
            else "PASS"
        )
        decision = SimpleNamespace(
            status=status,
            policy_hash="b" * 64,
            requested_target_lots=int(kwargs["strategy_target_lots"]),
            approved_target_lots=approved,
            current_lots=current,
            order_allowed=approved != current,
            reasons=("test risk decision",),
        )
        preflight = kwargs["portfolio_preflight"]
        return RiskRuntimeOutcome(
            enforced=True,
            mode=self.mode,
            approved_target_lots=approved,
            assessment=SimpleNamespace(decision=decision),
            decision_id=(
                f"risk-{preflight['snapshot_revision']}-"
                f"{kwargs['strategy_target_lots']}"
            ),
            portfolio_revision=preflight["snapshot_revision"],
            portfolio_decision_checksum=(
                preflight["snapshot_decision_checksum"]
            ),
        )


def coordinator(
    root: Path,
    risk: FakeRiskRuntime,
    *,
    selected_repository=None,
) -> CentralOrderCoordinator:
    return CentralOrderCoordinator(
        manager(root),
        selected_repository or repository(root),
        risk,
    )


def test_two_instruments_are_queued_sequentially_without_broker_authorization(
    tmp_path: Path,
):
    repo = repository(tmp_path)
    central = manager(tmp_path)
    risk = FakeRiskRuntime()
    service = CentralOrderCoordinator(central, repo, risk)
    sber = profile("SBER", "CANDLE_INTERVAL_HOUR")
    lkoh = profile("LKOH", "CANDLE_INTERVAL_30_MIN")
    sber_runtime = runtime(sber)
    lkoh_runtime = runtime(lkoh)

    first = service.coordinate(
        proposal(sber, sber_runtime),
        sber_runtime,
        sber,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )
    second = service.coordinate(
        proposal(lkoh, lkoh_runtime, close=200.0),
        lkoh_runtime,
        lkoh,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    state = central.state()
    assert first.status == "QUEUED"
    assert second.status == "QUEUED"
    assert first.broker_execution_authorized is False
    assert [item.candidate.ticker for item in state.queued] == ["SBER", "LKOH"]
    assert state.reserved_cash_kopecks == 303_000
    assert len(risk.calls) == 2


def test_existing_intent_is_reauthorized_after_portfolio_revision_change(
    tmp_path: Path,
):
    repo = repository(tmp_path)
    central = manager(tmp_path)
    risk = FakeRiskRuntime()
    service = CentralOrderCoordinator(central, repo, risk)
    lkoh = profile("LKOH", "CANDLE_INTERVAL_30_MIN")
    selected_runtime = runtime(lkoh)
    selected_proposal = proposal(lkoh, selected_runtime, close=200.0)

    first = service.coordinate(
        selected_proposal,
        selected_runtime,
        lkoh,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )
    repo.save(
        portfolio_state(revision=1, cash=900_000.0),
        expected_revision=0,
    )
    refreshed = service.coordinate(
        selected_proposal,
        selected_runtime,
        lkoh,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    queued = central.state().queued[0]
    assert refreshed.status == "REAUTHORIZED"
    assert refreshed.intent_id == first.intent_id == queued.intent_id
    assert queued.authorization.portfolio_revision == 1
    assert queued.authorization.available_cash_kopecks == 90_000_000


def test_risk_adjusted_target_is_the_only_target_saved_in_candidate(tmp_path: Path):
    repo = repository(tmp_path)
    central = manager(tmp_path)
    risk = FakeRiskRuntime(approved_target_lots=1)
    service = CentralOrderCoordinator(central, repo, risk)
    sber = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(sber)

    result = service.coordinate(
        proposal(sber, selected_runtime, target_lots=2),
        selected_runtime,
        sber,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    queued = central.state().queued[0]
    assert result.status == "QUEUED"
    assert result.proposed_target_lots == 2
    assert result.approved_target_lots == 1
    assert queued.candidate.target_lots == 1
    assert queued.authorization.authorized_target_lots == 1


def test_no_position_change_is_decided_after_risk_evaluation(tmp_path: Path):
    repo = repository(tmp_path)
    central = manager(tmp_path)
    risk = FakeRiskRuntime()
    service = CentralOrderCoordinator(central, repo, risk)
    sber = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(sber)

    result = service.coordinate(
        proposal(sber, selected_runtime, target_lots=0),
        selected_runtime,
        sber,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "NO_POSITION_CHANGE"
    assert result.preflight_status == "PASS"
    assert result.risk_status == "PASS"
    assert len(risk.calls) == 1
    assert central.state().queued == ()


def test_preflight_block_does_not_call_risk_or_create_intent(tmp_path: Path):
    repo = repository(
        tmp_path,
        portfolio_state(freshness=SnapshotFreshness.STALE),
    )
    central = manager(tmp_path)
    risk = FakeRiskRuntime()
    service = CentralOrderCoordinator(central, repo, risk)
    sber = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(sber)

    result = service.coordinate(
        proposal(sber, selected_runtime),
        selected_runtime,
        sber,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "PREFLIGHT_BLOCKED"
    assert risk.calls == []
    assert central.state().queued == ()


def test_canonical_change_after_risk_leaves_queue_untouched(tmp_path: Path):
    initial = portfolio_state()
    changed = portfolio_state(revision=1, cash=900_000.0)

    class ChangingRepository:
        def __init__(self) -> None:
            self.calls = 0

        def load(self, *, expected_account_id=None):
            assert expected_account_id == ACCOUNT
            self.calls += 1
            return initial if self.calls == 1 else changed

    changing = ChangingRepository()
    central = manager(tmp_path)
    risk = FakeRiskRuntime()
    service = CentralOrderCoordinator(central, changing, risk)
    sber = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(sber)

    result = service.coordinate(
        proposal(sber, selected_runtime),
        selected_runtime,
        sber,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "CANONICAL_CHANGED"
    assert len(risk.calls) == 1
    assert central.state().queued == ()


def test_real_sandbox_risk_runtime_authorizes_coordinator_intent(tmp_path: Path):
    repo = repository(tmp_path)
    central = manager(tmp_path)
    profiles = RiskProfileStore(tmp_path / "risk_profiles.json")
    profiles.save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(
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
        ),
        account_scope=ACCOUNT,
        source="V3_8_COORDINATOR_TEST",
    )
    state_store = RiskStateStore(tmp_path / "risk_state.json")
    risk = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profiles,
        state_store=state_store,
    )
    service = CentralOrderCoordinator(central, repo, risk)
    sber = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(sber)

    result = service.coordinate(
        proposal(sber, selected_runtime),
        selected_runtime,
        sber,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    queued = central.state().queued[0]
    risk_state = state_store.load_account(ACCOUNT)
    assert result.status == "QUEUED"
    assert result.risk_status == "PASS"
    assert queued.authorization.risk_policy_hash == profiles.require_profile(
        "SANDBOX_EXECUTION"
    )["policy_hash"]
    assert risk_state.last_evaluated_at == NOW
