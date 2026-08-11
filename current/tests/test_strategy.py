import numpy as np
import pandas as pd
import pytest

from trading_robot.strategy import (
    DonchianBreakoutConfig,
    SmaCrossoverConfig,
    TrendEnsembleConfig,
    VolatilityTargetConfig,
    generate_donchian_signals,
    generate_sma_signals,
    generate_trend_ensemble_signals,
)


def make_ohlc(close: np.ndarray) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=len(close), freq="D")
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
        },
        index=index,
    )


def test_sma_signal_has_expected_columns():
    candles = make_ohlc(np.linspace(100, 200, 100))
    result = generate_sma_signals(
        candles,
        SmaCrossoverConfig(fast_window=5, slow_window=20),
    )
    assert {
        "sma_fast",
        "sma_slow",
        "signal",
        "signal_change",
        "target_weight",
    }.issubset(result.columns)
    assert result["signal"].iloc[-1] == 1
    assert result["target_weight"].iloc[-1] == 1.0


def test_sma_hysteresis_reduces_small_crossovers():
    close = np.r_[np.full(30, 100.0), [100.05, 99.95] * 20]
    candles = make_ohlc(close)
    result = generate_sma_signals(
        candles,
        SmaCrossoverConfig(
            fast_window=2,
            slow_window=10,
            hysteresis_percent=0.01,
        ),
    )
    assert result["signal"].sum() == 0


def test_donchian_uses_prior_channel_and_enters_after_breakout():
    close = np.r_[np.full(65, 100.0), np.linspace(103, 130, 35)]
    candles = make_ohlc(close)
    result = generate_donchian_signals(
        candles,
        DonchianBreakoutConfig(
            entry_window=20,
            exit_window=10,
            atr_window=10,
            trailing_stop_atr=3.0,
            volatility=VolatilityTargetConfig(
                annual_target_volatility=None,
                window=10,
            ),
        ),
    )
    first_entry = result.index[result["signal_change"] == 1][0]
    assert result.loc[first_entry, "close"] > result.loc[first_entry, "donchian_upper"]
    assert result["signal"].iloc[-1] == 1


def test_volatility_target_never_exceeds_max_weight():
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0005, 0.03, 300)
    close = 100 * np.cumprod(1 + returns)
    candles = make_ohlc(close)
    result = generate_sma_signals(
        candles,
        SmaCrossoverConfig(
            fast_window=10,
            slow_window=30,
            volatility=VolatilityTargetConfig(
                annual_target_volatility=0.10,
                window=20,
                annual_periods=252,
                max_weight=0.60,
            ),
        ),
    )
    assert result["target_weight"].between(0, 0.60).all()


def test_trend_ensemble_produces_votes_and_signal():
    candles = make_ohlc(np.linspace(100, 300, 350))
    result = generate_trend_ensemble_signals(
        candles,
        TrendEnsembleConfig(
            sma_fast=20,
            sma_slow=60,
            momentum_window=40,
            breakout_window=50,
            vote_threshold=3,
            volatility=VolatilityTargetConfig(
                annual_target_volatility=None,
                window=20,
            ),
        ),
    )
    assert {"votes", "vote_sma", "vote_momentum", "target_weight"}.issubset(
        result.columns
    )
    assert result["signal"].iloc[-1] == 1


def test_invalid_windows_are_rejected():
    with pytest.raises(ValueError):
        SmaCrossoverConfig(fast_window=20, slow_window=10)
