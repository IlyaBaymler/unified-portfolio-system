from __future__ import annotations

import ast
import dataclasses
import hashlib
import hmac
import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

from trading_robot import broker_read_adapters as broker
from trading_robot import cash_availability as cl5
from trading_robot import cash_ledger_domain as ledger
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import cash_ledger_persistence as persistence
from trading_robot import central_order_manager as central
from trading_robot import portfolio_repository, risk_runtime, sandbox_execution_adapter

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
MODULE_PATH = CURRENT / "trading_robot" / "cash_availability.py"
FIXTURE_PATH = CURRENT / "tests" / "fixtures" / "v3_10_cash_availability_vectors.json"
ACCEPTED_CONTRACT_HEAD = "e1dc8ab570abaa7547714951423749e2986fe31a"
ORIGINAL_IMPLEMENTATION_HEAD = "547ae06d8befec25b039e46b6dc06702658c6db0"
STABLE_PREDECESSOR = "c3befe877e5f0d0058fbf485cb42ff54d57da7de"
IMPLEMENTATION_PATHS = {
    "current/trading_robot/cash_availability.py",
    "current/tests/test_v3_10_cash_availability.py",
    "current/tests/fixtures/v3_10_cash_availability_vectors.json",
}
KEY = bytes(range(32))
KEY_ID = "CL5_TEST_KEY_V1"
RAW_ACCOUNT = "sandbox-account-0001"
ACCOUNT_SCOPE = "15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3"
TS = "2026-09-11T10:00:00.000000000Z"
CENTRAL_TS = "2026-09-11T10:00:00+00:00"
TS_PLUS_10 = "2026-09-11T10:00:10.000000000Z"
TS_PLUS_11 = "2026-09-11T10:00:11.000000000Z"
TS_PLUS_120 = "2026-09-11T10:02:00.000000000Z"
TS_PLUS_121 = "2026-09-11T10:02:01.000000000Z"


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


def _scope(raw_account: str = RAW_ACCOUNT, *, key: bytes = KEY) -> str:
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


def _money_value(units: str, nano: int = 0, currency: str = "RUB") -> dict[str, object]:
    return {"currency": currency, "nano": nano, "units": units}


def _positions_response(
    *,
    money_units: str = "80",
    money_nano: int = 0,
    blocked_units: str = "20",
    blocked_nano: int = 0,
    foreign: bool = False,
    loading: bool = False,
) -> dict[str, object]:
    money = [_money_value(money_units, money_nano)]
    if foreign:
        money.append(_money_value("1", currency="USD"))
    return {
        "accountId": RAW_ACCOUNT,
        "blocked": [_money_value(blocked_units, blocked_nano)],
        "futures": [],
        "limitsLoadingInProgress": loading,
        "money": money,
        "options": [],
        "securities": [],
    }


def _response_with_canonical_size(target: int) -> dict[str, object]:
    response = _positions_response()
    delta = target - len(_canonical(response))
    item_count = (delta + 4_099) // 4_099
    total_string_scalars = delta - (3 * item_count) + 1
    assert item_count > 0
    assert 4_096 * (item_count - 1) <= total_string_scalars <= 4_096 * item_count
    lengths = [4_096] * (item_count - 1)
    lengths.append(total_string_scalars - sum(lengths))
    response["securities"] = ["x" * length for length in lengths]
    assert len(_canonical(response)) == target
    return response


