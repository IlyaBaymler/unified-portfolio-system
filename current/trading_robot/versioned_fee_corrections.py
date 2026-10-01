"""Detached v3 monetary review journal for one first same-ID fee revision.

The frozen v2 export is retained verbatim. One authenticated row atomically
contains an independently revalidated CL1 reversal/correction bundle and an
explicit observation-version provenance link. No synthetic provider IDs,
rewritten v1 observations, mutable 'latest version', or implicit runtime cutover.
Old CL2/CL4/CL5/CL6 formats remain unchanged and reject this export.
"""
from __future__ import annotations

import hmac
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from . import cash_ledger_opening_reconciliation as cl4
from . import cash_ledger_persistence as cl2
from . import cash_observation_versions as versions
from .broker_read_adapters import BrokerEnvironment
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha, evaluate_captured_fee_revision

SCHEMA_VERSION = 3
SQLITE_APPLICATION_ID = 0x434C3203
MAX_EXPORT_BYTES = 12 * 1024 * 1024
DOMAIN = "CL2_VERSION_FEE_MONETARY_REVIEW_V3"
_EXPORT_FIELDS = frozenset({"domain", "version", "financial_ready", "frozen_review_export_ascii",
                           "migration_json_ascii", "correction_record_json_ascii"})
_MIGRATION_FIELDS = frozenset({"domain", "version", "mode", "account_scope_sha256", "identity_key_id",
    "frozen_review_export_sha256", "version_head_sha256", "codec_registry_sha256", "created_at"})
_RECORD_FIELDS = frozenset({"domain", "version", "status", "parent_sha256", "version_record_sha256",
    "expected_store_revision", "capture_json_ascii", "original_transaction_sha256", "report_json_ascii",
    "bundle_json_ascii", "reversal_json_ascii", "correction_json_ascii", "provenance", "ledger_head_json_ascii"})
_SQL = (
    "CREATE TABLE cl2_version_cash_base (singleton INTEGER PRIMARY KEY CHECK(singleton=1), review BLOB NOT NULL, migration BLOB NOT NULL)",
    "CREATE TABLE cl2_version_cash_correction (singleton INTEGER PRIMARY KEY CHECK(singleton=1), canonical BLOB NOT NULL, sha256 TEXT NOT NULL)",
    "CREATE TRIGGER freeze_base_update BEFORE UPDATE ON cl2_version_cash_base BEGIN SELECT RAISE(ABORT,'FROZEN_REVIEW'); END",
    "CREATE TRIGGER freeze_base_delete BEFORE DELETE ON cl2_version_cash_base BEGIN SELECT RAISE(ABORT,'FROZEN_REVIEW'); END",
    "CREATE TRIGGER freeze_correction_update BEFORE UPDATE ON cl2_version_cash_correction BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CORRECTION'); END",
    "CREATE TRIGGER freeze_correction_delete BEFORE DELETE ON cl2_version_cash_correction BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CORRECTION'); END",
)


