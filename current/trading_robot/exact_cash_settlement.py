"""Receipt-bound CL3 -> CL2 cash components, NOT order-completion authority.

Bounded profile: one full FILL or positive partial CANCELLED RUB SHARE/ETF
execution and, when nonzero, one separately observed BROKER_FEE linked by
parentOperationId. Zero CANCELLED/REJECTED requires a complete empty operation
window or one fee bound to the exact exchange order; no zero trade is invented.  Payment must
be gross (not net); commission on the trade is informational, never booked twice.
CL3's generic ambiguous decisions are not changed.  Their existing source and
observation identities are reused after this stricter receipt match.

An immutable HMAC plan precedes CL2 writes. Each CL2 append remains atomic; this
is intentionally NOT an atomic transaction spanning the component set or owners.
An interrupted prefix resumes only against the same plan and exact ledger prefix.
The authority and Central guards from STEP13 remain closed after success.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import broker_read_adapters as cl3
from . import cash_ledger_opening_reconciliation as cl4
from .cash_ledger_domain import LedgerAccount, LedgerClassification, LedgerPosting, LedgerTransaction
from .cash_ledger_persistence import InboxStatusEvent
from .exact_order_receipt import _id, _keyed, _time, _uint, validate_exact_receipt_binding
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .runtime_cash_authority import RuntimeCashAuthorityState
from .state_persistence import atomic_write_json

_DOMAIN = "CL7_EXACT_CASH_COMPONENTS_V1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PLAN_BYTES = 2 * 1024 * 1024


class ExactCashSettlementError(RuntimeError):
    """Finite, privacy-safe failure; a failed call may have a retained CL2 prefix."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ExactCashSettlementError("EXACT_CASH_" + code)


def _canonical(value: Any) -> bytes:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ExactCashSettlementError("EXACT_CASH_CANONICAL_INVALID") from None
    _require(len(data) <= _MAX_PLAN_BYTES, "SIZE_LIMIT")
    return data


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _seal(key: bytes, value: Any) -> str:
    return hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        _require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _safe_path(path: Path) -> None:
    # Match the local cooperative-writer boundary; reject Windows reparse points
    # as well as POSIX links. This is not protection against a hostile keyed OS user.
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        _require(not stat.S_ISLNK(info.st_mode)
                 and not (getattr(info, "st_file_attributes", 0) & 0x400), "PLAN_LINK_FORBIDDEN")


class _PlanStore:
    def __init__(self, directory: Path, proof_hash: str, key: bytes) -> None:
        _require(type(proof_hash) is str and _HEX.fullmatch(proof_hash) is not None,
                 "PROOF_INVALID")
        self.path = directory / "exact_cash_components" / (proof_hash + ".json")
        self.key = key

    def load(self) -> dict[str, Any] | None:
        _safe_path(self.path)
        if not self.path.exists():
            return None
        _require(self.path.is_file() and self.path.stat().st_size <= _MAX_PLAN_BYTES,
                 "PLAN_INVALID")
        raw = self.path.read_bytes()
        _require(len(raw) <= _MAX_PLAN_BYTES, "PLAN_INVALID")
        try:
            document = json.loads(raw, object_pairs_hook=_pairs)
        except (ValueError, UnicodeError, RecursionError):
            raise ExactCashSettlementError("EXACT_CASH_PLAN_INVALID") from None
        _require(type(document) is dict and set(document) == {"payload", "hmac_sha256"},
                 "PLAN_INVALID")
        body = document["payload"]
        _require(type(body) is dict and set(body) == {
            "domain", "version", "match", "baseline_export_ascii", "first_watermark_sha256"},
            "PLAN_INVALID")
        _require(type(body["version"]) is int and body["version"] == 1
                 and body["domain"] == _DOMAIN and type(body["baseline_export_ascii"]) is str
                 and type(document["hmac_sha256"]) is str
                 and hmac.compare_digest(document["hmac_sha256"], _seal(self.key, body)),
                 "PLAN_AUTHENTICATION_FAILED")
        return body

    def create(self, payload: dict[str, Any]) -> None:
        _require(self.load() is None, "PLAN_ALREADY_EXISTS")
        # The caller holds the account authority lock. Never update an old plan.
        document = {"payload": payload, "hmac_sha256": _seal(self.key, payload)}
        _canonical(document)
        try:
            atomic_write_json(self.path, document, retry_delays=(), jitter_fraction=0,
                              write_checksum=False, keep_last_good=False)
        except Exception:
            raise ExactCashSettlementError("EXACT_CASH_PLAN_WRITE_FAILED") from None
        _require(self.load() == payload, "PLAN_READBACK_FAILED")


