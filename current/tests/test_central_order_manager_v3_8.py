from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from trading_robot.central_order_manager import (
    CentralOrderCandidate,
    CentralOrderConflictError,
    CentralOrderManager,
    CentralOrderStateError,
    CentralOrderStore,
    ExecutionAuthorization,
)
from trading_robot.journal import EventJournal
from trading_robot.multi_instrument_strategy import StrategyProposal
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    OwnershipStatus,
    PortfolioState,
    PortfolioTarget,
    PositionOrigin,
    PositionOwnership,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
    SnapshotFreshness,
)
from trading_robot.portfolio_preflight import (
    PortfolioPreflightGate,
    PortfolioSnapshotLease,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.runtime_bootstrap import validate_runtime_files

ACCOUNT = "sandbox-account-1"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc).isoformat()
AFTER = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def portfolio_state(
    *,
    revision: int = 0,
    actual_lots: int = 0,
    snapshot_at: str = NOW,
) -> PortfolioState:
    positions: tuple[PositionState, ...] = ()
    if actual_lots:
        target = PortfolioTarget(
            instrument_id="uid-sber",
            target_lots=actual_lots,
            strategy_id="sma",
            config_hash="c" * 64,
            candle_time=NOW,
        )
        positions = (
            PositionState(
                instrument_id="uid-sber",
                figi="figi-sber",
                ticker="SBER",
                class_code="TQBR",
                asset_type="share",
                currency="rub",
                quantity=float(actual_lots * 10),
                actual_lots=actual_lots,
                average_price=100.0,
                current_price=100.0,
                market_value=float(actual_lots * 1_000),
                expected_yield=0.0,
                target=target,
                ownership=PositionOwnership(
                    strategy_id="sma",
                    config_hash="c" * 64,
                    candle_interval="CANDLE_INTERVAL_HOUR",
                ),
                ownership_status=OwnershipStatus.ATTRIBUTED,
                pending_orders=(),
                reconciliation=ReconciliationResult(
                    instrument_id="uid-sber",
                    status=ReconciliationStatus.MATCHED,
                    blocking=False,
                    reasons=(),
                    actual_lots=actual_lots,
                    target_lots=actual_lots,
                    checked_at=NOW,
                ),
                origin=PositionOrigin.STRATEGY,
                last_candle_time=NOW,
            ),
        )
    return PortfolioState(
        version=2,
        account=AccountState(
            account_id=ACCOUNT,
            total_value=1_000_000.0,
            securities_value=0.0,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", 1_000_000.0),),
        ),
        snapshot_at=snapshot_at,
        generated_at=snapshot_at,
        freshness=SnapshotFreshness.FRESH,
        source="PORTFOLIO_MANAGER",
        positions=positions,
        warnings=(),
        state_status="READY",
        blocking=False,
        revision=revision,
    )


def save_portfolio(root: Path, state: PortfolioState | None = None) -> PortfolioRepository:
    repository = PortfolioRepository(root / "portfolio_state.json")
    repository.save(state or portfolio_state())
    return repository


def authorization(
    state: PortfolioState,
    *,
    instrument_id: str = "uid-sber",
    target_lots: int = 1,
    available_cash_kopecks: int | None = None,
) -> ExecutionAuthorization:
    lease = PortfolioSnapshotLease.from_state(state, leased_at=NOW)
    return ExecutionAuthorization(
        account_id=ACCOUNT,
        instrument_id=instrument_id,
        authorized_target_lots=target_lots,
        portfolio_revision=lease.revision,
        portfolio_decision_checksum=lease.decision_checksum,
        portfolio_document_checksum=lease.document_checksum,
        available_cash_kopecks=(
            available_cash_kopecks
            if available_cash_kopecks is not None
            else 100_000_000
        ),
        preflight_status="PASS",
        pending_order_ids=(),
        uncertain_order_ids=(),
        risk_status="PASS",
        risk_decision_id=f"risk-{instrument_id}-{target_lots}",
        risk_policy_hash="b" * 64,
        risk_order_allowed=True,
        authorized_at=NOW,
    )


def candidate(
    *,
    ticker: str = "SBER",
    instrument_id: str = "uid-sber",
    current_lots: int = 0,
    target_lots: int = 1,
    price_kopecks: int = 10_000,
    candle_time: str = NOW,
) -> CentralOrderCandidate:
    return CentralOrderCandidate(
        account_id=ACCOUNT,
        instrument_id=instrument_id,
        ticker=ticker,
        runtime_key=f"runtime-{instrument_id}",
        runtime_config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=candle_time,
        strategy_id="sma",
        strategy_profile_hash="c" * 64,
        current_lots=current_lots,
        target_lots=target_lots,
        estimated_price_kopecks=price_kopecks,
        lot_size=10,
        created_at=NOW,
    )


