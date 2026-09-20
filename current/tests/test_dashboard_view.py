from __future__ import annotations

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from trading_robot.dashboard_view import (
    InstrumentRuntimeView,
    MultiInstrumentDashboard,
    build_kill_switch_banner,
    full_account_id,
    latest_sandbox_decisions,
)
from trading_robot.journal import EventJournal, JournalEvent


def test_full_account_id_is_never_abbreviated():
    account_id = "00000000-0000-4000-8000-000000000042"
    assert full_account_id(account_id) == account_id


def test_kill_switch_banner_unknown_off_and_on():
    unknown = build_kill_switch_banner(None)
    assert unknown.state == "UNKNOWN"
    assert "UNKNOWN" in unknown.title

    off = build_kill_switch_banner({"kill_switch_active": False})
    assert off.state == "OFF"
    assert off.title == "KILL SWITCH: OFF"

    on = build_kill_switch_banner(
        {
            "kill_switch_active": True,
            "kill_switch_reason": "operator acceptance",
            "kill_switch_set_at": "2026-07-25T10:00:00+00:00",
            "kill_switch_actor": "risk_control_tool",
            "kill_switch_source": "CLI",
            "kill_switch_reduce_only_allowed": True,
        }
    )
    assert on.state == "ON"
    assert "НОВЫЕ ВХОДЫ ЗАБЛОКИРОВАНЫ" in on.title
    assert "operator acceptance" in on.detail
    assert "risk_control_tool" in on.detail
    assert "Reduce-only" in on.detail


def _decision_event(
    event_id: int,
    *,
    instrument_id: str = "configured-instrument",
    session_id: str = "current-session",
    account_scope_sha256: str = "a" * 64,
    event_type: str = "CENTRAL_COORDINATION_RESULT",
    action: str = "BUY",
    status: str = "RISK_BLOCKED",
    timestamp_utc: str = "2026-09-20T10:30:04+00:00",
) -> dict[str, object]:
    return {
        "id": event_id,
        "instrument_id": instrument_id,
        "session_id": session_id,
        "mode": "SANDBOX_EXECUTION",
        "event_type": event_type,
        "category": (
            "strategy" if event_type == "PRIMARY_STRATEGY_DECISION" else "decision"
        ),
        "action": action,
        "status": status,
        "timestamp_utc": timestamp_utc,
        "payload": {"account_scope_sha256": account_scope_sha256},
    }


def _project(*events: dict[str, object]):
    return latest_sandbox_decisions(
        events,
        session_id="current-session",
        account_scope_sha256="a" * 64,
        instrument_ids=("configured-instrument", "second-instrument"),
    )


def test_latest_decision_shows_actual_blocked_buy_hold_and_sell_actions():
    decisions = _project(
        _decision_event(10, action="BUY", status="RISK_BLOCKED"),
        _decision_event(11, action="HOLD", status="RISK_BLOCKED"),
        _decision_event(
            12,
            instrument_id="second-instrument",
            action="SELL",
            status="PREFLIGHT_BLOCKED",
        ),
    )
    assert decisions["configured-instrument"].action == "HOLD"
    assert decisions["configured-instrument"].status == "RISK_BLOCKED"
    assert decisions["configured-instrument"].at_utc == "2026-09-20 10:30:04Z"
    assert decisions["second-instrument"].action == "SELL"
    assert decisions["second-instrument"].status == "PREFLIGHT_BLOCKED"


def test_latest_decision_does_not_borrow_other_session_account_or_instrument():
    decisions = _project(
        _decision_event(1),
        _decision_event(20, session_id="previous-session", status="SUBMITTED"),
        _decision_event(21, account_scope_sha256="b" * 64, status="SUBMITTED"),
        _decision_event(22, instrument_id="unconfigured", status="SUBMITTED"),
    )
    assert set(decisions) == {"configured-instrument"}
    assert decisions["configured-instrument"].status == "RISK_BLOCKED"


def test_new_primary_proposal_is_not_displayed_as_old_buy_or_submitted_order():
    decisions = _project(
        _decision_event(8, action="BUY", status="RISK_BLOCKED"),
        _decision_event(
            9,
            event_type="PRIMARY_STRATEGY_DECISION",
            action="LONG",
            status="PROPOSED_NOT_AUTHORIZED",
        ),
    )
    assert decisions["configured-instrument"].action == "LONG"
    assert decisions["configured-instrument"].status == "PROPOSED_NOT_AUTHORIZED"


