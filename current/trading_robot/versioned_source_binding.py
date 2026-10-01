"""Durable v4 source/pin registry and exact commit-to-pin recovery.

A separate append-only SQLite registry records a checked prospective batch
BEFORE the source INSERT. Recovery accepts only its exact before or after
export, never learns pins from an arbitrary current database. This registry
is a cutover prerequisite, NOT trading authority or a multi-store transaction.
Registry exports include private financial history; HMAC is not encryption.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from . import cash_ledger_persistence as cl2
from . import cash_observation_versions as versions
from . import versioned_operational_store as v4
from .broker_read_adapters import BrokerReadRequest
from .locking import InterProcessFileLock, LockUnavailableError
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha
from .versioned_runtime_adapter import VersionedRuntimeStoreAdapter

DOMAIN = "V4_DURABLE_SOURCE_BINDING_V1"
APPLICATION_ID = 0x43504201
MAX_EVENTS = 256
MAX_REGISTRY_BYTES = 12 * 1024 * 1024
_SQL = (
    "CREATE TABLE binding_root (singleton INTEGER PRIMARY KEY CHECK(singleton=1), canonical BLOB NOT NULL)",
    "CREATE TABLE binding_event (sequence INTEGER PRIMARY KEY CHECK(sequence>0), canonical BLOB NOT NULL, sha256 TEXT NOT NULL UNIQUE)",
    "CREATE TRIGGER root_no_update BEFORE UPDATE ON binding_root BEGIN SELECT RAISE(ABORT,'IMMUTABLE_ROOT'); END",
    "CREATE TRIGGER root_no_delete BEFORE DELETE ON binding_root BEGIN SELECT RAISE(ABORT,'IMMUTABLE_ROOT'); END",
    "CREATE TRIGGER event_no_update BEFORE UPDATE ON binding_event BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVENT'); END",
    "CREATE TRIGGER event_no_delete BEFORE DELETE ON binding_event BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVENT'); END",
)
_ROOT_FIELDS = {"domain", "version", "mode", "runtime_scope_sha256", "owner_binding_sha256",
                "source", "registry_root", "initial_export_ascii", "initial_pins", "created_at"}
_EVENT_FIELDS = {"domain", "version", "sequence", "parent_sha256", "kind", "operation_id",
                 "prepared_sha256", "batch", "before_pins", "after_pins"}


class SourceBindingError(RuntimeError):
    """Finite error; a PREPARED binding may require explicit recovery."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SourceBindingError("SOURCE_BINDING_" + reason)


