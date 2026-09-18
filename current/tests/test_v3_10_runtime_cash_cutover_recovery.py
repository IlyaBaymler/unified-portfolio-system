from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import requests

from trading_robot import broker_read_adapters as cl3
from trading_robot import cash_availability as cl5
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import cash_ledger_persistence as persistence
from trading_robot import runtime_cash_authority as cl7
from trading_robot import tbank_sandbox
from trading_robot.cash_ledger_domain import Money
from trading_robot.central_order_manager import (
    CentralOrderCandidate,
    CentralOrderManager,
    CentralOrderStateError,
    CentralOrderStore,
    ExecutionAuthorization,
    central_reservation_projection_hash,
)
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    PortfolioMigrationMetadata,
    PortfolioState,
    SnapshotFreshness,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.sandbox_execution_adapter import (
    SandboxDispatchResult,
    SandboxExecutionAdapter,
    SandboxExecutionPolicy,
    _exact_provider_rejection,
    _require_cl7_proof_fresh,
)
from trading_robot.tbank_sandbox import TBankAPIError, TBankSandboxClient

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
FIXTURE = CURRENT / "tests" / "fixtures" / "v3_10_runtime_cash_cutover_vectors.json"
CONTRACT = (
    ROOT / "docs" / "project" / "V3_10_CL7_RUNTIME_CUTOVER_RECOVERY_CONTRACT_RU.md"
)
ACCEPTED_CONTRACT_HEAD = "2eaba15d1260ab84a0ebc1b3bc8f950e497931ca"
STABLE_PREDECESSOR = "2667dbea770a1a5df25bbba67f0e7e8b5f27dc63"
KEY = bytes(range(32))
KEY_ID = "CL5_TEST_KEY_V1"
RAW_ACCOUNT = "sandbox-account-0001"
ACCOUNT = "15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3"
T0 = "2026-09-11T10:00:00.000000000Z"
T1 = "2026-09-11T10:00:01.000000000Z"
T2 = "2026-09-11T10:00:02.000000000Z"
T3 = "2026-09-11T10:00:03.000000000Z"
T4 = "2026-09-11T10:00:04.000000000Z"
T5 = "2026-09-11T10:00:05.000000000Z"
T6 = "2026-09-11T10:00:06.000000000Z"
T65 = "2026-09-11T10:00:06.500000000Z"


def _withdraw_limits_transport_observation(
    response: dict[str, object],
    *,
    account_id: str = RAW_ACCOUNT,
) -> cl5.WithdrawLimitsTransportObservation:
    class StaticTransport:
        @staticmethod
        def _post(
            service: str,
            method: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            assert service == "SandboxService"
            assert method == "GetSandboxWithdrawLimits"
            assert payload == {"accountId": account_id}
            return response

    return tbank_sandbox.TBankSandboxClient.get_withdraw_limits(
        StaticTransport(), account_id
    )


IMPLEMENTATION_PATHS = {
    "current/trading_robot/runtime_cash_authority.py",
    "current/trading_robot/tbank_sandbox.py",
    "current/trading_robot/central_order_manager.py",
    "current/trading_robot/sandbox_execution_adapter.py",
    "current/trading_robot/bot.py",
    "current/trading_robot/diagnostics.py",
    "current/trading_robot/runtime_bootstrap.py",
    "current/run_bot.py",
    "current/tools/v3_10_runtime_cash_cutover.py",
    "current/tests/test_v3_10_runtime_cash_cutover_recovery.py",
    "current/tests/fixtures/v3_10_runtime_cash_cutover_vectors.json",
}


def _assert_shallow_pull_request_custody() -> None:
    assert os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    assert event_path is not None
    pull_request = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))[
        "pull_request"
    ]
    expected = {
        "agent/v3-10-clean-cl7-contract-freeze": (
            ACCEPTED_CONTRACT_HEAD,
            3,
            11,
        ),
        "program/v3-10-v4-stable-line": (
            STABLE_PREDECESSOR,
            5,
            12,
        ),
    }.get(pull_request["base"]["ref"])
    assert expected is not None
    expected_base, expected_commits, expected_files = expected
    assert pull_request["base"]["sha"] == expected_base
    assert pull_request["base"]["repo"]["full_name"] == (
        "baimleriv/unified-portfolio-system"
    )
    assert pull_request["head"]["ref"] == "agent/v3-10-clean-cl7-implementation"
    assert pull_request["head"]["repo"]["full_name"] == (
        "baimleriv/unified-portfolio-system"
    )
    assert pull_request["commits"] == expected_commits
    assert pull_request["changed_files"] == expected_files
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


@pytest.fixture(scope="module")
def vectors() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="ascii"))


def _reason(expected: cl7.CL7RuntimeReason, callable_, *args, **kwargs):
    with pytest.raises(cl7.CL7RuntimeError) as captured:
        callable_(*args, **kwargs)
    assert captured.value.reason is expected
    assert captured.value.__cause__ is None
    assert RAW_ACCOUNT not in str(captured.value)
    assert KEY.hex() not in str(captured.value)
    return captured.value


def _proof(**changes: object) -> cl7.LockedDispatchProof:
    values: dict[str, object] = {
        "raw_intent_id": "intent-001",
        "identity_key": KEY,
        "account_scope_sha256": ACCOUNT,
        "authority_record_revision": 5,
        "authority_record_sha256": "ed8146dd11385e70fb5a0c41b9c93006f8810ab1cf916b3429bf4beb5872f3bf",
        "availability_sha256": "cd375c47f6dc528ae8e64a13dfb7de9145f5964988893c7ef20561050411e238",
        "central_order_revision": 7,
        "central_reservation_projection_hash": "5" * 64,
        "cl6_context_identity_sha256": "05409978eaff9beafb68c9c4e2a0531c15995e8f337604dbbdc964e12a975bd6",
        "cl6_context_sha256": "fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9",
        "current_lots": 0,
        "direction": "BUY",
        "evaluated_at": T6,
        "free_investable_cash": Money("RUB", 50_000_000_000),
        "identity_key_id": KEY_ID,
        "ledger_head_sha256": "4" * 64,
        "ledger_revision": 5,
        "portfolio_decision_checksum": "a" * 64,
        "portfolio_document_checksum": "ab" * 32,
        "portfolio_revision": 9,
        "reconciliation_sha256": "1" * 64,
        "reserved_cash": Money("RUB", 10_000_000_000),
        "risk_policy_hash": "d" * 64,
        "risk_state_guard_hash": "e" * 64,
        "target_lots": 1,
    }
    values.update(changes)
    return cl7.LockedDispatchProof.build(**values)


def _armed_record(vectors: dict[str, object]) -> cl7.RuntimeCashAuthorityRecord:
    raw = vectors["armed_record"]["canonical_json_ascii"].encode("ascii")
    return cl7.RuntimeCashAuthorityRecord.from_canonical_bytes(raw)


def _commit_test_transition(
    manager: cl7.RuntimeCashAuthorityManager,
    current: cl7.RuntimeCashAuthorityRecord,
    *,
    at: str,
    kind: str,
    state: cl7.RuntimeCashAuthorityState,
    **changes: object,
) -> cl7.RuntimeCashAuthorityRecord:
    candidate = manager._change(
        current,
        at=at,
        kind=kind,
        state=state,
        **changes,
    )
    with manager.store.locked():
        return manager.store._commit_unlocked(
            candidate,
            expected_revision=current.record_revision,
            expected_sha256=current.sha256,
        )


