"""Paper-trading broker: simulates fills instantly at the requested price
and evaluates stop-loss/take-profit against OHLC bars. Used for both
backtesting and live paper trading (feeding it real-time bars but never
touching a real exchange).

Trading costs matter here far more than they look: this strategy places
its stop just beyond a swept level, so the risked distance is often only a
few tenths of a percent of price. A round trip then eats a large share of
the amount risked per trade - enough to decide whether a small edge
survives at all - so set these to the exchange's real numbers for any
backtest you intend to believe. They default to zero (frictionless,
matching the original behaviour).

Maker and taker are charged separately because this strategy's order mix
is lopsided: the entry and the take-profit are resting limit orders (maker,
and on many venues a third of the taker rate or less), while only the
stop-out crosses the spread. Charging everything at the taker rate roughly
doubles the modelled cost.

Slippage likewise applies to stop-loss exits only - a limit order fills at
its price or not at all, a stop pays up to get out.
"""
from __future__ import annotations

import pandas as pd

from ict_bot.execution.broker import Broker, Fill, Position
from ict_bot.strategy.ict_strategy import Side


class PaperBroker(Broker):
    def __init__(self, starting_balance: float, maker_fee_pct: float = 0.0,
                 taker_fee_pct: float = 0.0, stop_slippage_pct: float = 0.0):
        self.balance = starting_balance
        self.maker_fee_pct = maker_fee_pct
        self.taker_fee_pct = taker_fee_pct
        self.stop_slippage_pct = stop_slippage_pct
        self.fees_paid = 0.0
        self._positions: dict[str, Position] = {}
        self.fills: list[Fill] = []

    def _charge_fee(self, amount: float, price: float, maker: bool) -> None:
        rate = self.maker_fee_pct if maker else self.taker_fee_pct
        fee = abs(amount * price) * rate / 100.0
        self.balance -= fee
        self.fees_paid += fee

    def get_balance(self) -> float:
        return self.balance

    def get_open_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def open_position(self, symbol: str, side: Side, amount: float, price: float, stop_loss: float, take_profit: float, ts: pd.Timestamp) -> Position:
        if symbol in self._positions:
            raise ValueError(f"Position already open for {symbol}")
        position = Position(symbol=symbol, side=side, amount=amount, entry_price=price, stop_loss=stop_loss, take_profit=take_profit, opened_at=ts)
        self._positions[symbol] = position
        # The entry rests as a limit order in the order block, so it makes.
        self._charge_fee(amount, price, maker=True)
        self.fills.append(Fill(symbol, side, amount, price, ts, reason="entry"))
        return position

    def close_position(self, symbol: str, price: float, ts: pd.Timestamp, reason: str = "manual_close") -> Fill | None:
        position = self._positions.pop(symbol, None)
        if position is None:
            return None
        if reason == "stop_loss" and self.stop_slippage_pct:
            # Slippage always works against the position being closed.
            drift = price * self.stop_slippage_pct / 100.0
            price = price - drift if position.side == Side.LONG else price + drift
        pnl = self._pnl(position, price)
        self.balance += pnl
        # Only a stop-out crosses the spread; a take-profit is a resting limit.
        self._charge_fee(position.amount, price, maker=(reason == "take_profit"))
        fill = Fill(symbol, position.side, position.amount, price, ts, reason=reason)
        self.fills.append(fill)
        return fill

    def check_stop_and_target(self, symbol: str, bar: pd.Series, ts: pd.Timestamp) -> Fill | None:
        position = self._positions.get(symbol)
        if position is None:
            return None

        if position.side == Side.LONG:
            hit_stop = bar["low"] <= position.stop_loss
            hit_target = bar["high"] >= position.take_profit
            # conservative: if both levels are inside the same bar, assume the stop was hit first
            if hit_stop:
                return self.close_position(symbol, position.stop_loss, ts, reason="stop_loss")
            if hit_target:
                return self.close_position(symbol, position.take_profit, ts, reason="take_profit")
        else:
            hit_stop = bar["high"] >= position.stop_loss
            hit_target = bar["low"] <= position.take_profit
            if hit_stop:
                return self.close_position(symbol, position.stop_loss, ts, reason="stop_loss")
            if hit_target:
                return self.close_position(symbol, position.take_profit, ts, reason="take_profit")
        return None

    @staticmethod
    def _pnl(position: Position, exit_price: float) -> float:
        if position.side == Side.LONG:
            return (exit_price - position.entry_price) * position.amount
        return (position.entry_price - exit_price) * position.amount
