from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from test_central_order_coordinator_v3_8 import candles as strategy_candles
from test_central_order_coordinator_v3_8 import profile as strategy_profile
from test_central_order_coordinator_v3_8 import proposal as strategy_proposal
from test_central_order_coordinator_v3_8 import runtime as instrument_runtime
from test_central_order_manager_v3_8 import (
    ACCOUNT,
    NOW,
    authorization,
    candidate,
    manager,
    portfolio_state,
    save_portfolio,
)
from test_sandbox_execution_adapter_v3_8 import FakeSandboxTransport

from trading_robot.central_order_coordinator import CentralOrderCoordinator
from trading_robot.central_order_manager import CentralOrderConflictError
from trading_robot.locking import InterProcessFileLock, LockUnavailableError
from trading_robot.portfolio_risk_adapter import PortfolioRiskInstrumentMetadata
from trading_robot.portfolio_risk_runtime import (
    PortfolioRiskAuthorizationError,
    PortfolioRiskRuntime,
)
from trading_robot.portfolio_risk_shadow import PortfolioRiskCandidateQuote
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter, risk_state_guard_hash
from trading_robot.sandbox_execution_adapter import (
    SANDBOX_EXECUTION_CONFIRMATION,
    SandboxExecutionAdapter,
    SandboxExecutionPolicy,
)

NOW_DT = datetime.fromisoformat(NOW)


def enforced_policy(
    *,
    cash_reserve_rub: float = 0.0,
    max_strategy_concentration_fraction: float | None = None,
) -> RiskPolicy:
    return RiskPolicy(
        cash_reserve_rub=cash_reserve_rub,
        daily_loss_limit_rub=None,
        daily_loss_limit_fraction=None,
        weekly_loss_limit_rub=None,
        weekly_loss_limit_fraction=None,
        max_drawdown_fraction=None,
        max_daily_turnover_rub=None,
        max_daily_turnover_fraction=None,
        max_orders_per_day=None,
        risk_per_trade_rub=None,
        risk_per_trade_fraction=None,
        max_position_share_of_equity=1.0,
        max_position_value_rub=1_000_000.0,
        max_order_value_rub=1_000_000.0,
        commission_buffer_fraction=0.0,
        portfolio_policy_configured=True,
        portfolio_policy_mode="ENFORCED",
        max_strategy_concentration_fraction=(
            max_strategy_concentration_fraction
        ),
        max_snapshot_age_seconds=None,
        max_price_age_seconds=None,
    )


def services(
    root,
    *,
    cash_reserve_rub: float = 0.0,
    max_strategy_concentration_fraction: float | None = None,
):
    profile_store = RiskProfileStore(root / "risk_profiles.json")
    saved = profile_store.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        enforced_policy(
            cash_reserve_rub=cash_reserve_rub,
            max_strategy_concentration_fraction=(
                max_strategy_concentration_fraction
            ),
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
    )
    state_store = RiskStateStore(root / "risk_state.json")
    runtime = PortfolioRiskRuntime(
        account_id=ACCOUNT,
        profile_store=profile_store,
        state_store=state_store,
        instrument_metadata={
            "uid-sber": PortfolioRiskInstrumentMetadata(
                "uid-sber",
                10,
                "SHARE",
                "RUB",
            ),
            "uid-lkoh": PortfolioRiskInstrumentMetadata(
                "uid-lkoh",
                10,
                "SHARE",
                "RUB",
            ),
        },
    )
    return runtime, profile_store, state_store, saved["policy_hash"]


def base_authorization(state, *, instrument_id: str, policy_hash: str):
    return replace(
        authorization(state, instrument_id=instrument_id),
        risk_policy_hash=policy_hash,
        risk_state_guard_hash=risk_state_guard_hash(RiskState()),
    )


