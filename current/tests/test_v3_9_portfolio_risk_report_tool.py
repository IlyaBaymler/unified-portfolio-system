from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_portfolio_risk_adapter_v3_9 import ACCOUNT, portfolio

from tools import v3_9_portfolio_risk_report as report_tool
from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import PortfolioRiskReadServiceError
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.state_persistence import atomic_write_json


def prepare_runtime(root) -> None:
    PortfolioRepository(root / "portfolio_state.json").save(portfolio())
    CentralOrderStore(root / "central_order_state.json").initialize(ACCOUNT)
    RiskProfileStore(root / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope=ACCOUNT,
    )
    RiskStateStore(root / "risk_state.json").save_account(
        ACCOUNT,
        RiskState(
            daily_start_equity_rub=100_000.0,
            weekly_start_equity_rub=100_000.0,
            high_watermark_equity_rub=100_000.0,
        ),
    )


def test_tool_reads_checksummed_runtime_without_mutating_files(tmp_path) -> None:
    prepare_runtime(tmp_path)
    metadata_path = tmp_path / "portfolio_risk_metadata.json"
    atomic_write_json(
        metadata_path,
        {
            "version": 1,
            "instruments": [
                {
                    "instrument_id": "uid-sber",
                    "lot_size": 10,
                    "asset_class": "stock",
                    "currency": "rub",
                }
            ],
        },
        write_checksum=True,
    )
    tracked = (
        "portfolio_state.json",
        "portfolio_state.json.sha256",
        "central_order_state.json",
        "central_order_state.json.sha256",
        "risk_profiles.json",
        "risk_state.json",
    )
    before = {name: (tmp_path / name).read_bytes() for name in tracked}

    payload = report_tool.run(
        report_tool.parse_args(
            [
                str(tmp_path),
                "--account-id",
                ACCOUNT,
                "--mode",
                "SANDBOX_EXECUTION",
                "--metadata",
                str(metadata_path),
            ]
        )
    )

    assert payload["status"] == "CONFIGURATION_REQUIRED"
    assert payload["metrics"]["gross_exposure_rub"] == 1_000.0
    assert payload["execution_authorized"] is False
    assert {name: (tmp_path / name).read_bytes() for name in tracked} == before


def test_tool_can_write_an_explicit_read_only_report(tmp_path) -> None:
    prepare_runtime(tmp_path)
    metadata_path = tmp_path / "metadata.json"
    atomic_write_json(
        metadata_path,
        {
            "version": 1,
            "instruments": [
                {
                    "instrument_id": "uid-sber",
                    "lot_size": 10,
                    "asset_class": "stock",
                    "currency": "rub",
                }
            ],
        },
        write_checksum=True,
    )
    output = tmp_path / "report.json"

    result = report_tool.main(
        [
            str(tmp_path),
            "--account-id",
            ACCOUNT,
            "--mode",
            "SANDBOX_EXECUTION",
            "--metadata",
            str(metadata_path),
            "--output",
            str(output),
        ]
    )

    assert result == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["generated_at"]
    assert saved["account_id"] == ACCOUNT
    assert saved["execution_authorized"] is False


def test_tool_direct_script_entrypoint_resolves_project_package(tmp_path) -> None:
    prepare_runtime(tmp_path)
    script = Path(__file__).parents[1] / "tools" / "v3_9_portfolio_risk_report.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path),
            "--account-id",
            ACCOUNT,
            "--mode",
            "SANDBOX_EXECUTION",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout)["execution_authorized"] is False


def test_tool_rejects_metadata_without_checksum(tmp_path) -> None:
    prepare_runtime(tmp_path)
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps({"version": 1, "instruments": []}),
        encoding="utf-8",
    )

    with pytest.raises(PortfolioRiskReadServiceError, match="checksum is missing"):
        report_tool.run(
            report_tool.parse_args(
                [str(tmp_path), "--account-id", ACCOUNT, "--metadata", str(metadata_path)]
            )
        )


def test_tool_rejects_risk_profile_account_scope_mismatch(tmp_path) -> None:
    prepare_runtime(tmp_path)
    RiskProfileStore(tmp_path / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope="another-account",
    )

    with pytest.raises(
        PortfolioRiskReadServiceError,
        match="account scope mismatch",
    ):
        report_tool.run(
            report_tool.parse_args(
                [
                    str(tmp_path),
                    "--account-id",
                    ACCOUNT,
                    "--mode",
                    "SANDBOX_EXECUTION",
                ]
            )
        )


def test_tool_rejects_missing_central_reservation_store(tmp_path) -> None:
    prepare_runtime(tmp_path)
    (tmp_path / "central_order_state.json").unlink()
    (tmp_path / "central_order_state.json.sha256").unlink()

    with pytest.raises(
        PortfolioRiskReadServiceError,
        match="reservation projection cannot be trusted",
    ):
        report_tool.run(
            report_tool.parse_args(
                [str(tmp_path), "--account-id", ACCOUNT, "--mode", "SANDBOX_EXECUTION"]
            )
        )
