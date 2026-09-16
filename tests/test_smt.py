from ict_bot.ict.smt import smt_confirms, smt_divergence
from ict_bot.ict.structure import Trend
from tests.conftest import rows_to_df, zigzag_rows


def test_bullish_smt_when_a_makes_a_lower_low_and_b_does_not():
    # A: 100 -> 90 -> 95 -> 85 (lower low). B: mirror but its second dip holds higher.
    a = rows_to_df(zigzag_rows([100, 90, 95, 85, 92], bars_per_leg=3))
    b = rows_to_df(zigzag_rows([100, 90, 95, 93, 99], bars_per_leg=3))
    assert smt_divergence(a, b) == Trend.BULLISH


def test_bearish_smt_when_a_makes_a_higher_high_and_b_does_not():
    a = rows_to_df(zigzag_rows([100, 110, 105, 118, 108], bars_per_leg=3))
    b = rows_to_df(zigzag_rows([100, 110, 105, 107, 100], bars_per_leg=3))
    assert smt_divergence(a, b) == Trend.BEARISH


def test_no_divergence_when_both_markets_agree():
    a = rows_to_df(zigzag_rows([100, 90, 95, 85, 92], bars_per_leg=3))
    b = rows_to_df(zigzag_rows([100, 90, 95, 84, 91], bars_per_leg=3))
    assert smt_divergence(a, b) == Trend.UNKNOWN


def test_unknown_without_enough_structure():
    a = rows_to_df([(100.0, 101.0, 99.0, 100.0)] * 4)
    b = rows_to_df([(100.0, 101.0, 99.0, 100.0)] * 4)
    assert smt_divergence(a, b) == Trend.UNKNOWN


def test_only_the_overlapping_range_is_compared():
    a = rows_to_df(zigzag_rows([100, 90, 95, 85, 92], bars_per_leg=3), start="2024-01-01 00:00")
    b = rows_to_df(zigzag_rows([100, 90, 95, 93, 99], bars_per_leg=3), start="2024-01-01 00:00")
    # b starting later must not crash and still reads over the common window
    assert smt_divergence(a, b.iloc[3:]) in {Trend.BULLISH, Trend.UNKNOWN}


def test_smt_confirms_requires_matching_direction():
    assert smt_confirms(Trend.BULLISH, Trend.BULLISH)
    assert not smt_confirms(Trend.BULLISH, Trend.BEARISH)
    assert not smt_confirms(Trend.UNKNOWN, Trend.BULLISH)
    assert smt_confirms(Trend.UNKNOWN, Trend.BULLISH, allow_unknown=True)


def test_strategy_ignores_smt_without_a_correlated_frame():
    from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig
    from tests.test_ict_strategy import _long_setup_df

    df = _long_setup_df()
    cfg = ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0, require_smt=True)
    # require_smt is on but no correlated data was supplied - must not block.
    assert ICTStrategy(cfg).generate_signal(df) is not None


def test_strategy_smt_filter_blocks_unconfirmed_setup():
    from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig
    from tests.test_ict_strategy import _long_setup_df

    df = _long_setup_df()
    cfg = ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0,
                            require_smt=True, smt_allow_unknown=False)
    trace: list[str] = []
    # A copy of itself can never diverge from itself.
    assert ICTStrategy(cfg).generate_signal(df, trace=trace, correlated=df.copy()) is None
    assert "SMT" in trace[-1]
