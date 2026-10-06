"""Ordered Portfolio/Risk refresh for a selected, still DISARMED v4 source.

The ledger and registry are read, never written.  A saved plan admits only
P-before/R-before -> P-after/R-before -> P-after/R-after.  A complete-document
CAS protects the canonical write, including same-business-revision snapshots.
Risk is evaluated with a HOLD target; it may retain/set a cash-resync block.
This is maintenance, not order admission, resync approval, arm or dispatch.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import cash_ledger_opening_reconciliation as cl4
from . import cash_observation_versions as versions
from . import versioned_runtime_cutover as cut
from . import versioned_selected_sync as sync
from .broker_read_adapters import BrokerEnvironment
from .central_order_manager import CentralOrderState
from .exact_cash_settlement import _pairs, _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .journal import JournalEvent
from .portfolio_adapters import BrokerPortfolioAdapter, RuntimePortfolioAdapter
from .portfolio_cash_observation import DesktopOwnCashPolicy
from .portfolio_manager import CanonicalPortfolioManager
from .portfolio_model import CompatibilityShadowStatus, PortfolioMigrationMetadata, PortfolioState, SnapshotFreshness
from .portfolio_observation import PortfolioObservationPolicy
from .portfolio_reconciler import PortfolioReconciler, ReconciliationContext
from .portfolio_repository import portfolio_document_checksum
from .risk import RiskEngine, RiskPolicy, RiskSnapshot, RiskState
from .runtime_cash_authority import RuntimeCashAuthorityRecord as Authority
from .runtime_cash_authority import RuntimeCashAuthorityState as State
from .runtime_cash_authority import _transition_pair
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _sealed, _sha
from .versioned_source_binding import BindingCheckpoint
from .versioned_operational_store import OperationalPins

DOMAIN = "V4_OWNER_REFRESH_REBUILT_V1"
HELD = "VERSIONED_OWNER_REFRESH_HELD"
DONE = "VERSIONED_OWNER_REFRESH_COMMITTED"
MAX_BYTES = 4 * 1024 * 1024
PLAN_FIELDS = {"domain", "version", "kind", "selection_sha256", "target_root", "before_authority",
               "before_owners", "after_owners", "portfolio_candidate", "transaction_id", "captured_at",
               "pins", "checkpoint", "reads", "risk_policy", "config_sha256"}
RESULT_FIELDS = {"domain", "version", "kind", "plan_sha256", "completed_at"}


class OwnerRefreshError(RuntimeError):
    """Finite refusal; persisted financial maintenance HOLD is not guessed away."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise OwnerRefreshError("OWNER_REFRESH_" + code)


def _point(fault: Callable[[str], None] | None, point: str) -> None:
    if fault is not None:
        fault(point)


def _dir(root: Path, name: str) -> Path:
    path = root / "owner_refresh" / name
    _safe_path(path)
    return path


