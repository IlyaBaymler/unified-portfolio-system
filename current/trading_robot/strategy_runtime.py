from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from numbers import Integral, Real
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .backtest import infer_annual_periods
from .strategy import (
    DonchianBreakoutConfig,
    SmaCrossoverConfig,
    StrategyName,
    TrendEnsembleConfig,
    VolatilityTargetConfig,
    generate_strategy_signals,
)


VALID_STRATEGIES: tuple[StrategyName, ...] = ("sma", "donchian", "ensemble")
STRATEGY_VERSIONS: dict[StrategyName, str] = {
    "sma": "1.1",
    "donchian": "1.0",
    "ensemble": "1.0",
}
STRATEGY_TITLES_RU: dict[StrategyName, str] = {
    "sma": "SMA с гистерезисом",
    "donchian": "Donchian + ATR",
    "ensemble": "Ансамбль трендов",
}


def normalize_strategy_name(value: str) -> StrategyName:
    normalized = str(value).strip().lower()
    aliases = {
        "sma": "sma",
        "sma_hysteresis": "sma",
        "donchian": "donchian",
        "donchian_atr": "donchian",
        "ensemble": "ensemble",
        "trend_ensemble": "ensemble",
    }
    result = aliases.get(normalized)
    if result not in VALID_STRATEGIES:
        raise ValueError(
            f"Unknown strategy {value!r}. Supported: {', '.join(VALID_STRATEGIES)}."
        )
    return result  # type: ignore[return-value]


