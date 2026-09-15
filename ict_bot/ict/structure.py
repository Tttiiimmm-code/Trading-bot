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

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


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


def swing_masks(df: pd.DataFrame, left: int = 2, right: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Raw boolean ``(swing_high, swing_low)`` masks - the numpy core behind
    :func:`find_swing_points`, for callers that immediately work on arrays
    anyway (this runs on every bar of a backtest, so the DataFrame wrapper
    is worth skipping there).
    """
    n = len(df)
    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    span = left + right + 1
    if n >= span:
        # Sliding windows of length `span`; window w sits over bars
        # [w, w + span), so its centre bar is w + left.
        high_windows = sliding_window_view(highs, span)
        low_windows = sliding_window_view(lows, span)
        centre_highs = highs[left : n - right]
        centre_lows = lows[left : n - right]
        # Strictly greater than every other bar in the window == it is the
        # max and no other bar ties it.
        swing_high[left : n - right] = (high_windows == centre_highs[:, None]).sum(axis=1) == 1
        swing_high[left : n - right] &= centre_highs == high_windows.max(axis=1)
        swing_low[left : n - right] = (low_windows == centre_lows[:, None]).sum(axis=1) == 1
        swing_low[left : n - right] &= centre_lows == low_windows.min(axis=1)

    return swing_high, swing_low


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
    swing_high, swing_low = swing_masks(df, left=left, right=right)
    return pd.DataFrame({"swing_high": swing_high, "swing_low": swing_low}, index=df.index)


def detect_structure(df: pd.DataFrame, left: int = 2, right: int = 2) -> list[StructureEvent]:
    """Walk the confirmed swing points in time order and emit BOS/CHoCH
    events whenever price closes beyond the last relevant swing point.

    Uses candle ``close`` (not wick) to confirm a break, which is the
    common ICT convention for structure breaks.
    """
    # Scalar access on numpy arrays rather than pandas objects inside the
    # loop: this runs on every bar of every backtest window, and pandas'
    # per-element overhead dominated the whole backtest otherwise.
    swing_high_flags, swing_low_flags = swing_masks(df, left=left, right=right)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    index = df.index

    events: list[StructureEvent] = []

    trend = Trend.UNKNOWN
    # Tracked as bar offsets rather than SwingPoint objects: materialising a
    # Timestamp per bar dominated the loop, and only the (rare) bars that
    # actually emit an event need one.
    last_high_i: int | None = None
    last_low_i: int | None = None

    for i in range(len(df)):
        # register newly confirmed swings at bar i (a swing formed at i-right):
        # the reference point always tracks the MOST RECENTLY confirmed
        # swing, which is standard ICT structure-tracking behaviour.
        source_i = i - right
        if source_i >= 0:
            if swing_high_flags[source_i]:
                last_high_i = source_i
            if swing_low_flags[source_i]:
                last_low_i = source_i

        price = float(closes[i])

        if last_high_i is not None and price > float(highs[last_high_i]):
            new_trend = Trend.BULLISH
            event_type = EventType.CHOCH if trend == Trend.BEARISH else EventType.BOS
            broken = SwingPoint(index[last_high_i], float(highs[last_high_i]), "high")
            events.append(StructureEvent(index[i], price, event_type, new_trend, broken))
            trend = new_trend
            last_high_i = None  # consumed; wait for the next fresh swing high to form

        if last_low_i is not None and price < float(lows[last_low_i]):
            new_trend = Trend.BEARISH
            event_type = EventType.CHOCH if trend == Trend.BULLISH else EventType.BOS
            broken = SwingPoint(index[last_low_i], float(lows[last_low_i]), "low")
            events.append(StructureEvent(index[i], price, event_type, new_trend, broken))
            trend = new_trend
            last_low_i = None  # consumed; wait for the next fresh swing low to form

    return events


def current_trend(events: list[StructureEvent]) -> Trend:
    return events[-1].direction if events else Trend.UNKNOWN
