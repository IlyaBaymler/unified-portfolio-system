from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from trading_robot.backtest import BacktestConfig, infer_annual_periods, run_backtest
from trading_robot.moex_iss import MoexISSClient
from trading_robot.robustness import execution_stress_test, moving_block_bootstrap
from trading_robot.strategy import (
    DonchianBreakoutConfig,
    SmaCrossoverConfig,
    TrendEnsembleConfig,
    VolatilityTargetConfig,
    generate_donchian_signals,
    generate_sma_signals,
    generate_trend_ensemble_signals,
)
from trading_robot.validation import walk_forward_sma


st.set_page_config(page_title="MOEX Strategy Research Lab", layout="wide")
st.title("MOEX Strategy Research Lab")
st.caption(
    "Исследовательский GUI: next-open исполнение, раздельные издержки, "
    "walk-forward и стресс-тесты. Реальных заявок он не отправляет."
)

INTERVALS = {
    "День": 24,
    "1 час": 60,
    "10 минут": 10,
}


@st.cache_data(ttl=3600, show_spinner=False)
def load_candles(
    ticker: str,
    board: str,
    start_date: date,
    end_date: date,
    interval: int,
) -> pd.DataFrame:
    return MoexISSClient(board=board).get_candles(
        ticker,
        start_date,
        end_date,
        interval,
    )


def parse_int_grid(text: str) -> list[int]:
    values: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if part:
            values.append(int(part))
    if not values:
        raise ValueError("Сетка параметров не должна быть пустой.")
    return sorted(set(values))


def metric_cards(metrics: dict[str, float], prefix: str = "") -> None:
    rows = [
        ("Капитал", f"{metrics['ending_capital']:,.0f} ₽".replace(",", " ")),
        ("Доходность", f"{metrics['total_return']:.1%}"),
        ("CAGR", f"{metrics['cagr']:.1%}"),
        ("Макс. просадка", f"{metrics['max_drawdown']:.1%}"),
        ("Sharpe", f"{metrics['sharpe']:.2f}"),
        ("Sortino", f"{metrics['sortino']:.2f}"),
        ("Calmar", f"{metrics['calmar']:.2f}"),
        ("Оборот/год", f"{metrics['annual_turnover']:.1f}×"),
        ("Экспозиция", f"{metrics['average_exposure']:.1%}"),
        (
            "Сделки / исполнения",
            f"{int(metrics['completed_trades'])} / {int(metrics['order_count'])}",
        ),
    ]
    first = st.columns(5)
    second = st.columns(5)
    for column, (title, value) in zip(first + second, rows, strict=True):
        column.metric(prefix + title, value)


with st.sidebar:
    st.header("Данные")
    ticker = st.text_input("Тикер", "SBER").strip().upper()
    board = st.text_input("Режим торгов", "TQBR").strip().upper()
    interval_label = st.selectbox("Интервал", list(INTERVALS), index=0)
    end_date = st.date_input("Конец", date.today())
    start_date = st.date_input(
        "Начало",
        date.today() - timedelta(days=365 * 6),
    )

    st.header("Модель исполнения")
    initial_capital = st.number_input(
        "Начальный капитал, ₽",
        min_value=10_000.0,
        max_value=1_000_000_000.0,
        value=1_000_000.0,
        step=10_000.0,
    )
    max_allocation = st.slider(
        "Максимальная доля капитала",
        min_value=0.01,
        max_value=1.00,
        value=0.50,
        step=0.01,
    )
    commission_pct = st.number_input(
        "Комиссия за операцию, %",
        min_value=0.0,
        max_value=2.0,
        value=0.05,
        step=0.01,
        format="%.3f",
    )
    half_spread_pct = st.number_input(
        "Половина спреда, %",
        min_value=0.0,
        max_value=2.0,
        value=0.02,
        step=0.01,
        format="%.3f",
    )
    slippage_pct = st.number_input(
        "Доп. проскальзывание, %",
        min_value=0.0,
        max_value=2.0,
        value=0.03,
        step=0.01,
        format="%.3f",
    )
    rebalance_band_pct = st.number_input(
        "No-trade band, процентных пунктов",
        min_value=0.0,
        max_value=50.0,
        value=1.0,
        step=0.5,
        format="%.2f",
        help=(
            "Пока фактическая доля позиции отличается от целевой меньше этого "
            "порога, мелкая ребалансировка не выполняется. Вход и полный выход "
            "происходят независимо от порога."
        ),
    )
    impact_pct = st.number_input(
        "Коэффициент влияния на цену, %",
        min_value=0.0,
        max_value=5.0,
        value=0.00,
        step=0.01,
        format="%.3f",
        help="Умножается на квадратный корень доли объёма свечи.",
    )
    lot_size = st.number_input(
        "Размер лота, бумаг",
        min_value=1,
        max_value=1_000_000,
        value=1,
        step=1,
    )

    st.header("Риск")
    use_vol_target = st.checkbox("Таргетировать волатильность", value=True)
    target_vol_pct = st.number_input(
        "Целевая годовая волатильность, %",
        min_value=1.0,
        max_value=100.0,
        value=15.0,
        step=1.0,
        disabled=not use_vol_target,
    )
    vol_window = st.number_input(
        "Окно волатильности",
        min_value=5,
        max_value=500,
        value=20,
        step=1,
    )