def _positions(
    *,
    response: object | None = None,
    as_of: str = TS,
    evaluated_at: str = TS,
    account_scope: str = ACCOUNT_SCOPE,
    complete: bool = True,
) -> cl5.BrokerPositionsCashProof:
    return cl5.build_broker_positions_cash_proof(
        _positions_response() if response is None else response,
        account_scope_sha256=account_scope,
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=as_of,
        evaluated_at=evaluated_at,
        response_complete=complete,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _candidate(*, account: str = RAW_ACCOUNT) -> central.CentralOrderCandidate:
    return central.CentralOrderCandidate(
        account_id=account,
        instrument_id="uid-sber",
        ticker="SBER",
        runtime_key="runtime-sber",
        runtime_config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=CENTRAL_TS,
        strategy_id="sma",
        strategy_profile_hash="b" * 64,
        current_lots=0,
        target_lots=1,
        estimated_price_kopecks=3_000,
        lot_size=1,
        created_at=CENTRAL_TS,
    )


def _authorization(*, account: str = RAW_ACCOUNT) -> central.ExecutionAuthorization:
    return central.ExecutionAuthorization(
        account_id=account,
        instrument_id="uid-sber",
        authorized_target_lots=1,
        portfolio_revision=1,
        portfolio_decision_checksum="c" * 64,
        portfolio_document_checksum="d" * 64,
        available_cash_kopecks=100_000,
        preflight_status="PASS",
        pending_order_ids=(),
        uncertain_order_ids=(),
        risk_status="PASS",
        risk_decision_id="risk-decision",
        risk_policy_hash="e" * 64,
        risk_order_allowed=True,
        authorized_at=CENTRAL_TS,
    )


def _intent(
    status: str = "QUEUED",
    *,
    reserved_kopecks: int = 3_000,
    account: str = RAW_ACCOUNT,
) -> central.CentralOrderIntent:
    candidate = _candidate(account=account)
    intent = central.CentralOrderIntent.create(
        candidate,
        _authorization(account=account),
        queue_sequence=1,
        reserved_cash_kopecks=reserved_kopecks,
        created_at=CENTRAL_TS,
    )
    if status == "QUEUED":
        return intent
    intent = intent.transition("IN_FLIGHT", detail="claimed", at=CENTRAL_TS)
    if status == "IN_FLIGHT":
        return intent
    if status == "SUBMITTED":
        return intent.transition(
            "SUBMITTED",
            detail="submitted",
            at=CENTRAL_TS,
            broker_order_id="broker-order-1",
        )
    if status == "UNCERTAIN":
        return intent.transition(
            "UNCERTAIN",
            detail="unknown",
            at=CENTRAL_TS,
            uncertainty_reason="transport",
        )
    if status == "FAILED":
        return intent.transition(
            "FAILED", detail="failed", at=CENTRAL_TS, outcome="NOT_SUBMITTED"
        )
    raise AssertionError(status)


def _state(
    status: str | None = None,
    *,
    reserved_kopecks: int = 3_000,
    account: str = RAW_ACCOUNT,
) -> central.CentralOrderState:
    intents = (
        ()
        if status is None
        else (
            _intent(
                status,
                reserved_kopecks=reserved_kopecks,
                account=account,
            ),
        )
    )
    return central.CentralOrderState(
        account_id=account,
        revision=7 if intents else 0,
        next_sequence=2 if intents else 1,
        intents=intents,
        created_at=CENTRAL_TS,
        updated_at=CENTRAL_TS,
    )


def _reservations(
    status: str | None = None,
    *,
    reserved_kopecks: int = 3_000,
    evaluated_at: str = TS,
    account: str = RAW_ACCOUNT,
    account_scope: str = ACCOUNT_SCOPE,
) -> cl5.CentralReservationProjection:
    return cl5.project_central_reservations(
        _state(status, reserved_kopecks=reserved_kopecks, account=account),
        account_scope_sha256=account_scope,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=evaluated_at,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _cl4_proof(*, total_units: str = "100", as_of: str = TS) -> cl4.BrokerCashProof:
    return cl4.build_broker_cash_proof(
        {"totalAmountCurrencies": _money_value(total_units)},
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=as_of,
        evaluated_at=as_of,
        response_complete=True,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


@pytest.fixture(scope="module")
def opened_ledger(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    store = persistence.CashLedgerStore.create(
        tmp_path_factory.mktemp("cl5-ledger") / "store",
        (cl4.CL4_OPENING_CODEC,),
    )
    proof = _cl4_proof()
    plan = cl4.prepare_from_now_opening(
        store.export_bytes(), proof, evaluated_at=TS, identity_key=KEY
    )
    cl4.accept_from_now_opening(
        store,
        plan,
        confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}",
        evaluated_at=TS,
        identity_key=KEY,
    )
    exported = store.export_bytes()
    store.close()
    return exported


def _reconciliation(
    opened_ledger: bytes,
    *,
    total_units: str = "100",
    as_of: str = TS,
    evaluated_at: str | None = None,
) -> cl4.CashReconciliation:
    return cl4.reconcile_shadow_cash(
        opened_ledger,
        _cl4_proof(total_units=total_units, as_of=as_of),
        evaluated_at=as_of if evaluated_at is None else evaluated_at,
        identity_key=KEY,
    )


def _snapshot(
    opened_ledger: bytes,
    *,
    reconciliation: cl4.CashReconciliation | None = None,
    positions: cl5.BrokerPositionsCashProof | None = None,
    reservations: cl5.CentralReservationProjection | None = None,
    evaluated_at: str = TS,
) -> cl5.CashAvailabilitySnapshot:
    return cl5.build_cash_availability(
        opened_ledger,
        _reconciliation(opened_ledger) if reconciliation is None else reconciliation,
        _positions() if positions is None else positions,
        _reservations() if reservations is None else reservations,
        evaluated_at=evaluated_at,
        identity_key=KEY,
    )


def _zero_cash_ledger(tmp_path: Path) -> bytes:
    schema = _canonical(
        {
            "domain": "v3.10-operation-inbox-codec-schema",
            "fields": [
                {
                    "allowed_values": None,
                    "key": "operation_kind",
                    "kind": "STRING",
                    "max_scalars": "64",
                    "maximum": None,
                    "minimum": None,
                    "required": True,
                }
            ],
            "version": 1,
        }
    ).decode("ascii")
    descriptor = persistence.CodecDescriptor(
        "CL5_ZERO_CASH_TEST",
        schema,
        hashlib.sha256(schema.encode("ascii")).hexdigest(),
    )
    store = persistence.CashLedgerStore.create(
        tmp_path / "zero-cash-store",
        (cl4.CL4_OPENING_CODEC, descriptor),
    )
    proof = _cl4_proof()
    plan = cl4.prepare_from_now_opening(
        store.export_bytes(), proof, evaluated_at=TS, identity_key=KEY
    )
    cl4.accept_from_now_opening(
        store,
        plan,
        confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}",
        evaluated_at=TS,
        identity_key=KEY,
    )
    content = {"operation_kind": "WITHDRAW_ALL"}
    content_bytes = _canonical(content)
    source = ledger.SourceIdentity(
        account_scope_sha256=ACCOUNT_SCOPE,
        source_kind="SYNTHETIC",
        source_scope_sha256="6" * 64,
        source_content_sha256=hashlib.sha256(content_bytes).hexdigest(),
    )
    observation = persistence.InboxObservation.create(
        descriptor=descriptor,
        content=content,
        source=source,
        observed_at=TS_PLUS_10,
        provenance_sha256="7" * 64,
    )
    amount = ledger.Money(currency="RUB", minor_units=-100_000_000_000)
    transaction = ledger.LedgerTransaction(
        classification=ledger.LedgerClassification.WITHDRAWAL,
        effective_at=TS_PLUS_10,
        source=source,
        postings=(
            ledger.LedgerPosting(1, ledger.LedgerAccount.ASSET_BROKER_CASH, amount),
            ledger.LedgerPosting(2, ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW, -amount),
        ),
    )
    before = store.snapshot()
    store.append_observation(observation, expected_store_revision=before.store_revision)
    before_transaction = store.snapshot()
    store.append_transaction(
        transaction,
        observation.sha256,
        expected_store_revision=before_transaction.store_revision,
        expected_ledger_revision=before_transaction.ledger_revision,
    )
    exported = store.export_bytes()
    store.close()
    return exported


def _reason(
    reason: cl5.CL5Reason,
    callable_: object,
    *args: object,
    **kwargs: object,
) -> cl5.CL5Error:
    try:
        callable_(*args, **kwargs)
    except cl5.CL5Error as error:
        assert error.reason is reason
        assert str(error) == reason.value
        assert error.__cause__ is None
        assert error.__context__ is None
        return error
    raise AssertionError(f"expected {reason.value}")


def test_public_surface_and_signatures_are_exact() -> None:
    assert set(cl5.__all__) == {
        "AvailabilityStatus",
        "AvailabilityReason",
        "OverlapDisposition",
        "CL5Reason",
        "CL5Error",
        "BrokerPositionsCashProof",
        "CentralReservationProjection",
        "CashAvailabilitySnapshot",
        "build_broker_positions_cash_proof",
        "project_central_reservations",
        "build_cash_availability",
    }
    expected = {
        "build_broker_positions_cash_proof": [
            "response",
            "account_scope_sha256",
            "environment",
            "as_of",
            "evaluated_at",
            "response_complete",
            "identity_key",
            "identity_key_id",
        ],
        "project_central_reservations": [
            "state",
            "account_scope_sha256",
            "environment",
            "evaluated_at",
            "identity_key",
            "identity_key_id",
        ],
        "build_cash_availability": [
            "ledger_export_bytes",
            "reconciliation",
            "positions",
            "reservations",
            "evaluated_at",
            "identity_key",
        ],
    }
    for name, parameters in expected.items():
        assert list(inspect.signature(getattr(cl5, name)).parameters) == parameters
    assert list(cl5.AvailabilityStatus) == [
        cl5.AvailabilityStatus.READY,
        cl5.AvailabilityStatus.MANUAL_REVIEW_REQUIRED,
        cl5.AvailabilityStatus.BLOCKED,
    ]
    assert {item.value for item in cl5.CL5Reason} == {
        "TYPE_INVALID",
        "VERSION_UNSUPPORTED",
        "ENVIRONMENT_UNSUPPORTED",
        "CURRENCY_UNSUPPORTED",
        "ACCOUNT_SCOPE_INVALID",
        "IDENTITY_KEY_INVALID",
        "TIMESTAMP_INVALID",
        "PROOF_FROM_FUTURE",
        "PROOF_STALE",
        "DEPENDENCY_FROM_FUTURE",
        "PROOF_INCOMPLETE",
        "RESPONSE_BOUNDS_EXCEEDED",
        "RESPONSE_SCHEMA_INVALID",
        "POSITIONS_LOADING_IN_PROGRESS",
        "MONEY_INVALID",
        "PROOF_IDENTITY_INVALID",
        "CENTRAL_STATE_INVALID",
        "CENTRAL_ACCOUNT_MISMATCH",
        "CENTRAL_PROJECTION_INVALID",
        "CL4_EVIDENCE_INVALID",
        "ARITHMETIC_OVERFLOW",
        "CANONICAL_FORMAT_INVALID",
        "INTERNAL_BOUNDARY_FAILED",
    }


def test_contract_owned_known_answers(vectors: dict[str, object]) -> None:
    assert _scope() == ACCOUNT_SCOPE == vectors["account_scope"]["sha256"]
    proof = _positions(response=vectors["positions"]["response"])
    assert (
        proof.response_canonical_sha256
        == vectors["positions"]["response_canonical_sha256"]
    )
    assert proof.proof_identity_sha256 == vectors["positions"]["proof_identity_sha256"]
    assert (
        proof.canonical_bytes.decode("ascii")
        == vectors["positions"]["proof_canonical_ascii"]
    )
    assert proof.sha256 == vectors["positions"]["proof_sha256"]
    foreign = _positions(response=vectors["foreign_cash_mutation"]["response"])
    assert foreign.foreign_cash_present is True
    assert (
        foreign.response_canonical_sha256
        == vectors["foreign_cash_mutation"]["response_canonical_sha256"]
    )
    assert (
        foreign.proof_identity_sha256
        == vectors["foreign_cash_mutation"]["foreign_cash_present_true_hmac"]
    )
    mutation = vectors["foreign_cash_mutation"]
    false_hmac = hmac.new(
        KEY,
        mutation["foreign_cash_present_false_hmac_preimage_ascii"].encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    assert false_hmac == mutation["same_preimage_with_foreign_cash_present_false_hmac"]
    assert false_hmac != foreign.proof_identity_sha256


def test_projection_and_snapshot_known_answers(vectors: dict[str, object]) -> None:
    projection_vector = vectors["projection"]
    queued = ledger.Money(currency="RUB", minor_units=30_000_000_000)
    zero = ledger.Money(currency="RUB", minor_units=0)
    projection = cl5.CentralReservationProjection(
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        central_order_revision=7,
        central_reservation_projection_hash=projection_vector[
            "central_reservation_projection_hash"
        ],
        queued_reserved_cash=queued,
        ambiguous_reserved_cash=zero,
        total_reserved_cash=queued,
        queued_count=1,
        ambiguous_count=0,
        evaluated_at=TS,
        identity_key_id=KEY_ID,
        projection_identity_sha256=projection_vector["identity_sha256"],
    )
    assert (
        hmac.new(
            KEY,
            projection_vector["hmac_preimage_ascii"].encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        == projection.projection_identity_sha256
    )
    assert (
        projection.canonical_bytes.decode("ascii")
        == projection_vector["canonical_ascii"]
    )
    assert projection.sha256 == projection_vector["sha256"]
    snapshot = cl5.CashAvailabilitySnapshot(
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        currency="RUB",
        evaluated_at=TS,
        cl4_reconciliation_evaluated_at=TS,
        broker_cash_as_of=TS,
        broker_positions_as_of=TS,
        central_projection_evaluated_at=TS,
        reconciliation_sha256="11" * 32,
        cl4_adoption_candidate_sha256="22" * 32,
        ledger_export_sha256="33" * 32,
        ledger_revision=5,
        ledger_head_sha256="44" * 32,
        broker_positions_cash_proof_sha256=vectors["positions"]["proof_sha256"],
        broker_total_cash=ledger.Money(currency="RUB", minor_units=100_000_000_000),
        broker_blocked_cash=ledger.Money(currency="RUB", minor_units=20_000_000_000),
        broker_unblocked_cash=ledger.Money(currency="RUB", minor_units=80_000_000_000),
        central_reservation_projection_sha256=projection.sha256,
        central_order_revision=7,
        central_reservation_projection_hash="55" * 32,
        central_queued_reserved_cash=queued,
        central_ambiguous_reserved_cash=zero,
        central_total_reserved_cash=queued,
        overlap_disposition=cl5.OverlapDisposition.QUEUED_DISJOINT,
        status=cl5.AvailabilityStatus.READY,
        availability_reason=cl5.AvailabilityReason.READY,
        free_investable_cash=ledger.Money(currency="RUB", minor_units=50_000_000_000),
    )
    assert (
        snapshot.canonical_bytes.decode("ascii")
        == vectors["snapshot"]["canonical_ascii"]
    )
    assert snapshot.sha256 == vectors["snapshot"]["sha256"]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ([], cl5.CL5Reason.TYPE_INVALID),
        (
            {
                "accountId": RAW_ACCOUNT,
                "money": [],
                "blocked": [],
                "limitsLoadingInProgress": False,
                "unknown": [],
            },
            cl5.CL5Reason.RESPONSE_SCHEMA_INVALID,
        ),
        (
            {
                "accountId": RAW_ACCOUNT,
                "money": (),
                "blocked": [],
                "limitsLoadingInProgress": False,
            },
            cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED,
        ),
        (
            {
                "accountId": RAW_ACCOUNT,
                "money": [{"currency": "RUB", "units": "1", "nano": 0, "x": 1}],
                "blocked": [],
                "limitsLoadingInProgress": False,
            },
            cl5.CL5Reason.RESPONSE_SCHEMA_INVALID,
        ),
        (
            {
                "accountId": RAW_ACCOUNT,
                "money": [],
                "blocked": [],
                "limitsLoadingInProgress": True,
            },
            cl5.CL5Reason.POSITIONS_LOADING_IN_PROGRESS,
        ),
    ],
)
def test_positions_schema_and_type_fail_closed(
    response: object, reason: cl5.CL5Reason
) -> None:
    _reason(reason, _positions, response=response)


def test_positions_first_failure_and_graph_bounds() -> None:
    cyclic: dict[str, object] = _positions_response()
    cyclic["securities"] = cyclic
    _reason(cl5.CL5Reason.PROOF_INCOMPLETE, _positions, response=cyclic, complete=False)
    _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=cyclic)
    shared: list[object] = []
    aliased = _positions_response()
    aliased["securities"] = shared
    aliased["futures"] = shared
    _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=aliased)
    deep: object = "leaf"
    for _ in range(16):
        deep = [deep]
    too_deep = _positions_response()
    too_deep["securities"] = [deep]
    _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=too_deep)
    huge = _positions_response()
    huge["securities"] = ["x" * 4_096 for _ in range(260)]
    _reason(cl5.CL5Reason.CANONICAL_FORMAT_INVALID, _positions, response=huge)
    int_overflow = _positions_response()
    int_overflow["securities"] = [2**63]
    _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=int_overflow)
    overlong_string = _positions_response()
    overlong_string["securities"] = ["x" * 4_097]
    _reason(
        cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED,
        _positions,
        response=overlong_string,
    )
    overlong_key = _positions_response()
    overlong_key["securities"] = [{"x" * 129: None}]
    _reason(
        cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED,
        _positions,
        response=overlong_key,
    )
    too_many_keys = _positions_response()
    too_many_keys["securities"] = [{f"k{index}": None for index in range(4_097)}]
    _reason(
        cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED,
        _positions,
        response=too_many_keys,
    )
    too_many_nodes = _positions_response()
    too_many_nodes["securities"] = [None] * 100_000
    _reason(
        cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED,
        _positions,
        response=too_many_nodes,
    )


