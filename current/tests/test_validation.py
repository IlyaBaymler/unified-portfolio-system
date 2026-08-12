import numpy as np
import pandas as pd

from trading_robot.backtest import BacktestConfig
from trading_robot.strategy import SmaCrossoverConfig, VolatilityTargetConfig
from trading_robot.validation import sma_grid_search, walk_forward_sma


def make_candles(n: int = 360) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=n, freq="D")
    trend = np.linspace(100, 180, n)
    cycle = 8 * np.sin(np.linspace(0, 12 * np.pi, n))
    close = trend + cycle
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
        },
        index=index,
    )


def test_walk_forward_produces_non_overlapping_oos_segments():
    result = walk_forward_sma(
        make_candles(),
        fast_values=[5, 10],
        slow_values=[20, 30],
        strategy_template=SmaCrossoverConfig(
            fast_window=5,
            slow_window=20,
            volatility=VolatilityTargetConfig(
                annual_target_volatility=None,
                window=10,
            ),
        ),
        backtest_config=BacktestConfig(
            initial_capital=100_000,
            commission_rate=0,
            slippage_rate=0,
            half_spread_rate=0,
            position_fraction=1,
        ),
        train_bars=160,
        test_bars=50,
        step_bars=50,
        minimum_completed_trades=0,
    )
    assert not result.selections.empty
    assert not result.oos_curve.index.has_duplicates
    assert len(result.oos_curve) == len(result.selections) * 50
    assert "total_return" in result.aggregate_metrics
    assert result.aggregate_metrics["order_count"] > 0
    assert result.aggregate_metrics["average_exposure"] > 0
    assert {
        "train_raw_score",
        "train_score_dispersion",
    }.issubset(result.selections.columns)


def test_grid_search_reports_neighbourhood_stability_score():
    candles = make_candles()
    grid = sma_grid_search(
        candles,
        fast_values=[5, 10, 15],
        slow_values=[20, 30, 40],
        strategy_template=SmaCrossoverConfig(
            fast_window=5,
            slow_window=20,
            volatility=VolatilityTargetConfig(
                annual_target_volatility=None,
                window=10,
            ),
        ),
        backtest_config=BacktestConfig(
            initial_capital=100_000,
            commission_rate=0,
            slippage_rate=0,
            half_spread_rate=0,
            position_fraction=1,
        ),
        minimum_completed_trades=0,
    )
    assert {
        "raw_score",
        "neighbourhood_score",
        "neighbourhood_dispersion",
    }.issubset(grid.columns)
    assert np.isfinite(grid.iloc[0]["score"])
