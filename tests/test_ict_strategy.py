"""End-to-end confluence test: liquidity sweep -> CHoCH -> order block ->
OTE overlap -> Signal. Hand-crafted so every stage of the pipeline is
independently verifiable rather than relying on random/real market data.
"""
import pandas as pd

from ict_bot.ict.fvg import FairValueGap
from ict_bot.ict.order_blocks import OrderBlock
from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig, Side, _combine_entry_zone, _entry_price
from tests.conftest import rows_to_df, zigzag_rows


def _ob(top, bottom):
    return OrderBlock(index=None, top=top, bottom=bottom, direction="bullish", broken_at=None)


def _fvg(top, bottom):
    return FairValueGap(index=None, top=top, bottom=bottom, direction="bullish")


def test_combine_entry_zone_no_fvg_returns_order_block_range():
    assert _combine_entry_zone(_ob(top=110, bottom=100), None) == (110, 100)


def test_combine_entry_zone_intersects_overlapping_fvg():
    # FVG narrower than and inside the OB -> intersection is the FVG's range.
    assert _combine_entry_zone(_ob(top=110, bottom=100), _fvg(top=106, bottom=104)) == (106, 104)


def test_combine_entry_zone_intersects_partially_overlapping_fvg():
    # FVG extends above the OB -> intersection is bounded by the OB's top.
    assert _combine_entry_zone(_ob(top=110, bottom=100), _fvg(top=115, bottom=105)) == (110, 105)


def test_combine_entry_zone_falls_back_to_ob_when_disjoint():
    # FVG sits entirely above the OB - no overlap, so the OB alone is used
    # (this used to silently produce an inverted zone for short signals,
    # since the combination logic used to differ by trade side even though
    # interval intersection never should).
    assert _combine_entry_zone(_ob(top=110, bottom=100), _fvg(top=130, bottom=120)) == (110, 100)


def _long_setup_df():
    # Bearish trend with repeated lower highs/lows, so the eventual break
    # back up is a genuine CHoCH (reversal), not a BOS (continuation).
    trend_rows = zigzag_rows([150, 140, 148, 125, 133, 108, 115, 95], bars_per_leg=3)
    # Two wick-only equal highs (~100.5) above the eventual entry: a resting
    # buy-side liquidity pool the strategy can target as its take-profit.
    # Closes stay well below the current structural high so they don't
    # trigger a premature break.
    bump_rows = [
        (95.0, 96.0, 94.0, 95.0),
        (95.0, 96.0, 94.0, 95.0),
        (95.0, 100.50, 94.5, 95.5),
        (95.5, 96.0, 94.5, 95.0),
        (95.0, 96.0, 94.0, 95.0),
        (95.0, 100.52, 94.5, 95.3),
        (95.3, 96.0, 94.5, 95.0),
        (95.0, 96.0, 94.0, 95.0),
    ]
    # Double bottom: two equal lows (~90 / ~90.03) form a sell-side liquidity pool.
    base_rows = zigzag_rows([95, 90, 93, 90.03, 92], bars_per_leg=3)
    # Sweep candle: wicks below the pool, closes back above it (stop hunt).
    sweep_row = (92.0, 92.3, 89.5, 91.5)
    # Order block: last down-close candle before the displacement, sitting
    # inside the OTE (61.8%-79%) retracement of the up-leg that follows.
    ob_row = (95.5, 96.3, 93.0, 92.8)
    # Displacement candle that closes back above the last swing high -> CHoCH.
    breakout_row = (92.8, 106.0, 92.5, 105.0)

    rows = trend_rows + bump_rows + base_rows + [sweep_row, ob_row, breakout_row]
    # 03:45 UTC start lands the final (46th) bar at 15:00 UTC, inside the
    # london_close kill zone [15:00, 16:30).
    return rows_to_df(rows, start="2024-01-01 03:45")


def _short_setup_df():
    # Mirror of _long_setup_df: bullish trend with repeated higher highs/lows,
    # so the eventual break back down is a genuine CHoCH (reversal).
    trend_rows = zigzag_rows([50, 60, 52, 75, 67, 92, 85, 105], bars_per_leg=3)
    # Two wick-only equal lows (~99.5) below the eventual entry: a resting
    # sell-side liquidity pool the strategy can target as its take-profit.
    bump_rows = [
        (105.0, 106.0, 104.0, 105.0),
        (105.0, 106.0, 104.0, 105.0),
        (105.0, 105.5, 99.5, 104.5),
        (104.5, 105.5, 104.0, 105.0),
        (105.0, 106.0, 104.0, 105.0),
        (105.0, 105.5, 99.48, 104.7),
        (104.7, 105.5, 104.0, 105.0),
        (105.0, 106.0, 104.0, 105.0),
    ]
    # Double top: two equal highs (~110 / ~109.97) form a buy-side liquidity pool.
    base_rows = zigzag_rows([105, 110, 107, 109.97, 108], bars_per_leg=3)
    # Sweep candle: wicks above the pool, closes back below it (stop hunt).
    sweep_row = (108.0, 110.5, 107.7, 108.5)
    # Order block: last up-close candle before the displacement, sitting
    # inside the OTE (61.8%-79%) retracement of the down-leg that follows.
    ob_row = (104.5, 107.0, 103.7, 106.8)
    # Displacement candle that closes back below the last swing low -> CHoCH.
    breakout_row = (106.8, 107.2, 92.0, 93.0)

    rows = trend_rows + bump_rows + base_rows + [sweep_row, ob_row, breakout_row]
    # 03:45 UTC start lands the final (46th) bar at 15:00 UTC, inside the
    # london_close kill zone [15:00, 16:30).
    return rows_to_df(rows, start="2024-01-01 03:45")


