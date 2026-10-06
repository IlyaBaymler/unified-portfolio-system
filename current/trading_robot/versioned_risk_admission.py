"""Bounded native Strategy -> single Risk -> Portfolio Risk -> Central admission.

This path enqueues one request from a quiescent selected v4 source, never arms
or sends it.  A prepare callback in the real Central writer saves the exact
Risk/Central plan BEFORE either owner is changed.  Offline completion retains
old authorization timestamps and cannot stand in for fresh dispatch checks.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import cash_availability as cl5
from . import cash_ledger_opening_reconciliation as cl4
from . import cash_observation_versions as versions
from . import reporting_risk_cash_context as cl6
from . import versioned_financial_readers as readers
from . import versioned_owner_refresh as refresh
from . import versioned_runtime_cutover as cut
from . import versioned_selected_sync as sync
from .broker_read_adapters import BrokerEnvironment
from .central_order_manager import (CentralOrderCandidate, CentralOrderIntent, CentralOrderState,
    ExecutionAuthorization, PortfolioRiskAuthorizationProof, central_reservation_projection_hash)
from .exact_cash_settlement import _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .instrument_runtime import InstrumentRuntime
from .locking import InterProcessFileLock
from .multi_instrument_config import MultiInstrumentProfile
from .multi_instrument_strategy import StrategyCandleLoader, build_strategy_proposal
from .portfolio_model import PortfolioState
from .portfolio_preflight import PortfolioPreflightGate, PortfolioSnapshotLease
from .portfolio_risk_adapter import PortfolioRiskInputAdapter, PortfolioRiskInstrumentMetadata, portfolio_policy_from_risk_policy
from .portfolio_risk_evaluator import PortfolioRiskEvaluator
from .portfolio_risk_runtime import PortfolioRiskRuntime
from .risk import RiskEngine, RiskPolicy, RiskSnapshot, RiskState, average_true_range
from .risk_runtime import RiskRuntimeOutcome, risk_state_guard_hash
from .runtime_cash_authority import RuntimeCashAuthorityRecord as Authority
from .runtime_cash_authority import RuntimeCashAuthorityState as State
from .runtime_cash_authority import _transition_pair
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha
from .versioned_operational_store import OperationalPins
from .versioned_source_binding import BindingCheckpoint

_PLAN_STACK: ContextVar[tuple[str, ...]] = ContextVar("admission_plan_stack", default=())

DOMAIN = "VERSIONED_ORDER_ADMISSION_V1"
HELD = "VERSIONED_ORDER_ADMISSION_HELD"
DONE = "VERSIONED_ORDER_ADMISSION_COMMITTED"
MAX_BYTES = 3 * 1024 * 1024
PLAN_FIELDS = {"domain", "version", "kind", "selection_sha256", "target_root", "before_authority",
    "before_owners", "risk_after", "central_after", "runtime", "profile", "policy", "started_at",
    "evaluated_at", "reads", "evaluation", "pins", "checkpoint", "cash_nano", "source_export_sha256",
    "config_sha256", "config_document"}
RESULT_FIELDS = {"domain", "version", "kind", "plan_sha256", "completed_at"}
_INTERVALS = {"CANDLE_INTERVAL_1_MIN": 60, "CANDLE_INTERVAL_5_MIN": 300,
    "CANDLE_INTERVAL_15_MIN": 900, "CANDLE_INTERVAL_HOUR": 3600, "CANDLE_INTERVAL_DAY": 86400}


class VersionedAdmissionError(RuntimeError):
    """Finite refusal; an already saved admission HOLD is not guessed away."""


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise VersionedAdmissionError("V4_ADMISSION_" + code)


def _point(fault: Callable | None, name: str) -> None:
    if fault is not None:
        fault(name)


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _need(result.tzinfo is not None and result.utcoffset() is not None, "TIME_INVALID")
    return result.astimezone(timezone.utc)


def _dir(root: Path, kind: str) -> Path:
    path = root / "risk_admission" / kind
    _safe_path(path)
    return path


def _read(path: Path, key: bytes, fields: set[str]) -> tuple[dict, str]:
    _safe_path(path)
    _need(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES, "PLAN_UNAVAILABLE")
    from .exact_cash_settlement import _pairs
    data = json.loads(path.read_bytes(), object_pairs_hook=_pairs)
    _need(type(data) is dict and set(data) == {"payload", "hmac_sha256"}, "ENVELOPE_INVALID")
    body = data["payload"]
    _need(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
          and type(body["version"]) is int and body["version"] == 1, "SCHEMA_INVALID")
    _need(type(data["hmac_sha256"]) is str and hmac.compare_digest(data["hmac_sha256"],
        hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()), "SIGNATURE_INVALID")
    return body, _sha(_canonical(data))


def _write(path: Path, body: dict, key: bytes, fields: set[str]) -> str:
    _safe_path(path)
    raw = _sealed(body, key)
    _need(len(raw) <= MAX_BYTES and not path.exists(), "PLAN_EXISTS_OR_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(path, _parse(raw), retry_delays=(), jitter_fraction=0,
        write_checksum=False, keep_last_good=False)
    observed, digest = _read(path, key, fields)
    _need(observed == body and digest == _sha(raw), "PLAN_READBACK_FAILED")
    return digest


def _arg(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (list, tuple)):
        return [_arg(v) for v in value]
    if isinstance(value, dict):
        return {k: _arg(v) for k, v in value.items()}
    return value


def _frame_body(frame: pd.DataFrame) -> dict:
    cols = ["open", "high", "low", "close", "volume", "is_complete"]
    _need(type(frame) is pd.DataFrame and 2 <= len(frame) <= 2000
          and all(c in frame for c in cols), "CANDLES_INVALID")
    _need(frame.index.is_unique and frame.index.is_monotonic_increasing
          and frame.index.tz is not None and frame["is_complete"].dtype.kind == "b", "CANDLES_INVALID")
    rows = [[float(row[c]) for c in cols[:-1]] + [bool(row["is_complete"])]
            for _, row in frame.iterrows()]
    _need(all(all(math.isfinite(v) and v >= 0 for v in row[:-1]) and row[3] > 0
          and row[1] >= max(row[0], row[2], row[3]) and row[2] <= min(row[0], row[3])
          for row in rows), "CANDLES_INVALID")
    return {"index": [t.isoformat() for t in frame.index], "columns": cols, "rows": rows}


def _frame(data: dict) -> pd.DataFrame:
    _need(type(data) is dict and set(data) == {"index", "columns", "rows"}, "CANDLES_INVALID")
    result = pd.DataFrame(data["rows"], columns=data["columns"], index=pd.to_datetime(data["index"], utc=True))
    _need(_frame_body(result) == data, "CANDLES_INVALID")
    return result


class _Tape:
    def __init__(self, *, provider: Any = None, rows: list | None = None, guard: Callable = lambda: None):
        self.provider, self.rows, self.index, self.guard = provider, ([] if rows is None else rows), 0, guard

    def __getattr__(self, name: str) -> Callable:
        _need(name in {"get_candles", "get_last_prices", "get_positions", "get_max_lots", "get_orders"}, "READ_METHOD_INVALID")
        def call(*args, **kwargs):
            self.guard()
            _need(self.index < 8, "READ_LIMIT")
            req = {"method": name, "args": _arg(args), "kwargs": _arg(kwargs)}
            if self.provider is not None:
                answer = getattr(self.provider, name)(*args, **kwargs)
                value = _frame_body(answer) if name == "get_candles" else deepcopy(answer)
                self.rows.append({**req, "response": value})
                _need(len(_canonical(self.rows)) <= MAX_BYTES // 2, "CAPTURE_TOO_LARGE")
            else:
                _need(self.index < len(self.rows), "CAPTURE_INCOMPLETE")
                row = self.rows[self.index]
                _need(type(row) is dict and set(row) == {*req, "response"}
                      and all(row[k] == v for k, v in req.items()), "CAPTURE_REQUEST_MISMATCH")
                value = row["response"]
            self.index += 1
            self.guard()
            return _frame(value) if name == "get_candles" else deepcopy(value)
        return call


def _quote(rows: Any, uid: str, evaluated_at: str) -> tuple[int, str]:
    _need(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, "QUOTE_INVALID")
    row = rows[0]
    _need(row.get("instrumentUid") == uid and type(row.get("time")) is str, "QUOTE_IDENTITY_INVALID")
    price = row.get("price")
    _need(type(price) is dict and set(price) <= {"units", "nano", "currency"}
          and ("currency" not in price or price["currency"] in {"rub", "RUB"}) and type(price.get("units")) is str
          and re.fullmatch(r"0|[1-9][0-9]{0,14}", price["units"]) is not None
          and type(price.get("nano")) is int and 0 <= price["nano"] < 10**9, "QUOTE_INVALID")
    nano = int(price["units"]) * 10**9 + price["nano"]
    quote_ns = cl6._normalize_portfolio_timestamp(row["time"])[1]
    _need(nano > 0 and 0 <= cl6._timestamp_ns(evaluated_at) - quote_ns <= MAX_AGE_NS, "QUOTE_STALE")
    # A MARKET request stores a conservative cent estimate, never a wire limit price.
    return (nano + 10**7 - 1) // 10**7, _utc(row["time"]).isoformat()


def _evaluate(a: Any, r: Any, owners: dict, runtime: InstrumentRuntime, profile: MultiInstrumentProfile,
              policy: RiskPolicy, tape: _Tape, *, started_at: str, evaluated_at: str,
              cash_nano: int, pins: OperationalPins, source_raw: bytes) -> tuple[Any, Any, RiskState, dict]:
    previous = PortfolioState.from_dict(owners["portfolio"])
    state = RiskState.from_dict(owners["risk"])
    central = CentralOrderState.from_dict(owners["central"])
    _need(not central.queued and central.blocking_intent is None, "CENTRAL_NOT_QUIESCENT")
    _need(policy.enabled and policy.portfolio_policy_mode == "ENFORCED", "POLICY_NOT_ENFORCED")
    _need(not state.risk_resync_required, "RISK_RESYNC_REQUIRED")
    _need(state.last_snapshot_at is not None and _utc(state.last_snapshot_at) == _utc(previous.snapshot_at),
          "RISK_PORTFOLIO_SNAPSHOT_MISMATCH")
    uid = runtime.config.instrument_id
    _need(runtime.status == "ACTIVE" and runtime.config == profile.to_runtime_config(a.policy.account_id), "RUNTIME_CHANGED")
    metadata = a.cl7_own_funds_policy.instruments
    _need(uid in metadata and a.cash_authority_manager.buying_budget_policy.execution_order_type == "MARKET", "ORDER_PROFILE_UNSUPPORTED")
    own_row = metadata[uid]
    _need(0 <= (_utc(evaluated_at) - _utc(previous.snapshot_at)).total_seconds() <= 5, "PORTFOLIO_STALE")
    frame = StrategyCandleLoader(tape).load(runtime, profile, now=_utc(started_at))
    interval = _INTERVALS.get(runtime.config.candle_interval)
    _need(interval is not None, "CANDLE_INTERVAL_UNSUPPORTED")
    age = (_utc(started_at) - frame.index[-1].to_pydatetime()).total_seconds()
    _need(interval <= age <= interval * 2, "CANDLE_STALE_OR_OPEN")
    proposal = build_strategy_proposal(runtime, profile, frame, now=_utc(evaluated_at))
    position = previous.position(uid)
    current = position.actual_lots if position is not None else 0
    target = proposal.primary_target_lots
    _need(type(target) is int and target >= 0 and target != current, "NO_POSITION_CHANGE")
    _need(not any(i.candidate.runtime_key == runtime.runtime_key and i.candidate.candle_time == proposal.candle_time
                  for i in central.intents), "CANDLE_ALREADY_ADMITTED")
    lease = PortfolioSnapshotLease.from_state(previous, leased_at=_utc(evaluated_at).isoformat())
    preflight = PortfolioPreflightGate().evaluate(lease, account_id=a.policy.account_id,
        mode="SANDBOX_EXECUTION", instrument_id=uid, proposed_target_lots=target, legacy=None, require_dual_read=False)
    _need(preflight.allowed, "PREFLIGHT_BLOCKED")
    price_k, price_at = _quote(tape.get_last_prices([uid]), uid, evaluated_at)
    primary = proposal.decisions[proposal.primary_strategy]
    bot = profile.to_bot_config(mode="SANDBOX_EXECUTION", state_file="robot_state.json", journal_file="trading_events.db")
    price = price_k / 100.0
    stop = None if primary.stop_level is None else abs(price - float(primary.stop_level)) or None
    assessment = RiskEngine(policy).evaluate(RiskSnapshot(now=_utc(evaluated_at), strategy_target_lots=target,
        current_lots=current, price_rub=price, lot_size=own_row.lot_size,
        portfolio_equity_rub=previous.account.total_value, cash_rub=previous.account.cash("rub").available,
        atr_rub=average_true_range(frame, max(2, int(bot.donchian_atr_window))), stop_distance_rub=stop,
        snapshot_at=_utc(previous.snapshot_at), position_reconciled=True, pending_order=False, mode="SANDBOX_EXECUTION"), state)
    decision = assessment.decision
    _need(decision.order_allowed and decision.status in {"PASS", "ADJUSTED", "REDUCTION_ALLOWED"},
          "SINGLE_RISK_BLOCKED:" + ",".join(decision.breaches))
    _need(risk_state_guard_hash(assessment.state) == risk_state_guard_hash(state), "RISK_MAINTENANCE_REQUIRED")
    candidate = CentralOrderCandidate.from_strategy_proposal(proposal, account_id=a.policy.account_id,
        runtime_config_hash=runtime.config.runtime_config_hash, current_lots=current,
        approved_target_lots=decision.approved_target_lots, estimated_price_rub=price, lot_size=own_row.lot_size,
        order_type="MARKET")
    orders = tape.get_orders(a.policy.account_id)
    _need(type(orders) is list and not orders, "EXTERNAL_ACTIVE_ORDER")
    raw_cash = tape.get_positions(a.policy.account_id)
    common = {k: cut._common(a)[k] for k in ("identity_key", "identity_key_id", "account_scope_sha256")}
    common["environment"] = BrokerEnvironment.SANDBOX
    cash = cl4.build_broker_rub_position_cash_proof(raw_cash, raw_account_id=a.policy.account_id,
        as_of=started_at, evaluated_at=evaluated_at, response_complete=True, **common)
    _need(cash.cash.minor_units == cash_nano, "CASH_MISMATCH")
    budget_policy = a.cash_authority_manager.buying_budget_policy
    own = budget_policy.acquire(tape, account_scope_sha256=common["account_scope_sha256"],
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        clock=lambda: started_at, monotonic_ns=lambda: 0)
    evidence = cl6.build_portfolio_identity_evidence(lease, evaluated_at=evaluated_at, **common)
    risk_evidence = cl6.build_risk_guard_evidence(policy, state, raw_account_id=a.policy.account_id,
        captured_at=started_at, evaluated_at=evaluated_at, **common)
    reserves = cl5.project_central_reservations(central, evaluated_at=evaluated_at, **common)
    context = readers.build_versioned_cash_context(source_raw, pins=pins, codec_registry=tuple(a.cl7_ledger_store._registry.values()),
        broker_cash=cash, own_buying=own, reservations=reserves, portfolio=evidence, risk_guard=risk_evidence,
        buying_scope_sha256=budget_policy.scope_sha256, evaluated_at=evaluated_at,
        **{k: v for k, v in common.items() if k != "environment"})
    body = _parse(context.payload_bytes)
    _need(body["status"] == "CONSISTENT_REVIEW_ONLY", "CASH_CONTEXT_BLOCKED:" + str(body["reason"]))
    canonical_k = int((Decimal(str(previous.account.cash("rub").available)) * 100).to_integral_value(rounding=ROUND_FLOOR))
    available_k = min(canonical_k, int(body["free_cash_nano"]) // 10**7)
    native_metadata = {uid: PortfolioRiskInstrumentMetadata(instrument_id=uid, lot_size=row.lot_size,
                       asset_class=row.asset_class, currency=row.currency) for uid, row in metadata.items()}
    native = PortfolioRiskRuntime(account_id=a.policy.account_id, profile_store=a.risk_runtime.profile_store,
        state_store=a.risk_runtime.state_store, instrument_metadata=native_metadata)
    inp = PortfolioRiskInputAdapter().build(portfolio=previous, central_orders=central, risk_state=assessment.state,
        evaluated_at=_utc(evaluated_at), instrument_metadata=native_metadata)
    # Spending capacity is bounded by verified own money, never added to NAV.
    inp = replace(inp, cash_available_rub=min(inp.cash_available_rub, available_k / 100.0))
    change = native._change(previous, candidate, requested_target_lots=candidate.target_lots,
        price_at=_utc(price_at), price_source="TBANK_LAST_PRICE_EXCHANGE", cash_buffer_bps=100)
    portfolio_decision = PortfolioRiskEvaluator(portfolio_policy_from_risk_policy(policy)).evaluate(inp, change).decision
    PortfolioRiskRuntime._require_authorizing_decision(portfolio_decision, current_lots=current)
    outcome = RiskRuntimeOutcome(enforced=True, mode="SANDBOX_EXECUTION", approved_target_lots=decision.approved_target_lots,
        assessment=assessment, decision_id=_sha(_canonical({"domain": DOMAIN, "proposal": proposal.to_dict(),
            "single": decision.to_dict(), "pins": pins.to_dict()})),
        portfolio_revision=previous.revision, portfolio_decision_checksum=previous.decision_sha256)
    authorization = ExecutionAuthorization.from_gate_results(preflight, outcome, lease=lease,
        authorized_at=_utc(evaluated_at).isoformat())
    proof = PortfolioRiskAuthorizationProof(decision_id=portfolio_decision.decision_id,
        input_hash=portfolio_decision.input_hash, policy_hash=portfolio_decision.policy_hash, status=portfolio_decision.status,
        single_risk_approved_target_lots=candidate.target_lots, approved_target_lots=portfolio_decision.approved_target_lots,
        snapshot_revision=portfolio_decision.snapshot_revision, snapshot_checksum=portfolio_decision.snapshot_checksum,
        central_order_revision=portfolio_decision.central_order_revision,
        reservation_projection_hash=portfolio_decision.reservation_projection_hash,
        risk_state_guard_hash=portfolio_decision.risk_state_guard_hash, evaluated_at=_utc(evaluated_at).isoformat(),
        candidate_price_at=price_at, candidate_price_source="TBANK_LAST_PRICE_EXCHANGE", cash_buffer_bps=100,
        excluded_reservation_ids=())
    candidate = replace(candidate, target_lots=portfolio_decision.approved_target_lots)
    authorization = replace(authorization, authorized_target_lots=candidate.target_lots,
                            available_cash_kopecks=available_k, portfolio_risk=proof)
    # Validate market-lot capacity using the SAME actual read used for own-budget proof.
    limits = next(row["response"] for row in tape.rows if row["method"] == "get_max_lots" and row["args"][1] == uid)
    cap_group = limits.get("buyLimits" if candidate.direction == "BUY" else "sellLimits")
    _need(type(cap_group) is dict, "MARKET_LOT_CAP_MISSING")
    from .exact_own_funds import _uint
    cap = _uint(cap_group.get("buyMaxMarketLots" if candidate.direction == "BUY" else "sellMaxLots", "0"))
    _need(candidate.requested_lots <= cap, "MARKET_LOT_CAP_EXCEEDED")
    _need(candidate.direction != "SELL" or candidate.requested_lots <= current, "SHORT_FORBIDDEN")
    risk_after = replace(assessment.state, last_portfolio_risk_decision_id=portfolio_decision.decision_id,
        last_portfolio_risk_input_hash=portfolio_decision.input_hash,
        last_portfolio_risk_evaluated_at=portfolio_decision.evaluated_at.isoformat())
    return candidate, authorization, risk_after, {"proposal": proposal.to_dict(), "single_risk": decision.to_dict(),
        "portfolio_risk": portfolio_decision.to_dict(), "input": inp.to_dict(),
        "cash_context_sha256": context.sha256, "cash_nano": str(cash_nano), "available_cash_kopecks": available_k,
        "candidate": candidate.to_dict(), "authorization": authorization.to_dict(), "market_lot_cap": cap}


def _held(a: Any, plan: dict, digest: str) -> Authority:
    before = Authority.from_canonical_dict(plan["before_authority"])
    result = a.cash_authority_manager._change(before, at=plan["evaluated_at"], kind=HELD,
        state=State.EXACT_CASH_VERSIONED_ADMISSION_PENDING, pending_dispatch_proof_sha256=digest)
    _transition_pair(before, result)
    return result


def _after(a: Any, plan: dict, plan_sha: str, result: dict, digest: str) -> Authority:
    _need(result["kind"] == "COMMITTED" and result["plan_sha256"] == plan_sha, "RESULT_INVALID")
    held = _held(a, plan, plan_sha)
    record = a.cash_authority_manager._change(held, at=result["completed_at"], kind=DONE,
        state=State.EXACT_CASH_VERSIONED_DISARMED, pending_dispatch_proof_sha256=None, activation_context_sha256=digest)
    _transition_pair(held, record)
    return record


def _central_after(before: CentralOrderState, candidate: Any, auth: Any, stored: CentralOrderState) -> CentralOrderState:
    _need(len(stored.intents) == len(before.intents) + 1, "CENTRAL_COUNT_INVALID")
    _need(before.blocking_intent is None and not before.queued, "CENTRAL_NOT_QUIESCENT")
    _need(candidate.reservation_kopecks(cash_buffer_bps=100) <= auth.available_cash_kopecks, "RESERVE_EXCEEDS_CASH")
    template = stored.intents[-1]
    intent = CentralOrderIntent.create(candidate, auth, queue_sequence=before.next_sequence,
        reserved_cash_kopecks=candidate.reservation_kopecks(cash_buffer_bps=100), created_at=template.created_at)
    projected = replace(before, next_sequence=before.next_sequence + 1, intents=(*before.intents, intent))
    revision = before.revision + 1
    proof = auth.portfolio_risk.finalize(central_revision=revision,
        reservation_projection_hash=central_reservation_projection_hash(projected, revision=revision))
    intent = replace(intent, authorization=replace(auth, portfolio_risk=proof))
    return replace(projected, revision=revision, updated_at=stored.updated_at, intents=(*before.intents, intent))


def _plan(a: Any, r: Any, p: dict, selection: str, digest: str, *, semantic: bool = True) -> dict:
    stack = _PLAN_STACK.get()
    _need(digest not in stack and len(stack) < 16, "PLAN_CYCLE_OR_LIMIT")
    token = _PLAN_STACK.set((*stack, digest))
    try:
        return _plan_body(a, r, p, selection, digest, semantic=semantic)
    finally:
        _PLAN_STACK.reset(token)


def _plan_body(a: Any, r: Any, p: dict, selection: str, digest: str, *, semantic: bool = True) -> dict:
    body, actual = _read(_dir(Path(p["target_root"]), "plans") / (versions._hash(digest) + ".json"),
                         a.cl7_identity_key, PLAN_FIELDS)
    _need(actual == digest and body["selection_sha256"] == selection and body["target_root"] == p["target_root"]
          and body["config_sha256"] == p["config_sha256"] and body["kind"] == "PREPARED", "PLAN_IDENTITY_INVALID")
    config = body["config_document"]
    _need(type(config) is dict and set(config) == {"profiles", "runtimes", "risk_profiles"}
          and _sha(_canonical(config)) == body["config_sha256"], "CONFIG_DOCUMENT_INVALID")
    _need(body["runtime"] in config["runtimes"] and body["profile"] in config["profiles"], "CONFIG_MEMBER_INVALID")
    policy = RiskPolicy(**body["policy"])
    _need(policy.policy_hash == a.risk_runtime.current_policy_hash(), "POLICY_CHANGED")
    before = Authority.from_canonical_dict(body["before_authority"])
    _need(before.transition_kind in {"VERSIONED_OWNER_REFRESH_COMMITTED", "VERSIONED_CASH_FLOW_RESYNC_COMMITTED"},
          "PLAN_OWNER_REFRESH_REQUIRED")
    refresh._require_recent_operations(before, body["evaluated_at"])
    _need(0 <= timestamp_ns(body["evaluated_at"]) - timestamp_ns(body["started_at"]) <= MAX_AGE_NS, "CAPTURE_STALE")
    if semantic:
        cp, pins, _, raw = sync._lineage(a, p, selection, before)
        owners = sync._owner_anchor(a, p, selection, before)
        _need(owners == body["before_owners"] and body["pins"] == pins.to_dict() and body["checkpoint"] == sync._cp(cp),
              "PLAN_ANCHOR_INVALID")
        if raw is None:
            with sync._open(a, p, cp) as binding:
                raw = binding.source.export_bytes()
        _need(_sha(raw) == body["source_export_sha256"] == pins.export_sha256, "SOURCE_HASH_INVALID")
        tape = _Tape(rows=body["reads"])
        candidate, authorization, risk, evaluation = _evaluate(a, r, owners,
            InstrumentRuntime.from_dict(body["runtime"]), MultiInstrumentProfile.from_dict(body["profile"]),
            RiskPolicy(**body["policy"]), tape, started_at=body["started_at"], evaluated_at=body["evaluated_at"],
            cash_nano=body["cash_nano"], pins=pins, source_raw=raw)
        _need(tape.index == len(tape.rows) and evaluation == body["evaluation"]
              and risk.to_dict() == body["risk_after"], "ECONOMIC_PLAN_INVALID")
        central = CentralOrderState.from_dict(body["central_after"])
        _need(_central_after(CentralOrderState.from_dict(owners["central"]), candidate, authorization, central) == central,
              "CENTRAL_PLAN_INVALID")
        _need(body["cash_nano"] == sync._graph(a, raw).snapshot().cash_nano, "PLAN_CASH_INVALID")
    _held(a, body, digest)
    return body


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    plan_sha256: str
    intent_id: str
    requested_target_lots: int
    approved_target_lots: int
    reserved_cash_kopecks: int
    authority_sha256: str
    replay: bool

    def public_summary(self) -> dict:
        return {"domain": DOMAIN, "plan_sha256": self.plan_sha256, "status": "QUEUED",
            "risk_admission_performed": True, "broker_execution_authorized": False,
            "runtime_authority_granted": False, "replay": self.replay}


def _result(plan: dict, digest: str, record: Authority, replay: bool) -> AdmissionResult:
    intent = CentralOrderState.from_dict(plan["central_after"]).intents[-1]
    return AdmissionResult(digest, intent.intent_id, plan["evaluation"]["proposal"]["primary_target_lots"],
        intent.candidate.target_lots, intent.reserved_cash_kopecks, record.sha256, replay)


def _completed(a: Any, r: Any, p: dict, selection: str, record: Authority, *, semantic=True):
    result, actual = _read(_dir(Path(p["target_root"]), "results") / (record.activation_context_sha256 + ".json"),
                          a.cl7_identity_key, RESULT_FIELDS)
    _need(actual == record.activation_context_sha256, "RESULT_HASH_INVALID")
    plan = _plan(a, r, p, selection, result["plan_sha256"], semantic=semantic)
    _need(record == _after(a, plan, result["plan_sha256"], result, actual), "AUTHORITY_RESULT_MISMATCH")
    return plan, result["plan_sha256"]


def admission_lineage(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    # Evaluation reads configuration and signed capture, not the provider or live Risk.
    plan, _ = _completed(a, None, p, selection, record)
    return sync._lineage(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]),
                         seen=seen | {record.activation_context_sha256})


def committed_owners(a: Any, p: dict, selection: str, record: Authority, *, seen=frozenset()):
    plan, _ = _completed(a, None, p, selection, record, semantic=False)
    prior = sync._owner_anchor(a, p, selection, Authority.from_canonical_dict(plan["before_authority"]), seen=seen)
    _need(prior == plan["before_owners"], "OWNER_CHAIN_MISMATCH")
    return {"portfolio": prior["portfolio"], "risk": plan["risk_after"], "central": plan["central_after"]}


def _finish(a: Any, r: Any, p: dict, selection: str, digest: str, *, timely=lambda: None, fault=None) -> AdmissionResult:
    plan = _plan(a, r, p, selection, digest)
    held = _held(a, plan, digest)
    before_a = Authority.from_canonical_dict(plan["before_authority"])
    before = plan["before_owners"]
    pins, cp = OperationalPins(**plan["pins"]), BindingCheckpoint(**plan["checkpoint"])
    with _owner_locks(a, r, ledger=True), sync._open(a, p, cp) as binding, binding.locked_binding() as (_, view):
        _need(view.pins == pins and _sha(view.export_bytes()) == plan["source_export_sha256"], "SOURCE_CHANGED")
        state = binding._load()
        _need(state.checkpoint == cp and state.pending is None, "REGISTRY_CHANGED")
        def check():
            _need(cut._config(a, r) == plan["config_sha256"] and cut._identity(a, r) == p["runtime_identity"], "CONFIG_CHANGED")
            owners = cut._owners(a, r)
            _need(owners["portfolio"] == before["portfolio"], "PORTFOLIO_CHANGED")
            pairs = [(before["risk"], before["central"]), (plan["risk_after"], before["central"]),
                     (plan["risk_after"], plan["central_after"])]
            pair = (owners["risk"], owners["central"])
            _need(pair in pairs, "OWNER_PREFIX_CONFLICT")
            view.assert_active(); timely()
            return pairs.index(pair)
        current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        if current.state is State.EXACT_CASH_VERSIONED_DISARMED and current.transition_kind == DONE:
            complete, _ = _completed(a, r, p, selection, current, semantic=False)
            _need(complete == plan and check() == 2, "REPLAY_CONFLICT")
            return _result(plan, digest, current, True)
        _need(current == held, "CONFIRMED_HOLD_REQUIRED")
        phase = check()
        if phase == 0:
            r.risk.state_store.save_account_while_locked(a.policy.account_id, RiskState.from_dict(plan["risk_after"]))
            _point(fault, "admission.after_risk")
        if check() == 1:
            # The exact after-state was produced by native Central admission BEFORE HOLD.
            # Its complete semantics are re-derived above; authorization time is NOT renewed.
            a.manager.store._save_unlocked(CentralOrderState.from_dict(plan["central_after"]))
            _point(fault, "admission.after_central")
        _need(check() == 2, "OWNER_WRITE_FAILED")
        result_path = _dir(Path(p["target_root"]), "resolutions") / (digest + ".json")
        if result_path.exists():
            result, result_sha = _read(result_path, a.cl7_identity_key, RESULT_FIELDS)
        else:
            result = {"domain": DOMAIN, "version": 1, "kind": "COMMITTED", "plan_sha256": digest,
                      "completed_at": a.cl7_clock()}
            result_sha = _write(result_path, result, a.cl7_identity_key, RESULT_FIELDS)
        index = _dir(Path(p["target_root"]), "results") / (result_sha + ".json")
        if not index.exists():
            _write(index, result, a.cl7_identity_key, RESULT_FIELDS)
        after = _after(a, plan, digest, result, result_sha)
        _point(fault, "admission.after_result")
        r.manager.transaction_coordinator._record("VERSIONED_ORDER_ADMISSION_VERIFIED",
            account_id=a.policy.account_id, instrument_id=plan["runtime"]["config"]["instrument_id"],
            mode="SANDBOX_EXECUTION", status="COMMITTED",
            payload={"plan_sha256": digest, "broker_execution_authorized": False})
        _point(fault, "admission.after_audit")
        _need(check() == 2 and a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == held, "AUTHORITY_CHANGED")
        a.cash_authority_manager.store._commit_unlocked(after, expected_revision=held.record_revision, expected_sha256=held.sha256)
        _point(fault, "admission.after_authority")
        return _result(plan, digest, after, False)


@contextmanager
def _locks_before_central(a: Any, r: Any):
    with ExitStack() as locks:
        for path in (r.profiles.lock_path, r.runtimes.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=.1))
        locks.enter_context(r.manager.repository.locked_snapshot(expected_account_id=a.policy.account_id))
        for path in (r.risk.profile_store.lock_path, r.risk.state_store.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=.1))
        locks.enter_context(a.cash_authority_manager.ledger_guard(a.cl7_ledger_store))
        yield


def admit_selected_order(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
        expected_authority_sha256: str, instrument_id: str, fault_injector: Callable | None = None) -> AdmissionResult:
    """Read real candles/quote/own funds, run native evaluators and enqueue ONE request.

    No caller-supplied target, price, Risk decision, authorization or balance is
    accepted. Defaults/limits are not changed. One quiescent account, MARKET,
    same-day fresh owners, no replace/reauthorize and no direct broker dispatch.
    """
    r, root = recovery, cut._path(target_root)
    with a.cash_authority_manager.store.locked():
        p = sync._load_current_prepared(a, r, root, expected_selection_sha256)
        before_a = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        _need(before_a.sha256 == versions._hash(expected_authority_sha256), "AUTHORITY_CAS_CONFLICT")
        cp, pins, _, _ = sync._lineage(a, p, expected_selection_sha256, before_a)
        owners = sync._owner_anchor(a, p, expected_selection_sha256, before_a)
        _need(not CentralOrderState.from_dict(owners["central"]).queued, "CENTRAL_NOT_QUIESCENT")
        _need(before_a.state is State.EXACT_CASH_VERSIONED_DISARMED, "SOURCE_NOT_DISARMED")
        _need(before_a.transition_kind in {"VERSIONED_OWNER_REFRESH_COMMITTED", "VERSIONED_CASH_FLOW_RESYNC_COMMITTED"},
              "RECENT_OWNER_REFRESH_REQUIRED")
        profiles = [q for q in r.profiles.load_mode("SANDBOX_EXECUTION") if q.instrument_id == instrument_id]
        runtimes = [q for q in r.runtimes.load(expected_account_id=a.policy.account_id) if q.config.instrument_id == instrument_id]
        _need(len(profiles) == len(runtimes) == 1, "INSTRUMENT_NOT_CONFIGURED")
        profile, runtime = profiles[0], runtimes[0]
        policy, auto = r.risk._load_policy()
        _need(not auto, "POLICY_UNCONFIRMED")
        with sync._open(a, p, cp) as b:
            source = b.source.export_bytes(); snap = b.source.snapshot(pins=pins)
        started = a.cl7_clock(); start_wall = timestamp_ns(started); start_tick = a.cl7_monotonic_ns()
        last_wall, last_tick = start_wall, start_tick
        refresh._require_recent_operations(before_a, started)
        def timely():
            nonlocal last_wall, last_tick
            wall, tick = timestamp_ns(a.cl7_clock()), a.cl7_monotonic_ns()
            _need(type(tick) is int and 0 <= start_tick <= last_tick <= tick <= start_tick + MAX_AGE_NS
                  and start_wall <= last_wall <= wall <= start_wall + MAX_AGE_NS, "STALE")
            last_wall, last_tick = wall, tick
        config_document = {"profiles": [q.to_dict() for q in r.profiles.load_mode("SANDBOX_EXECUTION")],
            "runtimes": [q.to_dict() for q in r.runtimes.load(expected_account_id=a.policy.account_id)],
            "risk_profiles": r.risk.profile_store.load_document()}
        _need(_sha(_canonical(config_document)) == p["config_sha256"], "CONFIG_CHANGED")
        def guard():
            timely()
            _need(cut._config(a, r) == p["config_sha256"] and cut._identity(a, r) == p["runtime_identity"], "CONFIG_CHANGED")
            current = cut._owners(a, r)
            _need(current == {**owners, "authority": before_a.to_canonical_dict()}, "OWNER_CHANGED")
        tape = _Tape(provider=r.manager.api, guard=guard)
        # Collect first; the only decision timestamp is the actual POST-read time.
        # Acquisition uses native candle loading and own-budget policy; monetary
        # and order semantics are derived below from the complete immutable tape.
        StrategyCandleLoader(tape).load(runtime, profile, now=_utc(started))
        tape.get_last_prices([instrument_id])
        tape.get_orders(a.policy.account_id)
        tape.get_positions(a.policy.account_id)
        a.cash_authority_manager.buying_budget_policy.acquire(tape,
            account_scope_sha256=cut._common(a)["account_scope_sha256"], identity_key=a.cl7_identity_key,
            identity_key_id=a.cl7_identity_key_id, clock=lambda: started, monotonic_ns=lambda: 0)
        evaluated = a.cl7_clock(); timely()
        refresh._require_recent_operations(before_a, evaluated)
        replay = _Tape(rows=tape.rows)
        candidate, auth, risk_after, evaluation = _evaluate(a, r, owners, runtime, profile, policy, replay,
            started_at=started, evaluated_at=evaluated, cash_nano=snap.cash_nano, pins=pins, source_raw=source)
        _need(replay.index == len(replay.rows), "EXTRA_READS")
        holder = {}
        with _locks_before_central(a, r), ExitStack() as nested:
            guard()
            def builder(central):
                _need(central.to_dict() == owners["central"], "CENTRAL_CHANGED")
                b = nested.enter_context(sync._open(a, p, cp))
                state = b._load()
                _need(state.checkpoint == cp and state.pending is None, "REGISTRY_CHANGED")
                _, view = nested.enter_context(b.locked_binding())
                _need(view.pins == pins and view.export_bytes() == source, "SOURCE_CHANGED")
                holder["view"] = view
                guard()
                return candidate, auth
            def prepare(before, after):
                guard(); holder["view"].assert_active()
                _need(before.to_dict() == owners["central"] and _central_after(before, candidate, auth, after) == after,
                      "NATIVE_CENTRAL_MISMATCH")
                plan = {"domain": DOMAIN, "version": 1, "kind": "PREPARED", "selection_sha256": expected_selection_sha256,
                    "target_root": str(root), "before_authority": before_a.to_canonical_dict(), "before_owners": owners,
                    "risk_after": risk_after.to_dict(), "central_after": after.to_dict(), "runtime": runtime.to_dict(),
                    "profile": profile.to_dict(), "policy": asdict(policy), "started_at": started, "evaluated_at": evaluated,
                    "reads": tape.rows, "evaluation": evaluation, "pins": pins.to_dict(), "checkpoint": sync._cp(cp),
                    "cash_nano": snap.cash_nano, "source_export_sha256": _sha(source), "config_sha256": p["config_sha256"],
                    "config_document": config_document}
                plan = _parse(_canonical(plan)); digest = _sha(_sealed(plan, a.cl7_identity_key))
                _write(_dir(root, "plans") / (digest + ".json"), plan, a.cl7_identity_key, PLAN_FIELDS)
                holder["digest"] = digest
                _point(fault_injector, "admission.after_plan"); guard()
                held = _held(a, plan, digest)
                a.cash_authority_manager.store._commit_unlocked(held,
                    expected_revision=before_a.record_revision, expected_sha256=before_a.sha256)
                _point(fault_injector, "admission.after_hold"); timely()
                r.risk.state_store.save_account_while_locked(a.policy.account_id, risk_after)
                _point(fault_injector, "admission.after_risk"); timely(); holder["view"].assert_active()
                now = cut._owners(a, r)
                _need(now["portfolio"] == owners["portfolio"] and now["central"] == owners["central"]
                      and now["risk"] == risk_after.to_dict() and now["authority"] == held.to_canonical_dict(), "OWNER_CHANGED")
            a.manager.admit_portfolio(builder, cash_buffer_bps=100, prepare_commit=prepare)
            _point(fault_injector, "admission.after_central")
        return _finish(a, r, p, expected_selection_sha256, holder["digest"], timely=timely, fault=fault_injector)


def recover_selected_admission(a: Any, *, recovery: Any, target_root: object, expected_selection_sha256: str,
        expected_plan_sha256: str, fault_injector: Callable | None = None) -> AdmissionResult:
    """Resolve a CONFIRMED HOLD only. Preserve original (possibly expired) authorization."""
    with a.cash_authority_manager.store.locked():
        p = cut._load_prepared(a, recovery, cut._path(target_root), expected_selection_sha256, _check_initial_owners=False)
        return _finish(a, recovery, p, expected_selection_sha256, expected_plan_sha256, fault=fault_injector)