def manager(root: Path, *, journal: bool = False) -> CentralOrderManager:
    return CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=ACCOUNT,
        journal=(EventJournal(root / "trading_events.db") if journal else None),
    )


class FakeRiskRuntime:
    account_id = ACCOUNT
    mode = "SANDBOX_EXECUTION"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_execution(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            enforced=True,
            mode="SANDBOX_EXECUTION",
            status="RECORDED",
            error=None,
            execution_id=kwargs["execution_id"],
            decision_id=kwargs["decision_id"],
            policy_hash=kwargs["expected_policy_hash"],
            reconciliation_proof=kwargs["reconciliation_proof"],
        )


def test_candidate_is_derived_from_unauthorized_strategy_proposal():
    proposal = StrategyProposal(
        runtime_key="runtime-uid-sber",
        instrument_id="uid-sber",
        ticker="SBER",
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=NOW,
        strategy_profile_hash="c" * 64,
        primary_strategy="sma",
        primary_target_lots=2,
        decisions={},
        comparison={},
        generated_at=NOW,
    )

    result = CentralOrderCandidate.from_strategy_proposal(
        proposal,
        account_id=ACCOUNT,
        runtime_config_hash="a" * 64,
        current_lots=0,
        estimated_price_rub="100.001",
        lot_size=10,
    )

    assert result.target_lots == 2
    assert result.estimated_price_kopecks == 10_001
    assert result.direction == "BUY"
    assert proposal.execution_authorized is False


def test_authorization_factory_requires_one_preflight_risk_snapshot():
    state = portfolio_state()
    lease = PortfolioSnapshotLease.from_state(state, leased_at=NOW)
    preflight = PortfolioPreflightGate().evaluate(
        lease,
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        instrument_id="uid-sber",
        proposed_target_lots=1,
    )
    risk = SimpleNamespace(
        mode="SANDBOX_EXECUTION",
        enforced=True,
        error=None,
        assessment=SimpleNamespace(
            decision=SimpleNamespace(
                order_allowed=True,
                requested_target_lots=1,
                current_lots=0,
                approved_target_lots=1,
            ),
            state=RiskState(),
        ),
        portfolio_revision=lease.revision,
        portfolio_decision_checksum=lease.decision_checksum,
        approved_target_lots=1,
        status="PASS",
        decision_id="risk-decision-1",
        policy_hash="b" * 64,
    )

    result = ExecutionAuthorization.from_gate_results(
        preflight,
        risk,
        lease=lease,
        authorized_at=NOW,
    )

    assert result.authorized_target_lots == 1
    assert result.portfolio_revision == lease.revision
    assert result.available_cash_kopecks == 100_000_000
    assert result.risk_state_guard_hash is not None
    mismatched_lease = PortfolioSnapshotLease.from_state(
        replace(state, revision=1),
        leased_at=NOW,
    )
    with pytest.raises(CentralOrderConflictError, match="canonical lease"):
        ExecutionAuthorization.from_gate_results(
            preflight,
            risk,
            lease=mismatched_lease,
        )
    risk.portfolio_revision = lease.revision + 1
    with pytest.raises(CentralOrderConflictError, match="canonical snapshot"):
        ExecutionAuthorization.from_gate_results(
            preflight,
            risk,
            lease=lease,
        )


def test_duplicate_enqueue_is_idempotent_and_cash_is_reserved_once(
    tmp_path: Path,
):
    state = portfolio_state()
    central = manager(tmp_path)
    order = candidate(target_lots=2)
    proof = authorization(state, target_lots=2)

    first = central.enqueue(order, proof)
    duplicate = central.enqueue(order, proof)

    assert not first.idempotent
    assert duplicate.idempotent
    assert first.intent.intent_id == duplicate.intent.intent_id
    assert central.state().reserved_cash_kopecks == 202_000


def test_second_active_intent_for_same_instrument_scope_is_blocked(
    tmp_path: Path,
):
    central = manager(tmp_path)
    state = portfolio_state()
    central.enqueue(candidate(), authorization(state))

    with pytest.raises(CentralOrderConflictError, match="account/instrument"):
        central.enqueue(
            candidate(target_lots=2, candle_time=AFTER),
            authorization(state, target_lots=2),
        )

    assert len(central.state().queued) == 1
    assert len(central.state().intents) == 1


