"""One explicit replacement of a posted commission, not general fee finality.

A changed nonzero BROKER_FEE under a *new observed operation identity* replaces
one ordinary COMMISSION from the latest unchanged exact closure (or its one
STEP20 additive fee). The original is retained. CL2's existing atomic correction
bundle posts an exact reversal and the corrected full amount using the genuine
replacement observation for both components. No invented broker refund/source.

Same-ID changed content, pure aliases, zero replacement, repeated correction of
a correction and intervening owner activity remain blocked. A verified mismatch
on replay quarantines the completed correction rather than permitting re-arm.
No Portfolio/Risk/Central, POST, arm, generic CL3 or CL2 source changes.
"""
from __future__ import annotations

import hmac
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import broker_read_adapters as cl3
from . import cash_ledger_opening_reconciliation as cl4
from .cash_ledger_domain import (
    LedgerAccount, LedgerClassification, LedgerCorrectionBundle,
    LedgerPosting, LedgerTransaction,
)
from .cash_ledger_persistence import InboxObservation, InboxStatusEvent, LedgerHead
from .central_order_manager import CentralOrderState
from .desktop_fill_recovery import DesktopFillRecovery
from .exact_cash_settlement import (
    _HEX, _MAX_PLAN_BYTES, _PlanStore, _canonical, _check_prefix, _pairs,
    _safe_path, _seal, _sha,
)
from .exact_late_fee import _LateFeeStore, _MAX_WINDOW_NS, _collect_overlap, _owner_locks
from .exact_order_receipt import _id, _keyed, _time, _uint, decode_exact_order_receipt, validate_exact_receipt_binding
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .exact_settlement_closure import _ClosureStore, _config, _owners
from .locking import InterProcessFileLock
from .runtime_cash_authority import RuntimeCashAuthorityRecord, RuntimeCashAuthorityState
from .state_persistence import atomic_write_json

_DOMAIN = "CL7_EXACT_FEE_REPLACEMENT_V1"
_HOLD = "FEE_REPLACEMENT_HELD"
_DONE = "FEE_REPLACEMENT_CLOSED_DISARMED"
_REVIEW = "FEE_REPLACEMENT_REVIEW_HELD"


