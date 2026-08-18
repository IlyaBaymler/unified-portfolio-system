from __future__ import annotations

import argparse
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from trading_robot.risk import (
    RiskEngine,
    RiskPolicy,
    RiskSnapshot,
    RiskState,
)


DEFAULT_OUTPUT_DIR = Path("risk_alpha_output")


def _parse_datetime(value: Any, *, default: datetime | None = None) -> datetime:
    if value in (None, ""):
        if default is None:
            raise ValueError("Datetime value is required.")
        return default
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _policy_from_overrides(value: Mapping[str, Any] | None) -> RiskPolicy:
    if not value:
        return RiskPolicy()
    allowed = {field.name for field in fields(RiskPolicy)}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("Unknown RiskPolicy fields: " + ", ".join(unknown))
    return replace(RiskPolicy(), **dict(value))


def _snapshot_from_mapping(
    value: Mapping[str, Any],
    *,
    default_now: datetime,
) -> RiskSnapshot:
    raw = dict(value)
    now = _parse_datetime(raw.pop("now", None), default=default_now)
    snapshot_at = _parse_datetime(
        raw.pop("snapshot_at", None),
        default=now,
    )
    aliases = {
        "target_lots": "strategy_target_lots",
        "current": "current_lots",
        "price": "price_rub",
        "equity": "portfolio_equity_rub",
        "cash": "cash_rub",
        "atr": "atr_rub",
        "stop_distance": "stop_distance_rub",
        "reconciled": "position_reconciled",
        "pending": "pending_order",
    }
    for source, destination in aliases.items():
        if source in raw and destination not in raw:
            raw[destination] = raw.pop(source)
    return RiskSnapshot(now=now, snapshot_at=snapshot_at, **raw)


def built_in_scenarios() -> list[dict[str, Any]]:
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    common = {
        "now": now.isoformat(),
        "snapshot_at": now.isoformat(),
        "strategy_target_lots": 1,
        "current_lots": 0,
        "price_rub": 250.0,
        "lot_size": 10,
        "portfolio_equity_rub": 50_000.0,
        "cash_rub": 50_000.0,
        "securities_value_rub": 0.0,
        "atr_rub": 5.0,
        "position_reconciled": True,
        "pending_order": False,
        "mode": "DRY_RUN",
    }
    return [
        {
            "name": "PASS_BASELINE",
            "policy": {
                "max_position_value_rub": 100_000.0,
                "max_position_share_of_equity": 1.0,
                "max_order_value_rub": 100_000.0,
                "cash_reserve_rub": 0.0,
                "risk_per_trade_rub": None,
                "daily_loss_limit_rub": None,
                "daily_loss_limit_fraction": None,
                "weekly_loss_limit_rub": None,
                "max_drawdown_fraction": None,
                "max_daily_turnover_rub": None,
                "max_orders_per_day": None,
            },
            "snapshots": [common],
            "expected_statuses": ["PASS"],
        },
        {
            "name": "MAX_LOTS_ADJUSTMENT",
            "policy": {
                "max_position_lots": 1,
                "max_position_value_rub": 100_000.0,
                "max_position_share_of_equity": 1.0,
                "max_order_value_rub": 100_000.0,
                "cash_reserve_rub": 0.0,
                "risk_per_trade_rub": None,
                "daily_loss_limit_rub": None,
                "daily_loss_limit_fraction": None,
                "weekly_loss_limit_rub": None,
                "max_drawdown_fraction": None,
                "max_daily_turnover_rub": None,
                "max_orders_per_day": None,
            },
            "snapshots": [{**common, "strategy_target_lots": 4}],
            "expected_statuses": ["ADJUSTED"],
        },
        {
            "name": "STALE_SNAPSHOT_BLOCK",
            "policy": {"max_snapshot_age_seconds": 60},
            "snapshots": [
                {
                    **common,
                    "snapshot_at": (now - timedelta(seconds=61)).isoformat(),
                }
            ],
            "expected_statuses": ["BLOCKED"],
        },
        {
            "name": "PENDING_ORDER_BLOCK",
            "snapshots": [{**common, "pending_order": True}],
            "expected_statuses": ["BLOCKED"],
        },
        {
            "name": "UNRECONCILED_BLOCK",
            "snapshots": [{**common, "position_reconciled": False}],
            "expected_statuses": ["BLOCKED"],
        },
        {
            "name": "DAILY_LOSS_HALT",
            "policy": {
                "daily_loss_limit_rub": 500.0,
                "daily_loss_limit_fraction": None,
                "weekly_loss_limit_rub": None,
                "max_drawdown_fraction": None,
            },
            "snapshots": [
                {**common, "strategy_target_lots": 0},
                {
                    **common,
                    "now": (now + timedelta(hours=1)).isoformat(),
                    "snapshot_at": (now + timedelta(hours=1)).isoformat(),
                    "portfolio_equity_rub": 49_400.0,
                    "cash_rub": 49_400.0,
                },
            ],
            "expected_statuses": ["PASS", "BLOCKED"],
        },
        {
            "name": "KILL_SWITCH_REDUCE_ONLY",
            "state": {
                "kill_switch_active": True,
                "kill_switch_reason": "built-in test",
                "kill_switch_set_at": now.isoformat(),
            },
            "snapshots": [
                {**common, "strategy_target_lots": 0, "current_lots": 1}
            ],
            "expected_statuses": ["REDUCTION_ALLOWED"],
        },
        {
            "name": "ORDER_COUNT_HALT",
            "policy": {"max_orders_per_day": 2},
            "state": {
                "daily_date": "2026-07-22",
                "daily_start_equity_rub": 50_000.0,
                "weekly_key": "2026-W30",
                "weekly_start_equity_rub": 50_000.0,
                "high_watermark_equity_rub": 50_000.0,
                "daily_order_count": 2,
            },
            "snapshots": [common],
            "expected_statuses": ["BLOCKED"],
        },
    ]


