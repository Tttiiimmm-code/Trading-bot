from ict_bot.ict.fvg import detect_fvgs
from tests.conftest import rows_to_df


def test_bullish_fvg_detected():
    rows = [
        (100.0, 101.0, 99.0, 100.5),
        (100.5, 108.0, 100.0, 107.5),  # displacement candle
        (107.5, 110.0, 106.0, 109.0),  # candle3.low(106) > candle1.high(101) -> gap [101, 106]
    ]
    df = rows_to_df(rows)
    gaps = detect_fvgs(df)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.direction == "bullish"
    assert gap.bottom == 101.0
    assert gap.top == 106.0
    assert not gap.filled


def test_bearish_fvg_detected():
    rows = [
        (110.0, 111.0, 109.0, 109.5),
        (109.5, 110.0, 102.0, 102.5),  # displacement candle down
        (102.5, 104.0, 100.0, 101.0),  # candle1.low(109) > candle3.high(104) -> gap [104, 109]
    ]
    df = rows_to_df(rows)
    gaps = detect_fvgs(df)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.direction == "bearish"
    assert gap.bottom == 104.0
    assert gap.top == 109.0


def test_fvg_marked_filled_when_price_returns():
    rows = [
        (100.0, 101.0, 99.0, 100.5),
        (100.5, 108.0, 100.0, 107.5),
        (107.5, 110.0, 106.0, 109.0),
        (109.0, 109.5, 100.5, 101.0),  # dips back into the gap (bottom=101)
    ]
    df = rows_to_df(rows)
    gaps = detect_fvgs(df)
    assert gaps[0].filled
    assert gaps[0].filled_at == df.index[3]


def test_no_gap_when_candles_overlap():
    rows = [
        (100.0, 102.0, 99.0, 100.5),
        (100.5, 103.0, 99.5, 101.5),
        (101.5, 104.0, 100.5, 102.5),
    ]
    df = rows_to_df(rows)
    assert detect_fvgs(df) == []
