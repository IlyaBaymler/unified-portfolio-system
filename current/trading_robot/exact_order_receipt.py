"""Request-bound GetOrderState economics; never settlement or execution authority.

OrderStage.quantity is lots and OrderStage.price is per security. The similarly
named executedOrderPrice has a different meaning in PostOrder and GetOrderState;
it, initial prices and totalOrderAmount are not execution-price fallbacks here.
All economic arithmetic uses integer nanoroubles. Missing fees stay unknown.
This module performs no I/O and cannot append ledger entries or clear authority.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .broker_read_adapters import normalize_provider_timestamp
from .central_order_manager import CentralOrderIntent
from .exact_own_funds import digest, timestamp_ns
from .runtime_cash_authority import LockedDispatchProof, derive_account_scope

_DOMAIN = "CL7_EXACT_ORDER_RECEIPT_V1"
_MAX_NANO = 10**21  # Bounded non-margin RUB SHARE/ETF profile, like own-funds v1.
_UINT = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_STATUSES = frozenset({"NEW", "FILL", "PARTIALLYFILL", "CANCELLED", "REJECTED"})


class ExactOrderReceiptError(RuntimeError):
    """Finite codes only; no raw account, request, trade or provider diagnostics."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ExactOrderReceiptError("EXACT_RECEIPT_" + code)


def _uint(value: Any) -> int:
    _require(type(value) is int or (type(value) is str and _UINT.fullmatch(value) is not None),
             "QUANTITY_INVALID")
    number = int(value)
    _require(0 <= number <= 2**63 - 1, "QUANTITY_INVALID")
    return number


def _id(value: Any) -> str:
    _require(type(value) is str and 0 < len(value) <= 128 and value.strip() == value
             and all(32 <= ord(ch) < 127 for ch in value), "IDENTITY_INVALID")
    return value


def _money(value: Any) -> int:
    _require(type(value) is dict and set(value) <= {"currency", "units", "nano"}
             and value.get("currency") in ("rub", "RUB"), "MONEY_INVALID")
    # Missing scalar protobuf fields are their documented zero value. A missing
    # MoneyValue message is not zero: callers must preserve its absence instead.
    units = _uint(value.get("units", "0"))
    nano = value.get("nano", 0)
    _require(type(nano) is int and 0 <= nano < 10**9, "MONEY_INVALID")
    result = units * 10**9 + nano
    _require(result <= _MAX_NANO, "MONEY_OUT_OF_SCOPE")
    return result


def _time(value: Any) -> str:
    _require(type(value) is str and len(value) <= 40, "TIME_INVALID")
    if value.endswith("+00:00"):
        value = value[:-6] + "Z"
    try:
        result = normalize_provider_timestamp(value)
        timestamp_ns(result)
    except Exception:
        raise ExactOrderReceiptError("EXACT_RECEIPT_TIME_INVALID") from None
    return result