def _chain(
    root: Path,
) -> tuple[cl7.RuntimeCashAuthorityManager, cl7.RuntimeCashAuthorityRecord]:
    store = cl7.RuntimeCashAuthorityStore(root)
    manager = cl7.RuntimeCashAuthorityManager(store)
    current = store.bootstrap(transition_at=T0)
    current = _commit_test_transition(
        manager,
        current,
        at=T1,
        kind="PREPARE_CUTOVER",
        state=cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
        cutover_generation=1,
        account_scope_sha256=ACCOUNT,
        identity_key_id=KEY_ID,
        activation_context_sha256=None,
        ledger_head_sha256=None,
        ledger_revision=None,
        opening_cutoff=None,
        opening_record_sha256=None,
        operations_complete_through=None,
        pending_dispatch_proof_sha256=None,
    )
    current = _commit_test_transition(
        manager,
        current,
        at=T2,
        kind="PREPARATION_EVIDENCE_BOUND",
        state=cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
        ledger_revision=5,
        ledger_head_sha256="4" * 64,
        opening_cutoff=T0,
        opening_record_sha256="7" * 64,
        operations_complete_through="2026-09-11T10:00:00.000000001Z",
    )
    current = _commit_test_transition(
        manager,
        current,
        at=T3,
        kind="CONFIRM_CUTOVER",
        state=cl7.RuntimeCashAuthorityState.CUTOVER_CONFIRMED,
    )
    current = _commit_test_transition(
        manager,
        current,
        at=T4,
        kind="ACTIVATE_EXACT",
        state=cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ever_exact_activated=True,
        activation_context_sha256="fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9",
        ledger_revision=5,
        ledger_head_sha256="4" * 64,
        operations_complete_through="2026-09-11T10:00:00.000000001Z",
    )
    armed = manager.arm(
        raw_account_id=RAW_ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
        confirmation=manager.ARM_PHRASE,
        transition_at=T5,
    )
    return manager, armed


def _central_runtime(
    root: Path,
) -> tuple[PortfolioRepository, CentralOrderManager, object]:
    portfolio = PortfolioState(
        version=2,
        account=AccountState(
            account_id=RAW_ACCOUNT,
            total_value=1_000_000.0,
            securities_value=0.0,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", 1_000_000.0),),
        ),
        snapshot_at=T5,
        generated_at=T5,
        freshness=SnapshotFreshness.FRESH,
        source="PORTFOLIO_MANAGER",
        positions=(),
        warnings=(),
        state_status="READY",
        blocking=False,
        revision=9,
    )
    repository = PortfolioRepository(root / "portfolio_state.json")
    repository.save(portfolio)
    manager = CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"),
        account_id=RAW_ACCOUNT,
    )
    from trading_robot.portfolio_preflight import PortfolioSnapshotLease

    lease = PortfolioSnapshotLease.from_state(portfolio, leased_at=T5)
    authorization = ExecutionAuthorization(
        account_id=RAW_ACCOUNT,
        instrument_id="uid-sber",
        authorized_target_lots=1,
        portfolio_revision=lease.revision,
        portfolio_decision_checksum=lease.decision_checksum,
        portfolio_document_checksum=lease.document_checksum,
        available_cash_kopecks=100_000_000,
        preflight_status="PASS",
        pending_order_ids=(),
        uncertain_order_ids=(),
        risk_status="PASS",
        risk_decision_id="risk-decision-1",
        risk_policy_hash="d" * 64,
        risk_order_allowed=True,
        authorized_at=T5,
        risk_state_guard_hash="e" * 64,
    )
    candidate = CentralOrderCandidate(
        account_id=RAW_ACCOUNT,
        instrument_id="uid-sber",
        ticker="SBER",
        runtime_key="runtime-uid-sber",
        runtime_config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=T5,
        strategy_id="sma",
        strategy_profile_hash="c" * 64,
        current_lots=0,
        target_lots=1,
        estimated_price_kopecks=10_000,
        lot_size=10,
        created_at=T5,
    )
    intent = manager.enqueue(candidate, authorization).intent
    return repository, manager, intent


def _central_proof(
    authority: cl7.RuntimeCashAuthorityRecord,
    central: CentralOrderManager,
    intent: object,
) -> cl7.LockedDispatchProof:
    state = central.state()
    return _proof(
        raw_intent_id=intent.intent_id,
        authority_record_revision=authority.record_revision,
        authority_record_sha256=authority.sha256,
        central_order_revision=state.revision,
        central_reservation_projection_hash=central_reservation_projection_hash(state),
        evaluated_at=T6,
    )


