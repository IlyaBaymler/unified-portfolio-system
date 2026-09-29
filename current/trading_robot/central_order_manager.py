from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any, TypeVar
from uuid import NAMESPACE_URL, uuid5

from .journal import EventJournal, JournalEvent
from .locking import InterProcessFileLock, LockUnavailableError
from .multi_instrument_strategy import StrategyProposal
from .portfolio_model import PortfolioState, SnapshotFreshness
from .portfolio_preflight import (
    PortfolioPreflightDecision,
    PortfolioSnapshotLease,
)
from .portfolio_repository import PortfolioRepository, PortfolioRepositoryError
from .risk_runtime import RiskRuntimeOutcome, risk_state_guard_hash
from .runtime_cash_authority import LockedDispatchProof
from .state_persistence import (
    StatePersistenceError,
    atomic_write_json,
    read_json_verified,
)

CENTRAL_ORDER_SCHEMA_VERSION = 1
CENTRAL_ORDER_STATUSES = frozenset(
    {
        "QUEUED",
        "IN_FLIGHT",
        "SUBMITTED",
        "UNCERTAIN",
        "RECONCILED",
        "FAILED",
        "CANCELLED",
    }
)
RESERVATION_STATUSES = frozenset(
    {"QUEUED", "IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
)
ACCOUNT_BLOCKING_STATUSES = frozenset(
    {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
)
TERMINAL_STATUSES = frozenset({"RECONCILED", "FAILED", "CANCELLED"})
_ALLOWED_RISK_STATUSES = frozenset(
    {"PASS", "ADJUSTED", "REDUCTION_ALLOWED"}
)
_ALLOWED_PORTFOLIO_RISK_STATUSES = frozenset(
    {"PASS", "ADJUSTED", "REDUCTION_ALLOWED"}
)
_RECONCILIATION_OUTCOMES = frozenset(
    {"FILLED", "PARTIALLY_FILLED", "REJECTED", "CANCELLED", "NOT_SUBMITTED"}
)
_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "QUEUED": frozenset({"IN_FLIGHT", "FAILED", "CANCELLED"}),
    "IN_FLIGHT": frozenset(
        {"SUBMITTED", "UNCERTAIN", "FAILED", "CANCELLED"}
    ),
    "SUBMITTED": frozenset({"UNCERTAIN", "RECONCILED"}),
    "UNCERTAIN": frozenset({"RECONCILED"}),
    "RECONCILED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _locked_dispatch_proof_from_exact_text(value: str) -> LockedDispatchProof:
    if type(value) is not str:
        raise CentralOrderStateError("CL7 locked dispatch proof is invalid.")

    def exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    try:
        raw = json.loads(value, object_pairs_hook=exact_object)
        proof = LockedDispatchProof.from_canonical_dict(raw)
        encoded = value.encode("ascii")
    except Exception as exc:
        raise CentralOrderStateError(
            "CL7 locked dispatch proof is invalid."
        ) from exc
    if encoded != proof.canonical_bytes:
        raise CentralOrderStateError(
            "CL7 locked dispatch proof is not canonical."
        )
    return proof


class CentralOrderError(RuntimeError):
    """Base error for the v3.8 account-wide order queue."""


class CentralOrderConflictError(CentralOrderError):
    """Raised when a transition or account-wide gate is unsafe."""


class CentralOrderStateError(CentralOrderError):
    """Raised when persisted manager state fails validation."""


@dataclass(frozen=True, slots=True)
class PortfolioRiskAuthorizationProof:
    """Immutable M4 proof for one account-wide admission decision.

    ``admission_*`` is finalized by :class:`CentralOrderManager` while its
    account-wide lock is held.  This keeps evaluation, reservation and the
    persisted queue revision in one transaction.
    """

    decision_id: str
    input_hash: str
    policy_hash: str
    status: str
    single_risk_approved_target_lots: int
    approved_target_lots: int
    snapshot_revision: int
    snapshot_checksum: str
    central_order_revision: int
    reservation_projection_hash: str
    risk_state_guard_hash: str
    evaluated_at: str
    candidate_price_at: str
    candidate_price_source: str
    cash_buffer_bps: int
    excluded_reservation_ids: tuple[str, ...] = ()
    admission_central_revision: int | None = None
    admission_reservation_projection_hash: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "decision_id",
            "input_hash",
            "policy_hash",
            "snapshot_checksum",
            "reservation_projection_hash",
            "risk_state_guard_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _sha256_text(
                    getattr(self, field_name),
                    f"portfolio_risk.{field_name}",
                ),
            )
        status = str(self.status or "").strip().upper()
        if status not in _ALLOWED_PORTFOLIO_RISK_STATUSES:
            raise CentralOrderConflictError(
                f"Portfolio Risk status {status!r} does not authorize an order."
            )
        object.__setattr__(self, "status", status)
        for field_name in (
            "single_risk_approved_target_lots",
            "approved_target_lots",
            "snapshot_revision",
            "central_order_revision",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_negative_int(
                    getattr(self, field_name),
                    f"portfolio_risk.{field_name}",
                ),
            )
        object.__setattr__(
            self,
            "evaluated_at",
            _timestamp(self.evaluated_at, "portfolio_risk.evaluated_at"),
        )
        object.__setattr__(
            self,
            "candidate_price_at",
            _timestamp(
                self.candidate_price_at,
                "portfolio_risk.candidate_price_at",
            ),
        )
        object.__setattr__(
            self,
            "candidate_price_source",
            _required_text(
                self.candidate_price_source,
                "portfolio_risk.candidate_price_source",
            ).upper(),
        )
        normalized_buffer = _non_negative_int(
            self.cash_buffer_bps,
            "portfolio_risk.cash_buffer_bps",
        )
        if normalized_buffer > 5_000:
            raise CentralOrderStateError(
                "portfolio_risk.cash_buffer_bps must not exceed 5000."
            )
        object.__setattr__(self, "cash_buffer_bps", normalized_buffer)
        if isinstance(self.excluded_reservation_ids, (str, bytes)):
            raise CentralOrderStateError(
                "portfolio_risk.excluded_reservation_ids must be an array."
            )
        excluded = tuple(
            sorted(
                {
                    _required_text(item, "portfolio_risk.excluded_reservation_id")
                    for item in self.excluded_reservation_ids
                }
            )
        )
        object.__setattr__(self, "excluded_reservation_ids", excluded)
        admission_revision = self.admission_central_revision
        admission_hash = self.admission_reservation_projection_hash
        if (admission_revision is None) != (admission_hash is None):
            raise CentralOrderStateError(
                "Portfolio Risk admission revision/hash must be set together."
            )
        if admission_revision is not None:
            normalized_revision = _non_negative_int(
                admission_revision,
                "portfolio_risk.admission_central_revision",
            )
            if normalized_revision <= self.central_order_revision:
                raise CentralOrderStateError(
                    "Portfolio Risk admission revision must advance Central state."
                )
            object.__setattr__(
                self,
                "admission_central_revision",
                normalized_revision,
            )
            object.__setattr__(
                self,
                "admission_reservation_projection_hash",
                _sha256_text(
                    admission_hash,
                    "portfolio_risk.admission_reservation_projection_hash",
                ),
            )

    @property
    def finalized(self) -> bool:
        return self.admission_central_revision is not None

    def finalize(
        self,
        *,
        central_revision: int,
        reservation_projection_hash: str,
    ) -> PortfolioRiskAuthorizationProof:
        if self.finalized:
            raise CentralOrderStateError("Portfolio Risk proof is already finalized.")
        return replace(
            self,
            admission_central_revision=central_revision,
            admission_reservation_projection_hash=reservation_projection_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "input_hash": self.input_hash,
            "policy_hash": self.policy_hash,
            "status": self.status,
            "single_risk_approved_target_lots": (
                self.single_risk_approved_target_lots
            ),
            "approved_target_lots": self.approved_target_lots,
            "snapshot_revision": self.snapshot_revision,
            "snapshot_checksum": self.snapshot_checksum,
            "central_order_revision": self.central_order_revision,
            "reservation_projection_hash": self.reservation_projection_hash,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "evaluated_at": self.evaluated_at,
            "candidate_price_at": self.candidate_price_at,
            "candidate_price_source": self.candidate_price_source,
            "cash_buffer_bps": self.cash_buffer_bps,
            "excluded_reservation_ids": list(self.excluded_reservation_ids),
            "admission_central_revision": self.admission_central_revision,
            "admission_reservation_projection_hash": (
                self.admission_reservation_projection_hash
            ),
        }

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> PortfolioRiskAuthorizationProof:
        excluded = raw.get("excluded_reservation_ids", [])
        if not isinstance(excluded, list):
            raise CentralOrderStateError(
                "portfolio_risk.excluded_reservation_ids must be an array."
            )
        return cls(
            decision_id=raw.get("decision_id", ""),
            input_hash=raw.get("input_hash", ""),
            policy_hash=raw.get("policy_hash", ""),
            status=raw.get("status", ""),
            single_risk_approved_target_lots=raw.get(
                "single_risk_approved_target_lots", -1
            ),
            approved_target_lots=raw.get("approved_target_lots", -1),
            snapshot_revision=raw.get("snapshot_revision", -1),
            snapshot_checksum=raw.get("snapshot_checksum", ""),
            central_order_revision=raw.get("central_order_revision", -1),
            reservation_projection_hash=raw.get(
                "reservation_projection_hash", ""
            ),
            risk_state_guard_hash=raw.get("risk_state_guard_hash", ""),
            evaluated_at=raw.get("evaluated_at", ""),
            candidate_price_at=raw.get("candidate_price_at", ""),
            candidate_price_source=raw.get("candidate_price_source", ""),
            cash_buffer_bps=raw.get("cash_buffer_bps", -1),
            excluded_reservation_ids=tuple(excluded),
            admission_central_revision=raw.get("admission_central_revision"),
            admission_reservation_projection_hash=raw.get(
                "admission_reservation_projection_hash"
            ),
        )


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise CentralOrderStateError(f"{field_name} must not be empty.")
    return normalized


def _sha256_text(value: Any, field_name: str) -> str:
    normalized = _required_text(value, field_name).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise CentralOrderStateError(
            f"{field_name} must be a 64-character SHA-256 hex digest."
        )
    return normalized


