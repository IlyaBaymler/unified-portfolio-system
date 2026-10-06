"""Cash-only maintenance of a selected v4 source, with ordered authority pins.

Real provider reads precede owner locks. Immutable plans bind the exact v4
batch and registry event before either database is changed. Recovery accepts
only that planned prefix, writes no cash and never treats current pins as a
new authority. Portfolio/Risk/Central remain pinned and unchanged. This is not
v4 trading, a Portfolio refresh, an external cash-finality proof, or an atomic
transaction spanning the registry, ledger, JSON plans and authority files.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import broker_read_adapters as cl3
from . import cash_ledger_persistence as cl2
from . import cash_observation_versions as versions
from . import versioned_operational_store as v4
from . import versioned_runtime_cutover as cut
from . import versioned_source_binding as registry
from .exact_cash_settlement import _pairs, _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .journal import JournalEvent
from .runtime_cash_authority import RuntimeCashAuthorityRecord as Authority
from .runtime_cash_authority import RuntimeCashAuthorityState as State
from .runtime_cash_authority import _transition_pair
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha

DOMAIN = "V4_SELECTED_CASH_SYNC_V1"
HELD = "VERSIONED_SELECTED_SYNC_HELD"
COMMITTED = "VERSIONED_SELECTED_SYNC_COMMITTED"
ABORTED = "VERSIONED_SELECTED_SYNC_ABORTED"
MAX_ROUNDS = 16
MAX_PLAN_BYTES = 24 * 1024 * 1024
_PLAN = {"domain", "version", "kind", "selection_sha256", "target_root", "sequence",
         "prepared_at", "before_authority", "before_checkpoint", "before_export_ascii",
         "after_export_ascii", "registry_prepared_ascii"}
_RESULT = {"domain", "version", "kind", "plan_sha256", "outcome", "registry_resolution",
           "registry_checkpoint", "resolved_at"}
_CASH_TYPES = {"OPERATION_TYPE_INPUT", "OPERATION_TYPE_OUTPUT", "OPERATION_TYPE_BROKER_FEE"}


class SelectedSyncError(RuntimeError):
    """A finite refusal; a committed monetary prefix is not silently undone."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise SelectedSyncError("SELECTED_SYNC_" + code)


def _point(fault: Callable[[str], None] | None, point: str) -> None:
    if fault is not None:
        fault(point)


def _cp(value: registry.BindingCheckpoint) -> dict[str, Any]:
    return {"root_sha256": value.root_sha256, "sequence": value.sequence,
            "event_head_sha256": value.event_head_sha256}


def _dir(root: Path, kind: str) -> Path:
    path = root / "selected_sync" / kind
    _safe_path(path)
    return path