@dataclass(frozen=True, slots=True)
class CashComponentsResult:
    proof_sha256: str
    receipt_sha256: str
    plan_sha256: str
    operation_watermark_sha256: str
    cash_reconciliation_sha256: str
    ledger_head_sha256: str
    ledger_revision: int
    transaction_sha256s: tuple[str, ...]
    gross_nano: int
    commission_nano: int
    cash_delta_nano: int
    appended_transactions: int
    replay: bool

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "domain": _DOMAIN + "_RESULT", "version": 1,
            "status": "CASH_COMPONENTS_RECORDED_PENDING_CLOSURE",
            "proof_sha256": self.proof_sha256, "receipt_sha256": self.receipt_sha256,
            "plan_sha256": self.plan_sha256, "operation_watermark_sha256": self.operation_watermark_sha256,
            "cash_reconciliation_sha256": self.cash_reconciliation_sha256,
            "ledger_head_sha256": self.ledger_head_sha256, "ledger_revision": str(self.ledger_revision),
            "transaction_sha256s": list(self.transaction_sha256s),
            "gross_nano": str(self.gross_nano), "commission_nano": str(self.commission_nano),
            "cash_delta_nano": str(self.cash_delta_nano),
            "appended_transactions": self.appended_transactions, "replay": self.replay,
            "cash_components_verified": True, "settlement_verified": False,
            "authority_clear_allowed": False,
        }


def _collect(adapter: Any, authority: Any, receipt: Any, guard: Any) -> tuple[Any, list[dict[str, Any]]]:
    end = _time(adapter.cl7_clock())
    start = authority.operations_complete_through
    _require(type(start) is str and start < end and start[:10] == end[:10], "WINDOW_OUT_OF_SCOPE")
    _require(all(start <= s.execution_at < end for s in receipt.stages), "WINDOW_INCOMPLETE")
    rows: list[dict[str, Any]] = []

    def transport(payload: Any, timeout_ns: int) -> Any:
        guard()
        raw = adapter.transport.get_operations_by_cursor_once(payload, timeout_ns)
        # Snapshot once: the same immutable value supplies CL3 and the join.
        copied = json.loads(_canonical(raw), object_pairs_hook=_pairs)
        guard()
        if type(copied) is dict and type(copied.get("items")) is list:
            rows.extend(copied["items"])
        return copied

    request = cl3.BrokerReadRequest(
        environment=cl3.BrokerEnvironment.SANDBOX, raw_account_id=adapter.policy.account_id,
        identity_key=adapter.cl7_identity_key, identity_key_id=adapter.cl7_identity_key_id,
        from_inclusive=start, to_exclusive=end, limit=100, max_pages=4, max_items=4,
        absolute_deadline_ns=adapter.cl7_monotonic_ns() + MAX_AGE_NS,
        retry_policy=cl3.RetryPolicy(1, MAX_AGE_NS, ()), transport=transport,
        monotonic_ns=adapter.cl7_monotonic_ns, wait_ns=adapter.cl7_wait_ns,
    )
    batch = cl3.collect_tbank_operations(request)
    guard()
    _require(batch.watermark.account_scope_sha256 == receipt.account_scope_sha256
             and len(batch.decisions) == len(rows) == batch.watermark.item_count,
             "BATCH_BINDING_INVALID")
    return batch, rows


