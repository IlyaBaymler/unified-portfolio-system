"""Natural Strategy -> GUI -> real Risk/Central, with no live transport.

These tests qualify admission and no-op routing, NOT filled broker lifecycles.
Only the external provider is synthetic; economic owners are real classes.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from test_portfolio_risk_runtime_v3_9 import services
from test_v3_10_issue72_gui_runtime import _profile
from trading_robot.central_order_coordinator import CentralOrderCoordinator
from trading_robot.central_order_manager import CentralOrderManager, CentralOrderStore
from trading_robot.gui_runtime_controller import (
    GuiRuntimeBlockedError, GuiRuntimeController, ProductionGuiCycleSource,
    _CoordinatingHooks,
)
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_model import (
    AccountState, CashBalance, OwnershipStatus, PortfolioState, PortfolioTarget,
    PositionOrigin, PositionOwnership, PositionState, ReconciliationResult,
    ReconciliationStatus, SnapshotFreshness,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.sandbox_execution_adapter import (
    SandboxExecutionAdapter, SandboxExecutionPolicy, SANDBOX_EXECUTION_CONFIRMATION,
)

ACCOUNT = "sandbox-account-1"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
UID = "uid-sber"


class DeterministicProvider:
    """Only external data are substituted; every order method is forbidden."""
    def __init__(self, target: int, *, fail_quote: bool = False):
        self.target = target
        self.fail_quote = fail_quote
        self.quote_calls = 0
        self.order_calls = 0
        self.market_calls = []
        self.market_open = False
        self.post_behavior = "forbidden"
        self.sent_ids = []
        self.quote_at = NOW
        self.clock_at = NOW
        self.on_quote = None

    def get_candles(self, instrument_id, from_time, to_time, *, interval, limit):
        # The actual SMA owner, not a forced signal/target, evaluates these bars.
        values = [100.0 + i for i in range(80)]
        if self.target == 0:
            values.reverse()
        return pd.DataFrame({
            "open": values, "high": [v + 1 for v in values],
            "low": [v - 1 for v in values], "close": values,
            "volume": [100] * len(values), "is_complete": [True] * len(values),
        }, index=pd.date_range(end=NOW - timedelta(hours=1), periods=80, freq="h"))

    def get_instrument_by_id(self, instrument_id):
        return {"uid": instrument_id, "lot": 10, "currency": "rub"}

    def get_last_prices(self, instrument_ids):
        self.quote_calls += 1
        if self.on_quote is not None:
            self.on_quote()
        if self.fail_quote:
            raise RuntimeError("PRIVATE_PROVIDER_CANARY")
        return [{"instrumentUid": instrument_ids[0],
                 "price": {"units": "105", "nano": 0},
                 "time": self.quote_at.isoformat()}]

    def get_trading_status(self, instrument_id):
        self.market_calls.append(instrument_id)
        return {
            "apiTradeAvailableFlag": self.market_open,
            "marketOrderAvailableFlag": self.market_open,
            "bestpriceOrderAvailableFlag": self.market_open,
            "limitOrderAvailableFlag": self.market_open,
            "tradingStatus": (
                "SECURITY_TRADING_STATUS_NORMAL_TRADING" if self.market_open
                else "SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING"
            ),
        }

    def post_order(self, *args, **kwargs):
        self.order_calls += 1
        self.sent_ids.append(kwargs.get("order_id"))
        if self.post_behavior == "ambiguous":
            raise TimeoutError("SYNTHETIC_TIMEOUT_NOT_A_REAL_PROVIDER")
        if self.post_behavior == "submitted":
            return {
                "orderId": "synthetic-exchange-order",
                "orderRequestId": kwargs["order_id"],
                "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
                "lotsExecuted": "0",
            }
        raise AssertionError("ORDER_FORBIDDEN_IN_ADMISSION_QUALIFICATION")

    post_order_once = post_order


def _case(root: Path, *, current: int, target: int, fail_quote: bool = False,
          cash: float = 1_000_000.0, empty_status: str = "EMPTY"):
    profile_store = MultiInstrumentProfileStore(root / "multi_instrument_profiles.json")
    profiles = profile_store.save_mode("SANDBOX_EXECUTION", (
        _profile("SBER", "CANDLE_INTERVAL_HOUR"),
        _profile("LKOH", "CANDLE_INTERVAL_HOUR"),
    ))
    runtime_store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    runtimes = profile_store.bootstrap_runtime_registry(
        mode="SANDBOX_EXECUTION", account_id=ACCOUNT, runtime_store=runtime_store,
    )
    runtimes = tuple(replace(r, status="ACTIVE") for r in runtimes)
    runtime_store.save(runtimes)
    runtime = next(r for r in runtimes if r.config.instrument_id == UID)
    profile = next(p for p in profiles if p.instrument_id == UID)
    positions = ()
    if current:
        positions = (PositionState(
            instrument_id=UID, figi="", ticker="SBER", class_code="TQBR",
            asset_type="share", currency="rub", quantity=current * 10,
            actual_lots=current, average_price=100.0, current_price=105.0,
            market_value=current * 1050.0, expected_yield=0.0,
            target=PortfolioTarget(UID, current, "sma", profile.strategy_profile_hash),
            ownership=PositionOwnership("sma", profile.strategy_profile_hash,
                                        "CANDLE_INTERVAL_HOUR"),
            ownership_status=OwnershipStatus.ATTRIBUTED, pending_orders=(),
            reconciliation=ReconciliationResult(
                UID, ReconciliationStatus.MATCHED, False, (), current, current,
            ), origin=PositionOrigin.STRATEGY,
        ),)
    state = PortfolioState(
        version=2, account=AccountState(ACCOUNT, cash + current * 1050.0,
                                       current * 1050.0, 0.0, (CashBalance("rub", cash),)),
        snapshot_at=NOW.isoformat(), generated_at=NOW.isoformat(),
        freshness=SnapshotFreshness.FRESH, source="PORTFOLIO_MANAGER",
        positions=positions, warnings=(), state_status="READY" if current else empty_status,
        blocking=False, revision=1,
    )
    repository = PortfolioRepository(root / "portfolio_state.json")
    repository.save(state)
    central = CentralOrderManager(CentralOrderStore(root / "central_order_state.json"),
                                  account_id=ACCOUNT)
    portfolio_risk, risk_profiles, risk_state, _ = services(root)
    risk = RiskRuntimeAdapter(account_id=ACCOUNT, mode="SANDBOX_EXECUTION",
                              profile_store=risk_profiles, state_store=risk_state)
    coordinator = CentralOrderCoordinator(central, repository, risk,
                                         portfolio_risk_runtime=portfolio_risk)
    provider = DeterministicProvider(target, fail_quote=fail_quote)
    adapter = SandboxExecutionAdapter(provider, central, SandboxExecutionPolicy(ACCOUNT, enabled=True,
                                      confirmation=SANDBOX_EXECUTION_CONFIRMATION),
                                      risk_runtime=risk, portfolio_risk_runtime=portfolio_risk)
    source = ProductionGuiCycleSource(
        provider=provider, profile_store=profile_store, runtime_store=runtime_store,
        risk_runtime=risk, portfolio_refresher=lambda: repository.load(expected_account_id=ACCOUNT),
        account_id=ACCOUNT, clock=lambda: provider.clock_at,
    )
    journal = EventJournal(root / "trading_events.db")
    # Use the normal controller constructor, not a fake coordinator/dispatcher.
    controller = GuiRuntimeController(
        profile_store=profile_store, runtime_store=runtime_store,
        portfolio_repository=repository, central_order_coordinator=coordinator,
        execution_adapter=adapter, portfolio_risk_runtime=portfolio_risk,
        cash_authority=adapter.cash_authority_manager, account_id=ACCOUNT,
        account_scope_sha256="a" * 64, cycle_source=source, journal=journal,
    )
    now, latest, hooks = source()
    return SimpleNamespace(
        controller=controller, provider=provider, central=central, repository=repository,
        risk=risk, portfolio_risk=portfolio_risk, runtime=runtime, profile=profile,
        hooks=hooks, now=now, candle=latest[runtime.runtime_key], journal=journal,
        coordinator=coordinator, adapter=adapter, source=source,
    )


def _cycle(case):
    return _CoordinatingHooks(case.controller, case.hooks).evaluate_closed_candle(
        case.runtime, case.candle, case.now,
    )


@pytest.mark.parametrize("current,target", [(0, 0), (1, 1)])
def test_natural_hold_uses_real_risk_without_quote_or_order(tmp_path, current, target):
    case = _case(tmp_path, current=current, target=target, fail_quote=True)
    before = case.central.state()
    result = _cycle(case)
    assert result.status == "NO_POSITION_CHANGE", result.to_dict()
    assert result.current_lots == result.approved_target_lots == current
    assert case.provider.quote_calls == case.provider.order_calls == 0
    assert case.central.state() == before
    event = case.journal.recent(event_type="PRIMARY_STRATEGY_DECISION")[0]
    assert event["payload"]["proposed_target_lots"] == target
    assert case.journal.recent(event_type="CENTRAL_COORDINATION_RESULT")[0]["action"] == "HOLD"


@pytest.mark.parametrize("current,target,side", [(0, 1, "BUY"), (1, 0, "SELL")])
def test_natural_buy_sell_reach_authoritative_admission_not_broker(tmp_path, current, target, side):
    case = _case(tmp_path, current=current, target=target)
    result = _cycle(case)
    assert result.status == "QUEUED", result.to_dict()
    intent = case.central.state().queued[0]
    assert result.intent_id == intent.intent_id
    assert intent.candidate.current_lots == current
    assert intent.candidate.target_lots == target
    assert intent.authorization.portfolio_risk.finalized is True
    assert case.provider.quote_calls == 1
    assert case.provider.order_calls == 0
    assert case.adapter.dispatch_next(case.repository).status == "MARKET_IDLE"
    assert case.journal.recent(event_type="CENTRAL_COORDINATION_RESULT")[0]["action"] == side


def test_natural_reauthorization_dispatches_only_the_same_intent(tmp_path):
    # Isolate dispatch routing on READY; R4 EMPTY-with-history remains separate.
    case = _case(tmp_path, current=0, target=1, empty_status="READY")
    first = _cycle(case)
    before = len(case.provider.market_calls)
    state = case.repository.load(expected_account_id=ACCOUNT)
    case.repository.save(replace(state, revision=state.revision + 1))
    second = _cycle(case)
    assert second.status == "REAUTHORIZED", second.to_dict()
    assert second.intent_id == first.intent_id
    assert len(case.provider.market_calls) == before + 1
    assert len(case.central.state().intents) == 1
    assert case.provider.order_calls == 0


def test_natural_replacement_reaches_dispatch_without_replaying_old_intent(tmp_path):
    # Isolate dispatch routing on READY; R4 EMPTY-with-history remains separate.
    case = _case(tmp_path, current=0, target=1, empty_status="READY")
    first = _cycle(case)
    before = len(case.provider.market_calls)
    frame = case.hooks.frames[UID].copy()
    frame.index += timedelta(minutes=5)
    case.hooks.frames[UID] = frame
    case.candle += timedelta(minutes=5)
    case.now += timedelta(minutes=5)
    case.provider.clock_at = case.now
    case.provider.quote_at = case.now
    second = _cycle(case)
    assert second.status == "REPLACED", second.to_dict()
    assert second.intent_id != first.intent_id
    assert second.cancelled_intent_id == first.intent_id
    assert len(case.provider.market_calls) == before + 1
    assert len(case.central.state().queued) == 1
    assert case.central.state().queued[0].intent_id == second.intent_id
    assert case.provider.order_calls == 0


def test_new_proposal_cannot_dispatch_an_unrelated_account_queue_head(tmp_path):
    # Isolate dispatch routing on READY; R4 EMPTY-with-history remains separate.
    case = _case(tmp_path, current=0, target=1, empty_status="READY")
    others = case.controller.runtime_store.load(expected_account_id=ACCOUNT)
    other = next(r for r in others if r.config.instrument_id != UID)
    # Existing queue head was admitted by the real Strategy and Risk owners.
    proposal = case.hooks.evaluate_closed_candle(other, case.candle, case.now)
    request = case.hooks.coordination_request(other, proposal, case.candle, case.now)
    quote_args = {}
    loader = getattr(request, "portfolio_risk_quote_loader", None)
    if loader is not None:
        quote_args["portfolio_risk_quote_loader"] = loader
    existing = case.coordinator.coordinate(
        proposal, other, request.profile, candles=request.candles,
        lot_size=request.lot_size, now=request.evaluated_at,
        portfolio_risk_candidate_quote=request.portfolio_risk_candidate_quote,
        **quote_args,
    )
    assert existing.status == "QUEUED", existing.to_dict()
    assert case.provider.market_calls == []
    result = _cycle(case)
    assert result.status == "QUEUED", result.to_dict()
    assert result.intent_id != existing.intent_id
    assert case.provider.market_calls == []
    assert len(case.central.state().queued) == 2
    assert case.provider.order_calls == 0


def test_same_queued_proposal_reauthorization_preserves_one_unsent_intent(tmp_path):
    # Isolate dispatch routing on READY; R4 EMPTY-with-history remains separate.
    case = _case(tmp_path, current=0, target=1, empty_status="READY")
    first = _cycle(case)
    before = len(case.provider.market_calls)
    second = _cycle(case)
    assert second.status == "REAUTHORIZED", second.to_dict()
    assert second.intent_id == first.intent_id
    assert len(case.provider.market_calls) == before + 1
    assert len(case.central.state().intents) == 1
    assert case.provider.order_calls == 0


def test_empty_queued_history_reauthorizes_without_second_intent(tmp_path):
    # Step 3 replaces the old characterization of the now-corrected refusal.
    # This is still EMPTY, not the READY isolation fixture used above.
    case = _case(tmp_path, current=0, target=1)
    first = _cycle(case)
    assert first.status == "QUEUED"
    before = case.central.state()
    repeated = _cycle(case)
    assert repeated.status == "REAUTHORIZED"
    assert repeated.intent_id == first.intent_id
    assert len(case.central.state().intents) == 1
    assert case.central.state().reserved_cash_kopecks == before.reserved_cash_kopecks
    assert case.provider.order_calls == 0


@pytest.mark.parametrize("current,target", [(0, 1), (1, 0)])
@pytest.mark.parametrize("behavior,expected", [("submitted", "SUBMITTED"), ("ambiguous", "UNCERTAIN")])
def test_synthetic_legacy_dispatch_is_not_reposted_after_outcome(tmp_path, current, target, behavior, expected):
    # Explicitly READY and LEGACY_ACTIVE: this does not qualify exact-cash Q7A.
    case = _case(tmp_path, current=current, target=target, empty_status="READY")
    case.provider.market_open = True
    case.provider.post_behavior = behavior
    first = _cycle(case)
    assert first.status == "QUEUED", first.to_dict()
    assert case.central.state().blocking_intent.status == expected
    assert case.provider.order_calls == 1
    assert case.provider.sent_ids == [first.intent_id]
    quotes = case.provider.quote_calls
    second = _cycle(case)
    assert second.status == "ACCOUNT_BLOCKED", second.to_dict()
    assert case.provider.order_calls == 1
    assert case.provider.quote_calls == quotes
    assert len(case.central.state().intents) == 1


@pytest.mark.parametrize("current,target", [(0, 1), (1, 0)])
def test_required_quote_failure_is_finite_and_never_enqueues(tmp_path, current, target):
    case = _case(tmp_path, current=current, target=target, fail_quote=True)
    before = case.central.state()
    with pytest.raises(GuiRuntimeBlockedError, match="CANDIDATE_QUOTE_READ_FAILED") as exc:
        _cycle(case)
    assert "PRIVATE" not in str(exc.value)
    assert case.central.state() == before
    assert case.provider.quote_calls == 1 and case.provider.order_calls == 0


@pytest.mark.parametrize("offset", [-301, 6])
@pytest.mark.parametrize("current,target", [(0, 1), (1, 0)])
def test_required_quote_freshness_is_not_weakened(tmp_path, offset, current, target):
    case = _case(tmp_path, current=current, target=target)
    case.provider.quote_at = NOW + timedelta(seconds=offset)
    with pytest.raises(GuiRuntimeBlockedError, match="CANDIDATE_QUOTE_NOT_FRESH"):
        _cycle(case)
    assert case.central.state().intents == ()
    assert case.provider.order_calls == 0


@pytest.mark.parametrize("current,target", [(0, 0), (1, 1), (0, 1), (1, 0)])
def test_preflight_rejection_still_precedes_quote_and_dispatch(tmp_path, current, target):
    case = _case(tmp_path, current=current, target=target, fail_quote=True)
    state = case.repository.load(expected_account_id=ACCOUNT)
    case.repository.save(replace(state, freshness=SnapshotFreshness.STALE))
    result = _cycle(case)
    assert result.status == "PREFLIGHT_BLOCKED", result.to_dict()
    assert case.provider.quote_calls == case.provider.order_calls == 0
    assert case.central.state().intents == ()


def test_zero_cash_sell_is_not_treated_as_a_buy_cash_requirement(tmp_path):
    case = _case(tmp_path, current=1, target=0, cash=0.0)
    result = _cycle(case)
    assert result.status == "QUEUED", result.to_dict()
    assert result.approved_target_lots == 0
    assert case.central.state().queued[0].reserved_cash_kopecks == 0
    assert case.provider.quote_calls == 1 and case.provider.order_calls == 0


def test_unaffordable_buy_does_not_fetch_quote_or_create_order(tmp_path):
    case = _case(tmp_path, current=0, target=1, cash=1.0, fail_quote=True)
    result = _cycle(case)
    assert result.status == "RISK_BLOCKED", result.to_dict()
    assert result.proposed_target_lots == 1 and result.approved_target_lots == 0
    assert case.provider.quote_calls == case.provider.order_calls == 0
    assert case.central.state().intents == ()


def test_position_ownership_blocker_is_not_bypassed_for_sell(tmp_path):
    case = _case(tmp_path, current=1, target=0, fail_quote=True)
    state = case.repository.load(expected_account_id=ACCOUNT)
    case.repository.save(replace(state, positions=(replace(
        state.positions[0], ownership=None, ownership_status=OwnershipStatus.UNATTRIBUTED,
        origin=PositionOrigin.EXTERNAL,
    ),)))
    result = _cycle(case)
    assert result.status == "PREFLIGHT_BLOCKED", result.to_dict()
    assert case.provider.quote_calls == case.provider.order_calls == 0
    assert case.central.state().intents == ()


def test_canonical_drift_during_deferred_quote_prevents_admission(tmp_path):
    case = _case(tmp_path, current=0, target=1)
    def change():
        state = case.repository.load(expected_account_id=ACCOUNT)
        case.repository.save(replace(state, revision=state.revision + 1))
    case.provider.on_quote = change
    result = _cycle(case)
    assert result.status == "PORTFOLIO_RISK_CANONICAL_CHANGED", result.to_dict()
    assert case.central.state().intents == () and case.provider.order_calls == 0


def test_risk_state_drift_during_deferred_quote_prevents_admission(tmp_path):
    case = _case(tmp_path, current=0, target=1)
    def change():
        case.risk.state_store.update_account(ACCOUNT, lambda state: replace(
            state, kill_switch_active=True, kill_switch_reason="synthetic halt",
        ))
    case.provider.on_quote = change
    result = _cycle(case)
    assert result.status == "PORTFOLIO_RISK_STATE_CHANGED", result.to_dict()
    assert case.central.state().intents == () and case.provider.order_calls == 0


def test_post_quote_clock_is_bound_to_admission_and_gui_audit(tmp_path):
    case = _case(tmp_path, current=0, target=1)
    def advance_clock():
        case.provider.clock_at = NOW + timedelta(seconds=30)
        case.provider.quote_at = case.provider.clock_at
    case.provider.on_quote = advance_clock
    result = _cycle(case)
    assert result.status == "QUEUED", result.to_dict()
    authorization = case.central.state().queued[0].authorization
    assert datetime.fromisoformat(authorization.authorized_at) == case.provider.clock_at
    assert datetime.fromisoformat(authorization.portfolio_risk.evaluated_at) == case.provider.clock_at
    event = case.journal.recent(event_type="CENTRAL_COORDINATION_RESULT")[0]
    assert datetime.fromisoformat(event["timestamp_utc"]) == case.provider.clock_at


def test_regressing_quote_clock_is_rejected_without_intent(tmp_path):
    from trading_robot.central_order_coordinator import CentralOrderCoordinationError
    case = _case(tmp_path, current=0, target=1)
    def rewind():
        case.provider.clock_at = NOW - timedelta(seconds=1)
    case.provider.on_quote = rewind
    with pytest.raises(CentralOrderCoordinationError, match="CANDIDATE_QUOTE_OBSERVATION_INVALID"):
        _cycle(case)
    assert case.central.state().intents == () and case.provider.order_calls == 0


@pytest.mark.parametrize("bad", [None, (), 1, "PRIVATE_NOT_A_QUOTE"])
def test_invalid_deferred_observation_is_rejected_by_real_coordinator(tmp_path, bad):
    from trading_robot.central_order_coordinator import CentralOrderCoordinationError
    case = _case(tmp_path, current=0, target=1)
    proposal = case.hooks.evaluate_closed_candle(case.runtime, case.candle, case.now)
    request = case.hooks.coordination_request(case.runtime, proposal, case.candle, case.now)
    with pytest.raises(CentralOrderCoordinationError, match="CANDIDATE_QUOTE_OBSERVATION_INVALID"):
        case.coordinator.coordinate(
            proposal, case.runtime, request.profile, candles=request.candles,
            lot_size=request.lot_size, now=request.evaluated_at,
            portfolio_risk_quote_loader=lambda: bad,
        )
    assert case.central.state().intents == () and case.provider.order_calls == 0


def test_eager_and_deferred_quote_sources_cannot_be_combined(tmp_path):
    from trading_robot.central_order_coordinator import CentralOrderCoordinationError
    case = _case(tmp_path, current=0, target=1)
    proposal = case.hooks.evaluate_closed_candle(case.runtime, case.candle, case.now)
    request = case.hooks.coordination_request(case.runtime, proposal, case.candle, case.now)
    observation = request.portfolio_risk_quote_loader()
    before = case.risk.state_store.load_account(ACCOUNT)
    with pytest.raises(CentralOrderCoordinationError, match="CANDIDATE_QUOTE_SOURCE_INVALID"):
        case.coordinator.coordinate(
            proposal, case.runtime, request.profile, candles=request.candles,
            lot_size=request.lot_size, now=request.evaluated_at,
            portfolio_risk_candidate_quote=observation.quote,
            portfolio_risk_quote_loader=request.portfolio_risk_quote_loader,
        )
    assert case.risk.state_store.load_account(ACCOUNT) == before
    assert case.central.state().intents == () and case.provider.order_calls == 0