def make_backtest_config(**changes: Any) -> BacktestConfig:
    base = BacktestConfig(
        initial_capital=float(initial_capital),
        commission_rate=float(commission_pct) / 100.0,
        half_spread_rate=float(half_spread_pct) / 100.0,
        slippage_rate=float(slippage_pct) / 100.0,
        market_impact_coefficient=float(impact_pct) / 100.0,
        position_fraction=float(max_allocation),
        lot_size=int(lot_size),
        rebalance_tolerance=float(rebalance_band_pct) / 100.0,
        annual_periods=None,
    )
    return replace(base, **changes) if changes else base


def make_volatility_config(candles: pd.DataFrame) -> VolatilityTargetConfig:
    annual_periods = max(1, int(round(infer_annual_periods(candles.index))))
    return VolatilityTargetConfig(
        annual_target_volatility=(
            float(target_vol_pct) / 100.0 if use_vol_target else None
        ),
        window=int(vol_window),
        annual_periods=annual_periods,
        max_weight=1.0,
    )


backtest_tab, walkforward_tab, stress_tab, audit_tab = st.tabs(
    [
        "Одиночный бэктест",
        "Walk-forward SMA",
        "Стресс и Monte Carlo",
        "Инженерный аудит",
    ]
)

with backtest_tab:
    strategy_name = st.selectbox(
        "Стратегия",
        [
            "SMA с зоной нечувствительности",
            "Donchian breakout + ATR stop",
            "Ансамбль медленных трендов",
        ],
    )

    if strategy_name.startswith("SMA"):
        c1, c2, c3 = st.columns(3)
        fast = c1.number_input("Быстрая SMA", 2, 500, 20)
        slow = c2.number_input("Медленная SMA", 3, 1_500, 100)
        hysteresis_pct = c3.number_input(
            "Зона нечувствительности, %",
            0.0,
            10.0,
            0.20,
            0.05,
        )
    elif strategy_name.startswith("Donchian"):
        c1, c2, c3, c4 = st.columns(4)
        entry_window = c1.number_input("Пробой: окно входа", 5, 1_000, 55)
        exit_window = c2.number_input("Канал выхода", 2, 500, 20)
        atr_window = c3.number_input("ATR: окно", 5, 500, 20)
        stop_atr = c4.number_input("Трейлинг-стоп, ATR", 0.5, 20.0, 3.0, 0.25)
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        ens_fast = c1.number_input("SMA fast", 2, 500, 50)
        ens_slow = c2.number_input("SMA slow", 3, 1_500, 200)
        ens_mom = c3.number_input("Momentum", 5, 1_000, 126)
        ens_break = c4.number_input("Breakout", 5, 1_000, 100)
        ens_votes = c5.number_input("Голосов для входа", 1, 4, 3)

    run_single = st.button("Запустить исследовательский бэктест", type="primary")
    if run_single:
        try:
            if not ticker or not board:
                raise ValueError("Тикер и режим торгов не должны быть пустыми.")
            if start_date >= end_date:
                raise ValueError("Дата начала должна быть раньше даты окончания.")
            with st.spinner("Загружаю данные и моделирую исполнение на следующем открытии…"):
                candles = load_candles(
                    ticker,
                    board,
                    start_date,
                    end_date,
                    INTERVALS[interval_label],
                )
                vol_config = make_volatility_config(candles)
                if strategy_name.startswith("SMA"):
                    strategy_config = SmaCrossoverConfig(
                        fast_window=int(fast),
                        slow_window=int(slow),
                        hysteresis_percent=float(hysteresis_pct) / 100.0,
                        volatility=vol_config,
                    )
                    signals = generate_sma_signals(candles, strategy_config)
                    indicator_columns = ["close", "sma_fast", "sma_slow"]
                elif strategy_name.startswith("Donchian"):
                    strategy_config = DonchianBreakoutConfig(
                        entry_window=int(entry_window),
                        exit_window=int(exit_window),
                        atr_window=int(atr_window),
                        trailing_stop_atr=float(stop_atr),
                        volatility=vol_config,
                    )
                    signals = generate_donchian_signals(candles, strategy_config)
                    indicator_columns = [
                        "close",
                        "donchian_upper",
                        "donchian_lower",
                        "trailing_stop",
                    ]
                else:
                    strategy_config = TrendEnsembleConfig(
                        sma_fast=int(ens_fast),
                        sma_slow=int(ens_slow),
                        momentum_window=int(ens_mom),
                        breakout_window=int(ens_break),
                        vote_threshold=int(ens_votes),
                        volatility=vol_config,
                    )
                    signals = generate_trend_ensemble_signals(
                        candles,
                        strategy_config,
                    )
                    indicator_columns = ["close", "sma_fast", "sma_slow", "ema_slow"]

                bt_config = make_backtest_config()
                result = run_backtest(signals, bt_config)
                st.session_state["research_context"] = {
                    "ticker": ticker,
                    "candles": candles,
                    "signals": signals,
                    "strategy_config": strategy_config,
                    "backtest_config": bt_config,
                    "indicator_columns": indicator_columns,
                    "result": result,
                }
        except Exception as exc:
            st.error(f"Ошибка: {exc}")

    context = st.session_state.get("research_context")
    if context:
        result = context["result"]
        signals = context["signals"]
        metric_cards(result.metrics)
        st.caption(
            "Benchmark — пассивная позиция с той же максимальной долей капитала. "
            f"Его доходность: {result.metrics['benchmark_return']:.1%}."
        )

        st.subheader("Цена и индикаторы")
        requested_indicators = context.get(
            "indicator_columns",
            [
                "close",
                "sma_fast",
                "sma_slow",
                "donchian_upper",
                "donchian_lower",
                "trailing_stop",
                "ema_slow",
            ],
        )
        available_columns = [
            column
            for column in requested_indicators
            if column in result.curve.columns
        ]
        st.line_chart(result.curve[available_columns].dropna(how="all"))

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Кривая капитала")
            st.line_chart(
                result.curve[["equity", "buy_hold_equity"]].rename(
                    columns={
                        "equity": "Стратегия",
                        "buy_hold_equity": "Benchmark",
                    }
                )
            )
        with c2:
            st.subheader("Целевая и фактическая экспозиция")
            st.line_chart(
                result.curve[["target_weight_executed", "exposure"]].rename(
                    columns={
                        "target_weight_executed": "Цель",
                        "exposure": "Фактическая",
                    }
                )
            )

        st.subheader("Просадка")
        st.area_chart(result.curve[["drawdown"]])

        st.subheader("Исполнения")
        if result.trades.empty:
            st.info("На выбранном периоде исполнений нет.")
        else:
            st.dataframe(result.trades, use_container_width=True, hide_index=True)
            st.download_button(
                "Скачать исполнения CSV",
                result.trades.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{context['ticker']}_research_trades.csv",
                mime="text/csv",
            )

        with st.expander("Все метрики"):
            metric_frame = pd.DataFrame(
                {"metric": result.metrics.keys(), "value": result.metrics.values()}
            )
            st.dataframe(metric_frame, use_container_width=True, hide_index=True)

