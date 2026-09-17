"""A shared view of what every bot instance currently holds.

Each instance runs as its own process with its own risk manager, so
``max_open_positions`` only ever limits that one instance. Run one market
per instance - which is how the measured results were produced - and
nothing counts the total. Twenty-nine bots risking 0.5% each can have
14.5% of a real account at risk at once, and no part of the system
notices.

This is a notice board, not a lock manager. Every instance publishes the
positions it holds, under its own id, and reads the board before opening
anything new. Writes take a file lock and replace the file atomically, so
a crash mid-write cannot corrupt it.

Two deliberate limits, because the alternative is a distributed
transaction for a problem that does not need one:

* An entry whose instance has stopped republishing is treated as gone
  after ``stale_after_minutes``. A dead bot must not hold a slot forever;
  a live one refreshes its entry every bar.
* Two instances checking at the same instant can both see room and both
  open, so the cap can be exceeded by one. With bar-close trading and
  staggered polls this is rare, and one extra position is a far smaller
  risk than the coordination machinery needed to make it impossible.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slot:
    bot_id: str
    symbol: str
    side: str
    updated_at: pd.Timestamp

    def is_stale(self, now: pd.Timestamp, stale_after_minutes: float) -> bool:
        return (now - self.updated_at) > pd.Timedelta(minutes=stale_after_minutes)


class PortfolioBoard:
    def __init__(self, path: str, stale_after_minutes: float = 30.0):
        self.path = path
        self.stale_after_minutes = stale_after_minutes

    # -- reading -------------------------------------------------------
    def _read(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception:
            logger.exception("Portfolio board %s is unreadable; treating it as empty", self.path)
            return {}

    def slots(self, now: pd.Timestamp) -> list[Slot]:
        """Every position currently claimed, stale entries dropped."""
        out = []
        for bot_id, entry in self._read().items():
            try:
                updated = pd.Timestamp(entry["updated_at"])
            except Exception:
                continue
            for position in entry.get("positions", []):
                slot = Slot(bot_id=bot_id, symbol=position.get("symbol", "?"),
                            side=position.get("side", "?"), updated_at=updated)
                if not slot.is_stale(now, self.stale_after_minutes):
                    out.append(slot)
        return out

    def open_elsewhere(self, bot_id: str, now: pd.Timestamp) -> int:
        """How many positions other instances are holding right now."""
        return sum(1 for slot in self.slots(now) if slot.bot_id != bot_id)

    def can_open(self, bot_id: str, max_positions: int, now: pd.Timestamp) -> tuple[bool, int]:
        """Whether a new position would fit under the shared cap.

        Returns the decision and how many positions the portfolio already
        holds, so the caller can say why it stood down.
        """
        if max_positions <= 0:
            return True, 0
        held = len(self.slots(now))
        return held < max_positions, held

    # -- writing -------------------------------------------------------
    def publish(self, bot_id: str, positions: list, now: pd.Timestamp) -> None:
        """Record what this instance holds, and refresh its timestamp.

        Called on every bar, not only on a change: the timestamp is what
        tells the other instances this one is still alive.
        """
        entry = {
            "updated_at": now.isoformat(),
            "positions": [{"symbol": p.symbol, "side": p.side.value} for p in positions],
        }
        self._mutate(lambda board: board.__setitem__(bot_id, entry),
                     f"publish {bot_id} to")

    def release(self, bot_id: str) -> None:
        """Remove an instance's entry entirely.

        For an instance being retired on purpose. Stopping a bot does not
        remove what it published, so a retired flat bot's entry lingers
        until ``stale_after_minutes``, and a retired bot that held a
        position leaves an entry no live process will ever update - a slot
        claimed by nobody. Deleting it is only correct once that bot is
        really gone and really flat; the caller checks both.
        """
        self._mutate(lambda board: board.pop(bot_id, None), f"release {bot_id} from")

    def _mutate(self, change, what: str) -> None:
        """Apply a change to the board under an exclusive lock.

        The lock is a separate file so the board itself can be replaced
        atomically underneath it - replacing a file another process holds
        open by name would drop the lock.
        """
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        lock_path = self.path + ".lock"
        try:
            with open(lock_path, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    board = self._read()
                    change(board)
                    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
                    with os.fdopen(fd, "w") as f:
                        json.dump(board, f, indent=2)
                    os.replace(tmp, self.path)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        except Exception:  # pragma: no cover - disk full, permissions, ...
            logger.exception("Could not %s the portfolio board %s", what, self.path)
