"""Ordered full-FILL owner closure over a committed, request-bound v4 cash batch.

The cash/registry prefix is consumed, never rewritten. New closure reads the
receipt, complete overlapping operations, positions and own cash again. Offline
recovery re-derives the saved evidence and admits only P -> R -> C -> A prefixes.
Authority stays dispatch-pending until the final DISARMED transition. No POST,
cancel, automatic arm, generic reconciliation bypass or fee-finality claim.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from contextvars import ContextVar
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import broker_read_adapters as cl3
from . import cash_observation_versions as versions
from . import versioned_dispatch as dispatch
from . import versioned_fill_cash as cash
from . import versioned_fill_evidence as fill
from . import versioned_operational_store as v4
from . import versioned_owner_refresh as refresh
from . import versioned_risk_admission as admission
from . import versioned_runtime_cutover as cut
from . import versioned_selected_sync as sync
from . import versioned_source_binding as registry
from .central_order_manager import CentralOrderState
from .desktop_fill_recovery import _ReceiptReadView
from .exact_cash_settlement import _pairs, _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .exact_settlement_closure import _risk_after
from .portfolio_adapters import BrokerPortfolioAdapter, RuntimePortfolioAdapter, RuntimePositionRecord
from .portfolio_cash_observation import DesktopOwnCashPolicy
from .portfolio_manager import CanonicalPortfolioManager
from .portfolio_model import (
    CompatibilityShadowStatus, PortfolioMigrationMetadata, PortfolioState,
    PortfolioTarget, PositionOrigin, PositionOwnership, SnapshotFreshness,
)
from .portfolio_observation import PortfolioObservationPolicy
from .portfolio_reconciler import PortfolioReconciler, ReconciliationContext
from .portfolio_repository import portfolio_document_checksum
from .risk import RiskState
from .runtime_cash_authority import RuntimeCashAuthorityRecord as Authority
from .runtime_cash_authority import RuntimeCashAuthorityState as State
from .runtime_cash_authority import _transition_pair
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha

DOMAIN = "V4_FULL_FILL_OWNER_CLOSURE_V1"
DONE = "VERSIONED_FULL_FILL_CLOSED_DISARMED"
MAX_BYTES = 8 * 1024 * 1024
PLAN_FIELDS = {"domain", "version", "kind", "selection_sha256", "target_root",
    "dispatch_plan_sha256", "cash_plan_sha256", "cash_result_sha256", "before_authority",
    "before_owners", "after_owners", "portfolio_candidate", "execution", "transaction_id",
    "started_at", "captured_at", "reads", "operations", "pins", "checkpoint", "config_sha256", "covered_until"}
RESULT_FIELDS = {"domain", "version", "kind", "plan_sha256", "dispatch_plan_sha256", "completed_at"}
_STACK: ContextVar[tuple[str, ...]] = ContextVar("v4_fill_closure_stack", default=())


class VersionedFillClosureError(RuntimeError):
    """Finite refusal. Raw financial records and credentials are never echoed."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise VersionedFillClosureError("V4_FILL_CLOSURE_" + reason)


def _point(fault: Callable | None, name: str) -> None:
    if fault is not None:
        fault(name)


def _folder(root: Path, digest: str) -> Path:
    path = root / "versioned_fill_closure" / versions._hash(digest)
    _safe_path(path)
    return path