def admit(
    runtime: PortfolioRiskRuntime,
    selected_manager,
    repository,
    state,
    *,
    instrument_id: str = "uid-sber",
    ticker: str = "SBER",
    price_kopecks: int = 10_000,
    price_at: datetime = NOW_DT,
    policy_hash: str,
):
    selected_candidate = candidate(
        instrument_id=instrument_id,
        ticker=ticker,
        price_kopecks=price_kopecks,
    )
    return runtime.admit(
        selected_manager,
        repository,
        selected_candidate,
        base_authorization(
            state,
            instrument_id=instrument_id,
            policy_hash=policy_hash,
        ),
        price_at=price_at,
        price_source="TBANK_LAST_PRICE_EXCHANGE",
        evaluated_at=price_at,
    )


def test_authoritative_admission_persists_finalized_portfolio_proof(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, _profiles, state_store, policy_hash = services(tmp_path)

    result = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    persisted = selected_manager.state().queued[0]
    proof = persisted.authorization.portfolio_risk

    assert result.enqueue.state_revision == 1
    assert result.decision.status == "PASS"
    assert proof is not None
    assert proof.finalized is True
    assert proof.decision_id == result.decision.decision_id
    assert proof.admission_central_revision == 1
    assert persisted.candidate.target_lots == proof.approved_target_lots == 1
    risk_state = state_store.load_account(ACCOUNT)
    assert risk_state.last_portfolio_risk_decision_id == proof.decision_id
    assert risk_state.last_portfolio_risk_input_hash == proof.input_hash


def test_authoritative_admission_accepts_financially_valid_clean_empty(
    tmp_path,
) -> None:
    state = replace(portfolio_state(), state_status="EMPTY")
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, _profiles, _state_store, policy_hash = services(tmp_path)

    result = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )

    assert result.decision.status == "PASS"
    assert result.enqueue.intent is not None
    assert len(selected_manager.state().intents) == 1


def test_authoritative_admission_clean_empty_cash_near_miss_creates_no_intent(
    tmp_path,
) -> None:
    state = portfolio_state()
    state = replace(
        state,
        state_status="EMPTY",
        account=replace(state.account, cash_balances=()),
    )
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, _profiles, _state_store, policy_hash = services(tmp_path)

    with pytest.raises(PortfolioRiskAuthorizationError):
        admit(
            runtime,
            selected_manager,
            repository,
            state,
            policy_hash=policy_hash,
        )
    assert selected_manager.state().intents == ()


def test_authoritative_admission_rejects_unknown_currency_without_mutation(
    tmp_path,
) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    _runtime, profiles, state_store, policy_hash = services(tmp_path)
    runtime = PortfolioRiskRuntime(
        account_id=ACCOUNT,
        profile_store=profiles,
        state_store=state_store,
        instrument_metadata={
            "uid-sber": PortfolioRiskInstrumentMetadata(
                "uid-sber",
                10,
                "SHARE",
                None,
            ),
        },
    )

    with pytest.raises(PortfolioRiskAuthorizationError) as caught:
        admit(
            runtime,
            selected_manager,
            repository,
            state,
            policy_hash=policy_hash,
        )

    assert caught.value.status == "PORTFOLIO_RISK_CURRENCY_UNKNOWN"
    assert caught.value.retryable is False
    assert selected_manager.state().intents == ()
    assert state_store.load_account(ACCOUNT) == RiskState()


def test_coordinator_explicit_m4_path_queues_portfolio_authorized_target(
    tmp_path,
) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    portfolio_runtime, profiles, state_store, _policy_hash = services(tmp_path)
    risk_runtime = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profiles,
        state_store=state_store,
    )
    selected_profile = strategy_profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = instrument_runtime(selected_profile)

    result = CentralOrderCoordinator(
        selected_manager,
        repository,
        risk_runtime,
        portfolio_risk_runtime=portfolio_runtime,
    ).coordinate(
        strategy_proposal(selected_profile, selected_runtime),
        selected_runtime,
        selected_profile,
        candles=strategy_candles(),
        lot_size=10,
        now=NOW_DT,
        portfolio_risk_candidate_quote=PortfolioRiskCandidateQuote(
            unit_price_rub=100.0,
            price_at=NOW_DT,
            source="TBANK_LAST_PRICE_EXCHANGE",
        ),
    )

    assert result.status == "QUEUED"
    assert result.portfolio_risk_status == "PASS"
    assert result.portfolio_risk_decision_id
    proof = selected_manager.state().queued[0].authorization.portfolio_risk
    assert proof is not None and proof.finalized