def test_positions_exact_inclusive_response_bounds_and_canonical_phase() -> None:
    at_depth: object = None
    for _ in range(13):
        at_depth = [at_depth]
    response = _positions_response()
    response["securities"] = [at_depth]
    assert _positions(response=response).response_canonical_sha256
    too_deep: object = None
    for _ in range(14):
        too_deep = [too_deep]
    response = _positions_response()
    response["securities"] = [too_deep]
    _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=response)

    response = _positions_response()
    response["securities"] = [None] * 99_984
    assert _positions(response=response).response_canonical_sha256
    response["securities"].append(None)
    _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=response)

    response = _positions_response()
    response["securities"] = [-(2**63), (2**63) - 1]
    assert _positions(response=response).response_canonical_sha256
    for integer in (-(2**63) - 1, 2**63):
        response = _positions_response()
        response["securities"] = [integer]
        _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=response)

    response = _positions_response()
    response["securities"] = ["x" * 4_096, {"k" * 128: None}]
    assert _positions(response=response).response_canonical_sha256
    response = _positions_response()
    response["securities"] = [{f"k{index}": None for index in range(4_096)}]
    assert _positions(response=response).response_canonical_sha256

    at_canonical_limit = _response_with_canonical_size(1_048_576)
    assert _positions(response=at_canonical_limit).response_canonical_sha256
    _reason(
        cl5.CL5Reason.CANONICAL_FORMAT_INVALID,
        _positions,
        response=_response_with_canonical_size(1_048_577),
    )
    response = _positions_response()
    response["securities"] = ["\ud800"]
    _reason(cl5.CL5Reason.CANONICAL_FORMAT_INVALID, _positions, response=response)
    response = _positions_response()
    response["securities"] = [{"\ud800": None}]
    _reason(cl5.CL5Reason.RESPONSE_BOUNDS_EXCEEDED, _positions, response=response)