class ExactFeeReplacementError(RuntimeError):
    """Finite error; a durable quarantine or atomic bundle may remain."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ExactFeeReplacementError("EXACT_FEE_REPLACEMENT_" + reason)


class _ReplacementStore:
    def __init__(self, root: Path, proof: str, key: bytes) -> None:
        _require(type(proof) is str and _HEX.fullmatch(proof) is not None, "PROOF_INVALID")
        # One correction per closure, not one path per arbitrarily supplied ID.
        self.path = root / "exact_fee_replacement" / (proof + ".json")
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
            "domain", "version", "proof_sha256", "original_transaction_sha256",
            "closure_sha256", "cash_plan_sha256", "late_plan_sha256", "config_sha256",
            "baseline_export_ascii", "held_authority", "match", "watermark",
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
class FeeReplacementResult:
    proof_sha256: str
    plan_sha256: str
    bundle_sha256: str
    original_transaction_sha256: str
    ledger_head_sha256: str
    ledger_revision: int
    authority_record_sha256: str
    old_fee_nano: int
    new_fee_nano: int
    cash_delta_nano: int
    appended_transactions: int
    replay: bool

    def to_canonical_dict(self) -> dict[str, Any]:
        # Amounts, raw identities and source content stay private attributes/plans.
        return {"domain": _DOMAIN + "_RESULT", "version": 1, "status": _DONE,
                "proof_sha256": self.proof_sha256, "plan_sha256": self.plan_sha256,
                "bundle_sha256": self.bundle_sha256,
                "original_transaction_sha256": self.original_transaction_sha256,
                "ledger_head_sha256": self.ledger_head_sha256,
                "ledger_revision": str(self.ledger_revision),
                "authority_record_sha256": self.authority_record_sha256,
                "appended_transactions": self.appended_transactions, "replay": self.replay,
                "risk_execution_written": False, "automatic_rearm_allowed": False,
                "future_fee_finality_claimed": False}


def _component(export: dict[str, Any], tx_hash: str) -> tuple[InboxObservation, LedgerTransaction]:
    rows = [v for v in export["transactions"] if v["sha256"] == tx_hash]
    links = [v for v in export["provenance_links"] if v["transaction_sha256"] == tx_hash]
    _require(len(rows) == len(links) == 1, "ORIGINAL_COMPONENT_MISSING")
    obs = [v for v in export["observations"] if v["sha256"] == links[0]["observation_sha256"]]
    _require(len(obs) == 1 and obs[0]["current_status"] == "LEDGER_LINKED", "ORIGINAL_OBSERVATION_MISSING")
    observation = InboxObservation.from_canonical_bytes(obs[0]["canonical_json_ascii"], (cl3.TBANK_OPERATION_CODEC,))
    transaction = LedgerTransaction.from_canonical_dict(json.loads(rows[0]["canonical_json_ascii"]))
    _require(transaction.sha256 == tx_hash and transaction.source == observation.source, "ORIGINAL_SOURCE_INVALID")
    return observation, transaction


def _prefixes(baseline: bytes, observation: InboxObservation, bundle: LedgerCorrectionBundle) -> tuple[bytes, bytes, bytes]:
    """Predict all exact exports; no permissive set/amount-only prefix matching.

    CL2 itself validates and persists. This independent predicate is deliberately
    limited to a new observation and one atomic bundle, whose ledger revision is
    +1 although it contains two economic transactions.
    """
    base = json.loads(baseline)
    _require(observation.logical_source_sha256 not in {v["logical_source_sha256"] for v in base["observations"]},
             "SAME_ID_OR_SEEN_ALIAS_UNSUPPORTED")
    _require(bundle.original.sha256 in {v["sha256"] for v in base["transactions"]}, "ORIGINAL_NOT_IN_LEDGER")
    _require(all(json.loads(v["canonical_json_ascii"])["original_sha256"] != bundle.original.sha256
                 for v in base["correction_bundles"]), "ALREADY_CORRECTED")
    observed = deepcopy(base)
    observed["observations"].append({"canonical_json_ascii": observation.canonical_bytes.decode("ascii"),
        "current_status": "OBSERVED", "logical_source_sha256": observation.logical_source_sha256,
        "sha256": observation.sha256, "source_sha256": observation.source.sha256})
    observed["observations"].sort(key=lambda v: (v["logical_source_sha256"], v["sha256"]))
    codec = cl3.TBANK_OPERATION_CODEC
    if codec.sha256 not in {v["sha256"] for v in observed["codec_registry"]}:
        observed["codec_registry"].append({"canonical_json_ascii": codec.canonical_bytes.decode("ascii"), "sha256": codec.sha256})
        observed["codec_registry"].sort(key=lambda v: v["sha256"])
    observed["store_revision"] = str(int(base["store_revision"]) + 1)
    final = deepcopy(observed)
    for v in final["observations"]:
        if v["sha256"] == observation.sha256:
            v["current_status"] = "LEDGER_LINKED"
    for tx in (bundle.reversal, bundle.correction):
        final["transactions"].append({"canonical_json_ascii": tx.canonical_bytes.decode("ascii"),
            "economic_sha256": tx.economic_sha256, "sha256": tx.sha256, "source_sha256": tx.source.sha256})
        final["provenance_links"].append({"observation_sha256": observation.sha256, "transaction_sha256": tx.sha256})
    final["transactions"].sort(key=lambda v: v["sha256"])
    final["provenance_links"].sort(key=lambda v: (v["transaction_sha256"], v["observation_sha256"]))
    event = InboxStatusEvent(observation.sha256, 1, "OBSERVED", "LEDGER_LINKED",
                            "LEDGER_CORRECTION_ACCEPTED", related_bundle_sha256=bundle.sha256)
    final["inbox_status_events"].append({"canonical_json_ascii": event.canonical_bytes.decode("ascii"), "sha256": event.sha256})
    final["inbox_status_events"].sort(key=lambda v: (json.loads(v["canonical_json_ascii"])["observation_sha256"],
                                                   int(json.loads(v["canonical_json_ascii"])["event_no"])))
    final["correction_bundles"].append({"canonical_json_ascii": bundle.canonical_bytes.decode("ascii"), "sha256": bundle.sha256})
    final["correction_bundles"].sort(key=lambda v: (json.loads(v["canonical_json_ascii"])["original_sha256"], v["sha256"]))
    head = LedgerHead(int(base["ledger_revision"]) + 1, base["ledger_head_sha256"], "CORRECTION_BUNDLE", bundle.sha256)
    final["ledger_revision"] = str(head.ledger_revision)
    final["store_revision"] = str(int(base["store_revision"]) + 2)
    final["ledger_head_sha256"] = head.sha256
    final["ledger_head_json_ascii"] = head.canonical_bytes.decode("ascii")
    final["ledger_transitions"].append({"head_json_ascii": head.canonical_bytes.decode("ascii"), "head_sha256": head.sha256})
    return baseline, _canonical(observed), _canonical(final)


def _baseline_authority(a: Any, closure: Any, cash: Any, late: Any, baseline: bytes,
                        target: str) -> tuple[Any, int, set[str]]:
    """Validate the immutable predecessor chain, including optional STEP20."""
    data = json.loads(baseline)
    closed = baseline if late is None else late["baseline_export_ascii"].encode("ascii")
    _require(_sha(closed) == closure["cash_binding"]["ledger_export_sha256"], "CLOSED_LEDGER_CHANGED")
    initial_components = tuple(_component(json.loads(closed), v["transaction_sha256"])
                               for v in cash["match"]["components"])
    _require(_check_prefix(json.loads(cash["baseline_export_ascii"]), json.loads(closed), initial_components)
             == len(initial_components), "CLOSED_PREFIX_INVALID")
    initial = RuntimeCashAuthorityRecord.from_canonical_dict(closure["after"]["authority"])
    prior_fee = int(cash["match"]["commission_nano"])
    observations = {v["observation_sha256"] for v in cash["match"]["components"]}
    if late is None:
        _require(target in {t.sha256 for _, t in initial_components
                            if t.classification is LedgerClassification.COMMISSION}, "TARGET_NOT_CLOSURE_FEE")
        return initial, prior_fee, observations
    _require(late["proof_sha256"] == closure["proof_sha256"]
             and late["closure_sha256"] == _sha(_canonical(closure))
             and late["cash_plan_sha256"] == _sha(_canonical(cash))
             and late["config_sha256"] == closure["config_sha256"], "LATE_PREDECESSOR_INVALID")
    _require(target == late["match"]["transaction_sha256"], "TARGET_NOT_LATEST_ADDITIVE_FEE")
    obs, tx = _component(data, target)
    _require(obs.sha256 == late["match"]["observation_sha256"]
             and _check_prefix(json.loads(closed), data, ((obs, tx),)) == 1, "LATE_PREFIX_INVALID")
    held = RuntimeCashAuthorityRecord.from_canonical_dict(late["held_authority"])
    _require(held == a.cash_authority_manager._change(initial, at=held.transition_at,
        kind="LATE_FEE_ADJUSTMENT_HELD", state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
        pending_dispatch_proof_sha256=closure["proof_sha256"]), "LATE_HOLD_INVALID")
    done = a.cash_authority_manager._change(held, at=late["watermark"]["to_exclusive"],
        kind="LATE_FEE_ADJUSTMENT_CLOSED_DISARMED", state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        pending_dispatch_proof_sha256=None, ledger_head_sha256=data["ledger_head_sha256"],
        ledger_revision=int(data["ledger_revision"]), operations_complete_through=late["watermark"]["to_exclusive"])
    return done, prior_fee + int(late["match"]["delta_nano"]), observations | {obs.sha256}


def _validate_unchanged_rows(a: Any, original: Any, receipt: Any,
                             pairs: list[Any], raw: dict[str, Any]) -> str:
    """CL3 hashes do not cover raw trades/parent/instrument; revalidate them.

    Exact observation hashes alone must never attest fields outside that codec.
    The immutable commission summary is already covered by old observation hashes.
    """
    candidate = original.candidate
    if receipt.executed_lots:
        trades = [row for row, _ in pairs if row["type"] == "OPERATION_TYPE_" + receipt.direction]
        _require(len(trades) == 1, "TRADE_BINDING_MISSING")
        trade = trades[0]
        _require(trade.get("instrumentUid") == candidate.instrument_id
                 and trade.get("instrumentType", "").lower() in {"share", "etf"}
                 and trade.get("instrumentType", "").lower()
                     == a.cl7_own_funds_policy.instruments[candidate.instrument_id].asset_class.lower()
                 and trade["state"] == "OPERATION_STATE_EXECUTED"
                 and trade.get("parentOperationId", "") == "" and trade["childOperations"] == [],
                 "OLD_TRADE_BINDING_CHANGED")
        _require(_uint(trade["quantity"]) == receipt.requested_lots * receipt.lot_size
                 and _uint(trade["quantityDone"]) == receipt.executed_lots * receipt.lot_size
                 and _uint(trade["quantityRest"]) == (receipt.requested_lots - receipt.executed_lots) * receipt.lot_size,
                 "OLD_TRADE_QUANTITY_CHANGED")
        info = trade.get("tradesInfo")
        _require(type(info) is dict and set(info) == {"trades"} and type(info["trades"]) is list
                 and len(info["trades"]) == len(receipt.stages), "OLD_TRADE_STAGES_CHANGED")
        observed = {}
        for row in info["trades"]:
            _require(type(row) is dict and {"num", "date", "quantity", "price"} <= set(row)
                     and set(row) <= {"num", "date", "quantity", "price", "yield", "yieldRelative"},
                     "OLD_TRADE_STAGES_CHANGED")
            scope = _keyed(a.cl7_identity_key, {"domain": "CL7_EXACT_ORDER_RECEIPT_V1_TRADE",
                "account_scope_sha256": receipt.account_scope_sha256, "trade_id": _id(row["num"])})
            _require(scope not in observed, "OLD_TRADE_DUPLICATE")
            observed[scope] = (_uint(row["quantity"]), cl3.money_value_to_money(row["price"]).minor_units,
                               _time(row["date"]))
        expected = {v.trade_scope_sha256: (v.lots * receipt.lot_size, v.price_nano, v.execution_at)
                    for v in receipt.stages}
        _require(observed == expected, "OLD_TRADE_STAGES_CHANGED")
        parent = _id(trade["id"])
    else:
        _require(all(row["type"] == "OPERATION_TYPE_BROKER_FEE" for row, _ in pairs), "ZERO_HAS_TRADE")
        parent = _id(raw["orderId"])
    for row, _ in pairs:
        if row["type"] != "OPERATION_TYPE_BROKER_FEE":
            continue
        _require(row["state"] == "OPERATION_STATE_EXECUTED" and row.get("parentOperationId") == parent
                 and row.get("instrumentUid") == candidate.instrument_id and row["childOperations"] == []
                 and row.get("tradesInfo", {"trades": []}) == {"trades": []}
                 and all(_uint(row[n]) == 0 for n in ("quantity", "quantityDone", "quantityRest")),
                 "OLD_FEE_BINDING_CHANGED")
    return parent


def _reconcile_locked(a: Any, r: DesktopFillRecovery, proof_hash: str, target: str) -> FeeReplacementResult:
    _require(type(target) is str and _HEX.fullmatch(target) is not None, "TARGET_INVALID")
    manager, store, key, key_id = a.cash_authority_manager, a.cl7_ledger_store, a.cl7_identity_key, a.cl7_identity_key_id
    transport, own = a.transport, a.cl7_own_funds_policy
    config = _config(a, r)
    root = a.manager.store.path.parent
    plans, cs, ps, ls = (_ReplacementStore(root, proof_hash, key), _ClosureStore(root, proof_hash, key),
                         _PlanStore(root, proof_hash, key), _LateFeeStore(root, proof_hash, key))
    plan, closure, cash_plan, late_plan = plans.load(), cs.load(), ps.load(), ls.load()
    _require(closure is not None and cash_plan is not None
             and closure["proof_sha256"] == proof_hash and closure["config_sha256"] == config
             and closure["cash_binding"]["plan_sha256"] == _sha(_canonical(cash_plan)), "CLOSURE_REQUIRED")
    current = _owners(a, r)
    frozen = {k: closure["after"][k] for k in ("portfolio", "risk", "central")}
    _require(all(current[k] == v for k, v in frozen.items()), "NOT_LATEST_UNCHANGED_CLOSURE")
    state = CentralOrderState.from_dict(current["central"])
    _require(state.blocking_intent is None and not state.queued, "CENTRAL_NOT_QUIESCENT")
    pending = RuntimeCashAuthorityRecord.from_canonical_dict(closure["before"]["authority"])
    original = manager._recovery_intent_from_state(pending, CentralOrderState.from_dict(closure["before"]["central"]), identity_key=key)
    instrument = own.instruments.get(original.candidate.instrument_id)
    proof = validate_exact_receipt_binding(original, account_id=a.policy.account_id,
        identity_key=key, identity_key_id=key_id, instrument=instrument)
    _require(proof.sha256 == proof_hash, "PROOF_BINDING_INVALID")
    store.validate()
    baseline = store.export_bytes() if plan is None else plan["baseline_export_ascii"].encode("ascii")
    prior, prior_total_fee, expected_obs = _baseline_authority(a, closure, cash_plan, late_plan, baseline, target)
    old_obs, old_tx = _component(json.loads(baseline), target)
    _require(old_tx.classification is LedgerClassification.COMMISSION
             and old_tx.corrects_sha256 is None and old_tx.reversal_of_sha256 is None, "TARGET_NOT_ORDINARY_COMMISSION")
    old_amount = -next(v.money.minor_units for v in old_tx.postings if v.account is LedgerAccount.ASSET_BROKER_CASH)
    _require(0 < old_amount <= prior_total_fee, "ORIGINAL_FEE_INVALID")
    authority = RuntimeCashAuthorityRecord.from_canonical_dict(current["authority"])
    late_hash = None if late_plan is None else _sha(_canonical(late_plan))
    if plan is not None:
        _require(plan["proof_sha256"] == proof_hash and plan["original_transaction_sha256"] == target
                 and plan["closure_sha256"] == _sha(_canonical(closure))
                 and plan["cash_plan_sha256"] == _sha(_canonical(cash_plan))
                 and plan["late_plan_sha256"] == late_hash and plan["config_sha256"] == config, "PLAN_BINDING_INVALID")
        held = RuntimeCashAuthorityRecord.from_canonical_dict(plan["held_authority"])
    else:
        held = manager._change(prior, at=_time(a.cl7_clock()) if authority == prior else authority.transition_at,
            kind=_HOLD, state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
            pending_dispatch_proof_sha256=proof_hash)
    wanted_hold = manager._change(prior, at=held.transition_at, kind=_HOLD,
        state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING, pending_dispatch_proof_sha256=proof_hash)
    _require(held == wanted_hold, "HELD_AUTHORITY_CHANGED")
    if plan is None and authority == prior:
        with _owner_locks(a, r, ledger=True):
            _require(_owners(a, r) == current and _config(a, r) == config
                     and store.export_bytes() == baseline, "OWNERS_CHANGED")
            manager.store._commit_unlocked(held, expected_revision=prior.record_revision, expected_sha256=prior.sha256)
        authority = held
    # Authenticate the full completed state before replay, not just its status.
    replay_done = None
    if plan is not None:
        replay_done = manager._change(held, at=plan["watermark"]["to_exclusive"], kind=_DONE,
            state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED, pending_dispatch_proof_sha256=None,
            ledger_head_sha256=plan["match"]["final_head_sha256"],
            ledger_revision=int(plan["match"]["final_ledger_revision"]),
            operations_complete_through=plan["watermark"]["to_exclusive"])
        _require(authority == held or authority == replay_done, "AUTHORITY_CHANGED_OR_REVIEW_REQUIRED")
    else:
        _require(authority == held, "AUTHORITY_NOT_HELD")
    start_tick, start_wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
    _require(type(start_tick) is int and start_tick >= 0, "CLOCK_INVALID")
    last_tick, last_wall = start_tick, start_wall

    def guard() -> None:
        nonlocal last_tick, last_wall
        tick, wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
        _require(type(tick) is int and last_tick <= tick <= start_tick + MAX_AGE_NS
                 and last_wall <= wall <= start_wall + MAX_AGE_NS, "OBSERVATIONS_STALE")
        last_tick, last_wall = tick, wall
        _require(a.cash_authority_manager is manager and a.cl7_ledger_store is store
                 and a.transport is transport and a.cl7_identity_key == key and a.cl7_identity_key_id == key_id
                 and a.cl7_own_funds_policy is own, "ADAPTER_CHANGED")
        state = _owners(a, r)
        _require(all(state[k] == v for k, v in frozen.items())
                 and state["authority"] == authority.to_canonical_dict() and _config(a, r) == config, "OWNERS_CHANGED")
        _require(cs.load() == closure and ps.load() == cash_plan and ls.load() == late_plan
                 and plans.load() == plan, "PLAN_CHANGED")

    def work() -> FeeReplacementResult:
        nonlocal plan, authority
        guard()
        raw = json.loads(_canonical(a.transport.get_order_state(a.policy.account_id, original.intent_id,
                                                              by_request_id=True)), object_pairs_hook=_pairs)
        guard()
        receipt = decode_exact_order_receipt(raw, original, account_id=a.policy.account_id,
            identity_key=key, identity_key_id=key_id, instrument=instrument, observed_at=a.cl7_clock())
        _require(receipt.fee_status == "RECEIPT_COMMISSION_OBSERVED", "COMMISSION_UNKNOWN")
        normalized = dict(raw)
        old_total = int(cash_plan["match"]["commission_nano"])
        normalized["executedCommission"] = {"currency": "RUB", "units": str(old_total // 10**9), "nano": old_total % 10**9}
        unchanged = decode_exact_order_receipt(normalized, original, account_id=a.policy.account_id,
            identity_key=key, identity_key_id=key_id, instrument=instrument, observed_at=a.cl7_clock())
        _require(unchanged.receipt_identity_sha256 == cash_plan["match"]["receipt_sha256"]
                 == closure["cash_binding"]["receipt_sha256"], "EXECUTION_CHANGED")
        delta = receipt.executed_commission_nano - prior_total_fee
        new_amount = old_amount + delta
        _require(delta != 0 and new_amount > 0, "NO_CHANGE_OR_ZERO_REPLACEMENT_UNSUPPORTED")
        start, end = pending.operations_complete_through, _time(a.cl7_clock())
        _require(type(start) is str and 0 < timestamp_ns(end) - timestamp_ns(start) <= _MAX_WINDOW_NS
                 and end > prior.operations_complete_through, "WINDOW_OUT_OF_SCOPE")
        batch, rows = _collect_overlap(a, proof.account_scope_sha256, start, end, start_tick + MAX_AGE_NS, guard)
        required = expected_obs - {old_obs.sha256}
        pairs = list(zip(rows, batch.decisions))
        unchanged_pairs = [(row, d) for row, d in pairs if d.observation.sha256 in required]
        replacements = [(row, d) for row, d in pairs if d.observation.sha256 not in required]
        _require({d.observation.sha256 for _, d in unchanged_pairs} == required
                 and len(unchanged_pairs) == len(required) and len(replacements) == 1, "AMBIGUOUS_REPLACEMENT_WINDOW")
        fee, decision = replacements[0]
        parent = _validate_unchanged_rows(a, original, receipt, unchanged_pairs, raw)
        _require(fee["type"] == "OPERATION_TYPE_BROKER_FEE" and fee["state"] == "OPERATION_STATE_EXECUTED"
                 and fee.get("parentOperationId") == parent and fee.get("instrumentUid") == original.candidate.instrument_id
                 and fee["childOperations"] == [] and fee.get("tradesInfo", {"trades": []}) == {"trades": []}
                 and all(_uint(fee[n]) == 0 for n in ("quantity", "quantityDone", "quantityRest"))
                 and cl3.money_value_to_money(fee["commission"]).minor_units == 0
                 and _time(fee["date"]) == old_tx.effective_at, "FEE_BINDING_INVALID")
        payment = cl3.money_value_to_money(fee["payment"])
        _require(payment.minor_units == -new_amount, "CUMULATIVE_FEE_MISMATCH")
        _require(decision.kind is cl3.BrokerDecisionKind.REVIEW_REQUIRED
                 and decision.reason is cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS
                 and decision.transaction_proposal is None, "CL3_PROFILE_UNSUPPORTED")
        observation = decision.observation
        left = json.loads(old_obs.content_json_ascii)
        right = json.loads(observation.content_json_ascii)
        left.pop("payment_minor_units"); right.pop("payment_minor_units")
        _require(left == right and old_obs.logical_source_sha256 != observation.logical_source_sha256,
                 "SAME_ID_OR_CHANGED_FEE_PROFILE")
        reversal = LedgerTransaction.reversing(old_tx, effective_at=_time(fee["date"]), source=observation.source)
        correction = LedgerTransaction(classification=LedgerClassification.COMMISSION,
            effective_at=_time(fee["date"]), source=observation.source,
            postings=(LedgerPosting(1, LedgerAccount.ASSET_BROKER_CASH, payment),
                      LedgerPosting(2, LedgerAccount.EXPENSE_COMMISSION, -payment)), corrects_sha256=old_tx.sha256)
        bundle = LedgerCorrectionBundle(old_tx, reversal, correction)
        prefixes = _prefixes(baseline, observation, bundle)
        store.validate()
        live_export = store.export_bytes()
        _require(live_export in prefixes, "LEDGER_PREFIX_CONFLICT")
        before_n = prefixes.index(live_export)
        guard()
        cash_raw = manager.read_accounting_cash(a.transport, a.policy.account_id,
            clock=a.cl7_clock, monotonic_ns=a.cl7_monotonic_ns)
        guard()
        now = _time(a.cl7_clock())
        cash = cl4.build_broker_rub_position_cash_proof(cash_raw, raw_account_id=a.policy.account_id,
            account_scope_sha256=proof.account_scope_sha256, environment=cl3.BrokerEnvironment.SANDBOX,
            as_of=now, evaluated_at=now, response_complete=True, identity_key=key, identity_key_id=key_id)
        projection = cl4.project_shadow_cash(baseline, account_scope_sha256=proof.account_scope_sha256,
            environment=cl3.BrokerEnvironment.SANDBOX, as_of=now, identity_key=key)
        _require(projection.complete and projection.version == 3
                 and projection.expected_cash.minor_units - delta == cash.cash.minor_units, "BROKER_CASH_MISMATCH")
        _require(store.export_bytes() == live_export, "LEDGER_CHANGED_DURING_READ")
        final = json.loads(prefixes[2])
        binding = {"receipt_sha256": receipt.receipt_identity_sha256, "old_total_fee_nano": str(prior_total_fee),
            "new_total_fee_nano": str(receipt.executed_commission_nano), "old_fee_nano": str(old_amount),
            "new_fee_nano": str(new_amount), "cash_delta_nano": str(-delta),
            "observations": sorted(d.observation.sha256 for d in batch.decisions),
            "observation_sha256": observation.sha256, "bundle_sha256": bundle.sha256,
            "prefix_sha256s": [_sha(v) for v in prefixes], "overlap_start": start,
            "final_head_sha256": final["ledger_head_sha256"], "final_ledger_revision": final["ledger_revision"]}
        if plan is not None:
            _require(plan["match"] == binding, "REPLAY_EVIDENCE_CHANGED")
        else:
            candidate = {"domain": _DOMAIN, "version": 1, "proof_sha256": proof_hash,
                "original_transaction_sha256": target, "closure_sha256": _sha(_canonical(closure)),
                "cash_plan_sha256": _sha(_canonical(cash_plan)), "late_plan_sha256": late_hash,
                "config_sha256": config, "baseline_export_ascii": baseline.decode("ascii"),
                "held_authority": held.to_canonical_dict(), "match": binding,
                "watermark": json.loads(batch.watermark.canonical_bytes)}
            guard()
            plans.create(candidate)
            plan = candidate
        guard()
        if authority == held:
            _require(store.export_bytes() in prefixes, "LEDGER_PREFIX_CONFLICT")
            if store.export_bytes() == prefixes[0]:
                info = json.loads(prefixes[0])
                store.append_observation(observation, expected_store_revision=int(info["store_revision"]))
            guard()
            _require(store.export_bytes() in prefixes[1:], "LEDGER_PREFIX_CONFLICT")
            if store.export_bytes() == prefixes[1]:
                info = json.loads(prefixes[1])
                store.append_correction_bundle(bundle, observation.sha256, observation.sha256,
                    expected_store_revision=int(info["store_revision"]), expected_ledger_revision=int(info["ledger_revision"]))
        guard()
        store.validate()
        _require(store.export_bytes() == prefixes[2], "FINAL_LEDGER_CONFLICT")
        reconciliation = cl4.reconcile_shadow_cash(prefixes[2], cash, evaluated_at=_time(a.cl7_clock()), identity_key=key)
        _require(reconciliation.status is cl4.ReconciliationStatus.MATCHED, "FINAL_RECONCILIATION_FAILED")
        done = manager._change(held, at=plan["watermark"]["to_exclusive"], kind=_DONE,
            state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED, pending_dispatch_proof_sha256=None,
            ledger_head_sha256=final["ledger_head_sha256"], ledger_revision=int(final["ledger_revision"]),
            operations_complete_through=plan["watermark"]["to_exclusive"])
        _require(authority == held or authority == done, "FINAL_AUTHORITY_CONFLICT")
        with _owner_locks(a, r, ledger=True):
            guard()
            _require(store.export_bytes() == prefixes[2], "FINAL_LEDGER_CHANGED")
            if authority == held:
                r.manager.transaction_coordinator._record("EXACT_FEE_REPLACEMENT_ACCOUNTED",
                    account_id=a.policy.account_id, instrument_id=original.candidate.instrument_id,
                    mode="SANDBOX_EXECUTION", status="fee_replaced_disarmed",
                    payload={"proof_sha256": proof_hash, "bundle_sha256": bundle.sha256,
                             "plan_sha256": _sha(_canonical(plan)), "ledger_head_sha256": final["ledger_head_sha256"]})
                guard()
                manager.store._commit_unlocked(done, expected_revision=held.record_revision, expected_sha256=held.sha256)
                authority = done
        return FeeReplacementResult(proof_hash, _sha(_canonical(plan)), bundle.sha256, target,
            final["ledger_head_sha256"], int(final["ledger_revision"]), done.sha256,
            old_amount, new_amount, -delta, 0 if before_n == 2 else 2, before_n == 2)

    try:
        return work()
    except Exception:
        # Only a locally authenticated, completed correction can enter review.
        # Never redirect a stale request to another authority/owner graph.
        if replay_done is not None and authority == replay_done:
            with _owner_locks(a, r, ledger=True):
                state = _owners(a, r)
                _require(all(state[k] == v for k, v in frozen.items()) and _config(a, r) == config
                         and state["authority"] == replay_done.to_canonical_dict()
                         and cs.load() == closure and ps.load() == cash_plan and ls.load() == late_plan
                         and plans.load() == plan, "REPLAY_QUARANTINE_CONFLICT")
                _require(_sha(store.export_bytes()) == plan["match"]["prefix_sha256s"][2], "REPLAY_LEDGER_CONFLICT")
                reviewed = manager._change(replay_done, at=_time(a.cl7_clock()), kind=_REVIEW,
                    state=RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
                    pending_dispatch_proof_sha256=proof_hash)
                manager.store._commit_unlocked(reviewed, expected_revision=replay_done.record_revision,
                                               expected_sha256=replay_done.sha256)
        raise


def reconcile_fee_replacement(adapter: Any, *, recovery: DesktopFillRecovery,
                              proof_sha256: str, original_transaction_sha256: str) -> FeeReplacementResult:
    """Explicit one-bundle correction; errors can retain a durable fee quarantine.

    The source amount is not user supplied. It comes from fresh bound provider
    evidence. A verified old operation is never rewritten and a new source ID
    alone never authorizes new cash. Review quarantine requires separate handling.
    """
    try:
        _require(type(recovery) is DesktopFillRecovery, "OWNER_GRAPH_MISMATCH")
        with InterProcessFileLock(recovery.lock_path, timeout_seconds=0.1):
            with adapter.cash_authority_manager.store.locked():
                return _reconcile_locked(adapter, recovery, proof_sha256, original_transaction_sha256)
    except ExactFeeReplacementError:
        raise
    except Exception:
        raise ExactFeeReplacementError("EXACT_FEE_REPLACEMENT_DEPENDENCY_FAILED") from None
