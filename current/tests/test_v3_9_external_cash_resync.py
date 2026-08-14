from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import v3_9_external_cash_resync as tool
from trading_robot.central_order_manager import (
    CentralOrderCandidate,
    CentralOrderManager,
    CentralOrderStore,
    ExecutionAuthorization,
)
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    PortfolioState,
    SnapshotFreshness,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_recovery import (
    ExternalCashResyncError,
    ExternalCashResyncService,
)
from trading_robot.risk import RiskEngine, RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore

ACCOUNT = "sandbox-account-12345678"
DETECTED_AT = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
SNAPSHOT_AT = DETECTED_AT + timedelta(minutes=1)
PREPARE_AT = SNAPSHOT_AT + timedelta(seconds=1)
APPLY_AT = SNAPSHOT_AT + timedelta(seconds=2)


def canonical_state(
    *,
    account_id: str = ACCOUNT,
    revision: int = 7,
    snapshot_at: datetime = SNAPSHOT_AT,
    equity: float = 105_000.0,
    cash: float = 55_000.0,
) -> PortfolioState:
    return PortfolioState(
        version=2,
        account=AccountState(
            account_id=account_id,
            total_value=equity,
            securities_value=50_000.0,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", cash),),
        ),
        snapshot_at=snapshot_at.isoformat(),
        generated_at=snapshot_at.isoformat(),
        freshness=SnapshotFreshness.FRESH,
        source="PORTFOLIO_MANAGER",
        positions=(),
        warnings=(),
        state_status="READY",
        blocking=False,
        revision=revision,
    )


def runtime(tmp_path: Path) -> tuple[Path, ExternalCashResyncService]:
    root = tmp_path / "runtime"
    root.mkdir()
    PortfolioRepository(root / "portfolio_state.json").save(canonical_state())
    RiskProfileStore(root / "risk_profiles.json").confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            enabled=True,
            portfolio_policy_configured=True,
            portfolio_policy_mode="ENFORCED",
            max_gross_exposure_rub=500_000.0,
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
        source="M5_2_TEST",
    )
    state = RiskState(
        daily_date="2026-08-14",
        weekly_key="2026-W33",
        daily_start_equity_rub=100_000.0,
        weekly_start_equity_rub=100_000.0,
        high_watermark_equity_rub=101_000.0,
        daily_turnover_rub=12_500.0,
        daily_order_count=3,
        last_equity_rub=100_000.0,
        last_cash_rub=50_000.0,
        last_snapshot_at=(DETECTED_AT - timedelta(minutes=1)).isoformat(),
        recorded_execution_ids=("known-fill",),
    )
    detected, _event = RiskEngine(RiskPolicy()).mark_external_cash_change(
        state,
        now=DETECTED_AT,
        cash_rub=55_000.0,
        equity_rub=105_000.0,
        snapshot_at=DETECTED_AT,
    )
    RiskStateStore(root / "risk_state.json").save_account(ACCOUNT, detected)
    CentralOrderStore(root / "central_order_state.json").initialize(ACCOUNT)
    return root, ExternalCashResyncService.from_directory(
        root,
        account_id=ACCOUNT,
    )


def primary_bytes(root: Path) -> dict[str, bytes]:
    names = (
        "portfolio_state.json",
        "portfolio_state.json.sha256",
        "risk_profiles.json",
        "risk_state.json",
        "central_order_state.json",
        "central_order_state.json.sha256",
    )
    return {name: (root / name).read_bytes() for name in names}


def material_runtime_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file() and not path.name.endswith(".lock")
    }


def parsed_args(root: Path, action: str, *extra: str):
    return tool.parse_args(
        [
            action,
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            *extra,
        ]
    )


