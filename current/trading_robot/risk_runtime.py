from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pandas as pd

from .locking import InterProcessFileLock
from .risk import (
    ExecutionRecord,
    ExecutionRegistration,
    RiskAssessment,
    RiskEngine,
    RiskPolicy,
    RiskSnapshot,
    RiskState,
    average_true_range,
    portfolio_risk_inputs,
)
from .risk_persistence import (
    RiskPersistenceError,
    RiskProfileStore,
    RiskStateStore,
    normalize_risk_mode,
)

# A portfolio snapshot is captured after API work, while the cycle timestamp is
# recorded before it.  Normal request latency must not look like clock skew.
# Larger offsets remain fail-closed in RiskEngine as SNAPSHOT_FROM_FUTURE.
_LOCAL_SNAPSHOT_CAPTURE_TOLERANCE_SECONDS = 60.0

_RISK_DISPATCH_STATE_FIELDS = (
    "version",
    "daily_date",
    "weekly_key",
    "daily_start_equity_rub",
    "weekly_start_equity_rub",
    "high_watermark_equity_rub",
    "daily_turnover_rub",
    "daily_order_count",
    "kill_switch_active",
    "kill_switch_reason",
    "kill_switch_set_at",
    "kill_switch_source",
    "kill_switch_operator_ref",
    "instrument_kill_switches",
    "risk_resync_required",
    "risk_resync_reason",
    "risk_resync_set_at",
    "risk_resync_source",
    "risk_resync_cash_before_rub",
    "risk_resync_cash_observed_rub",
    "risk_resync_equity_observed_rub",
    "risk_resync_snapshot_at",
    "last_execution_at",
    "recorded_execution_ids",
)


