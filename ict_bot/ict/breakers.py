"""Breaker blocks.

An order block that *fails* - price trades decisively through it instead
of respecting it - flips polarity. A bullish order block that price closes
below becomes a bearish **breaker**: on the retest from underneath, the
same range is expected to cap price rather than support it. ICT treats a
breaker retest as a higher-conviction entry than a virgin order block,
because the level has already demonstrated that the orders sitting there
got run.

Direction here always describes what the level does *now*: a "bearish"
breaker is one to sell into, regardless of the polarity it had as an order
block.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict_bot.ict.order_blocks import OrderBlock


@dataclass(frozen=True)
class Breaker:
    index: pd.Timestamp  # the original order block's candle
    top: float
    bottom: float
    direction: str  # "bullish" | "bearish" - what it does now, post-flip
    broken_at: pd.Timestamp  # when price closed through the original block
    origin_direction: str  # the polarity it had as an order block

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


def detect_breakers(df: pd.DataFrame, blocks: list[OrderBlock]) -> list[Breaker]:
    """Find order blocks that price has closed through, flipped to their
    breaker polarity.

    A bullish order block breaks when a candle *closes* below its bottom
    (a wick through it is just a raid, not a failure); a bearish one
    breaks on a close above its top.
    """
    if not blocks:
        return []

    closes = df["close"].to_numpy()
    index = df.index
    breakers: list[Breaker] = []

    for ob in blocks:
        start = index.searchsorted(ob.broken_at, side="right")
        if start >= len(index):
            continue
        after = closes[start:]
        if ob.direction == "bullish":
            hits = (after < ob.bottom).nonzero()[0]
            flipped = "bearish"
        else:
            hits = (after > ob.top).nonzero()[0]
            flipped = "bullish"
        if len(hits) == 0:
            continue
        breakers.append(
            Breaker(
                index=ob.index,
                top=ob.top,
                bottom=ob.bottom,
                direction=flipped,
                broken_at=index[start + int(hits[0])],
                origin_direction=ob.direction,
            )
        )
    return breakers


def active_breakers(breakers: list[Breaker], direction: str, as_of: pd.Timestamp) -> list[Breaker]:
    """Breakers of ``direction`` that had already flipped by ``as_of``,
    most recently flipped first.
    """
    out = [b for b in breakers if b.direction == direction and b.broken_at <= as_of]
    return sorted(out, key=lambda b: b.broken_at, reverse=True)