with walkforward_tab:
    st.markdown(
        "Параметры выбираются только на обучающем окне, затем проверяются на "
        "следующем непересекающемся участке. Это не заменяет финальный untouched holdout."
    )
    c1, c2 = st.columns(2)
    fast_grid_text = c1.text_input(
        "Быстрые SMA через запятую",
        "10, 20, 30, 40, 50",
        key="wf_fast",
    )
    slow_grid_text = c2.text_input(
        "Медленные SMA через запятую",
        "60, 100, 150, 200, 250",
        key="wf_slow",
    )
    c1, c2, c3, c4 = st.columns(4)
    train_bars = c1.number_input("Обучение, баров", 50, 20_000, 756)
    test_bars = c2.number_input("Тест, баров", 10, 5_000, 126)
    objective = c3.selectbox("Критерий отбора", ["calmar", "sharpe", "sortino", "cagr"])
    min_trades = c4.number_input("Минимум сделок на train", 0, 100, 2)
    wf_hysteresis = st.number_input(
        "Фиксированная зона нечувствительности, %",
        0.0,
        10.0,
        0.20,
        0.05,
        key="wf_hysteresis",
    )

    run_wf = st.button("Запустить walk-forward", key="run_wf", type="primary")
    if run_wf:
        try:
            fast_values = parse_int_grid(fast_grid_text)
            slow_values = parse_int_grid(slow_grid_text)
            with st.spinner("Выполняю последовательный train/test анализ…"):
                candles = load_candles(
                    ticker,
                    board,
                    start_date,
                    end_date,
                    INTERVALS[interval_label],
                )
                valid_pairs = [
                    (fast_value, slow_value)
                    for fast_value in fast_values
                    for slow_value in slow_values
                    if slow_value > fast_value
                ]
                if not valid_pairs:
                    raise ValueError(
                        "В сетке нет ни одной пары, где slow_window > fast_window."
                    )
                template_fast, template_slow = valid_pairs[0]
                template = SmaCrossoverConfig(
                    fast_window=template_fast,
                    slow_window=template_slow,
                    hysteresis_percent=float(wf_hysteresis) / 100.0,
                    volatility=make_volatility_config(candles),
                )
                wf_result = walk_forward_sma(
                    candles,
                    fast_values,
                    slow_values,
                    template,
                    make_backtest_config(),
                    train_bars=int(train_bars),
                    test_bars=int(test_bars),
                    step_bars=int(test_bars),
                    objective=objective,
                    minimum_completed_trades=int(min_trades),
                )
                st.session_state["wf_result"] = wf_result
        except Exception as exc:
            st.error(f"Ошибка walk-forward: {exc}")

    wf_result = st.session_state.get("wf_result")
    if wf_result:
        metric_cards(wf_result.aggregate_metrics, prefix="OOS: ")
        st.subheader("Совокупная out-of-sample кривая")
        st.line_chart(wf_result.oos_curve[["equity"]])

        st.subheader("Выбранные параметры по окнам")
        st.dataframe(wf_result.selections, use_container_width=True, hide_index=True)

        stability = (
            wf_result.grid_results[wf_result.grid_results["status"] == "ok"]
            .groupby(["fast_window", "slow_window"], as_index=False)["score"]
            .median()
        )
        pivot = stability.pivot(
            index="fast_window",
            columns="slow_window",
            values="score",
        )
        st.subheader("Медианная устойчивость параметров по train-окнам")
        st.dataframe(pivot.style.format("{:.2f}"), use_container_width=True)

        st.download_button(
            "Скачать OOS-кривую CSV",
            wf_result.oos_curve.to_csv().encode("utf-8-sig"),
            file_name=f"{ticker}_walk_forward_oos.csv",
            mime="text/csv",
        )

