from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol
from uuid import uuid4

from .central_order_coordinator import CentralOrderCoordinator
from .global_scheduler import (
    GlobalScheduler,
    InstrumentRuntimeHooks,
    SchedulerTickResult,
)
from .instrument_runtime import (
    InstrumentRuntime,
    InstrumentRuntimeConflictError,
    InstrumentRuntimeStore,
)
from .multi_instrument_config import (
    MAX_V3_8_INSTRUMENTS,
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)
from .multi_instrument_strategy import (
    MultiInstrumentStrategyError,
    StrategyCandleLoader,
    build_strategy_proposal,
)
from .portfolio_risk_runtime import PortfolioRiskRuntime
from .runtime_cash_authority import (
    RuntimeCashAuthorityRecord,
    RuntimeCashAuthorityState,
)
from .sandbox_execution_adapter import SandboxExecutionAdapter, SandboxExecutionPolicy


class GuiRuntimeBlockedError(RuntimeError):
    """Fail-closed account-level GUI runtime gate."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or "GUI_RUNTIME_BLOCKED").strip().upper()
        self.detail = str(detail or "").strip()
        super().__init__(f"{self.reason}: {self.detail}".rstrip(": "))


@dataclass(frozen=True, slots=True)
class ConfiguredRuntimeBinding:
    profile: MultiInstrumentProfile
    runtime: InstrumentRuntime


@dataclass(frozen=True, slots=True)
class ConfiguredExecutionSet:
    """Exact process-local binding of profiles to persisted runtimes."""

    account_scope_sha256: str
    bindings: tuple[ConfiguredRuntimeBinding, ...]

    @property
    def runtime_keys(self) -> tuple[str, ...]:
        return tuple(item.runtime.runtime_key for item in self.bindings)

    @property
    def identity_sha256(self) -> str:
        payload = [
            {
                "account_scope_sha256": self.account_scope_sha256,
                "instrument_id": item.runtime.config.instrument_id,
                "candle_interval": item.runtime.config.candle_interval,
                "strategy_id": item.runtime.config.strategy_id,
                "runtime_config_hash": item.runtime.config.runtime_config_hash,
                "runtime_revision": item.runtime.revision,
            }
            for item in self.bindings
        ]
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GuiRuntimeTransitionResult:
    status: str
    configured_set_sha256: str
    runtime_statuses: tuple[tuple[str, str], ...]
    recovery_required: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "configured_set_sha256": self.configured_set_sha256,
            "runtime_statuses": [
                {"runtime_key": key, "status": status}
                for key, status in self.runtime_statuses
            ],
            "recovery_required": self.recovery_required,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GuiCoordinationRequest:
    proposal: Any
    profile: MultiInstrumentProfile
    candles: Any
    lot_size: int
    cash_buffer_bps: int = 100
    portfolio_risk_candidate_quote: Any | None = None


class GuiStrategyHooks(InstrumentRuntimeHooks, Protocol):
    def coordination_request(
        self,
        runtime: InstrumentRuntime,
        proposal: Any,
        candle_time: datetime,
        now: datetime,
    ) -> GuiCoordinationRequest | None: ...


class _ProductionGuiHooks:
    """One-cycle read adapter; economic mutation stays behind Central/CL7."""

    def __init__(
        self,
        *,
        provider: Any,
        risk_runtime: Any,
        portfolio_refresher: Callable[[], Any],
        profiles: Mapping[str, MultiInstrumentProfile],
        frames: Mapping[str, Any],
        lot_sizes: Mapping[str, int],
    ) -> None:
        self.provider = provider
        self.risk_runtime = risk_runtime
        self.portfolio_refresher = portfolio_refresher
        self.profiles = dict(profiles)
        self.frames = dict(frames)
        self.lot_sizes = dict(lot_sizes)

    def refresh_market_status(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        del now
        return self.provider.get_trading_status(runtime.config.instrument_id)

    def refresh_risk(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        del runtime, now
        return {
            "policy_hash": self.risk_runtime.current_policy_hash(),
            "state": self.risk_runtime.state_store.load_account(
                self.risk_runtime.account_id
            ).to_dict(),
        }

    def reconcile_portfolio(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        del runtime, now
        return self.portfolio_refresher()

    def evaluate_closed_candle(
        self,
        runtime: InstrumentRuntime,
        candle_time: datetime,
        now: datetime,
    ) -> Any:
        frame = self.frames[runtime.config.instrument_id]
        proposal = build_strategy_proposal(
            runtime,
            self.profiles[runtime.config.instrument_id],
            frame,
            now=now,
        )
        if datetime.fromisoformat(proposal.candle_time).astimezone(timezone.utc) != (
            candle_time.astimezone(timezone.utc)
        ):
            raise MultiInstrumentStrategyError("Cycle candle identity changed.")
        return proposal

    def coordination_request(
        self,
        runtime: InstrumentRuntime,
        proposal: Any,
        candle_time: datetime,
        now: datetime,
    ) -> GuiCoordinationRequest:
        del candle_time, now
        instrument_id = runtime.config.instrument_id
        return GuiCoordinationRequest(
            proposal=proposal,
            profile=self.profiles[instrument_id],
            candles=self.frames[instrument_id],
            lot_size=self.lot_sizes[instrument_id],
        )


class ProductionGuiCycleSource:
    """Provider-read cycle source for the single shipped controller graph.

    Construction performs no provider call. Reads occur only when the already
    gated account-level GUI loop invokes a cycle; order POST remains reachable
    solely through the controller's SandboxExecutionAdapter.
    """

    def __init__(
        self,
        *,
        provider: Any,
        profile_store: MultiInstrumentProfileStore,
        runtime_store: InstrumentRuntimeStore,
        risk_runtime: Any,
        portfolio_refresher: Callable[[], Any],
        account_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.profile_store = profile_store
        self.runtime_store = runtime_store
        self.risk_runtime = risk_runtime
        self.portfolio_refresher = portfolio_refresher
        self.account_id = str(account_id).strip()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.candle_loader = StrategyCandleLoader(provider)
        self._lot_sizes: dict[str, int] = {}

    def __call__(
        self,
    ) -> tuple[datetime, Mapping[str, datetime | None], GuiStrategyHooks]:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise GuiRuntimeBlockedError("CYCLE_CLOCK_INVALID")
        profiles = self.profile_store.load_mode("SANDBOX_EXECUTION")
        if len(profiles) not in {2, 3}:
            raise GuiRuntimeBlockedError("CONFIGURED_INSTRUMENT_COUNT_INVALID")
        runtimes = self.runtime_store.load(expected_account_id=self.account_id)
        by_instrument = {item.config.instrument_id: item for item in runtimes}
        if len(by_instrument) != len(runtimes) or len(runtimes) != len(profiles):
            raise GuiRuntimeBlockedError("CONFIGURED_SET_MISMATCH")
        frames: dict[str, Any] = {}
        latest: dict[str, datetime | None] = {}
        profile_map: dict[str, MultiInstrumentProfile] = {}
        lots: dict[str, int] = {}
        for profile in profiles:
            runtime = by_instrument.get(profile.instrument_id)
            if runtime is None or runtime.config.to_dict() != profile.to_runtime_config(
                self.account_id
            ).to_dict():
                raise GuiRuntimeBlockedError("PROFILE_RUNTIME_IDENTITY_MISMATCH")
            frame = self.candle_loader.load(runtime, profile, now=now)
            frames[profile.instrument_id] = frame
            timestamp = frame.index[-1]
            latest[runtime.runtime_key] = timestamp.to_pydatetime()
            profile_map[profile.instrument_id] = profile
            lot_size = self._lot_sizes.get(profile.instrument_id)
            if lot_size is None:
                metadata = self.provider.get_instrument_by_id(profile.instrument_id)
                lot_size = int(metadata.get("lot") or 0)
                if lot_size < 1:
                    raise GuiRuntimeBlockedError("INSTRUMENT_LOT_SIZE_INVALID")
                self._lot_sizes[profile.instrument_id] = lot_size
            lots[profile.instrument_id] = lot_size
        hooks = _ProductionGuiHooks(
            provider=self.provider,
            risk_runtime=self.risk_runtime,
            portfolio_refresher=self.portfolio_refresher,
            profiles=profile_map,
            frames=frames,
            lot_sizes=lots,
        )
        return now.astimezone(timezone.utc), latest, hooks


class _CoordinatingHooks:
    """Adapter that keeps Scheduler pure and routes proposals through Central."""

    def __init__(self, controller: GuiRuntimeController, hooks: GuiStrategyHooks) -> None:
        self.controller = controller
        self.hooks = hooks

    def refresh_market_status(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.hooks.refresh_market_status(runtime, now)

    def refresh_risk(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.hooks.refresh_risk(runtime, now)

    def reconcile_portfolio(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.hooks.reconcile_portfolio(runtime, now)

    def evaluate_closed_candle(
        self,
        runtime: InstrumentRuntime,
        candle_time: datetime,
        now: datetime,
    ) -> Any:
        proposal = self.hooks.evaluate_closed_candle(runtime, candle_time, now)
        if proposal is None:
            return None
        request = self.hooks.coordination_request(runtime, proposal, candle_time, now)
        if request is None:
            raise GuiRuntimeBlockedError(
                "COORDINATION_REQUEST_REQUIRED",
                "A non-null strategy proposal must be bound to one Central request.",
            )
        outcome = self.controller.central_order_coordinator.coordinate(
            request.proposal,
            runtime,
            request.profile,
            candles=request.candles,
            lot_size=request.lot_size,
            now=now,
            cash_buffer_bps=request.cash_buffer_bps,
            portfolio_risk_candidate_quote=request.portfolio_risk_candidate_quote,
        )
        if str(getattr(outcome, "status", "")).upper() in {
            "QUEUED",
            "INTENT_QUEUED",
            "AUTHORIZED",
        }:
            self.controller.execution_adapter.dispatch_next(
                self.controller.portfolio_repository
            )
        return outcome


class GuiRuntimeController:
    """Account-level GUI facade over accepted scheduler/economic owners.

    The controller owns no persisted economic state and exposes no provider
    transport.  Provider mutation is reachable only through the injected
    :class:`SandboxExecutionAdapter` after Central and CL7 checks.
    """

    def __init__(
        self,
        *,
        profile_store: MultiInstrumentProfileStore,
        runtime_store: InstrumentRuntimeStore,
        portfolio_repository: Any,
        central_order_coordinator: CentralOrderCoordinator,
        execution_adapter: SandboxExecutionAdapter,
        portfolio_risk_runtime: PortfolioRiskRuntime,
        cash_authority: Any,
        account_id: str,
        account_scope_sha256: str,
        cycle_source: Callable[
            [], tuple[datetime, Mapping[str, datetime | None], GuiStrategyHooks]
        ]
        | None = None,
        session_id: str | None = None,
    ) -> None:
        self.profile_store = profile_store
        self.runtime_store = runtime_store
        self.portfolio_repository = portfolio_repository
        self.central_order_coordinator = central_order_coordinator
        self.execution_adapter = execution_adapter
        self.portfolio_risk_runtime = portfolio_risk_runtime
        self.cash_authority = cash_authority
        self.account_id = str(account_id or "").strip()
        self.account_scope_sha256 = str(account_scope_sha256 or "").strip().lower()
        self.cycle_source = cycle_source
        self.session_id = str(session_id or uuid4())
        self.market_state = "OPEN"
        self.connected = True
        self.scheduler: GlobalScheduler | None = None
        self._configured_set: ConfiguredExecutionSet | None = None
        self._composition_blocker: GuiRuntimeBlockedError | None = None
        self._validate_static_bindings()

    @classmethod
    def blocked(cls, reason: str, detail: str = "") -> GuiRuntimeController:
        """Create the single fail-closed GUI controller before owner composition.

        A normal process therefore never has an absent controller.  An accepted
        composition root may instead pass a fully bound instance to ``main``;
        this placeholder cannot restore, start, render owner data or dispatch.
        """

        instance = object.__new__(cls)
        instance.cycle_source = None
        instance.session_id = str(uuid4())
        instance.market_state = "OPEN"
        instance.connected = False
        instance.account_id = ""
        instance.account_scope_sha256 = ""
        instance.scheduler = None
        instance._configured_set = None
        instance._composition_blocker = GuiRuntimeBlockedError(reason, detail)
        return instance

    @classmethod
    def compose(
        cls,
        *,
        profile_store: MultiInstrumentProfileStore,
        runtime_store: InstrumentRuntimeStore,
        portfolio_repository: Any,
        central_manager: Any,
        risk_runtime: Any,
        portfolio_risk_runtime: PortfolioRiskRuntime,
        execution_transport: Any,
        execution_policy: SandboxExecutionPolicy,
        cash_authority: Any,
        account_id: str,
        account_scope_sha256: str,
        cl7_identity_key: bytes | None = None,
        cl7_identity_key_id: str | None = None,
        cl7_ledger_store: Any | None = None,
        cycle_source: Callable[
            [], tuple[datetime, Mapping[str, datetime | None], GuiStrategyHooks]
        ]
        | None = None,
    ) -> GuiRuntimeController:
        """Compose the one accepted Central/adapter graph with shared Risk."""

        coordinator = CentralOrderCoordinator(
            central_manager,
            portfolio_repository,
            risk_runtime,
            portfolio_risk_runtime=portfolio_risk_runtime,
        )
        adapter = SandboxExecutionAdapter(
            execution_transport,
            central_manager,
            execution_policy,
            risk_runtime=risk_runtime,
            portfolio_risk_runtime=portfolio_risk_runtime,
            cash_authority_manager=cash_authority,
            cl7_identity_key=cl7_identity_key,
            cl7_identity_key_id=cl7_identity_key_id,
            cl7_ledger_store=cl7_ledger_store,
        )
        return cls(
            profile_store=profile_store,
            runtime_store=runtime_store,
            portfolio_repository=portfolio_repository,
            central_order_coordinator=coordinator,
            execution_adapter=adapter,
            portfolio_risk_runtime=portfolio_risk_runtime,
            cash_authority=cash_authority,
            account_id=account_id,
            account_scope_sha256=account_scope_sha256,
            cycle_source=cycle_source,
        )

    def restore(self) -> ConfiguredExecutionSet:
        configured = self._load_configured_set()
        self.scheduler = GlobalScheduler.restore(
            self.runtime_store,
            expected_account_id=self.account_id,
            session_id=self.session_id,
        )
        if tuple(item.runtime_key for item in self.scheduler.runtimes) != (
            configured.runtime_keys
        ):
            raise GuiRuntimeBlockedError(
                "CONFIGURED_SET_MISMATCH",
                "Scheduler restore differs from the validated configured set.",
            )
        self._configured_set = configured
        return configured

    def start_configured_set(self) -> GuiRuntimeTransitionResult:
        configured = self._prevalidate_start()
        scheduler = self.scheduler
        if scheduler is None:
            raise GuiRuntimeBlockedError("SCHEDULER_NOT_RESTORED")
        try:
            committed = scheduler.start_configured_set(
                configured.runtime_keys,
                expected_account_id=self.account_id,
            )
        except InstrumentRuntimeConflictError as exc:
            raise GuiRuntimeBlockedError("GROUP_START_FAILED", str(exc)) from exc
        self._configured_set = self._bind_profiles_to_runtimes(
            tuple(item.profile for item in configured.bindings), committed
        )
        return self._transition_result("ACTIVE", recovery_required=False)

    def stop_configured_set(self) -> GuiRuntimeTransitionResult:
        if self.scheduler is None or self._configured_set is None:
            self.restore()
        assert self.scheduler is not None and self._configured_set is not None
        current = self._load_configured_set()
        if current.identity_sha256 != self._configured_set.identity_sha256:
            raise GuiRuntimeBlockedError("CONFIGURED_SET_CHANGED")
        try:
            committed = self.scheduler.stop_configured_set(
                current.runtime_keys,
                expected_account_id=self.account_id,
            )
        except InstrumentRuntimeConflictError as exc:
            raise GuiRuntimeBlockedError("GROUP_STOP_FAILED", str(exc)) from exc
        self._configured_set = self._bind_profiles_to_runtimes(
            tuple(item.profile for item in current.bindings), committed
        )
        recovery_required = self._central_recovery_required(
            self.central_order_coordinator.manager.state()
        )
        return self._transition_result(
            "STOPPED",
            recovery_required=recovery_required,
            detail=(
                "Runtime evaluation stopped; accepted economic custody still requires recovery."
                if recovery_required
                else "Runtime evaluation stopped."
            ),
        )

    def service_tick(
        self,
        *,
        now: datetime,
        latest_closed_candles: Mapping[str, datetime | None],
        hooks: GuiStrategyHooks,
    ) -> SchedulerTickResult:
        if not self.connected:
            raise GuiRuntimeBlockedError("PROVIDER_DISCONNECTED")
        if self.market_state != "OPEN":
            raise GuiRuntimeBlockedError("MARKET_IDLE")
        if self.scheduler is None or self._configured_set is None:
            self.restore()
        if any(item.status != "ACTIVE" for item in self.scheduler.runtimes):
            raise GuiRuntimeBlockedError("CONFIGURED_SET_NOT_ACTIVE")
        if self._recovery_required():
            raise GuiRuntimeBlockedError("RECOVERY_REQUIRED")
        result = self.scheduler.tick(
            now=now,
            latest_closed_candles=latest_closed_candles,
            hooks=_CoordinatingHooks(self, hooks),
        )
        # Scheduler watermarks/revisions are durable owner state.  Keep the
        # process-local set identity aligned with the exact committed runtime
        # read model so a later account-level Stop cannot mistake an ordinary
        # completed tick for a concurrent configured-set replacement.
        assert self._configured_set is not None
        self._configured_set = self._bind_profiles_to_runtimes(
            tuple(item.profile for item in self._configured_set.bindings),
            self.scheduler.runtimes,
        )
        return result

    @property
    def service_ready(self) -> bool:
        return self._composition_blocker is None and self.cycle_source is not None

    def run_cycle(self) -> SchedulerTickResult:
        if self.cycle_source is None:
            raise GuiRuntimeBlockedError("GUI_RUNTIME_SOURCE_UNAVAILABLE")
        now, candles, hooks = self.cycle_source()
        return self.service_tick(
            now=now,
            latest_closed_candles=candles,
            hooks=hooks,
        )

    def set_connected(self, connected: bool) -> None:
        self.connected = bool(connected)

    def set_market_state(self, state: str) -> None:
        normalized = str(state or "").strip().upper()
        if normalized not in {"OPEN", "MARKET_IDLE"}:
            raise GuiRuntimeBlockedError("MARKET_STATE_INVALID")
        self.market_state = normalized

    @property
    def configured_set(self) -> ConfiguredExecutionSet | None:
        return self._configured_set

    def dashboard(
        self,
        *,
        portfolio_risk_snapshot: Mapping[str, Any] | None = None,
        cash_actionability_status: str | None = None,
    ) -> Any:
        """Build one read-only projection from accepted owner snapshots."""

        from .dashboard_view import build_multi_instrument_dashboard

        self._require_composed()
        configured = self._load_configured_set()
        portfolio = self.portfolio_repository.load(expected_account_id=self.account_id)
        central = self.central_order_coordinator.manager.state()
        risk_adapter = self.central_order_coordinator.risk_runtime
        risk_state = risk_adapter.state_store.load_account(self.account_id)
        risk_snapshot = {
            "risk_policy_hash": risk_adapter.current_policy_hash(),
            "risk_state_revision": getattr(risk_state, "revision", "UNKNOWN"),
            "risk_readiness": (
                "BLOCKED"
                if risk_state.kill_switch_active or risk_state.risk_resync_required
                else "READY"
            ),
            "kill_switch_active": risk_state.kill_switch_active,
            "risk_resync_required": risk_state.risk_resync_required,
        }
        if portfolio_risk_snapshot is None:
            portfolio_risk_snapshot = self._portfolio_risk_statuses(configured)
        authority = self._authority_record()
        if cash_actionability_status is None:
            cash_actionability_status = self._cash_actionability_status(authority)
        return build_multi_instrument_dashboard(
            (item.profile for item in configured.bindings),
            (item.runtime for item in configured.bindings),
            mode="SANDBOX_EXECUTION",
            portfolio_state=portfolio,
            central_state=central,
            risk_snapshot=risk_snapshot,
            portfolio_risk_snapshot=portfolio_risk_snapshot,
            authority_record=authority,
            cash_actionability_status=cash_actionability_status,
            account_scope_sha256=self.account_scope_sha256,
        )

    def _validate_static_bindings(self) -> None:
        if not self.account_id:
            raise GuiRuntimeBlockedError("ACCOUNT_SCOPE_MISSING")
        if len(self.account_scope_sha256) != 64 or any(
            item not in "0123456789abcdef" for item in self.account_scope_sha256
        ):
            raise GuiRuntimeBlockedError("ACCOUNT_SCOPE_PROOF_INVALID")
        coordinator = self.central_order_coordinator
        adapter = self.execution_adapter
        if self.portfolio_risk_runtime is None:
            raise GuiRuntimeBlockedError("PORTFOLIO_RISK_RUNTIME_MISSING")
        if coordinator.manager is not adapter.manager:
            raise GuiRuntimeBlockedError("CENTRAL_OWNER_MISMATCH")
        if coordinator.portfolio_repository is not self.portfolio_repository:
            raise GuiRuntimeBlockedError("PORTFOLIO_OWNER_MISMATCH")
        if (
            coordinator.portfolio_risk_runtime is not self.portfolio_risk_runtime
            or adapter.portfolio_risk_runtime is not self.portfolio_risk_runtime
        ):
            raise GuiRuntimeBlockedError("PORTFOLIO_RISK_RUNTIME_MISMATCH")
        if getattr(adapter, "risk_runtime", None) is not coordinator.risk_runtime:
            raise GuiRuntimeBlockedError("RISK_RUNTIME_MISMATCH")
        if getattr(adapter, "cash_authority_manager", None) is not self.cash_authority:
            raise GuiRuntimeBlockedError("CL7_AUTHORITY_OWNER_MISMATCH")
        if not bool(getattr(adapter.policy, "armed", False)):
            raise GuiRuntimeBlockedError("SANDBOX_EXECUTION_POLICY_NOT_ARMED")
        scopes = {
            coordinator.manager.account_id,
            adapter.manager.account_id,
            str(adapter.policy.account_id).strip(),
            self.portfolio_risk_runtime.account_id,
            str(getattr(coordinator.risk_runtime, "account_id", "")).strip(),
        }
        if scopes != {self.account_id}:
            raise GuiRuntimeBlockedError("ACCOUNT_SCOPE_MISMATCH")
        if str(getattr(coordinator.risk_runtime, "mode", "")).upper() != (
            "SANDBOX_EXECUTION"
        ):
            raise GuiRuntimeBlockedError("RISK_MODE_MISMATCH")

    def _require_composed(self) -> None:
        blocker = self._composition_blocker
        if blocker is not None:
            raise blocker

    def _portfolio_risk_statuses(
        self,
        configured: ConfiguredExecutionSet,
    ) -> dict[str, str]:
        """Read the accepted Portfolio Risk owner without granting authority."""

        try:
            report = self.portfolio_risk_runtime.recalculate_current(
                self.central_order_coordinator.manager,
                self.portfolio_repository,
            )
            if str(getattr(report, "account_id", "")).strip() != self.account_id:
                raise GuiRuntimeBlockedError("PORTFOLIO_RISK_ACCOUNT_MISMATCH")
            status = str(getattr(report, "status", "") or "").strip().upper()
            if not status:
                raise GuiRuntimeBlockedError("PORTFOLIO_RISK_STATUS_MISSING")
        except GuiRuntimeBlockedError:
            raise
        except Exception:  # noqa: BLE001 - presentation stays fail closed
            status = "BLOCKED"
        return {
            item.runtime.config.instrument_id: status
            for item in configured.bindings
        }

    @staticmethod
    def _cash_actionability_status(
        authority: RuntimeCashAuthorityRecord,
    ) -> str:
        if (
            authority.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED
            and authority.pending_dispatch_proof_sha256 is None
        ):
            return "LOCKED_REVALIDATION_REQUIRED"
        if (
            authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
            or authority.pending_dispatch_proof_sha256 is not None
        ):
            return "RECOVERY_REQUIRED"
        return "BLOCKED"

    def _load_configured_set(self) -> ConfiguredExecutionSet:
        self._require_composed()
        profiles = self.profile_store.load_mode("SANDBOX_EXECUTION")
        runtimes = self.runtime_store.load(expected_account_id=self.account_id)
        return self._bind_profiles_to_runtimes(profiles, runtimes)

    def _bind_profiles_to_runtimes(
        self,
        profiles: Sequence[MultiInstrumentProfile],
        runtimes: Sequence[InstrumentRuntime],
    ) -> ConfiguredExecutionSet:
        if not 1 <= len(profiles) <= MAX_V3_8_INSTRUMENTS:
            raise GuiRuntimeBlockedError("CONFIGURED_SET_SIZE_INVALID")
        by_instrument = {item.config.instrument_id: item for item in runtimes}
        if len(by_instrument) != len(runtimes) or len(runtimes) != len(profiles):
            raise GuiRuntimeBlockedError("CONFIGURED_SET_INCOMPLETE")
        bindings: list[ConfiguredRuntimeBinding] = []
        for profile in profiles:
            runtime = by_instrument.pop(profile.instrument_id, None)
            if runtime is None or runtime.config != profile.to_runtime_config(self.account_id):
                raise GuiRuntimeBlockedError("PROFILE_RUNTIME_IDENTITY_MISMATCH")
            bindings.append(ConfiguredRuntimeBinding(profile, runtime))
        if by_instrument:
            raise GuiRuntimeBlockedError("ORPHAN_RUNTIME")
        bindings.sort(
            key=lambda item: (
                item.runtime.config.account_id,
                item.runtime.config.instrument_id,
                item.runtime.config.candle_interval,
                item.runtime.config.strategy_id,
                item.runtime.config.runtime_config_hash,
            )
        )
        return ConfiguredExecutionSet(self.account_scope_sha256, tuple(bindings))

    def _prevalidate_start(self) -> ConfiguredExecutionSet:
        # Revalidate process-local owner identity at every Start.  The graph
        # can be stale or replaced after construction; construction-time
        # validation alone must not become a lasting authorization.
        self._validate_static_bindings()
        configured = self._load_configured_set()
        if self.scheduler is None:
            self.restore()
        assert self.scheduler is not None
        if self._configured_set is not None and any(
            item.status == "ACTIVE" for item in self.scheduler.runtimes
        ) and configured.identity_sha256 != self._configured_set.identity_sha256:
            raise GuiRuntimeBlockedError("ACTIVE_CONFIGURED_SET_MISMATCH")

        portfolio = self.portfolio_repository.load(expected_account_id=self.account_id)
        freshness = getattr(portfolio, "freshness", None)
        freshness_value = str(getattr(freshness, "value", freshness) or "").upper()
        if freshness_value != "FRESH" or bool(getattr(portfolio, "blocking", True)):
            raise GuiRuntimeBlockedError("PORTFOLIO_STATE_NOT_READY")
        for binding in configured.bindings:
            position = portfolio.position(binding.profile.instrument_id)
            if (
                position is None
                or position.target is None
                or position.reconciliation is None
                or bool(position.reconciliation.blocking)
                or str(getattr(position.reconciliation, "status", "UNKNOWN")).upper()
                in {"UNKNOWN", "STALE", "NOT_RECONCILED"}
            ):
                raise GuiRuntimeBlockedError(
                    "PORTFOLIO_POSITION_NOT_READY", binding.profile.instrument_id
                )

        central = self.central_order_coordinator.manager.state()
        if central.account_id != self.account_id:
            raise GuiRuntimeBlockedError("CENTRAL_ACCOUNT_SCOPE_MISMATCH")
        if self._central_recovery_required(central):
            raise GuiRuntimeBlockedError("RECOVERY_REQUIRED")

        policy_hash = self.central_order_coordinator.risk_runtime.current_policy_hash()
        if not policy_hash:
            raise GuiRuntimeBlockedError("RISK_POLICY_UNAVAILABLE")
        risk_state = self.central_order_coordinator.risk_runtime.state_store.load_account(
            self.account_id
        )
        if risk_state.kill_switch_active:
            raise GuiRuntimeBlockedError("RISK_KILL_SWITCH_ACTIVE")
        if risk_state.risk_resync_required:
            raise GuiRuntimeBlockedError("RISK_RESYNC_REQUIRED")
        configured_instruments = {
            item.profile.instrument_id for item in configured.bindings
        }
        if configured_instruments.intersection(
            item.instrument_id for item in risk_state.instrument_kill_switches
        ):
            raise GuiRuntimeBlockedError("RISK_INSTRUMENT_KILL_SWITCH_ACTIVE")

        authority = self._authority_record()
        if authority.account_scope_sha256 != self.account_scope_sha256:
            raise GuiRuntimeBlockedError("CL7_ACCOUNT_SCOPE_MISMATCH")
        reason_by_state = {
            RuntimeCashAuthorityState.LEGACY_ACTIVE: "CL7_EXACT_AUTHORITY_REQUIRED",
            RuntimeCashAuthorityState.CUTOVER_PREPARED: "CL7_CUTOVER_INCOMPLETE",
            RuntimeCashAuthorityState.CUTOVER_CONFIRMED: "CL7_CUTOVER_INCOMPLETE",
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED: "CL7_EXACT_AUTHORITY_DISARMED",
            RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING: "CL7_RECOVERY_REQUIRED",
        }
        if authority.state is not RuntimeCashAuthorityState.EXACT_CASH_ARMED:
            raise GuiRuntimeBlockedError(reason_by_state.get(authority.state, "CL7_STATE_INVALID"))
        if authority.pending_dispatch_proof_sha256 is not None:
            raise GuiRuntimeBlockedError("CL7_RECOVERY_REQUIRED")

        # A second read gives the store CAS exact revisions to protect against
        # changes after all owner prevalidation completed.
        exact = self._load_configured_set()
        if exact.identity_sha256 != configured.identity_sha256:
            raise GuiRuntimeBlockedError("CONFIGURED_SET_CHANGED")
        self._configured_set = configured
        return configured

    def _authority_record(self) -> RuntimeCashAuthorityRecord:
        loader = getattr(self.cash_authority, "status", None)
        if not callable(loader):
            loader = getattr(self.cash_authority, "load", None)
        if not callable(loader):
            raise GuiRuntimeBlockedError("CL7_AUTHORITY_UNAVAILABLE")
        record = loader()
        if not isinstance(record, RuntimeCashAuthorityRecord):
            raise GuiRuntimeBlockedError("CL7_AUTHORITY_INVALID")
        return record

    @staticmethod
    def _central_recovery_required(state: Any) -> bool:
        return any(
            str(getattr(item, "status", "")).upper()
            in {"QUEUED", "IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
            for item in state.intents
        )

    def _recovery_required(self) -> bool:
        central = self.central_order_coordinator.manager.state()
        authority = self._authority_record()
        return self._central_recovery_required(central) or (
            authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
            or authority.pending_dispatch_proof_sha256 is not None
        )

    def _transition_result(
        self,
        status: str,
        *,
        recovery_required: bool,
        detail: str = "",
    ) -> GuiRuntimeTransitionResult:
        assert self.scheduler is not None and self._configured_set is not None
        return GuiRuntimeTransitionResult(
            status=status,
            configured_set_sha256=self._configured_set.identity_sha256,
            runtime_statuses=tuple(
                (item.runtime_key, item.status) for item in self.scheduler.runtimes
            ),
            recovery_required=recovery_required,
            detail=detail,
        )


__all__ = [
    "ConfiguredExecutionSet",
    "ConfiguredRuntimeBinding",
    "GuiCoordinationRequest",
    "GuiRuntimeBlockedError",
    "GuiRuntimeController",
    "GuiRuntimeTransitionResult",
    "GuiStrategyHooks",
]
