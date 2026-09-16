"""Trailing stops and open-ended (no take-profit) exits."""
import pandas as pd

from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.ict_strategy import Side

TS = [pd.Timestamp("2024-01-01 00:00", tz="UTC") + pd.Timedelta(minutes=15 * i) for i in range(10)]


def bar(o, h, l, c):
    return pd.Series({"open": o, "high": h, "low": l, "close": c})


def _high_trail_broker():
    """The old default, kept under test: ratchet on the bar extreme."""
    return PaperBroker(10_000.0, trail_on="high")


def _long(broker, trail=None, take_profit=None):
    return broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0,
                                take_profit=take_profit, ts=TS[0], trail_distance=trail)


def test_long_stop_ratchets_up_with_price():
    broker = _high_trail_broker()
    position = _long(broker, trail=5.0)

    broker.check_stop_and_target("BTC/USDT", bar(100, 110, 99, 108), TS[1])
    assert position.stop_loss == 105.0  # 110 high - 5 trail

    broker.check_stop_and_target("BTC/USDT", bar(108, 120, 107, 119), TS[2])
    assert position.stop_loss == 115.0


def test_long_stop_never_moves_back_down():
    broker = _high_trail_broker()
    position = _long(broker, trail=5.0)
    broker.check_stop_and_target("BTC/USDT", bar(100, 120, 99, 118), TS[1])
    assert position.stop_loss == 115.0
    broker.check_stop_and_target("BTC/USDT", bar(118, 118, 116, 117), TS[2])
    assert position.stop_loss == 115.0  # lower high must not loosen the stop


def test_short_stop_ratchets_down():
    broker = _high_trail_broker()
    position = broker.open_position("BTC/USDT", Side.SHORT, amount=1.0, price=100.0, stop_loss=105.0,
                                    take_profit=None, ts=TS[0], trail_distance=5.0)
    broker.check_stop_and_target("BTC/USDT", bar(100, 101, 90, 92), TS[1])
    assert position.stop_loss == 95.0  # 90 low + 5 trail


def test_trailing_stop_eventually_closes_the_position():
    broker = _high_trail_broker()
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
    broker = _high_trail_broker()
    position = _long(broker, trail=5.0)
    fill = broker.check_stop_and_target("BTC/USDT", bar(100, 130, 96, 97), TS[1])
    assert fill is None  # original 95 stop was not touched (low 96)
    assert position.stop_loss == 125.0
    # It closes on the *next* bar instead - at 125, although this bar and
    # the last both closed at 97. That is the pathology that makes "high"
    # the wrong default: the exit price comes from a spike, not a level the
    # market actually traded at afterwards. Measured across 29 markets it
    # inflated the edge by about a quarter.
    fill = broker.check_stop_and_target("BTC/USDT", bar(97, 98, 96, 97), TS[2])
    assert fill is not None and fill.price == 125.0


def test_the_same_spike_is_harmless_when_trailing_on_the_close():
    broker = PaperBroker(10_000.0)          # default: trail_on="close"
    position = _long(broker, trail=5.0)
    assert broker.check_stop_and_target("BTC/USDT", bar(100, 130, 96, 97), TS[1]) is None
    assert position.stop_loss == 95.0       # 97 close - 5 is below the original stop
    # and the position is not handed a fictitious exit on the next bar.
    assert broker.check_stop_and_target("BTC/USDT", bar(97, 98, 96, 97), TS[2]) is None


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


def test_the_default_ratchets_on_the_close_not_the_extreme():
    broker = PaperBroker(10_000.0)          # default trail_on="close"
    position = _long(broker, trail=5.0)

    broker.check_stop_and_target("BTC/USDT", bar(100, 110, 99, 102), TS[1])
    assert position.stop_loss == 97.0       # 102 close - 5, not 110 high - 5


def test_a_single_bad_print_cannot_move_a_close_trailed_stop():
    # The reason for the default. Exchanges publish bad ticks: a KuCoin
    # LTC bar in this project's own data prints a high 43% above what OKX
    # recorded for the same candle. Trailing on the extreme drags the stop
    # to a level that never traded, the next bar "hits" it, and the broker
    # books an exit at a price that never existed.
    good = bar(100, 110, 99, 108)
    with_bad_tick = bar(100, 160, 99, 108)  # same bar, one absurd print

    on_high, on_close = [], []
    for kind, out in (("high", on_high), ("close", on_close)):
        for candle in (good, with_bad_tick):
            broker = PaperBroker(10_000.0, trail_on=kind)
            position = _long(broker, trail=5.0)
            broker.check_stop_and_target("BTC/USDT", candle, TS[1])
            out.append(position.stop_loss)

    assert on_high[0] != on_high[1]         # the bad print moved the stop 50 wide
    assert on_close[0] == on_close[1] == 103.0
