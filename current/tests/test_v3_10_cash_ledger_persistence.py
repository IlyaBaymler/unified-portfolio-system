from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from trading_robot import cash_ledger_persistence as persistence
from trading_robot.cash_ledger_domain import (
    LedgerCorrectionBundle,
    LedgerTransaction,
)
from trading_robot.cash_ledger_persistence import (
    CASH_LEDGER_STORE_SCHEMA_VERSION,
    CL2_CROSS_LANGUAGE_FIXTURE_VERSION,
    GENESIS_HEAD_SHA256,
    SCHEMA_FINGERPRINT_SHA256,
    SQLITE_APPLICATION_ID,
    CashLedgerStore,
    CodecDescriptor,
    InboxObservation,
    InboxStatusEvent,
    InjectedFault,
    LedgerHead,
    PersistenceDisposition,
    PersistenceError,
    PersistenceReason,
    canonical_json_bytes,
    normalize_codec_registry,
    parse_canonical_json,
    restore_backup,
    sha256_hex,
    verify_backup,
)

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
MODULE_PATH = CURRENT / "trading_robot" / "cash_ledger_persistence.py"
FIXTURE_PATH = CURRENT / "tests" / "fixtures" / "v3_10_cash_ledger_persistence_vectors.json"
ACCEPTED_CONTRACT_HEAD = "f3b0ab7db1889695eb1b56264133ac895b7ed52d"
CL1_PREDECESSOR = "095a24a0d8ad2487f09ec0c5f3473a6c65a84710"
CL2_ACCEPTED_IMPLEMENTATION_HEAD = "d684186c0628d27ed452ce4f11311155fdf7a44e"


@pytest.fixture(scope="module")
def fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="ascii"))


@pytest.fixture(scope="module")
def vectors(fixture: dict[str, object]) -> dict[str, dict[str, object]]:
    return {item["id"]: item for item in fixture["vectors"]}


@pytest.fixture(scope="module")
def descriptor(vectors: dict[str, dict[str, object]]) -> CodecDescriptor:
    return CodecDescriptor.from_canonical_bytes(
        vectors["codec-descriptor-synthetic"]["canonical_json_ascii"]
    )


def _observation(
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
    identifier: str,
) -> InboxObservation:
    return InboxObservation.from_canonical_bytes(
        vectors[identifier]["canonical_json_ascii"], [descriptor]
    )


def _transaction(
    vectors: dict[str, dict[str, object]], identifier: str
) -> LedgerTransaction:
    return LedgerTransaction.from_canonical_dict(
        parse_canonical_json(vectors[identifier]["canonical_json_ascii"])
    )


def _bundle(
    vectors: dict[str, dict[str, object]], identifier: str
) -> LedgerCorrectionBundle:
    vector = vectors[identifier]
    transaction_by_sha = {
        item["sha256"]: item_id
        for item_id, item in vectors.items()
        if item["kind"] == "cl1_transaction"
    }
    return LedgerCorrectionBundle.from_canonical_dict(
        parse_canonical_json(vector["canonical_json_ascii"]),
        original=_transaction(vectors, transaction_by_sha[vector["original_sha256"]]),
        reversal=_transaction(vectors, transaction_by_sha[vector["reversal_sha256"]]),
        correction=_transaction(
            vectors, transaction_by_sha[vector["correction_sha256"]]
        ),
    )


def _reason(reason: PersistenceReason) -> pytest.ExceptionInfo[PersistenceError]:
    return pytest.raises(PersistenceError, match=f"^{reason.value}$")


def _scenario_steps(
    tmp_path: Path,
    scenario: dict[str, object],
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> Iterator[tuple[dict[str, object], PersistenceDisposition | None, str | None]]:
    store = CashLedgerStore.create(tmp_path / scenario["id"], [descriptor])
    try:
        for step in scenario["steps"]:
            ids = step["input_ids"]
            kwargs = {"expected_store_revision": int(step["supplied_store_revision"])}
            if step["supplied_ledger_revision"] is not None:
                kwargs["expected_ledger_revision"] = int(
                    step["supplied_ledger_revision"]
                )
            disposition: PersistenceDisposition | None = None
            reason: str | None = None
            try:
                if step["operation"] == "APPEND_OBSERVATION":
                    disposition = store.append_observation(
                        _observation(vectors, descriptor, ids[0]), **kwargs
                    )
                elif step["operation"] == "APPEND_STATUS_EVENT":
                    disposition = store.append_status_event(
                        InboxStatusEvent.from_canonical_bytes(
                            vectors[ids[0]]["canonical_json_ascii"]
                        ),
                        **kwargs,
                    )
                elif step["operation"] == "APPEND_TRANSACTION":
                    disposition = store.append_transaction(
                        _transaction(vectors, ids[0]),
                        _observation(vectors, descriptor, ids[1]).sha256,
                        **kwargs,
                    )
                else:
                    assert step["operation"] == "APPEND_CORRECTION_BUNDLE"
                    disposition = store.append_correction_bundle(
                        _bundle(vectors, ids[0]),
                        _observation(vectors, descriptor, ids[1]).sha256,
                        _observation(vectors, descriptor, ids[2]).sha256,
                        **kwargs,
                    )
            except PersistenceError as exc:
                reason = exc.reason.value
            snapshot = store.snapshot()
            assert disposition is None or disposition.value == step["expected_disposition"]
            assert reason == step["expected_reason"]
            assert str(snapshot.store_revision) == step["expected_store_revision"]
            assert str(snapshot.ledger_revision) == step["expected_ledger_revision"]
            assert snapshot.ledger_head_sha256 == step["expected_head_sha256"]
            yield step, disposition, reason
        store.validate()
    finally:
        store.close()


def _populate_original(
    store: CashLedgerStore,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> tuple[InboxObservation, LedgerTransaction]:
    observation = _observation(vectors, descriptor, "observation-original")
    transaction = _transaction(vectors, "transaction-original")
    assert (
        store.append_observation(observation, expected_store_revision=0)
        is PersistenceDisposition.OBSERVATION_STORED
    )
    assert (
        store.append_transaction(
            transaction,
            observation.sha256,
            expected_store_revision=1,
            expected_ledger_revision=0,
        )
        is PersistenceDisposition.TRANSACTION_APPENDED
    )
    return observation, transaction


def test_v310_cl2_01_import_and_static_dependency_boundary() -> None:
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
    assert imported <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        "shutil",
        "sqlite3",
        "struct",
        "trading_robot",
        "typing",
    }
    code = (
        "import sqlite3\n"
        "sqlite3.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('io'))\n"
        "import trading_robot.cash_ledger_persistence\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(CURRENT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_v310_cl2_02_clean_create_schema_allowlist_and_fingerprint(
    tmp_path: Path, descriptor: CodecDescriptor
) -> None:
    root = tmp_path / "store"
    store = CashLedgerStore.create(root, [descriptor])
    assert store.snapshot().ledger_head_sha256 == GENESIS_HEAD_SHA256
    store.close()
    with sqlite3.connect(root / "store.sqlite3") as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == SQLITE_APPLICATION_ID
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == CASH_LEDGER_STORE_SCHEMA_VERSION
        )
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "ORDER BY type,name,tbl_name"
        ).fetchall()
        assert {row[0] for row in rows} == {"index", "table"}
        application = [
            {"name": row[1], "sql": row[3], "tbl_name": row[2], "type": row[0]}
            for row in rows
            if not row[1].startswith("sqlite_")
        ]
        assert sha256_hex(canonical_json_bytes(application)) == SCHEMA_FINGERPRINT_SHA256
        assert {row[1] for row in rows if row[0] == "table"} == {
            "cl2_meta",
            "cl2_codec",
            "cl2_observation",
            "cl2_inbox_status_event",
            "cl2_transaction",
            "cl2_posting",
            "cl2_transaction_observation",
            "cl2_correction_bundle",
            "cl2_ledger_transition",
        }


