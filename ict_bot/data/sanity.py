"""Guards against the two ways a price feed lies.

Both were found in this project's own data rather than imagined. Pulling
the same eight markets from a second exchange turned up a KuCoin LTC
candle whose high was 43% above what OKX recorded for the same period, and
several series are missing whole stretches of bars.

Neither is harmless here. A false high sets the Donchian channel for the
next twenty bars and suppresses real breakouts; a false low fires a stop
that never happened. A gap makes a "20-bar channel" silently span more
than twenty bars of real time, because the indicators work positionally.

Both guards are deliberately blunt. A rule fine enough to catch every bad
print is fine enough to be tuned until it produces a desired backtest, and
that is a worse failure than missing a few.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# A wick may reach this many times the surrounding median bar range before
# it is treated as a misprint. Five is loose: real crashes pass, the 43%
# LTC spike does not.
DEFAULT_WICK_LIMIT = 5.0
_WINDOW = 50


def clip_bad_wicks(df: pd.DataFrame, limit: float = DEFAULT_WICK_LIMIT) -> tuple[pd.DataFrame, int]:
    """Pull implausible wicks back to the limit. Returns the frame and how
    many bars were touched.

    Only the wick is clipped, never the open or close: a bad print moves
    the extreme of a candle, while open and close are struck prices that
    the rest of the bar has to agree with.
    """
    if limit <= 0 or len(df) < 10:
        return df, 0
    out = df.copy()
    body_high = out[["open", "close"]].max(axis=1)
    body_low = out[["open", "close"]].min(axis=1)
    typical = (out["high"] - out["low"]).rolling(_WINDOW, min_periods=10, center=True).median()
    allowance = typical * limit
    too_high = out["high"] > body_high + allowance
    too_low = out["low"] < body_low - allowance
    out.loc[too_high, "high"] = (body_high + allowance)[too_high]
    out.loc[too_low, "low"] = (body_low - allowance)[too_low]
    return out, int(too_high.sum() + too_low.sum())


def find_gaps(df: pd.DataFrame, timeframe_delta: pd.Timedelta) -> list[tuple[pd.Timestamp, int]]:
    """Where bars are missing, and how many at each break."""
    if len(df) < 2 or timeframe_delta <= pd.Timedelta(0):
        return []
    diffs = df.index.to_series().diff()
    gaps = []
    for ts, delta in diffs.items():
        if pd.isna(delta) or delta <= timeframe_delta:
            continue
        gaps.append((ts, int(delta / timeframe_delta) - 1))
    return gaps


def check(df: pd.DataFrame, label: str, timeframe_delta: pd.Timedelta | None = None,
          wick_limit: float = DEFAULT_WICK_LIMIT) -> pd.DataFrame:
    """Clean a frame and say out loud what was wrong with it.

    Logged rather than raised: a feed with one bad print is still worth
    trading on, and a bot that refuses to start over a data blemish is
    less useful than one that says what it found.
    """
    problems = []
    o, h, l, c = (df[x] for x in ("open", "high", "low", "close"))
    impossible = int(((h < l) | (c > h) | (c < l) | (o > h) | (o < l)).sum())
    if impossible:
        problems.append(f"{impossible} bar(s) where the OHLC values contradict each other")
    nonpositive = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    if nonpositive:
        problems.append(f"{nonpositive} bar(s) with a non-positive price")
    if df.index.duplicated().any():
        problems.append(f"{int(df.index.duplicated().sum())} duplicate timestamp(s)")
    if not df.index.is_monotonic_increasing:
        problems.append("timestamps are not in order")

    if timeframe_delta is not None:
        gaps = find_gaps(df, timeframe_delta)
        if gaps:
            missing = sum(count for _, count in gaps)
            problems.append(
                f"{len(gaps)} gap(s) totalling {missing} missing bar(s) "
                f"({missing / (len(df) + missing) * 100:.1f}% of the period); "
                f"indicators span more real time than their bar count implies")

    cleaned, clipped = clip_bad_wicks(df, wick_limit)
    if clipped:
        problems.append(f"{clipped} implausible wick(s) clipped back to {wick_limit:g}x the "
                        f"surrounding median range")

    if problems:
        logger.warning("Data quality for %s: %s", label, "; ".join(problems))
    return cleaned


def clip_with_context(history: pd.DataFrame, fresh: pd.DataFrame,
                      limit: float = DEFAULT_WICK_LIMIT) -> tuple[pd.DataFrame, int]:
    """Clip newly arrived bars, judged against the history before them.

    The live loop sees one or two bars at a time, which is far too few to
    establish what a normal range looks like. Judging a new bar against
    the window already held is the difference between catching a misprint
    and calling every volatile candle one.
    """
    if limit <= 0 or fresh.empty:
        return fresh, 0
    combined = pd.concat([history, fresh])
    cleaned, _ = clip_bad_wicks(combined, limit)
    result = cleaned.loc[fresh.index]
    touched = int((result["high"] != fresh["high"]).sum() + (result["low"] != fresh["low"]).sum())
    return result, touched