def _canonical(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ExactOrderReceiptError("EXACT_RECEIPT_PAYLOAD_INVALID") from None
    _require(len(raw) <= 262_144, "PAYLOAD_TOO_LARGE")
    return raw


def _keyed(key: bytes, value: Any) -> str:
    return hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()


def validate_exact_receipt_binding(
    intent: CentralOrderIntent, *, account_id: str, identity_key: bytes,
    identity_key_id: str, instrument: Any,
) -> LockedDispatchProof:
    """Validate the saved request/proof/metadata before a caller reads a receipt.

    This is a local integrity binding, not proof of a real broker execution.
    Caller must separately verify pending authority and current Central custody.
    """
    _require(type(intent) is CentralOrderIntent and intent.status in
             {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}, "INTENT_INVALID")
    _require(type(identity_key) is bytes and 32 <= len(identity_key) <= 64,
             "KEY_INVALID")
    c = intent.candidate
    _require(c.account_id == account_id, "ACCOUNT_MISMATCH")
    _require(instrument is not None and instrument.instrument_id == c.instrument_id
             and instrument.currency in ("rub", "RUB")
             and type(instrument.asset_class) is str
             and instrument.asset_class.lower() in {"share", "etf"}
             and type(instrument.lot_size) is int and instrument.lot_size == c.lot_size,
             "METADATA_MISMATCH")
    try:
        proof = LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof)
        proof.verify_identity(raw_intent_id=intent.intent_id, identity_key=identity_key)
        account_scope = derive_account_scope(account_id, identity_key=identity_key, identity_key_id=identity_key_id)
    except Exception:
        raise ExactOrderReceiptError("EXACT_RECEIPT_PROOF_INVALID") from None
    _require(proof.version == 2 and proof.own_funds_evidence is not None,
             "PROOF_VERSION_UNSUPPORTED")
    own = proof.own_funds_evidence
    _require(proof.account_scope_sha256 == account_scope
             and proof.identity_key_id == identity_key_id
             and proof.current_lots == c.current_lots and proof.target_lots == c.target_lots
             and proof.direction == c.direction, "PROOF_BINDING_MISMATCH")
    _require(own.request_sha256 == digest({"domain": "CL7_OWN_FUNDS_REQUEST_V1",
             "intent_id": intent.intent_id, "candidate": c.to_dict(), "get_max_lots_price": None})
             and own.metadata_sha256 == digest({"instrument_id": c.instrument_id,
                 "currency": instrument.currency, "asset_class": instrument.asset_class,
                 "lot_size": instrument.lot_size})
             and own.direction == c.direction and own.requested_lots == c.requested_lots
             and own.lot_size == c.lot_size and own.order_type == c.order_type
             and own.time_in_force == c.time_in_force, "PROOF_BINDING_MISMATCH")
    return proof


@dataclass(frozen=True, slots=True)
class ExactReceiptStage:
    trade_scope_sha256: str
    lots: int
    price_nano: int
    execution_at: str

    def to_dict(self) -> dict[str, object]:
        return {"trade_scope_sha256": self.trade_scope_sha256, "lots": str(self.lots),
                "price_nano": str(self.price_nano), "execution_at": self.execution_at}


@dataclass(frozen=True, slots=True)
class ExactOrderReceipt:
    proof_sha256: str
    account_scope_sha256: str
    order_scope_sha256: str
    direction: str
    provider_status: str
    requested_lots: int
    executed_lots: int
    lot_size: int
    stages: tuple[ExactReceiptStage, ...]
    executed_commission_nano: int | None
    service_commission_nano: int | None
    observed_at: str
    receipt_identity_sha256: str

    @property
    def terminal(self) -> bool:
        return self.provider_status in {"FILL", "CANCELLED", "REJECTED"}

    @property
    def gross_nano(self) -> int:
        return sum(stage.price_nano * stage.lots * self.lot_size for stage in self.stages)

    @property
    def average_price_rub(self) -> Fraction | None:
        if not self.executed_lots:
            return None
        return Fraction(self.gross_nano, self.executed_lots * self.lot_size * 10**9)

    @property
    def fee_status(self) -> str:
        if self.executed_commission_nano is None:
            return "EXECUTED_COMMISSION_UNKNOWN"
        if self.service_commission_nano is None:
            return "SERVICE_COMMISSION_UNKNOWN"
        if self.service_commission_nano:
            # Do not guess whether service commission overlaps the total fee.
            return "SERVICE_COMMISSION_OUT_OF_SCOPE"
        return "RECEIPT_COMMISSION_OBSERVED"

    @property
    def expected_cash_delta_nano(self) -> int | None:
        if self.fee_status != "RECEIPT_COMMISSION_OBSERVED":
            return None
        signed_gross = -self.gross_nano if self.direction == "BUY" else self.gross_nano
        return signed_gross - self.executed_commission_nano

    def to_canonical_dict(self) -> dict[str, object]:
        weighted = sum(stage.price_nano * stage.lots for stage in self.stages)
        return {
            "domain": _DOMAIN, "version": 1, "proof_sha256": self.proof_sha256,
            "account_scope_sha256": self.account_scope_sha256,
            "order_scope_sha256": self.order_scope_sha256, "direction": self.direction,
            "provider_status": self.provider_status, "terminal": self.terminal,
            "requested_lots": str(self.requested_lots), "executed_lots": str(self.executed_lots),
            "lot_size": str(self.lot_size), "executed_units": str(self.executed_lots * self.lot_size),
            "gross_rub_nano": str(self.gross_nano),
            "average_price": None if not self.executed_lots else {
                "weighted_nano": str(weighted), "lots_denominator": str(self.executed_lots)},
            "stages": [stage.to_dict() for stage in self.stages],
            "executed_commission_nano": None if self.executed_commission_nano is None
                else str(self.executed_commission_nano),
            "service_commission_nano": None if self.service_commission_nano is None
                else str(self.service_commission_nano),
            "fee_status": self.fee_status,
            "expected_cash_delta_nano": None if self.expected_cash_delta_nano is None
                else str(self.expected_cash_delta_nano),
            "observed_at": self.observed_at, "receipt_identity_sha256": self.receipt_identity_sha256,
            "settlement_verified": False, "authority_clear_allowed": False,
        }


