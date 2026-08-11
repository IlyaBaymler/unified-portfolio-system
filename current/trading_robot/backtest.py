from __future__ import annotations

from dataclasses import dataclass
from math import floor, sqrt

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Execution-aware long-only backtest settings.

    Signals are observed at bar close and executed at the *next* bar open.
    The cost model deliberately separates commission, spread, slippage and a
    simple square-root participation impact term.
    """

    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.0005
    slippage_rate: float = 0.0005
    half_spread_rate: float = 0.0002
    market_impact_coefficient: float = 0.0
    position_fraction: float = 0.10
    lot_size: int = 1
    allow_fractional: bool = False
    min_trade_value: float = 0.0
    rebalance_tolerance: float = 0.01
    annual_periods: int | None = None
    risk_free_rate: float = 0.0
    execution_delay_bars: int = 1

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive.")
        for name, value in (
            ("commission_rate", self.commission_rate),
            ("slippage_rate", self.slippage_rate),
            ("half_spread_rate", self.half_spread_rate),
            ("market_impact_coefficient", self.market_impact_coefficient),
            ("min_trade_value", self.min_trade_value),
            ("rebalance_tolerance", self.rebalance_tolerance),
        ):
            if value < 0:
                raise ValueError(f"{name} must not be negative.")
        if not 0 < self.position_fraction <= 1:
            raise ValueError("position_fraction must be in (0, 1].")
        if self.rebalance_tolerance >= 1:
            raise ValueError("rebalance_tolerance must be below 1.")
        if self.lot_size < 1:
            raise ValueError("lot_size must be a positive integer.")
        if self.annual_periods is not None and self.annual_periods <= 0:
            raise ValueError("annual_periods must be positive or None.")
        if self.risk_free_rate < -0.99:
            raise ValueError("risk_free_rate is implausibly low.")
        if self.execution_delay_bars < 1:
            raise ValueError("execution_delay_bars must be at least 1.")


@dataclass(slots=True)
class BacktestResult:
    curve: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, float]


def run_backtest(
    signal_frame: pd.DataFrame,
    config: BacktestConfig,
) -> BacktestResult:
    """Run a next-open, cash-and-position simulation.

    ``signal`` is interpreted as long/flat. If ``target_weight`` exists, it is
    used as a dynamic weight in [0, 1] and then capped by ``position_fraction``.
    No shorting or leverage is allowed.
    """
    required = {"open", "close", "signal"}
    missing = required.difference(signal_frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    if not isinstance(signal_frame.index, pd.DatetimeIndex):
        raise ValueError("signal_frame must use a DatetimeIndex.")
    if not signal_frame.index.is_monotonic_increasing:
        raise ValueError("signal_frame must be sorted by timestamp.")
    if signal_frame.index.has_duplicates:
        raise ValueError("signal_frame contains duplicate timestamps.")
    if len(signal_frame) < 2:
        raise ValueError("At least two bars are required.")

    curve = signal_frame.copy()
    for column in ("open", "close"):
        curve[column] = pd.to_numeric(curve[column], errors="coerce")
    if (curve[["open", "close"]] <= 0).any().any() or curve[
        ["open", "close"]
    ].isna().any().any():
        raise ValueError("Open and close prices must be finite and positive.")

    if "target_weight" in curve.columns:
        strategy_weight = pd.to_numeric(
            curve["target_weight"], errors="coerce"
        ).fillna(0.0)
    else:
        strategy_weight = pd.to_numeric(curve["signal"], errors="coerce").fillna(0.0)
    strategy_weight = strategy_weight.clip(0.0, 1.0)

    # A close-time decision at t is tradable no earlier than open t+1.
    curve["decision_weight"] = strategy_weight
    curve["target_weight_executed"] = (
        strategy_weight.shift(config.execution_delay_bars).fillna(0.0)
        * config.position_fraction
    ).clip(0.0, 1.0)

    cash = float(config.initial_capital)
    units = 0.0
    previous_close_equity = float(config.initial_capital)
    average_cost_per_unit = 0.0

    rows: list[dict[str, float]] = []
    orders: list[dict[str, object]] = []

    for timestamp, bar in curve.iterrows():
        open_price = float(bar["open"])
        close_price = float(bar["close"])
        equity_at_open = cash + units * open_price
        target_weight = float(bar["target_weight_executed"])
        current_weight_at_open = (
            units * open_price / equity_at_open if equity_at_open > 0 else 0.0
        )
        is_entry = units <= 1e-12 and target_weight > 0
        is_exit = units > 1e-12 and target_weight <= 0
        outside_no_trade_band = (
            abs(target_weight - current_weight_at_open)
            >= config.rebalance_tolerance
        )

        if is_entry or is_exit or outside_no_trade_band:
            target_value = max(equity_at_open, 0.0) * target_weight
            desired_units = _round_units(
                target_value / open_price,
                lot_size=config.lot_size,
                allow_fractional=config.allow_fractional,
            )
            desired_units = max(0.0, desired_units)
        else:
            desired_units = units
        delta_units = desired_units - units

        explicit_commission = 0.0
        implicit_cost = 0.0
        turnover = 0.0
        execution_price = np.nan
        realised_pnl = np.nan
        trade_return = np.nan

        preliminary_notional = abs(delta_units) * open_price
        if preliminary_notional < config.min_trade_value:
            delta_units = 0.0

        if abs(delta_units) > 1e-12:
            side = 1.0 if delta_units > 0 else -1.0
            volume = float(bar.get("volume", np.nan))
            participation = 0.0
            if np.isfinite(volume) and volume > 0:
                participation = min(abs(delta_units) / volume, 1.0)
            impact = config.market_impact_coefficient * sqrt(participation)
            adverse_rate = (
                config.slippage_rate + config.half_spread_rate + impact
            )
            execution_price = open_price * (1.0 + side * adverse_rate)

            if delta_units > 0:
                affordable = cash / (
                    execution_price * (1.0 + config.commission_rate)
                )
                affordable_units = _round_units(
                    affordable,
                    lot_size=config.lot_size,
                    allow_fractional=config.allow_fractional,
                )
                delta_units = min(delta_units, max(0.0, affordable_units))
            else:
                delta_units = max(delta_units, -units)

            if abs(delta_units) > 1e-12:
                traded_value = abs(delta_units) * execution_price
                explicit_commission = traded_value * config.commission_rate
                implicit_cost = abs(delta_units) * abs(execution_price - open_price)
                turnover = (
                    abs(delta_units) * open_price / equity_at_open
                    if equity_at_open > 0
                    else 0.0
                )

                action = "BUY" if delta_units > 0 else "SELL"
                units_before = units

                if delta_units > 0:
                    old_cost = units * average_cost_per_unit
                    new_cost = delta_units * execution_price + explicit_commission
                    units += delta_units
                    average_cost_per_unit = (
                        (old_cost + new_cost) / units if units > 0 else 0.0
                    )
                else:
                    sold_units = abs(delta_units)
                    proceeds = sold_units * execution_price - explicit_commission
                    cost_basis = sold_units * average_cost_per_unit
                    realised_pnl = proceeds - cost_basis
                    trade_return = realised_pnl / cost_basis if cost_basis > 0 else np.nan
                    units -= sold_units
                    if units <= 1e-12:
                        units = 0.0
                        average_cost_per_unit = 0.0

                # Positive delta consumes cash; negative delta adds cash.
                cash -= delta_units * execution_price + explicit_commission
                if abs(cash) < 1e-8:
                    cash = 0.0

                orders.append(
                    {
                        "time": timestamp,
                        "action": action,
                        "units": float(abs(delta_units)),
                        "lots": float(abs(delta_units) / config.lot_size),
                        "price": float(execution_price),
                        "reference_open": open_price,
                        "commission": float(explicit_commission),
                        "implicit_cost": float(implicit_cost),
                        "turnover": float(turnover),
                        "target_weight": target_weight,
                        "position_fraction": target_weight,
                        "units_before": float(units_before),
                        "units_after": float(units),
                        "realised_pnl": float(realised_pnl)
                        if np.isfinite(realised_pnl)
                        else np.nan,
                        "trade_return": float(trade_return)
                        if np.isfinite(trade_return)
                        else np.nan,
                    }
                )

        equity_close = cash + units * close_price
        strategy_return = equity_close / previous_close_equity - 1.0
        actual_weight = units * close_price / equity_close if equity_close > 0 else 0.0
        rows.append(
            {
                "cash": cash,
                "position_units": units,
                "position": actual_weight,
                "exposure": actual_weight,
                "execution_price": execution_price,
                "commission_cost": explicit_commission,
                "implicit_cost": implicit_cost,
                "cost": explicit_commission + implicit_cost,
                "turnover": turnover,
                "strategy_return": strategy_return,
                "equity": equity_close,
            }
        )
        previous_close_equity = equity_close

    simulation = pd.DataFrame(rows, index=curve.index)
    for column in simulation.columns:
        curve[column] = simulation[column]

    first_open = float(curve["open"].iloc[0])
    curve["asset_return"] = curve["close"].pct_change().fillna(0.0)
    curve["buy_hold_equity"] = config.initial_capital * (
        1.0
        - config.position_fraction
        + config.position_fraction * curve["close"] / first_open
    )
    peak = curve["equity"].cummax()
    curve["drawdown"] = curve["equity"] / peak - 1.0

    trades = pd.DataFrame(orders)
    metrics = calculate_metrics(curve, trades, config)
    return BacktestResult(curve=curve, trades=trades, metrics=metrics)


def _round_units(value: float, lot_size: int, allow_fractional: bool) -> float:
    if value <= 0:
        return 0.0
    if allow_fractional:
        return float(value)
    lots = floor(value / lot_size + 1e-12)
    return float(lots * lot_size)


def infer_annual_periods(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 252.0
    elapsed_years = (
        (index[-1] - index[0]).total_seconds() / (365.25 * 24 * 60 * 60)
    )
    if elapsed_years <= 0:
        return 252.0
    return max((len(index) - 1) / elapsed_years, 1.0)


def calculate_metrics(
    curve: pd.DataFrame,
    trades: pd.DataFrame,
    config: BacktestConfig,
) -> dict[str, float]:
    if curve.empty:
        raise ValueError("Cannot calculate metrics for an empty curve.")

    returns = pd.to_numeric(curve["strategy_return"], errors="coerce").fillna(0.0)
    ending_capital = float(curve["equity"].iloc[-1])
    total_return = ending_capital / config.initial_capital - 1.0
    max_drawdown = float(curve["drawdown"].min())

    elapsed_years = 0.0
    if len(curve.index) > 1:
        elapsed_years = (
            (curve.index[-1] - curve.index[0]).total_seconds()
            / (365.25 * 24 * 60 * 60)
        )
    if elapsed_years > 0 and ending_capital > 0:
        cagr = (ending_capital / config.initial_capital) ** (
            1.0 / elapsed_years
        ) - 1.0
    else:
        cagr = float("nan")

    annual_periods = float(
        config.annual_periods
        if config.annual_periods is not None
        else infer_annual_periods(curve.index)
    )
    risk_free_per_period = (1.0 + config.risk_free_rate) ** (
        1.0 / annual_periods
    ) - 1.0
    excess = returns - risk_free_per_period
    std = float(returns.std(ddof=0))
    sharpe = sqrt(annual_periods) * float(excess.mean()) / std if std > 0 else 0.0
    downside = returns.where(returns < 0, 0.0)
    downside_std = float(np.sqrt(np.mean(np.square(downside))))
    sortino = (
        sqrt(annual_periods) * float(excess.mean()) / downside_std
        if downside_std > 0
        else 0.0
    )
    annual_volatility = std * sqrt(annual_periods)
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 and np.isfinite(cagr) else 0.0

    episode_pnl = _completed_episode_pnl(trades)
    completed = int(len(episode_pnl))
    win_rate = float((episode_pnl > 0).mean()) if completed else 0.0
    gross_profit = float(episode_pnl[episode_pnl > 0].sum()) if completed else 0.0
    gross_loss = float(-episode_pnl[episode_pnl < 0].sum()) if completed else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
    expectancy = float(episode_pnl.mean()) if completed else 0.0

    drawdown_duration = _max_drawdown_duration(curve["drawdown"])
    annual_turnover = (
        float(curve["turnover"].sum()) / elapsed_years if elapsed_years > 0 else 0.0
    )
    benchmark_return = (
        float(curve["buy_hold_equity"].iloc[-1]) / config.initial_capital - 1.0
    )

    return {
        "ending_capital": ending_capital,
        "total_return": float(total_return),
        "benchmark_return": float(benchmark_return),
        "cagr": float(cagr),
        "annual_volatility": float(annual_volatility),
        "max_drawdown": max_drawdown,
        "max_drawdown_bars": float(drawdown_duration),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "completed_trades": float(completed),
        "order_count": float(len(trades)),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "expectancy_rub": float(expectancy),
        "average_exposure": float(curve["exposure"].mean()),
        "annual_turnover": float(annual_turnover),
        "total_commission": float(curve["commission_cost"].sum()),
        "total_implicit_cost": float(curve["implicit_cost"].sum()),
        "annual_periods_used": float(annual_periods),
    }


def _completed_episode_pnl(trades: pd.DataFrame) -> pd.Series:
    """Aggregate partial reductions into completed flat-to-flat trade episodes."""
    if trades.empty:
        return pd.Series(dtype=float)

    episodes: list[float] = []
    in_episode = False
    pnl = 0.0
    for _, row in trades.iterrows():
        action = str(row.get("action", "")).upper()
        units_before = float(row.get("units_before", 0.0) or 0.0)
        units_after = float(row.get("units_after", 0.0) or 0.0)
        if action == "BUY" and units_before <= 1e-12:
            in_episode = True
            pnl = 0.0
        realised = pd.to_numeric(
            pd.Series([row.get("realised_pnl")]),
            errors="coerce",
        ).iloc[0]
        if pd.notna(realised):
            pnl += float(realised)
        if in_episode and action == "SELL" and units_after <= 1e-12:
            episodes.append(pnl)
            in_episode = False
            pnl = 0.0
    return pd.Series(episodes, dtype=float)


def _max_drawdown_duration(drawdown: pd.Series) -> int:
    longest = 0
    current = 0
    for value in drawdown.fillna(0.0):
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def rebase_oos_curve(curve: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Return a test slice with equity rebased to 1.0 for aggregation."""
    subset = curve.loc[curve.index >= start].copy()
    if subset.empty:
        return subset
    subset["equity_rebased"] = (1.0 + subset["strategy_return"]).cumprod()
    subset["drawdown_rebased"] = (
        subset["equity_rebased"] / subset["equity_rebased"].cummax() - 1.0
    )
    return subset