def test_contract_and_fixture_custody(vectors: dict[str, object]) -> None:
    assert vectors["accepted_contract_commit"] == ACCEPTED_CONTRACT_HEAD
    base_object = subprocess.run(
        ["git", "cat-file", "-e", f"{ACCEPTED_CONTRACT_HEAD}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if base_object.returncode != 0:
        _assert_shallow_pull_request_custody()
        revision = "HEAD"
    else:
        revision = ACCEPTED_CONTRACT_HEAD
    blob = subprocess.run(
        ["git", "rev-parse", f"{revision}:docs/project/{CONTRACT.name}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert blob == "b57cdbcfeba65901c015a554ccc7521daf946faf"
    assert vectors["stable_line_predecessor"] == STABLE_PREDECESSOR


def test_public_surface_is_exact() -> None:
    assert cl7.__all__ == (
        "RuntimeCashAuthorityState",
        "RuntimeCashAuthorityOwner",
        "CL7RuntimeReason",
        "CL7RuntimeError",
        "RuntimeCashAuthorityRecord",
        "LockedDispatchProof",
        "RuntimeCashAuthorityStore",
        "RuntimeCashAuthorityManager",
        "legacy_execution_guard",
    )


def test_legacy_record_kat(vectors: dict[str, object]) -> None:
    record = cl7.RuntimeCashAuthorityRecord.bootstrap(T0)
    assert (
        record.canonical_bytes.decode("ascii")
        == vectors["legacy_record"]["canonical_json_ascii"]
    )
    assert record.sha256 == vectors["legacy_record"]["sha256"]
    assert record.owner is cl7.RuntimeCashAuthorityOwner.LEGACY_CASH_AUTHORITY


def test_armed_record_kat(vectors: dict[str, object]) -> None:
    record = _armed_record(vectors)
    assert record.sha256 == vectors["armed_record"]["sha256"]
    assert record.owner is cl7.RuntimeCashAuthorityOwner.CL7_EXACT_CASH_AUTHORITY


def test_locked_proof_kat(vectors: dict[str, object]) -> None:
    proof = _proof()
    assert proof.intent_scope_sha256 == vectors["intent_scope_sha256"]
    assert proof.proof_identity_sha256 == vectors["proof_identity_sha256"]
    assert proof.sha256 == vectors["locked_dispatch_proof_sha256"]
    proof.verify_identity(raw_intent_id="intent-001", identity_key=KEY)


@pytest.mark.parametrize(
    "field,value",
    [
        ("direction", "SELL"),
        ("current_lots", 1),
        ("target_lots", 2),
        ("reserved_cash", Money("RUB", 10_000_000_001)),
        ("free_investable_cash", Money("RUB", 50_000_000_001)),
        ("portfolio_revision", 10),
        ("central_order_revision", 8),
        ("evaluated_at", "2026-09-11T10:00:06.000000001Z"),
        ("risk_policy_hash", "c" * 64),
        ("ledger_head_sha256", "3" * 64),
    ],
)
def test_proof_mutations_change_identity(field: str, value: object) -> None:
    assert _proof(**{field: value}).sha256 != _proof().sha256


def test_proof_identity_rejects_wrong_raw_intent() -> None:
    _reason(
        cl7.CL7RuntimeReason.DISPATCH_PROOF_INVALID,
        _proof().verify_identity,
        raw_intent_id="intent-002",
        identity_key=KEY,
    )


def test_every_serialized_proof_field_mutation_invalidates_identity() -> None:
    canonical = _proof().to_canonical_dict()
    for field, original in canonical.items():
        mutated = copy.deepcopy(canonical)
        if isinstance(original, dict):
            mutated[field]["minor_units"] = str(int(mutated[field]["minor_units"]) + 1)
        elif field == "direction":
            mutated[field] = "SELL"
        elif field == "domain":
            mutated[field] = "INVALID_DOMAIN"
        elif field == "evaluated_at":
            mutated[field] = "2026-09-11T10:00:06.000000001Z"
        elif field == "identity_key_id":
            mutated[field] = "test-key-v2"
        elif field == "version":
            mutated[field] = 2
        elif isinstance(original, int):
            mutated[field] = original + 1
        elif isinstance(original, str) and original.isdecimal():
            mutated[field] = str(int(original) + 1)
        elif isinstance(original, str) and len(original) == 64:
            mutated[field] = ("0" if original[0] != "0" else "1") + original[1:]
        else:  # pragma: no cover - protects additions to the frozen keyset
            raise AssertionError(f"unhandled proof field: {field}")
        with pytest.raises(cl7.CL7RuntimeError):
            parsed = cl7.LockedDispatchProof.from_canonical_dict(mutated)
            parsed.verify_identity(raw_intent_id="intent-001", identity_key=KEY)


def test_account_scope_is_exact_cl3_hmac(vectors: dict[str, object]) -> None:
    assert (
        cl7.derive_account_scope(RAW_ACCOUNT, identity_key=KEY, identity_key_id=KEY_ID)
        == vectors["account_scope_sha256"]
    )


def test_missing_compatibility_requires_all_custody_absent(tmp_path: Path) -> None:
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    assert store.load().state is cl7.RuntimeCashAuthorityState.LEGACY_ACTIVE
    store.ledger_path.write_bytes(b"ledger")
    _reason(cl7.CL7RuntimeReason.AUTHORITY_RECORD_MISSING, store.load)


def test_bootstrap_and_full_state_chain(tmp_path: Path) -> None:
    manager, armed = _chain(tmp_path)
    assert armed.record_revision == 5
    assert armed.cutover_generation == 1
    assert armed.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_ARMED
    assert manager.status() == armed
    previous = cl7.RuntimeCashAuthorityRecord.from_canonical_bytes(
        manager.store.lastgood_path.read_bytes()
    )
    assert previous.record_revision == 4
    assert previous.sha256 == armed.previous_record_sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_scope_sha256", "a" * 64),
        ("activation_context_sha256", "a" * 64),
        ("identity_key_id", "KEY"),
        ("ledger_head_sha256", "a" * 64),
        ("ledger_revision", 0),
        ("opening_cutoff", T0),
        ("opening_record_sha256", "a" * 64),
        ("operations_complete_through", T0),
        ("pending_dispatch_proof_sha256", "a" * 64),
    ],
)
def test_bootstrap_rejects_every_non_null_evidence_field(
    field: str,
    value: object,
) -> None:
    bootstrap = cl7.RuntimeCashAuthorityRecord.bootstrap(T0)
    _reason(
        cl7.CL7RuntimeReason.STATE_INVALID,
        dataclasses.replace,
        bootstrap,
        **{field: value},
    )


def _prepared_candidate(
    manager: cl7.RuntimeCashAuthorityManager,
    current: cl7.RuntimeCashAuthorityRecord,
) -> cl7.RuntimeCashAuthorityRecord:
    return manager._change(
        current,
        at=T1,
        kind="PREPARE_CUTOVER",
        state=cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
        cutover_generation=1,
        account_scope_sha256=ACCOUNT,
        identity_key_id=KEY_ID,
    )


def test_authority_commit_prepares_all_temps_before_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    current = store.bootstrap(transition_at=T0)
    manager = cl7.RuntimeCashAuthorityManager(store)
    candidate = _prepared_candidate(manager, current)
    events: list[str] = []
    original_write = store._write_temp
    original_replace = store._replace_prepared

    def write(path: Path, value: bytes) -> Path:
        events.append("prepare:" + path.name)
        return original_write(path, value)

    def replace_temp(temporary: Path, path: Path) -> None:
        events.append("replace:" + path.name)
        original_replace(temporary, path)

    monkeypatch.setattr(store, "_write_temp", write)
    monkeypatch.setattr(store, "_replace_prepared", replace_temp)
    with store.locked():
        committed = store._commit_unlocked(
            candidate,
            expected_revision=current.record_revision,
            expected_sha256=current.sha256,
        )
    assert committed == candidate
    assert events == [
        "prepare:runtime_cash_authority.json",
        "prepare:runtime_cash_authority.json.sha256",
        "prepare:runtime_cash_authority.json.lastgood",
        "replace:runtime_cash_authority.json.lastgood",
        "replace:runtime_cash_authority.json",
        "replace:runtime_cash_authority.json.sha256",
    ]


def test_authority_bootstrap_prepares_pair_before_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    events: list[str] = []
    original_write = store._write_temp
    original_replace = store._replace_prepared

    def write(path: Path, value: bytes) -> Path:
        events.append("prepare:" + path.name)
        return original_write(path, value)

    def replace_temp(temporary: Path, path: Path) -> None:
        events.append("replace:" + path.name)
        original_replace(temporary, path)

    monkeypatch.setattr(store, "_write_temp", write)
    monkeypatch.setattr(store, "_replace_prepared", replace_temp)
    record = store.bootstrap(transition_at=T0)
    assert record.record_revision == 0
    assert events == [
        "prepare:runtime_cash_authority.json",
        "prepare:runtime_cash_authority.json.sha256",
        "replace:runtime_cash_authority.json",
        "replace:runtime_cash_authority.json.sha256",
    ]


@pytest.mark.parametrize(
    "replace_index,fail_after,expected",
    [
        (1, False, "OLD"),
        (1, True, "BLOCKED"),
        (2, False, "BLOCKED"),
        (2, True, "BLOCKED"),
        (3, False, "BLOCKED"),
        (3, True, "NEW"),
    ],
)
def test_authority_commit_crash_outcome_at_each_replace_boundary(
    tmp_path: Path,
    monkeypatch,
    replace_index: int,
    fail_after: bool,
    expected: str,
) -> None:
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    current = store.bootstrap(transition_at=T0)
    manager = cl7.RuntimeCashAuthorityManager(store)
    candidate = _prepared_candidate(manager, current)
    original_replace = store._replace_prepared
    calls = 0

    def crash(temporary: Path, path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == replace_index and not fail_after:
            raise OSError("synthetic crash before replace")
        original_replace(temporary, path)
        if calls == replace_index and fail_after:
            raise OSError("synthetic crash after replace")

    monkeypatch.setattr(store, "_replace_prepared", crash)
    with store.locked(), pytest.raises(OSError):
        store._commit_unlocked(
            candidate,
            expected_revision=current.record_revision,
            expected_sha256=current.sha256,
        )
    if expected == "OLD":
        assert store.load(allow_missing_legacy=False) == current
    elif expected == "NEW":
        assert store.load(allow_missing_legacy=False) == candidate
    else:
        with pytest.raises(cl7.CL7RuntimeError):
            store.load(allow_missing_legacy=False)


def test_cas_and_checksum_fail_closed(tmp_path: Path) -> None:
    manager, armed = _chain(tmp_path)
    candidate = dataclasses.replace(
        armed,
        state=cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        record_revision=6,
        previous_record_sha256=armed.sha256,
        transition_at=T6,
        transition_kind="DISARM_EXACT",
    )
    with manager.store.locked():
        _reason(
            cl7.CL7RuntimeReason.CAS_CONFLICT,
            manager.store._commit_unlocked,
            candidate,
            expected_revision=4,
            expected_sha256=armed.sha256,
        )
    manager.store.checksum_path.write_text("0" * 64 + "\n", encoding="ascii")
    _reason(cl7.CL7RuntimeReason.AUTHORITY_CHECKSUM_INVALID, manager.status)


def test_corrupt_active_never_restores_lastgood(tmp_path: Path) -> None:
    manager, _armed = _chain(tmp_path)
    previous = manager.store.lastgood_path.read_bytes()
    manager.store.path.write_bytes(b"{}")
    _reason(cl7.CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT, manager.status)
    assert manager.store.path.read_bytes() == b"{}"
    assert manager.store.lastgood_path.read_bytes() == previous


def test_unsafe_transition_primitives_are_not_public(tmp_path: Path) -> None:
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    manager = cl7.RuntimeCashAuthorityManager(store)
    for name in (
        "prepare",
        "bind_preparation_evidence",
        "confirm",
        "activate",
        "sync_advanced",
        "record_dispatch_attempt",
        "record_dispatch_attempt_locked",
        "clear_dispatch_locked",
        "rollback",
    ):
        assert not hasattr(manager, name)
    assert not hasattr(store, "commit")
    _reason(
        cl7.CL7RuntimeReason.CONFIRMATION_INVALID,
        manager._phrase,
        "prepare",
        manager.PREPARE_PHRASE,
    )
    _manager, current = _chain(tmp_path)
    _reason(
        cl7.CL7RuntimeReason.ACCOUNT_SCOPE_INVALID,
        manager._account,
        current,
        "other",
        KEY,
        KEY_ID,
    )


def test_disarm_cancel_and_rollback_rules(tmp_path: Path) -> None:
    manager, _armed = _chain(tmp_path)
    disarmed = manager.disarm(transition_at=T6)
    assert disarmed.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    rolled = _commit_test_transition(
        manager,
        disarmed,
        at=T65,
        kind="ROLLBACK_TO_LEGACY",
        state=cl7.RuntimeCashAuthorityState.LEGACY_ACTIVE,
    )
    assert rolled.state is cl7.RuntimeCashAuthorityState.LEGACY_ACTIVE
    assert rolled.ever_exact_activated is True


def test_pending_attempt_kat_and_no_rollback(
    tmp_path: Path, vectors: dict[str, object]
) -> None:
    armed = _armed_record(vectors)
    proof = _proof()
    pending = dataclasses.replace(
        armed,
        state=cl7.RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
        record_revision=6,
        previous_record_sha256=armed.sha256,
        transition_at=T65,
        transition_kind="DISPATCH_ATTEMPT_RECORDED",
        post_attempt_count=1,
        pending_dispatch_proof_sha256=proof.sha256,
    )
    assert pending.sha256 == vectors["pending_record_sha256"]
    manager, current = _chain(tmp_path)
    bound = _proof(
        authority_record_revision=current.record_revision,
        authority_record_sha256=current.sha256,
    )
    with manager.store.locked():
        pending = manager._record_dispatch_attempt_locked(
            current,
            bound,
            transition_at=T65,
        )
    assert pending.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    _reason(
        cl7.CL7RuntimeReason.ROLLBACK_FORBIDDEN_AFTER_ATTEMPT,
        manager.rollback_runtime,
        confirmation=manager.ROLLBACK_PHRASE,
    )


def test_restart_closure_disarms_and_preserves_attempt_count(tmp_path: Path) -> None:
    manager, current = _chain(tmp_path)
    proof = _proof(authority_record_revision=5, authority_record_sha256=current.sha256)
    with manager.store.locked():
        pending = manager._record_dispatch_attempt_locked(
            current,
            proof,
            transition_at=T65,
        )
    intent = SimpleNamespace(
        intent_id="intent-001",
        cl7_locked_dispatch_proof=proof.to_canonical_dict(),
        cl7_locked_dispatch_proof_sha256=proof.sha256,
        status="RECONCILED",
        outcome="FILLED",
    )
    central = SimpleNamespace(
        inspect_locked=lambda callback: callback(
            SimpleNamespace(
                intents=(intent,),
                revision=proof.central_order_revision + 1,
            )
        )
    )
    closed, disposition = manager.recover_runtime(
        central_manager=central,
        raw_account_id=RAW_ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
        transition_at="2026-09-11T10:00:07.000000000Z",
    )
    assert disposition == "RECOVERY_CLOSED_DISARMED"
    assert pending.post_attempt_count == closed.post_attempt_count == 1
    assert closed.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert closed.pending_dispatch_proof_sha256 is None


def test_legacy_guard_is_held_and_blocks_nonlegacy(tmp_path: Path) -> None:
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    store.bootstrap(transition_at=T0)
    with cl7.legacy_execution_guard(store) as record:
        assert record.state is cl7.RuntimeCashAuthorityState.LEGACY_ACTIVE
    manager = cl7.RuntimeCashAuthorityManager(store)
    current = manager.status()
    _commit_test_transition(
        manager,
        current,
        at=T1,
        kind="PREPARE_CUTOVER",
        state=cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
        cutover_generation=1,
        account_scope_sha256=ACCOUNT,
        identity_key_id=KEY_ID,
    )
    _reason(cl7.CL7RuntimeReason.DISPATCH_NOT_ARMED, _enter_guard, store)


def test_adapter_without_injected_manager_discovers_exact_authority(
    tmp_path: Path,
) -> None:
    authority_manager, _armed = _chain(tmp_path)
    repository, central, intent = _central_runtime(tmp_path)

    class Transport:
        post_calls = 0

        def post_order(self, *_args, **_kwargs):
            self.post_calls += 1
            raise AssertionError("exact custody must block the legacy path")

    transport = Transport()
    adapter = SandboxExecutionAdapter(
        transport,
        central,
        SandboxExecutionPolicy(
            account_id=RAW_ACCOUNT,
            enabled=True,
            confirmation="ENABLE V3.8 SANDBOX EXECUTION",
        ),
        risk_runtime=_ExactRiskGate(),
    )
    assert adapter.cash_authority_manager is not authority_manager
    result = adapter.dispatch_next(
        repository,
        expected_intent_id=intent.intent_id,
    )
    assert result.status == "CL7_CONTEXT_UNAVAILABLE"
    assert transport.post_calls == 0


def test_adapter_legacy_dispatch_holds_outer_authority_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, central, intent = _central_runtime(tmp_path)
    adapter = SandboxExecutionAdapter(
        SimpleNamespace(),
        central,
        SandboxExecutionPolicy(
            account_id=RAW_ACCOUNT,
            enabled=True,
            confirmation="ENABLE V3.8 SANDBOX EXECUTION",
        ),
        risk_runtime=_ExactRiskGate(),
    )

    def dispatch(_intent, _repository):
        competing = cl7.RuntimeCashAuthorityStore(
            tmp_path,
            lock_timeout_seconds=0,
        )
        _reason(cl7.CL7RuntimeReason.LOCK_UNAVAILABLE, _enter_guard, competing)
        return SandboxDispatchResult(status="LEGACY_LOCK_HELD")

    monkeypatch.setattr(adapter, "_dispatch_legacy_authorized", dispatch)
    result = adapter.dispatch_next(
        repository,
        expected_intent_id=intent.intent_id,
    )
    assert result.status == "LEGACY_LOCK_HELD"


def _enter_guard(store: cl7.RuntimeCashAuthorityStore) -> None:
    with cl7.legacy_execution_guard(store):
        pass


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 499])
def test_exact_provider_rejection_finite_predicate(status: int) -> None:
    assert _exact_provider_rejection(
        TBankAPIError(
            "redacted",
            status_code=status,
            transient=False,
            service="SandboxService",
            method="PostSandboxOrder",
            direct_response=True,
            redirect_followed=False,
        )
    )


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, None])
def test_ambiguous_provider_outcomes_never_clear(status: int | None) -> None:
    assert not _exact_provider_rejection(
        TBankAPIError(
            "redacted",
            status_code=status,
            transient=status is None,
            service="SandboxService",
            method="PostSandboxOrder",
            direct_response=status is not None,
            redirect_followed=False,
        )
    )


