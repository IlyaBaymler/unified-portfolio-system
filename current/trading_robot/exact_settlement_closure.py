"""Bounded, explicit STEP15 owner closure after completed STEP14 cash posting.

No public receipt/result flag grants authority. Every invocation re-reads the
receipt, operation window, cash plan and CL2 prefix. An authenticated immutable
plan admits only P -> R -> C -> A prefixes. Authority is always DISARMED last.
This is an ordered multi-store protocol, not a distributed transaction or fee
finality guarantee. Same-day RUB SHARE/ETF full/partial executions and proven
zero CANCELLED/REJECTED are in scope. Zero terminals do not create a Risk
execution or new ownership. Original requested targets are never rewritten.
"""
from __future__ import annotations

import hmac
import json
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from . import cash_ledger_opening_reconciliation as cl4
from .broker_read_adapters import BrokerEnvironment
from .central_order_manager import CentralOrderState
from .desktop_fill_recovery import DesktopFillRecovery, _ReceiptReadView
from .exact_cash_settlement import (
    _HEX,
    _MAX_PLAN_BYTES,
    _PlanStore,
    _canonical,
    _check_prefix,
    _collect,
    _components,
    _match,
    _pairs,
    _safe_path,
    _seal,
    _sha,
    settlement_outcome,
)
from .exact_order_receipt import _time, decode_exact_order_receipt, validate_exact_receipt_binding
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .locking import InterProcessFileLock
from .portfolio_adapters import BrokerPortfolioAdapter, RuntimePortfolioAdapter, RuntimePositionRecord
from .portfolio_model import (
    CompatibilityShadowStatus, PortfolioMigrationMetadata, PortfolioState,
    PortfolioTarget, PositionOrigin, PositionOwnership, SnapshotFreshness,
)
from .portfolio_reconciler import ReconciliationContext
from .risk import MOSCOW_TZ, ExecutionRecord, RiskEngine, RiskPolicy, RiskState
from .runtime_cash_authority import RuntimeCashAuthorityRecord, RuntimeCashAuthorityState
from .state_persistence import atomic_write_json

_DOMAIN = "CL7_EXACT_SETTLEMENT_CLOSURE_V1"
_KIND = "EXACT_SETTLEMENT_CLOSED_DISARMED"
_ZERO_KIND = "EXACT_ZERO_TERMINAL_CLOSED_DISARMED"