def test_invalid_current_session_decision_fails_closed_in_display():
    decisions = _project(
        _decision_event(3, action="BUY", status="RISK_BLOCKED"),
        _decision_event(4, action="BUY", status="SUBMITTED\nprivate-data"),
    )
    assert decisions["configured-instrument"].action == "AUDIT_INVALID"
    assert decisions["configured-instrument"].status == "AUDIT_INVALID"


def test_custom_equality_cannot_impersonate_account_scope():
    class EqualToEverything:
        def __eq__(self, _other):
            return True

    event = _decision_event(5, status="SUBMITTED")
    event["payload"] = {"account_scope_sha256": EqualToEverything()}
    assert _project(event) == {}


def test_wrong_journal_category_cannot_impersonate_coordination_outcome():
    event = _decision_event(6, status="SUBMITTED")
    event["category"] = "provider"
    assert _project(event) == {}


def test_decision_projection_reads_committed_journal_outcome(tmp_path):
    journal = EventJournal(tmp_path / "synthetic-events.db")
    journal.record(
        JournalEvent(
            category="decision",
            event_type="CENTRAL_COORDINATION_RESULT",
            session_id="current-session",
            instrument_id="configured-instrument",
            mode="SANDBOX_EXECUTION",
            action="BUY",
            status="PORTFOLIO_RISK_ADMISSION_UNAVAILABLE",
            payload={
                "account_scope_sha256": "a" * 64,
                "execution_authorized": False,
            },
            timestamp_utc="2026-09-20T10:30:04+00:00",
        )
    )
    events = journal.recent(
        limit=256,
        session_id="current-session",
        event_type="CENTRAL_COORDINATION_RESULT",
    )
    decision = _project(*events)["configured-instrument"]
    assert decision.action == "BUY"
    assert decision.status == "PORTFOLIO_RISK_ADMISSION_UNAVAILABLE"


