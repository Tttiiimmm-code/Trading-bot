"""Tests for CCXTBroker against a minimal fake exchange double - no network,
no real ccxt exchange needed. Covers the two live-trading bugs this broker
must avoid:

1. reading the wrong quote currency out of ``fetch_balance()`` (previously
   hardcoded to "USDT", broken for e.g. BTC/EUR or BTC/USDT:USDT symbols).
2. double-closing a position: once a native SL/TP order has actually filled
   on the exchange, the bar-based polling fallback must not *also* fire a
   second close order for the same position.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ict_bot.execution.ccxt_broker import CCXTBroker, quote_currency_from_symbol
from ict_bot.strategy.ict_strategy import Side


class FakeExchange:
    def __init__(self, balances: dict, order_price: float | None = None, fail_nth_create: int | None = None):
        self._balances = balances
        self._orders: dict[str, dict] = {}
        self._next_id = 1
        self.order_price = order_price
        self.cancelled: list[str] = []
        self.fail_nth_create = fail_nth_create
        self._create_calls = 0

    def fetch_balance(self) -> dict:
        return self._balances

    def create_order(self, symbol, type, side, amount, params=None) -> dict:
        self._create_calls += 1
        if self.fail_nth_create is not None and self._create_calls == self.fail_nth_create:
            raise RuntimeError("simulated exchange failure")
        order_id = str(self._next_id)
        self._next_id += 1
        order = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "amount": amount,
            # None mirrors a market order whose average isn't known yet, so
            # the broker falls back to the price it was given - same as a
            # real exchange response that omits "average".
            "average": self.order_price,
            "status": "open",
            "filled": 0.0,
            "params": params or {},
        }
        self._orders[order_id] = order
        return order

    def fetch_order(self, order_id, symbol) -> dict:
        return self._orders[order_id]

    def cancel_order(self, order_id, symbol) -> None:
        self.cancelled.append(order_id)
        self._orders.pop(order_id, None)

    def fill(self, order_id: str, price: float) -> None:
        """Test helper: simulate the exchange filling a resting order."""
        order = self._orders[order_id]
        order["status"] = "closed"
        order["filled"] = order["amount"]
        order["average"] = price


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("BTC/USDT", "USDT"),
        ("BTC/EUR", "EUR"),
        ("BTC/USDT:USDT", "USDT"),  # ccxt unified swap/futures symbol
        ("garbage", "USDT"),  # no "/" -> falls back to default
    ],
)
def test_quote_currency_from_symbol(symbol, expected):
    assert quote_currency_from_symbol(symbol) == expected


def test_get_balance_uses_configured_quote_currency():
    exchange = FakeExchange(balances={"EUR": {"free": 500.0, "used": 0.0, "total": 500.0}})
    broker = CCXTBroker(exchange, quote_currency="EUR")
    assert broker.get_balance() == 500.0


def test_get_balance_does_not_default_to_usdt_for_other_quote():
    # Regression: previously hardcoded to balance["USDT"], which silently
    # returned 0 for any non-USDT-quoted market.
    exchange = FakeExchange(balances={"EUR": {"free": 500.0, "used": 0.0, "total": 500.0}})
    broker = CCXTBroker(exchange, quote_currency="EUR")
    assert broker.get_balance() != 0.0


def _open_long(exchange, broker, ts):
    return broker.open_position(
        "BTC/USDT", Side.LONG, amount=1.0, price=100.0, stop_loss=95.0, take_profit=110.0, ts=ts
    )


def test_native_stop_fill_closes_position_without_extra_order():
    exchange = FakeExchange(balances={})
    broker = CCXTBroker(exchange, use_native_sl_tp=True)
    ts0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    _open_long(exchange, broker, ts0)

    sl_id, tp_id = broker._protective_orders["BTC/USDT"]
    orders_created_before = exchange._next_id

    # Exchange triggers the native stop-loss order.
    exchange.fill(sl_id, price=94.9)

    ts1 = pd.Timestamp("2024-01-01 00:15", tz="UTC")
    bar = pd.Series({"open": 96.0, "high": 96.5, "low": 94.5, "close": 95.0})
    fill = broker.check_stop_and_target("BTC/USDT", bar, ts1)

    assert fill is not None
    assert fill.reason == "stop_loss"
    assert fill.price == 94.9
    # No new order was created to close the position a second time.
    assert exchange._next_id == orders_created_before
    # The unfilled take-profit leg must be cancelled, not left resting.
    assert tp_id in exchange.cancelled
    assert broker.get_open_position("BTC/USDT") is None


def test_native_orders_resting_unfilled_does_not_trigger_bar_fallback():
    exchange = FakeExchange(balances={})
    broker = CCXTBroker(exchange, use_native_sl_tp=True)
    ts0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    _open_long(exchange, broker, ts0)
    orders_before = len(exchange._orders)

    # Bar range trades through the stop level, but the exchange hasn't
    # actually filled the native order yet - must not simulate a close.
    ts1 = pd.Timestamp("2024-01-01 00:15", tz="UTC")
    bar = pd.Series({"open": 96.0, "high": 96.5, "low": 94.5, "close": 95.0})
    fill = broker.check_stop_and_target("BTC/USDT", bar, ts1)

    assert fill is None
    assert broker.get_open_position("BTC/USDT") is not None
    assert len(exchange._orders) == orders_before


def test_polling_fallback_used_when_native_orders_not_placed():
    exchange = FakeExchange(balances={})
    broker = CCXTBroker(exchange, use_native_sl_tp=False)
    ts0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    _open_long(exchange, broker, ts0)
    assert "BTC/USDT" not in broker._protective_orders

    ts1 = pd.Timestamp("2024-01-01 00:15", tz="UTC")
    bar = pd.Series({"open": 96.0, "high": 96.5, "low": 94.5, "close": 95.0})
    fill = broker.check_stop_and_target("BTC/USDT", bar, ts1)

    assert fill is not None
    assert fill.reason == "stop_loss"
    assert fill.price == 95.0  # stop_loss price, not the bar low
    assert broker.get_open_position("BTC/USDT") is None


def test_failed_take_profit_placement_rolls_back_stop_loss_order():
    # entry order = create call #1, stop-loss = #2, take-profit = #3 (fails).
    exchange = FakeExchange(balances={}, fail_nth_create=3)
    broker = CCXTBroker(exchange, use_native_sl_tp=True)
    ts0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")

    _open_long(exchange, broker, ts0)  # must not raise despite the native SL/TP failure

    assert "BTC/USDT" not in broker._protective_orders
    # The stop-loss order that succeeded before the take-profit failed must
    # have been cancelled, not left resting untracked on the exchange.
    assert "2" in exchange.cancelled
    assert broker.get_open_position("BTC/USDT") is not None  # entry itself still succeeded


def test_manual_close_cancels_leftover_native_orders():
    exchange = FakeExchange(balances={})
    broker = CCXTBroker(exchange, use_native_sl_tp=True)
    ts0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    _open_long(exchange, broker, ts0)
    sl_id, tp_id = broker._protective_orders["BTC/USDT"]

    ts1 = pd.Timestamp("2024-01-01 00:15", tz="UTC")
    broker.close_position("BTC/USDT", price=101.0, ts=ts1, reason="manual_close")

    assert sl_id in exchange.cancelled
    assert tp_id in exchange.cancelled
    assert "BTC/USDT" not in broker._protective_orders
