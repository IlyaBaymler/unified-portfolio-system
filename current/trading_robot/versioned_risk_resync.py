"""Explicit, ledger-explained cash-flow resync of selected v4 Risk state.

This is NOT order admission, arm, or broker execution.  A recent committed
owner refresh supplies the observations. External flows shift preserved risk
baselines; fees/corrections remain P&L. The HWM is replayed from its pre-flow
anchor, not incremented twice after a raw-NAV owner refresh. All persisted
changes are tied to immutable source/owner plans and remain DISARMED.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from . import cash_observation_versions as versions
from . import versioned_owner_refresh as refresh
from . import versioned_runtime_cutover as cut
from . import versioned_selected_sync as sync
from .cash_ledger_domain import LedgerTransaction
from .exact_cash_settlement import _pairs, _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .journal import JournalEvent
from .portfolio_model import PortfolioState
from .risk import RiskEngine, RiskPolicy, RiskSnapshot, RiskState, _date_key, _week_key
from .runtime_cash_authority import RuntimeCashAuthorityRecord as Authority
from .runtime_cash_authority import RuntimeCashAuthorityState as State
from .runtime_cash_authority import _transition_pair
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha
from .versioned_operational_store import _effect

DOMAIN = "V4_VERIFIED_CASH_FLOW_RESYNC_V1"
HELD = "VERSIONED_CASH_FLOW_RESYNC_HELD"
DONE = "VERSIONED_CASH_FLOW_RESYNC_COMMITTED"
PLAN_FIELDS = {"domain", "version", "kind", "selection_sha256", "target_root", "before_authority",
               "before_owners", "after_risk", "risk_policy", "prepared_at", "pins", "checkpoint", "explanation"}
RESULT_FIELDS = {"domain", "version", "kind", "plan_sha256", "completed_at"}
MAX_BYTES = 2 * 1024 * 1024


class VerifiedCashFlowError(RuntimeError):
    """Finite refusal. An unresolved Risk maintenance HOLD is never guessed away."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise VerifiedCashFlowError("CASH_FLOW_RESYNC_" + code)


def _point(fault: Callable | None, name: str) -> None:
    if fault is not None:
        fault(name)


def _dir(root: Path, name: str) -> Path:
    path = root / "cash_flow_resync" / name
    _safe_path(path)
    return path


