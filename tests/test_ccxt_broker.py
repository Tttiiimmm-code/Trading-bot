"""CCXTBroker tests against a minimal fake ccxt exchange.

Covers three bugs found by reasoning about the live execution path:

1. ``get_balance()`` used to hardcode "USDT" as the quote currency,
   regardless of the configured ``market.symbol`` - trading any other pair
   (e.g. BTC/EUR) would read the wrong or a missing balance live.
2. The native stop-loss/take-profit orders placed by
   ``_place_protective_orders`` are two independent conditional orders,
   not an atomic OCO pair. Nothing tracked or cancelled the untriggered
   sibling once a position closed, orphaning a live conditional order on
   the exchange that could fire unexpectedly against a later position.
3. ``check_stop_and_target`` used to infer a close from OHLC bars even
   when native SL/TP orders were active. That let both detection paths
   fire independently: once a native order actually filled on the
   exchange, the next bar-based check would try to close the (already
   flat) position again with a fresh order the exchange rejects - and
   since that rejection never returns a Fill, the risk manager's
   open-position count gets stuck forever. Native orders' own status is
   now the sole source of truth whenever they're tracked.
"""
from __future__ import annotations

import pandas as pd

from ict_bot.execution.ccxt_broker import CCXTBroker
from ict_bot.strategy.ict_strategy import Side


class _FakeExchange:
    def __init__(self, balances: dict | None = None):
        self._balances = balances or {}
        self.created_orders: list[dict] = []
        self.cancelled_order_ids: list[str] = []
        self._orders_by_id: dict[str, dict] = {}
        self._next_order_id = 1

    def fetch_balance(self) -> dict:
        return self._balances

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        order_id = str(self._next_order_id)
        self._next_order_id += 1
        order = {
            "id": order_id,
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": 100.0,
            "average": None,
            "status": "open",
            "params": params or {},
        }
        self.created_orders.append(order)
        self._orders_by_id[order_id] = order
        return order

    def cancel_order(self, order_id, symbol):
        self.cancelled_order_ids.append(order_id)

    def fetch_order(self, order_id, symbol):
        order = self._orders_by_id.get(order_id)
        if order is None:
            raise Exception(f"order {order_id} not found")
        return order

    def fill_order(self, order_id: str, price: float) -> None:
        """Test helper: simulate the exchange filling a resting order."""
        self._orders_by_id[order_id]["status"] = "closed"
        self._orders_by_id[order_id]["average"] = price


class _FailingCancelExchange(_FakeExchange):
    """Simulates an exchange rejecting cancellation of an already-filled order."""

    def cancel_order(self, order_id, symbol):
        raise Exception(f"order {order_id} not found (already filled)")


def test_get_balance_reads_the_symbol_quote_currency():
    exchange = _FakeExchange({"EUR": {"free": 1234.5, "used": 0.0, "total": 1234.5}})
    broker = CCXTBroker(exchange, symbol="BTC/EUR")
    assert broker.get_balance() == 1234.5


def test_get_balance_not_confused_by_other_currencies_in_the_wallet():
    exchange = _FakeExchange(
        {
            "USDT": {"free": 999.0, "used": 0.0, "total": 999.0},
            "EUR": {"free": 42.0, "used": 0.0, "total": 42.0},
        }
    )
    broker = CCXTBroker(exchange, symbol="BTC/EUR")
    assert broker.get_balance() == 42.0


def test_get_balance_zero_when_quote_currency_missing_from_wallet():
    exchange = _FakeExchange({"USDT": {"free": 999.0, "used": 0.0, "total": 999.0}})
    broker = CCXTBroker(exchange, symbol="BTC/EUR")
    assert broker.get_balance() == 0.0


def _open_long(broker: CCXTBroker, symbol: str = "BTC/USDT"):
    ts = pd.Timestamp("2024-01-01 10:00", tz="UTC")
    return broker.open_position(symbol, Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=ts)


