from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from trading_robot import broker_read_adapters as broker
from trading_robot import cash_ledger_domain as ledger
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import cash_ledger_persistence as persistence

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
MODULE_PATH = CURRENT / "trading_robot" / "cash_ledger_opening_reconciliation.py"
FIXTURE_PATH = (
    CURRENT
    / "tests"
    / "fixtures"
    / "v3_10_cash_ledger_opening_reconciliation_vectors.json"
)
CL2_FIXTURE_PATH = CURRENT / "tests" / "fixtures" / "v3_10_cash_ledger_persistence_vectors.json"
ACCEPTED_CONTRACT_HEAD = "03c73362691a4efe325326de214661e5dd93d163"
CL3_PREDECESSOR = "4340c5d517dcece4f7db20b7cfc21e602c3efddc"
ACCOUNT = "1" * 64
OTHER_ACCOUNT = "2" * 64
KEY = bytes(range(32))
KEY_ID = "CL4_TEST_KEY"
AS_OF = "2026-01-02T03:04:05.123456789Z"
EVALUATED_AT = "2026-01-02T03:04:06.123456789Z"
LATER = "2026-01-02T03:10:00.000000000Z"
RESPONSE = {
    "totalAmountCurrencies": {
        "currency": "RUB",
        "nano": 500_000_000,
        "units": "123",
    }
}


@pytest.fixture(scope="module")
def fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="ascii"))


@pytest.fixture(scope="module")
def cl2_vectors() -> dict[str, dict[str, object]]:
    document = json.loads(CL2_FIXTURE_PATH.read_text(encoding="ascii"))
    return {item["id"]: item for item in document["vectors"]}


def _proof(
    *,
    response: object = RESPONSE,
    account: str = ACCOUNT,
    key: bytes = KEY,
    key_id: str = KEY_ID,
    as_of: str = AS_OF,
    evaluated_at: str = EVALUATED_AT,
) -> cl4.BrokerCashProof:
    return cl4.build_broker_cash_proof(
        response,
        account_scope_sha256=account,
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=as_of,
        evaluated_at=evaluated_at,
        response_complete=True,
        identity_key=key,
        identity_key_id=key_id,
    )


def _new_store(
    tmp_path: Path,
    *,
    descriptors: tuple[persistence.CodecDescriptor, ...] = (),
    fault_injector: object | None = None,
) -> persistence.CashLedgerStore:
    return persistence.CashLedgerStore.create(
        tmp_path / "store",
        (cl4.CL4_OPENING_CODEC, *descriptors),
        fault_injector=fault_injector,
    )


def _plan(
    store: persistence.CashLedgerStore,
    proof: cl4.BrokerCashProof | None = None,
) -> cl4.OpeningPlan:
    return cl4.prepare_from_now_opening(
        store.export_bytes(),
        _proof() if proof is None else proof,
        evaluated_at=EVALUATED_AT,
        identity_key=KEY,
    )


def _accept(
    store: persistence.CashLedgerStore,
    plan: cl4.OpeningPlan,
    *,
    evaluated_at: str = EVALUATED_AT,
) -> cl4.OpeningAcceptance:
    return cl4.accept_from_now_opening(
        store,
        plan,
        confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}",
        evaluated_at=evaluated_at,
        identity_key=KEY,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _reason(reason: cl4.CL4Reason, callable_: object, *args: object, **kwargs: object) -> cl4.CL4Error:
    try:
        callable_(*args, **kwargs)
    except cl4.CL4Error as error:
        assert error.reason is reason
        assert str(error) == reason.value
        assert error.__cause__ is None
        assert error.__context__ is None
        return error
    raise AssertionError(f"expected {reason.value}")


def _descriptor(cl2_vectors: dict[str, dict[str, object]]) -> persistence.CodecDescriptor:
    return persistence.CodecDescriptor.from_canonical_bytes(
        cl2_vectors["codec-descriptor-synthetic"]["canonical_json_ascii"]
    )


def _observation(
    cl2_vectors: dict[str, dict[str, object]],
    descriptor: persistence.CodecDescriptor,
    identifier: str,
) -> persistence.InboxObservation:
    return persistence.InboxObservation.from_canonical_bytes(
        cl2_vectors[identifier]["canonical_json_ascii"],
        [descriptor],
    )


def _transaction(
    cl2_vectors: dict[str, dict[str, object]], identifier: str
) -> ledger.LedgerTransaction:
    return ledger.LedgerTransaction.from_canonical_dict(
        persistence.parse_canonical_json(
            cl2_vectors[identifier]["canonical_json_ascii"].encode("ascii")
        )
    )


def _bundle(
    cl2_vectors: dict[str, dict[str, object]], identifier: str
) -> ledger.LedgerCorrectionBundle:
    value = persistence.parse_canonical_json(
        cl2_vectors[identifier]["canonical_json_ascii"].encode("ascii")
    )
    by_sha = {
        item["sha256"]: item_id
        for item_id, item in cl2_vectors.items()
        if item["kind"] == "cl1_transaction"
    }
    return ledger.LedgerCorrectionBundle.from_canonical_dict(
        value,
        original=_transaction(cl2_vectors, by_sha[value["original_sha256"]]),
        reversal=_transaction(cl2_vectors, by_sha[value["reversal_sha256"]]),
        correction=_transaction(cl2_vectors, by_sha[value["correction_sha256"]]),
    )


def _custom_observation(
    descriptor: persistence.CodecDescriptor,
    *,
    label: str,
    account: str = ACCOUNT,
    observed_at: str = LATER,
) -> persistence.InboxObservation:
    content = {"operation_kind": label}
    content_hash = persistence.sha256_hex(persistence.canonical_json_bytes(content))
    scope = hashlib.sha256((label + "-scope").encode("ascii")).hexdigest()
    source = ledger.SourceIdentity(
        account_scope_sha256=account,
        source_kind="SYNTHETIC",
        source_scope_sha256=scope,
        source_content_sha256=content_hash,
    )
    return persistence.InboxObservation.create(
        descriptor=descriptor,
        content=content,
        source=source,
        observed_at=observed_at,
        provenance_sha256=hashlib.sha256((label + "-proof").encode("ascii")).hexdigest(),
    )


def _custom_transaction(
    observation: persistence.InboxObservation,
    *,
    minor_units: int,
    effective_at: str = LATER,
    classification: ledger.LedgerClassification = ledger.LedgerClassification.MANUAL_ADJUSTMENT,
) -> ledger.LedgerTransaction:
    counterpart = {
        ledger.LedgerClassification.DEPOSIT: ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW,
        ledger.LedgerClassification.WITHDRAWAL: ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW,
        ledger.LedgerClassification.DIVIDEND: ledger.LedgerAccount.INCOME_DIVIDEND,
        ledger.LedgerClassification.COUPON: ledger.LedgerAccount.INCOME_COUPON,
        ledger.LedgerClassification.INTEREST: ledger.LedgerAccount.INCOME_INTEREST,
        ledger.LedgerClassification.COMMISSION: ledger.LedgerAccount.EXPENSE_COMMISSION,
        ledger.LedgerClassification.TAX: ledger.LedgerAccount.EXPENSE_TAX,
        ledger.LedgerClassification.TRADE_SETTLEMENT: ledger.LedgerAccount.ASSET_TRADE_CLEARING,
        ledger.LedgerClassification.MANUAL_ADJUSTMENT: ledger.LedgerAccount.EQUITY_MANUAL_ADJUSTMENT,
    }[classification]
    money = ledger.Money(currency="RUB", minor_units=minor_units)
    return ledger.LedgerTransaction(
        classification=classification,
        effective_at=effective_at,
        source=observation.source,
        postings=(
            ledger.LedgerPosting(1, ledger.LedgerAccount.ASSET_BROKER_CASH, money),
            ledger.LedgerPosting(2, counterpart, -money),
        ),
    )


def _append_transaction(
    store: persistence.CashLedgerStore,
    observation: persistence.InboxObservation,
    transaction: ledger.LedgerTransaction,
) -> None:
    before = store.snapshot()
    store.append_observation(observation, expected_store_revision=before.store_revision)
    after = store.snapshot()
    store.append_transaction(
        transaction,
        observation.sha256,
        expected_store_revision=after.store_revision,
        expected_ledger_revision=after.ledger_revision,
    )