class ExactSettlementClosureError(RuntimeError):
    """Finite error. Previously committed owner prefixes must not be rolled back."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ExactSettlementClosureError("EXACT_CLOSURE_" + reason)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class _ClosureStore:
    def __init__(self, root: Any, proof: str, key: bytes) -> None:
        _require(type(proof) is str and _HEX.fullmatch(proof) is not None, "PROOF_INVALID")
        self.path = root / "exact_settlement_closure" / (proof + ".json")
        self.key = key

    def load(self) -> dict[str, Any] | None:
        _safe_path(self.path)
        if not self.path.exists():
            return None
        _require(self.path.is_file() and self.path.stat().st_size <= _MAX_PLAN_BYTES, "PLAN_INVALID")
        raw = self.path.read_bytes()
        _require(len(raw) <= _MAX_PLAN_BYTES, "PLAN_INVALID")
        doc = json.loads(raw, object_pairs_hook=_pairs)
        _require(type(doc) is dict and set(doc) == {"payload", "hmac_sha256"}, "PLAN_INVALID")
        body = doc["payload"]
        _require(type(body) is dict and set(body) == {
            "domain", "version", "proof_sha256", "cash_binding", "watermark",
            "before", "after", "portfolio_candidate", "execution", "config_sha256",
            "transaction_id",
        }, "PLAN_INVALID")
        _require(body["domain"] == _DOMAIN and type(body["version"]) is int and body["version"] == 1
                 and type(doc["hmac_sha256"]) is str
                 and hmac.compare_digest(doc["hmac_sha256"], _seal(self.key, body)), "PLAN_AUTHENTICATION_FAILED")
        return body

    def create(self, body: dict[str, Any]) -> None:
        _require(self.load() is None, "PLAN_ALREADY_EXISTS")
        doc = {"payload": body, "hmac_sha256": _seal(self.key, body)}
        _canonical(doc)
        atomic_write_json(self.path, doc, retry_delays=(), jitter_fraction=0,
                          write_checksum=False, keep_last_good=False)
        _require(self.load() == body, "PLAN_READBACK_FAILED")


@dataclass(frozen=True, slots=True)
class ExactClosureResult:
    proof_sha256: str
    closure_plan_sha256: str
    cash_plan_sha256: str
    ledger_head_sha256: str
    ledger_revision: int
    authority_record_sha256: str
    replay: bool

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"domain": _DOMAIN + "_RESULT", "version": 1,
                "status": "EXACT_SETTLEMENT_CLOSED_DISARMED",
                "proof_sha256": self.proof_sha256, "closure_plan_sha256": self.closure_plan_sha256,
                "cash_plan_sha256": self.cash_plan_sha256,
                "ledger_head_sha256": self.ledger_head_sha256, "ledger_revision": str(self.ledger_revision),
                "authority_record_sha256": self.authority_record_sha256, "replay": self.replay,
                "cash_components_verified": True, "bounded_settlement_verified": True,
                "automatic_rearm_allowed": False, "future_fee_finality_claimed": False}


def _owners(a: Any, recovery: DesktopFillRecovery) -> dict[str, Any]:
    return {
        "portfolio": recovery.manager.repository.load(expected_account_id=a.policy.account_id).to_dict(),
        "risk": recovery.risk.state_store.load_account(a.policy.account_id).to_dict(),
        "central": a.manager.state().to_dict(),
        "authority": a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False).to_canonical_dict(),
    }


def _config(a: Any, recovery: DesktopFillRecovery) -> str:
    from .gui_runtime_controller import CL4MoneyNormalizingTransport
    own = a.cl7_own_funds_policy
    _require(own is not None, "METADATA_UNAVAILABLE")
    own.binding_guard()
    _require(type(recovery) is DesktopFillRecovery and recovery.central is a.manager
             and recovery.authority is a.cash_authority_manager and recovery.risk is a.risk_runtime
             and type(a.transport) is CL4MoneyNormalizingTransport
             and recovery.manager.api is a.transport._delegate and recovery.manager.account_id == a.policy.account_id
             and recovery.risk.account_id == a.policy.account_id and recovery.risk.mode == "SANDBOX_EXECUTION"
             and recovery.policy.account_id == recovery.cash_policy.account_id == a.policy.account_id
             and dict(recovery.policy.instruments) == dict(own.instruments)
             and dict(recovery.cash_policy.instruments) == dict(own.instruments)
             and recovery.manager.transaction_coordinator.journal is recovery.manager.journal
             and recovery.manager.journal is not None
             and a.cash_authority_manager.cash_source_version == 3
             and a.cl7_proof_builder is None, "OWNER_GRAPH_MISMATCH")
    profiles = recovery.profiles.load_mode("SANDBOX_EXECUTION")
    runtimes = recovery.runtimes.load(expected_account_id=a.policy.account_id)
    _require(bool(runtimes) and all(r.status == "ACTIVE" for r in runtimes), "CONFIGURED_SET_NOT_ACTIVE")
    return _sha(_canonical({"profiles": [p.to_dict() for p in profiles],
                            "runtimes": [r.to_dict() for r in runtimes],
                            "risk_profiles": recovery.risk.profile_store.load_document()}))


def _prefix(plan: dict[str, Any], state: dict[str, Any]) -> int:
    # Strictly ordered prefixes, not a Cartesian product of independently valid files.
    for n in range(5):
        expected = {key: plan["after" if i < n else "before"][key]
                    for i, key in enumerate(("portfolio", "risk", "central", "authority"))}
        if state == expected:
            return n
    raise ExactSettlementClosureError("EXACT_CLOSURE_OWNER_PREFIX_CONFLICT")


def _cash_evidence(a: Any, pending: Any, intent: Any, guard: Any) -> tuple[Any, Any, dict[str, Any], Any]:
    own = a.cl7_own_funds_policy
    instrument = own.instruments.get(intent.candidate.instrument_id)
    proof = validate_exact_receipt_binding(intent, account_id=a.policy.account_id,
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id, instrument=instrument)
    guard()
    raw = a.transport.get_order_state(a.policy.account_id, intent.intent_id, by_request_id=True)
    raw = json.loads(_canonical(raw), object_pairs_hook=_pairs)
    guard()
    receipt = decode_exact_order_receipt(raw, intent, account_id=a.policy.account_id,
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        instrument=instrument, observed_at=a.cl7_clock())
    batch, rows = _collect(a, pending, receipt, guard)
    components = _components(a, intent, receipt, batch, rows)
    cash_plan = _PlanStore(a.manager.store.path.parent, proof.sha256, a.cl7_identity_key).load()
    _require(cash_plan is not None and cash_plan["match"] == _match(receipt, components), "CASH_PLAN_REQUIRED")
    export = a.cl7_ledger_store.export_bytes()
    a.cl7_ledger_store.validate()
    _require(_check_prefix(json.loads(cash_plan["baseline_export_ascii"]), json.loads(export), components)
             == len(components), "CASH_COMPONENTS_INCOMPLETE")
    raw_cash = a.cash_authority_manager.read_accounting_cash(a.transport, a.policy.account_id,
        clock=a.cl7_clock, monotonic_ns=a.cl7_monotonic_ns)
    guard()
    now = _time(a.cl7_clock())
    cash = cl4.build_broker_rub_position_cash_proof(raw_cash, raw_account_id=a.policy.account_id,
        account_scope_sha256=receipt.account_scope_sha256, environment=BrokerEnvironment.SANDBOX,
        as_of=now, evaluated_at=now, response_complete=True, identity_key=a.cl7_identity_key,
        identity_key_id=a.cl7_identity_key_id)
    reconciled = cl4.reconcile_shadow_cash(export, cash, evaluated_at=now, identity_key=a.cl7_identity_key)
    _require(reconciled.status is cl4.ReconciliationStatus.MATCHED, "CASH_RECONCILIATION_FAILED")
    baseline = cl4.project_shadow_cash(cash_plan["baseline_export_ascii"].encode("ascii"),
        account_scope_sha256=receipt.account_scope_sha256, environment=BrokerEnvironment.SANDBOX,
        as_of=now, identity_key=a.cl7_identity_key)
    _require(baseline.complete and baseline.version == 3
             and baseline.ledger_head_sha256 == proof.ledger_head_sha256
             and baseline.ledger_revision == proof.ledger_revision
             and baseline.expected_cash.minor_units + receipt.expected_cash_delta_nano == cash.cash.minor_units,
             "CASH_BASELINE_MISMATCH")
    guard()
    _require(a.cl7_ledger_store.export_bytes() == export, "LEDGER_CHANGED")
    info = json.loads(export)
    binding = {"plan_sha256": _sha(_canonical(cash_plan)), "receipt_sha256": receipt.receipt_identity_sha256,
               "ledger_export_sha256": _sha(export), "ledger_head_sha256": info["ledger_head_sha256"],
               "ledger_revision": info["ledger_revision"], "cash_nano": str(cash.cash.minor_units)}
    return receipt, raw, binding, batch.watermark


def _portfolio_candidate(a: Any, recovery: Any, intent: Any, receipt: Any, raw: Any,
                         previous: PortfolioState, guard: Any) -> PortfolioState:
    c = intent.candidate
    _require(previous.portfolio_source == "CANONICAL" and previous.migration.complete
             and not previous.migration.legacy_read_path_enabled, "CANONICAL_INVALID")
    old = previous.position(c.instrument_id)
    if old is None:
        _require(c.current_lots == 0, "PREPOSITION_MISMATCH")
    else:
        _require(old.actual_lots == c.current_lots and old.target_lots in {None, c.current_lots}, "PREPOSITION_MISMATCH")
        if old.actual_lots:
            _require(old.ownership is not None and old.ownership.strategy_id == c.strategy_id
                     and old.ownership.config_hash == c.strategy_profile_hash
                     and old.ownership.candle_interval == c.candle_interval, "OWNERSHIP_MISMATCH")
    for pos in previous.positions:
        for p in pos.pending_orders:
            if p.active or p.uncertain:
                _require(p.source == "BROKER" and p.instrument_id == c.instrument_id
                         and p.order_request_id == intent.intent_id and p.broker_order_id == raw["orderId"], "OTHER_PENDING")
    profiles = [p for p in recovery.profiles.load_mode("SANDBOX_EXECUTION") if p.instrument_id == c.instrument_id]
    runtimes = [r for r in recovery.runtimes.load(expected_account_id=c.account_id) if r.runtime_key == c.runtime_key]
    _require(len(profiles) == len(runtimes) == 1, "RUNTIME_MISMATCH")
    profile, runtime = profiles[0], runtimes[0]
    _require(profile.strategy_profile_hash == c.strategy_profile_hash
             and runtime.config.runtime_config_hash == c.runtime_config_hash
             and runtime.config.to_dict() == profile.to_runtime_config(c.account_id).to_dict()
             and runtime.config.strategy_id == c.strategy_id and runtime.config.candle_interval == c.candle_interval,
             "RUNTIME_MISMATCH")
    portfolio, orders = recovery.policy.acquire(_ReceiptReadView(recovery.manager.api, SimpleNamespace(order=raw)), previous, guard)
    cash = recovery.cash_policy.acquire(a.transport, guard)
    broker = cash.apply(BrokerPortfolioAdapter.from_api_portfolio(portfolio, account_id=c.account_id, broker_orders=orders))
    guard()
    target = c.current_lots + (receipt.executed_lots if c.direction == "BUY" else -receipt.executed_lots)
    actual = next((p.actual_lots for p in broker.positions if p.instrument_id == c.instrument_id), 0)
    outcome = settlement_outcome(receipt)
    zero_terminal = outcome in {"CANCELLED", "REJECTED"}
    _require(target >= 0 and actual == target
             and (target == c.current_lots if zero_terminal else
                  target == c.target_lots if outcome == "FILLED" else
                  min(c.current_lots, c.target_lots) < target < max(c.current_lots, c.target_lots)),
             "OBSERVED_LOTS_MISMATCH")
    _require(_utc(broker.snapshot_at) > _utc(intent.updated_at)
             and all(_utc(broker.snapshot_at) >= _utc(s.execution_at) for s in receipt.stages), "SNAPSHOT_PREDATES_FILL")
    base = RuntimePortfolioAdapter.from_portfolio_state(previous)
    old_runtime = next((p for p in base.positions if p.instrument_id == c.instrument_id), None)
    owned = RuntimePositionRecord(instrument_id=c.instrument_id, figi=old_runtime.figi if old_runtime else "",
        ticker=c.ticker, class_code=runtime.config.class_code,
        target=PortfolioTarget(instrument_id=c.instrument_id, target_lots=target, strategy_id=c.strategy_id,
                               config_hash=c.strategy_profile_hash, candle_time=c.candle_time),
        ownership=PositionOwnership(strategy_id=c.strategy_id, config_hash=c.strategy_profile_hash,
            candle_interval=c.candle_interval, source="CANONICAL_TRANSACTION", attributed_at=broker.snapshot_at) if target else None,
        pending_orders=old_runtime.pending_orders if old_runtime else (), last_candle_time=c.candle_time,
        state_key="canonical:" + c.instrument_id)
    if zero_terminal:
        # A failed SELL must not re-attribute an existing holding; a failed BUY
        # cannot adopt a position merely because a provider snapshot contains it.
        owned = replace(owned, ownership=old_runtime.ownership if old_runtime else None)
    runtime_view = replace(base, positions=tuple([p for p in base.positions if p.instrument_id != c.instrument_id] + [owned]))
    candidate = recovery.manager.reconciler.reconcile(broker, runtime_view, previous=previous,
        context=ReconciliationContext(expected_account_id=c.account_id, freshness=SnapshotFreshness.FRESH,
            generated_at=broker.snapshot_at,
            journal_confirmed_instruments=frozenset() if zero_terminal else frozenset({c.instrument_id}),
            position_origins={} if zero_terminal else {c.instrument_id: PositionOrigin.STRATEGY}))
    candidate = recovery.manager._normalize_flat_positions(candidate)
    _require(not candidate.blocking and not any(p.active or p.uncertain for pos in candidate.positions for p in pos.pending_orders),
             "PORTFOLIO_RECONCILIATION_BLOCKED")
    return candidate


def _risk_after(before: dict[str, Any], execution: dict[str, Any]) -> RiskState:
    record = ExecutionRecord(**{**execution, "executed_at": _utc(execution["executed_at"]),
                               "portfolio_snapshot_at": _utc(execution["portfolio_snapshot_at"])})
    state = RiskState.from_dict(before)
    _require(record.execution_id not in state.recorded_execution_ids, "UNPLANNED_RISK_EXECUTION")
    registered = RiskEngine(RiskPolicy(enabled=False)).record_execution(state, record)
    _require(not registered.duplicate, "UNPLANNED_RISK_EXECUTION")
    return registered.state


def _make_plan(a: Any, recovery: Any, before: dict[str, Any], original: Any, receipt: Any,
               raw: Any, cash: Any, watermark: Any, config: str, guard: Any) -> dict[str, Any]:
    previous = PortfolioState.from_dict(before["portfolio"])
    candidate = _portfolio_candidate(a, recovery, original, receipt, raw, previous, guard)
    # A failed compatibility shadow is not silently adopted as a valid prefix.
    _require(previous.migration.compatibility_shadow_status is CompatibilityShadowStatus.OK
             and recovery.manager.transaction_coordinator.shadow_writer is not None, "SHADOW_PROFILE_UNSUPPORTED")
    tx = "exact-settlement:" + receipt.proof_sha256 + ":" + receipt.receipt_identity_sha256
    published = replace(candidate, revision=previous.revision + (candidate.decision_sha256 != previous.decision_sha256),
        portfolio_source="CANONICAL", migration=PortfolioMigrationMetadata.completed(
            source_schema=previous.migration.source_schema, migration_id=previous.migration.migration_id,
            migrated_at=previous.migration.migrated_at, shadow_status=CompatibilityShadowStatus.OK,
            detail="Canonical-only read path is active."), last_transaction_id=tx, last_transaction_status="COMMITTED")
    c = original.candidate
    zero_terminal = receipt.executed_lots == 0
    if zero_terminal:
        _require(original.intent_id not in before["risk"]["recorded_execution_ids"],
                 "ZERO_HAS_PRIOR_RISK_EXECUTION")
        execution = None
        risk = RiskState.from_dict(before["risk"])
    else:
        execution = {"execution_id": original.intent_id, "executed_at": max(s.execution_at for s in receipt.stages),
            "signed_lots": receipt.executed_lots if c.direction == "BUY" else -receipt.executed_lots,
            "price_rub": float(receipt.average_price_rub), "lot_size": c.lot_size,
            "portfolio_equity_rub": published.account.total_value,
            "portfolio_cash_rub": published.account.cash("rub").available,
            "portfolio_snapshot_at": published.snapshot_at, "execution_source": "STRATEGY"}
        risk = _risk_after(before["risk"], execution)
    central = CentralOrderState.from_dict(before["central"])
    final = original.transition("RECONCILED", at=_utc(published.snapshot_at).isoformat(),
        detail="exact cash + canonical settlement: " + receipt.proof_sha256,
        broker_order_id=raw["orderId"], outcome=settlement_outcome(receipt), executed_lots=receipt.executed_lots,
        reconciled_portfolio_revision=published.revision, reconciled_portfolio_decision_checksum=published.decision_sha256,
        reconciled_portfolio_snapshot_at=published.snapshot_at,
        risk_execution_status="NOT_REQUIRED" if zero_terminal else "RECORDED",
        risk_execution_id=None if zero_terminal else original.intent_id)
    central_after = replace(central.replace_intent(final), revision=central.revision + 1, updated_at=final.updated_at)
    pending = RuntimeCashAuthorityRecord.from_canonical_dict(before["authority"])
    authority = a.cash_authority_manager._change(pending, at=_time(a.cl7_clock()), kind=_ZERO_KIND if zero_terminal else _KIND,
        state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED, pending_dispatch_proof_sha256=None,
        ledger_head_sha256=cash["ledger_head_sha256"], ledger_revision=int(cash["ledger_revision"]),
        operations_complete_through=watermark.to_exclusive)
    return {"domain": _DOMAIN, "version": 1, "proof_sha256": receipt.proof_sha256,
            "cash_binding": cash, "watermark": json.loads(watermark.canonical_bytes), "before": before,
            "after": {"portfolio": published.to_dict(), "risk": risk.to_dict(),
                      "central": central_after.to_dict(), "authority": authority.to_canonical_dict()},
            "portfolio_candidate": candidate.to_dict(), "execution": execution,
            "config_sha256": config, "transaction_id": tx}


def _close_locked(a: Any, recovery: Any, proof_hash: str) -> ExactClosureResult:
    plans = _ClosureStore(a.manager.store.path.parent, proof_hash, a.cl7_identity_key)
    plan = plans.load()
    state = _owners(a, recovery)
    config = _config(a, recovery)
    if plan is None:
        pending = RuntimeCashAuthorityRecord.from_canonical_dict(state["authority"])
        _require(pending.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
                 and pending.pending_dispatch_proof_sha256 == proof_hash, "PENDING_OR_CHECKPOINT_REQUIRED")
        central = CentralOrderState.from_dict(state["central"])
        original = a.cash_authority_manager._recovery_intent_from_state(pending, central, identity_key=a.cl7_identity_key)
        _require(central.blocking_intent == original and not central.queued
                 and original.status in {"SUBMITTED", "UNCERTAIN"}, "INTENT_SCOPE_UNSUPPORTED")
    else:
        _require(plan["proof_sha256"] == proof_hash and plan["config_sha256"] == config, "PLAN_BINDING_MISMATCH")
        _prefix(plan, state)
        pending = RuntimeCashAuthorityRecord.from_canonical_dict(plan["before"]["authority"])
        central = CentralOrderState.from_dict(plan["before"]["central"])
        original = a.cash_authority_manager._recovery_intent_from_state(pending, central, identity_key=a.cl7_identity_key)
    start_tick, start_wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
    last_tick, last_wall = start_tick, start_wall
    _require(type(start_tick) is int and start_tick >= 0, "CLOCK_INVALID")
    expected = state
    def guard(*_: Any) -> None:
        nonlocal last_tick, last_wall
        tick, wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
        _require(type(tick) is int and last_tick <= tick <= start_tick + MAX_AGE_NS
                 and last_wall <= wall <= start_wall + MAX_AGE_NS, "OBSERVATIONS_STALE")
        last_tick, last_wall = tick, wall
        _require(_owners(a, recovery) == expected and _config(a, recovery) == config, "OWNERS_CHANGED")
        _require(plans.load() == plan, "PLAN_CHANGED")
    guard()
    receipt, raw, cash, watermark = _cash_evidence(a, pending, original, guard)
    now = _utc(a.cl7_clock())
    _require(all(_utc(s.execution_at).date() == now.date()
                 and _utc(s.execution_at).astimezone(MOSCOW_TZ).date() == now.astimezone(MOSCOW_TZ).date()
                 for s in receipt.stages), "ACCOUNTING_DAY_OUT_OF_SCOPE")
    if plan is None:
        plan_candidate = _make_plan(a, recovery, state, original, receipt, raw, cash, watermark, config, guard)
        guard()
        plans.create(plan_candidate)
        plan = plan_candidate
    else:
        _require(cash == plan["cash_binding"], "CASH_EVIDENCE_CHANGED")
        # Repeat live holdings validation against the immutable pre-state. No
        # current or earlier RECONCILED flag substitutes for a new observation.
        observed = _portfolio_candidate(a, recovery, original, receipt, raw,
                                       PortfolioState.from_dict(plan["before"]["portfolio"]), guard)
        wanted = PortfolioState.from_dict(plan["after"]["portfolio"])
        _require([(p.instrument_id, p.actual_lots, p.target_lots, p.ownership) for p in observed.positions]
                 == [(p.instrument_id, p.actual_lots, p.target_lots,
                      replace(p.ownership, attributed_at=observed.snapshot_at) if receipt.executed_lots and p.ownership and p.instrument_id == original.candidate.instrument_id else p.ownership)
                     for p in wanted.positions], "HOLDINGS_CHANGED")
    guard()
    phase = _prefix(plan, expected)
    replay = phase == 4
    if phase == 0:
        previous = PortfolioState.from_dict(plan["before"]["portfolio"])
        def transform(current: PortfolioState) -> PortfolioState:
            guard()
            _require(current.to_dict() == plan["before"]["portfolio"], "PORTFOLIO_CHANGED")
            return PortfolioState.from_dict(plan["portfolio_candidate"])
        event = ("EXACT_SETTLEMENT_ZERO_TERMINAL" if receipt.executed_lots == 0 else
                 "EXACT_SETTLEMENT_FULL_FILL" if settlement_outcome(receipt) == "FILLED"
                 else "EXACT_SETTLEMENT_PARTIAL_CANCEL")
        recovery.manager.transaction_coordinator.commit(event, transform,
            expected_revision=previous.revision, transaction_id=plan["transaction_id"], account_id=a.policy.account_id,
            instrument_id=original.candidate.instrument_id, mode="SANDBOX_EXECUTION")
        expected = {**expected, "portfolio": plan["after"]["portfolio"]}
        guard()
    # Final M4-compatible lock order: authority (already held), canonical,
    # policy/state, CL2 writer, Central. No provider calls under these locks.
    with ExitStack() as locks:
        # Configuration writers take only their corresponding file lock and do
        # not call the provider or wait for the cash-authority lock.
        for path in (recovery.profiles.lock_path, recovery.runtimes.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=0.1))
        canonical = locks.enter_context(recovery.manager.repository.locked_snapshot(expected_account_id=a.policy.account_id))
        for path in (recovery.risk.profile_store.lock_path, recovery.risk.state_store.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=0.1))
        locks.enter_context(a.cash_authority_manager.ledger_guard(a.cl7_ledger_store))
        locks.enter_context(InterProcessFileLock(a.manager.store.lock_path, timeout_seconds=0.1))
        guard()
        _require(canonical.to_dict() == plan["after"]["portfolio"]
                 and _sha(a.cl7_ledger_store.export_bytes()) == cash["ledger_export_sha256"], "FINAL_EVIDENCE_CHANGED")
        phase = _prefix(plan, expected)
        if phase < 4:
            # Canonical commit can survive a failure before its compatibility
            # shadow write. Rebuild only that non-authoritative shadow from the
            # exact committed state, never repair any economic owner by guess.
            _require(recovery.manager.transaction_coordinator.shadow_writer.write(canonical)
                     is CompatibilityShadowStatus.OK, "SHADOW_WRITE_FAILED")
            guard()
        lease = a.manager._validate_reconciliation(recovery.manager.repository, original,
            outcome=settlement_outcome(receipt), executed_lots=receipt.executed_lots, locked_portfolio_state=canonical)
        _require(lease.revision == canonical.revision, "CANONICAL_LEASE_INVALID")
        if receipt.executed_lots == 0:
            _require(plan["execution"] is None
                     and plan["after"]["risk"] == plan["before"]["risk"]
                     and original.intent_id not in plan["before"]["risk"]["recorded_execution_ids"],
                     "ZERO_RISK_PLAN_INVALID")
            # No zero ExecutionRecord, no RiskEngine call and no Risk store write.
        elif phase < 2:
            after_risk = _risk_after(plan["before"]["risk"], plan["execution"])
            _require(after_risk.to_dict() == plan["after"]["risk"], "RISK_PLAN_MISMATCH")
            recovery.risk.state_store.save_account_while_locked(a.policy.account_id, after_risk)
            expected = {**expected, "risk": plan["after"]["risk"]}
            guard()
        if phase < 3:
            a.manager.store._save_unlocked(CentralOrderState.from_dict(plan["after"]["central"]))
            expected = {**expected, "central": plan["after"]["central"]}
            guard()
        if phase < 4:
            # Durable audit is required before clear. Duplicate audit events on
            # replay are possible; financial state writes are not duplicated.
            recovery.manager.transaction_coordinator._record("EXACT_SETTLEMENT_ACCOUNTED",
                account_id=a.policy.account_id, instrument_id=original.candidate.instrument_id,
                mode="SANDBOX_EXECUTION", status="accounted_pending_clear",
                payload={"proof_sha256": proof_hash, "plan_sha256": _sha(_canonical(plan)),
                         "ledger_head_sha256": cash["ledger_head_sha256"]})
            guard()
            _require(_sha(a.cl7_ledger_store.export_bytes()) == cash["ledger_export_sha256"], "LEDGER_CHANGED")
            done = RuntimeCashAuthorityRecord.from_canonical_dict(plan["after"]["authority"])
            a.cash_authority_manager.store._commit_unlocked(done,
                expected_revision=pending.record_revision, expected_sha256=pending.sha256)
            expected = {**expected, "authority": plan["after"]["authority"]}
            guard()
    _require(_prefix(plan, _owners(a, recovery)) == 4, "CLOSURE_INCOMPLETE")
    done = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
    return ExactClosureResult(proof_hash, _sha(_canonical(plan)), cash["plan_sha256"],
        cash["ledger_head_sha256"], int(cash["ledger_revision"]), done.sha256, replay)


def finalize_exact_settlement(adapter: Any, *, recovery: DesktopFillRecovery,
                              proof_sha256: str) -> ExactClosureResult:
    """Explicit terminal-execution closure on the same owner graph, after STEP14.

    Does not post, cancel, run Strategy, automatically arm, or trust a supplied
    CashComponentsResult. Old generic completion APIs stay fail-closed.
    """
    try:
        _require(type(recovery) is DesktopFillRecovery, "OWNER_GRAPH_MISMATCH")
        with InterProcessFileLock(recovery.lock_path, timeout_seconds=0.1):
            with adapter.cash_authority_manager.store.locked():
                return _close_locked(adapter, recovery, proof_sha256)
    except ExactSettlementClosureError:
        raise
    except Exception:
        raise ExactSettlementClosureError("EXACT_CLOSURE_DEPENDENCY_FAILED") from None