with stress_tab:
    context = st.session_state.get("research_context")
    if not context:
        st.info("Сначала выполните одиночный бэктест на первой вкладке.")
    else:
        st.markdown(
            "Хорошая стратегия должна деградировать постепенно, а не разрушаться "
            "от удвоения издержек или задержки исполнения на один дополнительный бар."
        )
        c1, c2 = st.columns(2)
        with c1:
            run_stress = st.button("Запустить стресс исполнения", type="primary")
        with c2:
            bootstrap_count = st.number_input(
                "Monte Carlo симуляций",
                100,
                20_000,
                1_000,
                100,
            )
            block_size = st.number_input("Размер блока доходностей", 1, 250, 5)

        if run_stress:
            try:
                stress = execution_stress_test(
                    context["signals"],
                    context["backtest_config"],
                    cost_multipliers=(1.0, 2.0, 3.0),
                    delays=(1, 2),
                )
                bootstrap = moving_block_bootstrap(
                    context["result"].curve["strategy_return"],
                    simulations=int(bootstrap_count),
                    block_size=int(block_size),
                    initial_capital=float(initial_capital),
                )
                st.session_state["stress_result"] = stress
                st.session_state["bootstrap_result"] = bootstrap
            except Exception as exc:
                st.error(f"Ошибка стресс-теста: {exc}")

        stress = st.session_state.get("stress_result")
        bootstrap = st.session_state.get("bootstrap_result")
        if stress is not None:
            st.subheader("Издержки × задержка")
            formatted = stress.copy()
            for column in ["total_return", "cagr", "max_drawdown"]:
                formatted[column] = formatted[column].map(lambda value: f"{value:.1%}")
            st.dataframe(formatted, use_container_width=True, hide_index=True)
        if bootstrap is not None:
            st.subheader("Moving-block bootstrap")
            probability_of_loss = float(
                (bootstrap.distribution["total_return"] < 0).mean()
            )
            q05 = float(bootstrap.distribution["total_return"].quantile(0.05))
            dd05 = float(bootstrap.distribution["max_drawdown"].quantile(0.05))
            c1, c2, c3 = st.columns(3)
            c1.metric("Вероятность убытка в ресэмплинге", f"{probability_of_loss:.1%}")
            c2.metric("5-й процентиль доходности", f"{q05:.1%}")
            c3.metric("5-й процентиль max DD", f"{dd05:.1%}")
            st.dataframe(
                bootstrap.quantiles.style.format(
                    {
                        "ending_capital": "{:,.0f}",
                        "total_return": "{:.1%}",
                        "max_drawdown": "{:.1%}",
                    }
                ),
                use_container_width=True,
            )
            histogram = pd.cut(
                bootstrap.distribution["total_return"],
                bins=30,
            ).value_counts().sort_index()
            histogram.index = histogram.index.astype(str)
            st.bar_chart(histogram)

with audit_tab:
    st.subheader("Что изменено по сравнению с MVP")
    st.markdown(
        """
1. Сигнал закрытия исполняется на следующем открытии, поэтому gap не превращается в бесплатную доходность.
2. Комиссия, половина спреда, дополнительное проскальзывание и влияние объёма считаются отдельно.
3. Позиция округляется по размеру лота, покупки ограничены доступным денежным остатком.
4. CAGR и годовая волатильность рассчитываются по фактическому времени наблюдения.
5. Добавлены Sortino, Calmar, оборот, экспозиция, длительность просадки и разложение издержек.
6. Помимо SMA доступны Donchian breakout с ATR-выходом и ансамбль медленных трендов.
7. Есть walk-forward, сетка устойчивости, стресс издержек/задержки и moving-block bootstrap.
8. В Sandbox-роботе исправлена защита от повторной свечи, добавлены детерминированные orderId, восстановление незавершённого поручения, проверка торгового статуса и атомарное состояние.
        """
    )
    st.warning(
        "Ни один из тестов не доказывает будущую прибыльность. Финальный кандидат "
        "должен пройти untouched holdout и длительный forward/paper test без "
        "подстройки параметров."
    )