class VersionFeeCorrectionError(RuntimeError):
    """Bounded message only; exports contain private financial history."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise VersionFeeCorrectionError("VERSION_CORRECTION_" + code)


def _call(injector: Callable[[str], None] | None, point: str) -> None:
    if injector is not None:
        injector(point)


def _schema(conn: sqlite3.Connection) -> tuple:
    return tuple(tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name"))


def _expected_schema() -> tuple:
    conn = sqlite3.connect(":memory:")
    try:
        for sql in _SQL:
            conn.execute(sql)
        return _schema(conn)
    finally:
        conn.close()


_SCHEMA = _expected_schema()


def _signed_body(raw: str, key: bytes, fields: frozenset[str]) -> dict[str, Any]:
    data = _parse(raw)
    _require(type(data) is dict and set(data) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = data["payload"]
    _require(type(body) is dict and frozenset(body) == fields and body["domain"] == DOMAIN
             and type(body["version"]) is int and body["version"] == 3, "ENVELOPE_INVALID")
    expected = _parse(_sealed(body, key))["hmac_sha256"]
    _require(type(data["hmac_sha256"]) is str and hmac.compare_digest(data["hmac_sha256"], expected), "AUTHENTICATION_FAILED")
    return body


@dataclass(frozen=True, slots=True)
class VersionFeeSnapshot:
    export_sha256: str
    review_export_sha256: str
    observed_version_head_sha256: str
    correction_head_sha256: str
    store_revision: int
    ledger_revision: int
    ledger_head_sha256: str
    cash_nano: int
    transaction_count: int
    correction_recorded: bool
    financial_ready: bool = False
    runtime_cutover_performed: bool = False


@dataclass(frozen=True, slots=True)
class VersionFeeAppendResult:
    record_sha256: str
    version_record_sha256: str
    bundle_sha256: str
    appended_transactions: int
    cash_delta_nano: int
    replay: bool
    runtime_authority_granted: bool = False

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_RESULT", "version": 3,
            "status": "RECORDED_DETACHED_NO_RUNTIME_AUTHORITY", "record_sha256": self.record_sha256,
            "version_record_sha256": self.version_record_sha256, "bundle_sha256": self.bundle_sha256,
            "appended_transactions": self.appended_transactions, "replay": self.replay,
            "runtime_authority_granted": False, "financial_ready": False}


@dataclass(frozen=True, slots=True)
class _Checked:
    snapshot: VersionFeeSnapshot
    review: bytes
    graph: Any
    migration_raw: str
    migration: dict[str, Any]
    record: dict[str, Any] | None


def _record_payload(checked: _Checked, capture: bytes, target: str, *, key: bytes,
                    key_id: str, account: str, registry: Any) -> dict[str, Any]:
    graph = checked.graph
    _require(graph.snapshot.revision_count == 1, "FIRST_VERSION_ONLY")
    _require(graph.snapshot.store_revision < cl2.MAX_REVISION
             and graph.snapshot.ledger_revision < cl2.MAX_REVISION, "REVISION_EXHAUSTED")
    version = graph.records[0]
    version_hash = _parse(checked.review)["version_records"][0]["sha256"]
    _require(version["version_no"] == "2" and version["sequence"] == "1"
             and version["previous_version_sha256"] is None, "FIRST_VERSION_ONLY")
    evaluation = evaluate_captured_fee_revision(graph.base, capture,
        original_transaction_sha256=target, codec_registry=tuple(registry.values()),
        identity_key=key, identity_key_id=key_id, account_scope_sha256=account)
    _require(version["previous_observation_sha256"] == evaluation.revision_evidence.previous_observation_sha256
             and version["observation_sha256"] == evaluation.observation.sha256
             and version["observation_json_ascii"] == evaluation.observation.canonical_bytes.decode("ascii")
             and version["evidence"] == evaluation.revision_evidence.to_dict(), "VERSION_ECONOMIC_BINDING_INVALID")
    bundle = evaluation.bundle
    head = cl2.LedgerHead(graph.snapshot.ledger_revision + 1, graph.snapshot.ledger_head_sha256,
                         "CORRECTION_BUNDLE", bundle.sha256)
    # The version linkage lives in the authenticated v3 record and is checked
    # on EVERY export read, not falsely represented as v1 logical-source dedup.
    return {"domain": DOMAIN, "version": 3, "status": "RECORDED_DETACHED_NO_RUNTIME_AUTHORITY",
        "parent_sha256": _sha(checked.migration_raw.encode("ascii")),
        "version_record_sha256": version_hash, "expected_store_revision": str(graph.snapshot.store_revision),
        "capture_json_ascii": capture.decode("ascii"), "original_transaction_sha256": target,
        "report_json_ascii": evaluation.report.decode("ascii"), "bundle_json_ascii": bundle.canonical_bytes.decode("ascii"),
        "reversal_json_ascii": bundle.reversal.canonical_bytes.decode("ascii"),
        "correction_json_ascii": bundle.correction.canonical_bytes.decode("ascii"),
        "provenance": {"observed_version_record_sha256": version_hash,
            "observation_sha256": evaluation.observation.sha256,
            "previous_observation_sha256": evaluation.revision_evidence.previous_observation_sha256,
            "transaction_sha256s": [bundle.reversal.sha256, bundle.correction.sha256]},
        "ledger_head_json_ascii": head.canonical_bytes.decode("ascii")}


def _validate_export(raw: bytes, registry: Any, key: bytes, key_id: str, account: str,
                     *, expected_review_export_sha256: str | None = None,
                     expected_correction_head_sha256: str | None = None) -> _Checked:
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_EXPORT_BYTES, "BOUNDS_INVALID")
    data = _parse(raw)
    _require(type(data) is dict and frozenset(data) == _EXPORT_FIELDS
             and data["domain"] == DOMAIN and type(data["version"]) is int
             and data["version"] == 3 and data["financial_ready"] is False, "EXPORT_INVALID")
    review = data["frozen_review_export_ascii"].encode("ascii")
    graph = versions._validate_export(review, registry, key, key_id, account)
    _require(graph.snapshot.revision_count == 1, "FIRST_VERSION_ONLY")
    migration_raw = data["migration_json_ascii"]
    migration = _signed_body(migration_raw, key, _MIGRATION_FIELDS)
    _require(migration["mode"] == "DETACHED_MONETARY_QUALIFICATION_NO_CUTOVER"
             and migration["account_scope_sha256"] == account and migration["identity_key_id"] == key_id
             and migration["frozen_review_export_sha256"] == _sha(review)
             and migration["version_head_sha256"] == graph.snapshot.version_head_sha256
             and migration["codec_registry_sha256"] == versions._registry_sha(registry), "MIGRATION_BINDING_INVALID")
    created_at = versions._time(migration["created_at"])
    _require(created_at >= graph.records[0]["evidence"]["recorded_at"], "TIME_INVALID")
    if expected_review_export_sha256 is not None:
        _require(_sha(review) == versions._hash(expected_review_export_sha256), "REVIEW_PIN_MISMATCH")
    head = _sha(migration_raw.encode("ascii"))
    base = _parse(graph.base)
    projection = cl4.project_shadow_cash(graph.base, account_scope_sha256=account,
        environment=BrokerEnvironment.SANDBOX, as_of=created_at, identity_key=key)
    _require(projection.complete and projection.version == 3, "BASE_LEDGER_INVALID")
    initial = VersionFeeSnapshot(_sha(raw), _sha(review), graph.snapshot.version_head_sha256,
        head, graph.snapshot.store_revision, graph.snapshot.ledger_revision,
        graph.snapshot.ledger_head_sha256, projection.expected_cash.minor_units,
        len(base["transactions"]), False)
    checked = _Checked(initial, review, graph, migration_raw, migration, None)
    record = None
    record_raw = data["correction_record_json_ascii"]
    if record_raw is not None:
        record = _signed_body(record_raw, key, _RECORD_FIELDS)
        recomputed = _record_payload(checked, record["capture_json_ascii"].encode("ascii"),
            record["original_transaction_sha256"], key=key, key_id=key_id, account=account, registry=registry)
        _require(record == recomputed, "ECONOMIC_RECORD_INVALID")
        report = _parse(record["report_json_ascii"])
        ledger_head = _parse(record["ledger_head_json_ascii"])
        head = _sha(record_raw.encode("ascii"))
        initial = VersionFeeSnapshot(_sha(raw), _sha(review), graph.snapshot.version_head_sha256,
            head, graph.snapshot.store_revision + 1, graph.snapshot.ledger_revision + 1,
            _sha(record["ledger_head_json_ascii"].encode("ascii")), int(report["cash_after_nano"]),
            len(base["transactions"]) + 2, True)
        _require(int(ledger_head["ledger_revision"]) == initial.ledger_revision, "HEAD_INVALID")
    if expected_correction_head_sha256 is not None:
        _require(head == versions._hash(expected_correction_head_sha256), "CORRECTION_PIN_MISMATCH")
    return _Checked(initial, review, graph, migration_raw, migration, record)


def validate_fee_correction_export(raw: bytes, *, codec_registry: object, identity_key: bytes,
        identity_key_id: str, account_scope_sha256: str, expected_review_export_sha256: str | None = None,
        expected_correction_head_sha256: str | None = None) -> VersionFeeSnapshot:
    """Dedicated reader: revalidate full versions, evidence, provenance and CL1 money.

    The output is a detached as-of projection, NEVER a CL5/CL6 cash budget.
    Old valid snapshots require external pins to detect rollback.
    """
    try:
        return _validate_export(raw, versions._registry(codec_registry), versions._key(identity_key),
            versions._key_id(identity_key_id), versions._hash(account_scope_sha256),
            expected_review_export_sha256=expected_review_export_sha256,
            expected_correction_head_sha256=expected_correction_head_sha256).snapshot
    except VersionFeeCorrectionError:
        raise
    except Exception:
        raise VersionFeeCorrectionError("VERSION_CORRECTION_EXPORT_INVALID") from None


def _export(conn: sqlite3.Connection) -> bytes:
    row = conn.execute("SELECT review,migration FROM cl2_version_cash_base WHERE singleton=1").fetchone()
    _require(row is not None, "BASE_MISSING")
    correction = conn.execute("SELECT canonical,sha256 FROM cl2_version_cash_correction WHERE singleton=1").fetchone()
    if correction is not None:
        _require(_sha(bytes(correction[0])) == correction[1], "ROW_HASH_INVALID")
    return _canonical({"domain": DOMAIN, "version": 3, "financial_ready": False,
        "frozen_review_export_ascii": bytes(row[0]).decode("ascii"),
        "migration_json_ascii": bytes(row[1]).decode("ascii"),
        "correction_record_json_ascii": None if correction is None else bytes(correction[0]).decode("ascii")})


def _connection_check(conn: sqlite3.Connection, registry: Any, key: bytes, key_id: str, account: str) -> _Checked:
    _require(conn.execute("PRAGMA application_id").fetchone()[0] == SQLITE_APPLICATION_ID
             and conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
             and conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
             and _schema(conn) == _SCHEMA, "SCHEMA_INVALID")
    _require([tuple(r) for r in conn.execute("PRAGMA integrity_check")] == [("ok",)]
             and not conn.execute("PRAGMA foreign_key_check").fetchall(), "INTEGRITY_FAILURE")
    return _validate_export(_export(conn), registry, key, key_id, account)


def _publish_export(raw: bytes, target_root: object, *, registry: Any, key: bytes, key_id: str,
                    account: str, injector: Callable[[str], None] | None = None) -> Path:
    checked = _validate_export(raw, registry, key, key_id, account)
    data = _parse(raw)
    target = cl2._path_from(target_root)
    staging = target.with_name(target.name + ".fee-review-staging")
    cl2._validate_target_parent(target); cl2._require_absent_target(target, staging)
    conn, promoted, owned = None, False, False
    try:
        staging.mkdir(mode=0o700); owned = True
        conn = cl2._connect(staging / "store.sqlite3", 0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        for sql in _SQL:
            conn.execute(sql)
        conn.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
        conn.execute("PRAGMA user_version=3")
        conn.execute("INSERT INTO cl2_version_cash_base VALUES(1,?,?)",
                     (checked.review, checked.migration_raw.encode("ascii")))
        if checked.record is not None:
            record = data["correction_record_json_ascii"].encode("ascii")
            conn.execute("INSERT INTO cl2_version_cash_correction VALUES(1,?,?)", (record, _sha(record)))
        _connection_check(conn, registry, key, key_id, account)
        _call(injector, "copy.before_commit")
        conn.execute("COMMIT")
        _call(injector, "copy.after_commit")
        _require(_export(conn) == raw, "COPY_MISMATCH")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close(); conn = None
        _call(injector, "copy.before_promote")
        cl2._promote_staging(staging, target); promoted = True
        _call(injector, "copy.after_promote")
        return target
    finally:
        if conn is not None:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()
        if owned and not promoted and staging.exists():
            shutil.rmtree(staging)


def create_fee_correction_review(review_export: bytes, target_root: object, *, codec_registry: object,
        identity_key: bytes, identity_key_id: str, account_scope_sha256: str,
        expected_review_export_sha256: str, expected_version_head_sha256: str, created_at: str,
        fault_injector: Callable[[str], None] | None = None) -> Path:
    """Create a NEW detached v3 journal from pinned v2 bytes; no in-place upgrade."""
    registry, key = versions._registry(codec_registry), versions._key(identity_key)
    key_id, account = versions._key_id(identity_key_id), versions._hash(account_scope_sha256)
    _require(_sha(review_export) == versions._hash(expected_review_export_sha256), "REVIEW_PIN_MISMATCH")
    graph = versions._validate_export(review_export, registry, key, key_id, account, expected_version_head_sha256)
    _require(graph.snapshot.revision_count == 1, "FIRST_VERSION_ONLY")
    migration = _sealed({"domain": DOMAIN, "version": 3, "mode": "DETACHED_MONETARY_QUALIFICATION_NO_CUTOVER",
        "account_scope_sha256": account, "identity_key_id": key_id,
        "frozen_review_export_sha256": _sha(review_export), "version_head_sha256": graph.snapshot.version_head_sha256,
        "codec_registry_sha256": versions._registry_sha(registry), "created_at": versions._time(created_at)}, key)
    raw = _canonical({"domain": DOMAIN, "version": 3, "financial_ready": False,
        "frozen_review_export_ascii": review_export.decode("ascii"), "migration_json_ascii": migration.decode("ascii"),
        "correction_record_json_ascii": None})
    return _publish_export(raw, target_root, registry=registry, key=key, key_id=key_id, account=account, injector=fault_injector)


def restore_fee_correction_export(raw: bytes, target_root: object, *, codec_registry: object,
        identity_key: bytes, identity_key_id: str, account_scope_sha256: str,
        expected_export_sha256: str, fault_injector: Callable[[str], None] | None = None) -> Path:
    """Restore this complete detached journal to an absent target, not all runtime owners."""
    _require(_sha(raw) == versions._hash(expected_export_sha256), "EXPORT_PIN_MISMATCH")
    return _publish_export(raw, target_root, registry=versions._registry(codec_registry), key=versions._key(identity_key),
        key_id=versions._key_id(identity_key_id), account=versions._hash(account_scope_sha256), injector=fault_injector)


class VersionedFeeCorrectionStore:
    """One verified version-bound bundle; not a subtype of a trading CashLedgerStore."""
    def __init__(self, root: Path, conn: sqlite3.Connection, registry: Any, key: bytes,
                 key_id: str, account: str, injector: Callable[[str], None] | None,
                 custody: cl2._DatabaseCustody):
        self.root, self._connection, self._registry = root, conn, registry
        self._key, self._key_id, self._account, self._injector = key, key_id, account, injector
        self._custody = custody
        self._closed = False

    @classmethod
    def open(cls, root: object, *, codec_registry: object, identity_key: bytes,
             identity_key_id: str, account_scope_sha256: str,
             expected_correction_head_sha256: str | None = None,
             fault_injector: Callable[[str], None] | None = None) -> Self:
        path = cl2._path_from(root); db, identity = cl2._validate_live_root_identity(path)
        registry, key = versions._registry(codec_registry), versions._key(identity_key)
        key_id, account = versions._key_id(identity_key_id), versions._hash(account_scope_sha256)
        custody = cl2._open_database_custody(db, identity)
        try:
            ro = versions._read_connection(db)
            try:
                cl2._validate_open_root(path, ro, custody)
                ro.execute("BEGIN")
                _connection_check(ro, registry, key, key_id, account)
            finally:
                ro.close()
            conn = cl2._connect(db, 0)
            try:
                cl2._validate_open_root(path, conn, custody)
                conn.execute("BEGIN")
                checked = _connection_check(conn, registry, key, key_id, account)
                if expected_correction_head_sha256 is not None:
                    _require(checked.snapshot.correction_head_sha256 == versions._hash(expected_correction_head_sha256), "CORRECTION_PIN_MISMATCH")
                conn.execute("COMMIT")
                return cls(path, conn, registry, key, key_id, account, fault_injector, custody)
            except BaseException:
                conn.close(); raise
        except BaseException:
            custody.close(); raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            try:
                try:
                    if self._connection.in_transaction:
                        self._connection.execute("ROLLBACK")
                finally:
                    self._connection.close()
            finally:
                self._custody.close(); self._closed = True

    def _check(self) -> _Checked:
        _require(not self._closed, "STORE_CLOSED")
        cl2._validate_open_root(self.root, self._connection, self._custody)
        return _connection_check(self._connection, self._registry, self._key, self._key_id, self._account)

    def export_bytes(self) -> bytes:
        _require(not self._closed, "STORE_CLOSED")
        self._connection.execute("BEGIN")
        try:
            self._check()
            return _export(self._connection)
        finally:
            self._connection.execute("ROLLBACK")

    def snapshot(self) -> VersionFeeSnapshot:
        return _validate_export(self.export_bytes(), self._registry, self._key, self._key_id, self._account).snapshot

    def append_verified_fee_revision(self, capture: bytes, *, original_transaction_sha256: str,
            expected_store_revision: int, expected_review_export_sha256: str,
            expected_correction_head_sha256: str) -> VersionFeeAppendResult:
        _require(not self._closed and type(capture) is bytes, "INPUT_INVALID")
        _require(type(expected_store_revision) is int and 0 <= expected_store_revision <= cl2.MAX_REVISION, "CAS_INVALID")
        versions._hash(expected_review_export_sha256); versions._hash(expected_correction_head_sha256)
        versions._hash(original_transaction_sha256)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            raise VersionFeeCorrectionError("VERSION_CORRECTION_STORE_BUSY") from None
        try:
            checked = self._check()
            _require(checked.snapshot.review_export_sha256 == expected_review_export_sha256, "REVIEW_PIN_MISMATCH")
            _require(expected_store_revision == checked.graph.snapshot.store_revision
                     and expected_correction_head_sha256 == _sha(checked.migration_raw.encode("ascii")), "CAS_CONFLICT")
            payload = _record_payload(checked, capture, original_transaction_sha256,
                key=self._key, key_id=self._key_id, account=self._account, registry=self._registry)
            report = _parse(payload["report_json_ascii"])
            signed = _sealed(payload, self._key)
            result = VersionFeeAppendResult(_sha(signed), payload["version_record_sha256"],
                _sha(payload["bundle_json_ascii"].encode("ascii")), 2, int(report["cash_delta_nano"]), False)
            if checked.record is not None:
                _require(checked.record == payload, "REPLAY_CONFLICT_OR_SECOND_CORRECTION")
                self._connection.execute("ROLLBACK")
                return VersionFeeAppendResult(result.record_sha256, result.version_record_sha256,
                    result.bundle_sha256, 0, result.cash_delta_nano, True)
            _call(self._injector, "correction.before_insert")
            self._connection.execute("INSERT INTO cl2_version_cash_correction VALUES(1,?,?)", (signed, _sha(signed)))
            _call(self._injector, "correction.after_insert")
            self._check()
            _call(self._injector, "correction.before_commit")
            self._check()
            self._connection.execute("COMMIT")
            _call(self._injector, "correction.after_commit")
            return result
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def append_transaction(self, *_: object, **__: object) -> None:
        _require(False, "GENERIC_WRITER_DISABLED")

    def append_correction_bundle(self, *_: object, **__: object) -> None:
        _require(False, "VERSION_EVIDENCE_REQUIRED")
