"""Version-aware financial *review* of a detached monetary journal.

CL4-style reconciliation, CL5-style availability and CL6-style owner bindings
are recomputed over the complete v3 history, never a stripped v1 export. The
result is NOT a PortfolioRiskCashContext/LockedDispatchProof and cannot arm,
clear an authority record or grant dispatch. Runtime cutover remains separate.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from . import cash_availability as cl5
from . import cash_buying_availability as buying
from . import cash_ledger_opening_reconciliation as cl4
from . import cash_observation_versions as versions
from . import reporting_risk_cash_context as cl6
from . import versioned_fee_corrections as journal
from .broker_read_adapters import BrokerEnvironment
from .cash_ledger_domain import LedgerAccount, LedgerTransaction
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .versioned_fee_evidence import _canonical, _parse, _sha

if TYPE_CHECKING:
    from .versioned_operational_store import OperationalPins

DOMAIN = "CL4_CL6_VERSIONED_FINANCIAL_REVIEW_V1"
PROJECTION_DOMAIN = "CL4_VERSIONED_CASH_PROJECTION_V1"


class VersionedFinancialReadError(RuntimeError):
    """Finite messages; raw exports/owner evidence are private."""


def _require(value: bool, reason: str) -> None:
    if not value:
        raise VersionedFinancialReadError("VERSIONED_READ_" + reason)


@dataclass(frozen=True, slots=True)
class VersionedReadPins:
    """Caller pins must be kept independently of the export to detect rollback."""
    export_sha256: str
    review_export_sha256: str
    observed_version_head_sha256: str
    correction_head_sha256: str
    ledger_head_sha256: str
    ledger_revision: int
    store_revision: int

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name.endswith("sha256"):
                versions._hash(value)
            else:
                _require(type(value) is int and 0 <= value < 2**63, "PIN_INVALID")

    def to_dict(self) -> dict[str, str]:
        return {f.name: str(getattr(self, f.name)) for f in fields(self)}


def _checked(raw: bytes, pins: VersionedReadPins, registry: Any, key: bytes,
             key_id: str, account: str) -> Any:
    _require(type(pins) is VersionedReadPins, "PIN_INVALID")
    pins = VersionedReadPins(**{f.name: getattr(pins, f.name) for f in fields(pins)})
    _require(type(raw) is bytes and _sha(raw) == pins.export_sha256, "EXPORT_PIN_MISMATCH")
    checked = journal._validate_export(raw, versions._registry(registry), versions._key(key),
        versions._key_id(key_id), versions._hash(account),
        expected_review_export_sha256=pins.review_export_sha256,
        expected_correction_head_sha256=pins.correction_head_sha256)
    snapshot = checked.snapshot
    _require(checked.record is not None and snapshot.correction_recorded
             and snapshot.financial_ready is False and snapshot.runtime_cutover_performed is False,
             "COMPLETED_CORRECTION_REQUIRED")
    for f in fields(pins):
        _require(getattr(snapshot, f.name) == getattr(pins, f.name), "JOURNAL_PIN_MISMATCH")
    return checked


@dataclass(frozen=True, slots=True)
class VersionedCashProjection:
    pins: VersionedReadPins
    account_scope_sha256: str
    identity_key_id: str
    baseline_cash_nano: int
    correction_delta_nano: int
    expected_cash_nano: int
    transaction_count: int
    provenance_sha256: str
    economic_capture_at: str

    def to_canonical_dict(self) -> dict[str, Any]:
        return {"domain": PROJECTION_DOMAIN, "version": 1, "source_export_version": 3,
            "pins": self.pins.to_dict(), "account_scope_sha256": self.account_scope_sha256,
            "identity_key_id": self.identity_key_id, "currency": "RUB",
            "baseline_cash_nano": str(self.baseline_cash_nano),
            "correction_delta_nano": str(self.correction_delta_nano),
            "expected_cash_nano": str(self.expected_cash_nano),
            "transaction_count": self.transaction_count,
            "provenance_sha256": self.provenance_sha256,
            "economic_capture_at": self.economic_capture_at,
            "financial_ready": False, "runtime_authority_granted": False}

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha(self.canonical_bytes)


def project_versioned_cash(raw: bytes, *, pins: VersionedReadPins,
        codec_registry: object, identity_key: bytes, identity_key_id: str,
        account_scope_sha256: str) -> VersionedCashProjection:
    """Revalidate versions/economics/provenance, then sum all money exactly."""
    try:
        c = _checked(raw, pins, codec_registry, identity_key, identity_key_id, account_scope_sha256)
        # The complete v3 validator has already verified the v1 graph and both
        # CL1 bundle transactions. No forged v1 export/provenance is constructed.
        base = _parse(c.graph.base)
        def effect(tx: LedgerTransaction) -> int:
            return sum(p.money.minor_units for p in tx.postings
                       if p.account is LedgerAccount.ASSET_BROKER_CASH)
        transactions = [LedgerTransaction.from_canonical_dict(_parse(row["canonical_json_ascii"]))
                        for row in base["transactions"]]
        baseline = sum(effect(tx) for tx in transactions)
        correction = [LedgerTransaction.from_canonical_dict(_parse(c.record[n]))
                      for n in ("reversal_json_ascii", "correction_json_ascii")]
        delta = sum(effect(tx) for tx in correction)
        _require(baseline + delta == c.snapshot.cash_nano, "CASH_PROJECTION_MISMATCH")
        _require(len(transactions) + len(correction) == c.snapshot.transaction_count,
                 "TRANSACTION_COUNT_MISMATCH")
        capture = _parse(c.record["capture_json_ascii"])
        return VersionedCashProjection(pins, account_scope_sha256, identity_key_id,
            baseline, delta, baseline + delta, len(transactions) + 2,
            _sha(_canonical(c.record["provenance"])), capture["captured_at"])
    except VersionedFinancialReadError:
        raise
    except Exception:
        raise VersionedFinancialReadError("VERSIONED_READ_EXPORT_INVALID") from None


@dataclass(frozen=True, slots=True)
class VersionedFinancialContext:
    """Immutable authenticated read result, deliberately no execution interface."""
    payload_bytes: bytes
    identity_sha256: str

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical({"payload": _parse(self.payload_bytes),
                           "hmac_sha256": self.identity_sha256})

    @property
    def sha256(self) -> str:
        return _sha(self.canonical_bytes)

    def to_canonical_dict(self) -> dict[str, Any]:
        """Contains monetary amounts; this full financial report is private."""
        return _parse(self.canonical_bytes)

    def public_summary(self) -> dict[str, Any]:
        body = _parse(self.payload_bytes)
        return {"domain": DOMAIN + "_SUMMARY", "version": 1,
            "report_sha256": self.sha256, "status": body["status"],
            "reason": body["reason"], "cl7_status": "BLOCKED_CUTOVER_REQUIRED",
            "runtime_authority_granted": False, "financial_ready": False}


def _build(raw: bytes, *, pins: VersionedReadPins | OperationalPins, codec_registry: object,
        identity_key: bytes, identity_key_id: str, account_scope_sha256: str,
        broker_cash: cl4.BrokerCashProof, own_buying: buying.OwnBuyingCashProof,
        reservations: cl5.CentralReservationProjection,
        portfolio: cl6.PortfolioIdentityEvidence, risk_guard: cl6.RiskGuardEvidence,
        buying_scope_sha256: str, evaluated_at: str,
        current_economic_evidence_sha256: str | None = None) -> VersionedFinancialContext:
    key = versions._key(identity_key)
    key_id = versions._key_id(identity_key_id)
    scope = versions._hash(account_scope_sha256)
    # v4 keeps the entire validated v3 prefix plus ordinary CL3 batches. Never
    # strip the prefix into a fabricated v1 export or omit subsequent postings.
    from .versioned_operational_store import OperationalPins, project_operational_cash
    source_version = 4 if type(pins) is OperationalPins else 3
    projector = project_operational_cash if source_version == 4 else project_versioned_cash
    projection = projector(raw, pins=pins, codec_registry=codec_registry,
        identity_key=key, identity_key_id=key_id, account_scope_sha256=scope)
    _require(type(broker_cash) is cl4.BrokerCashProof and broker_cash.version == 3,
             "RUB_POSITION_PROOF_REQUIRED")
    cash = cl4._validate_proof(broker_cash, key, evaluated_at=evaluated_at)
    own = buying.validate_own_buying_proof(own_buying, key, buying_scope_sha256=buying_scope_sha256)
    res = cl5._validated_projection(reservations, key)
    _require(type(portfolio) is cl6.PortfolioIdentityEvidence
             and type(risk_guard) is cl6.RiskGuardEvidence, "OWNER_EVIDENCE_INVALID")
    port = cl6._validated_portfolio_evidence(portfolio, key)
    risk = cl6._validated_risk_evidence(risk_guard, key)
    dependencies = (cash, own, res, port, risk)
    _require(all(d.account_scope_sha256 == scope and d.identity_key_id == key_id
                 and d.environment is BrokerEnvironment.SANDBOX for d in dependencies),
             "EVIDENCE_IDENTITY_MISMATCH")
    now = timestamp_ns(evaluated_at)
    times = tuple(timestamp_ns(t) for t in (cash.as_of, own.as_of, own.completed_at,
        res.evaluated_at, port.portfolio_snapshot_at, port.captured_at, risk.captured_at))
    _require(now >= timestamp_ns(projection.economic_capture_at), "JOURNAL_FROM_FUTURE")
    _require(all(t <= now for t in times), "EVIDENCE_FROM_FUTURE")
    if current_economic_evidence_sha256 is not None:
        versions._hash(current_economic_evidence_sha256)
    diff = cash.cash.minor_units - projection.expected_cash_nano
    lower = min(cash.cash.minor_units, min(own.own_money_nano))
    free = lower - res.queued_reserved_cash.minor_units
    port_ready = (port.portfolio_schema_version == 2 and port.portfolio_source == "CANONICAL"
        and port.migration_status == "COMPLETED" and port.legacy_read_path_enabled is False
        and port.freshness == "FRESH" and port.blocking is False
        and port.state_status not in {"BLOCKED", "MANUAL_REVIEW_REQUIRED"})
    # BLOCKED is distinct from a mathematical match. A valid HMAC never grants
    # freshness, finality, an exact execution proof or a runtime migration.
    if any(now - t > MAX_AGE_NS for t in times):
        reason = "EVIDENCE_STALE"
    elif max(times) - min(times) > MAX_AGE_NS:
        reason = "MIXED_EVIDENCE_SNAPSHOT"
    elif diff:
        reason = "VERSIONED_CASH_MISMATCH"
    elif any(n > projection.expected_cash_nano for n in own.own_money_nano):
        reason = "OWN_BUDGET_EXCEEDS_ACCOUNTING"
    elif res.ambiguous_count:
        reason = "CENTRAL_PROVIDER_OVERLAP_UNKNOWN"
    elif free < 0:
        reason = "INSUFFICIENT_AFTER_RESERVATIONS"
    elif not port_ready:
        reason = "PORTFOLIO_NOT_READY"
    else:
        reason = "VALIDATED_FOR_REVIEW_ONLY"
    ready = reason == "VALIDATED_FOR_REVIEW_ONLY"
    body = {"domain": DOMAIN if source_version == 3 else "CL4_CL6_OPERATIONAL_V4_REVIEW_V1",
        "version": 1, "source_export_version": source_version,
        "status": "CONSISTENT_REVIEW_ONLY" if ready else "BLOCKED",
        "reason": reason, "cl4_status": "MATCHED" if diff == 0 else "MISMATCHED",
        "cl5_status": "COMPUTED_REVIEW_ONLY" if ready else "BLOCKED",
        "cl6_status": "BOUND_REVIEW_ONLY" if ready else "BLOCKED",
        "cl7_status": "BLOCKED_CUTOVER_REQUIRED", "evaluated_at": evaluated_at,
        "account_scope_sha256": scope, "identity_key_id": key_id, "currency": "RUB",
        "pins": pins.to_dict(), "projection_sha256": projection.sha256,
        "provenance_sha256": projection.provenance_sha256,
        "expected_cash_nano": str(projection.expected_cash_nano),
        "broker_cash_nano": str(cash.cash.minor_units), "difference_nano": str(diff),
        "own_money_nano": [str(n) for n in own.own_money_nano],
        "own_lower_bound_nano": str(lower), "queued_reserved_nano": str(res.queued_reserved_cash.minor_units),
        "ambiguous_reserved_nano": str(res.ambiguous_reserved_cash.minor_units),
        "ambiguous_count": res.ambiguous_count,
        "free_cash_nano": str(free) if ready else None,
        "broker_cash_proof_sha256": cash.sha256, "own_buying_proof_sha256": own.sha256,
        "buying_scope_sha256": own.buying_scope_sha256,
        "central_projection_sha256": res.sha256, "central_order_revision": str(res.central_order_revision),
        "reservation_projection_hash": res.central_reservation_projection_hash,
        "portfolio_evidence_sha256": port.sha256, "portfolio_revision": str(port.portfolio_revision),
        "portfolio_document_checksum": port.portfolio_document_checksum,
        "risk_guard_evidence_sha256": risk.sha256, "risk_policy_hash": risk.risk_policy_hash,
        "risk_state_guard_hash": risk.risk_state_guard_hash,
        "current_economic_evidence_sha256": current_economic_evidence_sha256,
        "runtime_authority_granted": False, "runtime_cutover_performed": False,
        "financial_ready": False, "request_bound_execution_proof": False,
        "future_fee_finality_claimed": False}
    payload = _canonical(body)
    return VersionedFinancialContext(payload, hmac.new(key, payload, hashlib.sha256).hexdigest())


def build_versioned_cash_context(raw: bytes, **kwargs: Any) -> VersionedFinancialContext:
    """Pure reader. Rebuilds all dependencies, no provider IO or state writes.

    This does not claim current operations completeness. Only the capture path
    additionally re-reads and economically matches the full provider window.
    """
    try:
        _require(kwargs.get("current_economic_evidence_sha256") is None,
                 "CAPTURE_BINDING_INTERNAL_ONLY")
        return _build(raw, **kwargs)
    except VersionedFinancialReadError:
        raise
    except Exception:
        raise VersionedFinancialReadError("VERSIONED_READ_DEPENDENCY_INVALID") from None


def validate_versioned_cash_context(value: VersionedFinancialContext, raw: bytes,
        **kwargs: Any) -> VersionedFinancialContext:
    """Recompute exact as-of content AND current freshness; HMAC alone is insufficient."""
    try:
        _require(type(value) is VersionedFinancialContext, "CONTEXT_TYPE_INVALID")
        rebuilt = build_versioned_cash_context(raw, **kwargs)
        _require(hmac.compare_digest(value.canonical_bytes, rebuilt.canonical_bytes),
                 "CONTEXT_CONTENT_MISMATCH")
        return rebuilt
    except VersionedFinancialReadError:
        raise
    except Exception:
        raise VersionedFinancialReadError("VERSIONED_READ_CONTEXT_INVALID") from None


def require_versioned_runtime_authority(value: object) -> None:
    """No boolean, HMAC report or detached v3 journal grants CL7 authority."""
    raise VersionedFinancialReadError("VERSIONED_READ_RUNTIME_CUTOVER_REQUIRED")