def _load(path: Path, key: bytes, fields: set[str]) -> tuple[dict, str]:
    _safe_path(path)
    _require(path.is_file() and 0 < path.stat().st_size <= MAX_PLAN_BYTES, "PLAN_UNAVAILABLE")
    raw = path.read_bytes()
    _require(len(raw) <= MAX_PLAN_BYTES, "PLAN_TOO_LARGE")
    doc = _parse(_canonical(json.loads(raw, object_pairs_hook=_pairs)))
    _require(type(doc) is dict and set(doc) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = doc["payload"]
    _require(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
             and type(body["version"]) is int and body["version"] == 1, "SCHEMA_INVALID")
    expected = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
    _require(type(doc["hmac_sha256"]) is str and hmac.compare_digest(doc["hmac_sha256"], expected),
             "AUTHENTICATION_FAILED")
    return body, _sha(_canonical(doc))


def _save(path: Path, body: dict, key: bytes, fields: set[str]) -> str:
    _safe_path(path)
    raw = _sealed(body, key)
    _require(len(raw) <= MAX_PLAN_BYTES and not path.exists(), "PLAN_EXISTS_OR_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path)
    atomic_write_json(path, _parse(raw), retry_delays=(), jitter_fraction=0,
                      write_checksum=False, keep_last_good=False)
    actual, digest = _load(path, key, fields)
    _require(actual == body and digest == _sha(raw), "PLAN_READBACK_FAILED")
    return digest


def _held(a: Any, plan: dict, digest: str) -> Authority:
    before = Authority.from_canonical_dict(plan["before_authority"])
    held = a.cash_authority_manager._change(before, at=plan["prepared_at"], kind=HELD,
        state=State.EXACT_CASH_VERSIONED_SYNC_PENDING, pending_dispatch_proof_sha256=digest)
    _transition_pair(before, held)
    return held


def _graph(a: Any, raw: bytes) -> Any:
    return v4._validate(raw, a.cl7_ledger_store._registry, a.cl7_identity_key,
                       a.cl7_identity_key_id, cut._common(a)["account_scope_sha256"])


def _plan_data(a: Any, p: dict, digest: str) -> tuple[dict, Any, Any, dict]:
    versions._hash(digest)
    plan, actual = _load(_dir(Path(p["target_root"]), "plans") / (digest + ".json"),
                         a.cl7_identity_key, _PLAN)
    _require(actual == digest and plan["kind"] == "PREPARED"
             and plan["selection_sha256"] == _sha(_sealed(p, a.cl7_identity_key))
             and plan["target_root"] == p["target_root"]
             and type(plan["sequence"]) is int and 1 <= plan["sequence"] <= MAX_ROUNDS,
             "PLAN_IDENTITY_INVALID")
    versions._time(plan["prepared_at"])
    before_raw, after_raw = (plan[k].encode("ascii") for k in ("before_export_ascii", "after_export_ascii"))
    before, after = _graph(a, before_raw), _graph(a, after_raw)
    expected = deepcopy(before.data)
    _require(len(after.data["batches"]) == len(before.data["batches"]) + 1, "BATCH_COUNT_INVALID")
    expected["batches"].append(after.data["batches"][-1])
    _require(_canonical(expected) == after_raw, "BATCH_PREFIX_INVALID")
    latest = after.record_bodies[-1]
    # Position/ownership changes require a separate receipt-aware maintenance
    # gate. This selected-source path accepts only non-trading cash operations.
    _, rows, _ = v4._decode_capture(latest["capture_json_ascii"].encode("ascii"),
                                   a.cl7_identity_key, a.cl7_identity_key_id, cut._common(a)["account_scope_sha256"])
    new_ids = {entry["provider_operation_sha256"] for entry in latest["entries"]}
    _require(all(row["type"] in _CASH_TYPES for row in rows
                 if v4._stable_id(row, a.cl7_identity_key, cut._common(a)["account_scope_sha256"]) in new_ids),
             "POSITION_OPERATION_REQUIRES_OWNER_REFRESH")
    _require(plan["prepared_at"] == latest["recorded_at"], "PLAN_TIME_INVALID")
    old_cp = registry.BindingCheckpoint(**plan["before_checkpoint"])
    event_raw = plan["registry_prepared_ascii"].encode("ascii")
    event = registry._unseal(event_raw, a.cl7_identity_key, registry._EVENT_FIELDS)
    _require(event["kind"] == "PREPARED" and event["prepared_sha256"] is None
             and event["sequence"] == old_cp.sequence + 1
             and event["parent_sha256"] == old_cp.event_head_sha256
             and event["before_pins"] == before.snapshot().pins.to_dict()
             and event["after_pins"] == after.snapshot().pins.to_dict()
             and event["batch"] == after.data["batches"][-1], "REGISTRY_PLAN_INVALID")
    before_a = Authority.from_canonical_dict(plan["before_authority"])
    _require(before_a.state is State.EXACT_CASH_VERSIONED_DISARMED
             and before_a.ledger_head_sha256 == before.snapshot().pins.ledger_head_sha256
             and before_a.ledger_revision == before.snapshot().pins.ledger_revision
             and before_a.operations_complete_through == before.covered_until, "BEFORE_AUTHORITY_INVALID")
    _held(a, plan, digest)
    return plan, before, after, event


def _registry_cp(plan: dict, event: dict, kind: str, key: bytes) -> registry.BindingCheckpoint:
    old = registry.BindingCheckpoint(**plan["before_checkpoint"])
    prepared_sha = _sha(plan["registry_prepared_ascii"].encode("ascii"))
    if kind == "BEFORE":
        return old
    if kind == "PREPARED":
        return registry.BindingCheckpoint(old.root_sha256, old.sequence + 1, prepared_sha)
    _require(kind in {"COMMITTED", "ABORTED"}, "RESOLUTION_KIND_INVALID")
    resolution = {**event, "sequence": old.sequence + 2, "parent_sha256": prepared_sha,
                  "kind": kind, "prepared_sha256": prepared_sha, "batch": None}
    return registry.BindingCheckpoint(old.root_sha256, old.sequence + 2, _sha(_sealed(resolution, key)))


def _after(a: Any, plan: dict, plan_sha: str, result: dict, digest: str, before: Any, after: Any,
           event: dict) -> tuple[Authority, registry.BindingCheckpoint, v4.OperationalPins, bytes]:
    _require(result["kind"] == "RESOLVED" and result["plan_sha256"] == plan_sha
             and result["outcome"] in {"COMMITTED", "ABORTED"}, "RESULT_INVALID")
    kind = result["registry_resolution"]
    _require(kind == "COMMITTED" if result["outcome"] == "COMMITTED" else kind in {"ABORTED", "BEFORE"},
             "RESULT_REGISTRY_INVALID")
    cp = _registry_cp(plan, event, kind, a.cl7_identity_key)
    _require(result["registry_checkpoint"] == _cp(cp), "RESULT_CHECKPOINT_INVALID")
    versions._time(result["resolved_at"])
    g = after if result["outcome"] == "COMMITTED" else before
    pins = g.snapshot().pins
    held = _held(a, plan, plan_sha)
    kwargs = dict(state=State.EXACT_CASH_VERSIONED_DISARMED,
                  pending_dispatch_proof_sha256=None, activation_context_sha256=digest)
    if result["outcome"] == "COMMITTED":
        kwargs.update(ledger_head_sha256=pins.ledger_head_sha256, ledger_revision=pins.ledger_revision,
                      operations_complete_through=g.covered_until)
    record = a.cash_authority_manager._change(held, at=result["resolved_at"],
        kind=COMMITTED if result["outcome"] == "COMMITTED" else ABORTED, **kwargs)
    _transition_pair(held, record)
    return record, cp, pins, _canonical(g.data)


def _lineage(a: Any, p: dict, selection: str, record: Authority, *, seen: frozenset[str] = frozenset()
             ) -> tuple[registry.BindingCheckpoint, v4.OperationalPins, int, bytes | None]:
    _require(record.state is State.EXACT_CASH_VERSIONED_DISARMED and len(seen) <= MAX_ROUNDS,
             "NOT_SELECTED_OR_CHAIN_LIMIT")
    digest = record.activation_context_sha256
    _require(type(digest) is str and digest not in seen, "CHAIN_CYCLE")
    if digest == selection:
        commit = cut._commit_plan(a, p, selection)
        _require(commit is not None and record.to_canonical_dict() == commit["after"], "SELECTION_CHANGED")
        return registry.BindingCheckpoint(**p["registry_checkpoint"]), v4.OperationalPins(**p["candidate_pins"]), 0, None
    if record.transition_kind == "VERSIONED_FULL_FILL_CLOSED_DISARMED":
        from .versioned_fill_closure import closure_lineage
        return closure_lineage(a, p, selection, record, seen=seen)
    if record.transition_kind == "VERSIONED_ORDER_ADMISSION_COMMITTED":
        from .versioned_risk_admission import admission_lineage
        return admission_lineage(a, p, selection, record, seen=seen)
    if record.transition_kind == "VERSIONED_CASH_FLOW_RESYNC_COMMITTED":
        from .versioned_risk_resync import resync_lineage
        return resync_lineage(a, p, selection, record, seen=seen)
    if record.transition_kind == "VERSIONED_OWNER_REFRESH_COMMITTED":
        from .versioned_owner_refresh import owner_lineage
        return owner_lineage(a, p, selection, record, seen=seen)
    versions._hash(digest)
    result, found = _load(_dir(Path(p["target_root"]), "results") / (digest + ".json"), a.cl7_identity_key, _RESULT)
    _require(found == digest, "RESULT_HASH_INVALID")
    plan_sha = versions._hash(result["plan_sha256"])
    plan, before, after, event = _plan_data(a, p, plan_sha)
    cp0, pins0, count, _ = _lineage(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]),
                                   seen=seen | {digest})
    _require(plan["before_checkpoint"] == _cp(cp0) and before.snapshot().pins == pins0
             and plan["sequence"] == count + 1, "CHAIN_PREFIX_INVALID")
    expected, cp, pins, raw = _after(a, plan, plan_sha, result, digest, before, after, event)
    _require(record == expected, "AUTHORITY_RESULT_MISMATCH")
    return cp, pins, count + 1, raw


