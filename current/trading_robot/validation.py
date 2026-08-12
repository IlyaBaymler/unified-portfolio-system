from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from typing import Iterable, Literal

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, BacktestResult, calculate_metrics, run_backtest
from .strategy import SmaCrossoverConfig, generate_sma_signals


ObjectiveName = Literal["calmar", "sharpe", "sortino", "cagr"]


@dataclass(slots=True)
class WalkForwardResult:
    selections: pd.DataFrame
    oos_curve: pd.DataFrame
    aggregate_metrics: dict[str, float]
    grid_results: pd.DataFrame


def sma_grid_search(
    candles: pd.DataFrame,
    fast_values: Iterable[int],
    slow_values: Iterable[int],
    strategy_template: SmaCrossoverConfig,
    backtest_config: BacktestConfig,
    objective: ObjectiveName = "calmar",
    minimum_completed_trades: int = 2,
) -> pd.DataFrame:
    """Evaluate a parameter grid on one fixed sample.

    The result includes every attempted parameter pair. Use it to look for a
    stable plateau, not merely the single highest cell.
    """
    rows: list[dict[str, float | int | str]] = []
    for fast, slow in product(sorted(set(fast_values)), sorted(set(slow_values))):
        if fast < 2 or slow <= fast:
            continue
        try:
            config = replace(
                strategy_template,
                fast_window=int(fast),
                slow_window=int(slow),
            )
            signals = generate_sma_signals(candles, config)
            result = run_backtest(signals, backtest_config)
            metric = float(result.metrics.get(objective, np.nan))
            enough_trades = (
                result.metrics["completed_trades"] >= minimum_completed_trades
            )
            score = metric if enough_trades and np.isfinite(metric) else -np.inf
            rows.append(
                {
                    "fast_window": int(fast),
                    "slow_window": int(slow),
                    "status": "ok",
                    "score": score,
                    "raw_score": score,
                    **result.metrics,
                }
            )
        except (ValueError, FloatingPointError) as exc:
            rows.append(
                {
                    "fast_window": int(fast),
                    "slow_window": int(slow),
                    "status": f"error: {exc}",
                    "score": -np.inf,
                    "raw_score": -np.inf,
                }
            )
    if not rows:
        raise ValueError("The parameter grid has no valid fast/slow pairs.")
    frame = pd.DataFrame(rows)
    if "annual_turnover" not in frame.columns:
        frame["annual_turnover"] = np.nan
    frame = _add_neighbourhood_stability(frame)
    return frame.sort_values(
        ["score", "raw_score", "annual_turnover"],
        ascending=[False, False, True],
        na_position="last",
    )