def _read(path: Path, key: bytes, fields: set[str]) -> tuple[dict, str]:
    _safe_path(path)
    _need(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, "PLAN_MISSING_OR_TOO_LARGE")
    raw = path.read_bytes()
    _need(len(raw) <= MAX_BYTES, "PLAN_TOO_LARGE")
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
    _need(not path.exists(), "PLAN_ALREADY_EXISTS")
    raw = _sealed(body, key)
    _need(len(raw) <= MAX_BYTES, "PLAN_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path)
    atomic_write_json(path, json.loads(raw), retry_delays=(), jitter_fraction=0,
                      write_checksum=False, keep_last_good=False)
    actual, digest = _read(path, key, fields)
    _need(actual == body and digest == _sha(raw), "READBACK_FAILED")
    return digest


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class _ReadTape:
    """Bounded primary capture/replay; no provider is reachable during replay."""
    def __init__(self, *, provider: Any = None, rows: list | None = None, guard: Callable = lambda: None):
        self.provider, self.rows, self.index, self.guard = provider, ([] if rows is None else rows), 0, guard

    def __getattr__(self, name: str) -> Callable:
        _need(name in {"get_portfolio", "get_orders", "get_order_state", "get_positions", "get_max_lots"},
              "READ_METHOD_INVALID")
        def read(*args: Any, **kwargs: Any) -> Any:
            self.guard()
            _need(self.index < 12, "READ_LIMIT")
            request = {"method": name, "args": list(args), "kwargs": kwargs}
            if self.provider is None:
                _need(self.index < len(self.rows), "CAPTURE_INCOMPLETE")
                row = self.rows[self.index]
                _need(type(row) is dict and set(row) == {"method", "args", "kwargs", "response"}
                      and all(row[k] == v for k, v in request.items()), "CAPTURE_REQUEST_MISMATCH")
                result = row["response"]
            else:
                result = getattr(self.provider, name)(*args, **kwargs)
                self.rows.append({**request, "response": deepcopy(result)})
                _need(len(_canonical(self.rows)) <= MAX_BYTES // 2, "CAPTURE_TOO_LARGE")
            self.index += 1
            self.guard()
            return deepcopy(result)
        return read


def _positions(state: PortfolioState) -> list:
    return sorted((p.instrument_id, p.actual_lots, p.target_lots, p.ownership, p.target, p.origin)
                  for p in state.positions)


def _derive(a: Any, before: dict, reads: list, captured_at: str, tx: str,
            risk_policy: dict, pins: OperationalPins) -> tuple[PortfolioState, PortfolioState, RiskState]:
    """Replay the actual adapters/reconciler and HOLD Risk evaluation, not a flag."""
    previous = PortfolioState.from_dict(before["portfolio"])
    risk_before = RiskState.from_dict(before["risk"])
    central = CentralOrderState.from_dict(before["central"])
    _need(not central.queued and central.blocking_intent is None, "CENTRAL_NOT_QUIESCENT")
    _need(previous.portfolio_source == "CANONICAL" and previous.migration.complete
          and not previous.migration.legacy_read_path_enabled and not previous.blocking
          and previous.migration.compatibility_shadow_status is CompatibilityShadowStatus.OK,
          "CANONICAL_UNSUPPORTED")
    _need(not any(o.active or o.uncertain for p in previous.positions for o in p.pending_orders), "PENDING_ORDER")
    instruments = a.cl7_own_funds_policy.instruments
    tape = _ReadTape(rows=reads)
    portfolio, orders = PortfolioObservationPolicy(a.policy.account_id, instruments).acquire(tape, previous)
    cash = DesktopOwnCashPolicy(a.policy.account_id, instruments).acquire(tape, lambda _: None)
    _need(tape.index == len(reads), "CAPTURE_EXTRA_READS")
    positions_rows = [row for row in reads if row["method"] == "get_positions"]
    _need(len(positions_rows) == 1, "CASH_READ_COUNT")
    proof = cl4.build_broker_rub_position_cash_proof(positions_rows[0]["response"],
        raw_account_id=a.policy.account_id, account_scope_sha256=cut._common(a)["account_scope_sha256"],
        environment=BrokerEnvironment.SANDBOX, as_of=captured_at, evaluated_at=captured_at,
        response_complete=True, identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id)
    del proof  # Strict CL4 rejection of blocked/unsupported/malformed cash is required.
    # The pinned full ledger was validated separately; compare it with this exact RUB read.
    _need(int(cash.rub_position * 10**9) == before["cash_nano"], "CASH_MISMATCH")
    broker = cash.apply(BrokerPortfolioAdapter.from_api_portfolio(portfolio, account_id=a.policy.account_id,
                                        broker_orders=orders, snapshot_at=captured_at))
    _need(_utc(captured_at) > _utc(previous.snapshot_at), "SNAPSHOT_NOT_NEWER")
    runtime = RuntimePortfolioAdapter.from_portfolio_state(previous)
    candidate = PortfolioReconciler().reconcile(broker, runtime, previous=previous,
        context=ReconciliationContext(expected_account_id=a.policy.account_id, freshness=SnapshotFreshness.FRESH,
                                      generated_at=captured_at))
    candidate = CanonicalPortfolioManager._normalize_flat_positions(candidate)
    _need(not candidate.blocking and _positions(candidate) == _positions(previous)
          and not any(o.active or o.uncertain for p in candidate.positions for o in p.pending_orders),
          "POSITION_OR_OWNERSHIP_CHANGED")
    published = replace(candidate, revision=previous.revision + (candidate.decision_sha256 != previous.decision_sha256),
        portfolio_source="CANONICAL", migration=PortfolioMigrationMetadata.completed(
            source_schema=previous.migration.source_schema, migration_id=previous.migration.migration_id,
            migrated_at=previous.migration.migrated_at, shadow_status=CompatibilityShadowStatus.OK,
            detail="Canonical-only read path is active."), last_transaction_id=tx, last_transaction_status="COMMITTED")
    uid = sorted(instruments)[0]
    position = published.position(uid)
    lots = position.actual_lots if position else 0
    policy = RiskPolicy(**risk_policy)
    _need(policy.enabled and policy.policy_hash == a.risk_runtime.current_policy_hash(), "RISK_POLICY_CHANGED")
    assessment = RiskEngine(policy).evaluate(RiskSnapshot(now=_utc(captured_at), strategy_target_lots=lots,
        current_lots=lots, price_rub=position.current_price if position and position.current_price else None,
        lot_size=instruments[uid].lot_size, portfolio_equity_rub=published.account.total_value,
        cash_rub=published.account.cash("rub").available, snapshot_at=_utc(captured_at),
        position_reconciled=True, pending_order=False, mode="SANDBOX_EXECUTION"), risk_before)
    _need(not assessment.decision.order_allowed
          and assessment.state.recorded_execution_ids == risk_before.recorded_execution_ids, "UNEXPECTED_RISK_EXECUTION")
    return candidate, published, assessment.state


def _require_recent_operations(authority: Authority, captured_at: str) -> None:
    # A fresh price/cash snapshot does not refresh operation-history coverage.
    # Require the checked cash sync window immediately before owner capture.
    end = authority.operations_complete_through
    _need(end is not None and 0 <= timestamp_ns(captured_at) - timestamp_ns(end) <= MAX_AGE_NS,
          "OPERATIONS_WINDOW_STALE")


def _held(a: Any, plan: dict, digest: str) -> Authority:
    before = Authority.from_canonical_dict(plan["before_authority"])
    held = a.cash_authority_manager._change(before, at=plan["captured_at"], kind=HELD,
        state=State.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING, pending_dispatch_proof_sha256=digest)
    _transition_pair(before, held)
    return held


def _after(a: Any, plan: dict, plan_sha: str, result: dict, digest: str) -> Authority:
    _need(result["kind"] == "COMMITTED" and result["plan_sha256"] == plan_sha, "RESULT_INVALID")
    versions._time(result["completed_at"])
    held = _held(a, plan, plan_sha)
    after = a.cash_authority_manager._change(held, at=result["completed_at"], kind=DONE,
        state=State.EXACT_CASH_VERSIONED_DISARMED, pending_dispatch_proof_sha256=None,
        activation_context_sha256=digest)
    _transition_pair(held, after)
    return after


def _plan(a: Any, p: dict, selection: str, digest: str, *, semantic: bool = True) -> dict:
    versions._hash(digest)
    plan, found = _read(_dir(Path(p["target_root"]), "plans") / (digest + ".json"), a.cl7_identity_key, PLAN_FIELDS)
    _need(found == digest and plan["kind"] == "PREPARED" and plan["selection_sha256"] == selection
          and plan["target_root"] == p["target_root"] and plan["config_sha256"] == p["config_sha256"],
          "PLAN_IDENTITY_INVALID")
    versions._time(plan["captured_at"])
    _require_recent_operations(Authority.from_canonical_dict(plan["before_authority"]), plan["captured_at"])
    _need(type(plan["transaction_id"]) is str and plan["transaction_id"].startswith("owner-refresh:"), "TRANSACTION_INVALID")
    _need(set(plan["before_owners"]) == {"portfolio", "risk", "central", "cash_nano"}
          and set(plan["after_owners"]) == {"portfolio", "risk", "central"}, "OWNER_SCHEMA_INVALID")
    if semantic:
        candidate, published, risk = _derive(a, plan["before_owners"], plan["reads"], plan["captured_at"],
            plan["transaction_id"], plan["risk_policy"], OperationalPins(**plan["pins"]))
        _need(candidate.to_dict() == plan["portfolio_candidate"] and plan["after_owners"] == {
            "portfolio": published.to_dict(), "risk": risk.to_dict(), "central": plan["before_owners"]["central"]},
            "PLAN_SEMANTICS_INVALID")
    _held(a, plan, digest)
    return plan


def _completed(a: Any, p: dict, selection: str, record: Authority, *, semantic: bool = True) -> tuple[dict, str]:
    digest = versions._hash(record.activation_context_sha256)
    result, found = _read(_dir(Path(p["target_root"]), "results") / (digest + ".json"), a.cl7_identity_key, RESULT_FIELDS)
    _need(found == digest, "RESULT_HASH_INVALID")
    plan_sha = versions._hash(result["plan_sha256"])
    plan = _plan(a, p, selection, plan_sha, semantic=semantic)
    _need(record == _after(a, plan, plan_sha, result, digest), "AUTHORITY_MISMATCH")
    return plan, plan_sha


def owner_lineage(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    plan, _ = _completed(a, p, selection, record)
    parent = Authority.from_canonical_dict(plan["before_authority"])
    cp, pins, count, raw = sync._lineage(a, p, selection, parent, seen=seen | {record.activation_context_sha256})
    _need(plan["pins"] == pins.to_dict() and plan["checkpoint"] == sync._cp(cp), "SOURCE_LINEAGE_MISMATCH")
    # Monetary values are not authorised by a caller's prepared checksum alone.
    if raw is None:
        cash_nano = int(p["cash_nano"])
    else:
        cash_nano = sync._graph(a, raw).snapshot().cash_nano
    _need(plan["before_owners"]["cash_nano"] == cash_nano, "PLAN_CASH_MISMATCH")
    return cp, pins, count, raw


def committed_owners(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()) -> dict:
    # Identity guard only. Entry/resolution separately verifies full monetary
    # lineage and replayed plan semantics; this path rechecks exact signed bytes.
    plan, _ = _completed(a, p, selection, record, semantic=False)
    parent = Authority.from_canonical_dict(plan["before_authority"])
    prior = sync._owner_anchor(a, p, selection, parent, seen=seen)
    _need(all(plan["before_owners"][k] == prior[k] for k in prior), "OWNER_CHAIN_MISMATCH")
    return plan["after_owners"]


@dataclass(frozen=True, slots=True)
class OwnerRefreshResult:
    plan_sha256: str
    authority_sha256: str
    portfolio_document_checksum: str
    risk_state_sha256: str
    risk_resync_required: bool
    replay: bool

    def public_summary(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_RESULT", "status": "OWNERS_REFRESHED_DISARMED", **asdict(self),
                "runtime_authority_granted": False, "risk_execution_written": False, "new_money_transactions": 0}


def _report(a: Any, plan: dict, digest: str, after: Authority, replay: bool) -> OwnerRefreshResult:
    portfolio = PortfolioState.from_dict(plan["after_owners"]["portfolio"])
    risk = plan["after_owners"]["risk"]
    return OwnerRefreshResult(digest, after.sha256, portfolio_document_checksum(portfolio),
                              _sha(_canonical(risk)), risk["risk_resync_required"], replay)


def _finish(a: Any, r: Any, p: dict, selection: str, plan_sha: str, *, timely: Callable, fault=None):
    plan = _plan(a, p, selection, plan_sha)
    before_a = Authority.from_canonical_dict(plan["before_authority"])
    cp, pins, _, raw = sync._lineage(a, p, selection, before_a)
    prior = sync._owner_anchor(a, p, selection, before_a)
    _need(plan["pins"] == pins.to_dict() and plan["checkpoint"] == sync._cp(cp)
          and all(prior[k] == plan["before_owners"][k] for k in prior), "PLAN_PARENT_MISMATCH")
    cash = int(p["cash_nano"]) if raw is None else sync._graph(a, raw).snapshot().cash_nano
    _need(cash == plan["before_owners"]["cash_nano"], "PLAN_CASH_MISMATCH")
    held = _held(a, plan, plan_sha)
    current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
    if current not in (before_a, held):
        _need(current.transition_kind == DONE, "AUTHORITY_PREFIX_CONFLICT")
        completed, completed_sha = _completed(a, p, selection, current)
        _need(completed_sha == plan_sha, "DIFFERENT_COMPLETED_PLAN")
        with _owner_locks(a, r, ledger=True), sync._open(a, p, cp) as binding, binding.locked_binding() as (_, view):
            registry_state = binding._load()
            _need(registry_state.pending is None and registry_state.checkpoint == cp
                  and registry_state.pins == pins, "COMPLETED_REGISTRY_CHANGED")
            _need(sync._load_current_prepared(a, r, Path(p["target_root"]), selection) == p,
                  "COMPLETED_OWNERS_CHANGED")
            _need(view.pins == pins, "COMPLETED_SOURCE_CHANGED")
        return _report(a, completed, completed_sha, current, True)

    def check() -> int:
        timely()
        _need(cut._load_prepared(a, r, Path(p["target_root"]), selection, _check_initial_owners=False) == p,
              "CONFIG_OR_SOURCE_CHANGED")
        _need(_read(_dir(Path(p["target_root"]), "plans") / (plan_sha + ".json"), a.cl7_identity_key,
                    PLAN_FIELDS) == (plan, plan_sha), "PLAN_CHANGED")
        now = cut._owners(a, r)
        _need(now["authority"] in (before_a.to_canonical_dict(), held.to_canonical_dict()), "AUTHORITY_CHANGED")
        _need(now["central"] == prior["central"], "CENTRAL_CHANGED")
        for phase in range(3):
            expected_p = plan["after_owners" if phase >= 1 else "before_owners"]["portfolio"]
            expected_r = plan["after_owners" if phase >= 2 else "before_owners"]["risk"]
            if now["portfolio"] == expected_p and now["risk"] == expected_r:
                return phase
        raise OwnerRefreshError("OWNER_REFRESH_OWNER_PREFIX_CONFLICT")

    with sync._open(a, p, cp) as binding:
        verified_state = binding._load()
        _need(verified_state.pending is None and verified_state.checkpoint == cp
              and verified_state.pins == pins, "SOURCE_OR_REGISTRY_CHANGED")
        def source_check(view):
            view.assert_active()
            sync._verify_rows(binding, verified_state)
            _need(view.export_bytes() == verified_state.raw, "SOURCE_OR_REGISTRY_CHANGED")
        with _owner_locks(a, r, ledger=True), binding.locked_binding() as (_, view):
            phase = check(); source_check(view)
            if current == before_a:
                _need(phase == 0, "UNHELD_OWNER_WRITE")
                a.cash_authority_manager.store._commit_unlocked(held,
                    expected_revision=before_a.record_revision, expected_sha256=before_a.sha256)
                _point(fault, "refresh.after_hold"); timely()
        if phase == 0:
            previous = PortfolioState.from_dict(plan["before_owners"]["portfolio"])
            def transform(current_portfolio):
                _need(check() == 0 and current_portfolio.to_dict() == previous.to_dict(), "PORTFOLIO_CHANGED")
                return PortfolioState.from_dict(plan["portfolio_candidate"])
            r.manager.transaction_coordinator.commit("VERSIONED_OWNER_REFRESH", transform,
                expected_revision=previous.revision, expected_document_checksum=portfolio_document_checksum(previous),
                transaction_id=plan["transaction_id"], account_id=a.policy.account_id, mode="SANDBOX_EXECUTION")
            _point(fault, "refresh.after_portfolio")
        with _owner_locks(a, r, ledger=True), binding.locked_binding() as (_, view):
            phase = check(); source_check(view)
            _need(phase >= 1, "PORTFOLIO_NOT_WRITTEN")
            # Only a non-authoritative compatibility shadow may be reconstructed.
            canonical = PortfolioState.from_dict(plan["after_owners"]["portfolio"])
            _need(r.manager.transaction_coordinator.shadow_writer is not None
                  and r.manager.transaction_coordinator.shadow_writer.write(canonical) is CompatibilityShadowStatus.OK,
                  "SHADOW_WRITE_FAILED")
            if phase == 1:
                check(); view.assert_active()
                r.risk.state_store.save_account_while_locked(a.policy.account_id,
                                            RiskState.from_dict(plan["after_owners"]["risk"]))
                _point(fault, "refresh.after_risk")
            _need(check() == 2, "OWNERS_NOT_WRITTEN"); view.assert_active()
            result_path = _dir(Path(p["target_root"]), "resolutions") / (plan_sha + ".json")
            if result_path.exists():
                result, digest = _read(result_path, a.cl7_identity_key, RESULT_FIELDS)
            else:
                result = {"domain": DOMAIN, "version": 1, "kind": "COMMITTED", "plan_sha256": plan_sha,
                          "completed_at": a.cl7_clock()}
                digest = _write(result_path, result, a.cl7_identity_key, RESULT_FIELDS)
            after = _after(a, plan, plan_sha, result, digest)
            index = _dir(Path(p["target_root"]), "results") / (digest + ".json")
            if index.exists():
                _need(_read(index, a.cl7_identity_key, RESULT_FIELDS) == (result, digest), "RESULT_CHANGED")
            else:
                _write(index, result, a.cl7_identity_key, RESULT_FIELDS)
            r.manager.journal.record(JournalEvent(category="owner_refresh", event_type="VERSIONED_OWNERS_REFRESHED",
                mode="SANDBOX_EXECUTION", status="DISARMED", timestamp_utc=a.cl7_clock(),
                payload={"plan_sha256": plan_sha, "result_sha256": digest, "runtime_authority_granted": False}))
            _point(fault, "refresh.after_audit")
            _need(check() == 2, "OWNERS_CHANGED"); view.assert_active(); source_check(view); timely()
            a.cash_authority_manager.store._commit_unlocked(after,
                expected_revision=held.record_revision, expected_sha256=held.sha256)
            _point(fault, "refresh.after_authority")
            return _report(a, plan, plan_sha, after, False)


def refresh_selected_owners(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
                            expected_authority_sha256: str, fault_injector: Callable | None = None) -> OwnerRefreshResult:
    """Fresh, explicit owner maintenance. Never clear resync, cash HOLD or arm."""
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        p = sync._load_current_prepared(a, r, root, expected_selection_sha256)
        before_a = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        _need(before_a.sha256 == expected_authority_sha256, "AUTHORITY_CAS_CONFLICT")
        cp, pins, _, _ = sync._lineage(a, p, expected_selection_sha256, before_a)
        before = sync._owner_anchor(a, p, expected_selection_sha256, before_a)
        _require_recent_operations(before_a, a.cl7_clock())
        policy, auto_created = r.risk._load_policy()
        _need(not auto_created and r.manager.transaction_coordinator.shadow_writer is not None, "PROFILE_UNSUPPORTED")
        with sync._open(a, p, cp) as binding:
            state = binding._load()
            _need(state.pending is None and state.checkpoint == cp and state.pins == pins, "REGISTRY_CHANGED")
            snap = binding.source.snapshot()
            _need(snap.pins == pins, "SOURCE_CHANGED")
            before = {**before, "cash_nano": snap.cash_nano}
        start_tick, start_wall = a.cl7_monotonic_ns(), timestamp_ns(a.cl7_clock())
        last_tick, last_wall = start_tick, start_wall
        def timely():
            nonlocal last_tick, last_wall
            tick, wall = a.cl7_monotonic_ns(), timestamp_ns(a.cl7_clock())
            _need(type(tick) is int and type(start_tick) is int
                  and 0 <= start_tick <= last_tick <= tick <= start_tick + MAX_AGE_NS
                  and last_wall <= wall <= start_wall + MAX_AGE_NS, "STALE")
            last_tick, last_wall = tick, wall
        def read_guard(*_):
            timely()
            _need(sync._load_current_prepared(a, r, root, expected_selection_sha256) == p
                  and a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == before_a,
                  "SOURCE_CHANGED_DURING_CAPTURE")
        tape = _ReadTape(provider=r.manager.api, guard=read_guard)
        previous = PortfolioState.from_dict(before["portfolio"])
        r.policy.acquire(tape, previous, read_guard)
        r.cash_policy.acquire(tape, read_guard)
        captured = a.cl7_clock(); versions._time(captured)
        _require_recent_operations(before_a, captured)
        tx = "owner-refresh:" + str(uuid4())
        candidate, published, risk = _derive(a, before, tape.rows, captured, tx, asdict(policy), pins)
        plan = {"domain": DOMAIN, "version": 1, "kind": "PREPARED", "selection_sha256": expected_selection_sha256,
                "target_root": p["target_root"], "before_authority": before_a.to_canonical_dict(),
                "before_owners": before, "after_owners": {"portfolio": published.to_dict(), "risk": risk.to_dict(),
                                                          "central": before["central"]},
                "portfolio_candidate": candidate.to_dict(), "transaction_id": tx, "captured_at": captured,
                "pins": pins.to_dict(), "checkpoint": sync._cp(cp), "reads": tape.rows,
                "risk_policy": asdict(policy), "config_sha256": p["config_sha256"]}
        plan = json.loads(_canonical(plan))
        with _owner_locks(a, r, ledger=True), sync._open(a, p, cp) as binding, binding.locked_binding() as (_, view):
            read_guard(); _need(view.pins == pins, "SOURCE_CHANGED")
            digest = _sha(_sealed(plan, a.cl7_identity_key))
            _write(_dir(root, "plans") / (digest + ".json"), plan, a.cl7_identity_key, PLAN_FIELDS)
            _point(fault_injector, "refresh.after_plan"); timely(); view.assert_active()
        return _finish(a, r, p, expected_selection_sha256, digest, timely=timely, fault=fault_injector)


def recover_owner_refresh(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
                          expected_refresh_plan_sha256: str, fault_injector: Callable | None = None) -> OwnerRefreshResult:
    """Resume a verified prefix offline; the captured snapshot is not current IO."""
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        p = cut._load_prepared(a, r, root, expected_selection_sha256, _check_initial_owners=False)
        return _finish(a, r, p, expected_selection_sha256, expected_refresh_plan_sha256,
                       timely=lambda: None, fault=fault_injector)