@contextmanager
def _open(a: Any, p: dict, cp: registry.BindingCheckpoint, *, fault=None) -> Iterator[Any]:
    source = v4.VersionedOperationalStore.open(Path(p["target_root"]) / "ledger", **cut._common(a))
    try:
        if fault is not None:
            source._injector = lambda point: fault("source." + point)
        _require(registry._source_identity(source) == p["target_identity"], "SOURCE_IDENTITY_CHANGED")
        binding = registry.DurableSourceBinding.open(Path(p["target_root"]) / "pins", source=source,
            checkpoint=cp, runtime_scope_sha256=p["runtime_scope_sha256"],
            owner_binding_sha256=p["owner_binding_sha256"], fault_injector=fault)
        yield binding
    finally:
        source.close()


def _verify_rows(binding: Any, state: Any) -> None:
    db = cl2._validate_live_root(binding.root)
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=0)
    try:
        conn.execute("BEGIN")
        binding._same_rows(conn, state)
    finally:
        conn.close()


def _owner_anchor(a: Any, p: dict, selection: str, record: Authority,
                  *, seen: frozenset[str] = frozenset()) -> dict:
    """Derive P/R/C only from an authenticated selection/sync/refresh chain."""
    _require(len(seen) <= MAX_ROUNDS and record.sha256 not in seen, "OWNER_CHAIN_LIMIT")
    seen = seen | {record.sha256}
    if record.state is State.EXACT_CASH_VERSIONED_SYNC_PENDING:
        plan, digest = _load(_dir(Path(p["target_root"]), "plans") / (record.pending_dispatch_proof_sha256 + ".json"),
                             a.cl7_identity_key, _PLAN)
        _require(digest == record.pending_dispatch_proof_sha256 and plan["selection_sha256"] == selection,
                 "HOLD_PLAN_MISMATCH")
        _require(record == _held(a, plan, record.pending_dispatch_proof_sha256), "HOLD_MISMATCH")
        return _owner_anchor(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]), seen=seen)
    _require(record.state is State.EXACT_CASH_VERSIONED_DISARMED, "NOT_SELECTED")
    if record.activation_context_sha256 == selection:
        commit = cut._commit_plan(a, p, selection)
        _require(commit is not None and record.to_canonical_dict() == commit["after"], "SELECTION_CHANGED")
        return {k: p["owners_before"][k] for k in ("portfolio", "risk", "central")}
    if record.transition_kind == "VERSIONED_FULL_FILL_CLOSED_DISARMED":
        from .versioned_fill_closure import committed_owners
        return committed_owners(a, p, selection, record, seen=seen)
    if record.transition_kind == "VERSIONED_ORDER_ADMISSION_COMMITTED":
        from .versioned_risk_admission import committed_owners
        return committed_owners(a, p, selection, record, seen=seen)
    if record.transition_kind == "VERSIONED_CASH_FLOW_RESYNC_COMMITTED":
        from .versioned_risk_resync import committed_owners
        return committed_owners(a, p, selection, record, seen=seen)
    if record.transition_kind == "VERSIONED_OWNER_REFRESH_COMMITTED":
        from .versioned_owner_refresh import committed_owners
        return committed_owners(a, p, selection, record, seen=seen)
    result, _ = _load(_dir(Path(p["target_root"]), "results") / (record.activation_context_sha256 + ".json"),
                      a.cl7_identity_key, _RESULT)
    plan, digest = _load(_dir(Path(p["target_root"]), "plans") / (result["plan_sha256"] + ".json"),
                        a.cl7_identity_key, _PLAN)
    _require(digest == result["plan_sha256"] and plan["selection_sha256"] == selection
             and plan["target_root"] == p["target_root"], "OWNER_PLAN_INVALID")
    held = _held(a, plan, digest)
    _transition_pair(held, record)
    _require(record.activation_context_sha256 == _sha(_sealed(result, a.cl7_identity_key)), "RESULT_MISMATCH")
    return _owner_anchor(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]), seen=seen)