def test_v310_cl2_03_open_never_creates_and_schema_versions_fail_closed(
    tmp_path: Path, descriptor: CodecDescriptor
) -> None:
    missing = tmp_path / "missing"
    with _reason(PersistenceReason.STORE_MISSING):
        CashLedgerStore.open(missing, [descriptor])
    assert not missing.exists()
    with _reason(PersistenceReason.PATH_INVALID):
        CashLedgerStore.open(Path("relative-store"), [descriptor])
    collision = tmp_path / "collision"
    collision.mkdir()
    with _reason(PersistenceReason.PATH_COLLISION):
        CashLedgerStore.create(collision, [descriptor])
    wrong_version = tmp_path / "wrong-version"
    CashLedgerStore.create(wrong_version, [descriptor]).close()
    with sqlite3.connect(wrong_version / "store.sqlite3") as connection:
        connection.execute("PRAGMA user_version=2")
    with _reason(PersistenceReason.VERSION_UNSUPPORTED):
        CashLedgerStore.open(wrong_version, [descriptor])
    extra_schema = tmp_path / "extra-schema"
    CashLedgerStore.create(extra_schema, [descriptor]).close()
    with sqlite3.connect(extra_schema / "store.sqlite3") as connection:
        connection.execute("CREATE TABLE unexpected(value TEXT)")
    with _reason(PersistenceReason.SCHEMA_INVALID):
        CashLedgerStore.open(extra_schema, [descriptor])
    for name, statement in (
        ("extra-view", "CREATE VIEW unexpected_view AS SELECT * FROM cl2_meta"),
        (
            "extra-trigger",
            (
                "CREATE TRIGGER unexpected_trigger AFTER UPDATE OF store_revision "
                "ON cl2_meta BEGIN DELETE FROM cl2_observation; END"
            ),
        ),
    ):
        unexpected_object = tmp_path / name
        CashLedgerStore.create(unexpected_object, [descriptor]).close()
        with sqlite3.connect(unexpected_object / "store.sqlite3") as connection:
            connection.execute(statement)
        with _reason(PersistenceReason.SCHEMA_INVALID):
            CashLedgerStore.open(unexpected_object, [descriptor])
    hard_linked = tmp_path / "hard-linked"
    CashLedgerStore.create(hard_linked, [descriptor]).close()
    hard_link = tmp_path / "second-link.sqlite3"
    os.link(hard_linked / "store.sqlite3", hard_link)
    try:
        with _reason(PersistenceReason.PATH_INVALID):
            CashLedgerStore.open(hard_linked, [descriptor])
    finally:
        hard_link.unlink()