def test_redirect_and_synthetic_error_are_not_explicit_rejection() -> None:
    assert not _exact_provider_rejection(
        TBankAPIError(
            "redacted",
            status_code=400,
            service="SandboxService",
            method="PostSandboxOrder",
            direct_response=True,
            redirect_followed=True,
        )
    )
    assert not _exact_provider_rejection(
        TBankAPIError(
            "redacted",
            status_code=400,
            service="SandboxService",
            method="PostSandboxOrder",
            direct_response=False,
        )
    )


def test_freshness_exact_edge_future_and_stale() -> None:
    _require_cl7_proof_fresh(_proof(), "2026-09-11T10:00:16.000000000Z")
    _reason(
        cl7.CL7RuntimeReason.CONTEXT_STALE,
        _require_cl7_proof_fresh,
        _proof(),
        "2026-09-11T10:00:16.000000001Z",
    )
    _reason(
        cl7.CL7RuntimeReason.CONTEXT_STALE,
        _require_cl7_proof_fresh,
        _proof(),
        "2026-09-11T10:00:05.999999999Z",
    )


def test_cursor_read_and_order_post_are_one_attempt_no_redirect(monkeypatch) -> None:
    client = TBankSandboxClient("dummy-token", max_retries=3)
    calls: list[dict[str, object]] = []

    class Response:
        ok = True
        status_code = 200
        headers: ClassVar[dict[str, str]] = {}
        history: tuple[object, ...] = ()

        @staticmethod
        def json() -> dict[str, object]:
            return {"items": [], "hasNext": False, "nextCursor": ""}

    def fake_post(_url, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(client._session, "post", fake_post)
    client.get_operations_by_cursor_once({"accountId": "private"}, 1_000_000_000)
    client.post_order_once("private", "instrument", 1, "BUY", order_id="intent-001")
    assert len(calls) == 2
    assert all(call["allow_redirects"] is False for call in calls)


def test_post_once_timeout_never_retries(monkeypatch) -> None:
    client = TBankSandboxClient("dummy-token", max_retries=9)
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout("lost")

    monkeypatch.setattr(client._session, "post", fail)
    with pytest.raises(TBankAPIError):
        client.post_order_once("private", "instrument", 1, "BUY", order_id="intent-001")
    assert calls == 1


def test_central_lease_persists_exact_proof_before_d3_recovery(
    tmp_path: Path,
) -> None:
    authority_manager, authority = _chain(tmp_path)
    repository, central, intent = _central_runtime(tmp_path)
    proof = _central_proof(authority, central, intent)
    with (
        authority_manager.store.locked(),
        repository.locked_snapshot(expected_account_id=RAW_ACCOUNT) as portfolio,
        central.locked_dispatch_lease(
            repository,
            expected_intent_id=intent.intent_id,
            locked_portfolio_state=portfolio,
            validator=lambda _state, _intent: proof,
        ) as lease,
    ):
        assert lease.intent.status == "IN_FLIGHT"
        assert lease.intent.cl7_locked_dispatch_proof == proof.to_canonical_dict()
        assert lease.intent.cl7_locked_dispatch_proof_sha256 == proof.sha256
    unchanged, disposition = authority_manager.recover_runtime(
        central_manager=central,
        raw_account_id=RAW_ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
        transition_at=T65,
    )
    assert disposition == "D3_PRE_SUBMIT_FAILED"
    assert unchanged == authority
    resolved = central.state().intents[0]
    assert resolved.status == "FAILED"
    assert resolved.outcome == "PRE_SUBMIT_FAILED"


@pytest.mark.parametrize("mutation", ["whitespace", "duplicate"])
def test_central_rejects_noncanonical_locked_proof_text(
    tmp_path: Path,
    mutation: str,
) -> None:
    authority_manager, authority = _chain(tmp_path)
    repository, central, intent = _central_runtime(tmp_path)
    proof = _central_proof(authority, central, intent)
    with (
        authority_manager.store.locked(),
        repository.locked_snapshot(expected_account_id=RAW_ACCOUNT) as portfolio,
        central.locked_dispatch_lease(
            repository,
            expected_intent_id=intent.intent_id,
            locked_portfolio_state=portfolio,
            validator=lambda _state, _intent: proof,
        ) as lease,
    ):
        raw = lease.intent.to_dict()
    prefix = "CL7_LOCKED_DISPATCH_PROOF="
    detail = raw["transitions"][-1]["detail"]
    canonical = detail[len(prefix) :]
    if mutation == "whitespace":
        changed = "{ " + canonical[1:]
    else:
        changed = (
            '{"account_scope_sha256":"'
            + proof.account_scope_sha256
            + '",'
            + canonical[1:]
        )
    raw["transitions"][-1]["detail"] = prefix + changed
    with pytest.raises(CentralOrderStateError):
        type(lease.intent).from_dict(raw)


def test_d3_invalid_hmac_cannot_mutate_central(
    tmp_path: Path,
) -> None:
    authority_manager, authority = _chain(tmp_path)
    repository, central, intent = _central_runtime(tmp_path)
    proof = _central_proof(authority, central, intent)
    with (
        authority_manager.store.locked(),
        repository.locked_snapshot(expected_account_id=RAW_ACCOUNT) as portfolio,
        central.locked_dispatch_lease(
            repository,
            expected_intent_id=intent.intent_id,
            locked_portfolio_state=portfolio,
            validator=lambda _state, _intent: proof,
        ),
    ):
        pass
    with pytest.raises(cl7.CL7RuntimeError) as captured:
        central.resolve_cl7_pre_submit(
            expected_intent_id=intent.intent_id,
            expected_proof_sha256=proof.sha256,
            authority_record_revision=authority.record_revision,
            authority_record_sha256=authority.sha256,
            account_scope_sha256=authority.account_scope_sha256,
            identity_key_id=authority.identity_key_id,
            identity_key=b"wrong-key" * 4,
            ledger_revision=authority.ledger_revision,
            ledger_head_sha256=authority.ledger_head_sha256,
        )
    assert captured.value.reason is cl7.CL7RuntimeReason.DISPATCH_PROOF_INVALID
    assert central.state().intents[0].status == "IN_FLIGHT"


def test_cash_ledger_guard_holds_the_actual_sqlite_writer_lock(
    tmp_path: Path,
) -> None:
    ledger = persistence.CashLedgerStore.create(
        tmp_path / "cash_ledger_v3_10.sqlite3",
        (cl4.CL4_OPENING_CODEC, cl3.TBANK_OPERATION_CODEC),
    )
    manager = cl7.RuntimeCashAuthorityManager(cl7.RuntimeCashAuthorityStore(tmp_path))
    competing = sqlite3.connect(
        ledger.root / "store.sqlite3",
        timeout=0,
        isolation_level=None,
    )
    try:
        with (
            manager.ledger_guard(ledger),
            pytest.raises(sqlite3.OperationalError),
        ):
            competing.execute("BEGIN IMMEDIATE")
        competing.execute("BEGIN IMMEDIATE")
        blocked_manager = cl7.RuntimeCashAuthorityManager(
            cl7.RuntimeCashAuthorityStore(tmp_path, lock_timeout_seconds=0)
        )
        with (
            pytest.raises(cl7.CL7RuntimeError) as captured,
            blocked_manager.ledger_guard(ledger),
        ):
            raise AssertionError("unreachable")
        assert captured.value.reason is cl7.CL7RuntimeReason.LOCK_UNAVAILABLE
        competing.execute("ROLLBACK")
    finally:
        competing.close()
        ledger.close()


@pytest.mark.parametrize(
    "decision_kind,operation_type,payment_units,expected_status",
    [
        ("TRANSACTION_PROPOSED", "OPERATION_TYPE_INPUT", "100", "LEDGER_LINKED"),
        ("REVIEW_REQUIRED", "OPERATION_TYPE_BUY", "100", "REVIEW_REQUIRED"),
        ("NOT_LEDGER_RELEVANT", "OPERATION_TYPE_BUY", "0", "REJECTED"),
    ],
)
def test_cl3_to_cl2_sync_mapping_and_watermark_commit(
    tmp_path: Path,
    decision_kind: str,
    operation_type: str,
    payment_units: str,
    expected_status: str,
) -> None:
    source_vectors = json.loads(
        (
            CURRENT / "tests" / "fixtures" / "v3_10_broker_read_adapters_vectors.json"
        ).read_text(encoding="ascii")
    )["known_answer"]
    request = source_vectors["request"]
    response = copy.deepcopy(source_vectors["response"])
    item = response["items"][0]
    item["type"] = operation_type
    item["payment"] = {
        "currency": "RUB",
        "units": payment_units,
        "nano": 0,
    }
    if decision_kind == "REVIEW_REQUIRED":
        item["state"] = "OPERATION_STATE_PROGRESS"
    elif decision_kind == "NOT_LEDGER_RELEVANT":
        item["state"] = "OPERATION_STATE_CANCELED"

    raw_account = request["raw_account_id"]
    key = bytes.fromhex(request["identity_key_hex"])
    key_id = request["identity_key_id"]
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    manager = cl7.RuntimeCashAuthorityManager(store)
    current = store.bootstrap(transition_at=T0)
    current = _commit_test_transition(
        manager,
        current,
        at=T1,
        kind="PREPARE_CUTOVER",
        state=cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
        cutover_generation=1,
        account_scope_sha256=cl7.derive_account_scope(
            raw_account,
            identity_key=key,
            identity_key_id=key_id,
        ),
        identity_key_id=key_id,
    )
    ledger = persistence.CashLedgerStore.create(
        tmp_path / "cash_ledger_v3_10.sqlite3",
        (cl4.CL4_OPENING_CODEC, cl3.TBANK_OPERATION_CODEC),
    )
    try:
        initial = ledger.snapshot()
        _commit_test_transition(
            manager,
            current,
            at=T2,
            kind="PREPARATION_EVIDENCE_BOUND",
            state=cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
            ledger_revision=initial.ledger_revision,
            ledger_head_sha256=initial.ledger_head_sha256,
            opening_cutoff=T0,
            opening_record_sha256="7" * 64,
            operations_complete_through=request["from_inclusive"],
        )
        clock_values = iter((0, 0, 0, 0, 0, 0, 0, 0))
        updated, batch = manager.synchronize_operations(
            ledger_store=ledger,
            raw_account_id=raw_account,
            identity_key=key,
            identity_key_id=key_id,
            sync_to_exclusive=request["to_exclusive"],
            transport=lambda _payload, _timeout: copy.deepcopy(response),
            monotonic_ns=lambda: next(clock_values, 0),
            wait_ns=lambda _delay: None,
            absolute_deadline_ns=1_000_000,
            retry_policy=cl3.RetryPolicy(1, 100_000, ()),
            transition_at=T3,
        )
        assert batch.decisions[0].kind.value == decision_kind
        assert updated.operations_complete_through == request["to_exclusive"]
        export = json.loads(ledger.export_bytes().decode("ascii"))
        assert export["observations"][0]["current_status"] == expected_status
        if decision_kind == "TRANSACTION_PROPOSED":
            assert len(export["transactions"]) == 1
        else:
            assert not export["transactions"]
    finally:
        ledger.close()


@pytest.mark.parametrize("withdraw_currency", ["RUB", "rub"])
def test_full_prepare_confirm_activate_arm_uses_fresh_cl2_to_cl6_evidence(
    tmp_path: Path,
    withdraw_currency: str,
) -> None:
    now = "2027-01-01T00:00:00.000000000Z"
    now_iso = "2027-01-01T00:00:00+00:00"
    store = cl7.RuntimeCashAuthorityStore(tmp_path)
    store.bootstrap(transition_at=now)
    manager = cl7.RuntimeCashAuthorityManager(store)
    ledger = persistence.CashLedgerStore.create(
        tmp_path / "cash_ledger_v3_10.sqlite3",
        (cl4.CL4_OPENING_CODEC, cl3.TBANK_OPERATION_CODEC),
    )
    portfolio = PortfolioRepository(tmp_path / "portfolio_state.json")
    portfolio.save(
        PortfolioState(
            version=2,
            account=AccountState(
                account_id=RAW_ACCOUNT,
                total_value=None,
                securities_value=None,
                expected_yield=None,
                cash_balances=(),
            ),
            snapshot_at=now_iso,
            generated_at=now_iso,
            freshness=SnapshotFreshness.FRESH,
            source="TBANK",
            positions=(),
            warnings=(),
            state_status="READY",
            blocking=False,
            revision=9,
            portfolio_source="CANONICAL",
            migration=PortfolioMigrationMetadata.completed(),
        )
    )
    profiles = RiskProfileStore(tmp_path / "risk_profiles.json")
    risk_state = RiskStateStore(tmp_path / "risk_state.json")
    profiles.save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope=RAW_ACCOUNT,
        source="CL7_SYNTHETIC_TEST",
    )
    risk_state.save_account(RAW_ACCOUNT, RiskState())
    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=RAW_ACCOUNT,
    )

    calls = {
        "operations": 0,
        "portfolio": 0,
        "withdraw_limits": 0,
        "order_mutations": 0,
    }

    class Provider:
        @staticmethod
        def get_portfolio(_account: str) -> dict[str, object]:
            calls["portfolio"] += 1
            return {
                "totalAmountCurrencies": {
                    "currency": "RUB",
                    "nano": 0,
                    "units": "150",
                }
            }

        @staticmethod
        def get_withdraw_limits(_account: str):
            calls["withdraw_limits"] += 1
            return _withdraw_limits_transport_observation(
                {
                    "blocked": [],
                    "blockedGuarantee": [],
                    "money": [
                        {
                            "currency": withdraw_currency,
                            "nano": 0,
                            "units": "150",
                        }
                    ],
                },
            )

        @staticmethod
        def get_operations_by_cursor_once(_payload, _timeout):
            calls["operations"] += 1
            return {"hasNext": False, "items": [], "nextCursor": ""}

        @staticmethod
        def post_order_once(*_args: object, **_kwargs: object) -> object:
            calls["order_mutations"] += 1
            raise AssertionError("order mutation is outside the synthetic lifecycle")

    inputs = {
        "ledger_store": ledger,
        "portfolio_repository": portfolio,
        "risk_profile_store": profiles,
        "risk_state_store": risk_state,
        "central_manager": central,
        "provider": Provider(),
        "raw_account_id": RAW_ACCOUNT,
        "identity_key": KEY,
        "identity_key_id": KEY_ID,
        "clock": lambda: now,
        "monotonic_ns": lambda: 0,
        "wait_ns": lambda _delay: None,
    }
    try:
        prepared, preview = manager.prepare_runtime(
            confirmation=manager.PREPARE_PHRASE,
            **inputs,
        )
        assert prepared.state is cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED
        assert prepared.opening_record_sha256 is not None
        assert preview.context.status.value == "READY_FOR_LOCKED_REVALIDATION"
        confirmed = manager.confirm_runtime(
            confirmation=manager.CONFIRM_PHRASE,
            runtime_dir=tmp_path,
            **inputs,
        )
        assert confirmed.state is cl7.RuntimeCashAuthorityState.CUTOVER_CONFIRMED
        activated = manager.activate_runtime(
            confirmation=manager.ACTIVATE_PHRASE,
            runtime_dir=tmp_path,
            **inputs,
        )
        assert activated.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED
        assert activated.activation_context_sha256 is not None
        armed = manager.arm(
            raw_account_id=RAW_ACCOUNT,
            identity_key=KEY,
            identity_key_id=KEY_ID,
            confirmation=manager.ARM_PHRASE,
            transition_at=now,
        )
        assert armed.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_ARMED
        disarmed = manager.disarm(transition_at=now)
        ledger_before = ledger.export_bytes()
        rolled_back = manager.rollback_runtime(
            confirmation=manager.ROLLBACK_PHRASE,
            runtime_dir=tmp_path,
            **inputs,
        )
        assert disarmed.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED
        assert rolled_back.state is cl7.RuntimeCashAuthorityState.LEGACY_ACTIVE
        assert rolled_back.ever_exact_activated is True
        assert ledger.export_bytes() == ledger_before
        assert calls == {
            "operations": 4,
            "portfolio": 5,
            "withdraw_limits": 4,
            "order_mutations": 0,
        }
    finally:
        ledger.close()