def walk_forward_sma(
    candles: pd.DataFrame,
    fast_values: Iterable[int],
    slow_values: Iterable[int],
    strategy_template: SmaCrossoverConfig,
    backtest_config: BacktestConfig,
    train_bars: int,
    test_bars: int,
    step_bars: int | None = None,
    objective: ObjectiveName = "calmar",
    minimum_completed_trades: int = 2,
) -> WalkForwardResult:
    """Rolling train/select/test validation with non-overlapping OOS segments.

    Parameters are selected on the training window only. The next test window
    is untouched until the selected pair is applied. To keep aggregate returns
    meaningful, ``step_bars`` must be at least ``test_bars``.
    """
    if train_bars < 50:
        raise ValueError("train_bars must be at least 50.")
    if test_bars < 10:
        raise ValueError("test_bars must be at least 10.")
    step = test_bars if step_bars is None else step_bars
    if step < test_bars:
        raise ValueError(
            "Overlapping OOS windows are disabled: step_bars must be >= test_bars."
        )
    if len(candles) < train_bars + test_bars:
        raise ValueError("Not enough candles for one train/test split.")

    fast_values = sorted(set(int(value) for value in fast_values))
    slow_values = sorted(set(int(value) for value in slow_values))
    if not fast_values or not slow_values:
        raise ValueError("Parameter lists must not be empty.")
    maximum_lookback = max(
        max(slow_values),
        strategy_template.volatility.window,
    )

    selections: list[dict[str, object]] = []
    grid_frames: list[pd.DataFrame] = []
    oos_frames: list[pd.DataFrame] = []
    split_id = 0

    train_start = 0
    while train_start + train_bars + test_bars <= len(candles):
        train_end = train_start + train_bars
        test_end = train_end + test_bars
        train = candles.iloc[train_start:train_end].copy()

        grid = sma_grid_search(
            train,
            fast_values,
            slow_values,
            strategy_template,
            backtest_config,
            objective=objective,
            minimum_completed_trades=minimum_completed_trades,
        )
        valid = grid[np.isfinite(pd.to_numeric(grid["score"], errors="coerce"))]
        if valid.empty:
            # Conservative fallback: choose the lowest-turnover valid run rather
            # than silently optimising a sample with zero usable trades.
            valid = grid[grid["status"] == "ok"].sort_values(
                "annual_turnover",
                ascending=True,
                na_position="last",
            )
        if valid.empty:
            raise ValueError(f"No valid strategy in split {split_id}.")
        winner = valid.iloc[0]
        selected_fast = int(winner["fast_window"])
        selected_slow = int(winner["slow_window"])

        warmup_start = max(train_start, train_end - maximum_lookback - 5)
        combined = candles.iloc[warmup_start:test_end].copy()
        selected_config = replace(
            strategy_template,
            fast_window=selected_fast,
            slow_window=selected_slow,
        )
        combined_signals = generate_sma_signals(combined, selected_config)
        test_start_time = candles.index[train_end]
        test_end_time = candles.index[test_end - 1]

        test_start_position = combined_signals.index.get_loc(test_start_time)
        if not isinstance(test_start_position, (int, np.integer)):
            raise ValueError("Unexpected duplicate timestamp in validation data.")
        previous_position = max(test_start_position - 1, 0)
        test_simulation_frame = combined_signals.iloc[previous_position:].copy()
        test_result = run_backtest(test_simulation_frame, backtest_config)
        oos = test_result.curve.loc[
            (test_result.curve.index >= test_start_time)
            & (test_result.curve.index <= test_end_time)
        ].copy()
        oos["split_id"] = split_id
        oos["selected_fast"] = selected_fast
        oos["selected_slow"] = selected_slow
        oos["oos_return"] = oos["strategy_return"]
        oos_frames.append(oos)

        segment_metrics = _metrics_from_returns(
            oos["strategy_return"],
            backtest_config,
            oos.index,
        )
        segment_metrics = _add_execution_metrics(segment_metrics, oos)
        selections.append(
            {
                "split_id": split_id,
                "train_start": train.index[0],
                "train_end": train.index[-1],
                "test_start": test_start_time,
                "test_end": test_end_time,
                "selected_fast": selected_fast,
                "selected_slow": selected_slow,
                "train_score": float(winner.get("score", np.nan)),
                "train_raw_score": float(winner.get("raw_score", np.nan)),
                "train_score_dispersion": float(
                    winner.get("neighbourhood_dispersion", np.nan)
                ),
                "train_calmar": float(winner.get("calmar", np.nan)),
                "train_sharpe": float(winner.get("sharpe", np.nan)),
                "train_turnover": float(winner.get("annual_turnover", np.nan)),
                "test_total_return": segment_metrics["total_return"],
                "test_max_drawdown": segment_metrics["max_drawdown"],
                "test_sharpe": segment_metrics["sharpe"],
                "test_calmar": segment_metrics["calmar"],
            }
        )
        grid = grid.copy()
        grid["split_id"] = split_id
        grid_frames.append(grid)

        split_id += 1
        train_start += step

    if not oos_frames:
        raise ValueError("No walk-forward split was produced.")

    oos_curve = pd.concat(oos_frames).sort_index()
    if oos_curve.index.has_duplicates:
        raise ValueError("OOS windows overlap; aggregate return would be invalid.")
    oos_curve["equity"] = backtest_config.initial_capital * (
        1.0 + oos_curve["oos_return"]
    ).cumprod()
    oos_curve["drawdown"] = (
        oos_curve["equity"] / oos_curve["equity"].cummax() - 1.0
    )
    aggregate_metrics = _metrics_from_returns(
        oos_curve["oos_return"],
        backtest_config,
        oos_curve.index,
    )
    aggregate_metrics = _add_execution_metrics(
        aggregate_metrics,
        oos_curve,
        group_column="split_id",
    )

    return WalkForwardResult(
        selections=pd.DataFrame(selections),
        oos_curve=oos_curve,
        aggregate_metrics=aggregate_metrics,
        grid_results=pd.concat(grid_frames, ignore_index=True),
    )


