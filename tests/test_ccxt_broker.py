"""CCXTBroker tests against a minimal fake ccxt exchange.

Covers two bugs found by reasoning about the live execution path:

1. ``get_balance()`` used to hardcode "USDT" as the quote currency,
   regardless of the configured ``market.symbol`` - trading any other pair
   (e.g. BTC/EUR) would read the wrong or a missing balance live.
2. The native stop-loss/take-profit orders placed by
   ``_place_protective_orders`` are two independent conditional orders,
   not an atomic OCO pair. Nothing tracked or cancelled the untriggered
   sibling once a position closed, orphaning a live conditional order on
   the exchange that could fire unexpectedly against a later position.
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
        self._next_order_id = 1

    def fetch_balance(self) -> dict:
        return self._balances

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        order_id = str(self._next_order_id)
        self._next_order_id += 1
        order = {"id": order_id, "symbol": symbol, "type": type, "side": side, "amount": amount, "price": 100.0, "params": params or {}}
        self.created_orders.append(order)
        return order

    def cancel_order(self, order_id, symbol):
        self.cancelled_order_ids.append(order_id)


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


def test_open_position_places_two_protective_orders_and_tracks_their_ids():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)

    _open_long(broker)

    protective = [o for o in exchange.created_orders if o["params"].get("reduceOnly")]
    assert len(protective) == 2
    assert {o["params"].get("stopLossPrice") for o in protective if "stopLossPrice" in o["params"]} == {95.0}
    assert {o["params"].get("takeProfitPrice") for o in protective if "takeProfitPrice" in o["params"]} == {110.0}
    assert broker._protective_order_ids["BTC/USDT"] == [o["id"] for o in protective]


def test_closing_a_position_cancels_the_untriggered_sibling_order():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)
    _open_long(broker)
    protective_ids = broker._protective_order_ids["BTC/USDT"]

    fill = broker.close_position("BTC/USDT", price=110.0, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"), reason="take_profit")

    assert fill is not None
    assert set(exchange.cancelled_order_ids) == set(protective_ids)
    assert "BTC/USDT" not in broker._protective_order_ids  # no orphaned tracking left behind


def test_check_stop_and_target_fallback_also_cancels_protective_orders():
    exchange = _FakeExchange()
    broker = CCXTBroker(exchange, symbol="BTC/USDT", use_native_sl_tp=True)
    _open_long(broker)
    protective_ids = broker._protective_order_ids["BTC/USDT"]

    bar = pd.Series({"open": 100.0, "high": 101.0, "low": 90.0, "close": 91.0})
    fill = broker.check_stop_and_target("BTC/USDT", bar, ts=pd.Timestamp("2024-01-01 11:00", tz="UTC"))

    assert fill is not None and fill.reason == "stop_loss"
    assert set(exchange.cancelled_order_ids) == set(protective_ids)


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