def test_initial_rebuild_timestamps_non_atomic_provider_reads_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = object.__new__(cl7.RuntimeCashAuthorityManager)
    current = SimpleNamespace(operations_complete_through=T0)
    batch = SimpleNamespace()
    captured: dict[str, object] = {}
    events: list[str] = []
    clock_values = iter(
        (
            T1,
            T1,
            T2,
            "2026-09-11T10:00:13.000000000Z",
            "2026-09-11T10:00:13.000000000Z",
        )
    )

    def synchronize(record: object, **_kwargs: object) -> tuple[object, object]:
        return record, batch

    def rebuild(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(manager, "synchronize_operations_locked", synchronize)
    monkeypatch.setattr(manager, "rebuild_context_with_locks", rebuild)

    class Provider:
        @staticmethod
        def get_operations_by_cursor_once(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("synthetic sync must be intercepted")

        @staticmethod
        def get_portfolio(_account: str) -> dict[str, object]:
            events.append("portfolio")
            return {"synthetic": "portfolio"}

        @staticmethod
        def get_withdraw_limits(_account: str) -> object:
            events.append("withdraw_limits")
            return SimpleNamespace()

    manager._sync_and_rebuild_locked(
        current,
        ledger_store=SimpleNamespace(),
        portfolio_repository=SimpleNamespace(),
        risk_profile_store=SimpleNamespace(),
        risk_state_store=SimpleNamespace(),
        central_manager=SimpleNamespace(),
        provider=Provider(),
        raw_account_id=RAW_ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
        clock=lambda: next(clock_values),
        monotonic_ns=lambda: 0,
        wait_ns=lambda _delay: None,
        commit_sync=False,
        require_ready=True,
    )

    assert events == ["portfolio", "withdraw_limits"]
    assert captured["broker_cash_as_of"] == T2
    assert captured["broker_withdraw_limits_as_of"] == (
        "2026-09-11T10:00:13.000000000Z"
    )
    assert captured["evaluated_at"] == "2026-09-11T10:00:13.000000000Z"


class _ExactRiskGate:
    account_id = RAW_ACCOUNT
    mode = "SANDBOX_EXECUTION"

    def __init__(self) -> None:
        self.state_store = SimpleNamespace(
            load_account=lambda _account: SimpleNamespace()
        )

    def _load_policy(self):
        return SimpleNamespace(), False

    def dispatch_authorization_guard(self, **_kwargs):
        class _Guard:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        return _Guard()


class _ExactTransport:
    def __init__(
        self,
        authority: cl7.RuntimeCashAuthorityManager,
        *,
        outcome: str,
    ) -> None:
        self.authority = authority
        self.outcome = outcome
        self.post_calls = 0
        self.state_at_post: cl7.RuntimeCashAuthorityState | None = None

    @staticmethod
    def get_trading_status(_instrument_id: str) -> dict[str, object]:
        return {
            "tradingStatus": "SECURITY_TRADING_STATUS_NORMAL_TRADING",
            "apiTradeAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "bestpriceOrderAvailableFlag": True,
        }

    @staticmethod
    def get_operations_by_cursor_once(*_args, **_kwargs):
        raise AssertionError("sync is replaced by the synthetic CL7 oracle")

    @staticmethod
    def get_portfolio(_account_id: str) -> dict[str, object]:
        return {"synthetic": "portfolio"}

    @staticmethod
    def get_withdraw_limits(_account_id: str):
        return _withdraw_limits_transport_observation(
            {"money": [], "blocked": [], "blockedGuarantee": []},
        )

    def post_order_once(self, *_args, order_id: str, **_kwargs):
        self.post_calls += 1
        self.state_at_post = self.authority.store._load_unlocked(
            allow_missing_legacy=False
        ).state
        if self.outcome == "rejected":
            raise TBankAPIError(
                "redacted",
                status_code=400,
                transient=False,
                service="SandboxService",
                method="PostSandboxOrder",
                direct_response=True,
                redirect_followed=False,
            )
        if self.outcome == "timeout":
            raise TBankAPIError(
                "redacted",
                status_code=None,
                transient=True,
                service="SandboxService",
                method="PostSandboxOrder",
                direct_response=False,
            )
        return {
            "orderId": "broker-order-1",
            "orderRequestId": (
                "wrong-intent" if self.outcome == "mismatch" else order_id
            ),
            "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
            "lotsExecuted": "0",
        }


@pytest.mark.parametrize(
    "outcome,result_status,central_status,authority_state",
    [
        (
            "accepted",
            "SUBMITTED",
            "SUBMITTED",
            cl7.RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
        ),
        (
            "rejected",
            "SUBMISSION_REJECTED",
            "FAILED",
            cl7.RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        ),
        (
            "timeout",
            "SUBMISSION_UNCERTAIN",
            "UNCERTAIN",
            cl7.RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
        ),
        (
            "mismatch",
            "SUBMISSION_UNCERTAIN",
            "UNCERTAIN",
            cl7.RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
        ),
    ],
)
def test_exact_dispatch_marker_precedes_single_post_and_classifies_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    result_status: str,
    central_status: str,
    authority_state: cl7.RuntimeCashAuthorityState,
) -> None:
    authority_manager, _authority = _chain(tmp_path)
    repository, central, intent = _central_runtime(tmp_path)
    transport = _ExactTransport(authority_manager, outcome=outcome)
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir()
    sqlite3.connect(ledger_root / "store.sqlite3").close()

    def no_op_sync(self, current, **_kwargs):
        return current, SimpleNamespace()

    monkeypatch.setattr(
        cl7.RuntimeCashAuthorityManager,
        "synchronize_operations_locked",
        no_op_sync,
    )
    monkeypatch.setattr(
        cl7.RuntimeCashAuthorityManager,
        "build_runtime_context",
        lambda *_args, **_kwargs: SimpleNamespace(context=SimpleNamespace()),
    )

    def proof_builder(current, state, queued):
        return _proof(
            raw_intent_id=queued.intent_id,
            authority_record_revision=current.record_revision,
            authority_record_sha256=current.sha256,
            central_order_revision=state.revision,
            central_reservation_projection_hash=central_reservation_projection_hash(
                state
            ),
        )

    adapter = SandboxExecutionAdapter(
        transport,
        central,
        SandboxExecutionPolicy(account_id=RAW_ACCOUNT),
        risk_runtime=_ExactRiskGate(),
        cash_authority_manager=authority_manager,
        cl7_identity_key=KEY,
        cl7_identity_key_id=KEY_ID,
        cl7_ledger_store=SimpleNamespace(root=ledger_root),
        cl7_proof_builder=proof_builder,
        cl7_clock=lambda: T6,
        cl7_monotonic_ns=lambda: 1,
        cl7_wait_ns=lambda _duration: None,
    )
    result = adapter.dispatch_next(
        repository,
        expected_intent_id=intent.intent_id,
    )
    assert result.status == result_status
    assert transport.post_calls == 1
    assert (
        transport.state_at_post
        is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    )
    assert authority_manager.status().state is authority_state
    persisted = central.state().intents[0]
    assert persisted.status == central_status
    assert persisted.cl7_locked_dispatch_proof_sha256 is not None
    if outcome == "rejected":
        assert persisted.outcome == "SUBMISSION_REJECTED"
    else:
        assert authority_manager.status().post_attempt_count == 1


def test_final_dispatch_timestamps_non_atomic_provider_reads_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_manager, _authority = _chain(tmp_path)
    repository, central, intent = _central_runtime(tmp_path)
    now = {"value": T1}

    class TimedTransport(_ExactTransport):
        def get_portfolio(self, _account_id: str) -> dict[str, object]:
            now["value"] = T2
            return {"synthetic": "portfolio"}

        def get_withdraw_limits(self, _account_id: str) -> object:
            now["value"] = "2026-09-11T10:00:13.000000000Z"
            return _withdraw_limits_transport_observation(
                {"money": [], "blocked": [], "blockedGuarantee": []},
            )

    transport = TimedTransport(authority_manager, outcome="accepted")
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir()
    sqlite3.connect(ledger_root / "store.sqlite3").close()
    captured: dict[str, object] = {}

    def no_op_sync(self: object, current: object, **_kwargs: object):
        return current, SimpleNamespace()

    def capture_context(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(context=SimpleNamespace())

    monkeypatch.setattr(
        cl7.RuntimeCashAuthorityManager,
        "synchronize_operations_locked",
        no_op_sync,
    )
    monkeypatch.setattr(
        cl7.RuntimeCashAuthorityManager,
        "build_runtime_context",
        capture_context,
    )

    def proof_builder(current: object, state: object, queued: object):
        return _proof(
            raw_intent_id=queued.intent_id,
            authority_record_revision=current.record_revision,
            authority_record_sha256=current.sha256,
            central_order_revision=state.revision,
            central_reservation_projection_hash=central_reservation_projection_hash(
                state
            ),
        )

    adapter = SandboxExecutionAdapter(
        transport,
        central,
        SandboxExecutionPolicy(account_id=RAW_ACCOUNT),
        risk_runtime=_ExactRiskGate(),
        cash_authority_manager=authority_manager,
        cl7_identity_key=KEY,
        cl7_identity_key_id=KEY_ID,
        cl7_ledger_store=SimpleNamespace(root=ledger_root),
        cl7_proof_builder=proof_builder,
        cl7_clock=lambda: now["value"],
        cl7_monotonic_ns=lambda: 1,
        cl7_wait_ns=lambda _duration: None,
    )
    result = adapter.dispatch_next(
        repository,
        expected_intent_id=intent.intent_id,
    )

    assert result.status == "SUBMITTED"
    assert transport.post_calls == 1
    assert captured["broker_cash_as_of"] == T2
    assert captured["broker_withdraw_limits_as_of"] == (
        "2026-09-11T10:00:13.000000000Z"
    )
    assert captured["evaluated_at"] == "2026-09-11T10:00:13.000000000Z"


def test_process_boundary_recovery_requires_exact_central_resolution(
    tmp_path: Path,
) -> None:
    authority_manager, authority = _chain(tmp_path)
    repository, central, intent = _central_runtime(tmp_path)
    proof = _central_proof(authority, central, intent)
    with authority_manager.store.locked():
        current = authority_manager.store._load_unlocked(allow_missing_legacy=False)
        with (
            repository.locked_snapshot(expected_account_id=RAW_ACCOUNT) as portfolio,
            central.locked_dispatch_lease(
                repository,
                expected_intent_id=intent.intent_id,
                locked_portfolio_state=portfolio,
                validator=lambda _state, _intent: proof,
            ) as lease,
        ):
            pending = authority_manager._record_dispatch_attempt_locked(
                current,
                proof,
                transition_at=T65,
            )
            lease.mark_submission_rejected(reason="synthetic explicit rejection")
    closed, disposition = authority_manager.recover_runtime(
        central_manager=central,
        raw_account_id=RAW_ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
        transition_at="2026-09-11T10:00:07.000000000Z",
    )
    assert pending.post_attempt_count == closed.post_attempt_count == 1
    assert disposition == "RECOVERY_CLOSED_DISARMED"
    assert closed.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert closed.pending_dispatch_proof_sha256 is None


def test_operator_surface_has_no_caller_asserted_evidence_or_quiescence() -> None:
    source = (CURRENT / "tools" / "v3_10_runtime_cash_cutover.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "--ledger-revision",
        "--ledger-head-sha256",
        "--opening-record-sha256",
        "--activation-context-sha256",
        "--proof-sha256",
        "--resolution",
        "--quiescent",
    ):
        assert forbidden not in source
    assert '"dispatch"' in source
    assert "post_order_once" not in source


def test_operator_recovery_validates_before_lookup_under_authority_lock() -> None:
    source = (CURRENT / "tools" / "v3_10_runtime_cash_cutover.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    recovery_lock = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "locked"
            for item in node.items
            for call in ast.walk(item.context_expr)
        )
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_recover_runtime_locked"
            for call in ast.walk(node)
        )
    )
    calls = {
        call.func.attr: call.lineno
        for call in ast.walk(recovery_lock)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr
        in {
            "_recovery_intent_locked",
            "_inspect_order",
            "mark_submitted",
            "mark_reconciled",
            "_recover_runtime_locked",
        }
    }
    assert calls["_recovery_intent_locked"] < calls["_inspect_order"]
    assert {
        "mark_submitted",
        "mark_reconciled",
        "_recover_runtime_locked",
    } <= calls.keys()
    assert all(
        recovery_lock.lineno <= line <= recovery_lock.end_lineno
        for line in calls.values()
    )


