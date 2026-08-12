from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_robot.bot import SandboxTradingBot
from trading_robot.export_naming import (
    build_export_filename,
    collision_safe_path,
    sanitize_component,
)
from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.portfolio import (
    ExternalCloseAcknowledgementRequest,
    PortfolioManager,
)
from trading_robot.portfolio_adapters import (
    BrokerPortfolioRecord,
    BrokerPositionRecord,
    RuntimePortfolioAdapter,
    RuntimePortfolioRecord,
    RuntimePositionRecord,
)
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    PortfolioState,
    PortfolioTarget,
    PositionOrigin,
    PositionOwnership,
)
from trading_robot.portfolio_reconciler import (
    PortfolioReconciler,
    ReconciliationContext,
)
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_profile_editor import (
    RiskProfileEditContext,
    RiskProfileEditError,
    RiskProfileEditor,
)
from trading_robot.strategy_runtime import StrategyDecision


def test_export_filename_uses_version_and_timestamp():
    moment = datetime(2026, 7, 31, 19, 58, 0)
    assert build_export_filename(
        "trading_events", "v3.7-alpha2", ".csv", now=moment
    ) == "trading_events_v3_7_alpha2_2026-07-31_195800.csv"


def test_export_filename_removes_windows_invalid_characters():
    assert sanitize_component('v3.7:alpha/1*1?') == "v3_7_alpha_1_1"


def test_collision_safe_path_adds_numeric_suffix(tmp_path: Path):
    first = tmp_path / "events.csv"
    first.write_text("one", encoding="utf-8")
    second = collision_safe_path(first)
    assert second.name == "events_02.csv"
    second.write_text("two", encoding="utf-8")
    assert collision_safe_path(first).name == "events_03.csv"


def test_collision_safe_path_supports_unicode_directories(tmp_path: Path):
    folder = tmp_path / "Тестовая папка"
    folder.mkdir()
    assert collision_safe_path(folder / "журнал.csv").parent == folder


@pytest.mark.parametrize("value,expected", [(1, 1), ("4", 4), (32, 32), (100, 100)])
def test_risk_editor_validates_sandbox_range(value, expected):
    assert RiskProfileEditor.validate_daily_order_limit(value) == expected


@pytest.mark.parametrize("value", [0, -1, 101, "", "text", "4.5", "4,5", True])
def test_risk_editor_rejects_invalid_limits(value):
    with pytest.raises(RiskProfileEditError):
        RiskProfileEditor.validate_daily_order_limit(value)


@pytest.mark.parametrize(
    "field",
    [
        "execution_active",
        "operation_active",
        "pending_order",
        "uncertain_order",
        "reconciliation_blocking",
        "maintenance_active",
    ],
)
def test_risk_editor_blocks_unsafe_operational_context(field: str):
    kwargs = {field: True}
    context = RiskProfileEditContext(
        account_id="account-1",
        mode="SANDBOX_EXECUTION",
        **kwargs,
    )
    assert context.block_reasons()


def _risk_editor(tmp_path: Path):
    profiles = RiskProfileStore(tmp_path / "risk_profiles.json")
    states = RiskStateStore(tmp_path / "risk_state.json")
    journal = EventJournal(tmp_path / "events.db")
    profiles.save_profile("SANDBOX_EXECUTION", RiskPolicy(max_orders_per_day=4))
    states.save_account(
        "account-1",
        RiskState(
            daily_order_count=3,
            daily_turnover_rub=1234.5,
            recorded_execution_ids=("exec-1", "exec-2", "exec-3"),
        ),
    )
    return profiles, states, journal, RiskProfileEditor(
        profile_store=profiles,
        state_store=states,
        journal=journal,
    )


