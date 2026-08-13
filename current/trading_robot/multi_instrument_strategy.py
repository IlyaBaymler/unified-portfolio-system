from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import pandas as pd

from .candle_policy import strategy_lookback_days
from .instrument_runtime import InstrumentRuntime
from .multi_instrument_config import MultiInstrumentProfile
from .strategy_runtime import (
    StrategyDecision,
    compare_strategy_decisions,
    evaluate_strategy_suite,
    strategy_suite_from_bot_config,
)


class MultiInstrumentStrategyError(RuntimeError):
    """Raised when read-only multi-instrument strategy evaluation is unsafe."""


class ReadOnlyCandleAPI(Protocol):
    def get_candles(
        self,
        instrument_id: str,
        from_time: datetime,
        to_time: datetime,
        *,
        interval: str,
        limit: int | None,
    ) -> pd.DataFrame: ...


class RuntimeServiceDelegate(Protocol):
    """Risk/reconciliation/market callbacks without an order-submit method."""

    def refresh_market_status(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any: ...

    def refresh_risk(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any: ...

    def reconcile_portfolio(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class StrategyProposal:
    """Read-only Strategy Engine output; never an execution authorization."""

    runtime_key: str
    instrument_id: str
    ticker: str
    candle_interval: str
    candle_time: str
    strategy_profile_hash: str
    primary_strategy: str
    primary_target_lots: int
    decisions: Mapping[str, StrategyDecision]
    comparison: Mapping[str, Any]
    generated_at: str
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.execution_authorized:
            raise MultiInstrumentStrategyError(
                "StrategyProposal cannot authorize broker execution."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_key": self.runtime_key,
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
            "candle_interval": self.candle_interval,
            "candle_time": self.candle_time,
            "strategy_profile_hash": self.strategy_profile_hash,
            "primary_strategy": self.primary_strategy,
            "primary_target_lots": self.primary_target_lots,
            "decisions": {
                key: decision.to_dict() for key, decision in self.decisions.items()
            },
            "comparison": dict(self.comparison),
            "generated_at": self.generated_at,
            "execution_authorized": False,
            "next_gate": "PORTFOLIO_POLICY_RISK_PREFLIGHT_EXECUTION",
        }


class StrategyCandleLoader:
    """Load complete candles with the same policy as the accepted bot path."""

    def __init__(self, api: ReadOnlyCandleAPI) -> None:
        # The API may be the existing Sandbox client, but this adapter stores
        # only its candle method and never exposes the object to Strategy code.
        self._get_candles = api.get_candles

    def load(
        self,
        runtime: InstrumentRuntime,
        profile: MultiInstrumentProfile,
        *,
        now: datetime,
    ) -> pd.DataFrame:
        config = profile.to_bot_config(
            mode="DRY_RUN",
            state_file="robot_state.json",
            journal_file="trading_events.db",
        )
        suite = strategy_suite_from_bot_config(config)
        required_bars = suite.required_bars_for_suite()
        lookback_days = strategy_lookback_days(
            runtime.config.candle_interval,
            required_bars=required_bars,
            requested_days=config.lookback_days,
        )
        candles = self._get_candles(
            runtime.config.instrument_id,
            now - timedelta(days=lookback_days),
            now,
            interval=runtime.config.candle_interval,
            limit=None,
        )
        if not isinstance(candles, pd.DataFrame):
            raise MultiInstrumentStrategyError(
                "Candle source must return a pandas DataFrame."
            )
        if "is_complete" not in candles.columns:
            raise MultiInstrumentStrategyError(
                "Candle source has no is_complete field."
            )
        complete = candles[candles["is_complete"]].copy()
        if len(complete) < required_bars:
            raise MultiInstrumentStrategyError(
                f"{profile.ticker} requires {required_bars} complete candles; "
                f"received {len(complete)}."
            )
        return complete


class MultiInstrumentStrategyAdapter:
    """GlobalScheduler hooks backed by read-only Strategy Engine proposals."""

    def __init__(
        self,
        profiles: Sequence[MultiInstrumentProfile],
        *,
        candle_loader: StrategyCandleLoader,
        services: RuntimeServiceDelegate,
    ) -> None:
        self.candle_loader = candle_loader
        self.services = services
        self._profiles = {profile.instrument_id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise MultiInstrumentStrategyError(
                "Duplicate instrument profile in Strategy adapter."
            )
        self._proposals: dict[str, StrategyProposal] = {}

    @property
    def proposals(self) -> tuple[StrategyProposal, ...]:
        return tuple(self._proposals[key] for key in sorted(self._proposals))

    def latest_proposal(self, runtime_key: str) -> StrategyProposal | None:
        return self._proposals.get(runtime_key)

    def refresh_market_status(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any:
        return self.services.refresh_market_status(runtime, now)

    def refresh_risk(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any:
        return self.services.refresh_risk(runtime, now)

    def reconcile_portfolio(
        self,
        runtime: InstrumentRuntime,
        now: datetime,
    ) -> Any:
        return self.services.reconcile_portfolio(runtime, now)

    def evaluate_closed_candle(
        self,
        runtime: InstrumentRuntime,
        candle_time: datetime,
        now: datetime,
    ) -> StrategyProposal:
        profile = self._profile_for(runtime)
        complete = self.candle_loader.load(runtime, profile, now=now)
        latest = _utc_timestamp(complete.index[-1])
        expected = _utc_datetime(candle_time)
        if latest != expected:
            raise MultiInstrumentStrategyError(
                f"Scheduler candle {expected.isoformat()} does not match latest "
                f"complete {profile.ticker} candle {latest.isoformat()}."
            )
        proposal = build_strategy_proposal(
            runtime,
            profile,
            complete,
            now=now,
        )
        self._proposals[runtime.runtime_key] = proposal
        return proposal

    def _profile_for(self, runtime: InstrumentRuntime) -> MultiInstrumentProfile:
        try:
            profile = self._profiles[runtime.config.instrument_id]
        except KeyError as exc:
            raise MultiInstrumentStrategyError(
                f"No Strategy profile for {runtime.config.instrument_id}."
            ) from exc
        desired = profile.to_runtime_config(runtime.config.account_id)
        if desired.to_dict() != runtime.config.to_dict():
            raise MultiInstrumentStrategyError(
                f"Runtime/profile identity mismatch for {profile.ticker}."
            )
        return profile


def build_strategy_proposal(
    runtime: InstrumentRuntime,
    profile: MultiInstrumentProfile,
    complete_candles: pd.DataFrame,
    *,
    now: datetime,
) -> StrategyProposal:
    """Evaluate a preloaded complete frame without exposing broker execution."""

    if not isinstance(complete_candles, pd.DataFrame) or complete_candles.empty:
        raise MultiInstrumentStrategyError(
            "Complete candle frame must be a non-empty DataFrame."
        )
    desired = profile.to_runtime_config(runtime.config.account_id)
    if desired.to_dict() != runtime.config.to_dict():
        raise MultiInstrumentStrategyError(
            f"Runtime/profile identity mismatch for {profile.ticker}."
        )
    config = profile.to_bot_config(
        mode="DRY_RUN",
        state_file="robot_state.json",
        journal_file="trading_events.db",
    )
    suite = strategy_suite_from_bot_config(config)
    decisions = evaluate_strategy_suite(complete_candles, suite)
    primary = decisions[suite.primary_strategy]
    latest = _utc_timestamp(complete_candles.index[-1])
    if _utc_datetime(datetime.fromisoformat(primary.candle_time)) != latest:
        raise MultiInstrumentStrategyError(
            "Strategy decision candle does not match the complete frame."
        )
    return StrategyProposal(
        runtime_key=runtime.runtime_key,
        instrument_id=runtime.config.instrument_id,
        ticker=runtime.config.ticker,
        candle_interval=runtime.config.candle_interval,
        candle_time=primary.candle_time,
        strategy_profile_hash=profile.strategy_profile_hash,
        primary_strategy=suite.primary_strategy,
        primary_target_lots=primary.target_lots,
        decisions=decisions,
        comparison=compare_strategy_decisions(decisions, suite.primary_strategy),
        generated_at=_utc_datetime(now).isoformat(),
    )


def _utc_timestamp(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MultiInstrumentStrategyError("Runtime timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc)