def test_positions_account_money_time_and_foreign_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reason(
        cl5.CL5Reason.ACCOUNT_SCOPE_INVALID,
        _positions,
        account_scope="f" * 64,
    )
    _reason(
        cl5.CL5Reason.MONEY_INVALID,
        _positions,
        response=_positions_response(money_units="01"),
    )
    duplicate = _positions_response()
    duplicate["money"].append(_money_value("1"))
    _reason(cl5.CL5Reason.RESPONSE_SCHEMA_INVALID, _positions, response=duplicate)
    _reason(
        cl5.CL5Reason.MONEY_INVALID,
        _positions,
        response=_positions_response(blocked_units="-1"),
    )
    _reason(
        cl5.CL5Reason.PROOF_FROM_FUTURE,
        _positions,
        as_of=TS_PLUS_11,
        evaluated_at=TS,
    )
    _reason(
        cl5.CL5Reason.PROOF_STALE,
        _positions,
        as_of=TS,
        evaluated_at=TS_PLUS_121,
    )
    edge = _positions(as_of=TS, evaluated_at=TS_PLUS_120)
    assert edge.as_of == TS
    foreign = _positions(response=_positions_response(foreign=True))
    assert foreign.foreign_cash_present is True
    assert foreign.positions_money_rub.minor_units == 80_000_000_000
    long_account = "a" * 4_096
    long_response = _positions_response()
    long_response["accountId"] = long_account
    boundary = _positions(response=long_response, account_scope=_scope(long_account))
    assert boundary.account_scope_sha256 == _scope(long_account)
    monkeypatch.setattr(broker, "money_value_to_money", lambda _value: object())
    _reason(cl5.CL5Reason.MONEY_INVALID, _positions)


