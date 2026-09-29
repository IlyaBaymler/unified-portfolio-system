"""Bounded economic consumer for a same-ID fee observation version.

Validates captured exact-closure evidence and the real CL3/CL4 codecs. This
module does not write runtime owners, conduct a cutover, or grant trading
permission. A detached correction journal consumes its reproducible report.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from . import broker_read_adapters as cl3
from . import cash_ledger_opening_reconciliation as cl4
from . import cash_ledger_persistence as cl2
from . import cash_observation_versions as versions
from .cash_ledger_domain import (
    LedgerAccount, LedgerClassification, LedgerCorrectionBundle, LedgerPosting,
    LedgerTransaction,
)
from .central_order_manager import CentralOrderState
from .exact_cash_settlement import _PlanStore, _check_prefix
from .exact_fee_replacement import _component, _validate_unchanged_rows
from .exact_late_fee import _MAX_WINDOW_NS, _collect_overlap, _owner_locks
from .exact_order_receipt import _time, _uint, decode_exact_order_receipt, validate_exact_receipt_binding
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .exact_settlement_closure import _ClosureStore, _config, _owners
from .runtime_cash_authority import RuntimeCashAuthorityManager, RuntimeCashAuthorityRecord, RuntimeCashAuthorityState

DOMAIN = "CL2_SAME_ID_FEE_CAPTURE_V1"
MAX_EVIDENCE_BYTES = 12 * 1024 * 1024
_FIELDS = frozenset({
    "domain", "version", "raw_account_id", "instrument", "captured_at",
    "closure_json_ascii", "cash_plan_json_ascii", "order_state",
    "operation_requests", "operation_responses", "rub_positions",
})
_CLOSURE_FIELDS = frozenset({
    "domain", "version", "proof_sha256", "cash_binding", "watermark", "before", "after",
    "portfolio_candidate", "execution", "config_sha256", "transaction_id",
})
_CASH_FIELDS = frozenset({"domain", "version", "match", "baseline_export_ascii", "first_watermark_sha256"})


class VersionFeeEvidenceError(RuntimeError):
    """Finite diagnostic only; all captures and plans are private, not redacted."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise VersionFeeEvidenceError("VERSION_FEE_" + reason)


def _canonical(value: Any) -> bytes:
    # Captured signed owner plans include the existing Risk float fields.
    # Money is decoded separately by strict CL1/CL3 integer codecs.
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
        _require(len(raw) <= MAX_EVIDENCE_BYTES, "INPUT_BOUNDS_INVALID")
        return raw
    except (ValueError, TypeError, RecursionError):
        raise VersionFeeEvidenceError("VERSION_FEE_CANONICAL_INVALID") from None


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse(value: bytes | str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for name, item in pairs:
            _require(name not in result, "DUPLICATE_FIELD")
            result[name] = item
        return result
    try:
        raw = value.encode("ascii") if type(value) is str else value
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_EVIDENCE_BYTES, "INPUT_BOUNDS_INVALID")
        data = json.loads(raw, object_pairs_hook=unique)
        _require(_canonical(data) == raw, "NONCANONICAL_INPUT")
        return data
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise VersionFeeEvidenceError("VERSION_FEE_CANONICAL_INVALID") from None


def _sealed(value: Any, key: bytes) -> bytes:
    return _canonical({"payload": value, "hmac_sha256": hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()})


def _unseal(value: str, key: bytes, fields: frozenset[str], domain: str) -> dict[str, Any]:
    data = _parse(value)
    _require(type(data) is dict and set(data) == {"payload", "hmac_sha256"}, "PLAN_INVALID")
    body = data["payload"]
    _require(type(body) is dict and frozenset(body) == fields and body["domain"] == domain
             and type(body["version"]) is int and body["version"] == 1
             and type(data["hmac_sha256"]) is str
             and hmac.compare_digest(data["hmac_sha256"], hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()),
             "PLAN_AUTHENTICATION_FAILED")
    return body