def test_prepare_is_read_only_and_apply_preserves_accounting(tmp_path: Path) -> None:
    root, service = runtime(tmp_path)
    before = primary_bytes(root)
    material_before = material_runtime_bytes(root)

    prepared = tool.run(parsed_args(root, "prepare"), now=PREPARE_AT)
    proof = service.prepare(now=PREPARE_AT)

    assert prepared["status"] == "PREPARED"
    assert prepared["proof"]["proof_sha256"] == proof.proof_sha256
    assert prepared["required_confirmation"] == proof.confirmation
    assert prepared["writes_performed"] is False
    assert primary_bytes(root) == before
    assert ACCOUNT not in json.dumps(prepared, ensure_ascii=False)

    applied = tool.run(
        parsed_args(
            root,
            "apply",
            "--proof-sha256",
            proof.proof_sha256,
            "--confirm",
            proof.confirmation,
        ),
        now=APPLY_AT,
    )
    state = RiskStateStore(root / "risk_state.json").load_account(ACCOUNT)

    assert applied["status"] == "APPLIED"
    assert applied["writes_performed"] is True
    assert applied["central_intent_created"] is False
    assert applied["dispatch_authorized"] is False
    assert applied["resubmit_authorized"] is False
    assert applied["provider_post_authorized"] is False
    assert ACCOUNT not in json.dumps(applied, ensure_ascii=False)
    assert state.risk_resync_required is False
    assert state.daily_start_equity_rub == 105_000.0
    assert state.weekly_start_equity_rub == 105_000.0
    assert state.high_watermark_equity_rub == 105_000.0
    assert state.last_cash_rub == 55_000.0
    assert state.daily_turnover_rub == 12_500.0
    assert state.daily_order_count == 3
    assert state.recorded_execution_ids == ("known-fill",)
    after = primary_bytes(root)
    material_after = material_runtime_bytes(root)
    assert after["portfolio_state.json"] == before["portfolio_state.json"]
    assert after["portfolio_state.json.sha256"] == before[
        "portfolio_state.json.sha256"
    ]
    assert after["risk_profiles.json"] == before["risk_profiles.json"]
    assert after["central_order_state.json"] == before["central_order_state.json"]
    assert after["central_order_state.json.sha256"] == before[
        "central_order_state.json.sha256"
    ]
    changed_material = {
        name
        for name in material_before.keys() | material_after.keys()
        if material_before.get(name) != material_after.get(name)
    }
    assert changed_material == {"risk_state.json", "risk_state.json.bak"}
    assert json.loads(material_after["risk_state.json.bak"]) == json.loads(
        material_before["risk_state.json"]
    )


def test_one_kopeck_external_cash_resync_round_trip(tmp_path: Path) -> None:
    root, _service = runtime(tmp_path)
    repository = PortfolioRepository(root / "portfolio_state.json")
    repository.save(
        canonical_state(
            revision=8,
            equity=100_000.01,
            cash=50_000.01,
        ),
        expected_revision=7,
        allow_equal_revision=False,
    )
    base = RiskState(
        daily_date="2026-08-14",
        weekly_key="2026-W33",
        daily_start_equity_rub=100_000.0,
        weekly_start_equity_rub=100_000.0,
        high_watermark_equity_rub=100_000.0,
        last_equity_rub=100_000.0,
        last_cash_rub=50_000.0,
    )
    detected, _event = RiskEngine(RiskPolicy()).mark_external_cash_change(
        base,
        now=DETECTED_AT,
        cash_rub=50_000.01,
        equity_rub=100_000.01,
        snapshot_at=DETECTED_AT,
    )
    RiskStateStore(root / "risk_state.json").save_account(ACCOUNT, detected)
    service = ExternalCashResyncService.from_directory(root, account_id=ACCOUNT)

    proof = service.prepare(now=PREPARE_AT)
    result = service.apply(
        expected_proof_sha256=proof.proof_sha256,
        confirmation=proof.confirmation,
        now=APPLY_AT,
    )

    assert proof.cash_delta_rub == 0.01
    assert result.state.risk_resync_required is False
    assert result.state.last_cash_rub == 50_000.01


def test_stale_proof_rejects_without_risk_state_write(tmp_path: Path) -> None:
    root, service = runtime(tmp_path)
    proof = service.prepare(now=PREPARE_AT)
    state_path = root / "risk_state.json"
    before = state_path.read_bytes()

    with pytest.raises(ExternalCashResyncError, match="from the future"):
        service.apply(
            expected_proof_sha256=proof.proof_sha256,
            confirmation=proof.confirmation,
            now=DETECTED_AT,
        )
    assert state_path.read_bytes() == before

    repository = PortfolioRepository(root / "portfolio_state.json")
    repository.save(
        canonical_state(
            revision=8,
            snapshot_at=SNAPSHOT_AT + timedelta(minutes=1),
            equity=106_000.0,
            cash=56_000.0,
        ),
        expected_revision=7,
        allow_equal_revision=False,
    )

    with pytest.raises(ExternalCashResyncError, match="proof changed"):
        service.apply(
            expected_proof_sha256=proof.proof_sha256,
            confirmation=proof.confirmation,
            now=SNAPSHOT_AT + timedelta(minutes=1, seconds=1),
        )

    assert state_path.read_bytes() == before
    assert RiskStateStore(state_path).load_account(ACCOUNT).risk_resync_required