def _read(path: Path, key: bytes, fields: set[str]) -> tuple[dict, str]:
    _safe_path(path)
    _need(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, "PLAN_UNAVAILABLE")
    raw = path.read_bytes()
    _need(len(raw) <= MAX_BYTES, "PLAN_TOO_LARGE")
    doc = json.loads(raw, object_pairs_hook=_pairs)
    _need(type(doc) is dict and set(doc) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = doc["payload"]
    _need(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
          and type(body["version"]) is int and body["version"] == 1, "SCHEMA_INVALID")
    _need(type(doc["hmac_sha256"]) is str and hmac.compare_digest(doc["hmac_sha256"],
          hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()), "SIGNATURE_INVALID")
    return body, _sha(_canonical(doc))


def _write(path: Path, body: dict, key: bytes, fields: set[str]) -> str:
    _safe_path(path)
    _need(not path.exists(), "PLAN_ALREADY_EXISTS")
    raw = _sealed(body, key)
    _need(len(raw) <= MAX_BYTES, "PLAN_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path)
    atomic_write_json(path, _parse(raw), retry_delays=(), jitter_fraction=0,
                      write_checksum=False, keep_last_good=False)
    _need(_read(path, key, fields) == (body, _sha(raw)), "READBACK_FAILED")
    return _sha(raw)


def _kopecks(value: object) -> int:
    _need(not isinstance(value, bool), "MONEY_INVALID")
    d = Decimal(str(value))
    _need(d.is_finite(), "MONEY_INVALID")
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _number(value: object) -> Decimal:
    _need(not isinstance(value, bool), "BASELINE_INVALID")
    d = Decimal(str(value))
    _need(d.is_finite() and d > 0, "BASELINE_INVALID")
    return d


def _held(a: Any, plan: dict, digest: str) -> Authority:
    before = Authority.from_canonical_dict(plan["before_authority"])
    held = a.cash_authority_manager._change(before, at=plan["prepared_at"], kind=HELD,
        state=State.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING, pending_dispatch_proof_sha256=digest)
    _transition_pair(before, held)
    return held


def _after(a: Any, plan: dict, plan_sha: str, result: dict, digest: str) -> Authority:
    _need(result["kind"] == "COMMITTED" and result["plan_sha256"] == plan_sha, "RESULT_INVALID")
    versions._time(result["completed_at"])
    held = _held(a, plan, plan_sha)
    after = a.cash_authority_manager._change(held, at=result["completed_at"], kind=DONE,
        state=State.EXACT_CASH_VERSIONED_DISARMED, activation_context_sha256=digest,
        pending_dispatch_proof_sha256=None)
    _transition_pair(held, after)
    return after


def _completed(a: Any, p: dict, selection: str, record: Authority, *, semantic: bool = True):
    digest = versions._hash(record.activation_context_sha256)
    result, found = _read(_dir(Path(p["target_root"]), "results") / (digest + ".json"),
                          a.cl7_identity_key, RESULT_FIELDS)
    _need(found == digest, "RESULT_HASH_INVALID")
    plan_sha = versions._hash(result["plan_sha256"])
    plan = _plan(a, p, selection, plan_sha, semantic=semantic)
    _need(record == _after(a, plan, plan_sha, result, digest), "AUTHORITY_RESULT_MISMATCH")
    return plan, plan_sha


def _timeline(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    """Find the last acknowledged cash anchor; replay intervening actual plans."""
    _need(record.sha256 not in seen and len(seen) < 16, "CHAIN_LIMIT")
    seen = seen | {record.sha256}
    if record.activation_context_sha256 == selection:
        _need(record.to_canonical_dict() == cut._commit_plan(a, p, selection)["after"], "ROOT_CHANGED")
        return p["owners_before"]["risk"], None, []
    if record.transition_kind == DONE:
        plan, _ = _completed(a, p, selection, record)
        return plan["after_risk"], plan["pins"], []
    if record.transition_kind == refresh.DONE:
        plan, _ = refresh._completed(a, p, selection, record)
        anchor, pins, events = _timeline(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]), seen=seen)
        events.append({"kind": "SNAPSHOT", "at": plan["captured_at"],
                       "risk": plan["after_owners"]["risk"], "portfolio": plan["after_owners"]["portfolio"]})
        return anchor, pins, events
    _need(record.transition_kind in {sync.COMMITTED, sync.ABORTED}, "UNSUPPORTED_PARENT")
    result, digest = sync._load(sync._dir(Path(p["target_root"]), "results") /
        (record.activation_context_sha256 + ".json"), a.cl7_identity_key, sync._RESULT)
    plan, before, after, event = sync._plan_data(a, p, result["plan_sha256"])
    _need(record == sync._after(a, plan, result["plan_sha256"], result, digest, before, after, event)[0],
          "SYNC_RESULT_INVALID")
    anchor, pins, events = _timeline(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]), seen=seen)
    if result["outcome"] == "COMMITTED":
        events.append({"kind": "BATCH", "at": plan["prepared_at"], "body": after.record_bodies[-1]})
    return anchor, pins, events


