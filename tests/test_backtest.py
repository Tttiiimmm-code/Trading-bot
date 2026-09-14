import pandas as pd

from ict_bot.backtest.engine import BacktestConfig, BacktestEngine
from ict_bot.backtest.metrics import compute_metrics, pair_trades
from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig
from ict_bot.strategy.risk import RiskConfig, RiskManager
from tests.test_ict_strategy import _long_setup_df


def _with_pullback_fill(df: pd.DataFrame) -> pd.DataFrame:
    """The signal in ``_long_setup_df`` fires on the very last bar, leaving
    no future bar for the engine's pending limit order to fill against.
    Append a couple of bars that trade back down through the ~94.65 order
    block so the pending order actually fills (and one more that clears
    take-profit) - what the strategy test doesn't need to care about.
    """
    extra = pd.DataFrame(
        [
            {"open": 105.0, "high": 106.0, "low": 93.0, "close": 95.0, "volume": 1.0},  # fills the entry
            {"open": 95.0, "high": 101.0, "low": 94.0, "close": 100.7, "volume": 1.0},  # clears take-profit
        ],
        index=pd.date_range(df.index[-1] + pd.Timedelta(minutes=15), periods=2, freq="15min", tz="UTC"),
    )
    return pd.concat([df, extra])


def test_backtest_engine_runs_end_to_end_and_fills_the_signal():
    df = _with_pullback_fill(_long_setup_df())
    strategy = ICTStrategy(ICTStrategyConfig(require_kill_zone=True, require_ote=True, min_risk_reward=1.0))
    risk_manager = RiskManager(RiskConfig(risk_per_trade_pct=1.0, max_open_positions=1))
    engine = BacktestEngine(df, strategy, risk_manager, BacktestConfig(symbol="TEST/USD", starting_balance=10_000, window_size=100, pending_order_expiry_bars=5))

    result = engine.run()

    assert len(result.equity_curve) == len(df)
    entries = [f for f in result.fills if f.reason == "entry"]
    assert len(entries) == 1
    assert entries[0].price > 0

    trades = pair_trades(result.fills)
    metrics = compute_metrics(trades, result.equity_curve, starting_balance=10_000)
    assert metrics["num_trades"] == len(trades)
    assert "win_rate_pct" in metrics
    assert "max_drawdown_pct" in metrics


def test_backtest_on_flat_data_takes_no_trades():
    from tests.conftest import rows_to_df, zigzag_rows

    df = rows_to_df(zigzag_rows([100, 100, 100], bars_per_leg=3, epsilon=0.0))
    strategy = ICTStrategy()
    risk_manager = RiskManager(RiskConfig())
    engine = BacktestEngine(df, strategy, risk_manager, BacktestConfig(starting_balance=10_000))

    result = engine.run()

    assert result.fills == []
    assert result.final_balance == 10_000
