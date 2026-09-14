"""Shared synthetic-data helpers for tests.

``zigzag_df`` builds an OHLCV DataFrame that walks straight-line legs
between a list of pivot prices. Every *interior* pivot (not the first or
last) becomes an unambiguous fractal swing high/low as long as
``bars_per_leg >= left/right`` on both sides, which makes it easy to
hand-craft data with known, verifiable market structure.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def zigzag_rows(pivots: list[float], bars_per_leg: int = 3, epsilon: float = 0.3) -> list[tuple[float, float, float, float]]:
    closes = [pivots[0]]
    for i in range(len(pivots) - 1):
        seg = np.linspace(pivots[i], pivots[i + 1], bars_per_leg + 1)[1:]
        closes.extend(seg)
    closes_arr = np.array(closes)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    highs = closes_arr + epsilon
    lows = closes_arr - epsilon
    return list(zip(opens, highs, lows, closes_arr))


def rows_to_df(rows: list[tuple[float, float, float, float]], start: str = "2024-01-01", freq: str = "15min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1.0
    return df


def zigzag_df(pivots: list[float], bars_per_leg: int = 3, epsilon: float = 0.3, start: str = "2024-01-01", freq: str = "15min") -> pd.DataFrame:
    return rows_to_df(zigzag_rows(pivots, bars_per_leg=bars_per_leg, epsilon=epsilon), start=start, freq=freq)
