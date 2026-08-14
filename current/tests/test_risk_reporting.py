from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_reporting import (
    build_risk_burn_in_report,
    build_risk_dashboard_snapshot,
    load_risk_dashboard_snapshot,
    write_burn_in_report,
    write_dashboard_snapshot,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def _decision_row(
    *,
    account_id: str = "ACC",
    status: str = "PASS",
    current_lots: int = 0,
    requested: int = 1,
    approved: int = 1,
    price_per_lot: float = 250.0,
    event_id: int = 10,
):
    return {
        "id": event_id,
        "timestamp_utc": NOW.isoformat(),
        "category": "risk",
        "event_type": "RISK_EVALUATED",
        "severity": "INFO",
        "account_id": account_id,
        "payload": {
            "risk_decision": {
                "status": status,
                "current_lots": current_lots,
                "requested_target_lots": requested,
                "approved_target_lots": approved,
                "approved_delta_lots": approved - current_lots,
                "breaches": [],
                "reasons": ["ok"],
                "lot_caps": {"MAX_POSITION_LOTS": 1},
                "metrics": {
                    "equity_rub": 50_000.0,
                    "cash_rub": 49_750.0,
                    "securities_value_rub": 250.0 if current_lots else 0.0,
                    "price_per_lot_rub": price_per_lot,
                    "risk_budget_rub": 500.0,
                    "risk_per_lot_rub": 3.0,
                },
            }
        },
    }


def _state(**changes) -> RiskState:
    base = RiskState(
        daily_date="2026-07-24",
        weekly_key="2026-W30",
        daily_start_equity_rub=50_000.0,
        weekly_start_equity_rub=50_100.0,
        high_watermark_equity_rub=50_200.0,
        daily_turnover_rub=500.0,
        daily_order_count=1,
        last_equity_rub=49_900.0,
        last_cash_rub=49_650.0,
        last_snapshot_at=(NOW - timedelta(seconds=30)).isoformat(),
        last_evaluated_at=NOW.isoformat(),
    )
    return replace(base, **changes)


def test_dashboard_snapshot_calculates_capacity_and_limits():
    snapshot = build_risk_dashboard_snapshot(
        account_id="ACC",
        mode="SANDBOX_EXECUTION",
        policy=RiskPolicy(),
        state=_state(),
        recent_rows=[_decision_row()],
        now=NOW,
    )

    assert snapshot.engine_status == "ACTIVE"
    assert snapshot.summary["daily_pnl_rub"] == -100.0
    assert snapshot.summary["weekly_pnl_rub"] == -200.0
    assert snapshot.summary["orders_remaining"] == 3
    assert snapshot.summary["round_trip_ready"] is True
    assert snapshot.summary["estimated_round_trip_turnover_rub"] == 500.0
    assert any(row.code == "MAX_ORDERS_PER_DAY" for row in snapshot.limits)


def test_dashboard_persistent_gates_override_latest_pass():
    halted = build_risk_dashboard_snapshot(
        account_id="ACC",
        mode="SANDBOX_EXECUTION",
        policy=RiskPolicy(),
        state=_state(kill_switch_active=True, kill_switch_reason="test"),
        recent_rows=[_decision_row()],
        now=NOW,
    )
    assert halted.engine_status == "HALTED"
    assert halted.summary["round_trip_ready"] is False

    resync = build_risk_dashboard_snapshot(
        account_id="ACC",
        mode="SANDBOX_EXECUTION",
        policy=RiskPolicy(),
        state=_state(
            kill_switch_active=True,
            risk_resync_required=True,
            risk_resync_reason="external",
        ),
        recent_rows=[_decision_row()],
        now=NOW,
    )
    assert resync.engine_status == "RESYNC_REQUIRED"


def test_dashboard_round_trip_preflight_blocks_when_daily_capacity_exhausted():
    policy = replace(
        RiskPolicy(),
        max_orders_per_day=4,
        max_daily_turnover_rub=1_000.0,
    )
    snapshot = build_risk_dashboard_snapshot(
        account_id="ACC",
        mode="SANDBOX_EXECUTION",
        policy=policy,
        state=_state(daily_order_count=3, daily_turnover_rub=900.0),
        recent_rows=[_decision_row(price_per_lot=250.0)],
        now=NOW,
    )

    assert snapshot.summary["orders_remaining"] == 1
    assert snapshot.summary["daily_turnover_remaining_rub"] == 100.0
    assert snapshot.summary["round_trip_ready"] is False
    assert len(snapshot.summary["round_trip_blockers"]) == 2


def test_load_dashboard_filters_events_by_account(tmp_path: Path):
    profiles = RiskProfileStore(tmp_path / "risk_profiles.json")
    profiles.save_profile("SANDBOX_EXECUTION", RiskPolicy())
    states = RiskStateStore(tmp_path / "risk_state.json")
    states.save_account("A", _state())
    states.save_account("B", _state())
    journal = EventJournal(tmp_path / "events.db")
    for account, status in (("A", "PASS"), ("B", "BLOCKED")):
        journal.record(
            JournalEvent(
                category="risk",
                event_type="RISK_EVALUATED",
                account_id=account,
                payload={
                    "risk_decision": {
                        "status": status,
                        "current_lots": 0,
                        "requested_target_lots": 1,
                        "approved_target_lots": 1 if status == "PASS" else 0,
                        "approved_delta_lots": 1 if status == "PASS" else 0,
                        "breaches": [],
                        "reasons": [],
                        "metrics": {"price_per_lot_rub": 250.0},
                    }
                },
            )
        )

    snapshot = load_risk_dashboard_snapshot(
        profile_store=profiles,
        state_store=states,
        journal=journal,
        account_id="A",
        now=NOW,
    )

    assert snapshot.latest_decision is not None
    assert snapshot.latest_decision["status"] == "PASS"
    assert all(row["account_id"] == "A" for row in snapshot.recent_events)


def _order_events(order_id: str = "ORDER-1"):
    names = (
        "INTENT_SAVED",
        "ORDER_SUBMITTED",
        "ORDER_ACCEPTED",
        "FILLED",
        "PORTFOLIO_RECONCILED",
        "EXECUTION_RECORDED",
        "RISK_ACCOUNTED",
    )
    rows = []
    for idx, name in enumerate(names, start=1):
        rows.append(
            {
                "id": idx,
                "timestamp_utc": (NOW + timedelta(seconds=idx)).isoformat(),
                "category": "risk" if name == "EXECUTION_RECORDED" else "order",
                "event_type": name,
                "severity": "INFO",
                "account_id": "ACC",
                "order_id": order_id,
                "payload": {
                    "risk_decision_id": "DECISION",
                    "risk_policy_hash": "HASH",
                    "risk_execution_id": order_id,
                    "duplicate": False,
                    "execution_source": "STRATEGY",
                },
            }
        )
    return rows


def test_burn_in_report_passes_for_complete_causal_order():
    report = build_risk_burn_in_report(
        _order_events(),
        account_id="ACC",
        now=NOW + timedelta(minutes=1),
    )

    assert report.overall_status == "PASS"
    assert report.metrics["orders_submitted"] == 1
    assert report.metrics["executions_recorded"] == 1
    assert all(check.status == "PASS" for check in report.checks)


def test_burn_in_report_detects_duplicate_and_missing_reconciliation():
    rows = _order_events()
    rows = [row for row in rows if row["event_type"] != "PORTFOLIO_RECONCILED"]
    duplicate = dict(rows[1])
    duplicate["id"] = 99
    rows.append(duplicate)

    report = build_risk_burn_in_report(rows, account_id="ACC", now=NOW)
    checks = {check.code: check for check in report.checks}

    assert report.overall_status == "FAIL"
    assert checks["DUPLICATE_ORDER_SUBMISSION"].status == "FAIL"
    assert checks["FILLED_WITHOUT_RECONCILIATION"].status == "FAIL"


def test_burn_in_report_warns_for_api_degraded_and_open_session():
    rows = [
        {
            "id": 1,
            "timestamp_utc": NOW.isoformat(),
            "category": "session",
            "event_type": "STARTED",
            "severity": "INFO",
            "session_id": "S1",
            "account_id": "ACC",
            "payload": {},
        },
        {
            "id": 2,
            "timestamp_utc": NOW.isoformat(),
            "category": "cycle",
            "event_type": "api_degraded",
            "status": "api_degraded",
            "severity": "WARNING",
            "account_id": "ACC",
            "payload": {},
        },
    ]
    report = build_risk_burn_in_report(rows, account_id="ACC", now=NOW)

    assert report.overall_status == "WARN"
    assert report.metrics["api_degraded_cycles"] == 1


def test_recovered_transient_api_failure_is_infrastructure_warn():
    rows = [
        {
            "id": 1,
            "timestamp_utc": NOW.isoformat(),
            "category": "api",
            "event_type": "API_REQUEST_FAILED",
            "severity": "ERROR",
            "account_id": "ACC",
            "payload": {"transient": True, "error_class": "IncompleteRead"},
        },
        {
            "id": 2,
            "timestamp_utc": (NOW + timedelta(seconds=10)).isoformat(),
            "category": "portfolio",
            "event_type": "PORTFOLIO_STATE_PUBLISHED",
            "severity": "INFO",
            "status": "READY",
            "account_id": "ACC",
            "payload": {
                "portfolio_source": "CANONICAL",
                "freshness": "FRESH",
                "blocking": False,
                "reconciliation_counts": {"MATCHED": 1},
            },
        },
    ]

    report = build_risk_burn_in_report(rows, account_id="ACC", now=NOW)
    checks = {check.code: check for check in report.checks}

    assert checks["API_FAILURE_CLASSIFICATION"].status == "WARN"
    assert checks["RISK_RUNTIME_ERRORS"].status == "PASS"
    assert report.overall_status == "WARN"
    assert report.metrics["api_transient_recovered"] == 1
    assert report.metrics["api_transient_unresolved"] == 0


def test_unresolved_transient_api_failure_remains_fail():
    rows = [
        {
            "id": 1,
            "timestamp_utc": NOW.isoformat(),
            "category": "api",
            "event_type": "API_REQUEST_FAILED",
            "severity": "ERROR",
            "account_id": "ACC",
            "payload": {"transient": True, "error_class": "ConnectionError"},
        }
    ]

    report = build_risk_burn_in_report(rows, account_id="ACC", now=NOW)
    checks = {check.code: check for check in report.checks}

    assert checks["API_FAILURE_CLASSIFICATION"].status == "FAIL"
    assert report.overall_status == "FAIL"
    assert report.metrics["api_transient_unresolved"] == 1


def test_report_writers_create_json_and_csv(tmp_path: Path):
    snapshot = build_risk_dashboard_snapshot(
        account_id="ACC",
        mode="SANDBOX_EXECUTION",
        policy=RiskPolicy(),
        state=_state(),
        recent_rows=[_decision_row()],
        now=NOW,
    )
    snapshot_path = write_dashboard_snapshot(snapshot, tmp_path / "snapshot.json")
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["account_id"] == "ACC"

    report = build_risk_burn_in_report(_order_events(), account_id="ACC", now=NOW)
    paths = write_burn_in_report(report, tmp_path / "report")
    assert paths["json"].exists()
    assert paths["checks_csv"].read_text(encoding="utf-8-sig").startswith("code,")
    assert paths["metrics_csv"].exists()


def test_burn_in_report_allows_operator_authorized_diagnostic_order_without_strategy_risk_decision():
    rows = _order_events(order_id="DIAG-1")
    for row in rows:
        if row["event_type"] in {
            "INTENT_SAVED",
            "ORDER_SUBMITTED",
            "ORDER_ACCEPTED",
            "FILLED",
            "PORTFOLIO_RECONCILED",
            "RISK_ACCOUNTED",
        }:
            row["category"] = "diagnostic_order"
            row["payload"].pop("risk_decision_id", None)
        row["payload"]["execution_source"] = "DIAGNOSTIC"

    report = build_risk_burn_in_report(rows, account_id="ACC", now=NOW)
    checks = {check.code: check for check in report.checks}

    assert checks["RISK_AUTHORIZATION_PRESENT"].status == "PASS"




def test_dashboard_and_exports_preserve_full_account_id_and_kill_switch_time(tmp_path: Path):
    account_id = "00000000-0000-4000-8000-000000000042"
    set_at = "2026-07-25T10:00:00+00:00"
    snapshot = build_risk_dashboard_snapshot(
        account_id=account_id,
        mode="SANDBOX_EXECUTION",
        policy=RiskPolicy(),
        state=_state(
            kill_switch_active=True,
            kill_switch_reason="operator acceptance",
            kill_switch_set_at=set_at,
        ),
        recent_rows=[_decision_row(account_id=account_id)],
        now=NOW,
    )

    assert snapshot.account_id == account_id
    assert snapshot.summary["kill_switch_set_at"] == set_at
    path = write_dashboard_snapshot(snapshot, tmp_path / "snapshot-full-account.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["account_id"] == account_id
    assert payload["summary"]["kill_switch_set_at"] == set_at

    report = build_risk_burn_in_report(
        _order_events(), account_id=account_id, now=NOW
    )
    assert report.account_id == account_id
    assert report.to_dict()["account_id"] == account_id

def test_burn_in_report_can_start_at_first_matching_software_version_session():
    legacy = _order_events(order_id="LEGACY")
    for row in legacy:
        row["id"] -= 20
    legacy.append(
        {
            "id": -30,
            "timestamp_utc": (NOW - timedelta(hours=2)).isoformat(),
            "category": "session",
            "event_type": "STARTED",
            "severity": "INFO",
            "session_id": "OLD",
            "account_id": "ACC",
            "payload": {"software_version": "0.3.6a3.post1"},
        }
    )
    # Deliberately corrupt the legacy causal order; it must be excluded.
    legacy = [row for row in legacy if row["event_type"] != "PORTFOLIO_RECONCILED"]
    current = [
        {
            "id": 100,
            "timestamp_utc": NOW.isoformat(),
            "category": "session",
            "event_type": "STARTED",
            "severity": "INFO",
            "session_id": "BETA",
            "account_id": "ACC",
            "payload": {"software_version": "0.3.6"},
        },
        *_order_events(order_id="BETA-ORDER"),
        {
            "id": 200,
            "timestamp_utc": (NOW + timedelta(minutes=1)).isoformat(),
            "category": "session",
            "event_type": "STOPPED",
            "severity": "INFO",
            "session_id": "BETA",
            "account_id": "ACC",
            "payload": {"software_version": "0.3.6"},
        },
    ]
    # Move current order events after the beta marker.
    for offset, row in enumerate(current[1:-1], start=101):
        row["id"] = offset

    report = build_risk_burn_in_report(
        [*legacy, *current],
        account_id="ACC",
        now=NOW + timedelta(minutes=2),
        software_version="0.3.6",
    )

    assert report.overall_status == "PASS"
    assert report.metrics["software_version_marker_found"] is True
    assert report.metrics["software_version_marker_event_id"] == 100
    assert report.metrics["unique_orders"] == 1


def test_dashboard_preserves_full_account_id_and_kill_switch_audit_metadata():
    full_id = "00000000-0000-4000-8000-000000000042"
    rows = [
        _decision_row(account_id=full_id),
        {
            "id": 99,
            "timestamp_utc": NOW.isoformat(),
            "category": "risk_control",
            "event_type": "KILL_SWITCH_ENGAGED",
            "severity": "WARNING",
            "account_id": full_id,
            "payload": {
                "actor": "operator",
                "source": "risk_control_tool",
                "reason": "acceptance",
                "changed_at": NOW.isoformat(),
            },
        },
    ]
    snapshot = build_risk_dashboard_snapshot(
        account_id=full_id,
        mode="SANDBOX_EXECUTION",
        policy=RiskPolicy(),
        state=_state(
            kill_switch_active=True,
            kill_switch_reason="acceptance",
            kill_switch_set_at=NOW.isoformat(),
        ),
        recent_rows=rows,
        now=NOW,
    )

    assert snapshot.account_id == full_id
    assert snapshot.to_dict()["account_id"] == full_id
    assert snapshot.summary["kill_switch_actor"] == "operator"
    assert snapshot.summary["kill_switch_source"] == "risk_control_tool"
    assert snapshot.summary["kill_switch_set_at"] == NOW.isoformat()


def test_expected_policy_block_is_warn_not_runtime_fail():
    row = _decision_row(
        status="BLOCKED",
        requested=1,
        approved=0,
        event_id=501,
    )
    row["severity"] = "WARNING"
    row["payload"]["expected_policy_block"] = True
    row["payload"]["breaches"] = ["MAX_ORDERS_PER_DAY"]
    row["payload"]["risk_decision"]["breaches"] = ["MAX_ORDERS_PER_DAY"]
    row["payload"]["risk_decision"]["reasons"] = [
        "Daily order limit reached: 4/4."
    ]
    report = build_risk_burn_in_report(
        [row],
        account_id="ACC",
        now=NOW,
    )
    checks = {check.code: check for check in report.checks}
    assert checks["RISK_RUNTIME_ERRORS"].status == "PASS"
    assert checks["EXPECTED_POLICY_BLOCKS"].status == "WARN"
    assert report.overall_status == "WARN"
    assert report.metrics["expected_policy_blocks"] == 1
    assert report.metrics["policy_block_breaches"] == {
        "MAX_ORDERS_PER_DAY": 1
    }


def test_dashboard_warns_when_daily_order_limit_is_reached():
    snapshot = build_risk_dashboard_snapshot(
        account_id="ACC",
        mode="SANDBOX_EXECUTION",
        policy=RiskPolicy(max_orders_per_day=4),
        state=_state(daily_order_count=4),
        recent_rows=[_decision_row(account_id="ACC")],
        now=NOW,
    )
    assert any(
        "Дневной лимит заявок достигнут: 4/4" in warning
        for warning in snapshot.warnings
    )
