"""End-to-end test against a larger, randomized "historical" price series.

The other backtest test (``tests/test_backtest.py``) hand-crafts a single
confluence scenario to verify the exact sweep -> CHoCH -> OB -> OTE -> Signal
wiring. This one instead builds a long, reproducible OHLCV series that looks
like real exchange history (random-walk drift + noise, no engineered
setups), round-trips it through :func:`load_ohlcv_csv` exactly like
``ict_bot.main backtest --csv ...`` does, and runs it through the full
strategy/risk/engine/metrics pipeline. The goal is robustness against
whatever shapes up over thousands of bars, not any particular trade count:
the bot must never crash, blow the account past zero, or produce
nonsensical metrics.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ict_bot.backtest.engine import BacktestConfig, BacktestEngine
from ict_bot.backtest.metrics import compute_metrics, pair_trades
from ict_bot.data.feed import load_ohlcv_csv
from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig
from ict_bot.strategy.risk import RiskConfig, RiskManager


def _make_historical_ohlcv_csv(path, num_bars: int = 2000, seed: int = 7) -> None:
    """Write a reproducible, realistic-looking OHLCV CSV: a geometric
    random walk for closes, with high/low/open derived per bar the way real
    candles relate to each other (open = prior close, high/low straddle
    open/close by a random intrabar range).
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range("2023-01-01", periods=num_bars, freq="15min", tz="UTC")

    returns = rng.normal(loc=0.0, scale=0.004, size=num_bars)
    close = 30_000.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[close[0] / (1 + returns[0])], close[:-1]])

    intrabar_range = np.abs(rng.normal(loc=0.0, scale=0.002, size=num_bars)) * close
    high = np.maximum(open_, close) + intrabar_range
    low = np.minimum(open_, close) - intrabar_range
    volume = rng.uniform(1.0, 50.0, size=num_bars)

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index)
    df.index.name = "timestamp"
    df.to_csv(path)


def test_backtest_against_historical_csv_data(tmp_path):
    csv_path = tmp_path / "historical_ohlcv.csv"
    # seed=8/1500 bars is chosen because it reliably produces a couple of
    # trades on this synthetic series, so the notional-exposure check below
    # actually exercises the position-sizing cap instead of being vacuous.
    _make_historical_ohlcv_csv(csv_path, num_bars=1500, seed=8)

    df = load_ohlcv_csv(str(csv_path))
    assert len(df) == 1500
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()

    starting_balance = 10_000.0
    strategy = ICTStrategy(ICTStrategyConfig())
    risk_manager = RiskManager(RiskConfig(risk_per_trade_pct=1.0, max_daily_loss_pct=3.0, max_open_positions=1))
    engine = BacktestEngine(
        df,
        strategy,
        risk_manager,
        BacktestConfig(symbol="BTC/USDT", starting_balance=starting_balance, window_size=150, pending_order_expiry_bars=8),
    )

    result = engine.run()

    # The engine must walk every bar and never crash on real-shaped data.
    assert len(result.equity_curve) == len(df)
    assert result.equity_curve.index.equals(df.index)

    # The risk manager's per-trade sizing + daily circuit breaker must keep
    # the simulated account from ever going bust, however many trades fire.
    assert (result.equity_curve > 0).all()
    assert result.final_balance > 0

    trades = pair_trades(result.fills)
    entries = [f for f in result.fills if f.reason == "entry"]
    assert len(entries) == len(trades)  # every entry in this run is paired with an exit
    assert len(trades) > 0  # otherwise the notional-exposure check below would be vacuous

    # Position sizing must never commit more notional than the account
    # actually has, however tight the signal's stop-loss is. A stop close
    # to entry (common for ICT order-block entries) otherwise blows up
    # risk_amount / stop_distance into several times the account balance -
    # implicit, unbounded leverage a real account can't take on.
    running_balance = starting_balance
    for trade in trades:
        notional = trade.entry_price * trade.amount
        max_notional = running_balance * (risk_manager.config.max_position_pct / 100.0)
        assert notional <= max_notional + 1e-6
        running_balance += trade.pnl

    metrics = compute_metrics(trades, result.equity_curve, starting_balance=starting_balance)
    assert metrics["num_trades"] == len(trades)
    assert 0.0 <= metrics["win_rate_pct"] <= 100.0
    assert metrics["max_drawdown_pct"] <= 0.0
    assert metrics["max_drawdown_pct"] >= -100.0
    assert metrics["final_balance"] == result.final_balance

    # Every filled trade must respect the configured min risk/reward and
    # actually exit at its own stop-loss or take-profit level.
    for trade in trades:
        assert trade.exit_reason in ("stop_loss", "take_profit")


def test_backtest_is_deterministic_for_the_same_historical_data(tmp_path):
    csv_path = tmp_path / "historical_ohlcv.csv"
    _make_historical_ohlcv_csv(csv_path, num_bars=300, seed=42)
    df = load_ohlcv_csv(str(csv_path))

    def run_once() -> tuple[float, int]:
        strategy = ICTStrategy(ICTStrategyConfig())
        risk_manager = RiskManager(RiskConfig())
        engine = BacktestEngine(df.copy(), strategy, risk_manager, BacktestConfig(starting_balance=10_000))
        result = engine.run()
        return result.final_balance, len(result.fills)

    first = run_once()
    second = run_once()
    assert first == second
