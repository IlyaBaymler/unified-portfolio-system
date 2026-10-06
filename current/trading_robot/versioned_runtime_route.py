"""Opt-in controller route over the qualified selected-v4 financial APIs.

One tick performs ONE financial phase. Explicit user calls alone may arm an
intent or approve a cash-flow resync. No fallback to the v1 trading route.
The caller must retain the returned authority checkpoint outside this object;
attachment/restart never silently accepts a discovered runtime/pin identity.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from . import cash_observation_versions as versions
from . import versioned_dispatch as dispatch
from . import versioned_fill_cash as cash
from . import versioned_fill_closure as closure
from . import versioned_owner_refresh as refresh
from . import versioned_risk_admission as admission
from . import versioned_risk_resync as resync
from . import versioned_runtime_cutover as cut
from . import versioned_selected_sync as sync
from .exact_cash_settlement import _safe_path
from .runtime_cash_authority import RuntimeCashAuthorityRecord, RuntimeCashAuthorityState as State


class VersionedRouteError(RuntimeError):
    """Bounded public refusal, never a provider payload or credential."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise VersionedRouteError("V4_ROUTE_" + code)


@dataclass(frozen=True, slots=True)
class VersionedTickResult:
    action: str
    status: str
    authority_sha256: str
    reference_sha256: str | None = None
    intent_id: str | None = None
    broker_post_called: bool = False

    def public_summary(self) -> dict[str, Any]:
        return {"domain": "SELECTED_V4_CONTROLLER_TICK_V1", "action": self.action,
                "status": self.status, "authority_sha256": self.authority_sha256,
                "reference_sha256": self.reference_sha256,
                "broker_post_called": self.broker_post_called, "automatic_arm": False}