def _read_batch(data: dict[str, Any], key: bytes, key_id: str, start: str,
                scope: str) -> tuple[Any, list[Any]]:
    """Re-run actual CL3 over a bounded immutable capture, including cursor requests."""
    requests, responses = data["operation_requests"], data["operation_responses"]
    _require(type(requests) is list and type(responses) is list
             and 0 < len(requests) == len(responses) <= 4, "PAGINATION_INVALID")
    position = 0
    rows: list[Any] = []

    def replay(payload: Any, timeout_ns: int) -> Any:
        nonlocal position
        _require(position < len(responses) and payload == requests[position], "REQUEST_MISMATCH")
        result = deepcopy(responses[position])
        position += 1
        if type(result) is dict and type(result.get("items")) is list:
            rows.extend(result["items"])
        return result

    batch = cl3.collect_tbank_operations(cl3.BrokerReadRequest(
        environment=cl3.BrokerEnvironment.SANDBOX, raw_account_id=data["raw_account_id"],
        identity_key=key, identity_key_id=key_id, from_inclusive=start,
        to_exclusive=data["captured_at"], limit=100, max_pages=4, max_items=8,
        absolute_deadline_ns=MAX_AGE_NS, retry_policy=cl3.RetryPolicy(1, MAX_AGE_NS, ()),
        transport=replay, monotonic_ns=lambda: 0, wait_ns=lambda _: None,
    ))
    _require(position == len(responses) and batch.watermark.account_scope_sha256 == scope
             and len(rows) == len(batch.decisions) == batch.watermark.item_count, "BATCH_INCOMPLETE")
    return batch, rows


@dataclass(frozen=True, slots=True)
class FeeVersionEvaluation:
    original: LedgerTransaction
    observation: cl2.InboxObservation
    bundle: LedgerCorrectionBundle
    report: bytes
    revision_evidence: versions.RevisionEvidence
    cash_before_nano: int
    cash_after_nano: int
    cash_delta_nano: int


def evaluate_captured_fee_revision(
    frozen_v1_export: bytes, capture: bytes, *, original_transaction_sha256: str,
    codec_registry: object, identity_key: bytes, identity_key_id: str,
    account_scope_sha256: str,
) -> FeeVersionEvaluation:
    """Reproduce the economic join, never trust the capture or an opaque binding hash.

    Scope: one ordinary initial closure fee, positive full FILL, one first
    same-ID change to a different positive amount. No late-fee predecessor,
    arbitrary correction-chain, same-amount alias, partial, or zero terminal.
    Captures attest a local as-of observation, not future fee finality.
    """
    try:
        return _evaluate(frozen_v1_export, capture, original_transaction_sha256,
                         codec_registry, identity_key, identity_key_id, account_scope_sha256)
    except VersionFeeEvidenceError:
        raise
    except Exception:
        # Do not leak captured account/order or provider exception values.
        raise VersionFeeEvidenceError("VERSION_FEE_EVIDENCE_INVALID") from None


