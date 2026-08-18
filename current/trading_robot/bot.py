from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from math import ceil
import json
import logging
from pathlib import Path
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pandas as pd

from . import __version__
from .candle_policy import candle_interval_policy, strategy_lookback_days
from .journal import EventJournal, JournalEvent
from .locking import InterProcessFileLock, LockUnavailableError
from .market_idle import classify_market_status, seconds_since
from .portfolio_manager import CanonicalPortfolioManager
from .portfolio_preflight import (
    PortfolioPreflightDecision,
    PortfolioPreflightGate,
    PortfolioRevisionCheck,
    PortfolioSnapshotLease,
    portfolio_risk_mapping,
)
from .post_fill_portfolio import (
    PostFillPortfolioCoordinator,
    PostFillPortfolioResult,
)
from .crash_injection import CrashInjector
from .orders import (
    OrderLifecycle,
    executed_lots,
    executed_order_price,
    is_terminal_order_status,
    lifecycle_for_status,
    normalize_execution_status,
    signed_lot_delta,
    transition_intent,
)
from .resilience import CircuitBreakerConfig, PersistentCircuitBreaker
from .recovery import RecoveryAction, RecoveryDecision, StartupRecoveryCoordinator
from .risk import portfolio_risk_inputs
from .risk_runtime import (
    RiskExecutionOutcome,
    RiskRuntimeAdapter,
    RiskRuntimeOutcome,
)
from .state_persistence import (
    StatePersistenceError,
    atomic_write_json,
)
from .strategy import StrategyName
from .strategy_runtime import (
    StrategyDecision,
    StrategySuiteConfig,
    compare_strategy_decisions,
    evaluate_strategy_suite,
    normalize_strategy_name,
    strategy_suite_from_bot_config,
)
from .tbank_sandbox import TBankAPIError, TBankSandboxClient


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BotConfig:
    ticker: str = "SBER"
    class_code: str = "TQBR"
    candle_interval: str = "CANDLE_INTERVAL_HOUR"
    primary_strategy: str = "sma"
    shadow_strategies: tuple[str, ...] = ()

    # SMA strategy. fast_window/slow_window are kept for compatibility with
    # previous .env files and command-line configurations.
    fast_window: int = 20
    slow_window: int = 50
    sma_hysteresis_percent: float = 0.002

    # Donchian breakout + ATR trailing exit.
    donchian_entry_window: int = 55
    donchian_exit_window: int = 20
    donchian_atr_window: int = 20
    donchian_trailing_stop_atr: float = 3.0

    # Slow trend ensemble.
    ensemble_sma_fast: int = 50
    ensemble_sma_slow: int = 200
    ensemble_momentum_window: int = 126
    ensemble_breakout_window: int = 100
    ensemble_vote_threshold: int = 3

    # Common risk/position-size layer. None disables volatility targeting.
    annual_target_volatility: float | None = None
    volatility_window: int = 20
    max_strategy_weight: float = 1.0

    lookback_days: int = 30
    poll_seconds: int = 300
    max_order_lots: int = 1
    dry_run: bool = True
    state_file: str = "robot_state.json"
    journal_file: str = ""
    order_type: str = "BESTPRICE"
    time_in_force: str = "FILL_AND_KILL"
    check_trading_status: bool = True
    failure_threshold: int = 3
    circuit_open_seconds: int = 60
    circuit_max_open_seconds: int = 900
    max_signal_age_seconds: int = 0
    reconcile_attempts: int = 3
    reconcile_delay_seconds: float = 0.5
    portfolio_reconcile_interval_seconds: int = 900

    # Market-aware idle mode.  The robot periodically checks the broker's
    # trading-status endpoint before loading candles.  When the configured
    # order type is explicitly unavailable, strategy evaluation is suspended
    # until the market becomes executable again.  Recovery of an existing
    # pending order always has priority over MARKET_IDLE.
    market_idle_enabled: bool = True
    market_status_check_seconds: int = 300
    market_idle_poll_seconds: int = 300
    market_idle_reconcile_seconds: int = 1800
    market_idle_heartbeat_seconds: int = 1800

    # v3.7-alpha3 canonical-only Portfolio Manager authorization gate.  It is
    # applied to Sandbox execution only; Dry-run remains observability-only.
    portfolio_preflight_enabled: bool = True
    # Deprecated compatibility input. Alpha3 always forces canonical-only reads.
    portfolio_preflight_require_dual_read: bool = False
    portfolio_state_file: str = ""

    # Observability-only settings; they never change trading decisions.
    session_id: str = ""
    heartbeat_log_seconds: int = 1800
    slow_cycle_seconds: float = 10.0

    def __post_init__(self) -> None:
        primary = normalize_strategy_name(self.primary_strategy)
        shadows: list[StrategyName] = []
        for item in self.shadow_strategies:
            strategy = normalize_strategy_name(item)
            if strategy != primary and strategy not in shadows:
                shadows.append(strategy)
        object.__setattr__(self, "primary_strategy", primary)
        object.__setattr__(self, "shadow_strategies", tuple(shadows))
        # Never allow an alpha2 profile to re-enable legacy portfolio reads.
        object.__setattr__(self, "portfolio_preflight_require_dual_read", False)

        StrategySuiteConfig(
            primary_strategy=primary,
            shadow_strategies=tuple(shadows),
            sma_fast_window=self.fast_window,
            sma_slow_window=self.slow_window,
            sma_hysteresis_percent=self.sma_hysteresis_percent,
            donchian_entry_window=self.donchian_entry_window,
            donchian_exit_window=self.donchian_exit_window,
            donchian_atr_window=self.donchian_atr_window,
            donchian_trailing_stop_atr=self.donchian_trailing_stop_atr,
            ensemble_sma_fast=self.ensemble_sma_fast,
            ensemble_sma_slow=self.ensemble_sma_slow,
            ensemble_momentum_window=self.ensemble_momentum_window,
            ensemble_breakout_window=self.ensemble_breakout_window,
            ensemble_vote_threshold=self.ensemble_vote_threshold,
            annual_target_volatility=self.annual_target_volatility,
            volatility_window=self.volatility_window,
            max_weight=self.max_strategy_weight,
            position_limit_lots=self.max_order_lots,
        )
        if self.lookback_days < 1:
            raise ValueError("lookback_days must be positive.")
        if self.poll_seconds < 10:
            raise ValueError("poll_seconds must be at least 10.")
        if self.max_order_lots < 1:
            raise ValueError("max_order_lots must be positive.")
        if self.order_type.upper() not in {"MARKET", "BESTPRICE"}:
            raise ValueError("order_type must be MARKET or BESTPRICE.")
        if self.time_in_force.upper() not in {
            "DAY",
            "FILL_AND_KILL",
            "FILL_OR_KILL",
        }:
            raise ValueError("Unsupported time_in_force.")
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be positive.")
        if self.circuit_open_seconds < 1:
            raise ValueError("circuit_open_seconds must be positive.")
        if self.circuit_max_open_seconds < self.circuit_open_seconds:
            raise ValueError(
                "circuit_max_open_seconds must be >= circuit_open_seconds."
            )
        if self.max_signal_age_seconds < 0:
            raise ValueError("max_signal_age_seconds must not be negative.")
        if self.reconcile_attempts < 1:
            raise ValueError("reconcile_attempts must be positive.")
        if self.reconcile_delay_seconds < 0:
            raise ValueError("reconcile_delay_seconds must not be negative.")
        if self.portfolio_reconcile_interval_seconds < 60:
            raise ValueError(
                "portfolio_reconcile_interval_seconds must be at least 60."
            )
        if self.market_status_check_seconds < 30:
            raise ValueError("market_status_check_seconds must be at least 30.")
        if self.market_idle_poll_seconds < 30:
            raise ValueError("market_idle_poll_seconds must be at least 30.")
        if self.market_idle_reconcile_seconds < 60:
            raise ValueError(
                "market_idle_reconcile_seconds must be at least 60."
            )
        if self.market_idle_heartbeat_seconds < 0:
            raise ValueError(
                "market_idle_heartbeat_seconds must not be negative."
            )
        if self.heartbeat_log_seconds < 0:
            raise ValueError("heartbeat_log_seconds must not be negative.")
        if self.slow_cycle_seconds <= 0:
            raise ValueError("slow_cycle_seconds must be positive.")