class VersionedRuntimeRoute:
    """Explicit runtime attachment, not new financial authority or a new store."""

    def __init__(self, controller: Any, *, target_root: object, selection_sha256: str,
                 expected_authority_sha256: str, instrument_id: str):
        from .desktop_fill_recovery import DesktopFillRecovery
        from .gui_runtime_controller import ProductionGuiCycleSource
        controller._require_composed()
        controller.require_execution_observable()
        controller.validate_metadata_binding()
        _need(isinstance(controller.cycle_source, ProductionGuiCycleSource), "PRODUCTION_SOURCE_REQUIRED")
        self.controller = controller
        self.a = controller.execution_adapter
        self.r = controller.cycle_source.portfolio_recovery
        _need(type(self.r) is DesktopFillRecovery
              and self.r.central is self.a.manager
              and self.r.risk is self.a.risk_runtime
              and self.r.manager.repository is controller.portfolio_repository
              and self.r.authority is self.a.cash_authority_manager
              and controller.cash_authority is self.a.cash_authority_manager,
              "OWNER_GRAPH_MISMATCH")
        self.root = cut._path(target_root)
        self.selection = versions._hash(selection_sha256)
        self.expected_authority = versions._hash(expected_authority_sha256)
        _need(type(instrument_id) is str and bool(instrument_id.strip()), "INSTRUMENT_REQUIRED")
        self.instrument_id = instrument_id
        self._busy = threading.Lock()
        self._needs_rebind = False
        self._maintenance_due = False
        self.last_result: VersionedTickResult | None = None
        with self.a.cash_authority_manager.store.locked():
            record = self.a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
            _need(record.sha256 == self.expected_authority, "AUTHORITY_CHECKPOINT_MISMATCH")
            _need(record.state.name.startswith("EXACT_CASH_VERSIONED_"), "NOT_SELECTED")
            cut._load_prepared(self.a, self.r, self.root, self.selection, _check_initial_owners=False)
        self._configured()

    def _configured(self):
        # Do not trust the scheduler's potentially stale in-memory ACTIVE set.
        config = self.controller._load_configured_set()
        _need(bool(config.bindings) and all(b.runtime.status == "ACTIVE" for b in config.bindings),
              "CONFIGURED_SET_NOT_ACTIVE")
        matches = [b for b in config.bindings if b.runtime.config.instrument_id == self.instrument_id]
        _need(len(matches) == 1, "INSTRUMENT_NOT_CONFIGURED")
        return matches[0].runtime

    def _check(self):
        _need(not self._needs_rebind, "EXPLICIT_REBIND_REQUIRED")
        c = self.controller
        c.require_execution_observable()
        c.validate_metadata_binding()
        _need(c.execution_adapter is self.a and c.cycle_source.portfolio_recovery is self.r,
              "OWNER_GRAPH_CHANGED")
        self._configured()
        record = self.a.cash_authority_manager.status()
        _need(record.sha256 == self.expected_authority, "AUTHORITY_CHECKPOINT_MISMATCH")
        return record

    def _read_gate(self) -> None:
        _need(self.controller.connected, "PROVIDER_DISCONNECTED")
        _need(self.controller.market_state == "OPEN", "MARKET_IDLE")

    def _args(self) -> dict[str, Any]:
        return {"recovery": self.r, "target_root": self.root,
                "expected_selection_sha256": self.selection}

    def _done(self, action: str, status: str, result: Any = None, *, reference=None,
              intent_id=None, post=False) -> VersionedTickResult:
        # An API-supplied committed checkpoint, never snapshot().pins adoption.
        expected = (result.sha256 if isinstance(result, RuntimeCashAuthorityRecord)
                    else getattr(result, "authority_sha256", self.expected_authority))
        observed = self.a.cash_authority_manager.status()
        _need(observed.sha256 == expected, "RESULT_AUTHORITY_MISMATCH")
        self.expected_authority = expected
        value = VersionedTickResult(action, status, expected, reference, intent_id, post)
        self.last_result = value
        return value

    def _serialized(self, operation):
        _need(self._busy.acquire(blocking=False), "BUSY")
        try:
            record = self._check()
            return operation(record)
        except BaseException:
            # The financial API may have committed before raising. Do not learn
            # the resulting checkpoint opportunistically; explicit reattachment
            # plus the API's own authenticated recovery resolves that boundary.
            self._needs_rebind = True
            raise
        finally:
            self._busy.release()

    def tick(self) -> VersionedTickResult:
        return self._serialized(self._tick)

    def _tick(self, record) -> VersionedTickResult:
        args = self._args()
        pending = record.pending_dispatch_proof_sha256
        # Offline recovery wins over market/candle/maintenance processing.
        if record.state is State.EXACT_CASH_VERSIONED_SYNC_PENDING:
            out = sync.recover_selected_sync(self.a, expected_sync_plan_sha256=pending, **args)
            return self._done("RECOVERY", "CASH_SYNC_RECOVERED", out, reference=out.sync_plan_sha256)
        if record.state is State.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING:
            out = refresh.recover_owner_refresh(self.a, expected_refresh_plan_sha256=pending, **args)
            return self._done("RECOVERY", "OWNERS_RECOVERED", out, reference=out.plan_sha256)
        if record.state is State.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING:
            out = resync.recover_cash_flow_resync(self.a, expected_plan_sha256=pending, **args)
            return self._done("RECOVERY", "RESYNC_RECOVERED", out, reference=out.plan_sha256)
        if record.state is State.EXACT_CASH_VERSIONED_ADMISSION_PENDING:
            out = admission.recover_selected_admission(self.a, expected_plan_sha256=pending, **args)
            return self._done("RECOVERY", "ADMISSION_RECOVERED", out, reference=out.plan_sha256,
                              intent_id=out.intent_id)
        if record.state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING:
            return self._post_fill(record)
        if record.state is State.EXACT_CASH_VERSIONED_ARMED:
            self._read_gate()
            # The persisted arm determines the one intent; never use a new signal.
            body, digest = dispatch._read(dispatch._path(self.root, "arms", record.activation_context_sha256),
                                           self.a.cl7_identity_key, dispatch.ARM_FIELDS)
            out = dispatch.dispatch_selected_order(self.a, expected_arm_sha256=digest,
                expected_authority_sha256=record.sha256, expected_intent_id=body["intent_id"], **args)
            return self._done("DISPATCH", out.status, out, reference=out.plan_sha256,
                              intent_id=out.intent_id, post=out.post_called_this_invocation)
        _need(record.state is State.EXACT_CASH_VERSIONED_DISARMED, "STATE_NOT_ROUTED")
        # Verify the selected-source lineage before returning passive queue state.
        with self.a.cash_authority_manager.store.locked():
            sync._load_current_prepared(self.a, self.r, self.root, self.selection)
            central = self.a.manager.state()
        _need(central.blocking_intent is None, "UNEXPECTED_BLOCKING_INTENT")
        if central.queued:
            _need(len(central.queued) == 1, "MULTIPLE_QUEUED_UNSUPPORTED")
            return self._done("WAIT", "WAITING_FOR_EXPLICIT_ARM", intent_id=central.queued[0].intent_id)
        self._read_gate()
        recent = record.transition_kind in {refresh.DONE, resync.DONE}
        # Outside the short observation window, refresh *operations first*.
        try:
            refresh._require_recent_operations(record, self.a.cl7_clock())
        except refresh.OwnerRefreshError:
            recent = False
        if self._maintenance_due or (not recent and record.transition_kind not in {sync.COMMITTED, sync.ABORTED}):
            out = sync.sync_selected_cash(self.a, expected_authority_sha256=record.sha256, **args)
            self._maintenance_due = False
            return self._done("MAINTENANCE", "CASH_SYNCED", out, reference=out.sync_plan_sha256)
        if not recent:
            # A stale SYNC result is not fresh enough for owner-refresh either.
            try:
                refresh._require_recent_operations(record, self.a.cl7_clock())
            except refresh.OwnerRefreshError:
                out = sync.sync_selected_cash(self.a, expected_authority_sha256=record.sha256, **args)
                return self._done("MAINTENANCE", "CASH_SYNCED", out, reference=out.sync_plan_sha256)
            out = refresh.refresh_selected_owners(self.a, expected_authority_sha256=record.sha256, **args)
            return self._done("MAINTENANCE", "OWNERS_REFRESHED", out, reference=out.plan_sha256)
        risk = self.r.risk.state_store.load_account(self.a.policy.account_id)
        if risk.risk_resync_required:
            return self._done("WAIT", "CASH_FLOW_CONFIRMATION_REQUIRED")
        try:
            out = admission.admit_selected_order(self.a, expected_authority_sha256=record.sha256,
                                                instrument_id=self.instrument_id, **args)
        except admission.VersionedAdmissionError as exc:
            if str(exc) != "V4_ADMISSION_NO_POSITION_CHANGE":
                raise
            self._maintenance_due = True
            return self._done("HOLD", "NO_POSITION_CHANGE")
        return self._done("ADMISSION", "QUEUED", out, reference=out.plan_sha256, intent_id=out.intent_id)

    def _post_fill(self, record):
        args, digest = self._args(), versions._hash(record.pending_dispatch_proof_sha256)
        plan_path = closure._folder(self.root, digest) / "plan.json"
        _safe_path(plan_path)
        if plan_path.exists():
            _, plan_sha = closure._read(plan_path, self.a.cl7_identity_key, closure.PLAN_FIELDS)
            out = closure.recover_selected_full_fill(self.a, expected_dispatch_plan_sha256=digest,
                                                     expected_closure_plan_sha256=plan_sha, **args)
            self._maintenance_due = True
            return self._done("RECOVERY", "FULL_FILL_CLOSED_DISARMED", out, reference=out.closure_plan_sha256)
        # Dispatch attempt may have been saved before native IN_FLIGHT.
        if not self.a.manager.state().blocking_intent:
            self._read_gate()
            out = dispatch.recover_selected_dispatch_identity(self.a, expected_plan_sha256=digest, **args)
            return self._done("RECOVERY", "DISPATCH_IDENTITY_OBSERVED", out, reference=digest)
        # Routing inspects existence only. The called API validates the complete
        # signed round chain and the exact source before it can write anything.
        folder = cash._folder(self.root, digest)
        paths = sorted(folder.iterdir()) if folder.exists() else []
        _need(len(paths) <= cash.MAX_ROUNDS, "CASH_ROUND_LIMIT")
        if paths:
            last = paths[-1]
            _safe_path(last)
            result_path = last / "result.json"
            if not result_path.exists():
                out = cash.recover_selected_fill_cash(self.a, expected_dispatch_plan_sha256=digest, **args)
                return self._done("RECOVERY", "CASH_" + out.outcome, out, reference=out.cash_plan_sha256)
            body, _ = cash._read(result_path, self.a.cl7_identity_key, cash.RESULT_FIELDS)
            if body["outcome"] == "COMMITTED":
                # Closure fully validates this result and its referenced plan.
                self._read_gate()
                _, plan_sha = cash._read(last / "plan.json", self.a.cl7_identity_key, cash.PLAN_FIELDS)
                out = closure.close_selected_full_fill(self.a, expected_dispatch_plan_sha256=digest,
                                                       expected_cash_plan_sha256=plan_sha, **args)
                self._maintenance_due = True
                return self._done("SETTLEMENT", "FULL_FILL_CLOSED_DISARMED", out, reference=out.closure_plan_sha256)
        self._read_gate()
        out = cash.record_selected_fill_cash(self.a, expected_dispatch_plan_sha256=digest, **args)
        return self._done("SETTLEMENT", "CASH_" + out.outcome, out, reference=out.cash_plan_sha256)

    def arm(self, *, intent_id: str, confirmation: str) -> VersionedTickResult:
        def perform(record):
            self._read_gate()
            out = dispatch.arm_selected_order(self.a, expected_authority_sha256=record.sha256,
                expected_intent_id=intent_id, confirmation=confirmation, **self._args())
            return self._done("ARM", "ARMED", out, reference=out.arm_sha256, intent_id=intent_id)
        return self._serialized(perform)

    def disarm(self, *, arm_sha256: str) -> VersionedTickResult:
        def perform(record):
            out = dispatch.disarm_selected_order(self.a, expected_authority_sha256=record.sha256,
                                                  expected_arm_sha256=arm_sha256, **self._args())
            return self._done("DISARM", "QUEUED_NOT_SENT", out)
        return self._serialized(perform)

    def prepare_resync(self):
        return self._serialized(lambda rec: resync.prepare_cash_flow_resync(self.a,
            expected_authority_sha256=rec.sha256, **self._args()))

    def confirm_resync(self, *, plan_sha256: str, confirmation: str) -> VersionedTickResult:
        def perform(_):
            out = resync.confirm_cash_flow_resync(self.a, expected_plan_sha256=plan_sha256,
                                                 confirmation=confirmation, **self._args())
            return self._done("RESYNC", "CASH_FLOW_ACKNOWLEDGED", out, reference=out.plan_sha256)
        return self._serialized(perform)