def decode_exact_order_receipt(
    raw: Any, intent: CentralOrderIntent, *, account_id: str, identity_key: bytes,
    identity_key_id: str, instrument: Any, observed_at: str,
) -> ExactOrderReceipt:
    """Decode only GetOrderState, not PostOrder, stream trades or account cash.

    Exact figures are receipt-derived expectations for a future CL3/CL2 match.
    Even an internally consistent full fill is not evidence of cash settlement.
    """
    proof = validate_exact_receipt_binding(intent, account_id=account_id,
        identity_key=identity_key, identity_key_id=identity_key_id, instrument=instrument)
    return _decode_bound_order_receipt(raw, intent, account_id=account_id,
        identity_key=identity_key, proof_sha256=proof.sha256,
        account_scope_sha256=proof.account_scope_sha256,
        proof_evaluated_at=proof.evaluated_at, observed_at=observed_at)


def _decode_bound_order_receipt(raw: Any, intent: CentralOrderIntent, *, account_id: str,
        identity_key: bytes, proof_sha256: str, account_scope_sha256: str,
        proof_evaluated_at: str, observed_at: str) -> ExactOrderReceipt:
    """Pure receipt economics after a caller validates its own dispatch binding.

    Private shared decoder, never execution/settlement authority. The v1 public
    entry retains its proof validation. The separate versioned consumer proves
    the immutable v4 request before using this arithmetic; no v1 proof is forged.
    """
    c = intent.candidate
    _require(type(raw) is dict, "PAYLOAD_INVALID")
    _canonical(raw)
    _require(_id(raw.get("orderRequestId")) == intent.intent_id
             and _id(raw.get("instrumentUid")) == c.instrument_id
             and raw.get("direction") == "ORDER_DIRECTION_" + c.direction
             and ("accountId" not in raw or raw["accountId"] == account_id)
             and ("brokerAccountId" not in raw or raw["brokerAccountId"] == account_id),
             "IDENTITY_MISMATCH")
    exchange = _id(raw.get("orderId"))
    _require(intent.broker_order_id is None or exchange == intent.broker_order_id,
             "IDENTITY_MISMATCH")
    _require(raw.get("currency") in ("rub", "RUB"), "CURRENCY_INVALID")
    _require(raw.get("orderType") == "ORDER_TYPE_" + c.order_type, "ORDER_TYPE_MISMATCH")
    requested, executed = _uint(raw.get("lotsRequested")), _uint(raw.get("lotsExecuted"))
    _require(requested == c.requested_lots and executed <= requested, "QUANTITY_MISMATCH")
    status_raw = raw.get("executionReportStatus")
    _require(type(status_raw) is str and status_raw in
             {"EXECUTION_REPORT_STATUS_" + s for s in _STATUSES}, "STATUS_INVALID")
    status = status_raw.removeprefix("EXECUTION_REPORT_STATUS_")
    _require((status == "FILL" and executed == requested) or
             (status in {"NEW", "REJECTED"} and executed == 0) or
             (status == "PARTIALLYFILL" and 0 < executed < requested) or
             (status == "CANCELLED" and executed < requested), "STATUS_QUANTITY_CONFLICT")
    attempts = [t for t in intent.transitions if t.status == "IN_FLIGHT"]
    _require(len(attempts) == 1, "ATTEMPT_LINEAGE_INVALID")
    earliest, now = _time(attempts[0].at), _time(observed_at)
    _require(_time(proof_evaluated_at) <= earliest <= now, "ATTEMPT_TIME_INVALID")
    raw_stages = raw.get("stages", [])
    _require(type(raw_stages) is list and len(raw_stages) <= 128, "STAGES_INVALID")
    seen: set[str] = set()
    stages = []
    for stage in raw_stages:
        _require(type(stage) is dict, "STAGE_INVALID")
        trade = _id(stage.get("tradeId"))
        _require(trade not in seen, "DUPLICATE_TRADE")
        seen.add(trade)
        lots, price = _uint(stage.get("quantity")), _money(stage.get("price"))
        _require(0 < lots <= executed and price > 0, "STAGE_QUANTITY_PRICE_INVALID")
        at = _time(stage.get("executionTime"))
        _require(earliest <= at <= now, "TRADE_TIME_INVALID")
        trade_scope = _keyed(identity_key, {"domain": _DOMAIN + "_TRADE",
            "account_scope_sha256": account_scope_sha256, "trade_id": trade})
        stages.append(ExactReceiptStage(trade_scope, lots, price, at))
    _require(sum(s.lots for s in stages) == executed, "STAGE_COVERAGE_MISMATCH")
    gross = sum(s.price_nano * s.lots * c.lot_size for s in stages)
    _require(gross <= _MAX_NANO, "MONEY_OUT_OF_SCOPE")
    if "averagePositionPrice" in raw:
        average = _money(raw["averagePositionPrice"])
        if executed:
            # One nanorouble per security is the bounded wire rounding error.
            weighted = sum(s.price_nano * s.lots for s in stages)
            _require(abs(average * executed - weighted) <= executed, "AVERAGE_PRICE_MISMATCH")
    commission = _money(raw["executedCommission"]) if "executedCommission" in raw else None
    service = _money(raw["serviceCommission"]) if "serviceCommission" in raw else None
    # A terminal zero-fill may carry an independently observed fee. The
    # receipt alone never books it or authorizes closure; the cash consumer
    # requires a matching fee operation. Active NEW retains the old boundary.
    _require(executed > 0 or status in {"CANCELLED", "REJECTED"}
             or (commission in (None, 0) and service in (None, 0)),
             "ZERO_FILL_FEE_OUT_OF_SCOPE")
    if commission is not None:
        _require(gross + commission <= _MAX_NANO, "MONEY_OUT_OF_SCOPE")
    order_scope = _keyed(identity_key, {"domain": _DOMAIN + "_ORDER",
        "account_scope_sha256": account_scope_sha256, "order_id": exchange})
    stages_tuple = tuple(sorted(stages, key=lambda s: s.trade_scope_sha256))
    # Receipt identity is stable across a later identical observation and stage
    # permutation; timing of the read remains explicit outside that identity.
    identity = _keyed(identity_key, {"domain": _DOMAIN, "proof_sha256": proof_sha256,
        "order_scope_sha256": order_scope, "provider_status": status,
        "stages": [s.to_dict() for s in stages_tuple], "commission": commission,
        "service_commission": service})
    return ExactOrderReceipt(proof_sha256, account_scope_sha256, order_scope,
        c.direction, status, requested, executed, c.lot_size, stages_tuple,
        commission, service, now, identity)