def test_production_order_post_owner_converges_to_adapter() -> None:
    sources = {
        path.name: ast.parse(path.read_text(encoding="utf-8"))
        for path in (CURRENT / "trading_robot").glob("*.py")
    }
    direct = []
    for name, tree in sources.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"post_order", "post_order_once"}
            ):
                direct.append((name, node.func.attr))
    assert ("sandbox_execution_adapter.py", "post_order_once") in direct
    # Legacy owners remain physically present for compatibility but both are
    # guarded by RuntimeCashAuthority before intent persistence and POST.
    assert "legacy_execution_guard" in (CURRENT / "trading_robot" / "bot.py").read_text(
        encoding="utf-8"
    )
    assert "legacy_execution_guard" in (
        CURRENT / "trading_robot" / "diagnostics.py"
    ).read_text(encoding="utf-8")
    assert "legacy_execution_guard" in (
        CURRENT / "trading_robot" / "sandbox_execution_adapter.py"
    ).read_text(encoding="utf-8")


def test_every_legacy_post_is_lexically_inside_the_authority_guard() -> None:
    for name in ("bot.py", "diagnostics.py"):
        tree = ast.parse((CURRENT / "trading_robot" / name).read_text(encoding="utf-8"))
        guarded_ranges = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            if any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "legacy_execution_guard"
                for item in node.items
                for call in ast.walk(item.context_expr)
            ):
                guarded_ranges.append((node.lineno, node.end_lineno))
        post_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "post_order"
        ]
        assert post_lines
        assert all(
            any(start <= line <= end for start, end in guarded_ranges)
            for line in post_lines
        )


