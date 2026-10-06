"""Bound full-fill evidence for a v4 cash batch; never an execution permit.

Only one full FILL and its zero/one separately linked fee are supported.
The ordinary CL3 decision remains REVIEW_REQUIRED for composite operations;
this named consumer supplies context without changing the generic decoder.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from . import cash_observation_versions as versions
from .central_order_manager import CentralOrderIntent
from .exact_cash_settlement import _components
from .exact_order_receipt import _decode_bound_order_receipt
from .exact_own_funds import OwnFundsEvidence, digest, timestamp_ns
from .runtime_cash_authority import derive_account_scope

DOMAIN = "V4_REQUEST_BOUND_FULL_FILL_V1"
FIELDS = {"domain", "version", "dispatch_plan_sha256", "intent", "instrument",
          "own_funds", "proof_evaluated_at", "observed_at", "order_state"}


class VersionedFillEvidenceError(RuntimeError):
    """Finite refusal; raw receipt and account IDs stay out of diagnostics."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise VersionedFillEvidenceError("V4_FILL_" + code)


def decode_bound_fill(value: Any, *, key: bytes, key_id: str, account_scope: str):
    _require(type(value) is dict and set(value) == FIELDS and value["domain"] == DOMAIN
             and type(value["version"]) is int and value["version"] == 1, "BINDING_SCHEMA_INVALID")
    versions._hash(value["dispatch_plan_sha256"])
    intent = CentralOrderIntent.from_dict(value["intent"])
    candidate = intent.candidate
    _require(intent.status in {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
             and intent.versioned_dispatch_plan_sha256 == value["dispatch_plan_sha256"]
             and intent.executed_lots == 0 and intent.outcome is None,
             "DISPATCH_INTENT_REQUIRED")
    _require(derive_account_scope(candidate.account_id, identity_key=key, identity_key_id=key_id)
             == account_scope, "ACCOUNT_MISMATCH")
    metadata = value["instrument"]
    _require(type(metadata) is dict and set(metadata) == {"instrument_id", "currency", "asset_class", "lot_size"},
             "METADATA_SCHEMA_INVALID")
    _require(metadata["instrument_id"] == candidate.instrument_id and metadata["currency"] == "RUB"
             and type(metadata["asset_class"]) is str and metadata["asset_class"].lower() in {"share", "etf"}
             and type(metadata["lot_size"]) is int and metadata["lot_size"] == candidate.lot_size
             and candidate.order_type == "MARKET"
             and candidate.time_in_force in {"FILL_AND_KILL", "FILL_OR_KILL"}, "REQUEST_PROFILE_UNSUPPORTED")
    own = OwnFundsEvidence.from_canonical_dict(value["own_funds"])
    _require(own.request_sha256 == digest({"domain": "CL7_OWN_FUNDS_REQUEST_V1", "intent_id": intent.intent_id,
             "candidate": candidate.to_dict(), "get_max_lots_price": None})
             and own.metadata_sha256 == digest(metadata)
             and (own.direction, own.requested_lots, own.lot_size, own.order_type, own.time_in_force)
                 == (candidate.direction, candidate.requested_lots, candidate.lot_size,
                     candidate.order_type, candidate.time_in_force), "OWN_PROOF_MISMATCH")
    own.check_age(value["proof_evaluated_at"])
    receipt = _decode_bound_order_receipt(value["order_state"], intent, account_id=candidate.account_id,
        identity_key=key, proof_sha256=value["dispatch_plan_sha256"], account_scope_sha256=account_scope,
        proof_evaluated_at=value["proof_evaluated_at"], observed_at=value["observed_at"])
    _require(receipt.provider_status == "FILL" and 0 < receipt.executed_lots == receipt.requested_lots,
             "FULL_FILL_REQUIRED")
    _require(receipt.fee_status == "RECEIPT_COMMISSION_OBSERVED", "FEES_UNRESOLVED")
    attempts = [t for t in intent.transitions if t.status == "IN_FLIGHT"]
    attempted = datetime.fromisoformat(attempts[0].at.replace("Z", "+00:00"))
    observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
    for tz in (timezone.utc, timezone(timedelta(hours=3))):
        _require(attempted.astimezone(tz).date() == observed.astimezone(tz).date(), "INTERDAY_UNSUPPORTED")
    return intent, SimpleNamespace(**metadata), receipt


def fill_transactions(value: dict, batch: Any, rows: list[dict], *, key: bytes, key_id: str,
                      account_scope: str) -> dict[str, Any]:
    """Join the exact *new* operations, not every row in the overlap window."""
    intent, instrument, receipt = decode_bound_fill(value, key=key, key_id=key_id, account_scope=account_scope)
    start, end = batch.watermark.from_inclusive, batch.watermark.to_exclusive
    _require(all(timestamp_ns(start) <= timestamp_ns(s.execution_at) < timestamp_ns(end)
                 for s in receipt.stages) and timestamp_ns(end) <= timestamp_ns(receipt.observed_at),
             "FILL_WINDOW_INCOMPLETE")
    # Explicit raw instrument binding is required for BOTH monetary components.
    _require(all(row.get("instrumentUid") == intent.candidate.instrument_id for row in rows),
             "OPERATION_INSTRUMENT_MISSING")
    _require(all(timestamp_ns(row["date"]) >= min(timestamp_ns(s.execution_at) for s in receipt.stages)
                 for row in rows), "OPERATION_PRECEDES_EXECUTION")
    adapter = SimpleNamespace(cl7_identity_key=key,
        cl7_own_funds_policy=SimpleNamespace(instruments={intent.candidate.instrument_id: instrument}))
    components = _components(adapter, intent, receipt, batch, rows)
    _require(len(components) == len(rows) == len(batch.decisions), "EXTRA_OPERATION")
    result = {observation.sha256: transaction for observation, transaction in components}
    _require(len(result) == len(components), "DUPLICATE_COMPONENT")
    return result
