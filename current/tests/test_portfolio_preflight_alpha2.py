from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from trading_robot.bot import SandboxTradingBot
from trading_robot.journal import EventJournal
from trading_robot.portfolio_adapters import RuntimePortfolioAdapter
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    OwnershipStatus,
    PendingOrderState,
    PendingOrderStatus,
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
    LegacyPortfolioView,
    PortfolioPreflightGate,
    PortfolioRevisionCheck,
    PortfolioSnapshotLease,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.post_fill_portfolio import PostFillPortfolioCoordinator

from tests.test_bot import FakeSandboxAPI, make_config


NOW = "2026-08-01T12:00:00+00:00"
ACCOUNT = "account-1"
INSTRUMENT = "instrument-uid"
CONFIG_HASH = "a" * 64


def _state(
    *,
    actual: int = 0,
    target: int | None = 0,
    freshness: SnapshotFreshness = SnapshotFreshness.FRESH,
    status: ReconciliationStatus = ReconciliationStatus.MATCHED,
    origin: PositionOrigin = PositionOrigin.STRATEGY,
    owner: bool | None = None,
    pending: tuple[PendingOrderState, ...] = (),
    revision: int = 1,
    account_id: str = ACCOUNT,
) -> PortfolioState:
    if owner is None:
        owner = actual != 0
    ownership = (
        PositionOwnership(
            strategy_id="sma",
            config_hash=CONFIG_HASH,
            candle_interval="CANDLE_INTERVAL_10_MIN",
        )
        if owner
        else None
    )
    target_state = (
        PortfolioTarget(
            instrument_id=INSTRUMENT,
            target_lots=target,
            strategy_id="sma" if owner else None,
            config_hash=CONFIG_HASH if owner else None,
            candle_time=NOW,
        )
        if target is not None
        else None
    )
    position = PositionState(
        instrument_id=INSTRUMENT,
        figi="figi-sber",
        ticker="SBER",
        class_code="TQBR",
        asset_type="share",
        currency="rub",
        quantity=float(actual),
        actual_lots=actual,
        average_price=100.0 if actual else None,
        current_price=101.0,
        market_value=101.0 * actual,
        expected_yield=None,
        target=target_state,
        ownership=ownership,
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
        origin=origin,
        last_candle_time=NOW,
    )
    blocking = freshness is not SnapshotFreshness.FRESH or status.blocking
    return PortfolioState(
        version=1,
        account=AccountState(
            account_id=account_id,
            total_value=100_000.0,
            securities_value=101.0 * actual,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", 100_000.0 - 101.0 * actual),),
        ),
        snapshot_at=NOW,
        generated_at=NOW,
        freshness=freshness,
        source="PORTFOLIO_MANAGER",
        positions=(position,),
        warnings=(),
        state_status="BLOCKED" if blocking else "READY",
        blocking=blocking,
        revision=revision,
    )


def _legacy(
    *,
    actual: int = 0,
    target: int | None = 0,
    owner: bool = False,
    pending: tuple[str, ...] = (),
    uncertain: tuple[str, ...] = (),
    mode: str = "SANDBOX_EXECUTION",
    account_id: str = ACCOUNT,
    instrument_id: str = INSTRUMENT,
) -> LegacyPortfolioView:
    return LegacyPortfolioView(
        account_id=account_id,
        mode=mode,
        instrument_id=instrument_id,
        actual_lots=actual,
        target_lots=target,
        owner_strategy_id="sma" if owner else None,
        owner_config_hash=CONFIG_HASH if owner else None,
        pending_order_ids=pending,
        uncertain_order_ids=uncertain,
    )


def _decision(state: PortfolioState, legacy: LegacyPortfolioView, *, proposed: int = 1):
    return PortfolioPreflightGate().evaluate(
        PortfolioSnapshotLease.from_state(state),
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        instrument_id=INSTRUMENT,
        proposed_target_lots=proposed,
        legacy=legacy,
        require_dual_read=True,
    )


def test_fresh_matched_flat_snapshot_passes():
    decision = _decision(_state(), _legacy())
    assert decision.passed is True
    assert decision.reasons == ()


