from __future__ import annotations

import copy
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
from trading_robot import sandbox_execution_adapter, tbank_sandbox

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
MODULE_PATH = CURRENT / "trading_robot" / "cash_availability.py"
FIXTURE_PATH = CURRENT / "tests" / "fixtures" / "v3_10_cash_availability_vectors.json"
ACCEPTED_CONTRACT_HEAD = "e1dc8ab570abaa7547714951423749e2986fe31a"
ACCEPTED_IMPLEMENTATION_HEAD = "eedfc112befa23d73ddd2dfda280cdec35a003d5"
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


def _withdraw_response(
    *,
    money_units: str = "80",
    money_nano: int = 0,
    blocked_units: str = "20",
    blocked_nano: int = 0,
    guarantee_units: str | None = None,
    include_money: bool = True,
    include_blocked: bool = True,
    foreign: bool = False,
) -> dict[str, object]:
    money = [_money_value(money_units, money_nano)] if include_money else []
    blocked = (
        [_money_value(blocked_units, blocked_nano)] if include_blocked else []
    )
    guarantee = (
        [] if guarantee_units is None else [_money_value(guarantee_units)]
    )
    if foreign:
        money.append(_money_value("1", currency="USD"))
    return {
        "blocked": blocked,
        "blockedGuarantee": guarantee,
        "money": money,
    }


def _response_with_canonical_size(target: int) -> dict[str, object]:
    response = _withdraw_response()
    delta = target - len(_canonical(response))
    assert delta >= 0
    response["money"][0]["units"] = "1" * (len("80") + delta)
    assert len(_canonical(response)) == target
    return response


def _withdraw(
    *,
    response: object | None = None,
    as_of: str = TS,
    evaluated_at: str = TS,
    account_scope: str = ACCOUNT_SCOPE,
    complete: bool = True,
) -> cl5.BrokerWithdrawLimitsCashProof:
    observation = _transport_observation(
        _withdraw_response() if response is None else response
    )
    return cl5.build_broker_withdraw_limits_cash_proof(
        observation,
        account_scope_sha256=account_scope,
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=as_of,
        evaluated_at=evaluated_at,
        response_complete=complete,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )


