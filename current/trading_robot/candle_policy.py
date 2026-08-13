from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import ceil


@dataclass(frozen=True, slots=True)
class CandleIntervalPolicy:
    interval: str
    duration: timedelta
    maximum_request_span: timedelta
    maximum_lookback_days: int
    estimated_bars_per_calendar_day: float
    automatic_max_signal_age_seconds: int

    def __post_init__(self) -> None:
        if not self.interval.startswith("CANDLE_INTERVAL_"):
            raise ValueError("Candle interval must use CANDLE_INTERVAL_*.")
        if self.duration.total_seconds() <= 0:
            raise ValueError("Candle duration must be positive.")
        if self.maximum_request_span.total_seconds() <= 0:
            raise ValueError("Maximum request span must be positive.")
        if self.maximum_lookback_days < 1:
            raise ValueError("Maximum lookback must be positive.")
        if self.estimated_bars_per_calendar_day <= 0:
            raise ValueError("Estimated bars per day must be positive.")
        if self.automatic_max_signal_age_seconds < 1:
            raise ValueError("Automatic signal age must be positive.")


# Request spans follow the official T-Invest GetCandles contract. Strategy
# lookback is kept one day inside short-period API boundaries so inclusive
# timestamps and clock skew cannot turn an otherwise valid request into 400.
CANDLE_INTERVAL_POLICIES: dict[str, CandleIntervalPolicy] = {
    "CANDLE_INTERVAL_10_MIN": CandleIntervalPolicy(
        interval="CANDLE_INTERVAL_10_MIN",
        duration=timedelta(minutes=10),
        maximum_request_span=timedelta(days=7),
        maximum_lookback_days=6,
        estimated_bars_per_calendar_day=30.0,
        automatic_max_signal_age_seconds=30 * 60,
    ),
    "CANDLE_INTERVAL_15_MIN": CandleIntervalPolicy(
        interval="CANDLE_INTERVAL_15_MIN",
        duration=timedelta(minutes=15),
        maximum_request_span=timedelta(days=21),
        maximum_lookback_days=20,
        estimated_bars_per_calendar_day=20.0,
        automatic_max_signal_age_seconds=45 * 60,
    ),
    "CANDLE_INTERVAL_30_MIN": CandleIntervalPolicy(
        interval="CANDLE_INTERVAL_30_MIN",
        duration=timedelta(minutes=30),
        maximum_request_span=timedelta(days=21),
        maximum_lookback_days=20,
        estimated_bars_per_calendar_day=10.0,
        automatic_max_signal_age_seconds=90 * 60,
    ),
    "CANDLE_INTERVAL_HOUR": CandleIntervalPolicy(
        interval="CANDLE_INTERVAL_HOUR",
        duration=timedelta(hours=1),
        maximum_request_span=timedelta(days=90),
        maximum_lookback_days=89,
        estimated_bars_per_calendar_day=6.0,
        # Allows an end-of-session hourly signal to execute next morning.
        automatic_max_signal_age_seconds=20 * 60 * 60,
    ),
    "CANDLE_INTERVAL_DAY": CandleIntervalPolicy(
        interval="CANDLE_INTERVAL_DAY",
        duration=timedelta(days=1),
        maximum_request_span=timedelta(days=365 * 6),
        maximum_lookback_days=2180,
        estimated_bars_per_calendar_day=0.55,
        automatic_max_signal_age_seconds=4 * 24 * 60 * 60,
    ),
}


def candle_interval_policy(interval: str) -> CandleIntervalPolicy:
    normalized = str(interval or "").strip().upper()
    try:
        return CANDLE_INTERVAL_POLICIES[normalized]
    except KeyError as exc:
        supported = ", ".join(CANDLE_INTERVAL_POLICIES)
        raise ValueError(
            f"Unsupported Sandbox candle interval: {normalized!r}. "
            f"Supported: {supported}."
        ) from exc


def strategy_lookback_days(
    interval: str,
    *,
    required_bars: int,
    requested_days: int,
) -> int:
    policy = candle_interval_policy(interval)
    if int(required_bars) < 1:
        raise ValueError("required_bars must be positive.")
    if int(requested_days) < 1:
        raise ValueError("requested_days must be positive.")
    estimated_days = max(
        1,
        ceil(
            (int(required_bars) / policy.estimated_bars_per_calendar_day) * 1.25
        ),
    )
    return min(
        max(int(requested_days), estimated_days),
        policy.maximum_lookback_days,
    )