def test_fresh_matched_open_strategy_position_passes():
    decision = _decision(
        _state(actual=1, target=1, owner=True),
        _legacy(actual=1, target=1, owner=True),
        proposed=1,
    )
    assert decision.passed is True
    assert decision.context.snapshot_revision == 1
    assert decision.context.actual_lots == 1


def test_stale_snapshot_blocks():
    decision = _decision(
        _state(freshness=SnapshotFreshness.STALE),
        _legacy(),
    )
    assert decision.passed is False
    assert any("STALE" in reason for reason in decision.reasons)


def test_target_mismatch_blocks():
    decision = _decision(
        _state(actual=0, target=1, status=ReconciliationStatus.TARGET_MISMATCH),
        _legacy(actual=0, target=1),
    )
    assert decision.passed is False
    assert any("TARGET_MISMATCH" in reason for reason in decision.reasons)


def test_unattributed_open_position_blocks():
    decision = _decision(
        _state(
            actual=1,
            target=None,
            owner=False,
            origin=PositionOrigin.DIAGNOSTIC,
            status=ReconciliationStatus.UNATTRIBUTED_OPEN_POSITION,
        ),
        _legacy(actual=1, target=None),
    )
    assert decision.passed is False
    assert any("ownership" in reason.lower() or "origin" in reason.lower() for reason in decision.reasons)


def test_pending_order_blocks():
    order = PendingOrderState(
        order_request_id="pending-1",
        instrument_id=INSTRUMENT,
        direction="BUY",
        requested_lots=1,
        status=PendingOrderStatus.NEW,
    )
    decision = _decision(
        _state(status=ReconciliationStatus.PENDING_ORDER, pending=(order,)),
        _legacy(pending=("pending-1",)),
    )
    assert decision.passed is False
    assert decision.context.pending_order_ids == ("pending-1",)


def test_uncertain_order_blocks():
    order = PendingOrderState(
        order_request_id="uncertain-1",
        instrument_id=INSTRUMENT,
        direction="BUY",
        requested_lots=1,
        status=PendingOrderStatus.UNKNOWN,
        uncertain=True,
    )
    decision = _decision(
        _state(status=ReconciliationStatus.PENDING_ORDER_UNCERTAIN, pending=(order,)),
        _legacy(uncertain=("uncertain-1",)),
    )
    assert decision.passed is False
    assert decision.context.uncertain_order_ids == ("uncertain-1",)


def test_account_mode_and_instrument_scope_mismatch_blocks():
    state = _state(account_id="other-account")
    legacy = _legacy(account_id="other-account", mode="DRY_RUN", instrument_id="other")
    decision = _decision(state, legacy)
    assert decision.passed is False
    assert len(decision.reasons) >= 3


def test_dual_read_actual_mismatch_blocks():
    decision = _decision(
        _state(actual=1, target=1, owner=True),
        _legacy(actual=0, target=0),
    )
    assert decision.passed is False
    assert decision.context.dual_read.matched is False
    assert any("actual lots" in reason.lower() for reason in decision.reasons)


def test_snapshot_lease_is_deterministic_for_same_state():
    state = _state()
    first = PortfolioSnapshotLease.from_state(state, leased_at=NOW)
    second = PortfolioSnapshotLease.from_state(state, leased_at=NOW)
    assert first.revision == second.revision
    assert first.decision_checksum == second.decision_checksum
    assert first.document_checksum == second.document_checksum


