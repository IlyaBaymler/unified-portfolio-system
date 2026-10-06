"""Explicit, quarantined late additive fee for the latest closed exact intent.

Only one additional BROKER_FEE is supported per closure, within seven days.
The complete overlapping CL3 window must preserve all previously settled
components. A fee alias/replacement, refund, missing earlier row or second
amendment is rejected, never silently re-booked. The cumulative receipt must
increase by exactly the additional debit. No Risk/Portfolio/Central write occurs.

The new authority hold is committed BEFORE provider reads and before any CL2
write. An error retains it. A keyed immutable plan admits only the exact CL2
append prefix; authority returns to DISARMED last. This is not an atomic
multi-store transaction, fee finality proof, background scanner or re-arm API.
Completed replay first authenticates local custody. Invalid fresh evidence then
commits a distinct review hold; no automatic resolution of that hold exists.
"""
from __future__ import annotations

import hmac
import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from . import broker_read_adapters as cl3
from . import cash_ledger_opening_reconciliation as cl4
from .cash_ledger_domain import LedgerAccount, LedgerClassification, LedgerPosting, LedgerTransaction
from .cash_ledger_persistence import InboxObservation
from .central_order_manager import CentralOrderState
from .desktop_fill_recovery import DesktopFillRecovery
from .exact_cash_settlement import (
    _HEX, _MAX_PLAN_BYTES, _PlanStore, _canonical, _check_prefix, _components,
    _match, _pairs, _safe_path, _seal, _sha,
)
from .exact_order_receipt import _id, _time, _uint, decode_exact_order_receipt, validate_exact_receipt_binding
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .exact_settlement_closure import _ClosureStore, _config, _owners
from .locking import InterProcessFileLock
from .runtime_cash_authority import RuntimeCashAuthorityRecord, RuntimeCashAuthorityState
from .state_persistence import atomic_write_json

_DOMAIN = "CL7_EXACT_LATE_ADDITIVE_FEE_V1"
_HOLD = "LATE_FEE_ADJUSTMENT_HELD"
_DONE = "LATE_FEE_ADJUSTMENT_CLOSED_DISARMED"
_REVIEW = "LATE_FEE_REVIEW_HELD"
_MAX_WINDOW_NS = 7 * 24 * 3600 * 10**9


