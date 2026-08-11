from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Literal

import numpy as np
import pandas as pd


StrategyName = Literal["sma", "donchian", "ensemble"]


@dataclass(frozen=True, slots=True)
class VolatilityTargetConfig:
    """Scale a directional signal by recent realised volatility.

    ``annual_target_volatility`` is a risk budget, not a return forecast.
    Set it to ``None`` to disable volatility scaling.
    """

    annual_target_volatility: float | None = None
    window: int = 20
    annual_periods: int = 252
    max_weight: float = 1.0
    min_annual_volatility: float = 0.03

    def __post_init__(self) -> None:
        if self.annual_target_volatility is not None:
            if not 0 < self.annual_target_volatility <= 2:
                raise ValueError(
                    "annual_target_volatility must be in (0, 2] or None."
                )
        if self.window < 5:
            raise ValueError("Volatility window must be at least 5 bars.")
        if self.annual_periods <= 0:
            raise ValueError("annual_periods must be positive.")
        if not 0 < self.max_weight <= 1:
            raise ValueError("max_weight must be in (0, 1].")
        if self.min_annual_volatility <= 0:
            raise ValueError("min_annual_volatility must be positive.")


@dataclass(frozen=True, slots=True)
class SmaCrossoverConfig:
    """Long-only moving-average crossover configuration.

    ``hysteresis_percent`` introduces a neutral band around the crossover and
    usually reduces whipsaw and turnover. It must be validated out of sample.
    """

    fast_window: int = 20
    slow_window: int = 50
    hysteresis_percent: float = 0.0
    volatility: VolatilityTargetConfig = VolatilityTargetConfig()

    def __post_init__(self) -> None:
        if self.fast_window < 2:
            raise ValueError("fast_window must be at least 2.")
        if self.slow_window <= self.fast_window:
            raise ValueError("slow_window must be greater than fast_window.")
        if not 0 <= self.hysteresis_percent <= 0.20:
            raise ValueError("hysteresis_percent must be in [0, 0.20].")


@dataclass(frozen=True, slots=True)
class DonchianBreakoutConfig:
    """Long-only channel breakout with a channel exit and ATR trailing stop."""

    entry_window: int = 55
    exit_window: int = 20
    atr_window: int = 20
    trailing_stop_atr: float = 3.0
    volatility: VolatilityTargetConfig = VolatilityTargetConfig(
        annual_target_volatility=0.15,
        window=20,
        annual_periods=252,
        max_weight=1.0,
    )

    def __post_init__(self) -> None:
        if self.entry_window < 5:
            raise ValueError("entry_window must be at least 5.")
        if not 2 <= self.exit_window < self.entry_window:
            raise ValueError("exit_window must be in [2, entry_window).")
        if self.atr_window < 5:
            raise ValueError("atr_window must be at least 5.")
        if self.trailing_stop_atr <= 0:
            raise ValueError("trailing_stop_atr must be positive.")


@dataclass(frozen=True, slots=True)
class TrendEnsembleConfig:
    """Ensemble of slow trend signals rather than one optimised parameter pair."""

    sma_fast: int = 50
    sma_slow: int = 200
    momentum_window: int = 126
    breakout_window: int = 100
    vote_threshold: int = 3
    volatility: VolatilityTargetConfig = VolatilityTargetConfig(
        annual_target_volatility=0.15,
        window=20,
        annual_periods=252,
        max_weight=1.0,
    )

    def __post_init__(self) -> None:
        if self.sma_fast < 2 or self.sma_slow <= self.sma_fast:
            raise ValueError("Require 2 <= sma_fast < sma_slow.")
        if self.momentum_window < 5 or self.breakout_window < 5:
            raise ValueError("Momentum and breakout windows must be at least 5.")
        if not 1 <= self.vote_threshold <= 4:
            raise ValueError("vote_threshold must be in [1, 4].")


def _validate_candles(candles: pd.DataFrame, required: set[str]) -> None:
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"Candles miss columns: {sorted(missing)}")
    if not isinstance(candles.index, pd.DatetimeIndex):
        raise ValueError("Candles must use a DatetimeIndex.")
    if not candles.index.is_monotonic_increasing:
        raise ValueError("Candles must be sorted by time.")
    if candles.index.has_duplicates:
        raise ValueError("Candles contain duplicate timestamps.")


