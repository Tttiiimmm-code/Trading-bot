from ict_bot.ict import structure
from tests.conftest import zigzag_df


def test_pure_uptrend_only_bullish_bos():
    df = zigzag_df([100, 90, 105, 95, 115], bars_per_leg=3)
    events = structure.detect_structure(df, left=2, right=2)
    assert len(events) >= 1
    assert all(e.direction == structure.Trend.BULLISH for e in events)
    assert all(e.event == structure.EventType.BOS for e in events)


def test_pure_downtrend_only_bearish_bos():
    df = zigzag_df([100, 110, 90, 105, 80], bars_per_leg=3)
    events = structure.detect_structure(df, left=2, right=2)
    assert len(events) >= 1
    assert all(e.direction == structure.Trend.BEARISH for e in events)
    assert all(e.event == structure.EventType.BOS for e in events)


def test_reversal_produces_choch():
    df = zigzag_df([100, 90, 105, 95, 115, 80], bars_per_leg=3)
    events = structure.detect_structure(df, left=2, right=2)
    assert events[-1].event == structure.EventType.CHOCH
    assert events[-1].direction == structure.Trend.BEARISH
    assert structure.current_trend(events) == structure.Trend.BEARISH


def test_no_swings_no_events_on_flat_data():
    df = zigzag_df([100, 100, 100], bars_per_leg=3, epsilon=0.0)
    events = structure.detect_structure(df, left=2, right=2)
    assert events == []


def test_find_swing_points_marks_interior_pivots():
    df = zigzag_df([100, 90, 105, 95], bars_per_leg=3)
    swings = structure.find_swing_points(df, left=2, right=2)
    assert swings["swing_low"].any()
    assert swings["swing_high"].any()