def _metrics_from_returns(
    returns: pd.Series,
    backtest_config: BacktestConfig,
    index: pd.DatetimeIndex,
) -> dict[str, float]:
    returns = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    synthetic = pd.DataFrame(index=index)
    synthetic["strategy_return"] = returns.to_numpy()
    synthetic["equity"] = backtest_config.initial_capital * (
        1.0 + synthetic["strategy_return"]
    ).cumprod()
    synthetic["drawdown"] = (
        synthetic["equity"] / synthetic["equity"].cummax() - 1.0
    )
    synthetic["turnover"] = 0.0
    synthetic["exposure"] = 0.0
    synthetic["commission_cost"] = 0.0
    synthetic["implicit_cost"] = 0.0
    synthetic["buy_hold_equity"] = backtest_config.initial_capital
    return calculate_metrics(
        synthetic,
        pd.DataFrame(),
        backtest_config,
    )


def _add_execution_metrics(
    metrics: dict[str, float],
    curve: pd.DataFrame,
    *,
    group_column: str | None = None,
) -> dict[str, float]:
    """Restore execution/exposure fields lost by return-only aggregation."""
    enriched = dict(metrics)
    turnover = pd.to_numeric(
        curve.get("turnover", pd.Series(0.0, index=curve.index)),
        errors="coerce",
    ).fillna(0.0)
    exposure = pd.to_numeric(
        curve.get("exposure", pd.Series(0.0, index=curve.index)),
        errors="coerce",
    ).fillna(0.0)

    completed = 0
    if group_column and group_column in curve.columns:
        groups = curve.groupby(group_column, sort=False)
    else:
        groups = [(None, curve)]
    for _, group in groups:
        group_exposure = pd.to_numeric(
            group.get("exposure", pd.Series(0.0, index=group.index)),
            errors="coerce",
        ).fillna(0.0)
        previous = group_exposure.shift(1, fill_value=0.0)
        completed += int(((previous > 1e-12) & (group_exposure <= 1e-12)).sum())

    elapsed_years = 0.0
    if len(curve.index) > 1:
        elapsed_years = (
            (curve.index[-1] - curve.index[0]).total_seconds()
            / (365.25 * 24 * 60 * 60)
        )
    enriched["completed_trades"] = float(completed)
    enriched["order_count"] = float((turnover > 0).sum())
    enriched["average_exposure"] = float(exposure.mean())
    enriched["annual_turnover"] = (
        float(turnover.sum()) / elapsed_years if elapsed_years > 0 else 0.0
    )
    for source, target in (
        ("commission_cost", "total_commission"),
        ("implicit_cost", "total_implicit_cost"),
    ):
        if source in curve.columns:
            enriched[target] = float(
                pd.to_numeric(curve[source], errors="coerce").fillna(0.0).sum()
            )
    return enriched


def _add_neighbourhood_stability(
    frame: pd.DataFrame,
    neighbourhood_size: int = 5,
) -> pd.DataFrame:
    """Replace isolated maxima with a local median plateau score.

    Distances are measured in log-window space, so 10→20 is treated similarly
    to 100→200. Only configurations with a finite raw objective participate.
    """
    result = frame.copy()
    result["neighbourhood_score"] = -np.inf
    result["neighbourhood_dispersion"] = np.nan

    raw = pd.to_numeric(result["raw_score"], errors="coerce")
    valid_mask = np.isfinite(raw.to_numpy(dtype=float))
    valid = result.loc[valid_mask]
    if valid.empty:
        result["score"] = result["neighbourhood_score"]
        return result

    coordinates = np.column_stack(
        [
            np.log(pd.to_numeric(valid["fast_window"]).to_numpy(dtype=float)),
            np.log(pd.to_numeric(valid["slow_window"]).to_numpy(dtype=float)),
        ]
    )
    scale = coordinates.std(axis=0, ddof=0)
    scale[scale <= 1e-12] = 1.0
    coordinates = coordinates / scale
    scores = pd.to_numeric(valid["raw_score"]).to_numpy(dtype=float)
    k = min(max(1, int(neighbourhood_size)), len(valid))

    for position, frame_index in enumerate(valid.index):
        distances = np.square(coordinates - coordinates[position]).sum(axis=1)
        nearest = np.argsort(distances)[:k]
        local_scores = scores[nearest]
        local_median = float(np.median(local_scores))
        local_dispersion = float(
            np.median(np.abs(local_scores - local_median))
        )
        result.at[frame_index, "neighbourhood_score"] = local_median
        result.at[frame_index, "neighbourhood_dispersion"] = local_dispersion

    result["score"] = result["neighbourhood_score"]
    return result