def test_two_individually_valid_buys_cannot_double_reserve_cash(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, _profiles, _state_store, policy_hash = services(
        tmp_path,
        cash_reserve_rub=998_500.0,
    )

    def attempt(identity: tuple[str, str]):
        instrument_id, ticker = identity
        try:
            result = admit(
                runtime,
                selected_manager,
                repository,
                state,
                instrument_id=instrument_id,
                ticker=ticker,
                policy_hash=policy_hash,
            )
            return result.enqueue.intent.intent_id
        except (PortfolioRiskAuthorizationError, CentralOrderConflictError):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                attempt,
                (("uid-sber", "SBER"), ("uid-lkoh", "LKOH")),
            )
        )

    assert sum(item is not None for item in outcomes) == 1
    central = selected_manager.state()
    assert len(central.queued) == 1
    assert central.reserved_cash_kopecks == 101_000


def test_queued_exposure_blocks_joint_strategy_concentration(tmp_path) -> None:
    isolated_root = tmp_path / "isolated"
    isolated_state = portfolio_state()
    isolated_repository = save_portfolio(isolated_root, isolated_state)
    isolated_manager = manager(isolated_root)
    isolated_runtime, _profiles, _state_store, isolated_policy_hash = services(
        isolated_root,
        max_strategy_concentration_fraction=0.0015,
    )
    individually_valid = admit(
        isolated_runtime,
        isolated_manager,
        isolated_repository,
        isolated_state,
        instrument_id="uid-lkoh",
        ticker="LKOH",
        policy_hash=isolated_policy_hash,
    )
    assert individually_valid.decision.status == "PASS"

    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, _profiles, _state_store, policy_hash = services(
        tmp_path,
        max_strategy_concentration_fraction=0.0015,
    )
    first = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    assert first.decision.status == "PASS"
    with pytest.raises(
        PortfolioRiskAuthorizationError,
        match="MAX_STRATEGY_CONCENTRATION",
    ):
        admit(
            runtime,
            selected_manager,
            repository,
            state,
            instrument_id="uid-lkoh",
            ticker="LKOH",
            policy_hash=policy_hash,
        )

    central = selected_manager.state()
    assert len(central.queued) == 1
    assert central.queued[0].candidate.instrument_id == "uid-sber"


def test_profile_lock_timeout_leaves_central_state_unchanged(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, _profiles, _state_store, policy_hash = services(tmp_path)
    runtime.lock_timeout_seconds = 0.05

    with InterProcessFileLock(
        runtime.profile_store.lock_path,
        timeout_seconds=1.0,
    ), pytest.raises(LockUnavailableError):
        admit(
            runtime,
            selected_manager,
            repository,
            state,
            policy_hash=policy_hash,
        )

    assert selected_manager.state().revision == 0
    assert selected_manager.state().intents == ()


def test_dispatch_reproduces_portfolio_proof_before_provider_post(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, profiles, state_store, policy_hash = services(tmp_path)
    admitted = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    risk_runtime = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profiles,
        state_store=state_store,
    )
    transport = FakeSandboxTransport()
    adapter = SandboxExecutionAdapter(
        transport,
        selected_manager,
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=True,
            confirmation=SANDBOX_EXECUTION_CONFIRMATION,
        ),
        risk_runtime=risk_runtime,
        portfolio_risk_runtime=runtime,
    )

    result = adapter.dispatch_next(
        repository,
        expected_intent_id=admitted.enqueue.intent.intent_id,
    )

    assert result.status == "SUBMITTED"
    assert result.order_was_sent is True
    assert transport.post_calls == 1