def test_queue_reserves_cash_across_instruments_and_preserves_order(
    tmp_path: Path,
):
    state = portfolio_state()
    central = manager(tmp_path)
    sber = candidate(target_lots=2)
    lkoh = candidate(
        ticker="LKOH",
        instrument_id="uid-lkoh",
        target_lots=1,
        price_kopecks=30_000,
    )
    central.enqueue(
        sber,
        authorization(
            state,
            target_lots=2,
            available_cash_kopecks=600_000,
        ),
    )

    with pytest.raises(CentralOrderConflictError, match="Insufficient"):
        central.enqueue(
            lkoh,
            authorization(
                state,
                instrument_id="uid-lkoh",
                available_cash_kopecks=500_000,
            ),
        )
    central.enqueue(
        lkoh,
        authorization(
            state,
            instrument_id="uid-lkoh",
            available_cash_kopecks=600_000,
        ),
    )

    current = central.state()
    assert [item.candidate.ticker for item in current.queued] == ["SBER", "LKOH"]
    assert current.reserved_cash_kopecks == 505_000
    with pytest.raises(CentralOrderConflictError, match="active reservations"):
        central.reauthorize_queued(
            current.queued[0].intent_id,
            authorization(
                state,
                target_lots=2,
                available_cash_kopecks=504_999,
            ),
        )
    assert central.state().queued[0].authorization.available_cash_kopecks == 600_000


def test_multi_lot_cash_contention_blocks_second_buy_without_queue_mutation(
    tmp_path: Path,
):
    state = portfolio_state()
    central = manager(tmp_path)
    sber = candidate(target_lots=3)
    lkoh = candidate(
        ticker="LKOH",
        instrument_id="uid-lkoh",
        target_lots=2,
        price_kopecks=20_000,
    )
    central.enqueue(
        sber,
        authorization(
            state,
            target_lots=3,
            available_cash_kopecks=800_000,
        ),
    )

    with pytest.raises(CentralOrderConflictError, match="Insufficient"):
        central.enqueue(
            lkoh,
            authorization(
                state,
                instrument_id="uid-lkoh",
                target_lots=2,
                available_cash_kopecks=700_000,
            ),
        )

    after_rejection = central.state()
    assert [item.candidate.ticker for item in after_rejection.queued] == ["SBER"]
    assert after_rejection.reserved_cash_kopecks == 303_000
    central.enqueue(
        lkoh,
        authorization(
            state,
            instrument_id="uid-lkoh",
            target_lots=2,
            available_cash_kopecks=800_000,
        ),
    )
    accepted = central.state()
    assert [item.candidate.ticker for item in accepted.queued] == [
        "SBER",
        "LKOH",
    ]
    assert accepted.reserved_cash_kopecks == 707_000


def test_sell_intent_does_not_reserve_cash(tmp_path: Path):
    state = portfolio_state()
    central = manager(tmp_path)

    result = central.enqueue(
        candidate(current_lots=1, target_lots=0),
        authorization(
            state,
            target_lots=0,
            available_cash_kopecks=0,
        ),
    )

    assert result.intent.candidate.direction == "SELL"
    assert result.reserved_cash_kopecks == 0


def test_prepare_next_is_sequential_and_never_authorizes_execution(
    tmp_path: Path,
):
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    central = manager(tmp_path)
    first = central.enqueue(
        candidate(),
        authorization(state),
    )
    central.enqueue(
        candidate(ticker="LKOH", instrument_id="uid-lkoh"),
        authorization(state, instrument_id="uid-lkoh"),
    )

    prepared = central.prepare_next(repository)

    assert prepared is not None
    assert prepared.intent.intent_id == first.intent.intent_id
    assert prepared.intent.status == "IN_FLIGHT"
    assert prepared.execution_authorized is False
    assert not hasattr(central, "post_order")
    with pytest.raises(CentralOrderConflictError, match="Account-wide"):
        central.prepare_next(repository)

    central.mark_pre_submit_failed(
        prepared.intent.intent_id,
        reason="adapter unavailable before broker call",
    )
    second = central.prepare_next(repository)
    assert second is not None
    assert second.intent.candidate.ticker == "LKOH"