def run_scenario(document: Mapping[str, Any]) -> dict[str, Any]:
    name = str(document.get("name") or "unnamed")
    policy = _policy_from_overrides(document.get("policy"))
    state = RiskState.from_dict(document.get("state"))
    engine = RiskEngine(policy)
    snapshots = document.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise ValueError(f"Scenario {name!r} must contain a non-empty snapshots list.")
    expected = list(document.get("expected_statuses") or [])
    base_now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    rows: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    for index, raw in enumerate(snapshots):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Scenario {name!r} snapshot {index} must be an object.")
        snap = _snapshot_from_mapping(raw, default_now=base_now)
        assessment = engine.evaluate(snap, state)
        state = assessment.state
        actual_status = assessment.decision.status
        expected_status = expected[index] if index < len(expected) else None
        passed = expected_status in (None, actual_status)
        rows.append(
            {
                "scenario": name,
                "step": index + 1,
                "expected_status": expected_status or "",
                "actual_status": actual_status,
                "passed": passed,
                "requested_target_lots": assessment.decision.requested_target_lots,
                "approved_target_lots": assessment.decision.approved_target_lots,
                "current_lots": assessment.decision.current_lots,
                "requested_action": assessment.decision.requested_action,
                "approved_action": assessment.decision.approved_action,
                "breaches": ";".join(assessment.decision.breaches),
                "reasons": ";".join(assessment.decision.reasons),
                "policy_hash": policy.policy_hash,
            }
        )
        assessments.append(assessment.to_dict())
    return {
        "name": name,
        "policy": asdict(policy),
        "policy_hash": policy.policy_hash,
        "rows": rows,
        "assessments": assessments,
        "passed": all(bool(row["passed"]) for row in rows),
    }


def run_documents(documents: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    results = [run_scenario(document) for document in documents]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(result["passed"] for result in results),
        "results": results,
    }


def write_outputs(report: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "risk_alpha_report.json"
    csv_path = output_dir / "risk_alpha_summary.csv"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    rows = [
        row
        for result in report.get("results", [])
        for row in result.get("rows", [])
    ]
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    return json_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline synthetic test harness for MOEX Research Robot "
            "v3.9.0 Risk Engine. It never connects to the broker."
        )
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        help="JSON file containing one scenario or a list of scenarios.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for JSON/CSV reports.",
    )
    return parser.parse_args()


def _load_scenario_file(path: Path) -> list[Mapping[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, list):
        return document
    if isinstance(document, dict) and isinstance(document.get("scenarios"), list):
        return document["scenarios"]
    if isinstance(document, dict):
        return [document]
    raise ValueError("Scenario JSON root must be an object or list.")


def main() -> int:
    args = parse_args()
    documents = (
        _load_scenario_file(args.scenario)
        if args.scenario
        else built_in_scenarios()
    )
    report = run_documents(documents)
    json_path, csv_path = write_outputs(report, args.output_dir)
    print(f"Risk alpha scenarios: {'PASS' if report['passed'] else 'FAIL'}")
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    for result in report["results"]:
        statuses = ", ".join(row["actual_status"] for row in result["rows"])
        print(f"- {result['name']}: {'PASS' if result['passed'] else 'FAIL'} [{statuses}]")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