def settlement_outcome(receipt: Any) -> str:
    """Classify only supported *terminal* economic outcomes, never active partials.

    The original requested target remains immutable. Zero terminal outcomes
    do not authorize any execution or cash effect without the separate match.
    """
    _require(type(receipt.executed_lots) is int and type(receipt.requested_lots) is int
             and 0 < receipt.requested_lots and 0 <= receipt.executed_lots <= receipt.requested_lots,
             "TERMINAL_QUANTITY_INVALID")
    if receipt.executed_lots == 0 and receipt.provider_status in {"CANCELLED", "REJECTED"}:
        _require(not receipt.stages and receipt.gross_nano == 0, "ZERO_FILL_HAS_TRADES")
        return receipt.provider_status
    if receipt.provider_status == "FILL" and receipt.executed_lots == receipt.requested_lots:
        return "FILLED"
    if receipt.provider_status == "CANCELLED" and receipt.executed_lots < receipt.requested_lots:
        return "PARTIALLY_FILLED"
    raise ExactCashSettlementError("EXACT_CASH_TERMINAL_EXECUTION_REQUIRED")


def _zero_components(adapter: Any, intent: Any, receipt: Any, batch: Any,
                     rows: list[dict[str, Any]]) -> tuple[tuple[Any, LedgerTransaction], ...]:
    """A bounded zero-terminal profile, not proof of all future fee finality.

    Empty read windows are accepted only with a positively bound terminal
    receipt and explicit zero fees. Nonzero fees must name the same exchange
    order, not a guessed operation, and are the only allowed operation.
    """
    attempts = [t for t in intent.transitions if t.status == "IN_FLIGHT"]
    _require(len(attempts) == 1, "ZERO_ATTEMPT_LINEAGE_INVALID")
    attempted = _time(attempts[0].at)
    start, end = batch.watermark.from_inclusive, batch.watermark.to_exclusive
    # Empty stages must not make temporal checks vacuously true. Cover the
    # persisted attempt through the current read, within UTC and Moscow days.
    utc_attempt = datetime.fromisoformat(attempted.replace("Z", "+00:00"))
    utc_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    moscow = timezone(timedelta(hours=3))
    _require(start <= attempted < end and utc_attempt.date() == utc_end.date()
             and utc_attempt.astimezone(moscow).date() == utc_end.astimezone(moscow).date(),
             "ZERO_WINDOW_OUT_OF_SCOPE")
    if receipt.executed_commission_nano == 0:
        _require(rows == [] and receipt.expected_cash_delta_nano == 0,
                 "ZERO_OPERATIONS_NOT_EMPTY")
        return ()
    _require(len(rows) == 1 and len(batch.decisions) == 1, "ZERO_FEE_EVIDENCE_INCOMPLETE")
    fee, decision = rows[0], batch.decisions[0]
    c = intent.candidate
    parent = _id(fee.get("parentOperationId"))
    parent_scope = _keyed(adapter.cl7_identity_key, {
        "domain": "CL7_EXACT_ORDER_RECEIPT_V1_ORDER",
        "account_scope_sha256": receipt.account_scope_sha256, "order_id": parent})
    _require(parent_scope == receipt.order_scope_sha256
             and fee["type"] == "OPERATION_TYPE_BROKER_FEE"
             and fee["state"] == "OPERATION_STATE_EXECUTED"
             and fee.get("instrumentUid") == c.instrument_id
             and fee["childOperations"] == []
             and fee.get("tradesInfo", {"trades": []}) == {"trades": []}
             and all(_uint(fee[name]) == 0 for name in ("quantity", "quantityDone", "quantityRest"))
             and cl3.money_value_to_money(fee["commission"]).minor_units == 0
             and attempted <= _time(fee["date"]) < end, "ZERO_FEE_BINDING_INVALID")
    payment = cl3.money_value_to_money(fee["payment"])
    _require(payment.minor_units == -receipt.executed_commission_nano
             and receipt.expected_cash_delta_nano == payment.minor_units, "ZERO_FEE_PAYMENT_MISMATCH")
    _require(decision.kind is cl3.BrokerDecisionKind.REVIEW_REQUIRED
             and decision.reason is cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS
             and decision.transaction_proposal is None, "ZERO_CL3_DECISION_UNSUPPORTED")
    transaction = LedgerTransaction(classification=LedgerClassification.COMMISSION,
        effective_at=_time(fee["date"]), source=decision.observation.source,
        postings=(LedgerPosting(1, LedgerAccount.ASSET_BROKER_CASH, payment),
                  LedgerPosting(2, LedgerAccount.EXPENSE_COMMISSION, -payment)),
        reversal_of_sha256=None, corrects_sha256=None)
    return ((decision.observation, transaction),)