def test_restart_converts_in_flight_to_uncertain_without_resubmit(
    tmp_path: Path,
):
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    first_manager = manager(tmp_path)
    first = first_manager.enqueue(
        candidate(),
        authorization(state),
    )
    first_manager.enqueue(
        candidate(ticker="LKOH", instrument_id="uid-lkoh"),
        authorization(state, instrument_id="uid-lkoh"),
    )
    first_manager.prepare_next(repository)

    restarted = manager(tmp_path)
    recovered = restarted.recover_after_restart()

    assert recovered is not None
    assert recovered.intent_id == first.intent.intent_id
    assert recovered.status == "UNCERTAIN"
    assert recovered.broker_order_id is None
    with pytest.raises(CentralOrderConflictError, match="Account-wide"):
        restarted.prepare_next(repository)

    repository.save(
        portfolio_state(snapshot_at=recovered.updated_at),
        expected_revision=0,
    )
    with pytest.raises(CentralOrderConflictError, match="predates"):
        restarted.mark_reconciled(
            recovered.intent_id,
            portfolio_repository=repository,
            outcome="NOT_SUBMITTED",
            executed_lots=0,
        )
    repository.save(
        portfolio_state(snapshot_at=AFTER),
        expected_revision=0,
    )
    restarted.mark_reconciled(
        recovered.intent_id,
        portfolio_repository=repository,
        outcome="NOT_SUBMITTED",
        executed_lots=0,
    )
    next_order = restarted.prepare_next(repository)
    assert next_order is not None
    assert next_order.intent.candidate.ticker == "LKOH"


def test_submitted_order_remains_blocking_until_reconciled(tmp_path: Path):
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    central = manager(tmp_path)
    queued = central.enqueue(
        candidate(),
        authorization(state),
    )
    central.prepare_next(repository)
    submitted = central.mark_submitted(
        queued.intent.intent_id,
        broker_order_id="broker-order-1",
    )

    assert submitted.status == "SUBMITTED"
    assert manager(tmp_path).recover_after_restart() is None
    with pytest.raises(CentralOrderConflictError, match="Account-wide"):
        central.prepare_next(repository)

    with pytest.raises(CentralOrderConflictError, match="actual lots"):
        central.mark_reconciled(
            submitted.intent_id,
            portfolio_repository=repository,
            outcome="FILLED",
            executed_lots=1,
        )
    repository.save(
        portfolio_state(revision=1, actual_lots=1, snapshot_at=AFTER),
        expected_revision=0,
    )
    with pytest.raises(CentralOrderConflictError, match="Risk accounting"):
        central.mark_reconciled(
            submitted.intent_id,
            portfolio_repository=repository,
            outcome="FILLED",
            executed_lots=1,
            execution_price_rub=100.0,
        )
    risk_runtime = FakeRiskRuntime()
    terminal = central.mark_reconciled(
        submitted.intent_id,
        portfolio_repository=repository,
        outcome="FILLED",
        executed_lots=1,
        risk_runtime=risk_runtime,
        execution_price_rub=100.0,
        execution_price_source="executedOrderPrice",
    )
    assert terminal.status == "RECONCILED"
    assert terminal.reconciled_portfolio_revision == 1
    assert terminal.reconciled_portfolio_decision_checksum
    assert terminal.reconciled_portfolio_snapshot_at == AFTER
    assert terminal.risk_execution_status == "RECORDED"
    assert terminal.risk_execution_id == submitted.intent_id
    assert risk_runtime.calls[0]["signed_lots"] == 1
    assert risk_runtime.calls[0]["decision_id"] == (
        submitted.authorization.risk_decision_id
    )
    assert central.state().reserved_cash_kopecks == 0


def test_confirmed_fill_is_persisted_in_real_risk_state(tmp_path: Path):
    initial = portfolio_state()
    repository = save_portfolio(tmp_path, initial)
    central = manager(tmp_path)
    queued = central.enqueue(candidate(), authorization(initial)).intent
    central.prepare_next(repository)
    submitted = central.mark_submitted(
        queued.intent_id,
        broker_order_id="broker-order-risk-accounting",
    )
    repository.save(
        portfolio_state(revision=1, actual_lots=1, snapshot_at=AFTER),
        expected_revision=0,
    )

    profile_store = RiskProfileStore(tmp_path / "risk_profiles.json")
    profile_store.save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope=ACCOUNT,
        source="V3_8_TEST",
    )
    state_store = RiskStateStore(tmp_path / "risk_state.json")
    risk_runtime = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profile_store,
        state_store=state_store,
    )

    terminal = central.mark_reconciled(
        submitted.intent_id,
        portfolio_repository=repository,
        outcome="FILLED",
        executed_lots=1,
        risk_runtime=risk_runtime,
        execution_price_rub=100.0,
        execution_price_source="executedOrderPrice",
    )

    risk_state = state_store.load_account(ACCOUNT)
    assert terminal.risk_execution_status == "RECORDED"
    assert risk_state.daily_order_count == 1
    assert risk_state.daily_turnover_rub == 1_000.0
    assert risk_state.recorded_execution_ids == (submitted.intent_id,)
    assert risk_state.last_execution_at == AFTER