def _unique_strategies(values: Iterable[str]) -> tuple[StrategyName, ...]:
    result: list[StrategyName] = []
    for value in values:
        strategy = normalize_strategy_name(value)
        if strategy not in result:
            result.append(strategy)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class StrategySuiteConfig:
    """Versioned PRIMARY/SHADOW strategy configuration for live evaluation.

    The suite contains strategy parameters and the per-strategy position cap.
    Order routing, portfolio reconciliation and broker recovery remain in the
    execution layer.
    """

    primary_strategy: StrategyName = "sma"
    shadow_strategies: tuple[StrategyName, ...] = ()

    sma_fast_window: int = 20
    sma_slow_window: int = 50
    sma_hysteresis_percent: float = 0.002

    donchian_entry_window: int = 55
    donchian_exit_window: int = 20
    donchian_atr_window: int = 20
    donchian_trailing_stop_atr: float = 3.0

    ensemble_sma_fast: int = 50
    ensemble_sma_slow: int = 200
    ensemble_momentum_window: int = 126
    ensemble_breakout_window: int = 100
    ensemble_vote_threshold: int = 3

    annual_target_volatility: float | None = None
    volatility_window: int = 20
    max_weight: float = 1.0
    position_limit_lots: int = 1
    min_annual_volatility: float = 0.03

    def __post_init__(self) -> None:
        primary = normalize_strategy_name(self.primary_strategy)
        shadows = _unique_strategies(self.shadow_strategies)
        shadows = tuple(item for item in shadows if item != primary)
        object.__setattr__(self, "primary_strategy", primary)
        object.__setattr__(self, "shadow_strategies", shadows)

        if self.sma_fast_window < 2 or self.sma_slow_window <= self.sma_fast_window:
            raise ValueError("Require 2 <= SMA fast < SMA slow.")
        if not 0 <= self.sma_hysteresis_percent <= 0.20:
            raise ValueError("SMA hysteresis must be in [0, 0.20].")

        if self.donchian_entry_window < 5:
            raise ValueError("Donchian entry window must be at least 5.")
        if not 2 <= self.donchian_exit_window < self.donchian_entry_window:
            raise ValueError("Donchian exit window must be below entry window.")
        if self.donchian_atr_window < 5:
            raise ValueError("Donchian ATR window must be at least 5.")
        if self.donchian_trailing_stop_atr <= 0:
            raise ValueError("Donchian ATR stop multiplier must be positive.")

        if (
            self.ensemble_sma_fast < 2
            or self.ensemble_sma_slow <= self.ensemble_sma_fast
        ):
            raise ValueError("Require 2 <= ensemble SMA fast < SMA slow.")
        if self.ensemble_momentum_window < 5:
            raise ValueError("Ensemble momentum window must be at least 5.")
        if self.ensemble_breakout_window < 5:
            raise ValueError("Ensemble breakout window must be at least 5.")
        if not 1 <= self.ensemble_vote_threshold <= 4:
            raise ValueError("Ensemble vote threshold must be in [1, 4].")

        if self.annual_target_volatility is not None:
            if not 0 < self.annual_target_volatility <= 2:
                raise ValueError("Target volatility must be in (0, 2] or None.")
        if self.volatility_window < 5:
            raise ValueError("Volatility window must be at least 5.")
        if not 0 < self.max_weight <= 1:
            raise ValueError("max_weight must be in (0, 1].")
        if self.position_limit_lots < 1:
            raise ValueError("position_limit_lots must be positive.")
        if self.min_annual_volatility <= 0:
            raise ValueError("min_annual_volatility must be positive.")

    @property
    def enabled_strategies(self) -> tuple[StrategyName, ...]:
        return (self.primary_strategy, *self.shadow_strategies)

    def role_for(self, strategy: StrategyName | str) -> str:
        normalized = normalize_strategy_name(str(strategy))
        return "PRIMARY" if normalized == self.primary_strategy else "SHADOW"

    def parameter_snapshot(self, strategy: StrategyName | str) -> dict[str, Any]:
        strategy = normalize_strategy_name(str(strategy))
        common = {
            "annual_target_volatility": self.annual_target_volatility,
            "volatility_window": self.volatility_window,
            "max_weight": self.max_weight,
            "position_limit_lots": self.position_limit_lots,
            "min_annual_volatility": self.min_annual_volatility,
        }
        if strategy == "sma":
            strategy_parameters = {
                "fast_window": self.sma_fast_window,
                "slow_window": self.sma_slow_window,
                "hysteresis_percent": self.sma_hysteresis_percent,
            }
        elif strategy == "donchian":
            strategy_parameters = {
                "entry_window": self.donchian_entry_window,
                "exit_window": self.donchian_exit_window,
                "atr_window": self.donchian_atr_window,
                "trailing_stop_atr": self.donchian_trailing_stop_atr,
            }
        else:
            strategy_parameters = {
                "sma_fast": self.ensemble_sma_fast,
                "sma_slow": self.ensemble_sma_slow,
                "momentum_window": self.ensemble_momentum_window,
                "breakout_window": self.ensemble_breakout_window,
                "vote_threshold": self.ensemble_vote_threshold,
            }
        return {
            "strategy_id": strategy,
            "strategy_version": STRATEGY_VERSIONS[strategy],
            "parameters": strategy_parameters,
            "risk": common,
        }

    def config_hash(self, strategy: StrategyName | str) -> str:
        canonical = json.dumps(
            self.parameter_snapshot(strategy),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def suite_hash(self) -> str:
        payload = {
            "primary_strategy": self.primary_strategy,
            "shadow_strategies": list(self.shadow_strategies),
            "strategies": {
                strategy: self.parameter_snapshot(strategy)
                for strategy in self.enabled_strategies
            },
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def required_bars(self, strategy: StrategyName | str) -> int:
        strategy = normalize_strategy_name(str(strategy))
        if strategy == "sma":
            base = max(self.sma_slow_window, self.volatility_window)
        elif strategy == "donchian":
            base = max(
                self.donchian_entry_window,
                self.donchian_exit_window,
                self.donchian_atr_window,
                self.volatility_window,
            ) + 2
        else:
            base = max(
                self.ensemble_sma_slow,
                self.ensemble_momentum_window,
                self.ensemble_breakout_window,
                self.volatility_window,
            ) + 2
        return int(base + 5)

    def evaluation_bars(self, strategy: StrategyName | str) -> int:
        """Return a deterministic live reconstruction window.

        Stateful rules such as SMA hysteresis and the Donchian trailing stop
        need more history than the mathematical indicator warm-up.  A finite
        window can never prove that it contains the original entry, but using
        a stable per-strategy window prevents a SHADOW strategy from changing
        the PRIMARY decision merely by extending the shared download range.
        """
        required = self.required_bars(strategy)
        return max(required + 10, required * 2)

    def required_bars_for_suite(self) -> int:
        return max(
            self.evaluation_bars(strategy)
            for strategy in self.enabled_strategies
        )

    def build_strategy_config(
        self,
        strategy: StrategyName | str,
        candles: pd.DataFrame,
    ) -> SmaCrossoverConfig | DonchianBreakoutConfig | TrendEnsembleConfig:
        strategy = normalize_strategy_name(str(strategy))
        annual_periods = max(1, int(round(infer_annual_periods(candles.index))))
        volatility = VolatilityTargetConfig(
            annual_target_volatility=self.annual_target_volatility,
            window=self.volatility_window,
            annual_periods=annual_periods,
            max_weight=self.max_weight,
            min_annual_volatility=self.min_annual_volatility,
        )
        if strategy == "sma":
            return SmaCrossoverConfig(
                fast_window=self.sma_fast_window,
                slow_window=self.sma_slow_window,
                hysteresis_percent=self.sma_hysteresis_percent,
                volatility=volatility,
            )
        if strategy == "donchian":
            return DonchianBreakoutConfig(
                entry_window=self.donchian_entry_window,
                exit_window=self.donchian_exit_window,
                atr_window=self.donchian_atr_window,
                trailing_stop_atr=self.donchian_trailing_stop_atr,
                volatility=volatility,
            )
        return TrendEnsembleConfig(
            sma_fast=self.ensemble_sma_fast,
            sma_slow=self.ensemble_sma_slow,
            momentum_window=self.ensemble_momentum_window,
            breakout_window=self.ensemble_breakout_window,
            vote_threshold=self.ensemble_vote_threshold,
            volatility=volatility,
        )


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    candle_time: str
    strategy_id: StrategyName
    strategy_version: str
    role: str
    config_hash: str
    signal: int
    target_weight: float
    target_lots: int
    reason: str
    indicators: dict[str, Any]
    bars_used: int
    required_bars: int
    stop_level: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value)
    return str(value)


def _normalise_timestamp(value: Any) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def _target_lots(signal: int, target_weight: float, maximum_lots: int) -> int:
    if signal != 1 or target_weight <= 0 or maximum_lots <= 0:
        return 0
    result = int(round(maximum_lots * min(max(target_weight, 0.0), 1.0)))
    return max(1, min(result, maximum_lots))


def _sma_reason(
    latest: pd.Series,
    previous: pd.Series | None,
    config: SmaCrossoverConfig,
) -> str:
    fast = float(latest["sma_fast"])
    slow = float(latest["sma_slow"])
    signal = int(latest["signal"])
    previous_signal = int(previous["signal"]) if previous is not None else signal
    enter_level = slow * (1.0 + config.hysteresis_percent)
    exit_level = slow * (1.0 - config.hysteresis_percent)
    if signal == 1 and previous_signal == 0:
        return "Вход: быстрая SMA выше верхней границы гистерезиса."
    if signal == 0 and previous_signal == 1:
        return "Выход: быстрая SMA ниже нижней границы гистерезиса."
    if signal == 1:
        if fast <= enter_level:
            return "Удержание LONG внутри зоны гистерезиса."
        return "Удержание LONG: быстрая SMA выше медленной."
    if fast >= exit_level:
        return "Состояние FLAT удерживается внутри зоны гистерезиса."
    return "FLAT: быстрая SMA ниже медленной."


def _donchian_reason(latest: pd.Series, previous: pd.Series | None) -> str:
    signal = int(latest["signal"])
    previous_signal = int(previous["signal"]) if previous is not None else signal
    close = float(latest["close"])
    upper = latest.get("donchian_upper")
    lower = latest.get("donchian_lower")
    stop = latest.get("trailing_stop")
    if signal == 1 and previous_signal == 0:
        return "Вход: закрытие выше предыдущего верхнего Donchian-канала."
    if signal == 0 and previous_signal == 1:
        if stop is not None and not pd.isna(stop) and close < float(stop):
            return "Выход: закрытие ниже ATR trailing stop."
        if lower is not None and not pd.isna(lower) and close < float(lower):
            return "Выход: закрытие ниже нижнего Donchian-канала."
        return "Выход из трендовой позиции."
    if signal == 1:
        return "Удержание LONG: пробой остаётся действующим."
    if upper is not None and not pd.isna(upper) and close <= float(upper):
        return "FLAT: подтверждённого пробоя верхнего канала нет."
    return "FLAT: стратегия ожидает новый пробой."


def _ensemble_reason(latest: pd.Series) -> str:
    votes = int(latest.get("votes", 0))
    signal = int(latest["signal"])
    return (
        f"LONG: согласовано {votes} из 4 трендовых голосов."
        if signal == 1
        else f"FLAT: согласовано только {votes} из 4 трендовых голосов."
    )


def evaluate_strategy_decision(
    candles: pd.DataFrame,
    suite: StrategySuiteConfig,
    strategy: StrategyName | str,
) -> StrategyDecision:
    strategy = normalize_strategy_name(str(strategy))
    required_bars = suite.required_bars(strategy)
    evaluation_bars = suite.evaluation_bars(strategy)
    if len(candles) < required_bars:
        raise ValueError(
            f"Strategy {strategy!r} requires at least {required_bars} candles; "
            f"received {len(candles)}."
        )
    strategy_candles = candles.tail(evaluation_bars).copy()
    config = suite.build_strategy_config(strategy, strategy_candles)
    signals = generate_strategy_signals(strategy_candles, strategy, config)
    latest = signals.iloc[-1]
    previous = signals.iloc[-2] if len(signals) > 1 else None
    signal = int(latest["signal"])
    target_weight = float(latest.get("target_weight", signal) or 0.0)
    target_lots = _target_lots(
        signal, target_weight, suite.position_limit_lots
    )

    if strategy == "sma":
        assert isinstance(config, SmaCrossoverConfig)
        indicators = {
            "close": _json_value(latest.get("close")),
            "sma_fast": _json_value(latest.get("sma_fast")),
            "sma_slow": _json_value(latest.get("sma_slow")),
            "enter_level": _json_value(
                float(latest["sma_slow"]) * (1.0 + config.hysteresis_percent)
            ),
            "exit_level": _json_value(
                float(latest["sma_slow"]) * (1.0 - config.hysteresis_percent)
            ),
            "annual_volatility": _json_value(latest.get("annual_volatility")),
        }
        reason = _sma_reason(latest, previous, config)
        stop_level = None
    elif strategy == "donchian":
        indicators = {
            "close": _json_value(latest.get("close")),
            "donchian_upper": _json_value(latest.get("donchian_upper")),
            "donchian_lower": _json_value(latest.get("donchian_lower")),
            "atr": _json_value(latest.get("atr")),
            "trailing_stop": _json_value(latest.get("trailing_stop")),
            "annual_volatility": _json_value(latest.get("annual_volatility")),
        }
        reason = _donchian_reason(latest, previous)
        raw_stop = latest.get("trailing_stop")
        stop_level = (
            None if raw_stop is None or pd.isna(raw_stop) else float(raw_stop)
        )
    else:
        indicators = {
            "close": _json_value(latest.get("close")),
            "sma_fast": _json_value(latest.get("sma_fast")),
            "sma_slow": _json_value(latest.get("sma_slow")),
            "ema_slow": _json_value(latest.get("ema_slow")),
            "momentum": _json_value(latest.get("momentum")),
            "breakout_level": _json_value(latest.get("breakout_level")),
            "vote_sma": _json_value(latest.get("vote_sma")),
            "vote_ema": _json_value(latest.get("vote_ema")),
            "vote_momentum": _json_value(latest.get("vote_momentum")),
            "vote_breakout": _json_value(latest.get("vote_breakout")),
            "votes": _json_value(latest.get("votes")),
            "annual_volatility": _json_value(latest.get("annual_volatility")),
        }
        reason = _ensemble_reason(latest)
        stop_level = None

    return StrategyDecision(
        candle_time=_normalise_timestamp(signals.index[-1]),
        strategy_id=strategy,
        strategy_version=STRATEGY_VERSIONS[strategy],
        role=suite.role_for(strategy),
        config_hash=suite.config_hash(strategy),
        signal=signal,
        target_weight=max(0.0, min(target_weight, 1.0)),
        target_lots=target_lots,
        reason=reason,
        indicators=indicators,
        bars_used=len(strategy_candles),
        required_bars=required_bars,
        stop_level=stop_level,
    )


def evaluate_strategy_suite(
    candles: pd.DataFrame,
    suite: StrategySuiteConfig,
) -> dict[StrategyName, StrategyDecision]:
    return {
        strategy: evaluate_strategy_decision(
            candles,
            suite,
            strategy,
        )
        for strategy in suite.enabled_strategies
    }


def compare_strategy_decisions(
    decisions: dict[StrategyName, StrategyDecision],
    primary_strategy: StrategyName | str,
) -> dict[str, Any]:
    primary_strategy = normalize_strategy_name(str(primary_strategy))
    primary = decisions[primary_strategy]
    signals = {key: value.signal for key, value in decisions.items()}
    target_lots = {key: value.target_lots for key, value in decisions.items()}
    disagreeing = [
        key for key, value in decisions.items() if value.signal != primary.signal
    ]
    all_agree = len(set(signals.values())) <= 1
    return {
        "candle_time": primary.candle_time,
        "primary_strategy": primary_strategy,
        "primary_signal": primary.signal,
        "primary_target_lots": primary.target_lots,
        "signals": signals,
        "target_lots": target_lots,
        "all_agree": all_agree,
        "disagreeing_strategies": disagreeing,
        "enabled_count": len(decisions),
        "event_type": (
            "PRIMARY_ONLY"
            if len(decisions) == 1
            else ("AGREEMENT" if all_agree else "DIVERGENCE")
        ),
    }
