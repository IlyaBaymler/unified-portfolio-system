"""Opt-in CL2 v2 revision custody; the copied monetary base is frozen.

This is NOT a trading store. It records same-logical-source observations for
review, without inventing provider IDs, rewriting v1, or authorizing a posting.
Runtime cutover and a monetary revision consumer require a separate contract.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import shutil
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from . import cash_ledger_opening_reconciliation as cl4
from . import cash_ledger_persistence as v1
from .broker_read_adapters import TBANK_OPERATION_CODEC

SCHEMA_VERSION = 2
EXPORT_VERSION = 2
SQLITE_APPLICATION_ID = 0x434C3202
MAX_EXPORT_BYTES = 32 * 1024 * 1024
MAX_VERSIONS = 4096
_DOMAIN = "v3.10-cl2-observation-revision-review"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_EXPORT_KEYS = frozenset({
    "domain", "version", "schema_version", "financial_ready", "base_export_json_ascii",
    "migration_json_ascii", "version_records", "revision_count", "version_head_sha256",
})
_MIGRATION_KEYS = frozenset({
    "domain", "version", "mode", "account_scope_sha256", "identity_key_id",
    "base_export_sha256", "codec_registry_sha256", "created_at",
})
_RECORD_KEYS = frozenset({
    "domain", "version", "change_kind", "status", "sequence", "version_no",
    "logical_source_sha256", "previous_observation_sha256", "previous_version_sha256",
    "global_parent_sha256", "observation_json_ascii", "observation_sha256", "evidence",
})
_EVIDENCE_KEYS = frozenset({
    "previous_observation_sha256", "observation_sha256", "request_sha256",
    "response_sha256", "binding_report_sha256", "recorded_at",
})


class ObservationVersionError(RuntimeError):
    """Bounded reason only; raw provider/account data never enters the message."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _fail(reason: str) -> None:
    raise ObservationVersionError(reason)


def _hash(value: object) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        _fail("HASH_INVALID")
    return value


def _key(value: object) -> bytes:
    if type(value) is not bytes or not 32 <= len(value) <= 4096:
        _fail("IDENTITY_KEY_INVALID")
    return value


def _key_id(value: object) -> str:
    if type(value) is not str or _KEY_ID.fullmatch(value) is None:
        _fail("IDENTITY_KEY_INVALID")
    return value


def _uint(value: object, *, maximum: int = v1.MAX_REVISION) -> int:
    if type(value) is not str or re.fullmatch(r"0|[1-9][0-9]{0,18}", value) is None:
        _fail("CANONICAL_INTEGER_INVALID")
    number = int(value)
    if number > maximum:
        _fail("BOUNDS_EXCEEDED")
    return number


def _time(value: object) -> str:
    if type(value) is not str or not v1._timestamp_is_valid(value):
        _fail("TIMESTAMP_INVALID")
    return value


def _canonical(value: object) -> bytes:
    try:
        return v1.canonical_json_bytes(value)
    except v1.PersistenceError as error:
        raise ObservationVersionError("CANONICAL_INVALID") from error


def _parse(value: object) -> object:
    try:
        return v1.parse_canonical_json(value)
    except v1.PersistenceError as error:
        raise ObservationVersionError("CANONICAL_INVALID") from error


def _ascii(value: object) -> bytes:
    if type(value) is not str:
        _fail("CANONICAL_INVALID")
    try:
        return value.encode("ascii")
    except UnicodeError as error:
        raise ObservationVersionError("CANONICAL_INVALID") from error


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _signed(payload: dict[str, object], key: bytes) -> bytes:
    signature = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
    return _canonical({"payload": payload, "hmac_sha256": signature})


def _authenticated(value: bytes, key: bytes, fields: frozenset[str]) -> dict[str, object]:
    data = _parse(value)
    if type(data) is not dict or set(data) != {"payload", "hmac_sha256"}:
        _fail("ENVELOPE_INVALID")
    payload = data["payload"]
    if type(payload) is not dict or frozenset(payload) != fields:
        _fail("ENVELOPE_INVALID")
    actual = _hash(data["hmac_sha256"])
    expected = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(actual, expected):
        _fail("AUTHENTICATION_FAILED")
    if payload["domain"] != _DOMAIN or type(payload["version"]) is not int or payload["version"] != 2:
        _fail("VERSION_UNSUPPORTED")
    return payload