def test_revision_recheck_passes_when_repository_is_unchanged(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    state = _state(revision=4)
    repository.save(state)
    check = PortfolioPreflightGate.recheck(
        repository,
        PortfolioSnapshotLease.from_state(state),
        expected_account_id=ACCOUNT,
    )
    assert check.unchanged is True
    assert check.current_revision == 4


def test_revision_recheck_blocks_changed_decision(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    original = _state(revision=4)
    repository.save(original)
    changed = _state(actual=1, target=1, owner=True, revision=5)
    repository.save(changed)
    check = PortfolioPreflightGate.recheck(
        repository,
        PortfolioSnapshotLease.from_state(original),
        expected_account_id=ACCOUNT,
    )
    assert check.unchanged is False
    assert check.current_revision == 5


def test_display_only_price_change_does_not_change_decision_checksum():
    original = _state(actual=1, target=1, owner=True)
    position = original.positions[0]
    changed_position = replace(position, current_price=150.0, market_value=150.0)
    changed = replace(
        original,
        positions=(changed_position,),
        snapshot_at="2026-08-01T12:01:00+00:00",
        generated_at="2026-08-01T12:01:00+00:00",
    )
    assert original.decision_sha256 == changed.decision_sha256


def test_active_partial_fill_blocks_but_terminal_fill_and_kill_does_not():
    root = {
        "version": 6,
        "bots": {
            f"{ACCOUNT}|SBER_TQBR|CANDLE_INTERVAL_10_MIN|primary:sma:{CONFIG_HASH[:16]}": {
                "instrument_id": INSTRUMENT,
                "last_confirmed_target_lots": 1,
                "pending_order": {
                    "order_id": "partial-1",
                    "action": "BUY",
                    "lots": 3,
                    "executed_lots": 1,
                    "lifecycle_state": "PARTIALLY_FILLED",
                    "time_in_force": "FILL_AND_KILL",
                },
            }
        },
        "execution_scopes": {},
    }
    record = RuntimePortfolioAdapter.from_mapping(root, account_id=ACCOUNT)
    pending = record.positions[0].pending_orders[0]
    assert pending.status is PendingOrderStatus.FILLED
    assert pending.active is False


class _StaticManager:
    def __init__(self, state: PortfolioState) -> None:
        self.state = state

    def refresh(self, *, record_event: bool = True) -> PortfolioState:
        return self.state


def test_post_fill_coordinator_accepts_fresh_matched_state():
    result = PostFillPortfolioCoordinator(
        _StaticManager(_state(actual=1, target=1, owner=True, revision=2))
    ).reconcile(instrument_id=INSTRUMENT, expected_lots=1)
    assert result.success is True
    assert result.revision == 2


def test_post_fill_coordinator_fails_closed_on_stale_state():
    result = PostFillPortfolioCoordinator(
        _StaticManager(
            _state(
                actual=1,
                target=1,
                owner=True,
                freshness=SnapshotFreshness.STALE,
                revision=2,
            )
        )
    ).reconcile(instrument_id=INSTRUMENT, expected_lots=1)
    assert result.success is False
    assert "freshness=STALE" in result.reason


def test_bot_records_preflight_and_post_fill_canonical_events(tmp_path: Path):
    api = FakeSandboxAPI()
    config = make_config(tmp_path / "robot_state.json", dry_run=False)
    bot = SandboxTradingBot(api, ACCOUNT, config, allow_execution=True)
    result = bot.run_once()
    events = EventJournal(tmp_path / "trading_events.db").recent(
        limit=500,
        account_id=ACCOUNT,
    )
    event_types = [item["event_type"] for item in events]
    assert result["status"] == "processed"
    assert result["post_fill_canonical"]["success"] is True
    assert "PORTFOLIO_PREFLIGHT_PASSED" in event_types
    assert "POST_FILL_CANONICAL_RECONCILED" in event_types


def test_bot_revision_race_produces_zero_broker_post(tmp_path: Path, monkeypatch):
    api = FakeSandboxAPI()
    config = make_config(tmp_path / "robot_state.json", dry_run=False)
    bot = SandboxTradingBot(api, ACCOUNT, config, allow_execution=True)

    def changed(*args, **kwargs):
        return PortfolioRevisionCheck(
            unchanged=False,
            expected_revision=1,
            current_revision=2,
            expected_decision_checksum="old",
            current_decision_checksum="new",
            reason="Synthetic revision race.",
        )

    monkeypatch.setattr(bot, "_recheck_portfolio_revision", changed)
    result = bot.run_once()
    assert result["status"] == "portfolio_revision_changed"
    assert result["order_was_sent"] is False
    assert api.post_count == 0


def test_bot_status_snapshot_exposes_last_preflight(tmp_path: Path):
    api = FakeSandboxAPI()
    config = make_config(tmp_path / "robot_state.json", dry_run=False)
    bot = SandboxTradingBot(api, ACCOUNT, config, allow_execution=True)
    bot.run_once()
    snapshot = bot.status_snapshot()
    assert snapshot["portfolio_preflight_enabled"] is True
    assert snapshot["last_portfolio_preflight"]["status"] == "PASS"
    assert snapshot["last_post_fill_canonical"]["success"] is True
