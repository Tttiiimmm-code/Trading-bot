import pandas as pd

from ict_bot.ict.breakers import active_breakers, detect_breakers
from ict_bot.ict.order_blocks import OrderBlock
from tests.conftest import rows_to_df


def _ob(direction: str, top: float, bottom: float, broken_at: pd.Timestamp, index: pd.Timestamp) -> OrderBlock:
    return OrderBlock(index=index, top=top, bottom=bottom, direction=direction, broken_at=broken_at)


def test_bullish_order_block_flips_to_bearish_breaker_on_close_below():
    rows = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 102.0, 99.5, 101.0),
        (101.0, 101.5, 94.0, 95.0),  # closes below the block's bottom (98)
        (95.0, 96.0, 94.0, 95.5),
    ]
    df = rows_to_df(rows)
    ob = _ob("bullish", top=100.0, bottom=98.0, broken_at=df.index[0], index=df.index[0])

    breakers = detect_breakers(df, [ob])
    assert len(breakers) == 1
    assert breakers[0].direction == "bearish"
    assert breakers[0].origin_direction == "bullish"
    assert breakers[0].broken_at == df.index[2]
    assert breakers[0].top == 100.0 and breakers[0].bottom == 98.0


def test_bearish_order_block_flips_to_bullish_breaker_on_close_above():
    rows = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 103.0, 99.5, 102.5),  # closes above the block's top (102)
        (102.5, 104.0, 102.0, 103.5),
    ]
    df = rows_to_df(rows)
    ob = _ob("bearish", top=102.0, bottom=100.0, broken_at=df.index[0], index=df.index[0])

    breakers = detect_breakers(df, [ob])
    assert len(breakers) == 1
    assert breakers[0].direction == "bullish"
    assert breakers[0].broken_at == df.index[1]


def test_wick_through_the_block_is_not_a_break():
    rows = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 90.0, 99.0),  # wicks well below 98 but closes above it
        (99.0, 100.0, 98.5, 99.5),
    ]
    df = rows_to_df(rows)
    ob = _ob("bullish", top=100.0, bottom=98.0, broken_at=df.index[0], index=df.index[0])
    assert detect_breakers(df, [ob]) == []


def test_break_before_the_structure_event_is_ignored():
    rows = [
        (100.0, 101.0, 90.0, 91.0),  # closes below 98, but this is before broken_at
        (91.0, 101.0, 90.5, 100.0),
        (100.0, 101.0, 99.0, 100.5),
    ]
    df = rows_to_df(rows)
    ob = _ob("bullish", top=100.0, bottom=98.0, broken_at=df.index[1], index=df.index[0])
    assert detect_breakers(df, [ob]) == []


def test_no_blocks_no_breakers():
    df = rows_to_df([(100.0, 101.0, 99.0, 100.0)] * 3)
    assert detect_breakers(df, []) == []


def test_breaker_contains_and_midpoint():
    df = rows_to_df([(100.0, 101.0, 99.0, 100.0), (100.0, 101.0, 90.0, 95.0)])
    ob = _ob("bullish", top=100.0, bottom=98.0, broken_at=df.index[0], index=df.index[0])
    breaker = detect_breakers(df, [ob])[0]
    assert breaker.midpoint == 99.0
    assert breaker.contains(99.0) and not breaker.contains(97.0)


def test_active_breakers_filters_by_direction_and_time():
    df = rows_to_df([(100.0, 101.0, 99.0, 100.0), (100.0, 101.0, 90.0, 95.0), (95.0, 96.0, 94.0, 95.0)])
    ob = _ob("bullish", top=100.0, bottom=98.0, broken_at=df.index[0], index=df.index[0])
    breakers = detect_breakers(df, [ob])

    assert active_breakers(breakers, "bearish", as_of=df.index[2]) == breakers
    assert active_breakers(breakers, "bullish", as_of=df.index[2]) == []
    assert active_breakers(breakers, "bearish", as_of=df.index[0]) == []
