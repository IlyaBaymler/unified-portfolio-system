from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, run_backtest


@dataclass(slots=True)
class BootstrapResult:
    distribution: pd.DataFrame
    quantiles: pd.DataFrame


def moving_block_bootstrap(
    returns: pd.Series,
    *,
    simulations: int = 1_000,
    block_size: int = 5,
    initial_capital: float = 1_000_000.0,
    seed: int = 42,
) -> BootstrapResult:
    """Resample return blocks to stress path dependence.

    This does not prove future performance. It asks a narrower question: how
    sensitive are terminal wealth and drawdown to a different ordering of
    return blocks with approximately preserved short-range dependence?
    """
    values = pd.to_numeric(returns, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) < 20:
        raise ValueError("At least 20 return observations are required.")
    if simulations < 100:
        raise ValueError("Use at least 100 bootstrap simulations.")
    if not 1 <= block_size <= len(values):
        raise ValueError("block_size must be between 1 and the sample length.")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive.")

    rng = np.random.default_rng(seed)
    maximum_start = len(values) - block_size
    records: list[dict[str, float]] = []

    for simulation in range(simulations):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = int(rng.integers(0, maximum_start + 1))
            sampled.extend(values[start : start + block_size].tolist())
        path_returns = np.asarray(sampled[: len(values)], dtype=float)
        equity = initial_capital * np.cumprod(1.0 + path_returns)
        running_peak = np.maximum.accumulate(equity)
        drawdown = equity / running_peak - 1.0
        records.append(
            {
                "simulation": float(simulation),
                "ending_capital": float(equity[-1]),
                "total_return": float(equity[-1] / initial_capital - 1.0),
                "max_drawdown": float(drawdown.min()),
            }
        )

    distribution = pd.DataFrame(records)
    quantile_levels = [0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99]
    quantiles = distribution[
        ["ending_capital", "total_return", "max_drawdown"]
    ].quantile(quantile_levels)
    quantiles.index.name = "quantile"
    return BootstrapResult(distribution=distribution, quantiles=quantiles)


def execution_stress_test(
    signal_frame: pd.DataFrame,
    base_config: BacktestConfig,
    *,
    cost_multipliers: Iterable[float] = (1.0, 2.0, 3.0),
    delays: Iterable[int] = (1, 2),
) -> pd.DataFrame:
    """Re-run one fixed strategy under harsher costs and slower execution."""
    rows: list[dict[str, float]] = []
    for multiplier in cost_multipliers:
        if multiplier <= 0:
            raise ValueError("Cost multipliers must be positive.")
        for delay in delays:
            config = replace(
                base_config,
                commission_rate=base_config.commission_rate * multiplier,
                slippage_rate=base_config.slippage_rate * multiplier,
                half_spread_rate=base_config.half_spread_rate * multiplier,
                market_impact_coefficient=(
                    base_config.market_impact_coefficient * multiplier
                ),
                execution_delay_bars=int(delay),
            )
            result = run_backtest(signal_frame, config)
            rows.append(
                {
                    "cost_multiplier": float(multiplier),
                    "execution_delay_bars": float(delay),
                    "total_return": result.metrics["total_return"],
                    "cagr": result.metrics["cagr"],
                    "max_drawdown": result.metrics["max_drawdown"],
                    "sharpe": result.metrics["sharpe"],
                    "calmar": result.metrics["calmar"],
                    "annual_turnover": result.metrics["annual_turnover"],
                    "total_cost": (
                        result.metrics["total_commission"]
                        + result.metrics["total_implicit_cost"]
                    ),
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["cost_multiplier", "execution_delay_bars"]
    )