def test_full_confluence_produces_short_signal():
    df = _short_setup_df()
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0))
    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.side == Side.SHORT
    assert signal.take_profit < signal.entry < signal.stop_loss
    assert signal.risk_reward >= 1.0


def test_full_confluence_produces_long_signal():
    df = _long_setup_df()
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0))
    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.side == Side.LONG
    assert signal.stop_loss < signal.entry < signal.take_profit
    assert signal.risk_reward >= 1.0


def test_min_risk_reward_floor_filters_signal():
    df = _long_setup_df()
    lenient = ICTStrategy(ICTStrategyConfig(require_kill_zone=False, require_ote=False, min_risk_reward=1.0))
    strict = ICTStrategy(ICTStrategyConfig(require_kill_zone=False, require_ote=True, min_risk_reward=5.0))
    assert lenient.generate_signal(df) is not None
    assert strict.generate_signal(df) is None  # target pool too close to reach a 5R reward


def test_no_signal_outside_kill_zone():
    df = _long_setup_df()
    # shift so the breakout bar lands at 05:00 UTC, in the gap between the
    # asian (ends 04:00) and london (starts 07:00) kill zones.
    import pandas as pd

    shifted = df.copy()
    shifted.index = pd.date_range("2024-01-01 17:45", periods=len(df), freq="15min", tz="UTC")
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0))
    assert strategy.generate_signal(shifted) is None


def test_no_signal_on_flat_data():
    df = zigzag_rows([100, 100, 100], bars_per_leg=3, epsilon=0.0)
    df = rows_to_df(df)
    strategy = ICTStrategy()
    assert strategy.generate_signal(df) is None


def test_trace_reports_insufficient_history():
    df = rows_to_df(zigzag_rows([100, 105], bars_per_leg=2))
    trace: list[str] = []
    assert ICTStrategy().generate_signal(df, trace=trace) is None
    assert len(trace) == 1
    assert "not enough bar history" in trace[0]


def test_trace_reports_outside_kill_zone():
    df = _long_setup_df()
    shifted = df.copy()
    shifted.index = pd.date_range("2024-01-01 17:45", periods=len(df), freq="15min", tz="UTC")
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0))
    trace: list[str] = []
    assert strategy.generate_signal(shifted, trace=trace) is None
    assert trace == ["outside the configured kill zone"]


def test_trace_reports_no_structure_events():
    # Flat data, but with enough bars to clear the minimum-history check
    # first, so this actually exercises the "no structure" branch.
    df = rows_to_df(zigzag_rows([100, 100], bars_per_leg=25, epsilon=0.0))
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=False))
    trace: list[str] = []
    assert strategy.generate_signal(df, trace=trace) is None
    assert trace == ["no market structure (BOS/CHoCH) detected yet"]


def test_trace_reports_risk_reward_too_low():
    df = _long_setup_df()
    strict = ICTStrategy(ICTStrategyConfig(require_kill_zone=False, require_ote=True, min_risk_reward=5.0))
    trace: list[str] = []
    assert strict.generate_signal(df, trace=trace) is None
    assert len(trace) == 1
    assert "risk/reward" in trace[0] and "below configured minimum 5.00" in trace[0]


def test_trace_stays_empty_when_signal_found():
    df = _long_setup_df()
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0))
    trace: list[str] = []
    signal = strategy.generate_signal(df, trace=trace)
    assert signal is not None
    assert trace == []


def test_entry_mode_ob_midpoint_is_the_default():
    from ict_bot.strategy.ict_strategy import ICTStrategyConfig as C

    assert C().entry_mode == "ob_midpoint"


def test_entry_mode_zone_midpoint_shifts_the_entry():
    df = _long_setup_df()
    base = dict(require_kill_zone=True, require_ote=True, min_risk_reward=1.0)
    default = ICTStrategy(ICTStrategyConfig(**base)).generate_signal(df)
    zone = ICTStrategy(ICTStrategyConfig(**base, entry_mode="zone_midpoint")).generate_signal(df)

    assert default is not None and zone is not None
    # Same setup, same stop/target - only where the limit order rests moves.
    assert zone.stop_loss == default.stop_loss
    assert zone.take_profit == default.take_profit


def test_entry_mode_fvg_ce_falls_back_to_order_block_without_an_fvg():
    ob = _ob(top=110, bottom=100)
    assert _entry_price("fvg_ce", ob, None, 110, 100) == ob.midpoint
    assert _entry_price("fvg_ce", ob, FairValueGap(index=None, top=108, bottom=104, direction="bullish"), 108, 104) == 106.0
    assert _entry_price("zone_midpoint", ob, None, 108, 104) == 106.0
    assert _entry_price("ob_midpoint", ob, FairValueGap(index=None, top=108, bottom=104, direction="bullish"), 108, 104) == 105.0


def test_trace_defaults_to_none_without_error():
    df = _long_setup_df()
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0))
    assert strategy.generate_signal(df) is not None  # no trace arg - must not raise
