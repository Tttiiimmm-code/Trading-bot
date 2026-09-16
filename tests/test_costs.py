"""Trading costs: fees on both sides, slippage on stop-outs only."""
import pandas as pd
import pytest

from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.ict_strategy import Side

TS = pd.Timestamp("2024-01-01 00:00", tz="UTC")
TS2 = pd.Timestamp("2024-01-01 00:15", tz="UTC")


def test_no_costs_by_default():
    broker = PaperBroker(10_000.0)
    broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=TS)
    broker.close_position("BTC/USDT", price=110.0, ts=TS2, reason="take_profit")
    assert broker.balance == 10_010.0
    assert broker.fees_paid == 0.0


def test_limit_entry_and_take_profit_are_charged_the_maker_rate():
    broker = PaperBroker(10_000.0, maker_fee_pct=0.1, taker_fee_pct=0.5)
    broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=TS)
    assert broker.balance == 10_000.0 - 0.1  # maker: 0.1% of 100 notional
    broker.close_position("BTC/USDT", price=110.0, ts=TS2, reason="take_profit")
    # +10 pnl, minus 0.1 entry and 0.11 exit, both at the maker rate
    assert broker.balance == pytest.approx(10_000.0 - 0.1 + 10.0 - 0.11)
    assert broker.fees_paid == pytest.approx(0.21)


def test_stop_out_is_charged_the_taker_rate():
    broker = PaperBroker(10_000.0, maker_fee_pct=0.1, taker_fee_pct=0.5)
    broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=TS)
    broker.close_position("BTC/USDT", price=95.0, ts=TS2, reason="stop_loss")
    # 0.1 maker on entry + 0.475 taker on the 95-priced exit
    assert broker.fees_paid == pytest.approx(0.1 + 0.475)


def test_stop_loss_exit_slips_against_a_long():
    broker = PaperBroker(10_000.0, stop_slippage_pct=1.0)
    broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=TS)
    fill = broker.close_position("BTC/USDT", price=95.0, ts=TS2, reason="stop_loss")
    assert fill.price == pytest.approx(95.0 - 0.95)  # filled 1% worse than the stop
    assert broker.balance == pytest.approx(10_000.0 - 5.95)


def test_stop_loss_exit_slips_against_a_short():
    broker = PaperBroker(10_000.0, stop_slippage_pct=1.0)
    broker.open_position("BTC/USDT", Side.SHORT, amount=1.0, price=100.0, stop_loss=105.0, take_profit=90.0, ts=TS)
    fill = broker.close_position("BTC/USDT", price=105.0, ts=TS2, reason="stop_loss")
    assert fill.price == pytest.approx(105.0 + 1.05)
    assert broker.balance == pytest.approx(10_000.0 - 6.05)


def test_take_profit_exit_does_not_slip():
    broker = PaperBroker(10_000.0, stop_slippage_pct=1.0)
    broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=TS)
    fill = broker.close_position("BTC/USDT", price=110.0, ts=TS2, reason="take_profit")
    assert fill.price == 110.0
    assert broker.balance == 10_010.0


def test_engine_passes_costs_to_its_broker():
    from ict_bot.backtest.engine import BacktestConfig, BacktestEngine
    from ict_bot.strategy.ict_strategy import ICTStrategy
    from ict_bot.strategy.risk import RiskConfig, RiskManager
    from tests.conftest import rows_to_df

    df = rows_to_df([(100.0, 101.0, 99.0, 100.0)] * 30)
    engine = BacktestEngine(df, ICTStrategy(), RiskManager(RiskConfig()),
                            BacktestConfig(maker_fee_pct=0.02, taker_fee_pct=0.05, stop_slippage_pct=0.02))
    assert engine.broker.maker_fee_pct == 0.02
    assert engine.broker.taker_fee_pct == 0.05
    assert engine.broker.stop_slippage_pct == 0.02


def test_fees_scale_with_notional_not_risk():
    # The whole point: a tight stop doesn't make the fee smaller.
    broker = PaperBroker(10_000.0, maker_fee_pct=0.05, taker_fee_pct=0.05)
    broker.open_position("BTC/USDT", Side.LONG, amount=10.0, price=1_000.0, stop_loss=999.0, take_profit=1_010.0, ts=TS)
    assert broker.fees_paid == 5.0  # 0.05% of 10,000 notional, while risk is only 10
