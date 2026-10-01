"""Version-aware ordinary-operation journal (v4), NOT an armed runtime ledger.

An immutable, fully validated v3 correction is the prefix. Complete CL3 cursor
captures append ordinary observations, real CL1 transactions and provenance in
one SQLite transaction per batch. Prefix operations/versions cannot be booked
again. The new format is deliberately not a CashLedgerStore subtype; CL7
cutover and further correction writers require independent qualification.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Self

from . import broker_read_adapters as cl3
from . import cash_ledger_opening_reconciliation as cl4
from . import cash_ledger_persistence as cl2
from . import cash_observation_versions as versions
from . import versioned_fee_corrections as correction
from .cash_ledger_domain import LedgerAccount, LedgerTransaction, Money
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .versioned_fee_evidence import _canonical, _parse, _read_batch, _sealed, _sha
from .versioned_financial_readers import VersionedReadPins, project_versioned_cash

DOMAIN = "CL2_VERSIONED_OPERATIONAL_JOURNAL_V4"
SQLITE_APPLICATION_ID = 0x434C3204
SCHEMA_VERSION = 4
MAX_BATCHES = 64
MAX_ITEMS = 128
MAX_PAGES = 4
MAX_WINDOW_NS = 7 * 24 * 3600 * 10**9
MAX_EXPORT_BYTES = 12 * 1024 * 1024
_EXPORT_FIELDS = frozenset({"domain", "version", "financial_ready", "runtime_cutover_performed",
                            "frozen_correction_export_ascii", "migration_json_ascii", "batches"})
_MIGRATION_FIELDS = frozenset({"domain", "version", "mode", "account_scope_sha256", "identity_key_id",
                              "seed_pins", "codec_registry_sha256", "created_at"})
_CAPTURE_FIELDS = frozenset({"raw_account_id", "from_inclusive", "to_exclusive", "limit",
                            "max_pages", "max_items", "requests", "responses", "rub_positions"})
_RECORD_FIELDS = frozenset({"domain", "version", "sequence", "parent_sha256", "before_pins",
    "capture_json_ascii", "capture_sha256", "recorded_at", "watermark_json_ascii", "entries",
    "cash_delta_nano", "cash_after_nano", "ledger_revision", "ledger_head_sha256",
    "transaction_count", "covered_until"})
_ALLOWED_TYPES = frozenset({"OPERATION_TYPE_INPUT", "OPERATION_TYPE_OUTPUT", "OPERATION_TYPE_BUY",
                            "OPERATION_TYPE_SELL", "OPERATION_TYPE_BROKER_FEE"})
_SQL = (
    "CREATE TABLE cl2_v4_base (singleton INTEGER PRIMARY KEY CHECK(singleton=1), seed BLOB NOT NULL, migration BLOB NOT NULL)",
    "CREATE TABLE cl2_v4_batch (sequence INTEGER PRIMARY KEY CHECK(sequence>0), canonical BLOB NOT NULL, sha256 TEXT NOT NULL UNIQUE)",
    "CREATE TRIGGER freeze_v4_base_update BEFORE UPDATE ON cl2_v4_base BEGIN SELECT RAISE(ABORT,'FROZEN_BASE'); END",
    "CREATE TRIGGER freeze_v4_base_delete BEFORE DELETE ON cl2_v4_base BEGIN SELECT RAISE(ABORT,'FROZEN_BASE'); END",
    "CREATE TRIGGER freeze_v4_batch_update BEFORE UPDATE ON cl2_v4_batch BEGIN SELECT RAISE(ABORT,'IMMUTABLE_BATCH'); END",
    "CREATE TRIGGER freeze_v4_batch_delete BEFORE DELETE ON cl2_v4_batch BEGIN SELECT RAISE(ABORT,'IMMUTABLE_BATCH'); END",
)


class OperationalStoreError(RuntimeError):
    """Finite message only. Raw captures and exports contain private data."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise OperationalStoreError("V4_" + reason)


def _call(injector: Callable[[str], None] | None, point: str) -> None:
    if injector is not None:
        injector(point)