def test_active_central_reservation_blocks_prepare(tmp_path: Path) -> None:
    root, service = runtime(tmp_path)
    manager = CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    portfolio = PortfolioRepository(root / "portfolio_state.json").load()
    candidate = CentralOrderCandidate(
        account_id=ACCOUNT,
        instrument_id="uid-sber",
        ticker="SBER",
        runtime_key="runtime-sber",
        runtime_config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=SNAPSHOT_AT.isoformat(),
        strategy_id="sma",
        strategy_profile_hash="b" * 64,
        current_lots=0,
        target_lots=1,
        estimated_price_kopecks=25_000,
        lot_size=10,
        created_at=SNAPSHOT_AT.isoformat(),
    )
    manager.enqueue(
        candidate,
        ExecutionAuthorization(
            account_id=ACCOUNT,
            instrument_id=candidate.instrument_id,
            authorized_target_lots=1,
            portfolio_revision=portfolio.revision,
            portfolio_decision_checksum=portfolio.decision_sha256,
            portfolio_document_checksum="c" * 64,
            available_cash_kopecks=5_500_000,
            preflight_status="PASS",
            pending_order_ids=(),
            uncertain_order_ids=(),
            risk_status="PASS",
            risk_decision_id="risk-decision",
            risk_policy_hash="d" * 64,
            risk_order_allowed=True,
            authorized_at=SNAPSHOT_AT.isoformat(),
            risk_state_guard_hash="e" * 64,
        ),
    )

    with pytest.raises(ExternalCashResyncError, match="reservations"):
        service.prepare(now=PREPARE_AT)


def test_position_resync_source_cannot_be_cleared_as_external_cash(
    tmp_path: Path,
) -> None:
    root, service = runtime(tmp_path)
    store = RiskStateStore(root / "risk_state.json")
    marked, _event = RiskEngine(RiskPolicy()).mark_external_activity(
        RiskState(
            daily_date="2026-08-14",
            weekly_key="2026-W33",
            daily_start_equity_rub=100_000.0,
            weekly_start_equity_rub=100_000.0,
            high_watermark_equity_rub=100_000.0,
            last_equity_rub=100_000.0,
            last_cash_rub=50_000.0,
        ),
        now=DETECTED_AT,
        reason="manual position drift",
        source="BROKER_POSITION_DRIFT",
    )
    store.save_account(ACCOUNT, marked)

    with pytest.raises(ExternalCashResyncError, match="not EXTERNAL_CASH_CHANGE"):
        service.prepare(now=PREPARE_AT)

    assert store.load_account(ACCOUNT).risk_resync_required is True


