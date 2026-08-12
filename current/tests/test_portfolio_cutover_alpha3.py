from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from trading_robot.journal import EventJournal
from trading_robot.portfolio_adapters import RuntimePortfolioAdapter
from trading_robot.portfolio_cutover import PortfolioCutoverManager
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    OwnershipStatus,
    PendingOrderState,
    PendingOrderStatus,
    PortfolioMigrationStatus,
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
    PortfolioDualReadStatus,
    PortfolioPreflightGate,
    PortfolioSnapshotLease,
)
from trading_robot.portfolio_repository import (
    PortfolioRepository,
    PortfolioRevisionConflictError,
    PortfolioRevisionRollbackError,
)
from trading_robot.portfolio_snapshot import PortfolioSnapshotBuilder
from trading_robot.portfolio_transactions import PortfolioTransactionCoordinator
from trading_robot.state_persistence import atomic_write_json

from tests.test_portfolio_manager_v3_7 import FakePortfolioAPI


NOW = "2026-08-10T10:00:00+00:00"
ACCOUNT = "account-1"
INSTRUMENT = "uid-sber"
CONFIG_HASH = "a" * 64


def state_v2(
    *,
    actual: int = 0,
    target: int | None = 0,
    status: ReconciliationStatus = ReconciliationStatus.MATCHED,
    pending: tuple[PendingOrderState, ...] = (),
    revision: int = 3,
) -> PortfolioState:
    owner = (
        PositionOwnership(
            "sma",
            CONFIG_HASH,
            "CANDLE_INTERVAL_10_MIN",
            source="CANONICAL",
            attributed_at=NOW,
        )
        if actual or (target not in {None, 0})
        else None
    )
    target_state = (
        PortfolioTarget(
            INSTRUMENT,
            int(target),
            strategy_id="sma" if owner else None,
            config_hash=CONFIG_HASH if owner else None,
            candle_time=NOW,
        )
        if target is not None
        else None
    )
    positions = ()
    if actual or target not in {None, 0} or pending:
        positions = (
            PositionState(
                instrument_id=INSTRUMENT,
                figi="figi-sber",
                ticker="SBER",
                class_code="TQBR",
                asset_type="share",
                currency="rub",
                quantity=float(actual),
                actual_lots=actual,
                average_price=250.0 if actual else None,
                current_price=260.0,
                market_value=260.0 * actual,
                expected_yield=10.0 if actual else None,
                target=target_state,
                ownership=owner,
                ownership_status=(
                    OwnershipStatus.ATTRIBUTED
                    if owner
                    else (OwnershipStatus.FLAT if actual == 0 else OwnershipStatus.UNATTRIBUTED)
                ),
                pending_orders=pending,
                reconciliation=ReconciliationResult(
                    instrument_id=INSTRUMENT,
                    status=status,
                    blocking=status.blocking,
                    reasons=(status.value,),
                    actual_lots=actual,
                    target_lots=target,
                    pending_order_ids=tuple(item.order_request_id for item in pending),
                    checked_at=NOW,
                ),
                origin=PositionOrigin.STRATEGY if owner else PositionOrigin.UNKNOWN,
                last_candle_time=NOW,
            ),
        )
    return PortfolioState(
        version=2,
        account=AccountState(
            account_id=ACCOUNT,
            total_value=100_000.0,
            securities_value=260.0 * actual,
            expected_yield=10.0 if actual else 0.0,
            cash_balances=(CashBalance("rub", 100_000.0 - 260.0 * actual),),
        ),
        snapshot_at=NOW,
        generated_at=NOW,
        freshness=SnapshotFreshness.FRESH,
        source="PORTFOLIO_MANAGER",
        positions=positions,
        warnings=(),
        state_status="BLOCKED" if status.blocking else ("EMPTY" if not positions else "READY"),
        blocking=status.blocking,
        revision=revision,
    )


def write_schema1(path: Path, state: PortfolioState) -> None:
    raw = state.to_dict()
    raw["version"] = 1
    for key in (
        "portfolio_source",
        "migration",
        "last_transaction_id",
        "last_transaction_status",
    ):
        raw.pop(key, None)
    atomic_write_json(path, raw, write_checksum=True, keep_last_good=True)