def test_clean_restart_preserves_queued_proof_without_duplicate_submit(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    original_manager = manager(tmp_path)
    runtime, profiles, state_store, policy_hash = services(tmp_path)
    admitted = admit(
        runtime,
        original_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    restarted_manager = manager(tmp_path)
    assert restarted_manager.recover_after_restart() is None
    transport = FakeSandboxTransport()
    adapter = SandboxExecutionAdapter(
        transport,
        restarted_manager,
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=True,
            confirmation=SANDBOX_EXECUTION_CONFIRMATION,
        ),
        risk_runtime=RiskRuntimeAdapter(
            account_id=ACCOUNT,
            mode="SANDBOX_EXECUTION",
            profile_store=profiles,
            state_store=state_store,
        ),
        portfolio_risk_runtime=runtime,
    )

    result = adapter.dispatch_next(
        repository,
        expected_intent_id=admitted.enqueue.intent.intent_id,
    )

    assert result.status == "SUBMITTED"
    assert transport.post_calls == 1


def test_reauthorized_queue_head_reproduces_excluded_self_projection(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, profiles, state_store, policy_hash = services(tmp_path)
    first = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    refreshed = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    assert refreshed.enqueue.reauthorized is True
    assert refreshed.enqueue.intent.intent_id == first.enqueue.intent.intent_id
    transport = FakeSandboxTransport()
    adapter = SandboxExecutionAdapter(
        transport,
        selected_manager,
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=True,
            confirmation=SANDBOX_EXECUTION_CONFIRMATION,
        ),
        risk_runtime=RiskRuntimeAdapter(
            account_id=ACCOUNT,
            mode="SANDBOX_EXECUTION",
            profile_store=profiles,
            state_store=state_store,
        ),
        portfolio_risk_runtime=runtime,
    )

    result = adapter.dispatch_next(
        repository,
        expected_intent_id=refreshed.enqueue.intent.intent_id,
    )

    assert result.status == "SUBMITTED"
    assert transport.post_calls == 1


def test_reauthorization_atomically_refreshes_price_reservation_and_proof(
    tmp_path,
) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, _profiles, _state_store, policy_hash = services(tmp_path)
    first = admit(
        runtime,
        selected_manager,
        repository,
        state,
        price_kopecks=10_000,
        policy_hash=policy_hash,
    )
    refreshed_at = NOW_DT + timedelta(minutes=1)

    refreshed = admit(
        runtime,
        selected_manager,
        repository,
        state,
        price_kopecks=12_000,
        price_at=refreshed_at,
        policy_hash=policy_hash,
    )

    queued = selected_manager.state().queued[0]
    proof = queued.authorization.portfolio_risk
    assert refreshed.enqueue.reauthorized is True
    assert queued.intent_id == first.enqueue.intent.intent_id
    assert queued.candidate.estimated_price_kopecks == 12_000
    assert queued.reserved_cash_kopecks == 121_200
    assert proof is not None and proof.finalized
    assert proof.candidate_price_at == refreshed_at.isoformat()
    decision = runtime.validate_dispatch(
        portfolio=state,
        central_orders=selected_manager.state(),
        intent=queued,
        evaluated_at=refreshed_at,
    )
    assert decision.status == "PASS"


def test_confirmed_fill_recalculates_current_portfolio_metrics(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, profiles, state_store, policy_hash = services(tmp_path)
    admitted = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    risk_runtime = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profiles,
        state_store=state_store,
    )
    adapter = SandboxExecutionAdapter(
        FakeSandboxTransport(),
        selected_manager,
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=True,
            confirmation=SANDBOX_EXECUTION_CONFIRMATION,
        ),
        risk_runtime=risk_runtime,
        portfolio_risk_runtime=runtime,
    )
    dispatched = adapter.dispatch_next(
        repository,
        expected_intent_id=admitted.enqueue.intent.intent_id,
    )
    assert dispatched.status == "SUBMITTED"
    reconciled_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    repository.save(
        portfolio_state(
            revision=1,
            actual_lots=1,
            snapshot_at=reconciled_at.isoformat(),
        ),
        expected_revision=0,
        allow_equal_revision=False,
    )

    reconciled = selected_manager.mark_reconciled(
        admitted.enqueue.intent.intent_id,
        portfolio_repository=repository,
        outcome="FILLED",
        executed_lots=1,
        risk_runtime=risk_runtime,
        execution_price_rub=100.0,
        execution_price_source="TBANK_EXECUTED_ORDER_PRICE",
    )
    report = runtime.recalculate_current(
        selected_manager,
        repository,
        evaluated_at=reconciled_at,
    )

    assert reconciled.status == "RECONCILED"
    assert reconciled.risk_execution_status == "RECORDED"
    assert report.metrics.gross_exposure_rub == 1_000.0
    assert report.metrics.cash_reserved_rub == 0.0


