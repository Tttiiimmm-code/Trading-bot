import pandas as pd

from ict_bot.ict.session_levels import period_extremes, reference_levels, reference_pools
from tests.conftest import rows_to_df


def _two_days() -> pd.DataFrame:
    # 96 x 15m bars = one full day, then 8 more bars into the next day.
    day1 = [(100.0, 110.0, 90.0, 105.0)] + [(105.0, 106.0, 95.0, 100.0)] * 95
    day2 = [(100.0, 102.0, 99.0, 101.0)] * 8
    return rows_to_df(day1 + day2, start="2024-01-01 00:00")


def test_period_extremes_only_covers_completed_periods():
    df = _two_days()
    daily = period_extremes(df, "1D")
    assert len(daily) == 1  # the second day is still forming
    assert daily["high"].iloc[0] == 110.0
    assert daily["low"].iloc[0] == 90.0


def test_reference_levels_reports_previous_day_and_current_open():
    df = _two_days()
    levels = reference_levels(df)
    assert levels.previous_day_high == 110.0
    assert levels.previous_day_low == 90.0
    assert levels.daily_open == 100.0  # open of the day in progress


def test_reference_levels_empty_without_a_completed_period():
    df = rows_to_df([(100.0, 101.0, 99.0, 100.0)] * 4, start="2024-01-01 00:00")
    levels = reference_levels(df)
    assert levels.previous_day_high is None
    assert levels.previous_day_low is None
    assert levels.daily_open == 100.0


def test_reference_pools_marks_a_swept_previous_day_high():
    day1 = [(100.0, 110.0, 90.0, 105.0)] + [(105.0, 106.0, 95.0, 100.0)] * 95
    # Next day wicks above the previous day's high (110) and closes back below.
    day2 = [(100.0, 112.0, 99.0, 104.0)] + [(104.0, 105.0, 103.0, 104.0)] * 7
    df = rows_to_df(day1 + day2, start="2024-01-01 00:00")

    pools = reference_pools(df, rules=("1D",))
    buy_side = [p for p in pools if p.kind == "buy_side"]
    assert len(buy_side) == 1
    assert buy_side[0].price == 110.0
    assert buy_side[0].swept
    assert buy_side[0].swept_at == df.index[96]


def test_reference_pools_unswept_level_stays_unswept():
    day1 = [(100.0, 110.0, 90.0, 105.0)] + [(105.0, 106.0, 95.0, 100.0)] * 95
    day2 = [(100.0, 104.0, 99.0, 101.0)] * 8  # never reaches 110
    df = rows_to_df(day1 + day2, start="2024-01-01 00:00")

    pools = reference_pools(df, rules=("1D",))
    buy_side = [p for p in pools if p.kind == "buy_side"]
    assert buy_side and not buy_side[0].swept


def test_reference_pools_produces_both_sides_per_period():
    df = _two_days()
    pools = reference_pools(df, rules=("1D",))
    assert {p.kind for p in pools} == {"buy_side", "sell_side"}


def test_session_liquidity_off_by_default():
    from ict_bot.strategy.ict_strategy import ICTStrategyConfig

    assert ICTStrategyConfig().use_session_liquidity is False