def _registry(value: object) -> dict[tuple[str, int], v1.CodecDescriptor]:
    try:
        return v1.normalize_codec_registry(value)
    except v1.PersistenceError as error:
        raise ObservationVersionError("CODEC_UNSUPPORTED") from error


def _registry_sha(registry: Mapping[tuple[str, int], v1.CodecDescriptor]) -> str:
    return _sha(_canonical([registry[key].to_canonical_dict() for key in sorted(registry)]))


def _observation(value: object, registry: Mapping) -> v1.InboxObservation:
    try:
        return v1.InboxObservation.from_canonical_bytes(value, tuple(registry.values()))
    except (v1.PersistenceError, ValueError, TypeError) as error:
        raise ObservationVersionError("OBSERVATION_INVALID") from error


@dataclass(frozen=True, slots=True)
class RevisionEvidence:
    """Local evidence references, NOT proof of economic validity or broker signature."""

    previous_observation_sha256: str
    observation_sha256: str
    request_sha256: str
    response_sha256: str
    binding_report_sha256: str
    recorded_at: str

    def to_dict(self) -> dict[str, str]:
        result = {name: getattr(self, name) for name in _EVIDENCE_KEYS}
        for name, value in result.items():
            _time(value) if name == "recorded_at" else _hash(value)
        return result

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if type(value) is not dict or frozenset(value) != _EVIDENCE_KEYS:
            _fail("EVIDENCE_INVALID")
        result = cls(**value)
        result.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class RevisionSnapshot:
    store_revision: int
    ledger_revision: int
    ledger_head_sha256: str
    revision_count: int
    version_head_sha256: str
    export_sha256: str
    financial_ready: bool = False


@dataclass(frozen=True, slots=True)
class RevisionAppendResult:
    record_sha256: str
    observation_sha256: str
    version_no: int
    replay: bool
    status: str = "REVIEW_REQUIRED"
    monetary_write_allowed: bool = False


@dataclass(frozen=True, slots=True)
class _Graph:
    snapshot: RevisionSnapshot
    base: bytes
    migration: dict[str, object]
    # source -> (latest observation, per-source version, version hash, recorded_at)
    tips: dict[str, tuple[v1.InboxObservation, int, str | None, str]]
    records: tuple[dict[str, object], ...]


