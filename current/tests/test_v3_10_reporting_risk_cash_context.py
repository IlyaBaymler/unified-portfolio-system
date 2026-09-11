from __future__ import annotations

import ast
import dataclasses
import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

import pytest

from trading_robot import broker_read_adapters as broker
from trading_robot import cash_availability as cl5
from trading_robot import cash_ledger_domain as ledger
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import central_order_manager as central
from trading_robot import portfolio_model, portfolio_preflight, risk
from trading_robot import reporting_risk_cash_context as cl6

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
MODULE_PATH = CURRENT / "trading_robot" / "reporting_risk_cash_context.py"
FIXTURE_PATH = (
    CURRENT / "tests" / "fixtures" / "v3_10_reporting_risk_cash_context_vectors.json"
)
ACCEPTED_CONTRACT_HEAD = "ef34c8018d18268de0982da484fd873cd2159894"
STABLE_PREDECESSOR = "864963dd69cb4c907cbc762b66ed12f012f32741"
IMPLEMENTATION_PATHS = {
    "current/trading_robot/reporting_risk_cash_context.py",
    "current/tests/test_v3_10_reporting_risk_cash_context.py",
    "current/tests/fixtures/v3_10_reporting_risk_cash_context_vectors.json",
}
KEY = bytes(range(32))
KEY_ID = "CL5_TEST_KEY_V1"
RAW_ACCOUNT = "sandbox-account-0001"
ACCOUNT_SCOPE = "15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3"
START = "2026-01-01T00:00:00.000000000Z"
FLOW = "2026-07-02T00:00:00.000000000Z"
END = "2027-01-01T00:00:00.000000000Z"
END_ISO = "2027-01-01T00:00:00+00:00"
END_PLUS_10 = "2027-01-01T00:00:10.000000000Z"
END_PLUS_121 = "2027-01-01T00:02:01.000000000Z"
ENV = broker.BrokerEnvironment.SANDBOX


@pytest.fixture(scope="module")
def vectors() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="ascii"))


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _scope(raw_account: str = RAW_ACCOUNT, key: bytes = KEY) -> str:
    return hmac.new(
        key,
        _canonical(
            {
                "account_id": raw_account,
                "domain": "v3.10-cl3-account-scope",
                "environment": "SANDBOX",
                "identity_key_id": KEY_ID,
                "provider": "TBANK",
                "version": 1,
            }
        ),
        hashlib.sha256,
    ).hexdigest()


def _reason(
    expected: cl6.CL6Reason,
    callable_: object,
    *args: object,
    **kwargs: object,
) -> cl6.CL6Error:
    with pytest.raises(cl6.CL6Error) as captured:
        callable_(*args, **kwargs)
    error = captured.value
    assert error.reason is expected
    assert str(error) == expected.value
    assert error.__cause__ is None
    return error


def _money(units: int) -> ledger.Money:
    return ledger.Money(currency="RUB", minor_units=units * 1_000_000_000)


