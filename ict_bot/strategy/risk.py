"""Position sizing and account-level risk controls.

The broker (paper or live) is the single source of truth for account
balance - the risk manager never keeps its own copy, it's always handed
the current balance explicitly. That keeps sizing and the daily-loss
circuit breaker from silently drifting out of sync with what the broker
actually did.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0  # % of current balance risked per trade
    max_daily_loss_pct: float = 3.0  # circuit breaker: stop opening new trades for the day
    max_open_positions: int = 1
    max_position_pct: float = 100.0  # cap notional exposure (amount * entry) as % of balance; 100 = no leverage


class RiskManager:
    def __init__(self, config: RiskConfig):
        self.config = config
        self._day_start_balance: float | None = None
        self._current_day = None
        self.open_positions = 0

    def position_size(self, balance: float, entry: float, stop_loss: float) -> float:
        """Units/contracts to buy so a stop-out loses exactly
        ``risk_per_trade_pct`` of ``balance`` - capped so the position's
        notional value never exceeds ``max_position_pct`` of ``balance``.

        Without that cap, a tight stop (common for ICT order-block entries
        close to the swept liquidity) makes ``risk_amount / stop_distance``
        blow up: a stop just 0.4% away from entry turns a 1%-risk sizing
        into several times the account's entire balance in notional
        exposure - implicit, unbounded leverage that a real account can't
        actually take on, and that multiplies any slippage or gap-through
        loss well past the intended risk_per_trade_pct.
        """
        risk_amount = balance * (self.config.risk_per_trade_pct / 100.0)
        stop_distance = abs(entry - stop_loss)
        if stop_distance <= 0 or entry <= 0:
            return 0.0
        size = risk_amount / stop_distance
        max_notional = balance * (self.config.max_position_pct / 100.0)
        return min(size, max_notional / entry)

    def _roll_day(self, ts: pd.Timestamp, balance: float) -> None:
        day = ts.date()
        if self._current_day != day:
            self._current_day = day
            self._day_start_balance = balance

    def daily_loss_hit(self, ts: pd.Timestamp, balance: float) -> bool:
        self._roll_day(ts, balance)
        if not self._day_start_balance:
            return False
        loss_pct = (self._day_start_balance - balance) / self._day_start_balance * 100.0
        return loss_pct >= self.config.max_daily_loss_pct

    def can_open_trade(self, ts: pd.Timestamp, balance: float) -> bool:
        if self.daily_loss_hit(ts, balance):
            return False
        return self.open_positions < self.config.max_open_positions

    def register_open(self) -> None:
        self.open_positions += 1

    def register_close(self) -> None:
        self.open_positions = max(0, self.open_positions - 1)