def _adjust_risk(anchor: RiskState, current: RiskState, portfolio: PortfolioState, events: list,
                 policy: RiskPolicy, at: str) -> tuple[RiskState, dict]:
    """Pure accounting derivation, not an authority or a persistence API.

    Shift absolute baselines by external flow only. Replay HWM from the anchor
    so raw-NAV refreshes do not cause the same deposit to be counted twice.
    Existing kill switches, execution IDs and counters are never cleared.
    """
    now = refresh._utc(at)
    _need(current.risk_resync_required and current.risk_resync_source == "EXTERNAL_CASH_CHANGE",
          "CASH_RESYNC_NOT_REQUIRED")
    _need(not anchor.risk_resync_required, "ANCHOR_ALREADY_BLOCKED")
    _need(anchor.daily_date == current.daily_date == _date_key(now)
          and anchor.weekly_key == current.weekly_key == _week_key(now), "PERIOD_CHANGE_UNSUPPORTED")
    _need(_kopecks(anchor.last_cash_rub) == _kopecks(current.last_cash_rub)
          == _kopecks(current.risk_resync_cash_before_rub), "CASH_ANCHOR_CHANGED")
    stable = ("daily_turnover_rub", "daily_order_count", "recorded_execution_ids", "last_execution_at")
    _need(all(getattr(anchor, k) == getattr(current, k) for k in stable), "EXECUTION_CHANGED")
    high = _number(anchor.high_watermark_equity_rub)
    flow_nano = 0
    fee_nano = 0
    tx_hashes = []
    last_observed_at = refresh._utc(anchor.last_snapshot_at) if anchor.last_snapshot_at else None
    for event in events:
        event_time = refresh._utc(event["at"])
        _need(_date_key(event_time) == anchor.daily_date and _week_key(event_time) == anchor.weekly_key,
              "INTERDAY_FLOW_UNSUPPORTED")
        if event["kind"] == "BATCH":
            batch_flow = 0
            for row in event["body"]["entries"]:
                if row["transaction_json_ascii"] is None:
                    continue
                tx = LedgerTransaction.from_canonical_dict(_parse(row["transaction_json_ascii"]))
                amount = _effect(tx)
                _need(tx.sha256 == row["transaction_sha256"] and tx.sha256 not in tx_hashes,
                      "DUPLICATE_OR_CHANGED_TRANSACTION")
                tx_hashes.append(tx.sha256)
                classification = tx.classification.value
                if classification in {"DEPOSIT", "WITHDRAWAL"}:
                    _need(amount % 10_000_000 == 0, "SUBKOPECK_EXTERNAL_FLOW_UNSUPPORTED")
                    _need((amount > 0) if classification == "DEPOSIT" else (amount < 0), "FLOW_SIGN_INVALID")
                    effective = refresh._utc(tx.effective_at)
                    _need(last_observed_at is not None and
                          last_observed_at <= effective <= refresh._utc(portfolio.snapshot_at),
                          "FLOW_OUTSIDE_ANCHOR_WINDOW")
                    batch_flow += amount
                else:
                    _need(classification == "COMMISSION" and amount < 0, "NON_CASH_FLOW_OPERATION")
                    fee_nano += amount
            flow_nano += batch_flow
            high += Decimal(batch_flow) / 10**9
            _need(high > 0, "NONPOSITIVE_FLOW_ADJUSTED_HWM")
        else:
            _need(event["kind"] == "SNAPSHOT", "EVENT_INVALID")
            rs = RiskState.from_dict(event["risk"])
            _need(rs.daily_date == anchor.daily_date and rs.weekly_key == anchor.weekly_key,
                  "PERIOD_CHANGE_UNSUPPORTED")
            high = max(high, _number(event["portfolio"]["account"]["total_value"]))
            _need(last_observed_at is not None and event_time >= last_observed_at, "SNAPSHOT_ORDER_INVALID")
            last_observed_at = event_time
    flow = Decimal(flow_nano) / 10**9
    daily, weekly = (_number(getattr(anchor, key)) + flow for key in
                     ("daily_start_equity_rub", "weekly_start_equity_rub"))
    _need(daily > 0 and weekly > 0, "NONPOSITIVE_ADJUSTED_BASELINE")
    equity = _number(portfolio.account.total_value)
    high = max(high, equity)
    cleared = {k: None for k in ("risk_resync_reason", "risk_resync_set_at", "risk_resync_source",
        "risk_resync_cash_before_rub", "risk_resync_cash_observed_rub", "risk_resync_equity_observed_rub",
        "risk_resync_snapshot_at")}
    _need(all(math.isfinite(float(v)) for v in (daily, weekly, high)), "FLOAT_BASELINE_OVERFLOW")
    after = replace(current, daily_start_equity_rub=float(daily), weekly_start_equity_rub=float(weekly),
        high_watermark_equity_rub=float(high), risk_resync_required=False, **cleared,
        last_cash_rub=portfolio.account.cash("rub").available, last_equity_rub=portfolio.account.total_value,
        last_snapshot_at=portfolio.snapshot_at, last_evaluated_at=now.isoformat())
    # Re-evaluate all existing account loss/halt rules; do not merely toggle resync.
    hold = RiskEngine(policy).evaluate(RiskSnapshot(now=now, strategy_target_lots=0, current_lots=0,
        price_rub=None, lot_size=1, portfolio_equity_rub=portfolio.account.total_value,
        cash_rub=portfolio.account.cash("rub").available, snapshot_at=refresh._utc(portfolio.snapshot_at),
        position_reconciled=True, pending_order=False, mode="SANDBOX_EXECUTION"), after)
    _need(not hold.state.risk_resync_required and not hold.decision.order_allowed, "RESYNC_REAPPEARED")
    _need(all(getattr(hold.state, k) == getattr(current, k) for k in stable), "RISK_ACCOUNTING_CHANGED")
    _need(not current.kill_switch_active or hold.state.kill_switch_active, "KILL_SWITCH_CLEARED")
    _need(hold.state.instrument_kill_switches == current.instrument_kill_switches, "INSTRUMENT_HALT_CHANGED")
    return hold.state, {"external_flow_nano": str(flow_nano), "ordinary_fee_effect_nano": str(fee_nano),
        "transaction_sha256s": tx_hashes, "adjusted_daily_baseline_rub": str(daily),
        "adjusted_weekly_baseline_rub": str(weekly), "adjusted_high_watermark_rub": str(high),
        "daily_pnl_rub": str(equity-daily), "account_risk_decision": hold.decision.to_dict()}


