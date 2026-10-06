"""Request-bound v4 FULL-FILL cash/registry substep. Financial owners stay pending.

The dispatch attempt must already be durable. A plan pins exact source before/
after and registry event before either database writes. Recovery advances pins,
not money; a noncommitted attempt can be aborted and recaptured in a new linked
round. No Portfolio/Risk/Central/authority mutation, POST, cancel or re-arm.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import broker_read_adapters as cl3
from . import cash_observation_versions as versions
from . import versioned_dispatch as dispatch
from . import versioned_fill_evidence as evidence
from . import versioned_operational_store as v4
from . import versioned_runtime_cutover as cut
from . import versioned_selected_sync as sync
from . import versioned_source_binding as registry
from .central_order_manager import CentralOrderState
from .exact_cash_settlement import _pairs, _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha

DOMAIN = "V4_DISPATCH_FILL_CASH_V1"
MAX_ROUNDS = 8
MAX_PLAN_BYTES = 28 * 1024 * 1024
PLAN_FIELDS = {"domain", "version", "kind", "round", "previous_result_sha256",
    "dispatch_plan_sha256", "selection_sha256", "target_root", "authority", "central_before",
    "started_at", "recorded_at", "before_checkpoint", "before_export_ascii",
    "after_export_ascii", "registry_prepared_ascii"}
RESULT_FIELDS = {"domain", "version", "kind", "plan_sha256", "outcome", "registry_resolution",
    "registry_checkpoint", "source_pins", "resolved_at"}


class VersionedFillCashError(RuntimeError):
    """Finite reason only; persisted captures and owners contain private data."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise VersionedFillCashError("V4_FILL_CASH_" + reason)


def _point(callback: Callable | None, point: str) -> None:
    if callback is not None:
        callback(point)


def _folder(root: Path, dispatch_sha: str) -> Path:
    p = root / "versioned_fill_cash" / versions._hash(dispatch_sha)
    _safe_path(p)
    return p