def test_gui_row_separates_journal_decision_from_unknown_position(tmp_path):
    from desktop_gui import TradingRobotGUI

    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Tree:
        def __init__(self):
            self.values = []

        def get_children(self):
            return ()

        def delete(self, *_items):
            self.values.clear()

        def insert(self, _parent, _index, *, tags, values):
            self.values.append(values)

    journal = EventJournal(tmp_path / "gui-events.db")
    journal.record(
        JournalEvent(
            category="decision",
            event_type="CENTRAL_COORDINATION_RESULT",
            session_id="current-session",
            instrument_id="configured-instrument",
            mode="SANDBOX_EXECUTION",
            action="BUY",
            status="RISK_BLOCKED",
            payload={"account_scope_sha256": "a" * 64},
            timestamp_utc="2026-09-20T10:30:04+00:00",
        )
    )
    row = InstrumentRuntimeView(
        instrument_id="configured-instrument",
        ticker="SYNTHETIC",
        candle_interval="CANDLE_INTERVAL_HOUR",
        strategy_id="sma",
        runtime_status="STOPPED",
        identity_status="MATCHED",
        current_lots=0,
        pending_orders=0,
        last_processed_candle="",
        runtime_key="synthetic-key",
        runtime_config_hash="synthetic-hash",
        detail="Missing owner evidence: PORTFOLIO",
    )
    controller = SimpleNamespace(
        session_id="current-session",
        account_scope_sha256="a" * 64,
        dashboard=lambda: MultiInstrumentDashboard(
            mode="SANDBOX_EXECUTION",
            state="ATTENTION",
            account_id="synthetic-account",
            detail="Missing owner evidence: PORTFOLIO",
            rows=(row,),
        ),
        portfolio_risk_runtime=SimpleNamespace(
            profile_store=SimpleNamespace(
                load_profile=lambda _mode: {
                    "portfolio_policy_status": "CONFIGURATION_REQUIRED"
                }
            )
        ),
    )
    tree = Tree()
    status = Variable()
    gui = SimpleNamespace(
        multi_instrument_tree=tree,
        multi_instrument_status=status,
        sb_profile_mode=Variable("SANDBOX_EXECUTION"),
        gui_runtime_controller=controller,
        event_journal=journal,
    )
    TradingRobotGUI._refresh_multi_instrument_dashboard(gui)
    assert tree.values[0][5] == "BUY / RISK_BLOCKED"
    assert tree.values[0][6] == "2026-09-20 10:30:04Z"
    assert tree.values[0][9] == "UNKNOWN"  # Position owner remains unknown.
    assert "Portfolio Risk profile: CONFIGURATION_REQUIRED" in status.get()

    gui.event_journal = SimpleNamespace(recent=lambda **_kwargs: 1 / 0)
    del gui._sandbox_decision_display_cache
    TradingRobotGUI._refresh_multi_instrument_dashboard(gui)
    assert tree.values[0][5] == "AUDIT_UNAVAILABLE"

    for _ in range(256):
        journal.record(
            JournalEvent(
                category="decision",
                event_type="CENTRAL_COORDINATION_RESULT",
                session_id="current-session",
                instrument_id="noisy-instrument",
                mode="SANDBOX_EXECUTION",
                action="HOLD",
                status="RISK_BLOCKED",
                payload={"account_scope_sha256": "a" * 64},
                timestamp_utc="2026-09-20T10:31:04+00:00",
            )
        )
    assert all(
        event["instrument_id"] == "noisy-instrument"
        for event in journal.recent(
            limit=256,
            category="decision",
            session_id="current-session",
            event_type="CENTRAL_COORDINATION_RESULT",
        )
    )
    with pytest.raises(ValueError, match="instrument_id"):
        journal.recent(instrument_id="")
    with pytest.raises(ValueError, match="instrument_id"):
        journal.recent(
            instrument_id=type("InstrumentSubclass", (str,), {})(
                "configured-instrument"
            )
        )
    with sqlite3.connect(journal.path) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM events WHERE category = ? "
            "AND session_id = ? AND instrument_id = ? AND event_type = ? "
            "ORDER BY id DESC LIMIT 1",
            (
                "decision",
                "current-session",
                "configured-instrument",
                "CENTRAL_COORDINATION_RESULT",
            ),
        ).fetchall()
    assert any("idx_events_decision_scope" in row[3] for row in plan)

    noisy_row = replace(row, instrument_id="noisy-instrument", ticker="NOISY")
    controller.dashboard = lambda: MultiInstrumentDashboard(
        mode="SANDBOX_EXECUTION",
        state="ATTENTION",
        account_id="synthetic-account",
        detail="Synthetic read-only decision test",
        rows=(row, noisy_row),
    )
    gui.event_journal = journal
    del gui._sandbox_decision_display_cache
    TradingRobotGUI._refresh_multi_instrument_dashboard(gui)
    assert tree.values[0][5] == "BUY / RISK_BLOCKED"
    assert tree.values[1][5] == "HOLD / RISK_BLOCKED"

    journal.record(
        JournalEvent(
            category="decision",
            event_type="CENTRAL_COORDINATION_RESULT",
            session_id="current-session",
            instrument_id="configured-instrument",
            mode="SANDBOX_EXECUTION",
            action="SELL",
            status="SUBMITTED",
            payload={"account_scope_sha256": "b" * 64},
            timestamp_utc="2026-09-20T10:32:04+00:00",
        )
    )
    del gui._sandbox_decision_display_cache
    TradingRobotGUI._refresh_multi_instrument_dashboard(gui)
    assert tree.values[0][5] == "AUDIT_INVALID"


def test_existing_journal_bytes_are_preserved_until_first_append(tmp_path):
    path = tmp_path / "pre-upgrade-events.db"
    old = EventJournal(path)
    old.record(
        JournalEvent(
            category="decision",
            event_type="CENTRAL_COORDINATION_RESULT",
            session_id="session",
            instrument_id="instrument",
        )
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_events_decision_scope")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = path.read_bytes()

    upgraded = EventJournal(path)
    assert path.read_bytes() == before
    assert len(upgraded.recent(session_id="session", instrument_id="instrument")) == 1
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(events)").fetchall()
        }
    assert "idx_events_decision_scope" not in indexes

    upgraded.record(
        JournalEvent(
            category="decision",
            event_type="CENTRAL_COORDINATION_RESULT",
            session_id="session",
            instrument_id="instrument",
        )
    )
    with sqlite3.connect(path) as connection:
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(events)").fetchall()
        }
    assert "idx_events_decision_scope" in indexes