def _point(
    amount: int,
    as_of: str,
    phase: cl6.ValuationPhase,
    revision: int,
    decision: str,
    document: str,
    source: str,
    *,
    account_scope: str = ACCOUNT_SCOPE,
) -> cl6.PortfolioValuationPoint:
    return cl6.build_portfolio_valuation_point(
        _money(amount),
        account_scope_sha256=account_scope,
        environment=ENV,
        as_of=as_of,
        phase=phase,
        portfolio_revision=revision,
        portfolio_decision_checksum=decision,
        portfolio_document_checksum=document,
        source_sha256=source,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _points() -> tuple[cl6.PortfolioValuationPoint, ...]:
    return (
        _point(
            100,
            START,
            cl6.ValuationPhase.PERIOD_START,
            1,
            "a" * 64,
            "ab" * 32,
            "ac" * 32,
        ),
        _point(
            110,
            FLOW,
            cl6.ValuationPhase.PRE_EXTERNAL_FLOW,
            2,
            "ba" * 32,
            "b" * 64,
            "bc" * 32,
        ),
        _point(
            180, END, cl6.ValuationPhase.PERIOD_END, 3, "ca" * 32, "cb" * 32, "c" * 64
        ),
    )


def _export(vectors: dict[str, object]) -> bytes:
    return vectors["report_end_to_end"]["ledger_export_ascii"].encode("ascii")


def _report(
    vectors: dict[str, object],
    points: tuple[cl6.PortfolioValuationPoint, ...] | None = None,
) -> cl6.PerformanceReport:
    return cl6.build_performance_report(
        _export(vectors),
        _points() if points is None else points,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        period_start=START,
        period_end=END,
        generated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _portfolio_evidence(
    *,
    snapshot_at: str = END_ISO,
    leased_at: str = END_ISO,
    freshness: portfolio_model.SnapshotFreshness = portfolio_model.SnapshotFreshness.FRESH,
    state_status: str = "READY",
    blocking: bool = False,
) -> cl6.PortfolioIdentityEvidence:
    state = portfolio_model.PortfolioState(
        version=portfolio_model.PORTFOLIO_STATE_SCHEMA_VERSION,
        account=portfolio_model.AccountState(
            account_id=RAW_ACCOUNT,
            total_value=None,
            securities_value=None,
            expected_yield=None,
            cash_balances=(),
        ),
        snapshot_at=snapshot_at,
        generated_at=snapshot_at,
        freshness=freshness,
        source="TBANK",
        positions=(),
        warnings=(),
        state_status=state_status,
        blocking=blocking,
        revision=9,
        portfolio_source="CANONICAL",
        migration=portfolio_model.PortfolioMigrationMetadata.completed(),
    )
    lease = portfolio_preflight.PortfolioSnapshotLease.from_state(
        state, leased_at=leased_at
    )
    return cl6.build_portfolio_identity_evidence(
        lease,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        evaluated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _risk_evidence(*, captured_at: str = END) -> cl6.RiskGuardEvidence:
    return cl6.build_risk_guard_evidence(
        risk.RiskPolicy(),
        risk.RiskState(),
        raw_account_id=RAW_ACCOUNT,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        captured_at=captured_at,
        evaluated_at=END_PLUS_121,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _cash_inputs(
    vectors: dict[str, object],
) -> tuple[
    cl4.CashReconciliation,
    cl5.BrokerPositionsCashProof,
    cl5.CentralReservationProjection,
    cl5.CashAvailabilitySnapshot,
]:
    exported = _export(vectors)
    cash_proof = cl4.build_broker_cash_proof(
        {"totalAmountCurrencies": {"currency": "RUB", "nano": 0, "units": "150"}},
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        as_of=END,
        evaluated_at=END,
        response_complete=True,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    reconciliation = cl4.reconcile_shadow_cash(
        exported,
        cash_proof,
        evaluated_at=END,
        identity_key=KEY,
    )
    positions = cl5.build_broker_positions_cash_proof(
        {
            "accountId": RAW_ACCOUNT,
            "blocked": [{"currency": "RUB", "nano": 0, "units": "20"}],
            "futures": [],
            "limitsLoadingInProgress": False,
            "money": [{"currency": "RUB", "nano": 0, "units": "130"}],
            "options": [],
            "securities": [],
        },
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        as_of=END,
        evaluated_at=END,
        response_complete=True,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    state = central.CentralOrderState(
        account_id=RAW_ACCOUNT,
        revision=0,
        next_sequence=1,
        intents=(),
        created_at=END_ISO,
        updated_at=END_ISO,
    )
    reservations = cl5.project_central_reservations(
        state,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        evaluated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    availability = cl5.build_cash_availability(
        exported,
        reconciliation,
        positions,
        reservations,
        evaluated_at=END,
        identity_key=KEY,
    )
    return reconciliation, positions, reservations, availability


def _context(
    vectors: dict[str, object],
    *,
    evaluated_at: str = END,
    portfolio: cl6.PortfolioIdentityEvidence | None = None,
    risk_guard: cl6.RiskGuardEvidence | None = None,
) -> cl6.PortfolioRiskCashContext:
    reconciliation, positions, reservations, availability = _cash_inputs(vectors)
    return cl6.build_portfolio_risk_cash_context(
        _export(vectors),
        reconciliation,
        positions,
        reservations,
        availability,
        _portfolio_evidence() if portfolio is None else portfolio,
        _risk_evidence() if risk_guard is None else risk_guard,
        evaluated_at=evaluated_at,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _portfolio_lease_with_pending() -> tuple[
    portfolio_preflight.PortfolioSnapshotLease,
    portfolio_model.PendingOrderState,
]:
    instrument_id = "BBG000000001"
    order = portfolio_model.PendingOrderState(
        order_request_id="request-1",
        instrument_id=instrument_id,
        direction="BUY",
        requested_lots=2,
        executed_lots=1,
        status=portfolio_model.PendingOrderStatus.PARTIALLY_FILLED,
        source="LOCAL",
    )
    reconciliation = portfolio_model.ReconciliationResult(
        instrument_id=instrument_id,
        status=portfolio_model.ReconciliationStatus.PENDING_ORDER,
        blocking=True,
        reasons=("PENDING_ORDER",),
        actual_lots=0,
        target_lots=2,
        pending_order_ids=(order.order_request_id,),
        checked_at=END_ISO,
    )
    position = portfolio_model.PositionState(
        instrument_id=instrument_id,
        figi=instrument_id,
        ticker="TEST",
        class_code="TQBR",
        asset_type="share",
        currency="rub",
        quantity=0.0,
        actual_lots=0,
        average_price=None,
        current_price=None,
        market_value=None,
        expected_yield=None,
        target=None,
        ownership=None,
        ownership_status=portfolio_model.OwnershipStatus.FLAT,
        pending_orders=(order,),
        reconciliation=reconciliation,
        origin=portfolio_model.PositionOrigin.STRATEGY,
    )
    state = portfolio_model.PortfolioState(
        version=portfolio_model.PORTFOLIO_STATE_SCHEMA_VERSION,
        account=portfolio_model.AccountState(RAW_ACCOUNT, None, None, None, ()),
        snapshot_at=END_ISO,
        generated_at=END_ISO,
        freshness=portfolio_model.SnapshotFreshness.FRESH,
        source="TBANK",
        positions=(position,),
        warnings=(),
        state_status="READY",
        blocking=True,
        revision=9,
    )
    return (
        portfolio_preflight.PortfolioSnapshotLease.from_state(
            state,
            leased_at=END_ISO,
        ),
        order,
    )


def test_v310_cl6_01_exact_contract_lineage_and_three_path_delta() -> None:
    safe = str(ROOT).replace("\\", "/")
    head = subprocess.run(
        ["git", "-c", f"safe.directory={safe}", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    merge_base = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={safe}",
            "merge-base",
            ACCEPTED_CONTRACT_HEAD,
            head,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert merge_base == ACCEPTED_CONTRACT_HEAD
    if head == ACCEPTED_CONTRACT_HEAD:
        status = subprocess.run(
            ["git", "-c", f"safe.directory={safe}", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        paths = {line[3:].replace("\\", "/") for line in status}
    else:
        paths = set(
            subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={safe}",
                    "diff",
                    "--name-only",
                    f"{ACCEPTED_CONTRACT_HEAD}..{head}",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        )
    assert paths == IMPLEMENTATION_PATHS
    assert (
        subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={safe}",
                "merge-base",
                STABLE_PREDECESSOR,
                head,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == STABLE_PREDECESSOR
    )


def test_v310_cl6_02_import_and_ast_authority_boundary() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_imports = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    forbidden = {
        "sqlite3",
        "requests",
        "httpx",
        "numpy",
        "pandas",
        "scipy",
        "tkinter",
        "trading_robot.portfolio_repository",
        "trading_robot.portfolio_risk_runtime",
        "trading_robot.sandbox_execution_adapter",
    }
    assert not (imported | from_imports) & forbidden
    assert not any(
        isinstance(node, (ast.Global, ast.Nonlocal)) for node in ast.walk(tree)
    )


def test_v310_cl6_03_fixture_is_canonical_and_contains_valid_export(
    vectors: dict[str, object],
) -> None:
    raw = FIXTURE_PATH.read_bytes()
    assert raw == (
        json.dumps(vectors, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")
    assert vectors["version"] == 1
    exported = _export(vectors)
    assert (
        hashlib.sha256(exported).hexdigest()
        == vectors["report_end_to_end"]["ledger_export_sha256"]
    )
    assert json.loads(exported)["domain"] == "v3.10-cash-ledger-export"


def test_v310_cl6_04_valuation_contract_kat() -> None:
    expected = (
        (
            "e682bf338569f70f14ef3deb3f49df71a6c2feb988fc5913c5bec42c051bcb76",
            "be422475178220a0b319a01930c3b2cd3d7addda7a0ee408cbda2c74e068d6fb",
        ),
        (
            "fca5ae307cfca3457c3a1e84754d3d8cc8ca08440975f7c1a3111d71dd92ed64",
            "9bf672552a16d03ada419a5f32373a0ab59d094f9d288dc6ba97cebd1be14d05",
        ),
        (
            "3c72886db0938de569b343cd9a2e87519fb3577ad918f8d779f9ed77e6addb6d",
            "7c1faae4f02e2c9f05350b1c5b2de1db01ea915a2581f78f09a90b2aa9ff1515",
        ),
    )
    assert (
        tuple((item.valuation_identity_sha256, item.sha256) for item in _points())
        == expected
    )


def test_v310_cl6_05_end_to_end_report_kat(vectors: dict[str, object]) -> None:
    report = _report(vectors)
    expected = vectors["report_end_to_end"]["expected"]
    assert (
        report.valuation_set_sha256
        == "086a40cbdeff0dc73b3374a3e03e704666ea3248fbfb82c12f59db478cb6940f"
    )
    assert (
        report.cash_flow_summary.sha256
        == "617f979548fe8a1d74cf9e52194e82c6e0b5e6e0ecb3567ec1ef0d576454236a"
    )
    assert (
        report.twr.sha256
        == "0cd7f7f361836486688cbed80a1cdfb925eaec0ca713a8cf8a66e5de694a03de"
    )
    assert (
        report.xirr.sha256
        == "65973bd57b96c7002363c5f4ba10ea582482c13ebeb647eab07d8bf883c4596c"
    )
    assert report.twr.rate_decimal == "0.237500000000"
    assert report.xirr.rate_decimal == "0.242497375454"
    assert report.report_identity_sha256 == expected["report_identity_sha256"]
    assert report.sha256 == expected["performance_report_sha256"]
    assert report.canonical_bytes.decode("ascii") == expected["canonical_ascii"]
    assert report.report_status is cl6.ReportStatus.COMPLETE


def test_v310_cl6_06_missing_valuation_is_degraded(vectors: dict[str, object]) -> None:
    start, _, end = _points()
    report = _report(vectors, (start, end))
    assert report.report_status is cl6.ReportStatus.DEGRADED
    assert report.twr.reason is cl6.MetricReason.VALUATION_MISSING
    assert report.xirr.reason is cl6.MetricReason.VALUATION_MISSING
    assert report.cash_flow_summary.transaction_count == 1


def test_v310_cl6_07_extra_or_duplicate_valuation_rejected(
    vectors: dict[str, object],
) -> None:
    points = _points()
    _reason(
        cl6.CL6Reason.VALUATION_SET_INVALID,
        _report,
        vectors,
        points + (points[0],),
    )
    extra = _point(
        120,
        "2026-08-01T00:00:00.000000000Z",
        cl6.ValuationPhase.PRE_EXTERNAL_FLOW,
        4,
        "d" * 64,
        "e" * 64,
        "f" * 64,
    )
    _reason(cl6.CL6Reason.VALUATION_SET_INVALID, _report, vectors, points + (extra,))


def test_v310_cl6_08_valuation_hmac_and_scope_mutations_fail(
    vectors: dict[str, object],
) -> None:
    points = list(_points())
    object.__setattr__(points[1], "source_sha256", "d" * 64)
    _reason(
        cl6.CL6Reason.VALUATION_IDENTITY_INVALID,
        _report,
        vectors,
        tuple(points),
    )
    foreign = list(_points())
    foreign[1] = _point(
        110,
        FLOW,
        cl6.ValuationPhase.PRE_EXTERNAL_FLOW,
        2,
        "ba" * 32,
        "b" * 64,
        "bc" * 32,
        account_scope="f" * 64,
    )
    _reason(
        cl6.CL6Reason.EVIDENCE_CORRELATION_INVALID, _report, vectors, tuple(foreign)
    )


@pytest.mark.parametrize(
    ("ledger_bytes", "valuations", "period_start", "expected"),
    [
        ("not-bytes", (), "bad", cl6.CL6Reason.TYPE_INVALID),
        (b"{}", [], "bad", cl6.CL6Reason.TYPE_INVALID),
        (b"{}", (), "bad", cl6.CL6Reason.TIMESTAMP_INVALID),
    ],
)
def test_v310_cl6_09_report_first_failure_order(
    ledger_bytes: object,
    valuations: object,
    period_start: str,
    expected: cl6.CL6Reason,
) -> None:
    _reason(
        expected,
        cl6.build_performance_report,
        ledger_bytes,
        valuations,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        period_start=period_start,
        period_end=END,
        generated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def test_v310_cl6_10_external_timestamp_bound_is_operational(
    vectors: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cl6.MAX_EXTERNAL_FLOW_TIMESTAMPS == 10_000
    monkeypatch.setattr(cl6, "MAX_EXTERNAL_FLOW_TIMESTAMPS", 0)
    _reason(cl6.CL6Reason.NUMERIC_BOUND_EXCEEDED, _report, vectors)


def test_v310_cl6_11_reversal_and_correction_use_physical_row_counts() -> None:
    source = ledger.SourceIdentity(ACCOUNT_SCOPE, "SYNTHETIC", "1" * 64, "2" * 64)
    amount = _money(10)
    original = ledger.LedgerTransaction(
        ledger.LedgerClassification.DEPOSIT,
        FLOW,
        source,
        (
            ledger.LedgerPosting(1, ledger.LedgerAccount.ASSET_BROKER_CASH, amount),
            ledger.LedgerPosting(2, ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW, -amount),
        ),
    )
    reversal = ledger.LedgerTransaction(
        ledger.LedgerClassification.REVERSAL,
        FLOW,
        source,
        (
            ledger.LedgerPosting(1, ledger.LedgerAccount.ASSET_BROKER_CASH, -amount),
            ledger.LedgerPosting(2, ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW, amount),
        ),
        reversal_of_sha256=original.sha256,
    )
    correction_amount = _money(20)
    correction = ledger.LedgerTransaction(
        ledger.LedgerClassification.DEPOSIT,
        FLOW,
        source,
        (
            ledger.LedgerPosting(
                1, ledger.LedgerAccount.ASSET_BROKER_CASH, correction_amount
            ),
            ledger.LedgerPosting(
                2, ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW, -correction_amount
            ),
        ),
        corrects_sha256=original.sha256,
    )
    by_hash = {item.sha256: item for item in (original, reversal, correction)}
    assert (
        cl6._reporting_category(reversal, by_hash)
        is cl6.ReportingCategory.EXTERNAL_FLOW
    )
    summary = cl6._summary(
        (
            (original, cl6.ReportingCategory.EXTERNAL_FLOW, amount.minor_units),
            (reversal, cl6.ReportingCategory.EXTERNAL_FLOW, -amount.minor_units),
            (
                correction,
                cl6.ReportingCategory.EXTERNAL_FLOW,
                correction_amount.minor_units,
            ),
        )
    )
    assert summary.transaction_count == 3
    assert summary.external_flow_count == 3
    assert summary.external_flow_cash_effect == correction_amount


def test_v310_cl6_12_portfolio_and_risk_evidence_are_deterministic() -> None:
    portfolio = _portfolio_evidence()
    guard = _risk_evidence()
    assert portfolio == _portfolio_evidence()
    assert guard == _risk_evidence()
    assert portfolio.portfolio_snapshot_at == END
    assert portfolio.captured_at == END
    assert guard.risk_state_version == risk.RISK_STATE_VERSION
    assert len(portfolio.evidence_identity_sha256) == 64
    assert len(guard.evidence_identity_sha256) == 64


def test_v310_cl6_13_portfolio_preflight_precedes_virtual_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    balance = portfolio_model.CashBalance("RUB", 1.0, 0.0)
    state = portfolio_model.PortfolioState(
        version=portfolio_model.PORTFOLIO_STATE_SCHEMA_VERSION,
        account=portfolio_model.AccountState(RAW_ACCOUNT, None, None, None, (balance,)),
        snapshot_at=END_ISO,
        generated_at=END_ISO,
        freshness=portfolio_model.SnapshotFreshness.FRESH,
        source="TBANK",
        positions=(),
        warnings=(),
        state_status="READY",
        blocking=False,
        revision=1,
    )
    lease = portfolio_preflight.PortfolioSnapshotLease.from_state(
        state, leased_at=END_ISO
    )
    object.__setattr__(balance, "available", 1)

    def trap(*args: object, **kwargs: object) -> object:
        raise AssertionError("virtual dispatch reached")

    monkeypatch.setattr(portfolio_model.PortfolioState, "to_dict", trap)
    _reason(
        cl6.CL6Reason.PORTFOLIO_EVIDENCE_INVALID,
        cl6.build_portfolio_identity_evidence,
        lease,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        evaluated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def test_v310_cl6_14_risk_preflight_precedes_hash_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = risk.RiskPolicy()
    state = risk.RiskState()
    object.__setattr__(policy, "cash_reserve_rub", 0)

    def trap(*args: object, **kwargs: object) -> object:
        raise AssertionError("hash dispatch reached")

    monkeypatch.setattr(risk.RiskPolicy, "policy_hash", property(trap))
    _reason(
        cl6.CL6Reason.RISK_EVIDENCE_INVALID,
        cl6.build_risk_guard_evidence,
        policy,
        state,
        raw_account_id=RAW_ACCOUNT,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        captured_at=END,
        evaluated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def test_v310_cl6_15_context_rebuild_and_ready(vectors: dict[str, object]) -> None:
    context = _context(vectors)
    assert context.status is cl6.RiskCashContextStatus.READY_FOR_LOCKED_REVALIDATION
    assert context.reason is cl6.RiskCashContextReason.READY
    assert context.free_investable_cash == _money(130)
    assert context.portfolio_snapshot_at == END
    assert context.risk_guard_captured_at == END


def test_v310_cl6_16_context_stale_precedence(vectors: dict[str, object]) -> None:
    context = _context(vectors, evaluated_at=END_PLUS_121)
    assert context.status is cl6.RiskCashContextStatus.BLOCKED
    assert context.reason is cl6.RiskCashContextReason.CASH_AVAILABILITY_STALE
    assert context.free_investable_cash is None


def test_v310_cl6_17_portfolio_not_ready_precedes_portfolio_stale(
    vectors: dict[str, object],
) -> None:
    blocked = _portfolio_evidence(
        freshness=portfolio_model.SnapshotFreshness.STALE,
        state_status="BLOCKED",
        blocking=False,
    )
    context = _context(vectors, portfolio=blocked)
    assert context.reason is cl6.RiskCashContextReason.PORTFOLIO_NOT_READY


def test_v310_cl6_18_context_rejects_detached_evidence(
    vectors: dict[str, object],
) -> None:
    portfolio = _portfolio_evidence()
    object.__setattr__(portfolio, "portfolio_revision", 10)
    _reason(
        cl6.CL6Reason.PORTFOLIO_EVIDENCE_INVALID,
        _context,
        vectors,
        portfolio=portfolio,
    )


def test_v310_cl6_19_dependency_from_future(vectors: dict[str, object]) -> None:
    _reason(
        cl6.CL6Reason.DEPENDENCY_FROM_FUTURE,
        _context,
        vectors,
        evaluated_at="2026-12-31T23:59:59.000000000Z",
    )


def test_v310_cl6_20_production_environment_rejected() -> None:
    _reason(
        cl6.CL6Reason.ENVIRONMENT_UNSUPPORTED,
        cl6.build_portfolio_valuation_point,
        _money(1),
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.PRODUCTION,
        as_of=END,
        phase=cl6.ValuationPhase.PERIOD_END,
        portfolio_revision=1,
        portfolio_decision_checksum="a" * 64,
        portfolio_document_checksum="b" * 64,
        source_sha256="c" * 64,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def test_v310_cl6_21_error_privacy_and_no_chaining() -> None:
    error = cl6.CL6Error(
        cl6.CL6Reason.PORTFOLIO_EVIDENCE_INVALID,
        evidence={
            "raw_account_id": RAW_ACCOUNT,
            "filesystem_path": str(ROOT),
            "portfolio_evidence_sha256": "a" * 64,
        },
    )
    rendered = f"{error!s} {error!r} {dict(error.evidence)!r}"
    assert RAW_ACCOUNT not in rendered
    assert str(ROOT) not in rendered
    assert error.evidence["portfolio_evidence_sha256"] == "a" * 64


def test_v310_cl6_22_dtos_are_frozen_and_canonical_have_no_float(
    vectors: dict[str, object],
) -> None:
    report = _report(vectors)
    context = _context(vectors)
    for value in (
        *_points(),
        report.cash_flow_summary,
        report.twr,
        report.xirr,
        report,
        _portfolio_evidence(),
        _risk_evidence(),
        context,
    ):
        assert dataclasses.is_dataclass(value)
        with pytest.raises(dataclasses.FrozenInstanceError):
            value.version = 2
        parsed = json.loads(value.canonical_bytes)
        stack = [parsed]
        while stack:
            item = stack.pop()
            assert type(item) is not float
            if type(item) is dict:
                stack.extend(item.values())
            elif type(item) is list:
                stack.extend(item)


def test_v310_cl6_23_context_does_not_accept_reporting_input(
    vectors: dict[str, object],
) -> None:
    report = _report(vectors)
    reconciliation, positions, reservations, availability = _cash_inputs(vectors)
    _reason(
        cl6.CL6Reason.TYPE_INVALID,
        cl6.build_portfolio_risk_cash_context,
        _export(vectors),
        reconciliation,
        positions,
        reservations,
        availability,
        report,
        _risk_evidence(),
        evaluated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def test_v310_cl6_24_fixture_and_module_are_only_new_paths() -> None:
    assert MODULE_PATH.is_file()
    assert FIXTURE_PATH.is_file()
    assert Path(__file__).resolve().is_file()
    assert _scope() == ACCOUNT_SCOPE
    assert os.environ.get("CL6_START_EXPERIMENT") is None


def test_v310_cl6_25_contract_owned_evidence_kats_are_executed(
    vectors: dict[str, object],
) -> None:
    expected = vectors["contract_owned_kat"]
    kat_at = "2026-09-11T10:00:00.000000000Z"
    report = dataclasses.replace(
        _report(vectors),
        ledger_export_sha256="3" * 64,
        ledger_revision=5,
        ledger_head_sha256="4" * 64,
        ledger_projection_sha256="6" * 64,
        report_identity_sha256="0" * 64,
    )
    report = dataclasses.replace(
        report,
        report_identity_sha256=cl6._report_identity(report, KEY),
    )
    portfolio = cl6.PortfolioIdentityEvidence(
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        captured_at=kat_at,
        portfolio_snapshot_at=kat_at,
        portfolio_revision=9,
        portfolio_decision_checksum="a" * 64,
        portfolio_document_checksum="ab" * 32,
        portfolio_schema_version=2,
        portfolio_source="CANONICAL",
        migration_status="COMPLETED",
        legacy_read_path_enabled=False,
        freshness="FRESH",
        state_status="READY",
        blocking=False,
        identity_key_id=KEY_ID,
        evidence_identity_sha256="0" * 64,
    )
    portfolio = dataclasses.replace(
        portfolio,
        evidence_identity_sha256=cl6._portfolio_evidence_identity(portfolio, KEY),
    )
    guard = cl6.RiskGuardEvidence(
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        captured_at=kat_at,
        risk_policy_hash="d" * 64,
        risk_state_guard_hash="e" * 64,
        risk_state_version=4,
        identity_key_id=KEY_ID,
        evidence_identity_sha256="0" * 64,
    )
    guard = dataclasses.replace(
        guard,
        evidence_identity_sha256=cl6._risk_evidence_identity(guard, KEY),
    )
    context = cl6.PortfolioRiskCashContext(
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        evaluated_at=kat_at,
        status=cl6.RiskCashContextStatus.READY_FOR_LOCKED_REVALIDATION,
        reason=cl6.RiskCashContextReason.READY,
        availability_sha256=(
            "cd375c47f6dc528ae8e64a13dfb7de9145f5964988893c7ef20561050411e238"
        ),
        availability_status="READY",
        availability_reason="READY",
        availability_evaluated_at=kat_at,
        broker_cash_as_of=kat_at,
        broker_positions_as_of=kat_at,
        free_investable_cash=_money(50),
        ledger_export_sha256="3" * 64,
        ledger_revision=5,
        ledger_head_sha256="4" * 64,
        reconciliation_sha256="1" * 64,
        central_order_revision=7,
        reservation_projection_hash="5" * 64,
        central_projection_evaluated_at=kat_at,
        portfolio_evidence_sha256=portfolio.sha256,
        portfolio_revision=9,
        portfolio_decision_checksum="a" * 64,
        portfolio_document_checksum="ab" * 32,
        portfolio_snapshot_at=kat_at,
        portfolio_captured_at=kat_at,
        risk_guard_evidence_sha256=guard.sha256,
        risk_guard_captured_at=kat_at,
        risk_policy_hash="d" * 64,
        risk_state_guard_hash="e" * 64,
        identity_key_id=KEY_ID,
        context_identity_sha256="0" * 64,
    )
    context = dataclasses.replace(
        context,
        context_identity_sha256=cl6._context_identity(context, KEY),
    )
    assert (
        report.report_identity_sha256 == expected["performance_report_identity_sha256"]
    )
    assert report.sha256 == expected["performance_report_sha256"]
    assert (
        portfolio.evidence_identity_sha256
        == expected["portfolio_identity_evidence_identity_sha256"]
    )
    assert portfolio.sha256 == expected["portfolio_identity_evidence_sha256"]
    assert (
        guard.evidence_identity_sha256
        == expected["risk_guard_evidence_identity_sha256"]
    )
    assert guard.sha256 == expected["risk_guard_evidence_sha256"]
    assert context.context_identity_sha256 == expected["context_identity_sha256"]
    assert context.sha256 == expected["context_sha256"]


def test_v310_cl6_26_risk_semantic_ranges_precede_hash_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatches: list[str] = []

    def policy_trap(self: risk.RiskPolicy) -> str:
        dispatches.append("policy")
        return "0" * 64

    def state_trap(state: risk.RiskState) -> str:
        dispatches.append("state")
        return "0" * 64

    monkeypatch.setattr(risk.RiskPolicy, "policy_hash", property(policy_trap))
    monkeypatch.setattr(cl6._risk_runtime, "risk_state_guard_hash", state_trap)
    cases = (
        ("policy", "max_position_lots", -1),
        ("policy", "max_orders_per_day", 0),
        ("policy", "max_snapshot_age_seconds", 2**1075),
        ("policy", "max_cash_usage_fraction", 0.0),
        ("policy", "commission_buffer_fraction", 1.0),
        ("state", "daily_order_count", -1),
        ("state", "daily_turnover_rub", -1.0),
    )
    for target, field, invalid in cases:
        policy = risk.RiskPolicy()
        state = risk.RiskState()
        object.__setattr__(policy if target == "policy" else state, field, invalid)
        _reason(
            cl6.CL6Reason.RISK_EVIDENCE_INVALID,
            cl6.build_risk_guard_evidence,
            policy,
            state,
            raw_account_id=RAW_ACCOUNT,
            account_scope_sha256=ACCOUNT_SCOPE,
            environment=ENV,
            captured_at=END,
            evaluated_at=END,
            identity_key=KEY,
            identity_key_id=KEY_ID,
        )
    assert dispatches == []


def test_v310_cl6_27_predecessor_valid_unbounded_integers_are_accepted() -> None:
    large = 2**70
    guard = cl6.build_risk_guard_evidence(
        risk.RiskPolicy(max_position_lots=large),
        risk.RiskState(daily_order_count=large),
        raw_account_id=RAW_ACCOUNT,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        captured_at=END,
        evaluated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    assert guard.risk_state_version == risk.RISK_STATE_VERSION


def test_v310_cl6_28_portfolio_lot_ranges_precede_serialization_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = ((-1, 0), (2, -1), (2, 3))
    forged: list[portfolio_preflight.PortfolioSnapshotLease] = []
    for requested, executed in cases:
        lease, order = _portfolio_lease_with_pending()
        object.__setattr__(order, "requested_lots", requested)
        object.__setattr__(order, "executed_lots", executed)
        forged.append(lease)

    def trap(*args: object, **kwargs: object) -> object:
        raise AssertionError("portfolio serialization reached")

    monkeypatch.setattr(portfolio_model.PortfolioState, "to_dict", trap)
    for lease in forged:
        _reason(
            cl6.CL6Reason.PORTFOLIO_EVIDENCE_INVALID,
            cl6.build_portfolio_identity_evidence,
            lease,
            account_scope_sha256=ACCOUNT_SCOPE,
            environment=ENV,
            evaluated_at=END,
            identity_key=KEY,
            identity_key_id=KEY_ID,
        )


def test_v310_cl6_29_context_key_and_timestamp_precede_ledger_bounds(
    vectors: dict[str, object],
) -> None:
    reconciliation, positions, reservations, availability = _cash_inputs(vectors)
    positional = (
        b"",
        reconciliation,
        positions,
        reservations,
        availability,
        _portfolio_evidence(),
        _risk_evidence(),
    )
    _reason(
        cl6.CL6Reason.TIMESTAMP_INVALID,
        cl6.build_portfolio_risk_cash_context,
        *positional,
        evaluated_at="bad",
        identity_key=b"",
        identity_key_id="bad",
    )
    _reason(
        cl6.CL6Reason.IDENTITY_KEY_INVALID,
        cl6.build_portfolio_risk_cash_context,
        *positional,
        evaluated_at=END,
        identity_key=b"",
        identity_key_id="bad",
    )
    _reason(
        cl6.CL6Reason.LEDGER_EXPORT_INVALID,
        cl6.build_portfolio_risk_cash_context,
        *positional,
        evaluated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


@pytest.mark.parametrize(
    "raw",
    (
        b'{"version":1,"version":1}',
        b'{"value":1.0}',
        b'{"value":"\\ud800"}',
    ),
)
def test_v310_cl6_30_canonical_ledger_json_rejections(
    raw: bytes,
) -> None:
    _reason(
        cl6.CL6Reason.LEDGER_EXPORT_INVALID,
        cl6.build_performance_report,
        raw,
        _points(),
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=ENV,
        period_start=START,
        period_end=END,
        generated_at=END,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def test_v310_cl6_31_reporting_classification_matrix_is_complete() -> None:
    expected = {
        ledger.LedgerClassification.OPENING_BALANCE: cl6.ReportingCategory.OPENING,
        ledger.LedgerClassification.DEPOSIT: cl6.ReportingCategory.EXTERNAL_FLOW,
        ledger.LedgerClassification.WITHDRAWAL: cl6.ReportingCategory.EXTERNAL_FLOW,
        ledger.LedgerClassification.DIVIDEND: cl6.ReportingCategory.INVESTMENT_INCOME,
        ledger.LedgerClassification.COUPON: cl6.ReportingCategory.INVESTMENT_INCOME,
        ledger.LedgerClassification.INTEREST: cl6.ReportingCategory.INVESTMENT_INCOME,
        ledger.LedgerClassification.COMMISSION: cl6.ReportingCategory.EXPENSE,
        ledger.LedgerClassification.TAX: cl6.ReportingCategory.EXPENSE,
        ledger.LedgerClassification.REFUND: cl6.ReportingCategory.EXPENSE,
        ledger.LedgerClassification.TRADE_SETTLEMENT: (
            cl6.ReportingCategory.INTERNAL_SETTLEMENT
        ),
        ledger.LedgerClassification.MANUAL_ADJUSTMENT: (
            cl6.ReportingCategory.MANUAL_ADJUSTMENT
        ),
    }
    assert cl6._CATEGORY_BY_CLASSIFICATION == expected
    assert ledger.LedgerClassification.REVERSAL not in expected


def test_v310_cl6_32_twr_and_xirr_edge_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _point(
        100, START, cl6.ValuationPhase.PERIOD_START, 1, "a" * 64, "b" * 64, "c" * 64
    )
    end = _point(
        120, END, cl6.ValuationPhase.PERIOD_END, 2, "d" * 64, "e" * 64, "f" * 64
    )
    no_flow = cl6._twr(initial_reason=None, start=start, end=end, pre={}, external={})
    assert no_flow.rate_decimal == "0.200000000000"

    pre_end = _point(
        100, END, cl6.ValuationPhase.PRE_EXTERNAL_FLOW, 3, "1" * 64, "2" * 64, "3" * 64
    )
    ambiguous = cl6._twr(
        initial_reason=None,
        start=start,
        end=end,
        pre={END: pre_end},
        external={END: _money(10).minor_units},
    )
    assert ambiguous.reason is cl6.MetricReason.END_FLOW_VALUATION_AMBIGUOUS

    zero = _point(
        0, START, cl6.ValuationPhase.PERIOD_START, 1, "a" * 64, "b" * 64, "c" * 64
    )
    non_positive = cl6._twr(
        initial_reason=None,
        start=zero,
        end=end,
        pre={},
        external={},
    )
    assert non_positive.reason is cl6.MetricReason.NON_POSITIVE_SUBPERIOD_BASE

    monkeypatch.setattr(cl6, "MAX_RATIONAL_DECIMAL_DIGITS", 0)
    bounded = cl6._twr(initial_reason=None, start=start, end=end, pre={}, external={})
    assert bounded.reason is cl6.MetricReason.RATIONAL_LIMIT_EXCEEDED
    monkeypatch.setattr(cl6, "MAX_RATIONAL_DECIMAL_DIGITS", 4_096)

    no_sign = cl6._xirr(initial_reason=None, start=zero, end=end, external={})
    assert no_sign.reason is cl6.MetricReason.XIRR_NO_SIGN_CHANGE
    multi = cl6._xirr(
        initial_reason=None,
        start=start,
        end=end,
        external={
            "2026-04-01T00:00:00.000000000Z": -300_000_000_000,
            "2026-08-01T00:00:00.000000000Z": 300_000_000_000,
        },
    )
    assert multi.reason is cl6.MetricReason.XIRR_MULTIPLE_SIGN_CHANGES
    assert multi.status is cl6.MetricStatus.AMBIGUOUS
    huge_end = _point(
        200_000, END, cl6.ValuationPhase.PERIOD_END, 2, "d" * 64, "e" * 64, "f" * 64
    )
    outside = cl6._xirr(initial_reason=None, start=start, end=huge_end, external={})
    assert outside.reason is cl6.MetricReason.XIRR_ROOT_OUT_OF_RANGE


def test_v310_cl6_33_every_context_identity_field_is_hmac_bound(
    vectors: dict[str, object],
) -> None:
    context = _context(vectors)
    mutations = {
        "account_scope_sha256": "f" * 64,
        "environment": broker.BrokerEnvironment.PRODUCTION,
        "evaluated_at": END_PLUS_10,
        "status": cl6.RiskCashContextStatus.BLOCKED,
        "reason": cl6.RiskCashContextReason.CASH_AVAILABILITY_STALE,
        "availability_sha256": "f" * 64,
        "availability_status": "BLOCKED",
        "availability_reason": "TEST",
        "availability_evaluated_at": END_PLUS_10,
        "broker_cash_as_of": END_PLUS_10,
        "broker_positions_as_of": END_PLUS_10,
        "free_investable_cash": _money(131),
        "ledger_export_sha256": "f" * 64,
        "ledger_revision": context.ledger_revision + 1,
        "ledger_head_sha256": "f" * 64,
        "reconciliation_sha256": "f" * 64,
        "central_order_revision": context.central_order_revision + 1,
        "reservation_projection_hash": "f" * 64,
        "central_projection_evaluated_at": END_PLUS_10,
        "portfolio_evidence_sha256": "f" * 64,
        "portfolio_revision": context.portfolio_revision + 1,
        "portfolio_decision_checksum": "f" * 64,
        "portfolio_document_checksum": "f" * 64,
        "portfolio_snapshot_at": END_PLUS_10,
        "portfolio_captured_at": END_PLUS_10,
        "risk_guard_evidence_sha256": "f" * 64,
        "risk_guard_captured_at": END_PLUS_10,
        "risk_policy_hash": "f" * 64,
        "risk_state_guard_hash": "f" * 64,
        "identity_key_id": "CL6_MUTATION_KEY_V1",
        "version": 2,
    }
    identity_fields = {
        field.name
        for field in dataclasses.fields(context)
        if field.name != "context_identity_sha256"
    }
    assert set(mutations) == identity_fields
    for field, value in mutations.items():
        forged = dataclasses.replace(context)
        object.__setattr__(forged, field, value)
        assert cl6._context_identity(forged, KEY) != context.context_identity_sha256


def test_v310_cl6_34_mandatory_acceptance_matrix_has_exact_traceability(
    vectors: dict[str, object],
) -> None:
    assert vectors["mandatory_acceptance_ids"] == [
        f"V310-CL6-{index:02d}" for index in range(1, 69)
    ]


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("total_value", _money(101)),
        ("as_of", FLOW),
        ("phase", cl6.ValuationPhase.PRE_EXTERNAL_FLOW),
        ("portfolio_revision", 2),
        ("portfolio_decision_checksum", "d" * 64),
        ("portfolio_document_checksum", "d" * 64),
        ("source_sha256", "d" * 64),
        ("account_scope_sha256", "d" * 64),
        ("environment", broker.BrokerEnvironment.PRODUCTION),
        ("identity_key_id", "CL6_MUTATION_KEY_V1"),
        ("valuation_identity_sha256", "d" * 64),
        ("version", 2),
    ),
)
def test_v310_cl6_35_valuation_retained_hmac_mutation_matrix(
    field: str,
    replacement: object,
) -> None:
    point = _points()[0]
    object.__setattr__(point, field, replacement)
    with pytest.raises(cl6.CL6Error) as captured:
        cl6._validated_valuation(point, KEY)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("portfolio_revision", 10),
        ("portfolio_decision_checksum", "f" * 64),
        ("portfolio_document_checksum", "f" * 64),
        ("portfolio_snapshot_at", "2026-12-31T23:59:59.000000000Z"),
        ("captured_at", "2026-12-31T23:59:59.000000000Z"),
        ("freshness", "STALE"),
        ("state_status", "BLOCKED"),
        ("migration_status", "BLOCKED"),
        ("legacy_read_path_enabled", True),
        ("blocking", True),
        ("portfolio_source", "LEGACY"),
        ("account_scope_sha256", "f" * 64),
        ("evidence_identity_sha256", "f" * 64),
        ("version", 2),
    ),
)
def test_v310_cl6_36_portfolio_evidence_retained_hmac_mutation_matrix(
    field: str,
    replacement: object,
) -> None:
    evidence = _portfolio_evidence()
    object.__setattr__(evidence, field, replacement)
    with pytest.raises(cl6.CL6Error) as captured:
        cl6._validated_portfolio_evidence(evidence, KEY)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("risk_policy_hash", "f" * 64),
        ("risk_state_guard_hash", "f" * 64),
        ("risk_state_version", 3),
        ("captured_at", "2026-12-31T23:59:59.000000000Z"),
        ("account_scope_sha256", "f" * 64),
        ("evidence_identity_sha256", "f" * 64),
        ("version", 2),
    ),
)
def test_v310_cl6_37_risk_evidence_retained_hmac_mutation_matrix(
    field: str,
    replacement: object,
) -> None:
    evidence = _risk_evidence()
    object.__setattr__(evidence, field, replacement)
    with pytest.raises(cl6.CL6Error) as captured:
        cl6._validated_risk_evidence(evidence, KEY)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "field",
    (
        "period_start",
        "period_end",
        "ledger_export_sha256",
        "ledger_revision",
        "ledger_head_sha256",
        "ledger_projection_sha256",
        "ledger_complete",
        "ledger_incompleteness_kinds",
        "valuation_set_sha256",
        "cash_flow_summary",
        "twr",
        "xirr",
        "report_status",
        "generated_at",
        "identity_key_id",
        "report_identity_sha256",
        "version",
    ),
)
def test_v310_cl6_38_report_retained_hmac_mutation_matrix(
    field: str,
    vectors: dict[str, object],
) -> None:
    report = _report(vectors)
    summary = dataclasses.replace(report.cash_flow_summary)
    object.__setattr__(summary, "transaction_count", summary.transaction_count + 1)
    twr = dataclasses.replace(report.twr)
    object.__setattr__(twr, "rate_decimal", "0.237500000001")
    xirr = dataclasses.replace(report.xirr)
    object.__setattr__(xirr, "rate_decimal", "0.242497375455")
    replacements = {
        "period_start": "2026-01-02T00:00:00.000000000Z",
        "period_end": "2026-12-31T00:00:00.000000000Z",
        "ledger_export_sha256": "f" * 64,
        "ledger_revision": report.ledger_revision + 1,
        "ledger_head_sha256": "f" * 64,
        "ledger_projection_sha256": "f" * 64,
        "ledger_complete": False,
        "ledger_incompleteness_kinds": ("TEST",),
        "valuation_set_sha256": "f" * 64,
        "cash_flow_summary": summary,
        "twr": twr,
        "xirr": xirr,
        "report_status": cl6.ReportStatus.DEGRADED,
        "generated_at": END_PLUS_10,
        "identity_key_id": "CL6_MUTATION_KEY_V1",
        "report_identity_sha256": "f" * 64,
        "version": 2,
    }
    forged = dataclasses.replace(report)
    object.__setattr__(forged, field, replacements[field])
    recomputed = cl6._report_identity(forged, KEY)
    if field == "report_identity_sha256":
        assert recomputed != forged.report_identity_sha256
    else:
        assert recomputed != report.report_identity_sha256
    assert forged.sha256 != report.sha256