def test_queue_change_after_admission_blocks_provider_post(tmp_path) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, profiles, state_store, policy_hash = services(tmp_path)
    admitted = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    selected_manager.enqueue(
        candidate(instrument_id="uid-lkoh", ticker="LKOH"),
        base_authorization(
            state,
            instrument_id="uid-lkoh",
            policy_hash=policy_hash,
        ),
    )
    transport = FakeSandboxTransport()
    adapter = SandboxExecutionAdapter(
        transport,
        selected_manager,
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=True,
            confirmation=SANDBOX_EXECUTION_CONFIRMATION,
        ),
        risk_runtime=RiskRuntimeAdapter(
            account_id=ACCOUNT,
            mode="SANDBOX_EXECUTION",
            profile_store=profiles,
            state_store=state_store,
        ),
        portfolio_risk_runtime=runtime,
    )

    result = adapter.dispatch_next(
        repository,
        expected_intent_id=admitted.enqueue.intent.intent_id,
    )

    assert result.status == "CANONICAL_PREFLIGHT_BLOCKED"
    assert result.order_was_sent is False
    assert transport.post_calls == 0


@pytest.mark.parametrize("mutation", ["policy", "risk_state", "canonical"])
def test_each_external_proof_change_blocks_provider_post(tmp_path, mutation) -> None:
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    selected_manager = manager(tmp_path)
    runtime, profiles, state_store, policy_hash = services(tmp_path)
    admitted = admit(
        runtime,
        selected_manager,
        repository,
        state,
        policy_hash=policy_hash,
    )
    if mutation == "policy":
        profiles.confirm_portfolio_policy(
            "SANDBOX_EXECUTION",
            replace(enforced_policy(), max_gross_exposure_rub=999_999.0),
            confirmation="CONFIRM PORTFOLIO RISK POLICY",
            account_scope=ACCOUNT,
        )
    elif mutation == "risk_state":
        state_store.save_account(
            ACCOUNT,
            replace(
                state_store.load_account(ACCOUNT),
                risk_resync_required=True,
                risk_resync_reason="external activity",
                risk_resync_set_at=NOW,
            ),
        )
    else:
        repository.save(
            replace(state, revision=1),
            expected_revision=0,
            allow_equal_revision=False,
        )
    transport = FakeSandboxTransport()
    adapter = SandboxExecutionAdapter(
        transport,
        selected_manager,
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=True,
            confirmation=SANDBOX_EXECUTION_CONFIRMATION,
        ),
        risk_runtime=RiskRuntimeAdapter(
            account_id=ACCOUNT,
            mode="SANDBOX_EXECUTION",
            profile_store=profiles,
            state_store=state_store,
        ),
        portfolio_risk_runtime=runtime,
    )

    result = adapter.dispatch_next(
        repository,
        expected_intent_id=admitted.enqueue.intent.intent_id,
    )

    assert result.status != "SUBMITTED"
    assert result.order_was_sent is False
    assert transport.post_calls == 0