def _timestamp(value: Any, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CentralOrderStateError(
            f"{field_name} must be an ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CentralOrderStateError(f"{field_name} must be timezone-aware.")
    return parsed.astimezone(timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise CentralOrderStateError(f"{field_name} must be an integer.")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise CentralOrderStateError(f"{field_name} must be an integer.") from exc
    if normalized < 0:
        raise CentralOrderStateError(f"{field_name} must not be negative.")
    return normalized


def _positive_int(value: Any, field_name: str) -> int:
    normalized = _non_negative_int(value, field_name)
    if normalized < 1:
        raise CentralOrderStateError(f"{field_name} must be positive.")
    return normalized


def _price_to_kopecks(value: Any) -> int:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CentralOrderStateError(
            "estimated_price_rub must be a positive decimal."
        ) from exc
    if not price.is_finite() or price <= 0:
        raise CentralOrderStateError(
            "estimated_price_rub must be a positive decimal."
        )
    return int((price * 100).to_integral_value(rounding=ROUND_CEILING))


def _cash_to_kopecks(value: Any) -> int:
    try:
        cash = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CentralOrderStateError(
            "Canonical RUB available cash must be a non-negative decimal."
        ) from exc
    if not cash.is_finite() or cash < 0:
        raise CentralOrderStateError(
            "Canonical RUB available cash must be a non-negative decimal."
        )
    return int((cash * 100).to_integral_value(rounding=ROUND_FLOOR))


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    """Immutable proof that canonical preflight and Risk approved one target."""

    account_id: str
    instrument_id: str
    authorized_target_lots: int
    portfolio_revision: int
    portfolio_decision_checksum: str
    portfolio_document_checksum: str
    available_cash_kopecks: int
    preflight_status: str
    pending_order_ids: tuple[str, ...]
    uncertain_order_ids: tuple[str, ...]
    risk_status: str
    risk_decision_id: str
    risk_policy_hash: str
    risk_order_allowed: bool
    authorized_at: str
    risk_state_guard_hash: str | None = None
    portfolio_risk: PortfolioRiskAuthorizationProof | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "account_id",
            _required_text(self.account_id, "authorization.account_id"),
        )
        object.__setattr__(
            self,
            "instrument_id",
            _required_text(self.instrument_id, "authorization.instrument_id"),
        )
        object.__setattr__(
            self,
            "authorized_target_lots",
            _non_negative_int(
                self.authorized_target_lots,
                "authorization.authorized_target_lots",
            ),
        )
        object.__setattr__(
            self,
            "portfolio_revision",
            _non_negative_int(
                self.portfolio_revision,
                "authorization.portfolio_revision",
            ),
        )
        object.__setattr__(
            self,
            "portfolio_decision_checksum",
            _sha256_text(
                self.portfolio_decision_checksum,
                "authorization.portfolio_decision_checksum",
            ),
        )
        object.__setattr__(
            self,
            "portfolio_document_checksum",
            _sha256_text(
                self.portfolio_document_checksum,
                "authorization.portfolio_document_checksum",
            ),
        )
        object.__setattr__(
            self,
            "available_cash_kopecks",
            _non_negative_int(
                self.available_cash_kopecks,
                "authorization.available_cash_kopecks",
            ),
        )
        preflight_status = str(self.preflight_status).strip().upper()
        if preflight_status != "PASS":
            raise CentralOrderConflictError(
                "Central Order Manager requires preflight PASS."
            )
        object.__setattr__(self, "preflight_status", preflight_status)
        if isinstance(self.pending_order_ids, (str, bytes)) or not isinstance(
            self.pending_order_ids,
            (list, tuple),
        ):
            raise CentralOrderStateError(
                "authorization.pending_order_ids must be an array."
            )
        if isinstance(self.uncertain_order_ids, (str, bytes)) or not isinstance(
            self.uncertain_order_ids,
            (list, tuple),
        ):
            raise CentralOrderStateError(
                "authorization.uncertain_order_ids must be an array."
            )
        pending = tuple(sorted({str(item) for item in self.pending_order_ids if str(item)}))
        uncertain = tuple(
            sorted({str(item) for item in self.uncertain_order_ids if str(item)})
        )
        if pending or uncertain:
            raise CentralOrderConflictError(
                "Canonical preflight contains pending or uncertain orders."
            )
        object.__setattr__(self, "pending_order_ids", pending)
        object.__setattr__(self, "uncertain_order_ids", uncertain)
        risk_status = str(self.risk_status).strip().upper()
        if risk_status not in _ALLOWED_RISK_STATUSES:
            raise CentralOrderConflictError(
                f"Risk status {risk_status!r} does not authorize an order."
            )
        if not isinstance(self.risk_order_allowed, bool):
            raise CentralOrderStateError(
                "authorization.risk_order_allowed must be boolean."
            )
        if not self.risk_order_allowed:
            raise CentralOrderConflictError("Risk decision did not allow an order.")
        object.__setattr__(self, "risk_status", risk_status)
        object.__setattr__(
            self,
            "risk_decision_id",
            _required_text(
                self.risk_decision_id,
                "authorization.risk_decision_id",
            ),
        )
        object.__setattr__(
            self,
            "risk_policy_hash",
            _sha256_text(
                self.risk_policy_hash,
                "authorization.risk_policy_hash",
            ),
        )
        object.__setattr__(self, "risk_order_allowed", True)
        object.__setattr__(
            self,
            "authorized_at",
            _timestamp(self.authorized_at, "authorization.authorized_at"),
        )
        state_guard = (
            None
            if self.risk_state_guard_hash in (None, "")
            else _sha256_text(
                self.risk_state_guard_hash,
                "authorization.risk_state_guard_hash",
            )
        )
        object.__setattr__(self, "risk_state_guard_hash", state_guard)
        if self.portfolio_risk is not None and not isinstance(
            self.portfolio_risk,
            PortfolioRiskAuthorizationProof,
        ):
            raise CentralOrderStateError(
                "authorization.portfolio_risk must be PortfolioRiskAuthorizationProof."
            )
        if self.portfolio_risk is not None:
            proof = self.portfolio_risk
            if proof.approved_target_lots != self.authorized_target_lots:
                raise CentralOrderStateError(
                    "Portfolio Risk target differs from final authorization target."
                )
            if proof.snapshot_revision != self.portfolio_revision:
                raise CentralOrderStateError(
                    "Portfolio Risk/canonical revision mismatch."
                )
            if proof.snapshot_checksum != self.portfolio_decision_checksum:
                raise CentralOrderStateError(
                    "Portfolio Risk/canonical checksum mismatch."
                )
            if proof.risk_state_guard_hash != self.risk_state_guard_hash:
                raise CentralOrderStateError(
                    "Portfolio Risk/single-order Risk state guard mismatch."
                )

    @classmethod
    def from_gate_results(
        cls,
        preflight: PortfolioPreflightDecision,
        risk: RiskRuntimeOutcome,
        *,
        lease: PortfolioSnapshotLease,
        authorized_at: str | None = None,
    ) -> ExecutionAuthorization:
        if not preflight.allowed:
            raise CentralOrderConflictError("Portfolio preflight is blocked.")
        if risk.mode != "SANDBOX_EXECUTION":
            raise CentralOrderConflictError(
                "Central Order Manager accepts SANDBOX_EXECUTION Risk only."
            )
        if not risk.enforced or risk.error or risk.assessment is None:
            raise CentralOrderConflictError(
                "Risk outcome is not an enforced successful assessment."
            )
        decision = risk.assessment.decision
        context = preflight.context
        if (
            lease.account_id != context.account_id
            or lease.revision != context.snapshot_revision
            or lease.decision_checksum != context.snapshot_decision_checksum
            or lease.document_checksum != context.snapshot_document_checksum
        ):
            raise CentralOrderConflictError(
                "Preflight and canonical lease do not share one snapshot."
            )
        if (
            risk.portfolio_revision != context.snapshot_revision
            or risk.portfolio_decision_checksum
            != context.snapshot_decision_checksum
        ):
            raise CentralOrderConflictError(
                "Risk and preflight do not share one canonical snapshot."
            )
        if (
            decision.requested_target_lots != context.proposed_target_lots
            or decision.current_lots != context.actual_lots
            or decision.approved_target_lots != risk.approved_target_lots
        ):
            raise CentralOrderConflictError(
                "Risk decision inputs do not match canonical preflight."
            )
        rub_cash = lease.state.account.cash("rub")
        if rub_cash is None:
            raise CentralOrderConflictError(
                "Canonical portfolio has no RUB available-cash balance."
            )
        return cls(
            account_id=context.account_id,
            instrument_id=context.instrument_id,
            authorized_target_lots=risk.approved_target_lots,
            portfolio_revision=context.snapshot_revision,
            portfolio_decision_checksum=context.snapshot_decision_checksum,
            portfolio_document_checksum=context.snapshot_document_checksum,
            available_cash_kopecks=_cash_to_kopecks(rub_cash.available),
            preflight_status=preflight.status.value,
            pending_order_ids=context.pending_order_ids,
            uncertain_order_ids=context.uncertain_order_ids,
            risk_status=risk.status,
            risk_decision_id=str(risk.decision_id or ""),
            risk_policy_hash=str(risk.policy_hash or ""),
            risk_order_allowed=bool(decision.order_allowed),
            authorized_at=authorized_at or _now(),
            risk_state_guard_hash=risk_state_guard_hash(risk.assessment.state),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "authorized_target_lots": self.authorized_target_lots,
            "portfolio_revision": self.portfolio_revision,
            "portfolio_decision_checksum": self.portfolio_decision_checksum,
            "portfolio_document_checksum": self.portfolio_document_checksum,
            "available_cash_kopecks": self.available_cash_kopecks,
            "preflight_status": self.preflight_status,
            "pending_order_ids": list(self.pending_order_ids),
            "uncertain_order_ids": list(self.uncertain_order_ids),
            "risk_status": self.risk_status,
            "risk_decision_id": self.risk_decision_id,
            "risk_policy_hash": self.risk_policy_hash,
            "risk_order_allowed": self.risk_order_allowed,
            "authorized_at": self.authorized_at,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "portfolio_risk": (
                self.portfolio_risk.to_dict()
                if self.portfolio_risk is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ExecutionAuthorization:
        pending = raw.get("pending_order_ids")
        uncertain = raw.get("uncertain_order_ids")
        risk_order_allowed = raw.get("risk_order_allowed")
        if not isinstance(pending, list):
            raise CentralOrderStateError(
                "authorization.pending_order_ids must be an array."
            )
        if not isinstance(uncertain, list):
            raise CentralOrderStateError(
                "authorization.uncertain_order_ids must be an array."
            )
        if not isinstance(risk_order_allowed, bool):
            raise CentralOrderStateError(
                "authorization.risk_order_allowed must be boolean."
            )
        raw_portfolio_risk = raw.get("portfolio_risk")
        if raw_portfolio_risk is not None and not isinstance(
            raw_portfolio_risk,
            Mapping,
        ):
            raise CentralOrderStateError(
                "authorization.portfolio_risk must be an object or null."
            )
        return cls(
            account_id=raw.get("account_id", ""),
            instrument_id=raw.get("instrument_id", ""),
            authorized_target_lots=raw.get("authorized_target_lots", -1),
            portfolio_revision=raw.get("portfolio_revision", -1),
            portfolio_decision_checksum=raw.get(
                "portfolio_decision_checksum", ""
            ),
            portfolio_document_checksum=raw.get(
                "portfolio_document_checksum", ""
            ),
            available_cash_kopecks=raw.get("available_cash_kopecks", -1),
            preflight_status=raw.get("preflight_status", ""),
            pending_order_ids=tuple(pending),
            uncertain_order_ids=tuple(uncertain),
            risk_status=raw.get("risk_status", ""),
            risk_decision_id=raw.get("risk_decision_id", ""),
            risk_policy_hash=raw.get("risk_policy_hash", ""),
            risk_order_allowed=risk_order_allowed,
            authorized_at=raw.get("authorized_at", ""),
            risk_state_guard_hash=raw.get("risk_state_guard_hash"),
            portfolio_risk=(
                PortfolioRiskAuthorizationProof.from_dict(raw_portfolio_risk)
                if raw_portfolio_risk is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CentralOrderCandidate:
    """Provider-neutral order request derived from a read-only proposal."""

    account_id: str
    instrument_id: str
    ticker: str
    runtime_key: str
    runtime_config_hash: str
    candle_interval: str
    candle_time: str
    strategy_id: str
    strategy_profile_hash: str
    current_lots: int
    target_lots: int
    estimated_price_kopecks: int
    lot_size: int
    order_type: str = "BESTPRICE"
    time_in_force: str = "FILL_AND_KILL"
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        for field_name in (
            "account_id",
            "instrument_id",
            "ticker",
            "runtime_key",
            "candle_interval",
            "strategy_id",
        ):
            normalized = _required_text(getattr(self, field_name), field_name)
            if field_name in {"ticker", "candle_interval"}:
                normalized = normalized.upper()
            if field_name == "strategy_id":
                normalized = normalized.lower()
            object.__setattr__(self, field_name, normalized)
        object.__setattr__(
            self,
            "runtime_config_hash",
            _sha256_text(self.runtime_config_hash, "runtime_config_hash"),
        )
        object.__setattr__(
            self,
            "strategy_profile_hash",
            _sha256_text(self.strategy_profile_hash, "strategy_profile_hash"),
        )
        current = _non_negative_int(self.current_lots, "current_lots")
        target = _non_negative_int(self.target_lots, "target_lots")
        if current == target:
            raise CentralOrderConflictError(
                "Order candidate must change the current position."
            )
        object.__setattr__(self, "current_lots", current)
        object.__setattr__(self, "target_lots", target)
        object.__setattr__(
            self,
            "estimated_price_kopecks",
            _positive_int(
                self.estimated_price_kopecks,
                "estimated_price_kopecks",
            ),
        )
        object.__setattr__(self, "lot_size", _positive_int(self.lot_size, "lot_size"))
        order_type = str(self.order_type).strip().upper()
        if order_type not in {"MARKET", "BESTPRICE"}:
            raise CentralOrderStateError("Unsupported order_type.")
        time_in_force = str(self.time_in_force).strip().upper()
        if time_in_force not in {"FILL_AND_KILL", "FILL_OR_KILL"}:
            raise CentralOrderStateError(
                "Central queue requires a terminal immediate time-in-force."
            )
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "time_in_force", time_in_force)
        object.__setattr__(
            self,
            "candle_time",
            _timestamp(self.candle_time, "candle_time"),
        )
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "created_at"),
        )

    @property
    def direction(self) -> str:
        return "BUY" if self.target_lots > self.current_lots else "SELL"

    @property
    def requested_lots(self) -> int:
        return abs(self.target_lots - self.current_lots)

    @classmethod
    def from_strategy_proposal(
        cls,
        proposal: StrategyProposal,
        *,
        account_id: str,
        runtime_config_hash: str,
        current_lots: int,
        approved_target_lots: int | None = None,
        estimated_price_rub: Any,
        lot_size: int,
        order_type: str = "BESTPRICE",
        time_in_force: str = "FILL_AND_KILL",
    ) -> CentralOrderCandidate:
        if proposal.execution_authorized:
            raise CentralOrderConflictError(
                "Strategy proposal must remain execution-unauthorized."
            )
        return cls(
            account_id=account_id,
            instrument_id=proposal.instrument_id,
            ticker=proposal.ticker,
            runtime_key=proposal.runtime_key,
            runtime_config_hash=runtime_config_hash,
            candle_interval=proposal.candle_interval,
            candle_time=proposal.candle_time,
            strategy_id=proposal.primary_strategy,
            strategy_profile_hash=proposal.strategy_profile_hash,
            current_lots=current_lots,
            target_lots=(
                proposal.primary_target_lots
                if approved_target_lots is None
                else approved_target_lots
            ),
            estimated_price_kopecks=_price_to_kopecks(estimated_price_rub),
            lot_size=lot_size,
            order_type=order_type,
            time_in_force=time_in_force,
            created_at=proposal.generated_at,
        )

    def reservation_kopecks(self, *, cash_buffer_bps: int) -> int:
        if self.direction == "SELL":
            return 0
        buffer_bps = _non_negative_int(cash_buffer_bps, "cash_buffer_bps")
        if buffer_bps > 5_000:
            raise CentralOrderStateError("cash_buffer_bps must not exceed 5000.")
        base = self.estimated_price_kopecks * self.lot_size * self.requested_lots
        return (base * (10_000 + buffer_bps) + 9_999) // 10_000

    def idempotency_key(self) -> str:
        payload = {
            "schema_version": CENTRAL_ORDER_SCHEMA_VERSION,
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "runtime_key": self.runtime_key,
            "runtime_config_hash": self.runtime_config_hash,
            "candle_time": self.candle_time,
            "strategy_profile_hash": self.strategy_profile_hash,
            "current_lots": self.current_lots,
            "target_lots": self.target_lots,
            "direction": self.direction,
            "requested_lots": self.requested_lots,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "runtime_key": self.runtime_key,
            "runtime_config_hash": self.runtime_config_hash,
            "candle_interval": self.candle_interval,
            "candle_time": self.candle_time,
            "strategy_id": self.strategy_id,
            "strategy_profile_hash": self.strategy_profile_hash,
            "current_lots": self.current_lots,
            "target_lots": self.target_lots,
            "direction": self.direction,
            "requested_lots": self.requested_lots,
            "estimated_price_kopecks": self.estimated_price_kopecks,
            "lot_size": self.lot_size,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CentralOrderCandidate:
        candidate = cls(
            account_id=raw.get("account_id", ""),
            instrument_id=raw.get("instrument_id", ""),
            ticker=raw.get("ticker", ""),
            runtime_key=raw.get("runtime_key", ""),
            runtime_config_hash=raw.get("runtime_config_hash", ""),
            candle_interval=raw.get("candle_interval", ""),
            candle_time=raw.get("candle_time", ""),
            strategy_id=raw.get("strategy_id", ""),
            strategy_profile_hash=raw.get("strategy_profile_hash", ""),
            current_lots=raw.get("current_lots", -1),
            target_lots=raw.get("target_lots", -1),
            estimated_price_kopecks=raw.get("estimated_price_kopecks", 0),
            lot_size=raw.get("lot_size", 0),
            order_type=raw.get("order_type", ""),
            time_in_force=raw.get("time_in_force", ""),
            created_at=raw.get("created_at", ""),
        )
        if raw.get("direction") != candidate.direction:
            raise CentralOrderStateError("Candidate direction mismatch.")
        if raw.get("requested_lots") != candidate.requested_lots:
            raise CentralOrderStateError("Candidate requested_lots mismatch.")
        return candidate


@dataclass(frozen=True, slots=True)
class OrderTransition:
    status: str
    at: str
    detail: str = ""

    def __post_init__(self) -> None:
        status = str(self.status).strip().upper()
        if status not in CENTRAL_ORDER_STATUSES:
            raise CentralOrderStateError(f"Unsupported transition status {status!r}.")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "at", _timestamp(self.at, "transition.at"))
        object.__setattr__(self, "detail", str(self.detail or ""))

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "at": self.at, "detail": self.detail}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> OrderTransition:
        return cls(
            status=raw.get("status", ""),
            at=raw.get("at", ""),
            detail=str(raw.get("detail") or ""),
        )


@dataclass(frozen=True, slots=True)
class CentralOrderIntent:
    intent_id: str
    idempotency_key: str
    queue_sequence: int
    candidate: CentralOrderCandidate
    authorization: ExecutionAuthorization
    status: str
    reserved_cash_kopecks: int
    broker_order_id: str | None
    uncertainty_reason: str | None
    outcome: str | None
    executed_lots: int
    reconciled_portfolio_revision: int | None
    reconciled_portfolio_decision_checksum: str | None
    reconciled_portfolio_snapshot_at: str | None
    risk_execution_status: str | None
    risk_execution_id: str | None
    created_at: str
    updated_at: str
    transitions: tuple[OrderTransition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(
            self,
            "idempotency_key",
            _sha256_text(self.idempotency_key, "idempotency_key"),
        )
        object.__setattr__(
            self,
            "queue_sequence",
            _positive_int(self.queue_sequence, "queue_sequence"),
        )
        if not isinstance(self.candidate, CentralOrderCandidate):
            raise CentralOrderStateError("candidate must be CentralOrderCandidate.")
        if not isinstance(self.authorization, ExecutionAuthorization):
            raise CentralOrderStateError(
                "authorization must be ExecutionAuthorization."
            )
        if self.candidate.account_id != self.authorization.account_id:
            raise CentralOrderStateError("Candidate/authorization account mismatch.")
        if self.candidate.instrument_id != self.authorization.instrument_id:
            raise CentralOrderStateError(
                "Candidate/authorization instrument mismatch."
            )
        if self.candidate.target_lots != self.authorization.authorized_target_lots:
            raise CentralOrderStateError("Candidate target is not Risk-authorized.")
        expected_key = self.candidate.idempotency_key()
        if self.idempotency_key != expected_key:
            raise CentralOrderStateError("Intent idempotency key mismatch.")
        expected_id = str(uuid5(NAMESPACE_URL, "central-order-v1|" + expected_key))
        if self.intent_id != expected_id:
            raise CentralOrderStateError("Intent identifier mismatch.")
        status = str(self.status).strip().upper()
        if status not in CENTRAL_ORDER_STATUSES:
            raise CentralOrderStateError(f"Unsupported intent status {status!r}.")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "reserved_cash_kopecks",
            _non_negative_int(
                self.reserved_cash_kopecks,
                "reserved_cash_kopecks",
            ),
        )
        if self.candidate.direction == "SELL" and self.reserved_cash_kopecks != 0:
            raise CentralOrderStateError("SELL intent must not reserve cash.")
        executed = _non_negative_int(self.executed_lots, "executed_lots")
        if executed > self.candidate.requested_lots:
            raise CentralOrderStateError("executed_lots exceed requested_lots.")
        object.__setattr__(self, "executed_lots", executed)
        reconciled_revision = (
            None
            if self.reconciled_portfolio_revision is None
            else _non_negative_int(
                self.reconciled_portfolio_revision,
                "reconciled_portfolio_revision",
            )
        )
        reconciled_checksum = (
            None
            if self.reconciled_portfolio_decision_checksum in (None, "")
            else _sha256_text(
                self.reconciled_portfolio_decision_checksum,
                "reconciled_portfolio_decision_checksum",
            )
        )
        reconciled_snapshot_at = (
            None
            if self.reconciled_portfolio_snapshot_at in (None, "")
            else _timestamp(
                self.reconciled_portfolio_snapshot_at,
                "reconciled_portfolio_snapshot_at",
            )
        )
        outcome = str(self.outcome or "").strip().upper() or None
        uncertainty_reason = (
            str(self.uncertainty_reason or "").strip() or None
        )
        if status == "UNCERTAIN" and uncertainty_reason is None:
            raise CentralOrderStateError(
                "UNCERTAIN intent requires an uncertainty reason."
            )
        if status in {"FAILED", "CANCELLED"} and outcome is None:
            raise CentralOrderStateError(
                f"{status} intent requires a terminal outcome."
            )
        if status not in {"FAILED", "CANCELLED", "RECONCILED"} and outcome:
            raise CentralOrderStateError(
                f"{status} intent must not contain a terminal outcome."
            )
        if status == "RECONCILED":
            if (
                outcome not in _RECONCILIATION_OUTCOMES
                or reconciled_revision is None
                or reconciled_checksum is None
                or reconciled_snapshot_at is None
            ):
                raise CentralOrderStateError(
                    "RECONCILED intent requires canonical reconciliation proof."
                )
            if outcome == "FILLED" and executed != self.candidate.requested_lots:
                raise CentralOrderStateError(
                    "Persisted FILLED outcome does not cover all requested lots."
                )
            if outcome == "PARTIALLY_FILLED" and not (
                0 < executed < self.candidate.requested_lots
            ):
                raise CentralOrderStateError(
                    "Persisted partial fill has an invalid executed-lot count."
                )
            if outcome in {"REJECTED", "CANCELLED", "NOT_SUBMITTED"} and executed:
                raise CentralOrderStateError(
                    f"Persisted {outcome} outcome must have zero executed lots."
                )
        elif (
            executed
            or reconciled_revision is not None
            or reconciled_checksum is not None
            or reconciled_snapshot_at is not None
        ):
            raise CentralOrderStateError(
                "Only RECONCILED intent may contain execution proof."
            )
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "uncertainty_reason", uncertainty_reason)
        object.__setattr__(
            self,
            "reconciled_portfolio_revision",
            reconciled_revision,
        )
        object.__setattr__(
            self,
            "reconciled_portfolio_decision_checksum",
            reconciled_checksum,
        )
        object.__setattr__(
            self,
            "reconciled_portfolio_snapshot_at",
            reconciled_snapshot_at,
        )
        risk_execution_status = (
            str(self.risk_execution_status or "").strip().upper() or None
        )
        risk_execution_id = str(self.risk_execution_id or "").strip() or None
        if status == "RECONCILED":
            if executed:
                if (
                    risk_execution_status not in {"RECORDED", "DUPLICATE"}
                    or risk_execution_id != self.intent_id
                ):
                    raise CentralOrderStateError(
                        "Executed reconciliation requires Risk accounting proof."
                    )
            elif risk_execution_status != "NOT_REQUIRED" or risk_execution_id:
                raise CentralOrderStateError(
                    "Zero-execution reconciliation must not contain Risk execution."
                )
        elif risk_execution_status is not None or risk_execution_id is not None:
            raise CentralOrderStateError(
                "Only RECONCILED intent may contain Risk execution proof."
            )
        object.__setattr__(
            self,
            "risk_execution_status",
            risk_execution_status,
        )
        object.__setattr__(self, "risk_execution_id", risk_execution_id)
        created_at = _timestamp(self.created_at, "created_at")
        updated_at = _timestamp(self.updated_at, "updated_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        transitions = tuple(self.transitions)
        if not transitions or transitions[-1].status != status:
            raise CentralOrderStateError(
                "Intent transition history does not match current status."
            )
        if transitions[0].status != "QUEUED" or transitions[0].at != created_at:
            raise CentralOrderStateError(
                "Intent transition history must begin with its QUEUED creation."
            )
        if transitions[-1].at != updated_at:
            raise CentralOrderStateError(
                "Intent transition history does not match updated_at."
            )
        for previous, current in pairwise(transitions):
            if datetime.fromisoformat(current.at) < datetime.fromisoformat(previous.at):
                raise CentralOrderStateError(
                    "Intent transition timestamps must be monotonic."
                )
            if current.status == previous.status == "QUEUED":
                continue
            if current.status not in _TRANSITIONS[previous.status]:
                raise CentralOrderStateError(
                    "Persisted central-order transition sequence is invalid."
                )
        object.__setattr__(self, "transitions", transitions)
        broker_id = str(self.broker_order_id or "").strip() or None
        if status == "SUBMITTED" and not broker_id:
            raise CentralOrderStateError(
                f"{status} intent requires broker_order_id."
            )
        if broker_id and status not in {"SUBMITTED", "UNCERTAIN", "RECONCILED"}:
            raise CentralOrderStateError(
                f"{status} intent must not contain broker_order_id."
            )
        object.__setattr__(self, "broker_order_id", broker_id)

    @property
    def cl7_locked_dispatch_proof(self) -> Mapping[str, Any] | None:
        """Return proof custody embedded in the immutable transition chain.

        Keeping the custody inside an existing predecessor field preserves the
        accepted CL5 ``CentralOrderIntent`` dataclass shape while the serialized
        intent exposes the two explicit CL7 fields required by the cutover
        contract.
        """

        prefix = "CL7_LOCKED_DISPATCH_PROOF="
        for transition in reversed(self.transitions):
            if transition.detail.startswith(prefix):
                try:
                    return _locked_dispatch_proof_from_exact_text(
                        transition.detail[len(prefix) :]
                    ).to_canonical_dict()
                except Exception as exc:
                    raise CentralOrderStateError(
                        "CL7 locked dispatch proof is invalid."
                    ) from exc
        return None

    @property
    def cl7_locked_dispatch_proof_sha256(self) -> str | None:
        proof = self.cl7_locked_dispatch_proof
        if proof is None:
            return None
        return LockedDispatchProof.from_canonical_dict(proof).sha256

    @classmethod
    def create(
        cls,
        candidate: CentralOrderCandidate,
        authorization: ExecutionAuthorization,
        *,
        queue_sequence: int,
        reserved_cash_kopecks: int,
        created_at: str | None = None,
    ) -> CentralOrderIntent:
        key = candidate.idempotency_key()
        intent_id = str(uuid5(NAMESPACE_URL, "central-order-v1|" + key))
        timestamp = _timestamp(created_at or _now(), "created_at")
        intent = cls(
            intent_id=intent_id,
            idempotency_key=key,
            queue_sequence=queue_sequence,
            candidate=candidate,
            authorization=authorization,
            status="QUEUED",
            reserved_cash_kopecks=reserved_cash_kopecks,
            broker_order_id=None,
            uncertainty_reason=None,
            outcome=None,
            executed_lots=0,
            reconciled_portfolio_revision=None,
            reconciled_portfolio_decision_checksum=None,
            reconciled_portfolio_snapshot_at=None,
            risk_execution_status=None,
            risk_execution_id=None,
            created_at=timestamp,
            updated_at=timestamp,
            transitions=(OrderTransition("QUEUED", timestamp, "admitted"),),
        )
        return intent

    def transition(
        self,
        status: str,
        *,
        detail: str,
        at: str | None = None,
        broker_order_id: str | None = None,
        uncertainty_reason: str | None = None,
        outcome: str | None = None,
        executed_lots: int | None = None,
        reconciled_portfolio_revision: int | None = None,
        reconciled_portfolio_decision_checksum: str | None = None,
        reconciled_portfolio_snapshot_at: str | None = None,
        risk_execution_status: str | None = None,
        risk_execution_id: str | None = None,
    ) -> CentralOrderIntent:
        normalized = str(status).strip().upper()
        if normalized not in _TRANSITIONS[self.status]:
            raise CentralOrderConflictError(
                f"Unsafe central-order transition {self.status} -> {normalized}."
            )
        timestamp = _timestamp(at or _now(), "transition.at")
        return replace(
            self,
            status=normalized,
            broker_order_id=broker_order_id or self.broker_order_id,
            uncertainty_reason=(
                uncertainty_reason
                if uncertainty_reason is not None
                else self.uncertainty_reason
            ),
            outcome=outcome if outcome is not None else self.outcome,
            executed_lots=(
                self.executed_lots if executed_lots is None else executed_lots
            ),
            reconciled_portfolio_revision=(
                self.reconciled_portfolio_revision
                if reconciled_portfolio_revision is None
                else reconciled_portfolio_revision
            ),
            reconciled_portfolio_decision_checksum=(
                self.reconciled_portfolio_decision_checksum
                if reconciled_portfolio_decision_checksum is None
                else reconciled_portfolio_decision_checksum
            ),
            reconciled_portfolio_snapshot_at=(
                self.reconciled_portfolio_snapshot_at
                if reconciled_portfolio_snapshot_at is None
                else reconciled_portfolio_snapshot_at
            ),
            risk_execution_status=(
                self.risk_execution_status
                if risk_execution_status is None
                else risk_execution_status
            ),
            risk_execution_id=(
                self.risk_execution_id
                if risk_execution_id is None
                else risk_execution_id
            ),
            updated_at=timestamp,
            transitions=(
                *self.transitions,
                OrderTransition(normalized, timestamp, detail),
            ),
        )

    def reauthorize(
        self,
        authorization: ExecutionAuthorization,
        *,
        candidate: CentralOrderCandidate | None = None,
        reserved_cash_kopecks: int | None = None,
        at: str | None = None,
    ) -> CentralOrderIntent:
        if self.status != "QUEUED":
            raise CentralOrderConflictError(
                "Only a QUEUED intent can receive a new authorization."
            )
        refreshed_candidate = candidate or self.candidate
        if refreshed_candidate.idempotency_key() != self.idempotency_key:
            raise CentralOrderConflictError(
                "Reauthorization cannot change intent identity."
            )
        if authorization.account_id != refreshed_candidate.account_id:
            raise CentralOrderConflictError("Reauthorization account mismatch.")
        if authorization.instrument_id != refreshed_candidate.instrument_id:
            raise CentralOrderConflictError("Reauthorization instrument mismatch.")
        if authorization.authorized_target_lots != refreshed_candidate.target_lots:
            raise CentralOrderConflictError("Reauthorization target mismatch.")
        if (
            authorization.portfolio_revision
            < self.authorization.portfolio_revision
        ):
            raise CentralOrderConflictError(
                "Reauthorization cannot roll back portfolio revision."
            )
        timestamp = _timestamp(at or _now(), "reauthorized_at")
        return replace(
            self,
            candidate=refreshed_candidate,
            authorization=authorization,
            reserved_cash_kopecks=(
                self.reserved_cash_kopecks
                if reserved_cash_kopecks is None
                else reserved_cash_kopecks
            ),
            updated_at=timestamp,
            transitions=(
                *self.transitions,
                OrderTransition(
                    "QUEUED",
                    timestamp,
                    "canonical preflight and Risk authorization refreshed",
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "idempotency_key": self.idempotency_key,
            "queue_sequence": self.queue_sequence,
            "candidate": self.candidate.to_dict(),
            "authorization": self.authorization.to_dict(),
            "status": self.status,
            "reserved_cash_kopecks": self.reserved_cash_kopecks,
            "broker_order_id": self.broker_order_id,
            "uncertainty_reason": self.uncertainty_reason,
            "outcome": self.outcome,
            "executed_lots": self.executed_lots,
            "reconciled_portfolio_revision": (
                self.reconciled_portfolio_revision
            ),
            "reconciled_portfolio_decision_checksum": (
                self.reconciled_portfolio_decision_checksum
            ),
            "reconciled_portfolio_snapshot_at": (
                self.reconciled_portfolio_snapshot_at
            ),
            "risk_execution_status": self.risk_execution_status,
            "risk_execution_id": self.risk_execution_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "transitions": [item.to_dict() for item in self.transitions],
            "cl7_locked_dispatch_proof": self.cl7_locked_dispatch_proof,
            "cl7_locked_dispatch_proof_sha256": (
                self.cl7_locked_dispatch_proof_sha256
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CentralOrderIntent:
        candidate = raw.get("candidate")
        authorization = raw.get("authorization")
        transitions = raw.get("transitions")
        if not isinstance(candidate, Mapping):
            raise CentralOrderStateError("Intent candidate must be an object.")
        if not isinstance(authorization, Mapping):
            raise CentralOrderStateError("Intent authorization must be an object.")
        if not isinstance(transitions, list):
            raise CentralOrderStateError("Intent transitions must be an array.")
        if any(not isinstance(item, Mapping) for item in transitions):
            raise CentralOrderStateError(
                "Every intent transition must be an object."
            )
        proof_present = "cl7_locked_dispatch_proof" in raw
        proof_sha_present = "cl7_locked_dispatch_proof_sha256" in raw
        if proof_present != proof_sha_present:
            raise CentralOrderStateError(
                "Central CL7 proof custody has a partial keyset."
            )
        intent = cls(
            intent_id=raw.get("intent_id", ""),
            idempotency_key=raw.get("idempotency_key", ""),
            queue_sequence=raw.get("queue_sequence", 0),
            candidate=CentralOrderCandidate.from_dict(candidate),
            authorization=ExecutionAuthorization.from_dict(authorization),
            status=raw.get("status", ""),
            reserved_cash_kopecks=raw.get("reserved_cash_kopecks", -1),
            broker_order_id=raw.get("broker_order_id"),
            uncertainty_reason=raw.get("uncertainty_reason"),
            outcome=raw.get("outcome"),
            executed_lots=raw.get("executed_lots", -1),
            reconciled_portfolio_revision=raw.get(
                "reconciled_portfolio_revision"
            ),
            reconciled_portfolio_decision_checksum=raw.get(
                "reconciled_portfolio_decision_checksum"
            ),
            reconciled_portfolio_snapshot_at=raw.get(
                "reconciled_portfolio_snapshot_at"
            ),
            risk_execution_status=raw.get("risk_execution_status"),
            risk_execution_id=raw.get("risk_execution_id"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            transitions=tuple(
                OrderTransition.from_dict(item)
                for item in transitions
            ),
        )
        supplied_proof = raw.get("cl7_locked_dispatch_proof")
        supplied_sha = raw.get("cl7_locked_dispatch_proof_sha256")
        if proof_present:
            if (
                supplied_proof != intent.cl7_locked_dispatch_proof
                or supplied_sha != intent.cl7_locked_dispatch_proof_sha256
            ):
                raise CentralOrderStateError(
                    "Central CL7 proof custody does not match its transition chain."
                )
        elif intent.cl7_locked_dispatch_proof is not None:
            raise CentralOrderStateError(
                "Central CL7 proof custody is missing explicit serialized fields."
            )
        return intent


@dataclass(frozen=True, slots=True)
class CentralOrderState:
    account_id: str
    revision: int
    next_sequence: int
    intents: tuple[CentralOrderIntent, ...]
    created_at: str
    updated_at: str
    version: int = CENTRAL_ORDER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if int(self.version) != CENTRAL_ORDER_SCHEMA_VERSION:
            raise CentralOrderStateError(
                f"Unsupported central-order schema {self.version!r}."
            )
        object.__setattr__(self, "version", CENTRAL_ORDER_SCHEMA_VERSION)
        object.__setattr__(self, "account_id", _required_text(self.account_id, "account_id"))
        object.__setattr__(self, "revision", _non_negative_int(self.revision, "revision"))
        object.__setattr__(
            self,
            "next_sequence",
            _positive_int(self.next_sequence, "next_sequence"),
        )
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))
        intents = tuple(self.intents)
        object.__setattr__(self, "intents", intents)
        ids = [item.intent_id for item in intents]
        keys = [item.idempotency_key for item in intents]
        sequences = [item.queue_sequence for item in intents]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise CentralOrderStateError("Duplicate central-order intent identity.")
        if len(sequences) != len(set(sequences)):
            raise CentralOrderStateError("Duplicate central-order queue sequence.")
        if any(item.candidate.account_id != self.account_id for item in intents):
            raise CentralOrderStateError("Central-order state mixes account scopes.")
        if sequences and self.next_sequence <= max(sequences):
            raise CentralOrderStateError("next_sequence does not advance the queue.")
        blockers = [item for item in intents if item.status in ACCOUNT_BLOCKING_STATUSES]
        if len(blockers) > 1:
            raise CentralOrderStateError(
                "More than one account-wide order is in a blocking state."
            )

    @classmethod
    def empty(cls, account_id: str, *, now: str | None = None) -> CentralOrderState:
        timestamp = _timestamp(now or _now(), "now")
        return cls(
            account_id=account_id,
            revision=0,
            next_sequence=1,
            intents=(),
            created_at=timestamp,
            updated_at=timestamp,
        )

    @property
    def reserved_cash_kopecks(self) -> int:
        return sum(
            item.reserved_cash_kopecks
            for item in self.intents
            if item.status in RESERVATION_STATUSES
        )

    @property
    def blocking_intent(self) -> CentralOrderIntent | None:
        return next(
            (
                item
                for item in self.intents
                if item.status in ACCOUNT_BLOCKING_STATUSES
            ),
            None,
        )

    @property
    def queued(self) -> tuple[CentralOrderIntent, ...]:
        return tuple(
            sorted(
                (item for item in self.intents if item.status == "QUEUED"),
                key=lambda item: item.queue_sequence,
            )
        )

    def replace_intent(self, updated: CentralOrderIntent) -> CentralOrderState:
        if updated.intent_id not in {item.intent_id for item in self.intents}:
            raise CentralOrderConflictError(f"Unknown intent {updated.intent_id}.")
        return replace(
            self,
            intents=tuple(
                updated if item.intent_id == updated.intent_id else item
                for item in self.intents
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "account_id": self.account_id,
            "revision": self.revision,
            "next_sequence": self.next_sequence,
            "reserved_cash_kopecks": self.reserved_cash_kopecks,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "intents": [item.to_dict() for item in self.intents],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CentralOrderState:
        raw_intents = raw.get("intents")
        if not isinstance(raw_intents, list):
            raise CentralOrderStateError("Central-order intents must be an array.")
        if any(not isinstance(item, Mapping) for item in raw_intents):
            raise CentralOrderStateError(
                "Every central-order intent must be an object."
            )
        state = cls(
            version=raw.get("version", 0),
            account_id=raw.get("account_id", ""),
            revision=raw.get("revision", -1),
            next_sequence=raw.get("next_sequence", 0),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            intents=tuple(
                CentralOrderIntent.from_dict(item)
                for item in raw_intents
            ),
        )
        if raw.get("reserved_cash_kopecks") != state.reserved_cash_kopecks:
            raise CentralOrderStateError("Reserved cash total mismatch.")
        return state


def central_reservation_projection_hash(
    state: CentralOrderState,
    *,
    excluded_reservation_ids: tuple[str, ...] = (),
    revision: int | None = None,
) -> str:
    """Hash the exact Central reservation projection used by Portfolio Risk."""

    if not isinstance(state, CentralOrderState):
        raise TypeError("state must be CentralOrderState.")
    if isinstance(excluded_reservation_ids, (str, bytes)):
        raise CentralOrderStateError(
            "excluded_reservation_ids must be an array."
        )
    excluded = tuple(
        sorted(
            {
                _required_text(item, "excluded_reservation_id")
                for item in excluded_reservation_ids
            }
        )
    )
    known = {item.intent_id for item in state.intents}
    unknown = sorted(set(excluded) - known)
    if unknown:
        raise CentralOrderStateError(
            "Excluded reservation does not exist in Central state: "
            + ", ".join(unknown)
        )
    selected_revision = (
        state.revision
        if revision is None
        else _non_negative_int(revision, "projection.revision")
    )
    payload = {
        "account_id": state.account_id,
        "revision": selected_revision,
        "excluded_reservation_ids": list(excluded),
        "reservations": [
            {
                "intent_id": intent.intent_id,
                "instrument_id": intent.candidate.instrument_id,
                "status": intent.status,
                "reserved_cash_kopecks": intent.reserved_cash_kopecks,
            }
            for intent in state.intents
            if intent.status in RESERVATION_STATUSES
            and intent.intent_id not in excluded
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class CentralOrderStore:
    """Checksum-managed, lock-serialized account-wide queue persistence."""

    SCHEMA_VERSION = CENTRAL_ORDER_SCHEMA_VERSION

    def __init__(
        self,
        path: str | Path,
        *,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))

    def initialize(self, account_id: str) -> CentralOrderState:
        selected = _required_text(account_id, "account_id")
        try:
            with InterProcessFileLock(
                self.lock_path,
                timeout_seconds=self.lock_timeout_seconds,
            ):
                if self.path.exists():
                    return self._load_unlocked(expected_account_id=selected)
                state = CentralOrderState.empty(selected)
                self._save_unlocked(state)
                return state
        except (LockUnavailableError, StatePersistenceError) as exc:
            raise CentralOrderStateError(str(exc)) from exc

    def load(
        self,
        *,
        expected_account_id: str | None = None,
    ) -> CentralOrderState:
        if not self.path.exists():
            raise CentralOrderStateError(
                f"Central-order state is missing: {self.path}"
            )
        return self._load_unlocked(expected_account_id=expected_account_id)

    def mutate(
        self,
        expected_account_id: str,
        operation: Callable[[CentralOrderState], tuple[CentralOrderState, T]],
    ) -> tuple[CentralOrderState, T]:
        try:
            with InterProcessFileLock(
                self.lock_path,
                timeout_seconds=self.lock_timeout_seconds,
            ):
                current = self._load_unlocked(
                    expected_account_id=expected_account_id
                )
                candidate, result = operation(current)
                if not isinstance(candidate, CentralOrderState):
                    raise CentralOrderStateError(
                        "Central-order mutation must return CentralOrderState."
                    )
                if candidate.account_id != current.account_id:
                    raise CentralOrderStateError(
                        "Central-order mutation cannot change account scope."
                    )
                if candidate.revision != current.revision:
                    raise CentralOrderStateError(
                        "Central-order mutation cannot set revision directly."
                    )
                if candidate != current:
                    candidate = replace(
                        candidate,
                        revision=current.revision + 1,
                        updated_at=_now(),
                    )
                    self._save_unlocked(candidate)
                return candidate, result
        except (LockUnavailableError, StatePersistenceError) as exc:
            raise CentralOrderStateError(str(exc)) from exc

    def _load_unlocked(
        self,
        *,
        expected_account_id: str | None,
    ) -> CentralOrderState:
        try:
            raw = read_json_verified(
                self.path,
                supported_versions={self.SCHEMA_VERSION},
            )
        except StatePersistenceError as exc:
            raise CentralOrderStateError(str(exc)) from exc
        state = CentralOrderState.from_dict(raw)
        if expected_account_id and state.account_id != str(expected_account_id):
            raise CentralOrderStateError(
                "Central-order state belongs to a different account."
            )
        return state

    def _save_unlocked(self, state: CentralOrderState) -> None:
        atomic_write_json(
            self.path,
            state.to_dict(),
            write_checksum=True,
            keep_last_good=True,
            validate_roundtrip=True,
        )


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    intent: CentralOrderIntent
    idempotent: bool
    state_revision: int
    reserved_cash_kopecks: int
    reauthorized: bool = False
    replaced_intent_id: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchPreparation:
    intent: CentralOrderIntent
    state_revision: int
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.execution_authorized:
            raise CentralOrderStateError(
                "DispatchPreparation cannot authorize broker execution."
            )


class LockedCentralDispatch:
    """One exact CL7 Central lease held through provider outcome persistence."""

    def __init__(
        self,
        manager: CentralOrderManager,
        state: CentralOrderState,
        intent: CentralOrderIntent,
        proof: LockedDispatchProof,
    ) -> None:
        self._manager = manager
        self._state = state
        self.intent = intent
        self.proof = proof
        self._outcome_persisted = False

    @property
    def state_revision(self) -> int:
        return self._state.revision

    @property
    def outcome_persisted(self) -> bool:
        return self._outcome_persisted

    def _persist(self, updated: CentralOrderIntent) -> CentralOrderIntent:
        if self._outcome_persisted:
            raise CentralOrderConflictError(
                "CL7 locked dispatch outcome was already persisted."
            )
        candidate = replace(
            self._state.replace_intent(updated),
            revision=self._state.revision + 1,
            updated_at=_now(),
        )
        self._manager.store._save_unlocked(candidate)
        readback = self._manager.store._load_unlocked(
            expected_account_id=self._manager.account_id
        )
        exact = next(
            (item for item in readback.intents if item.intent_id == updated.intent_id),
            None,
        )
        if exact is None or exact.to_dict() != updated.to_dict():
            raise CentralOrderStateError(
                "CL7 Central outcome read-back is not exact."
            )
        self._state = readback
        self.intent = exact
        self._outcome_persisted = True
        return exact

    def mark_submitted(self, *, broker_order_id: str) -> CentralOrderIntent:
        return self._persist(
            self.intent.transition(
                "SUBMITTED",
                detail="CL7 exact provider response was correlated",
                broker_order_id=_required_text(broker_order_id, "broker_order_id"),
            )
        )

    def mark_uncertain(self, *, reason: str) -> CentralOrderIntent:
        return self._persist(
            self.intent.transition(
                "UNCERTAIN",
                detail="CL7 exact submission requires lookup recovery",
                uncertainty_reason=_required_text(reason, "reason"),
            )
        )

    def mark_submission_rejected(self, *, reason: str) -> CentralOrderIntent:
        return self._persist(
            self.intent.transition(
                "FAILED",
                detail=_required_text(reason, "reason"),
                outcome="SUBMISSION_REJECTED",
            )
        )

    def mark_pre_submit_failed(self, *, reason: str) -> CentralOrderIntent:
        return self._persist(
            self.intent.transition(
                "FAILED",
                detail=_required_text(reason, "reason"),
                outcome="PRE_SUBMIT_FAILED",
            )
        )


class CentralOrderManager:
    """Account-wide queue and reservation coordinator without broker methods."""

    def __init__(
        self,
        store: CentralOrderStore,
        *,
        account_id: str,
        journal: EventJournal | None = None,
    ) -> None:
        self.store = store
        self.account_id = _required_text(account_id, "account_id")
        self.journal = journal
        self.store.initialize(self.account_id)

    def state(self) -> CentralOrderState:
        return self.store.load(expected_account_id=self.account_id)

    def inspect_locked(self, reader: Callable[[CentralOrderState], T]) -> T:
        """Read one Central state while participating in the store lock order."""

        if not callable(reader):
            raise TypeError("reader must be callable.")

        def operation(
            state: CentralOrderState,
        ) -> tuple[CentralOrderState, T]:
            return state, reader(state)

        _, result = self.store.mutate(self.account_id, operation)
        return result

    def resolve_cl7_pre_submit(
        self,
        *,
        expected_intent_id: str,
        expected_proof_sha256: str,
        authority_record_revision: int,
        authority_record_sha256: str,
        account_scope_sha256: str,
        identity_key_id: str,
        identity_key: bytes,
        ledger_revision: int,
        ledger_head_sha256: str,
    ) -> CentralOrderIntent:
        """Resolve exactly one D3 lease for which provider POST is impossible."""

        revision = _non_negative_int(
            authority_record_revision,
            "authority_record_revision",
        )
        authority_sha = _sha256_text(
            authority_record_sha256,
            "authority_record_sha256",
        )
        selected_intent = _required_text(expected_intent_id, "expected_intent_id")
        proof_sha = _sha256_text(expected_proof_sha256, "expected_proof_sha256")
        account_sha = _sha256_text(account_scope_sha256, "account_scope_sha256")
        key_id = _required_text(identity_key_id, "identity_key_id")
        expected_ledger_revision = _non_negative_int(
            ledger_revision,
            "ledger_revision",
        )
        expected_ledger_head = _sha256_text(
            ledger_head_sha256,
            "ledger_head_sha256",
        )
        if type(identity_key) is not bytes:
            raise CentralOrderStateError("CL7 identity key is invalid.")

        def operation(
            state: CentralOrderState,
        ) -> tuple[CentralOrderState, CentralOrderIntent]:
            matches: list[CentralOrderIntent] = []
            for item in state.intents:
                proof_raw = item.cl7_locked_dispatch_proof
                if proof_raw is None:
                    continue
                proof = LockedDispatchProof.from_canonical_dict(proof_raw)
                if (
                    item.intent_id == selected_intent
                    and item.status == "IN_FLIGHT"
                    and proof.sha256 == proof_sha
                    and proof.authority_record_revision == revision
                    and proof.authority_record_sha256 == authority_sha
                    and proof.account_scope_sha256 == account_sha
                    and proof.identity_key_id == key_id
                    and proof.ledger_revision == expected_ledger_revision
                    and proof.ledger_head_sha256 == expected_ledger_head
                    and state.revision == proof.central_order_revision + 1
                ):
                    proof.verify_identity(
                        raw_intent_id=item.intent_id,
                        identity_key=identity_key,
                    )
                    matches.append(item)
            if len(matches) != 1:
                raise CentralOrderConflictError(
                    "Exact CL7 D3 correlation is missing or ambiguous."
                )
            updated = matches[0].transition(
                "FAILED",
                detail="CL7 D3 recovered before durable provider-attempt marker",
                outcome="PRE_SUBMIT_FAILED",
            )
            return state.replace_intent(updated), updated

        state, updated = self.store.mutate(self.account_id, operation)
        exact = next(
            (item for item in state.intents if item.intent_id == updated.intent_id),
            None,
        )
        if exact is None or exact.to_dict() != updated.to_dict():
            raise CentralOrderStateError("CL7 D3 recovery read-back is not exact.")
        return exact

    def enqueue(
        self,
        candidate: CentralOrderCandidate,
        authorization: ExecutionAuthorization,
        *,
        cash_buffer_bps: int = 100,
    ) -> EnqueueResult:
        if candidate.account_id != self.account_id:
            raise CentralOrderConflictError("Candidate account scope mismatch.")
        if authorization.account_id != self.account_id:
            raise CentralOrderConflictError("Authorization account scope mismatch.")
        if candidate.instrument_id != authorization.instrument_id:
            raise CentralOrderConflictError(
                "Candidate/authorization instrument mismatch."
            )
        if candidate.target_lots != authorization.authorized_target_lots:
            raise CentralOrderConflictError(
                "Candidate target differs from Risk-approved target."
            )
        reservation = candidate.reservation_kopecks(
            cash_buffer_bps=cash_buffer_bps
        )

        def operation(
            state: CentralOrderState,
        ) -> tuple[CentralOrderState, tuple[CentralOrderIntent, bool]]:
            key = candidate.idempotency_key()
            duplicate = next(
                (item for item in state.intents if item.idempotency_key == key),
                None,
            )
            if duplicate is not None:
                return state, (duplicate, True)
            blocker = state.blocking_intent
            if blocker is not None:
                raise CentralOrderConflictError(
                    "Account-wide pending/uncertain gate is active: "
                    f"{blocker.intent_id} {blocker.status}."
                )
            active_same_instrument = next(
                (
                    item
                    for item in state.intents
                    if item.status in RESERVATION_STATUSES
                    and item.candidate.instrument_id == candidate.instrument_id
                ),
                None,
            )
            if active_same_instrument is not None:
                raise CentralOrderConflictError(
                    "An active intent already owns this account/instrument "
                    f"scope: {active_same_instrument.intent_id}."
                )
            if (
                state.reserved_cash_kopecks + reservation
                > authorization.available_cash_kopecks
            ):
                raise CentralOrderConflictError(
                    "Insufficient unreserved RUB cash for queued BUY intents."
                )
            intent = CentralOrderIntent.create(
                candidate,
                authorization,
                queue_sequence=state.next_sequence,
                reserved_cash_kopecks=reservation,
            )
            updated = replace(
                state,
                next_sequence=state.next_sequence + 1,
                intents=(*state.intents, intent),
            )
            return updated, (intent, False)

        state, (intent, idempotent) = self.store.mutate(
            self.account_id,
            operation,
        )
        if not idempotent:
            self._record("CENTRAL_ORDER_ENQUEUED", intent)
        return EnqueueResult(
            intent=intent,
            idempotent=idempotent,
            state_revision=state.revision,
            reserved_cash_kopecks=state.reserved_cash_kopecks,
        )

    def admit_portfolio(
        self,
        admission_builder: Callable[
            [CentralOrderState],
            tuple[CentralOrderCandidate, ExecutionAuthorization],
        ],
        *,
        cash_buffer_bps: int = 100,
    ) -> EnqueueResult:
        """Evaluate and reserve one M4 candidate under the Central lock.

        The builder receives the current serialized account projection.  It
        must return a final candidate and an authorization containing an
        unfinalized Portfolio Risk proof derived from that exact projection.
        """

        if not callable(admission_builder):
            raise TypeError("admission_builder must be callable.")

        def operation(
            state: CentralOrderState,
        ) -> tuple[
            CentralOrderState,
            tuple[CentralOrderIntent, bool, bool, str | None],
        ]:
            blocker = state.blocking_intent
            if blocker is not None:
                raise CentralOrderConflictError(
                    "Account-wide pending/uncertain gate is active: "
                    f"{blocker.intent_id} {blocker.status}."
                )
            candidate, authorization = admission_builder(state)
            if not isinstance(candidate, CentralOrderCandidate):
                raise CentralOrderStateError(
                    "Portfolio admission builder must return CentralOrderCandidate."
                )
            if not isinstance(authorization, ExecutionAuthorization):
                raise CentralOrderStateError(
                    "Portfolio admission builder must return ExecutionAuthorization."
                )
            proof = authorization.portfolio_risk
            if proof is None:
                raise CentralOrderConflictError(
                    "Authoritative admission requires Portfolio Risk proof."
                )
            if proof.finalized:
                raise CentralOrderStateError(
                    "Portfolio Risk proof must be finalized by Central admission."
                )
            if candidate.account_id != self.account_id:
                raise CentralOrderConflictError("Candidate account scope mismatch.")
            if authorization.account_id != self.account_id:
                raise CentralOrderConflictError(
                    "Authorization account scope mismatch."
                )
            if candidate.instrument_id != authorization.instrument_id:
                raise CentralOrderConflictError(
                    "Candidate/authorization instrument mismatch."
                )
            if candidate.target_lots != authorization.authorized_target_lots:
                raise CentralOrderConflictError(
                    "Candidate target differs from authorized Portfolio Risk target."
                )
            lower, upper = sorted(
                (
                    candidate.current_lots,
                    proof.single_risk_approved_target_lots,
                )
            )
            if not lower <= proof.approved_target_lots <= upper:
                raise CentralOrderConflictError(
                    "Portfolio Risk target exceeds the single-order Risk boundary."
                )
            if proof.central_order_revision != state.revision:
                raise CentralOrderConflictError(
                    "Portfolio Risk proof was not evaluated on the locked Central revision."
                )
            expected_pre_projection = central_reservation_projection_hash(
                state,
                excluded_reservation_ids=proof.excluded_reservation_ids,
            )
            if proof.reservation_projection_hash != expected_pre_projection:
                raise CentralOrderConflictError(
                    "Portfolio Risk reservation projection changed before admission."
                )

            duplicate = next(
                (
                    item
                    for item in state.intents
                    if item.idempotency_key == candidate.idempotency_key()
                ),
                None,
            )
            active_same_instrument = next(
                (
                    item
                    for item in state.intents
                    if item.status in RESERVATION_STATUSES
                    and item.candidate.instrument_id == candidate.instrument_id
                ),
                None,
            )
            expected_excluded = (
                (active_same_instrument.intent_id,)
                if active_same_instrument is not None
                else ()
            )
            if proof.excluded_reservation_ids != expected_excluded:
                raise CentralOrderConflictError(
                    "Portfolio Risk proof does not match the replaceable reservation scope."
                )
            if duplicate is not None and duplicate.status != "QUEUED":
                return state, (duplicate, True, False, None)

            if duplicate is not None:
                reservation = candidate.reservation_kopecks(
                    cash_buffer_bps=cash_buffer_bps
                )
                projected_reserved = (
                    state.reserved_cash_kopecks
                    - duplicate.reserved_cash_kopecks
                    + reservation
                )
                if projected_reserved > authorization.available_cash_kopecks:
                    raise CentralOrderConflictError(
                        "Canonical RUB cash no longer covers refreshed reservations."
                    )
                provisional = duplicate.reauthorize(
                    authorization,
                    candidate=candidate,
                    reserved_cash_kopecks=reservation,
                )
                projected_state = state.replace_intent(provisional)
                projected_revision = state.revision + 1
                projected_hash = central_reservation_projection_hash(
                    projected_state,
                    revision=projected_revision,
                )
                finalized = replace(
                    authorization,
                    portfolio_risk=proof.finalize(
                        central_revision=projected_revision,
                        reservation_projection_hash=projected_hash,
                    ),
                )
                refreshed = replace(provisional, authorization=finalized)
                return state.replace_intent(refreshed), (
                    refreshed,
                    True,
                    True,
                    None,
                )

            replaced_id = None
            working = state
            if active_same_instrument is not None:
                replaced_id = active_same_instrument.intent_id
                cancelled = active_same_instrument.transition(
                    "CANCELLED",
                    detail=(
                        "atomically superseded by a newer Portfolio Risk "
                        "admission"
                    ),
                    outcome="SUPERSEDED",
                )
                working = state.replace_intent(cancelled)

            reservation = candidate.reservation_kopecks(
                cash_buffer_bps=cash_buffer_bps
            )
            if (
                working.reserved_cash_kopecks + reservation
                > authorization.available_cash_kopecks
            ):
                raise CentralOrderConflictError(
                    "Insufficient unreserved RUB cash for queued BUY intents."
                )
            provisional = CentralOrderIntent.create(
                candidate,
                authorization,
                queue_sequence=state.next_sequence,
                reserved_cash_kopecks=reservation,
            )
            projected = replace(
                working,
                next_sequence=state.next_sequence + 1,
                intents=(*working.intents, provisional),
            )
            projected_revision = state.revision + 1
            projected_hash = central_reservation_projection_hash(
                projected,
                revision=projected_revision,
            )
            finalized = replace(
                authorization,
                portfolio_risk=proof.finalize(
                    central_revision=projected_revision,
                    reservation_projection_hash=projected_hash,
                ),
            )
            intent = replace(provisional, authorization=finalized)
            updated = replace(
                projected,
                intents=tuple(
                    intent if item.intent_id == intent.intent_id else item
                    for item in projected.intents
                ),
            )
            return updated, (intent, False, False, replaced_id)

        state, (intent, idempotent, reauthorized, replaced_id) = self.store.mutate(
            self.account_id,
            operation,
        )
        if reauthorized:
            self._record("CENTRAL_ORDER_REAUTHORIZED", intent)
        elif not idempotent:
            self._record("CENTRAL_ORDER_ENQUEUED", intent)
        return EnqueueResult(
            intent=intent,
            idempotent=idempotent,
            state_revision=state.revision,
            reserved_cash_kopecks=state.reserved_cash_kopecks,
            reauthorized=reauthorized,
            replaced_intent_id=replaced_id,
        )

    def reauthorize_queued(
        self,
        intent_id: str,
        authorization: ExecutionAuthorization,
    ) -> CentralOrderIntent:
        selected = _required_text(intent_id, "intent_id")

        def operation(
            state: CentralOrderState,
        ) -> tuple[CentralOrderState, CentralOrderIntent]:
            current = next(
                (item for item in state.intents if item.intent_id == selected),
                None,
            )
            if current is None:
                raise CentralOrderConflictError(f"Unknown intent {selected}.")
            updated = current.reauthorize(authorization)
            if (
                state.reserved_cash_kopecks
                > authorization.available_cash_kopecks
            ):
                raise CentralOrderConflictError(
                    "Canonical RUB cash no longer covers active reservations."
                )
            return state.replace_intent(updated), updated

        _, intent = self.store.mutate(self.account_id, operation)
        self._record("CENTRAL_ORDER_REAUTHORIZED", intent)
        return intent

    def prepare_next(
        self,
        portfolio_repository: PortfolioRepository,
        *,
        expected_intent_id: str | None = None,
        locked_portfolio_state: PortfolioState | None = None,
        portfolio_risk_validator: Callable[
            [PortfolioState, CentralOrderState, CentralOrderIntent],
            Any,
        ]
        | None = None,
    ) -> DispatchPreparation | None:
        expected = (
            _required_text(expected_intent_id, "expected_intent_id")
            if expected_intent_id is not None
            else None
        )

        def operation(
            state: CentralOrderState,
        ) -> tuple[CentralOrderState, CentralOrderIntent | None]:
            blocker = state.blocking_intent
            if blocker is not None:
                raise CentralOrderConflictError(
                    "Account-wide pending/uncertain gate is active: "
                    f"{blocker.intent_id} {blocker.status}."
                )
            if not state.queued:
                return state, None
            intent = state.queued[0]
            if expected is not None and intent.intent_id != expected:
                raise CentralOrderConflictError(
                    "Central-order queue head changed after external precheck."
                )
            portfolio_state = self._validate_portfolio_for_dispatch(
                portfolio_repository,
                intent,
                locked_portfolio_state=locked_portfolio_state,
            )
            proof = intent.authorization.portfolio_risk
            if proof is not None:
                if not proof.finalized:
                    raise CentralOrderConflictError(
                        "Portfolio Risk proof is not finalized."
                    )
                if state.revision != proof.admission_central_revision:
                    raise CentralOrderConflictError(
                        "Central queue changed after Portfolio Risk admission."
                    )
                if (
                    central_reservation_projection_hash(state)
                    != proof.admission_reservation_projection_hash
                ):
                    raise CentralOrderConflictError(
                        "Central reservation projection changed after Portfolio Risk admission."
                    )
            if portfolio_risk_validator is not None:
                portfolio_risk_validator(portfolio_state, state, intent)
            prepared = intent.transition(
                "IN_FLIGHT",
                detail="canonical snapshot rechecked; broker POST remains external",
            )
            return state.replace_intent(prepared), prepared

        state, intent = self.store.mutate(self.account_id, operation)
        if intent is None:
            return None
        self._record("CENTRAL_ORDER_PREPARED", intent)
        return DispatchPreparation(intent=intent, state_revision=state.revision)

    @contextmanager
    def locked_dispatch_lease(
        self,
        portfolio_repository: PortfolioRepository,
        *,
        expected_intent_id: str,
        locked_portfolio_state: PortfolioState,
        validator: Callable[
            [CentralOrderState, CentralOrderIntent], LockedDispatchProof
        ],
    ) -> Iterator[LockedCentralDispatch]:
        """Freeze Central from the final CL7 projection through POST outcome."""

        expected = _required_text(expected_intent_id, "expected_intent_id")
        if not isinstance(locked_portfolio_state, PortfolioState):
            raise CentralOrderStateError(
                "locked_portfolio_state must be a PortfolioState."
            )
        if not callable(validator):
            raise TypeError("validator must be callable.")
        lock = InterProcessFileLock(
            self.store.lock_path,
            timeout_seconds=self.store.lock_timeout_seconds,
        )
        try:
            lock.acquire()
            state = self.store._load_unlocked(expected_account_id=self.account_id)
            blocker = state.blocking_intent
            if blocker is not None:
                raise CentralOrderConflictError(
                    "Account-wide pending/uncertain gate is active."
                )
            if not state.queued or state.queued[0].intent_id != expected:
                raise CentralOrderConflictError(
                    "Central-order queue head changed before CL7 locked dispatch."
                )
            intent = state.queued[0]
            self._validate_portfolio_for_dispatch(
                portfolio_repository,
                intent,
                locked_portfolio_state=locked_portfolio_state,
            )
            proof = validator(state, intent)
            if not isinstance(proof, LockedDispatchProof):
                raise CentralOrderStateError(
                    "CL7 validator must return LockedDispatchProof."
                )
            if proof.central_order_revision != state.revision:
                raise CentralOrderConflictError(
                    "CL7 proof does not bind the pre-transition Central revision."
                )
            if proof.central_reservation_projection_hash != central_reservation_projection_hash(state):
                raise CentralOrderConflictError(
                    "CL7 proof does not bind the current reservation projection."
                )
            proof_json = proof.canonical_bytes.decode("ascii")
            prepared = intent.transition(
                "IN_FLIGHT",
                detail="CL7_LOCKED_DISPATCH_PROOF=" + proof_json,
            )
            persisted = replace(
                state.replace_intent(prepared),
                revision=state.revision + 1,
                updated_at=_now(),
            )
            self.store._save_unlocked(persisted)
            readback = self.store._load_unlocked(expected_account_id=self.account_id)
            readback_intent = next(
                item for item in readback.intents if item.intent_id == expected
            )
            if (
                readback.revision != proof.central_order_revision + 1
                or readback_intent.status != "IN_FLIGHT"
                or readback_intent.cl7_locked_dispatch_proof_sha256 != proof.sha256
                or readback_intent.cl7_locked_dispatch_proof != proof.to_canonical_dict()
            ):
                raise CentralOrderStateError(
                    "CL7 locked Central transition read-back is not exact."
                )
            lease = LockedCentralDispatch(self, readback, readback_intent, proof)
            yield lease
        except (LockUnavailableError, StatePersistenceError) as exc:
            raise CentralOrderStateError(str(exc)) from exc
        finally:
            lock.release()

    def mark_submitted(
        self,
        intent_id: str,
        *,
        broker_order_id: str,
    ) -> CentralOrderIntent:
        return self._transition(
            intent_id,
            "SUBMITTED",
            detail="external execution adapter reported broker acceptance",
            broker_order_id=_required_text(broker_order_id, "broker_order_id"),
            event_type="CENTRAL_ORDER_SUBMITTED",
        )

    def mark_uncertain(
        self,
        intent_id: str,
        *,
        reason: str,
    ) -> CentralOrderIntent:
        return self._transition(
            intent_id,
            "UNCERTAIN",
            detail="submission outcome requires reconciliation",
            uncertainty_reason=_required_text(reason, "reason"),
            event_type="CENTRAL_ORDER_UNCERTAIN",
        )

    def mark_pre_submit_failed(
        self,
        intent_id: str,
        *,
        reason: str,
    ) -> CentralOrderIntent:
        return self._transition(
            intent_id,
            "FAILED",
            detail=_required_text(reason, "reason"),
            outcome="PRE_SUBMIT_FAILED",
            event_type="CENTRAL_ORDER_FAILED",
        )

    def mark_submission_rejected(
        self,
        intent_id: str,
        *,
        reason: str,
    ) -> CentralOrderIntent:
        return self._transition(
            intent_id,
            "FAILED",
            detail=_required_text(reason, "reason"),
            outcome="SUBMISSION_REJECTED",
            event_type="CENTRAL_ORDER_SUBMISSION_REJECTED",
        )

    def cancel_queued(
        self,
        intent_id: str,
        *,
        reason: str,
    ) -> CentralOrderIntent:
        return self._transition(
            intent_id,
            "CANCELLED",
            detail=_required_text(reason, "reason"),
            outcome="OPERATOR_CANCELLED",
            event_type="CENTRAL_ORDER_CANCELLED",
        )

    def mark_reconciled(
        self,
        intent_id: str,
        *,
        portfolio_repository: PortfolioRepository,
        outcome: str,
        executed_lots: int,
        risk_runtime: Any | None = None,
        execution_price_rub: float | None = None,
        execution_price_source: str | None = None,
        expected_intent: CentralOrderIntent | None = None,
        expected_portfolio_state: PortfolioState | None = None,
    ) -> CentralOrderIntent:
        if expected_intent is not None and type(expected_intent) is not CentralOrderIntent:
            raise CentralOrderConflictError("Exact reconciliation intent is invalid.")
        if expected_portfolio_state is not None and type(expected_portfolio_state) is not PortfolioState:
            raise CentralOrderConflictError("Exact reconciliation portfolio is invalid.")
        selected = _required_text(intent_id, "intent_id")
        normalized_outcome = _required_text(outcome, "outcome").upper()
        normalized_executed_lots = _non_negative_int(
            executed_lots,
            "executed_lots",
        )

        with portfolio_repository.locked_snapshot(
            expected_account_id=self.account_id
        ) as locked_portfolio:
            if (expected_portfolio_state is not None
                    and locked_portfolio.to_dict() != expected_portfolio_state.to_dict()):
                raise CentralOrderConflictError("Exact reconciliation portfolio changed.")
            observed = next(
                (
                    item
                    for item in self.state().intents
                    if item.intent_id == selected
                ),
                None,
            )
            if observed is None:
                raise CentralOrderConflictError(f"Unknown intent {selected}.")
            # This API has no exact cash-settlement evidence parameter. Do not
            # retire reservations or record Risk from position proof alone.
            if observed.cl7_locked_dispatch_proof is not None:
                raise CentralOrderConflictError("EXACT_SETTLEMENT_REQUIRED")
            if expected_intent is not None and observed != expected_intent:
                raise CentralOrderConflictError("Exact reconciliation intent changed.")
            lease = self._validate_reconciliation(
                portfolio_repository,
                observed,
                outcome=normalized_outcome,
                executed_lots=normalized_executed_lots,
                locked_portfolio_state=locked_portfolio,
            )
            if normalized_executed_lots:
                risk_status, risk_execution_id = self._record_risk_execution(
                    risk_runtime,
                    observed,
                    lease,
                    executed_lots=normalized_executed_lots,
                    execution_price_rub=execution_price_rub,
                    execution_price_source=execution_price_source,
                )
            else:
                risk_status, risk_execution_id = "NOT_REQUIRED", None

            def operation(
                state: CentralOrderState,
            ) -> tuple[CentralOrderState, CentralOrderIntent]:
                current = next(
                    (item for item in state.intents if item.intent_id == selected),
                    None,
                )
                if current is None:
                    raise CentralOrderConflictError(f"Unknown intent {selected}.")
                if expected_intent is not None and current != expected_intent:
                    raise CentralOrderConflictError("Exact reconciliation intent changed.")
                if current.status != observed.status:
                    raise CentralOrderConflictError(
                        "Central order changed during reconciliation."
                    )
                self._validate_reconciliation(
                    portfolio_repository,
                    current,
                    outcome=normalized_outcome,
                    executed_lots=normalized_executed_lots,
                    locked_portfolio_state=locked_portfolio,
                )
                updated = current.transition(
                    "RECONCILED",
                    detail="canonical broker reconciliation completed",
                    outcome=normalized_outcome,
                    executed_lots=normalized_executed_lots,
                    reconciled_portfolio_revision=lease.revision,
                    reconciled_portfolio_decision_checksum=(
                        lease.decision_checksum
                    ),
                    reconciled_portfolio_snapshot_at=lease.state.snapshot_at,
                    risk_execution_status=risk_status,
                    risk_execution_id=risk_execution_id,
                )
                return state.replace_intent(updated), updated

            _, intent = self.store.mutate(self.account_id, operation)
        self._record("CENTRAL_ORDER_RECONCILED", intent)
        return intent

    def _record_risk_execution(
        self,
        risk_runtime: Any | None,
        intent: CentralOrderIntent,
        lease: PortfolioSnapshotLease,
        *,
        executed_lots: int,
        execution_price_rub: float | None,
        execution_price_source: str | None,
    ) -> tuple[str, str]:
        if risk_runtime is None:
            raise CentralOrderConflictError(
                "Confirmed execution requires Risk accounting runtime."
            )
        if str(getattr(risk_runtime, "account_id", "")) != self.account_id:
            raise CentralOrderConflictError(
                "Risk accounting runtime account scope mismatch."
            )
        if str(getattr(risk_runtime, "mode", "")).upper() != "SANDBOX_EXECUTION":
            raise CentralOrderConflictError(
                "Risk accounting requires SANDBOX_EXECUTION runtime."
            )
        try:
            price = float(execution_price_rub)
        except (TypeError, ValueError) as exc:
            raise CentralOrderConflictError(
                "Confirmed execution has no usable execution price."
            ) from exc
        if not math.isfinite(price) or price <= 0:
            raise CentralOrderConflictError(
                "Confirmed execution has no usable execution price."
            )
        position = lease.state.position(intent.candidate.instrument_id)
        actual_lots = int(position.actual_lots) if position is not None else 0
        signed_lots = (
            int(executed_lots)
            if intent.candidate.direction == "BUY"
            else -int(executed_lots)
        )
        expected_lots = intent.candidate.current_lots + signed_lots
        proof = {
            "position_reconciled": True,
            "canonical_reconciled": True,
            "account_id": self.account_id,
            "instrument_id": intent.candidate.instrument_id,
            "expected_lots_after": expected_lots,
            "actual_lots_after": actual_lots,
            "canonical_revision": lease.revision,
            "canonical_decision_checksum": lease.decision_checksum,
            "canonical_snapshot_at": lease.state.snapshot_at,
        }
        cash = lease.state.account.cash("rub")
        portfolio = {
            "totalAmountPortfolio": lease.state.account.total_value,
            "totalAmountCurrencies": cash.available if cash is not None else None,
            "totalAmountShares": lease.state.account.securities_value,
        }
        result = risk_runtime.record_execution(
            execution_id=intent.intent_id,
            executed_at=datetime.fromisoformat(
                lease.state.snapshot_at.replace("Z", "+00:00")
            ),
            signed_lots=signed_lots,
            price_rub=price,
            lot_size=intent.candidate.lot_size,
            portfolio=portfolio,
            decision_id=intent.authorization.risk_decision_id,
            expected_policy_hash=intent.authorization.risk_policy_hash,
            price_source=(
                str(execution_price_source or "").strip()
                or "provider_order_state"
            ),
            execution_source="STRATEGY",
            reconciliation_confirmed_at=lease.state.snapshot_at,
            reconciliation_proof=proof,
        )
        status = str(getattr(result, "status", "")).upper()
        if not bool(getattr(result, "enforced", False)):
            raise CentralOrderConflictError(
                "Risk execution accounting was not enforced."
            )
        if str(getattr(result, "mode", "")).upper() != "SANDBOX_EXECUTION":
            raise CentralOrderConflictError(
                "Risk execution accounting mode mismatch."
            )
        if getattr(result, "error", None) or status not in {
            "RECORDED",
            "DUPLICATE",
        }:
            raise CentralOrderConflictError(
                "Risk execution accounting did not complete: "
                f"{getattr(result, 'error', None) or status or 'UNKNOWN'}."
            )
        if str(getattr(result, "execution_id", "")) != intent.intent_id:
            raise CentralOrderConflictError(
                "Risk execution accounting ID mismatch."
            )
        if getattr(result, "decision_id", None) != (
            intent.authorization.risk_decision_id
        ):
            raise CentralOrderConflictError(
                "Risk execution decision ID mismatch."
            )
        if getattr(result, "policy_hash", None) != (
            intent.authorization.risk_policy_hash
        ):
            raise CentralOrderConflictError(
                "Risk execution policy hash mismatch."
            )
        result_proof = dict(getattr(result, "reconciliation_proof", None) or {})
        for key in (
            "canonical_revision",
            "canonical_decision_checksum",
            "canonical_snapshot_at",
            "expected_lots_after",
            "actual_lots_after",
        ):
            if result_proof.get(key) != proof[key]:
                raise CentralOrderConflictError(
                    f"Risk execution reconciliation proof mismatch: {key}."
                )
        return status, intent.intent_id

    def recover_after_restart(self) -> CentralOrderIntent | None:
        def operation(
            state: CentralOrderState,
        ) -> tuple[CentralOrderState, CentralOrderIntent | None]:
            in_flight = next(
                (item for item in state.intents if item.status == "IN_FLIGHT"),
                None,
            )
            if in_flight is None:
                return state, None
            recovered = in_flight.transition(
                "UNCERTAIN",
                detail="restart observed an unfinished dispatch handoff",
                uncertainty_reason=(
                    "Process restarted after dispatch preparation; automatic "
                    "resubmit is forbidden."
                ),
            )
            return state.replace_intent(recovered), recovered

        _, intent = self.store.mutate(self.account_id, operation)
        if intent is not None:
            self._record("CENTRAL_ORDER_RECOVERED_UNCERTAIN", intent)
        return intent

    def _transition(
        self,
        intent_id: str,
        status: str,
        *,
        detail: str,
        event_type: str,
        broker_order_id: str | None = None,
        uncertainty_reason: str | None = None,
        outcome: str | None = None,
        executed_lots: int | None = None,
    ) -> CentralOrderIntent:
        selected = _required_text(intent_id, "intent_id")

        def operation(
            state: CentralOrderState,
        ) -> tuple[CentralOrderState, CentralOrderIntent]:
            current = next(
                (item for item in state.intents if item.intent_id == selected),
                None,
            )
            if current is None:
                raise CentralOrderConflictError(f"Unknown intent {selected}.")
            updated = current.transition(
                status,
                detail=detail,
                broker_order_id=broker_order_id,
                uncertainty_reason=uncertainty_reason,
                outcome=outcome,
                executed_lots=executed_lots,
            )
            return state.replace_intent(updated), updated

        _, intent = self.store.mutate(self.account_id, operation)
        self._record(event_type, intent)
        return intent

    def _validate_portfolio_for_dispatch(
        self,
        repository: PortfolioRepository,
        intent: CentralOrderIntent,
        *,
        locked_portfolio_state: PortfolioState | None = None,
    ) -> PortfolioState:
        if locked_portfolio_state is None:
            try:
                state = repository.load(expected_account_id=self.account_id)
            except PortfolioRepositoryError as exc:
                raise CentralOrderConflictError(
                    f"Canonical portfolio reload failed: {exc}"
                ) from exc
        else:
            state = locked_portfolio_state
            if state.account_id != self.account_id:
                raise CentralOrderConflictError(
                    "Locked canonical portfolio account scope mismatch."
                )
        authorization = intent.authorization
        lease = PortfolioSnapshotLease.from_state(state)
        reasons: list[str] = []
        if state.portfolio_source != "CANONICAL" or not state.migration.complete:
            reasons.append("Canonical portfolio cutover is incomplete.")
        if state.migration.legacy_read_path_enabled:
            reasons.append("Legacy portfolio read path is enabled.")
        if state.freshness is not SnapshotFreshness.FRESH:
            reasons.append("Canonical portfolio snapshot is not FRESH.")
        if state.state_status in {"BLOCKED", "MANUAL_REVIEW_REQUIRED"}:
            reasons.append(f"Canonical state status is {state.state_status}.")
        if state.blocking:
            reasons.append("Canonical portfolio has a blocking condition.")
        active_orders = tuple(
            order.order_request_id
            for position in state.positions
            for order in position.pending_orders
            if order.active or order.uncertain
        )
        if active_orders:
            reasons.append("Canonical portfolio contains pending/uncertain orders.")
        if lease.revision != authorization.portfolio_revision:
            reasons.append("Canonical portfolio revision changed after authorization.")
        if lease.decision_checksum != authorization.portfolio_decision_checksum:
            reasons.append(
                "Canonical portfolio decision checksum changed after authorization."
            )
        if (
            authorization.portfolio_risk is not None
            and lease.document_checksum
            != authorization.portfolio_document_checksum
        ):
            reasons.append(
                "Canonical portfolio document checksum changed after authorization."
            )
        if reasons:
            raise CentralOrderConflictError(" ".join(reasons))
        return state

    def _validate_reconciliation(
        self,
        repository: PortfolioRepository,
        intent: CentralOrderIntent,
        *,
        outcome: str,
        executed_lots: int,
        locked_portfolio_state: PortfolioState | None = None,
    ) -> PortfolioSnapshotLease:
        if outcome not in _RECONCILIATION_OUTCOMES:
            raise CentralOrderConflictError(
                f"Unsupported reconciliation outcome {outcome!r}."
            )
        executed = _non_negative_int(executed_lots, "executed_lots")
        requested = intent.candidate.requested_lots
        if outcome == "FILLED" and executed != requested:
            raise CentralOrderConflictError(
                "FILLED reconciliation requires all requested lots."
            )
        if outcome == "PARTIALLY_FILLED" and not (0 < executed < requested):
            raise CentralOrderConflictError(
                "PARTIALLY_FILLED requires a strict partial lot count."
            )
        if outcome in {"REJECTED", "CANCELLED", "NOT_SUBMITTED"} and executed:
            raise CentralOrderConflictError(
                f"{outcome} reconciliation requires zero executed lots."
            )
        if locked_portfolio_state is None:
            try:
                state = repository.load(expected_account_id=self.account_id)
            except PortfolioRepositoryError as exc:
                raise CentralOrderConflictError(
                    f"Canonical portfolio reconciliation reload failed: {exc}"
                ) from exc
        else:
            state = locked_portfolio_state
            if state.account_id != self.account_id:
                raise CentralOrderConflictError(
                    "Locked reconciliation portfolio account scope mismatch."
                )
        reasons: list[str] = []
        if state.portfolio_source != "CANONICAL" or not state.migration.complete:
            reasons.append("Canonical portfolio cutover is incomplete.")
        if state.migration.legacy_read_path_enabled:
            reasons.append("Legacy portfolio read path is enabled.")
        if state.freshness is not SnapshotFreshness.FRESH:
            reasons.append("Reconciled portfolio snapshot is not FRESH.")
        if state.state_status in {"BLOCKED", "MANUAL_REVIEW_REQUIRED"}:
            reasons.append(f"Reconciled state status is {state.state_status}.")
        if state.blocking:
            reasons.append("Reconciled portfolio has a blocking condition.")
        active_orders = tuple(
            order.order_request_id
            for position in state.positions
            for order in position.pending_orders
            if order.active or order.uncertain
        )
        if active_orders:
            reasons.append("Reconciled portfolio still contains active orders.")
        snapshot_at = _timestamp(
            state.snapshot_at,
            "reconciled portfolio snapshot_at",
        )
        if datetime.fromisoformat(snapshot_at) <= datetime.fromisoformat(
            intent.updated_at
        ):
            reasons.append(
                "Canonical portfolio snapshot predates the order outcome."
            )
        position = state.position(intent.candidate.instrument_id)
        actual_lots = int(position.actual_lots) if position is not None else 0
        signed_executed = (
            executed if intent.candidate.direction == "BUY" else -executed
        )
        expected_actual = intent.candidate.current_lots + signed_executed
        if actual_lots != expected_actual:
            reasons.append(
                "Canonical actual lots do not prove the reported execution: "
                f"{actual_lots} != {expected_actual}."
            )
        if reasons:
            raise CentralOrderConflictError(" ".join(reasons))
        return PortfolioSnapshotLease.from_state(state)

    def _record(self, event_type: str, intent: CentralOrderIntent) -> None:
        if self.journal is None:
            return
        try:
            self.journal.record(
                JournalEvent(
                    category="central_order",
                    event_type=event_type,
                    severity=(
                        "WARNING"
                        if intent.status in {"UNCERTAIN", "FAILED"}
                        else "INFO"
                    ),
                    account_id=self.account_id,
                    instrument_id=intent.candidate.instrument_id,
                    ticker=intent.candidate.ticker,
                    order_id=intent.intent_id,
                    candle_time=intent.candidate.candle_time,
                    mode="SANDBOX_EXECUTION",
                    status=intent.status.lower(),
                    action=intent.candidate.direction,
                    strategy_id=intent.candidate.strategy_id,
                    config_hash=intent.candidate.runtime_config_hash,
                    payload={
                        "queue_sequence": intent.queue_sequence,
                        "reserved_cash_kopecks": intent.reserved_cash_kopecks,
                        "broker_order_id": intent.broker_order_id,
                        "uncertainty_reason": intent.uncertainty_reason,
                        "outcome": intent.outcome,
                        "executed_lots": intent.executed_lots,
                        "reconciled_portfolio_revision": (
                            intent.reconciled_portfolio_revision
                        ),
                        "reconciled_portfolio_decision_checksum": (
                            intent.reconciled_portfolio_decision_checksum
                        ),
                        "reconciled_portfolio_snapshot_at": (
                            intent.reconciled_portfolio_snapshot_at
                        ),
                        "risk_execution_status": intent.risk_execution_status,
                        "risk_execution_id": intent.risk_execution_id,
                        "execution_authorized": False,
                        "next_gate": "EXTERNAL_SANDBOX_EXECUTION_ADAPTER",
                    },
                )
            )
        except Exception:
            logger.exception(
                "Central-order journal write failed: event=%s intent=%s",
                event_type,
                intent.intent_id,
            )


__all__ = [
    "ACCOUNT_BLOCKING_STATUSES",
    "CENTRAL_ORDER_SCHEMA_VERSION",
    "CENTRAL_ORDER_STATUSES",
    "RESERVATION_STATUSES",
    "TERMINAL_STATUSES",
    "CentralOrderCandidate",
    "CentralOrderConflictError",
    "CentralOrderError",
    "CentralOrderIntent",
    "CentralOrderManager",
    "CentralOrderState",
    "CentralOrderStateError",
    "CentralOrderStore",
    "DispatchPreparation",
    "EnqueueResult",
    "ExecutionAuthorization",
    "LockedCentralDispatch",
    "OrderTransition",
]
