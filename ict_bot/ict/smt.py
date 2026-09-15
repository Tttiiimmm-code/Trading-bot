"""SMT divergence (Smart Money Technique).

Two assets that normally move together disagreeing at a turning point is,
in ICT's reading, a footprint of the party that has to hedge: one market
runs the stops below its old low while the correlated one refuses to
follow. The one that failed to make the new extreme is the one showing
strength.

- **Bullish SMT**: asset A makes a *lower low* while correlated asset B
  makes a *higher low* - the sell-side raid on A was not confirmed.
- **Bearish SMT**: asset A makes a *higher high* while B makes a *lower
  high*.

Nothing here assumes which asset is "leading"; the divergence is read
relative to the asset being traded (``df_a``).
"""
from __future__ import annotations

import pandas as pd

from ict_bot.ict.structure import Trend, swing_masks


def _last_two_swings(df: pd.DataFrame, kind: str, left: int, right: int) -> tuple[float, float] | None:
    """The two most recent confirmed swing prices of ``kind`` ("high"/"low"),
    oldest first. ``None`` when there aren't two.
    """
    high_mask, low_mask = swing_masks(df, left=left, right=right)
    mask = high_mask if kind == "high" else low_mask
    values = df["high"].to_numpy() if kind == "high" else df["low"].to_numpy()
    idx = mask.nonzero()[0]
    if len(idx) < 2:
        return None
    return float(values[idx[-2]]), float(values[idx[-1]])


def smt_divergence(df_a: pd.DataFrame, df_b: pd.DataFrame, left: int = 2, right: int = 2) -> Trend:
    """Divergence between ``df_a`` (the traded market) and ``df_b``.

    Both frames should cover the same period; only their overlapping range
    is compared, so a correlated feed with a different start is fine.
    Returns ``Trend.UNKNOWN`` when the two agree or there isn't enough
    swing structure to judge.
    """
    start = max(df_a.index[0], df_b.index[0])
    end = min(df_a.index[-1], df_b.index[-1])
    a, b = df_a.loc[start:end], df_b.loc[start:end]
    if len(a) < (left + right + 2) or len(b) < (left + right + 2):
        return Trend.UNKNOWN

    lows_a, lows_b = _last_two_swings(a, "low", left, right), _last_two_swings(b, "low", left, right)
    if lows_a and lows_b:
        a_lower_low = lows_a[1] < lows_a[0]
        b_higher_low = lows_b[1] > lows_b[0]
        if a_lower_low and b_higher_low:
            return Trend.BULLISH

    highs_a, highs_b = _last_two_swings(a, "high", left, right), _last_two_swings(b, "high", left, right)
    if highs_a and highs_b:
        a_higher_high = highs_a[1] > highs_a[0]
        b_lower_high = highs_b[1] < highs_b[0]
        if a_higher_high and b_lower_high:
            return Trend.BEARISH

    return Trend.UNKNOWN


def smt_confirms(divergence: Trend, direction: Trend, allow_unknown: bool = False) -> bool:
    """Whether an SMT reading supports a trade in ``direction``."""
    if divergence == Trend.UNKNOWN:
        return allow_unknown
    return divergence == direction
