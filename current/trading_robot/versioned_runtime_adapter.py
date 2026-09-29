"""Pinned v4 store adapter and lock-scoped request binding for CL7 integration.

The binding is NOT a LockedDispatchProof. It proves which complete cash graph
and request/evidence were checked during one live writer lease, not admission,
cutover, broker finality or permission to POST. Source owners are not mutated.
Trusted pins remain the caller's independently persisted responsibility.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cash_availability as cl5
from . import cash_observation_versions as versions
from . import versioned_financial_readers as readers
from . import versioned_operational_store as operational
from .broker_read_adapters import BrokerEnvironment, BrokerReadRequest
from .central_order_manager import CentralOrderIntent, CentralOrderState
from .exact_own_funds import MAX_AGE_NS, LockedOwnFundsPolicy, OwnFundsEvidence, digest, timestamp_ns
from .versioned_fee_evidence import _canonical, _parse, _sha

DOMAIN = "CL7_VERSIONED_REQUEST_BINDING_V1"


class VersionedRuntimeBindingError(RuntimeError):
    """Finite error only; full binding/evidence contains private financial data."""


def _require(value: bool, reason: str) -> None:
    if not value:
        raise VersionedRuntimeBindingError("V4_BINDING_" + reason)


def intent_fingerprint(intent: CentralOrderIntent) -> str:
    _require(type(intent) is CentralOrderIntent, "INTENT_INVALID")
    checked = CentralOrderIntent.from_dict(intent.to_dict())
    return _sha(_canonical(checked.to_dict()))


@dataclass(frozen=True, slots=True)
class VersionedRequestBinding:
    """Serialized identity is useful for audit, NOT usable outside its lease."""
    payload_bytes: bytes
    signature_sha256: str

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical({"payload": _parse(self.payload_bytes), "hmac_sha256": self.signature_sha256})

    @property
    def sha256(self) -> str:
        return _sha(self.canonical_bytes)

    def public_summary(self) -> dict[str, Any]:
        # Do not echo unchecked strings or quantities from the payload.
        return {"domain": DOMAIN + "_SUMMARY", "binding_sha256": self.sha256,
                "status": "REQUEST_BOUND_CUTOVER_REQUIRED", "runtime_authority_granted": False}


@dataclass(frozen=True, slots=True)
class VersionedRuntimeStoreAdapter:
    """Explicit adapter, not a type-spoofed v1 CashLedgerStore.

    Sync returns new pins but never silently adopts them. After a successful
    sync a new adapter must be constructed from independently persisted pins.
    An exception after commit requires inspecting the immutable v4 batch before
    advancing that trusted pin; blind snapshot().pins acceptance is not recovery.
    """
    store: operational.VersionedOperationalStore
    pins: operational.OperationalPins

    def __post_init__(self) -> None:
        _require(type(self.store) is operational.VersionedOperationalStore, "STORE_TYPE_INVALID")
        _require(type(self.pins) is operational.OperationalPins, "PIN_INVALID")
        self.store.snapshot(pins=self.pins)

    @property
    def root(self) -> Path:
        return self.store.root

    def snapshot(self) -> operational.OperationalSnapshot:
        return self.store.snapshot(pins=self.pins)

    def export_bytes(self) -> bytes:
        raw = self.store.export_bytes()
        operational.validate_operational_export(raw, pins=self.pins,
            codec_registry=tuple(self.store._registry.values()), identity_key=self.store._key,
            identity_key_id=self.store._key_id, account_scope_sha256=self.store._account)
        return raw

    @contextmanager
    def locked_snapshot(self, *, monotonic_ns: Callable[[], int] = time.monotonic_ns) -> Iterator[operational.LockedOperationalView]:
        with self.store.locked_snapshot(expected_pins=self.pins, monotonic_ns=monotonic_ns) as view:
            yield view

    def sync_tbank_operations(self, request: BrokerReadRequest, *, recorded_at: str,
                              read_rub_positions: Callable[[str], Any]) -> operational.OperationalSyncResult:
        # The ordinary source writer verifies the complete CL3 window and RUB.
        # Reuse its exact CAS; do not build caller-controlled transactions here.
        return self.store.sync_tbank_operations(request, expected_pins=self.pins,
            recorded_at=recorded_at, read_rub_positions=read_rub_positions)

    def _check_view(self, view: operational.LockedOperationalView) -> None:
        _require(type(view) is operational.LockedOperationalView, "LIVE_VIEW_REQUIRED")
        view.assert_active()
        _require(not self.store._closed, "STORE_CLOSED")
        _require(view.pins == self.pins and view._root == self.store.root
            and view._account == self.store._account and view._key_id == self.store._key_id
            and view._key == self.store._key, "VIEW_BINDING_MISMATCH")

    def build_request_binding(self, view: operational.LockedOperationalView, *,
            intent: CentralOrderIntent, central_state: CentralOrderState,
            expected_intent_sha256: str, own_policy: LockedOwnFundsPolicy,
            own_funds: OwnFundsEvidence, broker_cash, own_buying, portfolio,
            risk_guard, buying_scope_sha256: str, evaluated_at: str) -> VersionedRequestBinding:
        """Recompute cash/owner relations and bind a specific queued request.

        Inputs must come from independently locked/current owners at integration.
        This bounded API authenticates/recomputes supplied evidence; it does not
        claim to acquire their locks, invoke Risk admission or read the broker.
        """
        try:
            self._check_view(view)
            versions._hash(expected_intent_sha256)
            _require(intent_fingerprint(intent) == expected_intent_sha256, "INTENT_CHANGED")
            _require(type(central_state) is CentralOrderState, "CENTRAL_INVALID")
            central = CentralOrderState.from_dict(central_state.to_dict())
            candidate = intent.candidate
            _require(central.account_id == candidate.account_id and intent.status == "QUEUED"
                and central.blocking_intent is None and bool(central.queued)
                and central.queued[0] == intent, "QUEUE_HEAD_MISMATCH")
            _require(type(own_policy) is LockedOwnFundsPolicy
                and own_policy.account_id == candidate.account_id, "OWN_POLICY_INVALID")
            own_policy.binding_guard()
            row = own_policy.instruments.get(candidate.instrument_id)
            _require(row is not None and row.lot_size == candidate.lot_size,
                     "METADATA_MISMATCH")
            _require(type(own_funds) is OwnFundsEvidence, "OWN_FUNDS_INVALID")
            own = OwnFundsEvidence.from_canonical_dict(own_funds.to_canonical_dict())
            own.check_age(evaluated_at)
            _require(own.request_sha256 == digest({"domain": "CL7_OWN_FUNDS_REQUEST_V1",
                "intent_id": intent.intent_id, "candidate": candidate.to_dict(), "get_max_lots_price": None}),
                "OWN_REQUEST_MISMATCH")
            _require(own.metadata_sha256 == digest({"instrument_id": candidate.instrument_id,
                "currency": row.currency, "asset_class": row.asset_class, "lot_size": row.lot_size}),
                "OWN_METADATA_MISMATCH")
            _require((own.direction, own.order_type, own.time_in_force, own.requested_lots,
                       own.lot_size, own.estimated_price_kopecks) == (
                       candidate.direction, candidate.order_type, candidate.time_in_force,
                       candidate.requested_lots, candidate.lot_size, candidate.estimated_price_kopecks),
                       "OWN_REQUEST_MISMATCH")
            queued = sum(i.reserved_cash_kopecks * 10**7 for i in central.queued)
            _require(own.all_local_reservations_nano == queued
                and own.own_reservation_nano == intent.reserved_cash_kopecks * 10**7,
                "RESERVATIONS_MISMATCH")
            if candidate.direction == "SELL":
                _require(candidate.requested_lots <= candidate.current_lots, "SHORT_FORBIDDEN")
            common = dict(identity_key=self.store._key, identity_key_id=self.store._key_id,
                          account_scope_sha256=self.store._account)
            reservations = cl5.project_central_reservations(central, evaluated_at=evaluated_at,
                                environment=BrokerEnvironment.SANDBOX, **common)
            context = readers.build_versioned_cash_context(view.export_bytes(), pins=self.pins,
                codec_registry=tuple(self.store._registry.values()), broker_cash=broker_cash,
                own_buying=own_buying, reservations=reservations, portfolio=portfolio,
                risk_guard=risk_guard, buying_scope_sha256=buying_scope_sha256,
                evaluated_at=evaluated_at, **common)
            body = _parse(context.payload_bytes)
            _require(body["status"] == "CONSISTENT_REVIEW_ONLY", "CONTEXT_BLOCKED")
            authorization = intent.authorization
            _require(authorization.portfolio_revision == portfolio.portfolio_revision
                and authorization.portfolio_document_checksum == portfolio.portfolio_document_checksum
                and authorization.portfolio_decision_checksum == portfolio.portfolio_decision_checksum
                and authorization.risk_policy_hash == risk_guard.risk_policy_hash
                and authorization.risk_state_guard_hash == risk_guard.risk_state_guard_hash,
                "AUTHORIZATION_OWNER_MISMATCH")
            from datetime import datetime, timezone
            approved = datetime.fromisoformat(authorization.authorized_at.replace("Z", "+00:00"))
            _require(approved.tzinfo is not None and approved.utcoffset() is not None,
                     "AUTHORIZATION_STALE")
            utc = approved.astimezone(timezone.utc)
            approved_stamp = utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}000Z"
            _require(0 <= timestamp_ns(evaluated_at) - timestamp_ns(approved_stamp) <= MAX_AGE_NS,
                     "AUTHORIZATION_STALE")
            _require(own.positions_sha256 == broker_cash.response_canonical_sha256,
                     "OWN_CASH_RESPONSE_MISMATCH")
            _require(own.rub_position_nano == int(body["expected_cash_nano"])
                and own.broker_blocked_nano == 0, "OWN_CASH_MISMATCH")
            # Never subtract the selected request reserve twice. Each source's
            # available amount already includes the same complete queued set.
            free = int(body["free_cash_nano"])
            if candidate.direction == "BUY":
                free = min(free, own.free_after_reservations_nano)
            issued = timestamp_ns(evaluated_at)
            own_policy.binding_guard()
            self._check_view(view)
            payload = _canonical({"domain": DOMAIN, "version": 1, "source_export_version": 4,
                "lease_id": view._lease_id, "pins": self.pins.to_dict(),
                "account_scope_sha256": self.store._account, "identity_key_id": self.store._key_id,
                "intent_sha256": expected_intent_sha256, "candidate_sha256": _sha(_canonical(candidate.to_dict())),
                "central_state_sha256": _sha(_canonical(central.to_dict())),
                "context_json_ascii": context.canonical_bytes.decode("ascii"),
                "context_sha256": context.sha256, "own_funds": own.to_canonical_dict(),
                "evaluated_at": evaluated_at, "expires_at_ns": str(min(issued,
                    timestamp_ns(own.started_at)) + MAX_AGE_NS),
                "free_cash_nano": str(free), "reserved_cash_nano": str(own.own_reservation_nano),
                "status": "REQUEST_BOUND_CUTOVER_REQUIRED", "risk_admission_performed": False,
                "runtime_authority_granted": False, "runtime_cutover_performed": False})
            return VersionedRequestBinding(payload, hmac.new(self.store._key, payload, hashlib.sha256).hexdigest())
        except VersionedRuntimeBindingError:
            raise
        except Exception:
            raise VersionedRuntimeBindingError("V4_BINDING_EVIDENCE_OR_LEASE_INVALID") from None

    def validate_request_binding(self, binding: VersionedRequestBinding,
            view: operational.LockedOperationalView, *, now: str, **current_inputs) -> VersionedRequestBinding:
        """Recheck the same live lease, exact request and independent evidence.

        Validation does not refresh a proof's timestamps. All inputs are rebuilt
        at their original evaluated_at; freshness is checked again against now.
        """
        try:
            self._check_view(view)
            _require(type(binding) is VersionedRequestBinding, "TYPE_INVALID")
            payload = _parse(binding.payload_bytes)
            sig = hmac.new(self.store._key, binding.payload_bytes, hashlib.sha256).hexdigest()
            _require(type(binding.signature_sha256) is str and hmac.compare_digest(sig, binding.signature_sha256),
                     "AUTHENTICATION_FAILED")
            issued = timestamp_ns(payload["evaluated_at"])
            current = timestamp_ns(now)
            _require(issued <= current <= int(payload["expires_at_ns"]), "EXPIRED")
            _require(current_inputs.get("evaluated_at") == payload["evaluated_at"], "TIME_REBIND_FORBIDDEN")
            expected = self.build_request_binding(view, **current_inputs)
            _require(expected == binding, "INPUTS_CHANGED")
            # Re-evaluate underlying signed owner/cash evidence at *now*. A
            # binding issued near an evidence deadline cannot extend its life.
            fresh = dict(current_inputs, evaluated_at=now)
            self.build_request_binding(view, **fresh)
            self._check_view(view)
            return binding
        except VersionedRuntimeBindingError:
            raise
        except Exception:
            raise VersionedRuntimeBindingError("V4_BINDING_EVIDENCE_OR_LEASE_INVALID") from None

    def require_runtime_authority(self, *_: object, **__: object) -> None:
        raise VersionedRuntimeBindingError("V4_BINDING_CL7_CUTOVER_REQUIRED")