def _components(adapter: Any, intent: Any, receipt: Any, batch: Any,
                rows: list[dict[str, Any]]) -> tuple[tuple[Any, LedgerTransaction], ...]:
    outcome = settlement_outcome(receipt)
    _require(receipt.fee_status == "RECEIPT_COMMISSION_OBSERVED", "FEES_UNRESOLVED")
    if outcome in {"CANCELLED", "REJECTED"}:
        return _zero_components(adapter, intent, receipt, batch, rows)
    expected_count = 2 if receipt.executed_commission_nano else 1
    _require(len(rows) == expected_count, "OPERATIONS_INCOMPLETE_OR_OUT_OF_SCOPE")
    pairs = list(zip(rows, batch.decisions))
    trade_pairs = [(row, decision) for row, decision in pairs
                   if row["type"] == "OPERATION_TYPE_" + receipt.direction]
    _require(len(trade_pairs) == 1, "TRADE_OPERATION_AMBIGUOUS")
    trade, trade_decision = trade_pairs[0]
    c = intent.candidate
    _require(trade.get("instrumentUid") == c.instrument_id
             and trade.get("instrumentType", "").lower() in {"share", "etf"}
             and trade.get("instrumentType", "").lower() == adapter.cl7_own_funds_policy.instruments[c.instrument_id].asset_class.lower(),
             "INSTRUMENT_MISMATCH")
    _require(trade["state"] == "OPERATION_STATE_EXECUTED"
             and trade.get("parentOperationId", "") == "" and trade["childOperations"] == [],
             "TRADE_STATE_OR_COMPONENTS_UNSUPPORTED")
    units = receipt.executed_lots * receipt.lot_size
    # Operation quantities are securities, unlike receipt stages (lots).
    # For partial cancellation require the original order quantity and its
    # exact unexecuted remainder. Do not infer a terminal outcome from quantities.
    requested_units = receipt.requested_lots * receipt.lot_size
    _require(_uint(trade["quantity"]) == requested_units
             and _uint(trade["quantityDone"]) == units
             and _uint(trade["quantityRest"]) == requested_units - units,
             "UNITS_MISMATCH")
    info = trade.get("tradesInfo")
    _require(type(info) is dict and set(info) == {"trades"}
             and type(info["trades"]) is list and len(info["trades"]) == len(receipt.stages),
             "TRADE_EVIDENCE_MISSING")
    observed: dict[str, tuple[int, int, str]] = {}
    for row in info["trades"]:
        _require(type(row) is dict and {"num", "date", "quantity", "price"} <= set(row)
                 and set(row) <= {"num", "date", "quantity", "price", "yield", "yieldRelative"},
                 "TRADE_SCHEMA_INVALID")
        trade_id = _id(row["num"])
        scope = _keyed(adapter.cl7_identity_key, {
            "domain": "CL7_EXACT_ORDER_RECEIPT_V1_TRADE",
            "account_scope_sha256": receipt.account_scope_sha256, "trade_id": trade_id})
        _require(scope not in observed, "DUPLICATE_TRADE")
        observed[scope] = (_uint(row["quantity"]), cl3.money_value_to_money(row["price"]).minor_units,
                           _time(row["date"]))
    expected = {s.trade_scope_sha256: (s.lots * receipt.lot_size, s.price_nano, s.execution_at)
                for s in receipt.stages}
    _require(observed == expected, "TRADE_RECEIPT_MISMATCH")
    signed_gross = -receipt.gross_nano if receipt.direction == "BUY" else receipt.gross_nano
    _require(cl3.money_value_to_money(trade["payment"]).minor_units == signed_gross,
             "GROSS_PAYMENT_MISMATCH")
    _require(abs(cl3.money_value_to_money(trade["commission"]).minor_units) == receipt.executed_commission_nano,
             "COMMISSION_SUMMARY_MISMATCH")
    selected = [(trade, trade_decision, LedgerClassification.TRADE_SETTLEMENT,
                 LedgerAccount.ASSET_TRADE_CLEARING)]
    if receipt.executed_commission_nano:
        fee_pairs = [(row, d) for row, d in pairs if row["type"] == "OPERATION_TYPE_BROKER_FEE"]
        _require(len(fee_pairs) == 1, "FEE_EVIDENCE_INCOMPLETE")
        fee, fee_decision = fee_pairs[0]
        _require(fee["state"] == "OPERATION_STATE_EXECUTED"
                 and fee.get("parentOperationId") == trade["id"]
                 and fee.get("instrumentUid", c.instrument_id) == c.instrument_id
                 and fee["childOperations"] == []
                 and fee.get("tradesInfo", {"trades": []}) == {"trades": []}
                 and all(_uint(fee[name]) == 0 for name in ("quantity", "quantityDone", "quantityRest"))
                 and cl3.money_value_to_money(fee["commission"]).minor_units == 0,
                 "FEE_BINDING_INVALID")
        _require(cl3.money_value_to_money(fee["payment"]).minor_units == -receipt.executed_commission_nano,
                 "FEE_PAYMENT_MISMATCH")
        selected.append((fee, fee_decision, LedgerClassification.COMMISSION, LedgerAccount.EXPENSE_COMMISSION))
    components = []
    for row, decision, classification, counterpart in selected:
        _require(decision.kind is cl3.BrokerDecisionKind.TRANSACTION_PROPOSED or
                 (decision.kind is cl3.BrokerDecisionKind.REVIEW_REQUIRED and
                  (decision.reason is cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS
                   or (row is trade and outcome == "PARTIALLY_FILLED"
                       and decision.reason is cl3.BrokerDecisionReason.PARTIAL_EXECUTION_AMBIGUOUS))),
                 "CL3_DECISION_UNSUPPORTED")
        payment = cl3.money_value_to_money(row["payment"])
        transaction = LedgerTransaction(
            classification=classification, effective_at=_time(row["date"]), source=decision.observation.source,
            postings=(LedgerPosting(1, LedgerAccount.ASSET_BROKER_CASH, payment),
                      LedgerPosting(2, counterpart, -payment)), reversal_of_sha256=None, corrects_sha256=None)
        _require(decision.transaction_proposal is None or
                 decision.transaction_proposal.canonical_bytes == transaction.canonical_bytes,
                 "CL3_PROPOSAL_MISMATCH")
        components.append((decision.observation, transaction))
    return tuple(components)