def _derive(a: Any, p: dict, selection: str, before_a: Authority, policy_dict: dict, at: str):
    cp, pins, _, raw = sync._lineage(a, p, selection, before_a)
    owners = sync._owner_anchor(a, p, selection, before_a)
    _need(before_a.transition_kind == refresh.DONE and raw is not None, "RECENT_OWNER_REFRESH_REQUIRED")
    g = sync._graph(a, raw)
    portfolio = PortfolioState.from_dict(owners["portfolio"])
    current = RiskState.from_dict(owners["risk"])
    for t in (portfolio.snapshot_at, before_a.operations_complete_through):
        _need(t is not None and 0 <= timestamp_ns(at)-timestamp_ns(t) <= MAX_AGE_NS, "PLAN_EVIDENCE_STALE")
    anchor_dict, anchor_pins, events = _timeline(a, p, selection, before_a)
    anchor = RiskState.from_dict(anchor_dict)
    # Historical proof replay must use its authenticated Central anchor, not
    # today's queue. Live entry separately checks actual owners against this anchor.
    from .central_order_manager import CentralOrderState
    central = CentralOrderState.from_dict(owners["central"])
    _need(not portfolio.blocking and not central.blocking_intent, "OWNER_BLOCKED")
    _need(not central.queued, "CENTRAL_NOT_QUIESCENT")
    # At first resync the cash anchor precedes the acknowledged fee correction.
    # That difference is an expense, not an external flow.
    if anchor_pins is None:
        anchor_cash = g.seed_projection.baseline_cash_nano
    else:
        # Find the exact persisted source prefix selected by the prior resync.
        prefixes = [g.seed_projection.expected_cash_nano] + [int(b["cash_after_nano"]) for b in g.record_bodies]
        index = next((i for i in range(len(g.record_bodies)+1)
            if (g.data["batches"][i-1]["sha256"] if i else _sha(g.data["migration_json_ascii"].encode("ascii")))
            == anchor_pins["batch_head_sha256"]), None)
        _need(index is not None, "ANCHOR_SOURCE_MISSING")
        anchor_cash = prefixes[index]
    _need(_kopecks(anchor.last_cash_rub) == anchor_cash // 10_000_000, "UNEXPLAINED_INITIAL_CASH")
    _need(_kopecks(portfolio.account.cash("rub").available) == g.cash_nano // 10_000_000,
          "OWN_BUDGET_LIMITED_OR_CASH_MISMATCH")
    _need(refresh._utc(current.last_snapshot_at) == refresh._utc(portfolio.snapshot_at), "RISK_SNAPSHOT_MISMATCH")
    policy = RiskPolicy(**policy_dict)
    _need(policy.enabled and policy.portfolio_policy_configured and policy.portfolio_policy_mode == "ENFORCED"
          and policy.policy_hash == a.risk_runtime.current_policy_hash(), "POLICY_CHANGED")
    after, explanation = _adjust_risk(anchor, current, portfolio, events, policy, at)
    seed_expense = g.seed_projection.correction_delta_nano if anchor_pins is None else 0
    _need(g.cash_nano - anchor_cash == int(explanation["external_flow_nano"])
          + int(explanation["ordinary_fee_effect_nano"]) + seed_expense, "UNEXPLAINED_CASH_DELTA")
    explanation["seed_fee_correction_effect_nano"] = str(seed_expense)
    explanation.update(anchor_cash_nano=str(anchor_cash), current_cash_nano=str(g.cash_nano),
                       anchor_risk_sha256=_sha(_canonical(anchor_dict)))
    return cp, pins, owners, after, explanation


def _plan(a: Any, p: dict, selection: str, digest: str, *, semantic: bool = True) -> dict:
    versions._hash(digest)
    plan, actual = _read(_dir(Path(p["target_root"]), "plans") / (digest+".json"), a.cl7_identity_key, PLAN_FIELDS)
    _need(actual == digest and plan["kind"] == "PREPARED" and plan["selection_sha256"] == selection
          and plan["target_root"] == p["target_root"], "PLAN_IDENTITY_INVALID")
    versions._time(plan["prepared_at"])
    parent = Authority.from_canonical_dict(plan["before_authority"])
    if semantic:
        cp, pins, owners, after, explanation = _derive(a, p, selection, parent, plan["risk_policy"], plan["prepared_at"])
        _need(plan["pins"] == pins.to_dict() and plan["checkpoint"] == sync._cp(cp)
              and plan["before_owners"] == owners and plan["after_risk"] == after.to_dict()
              and plan["explanation"] == explanation, "PLAN_SEMANTICS_INVALID")
    _held(a, plan, digest)
    return plan


def resync_lineage(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    plan, _ = _completed(a, p, selection, record)
    return sync._lineage(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]),
                         seen=seen | {record.activation_context_sha256})