def _schema(conn: sqlite3.Connection) -> tuple:
    return tuple(tuple(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name"))


def _expected_schema() -> tuple:
    with sqlite3.connect(":memory:") as conn:
        for sql in _SQL:
            conn.execute(sql)
        return _schema(conn)


_SCHEMA = _expected_schema()


def _check(conn: sqlite3.Connection) -> None:
    _require(conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
             and conn.execute("PRAGMA user_version").fetchone()[0] == 1
             and _schema(conn) == _SCHEMA, "SCHEMA_INVALID")
    _require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "INTEGRITY_INVALID")


def _unseal(raw: bytes, key: bytes, fields: set[str]) -> dict[str, Any]:
    doc = _parse(raw)
    _require(type(doc) is dict and set(doc) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = doc["payload"]
    _require(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
             and type(body["version"]) is int and body["version"] == 1, "ENVELOPE_INVALID")
    expected = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
    _require(type(doc["hmac_sha256"]) is str
             and hmac.compare_digest(doc["hmac_sha256"], expected)
             and _canonical(doc) == raw, "AUTHENTICATION_FAILED")
    return body


def _source_identity(store: v4.VersionedOperationalStore) -> dict[str, Any]:
    _require(type(store) is v4.VersionedOperationalStore and not store._closed, "SOURCE_INVALID")
    db = cl2._validate_live_root(store.root)
    st = db.stat()
    return {"root": str(store.root.resolve(strict=True)), "device": str(st.st_dev), "inode": str(st.st_ino),
            "account_scope_sha256": store._account, "identity_key_id": store._key_id,
            "codec_registry_sha256": versions._registry_sha(store._registry),
            "schema_version": 4}


@dataclass(frozen=True, slots=True)
class BindingCheckpoint:
    """An independent minimum accepted registry position, not inferred from source."""
    root_sha256: str
    sequence: int
    event_head_sha256: str

    def __post_init__(self) -> None:
        versions._hash(self.root_sha256)
        versions._hash(self.event_head_sha256)
        _require(type(self.sequence) is int and 0 <= self.sequence <= MAX_EVENTS, "CHECKPOINT_INVALID")


@dataclass(frozen=True, slots=True)
class BindingSnapshot:
    checkpoint: BindingCheckpoint
    pins: v4.OperationalPins
    pending_plan_sha256: str | None
    runtime_scope_sha256: str
    owner_binding_sha256: str

    def public_summary(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_SUMMARY", "sequence": self.checkpoint.sequence,
                "binding_root_sha256": self.checkpoint.root_sha256,
                "event_head_sha256": self.checkpoint.event_head_sha256,
                "pending": self.pending_plan_sha256 is not None,
                "status": "SYNC_RECOVERY_REQUIRED" if self.pending_plan_sha256 else "PINNED_CUTOVER_REQUIRED",
                "runtime_cutover_performed": False, "runtime_authority_granted": False}


@dataclass
class _State:
    root: dict[str, Any]
    root_raw: bytes
    raw: bytes
    pins: v4.OperationalPins
    events: list[bytes]
    pending: dict[str, Any] | None = None
    after_raw: bytes | None = None

    @property
    def checkpoint(self) -> BindingCheckpoint:
        root = _sha(self.root_raw)
        return BindingCheckpoint(root, len(self.events), _sha(self.events[-1]) if self.events else root)

    def snapshot(self) -> BindingSnapshot:
        return BindingSnapshot(self.checkpoint, self.pins,
            _sha(self.events[-1]) if self.pending else None,
            self.root["runtime_scope_sha256"], self.root["owner_binding_sha256"])


class DurableSourceBinding:
    """One explicit physical source per registry; no path fallback or auto-cutover.

    The caller supplies independent initial pins, runtime/owner identity hashes
    and a minimum trusted BindingCheckpoint on reopen. Those hashes are identity
    anchors, NOT live locks or verification of Portfolio/Risk/Central. A full
    rollback before the supplied checkpoint is rejected; rolling back all local
    data AND the external checkpoint cannot be detected here.
    """

    def __init__(self, root: Path, source: v4.VersionedOperationalStore, *,
                 checkpoint: BindingCheckpoint, runtime_scope_sha256: str,
                 owner_binding_sha256: str, fault_injector: Callable[[str], None] | None = None):
        _require(type(checkpoint) is BindingCheckpoint, "CHECKPOINT_INVALID")
        self.root, self.source = root, source
        self._minimum = checkpoint
        self._runtime = versions._hash(runtime_scope_sha256)
        self._owners = versions._hash(owner_binding_sha256)
        self._fault, self._busy = fault_injector, False

    def _point(self, name: str) -> None:
        if self._fault is not None:
            self._fault(name)

    def _validate_source(self, raw: bytes, pins: v4.OperationalPins | None = None) -> v4.OperationalSnapshot:
        return v4._validate(raw, self.source._registry, self.source._key,
                            self.source._key_id, self.source._account, pins).snapshot()

    @classmethod
    def create(cls, root: object, *, source: v4.VersionedOperationalStore,
               expected_pins: v4.OperationalPins, runtime_scope_sha256: str,
               owner_binding_sha256: str, created_at: str,
               fault_injector: Callable[[str], None] | None = None) -> Self:
        _require(type(source) is v4.VersionedOperationalStore
                 and type(expected_pins) is v4.OperationalPins, "IDENTITY_INVALID")
        path = cl2._path_from(root)
        cl2._reject_overlapping_roots(source.root, path)
        cl2._validate_target_parent(path)
        staging = path.with_name(path.name + ".binding-staging")
        cl2._require_absent_target(path, staging)
        versions._hash(runtime_scope_sha256); versions._hash(owner_binding_sha256)
        versions._time(created_at)
        conn = None
        owned, promoted = False, False
        try:
            with source.locked_snapshot(expected_pins=expected_pins) as view:
                body = {"domain": DOMAIN, "version": 1, "mode": "PIN_REGISTRY_CUTOVER_REQUIRED",
                        "runtime_scope_sha256": runtime_scope_sha256, "owner_binding_sha256": owner_binding_sha256,
                        "source": _source_identity(source), "registry_root": str(path.resolve()),
                        "initial_export_ascii": view.export_bytes().decode("ascii"),
                        "initial_pins": expected_pins.to_dict(), "created_at": created_at}
                raw = _sealed(body, source._key)
                staging.mkdir(mode=0o700); owned = True
                conn = cl2._connect(staging / "store.sqlite3", 0)
                conn.execute("PRAGMA journal_mode=WAL"); conn.execute("BEGIN IMMEDIATE")
                for sql in _SQL:
                    conn.execute(sql)
                conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                conn.execute("PRAGMA user_version=1")
                conn.execute("INSERT INTO binding_root VALUES(1,?)", (raw,))
                _check(conn)
                if fault_injector:
                    fault_injector("create.before_commit")
                conn.execute("COMMIT")
                if fault_injector:
                    fault_injector("create.after_commit")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close(); conn = None
                view.assert_active()
                if fault_injector:
                    fault_injector("create.before_promote")
                cl2._promote_staging(staging, path); promoted = True
                if fault_injector:
                    fault_injector("create.after_promote")
            checkpoint = BindingCheckpoint(_sha(raw), 0, _sha(raw))
            return cls.open(path, source=source, checkpoint=checkpoint,
                runtime_scope_sha256=runtime_scope_sha256, owner_binding_sha256=owner_binding_sha256,
                fault_injector=fault_injector)
        finally:
            if conn is not None:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                conn.close()
            if owned and not promoted and staging.exists():
                shutil.rmtree(staging)

    @classmethod
    def open(cls, root: object, *, source: v4.VersionedOperationalStore,
             checkpoint: BindingCheckpoint, runtime_scope_sha256: str,
             owner_binding_sha256: str,
             fault_injector: Callable[[str], None] | None = None) -> Self:
        _require(type(source) is v4.VersionedOperationalStore and type(checkpoint) is BindingCheckpoint,
                 "IDENTITY_INVALID")
        path = cl2._path_from(root)
        cl2._reject_overlapping_roots(source.root, path)
        result = cls(path, source, checkpoint=checkpoint, runtime_scope_sha256=runtime_scope_sha256,
                     owner_binding_sha256=owner_binding_sha256, fault_injector=fault_injector)
        result.snapshot()  # Do not resolve/adopt a source transition on open.
        return result

    @contextmanager
    def _guard(self) -> Iterator[None]:
        _require(not self._busy, "REENTRANT_ACCESS")
        cl2._validate_live_root(self.root)
        lock_path = self.root.with_name(self.root.name + ".binding.lock")
        cl2._validate_existing_components(lock_path)
        self._busy = True
        try:
            with InterProcessFileLock(lock_path, timeout_seconds=0):
                yield
        except LockUnavailableError:
            raise SourceBindingError("SOURCE_BINDING_LOCKED") from None
        finally:
            self._busy = False

    def _load(self, conn: sqlite3.Connection | None = None) -> _State:
        owned = conn is None
        if owned:
            db = cl2._validate_live_root(self.root)
            conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=0)
            conn.execute("BEGIN")
        assert conn is not None
        try:
            _check(conn)
            row = conn.execute("SELECT canonical FROM binding_root WHERE singleton=1").fetchone()
            _require(row is not None, "ROOT_MISSING")
            raw = bytes(row[0])
            _require(0 < len(raw) <= MAX_REGISTRY_BYTES, "CAPACITY_EXCEEDED")
            root = _unseal(raw, self.source._key, _ROOT_FIELDS)
            _require(_sha(raw) == self._minimum.root_sha256, "ROOT_CHANGED")
            _require(root["mode"] == "PIN_REGISTRY_CUTOVER_REQUIRED"
                     and root["runtime_scope_sha256"] == self._runtime
                     and root["owner_binding_sha256"] == self._owners
                     and root["registry_root"] == str(self.root.resolve())
                     and root["source"] == _source_identity(self.source), "IDENTITY_CHANGED")
            versions._time(root["created_at"])
            base = root["initial_export_ascii"].encode("ascii")
            pins = v4.OperationalPins(**root["initial_pins"])
            self._validate_source(base, pins)
            state = _State(root, raw, base, pins, [])
            total = len(raw); operations: set[str] = set()
            for sequence, value, digest in conn.execute(
                    "SELECT sequence,canonical,sha256 FROM binding_event ORDER BY sequence"):
                value = bytes(value); total += len(value)
                _require(total <= MAX_REGISTRY_BYTES and sequence <= MAX_EVENTS, "CAPACITY_EXCEEDED")
                body = _unseal(value, self.source._key, _EVENT_FIELDS)
                _require(type(body["sequence"]) is int and sequence == body["sequence"] == len(state.events) + 1
                         and body["parent_sha256"] == state.checkpoint.event_head_sha256
                         and digest == _sha(value), "EVENT_CHAIN_INVALID")
                versions._hash(body["operation_id"])
                before = v4.OperationalPins(**body["before_pins"])
                after = v4.OperationalPins(**body["after_pins"])
                _require(before == state.pins, "BEFORE_PINS_INVALID")
                if body["kind"] == "PREPARED":
                    _require(state.pending is None and body["prepared_sha256"] is None
                             and body["operation_id"] not in operations, "PREPARE_CONFLICT")
                    operations.add(body["operation_id"])
                    data = _parse(state.raw)
                    data["batches"].append(body["batch"])
                    planned = _canonical(data)
                    self._validate_source(planned, after)
                    _require(after.store_revision == before.store_revision + 1
                             and after.seed_export_sha256 == before.seed_export_sha256,
                             "PREPARED_TRANSITION_INVALID")
                    state.pending, state.after_raw = body, planned
                else:
                    _require(body["kind"] in {"COMMITTED", "ABORTED"} and state.pending is not None
                             and body["batch"] is None and body["operation_id"] == state.pending["operation_id"]
                             and body["prepared_sha256"] == _sha(state.events[-1])
                             and body["after_pins"] == state.pending["after_pins"], "RESOLUTION_INVALID")
                    if body["kind"] == "COMMITTED":
                        assert state.after_raw is not None
                        state.raw, state.pins = state.after_raw, after
                    state.pending, state.after_raw = None, None
                state.events.append(value)
            checkpoint = state.checkpoint
            _require(checkpoint.sequence >= self._minimum.sequence, "CHECKPOINT_ROLLBACK")
            pinned = _sha(state.events[self._minimum.sequence - 1]) if self._minimum.sequence else _sha(raw)
            _require(pinned == self._minimum.event_head_sha256, "CHECKPOINT_FORK")
            return state
        except SourceBindingError:
            raise
        except Exception:
            raise SourceBindingError("SOURCE_BINDING_INVALID") from None
        finally:
            if owned:
                conn.close()

    def _same_rows(self, conn: sqlite3.Connection, state: _State) -> None:
        """Recheck exact already-validated bytes under the registry transaction.

        Full semantic validation took place in _load before source locking.
        Equality of every immutable row is the CAS; do not repeat the complete
        nested financial graph while holding the short source writer lease.
        """
        _check(conn)
        _require(state.root["source"] == _source_identity(self.source), "IDENTITY_CHANGED")
        root = conn.execute("SELECT canonical FROM binding_root WHERE singleton=1").fetchone()
        _require(root is not None and bytes(root[0]) == state.root_raw, "REGISTRY_CAS_CONFLICT")
        rows = conn.execute("SELECT sequence,canonical,sha256 FROM binding_event ORDER BY sequence").fetchall()
        _require(len(rows) == len(state.events), "REGISTRY_CAS_CONFLICT")
        for number, (sequence, raw, digest) in enumerate(rows, 1):
            _require(sequence == number and bytes(raw) == state.events[number - 1]
                     and digest == _sha(state.events[number - 1]), "REGISTRY_CAS_CONFLICT")

    def _append(self, state: _State, body: dict[str, Any], *,
                prepared_export: bytes | None = None) -> _State:
        # Inputs are private products of a fully validated state and _event.
        # PREPARED also gets the exact export verified by the source writer and
        # the callback. Resolution uses the authenticated plan's prior result.
        record = _sealed(body, self.source._key)
        _unseal(record, self.source._key, _EVENT_FIELDS)
        _require(body["sequence"] == len(state.events) + 1
                 and body["parent_sha256"] == state.checkpoint.event_head_sha256
                 and body["before_pins"] == state.pins.to_dict(), "EVENT_BINDING_INVALID")
        _require(len(state.events) < MAX_EVENTS
                 and len(state.root_raw) + sum(map(len, state.events)) + len(record) <= MAX_REGISTRY_BYTES,
                 "CAPACITY_EXCEEDED")
        if body["kind"] == "PREPARED":
            _require(state.pending is None and prepared_export is not None
                     and body["prepared_sha256"] is None, "PREPARE_CONFLICT")
            data = _parse(state.raw)
            data["batches"].append(body["batch"])
            _require(_canonical(data) == prepared_export
                     and _sha(prepared_export) == body["after_pins"]["export_sha256"],
                     "PREPARED_EXPORT_CONFLICT")
            after = _State(state.root, state.root_raw, state.raw, state.pins,
                           [*state.events, record], body, prepared_export)
        else:
            _require(body["kind"] in {"COMMITTED", "ABORTED"} and state.pending is not None
                     and body["prepared_sha256"] == _sha(state.events[-1]) and body["batch"] is None
                     and body["operation_id"] == state.pending["operation_id"]
                     and body["after_pins"] == state.pending["after_pins"], "RESOLUTION_INVALID")
            if body["kind"] == "COMMITTED":
                assert state.after_raw is not None
                raw, pins = state.after_raw, v4.OperationalPins(**body["after_pins"])
            else:
                raw, pins = state.raw, state.pins
            after = _State(state.root, state.root_raw, raw, pins, [*state.events, record])
        db = cl2._validate_live_root(self.root)
        conn = cl2._connect(db, 0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._same_rows(conn, state)
            self._point("registry.before_insert")
            conn.execute("INSERT INTO binding_event VALUES(?,?,?)",
                         (body["sequence"], record, _sha(record)))
            self._point("registry.after_insert")
            self._same_rows(conn, after)
            self._point("registry.before_commit")
            conn.execute("COMMIT")
            self._point("registry.after_commit")
            return after
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    def snapshot(self) -> BindingSnapshot:
        """Read the registry, including pending, without accepting current source pins."""
        with self._guard():
            return self._load().snapshot()

    def export_bytes(self) -> bytes:
        """Private audit export. Not a restore/cutover or broker-authenticated proof."""
        with self._guard():
            state = self._load()
            return _canonical({"root_ascii": state.root_raw.decode("ascii"),
                               "events_ascii": [r.decode("ascii") for r in state.events]})

    @contextmanager
    def locked_binding(self) -> Iterator[tuple[VersionedRuntimeStoreAdapter, v4.LockedOperationalView]]:
        """Only registry-verified, nonpending pins may issue a locked request view."""
        with self._guard():
            state = self._load()
            _require(state.pending is None, "PENDING_RECOVERY_REQUIRED")
            adapter = VersionedRuntimeStoreAdapter(self.source, state.pins)
            with adapter.locked_snapshot() as view:
                _require(view.export_bytes() == state.raw, "SOURCE_DIVERGED")
                yield adapter, view
                db = cl2._validate_live_root(self.root)
                conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
                try:
                    conn.execute("BEGIN")
                    self._same_rows(conn, state)
                finally:
                    conn.close()

    def _event(self, state: _State, *, kind: str, operation_id: str,
               after: v4.OperationalPins, batch: Any = None) -> dict[str, Any]:
        return {"domain": DOMAIN, "version": 1, "sequence": len(state.events) + 1,
                "parent_sha256": state.checkpoint.event_head_sha256, "kind": kind,
                "operation_id": operation_id, "prepared_sha256": None if kind == "PREPARED" else _sha(state.events[-1]),
                "batch": batch, "before_pins": state.pins.to_dict(), "after_pins": after.to_dict()}

    def _resolve(self, state: _State, *, allow_abort: bool) -> tuple[str, _State]:
        if state.pending is None:
            with self.source.locked_snapshot(expected_pins=state.pins) as view:
                _require(view.export_bytes() == state.raw, "SOURCE_DIVERGED")
            return "NO_PENDING", state
        # Only these two exports were independently saved BEFORE source INSERT.
        # An arbitrary valid current snapshot is never a candidate for adoption.
        current = self.source.export_bytes()
        planned_pins = v4.OperationalPins(**state.pending["after_pins"])
        if current == state.after_raw:
            kind, pins = "COMMITTED", planned_pins
        else:
            _require(allow_abort and current == state.raw, "SOURCE_DIVERGED")
            kind, pins = "ABORTED", state.pins
        with self.source.locked_snapshot(expected_pins=pins) as view:
            _require(view.export_bytes() == current, "SOURCE_CHANGED")
            self._point("resolution.before_record")
            body = self._event(state, kind=kind, operation_id=state.pending["operation_id"], after=planned_pins)
            after = self._append(state, body)
            self._point("resolution.after_record")
        return kind, after

    def recover_pending(self, *, expected_checkpoint: BindingCheckpoint) -> tuple[str, BindingSnapshot]:
        """Offline resolution only; no source write, provider call, arm or retry.

        Exact preimage means source did not commit: abort this pin plan and keep
        pins. A future sync needs a new collection. Exact planned postimage means
        advance pins without repeating any monetary write. Any other state blocks.
        """
        _require(type(expected_checkpoint) is BindingCheckpoint, "CHECKPOINT_INVALID")
        with self._guard():
            state = self._load()
            _require(state.checkpoint == expected_checkpoint, "REGISTRY_CAS_CONFLICT")
            result, after = self._resolve(state, allow_abort=True)
            return result, after.snapshot()

    def sync_tbank_operations(self, request: BrokerReadRequest, *,
                             expected_checkpoint: BindingCheckpoint, recorded_at: str,
                             read_rub_positions: Callable[[str], Any],
                             prepare_selected: Callable[[bytes, bytes, bytes], None] | None = None,
                             bound_full_fill: dict | None = None
                             ) -> tuple[v4.OperationalSyncResult, BindingSnapshot]:
        _require(type(expected_checkpoint) is BindingCheckpoint, "CHECKPOINT_INVALID")
        _require(prepare_selected is None or callable(prepare_selected), "OBSERVER_INVALID")
        with self._guard():
            state = self._load()
            _require(state.checkpoint == expected_checkpoint, "REGISTRY_CAS_CONFLICT")
            _require(state.pending is None, "PENDING_RECOVERY_REQUIRED")
            # Leave space for PREPARED plus resolution, even at capacity.
            _require(len(state.events) <= MAX_EVENTS - 2, "CAPACITY_EXCEEDED")
            self._validate_source(self.source.export_bytes(), state.pins)
            planned: list[_State] = []

            def prepare(before: bytes, after: bytes) -> None:
                _require(not planned and before == state.raw, "PREPARED_SOURCE_CONFLICT")
                next_snapshot = self._validate_source(after)
                row = _parse(after)["batches"][-1]
                event = self._event(state, kind="PREPARED", operation_id=secrets.token_hex(32),
                                    after=next_snapshot.pins, batch=row)
                if prepare_selected is not None:
                    # Publish the exact selected-authority intent before either
                    # registry PREPARED or source INSERT. Bytes cannot mutate event.
                    prepare_selected(before, after, _sealed(event, self.source._key))
                prepared = self._append(state, event, prepared_export=after)
                _require(prepared.after_raw == after, "PREPARED_EXPORT_CONFLICT")
                planned.append(prepared)
                self._point("sync.after_prepare")

            result = self.source.sync_tbank_operations(request, expected_pins=state.pins,
                recorded_at=recorded_at, read_rub_positions=read_rub_positions, prepare_commit=prepare,
                **({"bound_full_fill": bound_full_fill} if bound_full_fill is not None else {}))
            self._point("sync.after_source_commit")
            if planned:
                _require(not result.replay, "UNEXPECTED_REPLAY")
                _, final = self._resolve(planned[0], allow_abort=False)
                _require(final.pins == result.pins, "RESULT_PIN_CONFLICT")
            else:
                _require(result.replay and result.pins == state.pins, "PLAN_MISSING")
                _, final = self._resolve(state, allow_abort=False)
            return result, final.snapshot()

    def require_runtime_authority(self) -> None:
        raise SourceBindingError("SOURCE_BINDING_CL7_CUTOVER_REQUIRED")
