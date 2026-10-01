"""One explicitly armed v4 intent: checked dispatch, irreversible attempt, lookup.

This is a separate sandbox route, not a v1 LockedDispatchProof type conversion.
It never settles cash, releases reservations, records an execution or auto-rearms.
A durable attempt is consumed BEFORE Central IN_FLIGHT and the one physical POST.
After any uncertain prefix only identity lookup is allowed; not-found is not a
permission to resubmit. All provider calls in qualification are synthetic.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator

from . import cash_ledger_opening_reconciliation as cl4
from . import cash_observation_versions as versions
from . import reporting_risk_cash_context as cl6
from . import versioned_risk_admission as admission
from . import versioned_runtime_cutover as cut
from . import versioned_selected_sync as sync
from .broker_read_adapters import BrokerEnvironment
from .central_order_manager import CentralOrderIntent, CentralOrderState
from .exact_cash_settlement import _pairs, _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, OwnFundsEvidence, _uint, timestamp_ns
from .instrument_runtime import InstrumentRuntime
from .multi_instrument_config import MultiInstrumentProfile
from .multi_instrument_strategy import StrategyCandleLoader
from .portfolio_model import PortfolioState
from .portfolio_preflight import PortfolioSnapshotLease
from .risk import RiskPolicy, RiskState
from .runtime_cash_authority import RuntimeCashAuthorityRecord as Authority
from .runtime_cash_authority import RuntimeCashAuthorityState as State
from .runtime_cash_authority import _transition_pair
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha
from .versioned_operational_store import OperationalPins
from .versioned_runtime_adapter import intent_fingerprint
from .versioned_source_binding import BindingCheckpoint

DOMAIN = "CL7_VERSIONED_ONE_SHOT_DISPATCH_V1"
ARM = "VERSIONED_ORDER_ARMED"
ATTEMPT = "VERSIONED_ORDER_ATTEMPT_RECORDED"
DISARM = "VERSIONED_ORDER_DISARMED"
MARKER = "CL7_VERSIONED_DISPATCH_PLAN="
MAX_BYTES = 4 * 1024 * 1024
ARM_FIELDS = {"domain", "version", "kind", "selection_sha256", "target_root", "admitted_authority",
              "admission_plan_sha256", "intent_id", "intent_sha256", "armed_at"}
PLAN_FIELDS = {"domain", "version", "kind", "arm_sha256", "selection_sha256", "target_root",
               "started_at", "evaluated_at", "attempt_at", "central_before", "reads", "locked_reads",
               "market", "evaluation", "request_binding", "request"}


class VersionedDispatchError(RuntimeError):
    """Finite error; never echo broker payloads, raw IDs or credentials."""


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise VersionedDispatchError("V4_DISPATCH_" + code)


def _point(callback: Callable | None, name: str) -> None:
    if callback is not None:
        callback(name)


def _path(root: Path, group: str, digest: str) -> Path:
    path = root / "versioned_dispatch" / group / (versions._hash(digest) + ".json")
    _safe_path(path)
    return path


def _read(path: Path, key: bytes, fields: set[str]) -> tuple[dict, str]:
    _safe_path(path)
    _need(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, "PLAN_UNAVAILABLE")
    value = json.loads(path.read_bytes(), object_pairs_hook=_pairs)
    _need(type(value) is dict and set(value) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = value["payload"]
    _need(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
          and type(body["version"]) is int and body["version"] == 1, "SCHEMA_INVALID")
    signature = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
    _need(type(value["hmac_sha256"]) is str and hmac.compare_digest(signature, value["hmac_sha256"]),
          "SIGNATURE_INVALID")
    return body, _sha(_canonical(value))


def _save(root: Path, group: str, body: dict, key: bytes, fields: set[str]) -> str:
    body = _parse(_canonical(body))
    raw = _sealed(body, key)
    _need(len(raw) <= MAX_BYTES, "PLAN_TOO_LARGE")
    digest = _sha(raw)
    path = _path(root, group, digest)
    if path.exists():
        existing, observed = _read(path, key, fields)
        _need(existing == body and observed == digest, "PLAN_CONFLICT")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(path, _parse(raw), retry_delays=(), jitter_fraction=0,
                      write_checksum=False, keep_last_good=False)
    _need(_read(path, key, fields) == (body, digest), "PLAN_READBACK_FAILED")
    return digest


class _Deadline:
    def __init__(self, a: Any):
        self.a = a
        self.started_at = a.cl7_clock()
        self.wall = self.last_wall = timestamp_ns(self.started_at)
        self.tick = self.last_tick = a.cl7_monotonic_ns()
        _need(type(self.tick) is int and self.tick >= 0, "CLOCK_INVALID")

    def check(self) -> None:
        wall, tick = timestamp_ns(self.a.cl7_clock()), self.a.cl7_monotonic_ns()
        _need(type(tick) is int and self.last_tick <= tick <= self.tick + MAX_AGE_NS
              and self.last_wall <= wall <= self.wall + MAX_AGE_NS, "EXPIRED")
        self.last_wall, self.last_tick = wall, tick


def _armed(a: Any, arm: dict, digest: str) -> Authority:
    before = Authority.from_canonical_dict(arm["admitted_authority"])
    after = a.cash_authority_manager._change(before, at=arm["armed_at"], kind=ARM,
        state=State.EXACT_CASH_VERSIONED_ARMED, activation_context_sha256=digest)
    _transition_pair(before, after)
    return after


def _pending(a: Any, arm: dict, arm_sha: str, plan: dict, plan_sha: str) -> Authority:
    before = _armed(a, arm, arm_sha)
    after = a.cash_authority_manager._change(before, at=plan["attempt_at"], kind=ATTEMPT,
        state=State.EXACT_CASH_VERSIONED_DISPATCH_PENDING,
        pending_dispatch_proof_sha256=plan_sha, post_attempt_count=before.post_attempt_count + 1)
    _transition_pair(before, after)
    return after


def _load_arm(a: Any, r: Any, root: Path, selection: str, arm_sha: str) -> tuple[dict, dict, dict, dict]:
    arm, observed = _read(_path(root, "arms", arm_sha), a.cl7_identity_key, ARM_FIELDS)
    _need(observed == arm_sha and arm["kind"] == "ARM" and arm["selection_sha256"] == selection
          and arm["target_root"] == str(root), "ARM_IDENTITY_INVALID")
    p = cut._load_prepared(a, r, root, selection, _check_initial_owners=False)
    admitted = Authority.from_canonical_dict(arm["admitted_authority"])
    _need(admitted.state is State.EXACT_CASH_VERSIONED_DISARMED and admitted.transition_kind == admission.DONE,
          "ADMISSION_REQUIRED")
    ap, digest = admission._completed(a, r, p, selection, admitted)
    owners = {"portfolio": ap["before_owners"]["portfolio"], "risk": ap["risk_after"], "central": ap["central_after"]}
    central = CentralOrderState.from_dict(owners["central"])
    _need(len(central.queued) == 1 and central.blocking_intent is None, "QUEUE_NOT_SINGLE")
    intent = central.queued[0]
    _need(digest == arm["admission_plan_sha256"] and intent.intent_id == arm["intent_id"]
          and intent_fingerprint(intent) == arm["intent_sha256"], "ARM_REQUEST_CHANGED")
    _armed(a, arm, arm_sha)
    return arm, p, ap, owners


def _check_owners(a: Any, r: Any, p: dict, owners: dict, authority: Authority,
                  central: CentralOrderState | None = None) -> None:
    _need(cut._config(a, r) == p["config_sha256"] and cut._identity(a, r) == p["runtime_identity"], "CONFIG_CHANGED")
    expected = {**owners, "authority": authority.to_canonical_dict()}
    if central is not None:
        expected["central"] = central.to_dict()
    _need(cut._owners(a, r) == expected, "OWNER_CHANGED")
    a.cl7_own_funds_policy.binding_guard()


@contextmanager
def _locked_source(a: Any, r: Any, p: dict, ap: dict) -> Iterator[tuple[Any, Any]]:
    cp, pins = BindingCheckpoint(**ap["checkpoint"]), OperationalPins(**ap["pins"])
    with _owner_locks(a, r, ledger=True), sync._open(a, p, cp) as registry:
        with registry.locked_binding() as (adapter, view):
            state = registry._load()
            _need(state.checkpoint == cp and state.pending is None and view.pins == pins
                  and _sha(view.export_bytes()) == ap["source_export_sha256"], "SOURCE_OR_REGISTRY_CHANGED")
            yield adapter, view


def _fresh_dependency(arm: dict, ap: dict, now: str) -> None:
    current = timestamp_ns(now)
    for at in (arm["armed_at"], ap["evaluated_at"], ap["started_at"]):
        _need(0 <= current - timestamp_ns(at) <= MAX_AGE_NS, "ADMISSION_OR_ARM_EXPIRED")
    before = Authority.from_canonical_dict(arm["admitted_authority"])
    admission.refresh._require_recent_operations(before, now)
    stamp = timestamp_ns(cl6._normalize_portfolio_timestamp(
        ap["before_owners"]["portfolio"]["snapshot_at"])[0])
    _need(0 <= current - stamp <= MAX_AGE_NS, "PORTFOLIO_EXPIRED")


@dataclass(frozen=True, slots=True)
class ArmResult:
    arm_sha256: str
    authority_sha256: str
    intent_id: str

    def public_summary(self) -> dict:
        return {"domain": DOMAIN, "status": "ARMED_FOR_ONE_INTENT", "arm_sha256": self.arm_sha256,
                "broker_request_sent": False, "automatic_rearm_allowed": False}


@dataclass(frozen=True, slots=True)
class DispatchResult:
    status: str
    plan_sha256: str
    intent_id: str
    authority_sha256: str
    broker_order_id: str | None = None
    post_called_this_invocation: bool = False
    lookup_performed: bool = False

    def public_summary(self) -> dict:
        return {"domain": DOMAIN, "status": self.status, "plan_sha256": self.plan_sha256,
                "post_called_this_invocation": self.post_called_this_invocation,
                "lookup_performed": self.lookup_performed, "settlement_verified": False,
                "authority_clear_allowed": False, "automatic_rearm_allowed": False}


def arm_selected_order(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
        expected_authority_sha256: str, expected_intent_id: str, confirmation: str,
        fault_injector: Callable | None = None) -> ArmResult:
    """Explicit local consent for one already admitted intent; no provider calls.

    Required phrase: ARM VERSIONED ORDER <intent ID> <admitted authority SHA>.
    Expired admission is NOT renewed. This is source API, not autonomous consent.
    """
    r, root = recovery, cut._path(target_root)
    _need(type(confirmation) is str and confirmation ==
          f"ARM VERSIONED ORDER {expected_intent_id} {expected_authority_sha256}", "CONFIRMATION_INVALID")
    with a.cash_authority_manager.store.locked():
        deadline = _Deadline(a)
        p = cut._load_prepared(a, r, root, expected_selection_sha256, _check_initial_owners=False)
        before = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        _need(before.sha256 == versions._hash(expected_authority_sha256) and before.transition_kind == admission.DONE
              and before.state is State.EXACT_CASH_VERSIONED_DISARMED, "ADMISSION_REQUIRED")
        ap, admission_sha = admission._completed(a, r, p, expected_selection_sha256, before)
        owners = {"portfolio": ap["before_owners"]["portfolio"], "risk": ap["risk_after"], "central": ap["central_after"]}
        central = CentralOrderState.from_dict(owners["central"])
        _need(len(central.queued) == 1 and central.blocking_intent is None, "QUEUE_NOT_SINGLE")
        intent = central.queued[0]
        _need(intent.intent_id == expected_intent_id, "INTENT_MISMATCH")
        arm = {"domain": DOMAIN, "version": 1, "kind": "ARM", "selection_sha256": expected_selection_sha256,
               "target_root": str(root), "admitted_authority": before.to_canonical_dict(),
               "admission_plan_sha256": admission_sha, "intent_id": intent.intent_id,
               "intent_sha256": intent_fingerprint(intent), "armed_at": a.cl7_clock()}
        with _locked_source(a, r, p, ap) as (_, view):
            _check_owners(a, r, p, owners, before)
            deadline.check(); _fresh_dependency(arm, ap, a.cl7_clock())
            digest = _save(root, "arms", arm, a.cl7_identity_key, ARM_FIELDS)
            _point(fault_injector, "arm.after_plan")
            r.manager.transaction_coordinator._record("VERSIONED_ORDER_ARM_CONFIRMED",
                account_id=a.policy.account_id, instrument_id=intent.candidate.instrument_id, mode="SANDBOX_EXECUTION", status="ARMED",
                payload={"arm_sha256": digest})
            _check_owners(a, r, p, owners, before)
            deadline.check(); _fresh_dependency(arm, ap, a.cl7_clock()); view.assert_active()
            after = _armed(a, arm, digest)
            a.cash_authority_manager.store._commit_unlocked(after,
                expected_revision=before.record_revision, expected_sha256=before.sha256)
            _point(fault_injector, "arm.after_authority")
            return ArmResult(digest, after.sha256, intent.intent_id)


def disarm_selected_order(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
        expected_arm_sha256: str, expected_authority_sha256: str) -> Authority:
    """Revoke an unconsumed arm only. Does not cancel, remove or reauthorize QUEUED.

    Re-arming this revoked/expired admission is intentionally not implemented.
    """
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        arm, p, ap, owners = _load_arm(a, r, root, expected_selection_sha256, expected_arm_sha256)
        before = _armed(a, arm, expected_arm_sha256)
        _need(before.sha256 == expected_authority_sha256, "AUTHORITY_CAS_CONFLICT")
        with _locked_source(a, r, p, ap):
            _check_owners(a, r, p, owners, before)
            after = a.cash_authority_manager._change(before, at=a.cl7_clock(), kind=DISARM,
                state=State.EXACT_CASH_VERSIONED_DISARMED)
            _transition_pair(before, after)
            a.cash_authority_manager.store._commit_unlocked(after,
                expected_revision=before.record_revision, expected_sha256=before.sha256)
            return after


def _market(raw: Any, uid: str) -> None:
    _need(type(raw) is dict and raw.get("apiTradeAvailableFlag") is True
          and raw.get("marketOrderAvailableFlag") is True
          and raw.get("tradingStatus") == "SECURITY_TRADING_STATUS_NORMAL_TRADING", "MARKET_BLOCKED")
    for name in ("instrumentUid", "instrumentId"):
        _need(name not in raw or raw[name] == uid, "MARKET_IDENTITY_MISMATCH")
    _canonical(raw)


def _request(intent: CentralOrderIntent) -> dict:
    c = intent.candidate
    _need(c.order_type == "MARKET" and c.time_in_force in {"FILL_AND_KILL", "FILL_OR_KILL"}, "ORDER_PROFILE_INVALID")
    return {"account_id": c.account_id, "instrument_id": c.instrument_id, "lots": c.requested_lots,
            "direction": c.direction, "order_id": intent.intent_id, "order_type": c.order_type,
            "time_in_force": c.time_in_force}


def _evaluate_request(a: Any, r: Any, ap: dict, owners: dict, tape: admission._Tape, *,
                      started: str, evaluated: str, raw: bytes) -> dict:
    # Native replay uses the pre-admission Central projection, as native
    # PortfolioRiskRuntime does. The selected request is excluded ONLY for the
    # hypothetical decision; final cash binding counts its saved reserve once.
    evaluation_owners = {**owners, "central": ap["before_owners"]["central"]}
    candidate, _, state, evaluation = admission._evaluate(a, r, evaluation_owners,
        InstrumentRuntime.from_dict(ap["runtime"]), MultiInstrumentProfile.from_dict(ap["profile"]),
        RiskPolicy(**ap["policy"]), tape, started_at=started, evaluated_at=evaluated,
        cash_nano=ap["cash_nano"], pins=OperationalPins(**ap["pins"]), source_raw=raw)
    current = CentralOrderState.from_dict(owners["central"]).queued[0]
    _need(candidate == current.candidate and tape.index == len(tape.rows), "REAUTHORIZATION_REQUIRED")
    # Evaluation is read-only: only non-guard diagnostics may differ, never risk limits/baselines.
    _need(admission.risk_state_guard_hash(state) == admission.risk_state_guard_hash(RiskState.from_dict(owners["risk"])),
          "RISK_MAINTENANCE_REQUIRED")
    return evaluation


def _inputs(a: Any, owners: dict, ap: dict, rows: list, own_rows: list, *, started: str,
            evaluated: str, own: Any) -> dict:
    central = CentralOrderState.from_dict(owners["central"])
    intent = central.queued[0]
    common = {k: cut._common(a)[k] for k in ("identity_key", "identity_key_id", "account_scope_sha256")}
    common["environment"] = BrokerEnvironment.SANDBOX
    cash = cl4.build_broker_rub_position_cash_proof(own_rows[0]["response"], raw_account_id=a.policy.account_id,
        as_of=own.started_at, evaluated_at=evaluated, response_complete=True, **common)
    _need(cash.cash.minor_units == ap["cash_nano"], "CASH_MISMATCH")
    budget_rows = [v for v in rows if v["method"] == "get_max_lots"]
    tape = admission._Tape(rows=budget_rows)
    budget_policy = a.cash_authority_manager.buying_budget_policy
    budget = budget_policy.acquire(tape, account_scope_sha256=common["account_scope_sha256"],
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        clock=lambda: started, monotonic_ns=lambda: 0)
    _need(tape.index == len(tape.rows), "EXTRA_BUDGET_READS")
    lease = PortfolioSnapshotLease.from_state(PortfolioState.from_dict(owners["portfolio"]),
        leased_at=admission._utc(evaluated).isoformat())
    portfolio = cl6.build_portfolio_identity_evidence(lease, evaluated_at=evaluated, **common)
    risk = cl6.build_risk_guard_evidence(RiskPolicy(**ap["policy"]), RiskState.from_dict(owners["risk"]),
        raw_account_id=a.policy.account_id, captured_at=started, evaluated_at=evaluated, **common)
    return dict(intent=intent, central_state=central, expected_intent_sha256=intent_fingerprint(intent),
        own_policy=a.cl7_own_funds_policy, own_funds=own, broker_cash=cash, own_buying=budget,
        portfolio=portfolio, risk_guard=risk, buying_scope_sha256=budget_policy.scope_sha256, evaluated_at=evaluated)


def _write_central(a: Any, expected: CentralOrderState, after: CentralOrderState) -> None:
    _need(a.manager.store._load_unlocked(expected_account_id=a.policy.account_id) == expected, "CENTRAL_CAS_CONFLICT")
    a.manager.store._save_unlocked(after)
    _need(a.manager.store._load_unlocked(expected_account_id=a.policy.account_id) == after, "CENTRAL_READBACK_FAILED")


def _inflight(before: CentralOrderState, digest: str, at: str) -> CentralOrderState:
    intent = before.queued[0].transition("IN_FLIGHT", at=admission._utc(at).isoformat(), detail=MARKER + digest)
    return replace(before.replace_intent(intent), revision=before.revision + 1, updated_at=intent.updated_at)


def _outcome(before: CentralOrderState, status: str, at: str, exchange: str | None = None) -> CentralOrderState:
    intent = before.blocking_intent
    _need(intent is not None, "BLOCKING_INTENT_MISSING")
    after = intent.transition(status, at=admission._utc(at).isoformat(),
        detail="V4 correlated acknowledgement" if status == "SUBMITTED" else "V4 provider outcome uncertain",
        broker_order_id=exchange, uncertainty_reason="V4_LOOKUP_REQUIRED" if status == "UNCERTAIN" else None)
    return replace(before.replace_intent(after), revision=before.revision + 1, updated_at=after.updated_at)


def _exchange(raw: Any, intent: CentralOrderIntent, *, lookup: bool = False) -> str:
    _need(type(raw) is dict, "RESPONSE_INVALID")
    _need(len(_canonical(raw)) <= 262144, "RESPONSE_TOO_LARGE")
    _need("currency" not in raw or raw["currency"] in {"RUB", "rub"}, "RESPONSE_CURRENCY_INVALID")
    _need(raw.get("orderRequestId") == intent.intent_id, "RESPONSE_REQUEST_MISMATCH")
    value = raw.get("orderId")
    _need(type(value) is str and 0 < len(value) <= 128 and value.strip() == value
          and all(32 <= ord(c) < 127 for c in value), "EXCHANGE_ID_INVALID")
    _need(intent.broker_order_id is None or value == intent.broker_order_id, "EXCHANGE_ID_CHANGED")
    c = intent.candidate
    for field, expected in {"accountId": c.account_id, "brokerAccountId": c.account_id,
                           "instrumentUid": c.instrument_id, "direction": "ORDER_DIRECTION_" + c.direction,
                           "orderType": "ORDER_TYPE_" + c.order_type}.items():
        _need((not lookup and field not in raw) or field in {"accountId", "brokerAccountId"} and field not in raw
              or raw.get(field) == expected, "RESPONSE_IDENTITY_MISMATCH")
    if lookup or "lotsRequested" in raw:
        _need(_uint(raw.get("lotsRequested")) == c.requested_lots, "RESPONSE_QUANTITY_MISMATCH")
    if lookup:
        from .exact_order_receipt import _uint as receipt_uint
        filled = receipt_uint(raw.get("lotsExecuted"))
        status = raw.get("executionReportStatus")
        _need(0 <= filled <= c.requested_lots and status in {"EXECUTION_REPORT_STATUS_" + v
              for v in ("NEW", "FILL", "PARTIALLYFILL", "CANCELLED", "REJECTED")}, "RESPONSE_STATUS_INVALID")
        _need((status.endswith("_FILL") and filled == c.requested_lots)
              or (status.endswith(("_NEW", "_REJECTED")) and filled == 0)
              or (status.endswith("_PARTIALLYFILL") and 0 < filled < c.requested_lots)
              or (status.endswith("_CANCELLED") and filled < c.requested_lots), "RESPONSE_STATUS_CONFLICT")
    return value


def dispatch_selected_order(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
        expected_arm_sha256: str, expected_authority_sha256: str, expected_intent_id: str,
        fault_injector: Callable | None = None) -> DispatchResult:
    """One native post_order_once call, only after the attempt marker read-back.

    Current evidence is checked again UNDER owner/source locks. A fault, timeout,
    rejection, bad correlation, or stale proof after consumption remains pending.
    No path in this method clears an attempt or resubmits a pending intent.
    """
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        _need(current.state is State.EXACT_CASH_VERSIONED_ARMED and current.sha256 == expected_authority_sha256,
              "NOT_ARMED_OR_ALREADY_ATTEMPTED")
        deadline = _Deadline(a)
        arm, p, ap, owners = _load_arm(a, r, root, expected_selection_sha256, expected_arm_sha256)
        armed = _armed(a, arm, expected_arm_sha256)
        _need(current == armed and arm["intent_id"] == expected_intent_id, "ARM_REQUEST_MISMATCH")
        _need(callable(getattr(a.transport, "post_order_once", None)), "ONE_SHOT_TRANSPORT_REQUIRED")
        central = CentralOrderState.from_dict(owners["central"]); intent = central.queued[0]
        def guard() -> None:
            deadline.check(); _fresh_dependency(arm, ap, a.cl7_clock())
            _check_owners(a, r, p, owners, armed)
        guard()
        market = deepcopy(a.transport.get_trading_status(intent.candidate.instrument_id))
        guard(); _market(market, intent.candidate.instrument_id)
        tape = admission._Tape(provider=a.transport, guard=guard)
        runtime = InstrumentRuntime.from_dict(ap["runtime"]); profile = MultiInstrumentProfile.from_dict(ap["profile"])
        StrategyCandleLoader(tape).load(runtime, profile, now=admission._utc(deadline.started_at))
        tape.get_last_prices([intent.candidate.instrument_id]); tape.get_orders(a.policy.account_id)
        tape.get_positions(a.policy.account_id)
        a.cash_authority_manager.buying_budget_policy.acquire(tape,
            account_scope_sha256=current.account_scope_sha256, identity_key=a.cl7_identity_key,
            identity_key_id=a.cl7_identity_key_id, clock=lambda: deadline.started_at, monotonic_ns=lambda: 0)
        with _locked_source(a, r, p, ap) as (runtime_adapter, view):
            guard()
            a.manager._validate_portfolio_for_dispatch(r.manager.repository, intent,
                locked_portfolio_state=PortfolioState.from_dict(owners["portfolio"]))
            evaluated = a.cl7_clock()
            evaluation = _evaluate_request(a, r, ap, owners, admission._Tape(rows=tape.rows),
                started=deadline.started_at, evaluated=evaluated, raw=view.export_bytes())
            final_tape = admission._Tape(provider=a.transport, guard=guard)
            own = a.cl7_own_funds_policy.acquire(final_tape, intent, central,
                clock=a.cl7_clock, monotonic_ns=a.cl7_monotonic_ns)
            evaluated = a.cl7_clock(); guard()
            # Re-evaluate the native risk decision at the post-final-read time.
            evaluation = _evaluate_request(a, r, ap, owners, admission._Tape(rows=tape.rows),
                started=deadline.started_at, evaluated=evaluated, raw=view.export_bytes())
            inputs = _inputs(a, owners, ap, tape.rows, final_tape.rows, started=deadline.started_at,
                             evaluated=evaluated, own=own)
            binding = runtime_adapter.build_request_binding(view, **inputs)
            runtime_adapter.validate_request_binding(binding, view, now=a.cl7_clock(), **inputs)
            plan = {"domain": DOMAIN, "version": 1, "kind": "DISPATCH", "arm_sha256": expected_arm_sha256,
                "selection_sha256": expected_selection_sha256, "target_root": str(root),
                "started_at": deadline.started_at, "evaluated_at": evaluated, "attempt_at": a.cl7_clock(),
                "central_before": central.to_dict(), "reads": tape.rows, "locked_reads": final_tape.rows,
                "market": market, "evaluation": evaluation, "request_binding": _parse(binding.canonical_bytes),
                "request": _request(intent)}
            digest = _save(root, "plans", plan, a.cl7_identity_key, PLAN_FIELDS)
            _point(fault_injector, "dispatch.after_plan"); guard()
            r.manager.transaction_coordinator._record("VERSIONED_DISPATCH_VALIDATED", account_id=a.policy.account_id,
                instrument_id=intent.candidate.instrument_id, mode="SANDBOX_EXECUTION", status="ATTEMPT_REQUIRED", payload={"plan_sha256": digest})
            guard(); runtime_adapter.validate_request_binding(binding, view, now=a.cl7_clock(), **inputs)
            pending = _pending(a, arm, expected_arm_sha256, plan, digest)
            a.cash_authority_manager.store._commit_unlocked(pending,
                expected_revision=armed.record_revision, expected_sha256=armed.sha256)
            _point(fault_injector, "dispatch.after_attempt")
            # No guard after this point assumes the old authority. All failures
            # preserve the consumed attempt; the broad catch never hides a fault
            # in test hooks and never sends if current identities are unknown.
            def final_check(expected_central: CentralOrderState) -> None:
                deadline.check(); _fresh_dependency(arm, ap, a.cl7_clock())
                _check_owners(a, r, p, owners, pending, expected_central)
                runtime_adapter.validate_request_binding(binding, view, now=a.cl7_clock(), **inputs)
            final_check(central)
            inflight = _inflight(central, digest, plan["attempt_at"])
            _write_central(a, central, inflight)
            _point(fault_injector, "dispatch.after_inflight")
            final_check(inflight)
            # One immutable argument tuple, no price or margin override, no retry.
            q = plan["request"]
            try:
                response = a.transport.post_order_once(q["account_id"], q["instrument_id"], q["lots"], q["direction"],
                    order_id=q["order_id"], order_type=q["order_type"], time_in_force=q["time_in_force"])
            except Exception:
                # A direct broker rejection is deliberately not used to clear
                # v4 authority until zero-terminal settlement is qualified.
                _check_owners(a, r, p, owners, pending, inflight)
                uncertain = _outcome(inflight, "UNCERTAIN", a.cl7_clock())
                _write_central(a, inflight, uncertain)
                return DispatchResult("SUBMISSION_UNCERTAIN", digest, intent.intent_id, pending.sha256,
                                      post_called_this_invocation=True)
            _point(fault_injector, "dispatch.after_post")
            _check_owners(a, r, p, owners, pending, inflight)
            try:
                exchange = _exchange(response, intent)
                _need(not any(i.intent_id != intent.intent_id and i.broker_order_id == exchange
                              for i in central.intents), "EXCHANGE_ALREADY_BOUND")
            except Exception:
                _write_central(a, inflight, _outcome(inflight, "UNCERTAIN", a.cl7_clock()))
                return DispatchResult("SUBMISSION_UNCERTAIN", digest, intent.intent_id, pending.sha256,
                                      post_called_this_invocation=True)
            submitted = _outcome(inflight, "SUBMITTED", a.cl7_clock(), exchange)
            _write_central(a, inflight, submitted)
            _point(fault_injector, "dispatch.after_submitted")
            return DispatchResult("SUBMITTED", digest, intent.intent_id, pending.sha256, exchange, True)


def _historical_plan(a: Any, r: Any, arm: dict, ap: dict, owners: dict, plan: dict,
                     adapter: Any, view: Any) -> None:
    """Recompute archived final-funds and binding content without reviving its lease.

    The original random lease ID is audit-only. A new live view checks the same
    complete graph; all semantic fields must match except that ephemeral ID.
    This validation never produces an execution permit or refreshes timestamps.
    """
    _replay_historical_plan(a, r, arm, ap, owners, plan, view.export_bytes(),
        lambda **inputs: adapter.build_request_binding(view, **inputs))


def _historical_plan_from_export(a: Any, r: Any, arm: dict, ap: dict, owners: dict,
        plan: dict, raw: bytes) -> None:
    """Offline audit of the exact pre-fill snapshot, NOT a revived live permit."""
    from .versioned_runtime_adapter import _derive_request_binding
    pins = OperationalPins(**ap["pins"])
    graph = sync._graph(a, raw)
    _need(graph.snapshot().pins == pins and _sha(raw) == ap["source_export_sha256"],
          "ARCHIVED_SOURCE_MISMATCH")
    common = cut._common(a)
    def rebuild(**inputs):
        return _derive_request_binding(source_export=raw, pins=pins,
            lease_id=plan["request_binding"]["payload"]["lease_id"], **common, **inputs)
    _replay_historical_plan(a, r, arm, ap, owners, plan, raw, rebuild)


def _replay_historical_plan(a: Any, r: Any, arm: dict, ap: dict, owners: dict,
        plan: dict, raw: bytes, rebuild: Callable) -> None:
    _need(timestamp_ns(plan["started_at"]) <= timestamp_ns(plan["evaluated_at"])
          <= timestamp_ns(plan["attempt_at"])
          <= timestamp_ns(plan["started_at"]) + MAX_AGE_NS, "PLAN_TIME_INVALID")
    _fresh_dependency(arm, ap, plan["attempt_at"])
    original = CentralOrderState.from_dict(owners["central"]).queued[0]
    _market(plan["market"], original.candidate.instrument_id)
    evaluation = _evaluate_request(a, r, ap, owners, admission._Tape(rows=plan["reads"]),
        started=plan["started_at"], evaluated=plan["evaluated_at"], raw=raw)
    _need(evaluation == plan["evaluation"], "ECONOMIC_PLAN_INVALID")
    sealed = plan["request_binding"]
    _need(type(sealed) is dict and set(sealed) == {"payload", "hmac_sha256"}, "BINDING_INVALID")
    payload = sealed["payload"]
    _need(type(payload) is dict and type(sealed["hmac_sha256"]) is str
          and hmac.compare_digest(sealed["hmac_sha256"],
              hmac.new(a.cl7_identity_key, _canonical(payload), hashlib.sha256).hexdigest()), "BINDING_SIGNATURE_INVALID")
    versions._hash(payload["lease_id"])
    stored_own = OwnFundsEvidence.from_canonical_dict(payload["own_funds"])
    _need(timestamp_ns(plan["started_at"]) <= timestamp_ns(stored_own.started_at)
          <= timestamp_ns(stored_own.completed_at) <= timestamp_ns(plan["evaluated_at"]), "FINAL_READ_TIME_INVALID")
    tape = admission._Tape(rows=plan["locked_reads"])
    # First call starts acquisition; later checkpoints/complete retain the
    # recorded final acquisition time. All requests/responses are replay-checked.
    first = True
    def clock() -> str:
        nonlocal first
        if first:
            first = False
            return stored_own.started_at
        return stored_own.completed_at
    central = CentralOrderState.from_dict(owners["central"])
    own = a.cl7_own_funds_policy.acquire(tape, original, central, clock=clock, monotonic_ns=lambda: 0)
    _need(own == stored_own and tape.index == len(tape.rows), "FINAL_OWN_FUNDS_INVALID")
    inputs = _inputs(a, owners, ap, plan["reads"], plan["locked_reads"], started=plan["started_at"],
                     evaluated=plan["evaluated_at"], own=own)
    rebuilt = rebuild(**inputs)
    expected = _parse(rebuilt.payload_bytes)
    expected["lease_id"] = payload["lease_id"]
    _need(expected == payload, "BINDING_CONTENT_CHANGED")


def _central_prefix(before: CentralOrderState, observed: CentralOrderState, digest: str, at: str) -> None:
    """Accept only our exact zero-fill queued/in-flight/ack/uncertain structures."""
    if observed == before:
        return
    base = _inflight(before, digest, at)
    if observed == base:
        return
    selected = before.queued[0]
    found = next((i for i in observed.intents if i.intent_id == selected.intent_id), None)
    _need(found is not None and found.status in {"UNCERTAIN", "SUBMITTED"}, "CENTRAL_PREFIX_CONFLICT")
    # A successful lookup can convert an IN_FLIGHT prefix to SUBMITTED. We do
    # NOT invent UNCERTAIN->SUBMITTED, which native Central deliberately forbids.
    expected = _outcome(base, found.status, cl6._normalize_portfolio_timestamp(found.updated_at)[0],
                        found.broker_order_id if found.status == "SUBMITTED" else None)
    _need(observed == expected, "CENTRAL_PREFIX_CONFLICT")


def recover_selected_dispatch_identity(a: Any, *, recovery: Any, target_root: object,
        expected_selection_sha256: str, expected_plan_sha256: str) -> DispatchResult:
    """Only look up the ORIGINAL request ID. Never POST, clear, fill or settle.

    An unknown/404/timeout response leaves all owners unchanged. Successful
    correlation may bind exchange identity; quantities and fees are NOT booked.
    No old proof is made fresh by this recovery. A separate v4 settlement gate
    must consume receipt, operations and all owner evidence.
    """
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        plan, digest = _read(_path(root, "plans", expected_plan_sha256), a.cl7_identity_key, PLAN_FIELDS)
        _need(digest == expected_plan_sha256 and plan["kind"] == "DISPATCH"
              and plan["selection_sha256"] == expected_selection_sha256 and plan["target_root"] == str(root),
              "DISPATCH_PLAN_INVALID")
        arm, p, ap, owners = _load_arm(a, r, root, expected_selection_sha256, plan["arm_sha256"])
        pending = _pending(a, arm, plan["arm_sha256"], plan, digest)
        _need(a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == pending,
              "CONFIRMED_ATTEMPT_REQUIRED")
        before = CentralOrderState.from_dict(owners["central"]); original = before.queued[0]
        _need(plan["central_before"] == before.to_dict() and plan["request"] == _request(original), "PLAN_REQUEST_INVALID")
        # Reproduce all economics on their historical timestamps, never as a
        # renewed authorization. The source must still equal the pre-fill ledger.
        with _locked_source(a, r, p, ap) as (adapter, view):
            _historical_plan(a, r, arm, ap, owners, plan, adapter, view)
        inflight = _inflight(before, digest, plan["attempt_at"])
        observed = a.manager.state()
        _central_prefix(before, observed, digest, plan["attempt_at"])
        intent = next(i for i in observed.intents if i.intent_id == original.intent_id)
        _check_owners(a, r, p, owners, pending, observed)
        deadline = _Deadline(a)
        try:
            raw = a.transport.get_order_state(a.policy.account_id, original.intent_id, by_request_id=True)
        except Exception:
            raise VersionedDispatchError("V4_DISPATCH_LOOKUP_UNAVAILABLE_NO_RETRY") from None
        exchange = _exchange(raw, intent, lookup=True)
        _need(not any(i.intent_id != original.intent_id and i.broker_order_id == exchange
                      for i in observed.intents), "EXCHANGE_ALREADY_BOUND")
        deadline.check()
        with _locked_source(a, r, p, ap) as (_, view):
            _check_owners(a, r, p, owners, pending, observed); deadline.check(); view.assert_active()
            r.manager.transaction_coordinator._record("VERSIONED_DISPATCH_IDENTITY_OBSERVED",
                account_id=a.policy.account_id, instrument_id=original.candidate.instrument_id,
                mode="SANDBOX_EXECUTION", status="SETTLEMENT_REQUIRED",
                payload={"plan_sha256": digest, "response_sha256": _sha(_canonical(raw))})
            _check_owners(a, r, p, owners, pending, observed); deadline.check(); view.assert_active()
            # A found original request may bind a lost acknowledgement only in
            # native IN_FLIGHT (or the exact earlier marker/QUEUED prefix).
            # UNCERTAIN remains UNCERTAIN; the returned observation is not a
            # durable replacement receipt, financial completion or release.
            if observed == before:
                _write_central(a, before, inflight); observed = inflight
                intent = observed.blocking_intent
            if intent.status == "IN_FLIGHT":
                _write_central(a, observed, _outcome(observed, "SUBMITTED", a.cl7_clock(), exchange))
            return DispatchResult("UNCERTAIN_IDENTITY_OBSERVED" if intent.status == "UNCERTAIN"
                                  else "SUBMITTED_SETTLEMENT_REQUIRED",
                                  digest, original.intent_id, pending.sha256, exchange, lookup_performed=True)