def committed_owners(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    plan, _ = _completed(a, p, selection, record, semantic=False)
    prior = sync._owner_anchor(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]), seen=seen)
    _need(prior == plan["before_owners"], "OWNER_CHAIN_MISMATCH")
    return {**prior, "risk": plan["after_risk"]}


@dataclass(frozen=True, slots=True)
class CashFlowResyncProof:
    plan_sha256: str
    before_authority_sha256: str
    confirmation: str


@dataclass(frozen=True, slots=True)
class CashFlowResyncResult:
    plan_sha256: str
    authority_sha256: str
    risk_state_sha256: str
    replay: bool

    def public_summary(self) -> dict:
        return {**asdict(self), "status": "EXPLAINED_CASH_RESYNC_DISARMED", "runtime_authority_granted": False,
                "order_admission_performed": False, "risk_execution_written": False, "new_money_transactions": 0}


def _fresh(a: Any, plan: dict) -> None:
    now = timestamp_ns(a.cl7_clock())
    before = Authority.from_canonical_dict(plan["before_authority"])
    for value in (plan["prepared_at"], plan["before_owners"]["portfolio"]["snapshot_at"],
                  before.operations_complete_through):
        _need(value is not None and 0 <= now-timestamp_ns(value) <= MAX_AGE_NS, "EVIDENCE_STALE")