def _load_current_prepared(a: Any, r: Any, root: Path, selection: str) -> dict:
    p = cut._load_prepared(a, r, root, selection, _check_initial_owners=False)
    current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
    anchor = _owner_anchor(a, p, selection, current)
    observed = cut._owners(a, r)
    _require(all(observed[k] == anchor[k] for k in anchor), "OWNERS_CHANGED")
    return p


def _unchanged(a: Any, r: Any, p: dict, selection: str) -> None:
    _require(_load_current_prepared(a, r, Path(p["target_root"]), selection) == p, "OWNERS_OR_PLAN_CHANGED")


@dataclass(frozen=True, slots=True)
class SelectedSyncResult:
    selection_sha256: str
    sync_plan_sha256: str | None
    outcome: str
    authority_sha256: str
    pins: v4.OperationalPins
    registry_checkpoint: registry.BindingCheckpoint
    replay: bool

    def public_summary(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_RESULT", "selection_sha256": self.selection_sha256,
                "sync_plan_sha256": self.sync_plan_sha256, "outcome": self.outcome,
                "authority_sha256": self.authority_sha256, "source_export_sha256": self.pins.export_sha256,
                "replay": self.replay, "runtime_authority_granted": False,
                "portfolio_refreshed": False, "risk_execution_written": False,
                "automatic_rearm_allowed": False, "trading_cutover_complete": False}


