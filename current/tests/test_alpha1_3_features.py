from __future__ import annotations

from pathlib import Path

from trading_robot.diagnostic_feedback import build_diagnostic_feedback
from trading_robot.export_naming import ensure_export_directory


ROOT = Path(__file__).resolve().parents[1]


def _processed_result(*, reconciliation_status: str = "MATCHED") -> dict:
    return {
        "status": "processed",
        "ticker": "SBER",
        "direction": "SELL",
        "executed_lots": 1,
        "order_id": "order-123",
        "actual_lots_after": 0,
        "position_reconciled": True,
        "risk_execution_status": "RECORDED",
        "canonical_portfolio_snapshot": {
            "blocking": reconciliation_status != "MATCHED",
            "state_status": "READY" if reconciliation_status == "MATCHED" else "BLOCKED",
            "positions": [
                {
                    "ticker": "SBER",
                    "reconciliation_status": reconciliation_status,
                }
            ],
        },
    }


def test_diagnostic_sell_feedback_is_explicit_and_successful():
    feedback = build_diagnostic_feedback(
        _processed_result(), direction="SELL", ticker="SBER"
    )
    assert feedback.success is True
    assert feedback.requires_attention is False
    assert "SELL SBER" in feedback.message
    assert "Исполнено лотов: 1" in feedback.message
    assert "order-123" in feedback.message
    assert "Фактическая позиция после операции: 0" in feedback.message
    assert "Risk accounting: RECORDED" in feedback.message


def test_diagnostic_sell_feedback_warns_about_target_mismatch():
    feedback = build_diagnostic_feedback(
        _processed_result(reconciliation_status="TARGET_MISMATCH"),
        direction="SELL",
        ticker="SBER",
    )
    assert feedback.success is True
    assert feedback.requires_attention is True
    assert feedback.reconciliation_status == "TARGET_MISMATCH"
    assert "Strategy target/ownership" in feedback.message
    assert "новые входы блокируются" in feedback.message


def test_incomplete_diagnostic_result_is_not_reported_as_success():
    feedback = build_diagnostic_feedback(
        {
            "status": "submission_failed",
            "executed_lots": 0,
            "position_reconciled": True,
        },
        direction="BUY",
        ticker="SBER",
    )
    assert feedback.success is False
    assert feedback.requires_attention is True
    assert "не завершена" in feedback.title


def test_ensure_export_directory_creates_current_runtime_reports(tmp_path: Path):
    reports = tmp_path / "Текущая версия" / "reports"
    result = ensure_export_directory(reports)
    assert result == reports
    assert reports.is_dir()


def test_gui_uses_current_reports_dir_and_explicit_diagnostic_feedback():
    source = (ROOT / "desktop_gui.py").read_text(encoding="utf-8")
    assert "def _reports_initial_dir" in source
    assert source.count("initialdir=_reports_initial_dir()") >= 7
    assert "build_diagnostic_feedback" in source
    assert 'result["canonical_portfolio_snapshot"]' in source
    assert "self._display_portfolio_snapshot(snapshot)" in source
    assert "messagebox.showwarning" in source
    assert "messagebox.showinfo" in source
