"""Premium / discount zones and the Optimal Trade Entry (OTE) range.

Given a dealing range (a leg from a swing low to a swing high, or vice
versa), ICT splits it at the 50% midpoint: the upper half is "premium"
(favor selling/shorting) and the lower half is "discount" (favor
buying/longing). The Optimal Trade Entry zone is the 61.8%-79% Fibonacci
retracement of the most recent displacement leg - the sweet spot ICT
looks to enter a trade from once a bias has been established.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Zone(Enum):
    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"


@dataclass(frozen=True)
class DealingRange:
    low: float
    high: float

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0

    def zone_of(self, price: float, equilibrium_band_pct: float = 0.0) -> Zone:
        mid = self.midpoint
        span = self.high - self.low
        if span <= 0:
            return Zone.EQUILIBRIUM
        band = span * (equilibrium_band_pct / 100.0)
        if price > mid + band / 2:
            return Zone.PREMIUM
        if price < mid - band / 2:
            return Zone.DISCOUNT
        return Zone.EQUILIBRIUM

    def fib_level(self, ratio: float, direction: str) -> float:
        """Price at Fibonacci ``ratio`` retracement of this range.

        ``direction='up'``   -> range formed low->high (retracement measured down from high)
        ``direction='down'`` -> range formed high->low (retracement measured up from low)
        """
        span = self.high - self.low
        if direction == "up":
            return self.high - span * ratio
        return self.low + span * ratio


@dataclass(frozen=True)
class OTEZone:
    top: float
    bottom: float
    direction: str  # "bullish" | "bearish"

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


def optimal_trade_entry(rng: DealingRange, direction: str, low_ratio: float = 0.618, high_ratio: float = 0.79) -> OTEZone:
    """Compute the OTE (62%-79% retracement) zone of a displacement leg.

    ``direction='bullish'``: leg ran from ``rng.low`` up to ``rng.high``;
    entries are sought on the pullback between the 61.8% and 79% retracement
    levels measured down from the high.
    ``direction='bearish'``: mirror image, leg ran from high down to low.
    """
    fib_direction = "up" if direction == "bullish" else "down"
    level_low = rng.fib_level(low_ratio, fib_direction)
    level_high = rng.fib_level(high_ratio, fib_direction)
    top, bottom = max(level_low, level_high), min(level_low, level_high)
    return OTEZone(top=top, bottom=bottom, direction=direction)
