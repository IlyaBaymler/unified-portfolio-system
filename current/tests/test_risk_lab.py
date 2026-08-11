from __future__ import annotations

import json
from pathlib import Path

from risk_lab import (
    built_in_scenarios,
    run_documents,
    run_scenario,
    write_outputs,
)


def test_built_in_scenarios_pass():
    report = run_documents(built_in_scenarios())
    assert report["passed"] is True
    assert len(report["results"]) >= 8
    statuses = {
        row["actual_status"]
        for result in report["results"]
        for row in result["rows"]
    }
    assert {"PASS", "ADJUSTED", "BLOCKED", "REDUCTION_ALLOWED"}.issubset(
        statuses
    )


def test_custom_scenario_aliases():
    result = run_scenario(
        {
            "name": "aliases",
            "policy": {
                "max_position_lots": 1,
                "max_position_value_rub": 100000,
                "max_position_share_of_equity": 1.0,
                "max_order_value_rub": 100000,
                "cash_reserve_rub": 0,
                "risk_per_trade_rub": None,
                "daily_loss_limit_rub": None,
                "daily_loss_limit_fraction": None,
                "weekly_loss_limit_rub": None,
                "max_drawdown_fraction": None,
                "max_daily_turnover_rub": None,
                "max_orders_per_day": None,
            },
            "snapshots": [
                {
                    "target_lots": 2,
                    "current": 0,
                    "price": 250,
                    "lot_size": 10,
                    "equity": 50000,
                    "cash": 50000,
                    "atr": 5,
                    "reconciled": True,
                    "pending": False,
                    "mode": "DRY_RUN",
                }
            ],
            "expected_statuses": ["ADJUSTED"],
        }
    )
    assert result["passed"] is True
    assert result["rows"][0]["approved_target_lots"] == 1


def test_write_outputs(tmp_path: Path):
    report = run_documents(built_in_scenarios()[:2])
    json_path, csv_path = write_outputs(report, tmp_path)
    assert json_path.exists()
    assert csv_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["passed"] is True
    assert "scenario" in csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
