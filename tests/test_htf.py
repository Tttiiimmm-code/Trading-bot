from ict_bot.ict.htf import bias_allows, htf_bias, resample_ohlcv
from ict_bot.ict.structure import Trend
from tests.conftest import rows_to_df, zigzag_rows


def test_resample_aggregates_ohlcv_correctly():
    # 8 x 15m bars = two complete 1h buckets.
    rows = [
        (10.0, 12.0, 9.0, 11.0),
        (11.0, 14.0, 10.5, 13.0),
        (13.0, 13.5, 8.0, 9.0),
        (9.0, 10.0, 8.5, 9.5),
        (20.0, 22.0, 19.0, 21.0),
        (21.0, 24.0, 20.5, 23.0),
        (23.0, 23.5, 18.0, 19.0),
        (19.0, 20.0, 18.5, 19.5),
    ]
    df = rows_to_df(rows, start="2024-01-01 00:00")
    htf = resample_ohlcv(df, "1h")

    assert len(htf) == 2
    assert htf["open"].iloc[0] == 10.0  # first open of the bucket
    assert htf["high"].iloc[0] == 14.0  # max high
    assert htf["low"].iloc[0] == 8.0  # min low
    assert htf["close"].iloc[0] == 9.5  # last close
    assert htf["volume"].iloc[0] == 4.0  # summed


def test_resample_drops_the_still_forming_bucket():
    # 6 bars: one complete 1h bucket + half of the next.
    df = rows_to_df([(10.0, 11.0, 9.0, 10.0)] * 6, start="2024-01-01 00:00")
    assert len(resample_ohlcv(df, "1h")) == 1
    assert len(resample_ohlcv(df, "1h", drop_incomplete=False)) == 2


def test_resample_keeps_bucket_that_ends_exactly_on_the_boundary():
    df = rows_to_df([(10.0, 11.0, 9.0, 10.0)] * 8, start="2024-01-01 00:00")
    assert len(resample_ohlcv(df, "1h")) == 2


def test_htf_bias_follows_higher_timeframe_structure():
    # A sustained uptrend on the 15m series resamples into a bullish 1h trend.
    up = rows_to_df(zigzag_rows([100, 90, 115, 105, 130, 120, 150], bars_per_leg=24), start="2024-01-01 00:00")
    assert htf_bias(up, "1h") == Trend.BULLISH

    down = rows_to_df(zigzag_rows([150, 160, 130, 140, 110, 120, 90], bars_per_leg=24), start="2024-01-01 00:00")
    assert htf_bias(down, "1h") == Trend.BEARISH


def test_htf_bias_unknown_without_enough_history():
    df = rows_to_df([(10.0, 11.0, 9.0, 10.0)] * 20, start="2024-01-01 00:00")
    assert htf_bias(df, "4h") == Trend.UNKNOWN


def test_bias_allows_matches_direction():
    assert bias_allows(Trend.BULLISH, Trend.BULLISH)
    assert not bias_allows(Trend.BULLISH, Trend.BEARISH)
    assert bias_allows(Trend.UNKNOWN, Trend.BEARISH, allow_unknown=True)
    assert not bias_allows(Trend.UNKNOWN, Trend.BEARISH, allow_unknown=False)


def test_resample_handles_frame_without_volume():
    df = rows_to_df([(10.0, 11.0, 9.0, 10.0)] * 8, start="2024-01-01 00:00").drop(columns=["volume"])
    htf = resample_ohlcv(df, "1h")
    assert list(htf.columns) == ["open", "high", "low", "close"]
    assert len(htf) == 2


def test_strategy_htf_filter_blocks_opposing_setup():
    from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig
    from tests.test_ict_strategy import _long_setup_df

    df = _long_setup_df()
    base = dict(require_kill_zone=True, require_ote=True, min_risk_reward=1.0)
    assert ICTStrategy(ICTStrategyConfig(**base)).generate_signal(df) is not None

    # The setup is a long; forcing the HTF read to bearish must veto it.
    blocked = ICTStrategy(ICTStrategyConfig(**base, htf_bias_timeframe="1h", htf_bias_allow_unknown=False))
    trace: list[str] = []
    assert blocked.generate_signal(df, trace=trace) is None
    assert "bias" in trace[-1]


def test_strategy_htf_filter_off_by_default():
    from ict_bot.strategy.ict_strategy import ICTStrategyConfig

    assert ICTStrategyConfig().htf_bias_timeframe is None