def test_legacy_recovery_has_no_automatic_post_resubmit() -> None:
    tree = ast.parse(
        (CURRENT / "trading_robot" / "diagnostics.py").read_text(encoding="utf-8")
    )
    recovery = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "recover_pending"
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"post_order", "post_order_once"}
        for node in ast.walk(recovery)
    )


def test_exact_implementation_allowlist() -> None:
    base_object = subprocess.run(
        ["git", "cat-file", "-e", f"{ACCEPTED_CONTRACT_HEAD}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if base_object.returncode != 0:
        _assert_shallow_pull_request_custody()
        return
    changed = set(
        subprocess.run(
            ["git", "diff", "--name-only", ACCEPTED_CONTRACT_HEAD],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )
    changed.update(
        subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )
    assert changed == IMPLEMENTATION_PATHS
    assert CONTRACT.relative_to(ROOT).as_posix() not in changed


def test_fixture_is_ascii_and_self_consistent(vectors: dict[str, object]) -> None:
    raw = FIXTURE.read_bytes()
    assert raw.decode("ascii")
    assert (
        hashlib.sha256(
            vectors["legacy_record"]["canonical_json_ascii"].encode("ascii")
        ).hexdigest()
        == vectors["legacy_record"]["sha256"]
    )


def test_no_authenticated_provider_access_in_suite() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    assert "TBANK" + "_SANDBOX_TOKEN" not in source
    assert "sandbox-invest-public-api" + ".tbank.ru" not in source
    assert "dummy-token" in source
