"""Paper-trading broker: simulates fills instantly at the requested price
and evaluates stop-loss/take-profit against OHLC bars. Used for both
backtesting and live paper trading (feeding it real-time bars but never
touching a real exchange).
"""
from __future__ import annotations

import pandas as pd

from ict_bot.execution.broker import Broker, Fill, Position
from ict_bot.strategy.ict_strategy import Side


class PaperBroker(Broker):
    def __init__(self, starting_balance: float):
        self.balance = starting_balance
        self._positions: dict[str, Position] = {}
        self.fills: list[Fill] = []

    def get_balance(self) -> float:
        return self.balance

    def get_open_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def open_position(self, symbol: str, side: Side, amount: float, price: float, stop_loss: float, take_profit: float, ts: pd.Timestamp) -> Position:
        if symbol in self._positions:
            raise ValueError(f"Position already open for {symbol}")
        position = Position(symbol=symbol, side=side, amount=amount, entry_price=price, stop_loss=stop_loss, take_profit=take_profit, opened_at=ts)
        self._positions[symbol] = position
        self.fills.append(Fill(symbol, side, amount, price, ts, reason="entry"))
        return position

    def close_position(self, symbol: str, price: float, ts: pd.Timestamp, reason: str = "manual_close") -> Fill | None:
        position = self._positions.pop(symbol, None)
        if position is None:
            return None
        pnl = self._pnl(position, price)
        self.balance += pnl
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
