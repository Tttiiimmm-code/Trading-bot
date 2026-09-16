import pytest

from ict_bot.strategy.ict_strategy import Side
from ict_bot.strategy.trend_strategy import TrendStrategy, TrendStrategyConfig
from tests.conftest import rows_to_df

CFG = dict(entry_period=5, exit_period=3, atr_period=5, regime_period=0)


def _flat(n: int, price: float = 100.0, spread: float = 1.0) -> list[tuple]:
    return [(price, price + spread, price - spread, price)] * n


def _rising(n: int, start: float = 100.0, step: float = 5.0) -> list[tuple]:
    rows = []
    price = start
    for _ in range(n):
        rows.append((price, price + step, price - 1.0, price + step * 0.9))
        price += step
    return rows


def test_breakout_above_the_channel_produces_a_long():
    rows = _flat(20) + [(100.0, 130.0, 99.0, 125.0)]
    signal = TrendStrategy(TrendStrategyConfig(**CFG)).generate_signal(rows_to_df(rows))

    assert signal is not None
    assert signal.side == Side.LONG
    assert signal.entry == 125.0
    assert signal.take_profit is None  # open-ended, the trail decides
    assert signal.trail_distance is not None and signal.trail_distance > 0
    assert signal.stop_loss < signal.entry


def test_breakout_below_the_channel_produces_a_short():
    rows = _flat(20) + [(100.0, 101.0, 70.0, 75.0)]
    signal = TrendStrategy(TrendStrategyConfig(**CFG)).generate_signal(rows_to_df(rows))
    assert signal is not None and signal.side == Side.SHORT
    assert signal.stop_loss > signal.entry


def test_no_signal_inside_the_channel():
    trace: list[str] = []
    signal = TrendStrategy(TrendStrategyConfig(**CFG)).generate_signal(rows_to_df(_flat(25)), trace=trace)
    assert signal is None
    assert "no breakout" in trace[0]


def test_stop_and_trail_scale_with_atr():
    rows = _flat(20, spread=2.0) + [(100.0, 130.0, 99.0, 125.0)]
    cfg = TrendStrategyConfig(**CFG, atr_stop_multiple=2.0, trail_atr_multiple=3.0)
    signal = TrendStrategy(cfg).generate_signal(rows_to_df(rows))
    risk = signal.entry - signal.stop_loss
    # trail is 1.5x the initial stop because 3 ATR vs 2 ATR
    assert signal.trail_distance == pytest.approx(risk * 1.5)


def test_not_enough_history_is_reported():
    trace: list[str] = []
    assert TrendStrategy(TrendStrategyConfig(**CFG)).generate_signal(rows_to_df(_flat(3)), trace=trace) is None
    assert "not enough bar history" in trace[0]


def test_regime_filter_blocks_a_long_below_the_average():
    # Falling market, then a short-term pop above the 5-bar channel: the
    # long-run average is still far above, so a long must be refused.
    rows = [(200.0 - i * 5, 201.0 - i * 5, 199.0 - i * 5, 200.0 - i * 5) for i in range(25)]
    rows += [(80.0, 120.0, 79.0, 115.0)]
    cfg = TrendStrategyConfig(entry_period=5, exit_period=3, atr_period=5, regime_period=20)
    trace: list[str] = []
    assert TrendStrategy(cfg).generate_signal(rows_to_df(rows), trace=trace) is None
    assert "regime filter" in trace[0]


def test_regime_filter_allows_a_long_in_an_uptrend():
    rows = _rising(30)
    cfg = TrendStrategyConfig(entry_period=5, exit_period=3, atr_period=5, regime_period=20)
    signal = TrendStrategy(cfg).generate_signal(rows_to_df(rows))
    assert signal is not None and signal.side == Side.LONG


def test_direction_can_be_disabled():
    rows = _flat(20) + [(100.0, 130.0, 99.0, 125.0)]
    cfg = TrendStrategyConfig(**CFG, allow_long=False)
    trace: list[str] = []
    assert TrendStrategy(cfg).generate_signal(rows_to_df(rows), trace=trace) is None
    assert "long breakouts disabled" in trace[0]


def test_min_atr_pct_skips_quiet_markets():
    rows = _flat(20, spread=0.01) + [(100.0, 100.5, 99.99, 100.4)]
    cfg = TrendStrategyConfig(**CFG, min_atr_pct=1.0)
    trace: list[str] = []
    assert TrendStrategy(cfg).generate_signal(rows_to_df(rows), trace=trace) is None
    assert "volatility too low" in trace[0]


def test_runs_through_the_backtest_engine_unchanged():
    from ict_bot.backtest.engine import BacktestConfig, BacktestEngine
    from ict_bot.strategy.risk import RiskConfig, RiskManager

    rows = _flat(30) + _rising(40, start=100.0) + _flat(20, price=250.0)
    df = rows_to_df(rows)
    engine = BacktestEngine(df, TrendStrategy(TrendStrategyConfig(**CFG)),
                            RiskManager(RiskConfig()), BacktestConfig(symbol="TEST/USD", window_size=200))
    result = engine.run()
    assert len(result.equity_curve) == len(df)
    assert any(f.reason == "entry" for f in result.fills)


def test_the_no_breakout_message_says_how_far_away_a_signal_is():
    # "inside the channel" stays true for weeks and reads identically
    # whether the market is one tick or ten percent from a signal.
    trace: list[str] = []
    TrendStrategy(TrendStrategyConfig(**CFG)).generate_signal(rows_to_df(_flat(25)), trace=trace)
    assert "needs +1.00% to break up" in trace[0]
    assert "-1.00% to break down" in trace[0]
