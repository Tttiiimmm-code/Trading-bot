"""Bar-by-bar backtest loop.

A generated :class:`~ict_bot.strategy.ict_strategy.Signal` names an entry
*price* inside the order block / FVG zone, not the current market price -
in reality that would rest as a limit order until price retraces into the
zone. The engine mirrors that: a signal becomes a :class:`PendingOrder`
that is filled on the first future bar whose range trades through the
entry price, and expires unfilled after ``pending_order_expiry_bars``
bars if price never comes back.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict_bot.execution.broker import Fill
from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.ict_strategy import ICTStrategy, Side, Signal
from ict_bot.strategy.risk import RiskManager


@dataclass
class BacktestConfig:
    symbol: str = "BTC/USDT"
    starting_balance: float = 10_000.0
    window_size: int = 300
    pending_order_expiry_bars: int = 8


@dataclass
class PendingOrder:
    signal: Signal
    created_at_i: int


@dataclass
class BacktestResult:
    fills: list[Fill]
    equity_curve: pd.Series
    final_balance: float


class BacktestEngine:
    def __init__(self, df: pd.DataFrame, strategy: ICTStrategy, risk_manager: RiskManager, config: BacktestConfig | None = None):
        self.df = df
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.config = config or BacktestConfig()
        self.broker = PaperBroker(self.config.starting_balance)

    def run(self) -> BacktestResult:
        cfg = self.config
        symbol = cfg.symbol
        df = self.df
        pending: PendingOrder | None = None
        equity_index: list[pd.Timestamp] = []
        equity_values: list[float] = []

        for i in range(len(df)):
            ts = df.index[i]
            bar = df.iloc[i]

            fill = self.broker.check_stop_and_target(symbol, bar, ts)
            if fill is not None:
                self.risk_manager.register_close()

            if pending is not None:
                filled = self._try_fill(pending, bar, ts, symbol)
                expired = not filled and (i - pending.created_at_i) > cfg.pending_order_expiry_bars
                if filled or expired:
                    pending = None

            if pending is None and self.broker.get_open_position(symbol) is None:
                if self.risk_manager.can_open_trade(ts, self.broker.get_balance()):
                    window_start = max(0, i - cfg.window_size + 1)
                    window = df.iloc[window_start : i + 1]
                    signal = self.strategy.generate_signal(window)
                    if signal is not None:
                        pending = PendingOrder(signal=signal, created_at_i=i)

            equity_index.append(ts)
            equity_values.append(self._mark_to_market(bar, symbol))

        equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_index), name="equity")
        return BacktestResult(fills=self.broker.fills, equity_curve=equity_curve, final_balance=self.broker.get_balance())

    def _try_fill(self, pending: PendingOrder, bar: pd.Series, ts: pd.Timestamp, symbol: str) -> bool:
        signal = pending.signal
        touched = bar["low"] <= signal.entry <= bar["high"]
        if not touched:
            return False
        amount = self.risk_manager.position_size(self.broker.get_balance(), signal.entry, signal.stop_loss)
        if amount <= 0:
            return False
        self.broker.open_position(symbol, signal.side, amount, signal.entry, signal.stop_loss, signal.take_profit, ts)
        self.risk_manager.register_open()
        return True

    def _mark_to_market(self, bar: pd.Series, symbol: str) -> float:
        position = self.broker.get_open_position(symbol)
        balance = self.broker.get_balance()
        if position is None:
            return balance
        unrealized = (bar["close"] - position.entry_price) * position.amount
        if position.side == Side.SHORT:
            unrealized = -unrealized
        return balance + unrealized
