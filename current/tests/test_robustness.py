import numpy as np
import pandas as pd

from trading_robot.robustness import moving_block_bootstrap


def test_moving_block_bootstrap_returns_requested_distribution():
    rng = np.random.default_rng(42)
    returns = pd.Series(rng.normal(0.0005, 0.01, 200))
    result = moving_block_bootstrap(
        returns,
        simulations=200,
        block_size=5,
        initial_capital=100_000,
        seed=1,
    )
    assert len(result.distribution) == 200
    assert {"ending_capital", "total_return", "max_drawdown"}.issubset(
        result.distribution.columns
    )
    assert 0.05 in result.quantiles.index
