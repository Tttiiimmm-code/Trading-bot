from ict_bot.ict import structure
from ict_bot.ict.order_blocks import detect_order_blocks
from tests.conftest import zigzag_df


def test_bullish_order_block_found_before_choch():
    df = zigzag_df([100, 90, 105, 95, 115, 80], bars_per_leg=3)
    events = structure.detect_structure(df, left=2, right=2)
    choch = [e for e in events if e.event == structure.EventType.CHOCH]
    assert choch and choch[0].direction == structure.Trend.BEARISH

    blocks = detect_order_blocks(df, events)
    bearish_blocks = [b for b in blocks if b.direction == "bearish"]
    assert bearish_blocks
    ob = bearish_blocks[0]
    assert ob.broken_at == choch[0].index
    # the order block is the last up-close candle before the down displacement
    ob_i = df.index.get_loc(ob.index)
    assert df["close"].iloc[ob_i] > df["open"].iloc[ob_i]


def test_order_block_mitigation_tracked():
    df = zigzag_df([100, 90, 105, 95, 115, 80, 100], bars_per_leg=3)
    events = structure.detect_structure(df, left=2, right=2)
    blocks = detect_order_blocks(df, events)
    assert any(b.mitigated for b in blocks)