def _finish(a: Any, r: Any, p: dict, selection: str, plan_sha: str, binding: Any,
            *, timely: Callable[[], None], fault=None) -> SelectedSyncResult:
    # Caller holds authority and the full old-owner lock group. Recovery uses
    # exact persisted exports, not a fresh provider claim or a cash write.
    plan, before, after, event = _plan_data(a, p, plan_sha)
    before_a = Authority.from_canonical_dict(plan["before_authority"])
    cp0, pins0, count, _ = _lineage(a, p, selection, before_a)
    _require(cp0 == registry.BindingCheckpoint(**plan["before_checkpoint"])
             and pins0 == before.snapshot().pins and count + 1 == plan["sequence"], "PLAN_PARENT_INVALID")
    held = _held(a, plan, plan_sha)
    current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
    # Lost return after a durable final authority commit: verify, do not write.
    if current.state is State.EXACT_CASH_VERSIONED_DISARMED and current != before_a:
        cp, pins, _, _ = _lineage(a, p, selection, current)
        result, _ = _load(_dir(Path(p["target_root"]), "results") / (current.activation_context_sha256 + ".json"),
                          a.cl7_identity_key, _RESULT)
        _require(result["plan_sha256"] == plan_sha, "DIFFERENT_COMPLETED_PLAN")
        with binding._guard():
            state = binding._load()
            _require(state.pending is None and state.checkpoint == cp and state.pins == pins, "REGISTRY_DIVERGED")
            with binding.source.locked_snapshot(expected_pins=pins) as view:
                _require(view.export_bytes() == state.raw, "SOURCE_DIVERGED")
                _unchanged(a, r, p, selection); timely(); _verify_rows(binding, state); view.assert_active()
        return SelectedSyncResult(selection, plan_sha, result["outcome"], current.sha256, pins, cp, True)
    _require(current in (before_a, held), "AUTHORITY_PREFIX_INVALID")
    with binding._guard():
        state = binding._load()
        cp_prepared = _registry_cp(plan, event, "PREPARED", a.cl7_identity_key)
        cp_committed = _registry_cp(plan, event, "COMMITTED", a.cl7_identity_key)
        cp_aborted = _registry_cp(plan, event, "ABORTED", a.cl7_identity_key)
        if state.checkpoint == cp_prepared:
            _require(current == held and state.pending == event, "UNHELD_REGISTRY_PREPARED")
            kind, state = binding._resolve(state, allow_abort=True)
        elif state.checkpoint == cp0:
            _require(state.pending is None and state.pins == pins0, "REGISTRY_PREFIX_INVALID")
            kind = "BEFORE"
        elif state.checkpoint in (cp_committed, cp_aborted):
            _require(current == held and state.pending is None, "REGISTRY_PREFIX_INVALID")
            kind = "COMMITTED" if state.checkpoint == cp_committed else "ABORTED"
        else:
            raise SelectedSyncError("SELECTED_SYNC_UNPLANNED_REGISTRY_TRANSITION")
        outcome = "COMMITTED" if kind == "COMMITTED" else "ABORTED"
        g = after if outcome == "COMMITTED" else before
        pins = g.snapshot().pins
        _require(state.pins == pins and state.raw == _canonical(g.data), "REGISTRY_RESULT_INVALID")
        with binding.source.locked_snapshot(expected_pins=pins) as view:
            _require(view.export_bytes() == state.raw, "SOURCE_DIVERGED")
            _unchanged(a, r, p, selection); timely(); _verify_rows(binding, state); view.assert_active()
            if current == before_a:
                # An orphan plan before HOLD cannot have caused any source write.
                _require(kind == "BEFORE", "UNHELD_SOURCE_WRITE")
                current = a.cash_authority_manager.store._commit_unlocked(held,
                    expected_revision=current.record_revision, expected_sha256=current.sha256)
            resolution_path = _dir(Path(p["target_root"]), "resolutions") / (plan_sha + ".json")
            if resolution_path.exists():
                result, digest = _load(resolution_path, a.cl7_identity_key, _RESULT)
                _require(result["outcome"] == outcome and result["registry_resolution"] == kind,
                         "RESOLUTION_CONFLICT")
            else:
                result = {"domain": DOMAIN, "version": 1, "kind": "RESOLVED", "plan_sha256": plan_sha,
                    "outcome": outcome, "registry_resolution": kind, "registry_checkpoint": _cp(state.checkpoint),
                    "resolved_at": a.cl7_clock()}
                digest = _save(resolution_path, result, a.cl7_identity_key, _RESULT)
            expected, cp, _, _ = _after(a, plan, plan_sha, result, digest, before, after, event)
            index_path = _dir(Path(p["target_root"]), "results") / (digest + ".json")
            if index_path.exists():
                _require(_load(index_path, a.cl7_identity_key, _RESULT) == (result, digest), "RESULT_INDEX_CHANGED")
            else:
                _save(index_path, result, a.cl7_identity_key, _RESULT)
            _point(fault, "finish.after_result")
            _unchanged(a, r, p, selection); timely(); _verify_rows(binding, state); view.assert_active()
            a_record = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
            _require(a_record == held, "AUTHORITY_CHANGED")
            r.manager.journal.record(JournalEvent(category="versioned_sync", event_type="VERSIONED_SELECTED_SYNC_VERIFIED",
                mode="SANDBOX_EXECUTION", status=outcome, timestamp_utc=a.cl7_clock(), payload={
                    "plan_sha256": plan_sha, "resolution_sha256": digest,
                    "source_export_sha256": pins.export_sha256, "runtime_authority_granted": False}))
            _point(fault, "finish.after_audit")
            _unchanged(a, r, p, selection); timely(); _verify_rows(binding, state); view.assert_active()
            a.cash_authority_manager.store._commit_unlocked(expected,
                expected_revision=held.record_revision, expected_sha256=held.sha256)
            _point(fault, "finish.after_authority")
            return SelectedSyncResult(selection, plan_sha, outcome, expected.sha256, pins, cp, False)