class ExactLateFeeError(RuntimeError):
    """Finite failure. A persisted hold or CL2 prefix may remain after failure."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ExactLateFeeError("EXACT_LATE_FEE_" + code)


class _LateFeeStore:
    def __init__(self, root: Path, proof: str, key: bytes) -> None:
        _require(type(proof) is str and _HEX.fullmatch(proof) is not None, "PROOF_INVALID")
        self.path = root / "exact_late_fee" / (proof + ".json")
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
            "domain", "version", "proof_sha256", "closure_sha256", "cash_plan_sha256",
            "config_sha256", "held_authority", "baseline_export_ascii", "match", "watermark",
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
class LateFeeResult:
    proof_sha256: str
    plan_sha256: str
    transaction_sha256: str
    ledger_head_sha256: str
    ledger_revision: int
    authority_record_sha256: str
    fee_delta_nano: int
    appended_transactions: int
    replay: bool

    def to_canonical_dict(self) -> dict[str, Any]:
        # Amounts and raw account/order/operation IDs are private attributes.
        return {"domain": _DOMAIN + "_RESULT", "version": 1,
                "status": _DONE, "proof_sha256": self.proof_sha256,
                "plan_sha256": self.plan_sha256, "transaction_sha256": self.transaction_sha256,
                "ledger_head_sha256": self.ledger_head_sha256,
                "ledger_revision": str(self.ledger_revision),
                "authority_record_sha256": self.authority_record_sha256,
                "appended_transactions": self.appended_transactions, "replay": self.replay,
                "risk_execution_written": False, "automatic_rearm_allowed": False,
                "future_fee_finality_claimed": False}


@contextmanager
def _owner_locks(a: Any, r: DesktopFillRecovery, *, ledger: bool) -> Iterator[None]:
    # Authority is already held. Match the existing closure lock order.
    with ExitStack() as locks:
        for path in (r.profiles.lock_path, r.runtimes.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=0.1))
        locks.enter_context(r.manager.repository.locked_snapshot(expected_account_id=a.policy.account_id))
        for path in (r.risk.profile_store.lock_path, r.risk.state_store.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=0.1))
        if ledger:
            locks.enter_context(a.cash_authority_manager.ledger_guard(a.cl7_ledger_store))
        locks.enter_context(InterProcessFileLock(a.manager.store.lock_path, timeout_seconds=0.1))
        yield


def _collect_overlap(a: Any, scope: str, start: str, end: str, deadline: int,
                     guard: Any) -> tuple[Any, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []

    def transport(payload: Any, timeout_ns: int) -> Any:
        guard()
        raw = json.loads(_canonical(a.transport.get_operations_by_cursor_once(payload, timeout_ns)),
                         object_pairs_hook=_pairs)
        guard()
        if type(raw) is dict and type(raw.get("items")) is list:
            rows.extend(raw["items"])
        return raw

    batch = cl3.collect_tbank_operations(cl3.BrokerReadRequest(
        environment=cl3.BrokerEnvironment.SANDBOX, raw_account_id=a.policy.account_id,
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        from_inclusive=start, to_exclusive=end, limit=100, max_pages=4, max_items=8,
        absolute_deadline_ns=deadline, retry_policy=cl3.RetryPolicy(1, MAX_AGE_NS, ()),
        transport=transport, monotonic_ns=a.cl7_monotonic_ns, wait_ns=a.cl7_wait_ns))
    guard()
    _require(batch.watermark.account_scope_sha256 == scope
             and len(batch.decisions) == len(rows) == batch.watermark.item_count, "BATCH_BINDING_INVALID")
    return batch, rows


def _ledger_component(export: dict[str, Any], tx_hash: str) -> tuple[Any, LedgerTransaction]:
    rows = [v for v in export["transactions"] if v["sha256"] == tx_hash]
    links = [v for v in export["provenance_links"] if v["transaction_sha256"] == tx_hash]
    _require(len(rows) == len(links) == 1, "COMPLETED_COMPONENT_MISSING")
    obs = [v for v in export["observations"] if v["sha256"] == links[0]["observation_sha256"]]
    _require(len(obs) == 1 and obs[0]["current_status"] == "LEDGER_LINKED", "COMPLETED_OBSERVATION_MISSING")
    observation = InboxObservation.from_canonical_bytes(obs[0]["canonical_json_ascii"], (cl3.TBANK_OPERATION_CODEC,))
    transaction = LedgerTransaction.from_canonical_dict(json.loads(rows[0]["canonical_json_ascii"]))
    _require(transaction.sha256 == tx_hash and transaction.source == observation.source, "COMPLETED_SOURCE_INVALID")
    return observation, transaction


def _completed_replay_authority(a: Any, r: DesktopFillRecovery, proof_hash: str) -> RuntimeCashAuthorityRecord | None:
    """Authenticate the exact completed local lineage before any fresh reads.

    A caller hash, a plan signature or a DISARMED label alone is insufficient.
    This reader grants no quarantine over an unfinished or unrelated authority.
    """
    manager, store, key = a.cash_authority_manager, a.cl7_ledger_store, a.cl7_identity_key
    current = manager.store._load_unlocked(allow_missing_legacy=False)
    if current.state is not RuntimeCashAuthorityState.EXACT_CASH_DISARMED or current.transition_kind != _DONE:
        return None
    root = a.manager.store.path.parent
    plan = _LateFeeStore(root, proof_hash, key).load()
    closure = _ClosureStore(root, proof_hash, key).load()
    cash_plan = _PlanStore(root, proof_hash, key).load()
    _require(plan is not None and closure is not None and cash_plan is not None, "COMPLETED_PLANS_REQUIRED")
    config = _config(a, r)
    _require(plan["proof_sha256"] == closure["proof_sha256"] == proof_hash
             and cash_plan["match"]["proof_sha256"] == proof_hash
             and plan["closure_sha256"] == _sha(_canonical(closure))
             and plan["cash_plan_sha256"] == closure["cash_binding"]["plan_sha256"] == _sha(_canonical(cash_plan))
             and plan["config_sha256"] == closure["config_sha256"] == config, "COMPLETED_PLAN_BINDING_INVALID")
    owners = _owners(a, r)
    _require(all(owners[k] == closure["after"][k] for k in ("portfolio", "risk", "central")), "COMPLETED_OWNERS_CHANGED")
    central = CentralOrderState.from_dict(owners["central"])
    _require(central.blocking_intent is None and not central.queued, "COMPLETED_CENTRAL_NOT_QUIESCENT")
    pending = RuntimeCashAuthorityRecord.from_canonical_dict(closure["before"]["authority"])
    original = manager._recovery_intent_from_state(pending,
        CentralOrderState.from_dict(closure["before"]["central"]), identity_key=key)
    proof = validate_exact_receipt_binding(original, account_id=a.policy.account_id,
        identity_key=key, identity_key_id=a.cl7_identity_key_id,
        instrument=a.cl7_own_funds_policy.instruments.get(original.candidate.instrument_id))
    _require(proof.sha256 == proof_hash, "COMPLETED_PROOF_INVALID")
    baseline = plan["baseline_export_ascii"].encode("ascii")
    _require(_sha(baseline) == closure["cash_binding"]["ledger_export_sha256"], "COMPLETED_BASELINE_CHANGED")
    base = json.loads(baseline)
    old_components = tuple(_ledger_component(base, item["transaction_sha256"]) for item in cash_plan["match"]["components"])
    _require([o.sha256 for o, _ in old_components] == [item["observation_sha256"] for item in cash_plan["match"]["components"]]
             and _check_prefix(json.loads(cash_plan["baseline_export_ascii"]), base, old_components) == len(old_components),
             "COMPLETED_CASH_PREFIX_INVALID")
    match = plan["match"]
    _require(type(match) is dict and set(match) == {"old_receipt_sha256", "new_receipt_sha256", "delta_nano",
             "observations", "observation_sha256", "transaction_sha256", "overlap_start"}, "COMPLETED_MATCH_INVALID")
    store.validate()
    exported = store.export_bytes()
    final = json.loads(exported)
    observation, transaction = _ledger_component(final, match["transaction_sha256"])
    payment = next(p.money.minor_units for p in transaction.postings if p.account is LedgerAccount.ASSET_BROKER_CASH)
    # The validated Money posting supplies the range; a quantity parser would
    # incorrectly narrow the existing nanorouble monetary profile to uint64.
    delta = -payment
    _require(delta > 0 and match["delta_nano"] == str(delta) and transaction.classification is LedgerClassification.COMMISSION
             and transaction.corrects_sha256 is None and transaction.reversal_of_sha256 is None
             and observation.source.account_scope_sha256 == proof.account_scope_sha256
             and observation.sha256 == match["observation_sha256"]
             and type(match["new_receipt_sha256"]) is str and _HEX.fullmatch(match["new_receipt_sha256"]) is not None
             and match["new_receipt_sha256"] != match["old_receipt_sha256"]
             and match["old_receipt_sha256"] == cash_plan["match"]["receipt_sha256"] == closure["cash_binding"]["receipt_sha256"]
             and _check_prefix(base, final, ((observation, transaction),)) == 1, "COMPLETED_LEDGER_INVALID")
    watermark = cl3.CompletenessWatermark(
        account_scope_sha256=plan["watermark"]["account_scope_sha256"],
        from_inclusive=plan["watermark"]["from_inclusive"], to_exclusive=plan["watermark"]["to_exclusive"],
        request_fingerprint_sha256=plan["watermark"]["request_fingerprint_sha256"],
        page_chain_sha256=plan["watermark"]["page_chain_sha256"],
        page_count=_uint(plan["watermark"]["page_count"]), item_count=_uint(plan["watermark"]["item_count"]))
    expected_observations = sorted([o.sha256 for o, _ in old_components] + [observation.sha256])
    _require(json.loads(watermark.canonical_bytes) == plan["watermark"]
             and watermark.account_scope_sha256 == proof.account_scope_sha256
             and watermark.from_inclusive == match["overlap_start"] == pending.operations_complete_through
             and watermark.item_count == len(expected_observations) and match["observations"] == expected_observations,
             "COMPLETED_WATERMARK_INVALID")
    closed = RuntimeCashAuthorityRecord.from_canonical_dict(closure["after"]["authority"])
    _require(closed.account_scope_sha256 == proof.account_scope_sha256
             and closed.identity_key_id == a.cl7_identity_key_id
             and closed.ledger_head_sha256 == base["ledger_head_sha256"]
             and closed.ledger_revision == int(base["ledger_revision"]), "COMPLETED_CLOSURE_LEDGER_MISMATCH")
    held = RuntimeCashAuthorityRecord.from_canonical_dict(plan["held_authority"])
    wanted = manager._change(closed, at=held.transition_at, kind=_HOLD,
        state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING, pending_dispatch_proof_sha256=proof_hash)
    _require(held == wanted and RuntimeCashAuthorityRecord.from_canonical_bytes(manager.store.lastgood_path.read_bytes()) == held,
             "COMPLETED_HOLD_LINEAGE_INVALID")
    done = manager._change(held, at=watermark.to_exclusive, kind=_DONE,
        state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED, pending_dispatch_proof_sha256=None,
        ledger_head_sha256=final["ledger_head_sha256"], ledger_revision=int(final["ledger_revision"]),
        operations_complete_through=watermark.to_exclusive)
    _require(current == done, "COMPLETED_AUTHORITY_MISMATCH")
    return done


def _reconcile_locked(a: Any, r: DesktopFillRecovery, proof_hash: str) -> LateFeeResult:
    completed = _completed_replay_authority(a, r, proof_hash)
    identity = (a.cash_authority_manager, a.cl7_ledger_store, a.transport, a.cl7_own_funds_policy,
                a.cl7_identity_key, a.cl7_identity_key_id)
    try:
        return _adjust_locked(a, r, proof_hash)
    except Exception:
        if completed is not None:
            # Do not redirect a stale observation onto another owner/authority.
            now = (a.cash_authority_manager, a.cl7_ledger_store, a.transport, a.cl7_own_funds_policy,
                   a.cl7_identity_key, a.cl7_identity_key_id)
            _require(all(before is after for before, after in zip(identity[:4], now[:4], strict=True))
                     and identity[4:] == now[4:], "REPLAY_OWNER_GRAPH_CHANGED")
            with _owner_locks(a, r, ledger=True):
                _require(_completed_replay_authority(a, r, proof_hash) == completed, "REPLAY_QUARANTINE_CONFLICT")
                try:
                    at = _time(a.cl7_clock())
                    _require(at >= completed.transition_at, "REPLAY_CLOCK_REGRESSION")
                except Exception:
                    # A hold grants no freshness. Retain the last authenticated
                    # timestamp when the failing read/clock cannot supply one.
                    at = completed.transition_at
                reviewed = a.cash_authority_manager._change(completed, at=at, kind=_REVIEW,
                    state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
                    pending_dispatch_proof_sha256=proof_hash)
                committed = a.cash_authority_manager.store._commit_unlocked(reviewed,
                    expected_revision=completed.record_revision, expected_sha256=completed.sha256)
                _require(committed == reviewed, "REPLAY_QUARANTINE_READBACK_FAILED")
        raise


def _adjust_locked(a: Any, r: DesktopFillRecovery, proof_hash: str) -> LateFeeResult:
    store, manager, key = a.cl7_ledger_store, a.cash_authority_manager, a.cl7_identity_key
    root = a.manager.store.path.parent
    transport, key_id = a.transport, a.cl7_identity_key_id
    plans = _LateFeeStore(root, proof_hash, key)
    plan = plans.load()
    closure_store = _ClosureStore(root, proof_hash, key)
    closure = closure_store.load()
    cash_store = _PlanStore(root, proof_hash, key)
    cash_plan = cash_store.load()
    _require(closure is not None and cash_plan is not None
             and closure["proof_sha256"] == proof_hash
             and closure["cash_binding"]["plan_sha256"] == _sha(_canonical(cash_plan)), "CLOSURE_REQUIRED")
    config = _config(a, r)
    _require(config == closure["config_sha256"], "CONFIG_CHANGED")
    original_authority = RuntimeCashAuthorityRecord.from_canonical_dict(closure["after"]["authority"])
    _require(original_authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
             and original_authority.pending_dispatch_proof_sha256 is None, "CLOSURE_NOT_DISARMED")
    pending = RuntimeCashAuthorityRecord.from_canonical_dict(closure["before"]["authority"])
    prior_central = CentralOrderState.from_dict(closure["before"]["central"])
    original = manager._recovery_intent_from_state(pending, prior_central, identity_key=key)
    current = _owners(a, r)
    frozen_owners = {k: closure["after"][k] for k in ("portfolio", "risk", "central")}
    _require(all(current[k] == v for k, v in frozen_owners.items()), "NOT_LATEST_UNCHANGED_CLOSURE")
    central = CentralOrderState.from_dict(current["central"])
    _require(central.blocking_intent is None and not central.queued, "CENTRAL_NOT_QUIESCENT")
    own = a.cl7_own_funds_policy
    instrument = own.instruments.get(original.candidate.instrument_id)
    proof = validate_exact_receipt_binding(original, account_id=a.policy.account_id,
        identity_key=key, identity_key_id=a.cl7_identity_key_id, instrument=instrument)
    _require(proof.sha256 == proof_hash, "PROOF_BINDING_INVALID")
    authority = RuntimeCashAuthorityRecord.from_canonical_dict(current["authority"])
    old_export_hash = closure["cash_binding"]["ledger_export_sha256"]
    store.validate()
    exported = store.export_bytes()
    if plan is None:
        _require(_sha(exported) == old_export_hash, "PLAN_MISSING_FOR_CHANGED_LEDGER")
        baseline = exported
        if authority == original_authority:
            held = manager._change(authority, at=_time(a.cl7_clock()), kind=_HOLD,
                state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
                pending_dispatch_proof_sha256=proof_hash)
            with _owner_locks(a, r, ledger=True):
                _require(_owners(a, r) == current and _config(a, r) == config
                         and store.export_bytes() == baseline, "OWNERS_CHANGED")
                manager.store._commit_unlocked(held, expected_revision=authority.record_revision,
                                               expected_sha256=authority.sha256)
            authority = held
        else:
            held = manager._change(original_authority, at=authority.transition_at, kind=_HOLD,
                state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
                pending_dispatch_proof_sha256=proof_hash)
            _require(authority == held, "AUTHORITY_NOT_HELD")
    else:
        _require(plan["proof_sha256"] == proof_hash and plan["closure_sha256"] == _sha(_canonical(closure))
                 and plan["cash_plan_sha256"] == _sha(_canonical(cash_plan))
                 and plan["config_sha256"] == config, "PLAN_BINDING_INVALID")
        baseline = plan["baseline_export_ascii"].encode("ascii")
        _require(_sha(baseline) == old_export_hash, "BASELINE_CHANGED")
        held = RuntimeCashAuthorityRecord.from_canonical_dict(plan["held_authority"])
        wanted_hold = manager._change(original_authority, at=held.transition_at, kind=_HOLD,
            state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
            pending_dispatch_proof_sha256=proof_hash)
        _require(held == wanted_hold, "HELD_AUTHORITY_CHANGED")
        _require(authority == held or (
            authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
            and authority.transition_kind == _DONE and authority.previous_record_sha256 == held.sha256
        ), "AUTHORITY_CHANGED")
    # A durable hold now excludes supported arm/sync/dispatch/recover APIs.
    start_tick, start_wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
    _require(type(start_tick) is int and start_tick >= 0, "CLOCK_INVALID")
    last_tick, last_wall = start_tick, start_wall

    def guard() -> None:
        nonlocal last_tick, last_wall
        tick, wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
        _require(type(tick) is int and last_tick <= tick <= start_tick + MAX_AGE_NS
                 and last_wall <= wall <= start_wall + MAX_AGE_NS, "OBSERVATIONS_STALE")
        last_tick, last_wall = tick, wall
        _require(a.cl7_ledger_store is store and a.cash_authority_manager is manager
                 and a.transport is transport and a.cl7_identity_key == key
                 and a.cl7_identity_key_id == key_id, "ADAPTER_CHANGED")
        state = _owners(a, r)
        _require(all(state[k] == v for k, v in frozen_owners.items())
                 and state["authority"] == authority.to_canonical_dict()
                 and _config(a, r) == config and a.cl7_own_funds_policy is own, "OWNERS_CHANGED")
        _require(closure_store.load() == closure and cash_store.load() == cash_plan
                 and plans.load() == plan, "PLAN_CHANGED")

    guard()
    raw = json.loads(_canonical(a.transport.get_order_state(a.policy.account_id, original.intent_id,
                                                          by_request_id=True)), object_pairs_hook=_pairs)
    guard()
    receipt = decode_exact_order_receipt(raw, original, account_id=a.policy.account_id,
        identity_key=key, identity_key_id=a.cl7_identity_key_id, instrument=instrument,
        observed_at=a.cl7_clock())
    old_fee = int(cash_plan["match"]["commission_nano"])
    _require(receipt.fee_status == "RECEIPT_COMMISSION_OBSERVED"
             and receipt.executed_commission_nano > old_fee, "NO_POSITIVE_ADDITIONAL_FEE")
    normalized = dict(raw)
    normalized["executedCommission"] = {"currency": "RUB", "units": str(old_fee // 10**9), "nano": old_fee % 10**9}
    # This counterfactual is used ONLY to compare the immutable execution part
    # with the previously authenticated receipt, not as new broker evidence.
    old_receipt = decode_exact_order_receipt(normalized, original, account_id=a.policy.account_id,
        identity_key=key, identity_key_id=a.cl7_identity_key_id, instrument=instrument,
        observed_at=a.cl7_clock())
    _require(old_receipt.receipt_identity_sha256 == cash_plan["match"]["receipt_sha256"]
             == closure["cash_binding"]["receipt_sha256"], "EXECUTION_CHANGED")
    delta = receipt.executed_commission_nano - old_fee
    end, start = _time(a.cl7_clock()), pending.operations_complete_through
    _require(type(start) is str and 0 < timestamp_ns(end) - timestamp_ns(start) <= _MAX_WINDOW_NS
             and end > original_authority.operations_complete_through, "WINDOW_OUT_OF_SCOPE")
    batch, rows = _collect_overlap(a, proof.account_scope_sha256, start, end, start_tick + MAX_AGE_NS, guard)
    expected_old = {c["observation_sha256"] for c in cash_plan["match"]["components"]}
    paired = list(zip(rows, batch.decisions))
    old_pairs = [(row, d) for row, d in paired if d.observation.sha256 in expected_old]
    new_pairs = [(row, d) for row, d in paired if d.observation.sha256 not in expected_old]
    _require({d.observation.sha256 for _, d in old_pairs} == expected_old
             and len(old_pairs) == len(expected_old) and len(new_pairs) == 1, "OLD_COMPONENTS_OR_NEW_FEE_AMBIGUOUS")
    old_batch = SimpleNamespace(decisions=tuple(d for _, d in old_pairs),
                               watermark=SimpleNamespace(**closure["watermark"]))
    old_components = _components(a, original, old_receipt, old_batch, [row for row, _ in old_pairs])
    _require(_match(old_receipt, old_components) == cash_plan["match"], "OLD_COMPONENTS_CHANGED")
    base = json.loads(baseline)
    _require(_check_prefix(json.loads(cash_plan["baseline_export_ascii"]), base, old_components)
             == len(old_components), "CLOSED_LEDGER_INVALID")
    fee, decision = new_pairs[0]
    parent = (_id(raw["orderId"]) if receipt.executed_lots == 0 else
              next(row["id"] for row, _ in old_pairs if row["type"] == "OPERATION_TYPE_" + receipt.direction))
    attempted = _time(next(t.at for t in original.transitions if t.status == "IN_FLIGHT"))
    _require(fee["type"] == "OPERATION_TYPE_BROKER_FEE" and fee["state"] == "OPERATION_STATE_EXECUTED"
             and fee.get("parentOperationId") == parent
             and fee.get("instrumentUid") == original.candidate.instrument_id
             and fee["childOperations"] == [] and fee.get("tradesInfo", {"trades": []}) == {"trades": []}
             and all(_uint(fee[n]) == 0 for n in ("quantity", "quantityDone", "quantityRest"))
             and cl3.money_value_to_money(fee["commission"]).minor_units == 0
             and attempted <= _time(fee["date"]) < end, "FEE_BINDING_INVALID")
    payment = cl3.money_value_to_money(fee["payment"])
    _require(payment.minor_units == -delta, "FEE_DELTA_MISMATCH")
    _require(decision.kind is cl3.BrokerDecisionKind.REVIEW_REQUIRED
             and decision.reason is cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS
             and decision.transaction_proposal is None, "CL3_FEE_PROFILE_UNSUPPORTED")
    observation = decision.observation
    transaction = LedgerTransaction(classification=LedgerClassification.COMMISSION,
        effective_at=_time(fee["date"]), source=observation.source,
        postings=(LedgerPosting(1, LedgerAccount.ASSET_BROKER_CASH, payment),
                  LedgerPosting(2, LedgerAccount.EXPENSE_COMMISSION, -payment)),
        reversal_of_sha256=None, corrects_sha256=None)
    component = ((observation, transaction),)
    binding = {"old_receipt_sha256": old_receipt.receipt_identity_sha256,
               "new_receipt_sha256": receipt.receipt_identity_sha256, "delta_nano": str(delta),
               "observations": sorted(d.observation.sha256 for d in batch.decisions),
               "observation_sha256": observation.sha256, "transaction_sha256": transaction.sha256,
               "overlap_start": start}
    if plan is not None:
        _require(plan["match"] == binding, "RECEIPT_OPERATION_OR_ALIAS_CHANGED")
    already = _check_prefix(base, json.loads(store.export_bytes()), component)
    guard()
    before_reads = store.export_bytes()
    raw_cash = manager.read_accounting_cash(a.transport, a.policy.account_id,
        clock=a.cl7_clock, monotonic_ns=a.cl7_monotonic_ns)
    guard()
    now = _time(a.cl7_clock())
    cash = cl4.build_broker_rub_position_cash_proof(raw_cash, raw_account_id=a.policy.account_id,
        account_scope_sha256=proof.account_scope_sha256, environment=cl3.BrokerEnvironment.SANDBOX,
        as_of=now, evaluated_at=now, response_complete=True, identity_key=key,
        identity_key_id=a.cl7_identity_key_id)
    projection = cl4.project_shadow_cash(baseline, account_scope_sha256=proof.account_scope_sha256,
        environment=cl3.BrokerEnvironment.SANDBOX, as_of=now, identity_key=key)
    _require(projection.complete and projection.version == 3
             and projection.expected_cash.minor_units - delta == cash.cash.minor_units,
             "BROKER_CASH_MISMATCH")
    _require(store.export_bytes() == before_reads, "LEDGER_CHANGED_DURING_READ")
    guard()
    had_plan = plan is not None
    if plan is None:
        candidate = {"domain": _DOMAIN, "version": 1, "proof_sha256": proof_hash,
            "closure_sha256": _sha(_canonical(closure)), "cash_plan_sha256": _sha(_canonical(cash_plan)),
            "config_sha256": config, "held_authority": held.to_canonical_dict(),
            "baseline_export_ascii": baseline.decode("ascii"), "match": binding,
            "watermark": json.loads(batch.watermark.canonical_bytes)}
        plans.create(candidate)
        plan = candidate
    guard()
    if authority.state is RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING:
        # CL2 itself takes its SQLite writer transaction; do not deadlock it by
        # retaining a second writer connection across append calls.
        for name in ("append_observation", "append_transaction"):
            guard()
            store.validate()
            state = json.loads(store.export_bytes())
            _check_prefix(base, state, component)
            if name == "append_observation":
                store.append_observation(observation, expected_store_revision=int(state["store_revision"]))
            else:
                store.append_transaction(transaction, observation.sha256,
                    expected_store_revision=int(state["store_revision"]),
                    expected_ledger_revision=int(state["ledger_revision"]))
    guard()
    store.validate()
    final_export = store.export_bytes()
    _require(_check_prefix(base, json.loads(final_export), component) == 1, "FEE_NOT_RECORDED")
    reconciliation = cl4.reconcile_shadow_cash(final_export, cash, evaluated_at=_time(a.cl7_clock()), identity_key=key)
    _require(reconciliation.status is cl4.ReconciliationStatus.MATCHED, "FINAL_RECONCILIATION_FAILED")
    final = json.loads(final_export)
    done = manager._change(held, at=plan["watermark"]["to_exclusive"], kind=_DONE,
        state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED, pending_dispatch_proof_sha256=None,
        ledger_head_sha256=final["ledger_head_sha256"], ledger_revision=int(final["ledger_revision"]),
        operations_complete_through=plan["watermark"]["to_exclusive"])
    replay = authority == done
    _require(authority == held or replay, "FINAL_AUTHORITY_CONFLICT")
    with _owner_locks(a, r, ledger=True):
        guard()
        _require(store.export_bytes() == final_export, "FINAL_LEDGER_CHANGED")
        if not replay:
            r.manager.transaction_coordinator._record("EXACT_LATE_FEE_ACCOUNTED",
                account_id=a.policy.account_id, instrument_id=original.candidate.instrument_id,
                mode="SANDBOX_EXECUTION", status="fee_accounted_disarmed",
                payload={"proof_sha256": proof_hash, "plan_sha256": _sha(_canonical(plan)),
                         "ledger_head_sha256": final["ledger_head_sha256"]})
            guard()
            manager.store._commit_unlocked(done, expected_revision=held.record_revision, expected_sha256=held.sha256)
            authority = done
            guard()
    return LateFeeResult(proof_hash, _sha(_canonical(plan)), transaction.sha256,
        final["ledger_head_sha256"], int(final["ledger_revision"]), done.sha256,
        delta, 1 - already, had_plan and already == 1)


def reconcile_late_fee(adapter: Any, *, recovery: DesktopFillRecovery, proof_sha256: str) -> LateFeeResult:
    """Begin/resume one explicit late additive fee amendment, never new trading.

    Once the authenticated latest DISARMED closure is admitted, commit the hold
    before IO. Missing/invalid evidence retains it. No old result is overwritten.
    A second amendment, intervening owner change or alias migration needs a
    separate protocol, not a heuristic repost or reversal.
    """
    try:
        _require(type(recovery) is DesktopFillRecovery, "OWNER_GRAPH_MISMATCH")
        with InterProcessFileLock(recovery.lock_path, timeout_seconds=0.1):
            with adapter.cash_authority_manager.store.locked():
                return _reconcile_locked(adapter, recovery, proof_sha256)
    except ExactLateFeeError:
        raise
    except Exception:
        raise ExactLateFeeError("EXACT_LATE_FEE_DEPENDENCY_FAILED") from None