def test_v310_cl2_04_codec_schema_descriptor_and_registry_identity(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    assert descriptor.schema_sha256 == "d21206b6c54576f60fa9923817bc4b59f8b8ed04942be4a53ca5c3eb304e858b"
    assert descriptor.sha256 == "43d6d99e633e8806f7efc11f70816fd8236df824d775b5cdf18626c93abfa1b4"
    changed_schema = descriptor.schema_json_ascii.replace('"64"', '"63"')
    changed = CodecDescriptor(
        descriptor.codec_id,
        changed_schema,
        sha256_hex(changed_schema.encode("ascii")),
    )
    with _reason(PersistenceReason.CODEC_UNSUPPORTED):
        normalize_codec_registry([descriptor, changed])
    root = tmp_path / "store"
    store = CashLedgerStore.create(root, [descriptor])
    store.append_observation(
        _observation(vectors, descriptor, "observation-original"),
        expected_store_revision=0,
    )
    store.close()
    with _reason(PersistenceReason.CODEC_UNSUPPORTED):
        CashLedgerStore.open(root, [])


def test_v310_cl2_05_generic_flat_sanitized_content_validation() -> None:
    fields = [
        {
            "allowed_values": None,
            "key": "enabled",
            "kind": "BOOLEAN",
            "maximum": None,
            "max_scalars": None,
            "minimum": None,
            "required": True,
        },
        {
            "allowed_values": None,
            "key": "quantity",
            "kind": "INTEGER",
            "maximum": "10",
            "max_scalars": None,
            "minimum": "-10",
            "required": True,
        },
        {
            "allowed_values": ["A", "B"],
            "key": "side",
            "kind": "STRING",
            "maximum": None,
            "max_scalars": "1",
            "minimum": None,
            "required": True,
        },
    ]
    schema = canonical_json_bytes(
        {"domain": "v3.10-operation-inbox-codec-schema", "fields": fields, "version": 1}
    ).decode("ascii")
    codec = CodecDescriptor("GENERIC", schema, sha256_hex(schema.encode("ascii")))
    assert codec.validate_content('{"enabled":true,"quantity":10,"side":"A"}')
    for invalid in (
        '{"enabled":1,"quantity":10,"side":"A"}',
        '{"enabled":true,"quantity":11,"side":"A"}',
        '{"enabled":true,"quantity":10,"side":"C"}',
        '{"enabled":true,"quantity":10,"side":"A","unknown":null}',
        '{"enabled":true,"quantity":1.0,"side":"A"}',
    ):
        with _reason(PersistenceReason.SANITIZED_CONTENT_INVALID if "1.0" not in invalid else PersistenceReason.CANONICAL_FORMAT_INVALID):
            codec.validate_content(invalid)
    forbidden_field = fields.copy()
    forbidden_field[0] = {**fields[0], "key": "api_key"}
    forbidden_schema = canonical_json_bytes(
        {"domain": "v3.10-operation-inbox-codec-schema", "fields": forbidden_field, "version": 1}
    ).decode("ascii")
    with _reason(PersistenceReason.SENSITIVE_CONTENT_FORBIDDEN):
        CodecDescriptor(
            "PRIVATE",
            forbidden_schema,
            sha256_hex(forbidden_schema.encode("ascii")),
        )
    malformed_forbidden_fields = [dict(field) for field in forbidden_field]
    malformed_forbidden_fields[0]["required"] = 1
    malformed_forbidden_schema = canonical_json_bytes(
        {
            "domain": "v3.10-operation-inbox-codec-schema",
            "fields": malformed_forbidden_fields,
            "version": 1,
        }
    ).decode("ascii")
    with _reason(PersistenceReason.CANONICAL_FORMAT_INVALID):
        CodecDescriptor(
            "PRIVATE",
            malformed_forbidden_schema,
            sha256_hex(malformed_forbidden_schema.encode("ascii")),
        )
    with _reason(PersistenceReason.HASH_INVALID):
        CodecDescriptor("PRIVATE", forbidden_schema, "0" * 64)


def test_v310_cl2_06_logical_source_and_observation_known_answers(
    vectors: dict[str, dict[str, object]], descriptor: CodecDescriptor
) -> None:
    observation = _observation(
        vectors, descriptor, "observation-section25-sample"
    )
    assert observation.logical_source_sha256 == "1a8ab1a83f42334472fa30ffa57b921ae6ff9a01702e297acf81278bbd1fba1e"
    assert observation.sha256 == "91570ebf94038110b8c3059172c448a105359296118555d7faae3511f3f1e896"
    assert observation.canonical_bytes.decode("ascii") == vectors[
        "observation-section25-sample"
    ]["canonical_json_ascii"]


def test_v310_cl2_07_observation_duplicate_conflict_and_distinct(
    tmp_path: Path,
    fixture: dict[str, object],
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    scenario = next(
        item for item in fixture["scenarios"] if item["id"] == "observation-conflict-and-stale"
    )
    results = list(_scenario_steps(tmp_path, scenario, vectors, descriptor))
    assert [item[1].value if item[1] else item[2] for item in results] == [
        "REVISION_MISMATCH",
        "OBSERVATION_STORED",
        "OBSERVATION_ALREADY_PRESENT",
        "SOURCE_CONTENT_CONFLICT",
    ]


def test_v310_cl2_08_status_chain_contiguous_terminal_and_duplicate(
    tmp_path: Path,
    fixture: dict[str, object],
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    scenario = next(
        item for item in fixture["scenarios"] if item["id"] == "status-review-duplicate"
    )
    results = list(_scenario_steps(tmp_path, scenario, vectors, descriptor))
    assert results[-1][2] == PersistenceReason.STATUS_TRANSITION_INVALID.value


def test_v310_cl2_09_separate_store_and_ledger_revisions(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    store = CashLedgerStore.create(tmp_path / "store", [descriptor])
    try:
        assert store.snapshot().store_revision == store.snapshot().ledger_revision == 0
        observation = _observation(vectors, descriptor, "observation-original")
        store.append_observation(observation, expected_store_revision=0)
        assert (store.snapshot().store_revision, store.snapshot().ledger_revision) == (1, 0)
        store.append_status_event(
            InboxStatusEvent.from_canonical_bytes(vectors["status-review"]["canonical_json_ascii"]),
            expected_store_revision=1,
        )
        assert (store.snapshot().store_revision, store.snapshot().ledger_revision) == (2, 0)
    finally:
        store.close()


def test_v310_cl2_10_ordinary_transaction_postings_and_provenance_are_exact(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "store"
    store = CashLedgerStore.create(root, [descriptor])
    _, transaction = _populate_original(store, vectors, descriptor)
    store.close()
    with sqlite3.connect(root / "store.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM cl2_transaction").fetchone()
        assert bytes(row["canonical"]) == transaction.canonical_bytes
        postings = connection.execute(
            "SELECT line_no,sha256,canonical FROM cl2_posting ORDER BY line_no"
        ).fetchall()
        assert [bytes(item["canonical"]) for item in postings] == [
            item.canonical_bytes for item in transaction.postings
        ]
        assert connection.execute(
            "SELECT count(*) FROM cl2_transaction_observation"
        ).fetchone()[0] == 1


def test_v310_cl2_11_transaction_retry_source_and_economic_relations(
    tmp_path: Path,
    fixture: dict[str, object],
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    scenario = next(
        item for item in fixture["scenarios"] if item["id"] == "ordinary-duplicate-conflicts"
    )
    results = list(_scenario_steps(tmp_path, scenario, vectors, descriptor))
    assert [results[index][1].value if results[index][1] else results[index][2] for index in (2, 4, 5)] == [
        "TRANSACTION_ALREADY_PRESENT",
        "ECONOMIC_MATCH_REVIEW_REQUIRED",
        "SOURCE_CONFLICT",
    ]


def test_v310_cl2_12_duplicate_before_cas_stale_cas_and_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    store = CashLedgerStore.create(tmp_path / "store", [descriptor])
    observation = _observation(vectors, descriptor, "observation-original")
    try:
        store.append_observation(observation, expected_store_revision=0)
        assert store.append_observation(
            observation, expected_store_revision=0
        ) is PersistenceDisposition.OBSERVATION_ALREADY_PRESENT
        distinct = _observation(vectors, descriptor, "observation-economic")
        with _reason(PersistenceReason.REVISION_MISMATCH):
            store.append_observation(distinct, expected_store_revision=0)
    finally:
        store.close()
    exhausted = CashLedgerStore.create(tmp_path / "exhausted", [descriptor])
    monkeypatch.setattr(persistence, "MAX_REVISION", 0)
    try:
        with _reason(PersistenceReason.REVISION_EXHAUSTED):
            exhausted.append_observation(observation, expected_store_revision=0)
        assert exhausted.snapshot().store_revision == 0
    finally:
        exhausted.close()


def test_v310_cl2_13_genesis_transaction_bundle_head_known_answers_and_fixture(
    fixture: dict[str, object], vectors: dict[str, dict[str, object]]
) -> None:
    assert set(fixture) == {"domain", "scenarios", "vectors", "version"}
    assert fixture["domain"] == "v3.10-cash-ledger-persistence-fixture"
    assert fixture["version"] == CL2_CROSS_LANGUAGE_FIXTURE_VERSION
    assert [item["id"] for item in fixture["vectors"]] == sorted(
        item["id"] for item in fixture["vectors"]
    )
    additional_keys = {
        "codec_schema": set(),
        "codec_descriptor": {"schema_sha256"},
        "logical_source": set(),
        "observation": {"logical_source_sha256", "source_sha256"},
        "status_event": {
            "event_no",
            "from_status",
            "observation_sha256",
            "to_status",
        },
        "cl1_transaction": {"economic_sha256", "source_sha256"},
        "cl1_bundle": {
            "correction_sha256",
            "original_sha256",
            "reversal_sha256",
        },
        "ledger_head": {
            "ledger_revision",
            "previous_head_sha256",
            "transition_kind",
            "transition_sha256",
        },
        "export": {"ledger_head_sha256", "ledger_revision", "store_revision"},
        "backup_manifest": {
            "export_sha256",
            "ledger_head_sha256",
            "ledger_revision",
            "store_revision",
        },
    }
    for vector in fixture["vectors"]:
        assert set(vector) == {
            "canonical_json_ascii",
            "id",
            "kind",
            "sha256",
            *additional_keys[vector["kind"]],
        }
        raw = vector["canonical_json_ascii"].encode("ascii")
        assert canonical_json_bytes(parse_canonical_json(raw)) == raw
        assert hashlib.sha256(raw).hexdigest() == vector["sha256"]
    assert vectors["head-genesis"]["sha256"] == GENESIS_HEAD_SHA256
    assert vectors["head-sample-transaction"]["sha256"] == "980c7bf507e5724dd9de79492f3f1c2f5f235c1da4f9b31b512997eebc2714c2"
    assert vectors["head-sample-bundle"]["sha256"] == "b739f6edc1fe1d4bf6feeaf776eaeb9cc45a98fe6be90e4f5b6a27805a730b04"
    assert [item["id"] for item in fixture["scenarios"]] == sorted(
        item["id"] for item in fixture["scenarios"]
    )
    for scenario in fixture["scenarios"]:
        assert set(scenario) == {"id", "registry_ids", "steps"}
        for step in scenario["steps"]:
            assert set(step) == {
                "expected_disposition",
                "expected_head_sha256",
                "expected_ledger_revision",
                "expected_reason",
                "expected_store_revision",
                "input_ids",
                "operation",
                "supplied_ledger_revision",
                "supplied_store_revision",
            }
    for vector in fixture["vectors"]:
        if vector["kind"] == "ledger_head":
            assert LedgerHead.from_canonical_bytes(vector["canonical_json_ascii"]).sha256 == vector["sha256"]


def test_v310_cl2_14_three_correction_provenance_cardinalities(
    tmp_path: Path,
    fixture: dict[str, object],
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    identifiers = {
        "correction-distinct-and-second-branch",
        "correction-original-source-reuse",
        "correction-shared-new-source",
    }
    for scenario in fixture["scenarios"]:
        if scenario["id"] in identifiers:
            list(_scenario_steps(tmp_path, scenario, vectors, descriptor))


def test_v310_cl2_15_missing_evidence_partial_rows_and_ordinary_lineage_rejected(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    store = CashLedgerStore.create(tmp_path / "store", [descriptor])
    bundle = _bundle(vectors, "bundle-distinct")
    reversal_observation = _observation(
        vectors, descriptor, "observation-reversal-distinct"
    )
    try:
        with _reason(PersistenceReason.TRANSACTION_NOT_FOUND):
            store.append_correction_bundle(
                bundle,
                reversal_observation.sha256,
                _observation(
                    vectors, descriptor, "observation-correction-distinct"
                ).sha256,
                expected_store_revision=0,
                expected_ledger_revision=0,
            )
        store.append_observation(reversal_observation, expected_store_revision=0)
        with _reason(PersistenceReason.LINEAGE_CONFLICT):
            store.append_transaction(
                bundle.reversal,
                reversal_observation.sha256,
                expected_store_revision=1,
                expected_ledger_revision=0,
            )
        assert store.snapshot().ledger_revision == 0
    finally:
        store.close()


def test_v310_cl2_16_bundle_retry_and_second_branch_lineage_conflict(
    tmp_path: Path,
    fixture: dict[str, object],
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    scenario = next(
        item
        for item in fixture["scenarios"]
        if item["id"] == "correction-distinct-and-second-branch"
    )
    results = list(_scenario_steps(tmp_path, scenario, vectors, descriptor))
    assert results[5][1] is PersistenceDisposition.CORRECTION_BUNDLE_ALREADY_PRESENT
    assert results[-1][2] == PersistenceReason.LINEAGE_CONFLICT.value


def test_v310_cl2_17_physical_foreign_key_and_semantic_tampering_fails_closed(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "semantic"
    store = CashLedgerStore.create(root, [descriptor])
    store.append_observation(
        _observation(vectors, descriptor, "observation-original"),
        expected_store_revision=0,
    )
    store.close()
    with sqlite3.connect(root / "store.sqlite3") as connection:
        connection.execute(
            "UPDATE cl2_observation SET canonical=?", (sqlite3.Binary(b"{}"),)
        )
    with _reason(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE):
        CashLedgerStore.open(root, [descriptor])

    foreign = tmp_path / "foreign"
    store = CashLedgerStore.create(foreign, [descriptor])
    _populate_original(store, vectors, descriptor)
    store.close()
    with sqlite3.connect(foreign / "store.sqlite3") as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE cl2_transaction_observation SET observation_sha256=?",
            ("f" * 64,),
        )
    with _reason(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE):
        CashLedgerStore.open(foreign, [descriptor])


def test_v310_cl2_18_committed_wal_recovery_and_corrupt_sidecar_refusal(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "store"
    first = CashLedgerStore.create(root, [descriptor])
    database_bytes = (root / "store.sqlite3").read_bytes()
    wal_bytes: bytes
    shm_bytes: bytes
    try:
        first.append_observation(
            _observation(vectors, descriptor, "observation-original"),
            expected_store_revision=0,
        )
        wal_bytes = (root / "store.sqlite3-wal").read_bytes()
        shm_bytes = (root / "store.sqlite3-shm").read_bytes()
        assert len(wal_bytes) > 32
        second = CashLedgerStore.open(root, [descriptor])
        try:
            assert second.snapshot().store_revision == 1
        finally:
            second.close()
    finally:
        first.close()

    valid_recovery = tmp_path / "valid-recovery"
    valid_recovery.mkdir()
    (valid_recovery / "store.sqlite3").write_bytes(database_bytes)
    (valid_recovery / "store.sqlite3-wal").write_bytes(wal_bytes)
    (valid_recovery / "store.sqlite3-shm").write_bytes(shm_bytes)
    recovered = CashLedgerStore.open(valid_recovery, [descriptor])
    try:
        assert recovered.snapshot().store_revision == 1
    finally:
        recovered.close()

    corrupt_wal_root = tmp_path / "corrupt-wal"
    corrupt_wal_root.mkdir()
    corrupted = wal_bytes[:-1] + bytes([wal_bytes[-1] ^ 1])
    (corrupt_wal_root / "store.sqlite3").write_bytes(database_bytes)
    (corrupt_wal_root / "store.sqlite3-wal").write_bytes(corrupted)
    (corrupt_wal_root / "store.sqlite3-shm").write_bytes(shm_bytes)
    with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
        CashLedgerStore.open(corrupt_wal_root, [descriptor])
    assert (corrupt_wal_root / "store.sqlite3-wal").read_bytes() == corrupted

    corrupt_shm_root = tmp_path / "corrupt-shm"
    corrupt_shm_root.mkdir()
    corrupted_shm = shm_bytes[:-1] + bytes([shm_bytes[-1] ^ 1])
    (corrupt_shm_root / "store.sqlite3").write_bytes(database_bytes)
    (corrupt_shm_root / "store.sqlite3-wal").write_bytes(wal_bytes)
    (corrupt_shm_root / "store.sqlite3-shm").write_bytes(corrupted_shm)
    with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
        CashLedgerStore.open(corrupt_shm_root, [descriptor])
    assert (corrupt_shm_root / "store.sqlite3-shm").read_bytes() == corrupted_shm

    orphan_wal_root = tmp_path / "orphan-wal"
    orphan_wal_root.mkdir()
    (orphan_wal_root / "store.sqlite3").write_bytes(database_bytes)
    (orphan_wal_root / "store.sqlite3-wal").write_bytes(wal_bytes)
    with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
        CashLedgerStore.open(orphan_wal_root, [descriptor])
    assert (orphan_wal_root / "store.sqlite3-wal").read_bytes() == wal_bytes

    other_root = tmp_path / "other-store"
    other = CashLedgerStore.create(other_root, [descriptor])
    try:
        other.append_observation(
            _observation(vectors, descriptor, "observation-economic"),
            expected_store_revision=0,
        )
        other_shm = (other_root / "store.sqlite3-shm").read_bytes()
    finally:
        other.close()
    mismatched_root = tmp_path / "mismatched-sidecars"
    mismatched_root.mkdir()
    (mismatched_root / "store.sqlite3").write_bytes(database_bytes)
    (mismatched_root / "store.sqlite3-wal").write_bytes(wal_bytes)
    (mismatched_root / "store.sqlite3-shm").write_bytes(other_shm)
    with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
        CashLedgerStore.open(mismatched_root, [descriptor])

    journal_root = tmp_path / "rollback-journal"
    journal_root.mkdir()
    (journal_root / "store.sqlite3").write_bytes(database_bytes)
    journal = journal_root / "store.sqlite3-journal"
    journal.write_bytes(b"suspect-evidence")
    with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
        CashLedgerStore.open(journal_root, [descriptor])
    assert journal.read_bytes() == b"suspect-evidence"

    empty_root = tmp_path / "empty-shm"
    empty = CashLedgerStore.create(empty_root, [descriptor])
    empty_wal = (empty_root / "store.sqlite3-wal").read_bytes()
    empty_shm = (empty_root / "store.sqlite3-shm").read_bytes()
    assert empty_wal == b""
    empty.close()
    corrupted_empty_shm = empty_shm[:-1] + bytes([empty_shm[-1] ^ 1])
    (empty_root / "store.sqlite3-wal").write_bytes(empty_wal)
    (empty_root / "store.sqlite3-shm").write_bytes(corrupted_empty_shm)
    with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
        CashLedgerStore.open(empty_root, [descriptor])
    assert (empty_root / "store.sqlite3-shm").read_bytes() == corrupted_empty_shm


def test_v310_cl2_19_two_writer_busy_stale_writer_and_snapshot_consistency(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "store"
    first = CashLedgerStore.create(root, [descriptor], busy_timeout_ms=0)
    second = CashLedgerStore.open(root, [descriptor], busy_timeout_ms=0)
    try:
        first._connection.execute("BEGIN IMMEDIATE")
        with _reason(PersistenceReason.STORE_BUSY):
            second.append_observation(
                _observation(vectors, descriptor, "observation-original"),
                expected_store_revision=0,
            )
        first._connection.execute("ROLLBACK")
        before = second.snapshot()
        first.append_observation(
            _observation(vectors, descriptor, "observation-original"),
            expected_store_revision=0,
        )
        after = second.snapshot()
        assert (before.store_revision, after.store_revision) == (0, 1)
        with _reason(PersistenceReason.REVISION_MISMATCH):
            second.append_observation(
                _observation(vectors, descriptor, "observation-economic"),
                expected_store_revision=0,
            )
    finally:
        first.close()
        second.close()


def test_v310_cl2_20_precommit_faults_expose_only_prior_state(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    points = {
        "append_observation.before_transaction",
        "append_observation.after_codec",
        "append_observation.after_observation",
        "append_observation.after_meta",
        "append_observation.before_commit",
    }
    observation = _observation(vectors, descriptor, "observation-original")
    for index, point in enumerate(sorted(points)):
        def inject(candidate: str, target: str = point) -> None:
            if candidate == target:
                raise InjectedFault(target)

        root = tmp_path / f"observation-{index}"
        store = CashLedgerStore.create(root, [descriptor], fault_injector=inject)
        try:
            with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
                store.append_observation(observation, expected_store_revision=0)
            assert store.snapshot().store_revision == 0
            store.validate()
        finally:
            store.close()

    transaction_points = {
        "append_transaction.after_transaction",
        "append_transaction.after_provenance",
        "append_transaction.after_meta",
        "append_transaction.before_commit",
    }
    for index, point in enumerate(sorted(transaction_points)):
        root = tmp_path / f"transaction-{index}"
        setup = CashLedgerStore.create(root, [descriptor])
        setup.append_observation(observation, expected_store_revision=0)
        setup.close()

        def inject_transaction(candidate: str, target: str = point) -> None:
            if candidate == target:
                raise InjectedFault(target)

        store = CashLedgerStore.open(root, [descriptor], fault_injector=inject_transaction)
        try:
            with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
                store.append_transaction(
                    _transaction(vectors, "transaction-original"),
                    observation.sha256,
                    expected_store_revision=1,
                    expected_ledger_revision=0,
                )
            assert (
                store.snapshot().store_revision,
                store.snapshot().ledger_revision,
            ) == (1, 0)
            store.validate()
        finally:
            store.close()

    status_points = {
        "append_status_event.after_event",
        "append_status_event.after_meta",
        "append_status_event.before_commit",
    }
    review = InboxStatusEvent.from_canonical_bytes(
        vectors["status-review"]["canonical_json_ascii"]
    )
    for index, point in enumerate(sorted(status_points)):
        root = tmp_path / f"status-{index}"
        setup = CashLedgerStore.create(root, [descriptor])
        setup.append_observation(observation, expected_store_revision=0)
        setup.close()

        def inject_status(candidate: str, target: str = point) -> None:
            if candidate == target:
                raise InjectedFault(target)

        store = CashLedgerStore.open(root, [descriptor], fault_injector=inject_status)
        try:
            with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
                store.append_status_event(review, expected_store_revision=1)
            assert store.snapshot().store_revision == 1
            store.validate()
        finally:
            store.close()

    correction_points = {
        "append_correction_bundle.after_transactions",
        "append_correction_bundle.after_provenance",
        "append_correction_bundle.after_bundle",
        "append_correction_bundle.after_meta",
        "append_correction_bundle.before_commit",
    }
    bundle = _bundle(vectors, "bundle-distinct")
    reversal_observation = _observation(
        vectors, descriptor, "observation-reversal-distinct"
    )
    correction_observation = _observation(
        vectors, descriptor, "observation-correction-distinct"
    )
    for index, point in enumerate(sorted(correction_points)):
        root = tmp_path / f"correction-{index}"
        setup = CashLedgerStore.create(root, [descriptor])
        _populate_original(setup, vectors, descriptor)
        setup.append_observation(reversal_observation, expected_store_revision=2)
        setup.append_observation(correction_observation, expected_store_revision=3)
        setup.close()

        def inject_correction(candidate: str, target: str = point) -> None:
            if candidate == target:
                raise InjectedFault(target)

        store = CashLedgerStore.open(
            root, [descriptor], fault_injector=inject_correction
        )
        try:
            with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
                store.append_correction_bundle(
                    bundle,
                    reversal_observation.sha256,
                    correction_observation.sha256,
                    expected_store_revision=4,
                    expected_ledger_revision=1,
                )
            snapshot = store.snapshot()
            assert (snapshot.store_revision, snapshot.ledger_revision) == (4, 1)
            store.validate()
        finally:
            store.close()


def test_v310_cl2_21_postcommit_interruption_retry_has_one_effect(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    def inject(point: str) -> None:
        if point == "append_observation.after_commit":
            raise InjectedFault(point)

    store = CashLedgerStore.create(
        tmp_path / "store", [descriptor], fault_injector=inject
    )
    observation = _observation(vectors, descriptor, "observation-original")
    try:
        with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
            store.append_observation(observation, expected_store_revision=0)
        assert store.snapshot().store_revision == 1
        assert (
            store.append_observation(observation, expected_store_revision=0)
            is PersistenceDisposition.OBSERVATION_ALREADY_PRESENT
        )
        assert store.snapshot().store_revision == 1
    finally:
        store.close()


def test_v310_cl2_22_deterministic_sanitized_export_exact_order_and_bytes(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    store = CashLedgerStore.create(tmp_path / "store", [descriptor])
    try:
        _populate_original(store, vectors, descriptor)
        first = store.export_bytes()
        second = store.export_bytes()
        assert first == second
        exported = parse_canonical_json(first)
        assert set(exported) == {
            "codec_registry",
            "correction_bundles",
            "domain",
            "inbox_status_events",
            "ledger_head_json_ascii",
            "ledger_head_sha256",
            "ledger_revision",
            "ledger_transitions",
            "observations",
            "provenance_links",
            "schema_version",
            "store_revision",
            "transactions",
            "version",
        }
        assert b"store.sqlite3" not in first
        assert str(tmp_path).encode("ascii", errors="ignore") not in first
        assert b"password" not in first and b"api_key" not in first
        assert not any(isinstance(value, float) for value in _walk(exported))
    finally:
        store.close()


def _walk(value: object) -> Iterator[object]:
    yield value
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _git_output(*arguments: str) -> tuple[int, str]:
    with tempfile.TemporaryFile(mode="w+b") as output:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", *arguments],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        output.seek(0)
        return result.returncode, output.read().decode("utf-8")


def test_v310_cl2_23_online_backup_manifest_identity_and_no_clobber(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "store"
    backup = tmp_path / "backup"
    store = CashLedgerStore.create(root, [descriptor])
    try:
        _populate_original(store, vectors, descriptor)
        before = store.export_bytes()
        verification = store.backup(backup)
        assert sorted(item.name for item in backup.iterdir()) == [
            "export.json",
            "manifest.json",
            "store.sqlite3",
        ]
        assert verification.export_sha256 == sha256_hex(before)
        assert verify_backup(backup, [descriptor]) == verification
        with _reason(PersistenceReason.PATH_COLLISION):
            store.backup(backup)
        assert store.export_bytes() == before
        with _reason(PersistenceReason.PATH_INVALID):
            store.backup(root / "nested-backup")
        assert not (root / "nested-backup").exists()
        unexpected = root / "unexpected-child"
        unexpected.write_text("custody", encoding="ascii")
        try:
            with _reason(PersistenceReason.PATH_INVALID):
                store.validate()
            with _reason(PersistenceReason.PATH_INVALID):
                store.backup(tmp_path / "blocked-backup")
            assert not (tmp_path / "blocked-backup").exists()
        finally:
            unexpected.unlink()
    finally:
        store.close()


def test_v310_cl2_24_backup_missing_extra_and_tampered_evidence_is_read_only(
    tmp_path: Path,
    descriptor: CodecDescriptor,
) -> None:
    store = CashLedgerStore.create(tmp_path / "store", [descriptor])
    try:
        first = tmp_path / "backup-first"
        store.backup(first)
        (first / "export.json").write_bytes((first / "export.json").read_bytes() + b"\n")
        with _reason(PersistenceReason.BACKUP_MANIFEST_INVALID):
            verify_backup(first, [descriptor])
        assert (first / "export.json").read_bytes().endswith(b"\n")

        second = tmp_path / "backup-second"
        store.backup(second)
        (second / "extra").write_text("evidence", encoding="ascii")
        with _reason(PersistenceReason.BACKUP_MANIFEST_INVALID):
            verify_backup(second, [descriptor])
        assert (second / "extra").read_text(encoding="ascii") == "evidence"
    finally:
        store.close()


def test_v310_cl2_25_isolated_restore_exact_export_and_no_clobber(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    source = CashLedgerStore.create(tmp_path / "store", [descriptor])
    try:
        _populate_original(source, vectors, descriptor)
        backup = tmp_path / "backup"
        source.backup(backup)
        source_export = source.export_bytes()
        restored = restore_backup(backup, tmp_path / "restored", [descriptor])
        try:
            assert restored.export_bytes() == source_export
            assert restored.snapshot() == source.snapshot()
        finally:
            restored.close()
        with _reason(PersistenceReason.PATH_COLLISION):
            restore_backup(backup, tmp_path / "restored", [descriptor])
        with _reason(PersistenceReason.PATH_INVALID):
            restore_backup(backup, backup / "nested-restore", [descriptor])
        assert not (backup / "nested-restore").exists()
        assert verify_backup(backup, [descriptor]).export_sha256 == sha256_hex(
            source_export
        )

        corrupt_target = tmp_path / "corrupt-target"

        def corrupt_copy(point: str) -> None:
            if point == "restore.after_copy":
                database = (
                    tmp_path
                    / "corrupt-target.cl2-restore-staging"
                    / "store.sqlite3"
                )
                database.write_bytes(database.read_bytes() + b"\x00" * 4096)

        with _reason(PersistenceReason.RESTORE_VERIFICATION_FAILED):
            restore_backup(
                backup,
                corrupt_target,
                [descriptor],
                fault_injector=corrupt_copy,
            )
        assert not corrupt_target.exists()
    finally:
        source.close()


def test_v310_cl2_26_create_backup_restore_interruptions_never_promote(
    tmp_path: Path,
    descriptor: CodecDescriptor,
) -> None:
    def create_fault(point: str) -> None:
        if point == "create.before_promote":
            raise InjectedFault(point)

    create_target = tmp_path / "create-target"
    with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
        CashLedgerStore.create(
            create_target, [descriptor], fault_injector=create_fault
        )
    assert not create_target.exists()
    assert (tmp_path / "create-target.cl2-create-staging").exists()

    create_collision_target = tmp_path / "create-collision-target"

    def create_collision(point: str) -> None:
        if point == "create.before_promote":
            create_collision_target.mkdir()
            (create_collision_target / "sentinel").write_text("owned", encoding="ascii")

    with _reason(PersistenceReason.PATH_COLLISION):
        CashLedgerStore.create(
            create_collision_target,
            [descriptor],
            fault_injector=create_collision,
        )
    assert (create_collision_target / "sentinel").read_text(encoding="ascii") == "owned"
    assert (tmp_path / "create-collision-target.cl2-create-staging").exists()

    store = CashLedgerStore.create(tmp_path / "source", [descriptor])
    try:
        def backup_fault(point: str) -> None:
            if point == "backup.before_promote":
                raise InjectedFault(point)

        backup_target = tmp_path / "backup-target"
        store._fault_injector = backup_fault
        with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
            store.backup(backup_target)
        assert not backup_target.exists()
        assert (tmp_path / "backup-target.cl2-backup-staging").exists()

        backup_collision_target = tmp_path / "backup-collision-target"

        def backup_collision(point: str) -> None:
            if point == "backup.before_promote":
                backup_collision_target.mkdir()
                (backup_collision_target / "sentinel").write_text(
                    "owned", encoding="ascii"
                )

        store._fault_injector = backup_collision
        with _reason(PersistenceReason.PATH_COLLISION):
            store.backup(backup_collision_target)
        assert (
            backup_collision_target / "sentinel"
        ).read_text(encoding="ascii") == "owned"
        assert (tmp_path / "backup-collision-target.cl2-backup-staging").exists()

        source_drift_target = tmp_path / "source-drift-target"
        source_extra = store.root / "unexpected-during-backup"

        def backup_source_drift(point: str) -> None:
            if point == "backup.before_promote":
                source_extra.write_text("evidence", encoding="ascii")

        store._fault_injector = backup_source_drift
        try:
            with _reason(PersistenceReason.PATH_INVALID):
                store.backup(source_drift_target)
            assert source_extra.read_text(encoding="ascii") == "evidence"
            assert not source_drift_target.exists()
            assert (tmp_path / "source-drift-target.cl2-backup-staging").exists()
        finally:
            source_extra.unlink()
        store._fault_injector = None
        backup = tmp_path / "backup"
        store.backup(backup)

        def restore_fault(point: str) -> None:
            if point == "restore.before_promote":
                raise InjectedFault(point)

        restore_target = tmp_path / "restore-target"
        source_identity = sha256_hex((backup / "manifest.json").read_bytes())
        with _reason(PersistenceReason.INTERRUPTED_TRANSACTION):
            restore_backup(
                backup,
                restore_target,
                [descriptor],
                fault_injector=restore_fault,
            )
        assert not restore_target.exists()
        assert (tmp_path / "restore-target.cl2-restore-staging").exists()
        assert sha256_hex((backup / "manifest.json").read_bytes()) == source_identity

        restore_drift_target = tmp_path / "restore-drift-target"
        backup_extra = backup / "unexpected-during-restore"

        def restore_source_drift(point: str) -> None:
            if point == "restore.before_promote":
                backup_extra.write_text("evidence", encoding="ascii")

        try:
            with _reason(PersistenceReason.BACKUP_MANIFEST_INVALID):
                restore_backup(
                    backup,
                    restore_drift_target,
                    [descriptor],
                    fault_injector=restore_source_drift,
                )
            assert backup_extra.read_text(encoding="ascii") == "evidence"
            assert not restore_drift_target.exists()
            assert (tmp_path / "restore-drift-target.cl2-restore-staging").exists()
        finally:
            backup_extra.unlink()

        restore_collision_target = tmp_path / "restore-collision-target"

        def restore_collision(point: str) -> None:
            if point == "restore.before_promote":
                restore_collision_target.mkdir()
                (restore_collision_target / "sentinel").write_text(
                    "owned", encoding="ascii"
                )

        with _reason(PersistenceReason.PATH_COLLISION):
            restore_backup(
                backup,
                restore_collision_target,
                [descriptor],
                fault_injector=restore_collision,
            )
        assert (
            restore_collision_target / "sentinel"
        ).read_text(encoding="ascii") == "owned"
        assert (tmp_path / "restore-collision-target.cl2-restore-staging").exists()
    finally:
        store.close()


def test_v310_cl2_27_closed_reasons_and_ordered_multi_invalid_priority(
    tmp_path: Path,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    assert {item.value for item in PersistenceReason} == {
        "TYPE_INVALID",
        "CANONICAL_FORMAT_INVALID",
        "HASH_INVALID",
        "VERSION_UNSUPPORTED",
        "CODEC_UNSUPPORTED",
        "SANITIZED_CONTENT_INVALID",
        "SENSITIVE_CONTENT_FORBIDDEN",
        "PATH_INVALID",
        "PATH_COLLISION",
        "STORE_MISSING",
        "STORE_BUSY",
        "STORE_CLOSED",
        "SCHEMA_INVALID",
        "INTEGRITY_FAILURE",
        "SEMANTIC_INTEGRITY_FAILURE",
        "REVISION_MISMATCH",
        "REVISION_EXHAUSTED",
        "OBSERVATION_NOT_FOUND",
        "SOURCE_CONTENT_CONFLICT",
        "STATUS_TRANSITION_INVALID",
        "TRANSACTION_NOT_FOUND",
        "SOURCE_CONFLICT",
        "ECONOMIC_MATCH_REVIEW_REQUIRED",
        "LINEAGE_CONFLICT",
        "INTERRUPTED_TRANSACTION",
        "WAL_SIDECAR_INCONSISTENT",
        "BACKUP_MANIFEST_INVALID",
        "RESTORE_VERIFICATION_FAILED",
        "IO_FAILURE",
    }
    with _reason(PersistenceReason.TYPE_INVALID):
        InboxObservation.from_canonical_bytes(123, [])
    with _reason(PersistenceReason.CANONICAL_FORMAT_INVALID):
        InboxObservation.from_canonical_bytes("{}", [])
    with _reason(PersistenceReason.PATH_INVALID):
        CashLedgerStore.open(f"{tmp_path / 'embedded-nul'}\x00", [])
    oversized_event = canonical_json_bytes(
        {
            "domain": "v3.10-operation-inbox-status-event",
            "event_no": "1" * 5000,
            "from_status": "OBSERVED",
            "observation_sha256": "0" * 64,
            "reason": "REVIEW_REQUIRED",
            "related_bundle_sha256": None,
            "related_transaction_sha256": None,
            "to_status": "REVIEW_REQUIRED",
            "version": 1,
        }
    )
    with _reason(PersistenceReason.CANONICAL_FORMAT_INVALID):
        InboxStatusEvent.from_canonical_bytes(oversized_event)
    nested_depth = sys.getrecursionlimit() + 100
    with _reason(PersistenceReason.CANONICAL_FORMAT_INVALID):
        parse_canonical_json("[" * nested_depth + "0" + "]" * nested_depth)
    busy = sqlite3.OperationalError("translated contention message")
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY | (5 << 8)
    assert persistence._sqlite_reason(busy) is PersistenceReason.STORE_BUSY
    misleading = sqlite3.OperationalError("database is locked")
    misleading.sqlite_errorcode = sqlite3.SQLITE_IOERR
    assert persistence._sqlite_reason(misleading) is PersistenceReason.IO_FAILURE
    store = CashLedgerStore.create(tmp_path / "store", [descriptor])
    changed = _observation(vectors, descriptor, "observation-original-changed")
    original = _observation(vectors, descriptor, "observation-original")
    try:
        store.append_observation(original, expected_store_revision=0)
        with _reason(PersistenceReason.REVISION_MISMATCH):
            store.append_observation(changed, expected_store_revision=0)
        with _reason(PersistenceReason.TRANSACTION_NOT_FOUND):
            store.append_correction_bundle(
                _bundle(vectors, "bundle-distinct"),
                "e" * 64,
                "f" * 64,
                expected_store_revision=1,
                expected_ledger_revision=0,
            )
        store.close()
        with _reason(PersistenceReason.STORE_CLOSED):
            store.snapshot()
    finally:
        store.close()


def test_v310_cl2_28_three_path_delta_and_immutable_predecessor_files() -> None:
    allowed = {
        "current/trading_robot/cash_ledger_persistence.py",
        "current/tests/test_v3_10_cash_ledger_persistence.py",
        "current/tests/fixtures/v3_10_cash_ledger_persistence_vectors.json",
    }
    cumulative_allowed = {
        "docs/project/V3_10_CL2_APPEND_ONLY_PERSISTENCE_CONTRACT_RU.md",
        *allowed,
    }
    # Custody belongs to the accepted historical implementation, not a later
    # successor's cumulative delta. Missing history is a failure, never a PR
    # metadata substitute, skip, or request to fetch from inside pytest.
    replacement_exit, replacement_refs = _git_output(
        "for-each-ref", "--format=%(refname)", "refs/replace"
    )
    assert replacement_exit == 0 and not replacement_refs.strip()
    graft_exit, graft_path = _git_output(
        "rev-parse", "--path-format=absolute", "--git-path", "info/grafts"
    )
    assert graft_exit == 0 and not Path(graft_path.strip()).exists()
    for anchor in (
        ACCEPTED_CONTRACT_HEAD,
        CL1_PREDECESSOR,
        CL2_ACCEPTED_IMPLEMENTATION_HEAD,
    ):
        anchor_exit, _ = _git_output("cat-file", "-e", f"{anchor}^{{commit}}")
        assert anchor_exit == 0, f"Historical CL2 custody requires commit {anchor}"

    diff_exit, diff_text = _git_output(
        "diff",
        "--name-only",
        ACCEPTED_CONTRACT_HEAD,
        CL2_ACCEPTED_IMPLEMENTATION_HEAD,
    )
    assert diff_exit == 0
    assert {line.replace("\\", "/") for line in diff_text.splitlines()} == allowed
    ancestry_exit, ancestry_text = _git_output(
        "rev-list",
        "--left-right",
        "--count",
        f"{ACCEPTED_CONTRACT_HEAD}...{CL2_ACCEPTED_IMPLEMENTATION_HEAD}",
    )
    assert ancestry_exit == 0
    assert ancestry_text.split() == ["0", "5"]
    accepted_merge_exit, accepted_merge_base = _git_output(
        "merge-base", ACCEPTED_CONTRACT_HEAD, CL2_ACCEPTED_IMPLEMENTATION_HEAD
    )
    assert accepted_merge_exit == 0
    assert accepted_merge_base.strip() == ACCEPTED_CONTRACT_HEAD

    for path in (
        "docs/project/V3_10_CL2_APPEND_ONLY_PERSISTENCE_CONTRACT_RU.md",
        "current/trading_robot/cash_ledger_domain.py",
        "current/tests/test_v3_10_cash_ledger_domain.py",
        "current/tests/fixtures/v3_10_cash_ledger_vectors.json",
    ):
        for anchor in (ACCEPTED_CONTRACT_HEAD, CL2_ACCEPTED_IMPLEMENTATION_HEAD):
            blob_exit, _ = _git_output(
                "cat-file", "-e", f"{anchor}:{path}"
            )
            assert blob_exit == 0
        result, _ = _git_output(
            "diff", "--quiet", ACCEPTED_CONTRACT_HEAD,
            CL2_ACCEPTED_IMPLEMENTATION_HEAD, "--", path
        )
        assert result == 0

    merge_exit, merge_base = _git_output(
        "merge-base", CL1_PREDECESSOR, CL2_ACCEPTED_IMPLEMENTATION_HEAD
    )
    assert merge_exit == 0
    assert merge_base.strip() == CL1_PREDECESSOR
    cumulative_exit, cumulative_text = _git_output(
        "diff", "--name-only", CL1_PREDECESSOR, CL2_ACCEPTED_IMPLEMENTATION_HEAD
    )
    assert cumulative_exit == 0
    assert {
        line.replace("\\", "/") for line in cumulative_text.splitlines()
    } == cumulative_allowed
    for path in cumulative_allowed:
        historical_file_exit, _ = _git_output(
            "cat-file", "-e", f"{CL2_ACCEPTED_IMPLEMENTATION_HEAD}:{path}"
        )
        assert historical_file_exit == 0
    cumulative_count_exit, cumulative_count = _git_output(
        "rev-list", "--left-right", "--count",
        f"{CL1_PREDECESSOR}...{CL2_ACCEPTED_IMPLEMENTATION_HEAD}",
    )
    assert cumulative_count_exit == 0
    assert cumulative_count.split() == ["0", "7"]

    # Contemporary successors must retain the exact frozen implementation in
    # their ancestry; matching trees or unrelated reconstructed histories fail.
    successor_exit, _ = _git_output(
        "merge-base", "--is-ancestor", CL2_ACCEPTED_IMPLEMENTATION_HEAD, "HEAD"
    )
    assert successor_exit == 0


def test_v310_cl2_29_open_validation_never_raw_reads_live_shm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "live-store"
    store = CashLedgerStore.create(root, [descriptor])
    other = CashLedgerStore.create(tmp_path / "other-store", [descriptor])
    shared_memory = root / "store.sqlite3-shm"
    original_read_bytes = Path.read_bytes
    attempted_shm_reads: list[Path] = []

    def reject_live_shm(path: Path) -> bytes:
        if path == shared_memory and path.exists():
            attempted_shm_reads.append(path)
            raise PermissionError("simulated Windows live SHM denial")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_live_shm)
    try:
        store.append_observation(
            _observation(vectors, descriptor, "observation-original"),
            expected_store_revision=0,
        )
        expected = store.snapshot()
        exported = store.export_bytes()
        assert store.validate() == expected
        assert store.snapshot() == expected
        assert store.export_bytes() == exported
        assert store.backup(tmp_path / "backup").store_revision == expected.store_revision
        assert attempted_shm_reads == []

        with _reason(PersistenceReason.PATH_INVALID):
            persistence._validate_open_root(root, other._connection, other._custody)

        store._connection.execute("ATTACH DATABASE ':memory:' AS unexpected")
        try:
            with _reason(PersistenceReason.PATH_INVALID):
                store.validate()
        finally:
            store._connection.execute("DETACH DATABASE unexpected")

        journal = root / "store.sqlite3-journal"
        journal.write_bytes(b"suspect-evidence")
        try:
            with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
                store.validate()
        finally:
            journal.unlink()

        with _reason(PersistenceReason.WAL_SIDECAR_INCONSISTENT):
            persistence._validate_live_root(root)
        assert attempted_shm_reads == [shared_memory]
    finally:
        other.close()
        store.close()


def _replace_open_database_or_skip(
    root: Path,
    replacement_root: Path,
    displaced: Path,
) -> None:
    database = root / "store.sqlite3"
    try:
        os.replace(database, displaced)
    except PermissionError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 32:
            pytest.skip("Windows protects the open SQLite file with WinError 32")
        raise
    os.replace(replacement_root / "store.sqlite3", database)


def _restore_replaced_database(root: Path, displaced: Path) -> None:
    database = root / "store.sqlite3"
    if displaced.exists():
        if database.exists():
            database.unlink()
        os.replace(displaced, database)


def test_v310_cl2_30_live_identity_rejects_real_path_replacement(
    tmp_path: Path,
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "live-store"
    replacement_root = tmp_path / "replacement-store"
    store = CashLedgerStore.create(root, [descriptor])
    replacement = CashLedgerStore.create(replacement_root, [descriptor])
    replacement.close()
    displaced = tmp_path / "displaced-original.sqlite3"
    before = store.export_bytes()
    try:
        _replace_open_database_or_skip(root, replacement_root, displaced)
        reported = Path(store._connection.execute("PRAGMA database_list").fetchone()[2])
        assert reported.resolve(strict=True) == (root / "store.sqlite3").resolve(strict=True)
        with _reason(PersistenceReason.PATH_INVALID):
            store.validate()
        _restore_replaced_database(root, displaced)
        assert store.export_bytes() == before
    finally:
        _restore_replaced_database(root, displaced)
        store.close()


def test_v310_cl2_31_backup_rechecks_retained_source_identity_before_promote(
    tmp_path: Path,
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "live-store"
    replacement_root = tmp_path / "replacement-store"
    displaced = tmp_path / "displaced-original.sqlite3"
    replacement = CashLedgerStore.create(replacement_root, [descriptor])
    replacement.close()

    def replace_before_promote(point: str) -> None:
        if point == "backup.before_promote":
            _replace_open_database_or_skip(root, replacement_root, displaced)

    store = CashLedgerStore.create(root, [descriptor], fault_injector=replace_before_promote)
    before = store.export_bytes()
    target = tmp_path / "backup"
    try:
        with _reason(PersistenceReason.PATH_INVALID):
            store.backup(target)
        assert not target.exists()
        _restore_replaced_database(root, displaced)
        assert store.export_bytes() == before
    finally:
        _restore_replaced_database(root, displaced)
        store.close()


def test_v310_cl2_32_same_path_rejects_identity_token_and_handle_mismatch(
    tmp_path: Path,
    descriptor: CodecDescriptor,
) -> None:
    store = CashLedgerStore.create(tmp_path / "store", [descriptor])
    other = CashLedgerStore.create(tmp_path / "other", [descriptor])
    original_identity = store._custody._identity
    original_handle = store._custody._handle
    foreign_handle = (other.root / "store.sqlite3").open("rb", buffering=0)
    try:
        reported = Path(store._connection.execute("PRAGMA database_list").fetchone()[2])
        assert reported.resolve(strict=True) == (store.root / "store.sqlite3").resolve(strict=True)
        store._custody._identity = persistence._DatabaseIdentity(
            device=original_identity.device,
            inode=original_identity.inode + 1,
        )
        with _reason(PersistenceReason.PATH_INVALID):
            store.validate()
        store._custody._identity = original_identity

        store._custody._handle = foreign_handle
        with _reason(PersistenceReason.PATH_INVALID):
            store.validate()
    finally:
        store._custody._handle = original_handle
        store._custody._identity = original_identity
        foreign_handle.close()
        other.close()
        store.close()


def test_v310_cl2_33_custody_handle_lifetime_and_failed_open_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vectors: dict[str, dict[str, object]],
    descriptor: CodecDescriptor,
) -> None:
    root = tmp_path / "store"
    store = CashLedgerStore.create(root, [descriptor])
    identity = store._custody.identity
    custody = store._custody
    assert not custody.closed
    store.append_observation(
        _observation(vectors, descriptor, "observation-original"),
        expected_store_revision=0,
    )
    assert custody.identity == identity
    custody.validate(root / "store.sqlite3")
    store.close()
    assert custody.closed

    closed_custodies = []
    for _ in range(8):
        reopened = CashLedgerStore.open(root, [descriptor])
        closed_custodies.append(reopened._custody)
        reopened.close()
    assert all(item.closed for item in closed_custodies)

    captured = []
    real_open_custody = persistence._open_database_custody

    def capture_custody(database, expected_identity):
        result = real_open_custody(database, expected_identity)
        captured.append(result)
        return result

    def reject_live_open(*_args, **_kwargs):
        raise PersistenceError(PersistenceReason.PATH_INVALID)

    monkeypatch.setattr(persistence, "_open_database_custody", capture_custody)
    monkeypatch.setattr(persistence, "_validate_open_root", reject_live_open)
    with _reason(PersistenceReason.PATH_INVALID):
        CashLedgerStore.open(root, [descriptor])
    assert len(captured) == 1 and captured[0].closed