def test_v310_cl4_01_exact_exports_signatures_immutable_and_import_boundary() -> None:
    assert cl4.__all__ == (
        "OpeningMode",
        "ReconciliationStatus",
        "DiscrepancyKind",
        "AdoptionDisposition",
        "CL4Reason",
        "CL4Error",
        "BrokerCashProof",
        "OpeningPlan",
        "OpeningRecord",
        "LedgerCashProjection",
        "CashReconciliation",
        "AdoptionCandidate",
        "OpeningAcceptance",
        "CL4_OPENING_CODEC",
        "build_broker_cash_proof",
        "prepare_from_now_opening",
        "accept_from_now_opening",
        "project_shadow_cash",
        "reconcile_shadow_cash",
        "build_adoption_candidate",
    )
    assert {name for name in vars(cl4) if not name.startswith("_")} == set(cl4.__all__)
    expected_parameters = {
        "build_broker_cash_proof": [
            "response",
            "account_scope_sha256",
            "environment",
            "as_of",
            "evaluated_at",
            "response_complete",
            "identity_key",
            "identity_key_id",
        ],
        "prepare_from_now_opening": ["ledger_export_bytes", "proof", "evaluated_at", "identity_key"],
        "accept_from_now_opening": ["store", "plan", "confirmation", "evaluated_at", "identity_key"],
        "project_shadow_cash": ["ledger_export_bytes", "account_scope_sha256", "environment", "as_of", "identity_key"],
        "reconcile_shadow_cash": ["ledger_export_bytes", "proof", "evaluated_at", "identity_key"],
        "build_adoption_candidate": ["reconciliation", "ledger_export_bytes", "identity_key"],
    }
    for name, parameters in expected_parameters.items():
        assert list(inspect.signature(getattr(cl4, name)).parameters) == parameters
    assert inspect.signature(cl4.build_broker_cash_proof).parameters["environment"].annotation == "BrokerEnvironment"
    assert inspect.signature(cl4.accept_from_now_opening).parameters["store"].annotation == "CashLedgerStore"
    assert tuple(cl4.OpeningMode) == (cl4.OpeningMode.FROM_NOW,)
    assert tuple(cl4.ReconciliationStatus) == (
        cl4.ReconciliationStatus.MATCHED,
        cl4.ReconciliationStatus.DISCREPANCY,
        cl4.ReconciliationStatus.INCOMPLETE,
    )
    proof = _proof()
    assert dataclasses.is_dataclass(proof)
    assert proof.__slots__
    with pytest.raises((AttributeError, TypeError)):
        proof.as_of = LATER  # type: ignore[misc]
    code = (
        "import socket\n"
        "socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('network'))\n"
        "import trading_robot.cash_ledger_opening_reconciliation\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(CURRENT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=environment, check=False)
    assert result.returncode == 0, result.stderr


def test_v310_cl4_02_bounded_response_snapshot_and_canonical_identity() -> None:
    response = json.loads(json.dumps(RESPONSE))
    response["ignored"] = {"values": [1, True, None, "safe"]}
    proof = _proof(response=response)
    response["totalAmountCurrencies"]["nano"] = 1
    assert proof.cash.minor_units == 123_500_000_000
    shared: list[object] = []
    for invalid, reason in (
        ((RESPONSE,), cl4.CL4Reason.TYPE_INVALID),
        ({"totalAmountCurrencies": RESPONSE["totalAmountCurrencies"], "a": shared, "b": shared}, cl4.CL4Reason.RESPONSE_BOUNDS_EXCEEDED),
        ({"totalAmountCurrencies": {"currency": "RUB", "units": "0", "nano": 0}, "x": 1.0}, cl4.CL4Reason.RESPONSE_SCHEMA_INVALID),
        ({"totalAmountCurrencies": {"currency": "RUB", "units": "0", "nano": 0}, "x": 2**63}, cl4.CL4Reason.RESPONSE_BOUNDS_EXCEEDED),
    ):
        _reason(cl4.CL4Reason(reason), _proof, response=invalid)
    cycle: dict[str, object] = {"totalAmountCurrencies": {"currency": "RUB", "units": "0", "nano": 0}}
    cycle["cycle"] = cycle
    _reason(cl4.CL4Reason.RESPONSE_BOUNDS_EXCEEDED, _proof, response=cycle)
    nested: object = "leaf"
    for _ in range(16):
        nested = [nested]
    _reason(
        cl4.CL4Reason.RESPONSE_BOUNDS_EXCEEDED,
        _proof,
        response={"totalAmountCurrencies": {"currency": "RUB", "units": "0", "nano": 0}, "nested": nested},
    )


def test_v310_cl4_03_money_codec_exactness_and_substitutes() -> None:
    assert _proof().cash.minor_units == 123_500_000_000
    for value, reason in (
        ({"currency": "USD", "units": "1", "nano": 0}, cl4.CL4Reason.MONEY_INVALID),
        ({"currency": "RUB", "units": 1, "nano": 0}, cl4.CL4Reason.MONEY_INVALID),
        ({"currency": "RUB", "units": "1", "nano": 0.0}, cl4.CL4Reason.RESPONSE_SCHEMA_INVALID),
        ({"currency": "RUB", "units": "1", "nano": 0, "extra": 0}, cl4.CL4Reason.RESPONSE_SCHEMA_INVALID),
    ):
        _reason(reason, _proof, response={"totalAmountCurrencies": value})
    _reason(cl4.CL4Reason.RESPONSE_SCHEMA_INVALID, _proof, response={"totalAmountPortfolio": RESPONSE["totalAmountCurrencies"]})
    _reason(cl4.CL4Reason.RESPONSE_SCHEMA_INVALID, _proof, response={"money": RESPONSE["totalAmountCurrencies"]})


def test_v310_cl4_04_known_answer_hmac_forgery_and_privacy(tmp_path: Path) -> None:
    proof = _proof()
    assert proof.response_canonical_sha256 == "204dba39888167e2384d8188817a5ad4a01f132a405a9b7b06f75f76aceed3ce"
    assert proof.proof_identity_sha256 == "d21e770a4268fafb4ec39b0cc9d21f3c2155801829cdd5e106f5588dc4e4efb1"
    assert proof.sha256 == "588abe0b9d3979c300d8f681adccdf2776bc20c8dd4997e66ff8edaaa1e8010f"
    assert _proof(key=bytes(reversed(range(32)))).proof_identity_sha256 != proof.proof_identity_sha256
    assert _proof(key_id="CL4_OTHER_KEY").proof_identity_sha256 != proof.proof_identity_sha256
    with _new_store(tmp_path) as store:
        baseline = store.export_bytes()
        mutations = (
            ("cash", ledger.Money(currency="RUB", minor_units=proof.cash.minor_units + 1)),
            ("cash", ledger.Money(currency="RUB", minor_units=proof.cash.minor_units - 1)),
            ("as_of", "2026-01-02T03:04:05.123456788Z"),
            ("response_canonical_sha256", "0" * 64),
            ("account_scope_sha256", OTHER_ACCOUNT),
            ("identity_key_id", "CL4_OTHER_KEY"),
            ("proof_identity_sha256", "0" * 64),
        )
        for field, value in mutations:
            forged = dataclasses.replace(proof, **{field: value})
            _reason(
                cl4.CL4Reason.PROOF_IDENTITY_INVALID,
                cl4.prepare_from_now_opening,
                baseline,
                forged,
                evaluated_at=EVALUATED_AT,
                identity_key=KEY,
            )
        wrong_environment = copy.copy(proof)
        object.__setattr__(wrong_environment, "environment", broker.BrokerEnvironment.PRODUCTION)
        _reason(
            cl4.CL4Reason.ENVIRONMENT_UNSUPPORTED,
            cl4.prepare_from_now_opening,
            baseline,
            wrong_environment,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        v1 = copy.copy(proof)
        object.__setattr__(v1, "version", 1)
        _reason(
            cl4.CL4Reason.VERSION_UNSUPPORTED,
            cl4.prepare_from_now_opening,
            baseline,
            v1,
            evaluated_at=EVALUATED_AT,
            identity_key=b"bad",
        )
        stale_bad_hmac = dataclasses.replace(proof, proof_identity_sha256="0" * 64)
        _reason(
            cl4.CL4Reason.PROOF_STALE,
            cl4.prepare_from_now_opening,
            baseline,
            stale_bad_hmac,
            evaluated_at="2026-01-02T03:06:05.123456790Z",
            identity_key=KEY,
        )
    error = cl4.CL4Error(cl4.CL4Reason.PROOF_IDENTITY_INVALID)
    private = KEY.hex()
    assert private not in repr(proof) and private not in repr(error) and private not in str(error.evidence)


def test_v310_cl4_05_timestamp_freshness_boundaries() -> None:
    _proof(evaluated_at="2026-01-02T03:06:05.123456789Z")
    _reason(
        cl4.CL4Reason.PROOF_STALE,
        _proof,
        evaluated_at="2026-01-02T03:06:05.123456790Z",
    )
    _reason(
        cl4.CL4Reason.PROOF_FROM_FUTURE,
        _proof,
        evaluated_at="2026-01-02T03:04:05.123456788Z",
    )
    _reason(cl4.CL4Reason.TIMESTAMP_INVALID, _proof, as_of="2026-02-30T00:00:00.000000000Z")
    _reason(cl4.CL4Reason.TIMESTAMP_INVALID, _proof, as_of="2026-01-02T03:04:05.123Z")
    _reason(cl4.CL4Reason.PROOF_INCOMPLETE, cl4.build_broker_cash_proof, RESPONSE, account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=AS_OF, evaluated_at=EVALUATED_AT, response_complete=False, identity_key=KEY, identity_key_id=KEY_ID)


def test_v310_cl4_rs1_all_public_bounds_exact_max_and_max_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = {
        "_MAX_RESPONSE_DEPTH": cl4._MAX_RESPONSE_DEPTH,
        "_MAX_RESPONSE_NODES": cl4._MAX_RESPONSE_NODES,
        "_MAX_MAPPING_KEYS": cl4._MAX_MAPPING_KEYS,
        "_MAX_KEY_SCALARS": cl4._MAX_KEY_SCALARS,
        "_MAX_STRING_SCALARS": cl4._MAX_STRING_SCALARS,
        "_MAX_RESPONSE_CANONICAL_BYTES": cl4._MAX_RESPONSE_CANONICAL_BYTES,
        "_MAX_BASELINE_WITNESS_BYTES": cl4._MAX_BASELINE_WITNESS_BYTES,
        "_MAX_LEDGER_EXPORT_BYTES": cl4._MAX_LEDGER_EXPORT_BYTES,
        "_MAX_LEDGER_OBJECTS": cl4._MAX_LEDGER_OBJECTS,
    }

    for name, exact_max in (
        ("_MAX_RESPONSE_DEPTH", 3),
        ("_MAX_RESPONSE_NODES", 5),
        ("_MAX_MAPPING_KEYS", 3),
        ("_MAX_KEY_SCALARS", len("totalAmountCurrencies")),
        ("_MAX_STRING_SCALARS", 3),
        ("_MAX_RESPONSE_CANONICAL_BYTES", len(_canonical(RESPONSE))),
    ):
        monkeypatch.setattr(cl4, name, exact_max)
        _proof()
        monkeypatch.setattr(cl4, name, exact_max - 1)
        _reason(cl4.CL4Reason.RESPONSE_BOUNDS_EXCEEDED, _proof)
        monkeypatch.setattr(cl4, name, limits[name])

    with _new_store(tmp_path) as store:
        baseline = store.export_bytes()
        monkeypatch.setattr(cl4, "_MAX_BASELINE_WITNESS_BYTES", len(baseline))
        plan = _plan(store)
        monkeypatch.setattr(cl4, "_MAX_BASELINE_WITNESS_BYTES", len(baseline) - 1)
        _reason(
            cl4.CL4Reason.LEDGER_EXPORT_INVALID,
            cl4.prepare_from_now_opening,
            baseline,
            plan.proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        monkeypatch.setattr(
            cl4,
            "_MAX_BASELINE_WITNESS_BYTES",
            limits["_MAX_BASELINE_WITNESS_BYTES"],
        )
        _accept(store, plan)
        exported = store.export_bytes()
        document = json.loads(exported)
        aggregate_objects = sum(
            len(document[name])
            for name in (
                "codec_registry",
                "observations",
                "inbox_status_events",
                "transactions",
                "provenance_links",
                "correction_bundles",
                "ledger_transitions",
            )
        )
        projection_arguments = {
            "account_scope_sha256": ACCOUNT,
            "environment": broker.BrokerEnvironment.SANDBOX,
            "as_of": AS_OF,
            "identity_key": KEY,
        }
        monkeypatch.setattr(cl4, "_MAX_LEDGER_EXPORT_BYTES", len(exported))
        cl4.project_shadow_cash(exported, **projection_arguments)
        monkeypatch.setattr(cl4, "_MAX_LEDGER_EXPORT_BYTES", len(exported) - 1)
        _reason(
            cl4.CL4Reason.LEDGER_EXPORT_INVALID,
            cl4.project_shadow_cash,
            exported,
            **projection_arguments,
        )
        monkeypatch.setattr(
            cl4,
            "_MAX_LEDGER_EXPORT_BYTES",
            limits["_MAX_LEDGER_EXPORT_BYTES"],
        )
        monkeypatch.setattr(cl4, "_MAX_LEDGER_OBJECTS", aggregate_objects)
        cl4.project_shadow_cash(exported, **projection_arguments)
        monkeypatch.setattr(cl4, "_MAX_LEDGER_OBJECTS", aggregate_objects - 1)
        _reason(
            cl4.CL4Reason.LEDGER_EXPORT_INVALID,
            cl4.project_shadow_cash,
            exported,
            **projection_arguments,
        )


def test_v310_cl4_06_export_validation_schema_hash_head_and_order(tmp_path: Path, cl2_vectors: dict[str, dict[str, object]]) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        plan = _plan(store)
        _accept(store, plan)
        observation = _custom_observation(descriptor, label="AFTER")
        transaction = _custom_transaction(observation, minor_units=1)
        _append_transaction(store, observation, transaction)
        exported = store.export_bytes()
    document = json.loads(exported)
    cases: list[dict[str, object]] = []
    missing = dict(document)
    missing.pop("ledger_head_sha256")
    cases.append(missing)
    wrong_head = json.loads(exported)
    wrong_head["ledger_head_sha256"] = "0" * 64
    cases.append(wrong_head)
    duplicate_tx = json.loads(exported)
    duplicate_tx["transactions"].append(duplicate_tx["transactions"][0])
    cases.append(duplicate_tx)
    reversed_tx = json.loads(exported)
    reversed_tx["transactions"].reverse()
    cases.append(reversed_tx)
    dangling = json.loads(exported)
    dangling["provenance_links"][0]["observation_sha256"] = "0" * 64
    cases.append(dangling)
    for case in cases:
        _reason(
            cl4.CL4Reason.LEDGER_GRAPH_INVALID if set(case) == set(document) else cl4.CL4Reason.LEDGER_EXPORT_INVALID,
            cl4.project_shadow_cash,
            _canonical(case),
            account_scope_sha256=ACCOUNT,
            environment=broker.BrokerEnvironment.SANDBOX,
            as_of=LATER,
            identity_key=KEY,
        )
    duplicate_key = exported[:-1] + b',"version":1}'
    _reason(cl4.CL4Reason.LEDGER_EXPORT_INVALID, cl4.project_shadow_cash, duplicate_key, account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=LATER, identity_key=KEY)


def test_v310_cl4_07_deterministic_plan_codec_content_and_postings(tmp_path: Path) -> None:
    with _new_store(tmp_path) as store:
        baseline = store.export_bytes()
        plan = _plan(store)
        same = _plan(store)
        assert same.canonical_bytes == plan.canonical_bytes
        assert cl4.CL4_OPENING_CODEC.schema_sha256 == "1f15c6486dd348bdcf8f2b725111b01e583404efe5717ab5df0b7968d87bbfe3"
        assert cl4.CL4_OPENING_CODEC.sha256 == "d80e3ccde02d469f5bd99d9884cd36524f1d11069d2ce8bab08e9c0b65b3659d"
        assert plan.observation.source.source_scope_sha256 == "c6c936312caa64f076007b86d13941067ccee1f9e2a2cee06b3db5d272818410"
        assert plan.observation.source.source_content_sha256 == "9673dea00bc76b4865ceff9424e63a097a0419f2afd29e4475b99e19e4471f3e"
        assert plan.observation.provenance_sha256 == "9c212565e79474e62260d8da2276339c4509652cfff31d2614cf376beab0e6f7"
        assert plan.observation.sha256 == "a539c06c4ce0d2c0c1e3c3e29e92b0d322c0b253ed9e0c83d3fa18d4ea955ec3"
        assert plan.transaction.sha256 == "7c49d302b3a0cc20e0114ba56529f94dae4fff61501b9f7ce45bb435e1058ee5"
        assert plan.sha256 == "d0312594d9ca205bc2a1876d4e865e6dc3b020c22992bf220724d4c4b74d6793"
        assert plan.transaction.classification is ledger.LedgerClassification.OPENING_BALANCE
        assert [posting.account for posting in plan.transaction.postings] == [ledger.LedgerAccount.ASSET_BROKER_CASH, ledger.LedgerAccount.EQUITY_OPENING_BALANCE]
        assert [posting.money.minor_units for posting in plan.transaction.postings] == [123_500_000_000, -123_500_000_000]
    _reason(cl4.CL4Reason.OPENING_AMOUNT_UNSUPPORTED, cl4.prepare_from_now_opening, baseline, _proof(response={"totalAmountCurrencies": {"currency": "RUB", "units": "0", "nano": 0}}), evaluated_at=EVALUATED_AT, identity_key=KEY)


def test_v310_cl4_08_wrong_confirmation_and_stale_plan_are_zero_write(tmp_path: Path, cl2_vectors: dict[str, dict[str, object]]) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        plan = _plan(store)
        before = store.export_bytes()
        _reason(cl4.CL4Reason.CONFIRMATION_INVALID, cl4.accept_from_now_opening, store, plan, confirmation="ACCEPT", evaluated_at=EVALUATED_AT, identity_key=KEY)
        assert store.export_bytes() == before
        forged = dataclasses.replace(plan, pre_ledger_head_sha256="0" * 64)
        _reason(cl4.CL4Reason.CONFIRMATION_INVALID, cl4.accept_from_now_opening, store, forged, confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}", evaluated_at=EVALUATED_AT, identity_key=KEY)
        assert store.export_bytes() == before
        unrelated = _custom_observation(descriptor, label="UNRELATED", account=OTHER_ACCOUNT)
        snapshot = store.snapshot()
        store.append_observation(unrelated, expected_store_revision=snapshot.store_revision)
        externally_changed = store.export_bytes()
        _reason(cl4.CL4Reason.BASELINE_STALE, _accept, store, plan)
        assert store.export_bytes() == externally_changed


def test_v310_cl4_rs1_acceptance_literal_first_failure_order(
    tmp_path: Path,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    descriptor = _descriptor(cl2_vectors)
    wrong_key = bytes(reversed(range(32)))
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        plan = _plan(store)
        confirmation = f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}"
        _reason(
            cl4.CL4Reason.TIMESTAMP_INVALID,
            cl4.accept_from_now_opening,
            store,
            plan,
            confirmation=confirmation,
            evaluated_at="2026-01-02T03:04:06Z",
            identity_key=wrong_key,
        )

        stale_bad_hmac = dataclasses.replace(
            plan.proof,
            proof_identity_sha256="0" * 64,
        )
        stale_plan = dataclasses.replace(plan, proof=stale_bad_hmac)
        _reason(
            cl4.CL4Reason.PROOF_STALE,
            cl4.accept_from_now_opening,
            store,
            stale_plan,
            confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {stale_plan.sha256}",
            evaluated_at="2026-01-02T03:06:05.123456790Z",
            identity_key=wrong_key,
        )

        v1_plan = copy.copy(plan)
        object.__setattr__(v1_plan, "version", 1)
        _reason(
            cl4.CL4Reason.VERSION_UNSUPPORTED,
            cl4.accept_from_now_opening,
            store,
            v1_plan,
            confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {v1_plan.sha256}",
            evaluated_at=EVALUATED_AT,
            identity_key=b"bad",
        )

        malformed_witness = copy.copy(plan)
        object.__setattr__(malformed_witness, "baseline_export_bytes", b"{}")
        object.__setattr__(
            malformed_witness,
            "baseline_export_sha256",
            hashlib.sha256(b"{}").hexdigest(),
        )
        _reason(
            cl4.CL4Reason.BASELINE_STALE,
            cl4.accept_from_now_opening,
            store,
            malformed_witness,
            confirmation=(
                "ACCEPT V3.10 CL4 FROM_NOW OPENING "
                f"{malformed_witness.sha256}"
            ),
            evaluated_at=EVALUATED_AT,
            identity_key=wrong_key,
        )

        unrelated = _custom_observation(descriptor, label="FOREIGN", account=OTHER_ACCOUNT)
        store.append_observation(
            unrelated,
            expected_store_revision=store.snapshot().store_revision,
        )
        foreign = store.export_bytes()
        _reason(
            cl4.CL4Reason.BASELINE_STALE,
            cl4.accept_from_now_opening,
            store,
            plan,
            confirmation=confirmation,
            evaluated_at=EVALUATED_AT,
            identity_key=wrong_key,
        )
        assert store.export_bytes() == foreign


def test_v310_cl4_rs1_exact_baseline_witness_and_transfer_fail_closed(
    tmp_path: Path,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    descriptor = _descriptor(cl2_vectors)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    lower_root = tmp_path / "lower"
    first_root.mkdir()
    second_root.mkdir()
    lower_root.mkdir()
    with _new_store(first_root, descriptors=(descriptor,)) as first:
        first_observation = _custom_observation(
            descriptor,
            label="BASELINE_FIRST",
            account=OTHER_ACCOUNT,
        )
        first.append_observation(first_observation, expected_store_revision=0)
        plan = _plan(first)
        assert plan.baseline_export_bytes == first.export_bytes()
        assert plan.baseline_export_sha256 == hashlib.sha256(plan.baseline_export_bytes).hexdigest()
        assert plan.baseline_export_bytes.decode("ascii") not in repr(plan)
        forged_witness = copy.copy(plan)
        object.__setattr__(forged_witness, "baseline_export_bytes", b"{}")
        assert forged_witness != plan
        _reason(
            cl4.CL4Reason.LEDGER_EXPORT_INVALID,
            _accept,
            first,
            forged_witness,
        )
    with _new_store(second_root, descriptors=(descriptor,)) as second:
        second_observation = _custom_observation(
            descriptor,
            label="BASELINE_SECOND",
            account=OTHER_ACCOUNT,
        )
        second.append_observation(second_observation, expected_store_revision=0)
        assert second.snapshot().store_revision == plan.pre_store_revision
        assert second.snapshot().ledger_revision == plan.pre_ledger_revision
        assert second.snapshot().ledger_head_sha256 == plan.pre_ledger_head_sha256
        before = second.export_bytes()
        _reason(cl4.CL4Reason.BASELINE_STALE, _accept, second, plan)
        assert second.export_bytes() == before
    with _new_store(lower_root, descriptors=(descriptor,)) as lower:
        before = lower.export_bytes()
        _reason(cl4.CL4Reason.BASELINE_STALE, _accept, lower, plan)
        assert lower.export_bytes() == before


class _OnceFault:
    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(self, point: str) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise persistence.InjectedFault(point)


@pytest.mark.parametrize("fault_point", ["append_observation.after_commit", "append_transaction.after_commit"])
def test_v310_cl4_09_fault_resume_and_lost_success_replay(tmp_path: Path, fault_point: str) -> None:
    fault = _OnceFault(fault_point)
    with _new_store(tmp_path, fault_injector=fault) as store:
        plan = _plan(store)
        _reason(cl4.CL4Reason.PERSISTENCE_FAILURE, _accept, store, plan)
        accepted = _accept(store, plan, evaluated_at="2026-01-02T04:04:06.123456789Z")
        assert accepted.record.sha256 == "71109fceb92f5f3789e8e323e000fe1226266cb6733bf6072d6d33375a7747ce"
        again = _accept(store, plan, evaluated_at="2026-01-03T00:00:00.000000000Z")
        assert again.disposition == "OPENING_ALREADY_PRESENT"
        assert store.snapshot().ledger_revision == 1


def test_v310_cl4_09_staged_recovery_rejects_unrelated_append_fail_closed(
    tmp_path: Path,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        plan = _plan(store)
        store.append_observation(plan.observation, expected_store_revision=0)
        unrelated = _custom_observation(descriptor, label="STAGED_OTHER", account=OTHER_ACCOUNT)
        store.append_observation(unrelated, expected_store_revision=1)
        before = store.export_bytes()
        _reason(
            cl4.CL4Reason.BASELINE_STALE,
            _accept,
            store,
            plan,
            evaluated_at="2026-01-03T00:00:00.000000000Z",
        )
        assert store.export_bytes() == before
        assert store.snapshot().ledger_revision == 0


def test_v310_cl4_rs1_prospective_capacity_exact_edges_and_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        baseline = store.export_bytes()
        proof = _proof()
        plan = _plan(store, proof)
        prospective = cl4._prospective_states(plan, KEY)
        staged = json.loads(prospective.staged_bytes)
        committed = json.loads(prospective.committed_bytes)
        array_names = (
            "codec_registry",
            "observations",
            "inbox_status_events",
            "transactions",
            "provenance_links",
            "correction_bundles",
            "ledger_transitions",
        )
        exact_objects = max(
            sum(len(document[name]) for name in array_names)
            for document in (staged, committed)
        )
        exact_bytes = max(len(prospective.staged_bytes), len(prospective.committed_bytes))

        monkeypatch.setattr(cl4, "_MAX_REVISION", 2)
        cl4.prepare_from_now_opening(
            baseline,
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        monkeypatch.setattr(cl4, "_MAX_REVISION", 1)
        _reason(
            cl4.CL4Reason.OPENING_CAPACITY_EXHAUSTED,
            cl4.prepare_from_now_opening,
            baseline,
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        assert store.export_bytes() == baseline

        monkeypatch.setattr(cl4, "_MAX_REVISION", persistence.MAX_REVISION)
        monkeypatch.setattr(cl4, "_MAX_LEDGER_OBJECTS", exact_objects)
        cl4.prepare_from_now_opening(
            baseline,
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        monkeypatch.setattr(cl4, "_MAX_LEDGER_OBJECTS", exact_objects - 1)
        _reason(
            cl4.CL4Reason.OPENING_CAPACITY_EXHAUSTED,
            cl4.prepare_from_now_opening,
            baseline,
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        assert store.export_bytes() == baseline

        monkeypatch.setattr(cl4, "_MAX_LEDGER_OBJECTS", 100_000)
        monkeypatch.setattr(cl4, "_MAX_LEDGER_EXPORT_BYTES", exact_bytes)
        cl4.prepare_from_now_opening(
            baseline,
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        monkeypatch.setattr(cl4, "_MAX_LEDGER_EXPORT_BYTES", exact_bytes - 1)
        _reason(
            cl4.CL4Reason.OPENING_CAPACITY_EXHAUSTED,
            cl4.prepare_from_now_opening,
            baseline,
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        assert store.export_bytes() == baseline

        monkeypatch.setattr(cl4, "_MAX_LEDGER_EXPORT_BYTES", 16_777_216)
        monkeypatch.setattr(cl4, "_MAX_REVISION", 1)
        unrelated = _custom_observation(
            descriptor,
            label="CAPACITY_AND_FOREIGN",
            account=OTHER_ACCOUNT,
        )
        store.append_observation(unrelated, expected_store_revision=0)
        foreign = store.export_bytes()
        _reason(cl4.CL4Reason.BASELINE_STALE, _accept, store, plan)
        assert store.export_bytes() == foreign


def test_v310_cl4_rs1_capacity_descriptor_present_path(tmp_path: Path) -> None:
    with _new_store(tmp_path) as store:
        other_key = bytes(reversed(range(32)))
        other_proof = _proof(
            account=OTHER_ACCOUNT,
            key=other_key,
            key_id="OTHER_ACCOUNT_KEY",
        )
        other_plan = cl4.prepare_from_now_opening(
            store.export_bytes(),
            other_proof,
            evaluated_at=EVALUATED_AT,
            identity_key=other_key,
        )
        store.append_observation(other_plan.observation, expected_store_revision=0)
        baseline = store.export_bytes()
        plan = _plan(store)
        prospective = cl4._prospective_states(plan, KEY)
        staged = json.loads(prospective.staged_bytes)
        assert sum(
            row["sha256"] == cl4.CL4_OPENING_CODEC.sha256
            for row in staged["codec_registry"]
        ) == 1
        assert len(staged["observations"]) == 2
        assert store.export_bytes() == baseline


def test_v310_cl4_09_prepare_rejects_unresolved_or_post_proof_evidence(
    tmp_path: Path,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        unresolved = _custom_observation(
            descriptor,
            label="PENDING_BEFORE",
            observed_at=AS_OF,
        )
        store.append_observation(unresolved, expected_store_revision=0)
        _reason(
            cl4.CL4Reason.INBOX_INCOMPLETE,
            cl4.prepare_from_now_opening,
            store.export_bytes(),
            _proof(),
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )


def test_v310_cl4_10_same_and_different_plan_create_once(tmp_path: Path) -> None:
    with _new_store(tmp_path) as store:
        plan = _plan(store)
        alternate = _plan(store, _proof(response={"totalAmountCurrencies": {"currency": "RUB", "units": "124", "nano": 0}}))
        assert _accept(store, plan).disposition == "OPENING_APPENDED"
        assert _accept(store, plan).disposition == "OPENING_ALREADY_PRESENT"
        _reason(cl4.CL4Reason.OPENING_CONFLICT, _accept, store, alternate)
        _reason(cl4.CL4Reason.OPENING_CONFLICT, cl4.prepare_from_now_opening, store.export_bytes(), _proof(), evaluated_at=EVALUATED_AT, identity_key=KEY)


def test_v310_cl4_rs1_cas_races_never_create_partial_economic_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    descriptor = _descriptor(cl2_vectors)
    observation_root = tmp_path / "observation"
    transaction_root = tmp_path / "transaction"
    observation_root.mkdir()
    transaction_root.mkdir()
    original_observation = persistence.CashLedgerStore.append_observation
    with _new_store(observation_root, descriptors=(descriptor,)) as store:
        plan = _plan(store)
        unrelated = _custom_observation(
            descriptor,
            label="RACE_BEFORE_OBSERVATION_CAS",
            account=OTHER_ACCOUNT,
        )
        armed = True

        def raced_observation(
            self: persistence.CashLedgerStore,
            value: persistence.InboxObservation,
            *,
            expected_store_revision: int,
        ) -> persistence.PersistenceDisposition:
            nonlocal armed
            if armed:
                armed = False
                original_observation(
                    self,
                    unrelated,
                    expected_store_revision=expected_store_revision,
                )
            return original_observation(
                self,
                value,
                expected_store_revision=expected_store_revision,
            )

        monkeypatch.setattr(
            persistence.CashLedgerStore,
            "append_observation",
            raced_observation,
        )
        _reason(cl4.CL4Reason.OPENING_PLAN_STALE, _accept, store, plan)
        exported = json.loads(store.export_bytes())
        assert store.snapshot().ledger_revision == 0
        assert all(row["sha256"] != plan.observation.sha256 for row in exported["observations"])
        monkeypatch.setattr(
            persistence.CashLedgerStore,
            "append_observation",
            original_observation,
        )

    original_transaction = persistence.CashLedgerStore.append_transaction
    with _new_store(transaction_root, descriptors=(descriptor,)) as store:
        plan = _plan(store)
        store.append_observation(plan.observation, expected_store_revision=0)
        unrelated = _custom_observation(
            descriptor,
            label="RACE_BEFORE_TRANSACTION_CAS",
            account=OTHER_ACCOUNT,
        )
        armed = True

        def raced_transaction(
            self: persistence.CashLedgerStore,
            transaction: ledger.LedgerTransaction,
            observation_sha256: str,
            *,
            expected_store_revision: int,
            expected_ledger_revision: int,
        ) -> persistence.PersistenceDisposition:
            nonlocal armed
            if armed:
                armed = False
                original_observation(
                    self,
                    unrelated,
                    expected_store_revision=expected_store_revision,
                )
            return original_transaction(
                self,
                transaction,
                observation_sha256,
                expected_store_revision=expected_store_revision,
                expected_ledger_revision=expected_ledger_revision,
            )

        monkeypatch.setattr(
            persistence.CashLedgerStore,
            "append_transaction",
            raced_transaction,
        )
        _reason(cl4.CL4Reason.OPENING_PLAN_STALE, _accept, store, plan)
        exported = json.loads(store.export_bytes())
        assert store.snapshot().ledger_revision == 0
        assert exported["transactions"] == []


def test_v310_cl4_11_record_and_known_answer_graph(tmp_path: Path) -> None:
    with _new_store(tmp_path) as store:
        accepted = _accept(store, _plan(store))
        assert accepted.store_revision == 2
        assert accepted.ledger_revision == 1
        assert accepted.ledger_head_sha256 == "53dcc785fd962de251ce3745138866005584dd6ba179ae3e32a2d97b7fde6999"
        assert hashlib.sha256(store.export_bytes()).hexdigest() == "f7d0e10b4133ff989425fa276b1b3e18ef440668b5e68fcb0f2df67ad03484a2"
        assert accepted.record.sha256 == "71109fceb92f5f3789e8e323e000fe1226266cb6733bf6072d6d33375a7747ce"
        document = json.loads(store.export_bytes())
    document["observations"][0]["current_status"] = "OBSERVED"
    _reason(cl4.CL4Reason.LEDGER_GRAPH_INVALID, cl4.project_shadow_cash, _canonical(document), account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=AS_OF, identity_key=KEY)


def test_v310_cl4_rs1_record_requires_immediate_successor_revision(
    tmp_path: Path,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        prior_observation = _custom_observation(
            descriptor,
            label="PRIOR_REVISION",
            account=OTHER_ACCOUNT,
        )
        _append_transaction(
            store,
            prior_observation,
            _custom_transaction(prior_observation, minor_units=1),
        )
        proof = _proof()
        plan = _plan(store, proof)
        forged_content = cl4._OpeningContent(
            account_scope_sha256=ACCOUNT,
            baseline_export_sha256=plan.baseline_export_sha256,
            broker_cash_proof_sha256=proof.sha256,
            contract_version=2,
            cutoff=proof.as_of,
            environment=broker.BrokerEnvironment.SANDBOX,
            identity_key_id=KEY_ID,
            ledger_head_sha256=persistence.GENESIS_HEAD_SHA256,
            ledger_revision=0,
            opening_minor_units=proof.cash.minor_units,
            proof_identity_sha256=proof.proof_identity_sha256,
            store_revision=plan.pre_store_revision,
        )
        forged_observation, forged_transaction = cl4._opening_graph(
            forged_content,
            KEY,
        )
        _append_transaction(store, forged_observation, forged_transaction)
        _reason(
            cl4.CL4Reason.OPENING_CONFLICT,
            cl4.project_shadow_cash,
            store.export_bytes(),
            account_scope_sha256=ACCOUNT,
            environment=broker.BrokerEnvironment.SANDBOX,
            as_of=AS_OF,
            identity_key=KEY,
        )


@pytest.mark.parametrize(
    ("classification", "minor_units"),
    [
        (ledger.LedgerClassification.DEPOSIT, 1),
        (ledger.LedgerClassification.WITHDRAWAL, -1),
        (ledger.LedgerClassification.DIVIDEND, 1),
        (ledger.LedgerClassification.COUPON, 1),
        (ledger.LedgerClassification.INTEREST, 1),
        (ledger.LedgerClassification.COMMISSION, -1),
        (ledger.LedgerClassification.TAX, -1),
        (ledger.LedgerClassification.TRADE_SETTLEMENT, 1),
        (ledger.LedgerClassification.MANUAL_ADJUSTMENT, 1),
    ],
)
def test_v310_cl4_12_projection_classifications_and_account_scope(tmp_path: Path, cl2_vectors: dict[str, dict[str, object]], classification: ledger.LedgerClassification, minor_units: int) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        _accept(store, _plan(store))
        observation = _custom_observation(descriptor, label=classification.value)
        _append_transaction(store, observation, _custom_transaction(observation, minor_units=minor_units, classification=classification))
        other = _custom_observation(descriptor, label="OTHER", account=OTHER_ACCOUNT)
        _append_transaction(store, other, _custom_transaction(other, minor_units=999))
        projection = cl4.project_shadow_cash(store.export_bytes(), account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=LATER, identity_key=KEY)
        assert projection.expected_cash.minor_units == 123_500_000_000 + minor_units
        assert projection.complete


def test_v310_cl4_13_correction_bundle_net_once_and_opening_correction_rejected(tmp_path: Path, cl2_vectors: dict[str, dict[str, object]]) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        opening_plan = _plan(store)
        _accept(store, opening_plan)
        original_observation = _observation(cl2_vectors, descriptor, "observation-original")
        original = _transaction(cl2_vectors, "transaction-original")
        _append_transaction(store, original_observation, original)
        reversal_observation = _observation(cl2_vectors, descriptor, "observation-reversal-distinct")
        correction_observation = _observation(cl2_vectors, descriptor, "observation-correction-distinct")
        before = store.snapshot()
        store.append_observation(reversal_observation, expected_store_revision=before.store_revision)
        before = store.snapshot()
        store.append_observation(correction_observation, expected_store_revision=before.store_revision)
        before = store.snapshot()
        store.append_correction_bundle(_bundle(cl2_vectors, "bundle-distinct"), reversal_observation.sha256, correction_observation.sha256, expected_store_revision=before.store_revision, expected_ledger_revision=before.ledger_revision)
        projection = cl4.project_shadow_cash(store.export_bytes(), account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=LATER, identity_key=KEY)
        assert projection.expected_cash.minor_units == 125_500_000_000
        reversal_obs = _custom_observation(descriptor, label="OPENING_REVERSAL")
        correction_obs = _custom_observation(descriptor, label="OPENING_CORRECTION")
        reversal = ledger.LedgerTransaction.reversing(
            opening_plan.transaction,
            effective_at=LATER,
            source=reversal_obs.source,
        )
        corrected_money = ledger.Money(currency="RUB", minor_units=124_000_000_000)
        correction = ledger.LedgerTransaction(
            classification=ledger.LedgerClassification.OPENING_BALANCE,
            effective_at=LATER,
            source=correction_obs.source,
            postings=(
                ledger.LedgerPosting(1, ledger.LedgerAccount.ASSET_BROKER_CASH, corrected_money),
                ledger.LedgerPosting(2, ledger.LedgerAccount.EQUITY_OPENING_BALANCE, -corrected_money),
            ),
            corrects_sha256=opening_plan.transaction.sha256,
        )
        opening_bundle = ledger.LedgerCorrectionBundle(
            original=opening_plan.transaction,
            reversal=reversal,
            correction=correction,
        )
        before = store.snapshot()
        store.append_observation(reversal_obs, expected_store_revision=before.store_revision)
        before = store.snapshot()
        store.append_observation(correction_obs, expected_store_revision=before.store_revision)
        before = store.snapshot()
        store.append_correction_bundle(
            opening_bundle,
            reversal_obs.sha256,
            correction_obs.sha256,
            expected_store_revision=before.store_revision,
            expected_ledger_revision=before.ledger_revision,
        )
        _reason(
            cl4.CL4Reason.OPENING_CONFLICT,
            cl4.project_shadow_cash,
            store.export_bytes(),
            account_scope_sha256=ACCOUNT,
            environment=broker.BrokerEnvironment.SANDBOX,
            as_of=LATER,
            identity_key=KEY,
        )


def test_v310_cl4_14_late_future_and_evidence_windows(tmp_path: Path, cl2_vectors: dict[str, dict[str, object]]) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        opening_plan = _plan(store)
        _accept(store, opening_plan)
        late = _custom_observation(descriptor, label="LATE", observed_at=LATER)
        _append_transaction(store, late, _custom_transaction(late, minor_units=1, effective_at=AS_OF))
        future = _custom_observation(descriptor, label="FUTURE", observed_at="2026-01-02T03:20:00.000000000Z")
        _append_transaction(store, future, _custom_transaction(future, minor_units=1, effective_at="2026-01-02T03:20:00.000000000Z"))
        projection = cl4.project_shadow_cash(store.export_bytes(), account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=LATER, identity_key=KEY)
        assert projection.incompleteness_kinds == (
            cl4.DiscrepancyKind.LATE_PRE_CUTOFF_LEDGER_EFFECT,
            cl4.DiscrepancyKind.PROOF_BEFORE_LEDGER_EFFECT,
            cl4.DiscrepancyKind.PROOF_BEFORE_LEDGER_EVIDENCE,
        )
        assert not projection.complete


def test_v310_cl4_15_unresolved_and_rejected_inbox_semantics(tmp_path: Path, cl2_vectors: dict[str, dict[str, object]]) -> None:
    descriptor = _descriptor(cl2_vectors)
    with _new_store(tmp_path, descriptors=(descriptor,)) as store:
        _accept(store, _plan(store))
        unresolved = _custom_observation(descriptor, label="UNRESOLVED")
        before = store.snapshot()
        store.append_observation(unresolved, expected_store_revision=before.store_revision)
        projection = cl4.project_shadow_cash(store.export_bytes(), account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=LATER, identity_key=KEY)
        assert projection.unresolved_observation_sha256 == (unresolved.sha256,)
        assert cl4.DiscrepancyKind.UNRESOLVED_OBSERVATION in projection.incompleteness_kinds
        before = store.snapshot()
        store.append_status_event(persistence.InboxStatusEvent(observation_sha256=unresolved.sha256, event_no=1, from_status="OBSERVED", to_status="REJECTED", reason="NOT_LEDGER_RELEVANT"), expected_store_revision=before.store_revision)
        projection = cl4.project_shadow_cash(store.export_bytes(), account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=LATER, identity_key=KEY)
        assert projection.complete and not projection.unresolved_observation_sha256


def test_v310_cl4_16_exact_reconciliation_zero_plus_minus_one(tmp_path: Path) -> None:
    with _new_store(tmp_path) as store:
        _accept(store, _plan(store))
        exported = store.export_bytes()
        cases = (
            (500_000_000, cl4.ReconciliationStatus.MATCHED, cl4.DiscrepancyKind.NONE, 0),
            (500_000_001, cl4.ReconciliationStatus.DISCREPANCY, cl4.DiscrepancyKind.BROKER_ABOVE_EXPECTED, 1),
            (499_999_999, cl4.ReconciliationStatus.DISCREPANCY, cl4.DiscrepancyKind.BROKER_BELOW_EXPECTED, -1),
        )
        for nano, status, kind, delta in cases:
            proof = _proof(response={"totalAmountCurrencies": {"currency": "RUB", "units": "123", "nano": nano}})
            result = cl4.reconcile_shadow_cash(exported, proof, evaluated_at=EVALUATED_AT, identity_key=KEY)
            assert (result.status, result.discrepancy_kind, result.delta_minor_units) == (status, kind, delta)
            candidate = cl4.build_adoption_candidate(
                result,
                ledger_export_bytes=exported,
                identity_key=KEY,
            )
            expected_disposition = (
                cl4.AdoptionDisposition.SEPARATE_LOCKED_REVIEW_REQUIRED
                if delta == 0
                else cl4.AdoptionDisposition.BLOCKED
            )
            assert candidate.disposition is expected_disposition


def test_v310_cl4_17_candidate_is_fail_closed_and_recomputed(tmp_path: Path) -> None:
    with _new_store(tmp_path) as store:
        _accept(store, _plan(store))
        exported = store.export_bytes()
        reconciliation = cl4.reconcile_shadow_cash(exported, _proof(), evaluated_at=EVALUATED_AT, identity_key=KEY)
        candidate = cl4.build_adoption_candidate(reconciliation, ledger_export_bytes=exported, identity_key=KEY)
        assert candidate.disposition is cl4.AdoptionDisposition.SEPARATE_LOCKED_REVIEW_REQUIRED
        assert candidate.automatic_adoption is False
        assert candidate.requires_locked_revalidation is True
        assert candidate.runtime_cash_owner_changed is False
        assert candidate.sha256 == "017a995a90ab719da91edb0f89fdeecd7350c485f51ac279216512c5ddac20a3"
        forged = copy.copy(reconciliation)
        object.__setattr__(forged, "delta_minor_units", 1)
        _reason(cl4.CL4Reason.RECONCILIATION_INVALID, cl4.build_adoption_candidate, forged, ledger_export_bytes=exported, identity_key=KEY)
        _reason(cl4.CL4Reason.PROOF_IDENTITY_INVALID, cl4.build_adoption_candidate, reconciliation, ledger_export_bytes=exported, identity_key=b"x" * 32)
        rotated = _proof(key_id="CL4_ROTATED_KEY")
        _reason(
            cl4.CL4Reason.PROOF_IDENTITY_INVALID,
            cl4.reconcile_shadow_cash,
            exported,
            rotated,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        assert not any(callable(getattr(cl4, name, None)) for name in ("adopt", "apply", "migrate"))


def test_v310_cl4_17_non_target_opening_with_other_key_is_opaque(
    tmp_path: Path,
) -> None:
    other_key = bytes(reversed(range(32)))
    with _new_store(tmp_path) as store:
        first_plan = _plan(store)
        _accept(store, first_plan)
        other_proof = _proof(
            account=OTHER_ACCOUNT,
            key=other_key,
            key_id="OTHER_ACCOUNT_KEY",
        )
        other_plan = cl4.prepare_from_now_opening(
            store.export_bytes(),
            other_proof,
            evaluated_at=EVALUATED_AT,
            identity_key=other_key,
        )
        cl4.accept_from_now_opening(
            store,
            other_plan,
            confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {other_plan.sha256}",
            evaluated_at=EVALUATED_AT,
            identity_key=other_key,
        )
        projection = cl4.project_shadow_cash(
            store.export_bytes(),
            account_scope_sha256=ACCOUNT,
            environment=broker.BrokerEnvironment.SANDBOX,
            as_of=AS_OF,
            identity_key=KEY,
        )
        assert projection.expected_cash.minor_units == 123_500_000_000
        assert projection.complete


def test_v310_cl4_18_privacy_safe_objects_errors_and_fixture() -> None:
    raw = "PRIVATE_ACCOUNT_SENTINEL"
    proof = _proof(response={**RESPONSE, "ignored": raw})
    assert raw not in repr(proof) and raw not in proof.canonical_bytes.decode("ascii")
    assert KEY.hex() not in repr(proof) and KEY.hex() not in proof.canonical_bytes.decode("ascii")
    error = cl4.CL4Error(cl4.CL4Reason.TYPE_INVALID, "PRIVATE_TOKEN", {"stage": "BAD STAGE", "unknown": raw})
    assert error.cause_reason is None
    assert raw not in repr(error) and raw not in str(error.evidence)


def test_v310_cl4_rs1_unexpected_dependency_failure_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = "PRIVATE_PROVIDER_FAILURE"

    def fail_money_value(_: object) -> ledger.Money:
        raise RuntimeError(private)

    monkeypatch.setattr(broker, "money_value_to_money", fail_money_value)
    error = _reason(cl4.CL4Reason.INTERNAL_BOUNDARY_FAILED, _proof)
    assert private not in repr(error)
    assert private not in str(error)
    assert private not in str(error.evidence)


def test_v310_cl4_rs1_all_v1_public_artifacts_fail_closed(tmp_path: Path) -> None:
    with _new_store(tmp_path) as store:
        proof = _proof()
        plan = _plan(store, proof)
        accepted = _accept(store, plan)
        exported = store.export_bytes()
        projection = cl4.project_shadow_cash(
            exported,
            account_scope_sha256=ACCOUNT,
            environment=broker.BrokerEnvironment.SANDBOX,
            as_of=AS_OF,
            identity_key=KEY,
        )
        reconciliation = cl4.reconcile_shadow_cash(
            exported,
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        candidate = cl4.build_adoption_candidate(
            reconciliation,
            ledger_export_bytes=exported,
            identity_key=KEY,
        )

        v1_plan = copy.copy(plan)
        object.__setattr__(v1_plan, "version", 1)
        _reason(cl4.CL4Reason.VERSION_UNSUPPORTED, _accept, store, v1_plan)
        v1_nested_proof = copy.copy(proof)
        object.__setattr__(v1_nested_proof, "version", 1)
        v1_nested_plan = copy.copy(plan)
        object.__setattr__(v1_nested_plan, "proof", v1_nested_proof)
        _reason(
            cl4.CL4Reason.VERSION_UNSUPPORTED,
            cl4.accept_from_now_opening,
            store,
            v1_nested_plan,
            confirmation=(
                "ACCEPT V3.10 CL4 FROM_NOW OPENING "
                f"{v1_nested_plan.sha256}"
            ),
            evaluated_at="invalid",
            identity_key=b"bad",
        )
        for artifact in (
            accepted.record,
            projection,
            candidate,
        ):
            _reason(
                cl4.CL4Reason.VERSION_UNSUPPORTED,
                dataclasses.replace,
                artifact,
                version=1,
            )
        v1_reconciliation = copy.copy(reconciliation)
        object.__setattr__(v1_reconciliation, "version", 1)
        _reason(
            cl4.CL4Reason.VERSION_UNSUPPORTED,
            cl4.build_adoption_candidate,
            v1_reconciliation,
            ledger_export_bytes=exported,
            identity_key=KEY,
        )
        for field, nested in (
            ("proof", v1_nested_proof),
            (
                "projection",
                copy.copy(projection),
            ),
        ):
            if field == "projection":
                object.__setattr__(nested, "version", 1)
            forged = copy.copy(reconciliation)
            object.__setattr__(forged, field, nested)
            _reason(
                cl4.CL4Reason.VERSION_UNSUPPORTED,
                cl4.build_adoption_candidate,
                forged,
                ledger_export_bytes=b"{}",
                identity_key=b"bad",
            )


def test_v310_cl4_rs1_public_boundaries_reject_dto_subclasses(
    tmp_path: Path,
) -> None:
    class ProofSubclass(cl4.BrokerCashProof):
        __slots__ = ()

    class PlanSubclass(cl4.OpeningPlan):
        __slots__ = ()

    class ReconciliationSubclass(cl4.CashReconciliation):
        __slots__ = ()

    with _new_store(tmp_path) as store:
        proof = _proof()
        plan = _plan(store, proof)
        proof_subclass = ProofSubclass(
            **{item.name: getattr(proof, item.name) for item in dataclasses.fields(proof)}
        )
        _reason(
            cl4.CL4Reason.TYPE_INVALID,
            cl4.prepare_from_now_opening,
            store.export_bytes(),
            proof_subclass,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        plan_subclass = PlanSubclass(
            **{item.name: getattr(plan, item.name) for item in dataclasses.fields(plan)}
        )
        _reason(
            cl4.CL4Reason.TYPE_INVALID,
            cl4.accept_from_now_opening,
            store,
            plan_subclass,
            confirmation=(
                "ACCEPT V3.10 CL4 FROM_NOW OPENING "
                f"{plan_subclass.sha256}"
            ),
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        accepted = _accept(store, plan)
        reconciliation = cl4.reconcile_shadow_cash(
            store.export_bytes(),
            proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        reconciliation_subclass = ReconciliationSubclass(
            **{
                item.name: getattr(reconciliation, item.name)
                for item in dataclasses.fields(reconciliation)
            }
        )
        _reason(
            cl4.CL4Reason.TYPE_INVALID,
            cl4.build_adoption_candidate,
            reconciliation_subclass,
            ledger_export_bytes=store.export_bytes(),
            identity_key=KEY,
        )
        assert accepted.record.opening_money == proof.cash


def test_v310_cl4_19_static_no_authority_drift_and_exact_two_mutators() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported <= {"__future__", "collections", "dataclasses", "datetime", "enum", "hashlib", "hmac", "json", "re", "trading_robot", "types", "typing"}
    text = MODULE_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("sqlite3", "requests", "urllib", "socket", "getenv(", "os.environ", "time.time", "datetime.now", "random", "cashavailability"):
        assert forbidden not in text
    mutators = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"append_observation", "append_transaction", "append_status_event", "append_correction_bundle"}
    ]
    mutators.sort(key=lambda node: node.lineno)
    assert [node.func.attr for node in mutators] == ["append_observation", "append_transaction"]
    parent: dict[ast.AST, ast.AST] = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for call in mutators:
        node: ast.AST = call
        while not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node = parent[node]
        assert node.name == "accept_from_now_opening"


def test_v310_cl4_20_three_path_delta_and_predecessor_custody() -> None:
    changed = subprocess.run(["git", "diff", "--name-only", ACCEPTED_CONTRACT_HEAD], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    assert set(changed) <= {
        "current/trading_robot/cash_ledger_opening_reconciliation.py",
        "current/tests/test_v3_10_cash_ledger_opening_reconciliation.py",
        "current/tests/fixtures/v3_10_cash_ledger_opening_reconciliation_vectors.json",
    }
    protected = [
        "current/trading_robot/cash_ledger_domain.py",
        "current/tests/test_v3_10_cash_ledger_domain.py",
        "current/tests/fixtures/v3_10_cash_ledger_vectors.json",
        "current/trading_robot/cash_ledger_persistence.py",
        "current/tests/test_v3_10_cash_ledger_persistence.py",
        "current/tests/fixtures/v3_10_cash_ledger_persistence_vectors.json",
        "current/trading_robot/broker_read_adapters.py",
        "current/tests/test_v3_10_broker_read_adapters.py",
        "current/tests/fixtures/v3_10_broker_read_adapters_vectors.json",
    ]
    result = subprocess.run(["git", "diff", "--quiet", CL3_PREDECESSOR, "--", *protected], cwd=ROOT, check=False)
    assert result.returncode == 0


def test_v310_cl4_21_cross_language_fixture_exact_known_answers(
    fixture: dict[str, object],
    tmp_path: Path,
    cl2_vectors: dict[str, dict[str, object]],
) -> None:
    assert fixture["domain"] == "v3.10-cl4-opening-reconciliation-fixture"
    assert fixture["version"] == 2
    known = fixture["known_answer"]
    with _new_store(tmp_path) as store:
        baseline = store.export_bytes()
        proof = _proof()
        plan = _plan(store, proof)
        staged_export, expected_post_export = cl4._assemble_prospective_exports(plan)
        accepted = _accept(store, plan)
        exported = store.export_bytes()
        assert exported == expected_post_export
        projection = cl4.project_shadow_cash(exported, account_scope_sha256=ACCOUNT, environment=broker.BrokerEnvironment.SANDBOX, as_of=AS_OF, identity_key=KEY)
        reconciliation = cl4.reconcile_shadow_cash(exported, proof, evaluated_at=EVALUATED_AT, identity_key=KEY)
        candidate = cl4.build_adoption_candidate(reconciliation, ledger_export_bytes=exported, identity_key=KEY)
        plus_one_nano_proof = _proof(response={"totalAmountCurrencies": {"currency": "RUB", "units": "123", "nano": 500_000_001}})
        discrepancy = cl4.reconcile_shadow_cash(
            exported,
            plus_one_nano_proof,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
    descriptor = _descriptor(cl2_vectors)
    incomplete_root = tmp_path / "incomplete"
    incomplete_root.mkdir()
    with _new_store(incomplete_root, descriptors=(descriptor,)) as store:
        proof_for_incomplete = _proof()
        _accept(store, _plan(store, proof_for_incomplete))
        unresolved_content = {"operation_kind": "UNRESOLVED"}
        unresolved_source = ledger.SourceIdentity(
            account_scope_sha256=ACCOUNT,
            source_kind="SYNTHETIC",
            source_scope_sha256=hashlib.sha256(b"unresolved-scope").hexdigest(),
            source_content_sha256=persistence.sha256_hex(
                persistence.canonical_json_bytes(unresolved_content)
            ),
        )
        unresolved = persistence.InboxObservation.create(
            descriptor=descriptor,
            content=unresolved_content,
            source=unresolved_source,
            observed_at=AS_OF,
            provenance_sha256=hashlib.sha256(b"unresolved-proof").hexdigest(),
        )
        store.append_observation(
            unresolved,
            expected_store_revision=store.snapshot().store_revision,
        )
        incomplete = cl4.reconcile_shadow_cash(
            store.export_bytes(),
            proof_for_incomplete,
            evaluated_at=EVALUATED_AT,
            identity_key=KEY,
        )
        incomplete_export = store.export_bytes()
    actual = {
        "baseline_export": (persistence.parse_canonical_json(baseline), baseline.decode("ascii"), persistence.sha256_hex(baseline)),
        "proof": (proof.to_canonical_dict(), proof.canonical_bytes.decode("ascii"), proof.sha256),
        "codec": (cl4.CL4_OPENING_CODEC.to_canonical_dict(), cl4.CL4_OPENING_CODEC.canonical_bytes.decode("ascii"), cl4.CL4_OPENING_CODEC.sha256),
        "content": (json.loads(plan.observation.content_json_ascii), plan.observation.content_json_ascii, plan.observation.source.source_content_sha256),
        "observation": (plan.observation.to_canonical_dict(), plan.observation.canonical_bytes.decode("ascii"), plan.observation.sha256),
        "transaction": (plan.transaction.to_canonical_dict(), plan.transaction.canonical_bytes.decode("ascii"), plan.transaction.sha256),
        "plan": (plan.to_canonical_dict(), plan.canonical_bytes.decode("ascii"), plan.sha256),
        "staged_export": (persistence.parse_canonical_json(staged_export), staged_export.decode("ascii"), persistence.sha256_hex(staged_export)),
        "post_opening_export": (persistence.parse_canonical_json(exported), exported.decode("ascii"), persistence.sha256_hex(exported)),
        "record": (accepted.record.to_canonical_dict(), accepted.record.canonical_bytes.decode("ascii"), accepted.record.sha256),
        "projection": (projection.to_canonical_dict(), projection.canonical_bytes.decode("ascii"), projection.sha256),
        "matched_reconciliation": (reconciliation.to_canonical_dict(), reconciliation.canonical_bytes.decode("ascii"), reconciliation.sha256),
        "plus_one_nano_proof": (plus_one_nano_proof.to_canonical_dict(), plus_one_nano_proof.canonical_bytes.decode("ascii"), plus_one_nano_proof.sha256),
        "discrepancy_reconciliation": (discrepancy.to_canonical_dict(), discrepancy.canonical_bytes.decode("ascii"), discrepancy.sha256),
        "incomplete_export": (persistence.parse_canonical_json(incomplete_export), incomplete_export.decode("ascii"), persistence.sha256_hex(incomplete_export)),
        "incomplete_projection": (incomplete.projection.to_canonical_dict(), incomplete.projection.canonical_bytes.decode("ascii"), incomplete.projection.sha256),
        "incomplete_reconciliation": (incomplete.to_canonical_dict(), incomplete.canonical_bytes.decode("ascii"), incomplete.sha256),
        "adoption_candidate": (candidate.to_canonical_dict(), candidate.canonical_bytes.decode("ascii"), candidate.sha256),
    }
    for name, (canonical_dict, canonical_ascii, sha256) in actual.items():
        assert known[name]["canonical"] == canonical_dict
        assert known[name]["canonical_json_ascii"] == canonical_ascii
        assert known[name]["sha256"] == sha256
    assert known["response"]["canonical_json_ascii"] == _canonical(RESPONSE).decode("ascii")
    assert known["response"]["sha256"] == proof.response_canonical_sha256
    assert known["response"]["proof_identity_hmac_sha256"] == proof.proof_identity_sha256
