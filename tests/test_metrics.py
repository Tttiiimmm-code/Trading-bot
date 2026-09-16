import pandas as pd
import pytest

from ict_bot.backtest.metrics import compute_metrics, max_drawdown_pct, pair_trades
from ict_bot.execution.broker import Fill
from ict_bot.strategy.ict_strategy import Side


def _fill(symbol, side, amount, price, ts, reason="entry", fee=0.0):
    return Fill(symbol=symbol, side=side, amount=amount, price=price,
                timestamp=pd.Timestamp(ts, tz="UTC"), reason=reason, fee=fee)


def test_pair_trades_reports_pnl_net_of_fees():
    fills = [
        _fill("BTC/USDT", Side.LONG, 1.0, 100.0, "2024-01-01 00:00", fee=0.5),
        _fill("BTC/USDT", Side.LONG, 1.0, 110.0, "2024-01-01 01:00", reason="take_profit", fee=0.6),
    ]
    trade = pair_trades(fills)[0]
    assert trade.gross_pnl == 10.0
    assert trade.fees == 1.1
    assert trade.pnl == pytest.approx(8.9)


def test_fees_can_flip_a_marginal_winner_into_a_loser():
    fills = [
        _fill("BTC/USDT", Side.LONG, 10.0, 100.0, "2024-01-01 00:00", fee=0.5),
        _fill("BTC/USDT", Side.LONG, 10.0, 100.05, "2024-01-01 01:00", reason="take_profit", fee=0.5),
    ]
    trade = pair_trades(fills)[0]
    assert trade.gross_pnl == pytest.approx(0.5)  # +0.05 x 10 units
    assert trade.pnl == pytest.approx(-0.5)  # ... entirely eaten by 1.0 of fees
    metrics = compute_metrics([trade], pd.Series([10_000.0, 9_999.5]), starting_balance=10_000.0)
    assert metrics["win_rate_pct"] == 0.0


def test_pair_trades_computes_pnl_for_long_and_short():
    fills = [
        _fill("BTC/USDT", Side.LONG, 1.0, 100.0, "2024-01-01 00:00"),
        _fill("BTC/USDT", Side.LONG, 1.0, 110.0, "2024-01-01 01:00", reason="take_profit"),
        _fill("BTC/USDT", Side.SHORT, 2.0, 100.0, "2024-01-01 02:00"),
        _fill("BTC/USDT", Side.SHORT, 2.0, 90.0, "2024-01-01 03:00", reason="take_profit"),
    ]
    trades = pair_trades(fills)
    assert len(trades) == 2
    assert trades[0].pnl == 10.0  # long: (110-100)*1
    assert trades[1].pnl == 20.0  # short: (100-90)*2


def test_pair_trades_keeps_symbols_independent_when_interleaved():
    # Regression: pairing used to track a single global "open fill", so an
    # entry on one symbol while another symbol's position was still open
    # would silently overwrite it and mis-pair the eventual exits.
    fills = [
        _fill("BTC/USDT", Side.LONG, 1.0, 100.0, "2024-01-01 00:00"),
        _fill("ETH/USDT", Side.LONG, 1.0, 50.0, "2024-01-01 00:15"),
        _fill("ETH/USDT", Side.LONG, 1.0, 55.0, "2024-01-01 00:30", reason="take_profit"),
        _fill("BTC/USDT", Side.LONG, 1.0, 90.0, "2024-01-01 00:45", reason="stop_loss"),
    ]
    trades = pair_trades(fills)
    assert len(trades) == 2

    btc_trade = next(t for t in trades if t.symbol == "BTC/USDT")
    eth_trade = next(t for t in trades if t.symbol == "ETH/USDT")
    assert btc_trade.entry_price == 100.0 and btc_trade.exit_price == 90.0
    assert eth_trade.entry_price == 50.0 and eth_trade.exit_price == 55.0


def test_pair_trades_ignores_exit_with_no_matching_entry():
    fills = [_fill("BTC/USDT", Side.LONG, 1.0, 100.0, "2024-01-01 00:00", reason="stop_loss")]
    assert pair_trades(fills) == []


def test_max_drawdown_pct_on_known_curve():
    curve = pd.Series([100, 120, 90, 110], index=pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"))
    # peak 120 -> trough 90 is a 25% drawdown
    assert max_drawdown_pct(curve) == -25.0


def test_max_drawdown_pct_empty_curve_is_zero():
    assert max_drawdown_pct(pd.Series(dtype=float)) == 0.0


def test_compute_metrics_win_rate_and_profit_factor():
    fills = [
        _fill("BTC/USDT", Side.LONG, 1.0, 100.0, "2024-01-01 00:00"),
        _fill("BTC/USDT", Side.LONG, 1.0, 110.0, "2024-01-01 01:00", reason="take_profit"),
        _fill("BTC/USDT", Side.LONG, 1.0, 100.0, "2024-01-01 02:00"),
        _fill("BTC/USDT", Side.LONG, 1.0, 95.0, "2024-01-01 03:00", reason="stop_loss"),
    ]
    trades = pair_trades(fills)
    curve = pd.Series([10_000, 10_010, 10_005], index=pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"))
    metrics = compute_metrics(trades, curve, starting_balance=10_000)

    assert metrics["num_trades"] == 2
    assert metrics["win_rate_pct"] == 50.0
    assert metrics["profit_factor"] == 2.0  # 10 gross profit / 5 gross loss
    assert metrics["gross_profit"] == 10.0
    assert metrics["gross_loss"] == -5.0
    assert metrics["avg_win"] == 10.0
    assert metrics["avg_loss"] == -5.0


def test_compute_metrics_no_trades_defaults():
    metrics = compute_metrics([], pd.Series(dtype=float), starting_balance=10_000)
    assert metrics["num_trades"] == 0
    assert metrics["win_rate_pct"] == 0.0
    assert metrics["profit_factor"] == 0.0
    assert metrics["final_balance"] == 10_000
