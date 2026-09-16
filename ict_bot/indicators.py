"""Plain technical indicators, shared by strategies.

Deliberately small and dependency-free: each function takes an OHLCV frame
and returns a numpy array aligned to it, so callers can index by bar
without pandas overhead in hot loops.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def true_range(df: pd.DataFrame) -> np.ndarray:
    """Wilder's true range: the largest of the bar's own range, and its
    high/low measured against the previous close (which captures gaps).

    The first bar has no previous close, so it falls back to its own range.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]

    return np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))


def atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Average true range as a simple rolling mean of the true range.

    Bars before a full period is available get the mean of what exists so
    far rather than NaN, so callers never have to special-case the warm-up
    (they should still require enough history before trading).
    """
    tr = true_range(df)
    if period <= 1:
        return tr
    # min_periods=1 gives exactly the warm-up behaviour described above: for
    # the first bars the window is clipped to what exists. A running
    # cumulative sum would do the same but subtracts two large nearly-equal
    # numbers, which loses precision on long histories at high prices.
    return pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy()


def donchian(df: pd.DataFrame, period: int) -> tuple[np.ndarray, np.ndarray]:
    """Highest high and lowest low of the ``period`` bars *before* each bar.

    Excluding the bar itself is the point: a breakout has to be judged
    against the channel as it stood before this bar printed, otherwise the
    bar that makes the new high trivially "breaks" its own channel.
    Positions without a full period of history are NaN.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    if n > period:
        # Windows over high[:-1] end one bar early, which is the exclusion:
        # window k covers high[k:k+period] and lands at bar k+period.
        upper[period:] = sliding_window_view(high[:-1], period).max(axis=1)
        lower[period:] = sliding_window_view(low[:-1], period).min(axis=1)
    return upper, lower
