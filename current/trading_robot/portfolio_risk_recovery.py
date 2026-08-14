from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from .central_order_manager import (
    ACCOUNT_BLOCKING_STATUSES,
    RESERVATION_STATUSES,
    CentralOrderState,
    CentralOrderStore,
    central_reservation_projection_hash,
)
from .locking import InterProcessFileLock
from .portfolio_model import PortfolioState, SnapshotFreshness
from .portfolio_preflight import PortfolioSnapshotLease
from .portfolio_repository import PortfolioRepository
from .risk import RiskEngine, RiskEvent, RiskPolicy, RiskState, cash_delta_kopecks
from .risk_persistence import RiskProfileStore, RiskStateStore
from .risk_runtime import risk_state_guard_hash


class ExternalCashResyncError(RuntimeError):
    """Fail-closed M5.2 external cash recovery failure."""


@dataclass(frozen=True, slots=True)
class ExternalCashResyncProof:
    account_sha256: str
    policy_hash: str
    portfolio_revision: int
    portfolio_decision_checksum: str
    portfolio_document_checksum: str
    portfolio_snapshot_at: str
    portfolio_equity_rub: float
    portfolio_cash_rub: float
    cash_anchor_rub: float
    cash_delta_rub: float
    risk_state_guard_hash: str
    risk_resync_set_at: str
    central_revision: int
    central_reservation_projection_hash: str

    @property
    def proof_sha256(self) -> str:
        material = json.dumps(
            self.to_dict(include_hash=False),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @property
    def confirmation(self) -> str:
        return f"APPLY V3.9 EXTERNAL CASH RESYNC {self.proof_sha256}"

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": "v3.9-beta1-m5.2",
            "account_sha256": self.account_sha256,
            "policy_hash": self.policy_hash,
            "portfolio_revision": self.portfolio_revision,
            "portfolio_decision_checksum": self.portfolio_decision_checksum,
            "portfolio_document_checksum": self.portfolio_document_checksum,
            "portfolio_snapshot_at": self.portfolio_snapshot_at,
            "portfolio_equity_rub": self.portfolio_equity_rub,
            "portfolio_cash_rub": self.portfolio_cash_rub,
            "cash_anchor_rub": self.cash_anchor_rub,
            "cash_delta_rub": self.cash_delta_rub,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "risk_resync_set_at": self.risk_resync_set_at,
            "central_revision": self.central_revision,
            "central_reservation_projection_hash": (
                self.central_reservation_projection_hash
            ),
        }
        if include_hash:
            payload["proof_sha256"] = self.proof_sha256
        return payload


@dataclass(frozen=True, slots=True)
class ExternalCashResyncResult:
    proof: ExternalCashResyncProof
    state: RiskState
    event: RiskEvent