def _transport_observation(
    response: object,
    *,
    account_id: str = RAW_ACCOUNT,
) -> cl5.WithdrawLimitsTransportObservation:
    class StaticTransport:
        def _post(
            self,
            service: str,
            method: str,
            payload: dict[str, object],
        ) -> object:
            assert service == "SandboxService"
            assert method == "GetSandboxWithdrawLimits"
            assert payload == {"accountId": account_id}
            return response

    return tbank_sandbox.TBankSandboxClient.get_withdraw_limits(
        StaticTransport(), account_id
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
    withdraw_limits: cl5.BrokerWithdrawLimitsCashProof | None = None,
    reservations: cl5.CentralReservationProjection | None = None,
    evaluated_at: str = TS,
) -> cl5.CashAvailabilitySnapshot:
    return cl5.build_cash_availability(
        opened_ledger,
        _reconciliation(opened_ledger) if reconciliation is None else reconciliation,
        _withdraw() if withdraw_limits is None else withdraw_limits,
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


def test_v2_public_surface_and_signatures_are_exact() -> None:
    assert set(cl5.__all__) == {
        "AvailabilityStatus", "AvailabilityReason", "OverlapDisposition",
        "CL5Reason", "CL5Error", "BrokerPositionsCashProof",
        "BrokerWithdrawLimitsCashProof", "WithdrawLimitsTransportObservation",
        "CentralReservationProjection", "CashAvailabilitySnapshot",
        "build_broker_positions_cash_proof",
        "build_broker_withdraw_limits_cash_proof",
        "project_central_reservations", "build_cash_availability",
    }
    assert list(inspect.signature(
        cl5.build_broker_withdraw_limits_cash_proof
    ).parameters) == [
        "observation", "account_scope_sha256", "environment", "as_of",
        "evaluated_at", "response_complete", "identity_key", "identity_key_id",
    ]
    assert list(inspect.signature(cl5.build_cash_availability).parameters) == [
        "ledger_export_bytes", "reconciliation", "withdraw_limits",
        "reservations", "evaluated_at", "identity_key",
    ]


def test_contract_owned_withdraw_limits_identity_kat(
    vectors: dict[str, object],
) -> None:
    assert _scope() == ACCOUNT_SCOPE == vectors["account_scope"]["sha256"]
    proof = _withdraw(response=vectors["withdraw_limits"]["response"])
    assert proof.response_canonical_sha256 == (
        "2e36fc5bc9beaa9fc3eeac990897f6ab894801c09b0a14b65ad1c9d39d919319"
    )
    assert proof.observation_identity_sha256 == (
        "b21b64af91a4976216d45978a73cbbafb2033c782e601e191905d1cd0db1f24c"
    )
    assert proof.proof_identity_sha256 == (
        "de2b207c43a7dfea9acb9f7a792a24e8375ab8072cfad4fa8f04e63f536d40e5"
    )
    assert proof.sha256 == (
        "f5e03b70096a4e0f9756098d7a1a4e7550f6976aa5017d92aa25ec0f8f812f5b"
    )
    assert proof.canonical_bytes.decode("ascii") == vectors["withdraw_limits"][
        "proof_canonical_ascii"
    ]


def test_contract_owned_ready_snapshot_v2_kat(vectors: dict[str, object]) -> None:
    proof = _withdraw(response=vectors["withdraw_limits"]["response"])
    queued = ledger.Money(currency="RUB", minor_units=30_000_000_000)
    zero = ledger.Money(currency="RUB", minor_units=0)
    snapshot = cl5.CashAvailabilitySnapshot(
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        currency="RUB", evaluated_at=TS,
        cl4_reconciliation_evaluated_at=TS, broker_cash_as_of=TS,
        broker_withdraw_limits_as_of=TS,
        central_projection_evaluated_at=TS,
        reconciliation_sha256="11" * 32,
        cl4_adoption_candidate_sha256="22" * 32,
        ledger_export_sha256="33" * 32, ledger_revision=5,
        ledger_head_sha256="44" * 32,
        broker_withdraw_limits_cash_proof_sha256=proof.sha256,
        broker_total_cash=ledger.Money(currency="RUB", minor_units=100_000_000_000),
        broker_withdraw_blocked_cash=ledger.Money(currency="RUB", minor_units=20_000_000_000),
        broker_withdraw_blocked_guarantee_cash=zero,
        broker_withdrawable_cash_lower_bound=ledger.Money(currency="RUB", minor_units=80_000_000_000),
        central_reservation_projection_sha256="24505cbf486c30f5c85a2bb456db3587dc3d97f8ce9f5afc96f9b1405f511048",
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
    assert snapshot.version == 2
    assert snapshot.canonical_bytes.decode("ascii") == vectors["snapshot_v2"]["canonical_ascii"]
    assert snapshot.sha256 == "2adb09081ff97c8441f3a5444b11b2dd2afbeeacfbbed1cdabf467e85d540190"


@pytest.mark.parametrize(
    ("total", "available", "blocked", "guarantee", "queued", "expected"),
    [
        ("100", "80", "20", None, 3_000, 50_000_000_000),
        ("100", "70", "20", None, 3_000, 40_000_000_000),
        ("100", "110", "20", None, 0, 100_000_000_000),
        ("100", "70", "20", "10", 1_000, 60_000_000_000),
    ],
)
def test_q7_bvm_01_to_05_conservative_vectors(
    opened_ledger: bytes, total: str, available: str, blocked: str,
    guarantee: str | None, queued: int, expected: int,
) -> None:
    snapshot = _snapshot(
        opened_ledger,
        reconciliation=_reconciliation(opened_ledger, total_units=total),
        withdraw_limits=_withdraw(response=_withdraw_response(
            money_units=available, blocked_units=blocked,
            guarantee_units=guarantee,
        )),
        reservations=(
            _reservations() if queued == 0
            else _reservations("QUEUED", reserved_kopecks=queued)
        ),
    )
    assert snapshot.status is cl5.AvailabilityStatus.READY
    assert snapshot.free_investable_cash.minor_units == expected
    assert snapshot.broker_withdrawable_cash_lower_bound.minor_units == min(
        int(total), int(available)
    ) * 1_000_000_000
    assert snapshot.broker_withdraw_blocked_cash.minor_units == int(blocked) * 1_000_000_000
    assert snapshot.broker_withdraw_blocked_guarantee_cash.minor_units == (
        0 if guarantee is None else int(guarantee) * 1_000_000_000
    )


def test_q7_bvm_06_to_09_presence_flags_bind_absent_and_zero(
    opened_ledger: bytes,
) -> None:
    absent = _withdraw(response=_withdraw_response(
        include_money=False, include_blocked=False
    ))
    explicit = _withdraw(response=_withdraw_response(
        money_units="0", blocked_units="0", guarantee_units="0"
    ))
    assert (absent.available_rub_present, absent.blocked_rub_present,
            absent.blocked_guarantee_rub_present) == (False, False, False)
    assert (explicit.available_rub_present, explicit.blocked_rub_present,
            explicit.blocked_guarantee_rub_present) == (True, True, True)
    assert absent.proof_identity_sha256 != explicit.proof_identity_sha256
    snapshot = _snapshot(opened_ledger, withdraw_limits=absent)
    assert snapshot.status is cl5.AvailabilityStatus.READY
    assert snapshot.broker_withdrawable_cash_lower_bound.minor_units == 0
    assert snapshot.free_investable_cash.minor_units == 0


@pytest.mark.parametrize("field", ["money", "blocked", "blockedGuarantee"])
@pytest.mark.parametrize("currency", ["RUB", "rub"])
@pytest.mark.parametrize(("units", "nano"), [("7", 500_000_000), ("0", 0)])
def test_withdraw_limits_exact_rub_wire_aliases_preserve_raw_evidence(
    field: str,
    currency: str,
    units: str,
    nano: int,
) -> None:
    response: dict[str, object] = {
        "blocked": [],
        "blockedGuarantee": [],
        "money": [],
    }
    response[field] = [_money_value(units, nano, currency=currency)]
    original = copy.deepcopy(response)
    calls: list[tuple[str, str, dict[str, object]]] = []

    class FakeHttpTransport:
        @staticmethod
        def _post(
            service: str,
            method: str,
            payload: dict[str, object],
        ) -> object:
            calls.append((service, method, copy.deepcopy(payload)))
            return response

    observation = tbank_sandbox.TBankSandboxClient.get_withdraw_limits(
        FakeHttpTransport(), RAW_ACCOUNT
    )
    proof = cl5.build_broker_withdraw_limits_cash_proof(
        observation,
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=TS,
        evaluated_at=TS,
        response_complete=True,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )

    assert calls == [
        ("SandboxService", "GetSandboxWithdrawLimits", {"accountId": RAW_ACCOUNT})
    ]
    assert response == original
    assert observation.response == original
    assert proof.response_canonical_sha256 == hashlib.sha256(
        _canonical(original)
    ).hexdigest()
    projections = {
        "money": (proof.available_rub, proof.available_rub_present),
        "blocked": (proof.blocked_rub, proof.blocked_rub_present),
        "blockedGuarantee": (
            proof.blocked_guarantee_rub,
            proof.blocked_guarantee_rub_present,
        ),
    }
    projected, present = projections[field]
    assert projected == ledger.Money.from_units_nano(
        units=int(units), nano=nano, currency="RUB"
    )
    assert present is True
    assert proof.foreign_cash_present is False


def test_withdraw_limits_rub_alias_changes_wire_identity_not_money_projection() -> None:
    upper = _withdraw(
        response={
            "blocked": [],
            "blockedGuarantee": [],
            "money": [_money_value("8", 25, currency="RUB")],
        }
    )
    lower = _withdraw(
        response={
            "blocked": [],
            "blockedGuarantee": [],
            "money": [_money_value("8", 25, currency="rub")],
        }
    )
    assert lower.available_rub == upper.available_rub
    assert lower.available_rub_present is upper.available_rub_present is True
    assert lower.foreign_cash_present is upper.foreign_cash_present is False
    assert lower.response_canonical_sha256 != upper.response_canonical_sha256
    assert lower.observation_identity_sha256 != upper.observation_identity_sha256
    assert lower.proof_identity_sha256 != upper.proof_identity_sha256


@pytest.mark.parametrize("field", ["money", "blocked", "blockedGuarantee"])
@pytest.mark.parametrize(
    "currencies",
    [("RUB", "RUB"), ("rub", "rub"), ("RUB", "rub")],
)
def test_q7_bvm_10_semantic_duplicate_rub_aliases_reject(
    field: str,
    currencies: tuple[str, str],
) -> None:
    response: dict[str, object] = {
        "blocked": [],
        "blockedGuarantee": [],
        "money": [],
    }
    response[field] = [
        _money_value("1", currency=currencies[0]),
        _money_value("1", currency=currencies[1]),
    ]
    _reason(
        cl5.CL5Reason.WITHDRAW_LIMITS_RESPONSE_INVALID,
        _withdraw,
        response=response,
    )


@pytest.mark.parametrize(
    "currency",
    ["Rub", "rUb", " rub", "rub ", "ＲＵＢ", "РУБ"],
)
def test_withdraw_limits_non_alias_currency_tokens_remain_foreign(
    opened_ledger: bytes,
    currency: str,
) -> None:
    response = {
        "blocked": [],
        "blockedGuarantee": [],
        "money": [_money_value("1", currency=currency)],
    }
    proof = _withdraw(response=response)
    assert proof.available_rub.minor_units == 0
    assert proof.available_rub_present is False
    assert proof.foreign_cash_present is True
    snapshot = _snapshot(opened_ledger, withdraw_limits=proof)
    assert snapshot.status is cl5.AvailabilityStatus.BLOCKED
    assert snapshot.availability_reason is (
        cl5.AvailabilityReason.WITHDRAW_LIMITS_FOREIGN_CASH_PRESENT
    )


def test_q7_bvm_11_foreign_nonzero_blocks_but_zero_does_not(
    opened_ledger: bytes,
) -> None:
    foreign = _snapshot(opened_ledger, withdraw_limits=_withdraw(
        response=_withdraw_response(foreign=True)
    ))
    assert foreign.status is cl5.AvailabilityStatus.BLOCKED
    assert foreign.availability_reason is cl5.AvailabilityReason.WITHDRAW_LIMITS_FOREIGN_CASH_PRESENT
    response = _withdraw_response()
    response["money"].append(_money_value("0", currency="USD"))
    zero_foreign = _snapshot(opened_ledger, withdraw_limits=_withdraw(response=response))
    assert zero_foreign.status is cl5.AvailabilityStatus.READY


def test_q7_bvm_12_exact_container_scalar_and_equality_types_reject() -> None:
    class StringSubclass(str):
        pass
    class DictSubclass(dict):
        pass
    class CustomEquality:
        def __eq__(self, _other):
            return True
    responses = (
        {"money": (), "blocked": [], "blockedGuarantee": []},
        {"money": [], "blocked": [], "blockedGuarantee": False},
        {"money": [{"currency": "RUB", "units": "1", "nano": True}], "blocked": [], "blockedGuarantee": []},
        {"money": [{"currency": StringSubclass("RUB"), "units": "1", "nano": 0}], "blocked": [], "blockedGuarantee": []},
        {"money": [{"currency": CustomEquality(), "units": "1", "nano": 0}], "blocked": [], "blockedGuarantee": []},
    )
    for response in responses:
        with pytest.raises(cl5.CL5Error):
            _withdraw(response=response)
    with pytest.raises(cl5.CL5Error):
        cl5.WithdrawLimitsTransportObservation(
            RAW_ACCOUNT, "SandboxService", "GetSandboxWithdrawLimits", DictSubclass()
        )


def test_q7_bvm_13_21_22_request_provenance_and_observation_gate() -> None:
    mismatch = _transport_observation(
        _withdraw_response(), account_id="different-account"
    )
    kwargs = {
        "account_scope_sha256": ACCOUNT_SCOPE,
        "environment": broker.BrokerEnvironment.SANDBOX,
        "as_of": TS,
        "evaluated_at": TS,
        "response_complete": True,
        "identity_key": KEY,
        "identity_key_id": KEY_ID,
    }
    first_response = mismatch.response
    first_response["money"].clear()
    assert mismatch.response["money"] != []
    assert RAW_ACCOUNT not in repr(mismatch)
    with pytest.raises(AttributeError):
        mismatch.service = "OperationsService"
    _reason(
        cl5.CL5Reason.WITHDRAW_LIMITS_REQUEST_SCOPE_MISMATCH,
        cl5.build_broker_withdraw_limits_cash_proof, mismatch, **kwargs,
    )
    with pytest.raises(cl5.CL5Error) as same_account_forgery:
        cl5.WithdrawLimitsTransportObservation(
            RAW_ACCOUNT,
            "SandboxService",
            "GetSandboxWithdrawLimits",
            _withdraw_response(),
        )
    assert same_account_forgery.value.reason is (
        cl5.CL5Reason.WITHDRAW_LIMITS_OBSERVATION_INVALID
    )
    for service, method in (
        ("OperationsService", "GetSandboxWithdrawLimits"),
        ("SandboxService", "GetSandboxPositions"),
    ):
        with pytest.raises(cl5.CL5Error) as invalid_observation:
            cl5.WithdrawLimitsTransportObservation(
                RAW_ACCOUNT, service, method, _withdraw_response()
            )
        assert invalid_observation.value.reason is (
            cl5.CL5Reason.WITHDRAW_LIMITS_OBSERVATION_INVALID
        )


def test_q7_bvm_14_proof_tamper_rejects(opened_ledger: bytes) -> None:
    proof = _withdraw()
    object.__setattr__(proof, "available_rub_present", False)
    _reason(cl5.CL5Reason.PROOF_IDENTITY_INVALID, _snapshot, opened_ledger,
            withdraw_limits=proof)


def test_q7_bvm_15_16_stale_and_cross_proof_skew_block(
    opened_ledger: bytes,
) -> None:
    stale = _snapshot(opened_ledger, evaluated_at=TS_PLUS_121)
    assert stale.availability_reason is cl5.AvailabilityReason.BROKER_PROOF_STALE
    skewed = _snapshot(
        opened_ledger,
        withdraw_limits=_withdraw(as_of=TS_PLUS_11, evaluated_at=TS_PLUS_11),
        evaluated_at=TS_PLUS_11,
    )
    assert skewed.availability_reason is cl5.AvailabilityReason.MIXED_EVIDENCE_SNAPSHOT


@pytest.mark.parametrize("status", ["IN_FLIGHT", "SUBMITTED", "UNCERTAIN"])
def test_q7_bvm_17_ambiguous_reservations_remain_manual_review(
    opened_ledger: bytes, status: str,
) -> None:
    snapshot = _snapshot(opened_ledger, reservations=_reservations(status))
    assert snapshot.status is cl5.AvailabilityStatus.MANUAL_REVIEW_REQUIRED
    assert snapshot.availability_reason is cl5.AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN
    assert snapshot.free_investable_cash is None
    assert snapshot.broker_withdrawable_cash_lower_bound.minor_units == 80_000_000_000


def test_q7_bvm_18_queued_reservation_subtracts_exactly_once(
    opened_ledger: bytes,
) -> None:
    snapshot = _snapshot(opened_ledger, reservations=_reservations("QUEUED"))
    assert snapshot.broker_withdrawable_cash_lower_bound.minor_units == 80_000_000_000
    assert snapshot.free_investable_cash.minor_units == 50_000_000_000


def test_q7_bvm_19_builder_is_pure_and_cannot_reach_provider_post(
    opened_ledger: bytes, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    def forbidden(*_args, **_kwargs):
        calls.append("mutation")
        raise AssertionError("mutation")
    for owner, names in (
        (central.CentralOrderManager, ("enqueue", "prepare_next", "mark_submitted")),
        (persistence.CashLedgerStore, ("append_transaction", "append_observation")),
        (sandbox_execution_adapter.SandboxExecutionAdapter, ("dispatch_next",)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    assert _snapshot(opened_ledger).status is cl5.AvailabilityStatus.READY
    assert calls == []


def test_q7_bvm_20_errors_are_privacy_safe() -> None:
    error = _reason(
        cl5.CL5Reason.WITHDRAW_LIMITS_REQUEST_SCOPE_MISMATCH,
        _withdraw, account_scope="f" * 64,
    )
    rendered = f"{error!s} {error!r} {dict(error.evidence)!r}"
    assert RAW_ACCOUNT not in rendered
    assert KEY.hex() not in rendered


def test_q7_bvm_23_version_one_aliases_reject(opened_ledger: bytes) -> None:
    legacy = cl5.build_broker_positions_cash_proof(
        {"accountId": RAW_ACCOUNT, "blocked": [_money_value("20")],
         "limitsLoadingInProgress": False, "money": [_money_value("80")]},
        account_scope_sha256=ACCOUNT_SCOPE,
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=TS, evaluated_at=TS, response_complete=True,
        identity_key=KEY, identity_key_id=KEY_ID,
    )
    _reason(
        cl5.CL5Reason.TYPE_INVALID, cl5.build_cash_availability,
        opened_ledger, _reconciliation(opened_ledger), legacy, _reservations(),
        evaluated_at=TS, identity_key=KEY,
    )
    proof = _withdraw()
    object.__setattr__(proof, "version", 2)
    _reason(cl5.CL5Reason.VERSION_UNSUPPORTED, _snapshot, opened_ledger,
            withdraw_limits=proof)


def test_response_schema_incomplete_money_and_bounds_fail_closed() -> None:
    _reason(cl5.CL5Reason.WITHDRAW_LIMITS_INCOMPLETE, _withdraw, complete=False)
    responses = (
        {"money": [], "blocked": []},
        {"money": [], "blocked": [], "blockedGuarantee": [], "unknown": []},
        {"money": [{"currency": "RUB", "units": "01", "nano": 0}], "blocked": [], "blockedGuarantee": []},
        {"money": [{"currency": "RUB", "units": "1", "nano": 1_000_000_000}], "blocked": [], "blockedGuarantee": []},
    )
    for response in responses:
        _reason(cl5.CL5Reason.WITHDRAW_LIMITS_RESPONSE_INVALID,
                _withdraw, response=response)


def test_immutable_deterministic_snapshot_v2(opened_ledger: bytes) -> None:
    first = _snapshot(opened_ledger, reservations=_reservations("QUEUED"))
    second = _snapshot(opened_ledger, reservations=_reservations("QUEUED"))
    assert first == second
    assert first.version == 2
    assert first.canonical_bytes == second.canonical_bytes
    assert first.sha256 == second.sha256
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.status = cl5.AvailabilityStatus.BLOCKED


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
                3,
                3,
            ),
            "program/v3-10-v4-stable-line": (STABLE_PREDECESSOR, 5, 4),
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
        assert pull_request["head"]["sha"] != ACCEPTED_IMPLEMENTATION_HEAD
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
        assert head == os.environ.get("GITHUB_SHA")
        assert parents == [expected_base, pull_request["head"]["sha"]]
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
    if head != ACCEPTED_IMPLEMENTATION_HEAD:
        parent = subprocess.run(
            ["git", "rev-parse", "HEAD^"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        assert parent == ACCEPTED_IMPLEMENTATION_HEAD
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
        assert counts == ["0", "3"]
        correction_paths = subprocess.run(
            ["git", "diff", "--name-only", "HEAD^..HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.splitlines()
        assert correction_paths == ["current/tests/test_v3_10_cash_availability.py"]
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