def _match(receipt: Any, components: Any) -> dict[str, Any]:
    return {"proof_sha256": receipt.proof_sha256, "receipt_sha256": receipt.receipt_identity_sha256,
            "account_scope_sha256": receipt.account_scope_sha256,
            "gross_nano": str(receipt.gross_nano), "commission_nano": str(receipt.executed_commission_nano),
            "cash_delta_nano": str(receipt.expected_cash_delta_nano),
            "components": [{"observation_sha256": o.sha256, "transaction_sha256": t.sha256}
                           for o, t in components]}


def _check_prefix(baseline: dict[str, Any], current: dict[str, Any], components: Any) -> int:
    """Accept only the exact append prefixes of this immutable plan, nothing else."""
    for name in ("domain", "version", "schema_version", "correction_bundles"):
        _require(current[name] == baseline[name], "LEDGER_PREFIX_CONFLICT")
    for name in ("transactions", "observations", "codec_registry", "provenance_links", "inbox_status_events"):
        _require(type(current[name]) is list, "LEDGER_PREFIX_CONFLICT")
    def keyed(rows: Any) -> dict[str, Any]:
        return {row["sha256"]: row for row in rows}
    old_t, new_t = keyed(baseline["transactions"]), keyed(current["transactions"])
    old_o, new_o = keyed(baseline["observations"]), keyed(current["observations"])
    _require(all(new_t.get(k) == v for k, v in old_t.items())
             and all(new_o.get(k) == v for k, v in old_o.items()), "LEDGER_BASELINE_CHANGED")
    tx_ids = [t.sha256 for _, t in components]
    obs_ids = [o.sha256 for o, _ in components]
    nt = len(set(new_t) - set(old_t)); no = len(set(new_o) - set(old_o))
    _require(0 <= nt <= len(components) and nt <= no <= min(nt + 1, len(components))
             and set(new_t) - set(old_t) == set(tx_ids[:nt])
             and set(new_o) - set(old_o) == set(obs_ids[:no]), "LEDGER_PREFIX_CONFLICT")
    for i, (o, t) in enumerate(components):
        if i < nt:
            _require(new_t[t.sha256] == {"canonical_json_ascii": t.canonical_bytes.decode("ascii"),
                     "economic_sha256": t.economic_sha256, "sha256": t.sha256,
                     "source_sha256": t.source.sha256}, "TRANSACTION_CHANGED")
        if i < no:
            _require(new_o[o.sha256] == {"canonical_json_ascii": o.canonical_bytes.decode("ascii"),
                     "current_status": "LEDGER_LINKED" if i < nt else "OBSERVED",
                     "logical_source_sha256": o.logical_source_sha256, "sha256": o.sha256,
                     "source_sha256": o.source.sha256}, "OBSERVATION_CHANGED")
    codec = cl3.TBANK_OPERATION_CODEC
    expected_codecs = keyed(baseline["codec_registry"])
    if no:
        expected_codecs[codec.sha256] = {"canonical_json_ascii": codec.canonical_bytes.decode("ascii"),
                                        "sha256": codec.sha256}
    _require(keyed(current["codec_registry"]) == expected_codecs, "CODEC_CHANGED")
    extra_links = [{"observation_sha256": o.sha256, "transaction_sha256": t.sha256}
                   for o, t in components[:nt]]
    expected_events = keyed(baseline["inbox_status_events"])
    for o, t in components[:nt]:
        event = InboxStatusEvent(o.sha256, 1, "OBSERVED", "LEDGER_LINKED",
                                 "LEDGER_TRANSACTION_ACCEPTED", t.sha256)
        expected_events[event.sha256] = {"canonical_json_ascii": event.canonical_bytes.decode("ascii"),
                                        "sha256": event.sha256}
    _require(sorted(current["provenance_links"], key=_canonical) ==
             sorted(baseline["provenance_links"] + extra_links, key=_canonical)
             and keyed(current["inbox_status_events"]) == expected_events,
             "PROVENANCE_CHANGED")
    _require(int(current["ledger_revision"]) == int(baseline["ledger_revision"]) + nt
             and int(current["store_revision"]) == int(baseline["store_revision"]) + nt + no,
             "REVISION_CONFLICT")
    old_heads = baseline["ledger_transitions"]
    _require(current["ledger_transitions"][:len(old_heads)] == old_heads
             and len(current["ledger_transitions"]) == len(old_heads) + nt, "HEAD_PREFIX_CHANGED")
    for row, (_, t) in zip(current["ledger_transitions"][len(old_heads):], components[:nt]):
        head = json.loads(row["head_json_ascii"])
        _require(head["transition_kind"] == "TRANSACTION" and head["transition_sha256"] == t.sha256,
                 "HEAD_TRANSITION_CHANGED")
    return nt


