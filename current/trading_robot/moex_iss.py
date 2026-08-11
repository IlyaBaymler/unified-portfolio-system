from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

import pandas as pd
import requests


class MoexISSError(RuntimeError):
    """Raised when MOEX ISS cannot return valid candle data."""


@dataclass(slots=True)
class MoexISSClient:
    """Small client for historical MOEX ISS candles.

    MOEX ISS is used here only for research and backtesting. It is not an
    order-entry interface.
    """

    timeout_seconds: float = 20.0
    board: str = "TQBR"

    BASE_URL: Final[str] = "https://iss.moex.com/iss"

    def get_candles(
        self,
        ticker: str,
        date_from: str | date | datetime,
        date_to: str | date | datetime,
        interval: int = 60,
    ) -> pd.DataFrame:
        ticker = ticker.strip().upper()
        if not ticker:
            raise ValueError("Ticker must not be empty.")
        if interval not in {1, 10, 60, 24, 7, 31, 4}:
            raise ValueError(
                "Unsupported MOEX interval. Common values: 1, 10, 60, 24."
            )

        url = (
            f"{self.BASE_URL}/engines/stock/markets/shares/"
            f"boards/{self.board}/securities/{ticker}/candles.json"
        )
        params: dict[str, object] = {
            "from": self._format_date(date_from),
            "till": self._format_date(date_to),
            "interval": interval,
            "iss.meta": "off",
            "iss.only": "candles",
            "candles.columns": (
                "begin,open,high,low,close,volume,value"
            ),
        }

        all_rows: list[list[object]] = []
        columns: list[str] | None = None
        offset = 0

        with requests.Session() as session:
            while True:
                params["start"] = offset
                try:
                    response = session.get(
                        url, params=params, timeout=self.timeout_seconds
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (requests.RequestException, ValueError) as exc:
                    raise MoexISSError(f"MOEX ISS request failed: {exc}") from exc

                block = payload.get("candles", {})
                columns = block.get("columns", columns)
                rows = block.get("data", [])
                if not rows:
                    break

                all_rows.extend(rows)
                offset += len(rows)

                # ISS usually returns pages up to 500 rows. A shorter page is
                # normally the final page, but continuing once more is safe.
                if len(rows) < 500:
                    break

        if not columns or not all_rows:
            raise MoexISSError(
                f"No candles returned for {ticker} on board {self.board}."
            )

        frame = pd.DataFrame(all_rows, columns=columns)
        required = {"begin", "open", "high", "low", "close", "volume"}
        missing = required.difference(frame.columns)
        if missing:
            raise MoexISSError(f"MOEX response misses columns: {sorted(missing)}")

        frame["begin"] = pd.to_datetime(frame["begin"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "value"]:
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

        frame = (
            frame.dropna(subset=["begin", "open", "high", "low", "close"])
            .drop_duplicates(subset=["begin"], keep="last")
            .sort_values("begin")
            .set_index("begin")
        )
        return frame

    @staticmethod
    def _format_date(value: str | date | datetime) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
