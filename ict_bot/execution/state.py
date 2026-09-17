"""Persist live-loop state across restarts.

Without this the bot is amnesiac: ``systemd`` restarts it (``Restart=always``),
the process comes up with an empty ``_positions`` dict and a fresh simulated
balance, and it believes it is flat. Two things then go wrong.

In paper mode the simulated account silently resets to its starting
balance, so a multi-week paper run is really a series of unrelated short
runs - and the equity you read at the end is not the result of what the bot
did.

In live mode it is worse: the real position is still open on the exchange
with its stop resting, but the bot no longer trails that stop, no longer
counts the position against ``max_open_positions``, and will open a second
position on the next signal.

The state is written as JSON after every change that matters. It is a
cache, not a ledger: if it is missing or unreadable the bot starts fresh
(and says so), because refusing to start would be worse than restarting
flat. The trade journal, not this file, is the durable record.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

import pandas as pd

from ict_bot.execution.broker import Broker, Fill, Position
from ict_bot.strategy.ict_strategy import Side
from ict_bot.strategy.risk import RiskManager

logger = logging.getLogger(__name__)

STATE_VERSION = 1


def _position_to_dict(position: Position) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "side": position.side.value,
        "amount": position.amount,
        "entry_price": position.entry_price,
        # The trailed level, not the level it was opened with - that is the
        # whole point of persisting a trailing position.
        "stop_loss": position.stop_loss,
        "take_profit": position.take_profit,
        "opened_at": position.opened_at.isoformat(),
        "trail_distance": position.trail_distance,
    }


def _position_from_dict(raw: dict[str, Any]) -> Position:
    return Position(
        symbol=raw["symbol"],
        side=Side(raw["side"]),
        amount=float(raw["amount"]),
        entry_price=float(raw["entry_price"]),
        stop_loss=float(raw["stop_loss"]),
        take_profit=None if raw.get("take_profit") is None else float(raw["take_profit"]),
        opened_at=pd.Timestamp(raw["opened_at"]),
        trail_distance=None if raw.get("trail_distance") is None else float(raw["trail_distance"]),
    )


def _fill_to_dict(fill: Fill) -> dict[str, Any]:
    return {
        "symbol": fill.symbol,
        "side": fill.side.value,
        "amount": fill.amount,
        "price": fill.price,
        "timestamp": fill.timestamp.isoformat(),
        "reason": fill.reason,
        "fee": fill.fee,
    }


def _fill_from_dict(raw: dict[str, Any]) -> Fill:
    return Fill(
        symbol=raw["symbol"],
        side=Side(raw["side"]),
        amount=float(raw["amount"]),
        price=float(raw["price"]),
        timestamp=pd.Timestamp(raw["timestamp"]),
        reason=raw.get("reason", "entry"),
        fee=float(raw.get("fee", 0.0)),
    )


def save_state(path: str, broker: Broker, risk_manager: RiskManager, symbol: str, mode: str) -> None:
    """Write the state atomically, so a crash mid-write cannot leave a
    truncated file that the next start would refuse to parse."""
    state: dict[str, Any] = {
        "version": STATE_VERSION,
        "symbol": symbol,
        "mode": mode,
        "positions": [_position_to_dict(p) for p in getattr(broker, "_positions", {}).values()],
        "fills": [_fill_to_dict(f) for f in getattr(broker, "fills", [])],
        "open_positions": risk_manager.open_positions,
        "day_start_balance": risk_manager._day_start_balance,
        "current_day": str(risk_manager._current_day) if risk_manager._current_day else None,
    }
    if hasattr(broker, "balance"):  # paper only - live balance lives on the exchange
        state["balance"] = broker.balance
        state["fees_paid"] = getattr(broker, "fees_paid", 0.0)
    if hasattr(broker, "_protective_orders"):
        state["protective_orders"] = {k: list(v) for k, v in broker._protective_orders.items()}

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except Exception:  # pragma: no cover - disk full, permissions, ...
        logger.exception("Could not save state to %s; continuing without it", path)


def restore_state(path: str, broker: Broker, risk_manager: RiskManager, symbol: str, mode: str) -> bool:
    """Load a previously saved state into the broker and risk manager.

    Returns True if anything was restored. A state file for a different
    symbol or mode is ignored rather than applied - reusing one instance's
    state for another would attribute the wrong positions to the wrong
    market.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        logger.exception("State file %s is unreadable; starting flat", path)
        return False

    if state.get("version") != STATE_VERSION:
        logger.warning("State file %s is version %s, expected %s; starting flat",
                       path, state.get("version"), STATE_VERSION)
        return False
    if state.get("symbol") != symbol or state.get("mode") != mode:
        logger.warning("State file %s belongs to %s/%s, not %s/%s; starting flat",
                       path, state.get("symbol"), state.get("mode"), symbol, mode)
        return False

    if "balance" in state and hasattr(broker, "balance"):
        broker.balance = float(state["balance"])
        broker.fees_paid = float(state.get("fees_paid", 0.0))
    broker.fills = [_fill_from_dict(f) for f in state.get("fills", [])]
    positions = {}
    for raw in state.get("positions", []):
        position = _position_from_dict(raw)
        positions[position.symbol] = position
    broker._positions = positions
    if hasattr(broker, "_protective_orders"):
        broker._protective_orders = {k: (v[0], v[1]) for k, v in state.get("protective_orders", {}).items()}

    risk_manager.open_positions = int(state.get("open_positions", len(positions)))
    risk_manager._day_start_balance = state.get("day_start_balance")
    current_day = state.get("current_day")
    risk_manager._current_day = pd.Timestamp(current_day).date() if current_day else None

    for position in positions.values():
        logger.info("Restored open %s position: %.8f %s @ %.4f, stop %.4f%s",
                    position.side.value, position.amount, position.symbol, position.entry_price,
                    position.stop_loss, " (trailing)" if position.trail_distance else "")
    if "balance" in state:
        logger.info("Restored simulated balance %.2f (%d fills so far)", broker.balance, len(broker.fills))
    return True


def peek_open_positions(path: str) -> list[dict[str, Any]]:
    """What a saved state says is open, without starting a broker.

    For tooling that has to decide whether stopping an instance would
    strand a position. Deliberately forgiving: an absent, unreadable or
    older state file reads as "nothing known", because the caller's job is
    to warn, not to refuse.
    """
    try:
        with open(path) as f:
            state = json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        logger.warning("State file %s is unreadable; assuming it holds nothing", path)
        return []
    if not isinstance(state, dict):
        return []
    positions = state.get("positions", [])
    return [p for p in positions if isinstance(p, dict)]