def _record_locked(adapter: Any) -> CashComponentsResult:
    manager = adapter.cash_authority_manager
    store = adapter.cl7_ledger_store
    authority = manager.store._load_unlocked(allow_missing_legacy=False)
    _require(authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
             and manager.cash_source_version == 3 and store is not None,
             "AUTHORITY_OR_SOURCE_UNSUPPORTED")
    central = adapter.manager.state()
    intent = manager._recovery_intent_from_state(authority, central, identity_key=adapter.cl7_identity_key)
    own = adapter.cl7_own_funds_policy
    _require(own is not None, "METADATA_UNAVAILABLE")
    own.binding_guard()
    instrument = own.instruments.get(intent.candidate.instrument_id)
    proof = validate_exact_receipt_binding(intent, account_id=adapter.policy.account_id,
        identity_key=adapter.cl7_identity_key, identity_key_id=adapter.cl7_identity_key_id,
        instrument=instrument)
    start_tick, start_wall = adapter.cl7_monotonic_ns(), timestamp_ns(_time(adapter.cl7_clock()))
    _require(type(start_tick) is int and start_tick >= 0, "CLOCK_INVALID")
    last_tick, last_wall = start_tick, start_wall

    def guard() -> None:
        nonlocal last_tick, last_wall
        tick, wall = adapter.cl7_monotonic_ns(), timestamp_ns(_time(adapter.cl7_clock()))
        _require(type(tick) is int and last_tick <= tick <= start_tick + MAX_AGE_NS
                 and last_wall <= wall <= start_wall + MAX_AGE_NS, "OBSERVATION_STALE")
        last_tick, last_wall = tick, wall
        own.binding_guard()
        _require(adapter.cl7_own_funds_policy is own
                 and own.instruments.get(intent.candidate.instrument_id) == instrument
                 and adapter.manager.state() == central
                 and manager.store._load_unlocked(allow_missing_legacy=False) == authority,
                 "CUSTODY_CHANGED")

    guard()
    inspection = adapter._inspect_exact_order(intent)
    _require(inspection.status == "ORDER_OBSERVED" and inspection.exact_receipt is not None,
             "RECEIPT_UNAVAILABLE")
    receipt = inspection.exact_receipt
    settlement_outcome(receipt)
    _require(receipt.fee_status == "RECEIPT_COMMISSION_OBSERVED", "RECEIPT_OUT_OF_SCOPE")
    batch, rows = _collect(adapter, authority, receipt, guard)
    components = _components(adapter, intent, receipt, batch, rows)
    match = _match(receipt, components)
    plan_store = _PlanStore(adapter.manager.store.path.parent, proof.sha256, adapter.cl7_identity_key)
    plan = plan_store.load()
    had_plan = plan is not None
    store.validate()
    export = store.export_bytes()
    if plan is None:
        snapshot = json.loads(export)
        _require(int(snapshot["ledger_revision"]) == proof.ledger_revision
                 and snapshot["ledger_head_sha256"] == proof.ledger_head_sha256,
                 "PLAN_MISSING_FOR_CHANGED_LEDGER")
        baseline = json.loads(export)
        _require(not {t.sha256 for _, t in components} & {r["sha256"] for r in baseline["transactions"]}
                 and not {o.sha256 for o, _ in components} & {r["sha256"] for r in baseline["observations"]},
                 "BASELINE_CONTAINS_COMPONENTS")
        plan = {"domain": _DOMAIN, "version": 1, "match": match,
                "baseline_export_ascii": export.decode("ascii"), "first_watermark_sha256": batch.watermark.sha256}
    else:
        _require(plan["match"] == match, "PLAN_RECEIPT_OR_OPERATIONS_CHANGED")
        baseline = json.loads(plan["baseline_export_ascii"])
    baseline_projection = cl4.project_shadow_cash(plan["baseline_export_ascii"].encode("ascii"),
        account_scope_sha256=receipt.account_scope_sha256, environment=cl3.BrokerEnvironment.SANDBOX,
        as_of=_time(adapter.cl7_clock()), identity_key=adapter.cl7_identity_key)
    _require(baseline_projection.complete and baseline_projection.version == 3
             and baseline_projection.ledger_head_sha256 == proof.ledger_head_sha256
             and baseline_projection.ledger_revision == proof.ledger_revision,
             "BASELINE_INCOMPLETE_OR_CHANGED")
    already = _check_prefix(baseline, json.loads(export), components)
    guard()
    raw_cash = manager.read_accounting_cash(adapter.transport, adapter.policy.account_id,
        clock=adapter.cl7_clock, monotonic_ns=adapter.cl7_monotonic_ns)
    guard()
    now = _time(adapter.cl7_clock())
    cash_proof = cl4.build_broker_rub_position_cash_proof(raw_cash,
        raw_account_id=adapter.policy.account_id, account_scope_sha256=receipt.account_scope_sha256,
        environment=cl3.BrokerEnvironment.SANDBOX, as_of=now, evaluated_at=now, response_complete=True,
        identity_key=adapter.cl7_identity_key, identity_key_id=adapter.cl7_identity_key_id)
    _require(baseline_projection.expected_cash.minor_units + receipt.expected_cash_delta_nano
             == cash_proof.cash.minor_units, "BROKER_LEDGER_CASH_MISMATCH")
    guard()
    _require(store.export_bytes() == export, "LEDGER_CHANGED_DURING_READ")
    if plan_store.load() is None:
        plan_store.create(plan)
    _require(plan_store.load() == plan, "PLAN_CHANGED")
    for observation, transaction in components:
        guard()
        _require(plan_store.load() == plan, "PLAN_CHANGED")
        store.validate()
        checked = json.loads(store.export_bytes())
        _check_prefix(baseline, checked, components)
        store.append_observation(observation, expected_store_revision=int(checked["store_revision"]))
        guard()
        _require(plan_store.load() == plan, "PLAN_CHANGED")
        store.validate()
        checked = json.loads(store.export_bytes())
        _check_prefix(baseline, checked, components)
        store.append_transaction(transaction, observation.sha256,
            expected_store_revision=int(checked["store_revision"]),
            expected_ledger_revision=int(checked["ledger_revision"]))
    guard()
    store.validate()
    after = store.export_bytes()
    _require(_check_prefix(baseline, json.loads(after), components) == len(components), "COMPONENTS_INCOMPLETE")
    reconciliation = cl4.reconcile_shadow_cash(after, cash_proof,
        evaluated_at=_time(adapter.cl7_clock()), identity_key=adapter.cl7_identity_key)
    _require(reconciliation.status is cl4.ReconciliationStatus.MATCHED, "FINAL_RECONCILIATION_FAILED")
    guard()
    _require(plan_store.load() == plan and store.export_bytes() == after, "FINAL_STATE_CHANGED")
    final = json.loads(after)
    return CashComponentsResult(proof.sha256, receipt.receipt_identity_sha256, _sha(_canonical(plan)),
        batch.watermark.sha256, reconciliation.sha256, final["ledger_head_sha256"], int(final["ledger_revision"]),
        tuple(t.sha256 for _, t in components), receipt.gross_nano, receipt.executed_commission_nano,
        receipt.expected_cash_delta_nano, len(components) - already, had_plan and already == len(components))


def record_exact_cash_components(
    adapter: Any, *, expected_proof_sha256: str | None = None,
) -> CashComponentsResult:
    """Explicit cash-only recovery API. No Strategy, POST, CancelOrder or clear.

    Successful read-back proves the bounded component match at this ledger head,
    not fee finality, a broker-atomic snapshot, or release of trading authority.
    """
    try:
        with adapter.cash_authority_manager.store.locked():
            if expected_proof_sha256 is not None:
                _require(type(expected_proof_sha256) is str
                         and _HEX.fullmatch(expected_proof_sha256) is not None,
                         "EXPECTED_PROOF_INVALID")
                authority = adapter.cash_authority_manager.store._load_unlocked(
                    allow_missing_legacy=False)
                _require(authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
                         and authority.pending_dispatch_proof_sha256 == expected_proof_sha256,
                         "EXPECTED_PROOF_CHANGED")
            return _record_locked(adapter)
    except ExactCashSettlementError:
        raise
    except Exception:
        # Includes CL3, CL4, CL2, receipt, local persistence and provider failures.
        # Existing owner guards remain closed; never return a fabricated success.
        raise ExactCashSettlementError("EXACT_CASH_DEPENDENCY_FAILED") from None