class SandboxTradingBot:
    """Long-only PRIMARY/SHADOW robot with persistent recovery.

    Only PRIMARY can create broker orders. SHADOW strategies are evaluated on
    the same completed candles, persisted and journalled for forward comparison.
    The class has no production-order route.
    """

    STATE_VERSION = 6

    def __init__(
        self,
        api: TBankSandboxClient,
        account_id: str,
        config: BotConfig,
        *,
        allow_execution: bool = False,
        risk_runtime: RiskRuntimeAdapter | None = None,
        require_risk_runtime_for_execution: bool = False,
        recovery_coordinator: StartupRecoveryCoordinator | None = None,
        crash_injector: CrashInjector | None = None,
    ) -> None:
        self.api = api
        self.account_id = str(account_id)
        self.config = config
        self.allow_execution = bool(allow_execution) and not config.dry_run
        self.risk_runtime = risk_runtime
        self.require_risk_runtime_for_execution = bool(
            require_risk_runtime_for_execution
        )
        self.recovery_coordinator = (
            recovery_coordinator or StartupRecoveryCoordinator()
        )
        self.crash_injector = crash_injector or CrashInjector.from_environment()
        expected_risk_mode = (
            "SANDBOX_EXECUTION" if self.allow_execution else "DRY_RUN"
        )
        if self.risk_runtime is not None and self.risk_runtime.mode != expected_risk_mode:
            raise ValueError(
                "Risk runtime mode mismatch: expected "
                f"{expected_risk_mode}, got {self.risk_runtime.mode}."
            )
        if (
            self.allow_execution
            and self.require_risk_runtime_for_execution
            and self.risk_runtime is None
        ):
            raise ValueError(
                "Sandbox execution requires a RiskRuntimeAdapter in v3.9.0."
            )
        self.strategy_suite = strategy_suite_from_bot_config(config)
        self.primary_strategy: StrategyName = self.strategy_suite.primary_strategy
        self.primary_config_hash = self.strategy_suite.config_hash(
            self.primary_strategy
        )
        self.suite_hash = self.strategy_suite.suite_hash()
        self.instrument: dict[str, Any] | None = None
        self.instrument_id: str | None = None
        self.state_path = Path(config.state_file)
        journal_path = (
            Path(config.journal_file)
            if config.journal_file
            else self.state_path.with_name("trading_events.db")
        )
        self.journal = EventJournal(journal_path)
        self.portfolio_preflight_enabled = bool(
            config.portfolio_preflight_enabled and self.allow_execution
        )
        self.portfolio_state_path = (
            Path(config.portfolio_state_file)
            if str(config.portfolio_state_file).strip()
            else self.state_path.with_name("portfolio_state.json")
        )
        self.canonical_portfolio_manager: CanonicalPortfolioManager | None = None
        self.portfolio_preflight_gate = PortfolioPreflightGate()
        self.post_fill_portfolio: PostFillPortfolioCoordinator | None = None
        if self.portfolio_preflight_enabled:
            self.canonical_portfolio_manager = CanonicalPortfolioManager(
                self.api,
                self.account_id,
                robot_state_file=self.state_path,
                portfolio_state_file=self.portfolio_state_path,
                journal_file=journal_path,
            )
            self.post_fill_portfolio = PostFillPortfolioCoordinator(
                self.canonical_portfolio_manager
            )
        instrument_scope = f"{config.ticker.upper()}_{config.class_code.upper()}"
        self.state_key = "|".join(
            [
                self.account_id,
                instrument_scope,
                config.candle_interval,
                f"primary:{self.primary_strategy}:{self.primary_config_hash[:16]}",
            ]
        )
        # A broker position belongs to account + instrument, not to a
        # timeframe.  Therefore only one PRIMARY configuration may own the
        # position across all intervals in the same Sandbox account.
        self.execution_scope_key = "|".join(
            [self.account_id, instrument_scope]
        )
        self.legacy_execution_scope_key = "|".join(
            [self.account_id, instrument_scope, config.candle_interval]
        )
        self.legacy_state_key = "|".join(
            [
                self.account_id,
                instrument_scope,
                config.candle_interval,
                f"sma:{config.fast_window}:{config.slow_window}",
            ]
        )
        self.breaker_config = CircuitBreakerConfig(
            failure_threshold=config.failure_threshold,
            open_seconds=config.circuit_open_seconds,
            max_open_seconds=config.circuit_max_open_seconds,
        )
        self.session_id = config.session_id.strip() or str(uuid4())
        self.session_started_at = datetime.now(timezone.utc)
        self._session_closed = False
        self._active_run_id: str | None = None
        self._order_submission_attempted_in_cycle = False
        self._cycle_api_events: list[dict[str, Any]] = []
        self._cycle_timings: dict[str, float] = {}
        self._last_lookback_notice: tuple[Any, ...] | None = None
        self._last_heartbeat_log_monotonic = 0.0
        self._market_cycle_context: dict[str, Any] = {}
        if hasattr(self.api, "set_telemetry_callback"):
            self.api.set_telemetry_callback(self._handle_api_telemetry)
        self._journal_safe(
            JournalEvent(
                category="session",
                event_type="STARTED",
                severity="INFO",
                session_id=self.session_id,
                account_id=self.account_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload={
                    "strategy_suite_hash": self.suite_hash,
                    "shadow_strategies": list(self.strategy_suite.shadow_strategies),
                    "candle_interval": self.config.candle_interval,
                    "max_order_lots": self.config.max_order_lots,
                    "software_version": __version__,
                    "risk_enforced": self.risk_runtime is not None,
                    "risk_enforcement_scope": (
                        "DRY_RUN_AND_SANDBOX"
                        if self.risk_runtime is not None
                        else "NOT_ENFORCED"
                    ),
                    "market_idle_enabled": self.config.market_idle_enabled,
                    "market_status_check_seconds": (
                        self.config.market_status_check_seconds
                    ),
                    "market_idle_poll_seconds": (
                        self.config.market_idle_poll_seconds
                    ),
                    "market_idle_reconcile_seconds": (
                        self.config.market_idle_reconcile_seconds
                    ),
                },
                timestamp_utc=self.session_started_at.isoformat(),
            )
        )

    def run_once(self) -> dict[str, Any]:
        cycle_started_at = datetime.now(timezone.utc)
        cycle_started_perf = time.perf_counter()
        run_id = str(uuid4())
        lock_path = Path(str(self.state_path) + ".lock")
        try:
            with InterProcessFileLock(lock_path, timeout_seconds=1.0):
                return self._run_once_locked(
                    run_id=run_id,
                    cycle_started_at=cycle_started_at,
                    cycle_started_perf=cycle_started_perf,
                )
        except LockUnavailableError as exc:
            self._cycle_api_events = []
            result = {
                "status": "state_locked",
                "api_state": "NOT_CALLED",
                "ticker": self.config.ticker,
                "primary_strategy": self.primary_strategy,
                "primary_config_hash": self.primary_config_hash,
                "strategy_suite_hash": self.suite_hash,
                "mode": self._mode_name(),
                "error": str(exc),
                "recommended_wait_seconds": max(5, self.config.poll_seconds),
                "pending_order": None,
                "circuit_state": "CLOSED",
                "consecutive_failures": 0,
                "run_id": run_id,
            }
            self._finalize_cycle_observability(
                result,
                cycle_started_at=cycle_started_at,
                cycle_started_perf=cycle_started_perf,
            )
            self._record_cycle_safe(result, run_id)
            self._log_cycle_result(result)
            return result

    def _run_once_locked(
        self,
        *,
        run_id: str,
        cycle_started_at: datetime,
        cycle_started_perf: float,
    ) -> dict[str, Any]:
        self._active_run_id = run_id
        self._order_submission_attempted_in_cycle = False
        self._cycle_api_events = []
        self._cycle_timings = {}
        self._market_cycle_context = {}
        root_state: dict[str, Any] | None = None
        bot_state: dict[str, Any] = {}
        try:
            root_state = self._load_state()
            self._migrate_execution_scope(root_state)
            bots = root_state.setdefault("bots", {})
            self._migrate_legacy_state_key(bots)
            bot_state = bots.setdefault(self.state_key, {})
            self._migrate_bot_state(bot_state)
            health_state = bot_state.setdefault("health", {})
            breaker = PersistentCircuitBreaker(health_state, self.breaker_config)
            circuit_before_attempt = str(health_state.get("state") or "CLOSED")
            can_attempt, retry_after = breaker.can_attempt(cycle_started_at)
            self._record_circuit_transition_safe(
                previous_state=circuit_before_attempt,
                current_state=str(health_state.get("state") or "CLOSED"),
                run_id=run_id,
                breaker=breaker,
                now=cycle_started_at,
                reason="retry_window_elapsed" if can_attempt else None,
            )

            if not can_attempt:
                result = self._degraded_result(
                    now=cycle_started_at,
                    run_id=run_id,
                    bot_state=bot_state,
                    breaker=breaker,
                    status="circuit_open",
                    error="T-Invest API circuit breaker is open.",
                    recommended_wait_seconds=retry_after,
                )
                self._finalize_cycle_observability(
                    result,
                    cycle_started_at=cycle_started_at,
                    cycle_started_perf=cycle_started_perf,
                )
                self._record_cycle_safe(result, run_id)
                self._log_cycle_result(result)
                return result

            try:
                result = self._run_once_impl(
                    now=cycle_started_at,
                    run_id=run_id,
                    root_state=root_state,
                    bot_state=bot_state,
                )
            except TBankAPIError as exc:
                failure_at = datetime.now(timezone.utc)
                if exc.transient:
                    circuit_before_failure = str(
                        health_state.get("state") or "CLOSED"
                    )
                    opened_for = breaker.record_failure(str(exc), failure_at)
                    self._record_circuit_transition_safe(
                        previous_state=circuit_before_failure,
                        current_state=str(health_state.get("state") or "CLOSED"),
                        run_id=run_id,
                        breaker=breaker,
                        now=failure_at,
                        reason=str(exc),
                    )
                    self._save_state(root_state)
                    recommended = int(
                        exc.retry_after_seconds
                        or opened_for
                        or min(self.config.poll_seconds, 30)
                    )
                    result = self._degraded_result(
                        now=failure_at,
                        run_id=run_id,
                        bot_state=bot_state,
                        breaker=breaker,
                        status=(
                            "circuit_open"
                            if breaker.snapshot(failure_at)["circuit_state"] == "OPEN"
                            else "api_degraded"
                        ),
                        error=str(exc),
                        recommended_wait_seconds=max(5, recommended),
                        api_error=exc,
                    )
                    self._record_incident_safe(
                        event_type="TRANSIENT_API_ERROR",
                        severity="WARNING",
                        run_id=run_id,
                        bot_state=bot_state,
                        payload={
                            "error": str(exc),
                            "service": exc.service,
                            "method": exc.method,
                            "status_code": exc.status_code,
                            "tracking_id": exc.tracking_id,
                            "error_class": exc.error_class,
                            "health": breaker.snapshot(failure_at),
                        },
                    )
                    self._finalize_cycle_observability(
                        result,
                        cycle_started_at=cycle_started_at,
                        cycle_started_perf=cycle_started_perf,
                    )
                    self._record_cycle_safe(result, run_id)
                    self._log_cycle_result(result)
                    return result

                self._record_incident_safe(
                    event_type="NON_TRANSIENT_API_ERROR",
                    severity="ERROR",
                    run_id=run_id,
                    bot_state=bot_state,
                    payload={
                        "error": str(exc),
                        "service": exc.service,
                        "method": exc.method,
                        "status_code": exc.status_code,
                        "tracking_id": exc.tracking_id,
                        "error_class": exc.error_class,
                        "details": exc.details,
                    },
                )
                failed_result = {
                    "status": "failed",
                    "api_state": "ERROR",
                    "ticker": self.config.ticker,
                    "mode": self._mode_name(),
                    "error": str(exc),
                    "api_service": exc.service,
                    "api_method": exc.method,
                    "api_status_code": exc.status_code,
                    "tracking_id": exc.tracking_id,
                    "pending_order": bot_state.get("pending_order"),
                    "run_id": run_id,
                }
                self._finalize_cycle_observability(
                    failed_result,
                    cycle_started_at=cycle_started_at,
                    cycle_started_perf=cycle_started_perf,
                )
                self._record_cycle_safe(failed_result, run_id)
                self._log_cycle_result(failed_result)
                raise
            except StatePersistenceError:
                # The outer safety handler returns a structured, non-trading
                # result. Do not misclassify a local state failure as an
                # arbitrary strategy exception.
                raise
            except Exception as exc:
                self._record_incident_safe(
                    event_type="UNEXPECTED_ERROR",
                    severity="ERROR",
                    run_id=run_id,
                    bot_state=bot_state,
                    payload={"error": repr(exc)},
                )
                failed_result = {
                    "status": "failed",
                    "api_state": "UNKNOWN",
                    "ticker": self.config.ticker,
                    "mode": self._mode_name(),
                    "error": repr(exc),
                    "pending_order": bot_state.get("pending_order"),
                    "run_id": run_id,
                }
                self._finalize_cycle_observability(
                    failed_result,
                    cycle_started_at=cycle_started_at,
                    cycle_started_perf=cycle_started_perf,
                )
                self._record_cycle_safe(failed_result, run_id)
                self._log_cycle_result(failed_result)
                raise

            success_at = datetime.now(timezone.utc)
            circuit_before_success = str(health_state.get("state") or "CLOSED")
            breaker.record_success(success_at)
            self._record_circuit_transition_safe(
                previous_state=circuit_before_success,
                current_state=str(health_state.get("state") or "CLOSED"),
                run_id=run_id,
                breaker=breaker,
                now=success_at,
                reason="api_call_succeeded",
            )
            self._save_state(root_state)
            result.update(self._health_fields(breaker, success_at))
            result.setdefault("api_state", "AVAILABLE")
            result.setdefault("recommended_wait_seconds", self.config.poll_seconds)
            result.setdefault("mode", self._mode_name())
            result.setdefault("primary_strategy", self.primary_strategy)
            result.setdefault("primary_config_hash", self.primary_config_hash)
            result.setdefault("strategy_suite_hash", self.suite_hash)
            result.setdefault(
                "shadow_strategies", list(self.strategy_suite.shadow_strategies)
            )
            result.setdefault(
                "tracking_id",
                getattr(self.api, "last_response_meta", {}).get("tracking_id"),
            )
            self._finalize_cycle_observability(
                result,
                cycle_started_at=cycle_started_at,
                cycle_started_perf=cycle_started_perf,
            )
            self._record_cycle_safe(result, run_id)
            self._log_cycle_result(result)
            return result
        except StatePersistenceError as exc:
            failed_at = datetime.now(timezone.utc)
            pending = bot_state.get("pending_order")
            lifecycle = str((pending or {}).get("lifecycle_state") or "")
            definitely_submitted_states = {
                str(OrderLifecycle.ORDER_ACCEPTED),
                str(OrderLifecycle.NEW),
                str(OrderLifecycle.PARTIALLY_FILLED),
                str(OrderLifecycle.FILLED),
                str(OrderLifecycle.RECONCILIATION_REQUIRED),
                str(OrderLifecycle.RISK_ACCOUNTING_REQUIRED),
                str(OrderLifecycle.RISK_ACCOUNTED),
                str(OrderLifecycle.PORTFOLIO_RECONCILED),
            }
            order_was_sent = (
                self._order_submission_attempted_in_cycle
                or lifecycle in definitely_submitted_states
            )
            order_may_have_been_sent = (
                order_was_sent
                or lifecycle == str(OrderLifecycle.ORDER_SUBMITTED)
            )
            breaker_snapshot = PersistentCircuitBreaker(
                bot_state.setdefault("health", {}),
                self.breaker_config,
            )
            result = {
                "status": "state_save_failed",
                "api_state": "LOCAL_STATE_ERROR",
                "ticker": self.config.ticker,
                "instrument_id": self.instrument_id,
                "primary_strategy": self.primary_strategy,
                "primary_config_hash": self.primary_config_hash,
                "strategy_suite_hash": self.suite_hash,
                "shadow_strategies": list(self.strategy_suite.shadow_strategies),
                "mode": self._mode_name(),
                "error": str(exc),
                "state_path": str(exc.path),
                "state_save_phase": exc.phase,
                "state_save_attempts": exc.attempts,
                "pending_order": pending,
                "order_may_have_been_sent": order_may_have_been_sent,
                "order_was_sent": order_was_sent,
                "new_orders_blocked": True,
                "execution_block_reason": (
                    "Локальное состояние не сохранено. Новые заявки "
                    "заблокированы до успешного повторного цикла и сверки."
                ),
                "recommended_wait_seconds": max(5, min(self.config.poll_seconds, 30)),
                "run_id": run_id,
                "last_check_time": failed_at.isoformat(),
                **self._health_fields(breaker_snapshot, failed_at),
            }
            self._record_incident_safe(
                event_type="STATE_SAVE_FAILED",
                severity="ERROR",
                run_id=run_id,
                bot_state=bot_state,
                payload={
                    "error": str(exc),
                    "path": str(exc.path),
                    "phase": exc.phase,
                    "attempts": exc.attempts,
                    "pending_order": pending,
                    "order_may_have_been_sent": order_may_have_been_sent,
                },
            )
            self._finalize_cycle_observability(
                result,
                cycle_started_at=cycle_started_at,
                cycle_started_perf=cycle_started_perf,
            )
            self._record_cycle_safe(result, run_id)
            self._log_cycle_result(result)
            return result
        finally:
            self._active_run_id = None

    def _run_once_impl(
        self,
        *,
        now: datetime,
        run_id: str,
        root_state: dict[str, Any],
        bot_state: dict[str, Any],
    ) -> dict[str, Any]:
        with self._measure_phase("instrument_resolution"):
            self._ensure_instrument(bot_state)
        assert self.instrument is not None
        assert self.instrument_id is not None

        pending = bot_state.get("pending_order")
        if pending:
            recovery_decision = self.recovery_coordinator.assess(
                pending,
                allow_execution=self.allow_execution,
            )
            pending["recovery_id"] = recovery_decision.recovery_id
            pending["recovery_assessed_at"] = recovery_decision.assessed_at
            pending["recovery_action"] = str(recovery_decision.action)
            pending["recovery_reason"] = recovery_decision.reason
            bot_state["last_recovery"] = recovery_decision.to_dict()
            bot_state["pending_order"] = pending
            self._save_state(root_state)
            self._record_recovery_decision_safe(
                recovery_decision,
                pending=pending,
                run_id=run_id,
                event_type="RECOVERY_ASSESSED",
            )

            if recovery_decision.action == RecoveryAction.CANCEL_INTENT_AND_REEVALUATE:
                bot_state.pop("pending_order", None)
                bot_state["last_recovery"] = {
                    **recovery_decision.to_dict(),
                    "status": "COMPLETED",
                    "result": "INTENT_CANCELLED",
                }
                self._save_state(root_state)
                self._record_recovery_decision_safe(
                    recovery_decision,
                    pending=pending,
                    run_id=run_id,
                    event_type="RECOVERY_COMPLETED",
                    payload={"result": "INTENT_CANCELLED"},
                )
                return {
                    "status": "intent_reevaluation_required",
                    "ticker": self.config.ticker,
                    "instrument_id": self.instrument_id,
                    "candle_time": pending.get("candle_time"),
                    "action": pending.get("action"),
                    "order_was_sent": False,
                    "resubmitted": False,
                    "pending_order": None,
                    "new_orders_blocked": False,
                    "recovery": bot_state["last_recovery"],
                    "run_id": run_id,
                }

            if recovery_decision.action in {
                RecoveryAction.FINALIZE_ACCOUNTED,
                RecoveryAction.FINALIZE_FAILED,
            }:
                bot_state.pop("pending_order", None)
                bot_state["last_recovery"] = {
                    **recovery_decision.to_dict(),
                    "status": "COMPLETED",
                    "result": str(recovery_decision.action),
                }
                self._save_state(root_state)
                self._record_recovery_decision_safe(
                    recovery_decision,
                    pending=pending,
                    run_id=run_id,
                    event_type="RECOVERY_COMPLETED",
                    payload={"result": str(recovery_decision.action)},
                )
                return {
                    "status": "order_recovered",
                    "ticker": self.config.ticker,
                    "instrument_id": self.instrument_id,
                    "candle_time": pending.get("candle_time"),
                    "action": pending.get("action"),
                    "order_was_sent": True,
                    "pending_order": None,
                    "position_reconciled": bool(
                        pending.get("reconciliation_proof", {}).get(
                            "position_reconciled"
                        )
                    ),
                    "recovery": bot_state["last_recovery"],
                    "run_id": run_id,
                }

            if recovery_decision.action == RecoveryAction.BLOCK_MANUAL_REVIEW:
                pending["recovery_status"] = "MANUAL_REVIEW_REQUIRED"
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                self._record_recovery_decision_safe(
                    recovery_decision,
                    pending=pending,
                    run_id=run_id,
                    event_type="RECOVERY_BLOCKED",
                    severity="ERROR",
                )
                return {
                    "status": "recovery_blocked",
                    "ticker": self.config.ticker,
                    "instrument_id": self.instrument_id,
                    "candle_time": pending.get("candle_time"),
                    "action": pending.get("action"),
                    "order_was_sent": (
                        str(pending.get("lifecycle_state") or "")
                        not in {
                            str(OrderLifecycle.DECISION_CREATED),
                            str(OrderLifecycle.PRECHECK_PASSED),
                            str(OrderLifecycle.INTENT_SAVED),
                        }
                    ),
                    "pending_order": pending,
                    "new_orders_blocked": True,
                    "execution_block_reason": recovery_decision.reason,
                    "recovery": recovery_decision.to_dict(),
                    "run_id": run_id,
                }

            with self._measure_phase("pending_order_recovery"):
                recovery = self._recover_pending_order(
                    root_state,
                    bot_state,
                    pending,
                    allow_resubmit=False,
                    run_id=run_id,
                    recovery_decision=recovery_decision,
                )
            recovery = {**recovery_decision.to_dict(), **recovery}
            event_type = (
                "RECOVERY_COMPLETED"
                if not bot_state.get("pending_order")
                else "RECOVERY_PENDING"
            )
            self._record_recovery_decision_safe(
                recovery_decision,
                pending=pending,
                run_id=run_id,
                event_type=event_type,
                severity=(
                    "ERROR"
                    if recovery.get("status") in {
                        "unknown_submit_state",
                        "risk_authorization_missing",
                        "recovery_blocked",
                    }
                    else "INFO"
                ),
                payload={"result": recovery},
            )
            if bot_state.get("pending_order"):
                pending_status = str(recovery.get("status") or "order_pending")
                if pending_status not in {
                    "risk_accounting_pending",
                    "risk_authorization_missing",
                    "unknown_submit_state",
                    "recovery_blocked",
                }:
                    pending_status = "order_pending"
                return {
                    "status": pending_status,
                    "ticker": self.config.ticker,
                    "instrument_id": self.instrument_id,
                    "candle_time": pending.get("candle_time"),
                    "action": pending.get("action"),
                    "intended_action": pending.get("action"),
                    "order_was_sent": True,
                    "resubmitted": bool(recovery.get("resubmitted", False)),
                    "target_lots": pending.get("target_lots"),
                    "current_lots": recovery.get("actual_lots_after"),
                    "pending_order": bot_state.get("pending_order"),
                    "order_lifecycle_state": bot_state.get(
                        "pending_order", {}
                    ).get("lifecycle_state"),
                    "position_reconciled": recovery.get(
                        "position_reconciled", False
                    ),
                    "risk_decision_id": pending.get("risk_decision_id"),
                    "risk_policy_hash": pending.get("risk_policy_hash"),
                    "risk_execution_status": pending.get(
                        "risk_execution_status"
                    ),
                    "new_orders_blocked": True,
                    "recovery": recovery,
                    "run_id": run_id,
                }
            return {
                "status": (
                    "submission_failed"
                    if recovery.get("status") == "submission_failed"
                    else "order_recovered"
                ),
                "ticker": self.config.ticker,
                "instrument_id": self.instrument_id,
                "candle_time": pending.get("candle_time"),
                "action": pending.get("action"),
                "intended_action": pending.get("action"),
                "order_was_sent": True,
                "target_lots": pending.get("target_lots"),
                "current_lots": recovery.get("actual_lots_after"),
                "pending_order": None,
                "position_reconciled": recovery.get(
                    "position_reconciled", False
                ),
                "recovery": recovery,
                "run_id": run_id,
            }

        market_idle_result = self._market_idle_gate(
            root_state=root_state,
            bot_state=bot_state,
            now=now,
            run_id=run_id,
        )
        if market_idle_result is not None:
            return market_idle_result

        with self._measure_phase("candle_load"):
            complete, effective_lookback_days = self._load_strategy_candles(now)
        with self._measure_phase("strategy_evaluation"):
            decisions = evaluate_strategy_suite(
                complete,
                self.strategy_suite,
            )
        primary_decision = decisions[self.primary_strategy]
        decision_payloads = {
            strategy: decision.to_dict()
            for strategy, decision in decisions.items()
        }
        comparison = compare_strategy_decisions(decisions, self.primary_strategy)

        candle_timestamp = pd.Timestamp(primary_decision.candle_time)
        if candle_timestamp.tzinfo is None:
            candle_timestamp = candle_timestamp.tz_localize("UTC")
        else:
            candle_timestamp = candle_timestamp.tz_convert("UTC")
        candle_time = candle_timestamp.isoformat()
        candle_end = (
            candle_timestamp.to_pydatetime()
            + candle_interval_policy(self.config.candle_interval).duration
        )
        data_age_seconds = max(0.0, (now - candle_end).total_seconds())
        signal = primary_decision.signal
        strategy_target_lots = primary_decision.target_lots
        target_lots = strategy_target_lots
        effective_reason = primary_decision.reason
        effective_target_weight = primary_decision.target_weight
        external_close_ack_suppressed = False
        acknowledgement = bot_state.get("external_close_acknowledgement")
        if isinstance(acknowledgement, dict):
            effective_through = str(
                acknowledgement.get("effective_through_candle") or ""
            )
            if effective_through and candle_time <= effective_through:
                external_close_ack_suppressed = True
                signal = 0
                strategy_target_lots = 0
                target_lots = 0
                effective_target_weight = 0.0
                effective_reason = (
                    "FLAT: внешнее закрытие подтверждено оператором; "
                    "старое решение подавлено до новой завершённой свечи."
                )
                primary_payload = dict(decision_payloads[self.primary_strategy])
                primary_payload.update(
                    {
                        "signal": 0,
                        "target_weight": 0.0,
                        "target_lots": 0,
                        "reason": effective_reason,
                        "external_close_ack_suppressed": True,
                        "acknowledgement_id": acknowledgement.get(
                            "acknowledgement_id"
                        ),
                    }
                )
                decision_payloads[self.primary_strategy] = primary_payload
                comparison = dict(comparison)
                signals = dict(comparison.get("signals") or {})
                targets = dict(comparison.get("target_lots") or {})
                signals[self.primary_strategy] = 0
                targets[self.primary_strategy] = 0
                comparison["primary_signal"] = 0
                comparison["primary_target_lots"] = 0
                comparison["signals"] = signals
                comparison["target_lots"] = targets
                comparison["all_agree"] = len(set(signals.values())) <= 1
                comparison["disagreeing_strategies"] = [
                    key for key, value in signals.items() if value != 0
                ]
                comparison["event_type"] = (
                    "AGREEMENT" if comparison["all_agree"] else "DIVERGENCE"
                )
            elif effective_through and candle_time > effective_through:
                released = bot_state.pop("external_close_acknowledgement", None)
                self._journal_safe(
                    JournalEvent(
                        category="portfolio",
                        event_type="EXTERNAL_CLOSE_ACKNOWLEDGEMENT_RELEASED",
                        severity="INFO",
                        session_id=self.session_id,
                        run_id=run_id,
                        account_id=self.account_id,
                        instrument_id=self.instrument_id,
                        ticker=self.config.ticker,
                        candle_time=candle_time,
                        mode=self._mode_name(),
                        payload={
                            "acknowledgement": released,
                            "release_candle": candle_time,
                        },
                    )
                )

        bot_state["last_seen_candle"] = candle_time
        bot_state["last_strategy_decisions"] = decision_payloads
        bot_state["last_strategy_comparison"] = comparison
        self._record_strategy_decisions_safe(
            root_state=root_state,
            bot_state=bot_state,
            decisions=decisions,
            decision_payloads=decision_payloads,
            comparison=comparison,
            run_id=run_id,
        )
        last_consumed = bot_state.get("last_consumed_candle") or bot_state.get(
            "last_candle_time"
        )
        if last_consumed == candle_time:
            last_risk = bot_state.get("last_risk_decision") or {}
            if external_close_ack_suppressed:
                target_lots = 0
            elif self.risk_runtime is not None:
                try:
                    target_lots = int(
                        last_risk.get("approved_target_lots", strategy_target_lots)
                    )
                except (TypeError, ValueError):
                    target_lots = strategy_target_lots
            periodic_reconciliation = self._periodic_reconcile_existing_decision(
                root_state=root_state,
                bot_state=bot_state,
                target_lots=target_lots,
                now=now,
                run_id=run_id,
            )
            self._save_state(root_state)
            mismatch = bool(
                periodic_reconciliation
                and not periodic_reconciliation.get("position_reconciled", False)
            )
            return {
                "status": (
                    "position_mismatch" if mismatch else "already_processed"
                ),
                "ticker": self.config.ticker,
                "instrument_id": self.instrument_id,
                "candle_time": candle_time,
                "last_seen_candle": candle_time,
                "last_consumed_candle": last_consumed,
                "primary_strategy": self.primary_strategy,
                "primary_config_hash": self.primary_config_hash,
                "strategy_suite_hash": self.suite_hash,
                "shadow_strategies": list(self.strategy_suite.shadow_strategies),
                "strategy_decisions": decision_payloads,
                "strategy_comparison": comparison,
                "signal": signal,
                "target_weight": effective_target_weight,
                "strategy_target_lots": strategy_target_lots,
                "target_lots": target_lots,
                "risk_enforced": self.risk_runtime is not None,
                "risk_decision": bot_state.get("last_risk_decision"),
                "risk_status": (bot_state.get("last_risk_decision") or {}).get("status"),
                "risk_policy_hash": (bot_state.get("last_risk_decision") or {}).get("policy_hash"),
                "risk_decision_id": (bot_state.get("last_risk_decision") or {}).get("decision_id"),
                "current_lots": (
                    periodic_reconciliation.get("current_lots")
                    if periodic_reconciliation
                    else bot_state.get("last_confirmed_current_lots")
                ),
                "position_reconciled": (
                    periodic_reconciliation.get("position_reconciled")
                    if periodic_reconciliation
                    else None
                ),
                "execution_block_reason": (
                    periodic_reconciliation.get("execution_block_reason")
                    if periodic_reconciliation
                    else None
                ),
                "periodic_reconciliation": periodic_reconciliation,
                "reason": effective_reason,
                "external_close_ack_suppressed": external_close_ack_suppressed,
                "indicators": primary_decision.indicators,
                "stop_level": primary_decision.stop_level,
                "candles_used": len(complete),
                "effective_lookback_days": effective_lookback_days,
                "data_age_seconds": data_age_seconds,
                "pending_order": None,
                "new_orders_blocked": mismatch,
                "run_id": run_id,
            }

        with self._measure_phase("portfolio_read"):
            portfolio = self.api.get_portfolio(self.account_id)
            current_lots = self.api.position_lots(portfolio, self.instrument)
        bot_state["last_confirmed_current_lots"] = current_lots
        if "last_confirmed_target_lots" not in bot_state:
            # Migration/bootstrap baseline: canonical preflight must compare
            # against the broker-confirmed position, never the fresh proposed
            # signal that was just evaluated in this cycle.  The in-memory
            # runtime projection is passed directly to Portfolio Manager, so
            # this initialization does not add a persistence phase.
            bot_state["last_confirmed_target_lots"] = current_lots
        portfolio_snapshot_at = datetime.now(timezone.utc)
        portfolio_preflight_decision: PortfolioPreflightDecision | None = None
        portfolio_preflight_lease: PortfolioSnapshotLease | None = None
        portfolio_preflight_legacy: None = None
        if self.portfolio_preflight_enabled:
            with self._measure_phase("portfolio_preflight"):
                (
                    portfolio_preflight_decision,
                    portfolio_preflight_lease,
                    portfolio_preflight_legacy,
                ) = self._run_portfolio_preflight(
                    root_state=root_state,
                    bot_state=bot_state,
                    portfolio=portfolio,
                    current_lots=current_lots,
                    proposed_target_lots=strategy_target_lots,
                    run_id=run_id,
                    snapshot_at=portfolio_snapshot_at,
                )
        preflight_blocks_change = bool(
            portfolio_preflight_decision is not None
            and not portfolio_preflight_decision.passed
            and int(strategy_target_lots) != int(current_lots)
        )
        external_activity = bot_state.get("external_activity")
        risk_resync_gate = False
        if isinstance(external_activity, dict):
            # A failed persistence attempt must remain fail-closed. Once the
            # resync flag was persisted, the operator may clear it explicitly
            # with the audited baseline-reset command.
            risk_resync_gate = not bool(
                external_activity.get("risk_resync_persisted")
            )
            if self.risk_runtime is not None:
                try:
                    risk_resync_gate = (
                        risk_resync_gate
                        or self.risk_runtime.risk_resync_required()
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    risk_resync_gate = True
        risk_outcome: RiskRuntimeOutcome | None = None
        risk_block_repeated = False
        risk_block_fingerprint: str | None = None
        risk_block_repeat_count = 0
        if self.risk_runtime is not None and not preflight_blocks_change:
            raw_lot_size = (self.instrument or {}).get("lot", 1)
            try:
                lot_size = max(1, int(raw_lot_size or 1))
            except (TypeError, ValueError):
                lot_size = 1
            with self._measure_phase("risk_evaluation"):
                risk_outcome = self.risk_runtime.evaluate(
                    now=now,
                    strategy_target_lots=strategy_target_lots,
                    current_lots=current_lots,
                    price_rub=primary_decision.indicators.get("close"),
                    lot_size=lot_size,
                    portfolio=(
                        portfolio_risk_mapping(portfolio_preflight_lease.state)
                        if portfolio_preflight_lease is not None
                        else portfolio
                    ),
                    candles=complete,
                    atr_window=self.config.donchian_atr_window,
                    stop_level=primary_decision.stop_level,
                    position_reconciled=(
                        not risk_resync_gate
                        and (
                            portfolio_preflight_decision is None
                            or portfolio_preflight_decision.passed
                        )
                    ),
                    pending_order=bool(
                        portfolio_preflight_decision
                        and portfolio_preflight_decision.context.active_pending_order_ids
                    ),
                    snapshot_at=portfolio_snapshot_at,
                    portfolio_preflight=(
                        portfolio_preflight_decision.context.to_dict()
                        if portfolio_preflight_decision is not None
                        else None
                    ),
                    decision_context="|".join(
                        [
                            self.instrument_id,
                            candle_time,
                            self.primary_strategy,
                            self.primary_config_hash,
                        ]
                    ),
                )
            target_lots = risk_outcome.approved_target_lots
            bot_state["last_risk_decision"] = risk_outcome.to_dict()
            (
                risk_block_repeated,
                risk_block_fingerprint,
                risk_block_repeat_count,
            ) = self._update_risk_block_tracking(
                bot_state=bot_state,
                outcome=risk_outcome,
                candle_time=candle_time,
                strategy_target_lots=strategy_target_lots,
                current_lots=current_lots,
            )
            self._record_risk_outcome_safe(
                outcome=risk_outcome,
                run_id=run_id,
                candle_time=candle_time,
                strategy_target_lots=strategy_target_lots,
                current_lots=current_lots,
                suppress_evaluated=risk_block_repeated,
            )
        else:
            target_lots = (
                current_lots if preflight_blocks_change else strategy_target_lots
            )
            bot_state.pop("last_risk_block", None)

        foreign_pending = self._find_foreign_pending_order(root_state)
        strategy_guard_reason: str | None = None
        if self.allow_execution and foreign_pending:
            strategy_guard_reason = (
                "Another strategy configuration in the same account/instrument "
                "scope owns a pending order. Resolve it first."
            )
            self._record_incident_safe(
                event_type="FOREIGN_PENDING_ORDER",
                severity="ERROR",
                run_id=run_id,
                bot_state=bot_state,
                payload={
                    "reason": strategy_guard_reason,
                    "foreign_pending_order": foreign_pending,
                },
            )
        elif self.allow_execution:
            strategy_guard_reason = self._ensure_primary_assignment(
                root_state,
                current_lots=current_lots,
                run_id=run_id,
            )

        delta_lots = target_lots - current_lots
        action = "HOLD"
        lots = 0
        if delta_lots > 0:
            action = "BUY"
            lots = delta_lots
        elif delta_lots < 0:
            action = "SELL"
            lots = abs(delta_lots)

        display_action = action
        if preflight_blocks_change:
            display_action = "PORTFOLIO_BLOCKED"
        elif (
            risk_outcome is not None
            and risk_outcome.status in {"BLOCKED", "RUNTIME_ERROR"}
            and strategy_target_lots != current_lots
        ):
            display_action = "RISK_BLOCKED"
        elif not self.allow_execution and action in {"BUY", "SELL"}:
            display_action = f"WOULD_{action}"

        decision_payload = {
            "primary_strategy": self.primary_strategy,
            "strategy_version": primary_decision.strategy_version,
            "config_hash": primary_decision.config_hash,
            "strategy_suite_hash": self.suite_hash,
            "signal": signal,
            "target_weight": effective_target_weight,
            "strategy_target_lots": strategy_target_lots,
            "target_lots": target_lots,
            "external_close_ack_suppressed": external_close_ack_suppressed,
            "current_lots": current_lots,
            "risk_enforced": risk_outcome is not None,
            "risk_status": risk_outcome.status if risk_outcome else "NOT_ENFORCED",
            "risk_policy_hash": risk_outcome.policy_hash if risk_outcome else None,
            "risk_decision_id": risk_outcome.decision_id if risk_outcome else None,
            "risk_decision": risk_outcome.to_dict() if risk_outcome else None,
            "portfolio_preflight": (
                portfolio_preflight_decision.to_dict()
                if portfolio_preflight_decision is not None
                else None
            ),
            "portfolio_revision": (
                portfolio_preflight_lease.revision
                if portfolio_preflight_lease is not None
                else None
            ),
            "action": display_action,
            "intended_action": action,
            "order_was_sent": False,
            "lots": lots,
            "reason": effective_reason,
            "indicators": primary_decision.indicators,
            "stop_level": primary_decision.stop_level,
            "data_age_seconds": data_age_seconds,
            "shadow_decisions": {
                key: value.to_dict()
                for key, value in decisions.items()
                if key != self.primary_strategy
            },
        }
        if not risk_block_repeated:
            self._record_trade_decision_safe(
                run_id=run_id,
                candle_time=candle_time,
                payload=decision_payload,
            )

        trading_status: dict[str, Any] | None = None
        execution_block_reason: str | None = strategy_guard_reason
        block_retryable = strategy_guard_reason is not None
        max_age = self._max_signal_age_seconds()
        if (
            self.allow_execution
            and action in {"BUY", "SELL"}
            and data_age_seconds > max_age
        ):
            execution_block_reason = (
                "Decision is stale: data age exceeds the configured maximum."
            )

        if (
            action in {"BUY", "SELL"}
            and execution_block_reason is None
            and self.config.check_trading_status
        ):
            with self._measure_phase("trading_status"):
                trading_status = self.api.get_trading_status(self.instrument_id)
            if self.config.order_type.upper() == "MARKET":
                available = self.api.market_order_available(trading_status)
            else:
                available = self.api.best_price_available(trading_status)
            if not available:
                execution_block_reason = (
                    "The instrument is not currently available for the selected "
                    "order type."
                )
                block_retryable = True

        broker_max_buy_lots: int | None = None
        if (
            action == "BUY"
            and execution_block_reason is None
            and lots > 0
        ):
            with self._measure_phase("max_lots"):
                max_lots_response = self.api.get_max_lots(
                    self.account_id,
                    self.instrument_id,
                )
            broker_max_buy_lots = self.api.max_buy_lots(max_lots_response)
            if broker_max_buy_lots is not None:
                lots = min(lots, broker_max_buy_lots)
                if lots <= 0:
                    execution_block_reason = "Broker reports zero buy capacity."
                    block_retryable = True

        base_result: dict[str, Any] = {
            "ticker": self.config.ticker,
            "instrument_id": self.instrument_id,
            "candle_time": candle_time,
            "last_seen_candle": candle_time,
            "primary_strategy": self.primary_strategy,
            "primary_strategy_version": primary_decision.strategy_version,
            "primary_config_hash": self.primary_config_hash,
            "strategy_suite_hash": self.suite_hash,
            "shadow_strategies": list(self.strategy_suite.shadow_strategies),
            "strategy_decisions": decision_payloads,
            "strategy_comparison": comparison,
            "close": primary_decision.indicators.get("close"),
            "signal": signal,
            "target_weight": effective_target_weight,
            "reason": effective_reason,
            "indicators": primary_decision.indicators,
            "stop_level": primary_decision.stop_level,
            "candles_used": len(complete),
            "effective_lookback_days": effective_lookback_days,
            "data_age_seconds": data_age_seconds,
            "max_signal_age_seconds": max_age,
            "current_lots": current_lots,
            "strategy_target_lots": strategy_target_lots,
            "target_lots": target_lots,
            "risk_enforced": risk_outcome is not None,
            "risk_status": risk_outcome.status if risk_outcome else "NOT_ENFORCED",
            "risk_policy_hash": risk_outcome.policy_hash if risk_outcome else None,
            "risk_decision_id": risk_outcome.decision_id if risk_outcome else None,
            "risk_decision": risk_outcome.to_dict() if risk_outcome else None,
            "portfolio_preflight": (
                portfolio_preflight_decision.to_dict()
                if portfolio_preflight_decision is not None
                else None
            ),
            "portfolio_revision": (
                portfolio_preflight_lease.revision
                if portfolio_preflight_lease is not None
                else None
            ),
            "portfolio_decision_checksum": (
                portfolio_preflight_lease.decision_checksum
                if portfolio_preflight_lease is not None
                else None
            ),
            "risk_breaches": (
                list(risk_outcome.assessment.decision.breaches)
                if risk_outcome and risk_outcome.assessment
                else []
            ),
            "risk_reasons": (
                list(risk_outcome.assessment.decision.reasons)
                if risk_outcome and risk_outcome.assessment
                else ([risk_outcome.error] if risk_outcome and risk_outcome.error else [])
            ),
            "risk_block_repeated": risk_block_repeated,
            "risk_block_fingerprint": risk_block_fingerprint,
            "risk_block_repeat_count": risk_block_repeat_count,
            "action": display_action,
            "intended_action": action,
            "order_was_sent": False,
            "lots": lots,
            "dry_run": self.config.dry_run,
            "allow_execution": self.allow_execution,
            "execution_block_reason": execution_block_reason,
            "foreign_pending_order": foreign_pending,
            "broker_max_buy_lots": broker_max_buy_lots,
            "trading_status": trading_status,
            "pending_order": None,
            "new_orders_blocked": bool(
                portfolio_preflight_decision is not None
                and not portfolio_preflight_decision.passed
            ),
            "run_id": run_id,
        }

        if (
            preflight_blocks_change
            and execution_block_reason is None
            and not (
                risk_outcome is not None
                and risk_outcome.status in {"BLOCKED", "RUNTIME_ERROR"}
            )
        ):
            self._save_state(root_state)
            return {
                **base_result,
                "status": "portfolio_preflight_blocked",
                "accepted": False,
                "executed": False,
                "executed_lots": 0,
                "position_reconciled": False,
                "new_orders_blocked": True,
                "retryable_block": True,
                "execution_block_reason": "; ".join(
                    portfolio_preflight_decision.reasons
                    if portfolio_preflight_decision is not None
                    else ("Canonical portfolio preflight failed.",)
                ),
            }

        if (
            risk_outcome is not None
            and risk_outcome.status in {"BLOCKED", "RUNTIME_ERROR"}
            and strategy_target_lots != current_lots
        ):
            self._save_state(root_state)
            return {
                **base_result,
                "status": (
                    "risk_blocked_heartbeat"
                    if risk_block_repeated
                    else "risk_blocked"
                ),
                "accepted": False,
                "executed": False,
                "executed_lots": 0,
                "position_reconciled": True,
                "new_orders_blocked": True,
                "retryable_block": True,
                "suppress_cycle_event": risk_block_repeated,
            }

        if execution_block_reason is not None:
            if not block_retryable:
                # A permanently consumed decision did not change the broker
                # position. Persist that position as the reconciliation
                # baseline for future heartbeat checks.
                bot_state["last_confirmed_target_lots"] = current_lots
                self._consume_candle(
                    bot_state,
                    candle_time,
                    dry_run=self.config.dry_run,
                    order_id=None,
                    reason=execution_block_reason,
                )
            self._save_state(root_state)
            lowered_reason = execution_block_reason.lower()
            return {
                **base_result,
                "status": (
                    "stale_decision"
                    if "stale" in lowered_reason
                    else (
                        "strategy_switch_blocked"
                        if "strategy configuration" in lowered_reason
                        or "primary" in lowered_reason
                        else "execution_blocked"
                    )
                ),
                "accepted": False,
                "executed": False,
                "executed_lots": 0,
                "position_reconciled": True,
                "retryable_block": block_retryable,
            }

        if action == "HOLD":
            bot_state["last_confirmed_target_lots"] = current_lots
            self._consume_candle(
                bot_state,
                candle_time,
                dry_run=self.config.dry_run,
                order_id=None,
                reason="HOLD",
            )
            self._save_state(root_state)
            return {
                **base_result,
                "status": "processed",
                "accepted": False,
                "executed": False,
                "executed_lots": 0,
                "position_reconciled": True,
            }

        if not self.allow_execution:
            # Dry-run deliberately leaves the broker position unchanged. The
            # expected *broker* position is therefore the observed current
            # quantity, not the model target shown as WOULD_BUY/WOULD_SELL.
            bot_state["last_confirmed_target_lots"] = current_lots
            self._consume_candle(
                bot_state,
                candle_time,
                dry_run=True,
                order_id=None,
                reason="DRY_RUN",
            )
            self._save_state(root_state)
            return {
                **base_result,
                "status": "processed",
                "accepted": False,
                "executed": False,
                "executed_lots": 0,
                "position_reconciled": True,
            }

        order_id = self._deterministic_order_id(
            candle_time,
            action,
            target_lots,
            risk_decision_id=(risk_outcome.decision_id if risk_outcome else None),
        )
        intent: dict[str, Any] = {
            "order_id": order_id,
            "run_id": run_id,
            "candle_time": candle_time,
            "action": action,
            "lots": lots,
            "target_lots": target_lots,
            "current_lots_before": current_lots,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "order_type": self.config.order_type,
            "time_in_force": self.config.time_in_force,
            "strategy_id": self.primary_strategy,
            "strategy_version": primary_decision.strategy_version,
            "strategy_config_hash": self.primary_config_hash,
            "strategy_suite_hash": self.suite_hash,
            "strategy_reason": primary_decision.reason,
            "target_weight": primary_decision.target_weight,
            "stop_level": primary_decision.stop_level,
            "decision_price_rub": primary_decision.indicators.get("close"),
            "lot_size": max(1, int((self.instrument or {}).get("lot", 1) or 1)),
            "risk_enforced": risk_outcome is not None,
            "risk_mode": risk_outcome.mode if risk_outcome else None,
            "risk_status": risk_outcome.status if risk_outcome else "NOT_ENFORCED",
            "risk_decision_id": risk_outcome.decision_id if risk_outcome else None,
            "risk_policy_hash": risk_outcome.policy_hash if risk_outcome else None,
            "risk_requested_target_lots": strategy_target_lots,
            "risk_approved_target_lots": target_lots,
            "risk_breaches": (
                list(risk_outcome.assessment.decision.breaches)
                if risk_outcome and risk_outcome.assessment
                else []
            ),
            "risk_reasons": (
                list(risk_outcome.assessment.decision.reasons)
                if risk_outcome and risk_outcome.assessment
                else []
            ),
            "risk_execution_status": (
                "PENDING" if risk_outcome is not None else "NOT_ENFORCED"
            ),
            "portfolio_preflight_enabled": self.portfolio_preflight_enabled,
            "portfolio_snapshot_revision": (
                portfolio_preflight_lease.revision
                if portfolio_preflight_lease is not None
                else None
            ),
            "portfolio_snapshot_decision_checksum": (
                portfolio_preflight_lease.decision_checksum
                if portfolio_preflight_lease is not None
                else None
            ),
            "portfolio_snapshot_document_checksum": (
                portfolio_preflight_lease.document_checksum
                if portfolio_preflight_lease is not None
                else None
            ),
            "portfolio_preflight_context": (
                portfolio_preflight_decision.context.to_dict()
                if portfolio_preflight_decision is not None
                else None
            ),
            "portfolio_legacy_view": (
                portfolio_preflight_legacy.to_dict()
                if portfolio_preflight_legacy is not None
                else None
            ),
        }
        transition_intent(intent, OrderLifecycle.PRECHECK_PASSED)
        transition_intent(intent, OrderLifecycle.INTENT_SAVED)
        bot_state["pending_order"] = intent
        self._save_state(root_state)
        self._record_order_transition_safe(intent, run_id)
        self._crash_checkpoint(OrderLifecycle.INTENT_SAVED, intent=intent)

        if portfolio_preflight_lease is not None:
            revision_check = self._recheck_portfolio_revision(
                portfolio_preflight_lease,
                run_id=run_id,
            )
            intent["portfolio_revision_check"] = revision_check.to_dict()
            if not revision_check.unchanged:
                transition_intent(
                    intent,
                    OrderLifecycle.SUBMISSION_FAILED,
                    details={
                        "accepted": False,
                        "submission_attempted": False,
                        "failure_stage": "PRE_POST_PORTFOLIO_REVISION_RECHECK",
                        "portfolio_revision_changed": True,
                        "portfolio_revision_check": revision_check.to_dict(),
                    },
                )
                bot_state.pop("pending_order", None)
                bot_state["last_portfolio_revision_block"] = revision_check.to_dict()
                self._save_state(root_state)
                self._record_order_transition_safe(intent, run_id)
                return {
                    **base_result,
                    "action": action,
                    "status": "portfolio_revision_changed",
                    "order_id": order_id,
                    "order_was_sent": False,
                    "order_may_have_been_sent": False,
                    "accepted": False,
                    "executed": False,
                    "executed_lots": 0,
                    "position_reconciled": False,
                    "new_orders_blocked": True,
                    "retryable_block": True,
                    "execution_block_reason": revision_check.reason,
                    "portfolio_revision_check": revision_check.to_dict(),
                    "pending_order": None,
                }

        transition_intent(
            intent,
            OrderLifecycle.ORDER_SUBMITTED,
            details={"submission_attempted": True},
        )
        bot_state["pending_order"] = intent
        self._save_state(root_state)
        self._record_order_transition_safe(intent, run_id)
        self._crash_checkpoint(OrderLifecycle.ORDER_SUBMITTED, intent=intent)

        try:
            self._order_submission_attempted_in_cycle = True
            with self._measure_phase("order_submission"):
                order_response = self.api.post_order(
                    self.account_id,
                    self.instrument_id,
                    lots,
                    action,
                    order_id=order_id,
                    order_type=self.config.order_type,
                    time_in_force=self.config.time_in_force,
                )
        except TBankAPIError as exc:
            # A transient transport failure is ambiguous: the provider may
            # have accepted the order even though the response was lost. Keep
            # the persisted intent and recover it by the same deterministic
            # orderId on the next cycle. A concrete non-transient HTTP error,
            # however, proves that this submission was rejected and must not
            # be automatically repeated forever.
            if exc.transient or exc.status_code is None or exc.status_code < 400:
                raise
            transition_intent(
                intent,
                OrderLifecycle.SUBMISSION_FAILED,
                details={
                    "accepted": False,
                    "submission_error": str(exc),
                    "status_code": exc.status_code,
                    "tracking_id": exc.tracking_id,
                },
            )
            self._record_order_transition_safe(
                intent,
                run_id,
                payload={"api_error": exc.details},
            )
            bot_state.pop("pending_order", None)
            self._consume_candle(
                bot_state,
                candle_time,
                dry_run=False,
                order_id=order_id,
                reason="SUBMISSION_FAILED",
            )
            self._save_state(root_state)
            return {
                **base_result,
                "action": action,
                "order_was_sent": True,
                "status": "submission_failed",
                "order_id": order_id,
                "order_status": "REJECTED",
                "order_lifecycle_state": intent.get("lifecycle_state"),
                "accepted": False,
                "executed": False,
                "executed_lots": 0,
                "position_reconciled": True,
                "pending_order": None,
                "error": str(exc),
                "api_status_code": exc.status_code,
                "tracking_id": exc.tracking_id,
            }
        transition_intent(
            intent,
            OrderLifecycle.ORDER_ACCEPTED,
            details={"accepted": True},
        )
        bot_state["pending_order"] = intent
        self._save_state(root_state)
        self._record_order_transition_safe(
            intent,
            run_id,
            payload={"order_response": order_response},
        )
        self._crash_checkpoint(OrderLifecycle.ORDER_ACCEPTED, intent=intent)

        with self._measure_phase("order_state_read"):
            order_state = self.api.get_order_state(
                self.account_id,
                order_id,
                by_request_id=True,
            )
        final_payload = order_state or order_response
        order_status = normalize_execution_status(final_payload)
        filled_lots = executed_lots(final_payload)
        lifecycle = lifecycle_for_status(order_status)
        transition_intent(
            intent,
            lifecycle,
            details={
                "last_known_status": order_status,
                "executed_lots": filled_lots,
            },
        )
        bot_state["pending_order"] = intent
        self._save_state(root_state)
        self._record_order_transition_safe(
            intent,
            run_id,
            payload={"order_state": order_state},
        )
        self._crash_checkpoint(lifecycle, intent=intent)

        position_reconciled = False
        actual_lots_after: int | None = None
        expected_lots_after = current_lots + signed_lot_delta(
            action,
            filled_lots,
        )
        result_status = "order_pending"

        if is_terminal_order_status(order_status, self.config.time_in_force):
            with self._measure_phase("position_reconciliation"):
                reconciliation = self._reconcile_position(
                    expected_lots=expected_lots_after,
                )
            position_reconciled = reconciliation["position_reconciled"]
            actual_lots_after = reconciliation["actual_lots_after"]
            if position_reconciled:
                canonical_result = self._canonical_post_fill_reconcile(
                    root_state=root_state,
                    bot_state=bot_state,
                    intent=intent,
                    reconciliation=reconciliation,
                    expected_lots=expected_lots_after,
                    run_id=run_id,
                )
                canonical_ok = (
                    canonical_result is None or canonical_result.success
                )
                if not canonical_ok:
                    position_reconciled = False
                    transition_intent(
                        intent,
                        OrderLifecycle.RECONCILIATION_REQUIRED,
                        details={
                            "expected_lots_after": expected_lots_after,
                            "actual_lots_after": actual_lots_after,
                            "canonical_reconciliation_required": True,
                            "post_fill_canonical": canonical_result.to_dict(),
                        },
                    )
                    bot_state["pending_order"] = intent
                    self._save_state(root_state)
                    self._record_order_transition_safe(intent, run_id)
                    self._record_incident_safe(
                        event_type="POST_FILL_CANONICAL_RECONCILIATION_FAILED",
                        severity="ERROR",
                        run_id=run_id,
                        bot_state=bot_state,
                        payload={
                            "order_id": order_id,
                            "expected_lots_after": expected_lots_after,
                            "actual_lots_after": actual_lots_after,
                            "post_fill_canonical": canonical_result.to_dict(),
                        },
                    )
                    result_status = "canonical_reconciliation_pending"
                else:
                    if canonical_result is not None:
                        proof = dict(
                            reconciliation.get("reconciliation_proof") or {}
                        )
                        proof.update(
                            {
                                "canonical_reconciled": True,
                                "canonical_revision": canonical_result.revision,
                                "canonical_decision_checksum": (
                                    canonical_result.decision_checksum
                                ),
                                "canonical_snapshot_at": canonical_result.snapshot_at,
                            }
                        )
                        reconciliation["reconciliation_proof"] = proof
                        intent["post_fill_canonical"] = canonical_result.to_dict()
                    transition_intent(
                        intent,
                        OrderLifecycle.PORTFOLIO_RECONCILED,
                        details={
                            "expected_lots_after": expected_lots_after,
                            "actual_lots_after": actual_lots_after,
                            "reconcile_attempts_used": reconciliation.get(
                                "reconcile_attempts_used"
                            ),
                            "reconciliation_confirmed_at": reconciliation.get(
                                "reconciliation_confirmed_at"
                            ),
                            "reconciliation_proof": reconciliation.get(
                                "reconciliation_proof"
                            ),
                        },
                    )
                    bot_state["pending_order"] = intent
                    self._save_state(root_state)
                    self._record_order_transition_safe(
                        intent,
                        run_id,
                        payload={
                            "reconciliation_proof": reconciliation.get(
                                "reconciliation_proof"
                            )
                        },
                    )
                    self._crash_checkpoint(
                        OrderLifecycle.PORTFOLIO_RECONCILED,
                        intent=intent,
                    )

                    risk_execution = self._register_risk_execution(
                        intent=intent,
                        final_payload=final_payload,
                        fallback_payload=order_response,
                        reconciliation=reconciliation,
                        run_id=run_id,
                    )
                    if risk_execution is not None and risk_execution.status in {
                        "RECORDED",
                        "DUPLICATE",
                    }:
                        self._crash_checkpoint("EXECUTION_RECORDED", intent=intent)
                    if (
                        risk_execution is not None
                        and risk_execution.status == "RUNTIME_ERROR"
                    ):
                        transition_intent(
                            intent,
                            OrderLifecycle.RISK_ACCOUNTING_REQUIRED,
                            details={
                                "expected_lots_after": expected_lots_after,
                                "actual_lots_after": actual_lots_after,
                                "reconciliation_confirmed_at": reconciliation.get(
                                    "reconciliation_confirmed_at"
                                ),
                                "risk_execution_error": risk_execution.error,
                            },
                        )
                        bot_state["pending_order"] = intent
                        self._save_state(root_state)
                        self._record_order_transition_safe(intent, run_id)
                        self._record_incident_safe(
                            event_type="RISK_EXECUTION_ACCOUNTING_FAILED",
                            severity="ERROR",
                            run_id=run_id,
                            bot_state=bot_state,
                            payload={
                                "order_id": order_id,
                                "risk_decision_id": intent.get("risk_decision_id"),
                                "risk_policy_hash": intent.get("risk_policy_hash"),
                                "reconciliation_confirmed_at": reconciliation.get(
                                    "reconciliation_confirmed_at"
                                ),
                                "error": risk_execution.error,
                            },
                        )
                        result_status = "risk_accounting_pending"
                    else:
                        if risk_execution is not None:
                            transition_intent(
                                intent,
                                OrderLifecycle.RISK_ACCOUNTED,
                                details={
                                    "risk_execution_status": risk_execution.status,
                                    "risk_execution_duplicate": (
                                        risk_execution.duplicate
                                    ),
                                    "reconciliation_confirmed_at": (
                                        risk_execution.reconciliation_confirmed_at
                                    ),
                                },
                            )
                            bot_state["pending_order"] = intent
                            self._save_state(root_state)
                            self._record_order_transition_safe(intent, run_id)
                            self._crash_checkpoint(
                                OrderLifecycle.RISK_ACCOUNTED,
                                intent=intent,
                            )
                        bot_state.pop("pending_order", None)
                        bot_state["last_confirmed_current_lots"] = actual_lots_after
                        bot_state["last_confirmed_target_lots"] = actual_lots_after
                        self._consume_candle(
                            bot_state,
                            candle_time,
                            dry_run=False,
                            order_id=order_id,
                            reason=order_status or "TERMINAL",
                        )
                        result_status = "processed"
            else:
                transition_intent(
                    intent,
                    OrderLifecycle.RECONCILIATION_REQUIRED,
                    details={
                        "expected_lots_after": expected_lots_after,
                        "actual_lots_after": actual_lots_after,
                    },
                )
                bot_state["pending_order"] = intent
                self._record_order_transition_safe(intent, run_id)
                self._record_incident_safe(
                    event_type="POSITION_RECONCILIATION_MISMATCH",
                    severity="ERROR",
                    run_id=run_id,
                    bot_state=bot_state,
                    payload={
                        "order_id": order_id,
                        "expected_lots_after": expected_lots_after,
                        "actual_lots_after": actual_lots_after,
                    },
                )
        else:
            bot_state["pending_order"] = intent

        self._save_state(root_state)
        logger.warning(
            "SANDBOX ORDER: %s %s lot(s) %s order_id=%s status=%s "
            "executed_lots=%s reconciled=%s",
            action,
            lots,
            self.config.ticker,
            order_id,
            order_status,
            filled_lots,
            position_reconciled,
        )
        return {
            **base_result,
            "action": action,
            "order_was_sent": True,
            "status": result_status,
            "order_id": order_id,
            "order_status": order_status,
            "order_lifecycle_state": intent.get("lifecycle_state"),
            "accepted": True,
            "executed": filled_lots > 0 or order_status == "FILL",
            "executed_lots": filled_lots,
            "expected_lots_after": expected_lots_after,
            "actual_lots_after": actual_lots_after,
            "position_reconciled": position_reconciled,
            "pending_order": bot_state.get("pending_order"),
            "risk_execution": intent.get("risk_execution"),
            "risk_execution_status": intent.get("risk_execution_status"),
            "post_fill_canonical": intent.get("post_fill_canonical"),
            "new_orders_blocked": result_status in {
                "risk_accounting_pending",
                "canonical_reconciliation_pending",
            },
            "order_response": order_response,
            "order_state": order_state,
        }

    def _ensure_instrument(self, bot_state: dict[str, Any]) -> None:
        if self.instrument is None:
            self.instrument = self.api.find_instrument(
                self.config.ticker,
                self.config.class_code,
            )
            self.instrument_id = self.api.instrument_id(self.instrument)
            bot_state["instrument_id"] = self.instrument_id
        elif self.instrument_id is None:
            self.instrument_id = self.api.instrument_id(self.instrument)

    def _load_strategy_candles(
        self,
        now: datetime,
    ) -> tuple[pd.DataFrame, int]:
        assert self.instrument_id is not None
        interval = self.config.candle_interval
        policy = candle_interval_policy(interval)

        required_bars = self.strategy_suite.required_bars_for_suite()
        max_days = policy.maximum_lookback_days
        lookback_days = strategy_lookback_days(
            interval,
            required_bars=required_bars,
            requested_days=self.config.lookback_days,
        )

        notice = (interval, self.config.lookback_days, lookback_days, required_bars)
        if notice != self._last_lookback_notice:
            self._last_lookback_notice = notice
            if self.config.lookback_days > max_days:
                logger.warning(
                    "Candle lookback capped: requested=%s days interval=%s used=%s days",
                    self.config.lookback_days,
                    interval,
                    max_days,
                )
            elif lookback_days > self.config.lookback_days:
                logger.info(
                    "Candle warm-up: requested=%s days used=%s days required_bars=%s",
                    self.config.lookback_days,
                    lookback_days,
                    required_bars,
                )
        else:
            logger.debug(
                "Candle warm-up unchanged: interval=%s used=%s required_bars=%s",
                interval, lookback_days, required_bars,
            )

        while True:
            candles = self.api.get_candles(
                self.instrument_id,
                now - timedelta(days=lookback_days),
                now,
                interval=interval,
                limit=None,
            )
            if "is_complete" not in candles.columns:
                raise TBankAPIError(
                    "T-Invest candle response has no isComplete field."
                )
            complete = candles[candles["is_complete"]].copy()
            if len(complete) >= required_bars:
                return complete, lookback_days

            if lookback_days >= max_days:
                raise TBankAPIError(
                    "Недостаточно завершённых свечей для расчёта стратегии: "
                    f"нужно не менее {required_bars}, получено {len(complete)}. "
                    f"Интервал: {interval}, доступный период: {lookback_days} дней. "
                    "Уменьшите окна PRIMARY/SHADOW-стратегий, выберите более "
                    "короткий интервал или проверьте историю инструмента."
                )

            expanded_lookback = ceil(lookback_days * 1.8)
            next_lookback = min(
                max_days,
                max(lookback_days + 1, expanded_lookback),
            )
            logger.warning(
                "Only %s completed candles were returned; expanding lookback "
                "from %s to %s days.",
                len(complete),
                lookback_days,
                next_lookback,
            )
            lookback_days = next_lookback

    def _recover_pending_order(
        self,
        root_state: dict[str, Any],
        bot_state: dict[str, Any],
        pending: dict[str, Any],
        *,
        allow_resubmit: bool,
        run_id: str,
        recovery_decision: RecoveryDecision | None = None,
    ) -> dict[str, Any]:
        assert self.instrument is not None
        assert self.instrument_id is not None
        order_id = str(pending.get("order_id", ""))
        if not order_id:
            raise RuntimeError(
                "Pending order has no order_id; manual state inspection required."
            )

        lifecycle_state = str(pending.get("lifecycle_state") or "")
        recovery_action = (
            recovery_decision.action
            if recovery_decision is not None
            else RecoveryAction.LOOKUP_BROKER_ORDER
        )
        pending["recovery_id"] = (
            recovery_decision.recovery_id
            if recovery_decision is not None
            else pending.get("recovery_id")
        )
        pending["recovery_action"] = str(recovery_action)
        if recovery_action == RecoveryAction.RISK_ACCOUNT_ONLY:
            proof = dict(pending.get("reconciliation_proof") or {})
            reconciliation = {
                "position_reconciled": bool(proof.get("position_reconciled")),
                "expected_lots_after": proof.get(
                    "expected_lots_after", pending.get("expected_lots_after")
                ),
                "actual_lots_after": proof.get(
                    "actual_lots_after", pending.get("actual_lots_after")
                ),
                "reconcile_attempts_used": pending.get("reconcile_attempts_used"),
                "reconciliation_confirmed_at": pending.get(
                    "reconciliation_confirmed_at"
                ) or proof.get("confirmed_at"),
                "reconciliation_proof": proof,
                "portfolio_equity_rub": pending.get("portfolio_equity_rub"),
                "cash_rub": pending.get("cash_rub"),
                "securities_value_rub": pending.get("securities_value_rub"),
            }
            if (
                not reconciliation["position_reconciled"]
                or reconciliation["expected_lots_after"]
                != reconciliation["actual_lots_after"]
            ):
                pending["recovery_status"] = "RECONCILIATION_PROOF_INVALID"
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                return {
                    "status": "recovery_blocked",
                    "reason": "Persisted reconciliation proof is missing or inconsistent.",
                    **reconciliation,
                }
            state = {
                "executionReportStatus": (
                    "EXECUTION_REPORT_STATUS_"
                    + str(pending.get("last_known_status") or "FILL")
                ),
                "lotsExecuted": str(int(pending.get("executed_lots", 0) or 0)),
                "executedOrderPrice": pending.get("confirmed_execution_price_rub"),
                "recoveredFromConfirmedIntent": True,
            }
            canonical_record = pending.get("post_fill_canonical")
            canonical_already_confirmed = bool(
                isinstance(canonical_record, dict)
                and canonical_record.get("success") is True
            )
            if self.portfolio_preflight_enabled and not canonical_already_confirmed:
                canonical_result = self._canonical_post_fill_reconcile(
                    root_state=root_state,
                    bot_state=bot_state,
                    intent=pending,
                    reconciliation=reconciliation,
                    expected_lots=int(reconciliation["actual_lots_after"] or 0),
                    run_id=run_id,
                    recovery=True,
                )
                if canonical_result is not None and not canonical_result.success:
                    pending["recovery_status"] = "CANONICAL_RECONCILIATION_REQUIRED"
                    pending["canonical_reconciliation_required"] = True
                    bot_state["pending_order"] = pending
                    self._save_state(root_state)
                    return {
                        "status": "canonical_reconciliation_pending",
                        "reason": canonical_result.reason,
                        "post_fill_canonical": canonical_result.to_dict(),
                        **reconciliation,
                    }
                if canonical_result is not None:
                    proof = dict(reconciliation.get("reconciliation_proof") or {})
                    proof.update(
                        {
                            "canonical_reconciled": True,
                            "canonical_revision": canonical_result.revision,
                            "canonical_decision_checksum": (
                                canonical_result.decision_checksum
                            ),
                            "canonical_snapshot_at": canonical_result.snapshot_at,
                        }
                    )
                    reconciliation["reconciliation_proof"] = proof
            risk_execution = self._register_risk_execution(
                intent=pending,
                final_payload=state,
                fallback_payload=None,
                reconciliation=reconciliation,
                run_id=run_id,
            )
            if risk_execution is not None and risk_execution.status == "RUNTIME_ERROR":
                pending["recovery_status"] = "RISK_ACCOUNTING_REQUIRED"
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                return {
                    "status": "risk_accounting_pending",
                    "risk_execution": risk_execution.to_dict(),
                    **reconciliation,
                }
            if risk_execution is not None:
                transition_intent(
                    pending,
                    OrderLifecycle.RISK_ACCOUNTED,
                    details={
                        "risk_execution_status": risk_execution.status,
                        "risk_execution_duplicate": risk_execution.duplicate,
                        "reconciliation_confirmed_at": (
                            risk_execution.reconciliation_confirmed_at
                        ),
                    },
                )
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                self._record_order_transition_safe(pending, run_id)
                self._crash_checkpoint(OrderLifecycle.RISK_ACCOUNTED, intent=pending)
            bot_state.pop("pending_order", None)
            actual = int(reconciliation["actual_lots_after"] or 0)
            bot_state["last_confirmed_current_lots"] = actual
            bot_state["last_confirmed_target_lots"] = actual
            self._consume_candle(
                bot_state,
                str(pending.get("candle_time", "")),
                dry_run=False,
                order_id=order_id,
                reason="RECOVERED_RISK_ACCOUNTING",
            )
            self._save_state(root_state)
            return {
                "status": "risk_accounted",
                "risk_execution": (
                    risk_execution.to_dict() if risk_execution is not None else None
                ),
                **reconciliation,
            }

        execution_already_confirmed = recovery_action == RecoveryAction.RECONCILE_ONLY
        if execution_already_confirmed:
            filled = int(pending.get("executed_lots", 0) or 0)
            if filled <= 0:
                pending["recovery_status"] = "EXECUTION_PROOF_INVALID"
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                return {
                    "status": "recovery_blocked",
                    "reason": "A FILLED/reconciliation recovery has no executed_lots.",
                    "position_reconciled": False,
                }
            state = {
                "executionReportStatus": (
                    "EXECUTION_REPORT_STATUS_"
                    + str(pending.get("last_known_status") or "FILL")
                ),
                "lotsExecuted": str(filled),
                "executedOrderPrice": pending.get("confirmed_execution_price_rub"),
                "recoveredFromConfirmedIntent": True,
            }
        else:
            try:
                state = self.api.get_order_state(
                    self.account_id,
                    order_id,
                    by_request_id=True,
                )
            except TBankAPIError as exc:
                if exc.status_code != 404:
                    raise
                transition_intent(
                    pending,
                    OrderLifecycle.UNKNOWN_SUBMIT_STATE,
                    details={"recovery_status": "UNKNOWN_SUBMIT_STATE"},
                )
                pending["recovery_error"] = (
                    "Broker lookup by orderRequestId returned 404. "
                    "Automatic POST replay is forbidden in v3.9.0."
                )
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                self._record_incident_safe(
                    event_type="UNKNOWN_SUBMIT_STATE",
                    severity="ERROR",
                    run_id=run_id,
                    bot_state=bot_state,
                    payload={
                        "order_id": order_id,
                        "lifecycle_state": lifecycle_state,
                        "recovery_id": pending.get("recovery_id"),
                        "automatic_resubmit": False,
                    },
                )
                return {
                    "status": "unknown_submit_state",
                    "resubmitted": False,
                    "reason": pending["recovery_error"],
                    "position_reconciled": False,
                }

        status = normalize_execution_status(state)
        filled_lots = executed_lots(state)
        if not execution_already_confirmed:
            transition_intent(
                pending,
                lifecycle_for_status(status),
                details={
                    "last_known_status": status,
                    "executed_lots": filled_lots,
                },
            )
            bot_state["pending_order"] = pending
            self._save_state(root_state)
            self._record_order_transition_safe(
                pending,
                run_id,
                payload={"order_state": state, "recovery": True},
            )
        else:
            pending["last_known_status"] = status
            pending["executed_lots"] = filled_lots
            bot_state["pending_order"] = pending

        result: dict[str, Any] = {
            "status": status,
            "executed_lots": filled_lots,
            "position_reconciled": False,
            "order_state": state,
        }
        if not is_terminal_order_status(
            status,
            str(pending.get("time_in_force", self.config.time_in_force)),
        ):
            return result

        expected = int(pending.get("current_lots_before", 0)) + signed_lot_delta(
            str(pending["action"]),
            filled_lots,
        )
        reconciliation = self._reconcile_position(expected_lots=expected)
        if reconciliation["position_reconciled"]:
            canonical_result = self._canonical_post_fill_reconcile(
                root_state=root_state,
                bot_state=bot_state,
                intent=pending,
                reconciliation=reconciliation,
                expected_lots=expected,
                run_id=run_id,
                recovery=True,
            )
            canonical_ok = canonical_result is None or canonical_result.success
            if not canonical_ok:
                transition_intent(
                    pending,
                    OrderLifecycle.RECONCILIATION_REQUIRED,
                    details={
                        "expected_lots_after": expected,
                        "actual_lots_after": reconciliation.get("actual_lots_after"),
                        "canonical_reconciliation_required": True,
                        "post_fill_canonical": canonical_result.to_dict(),
                    },
                )
                pending["recovery_status"] = "CANONICAL_RECONCILIATION_REQUIRED"
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                self._record_order_transition_safe(pending, run_id)
                reconciliation.pop("_portfolio_payload", None)
                result.update(reconciliation)
                result.update(
                    {
                        "status": "canonical_reconciliation_pending",
                        "post_fill_canonical": canonical_result.to_dict(),
                    }
                )
                return result
            if canonical_result is not None:
                proof = dict(reconciliation.get("reconciliation_proof") or {})
                proof.update(
                    {
                        "canonical_reconciled": True,
                        "canonical_revision": canonical_result.revision,
                        "canonical_decision_checksum": (
                            canonical_result.decision_checksum
                        ),
                        "canonical_snapshot_at": canonical_result.snapshot_at,
                    }
                )
                reconciliation["reconciliation_proof"] = proof
        reconciliation.pop("_portfolio_payload", None)
        result.update(reconciliation)
        if reconciliation["position_reconciled"]:
            transition_intent(
                pending,
                OrderLifecycle.PORTFOLIO_RECONCILED,
                details={
                    "expected_lots_after": expected,
                    "actual_lots_after": reconciliation["actual_lots_after"],
                    "reconcile_attempts_used": reconciliation.get(
                        "reconcile_attempts_used"
                    ),
                    "reconciliation_confirmed_at": reconciliation.get(
                        "reconciliation_confirmed_at"
                    ),
                    "reconciliation_proof": reconciliation.get(
                        "reconciliation_proof"
                    ),
                },
            )
            bot_state["pending_order"] = pending
            self._save_state(root_state)
            self._record_order_transition_safe(
                pending,
                run_id,
                payload={
                    "recovery": True,
                    "reconciliation_proof": reconciliation.get(
                        "reconciliation_proof"
                    ),
                },
            )
            self._crash_checkpoint(
                OrderLifecycle.PORTFOLIO_RECONCILED,
                intent=pending,
            )

            risk_execution = self._register_risk_execution(
                intent=pending,
                final_payload=state,
                fallback_payload=None,
                reconciliation=reconciliation,
                run_id=run_id,
            )
            if risk_execution is not None and risk_execution.status in {
                "RECORDED",
                "DUPLICATE",
            }:
                self._crash_checkpoint("EXECUTION_RECORDED", intent=pending)
            result["risk_execution"] = (
                risk_execution.to_dict() if risk_execution is not None else None
            )
            result["risk_execution_status"] = pending.get(
                "risk_execution_status"
            )
            if (
                risk_execution is not None
                and risk_execution.status == "RUNTIME_ERROR"
            ):
                transition_intent(
                    pending,
                    OrderLifecycle.RISK_ACCOUNTING_REQUIRED,
                    details={
                        "expected_lots_after": expected,
                        "actual_lots_after": reconciliation["actual_lots_after"],
                        "reconciliation_confirmed_at": reconciliation.get(
                            "reconciliation_confirmed_at"
                        ),
                        "risk_execution_error": risk_execution.error,
                    },
                )
                bot_state["pending_order"] = pending
                self._save_state(root_state)
                self._record_order_transition_safe(pending, run_id)
                self._record_incident_safe(
                    event_type="RISK_EXECUTION_ACCOUNTING_FAILED",
                    severity="ERROR",
                    run_id=run_id,
                    bot_state=bot_state,
                    payload={
                        "order_id": order_id,
                        "risk_decision_id": pending.get("risk_decision_id"),
                        "risk_policy_hash": pending.get("risk_policy_hash"),
                        "reconciliation_confirmed_at": reconciliation.get(
                            "reconciliation_confirmed_at"
                        ),
                        "error": risk_execution.error,
                        "recovery": True,
                    },
                )
                result["status"] = "risk_accounting_pending"
            else:
                if risk_execution is not None:
                    transition_intent(
                        pending,
                        OrderLifecycle.RISK_ACCOUNTED,
                        details={
                            "risk_execution_status": risk_execution.status,
                            "risk_execution_duplicate": risk_execution.duplicate,
                            "reconciliation_confirmed_at": (
                                risk_execution.reconciliation_confirmed_at
                            ),
                        },
                    )
                    bot_state["pending_order"] = pending
                    self._save_state(root_state)
                    self._record_order_transition_safe(pending, run_id)
                    self._crash_checkpoint(
                        OrderLifecycle.RISK_ACCOUNTED,
                        intent=pending,
                    )
                bot_state.pop("pending_order", None)
                bot_state["last_confirmed_current_lots"] = reconciliation[
                    "actual_lots_after"
                ]
                bot_state["last_confirmed_target_lots"] = reconciliation[
                    "actual_lots_after"
                ]
                self._consume_candle(
                    bot_state,
                    str(pending.get("candle_time", "")),
                    dry_run=False,
                    order_id=order_id,
                    reason=status or "RECOVERED",
                )
        else:
            transition_intent(
                pending,
                OrderLifecycle.RECONCILIATION_REQUIRED,
                details={
                    "expected_lots_after": expected,
                    "actual_lots_after": reconciliation["actual_lots_after"],
                },
            )
            bot_state["pending_order"] = pending
            self._record_order_transition_safe(pending, run_id)
            self._record_incident_safe(
                event_type="POSITION_RECONCILIATION_MISMATCH",
                severity="ERROR",
                run_id=run_id,
                bot_state=bot_state,
                payload={
                    "order_id": order_id,
                    "expected_lots_after": expected,
                    "actual_lots_after": reconciliation["actual_lots_after"],
                    "recovery": True,
                },
            )
        self._save_state(root_state)
        return result

    def _periodic_reconciliation_due(
        self,
        bot_state: dict[str, Any],
        now: datetime,
    ) -> bool:
        raw = bot_state.get("last_periodic_reconcile_at")
        if not raw:
            return True
        try:
            previous = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return True
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        return (
            now - previous.astimezone(timezone.utc)
        ).total_seconds() >= self.config.portfolio_reconcile_interval_seconds

    def _market_status_check_due(
        self,
        monitor: dict[str, Any],
        now: datetime,
        *,
        active: bool,
    ) -> tuple[bool, int]:
        interval = (
            self.config.market_idle_poll_seconds
            if active
            else self.config.market_status_check_seconds
        )
        elapsed = seconds_since(monitor.get("last_status_check_at"), now)
        if elapsed is None or elapsed >= interval:
            return True, interval
        return False, max(5, int(ceil(interval - elapsed)))

    def _market_idle_reconciliation_due(
        self,
        idle_state: dict[str, Any],
        now: datetime,
    ) -> bool:
        elapsed = seconds_since(idle_state.get("last_reconcile_at"), now)
        return elapsed is None or elapsed >= self.config.market_idle_reconcile_seconds

    def _market_idle_heartbeat_due(
        self,
        idle_state: dict[str, Any],
        now: datetime,
    ) -> bool:
        if self.config.market_idle_heartbeat_seconds == 0:
            return True
        elapsed = seconds_since(idle_state.get("last_heartbeat_at"), now)
        return (
            elapsed is None
            or elapsed >= self.config.market_idle_heartbeat_seconds
        )

    def _run_portfolio_preflight(
        self,
        *,
        root_state: dict[str, Any],
        bot_state: dict[str, Any],
        portfolio: dict[str, Any],
        current_lots: int,
        proposed_target_lots: int,
        run_id: str,
        snapshot_at: datetime,
    ) -> tuple[
        PortfolioPreflightDecision,
        PortfolioSnapshotLease,
        None,
    ]:
        """Refresh, freeze and authorize one canonical portfolio revision."""

        assert self.instrument_id is not None
        manager = self.canonical_portfolio_manager
        if manager is None:
            raise RuntimeError("Canonical Portfolio Manager is unavailable.")
        started = time.perf_counter()
        self._journal_safe(
            JournalEvent(
                category="portfolio_preflight",
                event_type="PORTFOLIO_PREFLIGHT_STARTED",
                severity="INFO",
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload={
                    "proposed_target_lots": int(proposed_target_lots),
                    "current_lots": int(current_lots),
                },
            )
        )
        state = manager.refresh_from_api_portfolio(
            portfolio,
            instrument_metadata=self.instrument,
            record_event=True,
            snapshot_at=snapshot_at.isoformat(),
        )
        lease = PortfolioSnapshotLease.from_state(state)
        # Alpha3 canonical cutover: legacy portfolio projections are never read.
        legacy = None
        decision = self.portfolio_preflight_gate.evaluate(
            lease,
            account_id=self.account_id,
            mode=self._mode_name(),
            instrument_id=self.instrument_id,
            proposed_target_lots=proposed_target_lots,
            legacy=None,
            require_dual_read=False,
        )
        duration = max(0.0, time.perf_counter() - started)
        event_type = (
            "PORTFOLIO_PREFLIGHT_PASSED"
            if decision.passed
            else "PORTFOLIO_PREFLIGHT_BLOCKED"
        )
        self._journal_safe(
            JournalEvent(
                category="portfolio_preflight",
                event_type=event_type,
                severity="INFO" if decision.passed else "WARNING",
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status="passed" if decision.passed else "blocked",
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload={
                    "duration_seconds": duration,
                    "snapshot_revision": lease.revision,
                    "snapshot_decision_checksum": lease.decision_checksum,
                    "proposed_target_lots": int(proposed_target_lots),
                    "actual_lots": decision.context.actual_lots,
                    "canonical_target_lots": decision.context.canonical_target_lots,
                    "dual_read": decision.context.dual_read.to_dict(),
                    "blocking_reasons": list(decision.reasons),
                },
            )
        )
        if decision.passed:
            self._journal_safe(
                JournalEvent(
                    category="portfolio_preflight",
                    event_type="CANONICAL_ONLY_PREFLIGHT_PASSED",
                    severity="INFO",
                    session_id=self.session_id,
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    mode=self._mode_name(),
                    status="passed",
                    strategy_id=self.primary_strategy,
                    config_hash=self.primary_config_hash,
                    payload={
                        "snapshot_revision": lease.revision,
                        "schema_version": state.version,
                        "portfolio_source": state.portfolio_source,
                        "migration_status": state.migration.status.value,
                        "legacy_read_path_enabled": state.migration.legacy_read_path_enabled,
                    },
                )
            )
        bot_state["last_portfolio_preflight"] = decision.to_dict()
        return decision, lease, legacy

    def _recheck_portfolio_revision(
        self,
        lease: PortfolioSnapshotLease,
        *,
        run_id: str,
    ) -> PortfolioRevisionCheck:
        manager = self.canonical_portfolio_manager
        if manager is None:
            return PortfolioRevisionCheck(
                unchanged=False,
                expected_revision=lease.revision,
                current_revision=None,
                expected_decision_checksum=lease.decision_checksum,
                current_decision_checksum=None,
                reason="Canonical Portfolio Manager is unavailable before POST.",
            )
        check = self.portfolio_preflight_gate.recheck(
            manager.repository,
            lease,
            expected_account_id=self.account_id,
        )
        if not check.unchanged:
            self._journal_safe(
                JournalEvent(
                    category="portfolio_preflight",
                    event_type="PORTFOLIO_REVISION_CHANGED",
                    severity="WARNING",
                    session_id=self.session_id,
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    mode=self._mode_name(),
                    status="blocked",
                    strategy_id=self.primary_strategy,
                    config_hash=self.primary_config_hash,
                    payload=check.to_dict(),
                )
            )
        return check

    def _canonical_post_fill_reconcile(
        self,
        *,
        root_state: dict[str, Any],
        bot_state: dict[str, Any],
        intent: dict[str, Any],
        reconciliation: dict[str, Any],
        expected_lots: int,
        run_id: str,
        recovery: bool = False,
    ) -> PostFillPortfolioResult | None:
        """Require canonical MATCHED state after broker quantity reconciliation."""

        if not self.portfolio_preflight_enabled:
            return None
        coordinator = self.post_fill_portfolio
        if coordinator is None:
            return PostFillPortfolioResult(
                success=False,
                expected_lots=int(expected_lots),
                actual_lots=None,
                revision=None,
                decision_checksum=None,
                reconciliation_status=None,
                snapshot_at=None,
                state_status=None,
                reason="Post-fill Portfolio Coordinator is unavailable.",
            )
        actual = int(reconciliation.get("actual_lots_after") or 0)
        bot_state["last_confirmed_current_lots"] = actual
        bot_state["last_confirmed_target_lots"] = actual
        bot_state["pending_order"] = intent
        self._save_state(root_state)
        self._journal_safe(
            JournalEvent(
                category="portfolio",
                event_type="BROKER_POSITION_RECONCILED",
                severity="INFO",
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status="matched",
                action=str(intent.get("action") or ""),
                order_id=str(intent.get("order_id") or "") or None,
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload={
                    "expected_lots_after": int(expected_lots),
                    "actual_lots_after": actual,
                    "recovery": bool(recovery),
                    "reconciliation_proof": reconciliation.get(
                        "reconciliation_proof"
                    ),
                },
            )
        )
        portfolio_payload = reconciliation.pop("_portfolio_payload", None)
        manager = self.canonical_portfolio_manager
        if manager is None:
            return PostFillPortfolioResult(
                success=False,
                expected_lots=int(expected_lots),
                actual_lots=None,
                revision=None,
                decision_checksum=None,
                reconciliation_status=None,
                snapshot_at=None,
                state_status=None,
                reason="Canonical Portfolio Manager is unavailable for target commit.",
            )
        try:
            manager.stage_confirmed_target(
                instrument_id=str(self.instrument_id or ""),
                target_lots=int(expected_lots),
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                candle_interval=self.config.candle_interval,
                ticker=self.config.ticker,
                figi=str((self.instrument or {}).get("figi") or ""),
                class_code=self.config.class_code,
                candle_time=str(intent.get("candle_time") or "") or None,
                transaction_id=(
                    f"postfill-target:{intent.get('order_id')}:{expected_lots}"
                ),
            )
        except Exception as exc:
            return PostFillPortfolioResult(
                success=False,
                expected_lots=int(expected_lots),
                actual_lots=actual,
                revision=None,
                decision_checksum=None,
                reconciliation_status=None,
                snapshot_at=None,
                state_status="BLOCKED",
                reason=f"Canonical target commit failed after fill: {exc}",
            )
        if isinstance(portfolio_payload, dict):
            result = coordinator.reconcile_from_api_portfolio(
                portfolio_payload,
                instrument_id=str(self.instrument_id or ""),
                expected_lots=int(expected_lots),
                instrument_metadata=self.instrument,
                snapshot_at=str(
                    reconciliation.get("reconciliation_confirmed_at")
                    or datetime.now(timezone.utc).isoformat()
                ),
            )
        else:
            result = coordinator.reconcile(
                instrument_id=str(self.instrument_id or ""),
                expected_lots=int(expected_lots),
            )
        intent["post_fill_canonical"] = result.to_dict()
        event_type = (
            "POST_FILL_CANONICAL_RECONCILED"
            if result.success
            else "POST_FILL_CANONICAL_RECONCILIATION_FAILED"
        )
        self._journal_safe(
            JournalEvent(
                category="portfolio",
                event_type=event_type,
                severity="INFO" if result.success else "ERROR",
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status="matched" if result.success else "blocked",
                action=str(intent.get("action") or ""),
                order_id=str(intent.get("order_id") or "") or None,
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload={**result.to_dict(), "recovery": bool(recovery)},
            )
        )
        bot_state["last_post_fill_canonical"] = result.to_dict()
        bot_state["pending_order"] = intent
        self._save_state(root_state)
        return result

    def _record_market_event_safe(
        self,
        *,
        event_type: str,
        run_id: str,
        status: str,
        severity: str = "INFO",
        payload: dict[str, Any],
    ) -> None:
        self._journal_safe(
            JournalEvent(
                category="market",
                event_type=event_type,
                severity=severity,
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status=status,
                action="HOLD",
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload=payload,
            )
        )

    def _market_idle_result(
        self,
        *,
        bot_state: dict[str, Any],
        monitor: dict[str, Any],
        now: datetime,
        run_id: str,
        transition: str,
        heartbeat_recorded: bool,
        periodic_reconciliation: dict[str, Any] | None,
        recommended_wait_seconds: int,
        api_state: str,
    ) -> dict[str, Any]:
        idle_state = monitor.setdefault("idle", {})
        current_lots = bot_state.get("last_confirmed_current_lots")
        target_lots = bot_state.get("last_confirmed_target_lots")
        if periodic_reconciliation:
            current_lots = periodic_reconciliation.get("current_lots", current_lots)
            target_lots = periodic_reconciliation.get("target_lots", target_lots)
        return {
            "status": "market_idle",
            "market_state": "IDLE",
            "market_idle_active": True,
            "market_idle_transition": transition,
            "market_idle_since": idle_state.get("entered_at"),
            "market_idle_reason": idle_state.get("reason_code"),
            "market_idle_checks": int(idle_state.get("check_count") or 0),
            "market_status_checked_at": monitor.get("last_status_check_at"),
            "market_status_text": monitor.get("trading_status_text"),
            "market_order_availability_key": monitor.get("order_availability_key"),
            "trading_status": monitor.get("last_trading_status"),
            "ticker": self.config.ticker,
            "instrument_id": self.instrument_id,
            "candle_time": bot_state.get("last_seen_candle"),
            "last_seen_candle": bot_state.get("last_seen_candle"),
            "last_consumed_candle": bot_state.get("last_consumed_candle"),
            "action": "HOLD",
            "intended_action": "HOLD",
            "order_was_sent": False,
            "current_lots": current_lots,
            "target_lots": target_lots,
            "pending_order": None,
            "new_orders_blocked": True,
            "execution_block_reason": (
                "MARKET_IDLE: configured order route is not executable; "
                "strategy evaluation is paused."
            ),
            "periodic_reconciliation": periodic_reconciliation,
            "position_reconciled": (
                periodic_reconciliation.get("position_reconciled")
                if periodic_reconciliation
                else None
            ),
            "recommended_wait_seconds": max(5, recommended_wait_seconds),
            "api_state": api_state,
            "last_check_time": now.isoformat(),
            "run_id": run_id,
            # Repeated 5-minute checks do not need a cycle row.  The dedicated
            # market heartbeat and portfolio reconciliation events remain in
            # the journal.
            "suppress_cycle_event": (
                transition == "NONE"
                and not heartbeat_recorded
                and periodic_reconciliation is None
            ),
        }

    def _market_idle_gate(
        self,
        *,
        root_state: dict[str, Any],
        bot_state: dict[str, Any],
        now: datetime,
        run_id: str,
    ) -> dict[str, Any] | None:
        if (
            not self.config.market_idle_enabled
            or not self.config.check_trading_status
            or not hasattr(self.api, "get_trading_status")
        ):
            return None

        monitor = bot_state.setdefault("market_monitor", {})
        idle_state = monitor.setdefault("idle", {})
        active = bool(idle_state.get("active"))
        due, next_wait = self._market_status_check_due(
            monitor,
            now,
            active=active,
        )
        if not due:
            if not active:
                return None
            return self._market_idle_result(
                bot_state=bot_state,
                monitor=monitor,
                now=now,
                run_id=run_id,
                transition="NONE",
                heartbeat_recorded=False,
                periodic_reconciliation=None,
                recommended_wait_seconds=next_wait,
                api_state="NOT_CALLED",
            )

        with self._measure_phase("market_status"):
            status = self.api.get_trading_status(self.instrument_id)
        checked_at = datetime.now(timezone.utc)
        availability_assessment = classify_market_status(
            status,
            order_type=self.config.order_type,
        )
        availability = availability_assessment.executable
        reason_code = availability_assessment.reason_code
        order_key = availability_assessment.order_availability_key
        monitor["last_status_check_at"] = checked_at.isoformat()
        monitor["last_trading_status"] = dict(status)
        monitor["trading_status_text"] = (
            availability_assessment.trading_status_text
        )
        monitor["order_availability_key"] = order_key
        monitor["selected_order_type"] = self.config.order_type.upper()
        monitor["last_availability"] = availability
        monitor["last_reason_code"] = reason_code

        if availability is False:
            transition = "NONE"
            if not active:
                transition = "ENTERED"
                idle_state.clear()
                idle_state.update(
                    {
                        "active": True,
                        "entered_at": checked_at.isoformat(),
                        "check_count": 0,
                        "transition_count": int(
                            monitor.get("transition_count") or 0
                        )
                        + 1,
                    }
                )
                monitor["transition_count"] = idle_state["transition_count"]
            idle_state["active"] = True
            idle_state["last_seen_at"] = checked_at.isoformat()
            idle_state["reason_code"] = reason_code
            idle_state["check_count"] = int(idle_state.get("check_count") or 0) + 1
            idle_state["trading_status_text"] = monitor["trading_status_text"]

            periodic_reconciliation: dict[str, Any] | None = None
            if (
                self.allow_execution
                and self._market_idle_reconciliation_due(idle_state, checked_at)
            ):
                periodic_reconciliation = self._periodic_reconcile_existing_decision(
                    root_state=root_state,
                    bot_state=bot_state,
                    target_lots=int(
                        bot_state.get("last_confirmed_target_lots") or 0
                    ),
                    now=checked_at,
                    run_id=run_id,
                    force=True,
                    interval_seconds=self.config.market_idle_reconcile_seconds,
                )
                idle_state["last_reconcile_at"] = checked_at.isoformat()

            heartbeat_due = self._market_idle_heartbeat_due(
                idle_state,
                checked_at,
            )
            heartbeat_recorded = False
            event_payload = {
                "reason_code": reason_code,
                "selected_order_type": self.config.order_type.upper(),
                "order_availability_key": order_key,
                "trading_status": dict(status),
                "market_idle_poll_seconds": self.config.market_idle_poll_seconds,
                "market_idle_reconcile_seconds": (
                    self.config.market_idle_reconcile_seconds
                ),
                "last_seen_candle": bot_state.get("last_seen_candle"),
                "periodic_reconciliation": periodic_reconciliation,
            }
            if transition == "ENTERED":
                idle_state["last_heartbeat_at"] = checked_at.isoformat()
                heartbeat_recorded = True
                self._record_market_event_safe(
                    event_type="MARKET_IDLE_ENTERED",
                    run_id=run_id,
                    status="IDLE",
                    payload=event_payload,
                )
            elif heartbeat_due:
                idle_state["last_heartbeat_at"] = checked_at.isoformat()
                heartbeat_recorded = True
                self._record_market_event_safe(
                    event_type="MARKET_IDLE_HEARTBEAT",
                    run_id=run_id,
                    status="IDLE",
                    payload={
                        **event_payload,
                        "idle_duration_seconds": seconds_since(
                            idle_state.get("entered_at"), checked_at
                        ),
                        "check_count": idle_state.get("check_count"),
                    },
                )

            return self._market_idle_result(
                bot_state=bot_state,
                monitor=monitor,
                now=checked_at,
                run_id=run_id,
                transition=transition,
                heartbeat_recorded=heartbeat_recorded,
                periodic_reconciliation=periodic_reconciliation,
                recommended_wait_seconds=self.config.market_idle_poll_seconds,
                api_state="AVAILABLE",
            )

        if availability is None and active:
            # Do not resume from MARKET_IDLE on an ambiguous response.  The
            # next explicit executable status will leave idle mode.
            idle_state["last_seen_at"] = checked_at.isoformat()
            idle_state["reason_code"] = "STATUS_UNCERTAIN_WHILE_IDLE"
            idle_state["check_count"] = int(idle_state.get("check_count") or 0) + 1
            return self._market_idle_result(
                bot_state=bot_state,
                monitor=monitor,
                now=checked_at,
                run_id=run_id,
                transition="NONE",
                heartbeat_recorded=False,
                periodic_reconciliation=None,
                recommended_wait_seconds=self.config.market_idle_poll_seconds,
                api_state="AVAILABLE",
            )

        if availability is True and active:
            entered_at = idle_state.get("entered_at")
            duration = seconds_since(entered_at, checked_at)
            idle_state["active"] = False
            idle_state["exited_at"] = checked_at.isoformat()
            idle_state["exit_reason"] = "EXECUTABLE_STATUS_CONFIRMED"
            self._market_cycle_context = {
                "market_state": "OPEN",
                "market_idle_active": False,
                "market_idle_transition": "EXITED",
                "market_idle_since": entered_at,
                "market_idle_duration_seconds": duration,
                "market_status_checked_at": checked_at.isoformat(),
                "market_status_text": monitor.get("trading_status_text"),
                "market_order_availability_key": order_key,
            }
            self._record_market_event_safe(
                event_type="MARKET_IDLE_EXITED",
                run_id=run_id,
                status="OPEN",
                payload={
                    "entered_at": entered_at,
                    "exited_at": checked_at.isoformat(),
                    "idle_duration_seconds": duration,
                    "selected_order_type": self.config.order_type.upper(),
                    "order_availability_key": order_key,
                    "trading_status": dict(status),
                    "last_seen_candle": bot_state.get("last_seen_candle"),
                },
            )
        elif availability is True:
            self._market_cycle_context = {
                "market_state": "OPEN",
                "market_idle_active": False,
                "market_status_checked_at": checked_at.isoformat(),
                "market_status_text": monitor.get("trading_status_text"),
                "market_order_availability_key": order_key,
            }
        else:
            self._market_cycle_context = {
                "market_state": "UNKNOWN",
                "market_idle_active": False,
                "market_status_checked_at": checked_at.isoformat(),
                "market_status_text": monitor.get("trading_status_text"),
                "market_order_availability_key": order_key,
                "market_idle_reason": reason_code,
            }
        return None

    def _periodic_reconcile_existing_decision(
        self,
        *,
        root_state: dict[str, Any],
        bot_state: dict[str, Any],
        target_lots: int,
        now: datetime,
        run_id: str,
        force: bool = False,
        interval_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        if not self.allow_execution or (
            not force and not self._periodic_reconciliation_due(bot_state, now)
        ):
            return None
        assert self.instrument is not None
        with self._measure_phase("periodic_portfolio_reconciliation"):
            portfolio = self.api.get_portfolio(self.account_id)
            current_lots = self.api.position_lots(portfolio, self.instrument)
        guard_reason = self._ensure_primary_assignment(
            root_state,
            current_lots=current_lots,
            run_id=run_id,
        )
        expected_raw = bot_state.get("last_confirmed_target_lots")
        baseline_created = expected_raw is None
        expected_lots = (
            current_lots if baseline_created else int(expected_raw)
        )
        reconciled = guard_reason is None and current_lots == expected_lots
        bot_state["last_periodic_reconcile_at"] = now.isoformat()
        bot_state["last_confirmed_current_lots"] = current_lots
        # Do not move the expected-position baseline after a mismatch. Doing so
        # would make an external/manual drift disappear on the next heartbeat.
        if baseline_created:
            bot_state["last_confirmed_target_lots"] = expected_lots
        event_type = (
            "PERIODIC_POSITION_BASELINED"
            if baseline_created and reconciled
            else (
                "PERIODIC_POSITION_RECONCILED"
                if reconciled
                else "PERIODIC_POSITION_MISMATCH"
            )
        )
        severity = "INFO" if reconciled else "ERROR"
        self._journal_safe(
            JournalEvent(
                category="portfolio",
                event_type=event_type,
                severity=severity,
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status="reconciled" if reconciled else "mismatch",
                action="HOLD",
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload={
                    "current_lots": current_lots,
                    "target_lots": expected_lots,
                    "position_reconciled": reconciled,
                    "execution_block_reason": guard_reason,
                    "baseline_created": baseline_created,
                    "interval_seconds": (
                        interval_seconds
                        if interval_seconds is not None
                        else self.config.portfolio_reconcile_interval_seconds
                    ),
                },
            )
        )

        previous_external = bot_state.get("external_activity")
        if not reconciled:
            source = (
                "BROKER_POSITION_DRIFT"
                if current_lots != expected_lots
                else "OWNERSHIP_OR_CONFIGURATION_MISMATCH"
            )
            external_payload = {
                "detected_at": (
                    previous_external.get("detected_at")
                    if isinstance(previous_external, dict)
                    else now.isoformat()
                ),
                "last_detected_at": now.isoformat(),
                "source": source,
                "detection_source": "PERIODIC_PORTFOLIO_RECONCILIATION",
                "current_lots": current_lots,
                "expected_lots": expected_lots,
                "target_lots": expected_lots,
                "delta_lots": current_lots - expected_lots,
                "execution_block_reason": guard_reason,
                "risk_resync_required": True,
                # Compatibility alias retained for state created by early alpha builds.
                "requires_risk_resync": True,
            }
            is_new_external = not isinstance(previous_external, dict) or any(
                previous_external.get(key) != external_payload.get(key)
                for key in (
                    "source",
                    "current_lots",
                    "expected_lots",
                    "execution_block_reason",
                )
            )
            risk_resync_error: str | None = None
            risk_resync_persisted = False
            if self.risk_runtime is not None:
                try:
                    if is_new_external or not self.risk_runtime.risk_resync_required():
                        self.risk_runtime.mark_external_activity(
                            now=now,
                            reason=(
                                "External/manual account activity changed the "
                                "broker position outside the authorised execution path."
                            ),
                            source=source,
                        )
                    risk_resync_persisted = (
                        self.risk_runtime.risk_resync_required()
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    risk_resync_error = str(exc)
            external_payload["risk_resync_persisted"] = risk_resync_persisted
            external_payload["risk_resync_error"] = risk_resync_error
            external_payload["position_reconciled"] = False
            bot_state["external_activity"] = external_payload
            if is_new_external or (
                isinstance(previous_external, dict)
                and not previous_external.get("risk_resync_persisted")
                and risk_resync_persisted
            ):
                self._journal_safe(
                    JournalEvent(
                        category="risk",
                        event_type="EXTERNAL_ACTIVITY_DETECTED",
                        severity="ERROR",
                        session_id=self.session_id,
                        run_id=run_id,
                        account_id=self.account_id,
                        instrument_id=self.instrument_id,
                        ticker=self.config.ticker,
                        mode=self._mode_name(),
                        status="RESYNC_REQUIRED",
                        action="UNKNOWN",
                        strategy_id=self.primary_strategy,
                        config_hash=self.primary_config_hash,
                        payload=external_payload,
                    )
                )
        elif isinstance(previous_external, dict):
            persistent_resync_required = False
            resync_check_error: str | None = None
            if self.risk_runtime is not None:
                try:
                    if not previous_external.get("risk_resync_persisted"):
                        self.risk_runtime.mark_external_activity(
                            now=now,
                            reason=str(
                                previous_external.get("execution_block_reason")
                                or "External account activity requires risk resync."
                            ),
                            source=str(
                                previous_external.get("source") or "UNKNOWN"
                            ),
                        )
                        previous_external["risk_resync_persisted"] = True
                    persistent_resync_required = (
                        self.risk_runtime.risk_resync_required()
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    persistent_resync_required = True
                    resync_check_error = str(exc)

            awaiting_resync = (
                self.risk_runtime is not None and persistent_resync_required
            )
            if awaiting_resync:
                reconciled_payload = {
                    **previous_external,
                    "position_reconciled": True,
                    "position_reconciled_at": previous_external.get(
                        "position_reconciled_at"
                    )
                    or now.isoformat(),
                    "current_lots": current_lots,
                    "expected_lots": expected_lots,
                    "target_lots": expected_lots,
                    "delta_lots": current_lots - expected_lots,
                    "risk_resync_required": True,
                    "requires_risk_resync": True,
                    "risk_resync_error": resync_check_error,
                }
                first_position_reconcile = not previous_external.get(
                    "position_reconciled_at"
                )
                bot_state["external_activity"] = reconciled_payload
                if first_position_reconcile:
                    self._journal_safe(
                        JournalEvent(
                            category="risk",
                            event_type="EXTERNAL_POSITION_RECONCILED",
                            severity="WARNING",
                            session_id=self.session_id,
                            run_id=run_id,
                            account_id=self.account_id,
                            instrument_id=self.instrument_id,
                            ticker=self.config.ticker,
                            mode=self._mode_name(),
                            status="RISK_RESYNC_REQUIRED",
                            action="HOLD",
                            strategy_id=self.primary_strategy,
                            config_hash=self.primary_config_hash,
                            payload=reconciled_payload,
                        )
                    )
            else:
                cleared_payload = {
                    **previous_external,
                    "cleared_at": now.isoformat(),
                    "current_lots": current_lots,
                    "expected_lots": expected_lots,
                    "target_lots": expected_lots,
                    "delta_lots": current_lots - expected_lots,
                    "position_reconciled": True,
                    "risk_resync_required": False,
                    "requires_risk_resync": False,
                    "risk_resync_error": resync_check_error,
                }
                self._journal_safe(
                    JournalEvent(
                        category="risk",
                        event_type="EXTERNAL_ACTIVITY_CLEARED",
                        severity="INFO",
                        session_id=self.session_id,
                        run_id=run_id,
                        account_id=self.account_id,
                        instrument_id=self.instrument_id,
                        ticker=self.config.ticker,
                        mode=self._mode_name(),
                        status="RECONCILED",
                        action="HOLD",
                        strategy_id=self.primary_strategy,
                        config_hash=self.primary_config_hash,
                        payload=cleared_payload,
                    )
                )
                bot_state.pop("external_activity", None)

        if not reconciled:
            self._record_incident_safe(
                event_type="PERIODIC_POSITION_MISMATCH",
                severity="ERROR",
                run_id=run_id,
                bot_state=bot_state,
                payload=dict(bot_state.get("external_activity") or {}),
            )
        return {
            "performed": True,
            "current_lots": current_lots,
            "target_lots": expected_lots,
            "position_reconciled": reconciled,
            "execution_block_reason": guard_reason,
            "baseline_created": baseline_created,
            "external_activity_active": bool(
                bot_state.get("external_activity")
            ),
            "checked_at": now.isoformat(),
        }

    def _reconcile_position(self, *, expected_lots: int) -> dict[str, Any]:
        assert self.instrument is not None
        actual_lots: int | None = None
        attempts = 0
        last_risk_inputs: dict[str, float | None] = {
            "equity_rub": None,
            "cash_rub": None,
            "securities_value_rub": None,
        }
        for attempts in range(1, self.config.reconcile_attempts + 1):
            portfolio = self.api.get_portfolio(self.account_id)
            last_risk_inputs = portfolio_risk_inputs(portfolio)
            actual_lots = self.api.position_lots(portfolio, self.instrument)
            if actual_lots == expected_lots:
                confirmed_at = datetime.now(timezone.utc).isoformat()
                proof = {
                    "position_reconciled": True,
                    "account_id": self.account_id,
                    "instrument_id": self.instrument_id,
                    "expected_lots_after": expected_lots,
                    "actual_lots_after": actual_lots,
                    "reconcile_attempts_used": attempts,
                    "confirmed_at": confirmed_at,
                }
                return {
                    **proof,
                    "reconciliation_confirmed_at": confirmed_at,
                    "reconciliation_proof": proof,
                    "portfolio_equity_rub": last_risk_inputs.get("equity_rub"),
                    "cash_rub": last_risk_inputs.get("cash_rub"),
                    "securities_value_rub": last_risk_inputs.get(
                        "securities_value_rub"
                    ),
                    "_portfolio_payload": portfolio,
                }
            if (
                attempts < self.config.reconcile_attempts
                and self.config.reconcile_delay_seconds > 0
            ):
                time.sleep(self.config.reconcile_delay_seconds)
        checked_at = datetime.now(timezone.utc).isoformat()
        proof = {
            "position_reconciled": False,
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "expected_lots_after": expected_lots,
            "actual_lots_after": actual_lots,
            "reconcile_attempts_used": attempts,
            "confirmed_at": None,
            "checked_at": checked_at,
        }
        return {
            **proof,
            "reconciliation_confirmed_at": None,
            "reconciliation_proof": proof,
            "portfolio_equity_rub": last_risk_inputs.get("equity_rub"),
            "cash_rub": last_risk_inputs.get("cash_rub"),
            "securities_value_rub": last_risk_inputs.get(
                "securities_value_rub"
            ),
            "_portfolio_payload": portfolio,
        }

    def _register_risk_execution(
        self,
        *,
        intent: dict[str, Any],
        final_payload: dict[str, Any] | None,
        fallback_payload: dict[str, Any] | None,
        reconciliation: dict[str, Any],
        run_id: str,
    ) -> RiskExecutionOutcome | None:
        filled_lots = int(intent.get("executed_lots", 0) or 0)
        if filled_lots <= 0:
            intent["risk_execution_status"] = "NOT_REQUIRED"
            return None

        proof = dict(reconciliation.get("reconciliation_proof") or {})
        confirmed_at = reconciliation.get("reconciliation_confirmed_at")
        if (
            not reconciliation.get("position_reconciled")
            or not proof.get("position_reconciled")
            or proof.get("expected_lots_after") != proof.get("actual_lots_after")
        ):
            outcome = RiskExecutionOutcome(
                enforced=True,
                mode=(
                    self.risk_runtime.mode
                    if self.risk_runtime is not None
                    else "SANDBOX_EXECUTION"
                ),
                execution_id=str(intent.get("order_id") or ""),
                decision_id=str(intent.get("risk_decision_id") or "") or None,
                policy_hash=str(intent.get("risk_policy_hash") or "") or None,
                registration=None,
                execution_source="STRATEGY",
                reconciliation_confirmed_at=(
                    str(confirmed_at) if confirmed_at else None
                ),
                reconciliation_proof=proof or None,
                error=(
                    "Risk accounting was requested without a valid portfolio "
                    "reconciliation proof."
                ),
            )
            intent["risk_execution_status"] = outcome.status
            intent["risk_execution"] = outcome.to_dict()
            self._record_risk_execution_outcome_safe(
                outcome=outcome,
                intent=intent,
                run_id=run_id,
            )
            return outcome

        risk_expected = bool(intent.get("risk_enforced"))
        if self.risk_runtime is None:
            if not risk_expected and not self.require_risk_runtime_for_execution:
                intent["risk_execution_status"] = "NOT_ENFORCED"
                return None
            outcome = RiskExecutionOutcome(
                enforced=True,
                mode="SANDBOX_EXECUTION",
                execution_id=str(intent.get("order_id") or ""),
                decision_id=str(intent.get("risk_decision_id") or "") or None,
                policy_hash=str(intent.get("risk_policy_hash") or "") or None,
                registration=None,
                execution_source="STRATEGY",
                reconciliation_confirmed_at=str(confirmed_at),
                reconciliation_proof=proof,
                error="Risk runtime is unavailable for confirmed execution.",
            )
            intent["risk_execution_status"] = outcome.status
            intent["risk_execution"] = outcome.to_dict()
            self._record_risk_execution_outcome_safe(
                outcome=outcome,
                intent=intent,
                run_id=run_id,
            )
            return outcome

        fallback_price = intent.get("confirmed_execution_price_rub")
        if fallback_price is None:
            fallback_price = intent.get("decision_price_rub")
        price, price_source = executed_order_price(
            final_payload,
            fallback=fallback_price,
        )
        if price is None and fallback_payload is not final_payload:
            price, price_source = executed_order_price(
                fallback_payload,
                fallback=fallback_price,
            )
        if price is not None:
            intent["confirmed_execution_price_rub"] = price
            intent["confirmed_execution_price_source"] = price_source
        if price is None:
            outcome = RiskExecutionOutcome(
                enforced=True,
                mode=self.risk_runtime.mode,
                execution_id=str(intent.get("order_id") or ""),
                decision_id=str(intent.get("risk_decision_id") or "") or None,
                policy_hash=str(intent.get("risk_policy_hash") or "") or None,
                registration=None,
                execution_source="STRATEGY",
                reconciliation_confirmed_at=str(confirmed_at),
                reconciliation_proof=proof,
                error="Confirmed fill has no usable execution price.",
            )
        else:
            portfolio_summary = {
                "totalAmountPortfolio": reconciliation.get(
                    "portfolio_equity_rub"
                ),
                "totalAmountCurrencies": reconciliation.get("cash_rub"),
                "totalAmountShares": reconciliation.get(
                    "securities_value_rub"
                ),
            }
            outcome = self.risk_runtime.record_execution(
                execution_id=str(intent.get("order_id") or ""),
                executed_at=datetime.now(timezone.utc),
                signed_lots=signed_lot_delta(
                    str(intent.get("action") or ""),
                    filled_lots,
                ),
                price_rub=price,
                lot_size=max(1, int(intent.get("lot_size", 1) or 1)),
                portfolio=portfolio_summary,
                decision_id=str(intent.get("risk_decision_id") or "") or None,
                expected_policy_hash=(
                    str(intent.get("risk_policy_hash") or "") or None
                ),
                price_source=price_source,
                execution_source="STRATEGY",
                reconciliation_confirmed_at=str(confirmed_at),
                reconciliation_proof=proof,
            )

        intent["risk_execution_status"] = outcome.status
        intent["risk_execution"] = outcome.to_dict()
        intent["risk_execution_id"] = outcome.execution_id
        intent["risk_execution_price_rub"] = outcome.price_rub
        intent["risk_execution_price_source"] = outcome.price_source
        intent["risk_execution_source"] = outcome.execution_source
        intent["reconciliation_confirmed_at"] = confirmed_at
        intent["reconciliation_proof"] = proof
        self._record_risk_execution_outcome_safe(
            outcome=outcome,
            intent=intent,
            run_id=run_id,
        )
        return outcome

    def _consume_candle(
        self,
        bot_state: dict[str, Any],
        candle_time: str,
        *,
        dry_run: bool,
        order_id: str | None,
        reason: str,
    ) -> None:
        bot_state["last_seen_candle"] = candle_time
        bot_state["last_decision_candle"] = candle_time
        bot_state["last_consumed_candle"] = candle_time
        bot_state["last_candle_time"] = candle_time  # v2 compatibility
        bot_state["last_decision_reason"] = reason
        if dry_run:
            bot_state["last_dry_run_candle"] = candle_time
        if order_id:
            bot_state["last_order_candle"] = candle_time
            bot_state["last_order_id"] = order_id

    def _max_signal_age_seconds(self) -> int:
        if self.config.max_signal_age_seconds > 0:
            return self.config.max_signal_age_seconds
        return candle_interval_policy(
            self.config.candle_interval
        ).automatic_max_signal_age_seconds

    def _deterministic_order_id(
        self,
        candle_time: str,
        action: str,
        target_lots: int,
        *,
        risk_decision_id: str | None = None,
    ) -> str:
        assert self.instrument_id is not None
        source = "|".join(
            [
                self.account_id,
                self.instrument_id,
                self.config.candle_interval,
                self.primary_strategy,
                self.primary_config_hash,
                candle_time,
                action,
                str(target_lots),
                str(risk_decision_id or "NO_RISK_DECISION"),
            ]
        )
        return str(uuid5(NAMESPACE_URL, source))

    def _record_strategy_decisions_safe(
        self,
        *,
        root_state: dict[str, Any],
        bot_state: dict[str, Any],
        decisions: dict[StrategyName, StrategyDecision],
        decision_payloads: dict[str, dict[str, Any]] | None = None,
        comparison: dict[str, Any],
        run_id: str,
    ) -> None:
        """Persist and journal each forward decision at most once per bar."""
        assert self.instrument_id is not None
        strategy_states = root_state.setdefault("strategy_states", {})
        config_registry = root_state.setdefault("strategy_configs", {})

        for strategy, decision in decisions.items():
            persisted_decision = dict(
                (decision_payloads or {}).get(strategy) or decision.to_dict()
            )
            state_key = "|".join(
                [
                    self.account_id,
                    self.instrument_id,
                    self.config.candle_interval,
                    strategy,
                    decision.config_hash[:16],
                ]
            )
            state = strategy_states.setdefault(state_key, {})
            config_registry.setdefault(
                decision.config_hash,
                self.strategy_suite.parameter_snapshot(strategy),
            )
            state["strategy_id"] = strategy
            state["strategy_version"] = decision.strategy_version
            state["config_hash"] = decision.config_hash
            state["role"] = decision.role
            state["last_decision"] = persisted_decision
            decision_log_key = (
                f"{self.session_id}|{decision.candle_time}|{decision.role}"
            )
            if state.get("last_logged_key") == decision_log_key:
                continue

            state["last_logged_key"] = decision_log_key
            state["last_logged_candle"] = decision.candle_time
            self._journal_safe(
                JournalEvent(
                    category="strategy",
                    event_type=f"{decision.role}_DECISION",
                    severity="INFO",
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    candle_time=decision.candle_time,
                    payload={
                        **persisted_decision,
                        "configuration": self.strategy_suite.parameter_snapshot(
                            strategy
                        ),
                        "strategy_suite_hash": self.suite_hash,
                    },
                )
            )

        comparison_key = (
            f"{self.session_id}|{comparison.get('candle_time')}|{self.suite_hash}"
        )
        if bot_state.get("last_comparison_key") != comparison_key:
            bot_state["last_comparison_key"] = comparison_key
            bot_state["last_comparison_candle"] = comparison.get("candle_time")
            self._journal_safe(
                JournalEvent(
                    category="strategy_comparison",
                    event_type=str(comparison.get("event_type", "UNKNOWN")),
                    severity=(
                        "INFO" if comparison.get("all_agree", True) else "WARNING"
                    ),
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    candle_time=comparison.get("candle_time"),
                    payload={
                        **comparison,
                        "strategy_suite_hash": self.suite_hash,
                    },
                )
            )

    def _migrate_execution_scope(self, root_state: dict[str, Any]) -> None:
        """Migrate v3.4 per-timeframe ownership to account/instrument scope.

        Broker positions are aggregated by instrument.  Keeping separate
        owners for HOUR and DAY would allow two strategies to fight over the
        same position.  A single unambiguous legacy owner is migrated;
        conflicting legacy owners are retained as evidence and later block
        automatic assignment when a position is open.
        """
        scopes = root_state.setdefault("execution_scopes", {})
        if self.execution_scope_key in scopes:
            return
        prefix = self.execution_scope_key + "|"
        legacy_items = [
            (key, value)
            for key, value in scopes.items()
            if str(key).startswith(prefix) and isinstance(value, dict)
        ]
        active_items = [
            (key, value)
            for key, value in legacy_items
            if isinstance(value.get("active_primary"), dict)
        ]
        if len(active_items) == 1:
            key, value = active_items[0]
            migrated = dict(value)
            migrated["migrated_from_scope"] = key
            migrated["migrated_at"] = datetime.now(timezone.utc).isoformat()
            scopes[self.execution_scope_key] = migrated
        elif len(active_items) > 1:
            scopes[self.execution_scope_key] = {
                "migration_conflict": {
                    "legacy_scopes": [key for key, _ in active_items],
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
            }

    def _find_foreign_pending_order(
        self,
        root_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        prefix = self.execution_scope_key + "|"
        for key, candidate in root_state.get("bots", {}).items():
            if key == self.state_key or not str(key).startswith(prefix):
                continue
            pending = (
                candidate.get("pending_order")
                if isinstance(candidate, dict)
                else None
            )
            if pending:
                return {"state_key": key, "pending_order": pending}
        return None

    def _ensure_primary_assignment(
        self,
        root_state: dict[str, Any],
        *,
        current_lots: int,
        run_id: str,
    ) -> str | None:
        """Prevent silent transfer of an open position to another strategy."""
        scopes = root_state.setdefault("execution_scopes", {})
        scope = scopes.setdefault(self.execution_scope_key, {})
        migration_conflict = scope.get("migration_conflict")
        if migration_conflict and current_lots <= 0:
            # With a flat broker position there is no economic ownership to
            # preserve. Resolve an old multi-timeframe conflict explicitly so
            # it cannot reappear after the newly assigned PRIMARY opens a
            # position on a later bar.
            scope.pop("migration_conflict", None)
            scope["migration_conflict_resolved_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            self._journal_safe(
                JournalEvent(
                    category="strategy_config",
                    event_type="LEGACY_SCOPE_CONFLICT_CLEARED",
                    severity="WARNING",
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    payload={
                        "current_lots": current_lots,
                        "cleared_conflict": migration_conflict,
                    },
                )
            )
            migration_conflict = None
        if migration_conflict and current_lots > 0:
            reason = (
                "Strategy ownership cannot be assigned automatically: legacy "
                "state contains conflicting timeframe owners for the same "
                "account/instrument position. Reconcile or close the position "
                "before execution."
            )
            self._journal_safe(
                JournalEvent(
                    category="incident",
                    event_type="STRATEGY_SCOPE_MIGRATION_CONFLICT",
                    severity="ERROR",
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    payload={
                        "reason": reason,
                        "current_lots": current_lots,
                        "migration_conflict": migration_conflict,
                    },
                )
            )
            return reason
        snapshot = self.strategy_suite.parameter_snapshot(self.primary_strategy)
        desired = {
            "strategy_id": self.primary_strategy,
            "strategy_version": snapshot["strategy_version"],
            "config_hash": self.primary_config_hash,
            "candle_interval": self.config.candle_interval,
            "strategy_suite_hash": self.suite_hash,
            "shadow_strategies": list(self.strategy_suite.shadow_strategies),
        }
        active = scope.get("active_primary")
        same = bool(
            isinstance(active, dict)
            and active.get("strategy_id") == desired["strategy_id"]
            and active.get("config_hash") == desired["config_hash"]
            and active.get("candle_interval") == desired["candle_interval"]
        )
        if same:
            active["strategy_suite_hash"] = self.suite_hash
            active["shadow_strategies"] = list(
                self.strategy_suite.shadow_strategies
            )
            active["last_seen_at"] = datetime.now(timezone.utc).isoformat()
            return None

        if current_lots > 0 and not active:
            reason = (
                "Strategy configuration cannot be assigned automatically: the "
                "broker already reports an open position with no v3.5 PRIMARY "
                "ownership record. Close or reconcile the position first."
            )
            self._journal_safe(
                JournalEvent(
                    category="incident",
                    event_type="UNATTRIBUTED_OPEN_POSITION",
                    severity="ERROR",
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    payload={
                        "reason": reason,
                        "current_lots": current_lots,
                        "requested_primary": desired,
                    },
                )
            )
            return reason

        if active and current_lots > 0:
            reason = (
                "Strategy configuration change is blocked while the account "
                "holds an open position in this account/instrument scope."
            )
            self._journal_safe(
                JournalEvent(
                    category="incident",
                    event_type="STRATEGY_SWITCH_BLOCKED",
                    severity="ERROR",
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    payload={
                        "reason": reason,
                        "current_lots": current_lots,
                        "active_primary": active,
                        "requested_primary": desired,
                    },
                )
            )
            return reason

        event_type = "PRIMARY_ASSIGNED" if not active else "PRIMARY_CHANGED"
        desired["assigned_at"] = datetime.now(timezone.utc).isoformat()
        scope["active_primary"] = desired
        self._journal_safe(
            JournalEvent(
                category="strategy_config",
                event_type=event_type,
                severity="INFO",
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                payload={
                    "previous_primary": active,
                    "active_primary": desired,
                    "current_lots": current_lots,
                },
            )
        )
        return None

    def _degraded_result(
        self,
        *,
        now: datetime,
        run_id: str,
        bot_state: dict[str, Any],
        breaker: PersistentCircuitBreaker,
        status: str,
        error: str,
        recommended_wait_seconds: int,
        api_error: TBankAPIError | None = None,
    ) -> dict[str, Any]:
        pending = bot_state.get("pending_order")
        lifecycle = str((pending or {}).get("lifecycle_state") or "")
        definitely_submitted_states = {
            str(OrderLifecycle.ORDER_ACCEPTED),
            str(OrderLifecycle.NEW),
            str(OrderLifecycle.PARTIALLY_FILLED),
            str(OrderLifecycle.FILLED),
            str(OrderLifecycle.RECONCILIATION_REQUIRED),
            str(OrderLifecycle.RISK_ACCOUNTING_REQUIRED),
            str(OrderLifecycle.RISK_ACCOUNTED),
            str(OrderLifecycle.PORTFOLIO_RECONCILED),
        }
        order_was_sent = (
            self._order_submission_attempted_in_cycle
            or lifecycle in definitely_submitted_states
        )
        order_may_have_been_sent = (
            order_was_sent
            or lifecycle == str(OrderLifecycle.ORDER_SUBMITTED)
        )
        result = {
            "status": status,
            "api_state": "DEGRADED",
            "ticker": self.config.ticker,
            "instrument_id": bot_state.get("instrument_id"),
            "primary_strategy": self.primary_strategy,
            "primary_config_hash": self.primary_config_hash,
            "strategy_suite_hash": self.suite_hash,
            "shadow_strategies": list(self.strategy_suite.shadow_strategies),
            # No strategy was evaluated in this failed cycle. Keep current-cycle
            # fields empty and expose the previous successful decision under an
            # explicit last-known name so diagnostics cannot confuse old data
            # with a fresh signal.
            "strategy_decisions": None,
            "strategy_comparison": None,
            "last_known_strategy_decisions": bot_state.get(
                "last_strategy_decisions"
            ),
            "last_known_strategy_comparison": bot_state.get(
                "last_strategy_comparison"
            ),
            "mode": self._mode_name(),
            "last_check_time": now.isoformat(),
            "last_seen_candle": bot_state.get("last_seen_candle"),
            "last_consumed_candle": bot_state.get("last_consumed_candle"),
            "last_order_candle": bot_state.get("last_order_candle"),
            "pending_order": pending,
            "order_was_sent": order_was_sent,
            "order_may_have_been_sent": order_may_have_been_sent,
            "error": error,
            "recommended_wait_seconds": max(5, int(recommended_wait_seconds)),
            "run_id": run_id,
        }
        if api_error:
            result.update(
                {
                    "api_service": api_error.service,
                    "api_method": api_error.method,
                    "api_status_code": api_error.status_code,
                    "tracking_id": api_error.tracking_id,
                }
            )
        result.update(self._health_fields(breaker, now))
        return result

    def _record_circuit_transition_safe(
        self,
        *,
        previous_state: str,
        current_state: str,
        run_id: str,
        breaker: PersistentCircuitBreaker,
        now: datetime,
        reason: str | None = None,
    ) -> None:
        previous = str(previous_state or "CLOSED").upper()
        current = str(current_state or "CLOSED").upper()
        if previous == current:
            return
        severity = "WARNING" if current in {"OPEN", "HALF_OPEN"} else "INFO"
        self._journal_safe(
            JournalEvent(
                category="resilience",
                event_type=f"CIRCUIT_{current}",
                severity=severity,
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status=current.lower(),
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload={
                    "previous_state": previous,
                    "current_state": current,
                    "reason": reason,
                    "health": breaker.snapshot(now),
                },
            )
        )

    def _health_fields(
        self,
        breaker: PersistentCircuitBreaker,
        now: datetime,
    ) -> dict[str, Any]:
        snapshot = breaker.snapshot(now)
        return {
            "circuit_state": snapshot["circuit_state"],
            "consecutive_failures": snapshot["consecutive_failures"],
            "next_retry_at": snapshot["next_retry_at"],
            "retry_after_seconds": snapshot["retry_after_seconds"],
            "last_api_success_at": snapshot["last_success_at"],
            "last_api_failure_at": snapshot["last_failure_at"],
        }

    def _mode_name(self) -> str:
        return "SANDBOX_EXECUTION" if self.allow_execution else "DRY_RUN"

    def status_snapshot(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        root = self._load_state()
        bots = root.setdefault("bots", {})
        self._migrate_legacy_state_key(bots)
        bot_state = bots.setdefault(self.state_key, {})
        self._migrate_bot_state(bot_state)
        breaker = PersistentCircuitBreaker(
            bot_state.setdefault("health", {}),
            self.breaker_config,
        )
        self._migrate_execution_scope(root)
        execution_scope = root.setdefault("execution_scopes", {}).get(
            self.execution_scope_key, {}
        )
        canonical_summary: dict[str, Any] | None = None
        canonical_error: str | None = None
        if self.canonical_portfolio_manager is not None:
            try:
                canonical_state = (
                    self.canonical_portfolio_manager.repository.load_optional(
                        expected_account_id=self.account_id
                    )
                )
                if canonical_state is not None:
                    canonical_summary = {
                        "account_id": canonical_state.account_id,
                        "revision": canonical_state.revision,
                        "decision_checksum": canonical_state.decision_sha256,
                        "snapshot_at": canonical_state.snapshot_at,
                        "freshness": canonical_state.freshness.value,
                        "state_status": canonical_state.state_status,
                        "blocking": canonical_state.blocking,
                    }
            except Exception as exc:
                canonical_error = f"{type(exc).__name__}: {exc}"
        return {
            "ticker": self.config.ticker,
            "mode": self._mode_name(),
            "primary_strategy": self.primary_strategy,
            "primary_config_hash": self.primary_config_hash,
            "strategy_suite_hash": self.suite_hash,
            "shadow_strategies": list(self.strategy_suite.shadow_strategies),
            "last_strategy_decisions": bot_state.get("last_strategy_decisions"),
            "last_strategy_comparison": bot_state.get(
                "last_strategy_comparison"
            ),
            "last_seen_candle": bot_state.get("last_seen_candle"),
            "last_consumed_candle": bot_state.get("last_consumed_candle"),
            "last_dry_run_candle": bot_state.get("last_dry_run_candle"),
            "last_order_candle": bot_state.get("last_order_candle"),
            "last_order_id": bot_state.get("last_order_id"),
            "pending_order": bot_state.get("pending_order"),
            "last_periodic_reconcile_at": bot_state.get(
                "last_periodic_reconcile_at"
            ),
            "last_confirmed_current_lots": bot_state.get(
                "last_confirmed_current_lots"
            ),
            "last_confirmed_target_lots": bot_state.get(
                "last_confirmed_target_lots"
            ),
            "last_risk_block": bot_state.get("last_risk_block"),
            "market_monitor": bot_state.get("market_monitor"),
            "portfolio_preflight_enabled": self.portfolio_preflight_enabled,
            "last_portfolio_preflight": bot_state.get("last_portfolio_preflight"),
            "last_post_fill_canonical": bot_state.get("last_post_fill_canonical"),
            "canonical_portfolio": canonical_summary,
            "canonical_portfolio_error": canonical_error,
            "active_primary_owner": (
                execution_scope.get("active_primary")
                if isinstance(execution_scope, dict)
                else None
            ),
            **self._health_fields(breaker, now),
        }

    def run_forever(self) -> None:
        logger.info(
            "Robot session started: session=%s ticker=%s mode=%s primary=%s interval=%s",
            self.session_id[:8],
            self.config.ticker,
            self._mode_name(),
            self.primary_strategy,
            self.config.candle_interval,
        )
        logger.debug("Robot configuration: %s", asdict(self.config))
        try:
            while True:
                result = self.run_once()
                wait_seconds = int(
                    result.get("recommended_wait_seconds", self.config.poll_seconds)
                )
                time.sleep(max(5, wait_seconds))
        finally:
            self.end_session("run_forever_stopped")

    def _migrate_bot_state(self, bot_state: dict[str, Any]) -> None:
        old = bot_state.get("last_candle_time")
        if old and not bot_state.get("last_consumed_candle"):
            bot_state["last_consumed_candle"] = old
        if old and not bot_state.get("last_decision_candle"):
            bot_state["last_decision_candle"] = old
        bot_state.setdefault("health", {})

    def _migrate_legacy_state_key(self, bots: dict[str, Any]) -> None:
        """Move v3.4 SMA state to a versioned v3.5 key exactly once."""
        if self.state_key in bots or self.primary_strategy != "sma":
            return
        legacy = bots.get(self.legacy_state_key)
        if not isinstance(legacy, dict):
            return
        migrated = bots.pop(self.legacy_state_key)
        migrated["migrated_from_state_key"] = self.legacy_state_key
        migrated["migrated_at"] = datetime.now(timezone.utc).isoformat()
        bots[self.state_key] = migrated

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "version": self.STATE_VERSION,
                "bots": {},
                "strategy_states": {},
                "strategy_configs": {},
                "execution_scopes": {},
            }
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("State root is not an object.")
            state["version"] = max(
                int(state.get("version", 0) or 0),
                self.STATE_VERSION,
            )
            state.setdefault("bots", {})
            state.setdefault("strategy_states", {})
            state.setdefault("strategy_configs", {})
            state.setdefault("execution_scopes", {})
            return state
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                "Файл robot_state.json существует, но не читается. Робот "
                "остановлен, чтобы не потерять pending-order и не создать "
                "дублирующую заявку. Восстановите файл из резервной копии или "
                "убедитесь у брокера, что активных заявок нет, прежде чем "
                "создавать новое состояние."
            ) from exc

    def _handle_state_persistence_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        severity = {
            "STATE_SAVE_RETRY": "WARNING",
            "STATE_SAVE_RECOVERED": "INFO",
            "STATE_SAVE_FAILED": "ERROR",
        }.get(event_type, "INFO")
        self._journal_safe(
            JournalEvent(
                category="state",
                event_type=event_type,
                severity=severity,
                session_id=self.session_id,
                run_id=self._active_run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status=event_type.lower(),
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                payload=payload,
            )
        )
        if event_type == "STATE_SAVE_RETRY":
            logger.warning(
                "STATE_SAVE_RETRY session=%s attempt=%s/%s delay=%.3fs error=%s",
                self.session_id[:8],
                payload.get("attempt"),
                payload.get("max_attempts"),
                float(payload.get("delay_seconds", 0.0) or 0.0),
                payload.get("error"),
            )
        elif event_type == "STATE_SAVE_RECOVERED":
            logger.info(
                "STATE_SAVE_RECOVERED session=%s attempts=%s duration=%.3fs",
                self.session_id[:8],
                payload.get("attempt_count"),
                float(payload.get("duration_seconds", 0.0) or 0.0),
            )
        elif event_type == "STATE_SAVE_FAILED":
            logger.error(
                "STATE_SAVE_FAILED session=%s phase=%s attempts=%s error=%s",
                self.session_id[:8],
                payload.get("phase"),
                payload.get("max_attempts") or payload.get("attempt"),
                payload.get("error"),
            )

    def _save_state(self, state: dict[str, Any]) -> None:
        state["version"] = self.STATE_VERSION
        atomic_write_json(
            self.state_path,
            state,
            event_callback=self._handle_state_persistence_event,
            backup_existing=True,
        )

    def _crash_checkpoint(
        self,
        phase: OrderLifecycle | str,
        *,
        intent: dict[str, Any] | None = None,
    ) -> None:
        self.crash_injector.checkpoint(
            str(phase),
            context={
                "session_id": self.session_id,
                "run_id": self._active_run_id,
                "account_id": self.account_id,
                "instrument_id": self.instrument_id,
                "order_id": (intent or {}).get("order_id"),
                "lifecycle_state": (intent or {}).get("lifecycle_state"),
            },
        )

    @contextmanager
    def _measure_phase(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = max(0.0, time.perf_counter() - started)
            self._cycle_timings[name] = (
                self._cycle_timings.get(name, 0.0) + elapsed
            )

    def _handle_api_telemetry(self, event: dict[str, Any]) -> None:
        """Persist only operationally meaningful API telemetry.

        Successful one-attempt requests remain available in the cycle timing
        fields and DEBUG log.  Retries, recovered retries, failures and slow
        requests become explicit SQLite events.
        """
        event = dict(event)
        self._cycle_api_events.append(event)
        event_type = str(event.get("event_type", "API_EVENT"))
        duration = float(event.get("request_duration_seconds", 0.0) or 0.0)
        should_record = event_type in {
            "API_RETRY_SCHEDULED",
            "API_RETRY_RECOVERED",
            "API_REQUEST_FAILED",
        } or duration >= 5.0
        if not should_record:
            return
        if duration >= 5.0 and event_type == "API_REQUEST_SUCCEEDED":
            event_type = "API_SLOW_REQUEST"
        severity = (
            "ERROR" if event_type == "API_REQUEST_FAILED"
            else "WARNING" if event_type in {"API_RETRY_SCHEDULED", "API_SLOW_REQUEST"}
            else "INFO"
        )
        self._journal_safe(
            JournalEvent(
                category="api",
                event_type=event_type,
                severity=severity,
                session_id=self.session_id,
                run_id=self._active_run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                duration_seconds=duration or None,
                api_attempts=int(event.get("attempt_count", event.get("attempt", 0)) or 0) or None,
                payload=event,
            )
        )

    def _finalize_cycle_observability(
        self,
        result: dict[str, Any],
        *,
        cycle_started_at: datetime,
        cycle_started_perf: float,
    ) -> None:
        for key, value in self._market_cycle_context.items():
            result.setdefault(key, value)
        finished_at = datetime.now(timezone.utc)
        duration = max(0.0, time.perf_counter() - cycle_started_perf)
        api_events = list(self._cycle_api_events)
        terminal_types = {
            "API_REQUEST_SUCCEEDED",
            "API_RETRY_RECOVERED",
            "API_REQUEST_FAILED",
        }
        terminal_events = [
            item
            for item in api_events
            if item.get("event_type") in terminal_types
        ]
        # Use metadata produced by this cycle only.  Falling back to the
        # client's global ``last_response_meta`` can attach a stale request to
        # a cycle that never reached the API (for example state_locked or an
        # open circuit breaker).
        api_meta = dict(terminal_events[-1]) if terminal_events else {}
        retry_count = sum(
            1 for item in api_events
            if item.get("event_type") == "API_RETRY_SCHEDULED"
        )
        api_attempt_count = sum(
            int(item.get("attempt_count", 0) or 0)
            for item in terminal_events
        )
        api_total_duration = sum(
            float(item.get("request_duration_seconds", 0.0) or 0.0)
            for item in terminal_events
        )
        result.setdefault("session_id", self.session_id)
        result.setdefault("cycle_started_at", cycle_started_at.isoformat())
        result["cycle_finished_at"] = finished_at.isoformat()
        result["cycle_duration_seconds"] = round(duration, 6)
        result["last_check_time"] = finished_at.isoformat()
        result.setdefault("run_id", self._active_run_id)
        result["api_retry_count"] = retry_count
        result["api_event_count"] = len(api_events)
        result["api_request_count"] = len(terminal_events)
        result["api_attempt_count"] = api_attempt_count
        result["api_recovered_retry_count"] = sum(
            1
            for item in terminal_events
            if item.get("event_type") == "API_RETRY_RECOVERED"
        )
        result["api_total_duration_seconds"] = round(
            api_total_duration,
            6,
        )
        result["api_request_summary"] = [
            {
                "event_type": item.get("event_type"),
                "service": item.get("service"),
                "method": item.get("method"),
                "status_code": item.get("status_code"),
                "attempt_count": item.get("attempt_count"),
                "retry_count": item.get("retry_count"),
                "request_duration_seconds": item.get(
                    "request_duration_seconds"
                ),
                "request_completed_at": item.get("request_completed_at"),
                "tracking_id": item.get("tracking_id"),
            }
            for item in terminal_events
        ]
        result["timings"] = {
            key: round(value, 6)
            for key, value in sorted(self._cycle_timings.items())
        }
        if api_meta:
            result["api_completed_at"] = api_meta.get("request_completed_at")
            result["api_last_service"] = api_meta.get("service")
            result["api_last_method"] = api_meta.get("method")
            result["api_last_duration_seconds"] = api_meta.get(
                "request_duration_seconds"
            )
            result["api_last_attempt_count"] = api_meta.get("attempt_count")
            result["api_last_retry_count"] = api_meta.get("retry_count")
            result.setdefault("tracking_id", api_meta.get("tracking_id"))
        if duration >= self.config.slow_cycle_seconds:
            self._journal_safe(
                JournalEvent(
                    category="performance",
                    event_type="SLOW_CYCLE",
                    severity="WARNING",
                    session_id=self.session_id,
                    run_id=str(result.get("run_id") or ""),
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    candle_time=result.get("candle_time"),
                    mode=self._mode_name(),
                    status=str(result.get("status", "")),
                    action=result.get("action"),
                    strategy_id=self.primary_strategy,
                    config_hash=self.primary_config_hash,
                    duration_seconds=duration,
                    api_attempts=int(result.get("api_last_attempt_count") or 0) or None,
                    payload={
                        "threshold_seconds": self.config.slow_cycle_seconds,
                        "api_retry_count": retry_count,
                        "api_request_count": len(terminal_events),
                        "api_attempt_count": api_attempt_count,
                        "api_total_duration_seconds": round(
                            api_total_duration,
                            6,
                        ),
                        "api_last_request": api_meta,
                    },
                )
            )

    def _log_cycle_result(self, result: dict[str, Any]) -> None:
        status = str(result.get("status", "unknown"))
        duration = float(result.get("cycle_duration_seconds", 0.0) or 0.0)
        retries = int(result.get("api_retry_count", 0) or 0)
        common = (
            f"session={self.session_id[:8]} run={str(result.get('run_id') or '')[:8]} "
            f"mode={result.get('mode', self._mode_name())} ticker={self.config.ticker} "
            f"status={status} candle={result.get('candle_time') or '—'} "
            f"signal={result.get('signal', '—')} action={result.get('action', '—')} "
            f"position={result.get('current_lots', '—')}→{result.get('target_lots', '—')} "
            f"strategy_target={result.get('strategy_target_lots', result.get('target_lots', '—'))} "
            f"risk={result.get('risk_status', 'NOT_ENFORCED')} "
            f"duration={duration:.3f}s retries={retries}"
        )
        if status in {
            "already_processed",
            "risk_blocked_heartbeat",
            "market_idle",
        }:
            now_mono = time.monotonic()
            heartbeat_interval = (
                self.config.market_idle_heartbeat_seconds
                if status == "market_idle"
                else self.config.heartbeat_log_seconds
            )
            due = (
                result.get("market_idle_transition") == "ENTERED"
                or heartbeat_interval == 0
                or now_mono - self._last_heartbeat_log_monotonic
                >= heartbeat_interval
            )
            if due:
                self._last_heartbeat_log_monotonic = now_mono
                logger.info("HEARTBEAT %s", common)
            else:
                logger.debug("HEARTBEAT %s", common)
        elif status in {"state_save_failed", "failed"}:
            logger.error("CYCLE %s error=%s", common, result.get("error", "—"))
        elif status in {
            "api_degraded",
            "circuit_open",
            "state_locked",
            "risk_blocked",
            "risk_accounting_pending",
            "risk_authorization_missing",
        }:
            details = result.get("error") or result.get("execution_block_reason")
            if not details:
                reasons = result.get("risk_reasons") or []
                details = "; ".join(str(item) for item in reasons) or "—"
            logger.warning("CYCLE %s reason=%s", common, details)
        else:
            logger.info("CYCLE %s", common)
        logger.debug("CYCLE_PAYLOAD session=%s payload=%s", self.session_id, result)

    def end_session(self, reason: str = "stopped") -> None:
        if self._session_closed:
            return
        self._session_closed = True
        finished = datetime.now(timezone.utc)
        duration = max(0.0, (finished - self.session_started_at).total_seconds())
        self._journal_safe(
            JournalEvent(
                category="session",
                event_type="STOPPED",
                severity="INFO",
                session_id=self.session_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode=self._mode_name(),
                status="stopped",
                strategy_id=self.primary_strategy,
                config_hash=self.primary_config_hash,
                duration_seconds=duration,
                payload={"reason": reason, "software_version": __version__},
                timestamp_utc=finished.isoformat(),
            )
        )
        logger.info(
            "Robot session stopped: session=%s reason=%s duration=%.1fs",
            self.session_id[:8], reason, duration,
        )

    def _update_risk_block_tracking(
        self,
        *,
        bot_state: dict[str, Any],
        outcome: RiskRuntimeOutcome,
        candle_time: str | None,
        strategy_target_lots: int,
        current_lots: int,
    ) -> tuple[bool, str | None, int]:
        """Persist/deduplicate one deterministic pre-trade block.

        The key deliberately includes the candle, policy, breach set and
        requested/current targets.  A new candle or any relevant state/policy
        change therefore emits a fresh event, while minute polling of the same
        blocked signal becomes a lightweight heartbeat.  Runtime errors are
        never deduplicated.
        """

        if (
            outcome.assessment is None
            or outcome.status != "BLOCKED"
            or int(strategy_target_lots) == int(current_lots)
        ):
            bot_state.pop("last_risk_block", None)
            return False, None, 0

        breaches = tuple(
            sorted(str(item) for item in outcome.assessment.decision.breaches)
        )
        source = "|".join(
            [
                "risk-block-v1",
                self.account_id,
                str(self.instrument_id or self.config.ticker),
                str(candle_time or "NO_CANDLE"),
                str(outcome.policy_hash or "NO_POLICY"),
                ",".join(breaches),
                str(int(strategy_target_lots)),
                str(int(current_lots)),
            ]
        )
        fingerprint = str(uuid5(NAMESPACE_URL, source))
        now_iso = datetime.now(timezone.utc).isoformat()
        previous = bot_state.get("last_risk_block")
        repeated = bool(
            isinstance(previous, dict)
            and str(previous.get("fingerprint") or "") == fingerprint
        )
        repeat_count = (
            int(previous.get("repeat_count", 0) or 0) + 1
            if repeated and isinstance(previous, dict)
            else 0
        )
        first_seen = (
            str(previous.get("first_seen_at") or now_iso)
            if repeated and isinstance(previous, dict)
            else now_iso
        )
        bot_state["last_risk_block"] = {
            "fingerprint": fingerprint,
            "candle_time": candle_time,
            "policy_hash": outcome.policy_hash,
            "breaches": list(breaches),
            "strategy_target_lots": int(strategy_target_lots),
            "current_lots": int(current_lots),
            "first_seen_at": first_seen,
            "last_seen_at": now_iso,
            "repeat_count": repeat_count,
        }
        return repeated, fingerprint, repeat_count

    def _record_risk_outcome_safe(
        self,
        *,
        outcome: RiskRuntimeOutcome,
        run_id: str,
        candle_time: str | None,
        strategy_target_lots: int,
        current_lots: int,
        suppress_evaluated: bool = False,
    ) -> None:
        if outcome.assessment is not None:
            for event in outcome.assessment.events:
                if suppress_evaluated and event.event_type == "RISK_EVALUATED":
                    continue
                self._journal_safe(
                    JournalEvent(
                        category="risk",
                        event_type=event.event_type,
                        severity=event.severity,
                        session_id=self.session_id,
                        run_id=run_id,
                        account_id=self.account_id,
                        instrument_id=self.instrument_id,
                        ticker=self.config.ticker,
                        candle_time=candle_time,
                        mode=self._mode_name(),
                        status=outcome.status,
                        action=outcome.assessment.decision.approved_action,
                        strategy_id=self.primary_strategy,
                        config_hash=outcome.policy_hash,
                        payload={
                            **event.details,
                            "strategy_target_lots": strategy_target_lots,
                            "approved_target_lots": outcome.approved_target_lots,
                            "current_lots": current_lots,
                            "risk_decision_id": outcome.decision_id,
                            "risk_decision": outcome.assessment.decision.to_dict(),
                        },
                    )
                )
        else:
            self._journal_safe(
                JournalEvent(
                    category="risk",
                    event_type="RISK_RUNTIME_ERROR",
                    severity="ERROR",
                    session_id=self.session_id,
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    candle_time=candle_time,
                    mode=self._mode_name(),
                    status=outcome.status,
                    action="HOLD",
                    strategy_id=self.primary_strategy,
                    payload={
                        "error": outcome.error,
                        "strategy_target_lots": strategy_target_lots,
                        "approved_target_lots": outcome.approved_target_lots,
                        "current_lots": current_lots,
                    },
                )
            )

    def _record_risk_execution_outcome_safe(
        self,
        *,
        outcome: RiskExecutionOutcome,
        intent: dict[str, Any],
        run_id: str,
    ) -> None:
        if outcome.registration is not None:
            for event in outcome.registration.events:
                self._journal_safe(
                    JournalEvent(
                        category="risk",
                        event_type=event.event_type,
                        severity=event.severity,
                        session_id=self.session_id,
                        run_id=run_id,
                        account_id=self.account_id,
                        instrument_id=self.instrument_id,
                        ticker=self.config.ticker,
                        candle_time=intent.get("candle_time"),
                        mode=self._mode_name(),
                        status=outcome.status,
                        action=str(intent.get("action") or "HOLD"),
                        order_id=str(intent.get("order_id") or "") or None,
                        strategy_id=self.primary_strategy,
                        config_hash=outcome.policy_hash,
                        payload={
                            **event.details,
                            "risk_decision_id": outcome.decision_id,
                            "risk_policy_hash": outcome.policy_hash,
                            "risk_execution_id": outcome.execution_id,
                            "price_rub": outcome.price_rub,
                            "price_source": outcome.price_source,
                            "execution_source": outcome.execution_source,
                            "reconciliation_confirmed_at": (
                                outcome.reconciliation_confirmed_at
                            ),
                            "reconciliation_proof": (
                                dict(outcome.reconciliation_proof)
                                if outcome.reconciliation_proof is not None
                                else None
                            ),
                            "duplicate": outcome.duplicate,
                        },
                    )
                )
        else:
            self._journal_safe(
                JournalEvent(
                    category="risk",
                    event_type="RISK_EXECUTION_RUNTIME_ERROR",
                    severity="ERROR",
                    session_id=self.session_id,
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    candle_time=intent.get("candle_time"),
                    mode=self._mode_name(),
                    status=outcome.status,
                    action=str(intent.get("action") or "HOLD"),
                    order_id=str(intent.get("order_id") or "") or None,
                    strategy_id=self.primary_strategy,
                    config_hash=outcome.policy_hash,
                    payload={
                        "error": outcome.error,
                        "risk_decision_id": outcome.decision_id,
                        "risk_policy_hash": outcome.policy_hash,
                        "risk_execution_id": outcome.execution_id,
                        "price_rub": outcome.price_rub,
                        "price_source": outcome.price_source,
                        "execution_source": outcome.execution_source,
                        "reconciliation_confirmed_at": (
                            outcome.reconciliation_confirmed_at
                        ),
                        "reconciliation_proof": (
                            dict(outcome.reconciliation_proof)
                            if outcome.reconciliation_proof is not None
                            else None
                        ),
                    },
                )
            )

    def _record_trade_decision_safe(
        self,
        *,
        run_id: str,
        candle_time: str | None,
        payload: dict[str, Any],
    ) -> None:
        self._journal_safe(
            JournalEvent(
                category="decision",
                event_type="TRADE_DECISION",
                severity="INFO",
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                candle_time=candle_time,
                mode=self._mode_name(),
                status="evaluated",
                action=str(payload.get("action") or "HOLD"),
                strategy_id=str(payload.get("primary_strategy") or self.primary_strategy),
                config_hash=str(payload.get("config_hash") or self.primary_config_hash),
                payload=payload,
            )
        )

    def _record_cycle_safe(self, result: dict[str, Any], run_id: str) -> None:
        if bool(result.get("suppress_cycle_event")):
            return
        status = str(result.get("status", "UNKNOWN"))
        # Cycle rows are deliberately compact.  Full strategy decisions,
        # comparisons and provider responses have dedicated event categories.
        keep_keys = (
            "status",
            "api_state",
            "mode",
            "candle_time",
            "last_seen_candle",
            "last_consumed_candle",
            "signal",
            "target_weight",
            "current_lots",
            "strategy_target_lots",
            "target_lots",
            "risk_enforced",
            "risk_status",
            "risk_policy_hash",
            "risk_decision_id",
            "risk_execution_status",
            "risk_execution",
            "risk_breaches",
            "risk_reasons",
            "risk_block_repeated",
            "risk_block_fingerprint",
            "risk_block_repeat_count",
            "action",
            "intended_action",
            "order_was_sent",
            "order_may_have_been_sent",
            "new_orders_blocked",
            "lots",
            "accepted",
            "executed",
            "executed_lots",
            "position_reconciled",
            "pending_order",
            "execution_block_reason",
            "data_age_seconds",
            "candles_used",
            "effective_lookback_days",
            "circuit_state",
            "consecutive_failures",
            "next_retry_at",
            "recommended_wait_seconds",
            "market_state",
            "market_idle_active",
            "market_idle_transition",
            "market_idle_since",
            "market_idle_duration_seconds",
            "market_idle_reason",
            "market_idle_checks",
            "market_status_checked_at",
            "market_status_text",
            "market_order_availability_key",
            "error",
            "tracking_id",
            "cycle_started_at",
            "cycle_finished_at",
            "cycle_duration_seconds",
            "api_completed_at",
            "api_last_service",
            "api_last_method",
            "api_last_duration_seconds",
            "api_last_attempt_count",
            "api_last_retry_count",
            "api_retry_count",
            "api_event_count",
            "api_request_count",
            "api_attempt_count",
            "api_recovered_retry_count",
            "api_total_duration_seconds",
            "api_request_summary",
            "timings",
            "primary_strategy",
            "primary_config_hash",
            "strategy_suite_hash",
            "reason",
            "state_path",
            "state_save_phase",
            "state_save_attempts",
        )
        payload = {
            key: result.get(key)
            for key in keep_keys
            if key in result and result.get(key) is not None
        }
        severity = (
            "ERROR" if status in {"failed", "state_save_failed"}
            else "WARNING"
            if result.get("api_state") == "DEGRADED"
            or status in {
                "state_locked",
                "submission_failed",
                "order_pending",
                "strategy_switch_blocked",
                "execution_blocked",
                "risk_blocked",
                "circuit_open",
                "api_degraded",
                "state_save_failed",
            }
            else "INFO"
        )
        self._journal_safe(
            JournalEvent(
                category="cycle",
                event_type=status,
                severity=severity,
                session_id=self.session_id,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=(self.instrument_id or result.get("instrument_id")),
                ticker=self.config.ticker,
                order_id=result.get("order_id"),
                candle_time=result.get("candle_time"),
                mode=str(result.get("mode") or self._mode_name()),
                status=status,
                action=result.get("action"),
                strategy_id=str(result.get("primary_strategy") or self.primary_strategy),
                config_hash=str(result.get("primary_config_hash") or self.primary_config_hash),
                duration_seconds=float(result.get("cycle_duration_seconds", 0.0) or 0.0),
                api_attempts=int(result.get("api_attempt_count", 0) or 0) or None,
                payload=payload,
            )
        )

    def _record_order_event_safe(
        self,
        *,
        event_type: OrderLifecycle | str,
        run_id: str,
        candle_time: str | None,
        payload: dict[str, Any],
        order_id: str | None = None,
        severity: str = "INFO",
    ) -> None:
        self._journal_safe(
            JournalEvent(
                category="order",
                event_type=str(event_type),
                severity=severity,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                order_id=order_id,
                candle_time=candle_time,
                payload=payload,
            )
        )

    def _record_order_transition_safe(
        self,
        intent: dict[str, Any],
        run_id: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        merged = {
            "action": intent.get("action"),
            "lots": intent.get("lots"),
            "target_lots": intent.get("target_lots"),
            "current_lots_before": intent.get("current_lots_before"),
            "executed_lots": intent.get("executed_lots"),
            "expected_lots_after": intent.get("expected_lots_after"),
            "actual_lots_after": intent.get("actual_lots_after"),
            "last_known_status": intent.get("last_known_status"),
            "strategy_id": intent.get("strategy_id"),
            "strategy_version": intent.get("strategy_version"),
            "strategy_config_hash": intent.get("strategy_config_hash"),
            "strategy_suite_hash": intent.get("strategy_suite_hash"),
            "strategy_reason": intent.get("strategy_reason"),
            "target_weight": intent.get("target_weight"),
            "stop_level": intent.get("stop_level"),
            "risk_enforced": intent.get("risk_enforced"),
            "risk_mode": intent.get("risk_mode"),
            "risk_status": intent.get("risk_status"),
            "risk_decision_id": intent.get("risk_decision_id"),
            "risk_policy_hash": intent.get("risk_policy_hash"),
            "risk_requested_target_lots": intent.get(
                "risk_requested_target_lots"
            ),
            "risk_approved_target_lots": intent.get(
                "risk_approved_target_lots"
            ),
            "risk_execution_status": intent.get("risk_execution_status"),
            "risk_execution_id": intent.get("risk_execution_id"),
            "risk_execution_price_rub": intent.get(
                "risk_execution_price_rub"
            ),
            "risk_execution_price_source": intent.get(
                "risk_execution_price_source"
            ),
            "risk_execution_source": intent.get("risk_execution_source"),
            "reconciliation_confirmed_at": intent.get(
                "reconciliation_confirmed_at"
            ),
            "reconciliation_proof": intent.get("reconciliation_proof"),
            "reconcile_attempts_used": intent.get("reconcile_attempts_used"),
            "recovery_id": intent.get("recovery_id"),
            "recovery_action": intent.get("recovery_action"),
            "recovery_status": intent.get("recovery_status"),
            "recovery_reason": intent.get("recovery_reason"),
            **(payload or {}),
        }
        lifecycle = str(intent.get("lifecycle_state", "UNKNOWN"))
        severity = (
            "ERROR"
            if lifecycle in {
                str(OrderLifecycle.RECONCILIATION_REQUIRED),
                str(OrderLifecycle.RISK_ACCOUNTING_REQUIRED),
            }
            else "WARNING"
            if lifecycle in {
                str(OrderLifecycle.SUBMISSION_FAILED),
                str(OrderLifecycle.REJECTED),
                str(OrderLifecycle.CANCELLED),
            }
            else "INFO"
        )
        self._record_order_event_safe(
            event_type=lifecycle,
            run_id=run_id,
            candle_time=intent.get("candle_time"),
            order_id=intent.get("order_id"),
            payload=merged,
            severity=severity,
        )

    def _record_incident_safe(
        self,
        *,
        event_type: str,
        severity: str,
        run_id: str,
        bot_state: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        self._journal_safe(
            JournalEvent(
                category="incident",
                event_type=event_type,
                severity=severity,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id or bot_state.get("instrument_id"),
                ticker=self.config.ticker,
                order_id=(bot_state.get("pending_order") or {}).get("order_id"),
                candle_time=(bot_state.get("pending_order") or {}).get(
                    "candle_time"
                ),
                payload=payload,
            )
        )

    def _record_recovery_decision_safe(
        self,
        decision: RecoveryDecision,
        *,
        pending: dict[str, Any],
        run_id: str,
        event_type: str,
        severity: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._journal_safe(
            JournalEvent(
                category="recovery",
                event_type=event_type,
                severity=severity,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                order_id=str(pending.get("order_id") or "") or None,
                candle_time=str(pending.get("candle_time") or "") or None,
                mode=self._mode_name(),
                status=str(decision.action),
                action=str(pending.get("action") or "") or None,
                strategy_id=str(pending.get("strategy_id") or "") or None,
                config_hash=(
                    str(pending.get("strategy_config_hash") or "") or None
                ),
                payload={
                    **decision.to_dict(),
                    "pending_lifecycle_state": pending.get("lifecycle_state"),
                    "risk_decision_id": pending.get("risk_decision_id"),
                    "risk_policy_hash": pending.get("risk_policy_hash"),
                    **(payload or {}),
                },
            )
        )

    def _journal_safe(self, event: JournalEvent) -> None:
        try:
            payload = event.payload or {}
            enriched = replace(
                event,
                session_id=event.session_id or getattr(self, "session_id", None),
                mode=event.mode or self._mode_name(),
                status=event.status or (
                    str(payload.get("status")) if payload.get("status") is not None else None
                ),
                action=event.action or (
                    str(payload.get("action")) if payload.get("action") is not None else None
                ),
                strategy_id=event.strategy_id or (
                    str(payload.get("strategy_id"))
                    if payload.get("strategy_id") is not None
                    else None
                ),
                config_hash=event.config_hash or (
                    str(payload.get("config_hash"))
                    if payload.get("config_hash") is not None
                    else None
                ),
            )
            self.journal.record(enriched)
        except Exception:
            logger.exception("Failed to write structured event journal.")
