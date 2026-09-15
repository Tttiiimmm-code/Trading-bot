from ict_bot.ict.opening_gaps import detect_opening_gaps, unfilled_opening_gaps
from tests.conftest import rows_to_df

BARS_PER_DAY = 96


def _day(open_px: float, high: float, low: float, close_px: float) -> list[tuple]:
    """A day of 15m bars whose aggregate is exactly the given OHLC."""
    middle = [(open_px, high, low, open_px)] + [(open_px, high, low, open_px)] * (BARS_PER_DAY - 2)
    return [(open_px, high, low, open_px)] + middle[1:] + [(open_px, high, low, close_px)]


def test_detects_gap_up_between_days():
    df = rows_to_df(_day(100.0, 105.0, 95.0, 100.0) + _day(110.0, 115.0, 108.0, 112.0), start="2024-01-01 00:00")
    gaps = detect_opening_gaps(df, "1D")

    assert len(gaps) == 1
    assert gaps[0].kind == "NDOG"
    assert gaps[0].bottom == 100.0  # previous close
    assert gaps[0].top == 110.0  # new open
    assert gaps[0].midpoint == 105.0


def test_detects_gap_down_between_days():
    df = rows_to_df(_day(100.0, 105.0, 95.0, 100.0) + _day(90.0, 95.0, 88.0, 92.0), start="2024-01-01 00:00")
    gaps = detect_opening_gaps(df, "1D")
    assert len(gaps) == 1
    assert gaps[0].bottom == 90.0 and gaps[0].top == 100.0


def test_no_gap_when_open_matches_previous_close():
    df = rows_to_df(_day(100.0, 105.0, 95.0, 100.0) + _day(100.0, 105.0, 95.0, 101.0), start="2024-01-01 00:00")
    assert detect_opening_gaps(df, "1D") == []


def test_min_gap_pct_filters_noise_sized_gaps():
    df = rows_to_df(_day(100.0, 105.0, 95.0, 100.0) + _day(100.05, 105.0, 95.0, 101.0), start="2024-01-01 00:00")
    assert len(detect_opening_gaps(df, "1D", min_gap_pct=0.0)) == 1
    assert detect_opening_gaps(df, "1D", min_gap_pct=1.0) == []


def test_gap_marked_filled_once_price_covers_it():
    # Day 3 trades right back across the 100-110 gap.
    df = rows_to_df(
        _day(100.0, 105.0, 95.0, 100.0)
        + _day(110.0, 115.0, 108.0, 112.0)
        + _day(112.0, 115.0, 99.0, 105.0),
        start="2024-01-01 00:00",
    )
    gaps = detect_opening_gaps(df, "1D")
    assert gaps[0].filled
    assert gaps[0].filled_at is not None
    assert unfilled_opening_gaps(gaps) == []


def test_gap_stays_unfilled_when_price_never_returns():
    df = rows_to_df(
        _day(100.0, 105.0, 95.0, 100.0)
        + _day(110.0, 115.0, 108.0, 112.0)
        + _day(112.0, 120.0, 111.0, 118.0),
        start="2024-01-01 00:00",
    )
    gaps = detect_opening_gaps(df, "1D")
    assert not gaps[0].filled
    assert len(unfilled_opening_gaps(gaps)) == 1


def test_needs_two_completed_periods():
    df = rows_to_df(_day(100.0, 105.0, 95.0, 100.0), start="2024-01-01 00:00")
    assert detect_opening_gaps(df, "1D") == []


def test_kill_zone_presets_are_registered():
    from ict_bot.ict.killzones import KILL_ZONE_PRESETS, is_in_kill_zone
    import pandas as pd

    assert set(KILL_ZONE_PRESETS) == {"default", "silver_bullet", "london_ny"}
    sb = KILL_ZONE_PRESETS["silver_bullet"]
    assert is_in_kill_zone(pd.Timestamp("2024-01-01 14:30", tz="UTC"), sb)
    assert not is_in_kill_zone(pd.Timestamp("2024-01-01 16:30", tz="UTC"), sb)
