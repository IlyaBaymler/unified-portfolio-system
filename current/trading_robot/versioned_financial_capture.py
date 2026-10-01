"""Read-only fresh evidence for the version-aware financial review path.

Uses a still-unchanged last DISARMED source closure and an independently pinned
v3 journal. No source writes/HOLD/arm/cutover. Fresh observations are bound to
the already-reviewed corrected operation, not merely a matching RUB balance.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from . import cash_availability as cl5
from . import cash_buying_availability as buying
from . import cash_ledger_opening_reconciliation as cl4
from . import reporting_risk_cash_context as cl6
from . import versioned_fee_corrections as journal
from .broker_read_adapters import BrokerEnvironment
from .central_order_manager import CentralOrderState
from .exact_cash_settlement import _PlanStore
from .exact_late_fee import _collect_overlap, _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .exact_settlement_closure import _ClosureStore, _config, _owners
from .portfolio_preflight import PortfolioSnapshotLease
from .risk import RiskState
from .runtime_cash_authority import RuntimeCashAuthorityRecord, RuntimeCashAuthorityState, _transition_pair
from .versioned_fee_evidence import _canonical, _parse, _sha, evaluate_captured_fee_revision
from .versioned_financial_readers import (
    VersionedCashProjection, VersionedFinancialContext, VersionedFinancialReadError,
    VersionedReadPins, _build, _checked, _require, project_versioned_cash,
)


@dataclass(frozen=True, slots=True)
class VersionedFinancialCapture:
    context: VersionedFinancialContext
    projection: VersionedCashProjection
    primary_evidence_bytes: bytes
    primary_evidence_sha256: str
    # Full primary bytes are PRIVATE, not part of a support-bundle summary.

    def public_summary(self) -> dict[str, Any]:
        return {**self.context.public_summary(),
            "fresh_economic_evidence_sha256": self.primary_evidence_sha256,
            "source_owners_written": False, "provider_post_order_called": False}


def capture_versioned_financial_review(adapter: Any, *, recovery: Any,
        journal_store: journal.VersionedFeeCorrectionStore,
        pins: VersionedReadPins) -> VersionedFinancialCapture:
    """Fresh CL4/CL5/CL6 review, explicitly not an execution or migration API."""
    try:
        return _capture(adapter, recovery, journal_store, pins)
    except VersionedFinancialReadError:
        raise
    except Exception:
        raise VersionedFinancialReadError("VERSIONED_READ_CAPTURE_INVALID") from None


def _capture(a: Any, r: Any, store: Any, pins: VersionedReadPins, *,
        _cutover_origin: RuntimeCashAuthorityRecord | None = None,
        _cutover_held: RuntimeCashAuthorityRecord | None = None,
        _authority_lock_held: bool = False) -> VersionedFinancialCapture:
    _require(type(store) is journal.VersionedFeeCorrectionStore, "JOURNAL_TYPE_INVALID")
    manager, ledger = a.cash_authority_manager, a.cl7_ledger_store
    transport, own, budget = a.transport, a.cl7_own_funds_policy, manager.buying_budget_policy
    key, key_id, clock, monotonic = a.cl7_identity_key, a.cl7_identity_key_id, a.cl7_clock, a.cl7_monotonic_ns
    _require(type(budget) is buying.OwnBuyingBudgetPolicy and own is not None,
             "OWN_BUDGET_POLICY_REQUIRED")
    with (nullcontext() if _authority_lock_held else manager.store.locked()):
        config, owners = _config(a, r), _owners(a, r)
        authority = RuntimeCashAuthorityRecord.from_canonical_dict(owners["authority"])
        closure_owners = owners
        if _cutover_origin is not None or _cutover_held is not None:
            # Private continuation of a persisted, exact source-selection HOLD.
            # The caller also verifies the immutable cutover plan. This only
            # permits READS; it never clears a hold or grants financial authority.
            _require(type(_cutover_origin) is RuntimeCashAuthorityRecord
                     and type(_cutover_held) is RuntimeCashAuthorityRecord
                     and authority == _cutover_held
                     and _cutover_origin.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
                     and _cutover_held.state is RuntimeCashAuthorityState.EXACT_CASH_SOURCE_CUTOVER_PENDING
                     and _cutover_held.transition_kind == "VERSIONED_SOURCE_CUTOVER_HELD",
                     "CUTOVER_READ_SCOPE_INVALID")
            _transition_pair(_cutover_origin, _cutover_held)
            closure_owners = dict(owners, authority=_cutover_origin.to_canonical_dict())
        else:
            _require(authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
                     "DISARMED_SOURCE_REQUIRED")
        scope = authority.account_scope_sha256
        raw = store.export_bytes()
        registry = tuple(store._registry.values())
        checked = _checked(raw, pins, registry, key, key_id, scope)
        original_capture = _parse(checked.record["capture_json_ascii"])
        closed_payload = _parse(original_capture["closure_json_ascii"])["payload"]
        proof = closed_payload["proof_sha256"]
        root = a.manager.store.path.parent
        cs, ps = _ClosureStore(root, proof, key), _PlanStore(root, proof, key)
        closure, cash_plan = cs.load(), ps.load()
        source = ledger.export_bytes()
        _require(closure == closed_payload and closure_owners == closure["after"]
                 and config == closure["config_sha256"] and source == checked.graph.base
                 and cash_plan == _parse(original_capture["cash_plan_json_ascii"])["payload"],
                 "SOURCE_CLOSURE_CHANGED")
        _require(budget.account_id == a.policy.account_id
                 and dict(budget.instruments) == dict(own.instruments)
                 and budget.execution_order_type in {"MARKET", "BESTPRICE"},
                 "BUYING_POLICY_BINDING_INVALID")
        budget.binding_guard()
        budget_scope = budget.scope_sha256
        start_wall, start_tick = timestamp_ns(clock()), monotonic()
        _require(type(start_tick) is int and start_tick >= 0, "CLOCK_INVALID")
        last_wall, last_tick = start_wall, start_tick

        def guard() -> None:
            nonlocal last_wall, last_tick
            wall, tick = timestamp_ns(clock()), monotonic()
            _require(type(tick) is int and last_tick <= tick <= start_tick + MAX_AGE_NS
                     and last_wall <= wall <= start_wall + MAX_AGE_NS, "CAPTURE_STALE")
            last_tick, last_wall = tick, wall
            _require(a.cash_authority_manager is manager and a.cl7_ledger_store is ledger
                     and a.transport is transport and a.cl7_own_funds_policy is own
                     and manager.buying_budget_policy is budget
                     and a.cl7_identity_key == key and a.cl7_identity_key_id == key_id
                     and a.cl7_clock is clock and a.cl7_monotonic_ns is monotonic,
                     "OWNER_GRAPH_CHANGED")
            budget.binding_guard()
            _require(_owners(a, r) == owners and _config(a, r) == config
                     and ledger.export_bytes() == source and store.export_bytes() == raw
                     and cs.load() == closure and ps.load() == cash_plan
                     and budget.scope_sha256 == budget_scope, "SOURCE_CHANGED")
            # Include local validation time itself, not only provider latency.
            wall_end, tick_end = timestamp_ns(clock()), monotonic()
            _require(type(tick_end) is int and last_tick <= tick_end <= start_tick + MAX_AGE_NS
                     and last_wall <= wall_end <= start_wall + MAX_AGE_NS, "CAPTURE_STALE")
            last_tick, last_wall = tick_end, wall_end

        guard()
        pending = RuntimeCashAuthorityRecord.from_canonical_dict(closure["before"]["authority"])
        intent = manager._recovery_intent_from_state(pending,
            CentralOrderState.from_dict(closure["before"]["central"]), identity_key=key)
        _require(budget.execution_order_type == intent.candidate.order_type, "ORDER_CHOICE_CHANGED")
        response = _parse(_canonical(transport.get_order_state(a.policy.account_id, intent.intent_id,
                                                             by_request_id=True)))
        guard()
        end = clock()
        _require(timestamp_ns(end) >= timestamp_ns(original_capture["captured_at"]),
                 "OBSERVATION_ROLLBACK")
        requests, responses = [], []

        def collect(payload: Any, timeout_ns: int) -> Any:
            result = _parse(_canonical(transport.get_operations_by_cursor_once(payload, timeout_ns)))
            requests.append(_parse(_canonical(payload)))
            responses.append(result)
            return result

        view = SimpleNamespace(transport=SimpleNamespace(get_operations_by_cursor_once=collect),
            policy=a.policy, cl7_identity_key=key, cl7_identity_key_id=key_id,
            cl7_monotonic_ns=monotonic, cl7_wait_ns=a.cl7_wait_ns)
        _collect_overlap(view, scope, pending.operations_complete_through, end,
                         start_tick + MAX_AGE_NS, guard)
        position_at = clock()
        positions = _parse(_canonical(transport.get_positions(a.policy.account_id)))
        guard()
        fresh = dict(original_capture, captured_at=end, order_state=response,
            operation_requests=requests, operation_responses=responses, rub_positions=positions)
        fresh_bytes = _canonical(fresh)
        evaluation = evaluate_captured_fee_revision(checked.graph.base, fresh_bytes,
            original_transaction_sha256=checked.record["original_transaction_sha256"],
            codec_registry=registry, identity_key=key, identity_key_id=key_id, account_scope_sha256=scope)
        # New timestamps/request ranges need not equal the historical capture.
        # The observed version, execution, fee and complete correction must.
        _require(evaluation.bundle.canonical_bytes.decode("ascii") == checked.record["bundle_json_ascii"]
                 and evaluation.observation.sha256 == checked.record["provenance"]["observation_sha256"]
                 and evaluation.cash_after_nano == checked.snapshot.cash_nano,
                 "CURRENT_ECONOMICS_CHANGED")
        own_proof = budget.acquire(transport, account_scope_sha256=scope, identity_key=key,
            identity_key_id=key_id, clock=clock, monotonic_ns=monotonic)
        guard()
        evaluated = clock()
        cash = cl4.build_broker_rub_position_cash_proof(positions, raw_account_id=a.policy.account_id,
            account_scope_sha256=scope, environment=BrokerEnvironment.SANDBOX,
            as_of=position_at, evaluated_at=evaluated, response_complete=True,
            identity_key=key, identity_key_id=key_id)
        reservations = cl5.project_central_reservations(CentralOrderState.from_dict(owners["central"]),
            account_scope_sha256=scope, environment=BrokerEnvironment.SANDBOX,
            evaluated_at=evaluated, identity_key=key, identity_key_id=key_id)
        state = r.manager.repository.load(expected_account_id=a.policy.account_id)
        port = cl6.build_portfolio_identity_evidence(PortfolioSnapshotLease.from_state(state,
            leased_at=datetime.fromisoformat(evaluated.replace("Z", "+00:00")).isoformat()), account_scope_sha256=scope, environment=BrokerEnvironment.SANDBOX,
            evaluated_at=evaluated, identity_key=key, identity_key_id=key_id)
        profile = r.risk.profile_store.load_profile(r.risk.mode)
        _require(profile is not None and profile["account_scope"] in {None, a.policy.account_id},
                 "RISK_PROFILE_SCOPE_INVALID")
        risk_policy = profile["policy"]
        risk = cl6.build_risk_guard_evidence(risk_policy, RiskState.from_dict(owners["risk"]),
            raw_account_id=a.policy.account_id, account_scope_sha256=scope,
            environment=BrokerEnvironment.SANDBOX, captured_at=evaluated,
            evaluated_at=evaluated, identity_key=key, identity_key_id=key_id)
        result = _build(raw, pins=pins, codec_registry=registry, identity_key=key,
            identity_key_id=key_id, account_scope_sha256=scope, broker_cash=cash,
            own_buying=own_proof, reservations=reservations, portfolio=port, risk_guard=risk,
            buying_scope_sha256=budget_scope, evaluated_at=evaluated,
            current_economic_evidence_sha256=_sha(fresh_bytes))
        with _owner_locks(a, r, ledger=True):
            guard()
        return VersionedFinancialCapture(result,
            project_versioned_cash(raw, pins=pins, codec_registry=registry, identity_key=key,
                identity_key_id=key_id, account_scope_sha256=scope),
            fresh_bytes, _sha(fresh_bytes))