def sync_selected_cash(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
        expected_authority_sha256: str, from_inclusive: str | None = None,
        fault_injector: Callable[[str], None] | None = None) -> SelectedSyncResult:
    """Collect and sync cash-only operations, then adopt exactly that source head.

    This explicit mutating API never refreshes P/R/C, records a Risk execution,
    or arms. Incomplete provider evidence before PREPARED leaves owners unchanged.
    A durable plan/HOLD requires recover_selected_sync, not a blind retry.
    """
    root, r = cut._path(target_root), recovery
    versions._hash(expected_authority_sha256)
    with a.cash_authority_manager.store.locked():
        p = _load_current_prepared(a, r, root, expected_selection_sha256)
        central = a.manager.state()
        _require(not central.queued and central.blocking_intent is None, "ADMITTED_ORDER_REQUIRES_DISPATCH_RECOVERY")
        current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        _require(current.sha256 == expected_authority_sha256, "AUTHORITY_CAS_CONFLICT")
        cp, pins, count, _ = _lineage(a, p, expected_selection_sha256, current)
        _require(count < MAX_ROUNDS, "HISTORY_CAPACITY_EXCEEDED")
        with _open(a, p, cp, fault=fault_injector) as binding:
            state = binding._load()
            _require(state.checkpoint == cp and state.pins == pins and state.pending is None, "REGISTRY_CHANGED")
            raw = binding.source.export_bytes()
            g = _graph(a, raw)
            _require(raw == state.raw and g.snapshot().pins == pins, "SOURCE_CHANGED")
            wall_start, tick_start = timestamp_ns(a.cl7_clock()), a.cl7_monotonic_ns()
            _require(type(tick_start) is int and tick_start >= 0, "CLOCK_INVALID")
            end = a.cl7_clock()
            start = g.first_from if from_inclusive is None else from_inclusive
            versions._time(start)
            requests: list[Any] = []; responses: list[Any] = []

            def timely() -> None:
                tick, wall = a.cl7_monotonic_ns(), timestamp_ns(a.cl7_clock())
                _require(type(tick) is int and 0 <= tick - tick_start <= MAX_AGE_NS
                         and 0 <= wall - wall_start <= MAX_AGE_NS, "STALE")

            def transport(payload: Any, timeout: int) -> Any:
                timely(); requests.append(deepcopy(payload))
                response = a.transport.get_operations_by_cursor_once(payload, timeout)
                responses.append(deepcopy(response)); timely()
                return response

            request = cl3.BrokerReadRequest(cl3.BrokerEnvironment.SANDBOX, a.policy.account_id,
                a.cl7_identity_key, a.cl7_identity_key_id, start, end, 128, 4, 128,
                tick_start + MAX_AGE_NS, cl3.RetryPolicy(1, MAX_AGE_NS, ()), transport,
                a.cl7_monotonic_ns, a.cl7_wait_ns)
            cl3.collect_tbank_operations(request)
            cash = deepcopy(r.manager.api.get_positions(a.policy.account_id)); timely()
            recorded_at = a.cl7_clock()
            index = 0

            def cached(payload: Any, _: int) -> Any:
                nonlocal index
                timely()
                _require(index < len(requests) and payload == requests[index], "CAPTURE_REQUEST_CHANGED")
                result = deepcopy(responses[index]); index += 1
                return result

            def cached_cash(account: str) -> Any:
                _require(account == a.policy.account_id and index == len(requests), "CAPTURE_INCOMPLETE")
                timely(); return deepcopy(cash)

            planned: list[str] = []
            with _owner_locks(a, r, ledger=True):
                _unchanged(a, r, p, expected_selection_sha256); timely()
                _require(a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == current,
                         "AUTHORITY_CHANGED")

                def prepare(before_raw: bytes, after_raw: bytes, event_raw: bytes) -> None:
                    timely(); _unchanged(a, r, p, expected_selection_sha256)
                    _require(not planned and before_raw == raw, "PREPARE_SOURCE_CHANGED")
                    plan = {"domain": DOMAIN, "version": 1, "kind": "PREPARED",
                        "selection_sha256": expected_selection_sha256, "target_root": str(root.resolve()),
                        "sequence": count + 1, "prepared_at": recorded_at,
                        "before_authority": current.to_canonical_dict(), "before_checkpoint": _cp(cp),
                        "before_export_ascii": before_raw.decode("ascii"), "after_export_ascii": after_raw.decode("ascii"),
                        "registry_prepared_ascii": event_raw.decode("ascii")}
                    # Evaluate the cash-only profile BEFORE saving the outer plan.
                    ag = _graph(a, after_raw)
                    last = ag.record_bodies[-1]
                    _, rows, _ = v4._decode_capture(last["capture_json_ascii"].encode("ascii"),
                        a.cl7_identity_key, a.cl7_identity_key_id, binding.source._account)
                    ids = {e["provider_operation_sha256"] for e in last["entries"]}
                    _require(all(row["type"] in _CASH_TYPES for row in rows if
                        v4._stable_id(row, a.cl7_identity_key, binding.source._account) in ids),
                        "POSITION_OPERATION_REQUIRES_OWNER_REFRESH")
                    digest = _sha(_sealed(plan, a.cl7_identity_key))
                    _save(_dir(root, "plans") / (digest + ".json"), plan, a.cl7_identity_key, _PLAN)
                    planned.append(digest); _point(fault_injector, "sync.after_plan")
                    timely(); _unchanged(a, r, p, expected_selection_sha256)
                    held = _held(a, plan, digest)
                    a.cash_authority_manager.store._commit_unlocked(held,
                        expected_revision=current.record_revision, expected_sha256=current.sha256)
                    _point(fault_injector, "sync.after_hold"); timely()

                result, final = binding.sync_tbank_operations(replace(request, transport=cached),
                    expected_checkpoint=cp, recorded_at=recorded_at, read_rub_positions=cached_cash,
                    prepare_selected=prepare)
                _point(fault_injector, "sync.after_registry_commit")
                if not planned:
                    _require(result.replay and final.pins == pins and final.checkpoint == cp, "REPLAY_CHANGED")
                    _unchanged(a, r, p, expected_selection_sha256); timely()
                    return SelectedSyncResult(expected_selection_sha256, None, "NO_CHANGE", current.sha256, pins, cp, True)
                return _finish(a, r, p, expected_selection_sha256, planned[0], binding,
                               timely=timely, fault=fault_injector)


