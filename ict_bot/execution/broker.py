"""Broker abstraction shared by the paper simulator and the live ccxt
broker, so strategy/risk code never has to branch on "are we live or
paper".
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

import pandas as pd

from ict_bot.strategy.ict_strategy import Side


@dataclass
class Position:
    symbol: str
    side: Side
    amount: float
    entry_price: float
    stop_loss: float
    take_profit: float | None
    opened_at: pd.Timestamp
    trail_distance: float | None = None


@dataclass
class Fill:
    symbol: str
    side: Side
    amount: float
    price: float
    timestamp: pd.Timestamp
    reason: str = "entry"  # entry | stop_loss | take_profit | manual_close
    fee: float = 0.0  # commission charged for this fill, in quote currency


class Broker(abc.ABC):
    """Minimal order/position interface. Only what the bot needs: no
    order-book access, no order types beyond market - keep the surface
    small and easy to reason about for risk purposes.
    """

    @abc.abstractmethod
    def get_balance(self) -> float: ...

    @abc.abstractmethod
    def open_position(self, symbol: str, side: Side, amount: float, price: float, stop_loss: float,
                      take_profit: float | None, ts: pd.Timestamp, trail_distance: float | None = None) -> Position: ...

    @abc.abstractmethod
    def close_position(self, symbol: str, price: float, ts: pd.Timestamp, reason: str = "manual_close") -> Fill | None: ...

    @abc.abstractmethod
    def get_open_position(self, symbol: str) -> Position | None: ...

    @abc.abstractmethod
    def check_stop_and_target(self, symbol: str, bar: pd.Series, ts: pd.Timestamp) -> Fill | None:
        """Given the latest OHLC bar, check whether the open position's
        stop-loss or take-profit was hit intrabar and close it if so.
        """
        ...
