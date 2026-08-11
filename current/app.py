from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from trading_robot.backtest import BacktestConfig, run_backtest
from trading_robot.moex_iss import MoexISSClient
from trading_robot.strategy import SmaCrossoverConfig, generate_sma_signals


st.set_page_config(page_title="MOEX Research Robot", layout="wide")
st.title("MOEX Research Robot")
st.caption(
    "Исследовательский интерфейс: исторические данные MOEX ISS и бэктест. "
    "Он не отправляет реальные биржевые заявки."
)

with st.sidebar:
    st.header("Параметры")
    ticker = st.text_input("Тикер", "SBER").strip().upper()
    board = st.text_input("Режим торгов", "TQBR").strip().upper()
    interval_label = st.selectbox(
        "Интервал",
        ["День", "1 час", "10 минут"],
        index=0,
    )
    interval = {"День": 24, "1 час": 60, "10 минут": 10}[interval_label]
    end_date = st.date_input("Конец", date.today())
    start_date = st.date_input(
        "Начало", date.today() - timedelta(days=365 * 3)
    )
    fast = st.number_input("Быстрая SMA", 2, 500, 20)
    slow = st.number_input("Медленная SMA", 3, 1000, 50)
    initial_capital = st.number_input(
        "Начальный капитал, ₽", 10_000.0, 100_000_000.0, 1_000_000.0
    )
    position_fraction = st.slider(
        "Доля капитала в позиции", 0.01, 1.0, 0.10, 0.01
    )
    commission_percent = st.number_input(
        "Комиссия за операцию, %", 0.0, 2.0, 0.05, 0.01
    )
    half_spread_percent = st.number_input(
        "Половина спреда за операцию, %", 0.0, 2.0, 0.02, 0.01
    )
    slippage_percent = st.number_input(
        "Доп. проскальзывание за операцию, %", 0.0, 2.0, 0.03, 0.01
    )
    run = st.button("Загрузить и протестировать", type="primary")


@st.cache_data(ttl=3600, show_spinner=False)
def load_candles(
    ticker_value: str,
    board_value: str,
    from_value: date,
    to_value: date,
    interval_value: int,
) -> pd.DataFrame:
    return MoexISSClient(board=board_value).get_candles(
        ticker_value, from_value, to_value, interval_value
    )


if run:
    try:
        if start_date >= end_date:
            raise ValueError("Дата начала должна быть раньше даты окончания.")

        with st.spinner("Загружаю свечи MOEX и считаю бэктест…"):
            candles = load_candles(
                ticker, board, start_date, end_date, interval
            )
            signals = generate_sma_signals(
                candles,
                SmaCrossoverConfig(
                    fast_window=int(fast),
                    slow_window=int(slow),
                ),
            )
            result = run_backtest(
                signals,
                BacktestConfig(
                    initial_capital=float(initial_capital),
                    commission_rate=float(commission_percent) / 100.0,
                    half_spread_rate=float(half_spread_percent) / 100.0,
                    slippage_rate=float(slippage_percent) / 100.0,
                    position_fraction=float(position_fraction),
                    annual_periods=None,
                ),
            )

        metrics = result.metrics
        cols = st.columns(8)
        cols[0].metric(
            "Итоговый капитал",
            f"{metrics['ending_capital']:,.0f} ₽",
        )
        cols[1].metric(
            "Доходность",
            f"{metrics['total_return']:.1%}",
        )
        cols[2].metric("CAGR", f"{metrics['cagr']:.1%}")
        cols[3].metric(
            "Макс. просадка",
            f"{metrics['max_drawdown']:.1%}",
        )
        cols[4].metric("Sharpe", f"{metrics['sharpe']:.2f}")
        cols[5].metric("Sortino", f"{metrics['sortino']:.2f}")
        cols[6].metric("Calmar", f"{metrics['calmar']:.2f}")
        cols[7].metric(
            "Завершённых сделок",
            f"{int(metrics['completed_trades'])}",
        )

        st.subheader("Цена и скользящие средние")
        st.line_chart(
            result.curve[["close", "sma_fast", "sma_slow"]].dropna()
        )

        st.subheader("Кривая капитала")
        st.line_chart(
            result.curve[["equity", "buy_hold_equity"]].rename(
                columns={
                    "equity": "Стратегия",
                    "buy_hold_equity": "Buy & hold",
                }
            )
        )

        st.subheader("Просадка")
        st.area_chart(result.curve[["drawdown"]])

        st.subheader("Сделки")
        if result.trades.empty:
            st.info("На выбранном периоде сделок нет.")
        else:
            st.dataframe(result.trades, use_container_width=True)
            csv_data = result.trades.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "Скачать сделки CSV",
                csv_data,
                file_name=f"{ticker}_trades.csv",
                mime="text/csv",
            )

        with st.expander("Последние строки расчёта"):
            st.dataframe(result.curve.tail(100), use_container_width=True)

    except Exception as exc:
        st.error(f"Ошибка: {exc}")

else:
    st.info(
        "Выберите параметры слева и запустите тест. "
        "SMA-кроссовер здесь служит проверяемым шаблоном архитектуры, "
        "а не утверждением о прибыльности."
    )
