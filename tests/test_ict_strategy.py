"""End-to-end confluence test: liquidity sweep -> CHoCH -> order block ->
OTE overlap -> Signal. Hand-crafted so every stage of the pipeline is
independently verifiable rather than relying on random/real market data.
"""
from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig, Side
from tests.conftest import rows_to_df, zigzag_rows


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


def test_signal_entry_stays_inside_the_ote_band_when_required():
    """Regression: entry used to be hardcoded to ob.midpoint. The gating
    check only proves the OB (or OB/FVG) zone *overlaps* the OTE band, not
    that ob.midpoint itself falls inside it - an off-center overlap could
    produce a signal whose entry sits outside the very OTE retracement
    require_ote=True is supposed to guarantee.

    In the base fixture, the order block is [93.0, 96.3] with midpoint
    94.65, and the default 61.8%-79% OTE band comfortably contains it - so
    that case doesn't exercise the bug. Narrow the OTE ratios (still
    valid, still overlapping the OB) to a band that excludes 94.65 while
    still overlapping [93.0, 96.3]: the old code would have returned
    entry=94.65, outside this band.
    """
    from ict_bot.ict import premium_discount as pd_mod

    df = _long_setup_df()
    cfg = ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=0.1, ote_low_ratio=0.746, ote_high_ratio=0.785)
    strategy = ICTStrategy(cfg)
    signal = strategy.generate_signal(df)

    assert signal is not None
    # Independently recompute the OTE band from this fixture's sweep price
    # and CHoCH breakout close (same values the strategy itself derives).
    sweep_price = 89.715
    last_event_price = 105.0
    ote = pd_mod.optimal_trade_entry(pd_mod.DealingRange(low=sweep_price, high=last_event_price), "bullish", cfg.ote_low_ratio, cfg.ote_high_ratio)
    assert ote.bottom <= signal.entry <= ote.top
    assert signal.entry != 94.65  # the old, out-of-band ob.midpoint