def _validate_export(value: object, registry: Mapping, key: bytes, key_id: str,
                     account: str, expected_head: str | None = None) -> _Graph:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_EXPORT_BYTES:
        _fail("EXPORT_BOUNDS_INVALID")
    data = _parse(value)
    if type(data) is not dict or frozenset(data) != _EXPORT_KEYS:
        _fail("EXPORT_INVALID")
    if (data["domain"] != _DOMAIN or type(data["version"]) is not int
            or type(data["schema_version"]) is not int
            or data["version"] != 2 or data["schema_version"] != 2
            or data["financial_ready"] is not False):
        _fail("VERSION_UNSUPPORTED")
    base = _ascii(data["base_export_json_ascii"])
    migration_bytes = _ascii(data["migration_json_ascii"])
    migration = _authenticated(migration_bytes, key, _MIGRATION_KEYS)
    if (migration["mode"] != "FROZEN_BASE_REVISION_REVIEW_ONLY"
            or migration["account_scope_sha256"] != account
            or migration["identity_key_id"] != key_id
            or migration["base_export_sha256"] != _sha(base)
            or migration["codec_registry_sha256"] != _registry_sha(registry)):
        _fail("MIGRATION_BINDING_INVALID")
    created_at = _time(migration["created_at"])
    try:
        # The complete immutable v1 graph is verified, not a last-row projection.
        cl4._parse_ledger_export(base, target_account=account, identity_key=key)
    except (cl4.CL4Error, v1.PersistenceError) as error:
        raise ObservationVersionError("BASE_GRAPH_INVALID") from error
    original = _parse(base)
    tips: dict[str, tuple[v1.InboxObservation, int, str | None, str]] = {}
    for row in original["observations"]:
        item = _observation(_ascii(row["canonical_json_ascii"]), registry)
        if item.source.account_scope_sha256 != account:
            _fail("SOURCE_ACCOUNT_MISMATCH")
        if item.observed_at > created_at:
            _fail("TIMESTAMP_INVALID")
        tips[item.logical_source_sha256] = (item, 1, None, created_at)
    if not tips:
        _fail("SOURCE_ACCOUNT_UNBOUND")
    rows = data["version_records"]
    if type(rows) is not list or len(rows) > MAX_VERSIONS:
        _fail("VERSION_BOUNDS_EXCEEDED")
    head = _sha(migration_bytes)
    records: list[dict[str, object]] = []
    global_recorded_at = created_at
    for sequence, row in enumerate(rows, 1):
        if type(row) is not dict or set(row) != {"record_json_ascii", "sha256"}:
            _fail("VERSION_ROW_INVALID")
        raw = _ascii(row["record_json_ascii"])
        if row["sha256"] != _sha(raw):
            _fail("HASH_INVALID")
        record = _authenticated(raw, key, _RECORD_KEYS)
        if (record["change_kind"] != "SOURCE_CONTENT_REVISION"
                or record["status"] != "REVIEW_REQUIRED"
                or _uint(record["sequence"], maximum=MAX_VERSIONS) != sequence
                or record["global_parent_sha256"] != head):
            _fail("VERSION_CHAIN_INVALID")
        logical = _hash(record["logical_source_sha256"])
        if logical not in tips:
            _fail("SOURCE_UNBOUND")
        previous, version, parent_record, prior_time = tips[logical]
        current = _observation(_ascii(record["observation_json_ascii"]), registry)
        evidence = RevisionEvidence.from_dict(record["evidence"])
        if (record["previous_observation_sha256"] != previous.sha256
                or record["previous_version_sha256"] != parent_record
                or _uint(record["version_no"], maximum=MAX_VERSIONS + 1) != version + 1
                or current.sha256 != record["observation_sha256"]
                or current.logical_source_sha256 != logical
                or current.source.account_scope_sha256 != account
                or current.source.source_kind != "TBANK_OPERATION"
                or previous.source.source_kind != "TBANK_OPERATION"
                or current.descriptor.canonical_bytes != previous.descriptor.canonical_bytes
                or current.descriptor.canonical_bytes != TBANK_OPERATION_CODEC.canonical_bytes
                or current.content_json_ascii == previous.content_json_ascii
                or evidence.previous_observation_sha256 != previous.sha256
                or evidence.observation_sha256 != current.sha256
                or evidence.recorded_at <= prior_time
                or evidence.recorded_at < global_recorded_at
                or current.observed_at > evidence.recorded_at):
            _fail("VERSION_LINK_INVALID")
        # SourceIdentity is kept exactly as decoded by CL3. In particular it is
        # never replaced by provider-id + content-hash or by a generated alias.
        tips[logical] = (current, version + 1, row["sha256"], evidence.recorded_at)
        head = row["sha256"]
        global_recorded_at = evidence.recorded_at
        records.append(record)
    if (_uint(data["revision_count"], maximum=MAX_VERSIONS) != len(rows)
            or data["version_head_sha256"] != head):
        _fail("VERSION_CHAIN_INVALID")
    if expected_head is not None and _hash(expected_head) != head:
        _fail("PINNED_HEAD_MISMATCH")
    base_revision = _uint(original["store_revision"])
    if base_revision + len(rows) > v1.MAX_REVISION:
        _fail("REVISION_EXHAUSTED")
    return _Graph(RevisionSnapshot(
        base_revision + len(rows), _uint(original["ledger_revision"]),
        _hash(original["ledger_head_sha256"]), len(rows), head, _sha(value), False,
    ), base, migration, tips, tuple(records))


def validate_versioned_export(value: bytes, *, codec_registry: object,
                              identity_key: bytes, identity_key_id: str,
                              account_scope_sha256: str,
                              expected_version_head_sha256: str | None = None) -> RevisionSnapshot:
    """Verify the full v1 base and all v2 links; never return trading authority."""
    return _validate_export(value, _registry(codec_registry), _key(identity_key),
                            _key_id(identity_key_id), _hash(account_scope_sha256),
                            expected_version_head_sha256).snapshot