def test_risk_editor_updates_profile_and_preserves_counters(tmp_path: Path):
    profiles, states, journal, editor = _risk_editor(tmp_path)
    result = editor.apply_max_orders_per_day(
        RiskProfileEditContext("account-1", "SANDBOX_EXECUTION"),
        32,
    )
    assert result.changed is True
    assert result.old_limit == 4
    assert result.new_limit == 32
    loaded = profiles.require_profile("SANDBOX_EXECUTION")
    assert loaded["policy"].max_orders_per_day == 32
    assert loaded["account_scope"] == "account-1"
    assert loaded["source"] == "GUI_OPERATOR"
    state = states.load_account("account-1")
    assert state.daily_order_count == 3
    assert state.daily_turnover_rub == pytest.approx(1234.5)
    assert state.recorded_execution_ids == ("exec-1", "exec-2", "exec-3")
    rows = journal.recent(event_type="RISK_PROFILE_UPDATED")
    assert len(rows) == 1
    assert rows[0]["payload"]["old_value"] == 4
    assert rows[0]["payload"]["new_value"] == 32


def test_risk_editor_idempotent_apply_does_not_duplicate_event(tmp_path: Path):
    _profiles, _states, journal, editor = _risk_editor(tmp_path)
    context = RiskProfileEditContext("account-1", "SANDBOX_EXECUTION")
    first = editor.apply_max_orders_per_day(context, 4)
    second = editor.apply_max_orders_per_day(context, 4)
    assert first.changed is True  # first call adds the account scope
    assert second.changed is False
    assert len(journal.recent(event_type="RISK_PROFILE_UPDATED")) == 1


def test_risk_editor_decrease_below_current_count_reports_halted(tmp_path: Path):
    _profiles, _states, _journal, editor = _risk_editor(tmp_path)
    result = editor.apply_max_orders_per_day(
        RiskProfileEditContext("account-1", "SANDBOX_EXECUTION"),
        2,
    )
    assert result.halted_by_limit is True
    assert result.current_count == 3


def _broker_record(lots: int = 1) -> BrokerPortfolioRecord:
    return BrokerPortfolioRecord(
        account=AccountState(
            account_id="account-1",
            total_value=50_000,
            securities_value=275 * lots,
            expected_yield=0,
            cash_balances=(CashBalance("rub", 49_725),),
        ),
        snapshot_at=datetime.now(timezone.utc).isoformat(),
        positions=(
            BrokerPositionRecord(
                instrument_id="uid-sber",
                figi="figi-sber",
                ticker="SBER",
                class_code="TQBR",
                asset_type="share",
                currency="rub",
                quantity=float(lots),
                actual_lots=lots,
                average_price=270,
                current_price=275,
                market_value=275 * lots,
                expected_yield=5,
                pending_orders=(),
            ),
        ),
        source_status="OK",
        warnings=(),
    )


def test_diagnostic_origin_is_visible_for_unattributed_position():
    state = PortfolioReconciler().reconcile(
        _broker_record(),
        RuntimePortfolioRecord("account-1", (), (), "OK"),
        context=ReconciliationContext(
            position_origins={"uid-sber": PositionOrigin.DIAGNOSTIC}
        ),
    )
    assert state.positions[0].origin is PositionOrigin.DIAGNOSTIC


def test_strategy_runtime_takes_precedence_over_origin_hint():
    runtime = RuntimePortfolioRecord(
        account_id="account-1",
        positions=(
            RuntimePositionRecord(
                instrument_id="uid-sber",
                figi="figi-sber",
                ticker="SBER",
                class_code="TQBR",
                target=PortfolioTarget("uid-sber", 1, "sma", "a" * 64),
                ownership=PositionOwnership("sma", "a" * 64, "CANDLE_INTERVAL_10_MIN"),
                pending_orders=(),
                last_candle_time=None,
                state_key="state",
            ),
        ),
        warnings=(),
        state_status="OK",
    )
    state = PortfolioReconciler().reconcile(
        _broker_record(),
        runtime,
        context=ReconciliationContext(
            position_origins={"uid-sber": PositionOrigin.DIAGNOSTIC}
        ),
    )
    assert state.positions[0].origin is PositionOrigin.STRATEGY


