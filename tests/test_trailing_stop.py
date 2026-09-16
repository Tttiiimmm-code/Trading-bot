"""Trailing stops and open-ended (no take-profit) exits."""
import pandas as pd

from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.ict_strategy import Side

TS = [pd.Timestamp("2024-01-01 00:00", tz="UTC") + pd.Timedelta(minutes=15 * i) for i in range(10)]


def bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c})


def _long(broker, trail=None, take_profit=None):
    return broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0,
                                take_profit=take_profit, ts=TS[0], trail_distance=trail)


def test_long_stop_ratchets_up_with_price():
    broker = PaperBroker(10_000.0)
    position = _long(broker, trail=5.0)

    broker.check_stop_and_target("BTC/USDT", bar(100, 110, 99, 108), TS[1])
    assert position.stop_loss == 105.0  # 110 high - 5 trail

    broker.check_stop_and_target("BTC/USDT", bar(108, 120, 107, 119), TS[2])
    assert position.stop_loss == 115.0


def test_long_stop_never_moves_back_down():
    broker = PaperBroker(10_000.0)
    position = _long(broker, trail=5.0)
    broker.check_stop_and_target("BTC/USDT", bar(100, 120, 99, 118), TS[1])
    assert position.stop_loss == 115.0
    broker.check_stop_and_target("BTC/USDT", bar(118, 118, 116, 117), TS[2])
    assert position.stop_loss == 115.0  # lower high must not loosen the stop


def test_short_stop_ratchets_down():
    broker = PaperBroker(10_000.0)
    position = broker.open_position("BTC/USDT", Side.SHORT, amount=1.0, price=100.0, stop_loss=105.0,
                                    take_profit=None, ts=TS[0], trail_distance=5.0)
    broker.check_stop_and_target("BTC/USDT", bar(100, 101, 90, 92), TS[1])
    assert position.stop_loss == 95.0  # 90 low + 5 trail


def test_trailing_stop_eventually_closes_the_position():
    broker = PaperBroker(10_000.0)
    _long(broker, trail=5.0)
    broker.check_stop_and_target("BTC/USDT", bar(100, 120, 99, 118), TS[1])  # stop -> 115
    fill = broker.check_stop_and_target("BTC/USDT", bar(118, 119, 110, 112), TS[2])
    assert fill is not None and fill.reason == "stop_loss"
    assert fill.price == 115.0
    assert broker.balance == 10_015.0  # entered 100, stopped out at 115


def test_a_bar_cannot_both_raise_and_trigger_the_same_stop():
    # A spike up then straight back down: the stop it gets dragged to by this
    # bar's high must not also be considered hit by this bar's low, or the
    # backtest books an exit at a price the trail had not reached yet.
    broker = PaperBroker(10_000.0)
    position = _long(broker, trail=5.0)
    fill = broker.check_stop_and_target("BTC/USDT", bar(100, 130, 96, 97), TS[1])
    assert fill is None  # original 95 stop was not touched (low 96)
    assert position.stop_loss == 125.0
    # It closes on the *next* bar instead.
    fill = broker.check_stop_and_target("BTC/USDT", bar(97, 98, 96, 97), TS[2])
    assert fill is not None and fill.price == 125.0


def test_no_trail_distance_leaves_the_stop_alone():
    broker = PaperBroker(10_000.0)
    position = _long(broker, trail=None)
    broker.check_stop_and_target("BTC/USDT", bar(100, 130, 99, 128), TS[1])
    assert position.stop_loss == 95.0


def test_open_ended_position_has_no_take_profit_to_hit():
    broker = PaperBroker(10_000.0)
    _long(broker, trail=None, take_profit=None)
    # A huge up-bar must not close anything: there is no target.
    assert broker.check_stop_and_target("BTC/USDT", bar(100, 500, 99, 480), TS[1]) is None


def test_fixed_take_profit_still_works_alongside_trailing():
    broker = PaperBroker(10_000.0)
    _long(broker, trail=5.0, take_profit=110.0)
    fill = broker.check_stop_and_target("BTC/USDT", bar(100, 111, 99, 110), TS[1])
    assert fill is not None and fill.reason == "take_profit"


def test_signal_risk_reward_is_zero_without_a_target():
    from ict_bot.strategy.ict_strategy import Signal

    signal = Signal(index=TS[0], side=Side.LONG, entry=100.0, stop_loss=95.0,
                    take_profit=None, reason="trend", trail_distance=5.0)
    assert signal.risk_reward == 0.0
