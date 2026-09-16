"""Append-only CSV record of closed trades.

The log file tells you what happened in prose; this makes a paper run
*measurable*. It carries the same fields the backtest's ``Trade`` does, so
a live run can be scored with the same metrics rather than eyeballed - and
being append-only, it survives the restarts that reset everything else.

One row per closed trade, written the moment it closes. Rotating or
deleting the log never touches it.
"""
from __future__ import annotations

import csv
import logging
import os
from typing import Callable

from ict_bot.backtest.metrics import Trade

logger = logging.getLogger(__name__)

FIELDS = ["symbol", "side", "opened_at", "closed_at", "entry_price", "exit_price",
          "amount", "exit_reason", "gross_pnl", "fees", "pnl", "balance_after"]


class TradeJournal:
    """Writes closed trades to ``path``, creating it with a header if new."""

    def __init__(self, path: str):
        self.path = path
        self._recorded = 0

    def catch_up(self, trades: list[Trade], balance_fn: Callable[[], float]) -> int:
        """Record any trades not yet written. Returns how many were added.

        Called with the full list rather than a single trade so a restart
        that replays known fills cannot double-count: everything up to
        ``_recorded`` has already been written. The balance is a callable
        because reading it costs a network round trip on the live broker,
        and most calls here have nothing to write.
        """
        pending = trades[self._recorded:]
        if not pending:
            return 0
        balance = balance_fn()
        added = 0
        for trade in pending:
            if self._append(trade, balance):
                added += 1
        self._recorded = len(trades)
        return added

    def skip(self, count: int) -> None:
        """Mark this many trades as already recorded, without writing them.

        Used after restoring state: those trades were journalled by the
        process that made them, and the file is append-only.
        """
        self._recorded = count

    def _append(self, trade: Trade, balance: float) -> bool:
        try:
            directory = os.path.dirname(os.path.abspath(self.path)) or "."
            os.makedirs(directory, exist_ok=True)
            is_new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
            with open(self.path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                if is_new:
                    writer.writeheader()
                writer.writerow({
                    "symbol": trade.symbol,
                    "side": trade.side.value,
                    "opened_at": trade.opened_at.isoformat(),
                    "closed_at": trade.closed_at.isoformat(),
                    "entry_price": f"{trade.entry_price:.10g}",
                    "exit_price": f"{trade.exit_price:.10g}",
                    "amount": f"{trade.amount:.10g}",
                    "exit_reason": trade.exit_reason,
                    "gross_pnl": f"{trade.gross_pnl:.10g}",
                    "fees": f"{trade.fees:.10g}",
                    "pnl": f"{trade.pnl:.10g}",
                    "balance_after": f"{balance:.10g}",
                })
            return True
        except Exception:  # pragma: no cover - disk full, permissions, ...
            logger.exception("Could not write trade to journal %s", self.path)
            return False