def test_schema1_load_is_pending_until_cutover(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(path, state_v2())
    loaded = PortfolioRepository(path).load(expected_account_id=ACCOUNT)
    assert loaded.version == 2
    assert loaded.portfolio_source == "MIGRATION_PENDING"
    assert loaded.migration.status is PortfolioMigrationStatus.PENDING
    assert loaded.migration.legacy_read_path_enabled is True


def test_empty_schema1_cutover_completes_and_is_idempotent(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(path, state_v2())
    manager = PortfolioCutoverManager(
        PortfolioRepository(path),
        journal=EventJournal(tmp_path / "trading_events.db"),
        report_path=tmp_path / "canonical_migration_report.json",
    )
    preview = manager.preview(expected_account_id=ACCOUNT)
    assert preview.allowed is True
    first = manager.execute(
        confirmation=manager.CONFIRMATION,
        expected_account_id=ACCOUNT,
    )
    assert first.success is True
    assert first.status == "COMPLETED"
    migrated = PortfolioRepository(path).load(expected_account_id=ACCOUNT)
    assert migrated.version == 2
    assert migrated.portfolio_source == "CANONICAL"
    assert migrated.migration.complete is True
    assert migrated.revision == 4
    assert first.backup_path and Path(first.backup_path).exists()
    second = manager.execute(
        confirmation=manager.CONFIRMATION,
        expected_account_id=ACCOUNT,
    )
    assert second.status == "ALREADY_MIGRATED"
    assert PortfolioRepository(path).load().revision == 4


def test_open_matched_position_survives_cutover(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(path, state_v2(actual=1, target=1, revision=9))
    manager = PortfolioCutoverManager(PortfolioRepository(path))
    result = manager.execute(
        confirmation=manager.CONFIRMATION,
        expected_account_id=ACCOUNT,
    )
    assert result.success
    loaded = PortfolioRepository(path).load(expected_account_id=ACCOUNT)
    position = loaded.position(INSTRUMENT)
    assert position is not None
    assert position.actual_lots == 1
    assert position.target_lots == 1
    assert position.ownership and position.ownership.strategy_id == "sma"
    assert loaded.revision == 10


def test_unsafe_pending_order_blocks_cutover_without_rewrite(tmp_path: Path):
    order = PendingOrderState(
        order_request_id="pending-1",
        instrument_id=INSTRUMENT,
        direction="BUY",
        requested_lots=1,
        status=PendingOrderStatus.NEW,
    )
    path = tmp_path / "portfolio_state.json"
    write_schema1(
        path,
        state_v2(
            actual=0,
            target=0,
            status=ReconciliationStatus.PENDING_ORDER,
            pending=(order,),
        ),
    )
    before = path.read_bytes()
    manager = PortfolioCutoverManager(PortfolioRepository(path))
    result = manager.execute(
        confirmation=manager.CONFIRMATION,
        expected_account_id=ACCOUNT,
    )
    assert result.success is False
    assert result.status == "BLOCKED"
    assert path.read_bytes() == before
    assert PortfolioRepository(path).raw_schema_version() == 1


def test_exact_cutover_confirmation_is_required(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(path, state_v2())
    manager = PortfolioCutoverManager(PortfolioRepository(path))
    result = manager.execute(confirmation="yes", expected_account_id=ACCOUNT)
    assert result.success is False
    assert result.status == "BLOCKED"
    assert PortfolioRepository(path).raw_schema_version() == 1


def test_transaction_revision_is_monotonic_and_idempotent(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(state_v2(revision=5))
    coordinator = PortfolioTransactionCoordinator(repository)
    first = coordinator.commit(
        "SET_WARNING",
        lambda state: replace(state, warnings=("display-only",)),
        transaction_id="tx-1",
        expected_revision=5,
        account_id=ACCOUNT,
    )
    # Warnings are display-only and therefore do not change authorization revision.
    assert first.new_revision == 5
    second = coordinator.commit(
        "SET_WARNING",
        lambda state: replace(state, warnings=("other",)),
        transaction_id="tx-1",
        expected_revision=5,
        account_id=ACCOUNT,
    )
    assert second.idempotent is True
    assert repository.load().revision == 5


def test_transaction_business_change_increments_revision_once(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(state_v2(revision=5))
    coordinator = PortfolioTransactionCoordinator(repository)
    result = coordinator.commit(
        "MARK_STALE",
        lambda state: state.with_freshness(SnapshotFreshness.STALE),
        transaction_id="tx-stale",
        expected_revision=5,
        account_id=ACCOUNT,
    )
    assert result.old_revision == 5
    assert result.new_revision == 6
    assert repository.load().revision == 6


def test_transaction_conflict_and_repository_rollback_are_rejected(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    original = state_v2(revision=7)
    repository.save(original)
    coordinator = PortfolioTransactionCoordinator(repository)
    with pytest.raises(PortfolioRevisionConflictError):
        coordinator.commit(
            "CONFLICT",
            lambda state: state,
            expected_revision=6,
            account_id=ACCOUNT,
        )
    with pytest.raises(PortfolioRevisionRollbackError):
        repository.save(replace(original, revision=6))


def test_alpha3_preflight_uses_canonical_state_without_legacy_projection():
    state = state_v2(actual=1, target=1)
    decision = PortfolioPreflightGate().evaluate(
        PortfolioSnapshotLease.from_state(state),
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        instrument_id=INSTRUMENT,
        proposed_target_lots=1,
    )
    assert decision.passed is True
    assert decision.context.dual_read.status is PortfolioDualReadStatus.DISABLED
    assert decision.context.legacy_read_path_enabled is False


def test_preflight_blocks_pending_migration_document(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(path, state_v2())
    state = PortfolioRepository(path).load(expected_account_id=ACCOUNT)
    decision = PortfolioPreflightGate().evaluate(
        PortfolioSnapshotLease.from_state(state),
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        instrument_id=INSTRUMENT,
        proposed_target_lots=1,
    )
    assert decision.passed is False
    assert any("migration" in reason.lower() for reason in decision.reasons)


def test_manager_refresh_never_calls_legacy_robot_state_adapter(tmp_path: Path, monkeypatch):
    api = FakePortfolioAPI(lots=0)
    manager = CanonicalPortfolioManager(
        api,
        ACCOUNT,
        robot_state_file=tmp_path / "robot_state.json",
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )

    def forbidden(*args, **kwargs):  # pragma: no cover - fails if called
        raise AssertionError("legacy portfolio read path was called")

    monkeypatch.setattr(RuntimePortfolioAdapter, "from_robot_state", forbidden)
    state = manager.refresh()
    assert state.portfolio_source == "CANONICAL"
    assert state.migration.legacy_read_path_enabled is False


def test_post_fill_target_commit_then_broker_refresh_is_matched(tmp_path: Path):
    api = FakePortfolioAPI(lots=0)
    manager = CanonicalPortfolioManager(
        api,
        ACCOUNT,
        robot_state_file=tmp_path / "robot_state.json",
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )
    manager.refresh(record_event=False)
    manager.stage_confirmed_target(
        instrument_id=INSTRUMENT,
        target_lots=1,
        strategy_id="sma",
        config_hash=CONFIG_HASH,
        candle_interval="CANDLE_INTERVAL_10_MIN",
        ticker="SBER",
        figi="figi-sber",
        class_code="TQBR",
        candle_time=NOW,
        transaction_id="postfill-buy",
    )
    api.current_lots = 1
    state = manager.refresh()
    position = state.position(INSTRUMENT)
    assert position is not None
    assert position.actual_lots == 1
    assert position.target_lots == 1
    assert position.reconciliation.status is ReconciliationStatus.MATCHED
    assert state.migration.complete
    assert (tmp_path / "portfolio_legacy_shadow.json").exists()


def test_snapshot_projection_exposes_cutover_metadata():
    payload = PortfolioSnapshotBuilder(state_v2()).to_dict()
    assert payload["schema_version"] == 2
    assert payload["portfolio_source"] == "CANONICAL"
    assert payload["migration_status"] == "COMPLETED"
    assert payload["legacy_read_path_enabled"] is False


def test_manager_refuses_silent_schema1_cutover(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(path, state_v2())
    with pytest.raises(Exception, match="explicit canonical cutover"):
        CanonicalPortfolioManager(
            FakePortfolioAPI(lots=0),
            ACCOUNT,
            robot_state_file=tmp_path / "robot_state.json",
            portfolio_state_file=path,
            journal_file=tmp_path / "trading_events.db",
        )
    assert PortfolioRepository(path).raw_schema_version() == 1


def test_cutover_blocks_account_mismatch_without_rewrite(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(path, state_v2())
    before = path.read_bytes()
    result = PortfolioCutoverManager(PortfolioRepository(path)).execute(
        confirmation=PortfolioCutoverManager.CONFIRMATION,
        expected_account_id="another-account",
    )
    assert result.success is False
    assert result.status == "BLOCKED"
    assert path.read_bytes() == before


def test_cutover_blocks_target_mismatch_without_rewrite(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    write_schema1(
        path,
        state_v2(
            actual=0,
            target=1,
            status=ReconciliationStatus.TARGET_MISMATCH,
        ),
    )
    before = path.read_bytes()
    result = PortfolioCutoverManager(PortfolioRepository(path)).execute(
        confirmation=PortfolioCutoverManager.CONFIRMATION,
        expected_account_id=ACCOUNT,
    )
    assert result.success is False
    assert result.status == "BLOCKED"
    assert path.read_bytes() == before


def test_canonical_ownership_recovery_does_not_read_legacy_state(tmp_path: Path):
    from trading_robot.portfolio import OwnershipRecoveryRequest

    api = FakePortfolioAPI(lots=1)
    manager = CanonicalPortfolioManager(
        api,
        ACCOUNT,
        robot_state_file=tmp_path / "robot_state.json",
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )
    first = manager.refresh()
    assert first.position(INSTRUMENT).reconciliation.status is ReconciliationStatus.UNATTRIBUTED_OPEN_POSITION
    request = OwnershipRecoveryRequest(
        account_id=ACCOUNT,
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_10_MIN",
        primary_strategy="sma",
        primary_config_hash=CONFIG_HASH,
        strategy_suite_hash="b" * 64,
        shadow_strategies=("donchian",),
        expected_lots=1,
        confirmation_text="ADOPT SBER 1",
    )
    result = manager.recover_ownership(request)
    assert result["status"] == "ownership_recovered"
    state = manager.refresh()
    position = state.position(INSTRUMENT)
    assert position is not None
    assert position.actual_lots == 1
    assert position.target_lots == 1
    assert position.ownership and position.ownership.strategy_id == "sma"
    assert position.reconciliation.status is ReconciliationStatus.MATCHED


def test_canonical_external_close_ack_clears_target_without_risk_history_change(tmp_path: Path):
    from trading_robot.journal import JournalEvent
    from trading_robot.portfolio import ExternalCloseAcknowledgementRequest

    api = FakePortfolioAPI(lots=1)
    manager = CanonicalPortfolioManager(
        api,
        ACCOUNT,
        robot_state_file=tmp_path / "robot_state.json",
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "trading_events.db",
    )
    manager.refresh(record_event=False)
    manager.stage_confirmed_target(
        instrument_id=INSTRUMENT,
        target_lots=1,
        strategy_id="sma",
        config_hash=CONFIG_HASH,
        candle_interval="CANDLE_INTERVAL_10_MIN",
        ticker="SBER",
        figi="figi-sber",
        class_code="TQBR",
        candle_time=NOW,
        transaction_id="strategy-buy",
    )
    order_id = "diagnostic-sell-1"
    for event_type in (
        "FILLED",
        "PORTFOLIO_RECONCILED",
        "EXECUTION_RECORDED",
        "RISK_ACCOUNTED",
    ):
        manager.journal.record(
            JournalEvent(
                category="order",
                event_type=event_type,
                account_id=ACCOUNT,
                instrument_id=INSTRUMENT,
                ticker="SBER",
                order_id=order_id,
                payload={"execution_source": "DIAGNOSTIC"},
            )
        )
    api.current_lots = 0
    mismatched = manager.refresh()
    assert mismatched.position(INSTRUMENT).reconciliation.status is ReconciliationStatus.TARGET_MISMATCH
    request = ExternalCloseAcknowledgementRequest(
        account_id=ACCOUNT,
        ticker="SBER",
        class_code="TQBR",
        expected_target_lots=1,
        confirmation_text="ACK EXTERNAL CLOSE SBER 0",
    )
    result = manager.acknowledge_external_close(request)
    assert result["status"] == "external_close_acknowledged"
    assert result["risk_state_changed"] is False
    assert result["execution_history_changed"] is False
    state = PortfolioRepository(tmp_path / "portfolio_state.json").load(
        expected_account_id=ACCOUNT
    )
    position = state.position(INSTRUMENT)
    assert position is not None
    assert position.actual_lots == 0
    assert position.target_lots == 0
    assert position.ownership is None
    assert position.reconciliation.status is ReconciliationStatus.MATCHED


def test_runtime_bootstrap_reports_schema1_migration_required(tmp_path: Path):
    from trading_robot.runtime_bootstrap import bootstrap_runtime

    write_schema1(tmp_path / "portfolio_state.json", state_v2())
    report = bootstrap_runtime(tmp_path)
    item = next(item for item in report.items if item.name == "portfolio_state.json")
    assert report.ok is True
    assert item.action == "MIGRATION_REQUIRED"
    assert any("schema 1" in warning for warning in report.warnings)
    assert PortfolioRepository(tmp_path / "portfolio_state.json").raw_schema_version() == 1


def test_bot_config_cannot_reenable_legacy_portfolio_reads():
    from trading_robot.bot import BotConfig

    config = BotConfig(portfolio_preflight_require_dual_read=True)
    assert config.portfolio_preflight_require_dual_read is False