def prepare_cash_flow_resync(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_authority_sha256: str) -> CashFlowResyncProof:
    """Prepare a recent, ledger-explained plan. Writes only private plan evidence."""
    r, root, selection = recovery, cut._path(target_root), expected_selection_sha256
    with a.cash_authority_manager.store.locked():
        p = sync._load_current_prepared(a, r, root, selection)
        before = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        _need(before.sha256 == expected_authority_sha256, "AUTHORITY_CAS_CONFLICT")
        policy, created = r.risk._load_policy()
        _need(not created, "POLICY_UNAVAILABLE")
        at = a.cl7_clock()
        cp, pins, owners, after, explanation = _derive(a, p, selection, before, asdict(policy), at)
        body = {"domain": DOMAIN, "version": 1, "kind": "PREPARED", "selection_sha256": selection,
            "target_root": p["target_root"], "before_authority": before.to_canonical_dict(),
            "before_owners": owners, "after_risk": after.to_dict(), "risk_policy": asdict(policy),
            "prepared_at": at, "pins": pins.to_dict(), "checkpoint": sync._cp(cp), "explanation": explanation}
        body = _parse(_canonical(body))
        with _owner_locks(a, r, ledger=True), sync._open(a, p, cp) as binding, binding.locked_binding() as (_, view):
            _fresh(a, body)
            _need(sync._load_current_prepared(a, r, root, selection) == p and
                  a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == before,
                  "OWNERS_CHANGED")
            _need(view.pins == pins, "SOURCE_CHANGED")
            digest = _sha(_sealed(body, a.cl7_identity_key))
            path = _dir(root, "plans") / (digest+".json")
            if path.exists():
                _need(_read(path, a.cl7_identity_key, PLAN_FIELDS) == (body,digest), "PLAN_CHANGED")
            else:
                _write(path, body, a.cl7_identity_key, PLAN_FIELDS)
            view.assert_active()
        return CashFlowResyncProof(digest, before.sha256, "ACKNOWLEDGE VERIFIED CASH FLOW " + digest)


