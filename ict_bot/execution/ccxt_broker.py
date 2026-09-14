"""Live/testnet broker backed by ccxt. Places a market order for entry and
tries to attach exchange-side stop-loss/take-profit orders via ccxt's
unified ``stopLossPrice``/``takeProfitPrice`` params where the exchange
supports them. :meth:`check_stop_and_target` then polls those native
orders' own status as the source of truth for a close; only when no
native orders are tracked for a symbol (``use_native_sl_tp`` is off, or
placing them failed) does it fall back to inferring a close from OHLC
bars, same as the paper broker.

Exchange behaviour for conditional orders varies a lot - always validate
against the target exchange's testnet before risking real funds, and read
the ccxt docs for that specific exchange.
"""
from __future__ import annotations

import logging

import ccxt
import pandas as pd

from ict_bot.execution.broker import Broker, Fill, Position
from ict_bot.strategy.ict_strategy import Side

logger = logging.getLogger(__name__)


class CCXTBroker(Broker):
    def __init__(self, exchange: ccxt.Exchange, symbol: str, use_native_sl_tp: bool = True):
        self.exchange = exchange
        # Balance must be read in the *quote* currency of the traded pair
        # (e.g. "USDT" for BTC/USDT, "EUR" for BTC/EUR) - hardcoding a
        # single currency here would silently size positions off the wrong
        # (or a missing, zeroed) balance for any other market.symbol.
        self.quote_currency = symbol.split("/")[1]
        self.use_native_sl_tp = use_native_sl_tp
        self._positions: dict[str, Position] = {}
        self._protective_order_ids: dict[str, dict[str, str]] = {}
        self.fills: list[Fill] = []

    def get_balance(self) -> float:
        balance = self.exchange.fetch_balance()
        quote = balance.get(self.quote_currency, {})
        if isinstance(quote, dict):
            return float(quote.get("free", 0.0))
        return float(quote or 0.0)

    def get_open_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def open_position(self, symbol: str, side: Side, amount: float, price: float, stop_loss: float, take_profit: float, ts: pd.Timestamp) -> Position:
        if symbol in self._positions:
            raise ValueError(f"Position already open for {symbol}")

        ccxt_side = "buy" if side == Side.LONG else "sell"
        order = self.exchange.create_order(symbol, type="market", side=ccxt_side, amount=amount)
        fill_price = float(order.get("average") or order.get("price") or price)

        if self.use_native_sl_tp:
            try:
                self._protective_order_ids[symbol] = self._place_protective_orders(symbol, side, amount, stop_loss, take_profit)
            except Exception:  # pragma: no cover - exchange/network dependent
                logger.exception("Failed to place native SL/TP orders for %s; falling back to polling.", symbol)

        position = Position(symbol=symbol, side=side, amount=amount, entry_price=fill_price, stop_loss=stop_loss, take_profit=take_profit, opened_at=ts)
        self._positions[symbol] = position
        self.fills.append(Fill(symbol, side, amount, fill_price, ts, reason="entry"))
        return position

    def _place_protective_orders(self, symbol: str, side: Side, amount: float, stop_loss: float, take_profit: float) -> dict[str, str]:
        close_side = "sell" if side == Side.LONG else "buy"
        sl_order = self.exchange.create_order(symbol, type="market", side=close_side, amount=amount, params={"stopLossPrice": stop_loss, "reduceOnly": True})
        tp_order = self.exchange.create_order(symbol, type="market", side=close_side, amount=amount, params={"takeProfitPrice": take_profit, "reduceOnly": True})
        return {k: v for k, v in (("stop_loss", sl_order.get("id")), ("take_profit", tp_order.get("id"))) if v}

    def _cancel_protective_orders(self, symbol: str) -> None:
        """Cancel whichever of the SL/TP bracket orders didn't trigger.

        The two orders placed in ``_place_protective_orders`` are
        independent conditional orders, not an atomic OCO pair - the
        exchange doesn't cancel one when the other fires. Leaving the
        untriggered sibling resting after the position closes (by either
        order filling, or by the polling fallback in
        ``check_stop_and_target``) orphans it on the exchange, ready to
        fire unexpectedly against whatever position exists next.
        """
        order_ids = self._protective_order_ids.pop(symbol, {})
        for order_id in order_ids.values():
            try:
                self.exchange.cancel_order(order_id, symbol)
            except Exception:  # pragma: no cover - exchange/network dependent; already filled/cancelled orders error here
                logger.debug("Could not cancel protective order %s for %s (likely already filled or cancelled).", order_id, symbol)

    def close_position(self, symbol: str, price: float, ts: pd.Timestamp, reason: str = "manual_close") -> Fill | None:
        position = self._positions.pop(symbol, None)
        if position is None:
            return None
        self._cancel_protective_orders(symbol)
        close_side = "sell" if position.side == Side.LONG else "buy"
        order = self.exchange.create_order(symbol, type="market", side=close_side, amount=position.amount, params={"reduceOnly": True})
        fill_price = float(order.get("average") or order.get("price") or price)
        fill = Fill(symbol, position.side, position.amount, fill_price, ts, reason=reason)
        self.fills.append(fill)
        return fill

    def check_stop_and_target(self, symbol: str, bar: pd.Series, ts: pd.Timestamp) -> Fill | None:
        """Detect whether the open position closed.

        When native SL/TP orders are tracked for this symbol, their actual
        status on the exchange is the only source of truth for whether -
        and at what price - the position closed: inferring a close from
        OHLC bars *as well* would let both mechanisms fire independently,
        double-closing an already-flat position (the second close_position
        call issues a real order the exchange will reject) and leaving the
        risk manager's open-position count stuck since a rejected close
        never returns a ``Fill``. Only fall back to bar-based polling when
        no native orders are being tracked (``use_native_sl_tp`` is off, or
        placing them failed and the position is unprotected).
        """
        position = self._positions.get(symbol)
        if position is None:
            return None

        protective = self._protective_order_ids.get(symbol)
        if protective:
            return self._check_native_fill(symbol, position, protective, ts)

        if position.side == Side.LONG:
            if bar["low"] <= position.stop_loss:
                return self.close_position(symbol, position.stop_loss, ts, reason="stop_loss")
            if bar["high"] >= position.take_profit:
                return self.close_position(symbol, position.take_profit, ts, reason="take_profit")
        else:
            if bar["high"] >= position.stop_loss:
                return self.close_position(symbol, position.stop_loss, ts, reason="stop_loss")
            if bar["low"] <= position.take_profit:
                return self.close_position(symbol, position.take_profit, ts, reason="take_profit")
        return None

    def _check_native_fill(self, symbol: str, position: Position, protective: dict[str, str], ts: pd.Timestamp) -> Fill | None:
        for reason, order_id in protective.items():
            try:
                order = self.exchange.fetch_order(order_id, symbol)
            except Exception:  # pragma: no cover - exchange/network dependent
                logger.exception("Could not fetch protective order %s (%s) for %s.", order_id, reason, symbol)
                continue
            if order.get("status") != "closed":
                continue
            self._positions.pop(symbol, None)
            self._cancel_protective_orders(symbol)  # cancel the sibling; cancelling the filled leg itself just no-ops
            target_price = position.stop_loss if reason == "stop_loss" else position.take_profit
            fill_price = float(order.get("average") or order.get("price") or target_price)
            fill = Fill(symbol, position.side, position.amount, fill_price, ts, reason=reason)
            self.fills.append(fill)
            return fill
        return None
