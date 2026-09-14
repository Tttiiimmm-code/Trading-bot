"""Market structure: swing points, Break of Structure (BOS) and
Change of Character (CHoCH).

ICT reads market structure as a sequence of confirmed swing highs/lows.
A trend is bullish while price keeps printing higher highs / higher lows;
a break above the last swing high while trending bearish is a CHoCH
(reversal signal), while the same break while already trending bullish is
just a BOS (continuation / trend confirmation). The mirror image applies
to the downside.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Trend(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


class EventType(Enum):
    BOS = "BOS"       # break of structure: trend continuation
    CHOCH = "CHoCH"    # change of character: trend reversal


@dataclass(frozen=True)
class SwingPoint:
    index: pd.Timestamp
    price: float
    kind: str  # "high" | "low"


@dataclass(frozen=True)
class StructureEvent:
    index: pd.Timestamp
    price: float
    event: EventType
    direction: Trend  # resulting trend direction after this event
    broken_swing: SwingPoint


def find_swing_points(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.DataFrame:
    """Detect fractal swing highs/lows.

    A bar is a swing high if its ``high`` is strictly greater than the
    ``high`` of ``left`` bars before and ``right`` bars after it (and
    symmetric for swing lows). Returns a DataFrame indexed like ``df``
    with boolean columns ``swing_high`` / ``swing_low``.

    Note: a swing point at index ``i`` is only *confirmed* once bar
    ``i + right`` has closed - callers doing live/streaming detection must
    respect that lag to avoid look-ahead bias.
    """
    n = len(df)
    swing_high = [False] * n
    swing_low = [False] * n
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    for i in range(left, n - right):
        window_high = highs[i - left : i + right + 1]
        if highs[i] == window_high.max() and (window_high == highs[i]).sum() == 1:
            swing_high[i] = True
        window_low = lows[i - left : i + right + 1]
        if lows[i] == window_low.min() and (window_low == lows[i]).sum() == 1:
            swing_low[i] = True

    return pd.DataFrame({"swing_high": swing_high, "swing_low": swing_low}, index=df.index)


def detect_structure(df: pd.DataFrame, left: int = 2, right: int = 2) -> list[StructureEvent]:
    """Walk the confirmed swing points in time order and emit BOS/CHoCH
    events whenever price closes beyond the last relevant swing point.

    Uses candle ``close`` (not wick) to confirm a break, which is the
    common ICT convention for structure breaks.
    """
    swings = find_swing_points(df, left=left, right=right)
    closes = df["close"]

    events: list[StructureEvent] = []

    trend = Trend.UNKNOWN
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None

    for i in range(len(df)):
        # register newly confirmed swings at bar i (a swing formed at i-right):
        # the reference point always tracks the MOST RECENTLY confirmed
        # swing, which is standard ICT structure-tracking behaviour.
        source_i = i - right
        if source_i >= 0:
            if swings["swing_high"].iloc[source_i]:
                last_high = SwingPoint(df.index[source_i], float(df["high"].iloc[source_i]), "high")
            if swings["swing_low"].iloc[source_i]:
                last_low = SwingPoint(df.index[source_i], float(df["low"].iloc[source_i]), "low")

        price = float(closes.iloc[i])
        ts = df.index[i]

        if last_high is not None and price > last_high.price:
            new_trend = Trend.BULLISH
            event_type = EventType.CHOCH if trend == Trend.BEARISH else EventType.BOS
            events.append(StructureEvent(ts, price, event_type, new_trend, last_high))
            trend = new_trend
            last_high = None  # consumed; wait for the next fresh swing high to form

        if last_low is not None and price < last_low.price:
            new_trend = Trend.BEARISH
            event_type = EventType.CHOCH if trend == Trend.BULLISH else EventType.BOS
            events.append(StructureEvent(ts, price, event_type, new_trend, last_low))
            trend = new_trend
            last_low = None  # consumed; wait for the next fresh swing low to form

    return events


def current_trend(events: list[StructureEvent]) -> Trend:
    return events[-1].direction if events else Trend.UNKNOWN