@pytest.mark.parametrize(
    ("status", "queued", "ambiguous"),
    [
        (None, 0, 0),
        ("QUEUED", 30_000_000_000, 0),
        ("IN_FLIGHT", 0, 30_000_000_000),
        ("SUBMITTED", 0, 30_000_000_000),
        ("UNCERTAIN", 0, 30_000_000_000),
        ("FAILED", 0, 0),
    ],
)
def test_central_lifecycle_partition_and_kopeck_conversion(
    status: str | None, queued: int, ambiguous: int
) -> None:
    projection = _reservations(status)
    assert projection.queued_reserved_cash.minor_units == queued
    assert projection.ambiguous_reserved_cash.minor_units == ambiguous
    assert projection.total_reserved_cash.minor_units == queued + ambiguous
    assert projection.queued_count == (1 if status == "QUEUED" else 0)
    assert projection.ambiguous_count == (
        1 if status in {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"} else 0
    )


def test_central_exact_recursive_boundary_precedes_virtual_dispatch() -> None:
    class EvilIntent(central.CentralOrderIntent):
        called = False

        def to_dict(self) -> dict[str, object]:
            type(self).called = True
            raise AssertionError("virtual dispatch")

    base = _intent()
    evil = EvilIntent(
        **{field.name: getattr(base, field.name) for field in dataclasses.fields(base)}
    )
    state = _state()
    object.__setattr__(state, "intents", (evil,))
    _reason(
        cl5.CL5Reason.CENTRAL_STATE_INVALID,
        cl5.project_central_reservations,
        state,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    assert EvilIntent.called is False


def test_central_cardinality_precedes_element_scan_and_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state("QUEUED")
    object.__setattr__(state, "intents", (state.intents[0],) * 100_001)
    normalized = False

    def forbidden_normalization(_state: object) -> dict[str, object]:
        nonlocal normalized
        normalized = True
        raise AssertionError("normalization before cardinality rejection")

    monkeypatch.setattr(central.CentralOrderState, "to_dict", forbidden_normalization)
    _reason(
        cl5.CL5Reason.CENTRAL_STATE_INVALID,
        cl5.project_central_reservations,
        state,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    assert normalized is False


def test_central_bounds_roundtrip_time_and_account() -> None:
    malformed = _state()
    object.__setattr__(malformed, "revision", True)
    _reason(
        cl5.CL5Reason.CENTRAL_STATE_INVALID,
        cl5.project_central_reservations,
        malformed,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    integer_overflow = _state()
    object.__setattr__(integer_overflow, "revision", 2**63)
    _reason(
        cl5.CL5Reason.CENTRAL_STATE_INVALID,
        cl5.project_central_reservations,
        integer_overflow,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    malformed_text = _state("QUEUED")
    object.__setattr__(malformed_text.intents[0].transitions[0], "detail", "x" * 4_097)
    _reason(
        cl5.CL5Reason.CENTRAL_STATE_INVALID,
        cl5.project_central_reservations,
        malformed_text,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    future = dataclasses.replace(
        _state(),
        created_at="2026-09-11T10:00:11+00:00",
        updated_at="2026-09-11T10:00:11+00:00",
    )
    _reason(
        cl5.CL5Reason.DEPENDENCY_FROM_FUTURE,
        cl5.project_central_reservations,
        future,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    _reason(
        cl5.CL5Reason.CENTRAL_ACCOUNT_MISMATCH,
        _reservations,
        account="another-account",
    )


def test_central_transition_bound_and_projection_hash_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state("QUEUED")
    transition = state.intents[0].transitions[0]
    at_limit = dataclasses.replace(
        state.intents[0],
        transitions=(transition,) * 64,
    )
    at_limit_state = dataclasses.replace(state, intents=(at_limit,))
    projection = cl5.project_central_reservations(
        at_limit_state,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    assert projection.central_reservation_projection_hash == (
        central.central_reservation_projection_hash(
            at_limit_state,
            excluded_reservation_ids=(),
        )
    )
    over_limit = dataclasses.replace(
        state.intents[0],
        transitions=(transition,) * 65,
    )
    over_limit_state = dataclasses.replace(state, intents=(over_limit,))
    _reason(
        cl5.CL5Reason.CENTRAL_STATE_INVALID,
        cl5.project_central_reservations,
        over_limit_state,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    monkeypatch.setattr(
        central,
        "central_reservation_projection_hash",
        lambda *_args, **_kwargs: object(),
    )
    _reason(cl5.CL5Reason.CENTRAL_PROJECTION_INVALID, _reservations)


def test_central_sell_never_contributes_reserved_cash() -> None:
    candidate = dataclasses.replace(_candidate(), current_lots=1, target_lots=0)
    authorization = dataclasses.replace(
        _authorization(),
        authorized_target_lots=0,
    )
    intent = central.CentralOrderIntent.create(
        candidate,
        authorization,
        queue_sequence=1,
        reserved_cash_kopecks=0,
        created_at=CENTRAL_TS,
    )
    state = central.CentralOrderState(
        account_id=RAW_ACCOUNT,
        revision=1,
        next_sequence=2,
        intents=(intent,),
        created_at=CENTRAL_TS,
        updated_at=CENTRAL_TS,
    )
    projection = cl5.project_central_reservations(
        state,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    assert projection.queued_count == 1
    assert projection.queued_reserved_cash.minor_units == 0
    forged = dataclasses.replace(intent)
    object.__setattr__(forged, "reserved_cash_kopecks", 1)
    object.__setattr__(state, "intents", (forged,))
    _reason(
        cl5.CL5Reason.CENTRAL_STATE_INVALID,
        cl5.project_central_reservations,
        state,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        evaluated_at=TS,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def test_ready_formulas_and_exact_sub_kopeck_cash(opened_ledger: bytes) -> None:
    no_reservation = _snapshot(opened_ledger)
    assert no_reservation.status is cl5.AvailabilityStatus.READY
    assert (
        no_reservation.overlap_disposition
        is cl5.OverlapDisposition.NO_LOCAL_RESERVATION
    )
    assert no_reservation.free_investable_cash.minor_units == 80_000_000_000
    queued = _snapshot(opened_ledger, reservations=_reservations("QUEUED"))
    assert queued.status is cl5.AvailabilityStatus.READY
    assert queued.overlap_disposition is cl5.OverlapDisposition.QUEUED_DISJOINT
    assert queued.free_investable_cash.minor_units == 50_000_000_000
    sub_kopeck = _positions(
        response=_positions_response(
            money_units="79",
            money_nano=995_000_000,
            blocked_units="20",
            blocked_nano=5_000_000,
        )
    )
    exact = _snapshot(
        opened_ledger, positions=sub_kopeck, reservations=_reservations("QUEUED")
    )
    assert exact.free_investable_cash.minor_units == 49_995_000_000
    queued_without_provider_block = _snapshot(
        opened_ledger,
        positions=_positions(
            response=_positions_response(money_units="100", blocked_units="0")
        ),
        reservations=_reservations("QUEUED"),
    )
    assert queued_without_provider_block.status is cl5.AvailabilityStatus.READY
    assert (
        queued_without_provider_block.free_investable_cash.minor_units == 70_000_000_000
    )


def test_zero_cash_zero_blocked_zero_reservations(tmp_path: Path) -> None:
    ledger_export = _zero_cash_ledger(tmp_path)
    reconciliation = _reconciliation(
        ledger_export,
        total_units="0",
        as_of=TS_PLUS_10,
        evaluated_at=TS_PLUS_10,
    )
    snapshot = _snapshot(
        ledger_export,
        reconciliation=reconciliation,
        positions=_positions(
            response=_positions_response(money_units="0", blocked_units="0"),
            as_of=TS_PLUS_10,
            evaluated_at=TS_PLUS_10,
        ),
        reservations=_reservations(evaluated_at=TS_PLUS_10),
        evaluated_at=TS_PLUS_10,
    )
    assert snapshot.status is cl5.AvailabilityStatus.READY
    assert snapshot.broker_total_cash.minor_units == 0
    assert snapshot.free_investable_cash.minor_units == 0


@pytest.mark.parametrize("status", ["IN_FLIGHT", "SUBMITTED", "UNCERTAIN"])
@pytest.mark.parametrize("blocked_units", ["0", "20", "30", "40"])
def test_ambiguous_overlap_never_emits_free_cash(
    opened_ledger: bytes, status: str, blocked_units: str
) -> None:
    money_units = str(100 - int(blocked_units))
    positions = _positions(
        response=_positions_response(
            money_units=money_units,
            blocked_units=blocked_units,
        )
    )
    snapshot = _snapshot(
        opened_ledger,
        positions=positions,
        reservations=_reservations(status),
    )
    assert snapshot.status is cl5.AvailabilityStatus.MANUAL_REVIEW_REQUIRED
    assert (
        snapshot.availability_reason
        is cl5.AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN
    )
    assert (
        snapshot.overlap_disposition
        is cl5.OverlapDisposition.AMBIGUOUS_PROVIDER_OVERLAP
    )
    assert snapshot.free_investable_cash is None


def test_disposition_precedence_and_insufficient_cash(opened_ledger: bytes) -> None:
    mismatch = _positions(response=_positions_response(money_units="79"))
    result = _snapshot(opened_ledger, positions=mismatch)
    assert result.availability_reason is cl5.AvailabilityReason.BROKER_VIEW_MISMATCH
    foreign = _positions(response=_positions_response(foreign=True))
    result = _snapshot(opened_ledger, positions=foreign)
    assert result.availability_reason is cl5.AvailabilityReason.FOREIGN_CASH_PRESENT
    insufficient = _snapshot(
        opened_ledger,
        reservations=_reservations("QUEUED", reserved_kopecks=8_001),
    )
    assert (
        insufficient.availability_reason
        is cl5.AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS
    )
    assert insufficient.free_investable_cash is None
    not_ready = _reconciliation(opened_ledger, total_units="101")
    result = _snapshot(
        opened_ledger,
        reconciliation=not_ready,
        positions=mismatch,
        reservations=_reservations("IN_FLIGHT"),
    )
    assert result.availability_reason is cl5.AvailabilityReason.CL4_NOT_READY


def test_freshness_skew_and_future_boundaries(opened_ledger: bytes) -> None:
    stale_positions = _positions(as_of=TS, evaluated_at=TS)
    result = _snapshot(
        opened_ledger,
        positions=stale_positions,
        evaluated_at=TS_PLUS_121,
    )
    assert result.availability_reason is cl5.AvailabilityReason.BROKER_PROOF_STALE
    stale_reservations = _reservations(evaluated_at=TS)
    fresh_positions = _positions(as_of=TS_PLUS_121, evaluated_at=TS_PLUS_121)
    fresh_reconciliation = _reconciliation(
        opened_ledger, as_of=TS_PLUS_121, evaluated_at=TS_PLUS_121
    )
    result = _snapshot(
        opened_ledger,
        reconciliation=fresh_reconciliation,
        positions=fresh_positions,
        reservations=stale_reservations,
        evaluated_at=TS_PLUS_121,
    )
    assert result.availability_reason is cl5.AvailabilityReason.CENTRAL_PROJECTION_STALE
    skewed_positions = _positions(as_of=TS_PLUS_11, evaluated_at=TS_PLUS_11)
    result = _snapshot(
        opened_ledger,
        positions=skewed_positions,
        evaluated_at=TS_PLUS_11,
    )
    assert result.availability_reason is cl5.AvailabilityReason.MIXED_EVIDENCE_SNAPSHOT
    at_edge = _positions(as_of=TS_PLUS_10, evaluated_at=TS_PLUS_10)
    result = _snapshot(
        opened_ledger,
        positions=at_edge,
        evaluated_at=TS_PLUS_10,
    )
    assert result.status is cl5.AvailabilityStatus.READY
    _reason(
        cl5.CL5Reason.DEPENDENCY_FROM_FUTURE,
        _snapshot,
        opened_ledger,
        positions=at_edge,
        evaluated_at=TS,
    )


def test_forged_proofs_and_cl4_binding_fail_closed(
    opened_ledger: bytes, vectors: dict[str, object]
) -> None:
    foreign = _positions(response=vectors["foreign_cash_mutation"]["response"])
    object.__setattr__(foreign, "foreign_cash_present", False)
    _reason(
        cl5.CL5Reason.PROOF_IDENTITY_INVALID,
        _snapshot,
        opened_ledger,
        positions=foreign,
    )
    projection = _reservations("QUEUED")
    object.__setattr__(projection, "projection_identity_sha256", "0" * 64)
    _reason(
        cl5.CL5Reason.CENTRAL_PROJECTION_INVALID,
        _snapshot,
        opened_ledger,
        reservations=projection,
    )
    _reason(
        cl5.CL5Reason.CL4_EVIDENCE_INVALID,
        cl5.build_cash_availability,
        opened_ledger + b" ",
        _reconciliation(opened_ledger),
        _positions(),
        _reservations(),
        evaluated_at=TS,
        identity_key=KEY,
    )


def test_immutable_deterministic_and_privacy_safe(opened_ledger: bytes) -> None:
    first = _snapshot(opened_ledger, reservations=_reservations("QUEUED"))
    second = _snapshot(opened_ledger, reservations=_reservations("QUEUED"))
    assert first == second
    assert first.canonical_bytes == second.canonical_bytes
    assert first.sha256 == second.sha256
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.status = cl5.AvailabilityStatus.BLOCKED
    error = _reason(
        cl5.CL5Reason.ACCOUNT_SCOPE_INVALID,
        _positions,
        account_scope="f" * 64,
    )
    text = f"{error!s} {error!r} {dict(error.evidence)!r}"
    assert RAW_ACCOUNT not in text
    assert "token" not in text.lower()
    assert KEY.hex() not in text


def test_module_has_no_io_runtime_or_central_mutation_authority() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imports & {
        "asyncio",
        "httpx",
        "os",
        "pathlib",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "urllib",
    }
    forbidden_calls = {
        "accept_from_now_opening",
        "append_correction_bundle",
        "append_observation",
        "append_status_event",
        "append_transaction",
        "cancel_queued",
        "dispatch_next",
        "enqueue",
        "evaluate",
        "mark_reconciled",
        "mark_submitted",
        "mark_uncertain",
        "open",
        "post_order",
        "replace_intent",
        "save",
        "transition",
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    calls.update(
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )
    assert not calls & forbidden_calls


def test_ready_build_has_zero_runtime_authority_calls(
    monkeypatch: pytest.MonkeyPatch,
    opened_ledger: bytes,
) -> None:
    reconciliation = _reconciliation(opened_ledger)
    positions = _positions()
    reservations = _reservations("QUEUED")
    before = (
        opened_ledger,
        reconciliation.canonical_bytes,
        positions.canonical_bytes,
        reservations.canonical_bytes,
    )
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        calls.append("forbidden")
        raise AssertionError("CL5 crossed a mutation/runtime authority boundary")

    for owner, names in (
        (
            central.CentralOrderManager,
            (
                "enqueue",
                "admit_portfolio",
                "reauthorize_queued",
                "prepare_next",
                "mark_submitted",
                "mark_uncertain",
                "mark_pre_submit_failed",
                "mark_submission_rejected",
                "cancel_queued",
                "mark_reconciled",
                "recover_after_restart",
            ),
        ),
        (
            persistence.CashLedgerStore,
            (
                "append_observation",
                "append_status_event",
                "append_transaction",
                "append_correction_bundle",
            ),
        ),
        (
            portfolio_repository.PortfolioRepository,
            ("load", "load_optional", "locked_snapshot", "save"),
        ),
        (
            risk_runtime.RiskRuntimeAdapter,
            ("evaluate", "dispatch_authorization_guard"),
        ),
        (sandbox_execution_adapter.SandboxExecutionAdapter, ("dispatch_next",)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(broker, "collect_tbank_operations", forbidden)

    snapshot = cl5.build_cash_availability(
        opened_ledger,
        reconciliation,
        positions,
        reservations,
        evaluated_at=TS,
        identity_key=KEY,
    )
    assert snapshot.status is cl5.AvailabilityStatus.READY
    assert calls == []
    assert before == (
        opened_ledger,
        reconciliation.canonical_bytes,
        positions.canonical_bytes,
        reservations.canonical_bytes,
    )


def test_exact_successor_custody_and_three_path_delta() -> None:
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        assert event_path is not None
        pull_request = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))[
            "pull_request"
        ]
        expected = {
            "agent/v3-10-clean-cl5-contract-freeze": (
                ACCEPTED_CONTRACT_HEAD,
                2,
                3,
            ),
            "program/v3-10-v4-stable-line": (STABLE_PREDECESSOR, 4, 4),
        }.get(pull_request["base"]["ref"])
        assert expected is not None
        expected_base, expected_commits, expected_files = expected
        assert pull_request["base"]["sha"] == expected_base
        assert pull_request["base"]["repo"]["full_name"] == (
            "baimleriv/unified-portfolio-system"
        )
        assert pull_request["head"]["ref"] == ("agent/v3-10-clean-cl5-implementation")
        assert pull_request["head"]["repo"]["full_name"] == (
            "baimleriv/unified-portfolio-system"
        )
        assert pull_request["commits"] == expected_commits
        assert pull_request["changed_files"] == expected_files
        expected_paths = set(IMPLEMENTATION_PATHS)
        if expected_base == STABLE_PREDECESSOR:
            expected_paths.add(
                "docs/project/V3_10_CL5_CASH_AVAILABILITY_CONTRACT_RU.md"
            )
        changed = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                expected_base,
                pull_request["head"]["sha"],
            ],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.splitlines()
        assert set(changed) == expected_paths
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        commit_text = subprocess.run(
            ["git", "cat-file", "-p", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        parents = [
            line.removeprefix("parent ")
            for line in commit_text.splitlines()
            if line.startswith("parent ")
        ]
        head_commit_text = subprocess.run(
            ["git", "cat-file", "-p", pull_request["head"]["sha"]],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        head_parents = [
            line.removeprefix("parent ")
            for line in head_commit_text.splitlines()
            if line.startswith("parent ")
        ]
        assert head == os.environ.get("GITHUB_SHA")
        assert parents == [expected_base, pull_request["head"]["sha"]]
        assert head_parents == [ORIGINAL_IMPLEMENTATION_HEAD]
        return

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    merge_base = subprocess.run(
        ["git", "merge-base", ACCEPTED_CONTRACT_HEAD, "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert merge_base == ACCEPTED_CONTRACT_HEAD
    if head != ORIGINAL_IMPLEMENTATION_HEAD:
        parent = subprocess.run(
            ["git", "rev-parse", "HEAD^"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        assert parent == ORIGINAL_IMPLEMENTATION_HEAD
        counts = subprocess.run(
            [
                "git",
                "rev-list",
                "--left-right",
                "--count",
                f"{ACCEPTED_CONTRACT_HEAD}...HEAD",
            ],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.split()
        assert counts == ["0", "2"]
    changed = subprocess.run(
        ["git", "diff", "--name-only", ACCEPTED_CONTRACT_HEAD],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    assert set(changed) == IMPLEMENTATION_PATHS
    contract = "docs/project/V3_10_CL5_CASH_AVAILABILITY_CONTRACT_RU.md"
    immutable = subprocess.run(
        ["git", "diff", "--quiet", ACCEPTED_CONTRACT_HEAD, "--", contract],
        cwd=ROOT,
        check=False,
    )
    assert immutable.returncode == 0
    stable_merge_base = subprocess.run(
        ["git", "merge-base", STABLE_PREDECESSOR, "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert stable_merge_base == STABLE_PREDECESSOR
    cumulative = subprocess.run(
        ["git", "diff", "--name-only", f"{STABLE_PREDECESSOR}..HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    assert set(cumulative) == IMPLEMENTATION_PATHS | {contract}