def _finish(a: Any, r: Any, p: dict, selection: str, digest: str, *, fresh: bool, fault=None):
    start = a.cl7_monotonic_ns()
    plan = _plan(a, p, selection, digest)
    before = Authority.from_canonical_dict(plan["before_authority"])
    held = _held(a, plan, digest)
    cp, pins, _, _ = sync._lineage(a, p, selection, before)
    owners = plan["before_owners"]
    last = start
    def time_check():
        nonlocal last
        tick = a.cl7_monotonic_ns()
        _need(type(start) is int and type(tick) is int and start <= last <= tick <= start+MAX_AGE_NS,
              "DEADLINE_EXCEEDED")
        last = tick
        if fresh:
            _fresh(a, plan)
    with _owner_locks(a, r, ledger=True), sync._open(a, p, cp) as binding, binding.locked_binding() as (_, view):
        registry_state = binding._load()
        _need(registry_state.checkpoint == cp and registry_state.pending is None and view.pins == pins,
              "SOURCE_OR_REGISTRY_CHANGED")
        def check():
            time_check(); view.assert_active(); sync._verify_rows(binding, registry_state)
            _need(cut._load_prepared(a,r,Path(p["target_root"]),selection,_check_initial_owners=False) == p,
                  "CONFIG_OR_SOURCE_CHANGED")
            _need(_read(_dir(Path(p["target_root"]),"plans")/(digest+".json"),a.cl7_identity_key,PLAN_FIELDS)
                  == (plan,digest), "PLAN_CHANGED")
            now = cut._owners(a,r)
            _need(now["portfolio"] == owners["portfolio"] and now["central"] == owners["central"], "OWNERS_CHANGED")
            _need(now["risk"] in (owners["risk"],plan["after_risk"]), "RISK_PREFIX_CONFLICT")
            return Authority.from_canonical_dict(now["authority"]), now["risk"]
        current, risk = check()
        if current not in (before,held):
            _need(current.transition_kind == DONE and risk == plan["after_risk"], "AUTHORITY_PREFIX_CONFLICT")
            done, done_sha = _completed(a,p,selection,current,semantic=False)
            _need(done_sha == digest and done == plan, "DIFFERENT_COMPLETED_PLAN")
            return CashFlowResyncResult(digest,current.sha256,_sha(_canonical(risk)),True)
        if current == before:
            _need(fresh and risk == owners["risk"], "EXPLICIT_CONFIRMATION_REQUIRED")
            a.cash_authority_manager.store._commit_unlocked(held,
                expected_revision=before.record_revision,expected_sha256=before.sha256)
            _point(fault,"resync.after_hold")
        current, risk = check()
        _need(current == held, "AUTHORITY_CHANGED")
        if risk == owners["risk"]:
            r.risk.state_store.save_account_while_locked(a.policy.account_id,RiskState.from_dict(plan["after_risk"]))
            _point(fault,"resync.after_risk")
        _need(check() == (held,plan["after_risk"]), "RISK_NOT_WRITTEN")
        result_path = _dir(Path(p["target_root"]),"resolutions")/(digest+".json")
        if result_path.exists():
            result,result_sha = _read(result_path,a.cl7_identity_key,RESULT_FIELDS)
        else:
            result={"domain":DOMAIN,"version":1,"kind":"COMMITTED","plan_sha256":digest,"completed_at":a.cl7_clock()}
            result_sha=_write(result_path,result,a.cl7_identity_key,RESULT_FIELDS)
        after=_after(a,plan,digest,result,result_sha)
        index=_dir(Path(p["target_root"]),"results")/(result_sha+".json")
        if index.exists():
            _need(_read(index,a.cl7_identity_key,RESULT_FIELDS)==(result,result_sha),"RESULT_CHANGED")
        else:
            _write(index,result,a.cl7_identity_key,RESULT_FIELDS)
        _point(fault,"resync.after_result")
        r.manager.journal.record(JournalEvent(category="cash_flow_resync",event_type="VERIFIED_CASH_FLOW_RESYNC",
            mode="SANDBOX_EXECUTION",status="DISARMED",timestamp_utc=a.cl7_clock(),
            payload={"plan_sha256":digest,"result_sha256":result_sha,"runtime_authority_granted":False}))
        _point(fault,"resync.after_audit")
        _need(check()==(held,plan["after_risk"]),"OWNERS_CHANGED")
        a.cash_authority_manager.store._commit_unlocked(after,
            expected_revision=held.record_revision,expected_sha256=held.sha256)
        _point(fault,"resync.after_authority")
        return CashFlowResyncResult(digest,after.sha256,_sha(_canonical(plan["after_risk"])),False)


def confirm_cash_flow_resync(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_plan_sha256: str, confirmation: str,
        fault_injector: Callable | None = None) -> CashFlowResyncResult:
    """Explicit scoped acknowledgement, not a generic reset or trading permission."""
    versions._hash(expected_plan_sha256)
    _need(confirmation == "ACKNOWLEDGE VERIFIED CASH FLOW " + expected_plan_sha256, "CONFIRMATION_REQUIRED")
    root = cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        p=cut._load_prepared(a,recovery,root,expected_selection_sha256,_check_initial_owners=False)
        return _finish(a,recovery,p,expected_selection_sha256,expected_plan_sha256,fresh=True,fault=fault_injector)


def recover_cash_flow_resync(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_plan_sha256: str,
        fault_injector: Callable | None = None) -> CashFlowResyncResult:
    """Offline completion only after a durable confirmed HOLD, or exact replay."""
    root=cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        p=cut._load_prepared(a,recovery,root,expected_selection_sha256,_check_initial_owners=False)
        return _finish(a,recovery,p,expected_selection_sha256,expected_plan_sha256,fresh=False,fault=fault_injector)