def test_portfolio_revision_change_blocks_dispatch(tmp_path: Path):
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    central = manager(tmp_path)
    central.enqueue(
        candidate(),
        authorization(state),
    )
    updated_state = replace(state, revision=1)
    repository.save(updated_state, expected_revision=0)

    with pytest.raises(CentralOrderConflictError, match="revision changed"):
        central.prepare_next(repository)
    assert central.state().queued[0].status == "QUEUED"
    queued = central.state().queued[0]
    central.reauthorize_queued(
        queued.intent_id,
        authorization(updated_state),
    )
    assert central.prepare_next(repository) is not None


def test_expected_queue_head_prevents_cross_instrument_precheck_race(
    tmp_path: Path,
):
    state = portfolio_state()
    repository = save_portfolio(tmp_path, state)
    central = manager(tmp_path)
    queued = central.enqueue(candidate(), authorization(state)).intent

    with pytest.raises(CentralOrderConflictError, match="queue head changed"):
        central.prepare_next(
            repository,
            expected_intent_id="00000000-0000-0000-0000-000000000000",
        )

    assert central.state().queued[0].intent_id == queued.intent_id


def test_store_fails_closed_on_checksum_mismatch(tmp_path: Path):
    central = manager(tmp_path)
    state = portfolio_state()
    central.enqueue(
        candidate(),
        authorization(state),
    )
    path = tmp_path / "central_order_state.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["intents"][0]["status"] = "CANCELLED"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CentralOrderStateError, match="CHECKSUM_MISMATCH"):
        central.state()


def test_runtime_validation_backup_restore_and_journal_cover_central_queue(
    tmp_path: Path,
):
    source = tmp_path / "source"
    source.mkdir()
    central = manager(source, journal=True)
    state = portfolio_state()
    queued = central.enqueue(
        candidate(),
        authorization(state),
    )
    report = validate_runtime_files(source)
    actions = {item.name: item.action for item in report.items}
    backup = RuntimeBackupManager(source, app_version="0.3.8a1").create_backup(
        tmp_path / "central-runtime.zip"
    )
    restored = tmp_path / "restored"
    RuntimeBackupManager(restored, app_version="0.3.8a1").restore_backup(
        backup,
        confirmation="RESTORE RUNTIME",
    )

    loaded = CentralOrderStore(restored / "central_order_state.json").load(
        expected_account_id=ACCOUNT
    )
    journal_rows = EventJournal(source / "trading_events.db").recent(
        category="central_order"
    )
    assert actions["central_order_state.json"] == "VALIDATED"
    assert loaded.intents[0].intent_id == queued.intent.intent_id
    assert (restored / "central_order_state.json.sha256").exists()
    assert journal_rows[0]["event_type"] == "CENTRAL_ORDER_ENQUEUED"
    assert journal_rows[0]["payload"]["execution_authorized"] is False


def test_persisted_identity_tampering_is_rejected_even_with_new_checksum(
    tmp_path: Path,
):
    central = manager(tmp_path)
    state = portfolio_state()
    central.enqueue(
        candidate(),
        authorization(state),
    )
    path = tmp_path / "central_order_state.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["intents"][0]["intent_id"] = "forged"
    path.write_text(json.dumps(document), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        digest + "\n",
        encoding="ascii",
    )

    with pytest.raises(CentralOrderStateError, match="identifier mismatch"):
        central.state()


def test_persisted_authorization_boolean_type_is_fail_closed(tmp_path: Path):
    central = manager(tmp_path)
    state = portfolio_state()
    central.enqueue(candidate(), authorization(state))
    path = tmp_path / "central_order_state.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["intents"][0]["authorization"]["risk_order_allowed"] = "false"
    path.write_text(json.dumps(document), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        digest + "\n",
        encoding="ascii",
    )

    with pytest.raises(CentralOrderStateError, match="must be boolean"):
        central.state()


def test_persisted_illegal_transition_is_fail_closed(tmp_path: Path):
    central = manager(tmp_path)
    state = portfolio_state()
    central.enqueue(candidate(), authorization(state))
    path = tmp_path / "central_order_state.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    intent = document["intents"][0]
    intent["status"] = "SUBMITTED"
    intent["broker_order_id"] = "forged-broker-order"
    intent["transitions"].append(
        {
            "status": "SUBMITTED",
            "at": intent["updated_at"],
            "detail": "forged direct transition",
        }
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        digest + "\n",
        encoding="ascii",
    )

    with pytest.raises(CentralOrderStateError, match="transition sequence"):
        central.state()