_NEW_SQL = (
    "CREATE TABLE cl2_revision_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "schema_version INTEGER NOT NULL CHECK(schema_version=2), migration BLOB NOT NULL, "
    "revision_count INTEGER NOT NULL CHECK(revision_count>=0), version_head_sha256 TEXT NOT NULL)",
    "CREATE TABLE cl2_observed_version (sequence INTEGER PRIMARY KEY CHECK(sequence>0), "
    "logical_source_sha256 TEXT NOT NULL, version_no INTEGER NOT NULL CHECK(version_no>=2), "
    "previous_observation_sha256 TEXT NOT NULL, observation_sha256 TEXT NOT NULL, "
    "sha256 TEXT NOT NULL UNIQUE, canonical BLOB NOT NULL, UNIQUE(logical_source_sha256,version_no))",
)
_BASE_TABLES = tuple(row[1] for row in v1._SCHEMA_STATEMENTS if row[0] == "table")
_TRIGGER_SQL = tuple(
    f"CREATE TRIGGER v2_frozen_{table}_{event.lower()} BEFORE {event} ON {table} "
    "BEGIN SELECT RAISE(ABORT,'V2_FROZEN_BASE'); END"
    for table in _BASE_TABLES for event in ("INSERT", "UPDATE", "DELETE")
) + tuple(
    f"CREATE TRIGGER v2_immutable_{table}_{event.lower()} BEFORE {event} ON {table} "
    "BEGIN SELECT RAISE(ABORT,'V2_IMMUTABLE_VERSION'); END"
    for table in ("cl2_observed_version", "cl2_revision_meta")
    for event in (("UPDATE", "DELETE") if table == "cl2_observed_version" else ("INSERT", "DELETE"))
)


