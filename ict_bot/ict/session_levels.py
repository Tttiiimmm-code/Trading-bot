"""Daily and weekly reference levels.

In ICT these are the liquidity that actually matters: the previous day's
and previous week's high/low (PDH/PDL, PWH/PWL) are where stops rest and
where price is being "drawn" to, and the daily/weekly open is the
reference that splits the range into premium and discount.

The equal-highs/equal-lows clusters in :mod:`ict_bot.ict.liquidity` are a
proxy for the same idea derived purely from swing structure; these levels
are the explicit, calendar-based version, and the two complement each
other. Levels are exposed as :class:`~ict_bot.ict.liquidity.LiquidityPool`
values so they flow through the existing sweep/target machinery unchanged.

Only *completed* periods are used - the day or week currently in progress
has no final high/low yet, and treating its running extreme as a level
would be look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict_bot.ict.htf import resample_ohlcv
from ict_bot.ict.liquidity import LiquidityPool, mark_sweeps


@dataclass(frozen=True)
class ReferenceLevels:
    """Latest completed daily/weekly extremes and opens as of a window's end."""

    previous_day_high: float | None = None
    previous_day_low: float | None = None
    previous_week_high: float | None = None
    previous_week_low: float | None = None
    daily_open: float | None = None
    weekly_open: float | None = None


def period_extremes(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Per-period OHLC for every *completed* period in ``df``."""
    return resample_ohlcv(df, rule)


def reference_levels(df: pd.DataFrame) -> ReferenceLevels:
    """Previous day/week extremes plus the current day's and week's open.

    The opens come from the period in progress (that value is fixed the
    moment the period starts, so it is known, not look-ahead), the
    extremes from the last period that has actually closed.
    """
    daily = resample_ohlcv(df, "1D")
    weekly = resample_ohlcv(df, "1W")
    daily_all = resample_ohlcv(df, "1D", drop_incomplete=False)
    weekly_all = resample_ohlcv(df, "1W", drop_incomplete=False)
    return ReferenceLevels(
        previous_day_high=float(daily["high"].iloc[-1]) if len(daily) else None,
        previous_day_low=float(daily["low"].iloc[-1]) if len(daily) else None,
        previous_week_high=float(weekly["high"].iloc[-1]) if len(weekly) else None,
        previous_week_low=float(weekly["low"].iloc[-1]) if len(weekly) else None,
        daily_open=float(daily_all["open"].iloc[-1]) if len(daily_all) else None,
        weekly_open=float(weekly_all["open"].iloc[-1]) if len(weekly_all) else None,
    )


def reference_pools(df: pd.DataFrame, rules: tuple[str, ...] = ("1D", "1W"), lookback_periods: int = 2) -> list[LiquidityPool]:
    """The last ``lookback_periods`` completed periods' highs/lows as
    liquidity pools, with sweeps marked the same way swing-derived pools
    are.
    """
    pools: list[LiquidityPool] = []
    for rule in rules:
        periods = resample_ohlcv(df, rule)
        if periods.empty:
            continue
        step = pd.Timedelta(rule)
        for ts, row in periods.iloc[-lookback_periods:].iterrows():
            # Anchor the touch on the period's final bar: the level is set
            # by then, and the very next bar (the first of the new period)
            # must already be able to sweep it.
            end_i = df.index.searchsorted(ts + step, side="left") - 1
            if end_i < 0:
                continue
            confirmed_at = df.index[end_i]
            pools.append(LiquidityPool(price=float(row["high"]), kind="buy_side", touches=(confirmed_at,)))
            pools.append(LiquidityPool(price=float(row["low"]), kind="sell_side", touches=(confirmed_at,)))
    return mark_sweeps(pools, df)