def test_error_path_redacts_expected_and_stored_account_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "mismatched-runtime"
    root.mkdir()
    other_account = "stored-secret-account-87654321"
    PortfolioRepository(root / "portfolio_state.json").save(
        canonical_state(account_id=other_account)
    )

    exit_code = tool.main(
        [
            "prepare",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)

    assert exit_code == 1
    assert captured.out == ""
    assert ACCOUNT not in captured.err
    assert other_account not in captured.err
    assert "Traceback" not in captured.err
    assert payload["status"] == "ERROR"
    assert payload["writes_performed"] is False
    assert payload["provider_post_authorized"] is False


def test_apply_output_error_reports_completed_state_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, service = runtime(tmp_path)
    proof = service.prepare(now=PREPARE_AT)
    original_run = tool.run

    def run_at_apply_time(args):
        return original_run(args, now=APPLY_AT)

    monkeypatch.setattr(tool, "run", run_at_apply_time)
    exit_code = tool.main(
        [
            "apply",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            "--proof-sha256",
            proof.proof_sha256,
            "--confirm",
            proof.confirmation,
            "--output",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    state = RiskStateStore(root / "risk_state.json").load_account(ACCOUNT)

    assert exit_code == 1
    assert captured.out == ""
    assert payload["status"] == "APPLIED_OUTPUT_ERROR"
    assert payload["operation_status"] == "APPLIED"
    assert payload["writes_performed"] is True
    assert payload["provider_post_authorized"] is False
    assert ACCOUNT not in captured.err
    assert state.risk_resync_required is False
    assert state.last_cash_rub == 55_000.0


@pytest.mark.parametrize(
    ("action", "output_name", "extra"),
    [
        ("prepare", "operator-proof.json", ()),
        (
            "apply",
            "risk_state.json",
            (
                "--proof-sha256",
                "0" * 64,
                "--confirm",
                "APPLY V3.9 EXTERNAL CASH RESYNC " + "0" * 64,
            ),
        ),
    ],
)
def test_cli_rejects_output_inside_runtime_before_operation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    output_name: str,
    extra: tuple[str, ...],
) -> None:
    root, _service = runtime(tmp_path)
    before = material_runtime_bytes(root)

    def operation_must_not_run(_args):
        raise AssertionError("runtime operation started before output validation")

    monkeypatch.setattr(tool, "run", operation_must_not_run)
    exit_code = tool.main(
        [
            action,
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            "--output",
            str(root / output_name),
            *extra,
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)

    assert exit_code == 1
    assert captured.out == ""
    assert payload["status"] == "ERROR"
    assert payload["writes_performed"] is False
    assert "outside --runtime-dir" in payload["error"]["message"]
    assert material_runtime_bytes(root) == before


def test_prepare_cli_writes_report_outside_runtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _service = runtime(tmp_path)
    before = material_runtime_bytes(root)
    output = tmp_path / "external_cash_resync_proof.json"
    original_run = tool.run

    def run_at_prepare_time(args):
        return original_run(args, now=PREPARE_AT)

    monkeypatch.setattr(tool, "run", run_at_prepare_time)
    exit_code = tool.main(
        [
            "prepare",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
    assert payload["status"] == "PREPARED"
    assert payload["writes_performed"] is False
    assert material_runtime_bytes(root) == before


def test_prepare_and_apply_reject_stale_canonical_snapshot_without_write(
    tmp_path: Path,
) -> None:
    root, service = runtime(tmp_path)
    before = primary_bytes(root)

    with pytest.raises(ExternalCashResyncError, match="snapshot is stale"):
        service.prepare(now=SNAPSHOT_AT + timedelta(seconds=121))
    assert primary_bytes(root) == before

    proof = service.prepare(now=PREPARE_AT)
    with pytest.raises(ExternalCashResyncError, match="snapshot is stale"):
        service.apply(
            expected_proof_sha256=proof.proof_sha256,
            confirmation=proof.confirmation,
            now=SNAPSHOT_AT + timedelta(seconds=121),
        )
    assert primary_bytes(root) == before


def test_prepare_requires_policy_snapshot_age_guard(tmp_path: Path) -> None:
    root, service = runtime(tmp_path)
    RiskProfileStore(root / "risk_profiles.json").confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            enabled=True,
            block_on_stale_snapshot=False,
            max_snapshot_age_seconds=None,
            portfolio_policy_configured=True,
            portfolio_policy_mode="ENFORCED",
            max_gross_exposure_rub=500_000.0,
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
        source="M5_2_TEST",
    )
    before = primary_bytes(root)

    with pytest.raises(ExternalCashResyncError, match="finite canonical"):
        service.prepare(now=PREPARE_AT)

    assert primary_bytes(root) == before


def test_wrong_confirmation_rejects_without_risk_state_write(tmp_path: Path) -> None:
    root, service = runtime(tmp_path)
    proof = service.prepare(now=PREPARE_AT)
    before = primary_bytes(root)

    with pytest.raises(ExternalCashResyncError, match="Confirmation must be"):
        service.apply(
            expected_proof_sha256=proof.proof_sha256,
            confirmation="APPLY V3.9 EXTERNAL CASH RESYNC wrong",
            now=APPLY_AT,
        )

    assert primary_bytes(root) == before