def _atr(candles: pd.DataFrame, window: int) -> pd.Series:
    previous_close = candles["close"].shift(1)
    true_range = pd.concat(
        [
            candles["high"] - candles["low"],
            (candles["high"] - previous_close).abs(),
            (candles["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _apply_volatility_target(
    close: pd.Series,
    raw_signal: pd.Series,
    config: VolatilityTargetConfig,
) -> tuple[pd.Series, pd.Series]:
    period_returns = close.pct_change()
    annual_volatility = (
        period_returns.rolling(config.window, min_periods=config.window).std(ddof=0)
        * sqrt(config.annual_periods)
    )

    if config.annual_target_volatility is None:
        scale = pd.Series(config.max_weight, index=close.index, dtype=float)
    else:
        denominator = annual_volatility.clip(lower=config.min_annual_volatility)
        scale = (config.annual_target_volatility / denominator).clip(
            lower=0.0,
            upper=config.max_weight,
        )
        scale = scale.where(annual_volatility.notna(), 0.0)

    target_weight = raw_signal.astype(float) * scale
    return target_weight.clip(0.0, config.max_weight), annual_volatility


def generate_sma_signals(
    candles: pd.DataFrame,
    config: SmaCrossoverConfig,
) -> pd.DataFrame:
    """Generate a long/flat SMA signal at the bar close.

    A backtest or broker adapter must execute this decision no earlier than the
    next tradable quote. The function itself never assumes a fill price.
    """
    _validate_candles(candles, {"close"})
    if len(candles) < max(config.slow_window, config.volatility.window):
        raise ValueError(
            "Insufficient candles for the selected SMA and volatility windows."
        )

    result = candles.copy()
    result["sma_fast"] = result["close"].rolling(
        config.fast_window,
        min_periods=config.fast_window,
    ).mean()
    result["sma_slow"] = result["close"].rolling(
        config.slow_window,
        min_periods=config.slow_window,
    ).mean()

    band = config.hysteresis_percent
    enter = result["sma_fast"] > result["sma_slow"] * (1.0 + band)
    exit_ = result["sma_fast"] < result["sma_slow"] * (1.0 - band)

    state = 0
    raw: list[int] = []
    for is_enter, is_exit, is_ready in zip(
        enter.fillna(False),
        exit_.fillna(False),
        result["sma_slow"].notna(),
        strict=True,
    ):
        if not is_ready:
            state = 0
        elif state == 0 and bool(is_enter):
            state = 1
        elif state == 1 and bool(is_exit):
            state = 0
        raw.append(state)

    result["raw_signal"] = pd.Series(raw, index=result.index, dtype=int)
    result["signal"] = result["raw_signal"]
    result["target_weight"], result["annual_volatility"] = (
        _apply_volatility_target(
            result["close"],
            result["raw_signal"],
            config.volatility,
        )
    )
    result["signal_change"] = result["signal"].diff().fillna(0).astype(int)
    return result


def generate_donchian_signals(
    candles: pd.DataFrame,
    config: DonchianBreakoutConfig,
) -> pd.DataFrame:
    """Generate a channel-breakout trend signal without same-bar look-ahead."""
    _validate_candles(candles, {"high", "low", "close"})
    required_bars = max(
        config.entry_window,
        config.exit_window,
        config.atr_window,
        config.volatility.window,
    ) + 2
    if len(candles) < required_bars:
        raise ValueError(f"At least {required_bars} candles are required.")

    result = candles.copy()
    result["donchian_upper"] = (
        result["high"].rolling(config.entry_window).max().shift(1)
    )
    result["donchian_lower"] = (
        result["low"].rolling(config.exit_window).min().shift(1)
    )
    result["atr"] = _atr(result, config.atr_window)

    state = 0
    highest_close = np.nan
    trailing_stop = np.nan
    raw_signal: list[int] = []
    stop_values: list[float] = []

    for _, row in result.iterrows():
        close = float(row["close"])
        upper = row["donchian_upper"]
        lower = row["donchian_lower"]
        atr = row["atr"]

        if state == 0:
            highest_close = np.nan
            trailing_stop = np.nan
            if pd.notna(upper) and close > float(upper):
                state = 1
                highest_close = close
                if pd.notna(atr):
                    trailing_stop = close - config.trailing_stop_atr * float(atr)
        else:
            highest_close = max(float(highest_close), close)
            if pd.notna(atr):
                trailing_stop = highest_close - config.trailing_stop_atr * float(atr)
            channel_exit = pd.notna(lower) and close < float(lower)
            stop_exit = pd.notna(trailing_stop) and close < float(trailing_stop)
            if channel_exit or stop_exit:
                state = 0
                highest_close = np.nan
                trailing_stop = np.nan

        raw_signal.append(state)
        stop_values.append(float(trailing_stop) if pd.notna(trailing_stop) else np.nan)

    result["trailing_stop"] = stop_values
    result["raw_signal"] = pd.Series(raw_signal, index=result.index, dtype=int)
    result["signal"] = result["raw_signal"]
    result["target_weight"], result["annual_volatility"] = (
        _apply_volatility_target(
            result["close"],
            result["raw_signal"],
            config.volatility,
        )
    )
    result["signal_change"] = result["signal"].diff().fillna(0).astype(int)
    return result


def generate_trend_ensemble_signals(
    candles: pd.DataFrame,
    config: TrendEnsembleConfig,
) -> pd.DataFrame:
    """Combine four slow trend votes and scale exposure by volatility."""
    _validate_candles(candles, {"high", "low", "close"})
    required_bars = max(
        config.sma_slow,
        config.momentum_window,
        config.breakout_window,
        config.volatility.window,
    ) + 2
    if len(candles) < required_bars:
        raise ValueError(f"At least {required_bars} candles are required.")

    result = candles.copy()
    result["sma_fast"] = result["close"].rolling(config.sma_fast).mean()
    result["sma_slow"] = result["close"].rolling(config.sma_slow).mean()
    result["ema_slow"] = result["close"].ewm(
        span=config.sma_slow,
        adjust=False,
        min_periods=config.sma_slow,
    ).mean()
    result["momentum"] = result["close"] / result["close"].shift(
        config.momentum_window
    ) - 1.0
    result["breakout_level"] = (
        result["high"].rolling(config.breakout_window).max().shift(1)
    )

    result["vote_sma"] = (result["sma_fast"] > result["sma_slow"]).astype(int)
    result["vote_ema"] = (result["close"] > result["ema_slow"]).astype(int)
    result["vote_momentum"] = (result["momentum"] > 0).astype(int)
    result["vote_breakout"] = (
        result["close"] > result["breakout_level"]
    ).astype(int)
    result["votes"] = result[
        ["vote_sma", "vote_ema", "vote_momentum", "vote_breakout"]
    ].sum(axis=1)

    ready = result[
        ["sma_slow", "ema_slow", "momentum", "breakout_level"]
    ].notna().all(axis=1)
    result["raw_signal"] = (
        (result["votes"] >= config.vote_threshold) & ready
    ).astype(int)
    result["signal"] = result["raw_signal"]
    result["target_weight"], result["annual_volatility"] = (
        _apply_volatility_target(
            result["close"],
            result["raw_signal"],
            config.volatility,
        )
    )
    result["signal_change"] = result["signal"].diff().fillna(0).astype(int)
    return result


def generate_strategy_signals(
    candles: pd.DataFrame,
    strategy: StrategyName,
    config: SmaCrossoverConfig | DonchianBreakoutConfig | TrendEnsembleConfig,
) -> pd.DataFrame:
    if strategy == "sma" and isinstance(config, SmaCrossoverConfig):
        return generate_sma_signals(candles, config)
    if strategy == "donchian" and isinstance(config, DonchianBreakoutConfig):
        return generate_donchian_signals(candles, config)
    if strategy == "ensemble" and isinstance(config, TrendEnsembleConfig):
        return generate_trend_ensemble_signals(candles, config)
    raise TypeError(f"Strategy {strategy!r} does not match config type {type(config)!r}.")
