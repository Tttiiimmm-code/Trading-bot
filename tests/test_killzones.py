import pandas as pd

from ict_bot.ict.killzones import DEFAULT_KILL_ZONES, KillZone, is_in_kill_zone, tag_kill_zones


def test_in_london_kill_zone():
    ts = pd.Timestamp("2024-01-01 08:30", tz="UTC")
    assert is_in_kill_zone(ts)


def test_outside_all_kill_zones():
    ts = pd.Timestamp("2024-01-01 05:30", tz="UTC")
    assert not is_in_kill_zone(ts)


def test_wrap_around_midnight_window():
    zone = KillZone("late", 22.0, 2.0)
    assert zone.contains(pd.Timestamp("2024-01-01 23:00", tz="UTC"))
    assert zone.contains(pd.Timestamp("2024-01-01 01:00", tz="UTC"))
    assert not zone.contains(pd.Timestamp("2024-01-01 12:00", tz="UTC"))


def test_naive_timestamp_treated_as_utc():
    ts = pd.Timestamp("2024-01-01 08:30")
    assert is_in_kill_zone(ts)


def test_tag_kill_zones_returns_aligned_series():
    idx = pd.date_range("2024-01-01 00:00", periods=48, freq="15min", tz="UTC")
    df = pd.DataFrame(index=idx)
    tags = tag_kill_zones(df, DEFAULT_KILL_ZONES)
    assert list(tags.index) == list(df.index)
    assert tags.dtype == bool
