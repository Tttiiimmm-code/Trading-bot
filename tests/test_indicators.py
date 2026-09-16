import math

from ict_bot.indicators import atr, donchian, true_range
from tests.conftest import rows_to_df


def test_true_range_uses_the_bars_own_range_without_a_gap():
    df = rows_to_df([(100.0, 110.0, 90.0, 105.0), (105.0, 112.0, 102.0, 108.0)])
    tr = true_range(df)
    assert tr[0] == 20.0  # first bar: high - low
    assert tr[1] == 10.0  # no gap, so still high - low


def test_true_range_captures_a_gap_against_the_previous_close():
    df = rows_to_df([(100.0, 101.0, 99.0, 100.0), (150.0, 152.0, 148.0, 151.0)])
    tr = true_range(df)
    assert tr[1] == 52.0  # 152 high vs the 100 previous close, not the 4-wide bar


def test_atr_averages_the_true_range():
    df = rows_to_df([(100.0, 110.0, 90.0, 100.0)] * 5)
    values = atr(df, period=3)
    assert all(v == 20.0 for v in values)


def test_atr_warm_up_uses_what_history_exists():
    df = rows_to_df([(100.0, 110.0, 90.0, 100.0), (100.0, 130.0, 70.0, 100.0)])
    values = atr(df, period=10)
    assert values[0] == 20.0
    assert values[1] == 40.0  # mean of 20 and 60, not NaN


def test_donchian_excludes_the_current_bar():
    # The last bar makes a new high; the channel it is judged against must
    # still be the one formed by the bars before it.
    rows = [(10.0, 12.0, 8.0, 11.0)] * 3 + [(11.0, 50.0, 10.0, 49.0)]
    df = rows_to_df(rows)
    upper, lower = donchian(df, period=3)
    assert upper[3] == 12.0
    assert lower[3] == 8.0


def test_donchian_is_nan_before_a_full_period():
    df = rows_to_df([(10.0, 12.0, 8.0, 11.0)] * 5)
    upper, lower = donchian(df, period=3)
    assert all(math.isnan(x) for x in upper[:3])
    assert not math.isnan(upper[3])


def test_donchian_tracks_a_rising_channel():
    rows = [(10.0, 12.0, 8.0, 11.0), (11.0, 14.0, 10.0, 13.0), (13.0, 16.0, 12.0, 15.0), (15.0, 18.0, 14.0, 17.0)]
    df = rows_to_df(rows)
    upper, lower = donchian(df, period=2)
    assert upper[2] == 14.0 and lower[2] == 8.0
    assert upper[3] == 16.0 and lower[3] == 10.0
