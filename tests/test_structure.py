from ict_bot.ict import structure
from tests.conftest import rows_to_df, zigzag_df


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


def test_swing_masks_match_find_swing_points():
    df = zigzag_df([100, 90, 105, 95, 115, 80], bars_per_leg=3)
    for left, right in [(1, 1), (2, 2), (3, 2)]:
        highs, lows = structure.swing_masks(df, left=left, right=right)
        frame = structure.find_swing_points(df, left=left, right=right)
        assert list(highs) == list(frame["swing_high"])
        assert list(lows) == list(frame["swing_low"])


def test_swing_masks_ignore_ties_and_edges():
    # A tied high can't be a fractal swing, and the first/last `left`/`right`
    # bars can never be confirmed swings.
    df = rows_to_df([
        (10.0, 12.0, 9.0, 11.0),
        (11.0, 15.0, 10.0, 14.0),
        (14.0, 15.0, 13.0, 14.5),  # ties the previous high -> neither is a swing high
        (14.5, 13.0, 8.0, 9.0),
        (9.0, 11.0, 8.5, 10.0),
    ])
    highs, lows = structure.swing_masks(df, left=1, right=1)
    assert not highs[1] and not highs[2]
    assert not highs[0] and not highs[-1]
    assert not lows[0] and not lows[-1]