def _read(path: Path, key: bytes, fields: set[str]) -> tuple[dict, str]:
    _safe_path(path)
    _need(path.is_file() and 0 < path.stat().st_size <= MAX_PLAN_BYTES, "PLAN_UNAVAILABLE")
    raw = path.read_bytes()
    _need(0 < len(raw) <= MAX_PLAN_BYTES, "PLAN_UNAVAILABLE")
    value = json.loads(raw, object_pairs_hook=_pairs)
    _need(type(value) is dict and set(value) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = value["payload"]
    _need(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
          and type(body["version"]) is int and body["version"] == 1, "SCHEMA_INVALID")
    sig = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
    _need(type(value["hmac_sha256"]) is str and hmac.compare_digest(sig, value["hmac_sha256"]),
          "SIGNATURE_INVALID")
    return body, _sha(_canonical(value))


def _save(path: Path, body: dict, key: bytes, fields: set[str]) -> str:
    _safe_path(path)
    _need(not path.exists(), "RECORD_ALREADY_EXISTS")
    raw = _sealed(body, key)
    _need(len(raw) <= MAX_PLAN_BYTES, "PLAN_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(path, _parse(raw), retry_delays=(), jitter_fraction=0,
                      write_checksum=False, keep_last_good=False)
    _need(_read(path, key, fields) == (body, _sha(raw)), "READBACK_FAILED")
    return _sha(raw)


def _load_dispatch(a: Any, r: Any, root: Path, selection: str, dispatch_sha: str):
    dp, digest = dispatch._read(dispatch._path(root, "plans", dispatch_sha),
                                a.cl7_identity_key, dispatch.PLAN_FIELDS)
    _need(digest == dispatch_sha and dp["kind"] == "DISPATCH" and dp["selection_sha256"] == selection
          and dp["target_root"] == str(root), "DISPATCH_IDENTITY_INVALID")
    arm, p, ap, owners = dispatch._load_arm(a, r, root, selection, dp["arm_sha256"])
    pending = dispatch._pending(a, arm, dp["arm_sha256"], dp, digest)
    _need(a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == pending,
          "CONFIRMED_PENDING_ATTEMPT_REQUIRED")
    before = CentralOrderState.from_dict(owners["central"])
    _need(dp["central_before"] == before.to_dict() and dp["request"] == dispatch._request(before.queued[0]),
          "DISPATCH_REQUEST_INVALID")
    current = a.manager.state()
    dispatch._central_prefix(before, current, digest, dp["attempt_at"])
    intent = next(i for i in current.intents if i.intent_id == before.queued[0].intent_id)
    _need(intent.status in {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}, "INFLIGHT_IDENTITY_REQUIRED")
    dispatch._check_owners(a, r, p, owners, pending, current)
    return dp, arm, p, ap, owners, pending, current, intent


def _validate_plan(a: Any, ctx: tuple, plan: dict, digest: str, previous: str | None,
                   cp: registry.BindingCheckpoint, number: int):
    dp, arm, p, ap, owners, pending, central, intent = ctx
    _need(plan["kind"] == "PREPARED" and plan["round"] == number
          and type(plan["round"]) is int and plan["previous_result_sha256"] == previous
          and plan["dispatch_plan_sha256"] == pending.pending_dispatch_proof_sha256
          and plan["selection_sha256"] == dp["selection_sha256"] and plan["target_root"] == p["target_root"]
          and plan["authority"] == pending.to_canonical_dict() and plan["central_before"] == central.to_dict()
          and plan["before_checkpoint"] == sync._cp(cp), "PLAN_BINDING_CHANGED")
    raw_before = plan["before_export_ascii"].encode("ascii")
    raw_after = plan["after_export_ascii"].encode("ascii")
    before, after = sync._graph(a, raw_before), sync._graph(a, raw_after)
    _need(before.snapshot().pins == v4.OperationalPins(**ap["pins"]), "BASE_PINS_CHANGED")
    appended = deepcopy(before.data)
    _need(len(after.data["batches"]) == len(before.data["batches"]) + 1, "BATCH_COUNT_CHANGED")
    appended["batches"].append(after.data["batches"][-1])
    _need(_canonical(appended) == raw_after, "CASH_PREFIX_CHANGED")
    latest = after.record_bodies[-1]
    capture = _parse(latest["capture_json_ascii"])
    bound = capture.get("bound_full_fill")
    _need(type(bound) is dict and bound["dispatch_plan_sha256"] == plan["dispatch_plan_sha256"]
          and bound["intent"] == intent.to_dict() and bound["proof_evaluated_at"] == dp["evaluated_at"]
          and bound["own_funds"] == dp["request_binding"]["payload"]["own_funds"], "FILL_BINDING_CHANGED")
    instrument = a.cl7_own_funds_policy.instruments[intent.candidate.instrument_id]
    metadata = {"instrument_id": intent.candidate.instrument_id, "currency": instrument.currency,
                "asset_class": instrument.asset_class, "lot_size": instrument.lot_size}
    _need(bound["instrument"] == metadata, "METADATA_CHANGED")
    _, _, receipt = evidence.decode_bound_fill(bound, key=a.cl7_identity_key,
        key_id=a.cl7_identity_key_id, account_scope=cut._common(a)["account_scope_sha256"])
    _need(latest["cash_delta_nano"] == str(receipt.expected_cash_delta_nano)
          and latest["recorded_at"] == plan["recorded_at"]
          and timestamp_ns(plan["started_at"]) <= timestamp_ns(bound["observed_at"])
          <= timestamp_ns(plan["recorded_at"]) <= timestamp_ns(plan["started_at"]) + MAX_AGE_NS,
          "PLAN_ECONOMICS_OR_TIME_INVALID")
    event = registry._unseal(plan["registry_prepared_ascii"].encode("ascii"), a.cl7_identity_key,
                             registry._EVENT_FIELDS)
    _need(event["kind"] == "PREPARED" and event["prepared_sha256"] is None
          and event["sequence"] == cp.sequence + 1 and event["parent_sha256"] == cp.event_head_sha256
          and event["before_pins"] == before.snapshot().pins.to_dict()
          and event["after_pins"] == after.snapshot().pins.to_dict()
          and event["batch"] == after.data["batches"][-1], "REGISTRY_PLAN_CHANGED")
    return before, after, event, receipt


def _check_result(a: Any, plan: dict, digest: str, result: dict, before: Any, after: Any, event: dict):
    kind = result["registry_resolution"]
    _need(result["kind"] == "RESOLVED" and result["plan_sha256"] == digest
          and kind in {"BEFORE", "ABORTED", "COMMITTED"}
          and result["outcome"] == ("COMMITTED" if kind == "COMMITTED" else "ABORTED"),
          "RESULT_INVALID")
    cp = sync._registry_cp(plan, event, kind, a.cl7_identity_key)
    graph = after if kind == "COMMITTED" else before
    _need(result["registry_checkpoint"] == sync._cp(cp)
          and result["source_pins"] == graph.snapshot().pins.to_dict(), "RESULT_PINS_CHANGED")
    _need(timestamp_ns(result["resolved_at"]) >= timestamp_ns(plan["recorded_at"]), "RESULT_TIME_INVALID")
    return cp, graph


def _history(a: Any, ctx: tuple):
    dp, arm, p, ap, owners, pending, central, intent = ctx
    folder = _folder(Path(p["target_root"]), pending.pending_dispatch_proof_sha256)
    cp = registry.BindingCheckpoint(**ap["checkpoint"])
    previous = None
    records = []
    dirs = sorted(folder.iterdir()) if folder.exists() else []
    _need(len(dirs) <= MAX_ROUNDS, "ROUND_LIMIT")
    for n, path in enumerate(dirs, 1):
        _safe_path(path)
        _need(path.is_dir() and path.name == f"{n:04d}" and {f.name for f in path.iterdir()}
              <= {"plan.json", "result.json"}, "ROUND_CHAIN_INVALID")
        plan, digest = _read(path / "plan.json", a.cl7_identity_key, PLAN_FIELDS)
        before, after, event, receipt = _validate_plan(a, ctx, plan, digest, previous, cp, n)
        result = None
        records.append((path, plan, digest, before, after, event, receipt))
        if (path / "result.json").exists():
            result, previous = _read(path / "result.json", a.cl7_identity_key, RESULT_FIELDS)
            cp, graph = _check_result(a, plan, digest, result, before, after, event)
            _need(result["outcome"] != "COMMITTED" or n == len(dirs), "ROUND_AFTER_COMMIT")
        else:
            _need(n == len(dirs), "UNRESOLVED_PRIOR_ROUND")
        records[-1] += (result,)
    return cp, previous, records


@dataclass(frozen=True, slots=True)
class VersionedFillCashResult:
    dispatch_plan_sha256: str
    cash_plan_sha256: str
    outcome: str
    source_pins: v4.OperationalPins
    registry_checkpoint: registry.BindingCheckpoint
    gross_nano: int
    commission_nano: int
    cash_delta_nano: int
    transaction_sha256s: tuple[str, ...]
    appended_transactions: int
    replay: bool

    def public_summary(self) -> dict:
        return {"domain": DOMAIN + "_RESULT", "dispatch_plan_sha256": self.dispatch_plan_sha256,
            "cash_plan_sha256": self.cash_plan_sha256, "outcome": self.outcome,
            "source_export_sha256": self.source_pins.export_sha256,
            "appended_transactions": self.appended_transactions, "replay": self.replay,
            "cash_components_verified": self.outcome == "COMMITTED", "settlement_verified": False,
            "authority_clear_allowed": False, "post_order_called": False, "risk_execution_written": False}


def _finish(a: Any, r: Any, ctx: tuple, record: tuple, binding: Any, *, timely: Callable,
            fault=None, appended: int = 0) -> VersionedFillCashResult:
    dp, arm, p, ap, owners, pending, central, intent = ctx
    path, plan, digest, before, after, event, receipt, existing = record
    cp0 = registry.BindingCheckpoint(**plan["before_checkpoint"])
    with binding._guard():
        state = binding._load()
        if state.checkpoint == sync._registry_cp(plan, event, "PREPARED", a.cl7_identity_key):
            _need(state.pending == event, "FOREIGN_PREPARED")
            kind, state = binding._resolve(state, allow_abort=True)
        elif state.checkpoint == cp0:
            _need(state.pending is None and state.raw == plan["before_export_ascii"].encode("ascii"),
                  "BEFORE_CHANGED")
            kind = "BEFORE"
        elif state.checkpoint == sync._registry_cp(plan, event, "COMMITTED", a.cl7_identity_key):
            kind = "COMMITTED"
        elif state.checkpoint == sync._registry_cp(plan, event, "ABORTED", a.cl7_identity_key):
            kind = "ABORTED"
        else:
            raise VersionedFillCashError("V4_FILL_CASH_UNPLANNED_REGISTRY_TRANSITION")
        graph = after if kind == "COMMITTED" else before
        pins = graph.snapshot().pins
        _need(state.pending is None and state.pins == pins and state.raw == _canonical(graph.data),
              "SOURCE_RESULT_CHANGED")
        with binding.source.locked_snapshot(expected_pins=pins) as view:
            _need(view.export_bytes() == state.raw, "SOURCE_CHANGED")
            dispatch._check_owners(a, r, p, owners, pending, central)
            timely(); sync._verify_rows(binding, state); view.assert_active()
            if existing is None:
                result = {"domain": DOMAIN, "version": 1, "kind": "RESOLVED", "plan_sha256": digest,
                    "outcome": "COMMITTED" if kind == "COMMITTED" else "ABORTED",
                    "registry_resolution": kind, "registry_checkpoint": sync._cp(state.checkpoint),
                    "source_pins": pins.to_dict(), "resolved_at": a.cl7_clock()}
                _check_result(a, plan, digest, result, before, after, event)
                r.manager.transaction_coordinator._record("VERSIONED_FILL_CASH_VERIFIED",
                    account_id=a.policy.account_id, instrument_id=intent.candidate.instrument_id,
                    mode="SANDBOX_EXECUTION", status="PENDING_OWNERS", payload={
                        "plan_sha256": digest, "source_export_sha256": pins.export_sha256,
                        "authority_clear_allowed": False})
                _point(fault, "cash.after_audit")
                dispatch._check_owners(a, r, p, owners, pending, central)
                timely(); sync._verify_rows(binding, state); view.assert_active()
                _save(path / "result.json", result, a.cl7_identity_key, RESULT_FIELDS)
                _point(fault, "cash.after_result")
            else:
                result = existing
                cp, _ = _check_result(a, plan, digest, result, before, after, event)
                _need(cp == state.checkpoint and result["registry_resolution"] == kind, "REPLAY_CHANGED")
            ids = tuple(e["transaction_sha256"] for e in after.record_bodies[-1]["entries"])
            return VersionedFillCashResult(plan["dispatch_plan_sha256"], digest, result["outcome"], pins,
                state.checkpoint, receipt.gross_nano, receipt.executed_commission_nano,
                receipt.expected_cash_delta_nano, ids if kind == "COMMITTED" else (), appended,
                existing is not None)


def recover_selected_fill_cash(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_dispatch_plan_sha256: str,
        fault_injector: Callable | None = None) -> VersionedFillCashResult:
    """Resolve exactly planned before/after. Never replay an old capture to source."""
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        ctx = _load_dispatch(a, r, root, expected_selection_sha256, expected_dispatch_plan_sha256)
        _, _, records = _history(a, ctx)
        _need(bool(records), "CASH_PLAN_REQUIRED")
        latest = records[-1]
        dispatch._historical_plan_from_export(a, r, ctx[1], ctx[3], ctx[4], ctx[0],
                                             latest[1]["before_export_ascii"].encode("ascii"))
        with _owner_locks(a, r, ledger=True), sync._open(a, ctx[2],
                registry.BindingCheckpoint(**ctx[3]["checkpoint"])) as binding:
            return _finish(a, r, ctx, latest, binding, timely=lambda: None, fault=fault_injector)


def record_selected_fill_cash(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_dispatch_plan_sha256: str,
        fault_injector: Callable | None = None) -> VersionedFillCashResult:
    """Fresh full-fill capture -> one atomic cash batch -> registry; keep pending.

    If the latest round is incomplete it is only resolved in this invocation.
    After a proven ABORTED result the NEXT invocation makes a new fresh capture.
    A committed result is locally replayed, not re-certified against today's broker.
    """
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        ctx = _load_dispatch(a, r, root, expected_selection_sha256, expected_dispatch_plan_sha256)
        dp, arm, p, ap, owners, pending, central, intent = ctx
        cp, parent, records = _history(a, ctx)
        if records and (records[-1][-1] is None or records[-1][-1]["outcome"] == "COMMITTED"):
            latest = records[-1]
            dispatch._historical_plan_from_export(a, r, arm, ap, owners, dp,
                                                 latest[1]["before_export_ascii"].encode("ascii"))
            with _owner_locks(a, r, ledger=True), sync._open(a, p,
                    registry.BindingCheckpoint(**ap["checkpoint"])) as binding:
                return _finish(a, r, ctx, latest, binding, timely=lambda: None, fault=fault_injector)
        _need(len(records) < MAX_ROUNDS, "ROUND_LIMIT")
        with sync._open(a, p, cp) as initial:
            s = initial.snapshot()
            _need(s.pending_plan_sha256 is None and s.checkpoint == cp and s.pins == v4.OperationalPins(**ap["pins"]),
                  "INITIAL_SOURCE_CHANGED")
            before_raw = initial.source.export_bytes()
        dispatch._historical_plan_from_export(a, r, arm, ap, owners, dp, before_raw)
        deadline = dispatch._Deadline(a)
        try:
            raw = _parse(_canonical(a.transport.get_order_state(a.policy.account_id, intent.intent_id,
                                                               by_request_id=True)))
        except Exception:
            raise VersionedFillCashError("V4_FILL_CASH_LOOKUP_UNAVAILABLE_NO_RETRY") from None
        exchange = dispatch._exchange(raw, intent, lookup=True)
        _need(not any(i.intent_id != intent.intent_id and i.broker_order_id == exchange
                      for i in central.intents), "EXCHANGE_ALREADY_BOUND")
        now = a.cl7_clock()
        instrument = a.cl7_own_funds_policy.instruments[intent.candidate.instrument_id]
        bound = {"domain": evidence.DOMAIN, "version": 1,
            "dispatch_plan_sha256": expected_dispatch_plan_sha256, "intent": intent.to_dict(),
            "instrument": {"instrument_id": intent.candidate.instrument_id, "currency": instrument.currency,
                           "asset_class": instrument.asset_class, "lot_size": instrument.lot_size},
            "own_funds": dp["request_binding"]["payload"]["own_funds"],
            "proof_evaluated_at": dp["evaluated_at"], "observed_at": now, "order_state": raw}
        evidence.decode_bound_fill(bound, key=a.cl7_identity_key, key_id=a.cl7_identity_key_id,
                                   account_scope=cut._common(a)["account_scope_sha256"])
        graph = sync._graph(a, before_raw)
        requests, responses = [], []
        def collect(payload, timeout):
            deadline.check()
            value = _parse(_canonical(a.transport.get_operations_by_cursor_once(payload, timeout)))
            requests.append(deepcopy(payload)); responses.append(value)
            deadline.check()
            return deepcopy(value)
        request = cl3.BrokerReadRequest(cl3.BrokerEnvironment.SANDBOX, a.policy.account_id,
            a.cl7_identity_key, a.cl7_identity_key_id, graph.first_from, now, 128, 4, 128,
            a.cl7_monotonic_ns() + MAX_AGE_NS, cl3.RetryPolicy(1, MAX_AGE_NS, ()),
            collect, a.cl7_monotonic_ns, a.cl7_wait_ns)
        cl3.collect_tbank_operations(request)
        positions = _parse(_canonical(a.transport.get_positions(a.policy.account_id)))
        recorded_at = a.cl7_clock(); deadline.check()
        dispatch._check_owners(a, r, p, owners, pending, central)
        index = 0
        def replay(payload, timeout):
            nonlocal index
            _need(index < len(requests) and payload == requests[index], "CAPTURE_REPLAY_MISMATCH")
            result = deepcopy(responses[index]); index += 1
            return result
        cached = cl3.BrokerReadRequest(request.environment, request.raw_account_id, request.identity_key,
            request.identity_key_id, request.from_inclusive, request.to_exclusive, request.limit,
            request.max_pages, request.max_items, request.absolute_deadline_ns, request.retry_policy,
            replay, request.monotonic_ns, request.wait_ns)
        number = len(records) + 1
        path = _folder(root, expected_dispatch_plan_sha256) / f"{number:04d}"
        holder = []
        with _owner_locks(a, r, ledger=True), sync._open(a, p, cp, fault=fault_injector) as binding:
            dispatch._check_owners(a, r, p, owners, pending, central); deadline.check()
            def prepare(before, after, event):
                _need(before == before_raw and not holder, "PREPARE_SOURCE_CHANGED")
                plan = {"domain": DOMAIN, "version": 1, "kind": "PREPARED", "round": number,
                    "previous_result_sha256": parent, "dispatch_plan_sha256": expected_dispatch_plan_sha256,
                    "selection_sha256": expected_selection_sha256, "target_root": str(root),
                    "authority": pending.to_canonical_dict(), "central_before": central.to_dict(),
                    "started_at": deadline.started_at, "recorded_at": recorded_at,
                    "before_checkpoint": sync._cp(cp), "before_export_ascii": before.decode("ascii"),
                    "after_export_ascii": after.decode("ascii"), "registry_prepared_ascii": event.decode("ascii")}
                checked = _validate_plan(a, ctx, plan, _sha(_sealed(plan, a.cl7_identity_key)), parent, cp, number)
                dispatch._check_owners(a, r, p, owners, pending, central); deadline.check()
                digest = _save(path / "plan.json", plan, a.cl7_identity_key, PLAN_FIELDS)
                holder.append((path, plan, digest, *checked, None))
                _point(fault_injector, "cash.after_plan")
                dispatch._check_owners(a, r, p, owners, pending, central); deadline.check()
            outcome, _ = binding.sync_tbank_operations(cached, expected_checkpoint=cp,
                recorded_at=recorded_at, read_rub_positions=lambda account: deepcopy(positions),
                prepare_selected=prepare, bound_full_fill=bound)
            _need(index == len(requests) and len(holder) == 1 and not outcome.replay, "PLAN_MISSING")
            return _finish(a, r, ctx, holder[0], binding, timely=deadline.check, fault=fault_injector,
                           appended=outcome.appended_transactions)
