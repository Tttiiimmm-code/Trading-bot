"""Live/testnet broker backed by ccxt. Places a market order for entry and
tries to attach exchange-side stop-loss/take-profit orders via ccxt's
unified ``stopLossPrice``/``takeProfitPrice`` params where the exchange
supports them; otherwise stop/target must be enforced by polling
(:meth:`check_stop_and_target`), same as the paper broker.

A trailing stop gets a native order too, cancelled and re-placed each time
the trail ratchets. Managing it by polling alone would leave the position
unprotected between polls - on a 4h timeframe, four hours at a time.

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


def quote_currency_from_symbol(symbol: str, default: str = "USDT") -> str:
    """Extract the quote currency from a ccxt unified symbol, e.g.
    ``"BTC/USDT"`` -> ``"USDT"``, and ``"BTC/USDT:USDT"`` (swaps/futures,
    where ``:SETTLE`` names the settlement currency) -> ``"USDT"``.
    """
    if "/" not in symbol:
        return default
    quote = symbol.split("/", 1)[1]
    return quote.split(":", 1)[0] or default


class CCXTBroker(Broker):
    def __init__(self, exchange: ccxt.Exchange, quote_currency: str = "USDT", use_native_sl_tp: bool = True):
        self.exchange = exchange
        self.quote_currency = quote_currency
        self.use_native_sl_tp = use_native_sl_tp
        self._positions: dict[str, Position] = {}
        # symbol -> (stop_loss_order_id, take_profit_order_id) for positions
        # whose protective orders were placed natively on the exchange.
        self._protective_orders: dict[str, tuple[str, str | None]] = {}
        self.fills: list[Fill] = []

    def get_balance(self) -> float:
        balance = self.exchange.fetch_balance()
        quote = balance.get(self.quote_currency, balance.get("total", {}))
        if isinstance(quote, dict):
            return float(quote.get("free", 0.0))
        return float(quote or 0.0)

    def get_open_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def open_position(self, symbol: str, side: Side, amount: float, price: float, stop_loss: float,
                      take_profit: float | None, ts: pd.Timestamp, trail_distance: float | None = None) -> Position:
        if symbol in self._positions:
            raise ValueError(f"Position already open for {symbol}")

        ccxt_side = "buy" if side == Side.LONG else "sell"
        order = self.exchange.create_order(symbol, type="market", side=ccxt_side, amount=amount)
        fill_price = float(order.get("average") or order.get("price") or price)

        if self.use_native_sl_tp:
            try:
                self._protective_orders[symbol] = self._place_protective_orders(symbol, side, amount, stop_loss, take_profit)
            except Exception:  # pragma: no cover - exchange/network dependent
                logger.exception("Failed to place native SL/TP orders for %s; falling back to polling.", symbol)

        position = Position(symbol=symbol, side=side, amount=amount, entry_price=fill_price, stop_loss=stop_loss,
                            take_profit=take_profit, opened_at=ts, trail_distance=trail_distance)
        self._positions[symbol] = position
        self.fills.append(Fill(symbol, side, amount, fill_price, ts, reason="entry"))
        return position

    def _place_protective_orders(self, symbol: str, side: Side, amount: float, stop_loss: float,
                                 take_profit: float | None) -> tuple[str, str | None]:
        close_side = "sell" if side == Side.LONG else "buy"
        sl_order = self.exchange.create_order(symbol, type="market", side=close_side, amount=amount, params={"stopLossPrice": stop_loss, "reduceOnly": True})
        if take_profit is None:
            return sl_order["id"], None  # open-ended exit: stop only
        try:
            tp_order = self.exchange.create_order(symbol, type="market", side=close_side, amount=amount, params={"takeProfitPrice": take_profit, "reduceOnly": True})
        except Exception:
            # Don't leave the already-placed stop-loss order resting on the
            # exchange, untracked, if the take-profit leg failed to place.
            try:
                self.exchange.cancel_order(sl_order["id"], symbol)
            except Exception:  # pragma: no cover - exchange/network dependent
                logger.exception("Failed to roll back orphaned stop-loss order %s for %s", sl_order["id"], symbol)
            raise
        return sl_order["id"], tp_order["id"]

    def _cancel_protective_orders(self, symbol: str, skip_order_id: str | None = None) -> None:
        order_ids = self._protective_orders.pop(symbol, None)
        if not order_ids:
            return
        for order_id in order_ids:
            if order_id == skip_order_id:
                continue
            try:
                self.exchange.cancel_order(order_id, symbol)
            except Exception:  # pragma: no cover - exchange/network dependent
                logger.exception("Failed to cancel leftover protective order %s for %s", order_id, symbol)

    def _move_native_stop(self, symbol: str, position: Position) -> None:
        """Re-place the resting exchange-side stop at the trailed level.

        A resting stop order never moves by itself, so a trailing exit has
        to cancel and re-place it. Order matters: cancel first, because two
        live stop orders for one position can both trigger. If the
        replacement fails, drop the native tracking so the polling fallback
        takes over rather than leaving the position unprotected *and*
        believed to be protected.
        """
        order_ids = self._protective_orders.get(symbol)
        if order_ids is None:
            return
        sl_id, tp_id = order_ids
        try:
            self.exchange.cancel_order(sl_id, symbol)
        except Exception:  # pragma: no cover - exchange/network dependent
            logger.exception("Failed to cancel stop order %s for %s while trailing; leaving it in place", sl_id, symbol)
            return
        close_side = "sell" if position.side == Side.LONG else "buy"
        try:
            order = self.exchange.create_order(symbol, type="market", side=close_side, amount=position.amount,
                                               params={"stopLossPrice": position.stop_loss, "reduceOnly": True})
        except Exception:  # pragma: no cover - exchange/network dependent
            self._protective_orders.pop(symbol, None)
            logger.exception("Failed to re-place the trailing stop for %s at %.8f; falling back to polling",
                             symbol, position.stop_loss)
            return
        self._protective_orders[symbol] = (order["id"], tp_id)
        logger.info("Trailed the %s stop to %.8f (order %s)", symbol, position.stop_loss, order["id"])

    def _trail(self, position: Position, bar: pd.Series) -> bool:
        """Ratchet the stop along with the bar. Returns True if it moved."""
        if position.trail_distance is None:
            return False
        if position.side == Side.LONG:
            new_stop = max(position.stop_loss, float(bar["high"]) - position.trail_distance)
        else:
            new_stop = min(position.stop_loss, float(bar["low"]) + position.trail_distance)
        if new_stop == position.stop_loss:
            return False
        position.stop_loss = new_stop
        return True

    def close_position(self, symbol: str, price: float, ts: pd.Timestamp, reason: str = "manual_close") -> Fill | None:
        position = self._positions.pop(symbol, None)
        if position is None:
            return None
        # Cancel any still-resting native SL/TP order before sending our own
        # close, so a stop or target that hasn't triggered yet doesn't sit on
        # the exchange as an orphaned order for a position that no longer
        # exists locally.
        self._cancel_protective_orders(symbol)
        close_side = "sell" if position.side == Side.LONG else "buy"
        order = self.exchange.create_order(symbol, type="market", side=close_side, amount=position.amount, params={"reduceOnly": True})
        fill_price = float(order.get("average") or order.get("price") or price)
        fill = Fill(symbol, position.side, position.amount, fill_price, ts, reason=reason)
        self.fills.append(fill)
        return fill

    def _check_native_fill(self, symbol: str, position: Position, ts: pd.Timestamp) -> Fill | None:
        """If native protective orders are resting for this position, check
        whether the exchange already filled one of them. Checked in
        stop-then-target order to match the paper broker's conservative
        same-bar tie-break.
        """
        order_ids = self._protective_orders.get(symbol)
        if order_ids is None:
            return None
        sl_id, tp_id = order_ids
        for order_id, reason, fallback_price in ((sl_id, "stop_loss", position.stop_loss), (tp_id, "take_profit", position.take_profit)):
            if order_id is None:
                continue
            try:
                order = self.exchange.fetch_order(order_id, symbol)
            except Exception:  # pragma: no cover - exchange/network dependent
                logger.exception("Failed to fetch protective order %s for %s", order_id, symbol)
                continue
            if order.get("status") == "closed" and float(order.get("filled") or 0) > 0:
                fill_price = float(order.get("average") or order.get("price") or fallback_price)
                self._cancel_protective_orders(symbol, skip_order_id=order_id)
                self._positions.pop(symbol, None)
                fill = Fill(symbol, position.side, position.amount, fill_price, ts, reason=reason)
                self.fills.append(fill)
                return fill
        return None

    def check_stop_and_target(self, symbol: str, bar: pd.Series, ts: pd.Timestamp) -> Fill | None:
        """Polling fallback for exchanges/setups where native SL/TP orders
        weren't placed: mirrors the paper broker's conservative logic.

        When native orders *are* resting for this position, the exchange is
        the source of truth: we only report a close once one of those orders
        has actually filled, instead of also simulating a close from the bar
        range and firing a second, redundant close order.
        """
        position = self._positions.get(symbol)
        if position is None:
            return None

        native_fill = self._check_native_fill(symbol, position, ts)
        if native_fill is not None:
            return native_fill
        if symbol in self._protective_orders:
            # Native orders are still resting and unfilled, so the exchange
            # handles the exit - but a trailing stop has to be dragged along
            # by hand, since a resting order stays where it was placed.
            if self._trail(position, bar):
                self._move_native_stop(symbol, position)
            return None

        if position.side == Side.LONG:
            if bar["low"] <= position.stop_loss:
                return self.close_position(symbol, position.stop_loss, ts, reason="stop_loss")
            if position.take_profit is not None and bar["high"] >= position.take_profit:
                return self.close_position(symbol, position.take_profit, ts, reason="take_profit")
        else:
            if bar["high"] >= position.stop_loss:
                return self.close_position(symbol, position.stop_loss, ts, reason="stop_loss")
            if position.take_profit is not None and bar["low"] <= position.take_profit:
                return self.close_position(symbol, position.take_profit, ts, reason="take_profit")

        # Same ordering rule as the paper broker: this bar is judged against
        # the stop it opened with, and only then drags the stop along.
        self._trail(position, bar)
        return None
