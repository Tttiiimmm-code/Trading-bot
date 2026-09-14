"""Liquidity pools and liquidity sweeps.

Retail stop-losses cluster just beyond equal highs/lows, old swing points
and round numbers. ICT calls the resting orders above equal highs
"buy-side liquidity" and below equal lows "sell-side liquidity". A
"sweep" (a.k.a. stop hunt / liquidity grab) is a candle that wicks beyond
one of these pools and then closes back on the other side of it - price
took the liquidity and reversed, which ICT reads as a high-probability
reversal signal, especially inside a kill zone.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from ict_bot.ict.structure import find_swing_points


@dataclass(frozen=True)
class LiquidityPool:
    price: float
    kind: str  # "buy_side" (above equal highs) | "sell_side" (below equal lows)
    touches: tuple[pd.Timestamp, ...]
    swept: bool = False
    swept_at: pd.Timestamp | None = None


def _cluster(points: list[tuple[pd.Timestamp, float]], tolerance_pct: float) -> list[list[tuple[pd.Timestamp, float]]]:
    if not points:
        return []
    points = sorted(points, key=lambda p: p[1])
    clusters: list[list[tuple[pd.Timestamp, float]]] = [[points[0]]]
    for ts, price in points[1:]:
        ref_price = clusters[-1][0][1]
        if ref_price == 0:
            same = price == 0
        else:
            same = abs(price - ref_price) / abs(ref_price) * 100 <= tolerance_pct
        if same:
            clusters[-1].append((ts, price))
        else:
            clusters.append([(ts, price)])
    return clusters


def find_liquidity_pools(
    df: pd.DataFrame, tolerance_pct: float = 0.05, min_touches: int = 2, left: int = 2, right: int = 2
) -> list[LiquidityPool]:
    """Cluster swing highs/lows that sit within ``tolerance_pct`` of each
    other into equal-high / equal-low liquidity pools.
    """
    swings = find_swing_points(df, left=left, right=right)
    highs = [(df.index[i], float(df["high"].iloc[i])) for i in range(len(df)) if swings["swing_high"].iloc[i]]
    lows = [(df.index[i], float(df["low"].iloc[i])) for i in range(len(df)) if swings["swing_low"].iloc[i]]

    pools: list[LiquidityPool] = []
    for cluster in _cluster(highs, tolerance_pct):
        if len(cluster) >= min_touches:
            level = sum(p for _, p in cluster) / len(cluster)
            pools.append(LiquidityPool(price=level, kind="buy_side", touches=tuple(t for t, _ in cluster)))
    for cluster in _cluster(lows, tolerance_pct):
        if len(cluster) >= min_touches:
            level = sum(p for _, p in cluster) / len(cluster)
            pools.append(LiquidityPool(price=level, kind="sell_side", touches=tuple(t for t, _ in cluster)))

    return _mark_sweeps(pools, df)


def _mark_sweeps(pools: list[LiquidityPool], df: pd.DataFrame) -> list[LiquidityPool]:
    result = []
    for pool in pools:
        last_touch = max(pool.touches)
        future = df[df.index > last_touch]
        swept, swept_at = False, None
        for ts, row in future.iterrows():
            if pool.kind == "buy_side" and row["high"] > pool.price and row["close"] < pool.price:
                swept, swept_at = True, ts
                break
            if pool.kind == "sell_side" and row["low"] < pool.price and row["close"] > pool.price:
                swept, swept_at = True, ts
                break
        result.append(replace(pool, swept=swept, swept_at=swept_at))
    return result


def unswept_pools(pools: list[LiquidityPool], kind: str | None = None) -> list[LiquidityPool]:
    out = [p for p in pools if not p.swept]
    if kind is not None:
        out = [p for p in out if p.kind == kind]
    return out


def recent_sweep(pools: list[LiquidityPool], as_of: pd.Timestamp, within_bars: int, df: pd.DataFrame) -> LiquidityPool | None:
    """Return the most recent swept pool whose sweep happened within the
    last ``within_bars`` candles counted back from ``as_of`` (inclusive).
    """
    if as_of not in df.index:
        return None
    as_of_i = df.index.get_loc(as_of)
    cutoff = df.index[max(0, as_of_i - within_bars)]
    candidates = [p for p in pools if p.swept and cutoff <= p.swept_at <= as_of]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.swept_at)
