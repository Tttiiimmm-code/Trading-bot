"""Fair Value Gaps (FVG) / imbalances.

A bullish FVG is the price gap left behind after a strong up move: for
three consecutive candles (1, 2, 3), candle 3's low is above candle 1's
high, leaving ``[candle1.high, candle3.low]`` untraded. Bearish FVGs are
the mirror image. ICT treats these gaps as magnets/entries: price often
returns ("fills") into the gap before continuing in the direction of the
displacement.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd


@dataclass(frozen=True)
class FairValueGap:
    index: pd.Timestamp  # timestamp of the middle (displacement) candle
    top: float
    bottom: float
    direction: str  # "bullish" | "bearish"
    filled: bool = False
    filled_at: pd.Timestamp | None = None

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0


def detect_fvgs(df: pd.DataFrame, min_gap_pct: float = 0.0) -> list[FairValueGap]:
    """Detect all FVGs in ``df`` and mark whether/when they were later filled.

    ``min_gap_pct`` filters out negligible gaps: the gap size as a percent
    of the middle candle's close must exceed this threshold (0 = keep all).
    """
    gaps: list[FairValueGap] = []
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    n = len(df)

    for i in range(1, n - 1):
        c1_high, c1_low = highs[i - 1], lows[i - 1]
        c3_high, c3_low = highs[i + 1], lows[i + 1]
        mid_close = closes[i]

        if c3_low > c1_high:
            gap_size = c3_low - c1_high
            if mid_close == 0 or (gap_size / mid_close) * 100 >= min_gap_pct:
                gaps.append(FairValueGap(df.index[i], top=c3_low, bottom=c1_high, direction="bullish"))
        elif c1_low > c3_high:
            gap_size = c1_low - c3_high
            if mid_close == 0 or (gap_size / mid_close) * 100 >= min_gap_pct:
                gaps.append(FairValueGap(df.index[i], top=c1_low, bottom=c3_high, direction="bearish"))

    return _mark_fills(gaps, df)


def _mark_fills(gaps: list[FairValueGap], df: pd.DataFrame) -> list[FairValueGap]:
    result = []
    for gap in gaps:
        future = df[df.index > gap.index]
        filled = False
        filled_at = None
        for ts, row in future.iterrows():
            if gap.direction == "bullish" and row["low"] <= gap.bottom:
                filled, filled_at = True, ts
                break
            if gap.direction == "bearish" and row["high"] >= gap.top:
                filled, filled_at = True, ts
                break
        result.append(replace(gap, filled=filled, filled_at=filled_at))
    return result


def unfilled_fvgs(gaps: list[FairValueGap], direction: str | None = None) -> list[FairValueGap]:
    out = [g for g in gaps if not g.filled]
    if direction is not None:
        out = [g for g in out if g.direction == direction]
    return out