_FLAT_BAR = pd.Series({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0})


def test_open_position_places_two_protective_orders_and_tracks_their_ids():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)

    _open_long(broker)

    protective = [o for o in exchange.created_orders if o["params"].get("reduceOnly")]
    assert len(protective) == 2
    sl_id = next(o["id"] for o in protective if "stopLossPrice" in o["params"])
    tp_id = next(o["id"] for o in protective if "takeProfitPrice" in o["params"])
    assert broker._protective_order_ids["BTC/USDT"] == {"stop_loss": sl_id, "take_profit": tp_id}


def test_closing_a_position_cancels_the_untriggered_sibling_order():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)
    _open_long(broker)
    protective_ids = set(broker._protective_order_ids["BTC/USDT"].values())

    fill = broker.close_position("BTC/USDT", price=110.0, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"), reason="take_profit")

    assert fill is not None
    assert set(exchange.cancelled_order_ids) == protective_ids
    assert "BTC/USDT" not in broker._protective_order_ids  # no orphaned tracking left behind


def test_cancel_failure_for_an_already_filled_order_does_not_raise():
    exchange = _FailingCancelExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)
    _open_long(broker)

    # Must not propagate: the sibling order legitimately no longer exists
    # once one leg of the bracket has already filled on the exchange.
    fill = broker.close_position("BTC/USDT", price=110.0, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"), reason="take_profit")
    assert fill is not None


def test_no_protective_orders_when_native_sl_tp_disabled():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=False)

    _open_long(broker)

    assert len(exchange.created_orders) == 1  # entry only
    assert broker._protective_order_ids == {}


def test_check_stop_and_target_detects_a_native_stop_loss_fill():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)
    _open_long(broker)
    sl_id = broker._protective_order_ids["BTC/USDT"]["stop_loss"]
    tp_id = broker._protective_order_ids["BTC/USDT"]["take_profit"]
    exchange.fill_order(sl_id, price=94.8)

    # The bar itself is irrelevant here: the native order's own status is
    # the source of truth, not another bar-based re-derivation of it.
    fill = broker.check_stop_and_target("BTC/USDT", _FLAT_BAR, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"))

    assert fill is not None
    assert fill.reason == "stop_loss"
    assert fill.price == 94.8
    assert broker.get_open_position("BTC/USDT") is None
    assert tp_id in exchange.cancelled_order_ids  # untriggered sibling cleaned up
    assert "BTC/USDT" not in broker._protective_order_ids


def test_check_stop_and_target_detects_a_native_take_profit_fill():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)
    _open_long(broker)
    tp_id = broker._protective_order_ids["BTC/USDT"]["take_profit"]
    exchange.fill_order(tp_id, price=110.2)

    fill = broker.check_stop_and_target("BTC/USDT", _FLAT_BAR, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"))

    assert fill is not None
    assert fill.reason == "take_profit"
    assert fill.price == 110.2


def test_check_stop_and_target_returns_none_while_native_orders_still_open():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)
    _open_long(broker)

    # A bar that would trip the bar-based fallback must NOT close the
    # position while native orders are tracked and still unfilled -
    # neither order has actually closed on the exchange yet.
    triggering_bar = pd.Series({"open": 100.0, "high": 111.0, "low": 90.0, "close": 92.0})
    fill = broker.check_stop_and_target("BTC/USDT", triggering_bar, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"))

    assert fill is None
    assert broker.get_open_position("BTC/USDT") is not None


def test_check_stop_and_target_falls_back_to_bar_polling_when_native_disabled():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=False)
    _open_long(broker)

    bar = pd.Series({"open": 100.0, "high": 101.0, "low": 90.0, "close": 91.0})
    fill = broker.check_stop_and_target("BTC/USDT", bar, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"))

    assert fill is not None
    assert fill.reason == "stop_loss"
    assert fill.price == 100.0  # the fake exchange's fixed fill price for the reduceOnly close order
