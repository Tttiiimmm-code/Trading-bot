"""Higher-timeframe (HTF) context.

ICT is explicitly multi-timeframe: the higher timeframe sets the bias
("draw on liquidity"), the lower timeframe only supplies the entry. Taking
a lower-timeframe reversal against the higher-timeframe trend is the
classic way to collect a string of small losses.

The HTF series is derived by resampling the trading timeframe's own window
rather than fetching a second feed, so callers keep passing a single
DataFrame around. Only *completed* HTF candles are used: the bucket the
current bar sits in is still forming, and letting a half-built candle flip
the bias mid-move would make the filter jitter (it is not look-ahead - the
partial candle only contains past bars - it is just noisy).
"""
from __future__ import annotations

import pandas as pd

from ict_bot.ict.structure import Trend, current_trend, detect_structure

_AGGREGATION = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_ohlcv(df: pd.DataFrame, rule: str, drop_incomplete: bool = True) -> pd.DataFrame:
    """Resample an OHLCV frame to a higher timeframe (e.g. ``"4h"``).

    ``drop_incomplete`` removes the final bucket unless the window happens
    to end exactly on its boundary, so downstream analysis only ever sees
    closed HTF candles.
    """
    columns = [c for c in _AGGREGATION if c in df.columns]
    resampled = df.resample(rule, label="left", closed="left").agg({c: _AGGREGATION[c] for c in columns})
    resampled = resampled.dropna(subset=["open", "high", "low", "close"])
    if drop_incomplete and not resampled.empty:
        step = pd.Timedelta(rule)
        last_bucket_end = resampled.index[-1] + step
        # The bucket is only complete once the data runs past its end; bars
        # are timestamped at their open, so the final bar's own length counts.
        if df.index[-1] + _bar_interval(df) < last_bucket_end:
            resampled = resampled.iloc[:-1]
    return resampled


def _bar_interval(df: pd.DataFrame) -> pd.Timedelta:
    if len(df.index) < 2:
        return pd.Timedelta(0)
    return df.index[-1] - df.index[-2]


def htf_bias(df: pd.DataFrame, rule: str, left: int = 2, right: int = 2, min_bars: int = 20) -> Trend:
    """Market-structure bias on the higher timeframe.

    Returns the trend implied by the most recent completed HTF structure
    event, or ``Trend.UNKNOWN`` when there isn't enough HTF history to say
    (callers decide whether unknown means "block" or "allow").
    """
    htf = resample_ohlcv(df, rule)
    if len(htf) < max(min_bars, left + right + 2):
        return Trend.UNKNOWN
    return current_trend(detect_structure(htf, left=left, right=right))


def bias_allows(bias: Trend, direction: Trend, allow_unknown: bool = True) -> bool:
    """Whether a trade in ``direction`` is permitted under ``bias``."""
    if bias == Trend.UNKNOWN:
        return allow_unknown
    return bias == direction