class ExternalCashResyncService:
    """Prepare and atomically apply one canonical external-cash resync.

    Lock order is the existing M4 order: canonical portfolio, Risk profile,
    Risk state, then Central.  The Central state participates in the operation
    without mutation so a reservation or dispatch handoff cannot race the
    RiskState write.
    """

    def __init__(
        self,
        *,
        account_id: str,
        portfolio_repository: PortfolioRepository,
        profile_store: RiskProfileStore,
        state_store: RiskStateStore,
        central_store: CentralOrderStore,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.account_id = str(account_id or "").strip()
        if not self.account_id:
            raise ValueError("account_id must not be empty.")
        self.portfolio_repository = portfolio_repository
        self.profile_store = profile_store
        self.state_store = state_store
        self.central_store = central_store
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        account_id: str,
    ) -> ExternalCashResyncService:
        root = Path(directory)
        return cls(
            account_id=account_id,
            portfolio_repository=PortfolioRepository(root / "portfolio_state.json"),
            profile_store=RiskProfileStore(root / "risk_profiles.json"),
            state_store=RiskStateStore(root / "risk_state.json"),
            central_store=CentralOrderStore(root / "central_order_state.json"),
        )

    def prepare(
        self,
        *,
        now: datetime | None = None,
    ) -> ExternalCashResyncProof:
        validated_at = self._aware(now or datetime.now(timezone.utc))
        with self._locked_context() as context:
            portfolio, profile, risk_state = context

            def inspect(
                central: CentralOrderState,
            ) -> tuple[CentralOrderState, ExternalCashResyncProof]:
                return central, self._build_proof(
                    portfolio=portfolio,
                    profile=profile,
                    risk_state=risk_state,
                    central=central,
                    now=validated_at,
                )

            _state, proof = self.central_store.mutate(self.account_id, inspect)
            return proof

    def apply(
        self,
        *,
        expected_proof_sha256: str,
        confirmation: str,
        now: datetime | None = None,
    ) -> ExternalCashResyncResult:
        expected = str(expected_proof_sha256 or "").strip().lower()
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ExternalCashResyncError(
                "expected_proof_sha256 must be a complete 64-character SHA-256 value."
            )
        completed_at = self._aware(now or datetime.now(timezone.utc))
        with self._locked_context() as context:
            portfolio, profile, risk_state = context

            def apply_locked(
                central: CentralOrderState,
            ) -> tuple[CentralOrderState, ExternalCashResyncResult]:
                proof = self._build_proof(
                    portfolio=portfolio,
                    profile=profile,
                    risk_state=risk_state,
                    central=central,
                    now=completed_at,
                )
                if proof.proof_sha256 != expected:
                    raise ExternalCashResyncError(
                        "External cash resync proof changed; run prepare again."
                    )
                if (
                    str(confirmation or "").strip().upper()
                    != proof.confirmation.upper()
                ):
                    raise ExternalCashResyncError(
                        f"Confirmation must be exactly '{proof.confirmation}'."
                    )
                snapshot_at = self._parse_timestamp(portfolio.snapshot_at)
                if completed_at < snapshot_at:
                    raise ExternalCashResyncError(
                        "External cash resync completion predates the canonical snapshot."
                    )
                updated, event = RiskEngine(
                    RiskPolicy(enabled=False)
                ).complete_external_cash_resync(
                    risk_state,
                    now=completed_at,
                    equity_rub=proof.portfolio_equity_rub,
                    cash_rub=proof.portfolio_cash_rub,
                    snapshot_at=snapshot_at,
                )
                self.state_store.save_account_while_locked(
                    self.account_id,
                    updated,
                )
                return central, ExternalCashResyncResult(
                    proof=proof,
                    state=updated,
                    event=event,
                )

            _state, result = self.central_store.mutate(
                self.account_id,
                apply_locked,
            )
            return result

    def _locked_context(self):
        return _ExternalCashResyncLockContext(self)

    def _build_proof(
        self,
        *,
        portfolio: PortfolioState,
        profile: dict[str, Any],
        risk_state: RiskState,
        central: CentralOrderState,
        now: datetime,
    ) -> ExternalCashResyncProof:
        self._validate_portfolio(
            portfolio,
            risk_state,
            policy=profile["policy"],
            now=now,
        )
        self._validate_central(central)
        cash = portfolio.account.cash("rub")
        equity = self._non_negative_money(
            portfolio.account.total_value,
            "canonical portfolio equity",
        )
        available_cash = self._non_negative_money(
            cash.available if cash is not None else None,
            "canonical available RUB cash",
        )
        anchor = self._non_negative_money(
            risk_state.risk_resync_cash_before_rub,
            "external cash anchor",
        )
        delta_in_kopecks = cash_delta_kopecks(anchor, available_cash)
        if delta_in_kopecks is None or abs(delta_in_kopecks) < 1:
            raise ExternalCashResyncError(
                "Canonical RUB cash no longer differs from the trusted anchor."
            )
        delta = delta_in_kopecks / 100.0
        lease = PortfolioSnapshotLease.from_state(portfolio)
        return ExternalCashResyncProof(
            account_sha256=hashlib.sha256(
                self.account_id.encode("utf-8")
            ).hexdigest(),
            policy_hash=str(profile["policy_hash"]),
            portfolio_revision=lease.revision,
            portfolio_decision_checksum=lease.decision_checksum,
            portfolio_document_checksum=lease.document_checksum,
            portfolio_snapshot_at=portfolio.snapshot_at,
            portfolio_equity_rub=equity,
            portfolio_cash_rub=available_cash,
            cash_anchor_rub=anchor,
            cash_delta_rub=delta,
            risk_state_guard_hash=risk_state_guard_hash(risk_state),
            risk_resync_set_at=str(risk_state.risk_resync_set_at),
            central_revision=central.revision,
            central_reservation_projection_hash=(
                central_reservation_projection_hash(central)
            ),
        )

    def _validate_portfolio(
        self,
        portfolio: PortfolioState,
        risk_state: RiskState,
        *,
        policy: RiskPolicy,
        now: datetime,
    ) -> None:
        reasons: list[str] = []
        if portfolio.account_id != self.account_id:
            reasons.append("Canonical portfolio account scope mismatch.")
        if portfolio.portfolio_source != "CANONICAL":
            reasons.append("Portfolio source is not CANONICAL.")
        if not portfolio.migration.complete:
            reasons.append("Canonical portfolio migration is incomplete.")
        if portfolio.freshness is not SnapshotFreshness.FRESH:
            reasons.append("Canonical portfolio snapshot is not FRESH.")
        if portfolio.state_status not in {"READY", "ACTIVE"}:
            reasons.append(f"Canonical state status is {portfolio.state_status}.")
        if portfolio.blocking:
            reasons.append("Canonical portfolio has a blocking condition.")
        active_orders = tuple(
            order.order_request_id
            for position in portfolio.positions
            for order in position.pending_orders
            if order.active or order.uncertain
        )
        if active_orders:
            reasons.append("Canonical portfolio contains pending/uncertain orders.")
        snapshot_at = self._parse_timestamp(portfolio.snapshot_at)
        validated_at = self._aware(now)
        max_snapshot_age = policy.max_snapshot_age_seconds
        if (
            not policy.block_on_stale_snapshot
            or max_snapshot_age is None
            or not isfinite(float(max_snapshot_age))
        ):
            reasons.append(
                "Risk policy does not enforce a finite canonical snapshot age."
            )
        else:
            snapshot_age = (validated_at - snapshot_at).total_seconds()
            if snapshot_age < 0:
                reasons.append("Canonical snapshot is from the future.")
            elif snapshot_age > float(max_snapshot_age):
                reasons.append(
                    "Canonical snapshot is stale for external cash resync."
                )
        if not risk_state.risk_resync_required:
            reasons.append("RiskState does not require resynchronization.")
        if risk_state.risk_resync_source != "EXTERNAL_CASH_CHANGE":
            reasons.append("Risk resync source is not EXTERNAL_CASH_CHANGE.")
        if not risk_state.risk_resync_set_at:
            reasons.append("Risk resync timestamp is missing.")
        else:
            resync_set_at = self._parse_timestamp(risk_state.risk_resync_set_at)
            if snapshot_at <= resync_set_at:
                reasons.append(
                    "Canonical snapshot must be newer than cash-change detection."
                )
        if reasons:
            raise ExternalCashResyncError(" ".join(reasons))

    def _validate_central(self, central: CentralOrderState) -> None:
        active = tuple(
            intent
            for intent in central.intents
            if intent.status in RESERVATION_STATUSES
            or intent.status in ACCOUNT_BLOCKING_STATUSES
        )
        if central.reserved_cash_kopecks or active:
            raise ExternalCashResyncError(
                "Central reservations or pending/uncertain intents block cash resync."
            )

    def _load_profile(self) -> dict[str, Any]:
        profile = self.profile_store.require_portfolio_policy("SANDBOX_EXECUTION")
        policy = profile["policy"]
        if policy.portfolio_policy_mode != "ENFORCED" or not policy.enabled:
            raise ExternalCashResyncError(
                "External cash resync requires an enabled ENFORCED policy."
            )
        scope = str(profile.get("account_scope") or "").strip()
        if not scope or scope != self.account_id:
            raise ExternalCashResyncError(
                "External cash resync requires an exact non-empty account scope."
            )
        return profile

    @staticmethod
    def _non_negative_money(value: Any, name: str) -> float:
        try:
            selected = float(value)
        except (TypeError, ValueError) as exc:
            raise ExternalCashResyncError(f"{name} is unavailable.") from exc
        if not isfinite(selected) or selected < 0:
            raise ExternalCashResyncError(
                f"{name} must be finite and non-negative."
            )
        return selected

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExternalCashResyncError("M5.2 timestamps must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @classmethod
    def _parse_timestamp(cls, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExternalCashResyncError("M5.2 timestamp is invalid.") from exc
        return cls._aware(parsed)


class _ExternalCashResyncLockContext:
    def __init__(self, service: ExternalCashResyncService) -> None:
        self.service = service
        self._portfolio_context = None
        self._profile_lock = None
        self._state_lock = None

    def __enter__(self) -> tuple[PortfolioState, dict[str, Any], RiskState]:
        service = self.service
        try:
            self._portfolio_context = service.portfolio_repository.locked_snapshot(
                expected_account_id=service.account_id
            )
            portfolio = self._portfolio_context.__enter__()
            self._profile_lock = InterProcessFileLock(
                service.profile_store.lock_path,
                timeout_seconds=service.lock_timeout_seconds,
            )
            self._profile_lock.acquire()
            self._state_lock = InterProcessFileLock(
                service.state_store.lock_path,
                timeout_seconds=service.lock_timeout_seconds,
            )
            self._state_lock.acquire()
            profile = service._load_profile()
            risk_state = service.state_store.load_account(service.account_id)
            return portfolio, profile, risk_state
        except Exception as error:
            self.__exit__(None, None, None)
            if isinstance(error, ExternalCashResyncError):
                raise
            raise ExternalCashResyncError(
                "External cash resync context is unavailable "
                f"({type(error).__name__})."
            ) from error

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._state_lock is not None:
            self._state_lock.release()
            self._state_lock = None
        if self._profile_lock is not None:
            self._profile_lock.release()
            self._profile_lock = None
        if self._portfolio_context is not None:
            self._portfolio_context.__exit__(exc_type, exc, traceback)
            self._portfolio_context = None


__all__ = [
    "ExternalCashResyncError",
    "ExternalCashResyncProof",
    "ExternalCashResyncResult",
    "ExternalCashResyncService",
]