def test_position_origin_round_trip_and_backward_default():
    state = PortfolioReconciler().reconcile(
        _broker_record(),
        RuntimePortfolioRecord("account-1", (), (), "OK"),
        context=ReconciliationContext(
            position_origins={"uid-sber": PositionOrigin.DIAGNOSTIC}
        ),
    )
    payload = state.to_dict()
    assert PortfolioState.from_dict(payload).positions[0].origin is PositionOrigin.DIAGNOSTIC
    del payload["positions"][0]["origin"]
    assert PortfolioState.from_dict(payload).positions[0].origin is PositionOrigin.UNKNOWN


class _FlatPortfolioAPI:
    def __init__(self, *, lots: int = 0, orders: list[dict] | None = None) -> None:
        self.lots = lots
        self.orders = list(orders or [])

    def find_instrument(self, ticker: str, class_code: str):
        return {
            "ticker": ticker,
            "classCode": class_code,
            "uid": "uid-sber",
            "figi": "figi-sber",
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_portfolio(self, account_id: str):
        return {"positions": [] if self.lots == 0 else [{"quantityLots": {"units": str(self.lots)}}]}

    def get_orders(self, account_id: str):
        return list(self.orders)

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions") or []
        return int(positions[0]["quantityLots"]["units"]) if positions else 0


def _ack_state() -> dict:
    scope = "account-1|SBER_TQBR"
    key = scope + "|CANDLE_INTERVAL_10_MIN|primary:sma:aaaaaaaaaaaaaaaa"
    return {
        "version": 6,
        "bots": {
            key: {
                "instrument_id": "uid-sber",
                "figi": "figi-sber",
                "last_seen_candle": "2026-07-31T12:00:00+00:00",
                "last_consumed_candle": "2026-07-31T12:00:00+00:00",
                "last_strategy_decisions": {
                    "sma": {
                        "strategy_id": "sma",
                        "config_hash": "a" * 64,
                        "candle_time": "2026-07-31T12:00:00+00:00",
                        "target_lots": 1,
                    }
                },
            }
        },
        "execution_scopes": {
            scope: {
                "active_primary": {
                    "strategy_id": "sma",
                    "config_hash": "a" * 64,
                    "candle_interval": "CANDLE_INTERVAL_10_MIN",
                }
            }
        },
    }


def _ack_manager(tmp_path: Path, api: _FlatPortfolioAPI) -> PortfolioManager:
    state_path = tmp_path / "robot_state.json"
    state_path.write_text(json.dumps(_ack_state()), encoding="utf-8")
    service = PortfolioManager(
        api,
        "account-1",
        state_file=state_path,
        journal_file=tmp_path / "events.db",
    )
    order_id = "filled-order-1"
    for event_type in (
        "FILLED",
        "PORTFOLIO_RECONCILED",
        "EXECUTION_RECORDED",
        "RISK_ACCOUNTED",
    ):
        service.journal.record(
            JournalEvent(
                category="order",
                event_type=event_type,
                account_id="account-1",
                instrument_id="uid-sber",
                ticker="SBER",
                order_id=order_id,
            )
        )
    return service


def _ack_request(phrase: str = "ACK EXTERNAL CLOSE SBER 0"):
    return ExternalCloseAcknowledgementRequest(
        account_id="account-1",
        ticker="SBER",
        class_code="TQBR",
        expected_target_lots=1,
        confirmation_text=phrase,
    )


def test_external_close_acknowledgement_clears_stale_target_audited(tmp_path: Path):
    service = _ack_manager(tmp_path, _FlatPortfolioAPI())
    result = service.acknowledge_external_close(_ack_request())
    assert result["status"] == "external_close_acknowledged"
    assert result["risk_state_changed"] is False
    state = json.loads((tmp_path / "robot_state.json").read_text(encoding="utf-8"))
    scope = state["execution_scopes"]["account-1|SBER_TQBR"]
    assert "active_primary" not in scope
    bot = next(iter(state["bots"].values()))
    assert bot["last_strategy_decisions"] == {}
    assert bot["last_confirmed_target_lots"] == 0
    events = service.journal.recent(account_id="account-1")
    types = [row["event_type"] for row in events]
    assert types.count("PORTFOLIO_TARGET_CLEARED") == 1
    assert types.count("EXTERNAL_CLOSE_ACKNOWLEDGED") == 1


def test_external_close_acknowledgement_rejects_wrong_phrase(tmp_path: Path):
    service = _ack_manager(tmp_path, _FlatPortfolioAPI())
    with pytest.raises(ValueError, match="Confirmation mismatch"):
        service.acknowledge_external_close(_ack_request("WRONG"))


def test_external_close_acknowledgement_rejects_nonflat_broker(tmp_path: Path):
    service = _ack_manager(tmp_path, _FlatPortfolioAPI(lots=1))
    with pytest.raises(RuntimeError, match="not flat"):
        service.acknowledge_external_close(_ack_request())


def test_external_close_acknowledgement_rejects_pending_order(tmp_path: Path):
    service = _ack_manager(
        tmp_path,
        _FlatPortfolioAPI(
            orders=[{"instrumentUid": "uid-sber", "orderRequestId": "pending"}]
        ),
    )
    with pytest.raises(RuntimeError, match="active order"):
        service.acknowledge_external_close(_ack_request())


def test_runtime_adapter_suppresses_acknowledged_stale_decision(tmp_path: Path):
    state = _ack_state()
    bot = next(iter(state["bots"].values()))
    bot["external_close_acknowledgement"] = {
        "acknowledgement_id": "ack-1",
        "effective_through_candle": "2026-07-31T12:00:00+00:00",
    }
    path = tmp_path / "robot_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    runtime = RuntimePortfolioAdapter.from_robot_state(path, account_id="account-1")
    assert runtime.positions[0].target is None
    assert runtime.positions[0].ownership is None


def test_gui_source_contains_alpha1_1_operator_features():
    source = (Path(__file__).resolve().parents[1] / "desktop_gui.py").read_text(
        encoding="utf-8"
    )
    assert "ACK EXTERNAL CLOSE" in source
    assert "RISK_PROFILE_UPDATED" not in source  # journal event belongs to service layer
    assert "Sandbox burn-in (32)" in source
    assert "build_export_filename" in source
    assert "trading_events_v3_6_beta1.csv" not in source


def test_ack_suppression_persists_effective_primary_decision():
    bot = object.__new__(SandboxTradingBot)
    bot.account_id = "account-1"
    bot.instrument_id = "uid-sber"
    bot.config = SimpleNamespace(ticker="SBER", candle_interval="CANDLE_INTERVAL_10_MIN")
    bot.strategy_suite = SimpleNamespace(
        parameter_snapshot=lambda strategy: {"strategy": strategy}
    )
    bot.suite_hash = "suite-hash"
    bot.session_id = "session-1"
    captured = []
    bot._journal_safe = captured.append

    decision = StrategyDecision(
        candle_time="2026-07-31T12:00:00+00:00",
        strategy_id="sma",
        strategy_version="1.1",
        role="PRIMARY",
        config_hash="a" * 64,
        signal=1,
        target_weight=1.0,
        target_lots=1,
        reason="original",
        indicators={},
        bars_used=50,
        required_bars=25,
    )
    effective = decision.to_dict()
    effective.update(
        signal=0,
        target_weight=0.0,
        target_lots=0,
        reason="external close acknowledged",
        external_close_ack_suppressed=True,
    )
    root = {}
    bot_state = {}
    bot._record_strategy_decisions_safe(
        root_state=root,
        bot_state=bot_state,
        decisions={"sma": decision},
        decision_payloads={"sma": effective},
        comparison={
            "candle_time": decision.candle_time,
            "event_type": "AGREEMENT",
            "all_agree": True,
        },
        run_id="run-1",
    )

    state = next(iter(root["strategy_states"].values()))
    assert state["last_decision"]["target_lots"] == 0
    assert state["last_decision"]["external_close_ack_suppressed"] is True
    primary_event = next(event for event in captured if event.event_type == "PRIMARY_DECISION")
    assert primary_event.payload["target_lots"] == 0
    assert primary_event.payload["external_close_ack_suppressed"] is True