class RiskDispatchAuthorizationError(RuntimeError):
    """Fail-closed reason why a saved Risk authorization is no longer current."""

    def __init__(
        self,
        status: str,
        message: str,
        *,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status = str(status).strip().upper()
        self.retryable = bool(retryable)


def risk_state_guard_hash(state: RiskState) -> str:
    """Hash only RiskState fields that can change order authorization."""

    if not isinstance(state, RiskState):
        raise TypeError("risk dispatch guard requires RiskState.")
    serialized = state.to_dict()
    payload = {field: serialized[field] for field in _RISK_DISPATCH_STATE_FIELDS}
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RiskRuntimeOutcome:
    """One fail-closed bridge result between the bot and Risk Engine core."""

    enforced: bool
    mode: str
    approved_target_lots: int
    assessment: RiskAssessment | None
    decision_id: str | None = None
    error: str | None = None
    profile_auto_created: bool = False
    portfolio_revision: int | None = None
    portfolio_decision_checksum: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "RUNTIME_ERROR"
        if self.assessment is None:
            return "NOT_ENFORCED"
        return self.assessment.decision.status

    @property
    def policy_hash(self) -> str | None:
        if self.assessment is None:
            return None
        return self.assessment.decision.policy_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "enforced": self.enforced,
            "mode": self.mode,
            "status": self.status,
            "decision_id": self.decision_id,
            "approved_target_lots": self.approved_target_lots,
            "policy_hash": self.policy_hash,
            "profile_auto_created": self.profile_auto_created,
            "portfolio_revision": self.portfolio_revision,
            "portfolio_decision_checksum": self.portfolio_decision_checksum,
            "error": self.error,
            "assessment": (
                self.assessment.to_dict() if self.assessment is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class RiskExecutionOutcome:
    """Result of idempotently applying a confirmed fill to RiskState."""

    enforced: bool
    mode: str
    execution_id: str
    decision_id: str | None
    policy_hash: str | None
    registration: ExecutionRegistration | None
    price_rub: float | None = None
    price_source: str | None = None
    execution_source: str = "STRATEGY"
    reconciliation_confirmed_at: str | None = None
    reconciliation_proof: dict[str, Any] | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "RUNTIME_ERROR"
        if self.registration is None:
            return "NOT_RECORDED"
        return "DUPLICATE" if self.registration.duplicate else "RECORDED"

    @property
    def duplicate(self) -> bool:
        return bool(self.registration and self.registration.duplicate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enforced": self.enforced,
            "mode": self.mode,
            "status": self.status,
            "execution_id": self.execution_id,
            "decision_id": self.decision_id,
            "policy_hash": self.policy_hash,
            "price_rub": self.price_rub,
            "price_source": self.price_source,
            "execution_source": self.execution_source,
            "reconciliation_confirmed_at": self.reconciliation_confirmed_at,
            "reconciliation_proof": (
                dict(self.reconciliation_proof)
                if self.reconciliation_proof is not None
                else None
            ),
            "duplicate": self.duplicate,
            "error": self.error,
            "registration": (
                self.registration.to_dict() if self.registration is not None else None
            ),
        }


class RiskRuntimeAdapter:
    """Persistence-aware Risk Engine adapter for Dry-run and Sandbox.

    DRY_RUN may create its default profile on first use. SANDBOX_EXECUTION is
    deliberately fail-closed: a checksummed profile must already exist.
    Confirmed broker fills are recorded separately and idempotently only after
    the order state and portfolio reconciliation are known.
    """

    def __init__(
        self,
        *,
        account_id: str,
        mode: str,
        profile_store: RiskProfileStore,
        state_store: RiskStateStore,
        auto_create_dry_run_profile: bool = True,
    ) -> None:
        self.account_id = str(account_id).strip()
        if not self.account_id:
            raise ValueError("account_id must not be empty.")
        self.mode = normalize_risk_mode(mode)
        self.profile_store = profile_store
        self.state_store = state_store
        self.auto_create_dry_run_profile = bool(auto_create_dry_run_profile)

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        account_id: str,
        mode: str = "DRY_RUN",
        auto_create_dry_run_profile: bool = True,
    ) -> RiskRuntimeAdapter:
        root = Path(directory)
        return cls(
            account_id=account_id,
            mode=mode,
            profile_store=RiskProfileStore(root / "risk_profiles.json"),
            state_store=RiskStateStore(root / "risk_state.json"),
            auto_create_dry_run_profile=auto_create_dry_run_profile,
        )

    def _load_policy(self) -> tuple[RiskPolicy, bool]:
        loaded = self.profile_store.load_profile(self.mode)
        auto_created = False
        if loaded is None:
            if self.mode != "DRY_RUN" or not self.auto_create_dry_run_profile:
                raise RiskPersistenceError(
                    f"Risk profile {self.mode} is not saved; new exposure is blocked."
                )
            loaded = self.profile_store.save_profile(
                "DRY_RUN",
                RiskPolicy(),
                select=True,
            )
            auto_created = True
        profile_scope = str(loaded.get("account_scope") or "").strip()
        if profile_scope and profile_scope != self.account_id:
            raise RiskPersistenceError(
                "Risk profile account scope mismatch: "
                f"{profile_scope} != {self.account_id}."
            )
        policy = loaded["policy"]
        if self.mode == "SANDBOX_EXECUTION" and not policy.enabled:
            raise RiskPersistenceError(
                "SANDBOX_EXECUTION risk profile cannot disable Risk Engine."
            )
        return policy, auto_created

    def current_policy_hash(self) -> str:
        policy, _auto_created = self._load_policy()
        return policy.policy_hash

    @contextmanager
    def dispatch_authorization_guard(
        self,
        *,
        expected_policy_hash: str,
        expected_state_guard_hash: str | None,
        instrument_id: str | None = None,
    ) -> Iterator[None]:
        """Hold Risk policy/state stable across the final Sandbox handoff."""

        if self.mode != "SANDBOX_EXECUTION":
            raise RiskDispatchAuthorizationError(
                "RISK_MODE_MISMATCH",
                "Dispatch authorization requires SANDBOX_EXECUTION Risk.",
                retryable=False,
            )
        expected_policy = str(expected_policy_hash or "").strip().lower()
        expected_state = str(expected_state_guard_hash or "").strip().lower()
        if not expected_state:
            raise RiskDispatchAuthorizationError(
                "RISK_REAUTHORIZATION_REQUIRED",
                "Queued intent predates the dispatch-time RiskState guard.",
            )
        verified = False
        try:
            with (
                InterProcessFileLock(
                    self.profile_store.lock_path,
                    timeout_seconds=5.0,
                ),
                InterProcessFileLock(
                    self.state_store.lock_path,
                    timeout_seconds=5.0,
                ),
            ):
                policy, _auto_created = self._load_policy()
                state = self.state_store.load_account(self.account_id)
                if policy.policy_hash != expected_policy:
                    raise RiskDispatchAuthorizationError(
                        "RISK_POLICY_CHANGED",
                        "Risk policy changed after intent authorization.",
                    )
                if state.kill_switch_active:
                    raise RiskDispatchAuthorizationError(
                        "RISK_KILL_SWITCH_ACTIVE",
                        "Risk kill switch is active; Sandbox POST is blocked.",
                    )
                if state.risk_resync_required:
                    raise RiskDispatchAuthorizationError(
                        "RISK_RESYNC_REQUIRED",
                        "Risk resynchronization is required; Sandbox POST is blocked.",
                    )
                selected_instrument = str(instrument_id or "").strip()
                if selected_instrument and any(
                    item.instrument_id == selected_instrument
                    for item in state.instrument_kill_switches
                ):
                    raise RiskDispatchAuthorizationError(
                        "RISK_INSTRUMENT_KILL_SWITCH_ACTIVE",
                        "Instrument Risk kill switch is active; Sandbox POST is blocked.",
                    )
                if risk_state_guard_hash(state) != expected_state:
                    raise RiskDispatchAuthorizationError(
                        "RISK_STATE_CHANGED",
                        "RiskState changed after intent authorization.",
                    )
                verified = True
                yield
        except RiskDispatchAuthorizationError:
            raise
        except Exception as exc:
            if verified:
                raise
            raise RiskDispatchAuthorizationError(
                "RISK_AUTHORIZATION_UNAVAILABLE",
                f"Dispatch-time Risk verification failed: {exc}",
            ) from exc

    def _decision_id(
        self,
        *,
        context: str,
        policy_hash: str,
        strategy_target_lots: int,
        current_lots: int,
    ) -> str:
        source = "|".join(
            [
                "risk-decision-v1",
                self.account_id,
                self.mode,
                str(context),
                policy_hash,
                str(int(strategy_target_lots)),
                str(int(current_lots)),
            ]
        )
        return str(uuid5(NAMESPACE_URL, source))

    def evaluate(
        self,
        *,
        now: datetime,
        strategy_target_lots: int,
        current_lots: int,
        price_rub: float | None,
        lot_size: int,
        portfolio: Mapping[str, Any],
        candles: pd.DataFrame,
        atr_window: int,
        stop_level: float | None,
        position_reconciled: bool | None = True,
        pending_order: bool | None = False,
        snapshot_at: datetime | None = None,
        decision_context: str | None = None,
        portfolio_preflight: Mapping[str, Any] | None = None,
    ) -> RiskRuntimeOutcome:
        """Evaluate and persist one risk decision.

        Any profile/state/persistence error returns current_lots as the approved
        target. The caller must treat the outcome as retryable fail-closed and
        must not create INTENT_SAVED or ORDER_SUBMITTED.
        """

        try:
            policy, auto_created = self._load_policy()
            state = self.state_store.load_account(self.account_id)
            inputs = portfolio_risk_inputs(portfolio)
            close = float(price_rub) if price_rub is not None else None
            explicit_stop_distance: float | None = None
            if close is not None and stop_level is not None:
                distance = abs(close - float(stop_level))
                if distance > 0:
                    explicit_stop_distance = distance
            atr = average_true_range(candles, max(2, int(atr_window)))
            cycle_now = (
                now.replace(tzinfo=timezone.utc)
                if now.tzinfo is None
                else now.astimezone(timezone.utc)
            )
            if snapshot_at is None:
                captured_snapshot_at = datetime.now(timezone.utc)
            else:
                captured_snapshot_at = (
                    snapshot_at.replace(tzinfo=timezone.utc)
                    if snapshot_at.tzinfo is None
                    else snapshot_at.astimezone(timezone.utc)
                )
            capture_delay = (captured_snapshot_at - cycle_now).total_seconds()
            evaluation_now = cycle_now
            if 0.0 < capture_delay <= _LOCAL_SNAPSHOT_CAPTURE_TOLERANCE_SECONDS:
                evaluation_now = captured_snapshot_at
            snapshot = RiskSnapshot(
                now=evaluation_now,
                strategy_target_lots=int(strategy_target_lots),
                current_lots=int(current_lots),
                price_rub=close,
                lot_size=max(1, int(lot_size)),
                portfolio_equity_rub=inputs["equity_rub"],
                cash_rub=inputs["cash_rub"],
                securities_value_rub=inputs["securities_value_rub"],
                atr_rub=atr,
                stop_distance_rub=explicit_stop_distance,
                snapshot_at=captured_snapshot_at,
                position_reconciled=position_reconciled,
                pending_order=pending_order,
                mode=self.mode,
            )
            assessment = RiskEngine(policy).evaluate(snapshot, state)
            self.state_store.save_account(self.account_id, assessment.state)
            context = decision_context or now.astimezone(timezone.utc).isoformat()
            decision_id = self._decision_id(
                context=context,
                policy_hash=assessment.decision.policy_hash,
                strategy_target_lots=strategy_target_lots,
                current_lots=current_lots,
            )
            preflight = dict(portfolio_preflight or {})
            return RiskRuntimeOutcome(
                enforced=True,
                mode=self.mode,
                approved_target_lots=assessment.decision.approved_target_lots,
                assessment=assessment,
                decision_id=decision_id,
                profile_auto_created=auto_created,
                portfolio_revision=(
                    int(preflight["snapshot_revision"])
                    if preflight.get("snapshot_revision") is not None
                    else None
                ),
                portfolio_decision_checksum=(
                    str(preflight.get("snapshot_decision_checksum") or "") or None
                ),
            )
        except (RiskPersistenceError, OSError, ValueError, TypeError) as exc:
            preflight = dict(portfolio_preflight or {})
            return RiskRuntimeOutcome(
                enforced=True,
                mode=self.mode,
                approved_target_lots=int(current_lots),
                assessment=None,
                error=str(exc),
                portfolio_revision=(
                    int(preflight["snapshot_revision"])
                    if preflight.get("snapshot_revision") is not None
                    else None
                ),
                portfolio_decision_checksum=(
                    str(preflight.get("snapshot_decision_checksum") or "") or None
                ),
            )

    def mark_external_activity(
        self,
        *,
        now: datetime,
        reason: str,
        source: str,
    ) -> dict[str, Any]:
        """Persist an account-level fail-closed resync requirement."""

        holder: dict[str, Any] = {}

        def updater(state):
            updated, event = RiskEngine(
                RiskPolicy(enabled=False)
            ).mark_external_activity(
                state,
                now=now,
                reason=reason,
                source=source,
            )
            holder["event"] = event
            return updated

        updated = self.state_store.update_account(self.account_id, updater)
        event = holder["event"]
        return {
            "state": updated.to_dict(),
            "event": event.to_dict(),
        }

    def risk_resync_required(self) -> bool:
        """Return the persistent account resync gate."""

        return bool(self.state_store.load_account(self.account_id).risk_resync_required)

    def record_execution(
        self,
        *,
        execution_id: str,
        executed_at: datetime,
        signed_lots: int,
        price_rub: float,
        lot_size: int,
        portfolio: Mapping[str, Any] | None = None,
        decision_id: str | None = None,
        expected_policy_hash: str | None = None,
        price_source: str | None = None,
        execution_source: str = "STRATEGY",
        reconciliation_confirmed_at: str | None = None,
        reconciliation_proof: Mapping[str, Any] | None = None,
    ) -> RiskExecutionOutcome:
        """Idempotently persist one confirmed economic fill.

        `expected_policy_hash` comes from the saved intent. Accounting does not
        require the current profile to remain unchanged after submission; the
        authorization context is the one persisted before INTENT_SAVED.
        A confirmed reconciliation proof is mandatory for every accounting
        source, including operator diagnostics.
        """

        normalized_execution_id = str(execution_id).strip()
        normalized_source = str(execution_source or "STRATEGY").strip().upper()
        proof = dict(reconciliation_proof or {})
        try:
            if not normalized_execution_id:
                raise RiskPersistenceError("execution_id must not be empty.")
            if not normalized_source:
                raise RiskPersistenceError("execution_source must not be empty.")
            if not proof.get("position_reconciled"):
                raise RiskPersistenceError(
                    "Risk execution accounting requires confirmed portfolio reconciliation."
                )
            expected_lots = proof.get("expected_lots_after")
            actual_lots = proof.get("actual_lots_after")
            if (
                expected_lots is None
                or actual_lots is None
                or int(expected_lots) != int(actual_lots)
            ):
                raise RiskPersistenceError(
                    "Risk execution accounting proof has mismatched expected/actual lots."
                )
            proof_account = str(proof.get("account_id") or "").strip()
            if proof_account and proof_account != self.account_id:
                raise RiskPersistenceError(
                    "Risk execution accounting proof belongs to another account."
                )
            policy_hash = str(expected_policy_hash or "").strip() or None
            if policy_hash is None:
                if normalized_source == "STRATEGY":
                    policy, _auto_created = self._load_policy()
                    policy_hash = policy.policy_hash
                else:
                    try:
                        policy, _auto_created = self._load_policy()
                        policy_hash = policy.policy_hash
                    except RiskPersistenceError:
                        policy_hash = f"EXTERNAL_ACTIVITY:{normalized_source}"
            inputs = portfolio_risk_inputs(portfolio or {})
            record = ExecutionRecord(
                execution_id=normalized_execution_id,
                executed_at=executed_at,
                signed_lots=int(signed_lots),
                price_rub=float(price_rub),
                lot_size=max(1, int(lot_size)),
                portfolio_equity_rub=inputs["equity_rub"],
                portfolio_cash_rub=inputs["cash_rub"],
                portfolio_snapshot_at=executed_at,
                execution_source=normalized_source,
            )
            holder: dict[str, ExecutionRegistration] = {}

            def updater(state):
                registration = RiskEngine(RiskPolicy(enabled=False)).record_execution(
                    state,
                    record,
                )
                holder["registration"] = registration
                return registration.state

            self.state_store.update_account(self.account_id, updater)
            registration = holder["registration"]
            return RiskExecutionOutcome(
                enforced=True,
                mode=self.mode,
                execution_id=normalized_execution_id,
                decision_id=decision_id,
                policy_hash=policy_hash,
                registration=registration,
                price_rub=float(price_rub),
                price_source=price_source,
                execution_source=normalized_source,
                reconciliation_confirmed_at=reconciliation_confirmed_at,
                reconciliation_proof=proof,
            )
        except (RiskPersistenceError, OSError, ValueError, TypeError) as exc:
            return RiskExecutionOutcome(
                enforced=True,
                mode=self.mode,
                execution_id=normalized_execution_id,
                decision_id=decision_id,
                policy_hash=str(expected_policy_hash or "").strip() or None,
                registration=None,
                price_rub=(float(price_rub) if price_rub is not None else None),
                price_source=price_source,
                execution_source=normalized_source,
                reconciliation_confirmed_at=reconciliation_confirmed_at,
                reconciliation_proof=proof or None,
                error=str(exc),
            )
