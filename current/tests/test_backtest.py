import numpy as np
import pandas as pd

from trading_robot.backtest import BacktestConfig, run_backtest


def base_frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=5, freq="D")
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 200.0, 220.0, 242.0],
            "high": [100.0, 100.0, 200.0, 220.0, 242.0],
            "low": [100.0, 100.0, 200.0, 220.0, 242.0],
            "close": [100.0, 100.0, 200.0, 220.0, 242.0],
            "volume": [10_000] * 5,
            "signal": [0, 1, 1, 0, 0],
        },
        index=index,
    )


def zero_cost_config(**kwargs) -> BacktestConfig:
    values = dict(
        initial_capital=1000,
        commission_rate=0,
        slippage_rate=0,
        half_spread_rate=0,
        market_impact_coefficient=0,
        position_fraction=1,
        lot_size=1,
        annual_periods=252,
    )
    values.update(kwargs)
    return BacktestConfig(**values)


def test_signal_is_executed_at_next_open_without_free_gap_profit():
    result = run_backtest(base_frame(), zero_cost_config())
    buy = result.trades[result.trades["action"] == "BUY"].iloc[0]

    # Signal appears at the second close and is filled at the third open, 200.
    assert buy["time"] == base_frame().index[2]
    assert buy["price"] == 200.0
    assert np.isclose(result.curve["equity"].iloc[2], 1000.0)
    # The 100 -> 200 overnight jump is not credited to a strategy that entered at 200.
    assert result.curve["equity"].iloc[2] < 1100.0


def test_extra_execution_delay_moves_fill_one_more_bar():
    result = run_backtest(
        base_frame(),
        zero_cost_config(execution_delay_bars=2),
    )
    buy = result.trades[result.trades["action"] == "BUY"].iloc[0]
    assert buy["time"] == base_frame().index[3]
    assert buy["price"] == 220.0


def test_spread_slippage_and_commission_reduce_equity():
    result = run_backtest(
        base_frame(),
        BacktestConfig(
            initial_capital=1000,
            commission_rate=0.001,
            slippage_rate=0.001,
            half_spread_rate=0.001,
            position_fraction=1,
            lot_size=1,
            annual_periods=252,
        ),
    )
    buy = result.trades[result.trades["action"] == "BUY"].iloc[0]
    assert buy["price"] > 200.0
    assert buy["commission"] > 0
    assert result.curve["equity"].iloc[2] < 1000.0
    assert result.metrics["total_commission"] > 0
    assert result.metrics["total_implicit_cost"] > 0


def test_lot_rounding_and_cash_constraint_are_respected():
    frame = base_frame()
    result = run_backtest(
        frame,
        zero_cost_config(initial_capital=950, lot_size=3),
    )
    buy = result.trades[result.trades["action"] == "BUY"].iloc[0]
    assert buy["units"] % 3 == 0
    assert (result.curve["cash"] >= -1e-8).all()


def test_metrics_include_risk_and_turnover_fields():
    result = run_backtest(base_frame(), zero_cost_config())
    assert {
        "sortino",
        "calmar",
        "annual_turnover",
        "average_exposure",
        "max_drawdown_bars",
        "order_count",
    }.issubset(result.metrics)


def test_no_trade_band_avoids_tiny_target_weight_rebalances():
    index = pd.date_range("2024-01-01", periods=6, freq="D")
    frame = pd.DataFrame(
        {
            "open": [100.0] * 6,
            "high": [100.0] * 6,
            "low": [100.0] * 6,
            "close": [100.0] * 6,
            "volume": [100_000] * 6,
            "signal": [1] * 6,
            "target_weight": [0.50, 0.505, 0.495, 0.504, 0.496, 0.50],
        },
        index=index,
    )
    result = run_backtest(
        frame,
        zero_cost_config(
            initial_capital=100_000,
            position_fraction=1.0,
            rebalance_tolerance=0.01,
        ),
    )

    assert len(result.trades) == 1
    assert result.trades.iloc[0]["action"] == "BUY"
