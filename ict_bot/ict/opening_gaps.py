"""New Day / New Week Opening Gaps (NDOG / NWOG).

The gap between one period's close and the next period's open. ICT treats
these as unfilled liquidity voids that price is drawn back into, and uses
their midpoint as a reference the same way an FVG's consequent
encroachment is used.

In 24/7 crypto the "gap" is usually tiny - there is no weekend close - so
these levels matter far less than on futures, where the concept comes
from. ``min_gap_pct`` exists to discard the noise-sized ones.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from ict_bot.ict.htf import resample_ohlcv


@dataclass(frozen=True)
class OpeningGap:
    index: pd.Timestamp  # the open of the new period
    top: float
    bottom: float
    kind: str  # "NDOG" | "NWOG"
    filled: bool = False
    filled_at: pd.Timestamp | None = None

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0


_KIND_BY_RULE = {"1D": "NDOG", "1W": "NWOG"}


def detect_opening_gaps(df: pd.DataFrame, rule: str = "1D", min_gap_pct: float = 0.0) -> list[OpeningGap]:
    """Gaps between consecutive completed periods' close and open.

    ``min_gap_pct`` is the gap size as a percent of the closing price;
    anything smaller is ignored.
    """
    periods = resample_ohlcv(df, rule)
    if len(periods) < 2:
        return []

    kind = _KIND_BY_RULE.get(rule, rule)
    closes = periods["close"].to_numpy()
    opens = periods["open"].to_numpy()
    index = periods.index

    gaps: list[OpeningGap] = []
    for i in range(1, len(periods)):
        prev_close, new_open = float(closes[i - 1]), float(opens[i])
        if prev_close == new_open:
            continue
        size = abs(new_open - prev_close)
        if prev_close != 0 and (size / abs(prev_close)) * 100 < min_gap_pct:
            continue
        gaps.append(OpeningGap(
            index=index[i],
            top=max(prev_close, new_open),
            bottom=min(prev_close, new_open),
            kind=kind,
        ))
    return _mark_fills(gaps, df)


def _mark_fills(gaps: list[OpeningGap], df: pd.DataFrame) -> list[OpeningGap]:
    """A gap counts as filled once price trades back through its far side."""
    result = []
    for gap in gaps:
        future = df[df.index > gap.index]
        filled, filled_at = False, None
        for ts, row in future.iterrows():
            if row["low"] <= gap.bottom and row["high"] >= gap.top:
                filled, filled_at = True, ts
                break
        result.append(replace(gap, filled=filled, filled_at=filled_at))
    return result


def unfilled_opening_gaps(gaps: list[OpeningGap]) -> list[OpeningGap]:
    return [g for g in gaps if not g.filled]