def _read(path: Path, key: bytes, fields: set[str]) -> tuple[dict, str]:
    _safe_path(path)
    _need(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, "RECORD_UNAVAILABLE")
    raw = path.read_bytes()
    _need(0 < len(raw) <= MAX_BYTES, "RECORD_TOO_LARGE")
    doc = json.loads(raw, object_pairs_hook=_pairs)
    _need(type(doc) is dict and set(doc) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = doc["payload"]
    _need(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
          and type(body["version"]) is int and body["version"] == 1, "SCHEMA_INVALID")
    signature = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
    _need(type(doc["hmac_sha256"]) is str and hmac.compare_digest(signature, doc["hmac_sha256"]),
          "SIGNATURE_INVALID")
    return body, _sha(_canonical(doc))


def _write(path: Path, body: dict, key: bytes, fields: set[str]) -> str:
    _safe_path(path)
    _need(not path.exists(), "RECORD_ALREADY_EXISTS")
    raw = _sealed(body, key)
    _need(len(raw) <= MAX_BYTES, "RECORD_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(path, _parse(raw), retry_delays=(), jitter_fraction=0,
                      write_checksum=False, keep_last_good=False)
    _need(_read(path, key, fields) == (body, _sha(raw)), "RECORD_READBACK_FAILED")
    return _sha(raw)


def _cash_context(a: Any, p: dict, selection: str, dispatch_sha: str, central_raw: dict) -> tuple:
    """Validate historical authorization/cash without assuming today's owners.

    Live owner guards are separate. No synthetic v1 proof or temporary rollback
    of owner files is used to make historical validation succeed.
    """
    dp, digest = dispatch._read(dispatch._path(Path(p["target_root"]), "plans", dispatch_sha),
                                a.cl7_identity_key, dispatch.PLAN_FIELDS)
    _need(digest == dispatch_sha and dp["kind"] == "DISPATCH"
          and dp["selection_sha256"] == selection and dp["target_root"] == p["target_root"], "DISPATCH_MISMATCH")
    arm, arm_sha = dispatch._read(dispatch._path(Path(p["target_root"]), "arms", dp["arm_sha256"]),
                                  a.cl7_identity_key, dispatch.ARM_FIELDS)
    _need(arm_sha == dp["arm_sha256"] and arm["kind"] == "ARM"
          and arm["selection_sha256"] == selection and arm["target_root"] == p["target_root"], "ARM_MISMATCH")
    parent = Authority.from_canonical_dict(arm["admitted_authority"])
    _need(parent.transition_kind == admission.DONE and parent.state is State.EXACT_CASH_VERSIONED_DISARMED,
          "ADMISSION_REQUIRED")
    ap, ap_sha = admission._completed(a, None, p, selection, parent)
    owners = {"portfolio": ap["before_owners"]["portfolio"], "risk": ap["risk_after"], "central": ap["central_after"]}
    queued = CentralOrderState.from_dict(owners["central"])
    _need(len(queued.queued) == 1 and queued.blocking_intent is None, "SINGLE_INTENT_REQUIRED")
    original = queued.queued[0]
    _need(ap_sha == arm["admission_plan_sha256"] and original.intent_id == arm["intent_id"]
          and dispatch.intent_fingerprint(original) == arm["intent_sha256"]
          and dp["central_before"] == queued.to_dict() and dp["request"] == dispatch._request(original),
          "REQUEST_MISMATCH")
    pending = dispatch._pending(a, arm, arm_sha, dp, dispatch_sha)
    central = CentralOrderState.from_dict(central_raw)
    dispatch._central_prefix(queued, central, dispatch_sha, dp["attempt_at"])
    intent = next(i for i in central.intents if i.intent_id == original.intent_id)
    _need(intent.status in {"SUBMITTED", "UNCERTAIN", "IN_FLIGHT"}
          and central.blocking_intent == intent and not central.queued, "PENDING_INTENT_REQUIRED")
    ctx = dp, arm, p, ap, owners, pending, central, intent
    cp, result_sha, records = cash._history(a, ctx)
    _need(bool(records) and records[-1][-1] is not None
          and records[-1][-1]["outcome"] == "COMMITTED", "COMMITTED_CASH_REQUIRED")
    latest = records[-1]
    dispatch._historical_plan_from_export(a, None, arm, ap, owners, dp,
                                         latest[1]["before_export_ascii"].encode("ascii"))
    return ctx, latest, cp, result_sha


def _economics(a: Any, context: tuple, plan: dict) -> tuple[dict, PortfolioState, dict]:
    """Reproduce Portfolio, one native Risk execution and native Central states."""
    ctx, record, cp, result_sha = context
    dp, arm, p, ap, owners, pending, central, intent = ctx
    _, cash_plan, cash_sha, _, graph, _, receipt, _ = record
    before = {**owners, "central": central.to_dict()}
    _need(plan["before_owners"] == before and plan["before_authority"] == pending.to_canonical_dict()
          and plan["cash_plan_sha256"] == cash_sha and plan["cash_result_sha256"] == result_sha
          and plan["pins"] == graph.snapshot().pins.to_dict() and plan["checkpoint"] == sync._cp(cp)
          and plan["covered_until"] == graph.covered_until,
          "CASH_OR_OWNER_BINDING_CHANGED")
    started, observed = plan["started_at"], plan["captured_at"]
    _need(timestamp_ns(cash_plan["recorded_at"]) <= timestamp_ns(started)
          <= timestamp_ns(observed) <= timestamp_ns(started) + MAX_AGE_NS, "CAPTURE_TIME_INVALID")
    previous = PortfolioState.from_dict(before["portfolio"])
    c = intent.candidate
    _need(previous.portfolio_source == "CANONICAL" and previous.migration.complete
          and not previous.migration.legacy_read_path_enabled and not previous.blocking
          and previous.migration.compatibility_shadow_status is CompatibilityShadowStatus.OK, "CANONICAL_INVALID")
    old = previous.position(c.instrument_id)
    _need((old is None and c.current_lots == 0) or (old is not None
          and old.actual_lots == c.current_lots and old.target_lots in {None, c.current_lots}), "PREPOSITION_CHANGED")
    if old is not None and old.actual_lots:
        _need(old.ownership is not None and old.ownership.strategy_id == c.strategy_id
              and old.ownership.config_hash == c.strategy_profile_hash
              and old.ownership.candle_interval == c.candle_interval, "OWNERSHIP_CHANGED")
    _need(not any(o.active or o.uncertain for pos in previous.positions for o in pos.pending_orders),
          "PREEXISTING_PENDING_ORDER")

    tape = refresh._ReadTape(rows=plan["reads"])
    order = tape.get_order_state(a.policy.account_id, intent.intent_id, by_request_id=True)
    original_bound = _parse(graph.record_bodies[-1]["capture_json_ascii"])["bound_full_fill"]
    live_bound = {**original_bound, "order_state": order, "observed_at": observed}
    _, _, fresh = fill.decode_bound_fill(live_bound, key=a.cl7_identity_key, key_id=a.cl7_identity_key_id,
                                        account_scope=cut._common(a)["account_scope_sha256"])
    _need(fresh.receipt_identity_sha256 == receipt.receipt_identity_sha256, "RECEIPT_CHANGED")
    _need(not any(other.intent_id != intent.intent_id and other.broker_order_id == order["orderId"]
                  for other in central.intents), "EXCHANGE_ID_COLLISION")
    instruments = a.cl7_own_funds_policy.instruments
    policy = PortfolioObservationPolicy(a.policy.account_id, instruments)
    portfolio, orders = policy.acquire(_ReceiptReadView(tape, SimpleNamespace(order=order)), previous)
    cash_observation = DesktopOwnCashPolicy(a.policy.account_id, instruments).acquire(tape, lambda *_: None)
    _need(tape.index == len(tape.rows), "EXTRA_CAPTURE_READS")
    positions = [row["response"] for row in tape.rows if row["method"] == "get_positions"]
    _need(len(positions) == 1 and plan["operations"]["rub_positions"] == positions[0]
          and plan["operations"]["from_inclusive"] == graph.first_from
          and timestamp_ns(started) <= timestamp_ns(plan["operations"]["to_exclusive"]) <= timestamp_ns(observed),
          "OPERATIONS_CAPTURE_MISMATCH")
    checked = v4._derive(graph, _canonical(plan["operations"]), observed, a.cl7_identity_key,
                         a.cl7_identity_key_id, cut._common(a)["account_scope_sha256"])
    _need(not checked["entries"] and checked["cash_delta_nano"] == "0"
          and checked["ledger_head_sha256"] == graph.ledger_head, "UNSETTLED_NEW_OPERATIONS")
    _need(int(cash_observation.rub_position * 10**9) == graph.cash_nano, "RUB_MISMATCH")
    broker = cash_observation.apply(BrokerPortfolioAdapter.from_api_portfolio(portfolio,
        account_id=c.account_id, broker_orders=orders, snapshot_at=observed))
    _need(timestamp_ns(observed) > timestamp_ns(dispatch.cl6._normalize_portfolio_timestamp(intent.updated_at)[0])
          and timestamp_ns(observed) > timestamp_ns(previous.snapshot_at)
          and all(timestamp_ns(s.execution_at) <= timestamp_ns(observed) for s in fresh.stages),
          "SNAPSHOT_PREDATES_EXECUTION")
    target = c.current_lots + (fresh.executed_lots if c.direction == "BUY" else -fresh.executed_lots)
    actual = next((v.actual_lots for v in broker.positions if v.instrument_id == c.instrument_id), 0)
    _need(actual == target == c.target_lots and target >= 0, "OBSERVED_POSITION_MISMATCH")
    base = RuntimePortfolioAdapter.from_portfolio_state(previous)
    old_runtime = next((v for v in base.positions if v.instrument_id == c.instrument_id), None)
    owned = RuntimePositionRecord(instrument_id=c.instrument_id, figi=old_runtime.figi if old_runtime else "",
        ticker=c.ticker, class_code=ap["runtime"]["config"]["class_code"],
        target=PortfolioTarget(instrument_id=c.instrument_id, target_lots=target, strategy_id=c.strategy_id,
                               config_hash=c.strategy_profile_hash, candle_time=c.candle_time),
        ownership=PositionOwnership(strategy_id=c.strategy_id, config_hash=c.strategy_profile_hash,
            candle_interval=c.candle_interval, source="CANONICAL_TRANSACTION", attributed_at=observed) if target else None,
        pending_orders=old_runtime.pending_orders if old_runtime else (), last_candle_time=c.candle_time,
        state_key="canonical:" + c.instrument_id)
    runtime = replace(base, positions=tuple([v for v in base.positions if v.instrument_id != c.instrument_id] + [owned]))
    candidate = PortfolioReconciler().reconcile(broker, runtime, previous=previous,
        context=ReconciliationContext(expected_account_id=c.account_id, freshness=SnapshotFreshness.FRESH,
            generated_at=observed, journal_confirmed_instruments=frozenset({c.instrument_id}),
            position_origins={c.instrument_id: PositionOrigin.STRATEGY}))
    candidate = CanonicalPortfolioManager._normalize_flat_positions(candidate)
    _need(not candidate.blocking and not any(o.active or o.uncertain for pos in candidate.positions for o in pos.pending_orders),
          "PORTFOLIO_RECONCILIATION_BLOCKED")
    def other_positions(state: PortfolioState):
        return sorted((pos.instrument_id, pos.actual_lots, pos.target_lots, pos.ownership, pos.target, pos.origin)
                      for pos in state.positions if pos.instrument_id != c.instrument_id)
    _need(other_positions(candidate) == other_positions(previous), "UNRELATED_POSITION_CHANGED")
    tx = "v4-full-fill:" + plan["dispatch_plan_sha256"] + ":" + cash_sha
    _need(plan["transaction_id"] == tx, "TRANSACTION_ID_CHANGED")
    published = replace(candidate, revision=previous.revision + (candidate.decision_sha256 != previous.decision_sha256),
        portfolio_source="CANONICAL", migration=PortfolioMigrationMetadata.completed(
            source_schema=previous.migration.source_schema, migration_id=previous.migration.migration_id,
            migrated_at=previous.migration.migrated_at, shadow_status=CompatibilityShadowStatus.OK,
            detail="Canonical-only read path is active."), last_transaction_id=tx, last_transaction_status="COMMITTED")
    execution = {"execution_id": intent.intent_id, "executed_at": max(s.execution_at for s in fresh.stages),
        "signed_lots": fresh.executed_lots if c.direction == "BUY" else -fresh.executed_lots,
        "price_rub": float(fresh.average_price_rub), "lot_size": c.lot_size,
        "portfolio_equity_rub": published.account.total_value,
        "portfolio_cash_rub": published.account.cash("rub").available,
        "portfolio_snapshot_at": observed, "execution_source": "STRATEGY"}
    risk = _risk_after(before["risk"], execution)
    # Do not silently create a new Risk period or erase its loss/cash-flow anchors.
    for field in ("daily_date", "weekly_key", "daily_start_equity_rub", "weekly_start_equity_rub"):
        _need(risk.to_dict()[field] == before["risk"][field], "RISK_PERIOD_CHANGE_UNSUPPORTED")
    _need(tuple(risk.recorded_execution_ids) == (*before["risk"]["recorded_execution_ids"], intent.intent_id),
          "RISK_EXECUTION_HISTORY_CHANGED")
    ack = intent
    if intent.status == "IN_FLIGHT":
        ack = intent.transition("SUBMITTED", at=refresh._utc(receipt.observed_at).isoformat(),
                                detail="full-fill identity from committed cash evidence", broker_order_id=order["orderId"])
    final = ack.transition("RECONCILED", at=refresh._utc(observed).isoformat(),
        detail="v4 full-fill cash and owners: " + cash_sha, broker_order_id=order["orderId"],
        outcome="FILLED", executed_lots=fresh.executed_lots,
        reconciled_portfolio_revision=published.revision,
        reconciled_portfolio_decision_checksum=published.decision_sha256,
        reconciled_portfolio_snapshot_at=published.snapshot_at,
        risk_execution_status="RECORDED", risk_execution_id=intent.intent_id)
    central_after = replace(central.replace_intent(final), revision=central.revision + (2 if ack is not intent else 1),
                            updated_at=final.updated_at)
    a.manager._validate_reconciliation(None, ack, outcome="FILLED", executed_lots=fresh.executed_lots,
                                       locked_portfolio_state=published)
    return {"portfolio": published.to_dict(), "risk": risk.to_dict(), "central": central_after.to_dict()}, candidate, execution


def _load_plan(a: Any, p: dict, selection: str, dispatch_sha: str, *, semantic: bool = True):
    stack = _STACK.get()
    _need(dispatch_sha not in stack and len(stack) < 16, "PLAN_CYCLE_OR_LIMIT")
    token = _STACK.set((*stack, dispatch_sha))
    try:
        path = _folder(Path(p["target_root"]), dispatch_sha) / "plan.json"
        plan, digest = _read(path, a.cl7_identity_key, PLAN_FIELDS)
        _need(plan["kind"] == "PREPARED" and plan["selection_sha256"] == selection
              and plan["dispatch_plan_sha256"] == dispatch_sha and plan["target_root"] == p["target_root"]
              and plan["config_sha256"] == p["config_sha256"], "PLAN_IDENTITY_CHANGED")
        if semantic:
            context = _cash_context(a, p, selection, dispatch_sha, plan["before_owners"]["central"])
            after, candidate, execution = _economics(a, context, plan)
            _need(after == plan["after_owners"] and candidate.to_dict() == plan["portfolio_candidate"]
                  and execution == plan["execution"], "ECONOMIC_PLAN_INVALID")
            return plan, digest, context
        return plan, digest, None
    finally:
        _STACK.reset(token)


def _after(a: Any, plan: dict, digest: str, result: dict, result_sha: str) -> Authority:
    _need(result["kind"] == "COMMITTED" and result["plan_sha256"] == digest
          and result["dispatch_plan_sha256"] == plan["dispatch_plan_sha256"]
          and result_sha == _sha(_sealed(result, a.cl7_identity_key)), "RESULT_CHANGED")
    versions._time(result["completed_at"])
    _need(timestamp_ns(result["completed_at"]) >= timestamp_ns(plan["captured_at"]), "RESULT_TIME_INVALID")
    before = Authority.from_canonical_dict(plan["before_authority"])
    pins = v4.OperationalPins(**plan["pins"])
    after = a.cash_authority_manager._change(before, at=result["completed_at"], kind=DONE,
        state=State.EXACT_CASH_VERSIONED_DISARMED, pending_dispatch_proof_sha256=None,
        activation_context_sha256=result_sha, ledger_head_sha256=pins.ledger_head_sha256,
        ledger_revision=pins.ledger_revision, operations_complete_through=plan["covered_until"])
    _transition_pair(before, after)
    return after


def _completed(a: Any, p: dict, selection: str, record: Authority, *, semantic=True):
    path = Path(p["target_root"]) / "versioned_fill_closure" / "results" / (versions._hash(record.activation_context_sha256) + ".json")
    result, digest = _read(path, a.cl7_identity_key, RESULT_FIELDS)
    dispatch_sha = versions._hash(result["dispatch_plan_sha256"])
    _need(digest == record.activation_context_sha256, "RESULT_PLAN_MISSING")
    plan, plan_sha, context = _load_plan(a, p, selection, dispatch_sha, semantic=semantic)
    _need(_read(_folder(Path(p["target_root"]), dispatch_sha) / "result.json", a.cl7_identity_key, RESULT_FIELDS)
          == (result, digest) and record == _after(a, plan, plan_sha, result, digest), "AUTHORITY_RESULT_MISMATCH")
    return plan, plan_sha, context


def closure_lineage(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    plan, _, context = _completed(a, p, selection, record)
    ctx, record_cash, cp, _ = context
    parent = Authority.from_canonical_dict(ctx[1]["admitted_authority"])
    cp0, pins0, count, _ = sync._lineage(a, p, selection, parent, seen=seen | {record.activation_context_sha256})
    _need(cp0 == registry.BindingCheckpoint(**ctx[3]["checkpoint"])
          and pins0 == record_cash[3].snapshot().pins, "PARENT_LINEAGE_CHANGED")
    return cp, record_cash[4].snapshot().pins, count, plan_source(context)


def plan_source(context: tuple) -> bytes:
    return context[1][1]["after_export_ascii"].encode("ascii")


def committed_owners(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    plan, _, _ = _completed(a, p, selection, record, semantic=False)
    # Full semantic/cash validation is performed by closure_lineage on entry.
    # Identity guards only recheck immutable signed documents between writes.
    return plan["after_owners"]


@dataclass(frozen=True, slots=True)
class VersionedClosureResult:
    closure_plan_sha256: str
    cash_plan_sha256: str
    authority_sha256: str
    ledger_head_sha256: str
    ledger_revision: int
    replay: bool

    def public_summary(self) -> dict:
        return {"domain": DOMAIN + "_RESULT", "status": "FULL_FILL_CLOSED_DISARMED",
            "closure_plan_sha256": self.closure_plan_sha256, "cash_plan_sha256": self.cash_plan_sha256,
            "authority_sha256": self.authority_sha256, "ledger_head_sha256": self.ledger_head_sha256,
            "ledger_revision": self.ledger_revision, "replay": self.replay,
            "bounded_settlement_verified": True, "runtime_authority_granted": False,
            "automatic_rearm_allowed": False, "new_money_transactions": 0,
            "future_fee_finality_claimed": False, "post_order_called": False}


def _report(plan: dict, digest: str, authority: Authority, replay: bool):
    return VersionedClosureResult(digest, plan["cash_plan_sha256"], authority.sha256,
                                  authority.ledger_head_sha256, authority.ledger_revision, replay)


def _owner_phase(before: dict, after: dict, observed: dict, pending: dict,
                 final: dict | None = None) -> int:
    """Only ordered P/R/C prefixes; a terminal authority requires all three."""
    _need(set(observed) == {"portfolio", "risk", "central", "authority"}, "OWNER_SHAPE_INVALID")
    for phase in range(4):
        expected = {key: after[key] if i < phase else before[key]
                    for i, key in enumerate(("portfolio", "risk", "central"))}
        if all(observed[k] == expected[k] for k in expected):
            if observed["authority"] == pending:
                return phase
            if phase == 3 and final is not None and observed["authority"] == final:
                return 4
    raise VersionedFillClosureError("V4_FILL_CLOSURE_OWNER_PREFIX_CONFLICT")


def _evidence_fence(root: Path, dispatch_sha: str) -> Callable:
    """Freeze files already semantically checked, not a new source of trust."""
    own_folder = _folder(root, dispatch_sha)
    def paths():
        result = tuple(sorted(q for q in root.rglob("*.json") if not q.is_relative_to(own_folder)))
        _need(len(result) <= 512, "EVIDENCE_FILE_LIMIT")
        for q in result:
            _safe_path(q)
        return result
    saved_paths = paths()
    immutable = tuple((q, q.read_bytes()) for q in saved_paths)
    _need(sum(len(raw) for _, raw in immutable) <= 48 * 1024 * 1024, "EVIDENCE_BYTE_LIMIT")
    def check(extra_result_sha: str | None = None):
        expected = set(saved_paths)
        if extra_result_sha is not None:
            expected.add(root / "versioned_fill_closure" / "results" / (versions._hash(extra_result_sha) + ".json"))
        actual = set(paths())
        _need(set(saved_paths) <= actual <= expected and all(q.read_bytes() == data for q, data in immutable),
              "HISTORICAL_EVIDENCE_CHANGED")
    return check


@contextmanager
def _verified_binding(a: Any, p: dict, cp: Any, raw: bytes):
    with sync._open(a, p, cp) as binding:
        state = binding._load()
        _need(state.pending is None and state.checkpoint == cp and state.raw == raw,
              "SOURCE_OR_REGISTRY_CHANGED")
        yield binding, state


@contextmanager
def _locked_bound_snapshot(binding: Any, state: Any):
    # _load already validated the complete registry graph. Recheck every row,
    # physical source identity and schema under its lock; validate source under
    # its real SQLite writer lease. No current pins are accepted implicitly.
    with binding._guard():
        sync._verify_rows(binding, state)
        with binding.source.locked_snapshot(expected_pins=state.pins) as view:
            _need(view.export_bytes() == state.raw, "SOURCE_CHANGED")
            yield view
            sync._verify_rows(binding, state)
            view.assert_active()


def _finish(a: Any, r: Any, p: dict, selection: str, dispatch_sha: str, *, timely: Callable,
            fault=None, _validated=None):
    if _validated is None:
        plan, digest, context = _load_plan(a, p, selection, dispatch_sha)
        evidence_check = _evidence_fence(Path(p["target_root"]), dispatch_sha)
        open_binding = _verified_binding(a, p, context[2], plan_source(context))
    else:
        plan, digest, context, evidence_check, bound = _validated
        _need(_read(_folder(Path(p["target_root"]), dispatch_sha) / "plan.json",
                    a.cl7_identity_key, PLAN_FIELDS) == (plan, digest), "PLAN_CHANGED")
        evidence_check()
        open_binding = nullcontext(bound)
    ctx, cash_record, cp, _ = context
    pending, intent = ctx[5], ctx[7]
    pins, raw = cash_record[4].snapshot().pins, plan_source(context)
    folder = _folder(Path(p["target_root"]), dispatch_sha)
    result_path = folder / "result.json"
    saved_result = _read(result_path, a.cl7_identity_key, RESULT_FIELDS) if result_path.exists() else None
    final = _after(a, plan, digest, *saved_result) if saved_result is not None else None
    before, after = plan["before_owners"], plan["after_owners"]

    def check() -> int:
        timely()
        _need(cut._load_prepared(a, r, Path(p["target_root"]), selection, _check_initial_owners=False) == p,
              "CONFIG_OR_OLD_SOURCE_CHANGED")
        _need(_read(folder / "plan.json", a.cl7_identity_key, PLAN_FIELDS) == (plan, digest), "PLAN_CHANGED")
        evidence_check(saved_result[1] if saved_result is not None else None)
        return _owner_phase(before, after, cut._owners(a, r), pending.to_canonical_dict(),
                            final.to_canonical_dict() if final is not None else None)

    with open_binding as (binding, state):
        _need(state.pending is None and state.checkpoint == cp and state.pins == pins and state.raw == raw,
              "SOURCE_OR_REGISTRY_CHANGED")
        def source_check(view):
            sync._verify_rows(binding, state)
            _need(view.pins == pins and view.export_bytes() == raw, "SOURCE_CHANGED")
            view.assert_active()
        with _owner_locks(a, r, ledger=True), _locked_bound_snapshot(binding, state) as view:
            phase = check(); source_check(view)
            if phase == 4:
                _completed(a, p, selection, final, semantic=False)
                return _report(plan, digest, final, True)
        if phase == 0:
            previous = PortfolioState.from_dict(before["portfolio"])
            def transform(current):
                _need(check() == 0 and current.to_dict() == before["portfolio"], "PORTFOLIO_CHANGED")
                return PortfolioState.from_dict(plan["portfolio_candidate"])
            r.manager.transaction_coordinator.commit("VERSIONED_FULL_FILL_SETTLEMENT", transform,
                expected_revision=previous.revision, expected_document_checksum=portfolio_document_checksum(previous),
                transaction_id=plan["transaction_id"], account_id=a.policy.account_id,
                instrument_id=intent.candidate.instrument_id, mode="SANDBOX_EXECUTION")
            _point(fault, "closure.after_portfolio")
        with _owner_locks(a, r, ledger=True), _locked_bound_snapshot(binding, state) as view:
            phase = check(); source_check(view)
            _need(phase >= 1, "CANONICAL_NOT_WRITTEN")
            canonical = PortfolioState.from_dict(after["portfolio"])
            _need(r.manager.transaction_coordinator.shadow_writer is not None
                  and r.manager.transaction_coordinator.shadow_writer.write(canonical) is CompatibilityShadowStatus.OK,
                  "SHADOW_WRITE_FAILED")
            a.manager._validate_reconciliation(r.manager.repository, intent, outcome="FILLED",
                executed_lots=context[1][6].executed_lots, locked_portfolio_state=canonical)
            check(); source_check(view)
            if phase == 1:
                r.risk.state_store.save_account_while_locked(a.policy.account_id, RiskState.from_dict(after["risk"]))
                _point(fault, "closure.after_risk")
            phase = check(); source_check(view)
            if phase == 2:
                a.manager.store._save_unlocked(CentralOrderState.from_dict(after["central"]))
                _point(fault, "closure.after_central")
            _need(check() == 3, "OWNERS_NOT_WRITTEN"); source_check(view)
            if saved_result is None:
                result = {"domain": DOMAIN, "version": 1, "kind": "COMMITTED", "plan_sha256": digest,
                          "dispatch_plan_sha256": dispatch_sha, "completed_at": a.cl7_clock()}
                result_sha = _write(result_path, result, a.cl7_identity_key, RESULT_FIELDS)
                saved_result = result, result_sha
                final = _after(a, plan, digest, result, result_sha)
            _point(fault, "closure.after_result_file")
            index = Path(p["target_root"]) / "versioned_fill_closure" / "results" / (saved_result[1] + ".json")
            if index.exists():
                _need(_read(index, a.cl7_identity_key, RESULT_FIELDS) == saved_result, "RESULT_INDEX_CHANGED")
            else:
                _write(index, saved_result[0], a.cl7_identity_key, RESULT_FIELDS)
            _point(fault, "closure.after_result")
            _need(check() == 3, "OWNERS_CHANGED"); source_check(view)
            r.manager.transaction_coordinator._record("VERSIONED_FULL_FILL_ACCOUNTED",
                account_id=a.policy.account_id, instrument_id=intent.candidate.instrument_id,
                mode="SANDBOX_EXECUTION", status="ACCOUNTED_PENDING_CLEAR",
                payload={"closure_plan_sha256": digest, "cash_plan_sha256": plan["cash_plan_sha256"],
                         "source_export_sha256": pins.export_sha256})
            _point(fault, "closure.after_audit")
            _need(check() == 3, "OWNERS_CHANGED"); source_check(view)
            a.cash_authority_manager.store._commit_unlocked(final,
                expected_revision=pending.record_revision, expected_sha256=pending.sha256)
            _point(fault, "closure.after_authority")
            _need(check() == 4, "CLOSURE_INCOMPLETE"); source_check(view)
            return _report(plan, digest, final, False)


def close_selected_full_fill(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_dispatch_plan_sha256: str,
        expected_cash_plan_sha256: str, fault_injector: Callable | None = None) -> VersionedClosureResult:
    """Fresh full-fill owner closure; repeated saved plans use offline recovery.

    This writes owners and authority, not the broker or monetary source. A
    failed Portfolio prefix is retained pending rather than rolled back.
    """
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        p = cut._load_prepared(a, r, root, expected_selection_sha256, _check_initial_owners=False)
        path = _folder(root, expected_dispatch_plan_sha256) / "plan.json"
        if path.exists():
            plan, _ = _read(path, a.cl7_identity_key, PLAN_FIELDS)
            _need(plan["cash_plan_sha256"] == expected_cash_plan_sha256, "CASH_PLAN_MISMATCH")
            return _finish(a, r, p, expected_selection_sha256, expected_dispatch_plan_sha256,
                           timely=lambda: None, fault=fault_injector)
        context = _cash_context(a, p, expected_selection_sha256, expected_dispatch_plan_sha256,
                                a.manager.state().to_dict())
        ctx, record, cp, result_sha = context
        pending, intent = ctx[5], ctx[7]
        _need(record[2] == expected_cash_plan_sha256, "CASH_PLAN_MISMATCH")
        before = {**ctx[4], "central": ctx[6].to_dict()}
        dispatch._check_owners(a, r, p, before, pending)
        evidence_check = _evidence_fence(root, expected_dispatch_plan_sha256)
        with _verified_binding(a, p, cp, plan_source(context)) as bound:
            binding, state = bound
            deadline = dispatch._Deadline(a)
            def guard(*_):
                deadline.check(); evidence_check(); dispatch._check_owners(a, r, p, before, pending)
            tape = refresh._ReadTape(provider=r.manager.api, guard=guard)
            raw_order = tape.get_order_state(a.policy.account_id, intent.intent_id, by_request_id=True)
            previous = PortfolioState.from_dict(before["portfolio"])
            r.policy.acquire(_ReceiptReadView(tape, SimpleNamespace(order=raw_order)), previous, guard)
            r.cash_policy.acquire(tape, guard)
            graph = record[4]
            until = a.cl7_clock()
            requests, responses = [], []
            def collect(payload, timeout):
                guard()
                response = _parse(_canonical(a.transport.get_operations_by_cursor_once(payload, timeout)))
                requests.append(deepcopy(payload)); responses.append(response)
                guard(); return deepcopy(response)
            cl3.collect_tbank_operations(cl3.BrokerReadRequest(cl3.BrokerEnvironment.SANDBOX,
                a.policy.account_id, a.cl7_identity_key, a.cl7_identity_key_id, graph.first_from, until,
                128, 4, 128, a.cl7_monotonic_ns() + MAX_AGE_NS, cl3.RetryPolicy(1, MAX_AGE_NS, ()),
                collect, a.cl7_monotonic_ns, a.cl7_wait_ns))
            captured = a.cl7_clock()
            plan = {"domain": DOMAIN, "version": 1, "kind": "PREPARED", "selection_sha256": expected_selection_sha256,
                "target_root": p["target_root"], "dispatch_plan_sha256": expected_dispatch_plan_sha256,
                "cash_plan_sha256": record[2], "cash_result_sha256": result_sha,
                "before_authority": pending.to_canonical_dict(), "before_owners": before,
                "transaction_id": "v4-full-fill:" + expected_dispatch_plan_sha256 + ":" + record[2],
                "started_at": deadline.started_at, "captured_at": captured, "reads": tape.rows,
                "operations": {"raw_account_id": a.policy.account_id, "from_inclusive": graph.first_from,
                    "to_exclusive": until, "limit": 128, "max_pages": 4, "max_items": 128,
                    "requests": requests, "responses": responses,
                    "rub_positions": next(row["response"] for row in tape.rows if row["method"] == "get_positions")},
                "pins": graph.snapshot().pins.to_dict(), "checkpoint": sync._cp(cp),
                "covered_until": graph.covered_until, "config_sha256": p["config_sha256"]}
            after, candidate, execution = _economics(a, context, plan)
            plan.update(after_owners=after, portfolio_candidate=candidate.to_dict(), execution=execution)
            plan = _parse(_canonical(plan))
            guard()
            with _owner_locks(a, r, ledger=True), _locked_bound_snapshot(binding, state) as view:
                guard(); sync._verify_rows(binding, state)
                _need(view.export_bytes() == state.raw, "SOURCE_CHANGED")
                digest = _write(path, plan, a.cl7_identity_key, PLAN_FIELDS)
                _point(fault_injector, "closure.after_plan")
                guard(); view.assert_active()
            return _finish(a, r, p, expected_selection_sha256, expected_dispatch_plan_sha256,
                           timely=deadline.check, fault=fault_injector,
                           _validated=(plan, digest, context, evidence_check, bound))


def recover_selected_full_fill(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_dispatch_plan_sha256: str,
        expected_closure_plan_sha256: str, fault_injector: Callable | None = None) -> VersionedClosureResult:
    """Only reproduce a saved, cash-backed owner prefix. No provider or cash write."""
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        p = cut._load_prepared(a, r, root, expected_selection_sha256, _check_initial_owners=False)
        _, actual = _read(_folder(root, expected_dispatch_plan_sha256) / "plan.json", a.cl7_identity_key, PLAN_FIELDS)
        _need(actual == expected_closure_plan_sha256, "PLAN_HASH_MISMATCH")
        return _finish(a, r, p, expected_selection_sha256, expected_dispatch_plan_sha256,
                       timely=lambda: None, fault=fault_injector)