def _evaluate(base: bytes, capture: bytes, target: str, registry: object,
              key: bytes, key_id: str, scope: str) -> FeeVersionEvaluation:
    versions._key(key); versions._key_id(key_id); versions._hash(scope); versions._hash(target)
    registered = versions._registry(registry)
    _require(cl3.TBANK_OPERATION_CODEC in registered.values(), "CODEC_UNSUPPORTED")
    _require(type(base) is bytes and type(capture) is bytes and 0 < len(capture) <= MAX_EVIDENCE_BYTES,
             "INPUT_BOUNDS_INVALID")
    data = _parse(capture)
    _require(type(data) is dict and frozenset(data) == _FIELDS and data["domain"] == DOMAIN
             and type(data["version"]) is int and data["version"] == 1, "CAPTURE_INVALID")
    captured_at = _time(data["captured_at"])
    _require(captured_at == data["captured_at"], "TIME_NOT_CANONICAL")
    projection = cl4.project_shadow_cash(base, account_scope_sha256=scope,
        environment=cl3.BrokerEnvironment.SANDBOX, as_of=captured_at, identity_key=key)
    _require(projection.complete and projection.version == 3, "BASE_LEDGER_INVALID")
    closure = _unseal(data["closure_json_ascii"], key, _CLOSURE_FIELDS, "CL7_EXACT_SETTLEMENT_CLOSURE_V1")
    cash = _unseal(data["cash_plan_json_ascii"], key, _CASH_FIELDS, "CL7_EXACT_CASH_COMPONENTS_V1")
    _require(closure["cash_binding"]["ledger_export_sha256"] == _sha(base)
             and closure["cash_binding"]["plan_sha256"] == _sha(_canonical(cash))
             and closure["proof_sha256"] == cash["match"]["proof_sha256"], "CLOSURE_BINDING_INVALID")
    parsed_base = _parse(base)
    components = tuple(_component(parsed_base, item["transaction_sha256"]) for item in cash["match"]["components"])
    _require(_check_prefix(_parse(cash["baseline_export_ascii"]), parsed_base, components) == len(components),
             "BASE_CASH_PREFIX_INVALID")
    _require(len(components) == 2 and not parsed_base["correction_bundles"], "ORIGINAL_PROFILE_UNSUPPORTED")
    old_obs, original = _component(parsed_base, target)
    _require(original.classification is LedgerClassification.COMMISSION
             and original.corrects_sha256 is None and original.reversal_of_sha256 is None
             and target in {tx.sha256 for _, tx in components}, "NOT_ORDINARY_CLOSURE_FEE")
    _require(all(o.sha256 == item["observation_sha256"] for (o, _), item in zip(components, cash["match"]["components"], strict=True)),
             "ORIGINAL_PROVENANCE_INVALID")
    old_amount = -next(p.money.minor_units for p in original.postings if p.account is LedgerAccount.ASSET_BROKER_CASH)
    _require(old_amount == int(cash["match"]["commission_nano"]) and old_amount > 0, "OLD_FEE_INVALID")
    pending = RuntimeCashAuthorityRecord.from_canonical_dict(closure["before"]["authority"])
    finished = RuntimeCashAuthorityRecord.from_canonical_dict(closure["after"]["authority"])
    before_central = CentralOrderState.from_dict(closure["before"]["central"])
    original_intent = RuntimeCashAuthorityManager._recovery_intent_from_state(
        None, pending, before_central, identity_key=key)
    info = data["instrument"]
    _require(type(info) is dict and set(info) == {"instrument_id", "currency", "asset_class", "lot_size"},
             "INSTRUMENT_INVALID")
    instrument = SimpleNamespace(**info)
    proof = validate_exact_receipt_binding(original_intent, account_id=data["raw_account_id"],
        identity_key=key, identity_key_id=key_id, instrument=instrument)
    _require(proof.sha256 == closure["proof_sha256"] and proof.account_scope_sha256 == scope
             and finished.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
             and finished.pending_dispatch_proof_sha256 is None
             and finished.ledger_head_sha256 == parsed_base["ledger_head_sha256"]
             and finished.ledger_revision == int(parsed_base["ledger_revision"])
             and finished.post_attempt_count == pending.post_attempt_count, "CLOSURE_NOT_CLOSED")
    done = [i for i in CentralOrderState.from_dict(closure["after"]["central"]).intents if i.intent_id == original_intent.intent_id]
    _require(len(done) == 1 and done[0].status == "RECONCILED" and done[0].outcome == "FILLED"
             and done[0].candidate == original_intent.candidate
             and done[0].cl7_locked_dispatch_proof == original_intent.cl7_locked_dispatch_proof
             and done[0].risk_execution_status == "RECORDED", "CLOSURE_INTENT_INVALID")
    raw = data["order_state"]
    receipt = decode_exact_order_receipt(raw, original_intent, account_id=data["raw_account_id"],
        identity_key=key, identity_key_id=key_id, instrument=instrument, observed_at=captured_at)
    _require(receipt.provider_status == "FILL" and receipt.executed_lots == receipt.requested_lots
             and receipt.fee_status == "RECEIPT_COMMISSION_OBSERVED", "RECEIPT_PROFILE_UNSUPPORTED")
    # Only comparison of unchanged execution uses the historic fee. This value
    # is never mislabelled as a new provider observation or used as a cash amount.
    comparison = deepcopy(raw)
    comparison["executedCommission"] = {"currency": "RUB", "units": str(old_amount // 10**9), "nano": old_amount % 10**9}
    old_receipt = decode_exact_order_receipt(comparison, original_intent, account_id=data["raw_account_id"],
        identity_key=key, identity_key_id=key_id, instrument=instrument, observed_at=captured_at)
    _require(old_receipt.receipt_identity_sha256 == cash["match"]["receipt_sha256"]
             == closure["cash_binding"]["receipt_sha256"], "EXECUTION_CHANGED")
    new_amount = receipt.executed_commission_nano
    _require(type(new_amount) is int and new_amount > 0 and new_amount != old_amount, "FEE_CHANGE_UNSUPPORTED")
    start = pending.operations_complete_through
    _require(type(start) is str and 0 < timestamp_ns(captured_at) - timestamp_ns(start) <= _MAX_WINDOW_NS
             and captured_at > finished.operations_complete_through
             and all(start <= s.execution_at < captured_at for s in receipt.stages), "WINDOW_INVALID")
    batch, rows = _read_batch(data, key, key_id, start, scope)
    required = {o.sha256 for o, _ in components} - {old_obs.sha256}
    pairs = list(zip(rows, batch.decisions, strict=True))
    unchanged = [(row, d) for row, d in pairs if d.observation.sha256 in required]
    revised = [(row, d) for row, d in pairs if d.observation.sha256 not in required]
    _require(len(unchanged) == len(required) and {d.observation.sha256 for _, d in unchanged} == required
             and len(revised) == 1, "OPERATION_SET_INVALID")
    view = SimpleNamespace(cl7_identity_key=key, cl7_own_funds_policy=SimpleNamespace(instruments={instrument.instrument_id: instrument}))
    parent = _validate_unchanged_rows(view, original_intent, receipt, unchanged, raw)
    fee, decision = revised[0]
    _require(fee["type"] == "OPERATION_TYPE_BROKER_FEE" and fee["state"] == "OPERATION_STATE_EXECUTED"
             and fee.get("parentOperationId") == parent and fee.get("instrumentUid") == instrument.instrument_id
             and fee["childOperations"] == [] and fee.get("tradesInfo", {"trades": []}) == {"trades": []}
             and all(_uint(fee[n]) == 0 for n in ("quantity", "quantityDone", "quantityRest"))
             and cl3.money_value_to_money(fee["commission"]).minor_units == 0
             and _time(fee["date"]) == original.effective_at, "FEE_BINDING_INVALID")
    observation = decision.observation
    _require(decision.kind is cl3.BrokerDecisionKind.REVIEW_REQUIRED
             and decision.reason is cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS
             and decision.transaction_proposal is None, "CL3_PROFILE_UNSUPPORTED")
    old_content, new_content = _parse(old_obs.content_json_ascii), _parse(observation.content_json_ascii)
    old_content.pop("payment_minor_units"); new_content.pop("payment_minor_units")
    _require(old_content == new_content and old_obs.logical_source_sha256 == observation.logical_source_sha256
             and old_obs.source.source_scope_sha256 == observation.source.source_scope_sha256
             and old_obs.descriptor == observation.descriptor and old_obs.sha256 != observation.sha256,
             "SAME_ID_BINDING_INVALID")
    payment = cl3.money_value_to_money(fee["payment"])
    _require(payment.minor_units == -new_amount, "FEE_TOTAL_MISMATCH")
    reversal = LedgerTransaction.reversing(original, effective_at=original.effective_at, source=observation.source)
    correction = LedgerTransaction(classification=LedgerClassification.COMMISSION,
        effective_at=original.effective_at, source=observation.source,
        postings=(LedgerPosting(1, LedgerAccount.ASSET_BROKER_CASH, payment),
                  LedgerPosting(2, LedgerAccount.EXPENSE_COMMISSION, -payment)), corrects_sha256=target)
    bundle = LedgerCorrectionBundle(original, reversal, correction)
    cash_proof = cl4.build_broker_rub_position_cash_proof(data["rub_positions"], raw_account_id=data["raw_account_id"],
        account_scope_sha256=scope, environment=cl3.BrokerEnvironment.SANDBOX,
        as_of=captured_at, evaluated_at=captured_at, response_complete=True, identity_key=key, identity_key_id=key_id)
    delta = old_amount - new_amount
    _require(projection.expected_cash.minor_units + delta == cash_proof.cash.minor_units, "BROKER_CASH_MISMATCH")
    request_hash = _sha(_canonical(data["operation_requests"]))
    response_hash = _sha(_canonical(data["operation_responses"]))
    report = _canonical({
        "domain": "CL2_SAME_ID_FEE_ECONOMIC_REPORT_V1", "version": 1,
        "account_scope_sha256": scope, "identity_key_id": key_id,
        "baseline_export_sha256": _sha(base), "closure_sha256": _sha(_canonical(closure)),
        "cash_plan_sha256": _sha(_canonical(cash)), "proof_sha256": proof.sha256,
        "original_transaction_sha256": target, "previous_observation_sha256": old_obs.sha256,
        "observation_sha256": observation.sha256, "logical_source_sha256": observation.logical_source_sha256,
        "bundle_sha256": bundle.sha256, "old_fee_nano": str(old_amount), "new_fee_nano": str(new_amount),
        "cash_delta_nano": str(delta), "cash_before_nano": str(projection.expected_cash.minor_units),
        "cash_after_nano": str(cash_proof.cash.minor_units), "captured_at": captured_at,
        "capture_sha256": _sha(capture), "request_sha256": request_hash, "response_sha256": response_hash,
        "receipt_sha256": receipt.receipt_identity_sha256, "cash_proof_sha256": _sha(_canonical(cash_proof.to_canonical_dict())),
        "watermark": _parse(batch.watermark.canonical_bytes), "runtime_authority_granted": False,
    })
    evidence = versions.RevisionEvidence(old_obs.sha256, observation.sha256,
        request_hash, response_hash, _sha(report), captured_at)
    return FeeVersionEvaluation(original, observation, bundle, report, evidence,
        projection.expected_cash.minor_units, cash_proof.cash.minor_units, delta)


@dataclass(frozen=True, slots=True)
class FeeVersionCapture:
    capture: bytes
    evaluation: FeeVersionEvaluation
    review_export_sha256: str
    expected_version_head_sha256: str
    expected_store_revision: int


def capture_same_id_fee_revision(adapter: Any, *, recovery: Any, review_store: versions.ObservationVersionStore,
                                 proof_sha256: str, original_transaction_sha256: str) -> FeeVersionCapture:
    """Explicit read-only capture for a detached review, not a HOLD/cutover or fee scanner.

    Requires the still-unchanged last DISARMED runtime closure. Cooperative
    owners are rechecked during reads and under final locks. This does not freeze
    future broker data or authorize any runtime writer.
    """
    try:
        return _capture(adapter, recovery, review_store, proof_sha256, original_transaction_sha256)
    except VersionFeeEvidenceError:
        raise
    except Exception:
        raise VersionFeeEvidenceError("VERSION_FEE_CAPTURE_FAILED") from None


def _capture(a: Any, r: Any, review: Any, proof_hash: str, target: str) -> FeeVersionCapture:
    _require(type(review) is versions.ObservationVersionStore, "REVIEW_STORE_INVALID")
    manager, ledger, key, key_id = a.cash_authority_manager, a.cl7_ledger_store, a.cl7_identity_key, a.cl7_identity_key_id
    transport, own = a.transport, a.cl7_own_funds_policy
    with manager.store.locked():
        config, owners = _config(a, r), _owners(a, r)
        root = a.manager.store.path.parent
        cs, ps = _ClosureStore(root, proof_hash, key), _PlanStore(root, proof_hash, key)
        closure, cash = cs.load(), ps.load()
        _require(closure is not None and cash is not None and closure["proof_sha256"] == proof_hash
                 and closure["config_sha256"] == config
                 and owners == closure["after"], "LATEST_CLOSURE_REQUIRED")
        authority = RuntimeCashAuthorityRecord.from_canonical_dict(owners["authority"])
        _require(authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED, "DISARMED_REQUIRED")
        frozen, base = review.export_bytes(), ledger.export_bytes()
        graph = versions._validate_export(frozen, review._registry, key, key_id, authority.account_scope_sha256)
        _require(graph.base == base and graph.snapshot.revision_count == 0, "FROZEN_ROOT_REQUIRED")
        start_tick, start_wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
        last_tick, last_wall = start_tick, start_wall
        original = manager._recovery_intent_from_state(
            RuntimeCashAuthorityRecord.from_canonical_dict(closure["before"]["authority"]),
            CentralOrderState.from_dict(closure["before"]["central"]), identity_key=key)
        instrument = a.cl7_own_funds_policy.instruments[original.candidate.instrument_id]
        pending = RuntimeCashAuthorityRecord.from_canonical_dict(closure["before"]["authority"])

        def guard() -> None:
            nonlocal last_tick, last_wall
            tick, wall = a.cl7_monotonic_ns(), timestamp_ns(_time(a.cl7_clock()))
            _require(type(tick) is int and last_tick <= tick <= start_tick + MAX_AGE_NS
                     and last_wall <= wall <= start_wall + MAX_AGE_NS, "CAPTURE_STALE")
            last_tick, last_wall = tick, wall
            _require(_owners(a, r) == owners and _config(a, r) == config
                     and ledger.export_bytes() == base and review.export_bytes() == frozen
                     and cs.load() == closure and ps.load() == cash
                     and a.cash_authority_manager is manager and a.cl7_ledger_store is ledger
                     and a.cl7_identity_key == key and a.cl7_identity_key_id == key_id
                     and a.transport is transport and a.cl7_own_funds_policy is own, "SOURCE_CHANGED")

        guard()
        raw = _parse(_canonical(a.transport.get_order_state(a.policy.account_id, original.intent_id, by_request_id=True)))
        guard()
        end = _time(a.cl7_clock())
        requests, responses = [], []

        def collect(payload: Any, timeout_ns: int) -> Any:
            request = _parse(_canonical(payload))
            response = _parse(_canonical(a.transport.get_operations_by_cursor_once(payload, timeout_ns)))
            requests.append(request); responses.append(response)
            return response

        view = SimpleNamespace(transport=SimpleNamespace(get_operations_by_cursor_once=collect),
            policy=a.policy, cl7_identity_key=key, cl7_identity_key_id=key_id,
            cl7_monotonic_ns=a.cl7_monotonic_ns, cl7_wait_ns=a.cl7_wait_ns)
        _collect_overlap(view, authority.account_scope_sha256, pending.operations_complete_through,
                         end, start_tick + MAX_AGE_NS, guard)
        positions = manager.read_accounting_cash(a.transport, a.policy.account_id,
            clock=a.cl7_clock, monotonic_ns=a.cl7_monotonic_ns)
        guard()
        capture = _canonical({"domain": DOMAIN, "version": 1, "raw_account_id": a.policy.account_id,
            "instrument": {name: getattr(instrument, name) for name in ("instrument_id", "currency", "asset_class", "lot_size")},
            "captured_at": end, "closure_json_ascii": _sealed(closure, key).decode("ascii"),
            "cash_plan_json_ascii": _sealed(cash, key).decode("ascii"), "order_state": raw,
            "operation_requests": requests, "operation_responses": responses, "rub_positions": positions})
        result = evaluate_captured_fee_revision(base, capture, original_transaction_sha256=target,
            codec_registry=tuple(review._registry.values()), identity_key=key, identity_key_id=key_id,
            account_scope_sha256=authority.account_scope_sha256)
        with _owner_locks(a, r, ledger=True):
            guard()
        return FeeVersionCapture(capture, result, _sha(frozen), graph.snapshot.version_head_sha256,
                                 graph.snapshot.store_revision)
