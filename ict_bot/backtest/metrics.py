"""Turn a list of broker fills + an equity curve into trade-level and
portfolio-level performance metrics.

Assumes at most one open position per symbol at a time (the default
``max_open_positions=1`` risk setting), so fills simply alternate
entry, exit, entry, exit, ... per symbol.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict_bot.execution.broker import Fill
from ict_bot.strategy.ict_strategy import Side


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    amount: float
    opened_at: pd.Timestamp
    closed_at: pd.Timestamp
    exit_reason: str
    pnl: float  # net of commissions - what the balance actually changed by
    fees: float = 0.0  # commission paid across both fills

    @property
    def gross_pnl(self) -> float:
        return self.pnl + self.fees


def pair_trades(fills: list[Fill]) -> list[Trade]:
    trades: list[Trade] = []
    open_fills: dict[str, Fill] = {}  # keyed by symbol, in case fills interleave across markets
    for fill in fills:
        if fill.reason == "entry":
            open_fills[fill.symbol] = fill
            continue
        open_fill = open_fills.pop(fill.symbol, None)
        if open_fill is None:
            continue
        if open_fill.side == Side.LONG:
            gross = (fill.price - open_fill.price) * open_fill.amount
        else:
            gross = (open_fill.price - fill.price) * open_fill.amount
        # Net of commissions: with stops this tight the fee is a meaningful
        # fraction of the amount risked, so a gross win rate / profit factor
        # would flatter the strategy against the balance it actually leaves.
        fees = open_fill.fee + fill.fee
        trades.append(
            Trade(
                symbol=open_fill.symbol,
                side=open_fill.side,
                entry_price=open_fill.price,
                exit_price=fill.price,
                amount=open_fill.amount,
                opened_at=open_fill.timestamp,
                closed_at=fill.timestamp,
                exit_reason=fill.reason,
                pnl=gross - fees,
                fees=fees,
            )
        )
    return trades


def max_drawdown_pct(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max.replace(0, pd.NA)
    return float(drawdown.min(skipna=True) * 100.0) if not drawdown.empty else 0.0


def compute_metrics(trades: list[Trade], equity_curve: pd.Series, starting_balance: float) -> dict:
    """Trade- and portfolio-level figures.

    Note what "gross" means in ``gross_profit``/``gross_loss``: the sum of
    the winners and the sum of the losers, before netting the two against
    each other. It is the standard profit-factor terminology, but it does
    NOT mean "before commissions" - every figure here is built from
    ``Trade.pnl``, which is already net of fees. ``Trade.gross_pnl`` is the
    one that means before commissions.

    ``total_return_pct`` and ``final_balance`` come from the equity curve,
    which is marked to market, so a position still open on the last bar
    counts at its unrealised value even though it is not in ``num_trades``.
    """
    num_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    final_balance = float(equity_curve.iloc[-1]) if not equity_curve.empty else starting_balance

    return {
        "num_trades": num_trades,
        "win_rate_pct": (len(wins) / num_trades * 100.0) if num_trades else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": -gross_loss,
        "total_return_pct": (final_balance - starting_balance) / starting_balance * 100.0 if starting_balance else 0.0,
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "final_balance": final_balance,
        "avg_win": (gross_profit / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
    }
