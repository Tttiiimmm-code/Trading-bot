"""Regression coverage for cmd_live's per-bar processing.

A poll can return more than one newly-closed candle (any delay between
polls - a slow response, the exception backoff, downtime - is enough,
since normally only 0-1 candles close per poll). The live loop used to
fetch only the last 2 candles and evaluate stop-loss/take-profit and
pending-limit-order fills against just the newest of them, silently
skipping whatever happened on any earlier candle in the gap. That could
leave a position open well past where it should have been stopped out.

_process_closed_bar is the extracted per-candle step (mirrors one
iteration of the backtest engine's bar loop); these tests call it once
per candle, in order, the way the fixed live loop now does.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from ict_bot.main import _no_conflicting_position_exists, _process_closed_bar
from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.ict_strategy import ICTStrategy, Side
from ict_bot.strategy.risk import RiskConfig, RiskManager


class _FakeExchange:
    def __init__(self, positions=None, supports_fetch_positions=True):
        self._positions = positions or []
        self.has = {"fetchPositions": supports_fetch_positions}
        self.id = "fake"

    def fetch_positions(self, symbols):
        return self._positions


def _config(pending_order_expiry_bars: int = 8):
    return SimpleNamespace(backtest=SimpleNamespace(pending_order_expiry_bars=pending_order_expiry_bars))


def test_stop_loss_hit_on_an_earlier_gapped_bar_is_not_missed():
    broker = PaperBroker(10_000)
    ts0 = pd.Timestamp("2024-01-01 10:00", tz="UTC")
    broker.open_position("BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=ts0)

    risk_manager = RiskManager(RiskConfig(max_open_positions=1))
    risk_manager.register_open()
    strategy = ICTStrategy()
    window = pd.DataFrame()

    # Two candles closed in the same gap: the first wicks through the
    # stop-loss, the second fully recovers above it and never touches the
    # stop level again.
    bar1_ts = pd.Timestamp("2024-01-01 10:15", tz="UTC")
    bar1 = pd.Series({"open": 99.0, "high": 99.5, "low": 90.0, "close": 91.0})
    bar2_ts = pd.Timestamp("2024-01-01 10:30", tz="UTC")
    bar2 = pd.Series({"open": 96.5, "high": 99.0, "low": 96.0, "close": 98.0})

    pending, pending_bars_left = None, 0
    for ts, bar in [(bar1_ts, bar1), (bar2_ts, bar2)]:
        pending, pending_bars_left = _process_closed_bar(
            broker, risk_manager, strategy, _config(), "BTC/USDT", window, pending, pending_bars_left, bar, ts
        )

    # Checking only bar2 (the old behavior) would never detect a stop
    # hit - bar2's low (96.0) never crosses the 95.0 stop - and the
    # position would incorrectly stay open.
    assert broker.get_open_position("BTC/USDT") is None
    stop_fills = [f for f in broker.fills if f.reason == "stop_loss"]
    assert len(stop_fills) == 1
    assert stop_fills[0].timestamp == bar1_ts
    assert risk_manager.open_positions == 0


def test_pending_order_fill_on_an_earlier_gapped_bar_is_not_missed():
    from ict_bot.strategy.ict_strategy import Signal

    broker = PaperBroker(10_000)
    risk_manager = RiskManager(RiskConfig(max_open_positions=1))
    strategy = ICTStrategy()
    window = pd.DataFrame()

    pending = Signal(index=pd.Timestamp("2024-01-01 10:00", tz="UTC"), side=Side.LONG, entry=95.0, stop_loss=90.0, take_profit=110.0, reason="test")
    pending_bars_left = 8

    # The first gapped candle trades through the pending entry price; the
    # second stays well above it and would never fill the order on its own.
    bar1_ts = pd.Timestamp("2024-01-01 10:15", tz="UTC")
    bar1 = pd.Series({"open": 98.0, "high": 98.5, "low": 94.0, "close": 96.0})
    bar2_ts = pd.Timestamp("2024-01-01 10:30", tz="UTC")
    bar2 = pd.Series({"open": 99.0, "high": 101.0, "low": 98.5, "close": 100.0})

    for ts, bar in [(bar1_ts, bar1), (bar2_ts, bar2)]:
        pending, pending_bars_left = _process_closed_bar(
            broker, risk_manager, strategy, _config(), "BTC/USDT", window, pending, pending_bars_left, bar, ts
        )

    position = broker.get_open_position("BTC/USDT")
    assert position is not None
    assert position.entry_price == 95.0
    entries = [f for f in broker.fills if f.reason == "entry"]
    assert len(entries) == 1
    assert entries[0].timestamp == bar1_ts


def test_no_conflicting_position_exists_true_when_flat():
    exchange = _FakeExchange(positions=[{"symbol": "BTC/USDT", "contracts": 0.0}])
    assert _no_conflicting_position_exists(exchange, "BTC/USDT") is True


def test_no_conflicting_position_exists_false_when_a_position_is_already_open():
    """Regression: CCXTBroker only tracks positions in local memory. A
    process that starts fresh while the exchange already has an open
    position (from a crash/restart mid-position) must refuse to start,
    not silently open a second, uncoordinated one on top of it.
    """
    exchange = _FakeExchange(positions=[{"symbol": "BTC/USDT", "contracts": 0.001, "side": "long"}])
    assert _no_conflicting_position_exists(exchange, "BTC/USDT") is False


def test_no_conflicting_position_exists_true_when_exchange_cannot_check():
    exchange = _FakeExchange(supports_fetch_positions=False)
    assert _no_conflicting_position_exists(exchange, "BTC/USDT") is True


class _AlwaysRejectsOrdersBroker(PaperBroker):
    """Simulates open_position() always failing - e.g. a computed size
    below the exchange's minimum order quantity/notional, or a transient
    network error.
    """

    def open_position(self, *args, **kwargs):
        raise RuntimeError("order rejected")


def test_pending_signal_still_expires_normally_when_open_position_keeps_failing():
    """Regression: an exception from open_position() used to propagate
    out of _process_closed_bar before its (pending, pending_bars_left)
    return value was produced, so the caller never saw the decrement
    this call already computed - pending_bars_left effectively never
    counted down and the pending signal could retry forever instead of
    expiring after pending_order_expiry_bars, as it's supposed to.
    """
    from ict_bot.strategy.ict_strategy import Signal

    broker = _AlwaysRejectsOrdersBroker(10_000)
    risk_manager = RiskManager(RiskConfig(max_open_positions=1))
    strategy = ICTStrategy()
    window = pd.DataFrame()
    config = _config(pending_order_expiry_bars=3)

    pending = Signal(index=pd.Timestamp("2024-01-01 10:00", tz="UTC"), side=Side.LONG, entry=95.0, stop_loss=90.0, take_profit=110.0, reason="test")
    pending_bars_left = 3
    # Every bar keeps trading through the entry price, so open_position()
    # (and thus the failure) is attempted on every one of them.
    bar = pd.Series({"open": 96.0, "high": 96.5, "low": 94.0, "close": 95.5})

    for i in range(3):
        ts = pd.Timestamp("2024-01-01 10:00", tz="UTC") + pd.Timedelta(minutes=15 * (i + 1))
        pending, pending_bars_left = _process_closed_bar(
            broker, risk_manager, strategy, config, "BTC/USDT", window, pending, pending_bars_left, bar, ts
        )
        assert pending_bars_left == 3 - (i + 1)  # decrements every bar despite the failure

    # Expired after exactly pending_order_expiry_bars failed attempts,
    # not stuck retrying forever.
    assert pending is None
    assert broker.get_open_position("BTC/USDT") is None