def recover_selected_sync(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
        expected_sync_plan_sha256: str, fault_injector: Callable[[str], None] | None = None) -> SelectedSyncResult:
    """Offline bookkeeping only: no provider call, monetary write or re-POST."""
    root, r = cut._path(target_root), recovery
    with a.cash_authority_manager.store.locked(), _owner_locks(a, r, ledger=True):
        p = _load_current_prepared(a, r, root, expected_selection_sha256)
        plan, _, _, _ = _plan_data(a, p, expected_sync_plan_sha256)
        with _open(a, p, registry.BindingCheckpoint(**plan["before_checkpoint"]), fault=fault_injector) as binding:
            return _finish(a, r, p, expected_selection_sha256, expected_sync_plan_sha256, binding,
                           timely=lambda: None, fault=fault_injector)


@contextmanager
def locked_current_selected_source(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_authority_sha256: str) -> Iterator[tuple[Any, Any]]:
    """Resolve initial or synced selection; unresolved plans cannot yield a view."""
    versions._hash(expected_authority_sha256)
    root, r = cut._path(target_root), recovery
    with a.cash_authority_manager.store.locked(), _owner_locks(a, r, ledger=True):
        p = _load_current_prepared(a, r, root, expected_selection_sha256)
        current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        _require(current.sha256 == expected_authority_sha256, "AUTHORITY_CAS_CONFLICT")
        cp, pins, _, _ = _lineage(a, p, expected_selection_sha256, current)
        with _open(a, p, cp) as binding:
            state = binding.snapshot()
            _require(state.checkpoint == cp and state.pins == pins and state.pending_plan_sha256 is None,
                     "UNADOPTED_REGISTRY")
            with binding.locked_binding() as (adapter, view):
                yield adapter, view
                view.assert_active(); _unchanged(a, r, p, expected_selection_sha256)
                _require(a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == current,
                         "AUTHORITY_CHANGED")
