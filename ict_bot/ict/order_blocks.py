"""Order blocks: the last opposite-direction candle before the
displacement move that causes a confirmed Break of Structure / Change of
Character.

A bullish order block is the last down-close candle before a strong
up-move that breaks structure to the upside; price is expected to return
to that candle's range once, get bought up ("mitigated"), and continue
higher. Bearish order blocks are the mirror image.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from ict_bot.ict.structure import StructureEvent, Trend


@dataclass(frozen=True)
class OrderBlock:
    index: pd.Timestamp
    top: float
    bottom: float
    direction: str  # "bullish" | "bearish"
    broken_at: pd.Timestamp  # index of the structure break this OB caused
    mitigated: bool = False
    mitigated_at: pd.Timestamp | None = None

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0


def detect_order_blocks(df: pd.DataFrame, events: list[StructureEvent]) -> list[OrderBlock]:
    """For each structure event, walk backwards from the breaking candle to
    find the last opposite-colored candle - that is the order block that
    triggered the displacement leg responsible for the break.
    """
    blocks: list[OrderBlock] = []
    opens = df["open"]
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    for ev in events:
        if ev.index not in df.index:
            continue
        end_i = df.index.get_loc(ev.index)
        if isinstance(end_i, slice):
            end_i = end_i.stop - 1

        if ev.direction == Trend.BULLISH:
            for i in range(end_i, -1, -1):
                if closes.iloc[i] < opens.iloc[i]:
                    blocks.append(
                        OrderBlock(df.index[i], top=float(highs.iloc[i]), bottom=float(lows.iloc[i]),
                                   direction="bullish", broken_at=ev.index)
                    )
                    break
        else:
            for i in range(end_i, -1, -1):
                if closes.iloc[i] > opens.iloc[i]:
                    blocks.append(
                        OrderBlock(df.index[i], top=float(highs.iloc[i]), bottom=float(lows.iloc[i]),
                                   direction="bearish", broken_at=ev.index)
                    )
                    break

    return _mark_mitigations(blocks, df)


def _mark_mitigations(blocks: list[OrderBlock], df: pd.DataFrame) -> list[OrderBlock]:
    result = []
    for ob in blocks:
        future = df[df.index > ob.broken_at]
        mitigated = False
        mitigated_at = None
        for ts, row in future.iterrows():
            if ob.direction == "bullish" and row["low"] <= ob.top:
                mitigated, mitigated_at = True, ts
                break
            if ob.direction == "bearish" and row["high"] >= ob.bottom:
                mitigated, mitigated_at = True, ts
                break
        result.append(replace(ob, mitigated=mitigated, mitigated_at=mitigated_at))
    return result


def unmitigated_order_blocks(blocks: list[OrderBlock], direction: str | None = None) -> list[OrderBlock]:
    out = [b for b in blocks if not b.mitigated]
    if direction is not None:
        out = [b for b in out if b.direction == direction]
    return out