def _schema_rows(connection: sqlite3.Connection) -> tuple[tuple, ...]:
    # Include even SQLite's autoindices; unexpected tables, indexes and triggers
    # must not be silently ignored.
    return tuple(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name"))


def _expected_schema() -> tuple[tuple, ...]:
    connection = sqlite3.connect(":memory:")
    try:
        for statement in tuple(row[3] for row in v1._SCHEMA_STATEMENTS) + _NEW_SQL + _TRIGGER_SQL:
            connection.execute(statement)
        return _schema_rows(connection)
    finally:
        connection.close()


_EXPECTED_SCHEMA = _expected_schema()


def _export(connection: sqlite3.Connection) -> bytes:
    if connection.execute("SELECT count(*) FROM cl2_observed_version").fetchone()[0] > MAX_VERSIONS:
        _fail("VERSION_BOUNDS_EXCEEDED")
    row = connection.execute("SELECT * FROM cl2_revision_meta WHERE singleton=1").fetchone()
    if row is None or row["schema_version"] != 2:
        _fail("SCHEMA_INVALID")
    records = [
        {"record_json_ascii": bytes(item["canonical"]).decode("ascii"), "sha256": item["sha256"]}
        for item in connection.execute("SELECT * FROM cl2_observed_version ORDER BY sequence")
    ]
    return _canonical({
        "domain": _DOMAIN, "version": 2, "schema_version": 2, "financial_ready": False,
        "base_export_json_ascii": v1._export_from_connection(connection).decode("ascii"),
        "migration_json_ascii": bytes(row["migration"]).decode("ascii"),
        "version_records": records, "revision_count": str(row["revision_count"]),
        "version_head_sha256": row["version_head_sha256"],
    })


def _validate_connection(connection: sqlite3.Connection, registry: Mapping,
                         key: bytes, key_id: str, account: str,
                         expected_head: str | None = None) -> _Graph:
    try:
        if (connection.execute("PRAGMA application_id").fetchone()[0] != SQLITE_APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != 2
                or connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
                or _schema_rows(connection) != _EXPECTED_SCHEMA):
            _fail("SCHEMA_INVALID")
        integrity = [tuple(row) for row in connection.execute("PRAGMA integrity_check")]
        if integrity != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
            _fail("INTEGRITY_FAILURE")
        v1._validate_semantics(connection, dict(registry))
        graph = _validate_export(_export(connection), registry, key, key_id, account, expected_head)
        rows = connection.execute("SELECT * FROM cl2_observed_version ORDER BY sequence").fetchall()
        for row, record in zip(rows, graph.records, strict=True):
            if (row["sequence"] != int(record["sequence"])
                    or row["version_no"] != int(record["version_no"])
                    or row["logical_source_sha256"] != record["logical_source_sha256"]
                    or row["previous_observation_sha256"] != record["previous_observation_sha256"]
                    or row["observation_sha256"] != record["observation_sha256"]):
                _fail("ROW_BINDING_INVALID")
        return graph
    except (sqlite3.Error, v1.PersistenceError, UnicodeError, ValueError, TypeError) as error:
        raise ObservationVersionError("INTEGRITY_FAILURE") from error


def _fault(injector: Callable[[str], None] | None, point: str) -> None:
    if injector is not None:
        injector(point)


def _read_connection(database: Path) -> sqlite3.Connection:
    # Do not use immutable=1: a live WAL may contain committed data.
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _copy(source_root: object, target_root: object, *, registry: Mapping, key: bytes,
          key_id: str, account: str, expected_export: str, created_at: str | None,
          fault_injector: Callable[[str], None] | None) -> Path:
    source = v1._path_from(source_root)
    target = v1._path_from(target_root)
    staging = target.with_name(target.name + ".revision-staging")
    v1._validate_target_parent(target)
    v1._reject_overlapping_roots(source, target, staging)
    v1._require_absent_target(target, staging)
    database = v1._validate_live_root(source)
    _hash(expected_export)
    connection = _read_connection(database)
    destination = None
    owned_staging = False
    promoted = False
    try:
        connection.execute("BEGIN")
        if created_at is not None:
            v1._validate_connection(connection, dict(registry))
            source_export = v1._export_from_connection(connection)
        else:
            _validate_connection(connection, registry, key, key_id, account)
            source_export = _export(connection)
        if _sha(source_export) != expected_export:
            _fail("SOURCE_EXPORT_MISMATCH")
        _fault(fault_injector, "copy.before_copy")
        staging.mkdir(mode=0o700)
        owned_staging = True
        destination = v1._connect(staging / "store.sqlite3", 0)
        connection.backup(destination)
        _fault(fault_injector, "copy.after_copy")
        if created_at is not None:
            v1._validate_connection(destination, dict(registry))
            if v1._export_from_connection(destination) != source_export:
                _fail("COPY_MISMATCH")
            migration = _signed({
                "domain": _DOMAIN, "version": 2, "mode": "FROZEN_BASE_REVISION_REVIEW_ONLY",
                "account_scope_sha256": account, "identity_key_id": key_id,
                "base_export_sha256": expected_export, "codec_registry_sha256": _registry_sha(registry),
                "created_at": _time(created_at),
            }, key)
            destination.execute("BEGIN IMMEDIATE")
            for statement in _NEW_SQL:
                destination.execute(statement)
            destination.execute("INSERT INTO cl2_revision_meta VALUES (1,2,?,0,?)",
                                (migration, _sha(migration)))
            for statement in _TRIGGER_SQL:
                destination.execute(statement)
            destination.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
            destination.execute("PRAGMA user_version=2")
            _validate_connection(destination, registry, key, key_id, account)
            _fault(fault_injector, "copy.after_schema")
            destination.execute("COMMIT")
        else:
            _validate_connection(destination, registry, key, key_id, account)
            if _export(destination) != source_export:
                _fail("COPY_MISMATCH")
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        destination.close()
        destination = None
        # The source is a pinned read snapshot, not an in-place runtime cutover.
        connection.execute("ROLLBACK")
        _fault(fault_injector, "copy.before_promote")
        v1._promote_staging(staging, target)
        promoted = True
        _fault(fault_injector, "copy.after_promote")
        return target
    finally:
        if destination is not None:
            if destination.in_transaction:
                destination.execute("ROLLBACK")
            destination.close()
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()
        if owned_staging and not promoted and staging.exists():
            shutil.rmtree(staging)


def copy_v1_for_revision_review(source_root: object, target_root: object, *,
                                codec_registry: object, identity_key: bytes, identity_key_id: str,
                                account_scope_sha256: str, expected_source_export_sha256: str,
                                created_at: str, fault_injector: Callable[[str], None] | None = None) -> Path:
    """Copy committed v1 state to a NEW v2 target; source/runtime are not migrated in place."""
    return _copy(source_root, target_root, registry=_registry(codec_registry), key=_key(identity_key),
                 key_id=_key_id(identity_key_id), account=_hash(account_scope_sha256),
                 expected_export=expected_source_export_sha256, created_at=_time(created_at),
                 fault_injector=fault_injector)


def copy_versioned_snapshot(source_root: object, target_root: object, *,
                            codec_registry: object, identity_key: bytes, identity_key_id: str,
                            account_scope_sha256: str, expected_export_sha256: str,
                            fault_injector: Callable[[str], None] | None = None) -> Path:
    """Validated backup/restore to an absent target, including WAL and all version relations."""
    return _copy(source_root, target_root, registry=_registry(codec_registry), key=_key(identity_key),
                 key_id=_key_id(identity_key_id), account=_hash(account_scope_sha256),
                 expected_export=expected_export_sha256, created_at=None, fault_injector=fault_injector)


class ObservationVersionStore:
    """Append-only observed-version review store, not a CashLedgerStore subtype."""

    def __init__(self, root: Path, connection: sqlite3.Connection, registry: Mapping,
                 key: bytes, key_id: str, account: str, injector: Callable[[str], None] | None):
        self.root, self._connection, self._registry = root, connection, registry
        self._key, self._key_id, self._account, self._injector = key, key_id, account, injector
        self._closed = False

    @classmethod
    def open(cls, root: object, *, codec_registry: object, identity_key: bytes,
             identity_key_id: str, account_scope_sha256: str,
             expected_version_head_sha256: str | None = None,
             fault_injector: Callable[[str], None] | None = None) -> Self:
        path = v1._path_from(root)
        database = v1._validate_live_root(path)
        registry, key = _registry(codec_registry), _key(identity_key)
        key_id, account = _key_id(identity_key_id), _hash(account_scope_sha256)
        # Reject foreign/v1 headers before opening a write-capable connection.
        ro = _read_connection(database)
        try:
            ro.execute("BEGIN")
            _validate_connection(ro, registry, key, key_id, account, expected_version_head_sha256)
        finally:
            ro.close()
        connection = v1._connect(database, 0)
        try:
            connection.execute("BEGIN")
            _validate_connection(connection, registry, key, key_id, account, expected_version_head_sha256)
            connection.execute("COMMIT")
            return cls(path, connection, registry, key, key_id, account, fault_injector)
        except BaseException:
            connection.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            self._connection.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            _fail("STORE_CLOSED")
        v1._validate_live_root(self.root)

    def _graph(self) -> _Graph:
        return _validate_connection(self._connection, self._registry, self._key,
                                    self._key_id, self._account)

    def export_bytes(self) -> bytes:
        self._ensure_open()
        self._connection.execute("BEGIN")
        try:
            self._graph()
            return _export(self._connection)
        finally:
            self._connection.execute("ROLLBACK")

    def snapshot(self) -> RevisionSnapshot:
        return validate_versioned_export(self.export_bytes(), codec_registry=tuple(self._registry.values()),
                                         identity_key=self._key, identity_key_id=self._key_id,
                                         account_scope_sha256=self._account)

    def append_observation(self, observation: v1.InboxObservation, *, expected_store_revision: int) -> str:
        """Generic ingestion cannot acquire version privileges through this API."""
        if type(observation) is not v1.InboxObservation:
            _fail("OBSERVATION_INVALID")
        base = _parse(_parse(self.export_bytes())["base_export_json_ascii"])
        for row in base["observations"]:
            if row["logical_source_sha256"] == observation.logical_source_sha256:
                if row["canonical_json_ascii"].encode("ascii") == observation.canonical_bytes:
                    return "OBSERVATION_ALREADY_PRESENT"
                raise v1.PersistenceError(v1.PersistenceReason.SOURCE_CONTENT_CONFLICT)
        _fail("BASE_FROZEN_NEW_SOURCE_UNSUPPORTED")

    def append_observed_version(self, observation: v1.InboxObservation, *,
                                expected_previous_observation_sha256: str,
                                expected_store_revision: int, expected_version_head_sha256: str,
                                evidence: RevisionEvidence) -> RevisionAppendResult:
        self._ensure_open()
        if type(observation) is not v1.InboxObservation or type(evidence) is not RevisionEvidence:
            _fail("TYPE_INVALID")
        if type(expected_store_revision) is not int or not 0 <= expected_store_revision <= v1.MAX_REVISION:
            _fail("REVISION_INVALID")
        previous_sha, expected_head = _hash(expected_previous_observation_sha256), _hash(expected_version_head_sha256)
        checked = _observation(observation.canonical_bytes, self._registry)
        evidence_dict = evidence.to_dict()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise ObservationVersionError("STORE_BUSY") from error
        try:
            graph = self._graph()
            # A content hash can reappear (A -> B -> A). Version identity is the
            # predecessor + recorded observation envelope, not content alone.
            matching_observation = False
            for record in graph.records:
                if record["observation_sha256"] != checked.sha256:
                    continue
                matching_observation = True
                if (record["previous_observation_sha256"] == previous_sha
                        and record["evidence"] == evidence_dict
                        and record["observation_json_ascii"] == checked.canonical_bytes.decode("ascii")
                        and record["global_parent_sha256"] == expected_head
                        and expected_store_revision == _uint(_parse(graph.base)["store_revision"]) + int(record["sequence"]) - 1):
                    result = RevisionAppendResult(_sha(_signed(record, self._key)), checked.sha256,
                                                  int(record["version_no"]), True)
                    self._connection.execute("ROLLBACK")
                    return result
            if matching_observation:
                tip = graph.tips.get(checked.logical_source_sha256)
                if (graph.snapshot.store_revision != expected_store_revision
                        or graph.snapshot.version_head_sha256 != expected_head
                        or tip is None or tip[0].sha256 != previous_sha):
                    _fail("REPLAY_BINDING_CONFLICT")
            if (graph.snapshot.store_revision != expected_store_revision
                    or graph.snapshot.version_head_sha256 != expected_head):
                _fail("CAS_CONFLICT")
            if checked.logical_source_sha256 not in graph.tips:
                _fail("SOURCE_UNBOUND")
            previous, number, parent, _ = graph.tips[checked.logical_source_sha256]
            if previous.sha256 != previous_sha:
                _fail("PREDECESSOR_MISMATCH")
            if graph.snapshot.revision_count >= MAX_VERSIONS:
                _fail("VERSION_BOUNDS_EXCEEDED")
            sequence = graph.snapshot.revision_count + 1
            payload = {
                "domain": _DOMAIN, "version": 2, "change_kind": "SOURCE_CONTENT_REVISION",
                "status": "REVIEW_REQUIRED", "sequence": str(sequence), "version_no": str(number + 1),
                "logical_source_sha256": checked.logical_source_sha256,
                "previous_observation_sha256": previous.sha256, "previous_version_sha256": parent,
                "global_parent_sha256": expected_head,
                "observation_json_ascii": checked.canonical_bytes.decode("ascii"),
                "observation_sha256": checked.sha256, "evidence": evidence_dict,
            }
            raw, record_hash = _signed(payload, self._key), _sha(_signed(payload, self._key))
            # Run all pure graph validation BEFORE any row is inserted.
            prospective = _parse(_export(self._connection))
            prospective["version_records"].append({"record_json_ascii": raw.decode("ascii"), "sha256": record_hash})
            prospective.update(revision_count=str(sequence), version_head_sha256=record_hash)
            _validate_export(_canonical(prospective), self._registry, self._key, self._key_id, self._account)
            _fault(self._injector, "version.before_insert")
            self._connection.execute("INSERT INTO cl2_observed_version VALUES (?,?,?,?,?,?,?)", (
                sequence, checked.logical_source_sha256, number + 1, previous.sha256,
                checked.sha256, record_hash, raw,
            ))
            _fault(self._injector, "version.after_insert")
            changed = self._connection.execute(
                "UPDATE cl2_revision_meta SET revision_count=?,version_head_sha256=? "
                "WHERE singleton=1 AND revision_count=? AND version_head_sha256=?",
                (sequence, record_hash, sequence - 1, expected_head),
            ).rowcount
            if changed != 1:
                _fail("CAS_CONFLICT")
            _fault(self._injector, "version.after_meta")
            self._graph()
            _fault(self._injector, "version.before_commit")
            self._connection.execute("COMMIT")
            _fault(self._injector, "version.after_commit")
            return RevisionAppendResult(record_hash, checked.sha256, number + 1, False)
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def append_transaction(self, *_: object, **__: object) -> None:
        _fail("MONETARY_CONSUMER_NOT_QUALIFIED")

    def append_correction_bundle(self, *_: object, **__: object) -> None:
        _fail("MONETARY_CONSUMER_NOT_QUALIFIED")

    def append_status_event(self, *_: object, **__: object) -> None:
        _fail("REVISION_REVIEW_REQUIRED")