def _schema(conn: sqlite3.Connection) -> tuple:
    return tuple(tuple(r) for r in conn.execute(
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


def _unseal(raw: str, key: bytes, required_fields: frozenset[str]) -> dict[str, Any]:
    d = _parse(raw)
    _require(type(d) is dict and set(d) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    p = d["payload"]
    _require(type(p) is dict and frozenset(p) == required_fields and p["domain"] == DOMAIN
             and type(p["version"]) is int and p["version"] == 4, "ENVELOPE_INVALID")
    expected = hmac.new(key, _canonical(p), hashlib.sha256).hexdigest()
    _require(type(d["hmac_sha256"]) is str and hmac.compare_digest(d["hmac_sha256"], expected),
             "AUTHENTICATION_FAILED")
    return p


@dataclass(frozen=True, slots=True)
class OperationalPins:
    """Keep trusted pins independently; a file cannot attest its own freshness."""
    export_sha256: str
    seed_export_sha256: str
    batch_head_sha256: str
    store_revision: int
    ledger_revision: int
    ledger_head_sha256: str

    def __post_init__(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name.endswith("sha256"):
                versions._hash(v)
            else:
                _require(type(v) is int and 0 <= v <= cl2.MAX_REVISION, "PIN_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    pins: OperationalPins
    cash_nano: int
    transaction_count: int
    batch_count: int
    ordinary_observation_count: int
    covered_until: str
    financial_ready: bool = False
    runtime_cutover_performed: bool = False


@dataclass(frozen=True, slots=True)
class OperationalSyncResult:
    batch_sha256: str
    capture_sha256: str
    appended_observations: int
    appended_transactions: int
    cash_delta_nano: int
    replay: bool
    pins: OperationalPins

    def public_summary(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_RESULT", "status": "RECORDED_CUTOVER_REQUIRED",
            "batch_sha256": self.batch_sha256, "capture_sha256": self.capture_sha256,
            "appended_observations": self.appended_observations,
            "appended_transactions": self.appended_transactions, "replay": self.replay,
            "runtime_authority_granted": False, "financial_ready": False}


@dataclass(frozen=True, slots=True)
class OperationalCashProjection:
    pins: OperationalPins
    account_scope_sha256: str
    identity_key_id: str
    baseline_cash_nano: int
    correction_delta_nano: int
    ordinary_delta_nano: int
    expected_cash_nano: int
    transaction_count: int
    provenance_sha256: str
    economic_capture_at: str

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_PROJECTION", "version": 4, "pins": self.pins.to_dict(),
            "account_scope_sha256": self.account_scope_sha256, "identity_key_id": self.identity_key_id,
            "baseline_cash_nano": str(self.baseline_cash_nano),
            "correction_delta_nano": str(self.correction_delta_nano),
            "ordinary_delta_nano": str(self.ordinary_delta_nano),
            "expected_cash_nano": str(self.expected_cash_nano), "transaction_count": self.transaction_count,
            "provenance_sha256": self.provenance_sha256, "economic_capture_at": self.economic_capture_at,
            "financial_ready": False, "runtime_authority_granted": False}

    @property
    def sha256(self) -> str:
        return _sha(_canonical(self.to_canonical_dict()))


@dataclass
class _Graph:
    data: dict[str, Any]
    seed_projection: Any
    seed_checked: Any
    known: dict[str, dict[str, Any]]
    logical_sources: set[str]
    transactions: set[str]
    cash_nano: int
    ledger_revision: int
    ledger_head: str
    transaction_count: int
    covered_until: str
    seed_until: str
    first_from: str
    recorded_at: str
    batch_head: str
    record_bodies: list[dict[str, Any]]
    observation_count: int = 0

    def snapshot(self) -> OperationalSnapshot:
        raw = _canonical(self.data)
        pins = OperationalPins(_sha(raw), _sha(self.data["frozen_correction_export_ascii"].encode("ascii")),
            self.batch_head, self.seed_projection.pins.store_revision + len(self.record_bodies),
            self.ledger_revision, self.ledger_head)
        return OperationalSnapshot(pins, self.cash_nano, self.transaction_count, len(self.record_bodies),
                                   self.observation_count, self.covered_until)


def _effect(tx: LedgerTransaction) -> int:
    return sum(p.money.minor_units for p in tx.postings if p.account is LedgerAccount.ASSET_BROKER_CASH)


def _stable_id(row: dict[str, Any], key: bytes, account: str) -> str:
    # State is intentionally NOT part of identity: changing an operation's state
    # cannot create another ordinary source and book the payment a second time.
    return hmac.new(key, _canonical({"domain": DOMAIN + "_PROVIDER_OPERATION",
        "account_scope_sha256": account, "provider_id": row["id"]}), hashlib.sha256).hexdigest()


def _economic_signature(row: dict[str, Any]) -> str:
    # Cursors are pagination evidence, not economic identity. All other primary
    # fields are retained. Different IDs with identical data are quarantined as
    # ambiguous aliases, even if that conservatively rejects two real events.
    return _sha(_canonical({k: v for k, v in row.items() if k not in {"id", "cursor"}}))


def _known_row(row: dict[str, Any], obs: cl2.InboxObservation) -> dict[str, Any]:
    return {"signature": _economic_signature(row), "observation_sha256": obs.sha256,
            "logical_source_sha256": obs.logical_source_sha256, "effective_at": obs.observed_at}


def _seed_graph(data: dict[str, Any], registry: Any, key: bytes, key_id: str, account: str) -> _Graph:
    raw = data["frozen_correction_export_ascii"].encode("ascii")
    m = _unseal(data["migration_json_ascii"], key, _MIGRATION_FIELDS)
    _require(m["mode"] == "ORDINARY_APPEND_CANDIDATE_NO_RUNTIME_CUTOVER"
        and m["account_scope_sha256"] == account and m["identity_key_id"] == key_id
        and m["codec_registry_sha256"] == versions._registry_sha(registry), "MIGRATION_BINDING_INVALID")
    pins = VersionedReadPins(**m["seed_pins"])
    projection = project_versioned_cash(raw, pins=pins, codec_registry=tuple(registry.values()),
        identity_key=key, identity_key_id=key_id, account_scope_sha256=account)
    c = correction._validate_export(raw, registry, key, key_id, account)
    _require(c.record is not None, "CORRECTION_REQUIRED")
    cap = _parse(c.record["capture_json_ascii"])
    start = cap["operation_requests"][0]["from"]
    batch, rows = _read_batch(cap, key, key_id, start, account)
    known = {_stable_id(r, key, account): _known_row(r, d.observation)
             for r, d in zip(rows, batch.decisions, strict=True)}
    base = _parse(c.graph.base)
    logical = {cl2.InboxObservation.from_canonical_bytes(r["canonical_json_ascii"].encode("ascii"),
                codec_registry=tuple(registry.values())).logical_source_sha256 for r in base["observations"]}
    logical.update(v["logical_source_sha256"] for v in c.graph.records)
    transactions = {r["sha256"] for r in base["transactions"]}
    transactions.update(c.record["provenance"]["transaction_sha256s"])
    created = versions._time(m["created_at"])
    _require(created >= cap["captured_at"], "CREATION_BEFORE_SEED")
    empty_data = dict(data, batches=[])
    return _Graph(empty_data, projection, c, known, logical, transactions, projection.expected_cash_nano,
        pins.ledger_revision, pins.ledger_head_sha256, projection.transaction_count, cap["captured_at"],
        cap["captured_at"], start, created, _sha(data["migration_json_ascii"].encode("ascii")), [])


def _decode_capture(raw: bytes, key: bytes, key_id: str, account: str) -> tuple[Any, list[Any], dict[str, Any]]:
    c = _parse(raw)
    _require(type(c) is dict and frozenset(c) in
             (_CAPTURE_FIELDS, _CAPTURE_FIELDS | {"bound_full_fill"}), "CAPTURE_FIELDS_INVALID")
    for name, maximum in (("limit", MAX_ITEMS), ("max_items", MAX_ITEMS), ("max_pages", MAX_PAGES)):
        _require(type(c[name]) is int and 1 <= c[name] <= maximum, "READ_BOUNDS_INVALID")
    start, end = versions._time(c["from_inclusive"]), versions._time(c["to_exclusive"])
    _require(0 < timestamp_ns(end) - timestamp_ns(start) <= MAX_WINDOW_NS, "WINDOW_INVALID")
    requests, responses = c["requests"], c["responses"]
    _require(type(requests) is list and type(responses) is list
             and 0 < len(requests) == len(responses) <= MAX_PAGES, "PAGINATION_INVALID")
    index = 0
    rows = []

    def replay(payload: Any, _: int) -> Any:
        nonlocal index
        _require(index < len(responses) and payload == requests[index], "REQUEST_MISMATCH")
        answer = deepcopy(responses[index]); index += 1
        if type(answer) is dict and type(answer.get("items")) is list:
            rows.extend(answer["items"])
        return answer

    batch = cl3.collect_tbank_operations(cl3.BrokerReadRequest(
        cl3.BrokerEnvironment.SANDBOX, c["raw_account_id"], key, key_id, start, end,
        c["limit"], c["max_pages"], c["max_items"], MAX_AGE_NS,
        cl3.RetryPolicy(1, MAX_AGE_NS, ()), replay, lambda: 0, lambda _: None))
    _require(index == len(responses) and batch.watermark.account_scope_sha256 == account
             and len(rows) == len(batch.decisions), "CAPTURE_INCOMPLETE")
    return batch, rows, c


def _derive(graph: _Graph, capture: bytes, recorded_at: str, key: bytes,
            key_id: str, account: str) -> dict[str, Any]:
    batch, rows, c = _decode_capture(capture, key, key_id, account)
    _require(graph.first_from <= c["from_inclusive"] <= graph.covered_until
             and c["to_exclusive"] >= graph.covered_until, "WINDOW_GAP_OR_REGRESSION")
    recorded_at = versions._time(recorded_at)
    _require(recorded_at >= max(graph.recorded_at, c["to_exclusive"]), "REGISTRATION_TIME_INVALID")
    _require(len(graph.record_bodies) < MAX_BATCHES, "BATCH_CAPACITY_EXCEEDED")
    # A tagged receipt profile supplements, never weakens, generic CL3.
    bound = c.get("bound_full_fill")
    overrides = {}
    if "bound_full_fill" in c:
        from .versioned_fill_evidence import fill_transactions, decode_bound_fill
        _, _, receipt = decode_bound_fill(bound, key=key, key_id=key_id, account_scope=account)
        _require(c["to_exclusive"] == bound["observed_at"], "FILL_CAPTURE_TIME_MISMATCH")
        selected = [(row, decision) for row, decision in zip(rows, batch.decisions, strict=True)
                    if _stable_id(row, key, account) not in graph.known]
        _require(bool(selected), "FILL_COMPONENTS_ALREADY_RECORDED")
        new_rows = [row for row, _ in selected]
        selected_batch = replace(batch, decisions=tuple(d for _, d in selected))
        overrides = fill_transactions(bound, selected_batch, new_rows,
                                      key=key, key_id=key_id, account_scope=account)
        current_trade_ids = {stage["tradeId"] for stage in bound["order_state"]["stages"]}
        previous_ids = set()
        seed_capture = _parse(graph.seed_checked.record["capture_json_ascii"])
        histories = [seed_capture["operation_responses"]]
        for old_body in graph.record_bodies:
            old_capture = _parse(old_body["capture_json_ascii"])
            if "bound_full_fill" in old_capture:
                _require(old_capture["bound_full_fill"]["dispatch_plan_sha256"]
                         != bound["dispatch_plan_sha256"], "FILL_ATTEMPT_ALREADY_RECORDED")
            histories.append(old_capture["responses"])
        for pages in histories:
            for page in pages:
                for row in page.get("items", []):
                    for trade in row.get("tradesInfo", {}).get("trades", []):
                        previous_ids.add(trade["num"])
        _require(not current_trade_ids & previous_ids, "FILL_TRADE_ID_ALREADY_OBSERVED")
    seen = set()
    new = []
    signatures = {v["signature"] for v in graph.known.values()}
    for row, decision in zip(rows, batch.decisions, strict=True):
        source_id = _stable_id(row, key, account)
        observed = _known_row(row, decision.observation)
        seen.add(source_id)
        if source_id in graph.known:
            _require(observed == graph.known[source_id], "SOURCE_CONTENT_CONFLICT")
            continue
        _require(decision.observation.logical_source_sha256 not in graph.logical_sources,
                 "HISTORICAL_SOURCE_CONFLICT")
        _require(observed["signature"] not in signatures, "POSSIBLE_ALIAS_REVIEW_REQUIRED")
        _require(observed["effective_at"] >= graph.seed_until, "PRE_CUTOVER_OPERATION_REVIEW_REQUIRED")
        _require(row["type"] in _ALLOWED_TYPES, "OPERATION_PROFILE_UNSUPPORTED")
        if decision.observation.sha256 in overrides:
            _require(bound is not None, "FILL_BINDING_REQUIRED")
        elif decision.kind is cl3.BrokerDecisionKind.NOT_LEDGER_RELEVANT:
            # Generic CL3 marks canceled rows nonledger without proving zero fill.
            _require(row["quantityDone"] == "0" and cl3.money_value_to_money(row["payment"]).minor_units == 0
                and cl3.money_value_to_money(row["commission"]).minor_units == 0
                and not row.get("parentOperationId") and not row["childOperations"]
                and not row.get("tradesInfo"), "CANCELED_EFFECT_UNKNOWN")
        else:
            _require(decision.kind is cl3.BrokerDecisionKind.TRANSACTION_PROPOSED
                     and decision.transaction_proposal is not None, "OPERATION_REVIEW_REQUIRED")
        new.append((row, decision, source_id, observed))
        signatures.add(observed["signature"])
    required = {k for k, v in graph.known.items()
                if c["from_inclusive"] <= v["effective_at"] < c["to_exclusive"]}
    _require(required <= seen, "PREVIOUS_OBSERVATION_MISSING")
    new.sort(key=lambda x: (x[3]["effective_at"], x[2]))
    ledger_rev, ledger_head = graph.ledger_revision, graph.ledger_head
    count, delta, entries = graph.transaction_count, 0, []
    for row, d, source_id, observed in new:
        tx = overrides.get(d.observation.sha256, d.transaction_proposal)
        head = None
        if tx is not None:
            _require(tx.sha256 not in graph.transactions, "DUPLICATE_TRANSACTION")
            ledger_rev += 1
            _require(ledger_rev <= cl2.MAX_REVISION, "REVISION_EXHAUSTED")
            head = cl2.LedgerHead(ledger_rev, ledger_head, "TRANSACTION", tx.sha256)
            ledger_head = head.sha256
            delta += _effect(tx); count += 1
        entries.append({"provider_operation_sha256": source_id, "raw_signature_sha256": observed["signature"],
            "observation_json_ascii": d.observation.canonical_bytes.decode("ascii"),
            "observation_sha256": d.observation.sha256,
            "decision_kind": d.kind.value, "decision_reason": d.reason.value,
            "transaction_json_ascii": None if tx is None else tx.canonical_bytes.decode("ascii"),
            "transaction_sha256": None if tx is None else tx.sha256,
            "ledger_head_json_ascii": None if head is None else head.canonical_bytes.decode("ascii"),
            "provenance": {"observation_sha256": d.observation.sha256,
                "logical_source_sha256": d.observation.logical_source_sha256,
                "transaction_sha256": None if tx is None else tx.sha256}})
    cash = graph.cash_nano + delta
    Money("RUB", cash)
    _require(cash >= 0, "NEGATIVE_CASH_UNSUPPORTED")
    primary_cash = cl4.build_broker_rub_position_cash_proof(c["rub_positions"],
        raw_account_id=c["raw_account_id"], as_of=recorded_at, evaluated_at=recorded_at,
        response_complete=True, account_scope_sha256=account,
        environment=cl3.BrokerEnvironment.SANDBOX, identity_key=key, identity_key_id=key_id)
    _require(primary_cash.cash.minor_units == cash, "BROKER_CASH_MISMATCH")
    _require(graph.snapshot().pins.store_revision < cl2.MAX_REVISION, "REVISION_EXHAUSTED")
    return {"domain": DOMAIN, "version": 4, "sequence": len(graph.record_bodies) + 1,
        "parent_sha256": graph.batch_head, "before_pins": graph.snapshot().pins.to_dict(),
        "capture_json_ascii": capture.decode("ascii"), "capture_sha256": _sha(capture),
        "recorded_at": recorded_at, "watermark_json_ascii": batch.watermark.canonical_bytes.decode("ascii"),
        "entries": entries, "cash_delta_nano": str(delta), "cash_after_nano": str(cash),
        "ledger_revision": ledger_rev, "ledger_head_sha256": ledger_head,
        "transaction_count": count, "covered_until": c["to_exclusive"]}


def _advance(graph: _Graph, body: dict[str, Any], raw: bytes, key: bytes, key_id: str, account: str) -> None:
    batch, rows, _ = _decode_capture(body["capture_json_ascii"].encode("ascii"), key, key_id, account)
    for row, decision in zip(rows, batch.decisions, strict=True):
        graph.known[_stable_id(row, key, account)] = _known_row(row, decision.observation)
        graph.logical_sources.add(decision.observation.logical_source_sha256)
    for entry in body["entries"]:
        if entry["transaction_sha256"] is not None:
            graph.transactions.add(entry["transaction_sha256"])
    graph.observation_count += len(body["entries"])
    graph.cash_nano = int(body["cash_after_nano"])
    graph.ledger_revision, graph.ledger_head = body["ledger_revision"], body["ledger_head_sha256"]
    graph.transaction_count = body["transaction_count"]
    graph.covered_until, graph.recorded_at = body["covered_until"], body["recorded_at"]
    graph.batch_head = _sha(raw)
    graph.record_bodies.append(body)
    graph.data["batches"].append({"sha256": _sha(raw), "canonical_json_ascii": raw.decode("ascii")})


def _validate(raw: bytes, registry: Any, key: bytes, key_id: str, account: str,
              pins: OperationalPins | None = None) -> _Graph:
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_EXPORT_BYTES, "EXPORT_BOUNDS_INVALID")
    data = _parse(raw)
    _require(type(data) is dict and frozenset(data) == _EXPORT_FIELDS and data["domain"] == DOMAIN
        and type(data["version"]) is int and data["version"] == 4
        and data["financial_ready"] is False and data["runtime_cutover_performed"] is False,
        "EXPORT_INVALID")
    records = data["batches"]
    _require(type(records) is list and len(records) <= MAX_BATCHES, "BATCH_CAPACITY_EXCEEDED")
    graph = _seed_graph(data, registry, key, key_id, account)
    captures = set()
    for row in records:
        _require(type(row) is dict and set(row) == {"sha256", "canonical_json_ascii"}, "ROW_INVALID")
        record = row["canonical_json_ascii"].encode("ascii")
        _require(_sha(record) == row["sha256"], "ROW_HASH_INVALID")
        body = _unseal(row["canonical_json_ascii"], key, _RECORD_FIELDS)
        _require(body["capture_sha256"] not in captures, "DUPLICATE_CAPTURE")
        expected = _derive(graph, body["capture_json_ascii"].encode("ascii"), body["recorded_at"], key, key_id, account)
        _require(_canonical(body) == _canonical(expected), "BATCH_SEMANTICS_INVALID")
        _advance(graph, body, record, key, key_id, account)
        captures.add(body["capture_sha256"])
    _require(_canonical(graph.data) == raw, "EXPORT_RECONSTRUCTION_INVALID")
    if pins is not None:
        _require(type(pins) is OperationalPins, "PIN_MISMATCH")
        pins = OperationalPins(**pins.to_dict())
        _require(graph.snapshot().pins == pins, "PIN_MISMATCH")
    return graph


def validate_operational_export(raw: bytes, *, codec_registry: object, identity_key: bytes,
        identity_key_id: str, account_scope_sha256: str, pins: OperationalPins) -> OperationalSnapshot:
    try:
        return _validate(raw, versions._registry(codec_registry), versions._key(identity_key),
            versions._key_id(identity_key_id), versions._hash(account_scope_sha256), pins).snapshot()
    except OperationalStoreError:
        raise
    except Exception:
        raise OperationalStoreError("V4_EXPORT_INVALID") from None


def _project_validated_graph(g: _Graph, identity_key_id: str, account_scope_sha256: str) -> OperationalCashProjection:
    """Internal projection from the full graph checked in the same call/lease."""
    delta = sum(_effect(LedgerTransaction.from_canonical_dict(_parse(e["transaction_json_ascii"])))
        for b in g.record_bodies for e in b["entries"] if e["transaction_json_ascii"] is not None)
    _require(g.seed_projection.expected_cash_nano + delta == g.cash_nano, "CASH_SUM_MISMATCH")
    return OperationalCashProjection(g.snapshot().pins, account_scope_sha256, identity_key_id,
        g.seed_projection.baseline_cash_nano, g.seed_projection.correction_delta_nano, delta,
        g.cash_nano, g.transaction_count, _sha(_canonical([e["provenance"]
            for b in g.record_bodies for e in b["entries"]] + [g.seed_projection.provenance_sha256])),
        g.covered_until)


def project_operational_cash(raw: bytes, *, pins: OperationalPins, codec_registry: object,
        identity_key: bytes, identity_key_id: str, account_scope_sha256: str) -> OperationalCashProjection:
    try:
        g = _validate(raw, versions._registry(codec_registry), versions._key(identity_key),
            versions._key_id(identity_key_id), versions._hash(account_scope_sha256), pins)
        return _project_validated_graph(g, identity_key_id, account_scope_sha256)
    except OperationalStoreError:
        raise
    except Exception:
        raise OperationalStoreError("V4_PROJECTION_INVALID") from None


def _export(conn: sqlite3.Connection) -> bytes:
    row = conn.execute("SELECT seed,migration FROM cl2_v4_base WHERE singleton=1").fetchone()
    _require(row is not None, "BASE_MISSING")
    batches = []
    for number, canonical, sha in conn.execute("SELECT sequence,canonical,sha256 FROM cl2_v4_batch ORDER BY sequence"):
        _require(number == len(batches) + 1 and _sha(bytes(canonical)) == sha, "ROW_SEQUENCE_INVALID")
        batches.append({"sha256": sha, "canonical_json_ascii": bytes(canonical).decode("ascii")})
    return _canonical({"domain": DOMAIN, "version": 4, "financial_ready": False,
        "runtime_cutover_performed": False, "frozen_correction_export_ascii": bytes(row[0]).decode("ascii"),
        "migration_json_ascii": bytes(row[1]).decode("ascii"), "batches": batches})


def _check_connection(conn: sqlite3.Connection) -> None:
    _require(conn.execute("PRAGMA application_id").fetchone()[0] == SQLITE_APPLICATION_ID
             and conn.execute("PRAGMA user_version").fetchone()[0] == 4 and _schema(conn) == _SCHEMA,
             "SCHEMA_INVALID")
    _require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "INTEGRITY_INVALID")


def _publish(raw: bytes, target_root: object, registry: Any, key: bytes, key_id: str, account: str,
             injector: Callable[[str], None] | None = None) -> Path:
    g = _validate(raw, registry, key, key_id, account)
    target = cl2._path_from(target_root); staging = target.with_name(target.name + ".v4-staging")
    cl2._validate_target_parent(target); cl2._require_absent_target(target, staging)
    conn, owned, promoted = None, False, False
    try:
        staging.mkdir(mode=0o700); owned = True
        conn = cl2._connect(staging / "store.sqlite3", 0)
        conn.execute("PRAGMA journal_mode=WAL"); conn.execute("BEGIN IMMEDIATE")
        for sql in _SQL:
            conn.execute(sql)
        conn.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
        conn.execute("PRAGMA user_version=4")
        conn.execute("INSERT INTO cl2_v4_base VALUES(1,?,?)", (
            g.data["frozen_correction_export_ascii"].encode("ascii"), g.data["migration_json_ascii"].encode("ascii")))
        for n, row in enumerate(g.data["batches"], 1):
            conn.execute("INSERT INTO cl2_v4_batch VALUES(?,?,?)", (n, row["canonical_json_ascii"].encode("ascii"), row["sha256"]))
        _check_connection(conn)
        _require(_export(conn) == raw, "RESTORE_MISMATCH")
        _call(injector, "copy.before_commit"); conn.execute("COMMIT"); _call(injector, "copy.after_commit")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)"); conn.close(); conn = None
        _call(injector, "copy.before_promote"); cl2._promote_staging(staging, target); promoted = True
        _call(injector, "copy.after_promote")
        return target
    finally:
        if conn is not None:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()
        if owned and not promoted and staging.exists():
            shutil.rmtree(staging)


def create_operational_store(seed_export: bytes, target_root: object, *, seed_pins: VersionedReadPins,
        codec_registry: object, identity_key: bytes, identity_key_id: str, account_scope_sha256: str,
        created_at: str, fault_injector: Callable[[str], None] | None = None) -> Path:
    """Create a new candidate DB from pinned v3 bytes; never migrate in place."""
    registry, key = versions._registry(codec_registry), versions._key(identity_key)
    key_id, account = versions._key_id(identity_key_id), versions._hash(account_scope_sha256)
    project_versioned_cash(seed_export, pins=seed_pins, codec_registry=tuple(registry.values()),
        identity_key=key, identity_key_id=key_id, account_scope_sha256=account)
    migration = _sealed({"domain": DOMAIN, "version": 4, "mode": "ORDINARY_APPEND_CANDIDATE_NO_RUNTIME_CUTOVER",
        "account_scope_sha256": account, "identity_key_id": key_id,
        "seed_pins": {f.name: getattr(seed_pins, f.name) for f in fields(seed_pins)},
        "codec_registry_sha256": versions._registry_sha(registry), "created_at": versions._time(created_at)}, key)
    raw = _canonical({"domain": DOMAIN, "version": 4, "financial_ready": False,
        "runtime_cutover_performed": False, "frozen_correction_export_ascii": seed_export.decode("ascii"),
        "migration_json_ascii": migration.decode("ascii"), "batches": []})
    return _publish(raw, target_root, registry, key, key_id, account, fault_injector)


def restore_operational_export(raw: bytes, target_root: object, *, pins: OperationalPins,
        codec_registry: object, identity_key: bytes, identity_key_id: str, account_scope_sha256: str,
        fault_injector: Callable[[str], None] | None = None) -> Path:
    registry, key = versions._registry(codec_registry), versions._key(identity_key)
    key_id, account = versions._key_id(identity_key_id), versions._hash(account_scope_sha256)
    _validate(raw, registry, key, key_id, account, pins)
    return _publish(raw, target_root, registry, key, key_id, account, fault_injector)


class LockedOperationalView:
    """One validated v4 snapshot on an actual writer-locked connection.

    Not a CashLedgerStore or runtime authority. Instances are issued only by
    locked_snapshot; use after exit, across threads, or after a transaction
    boundary is rejected. Full export is private financial history.
    """
    __slots__ = ("_connection", "_raw", "_snapshot", "_active", "_thread", "_root",
                 "_file_identity", "_lease_id", "_started", "_clock", "_registry", "_key", "_key_id", "_account",
                 "_projection", "_total_changes", "_transaction_ended")

    def __init__(self, token: object, *, connection: sqlite3.Connection, raw: bytes,
                 snapshot: OperationalSnapshot, projection: OperationalCashProjection, root: Path, started: int,
                 clock: Callable[[], int], registry: Any, key: bytes, key_id: str, account: str):
        _require(token is _LOCKED_VIEW_TOKEN, "VIEW_CONSTRUCTION_FORBIDDEN")
        self._connection, self._raw, self._snapshot = connection, raw, snapshot
        self._active, self._thread, self._root = True, threading.get_ident(), root
        self._lease_id = secrets.token_hex(32)
        st = (root / "store.sqlite3").stat()
        self._file_identity = (st.st_dev, st.st_ino)
        self._started, self._clock = started, clock
        self._registry, self._key, self._key_id, self._account = registry, key, key_id, account
        self._projection = projection
        self._total_changes = connection.total_changes
        self._transaction_ended = False
        # This connection is owned solely by locked_snapshot. Detect a caller
        # ending/restarting its transaction even when the final bytes match.
        def trace(statement: str) -> None:
            import re
            cleaned = re.sub(r"/\*.*?\*/|--[^\n]*(?:\n|$)", " ", statement, flags=re.S).strip()
            cleaned = cleaned.lstrip("; \t\r\n")
            first = cleaned.split(None, 1)[0].upper() if cleaned else ""
            if first in {"COMMIT", "END", "ROLLBACK", "BEGIN"}:
                self._transaction_ended = True
        connection.set_trace_callback(trace)

    def assert_active(self) -> None:
        _require(self._active and threading.get_ident() == self._thread, "VIEW_NOT_ACTIVE")
        _require(self._connection.in_transaction and not self._transaction_ended, "VIEW_TRANSACTION_LOST")
        _require(self._connection.total_changes == self._total_changes, "VIEW_CHANGED")
        tick = self._clock()
        _require(type(tick) is int and 0 <= tick - self._started <= MAX_AGE_NS, "VIEW_EXPIRED")
        db = cl2._validate_live_root(self._root)
        st = db.stat()
        _require((st.st_dev, st.st_ino) == self._file_identity, "VIEW_DATABASE_REPLACED")
        _check_connection(self._connection)
        _require(_export(self._connection) == self._raw, "VIEW_CHANGED")
        end = self._clock()
        _require(type(end) is int and tick <= end and end - self._started <= MAX_AGE_NS, "VIEW_EXPIRED")

    @property
    def pins(self) -> OperationalPins:
        self.assert_active()
        return self._snapshot.pins

    def snapshot(self) -> OperationalSnapshot:
        self.assert_active()
        return self._snapshot

    def export_bytes(self) -> bytes:
        self.assert_active()
        return self._raw

    def project_cash(self) -> OperationalCashProjection:
        self.assert_active()
        # Never reuse across views. Full graph was validated before this view
        # was issued; assert_active still checks exact DB bytes/schema each time.
        result = self._projection
        self.assert_active()
        return result


_LOCKED_VIEW_TOKEN = object()


class VersionedOperationalStore:
    """Ordinary CL3 writers over versioned history; cutover is NOT implemented.

    Mutation accepts a whole successfully decoded bounded batch, not a caller's
    transaction amount. Every stored batch is re-decoded on open/read/replay.
    """
    def __init__(self, root: Path, conn: sqlite3.Connection, registry: Any, key: bytes,
                 key_id: str, account: str, injector: Callable[[str], None] | None):
        self.root, self._connection, self._registry = root, conn, registry
        self._key, self._key_id, self._account, self._injector = key, key_id, account, injector
        self._closed = False

    @classmethod
    def open(cls, root: object, *, codec_registry: object, identity_key: bytes,
             identity_key_id: str, account_scope_sha256: str, pins: OperationalPins | None = None,
             fault_injector: Callable[[str], None] | None = None) -> Self:
        path = cl2._path_from(root); db = cl2._validate_live_root(path)
        registry, key = versions._registry(codec_registry), versions._key(identity_key)
        key_id, account = versions._key_id(identity_key_id), versions._hash(account_scope_sha256)
        ro = versions._read_connection(db)
        try:
            ro.execute("BEGIN"); _check_connection(ro)
            _validate(_export(ro), registry, key, key_id, account, pins)
        finally:
            ro.close()
        conn = cl2._connect(db, 0)
        obj = cls(path, conn, registry, key, key_id, account, fault_injector)
        try:
            obj.snapshot(pins=pins)
            return obj
        except BaseException:
            conn.close(); raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close(); self._closed = True

    def export_bytes(self) -> bytes:
        _require(not self._closed and not self._connection.in_transaction, "STORE_NOT_IDLE")
        try:
            self._connection.execute("BEGIN"); _check_connection(self._connection)
            raw = _export(self._connection)
            _validate(raw, self._registry, self._key, self._key_id, self._account)
            self._connection.execute("COMMIT")
            return raw
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def snapshot(self, *, pins: OperationalPins | None = None) -> OperationalSnapshot:
        return _validate(self.export_bytes(), self._registry, self._key, self._key_id, self._account, pins).snapshot()

    @contextmanager
    def locked_snapshot(self, *, expected_pins: OperationalPins,
                        monotonic_ns: Callable[[], int] = time.monotonic_ns) -> Iterator[LockedOperationalView]:
        """Freeze a pinned, fully validated snapshot; this method never writes.

        A separate owned connection avoids nested BEGIN on the ordinary sync
        connection. BEGIN IMMEDIATE prevents *other* SQLite writers as well as
        this store's sync. No path is created, no pin is learned implicitly.
        """
        _require(not self._closed and not self._connection.in_transaction, "STORE_NOT_IDLE")
        _require(type(expected_pins) is OperationalPins and callable(monotonic_ns), "PIN_INVALID")
        begin = monotonic_ns()
        _require(type(begin) is int and begin >= 0, "CLOCK_INVALID")
        db = cl2._validate_live_root(self.root)
        identity = db.stat()
        conn = None
        view = None
        try:
            conn = sqlite3.connect(db.resolve().as_uri() + "?mode=rw", uri=True,
                                   timeout=0, isolation_level=None)
            conn.execute("PRAGMA busy_timeout=0")
            conn.execute("BEGIN IMMEDIATE")
            _check_connection(conn)
            st = db.stat()
            _require((st.st_dev, st.st_ino) == (identity.st_dev, identity.st_ino), "VIEW_DATABASE_REPLACED")
            raw = _export(conn)
            graph = _validate(raw, self._registry, self._key, self._key_id, self._account, expected_pins)
            view = LockedOperationalView(_LOCKED_VIEW_TOKEN, connection=conn, raw=raw,
                snapshot=graph.snapshot(), projection=_project_validated_graph(graph, self._key_id, self._account),
                root=self.root, started=begin, clock=monotonic_ns,
                registry=self._registry, key=self._key, key_id=self._key_id, account=self._account)
            view.assert_active()
            yield view
            view.assert_active()
        except sqlite3.Error:
            raise OperationalStoreError("V4_VIEW_LOCK_OR_DATABASE_UNAVAILABLE") from None
        finally:
            if view is not None:
                view._active = False
            if conn is not None:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                finally:
                    conn.close()

    def sync_tbank_operations(self, request: cl3.BrokerReadRequest, *, expected_pins: OperationalPins,
                             recorded_at: str,
                             read_rub_positions: Callable[[str], Any],
                             prepare_commit: Callable[[bytes, bytes], None] | None = None,
                             bound_full_fill: dict | None = None) -> OperationalSyncResult:
        """Read a complete bounded CL3 window, then atomically append with CAS.

        recorded_at is caller-supplied; acceptance bounds monotonic elapsed time.
        No orders, authority writes, retries or local fallback cash are added.
        A failed collection never writes a prefix or advances a watermark.
        Optional prepare_commit durably pins the exact before/after export
        before INSERT. It grants no monetary authority and cannot alter the
        derived batch. It is not called for a no-write replay.
        """
        try:
            _require(type(request) is cl3.BrokerReadRequest and request.environment is cl3.BrokerEnvironment.SANDBOX
                and request.identity_key == self._key and request.identity_key_id == self._key_id
                and cl3._account_scope(request) == self._account, "REQUEST_IDENTITY_INVALID")
            _require(request.retry_policy.max_attempts == 1 and request.limit <= MAX_ITEMS
                and request.max_items <= MAX_ITEMS and request.max_pages <= MAX_PAGES, "READ_BOUNDS_INVALID")
            _require(callable(read_rub_positions), "CASH_READER_REQUIRED")
            _require(prepare_commit is None or callable(prepare_commit), "COMMIT_OBSERVER_INVALID")
            versions._time(recorded_at)
            _require(recorded_at >= request.to_exclusive, "REGISTRATION_TIME_INVALID")
            self.snapshot(pins=expected_pins)
            start = request.monotonic_ns()
            _require(type(start) is int and 0 <= start < request.absolute_deadline_ns, "CLOCK_INVALID")
            deadline = min(request.absolute_deadline_ns, start + MAX_AGE_NS)
            payloads, responses = [], []

            def capture(payload: Any, timeout_ns: int) -> Any:
                payloads.append(deepcopy(payload))
                response = request.transport(payload, timeout_ns)
                responses.append(deepcopy(response))
                return response

            cl3.collect_tbank_operations(replace(request, transport=capture, absolute_deadline_ns=deadline))
            raw = _canonical({"raw_account_id": request.raw_account_id,
                "from_inclusive": request.from_inclusive, "to_exclusive": request.to_exclusive,
                "limit": request.limit, "max_pages": request.max_pages, "max_items": request.max_items,
                "requests": payloads, "responses": responses,
                "rub_positions": deepcopy(read_rub_positions(request.raw_account_id))})

            if bound_full_fill is not None:
                captured = _parse(raw)
                captured["bound_full_fill"] = _parse(_canonical(bound_full_fill))
                raw = _canonical(captured)

            def timely() -> None:
                now = request.monotonic_ns()
                _require(type(now) is int and start <= now < deadline, "DEADLINE_EXCEEDED")

            timely()
            return self._append_capture(raw, expected_pins, recorded_at, timely, prepare_commit)
        except OperationalStoreError:
            raise
        except Exception:
            raise OperationalStoreError("V4_SYNC_FAILED") from None

    def _append_capture(self, capture: bytes, expected: OperationalPins, recorded_at: str,
                        timely: Callable[[], None],
                        prepare_commit: Callable[[bytes, bytes], None] | None = None) -> OperationalSyncResult:
        _require(not self._closed and not self._connection.in_transaction, "STORE_NOT_IDLE")
        conn = self._connection
        try:
            conn.execute("BEGIN IMMEDIATE"); _check_connection(conn)
            raw = _export(conn)
            g = _validate(raw, self._registry, self._key, self._key_id, self._account, expected)
            digest = _sha(capture)
            for body, row in zip(g.record_bodies, g.data["batches"], strict=True):
                if body["capture_sha256"] == digest:
                    _require(body["capture_json_ascii"].encode("ascii") == capture, "CAPTURE_CONFLICT")
                    timely(); conn.execute("COMMIT")
                    return OperationalSyncResult(row["sha256"], digest, 0, 0, 0, True, g.snapshot().pins)
            body = _derive(g, capture, recorded_at, self._key, self._key_id, self._account)
            signed = _sealed(body, self._key)
            if prepare_commit is not None:
                proposed = deepcopy(g.data)
                proposed["batches"].append({"sha256": _sha(signed),
                                            "canonical_json_ascii": signed.decode("ascii")})
                proposed_raw = _canonical(proposed)
                _validate(proposed_raw, self._registry, self._key, self._key_id, self._account)
                timely()
                prepare_commit(raw, proposed_raw)
                # A callback is bookkeeping, not a way to replace the checked
                # batch or mutate this connection before its atomic INSERT.
                _require(conn.in_transaction and _export(conn) == raw, "PREPARE_CHANGED_SOURCE")
                _check_connection(conn)
                timely()
            _call(self._injector, "append.before_insert")
            conn.execute("INSERT INTO cl2_v4_batch VALUES(?,?,?)", (body["sequence"], signed, _sha(signed)))
            _call(self._injector, "append.after_insert")
            after = _validate(_export(conn), self._registry, self._key, self._key_id, self._account)
            _call(self._injector, "append.before_commit"); timely(); conn.execute("COMMIT")
            _call(self._injector, "append.after_commit")
            return OperationalSyncResult(_sha(signed), digest, len(body["entries"]),
                sum(e["transaction_sha256"] is not None for e in body["entries"]),
                int(body["cash_delta_nano"]), False, after.snapshot().pins)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    def append_transaction(self, *_: object, **__: object) -> None:
        raise OperationalStoreError("V4_PRIMARY_BATCH_REQUIRED")

    def append_observation(self, *_: object, **__: object) -> None:
        raise OperationalStoreError("V4_PRIMARY_BATCH_REQUIRED")

    def append_correction_bundle(self, *_: object, **__: object) -> None:
        raise OperationalStoreError("V4_FURTHER_CORRECTION_NOT_QUALIFIED")

    def require_runtime_authority(self) -> None:
        raise OperationalStoreError("V4_CL7_CUTOVER_REQUIRED")
