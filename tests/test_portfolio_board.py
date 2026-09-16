"""The shared position board across instances.

Each bot runs as its own process, so nothing counted the total exposure:
twenty-nine bots risking 0.5% each can have 14.5% at risk with no part of
the system noticing.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ict_bot.execution.broker import Position
from ict_bot.execution.portfolio import PortfolioBoard
from ict_bot.strategy.ict_strategy import Side

NOW = pd.Timestamp("2026-09-16 12:00", tz="UTC")


def _position(symbol: str, side: Side = Side.LONG) -> Position:
    return Position(symbol=symbol, side=side, amount=1.0, entry_price=100.0,
                    stop_loss=90.0, take_profit=None, opened_at=NOW)


def _board(tmp_path, stale_after_minutes=30.0) -> PortfolioBoard:
    return PortfolioBoard(str(tmp_path / "portfolio.json"), stale_after_minutes)


def test_an_empty_board_lets_anyone_open(tmp_path):
    allowed, held = _board(tmp_path).can_open("trend-btc", 5, NOW)
    assert allowed and held == 0


def test_positions_from_all_instances_are_counted(tmp_path):
    board = _board(tmp_path)
    board.publish("trend-btc", [_position("BTC/USDT")], NOW)
    board.publish("trend-eth", [_position("ETH/USDT")], NOW)
    board.publish("trend-sol", [_position("SOL/USDT")], NOW)

    allowed, held = board.can_open("trend-doge", 5, NOW)
    assert allowed and held == 3
    assert board.open_elsewhere("trend-btc", NOW) == 2


def test_the_cap_blocks_a_new_position(tmp_path):
    board = _board(tmp_path)
    for i in range(5):
        board.publish(f"bot-{i}", [_position(f"SYM{i}/USDT")], NOW)

    allowed, held = board.can_open("bot-new", 5, NOW)
    assert not allowed and held == 5
    # And an instance already holding one is not exempt.
    assert board.can_open("bot-0", 5, NOW)[0] is False


def test_a_cap_of_zero_disables_the_check(tmp_path):
    board = _board(tmp_path)
    for i in range(50):
        board.publish(f"bot-{i}", [_position(f"SYM{i}/USDT")], NOW)
    assert board.can_open("bot-new", 0, NOW) == (True, 0)


def test_a_dead_instance_stops_holding_a_slot(tmp_path):
    # Without this a bot that is killed while holding a position would
    # occupy a slot forever and slowly starve the others.
    board = _board(tmp_path, stale_after_minutes=30.0)
    board.publish("crashed-bot", [_position("BTC/USDT")], NOW - pd.Timedelta(hours=3))
    board.publish("live-bot", [_position("ETH/USDT")], NOW)

    slots = board.slots(NOW)
    assert [s.bot_id for s in slots] == ["live-bot"]
    assert board.can_open("other", 2, NOW) == (True, 1)


def test_republishing_refreshes_the_claim(tmp_path):
    board = _board(tmp_path, stale_after_minutes=30.0)
    board.publish("slow-bot", [_position("BTC/USDT")], NOW - pd.Timedelta(hours=3))
    assert board.slots(NOW) == []
    board.publish("slow-bot", [_position("BTC/USDT")], NOW)
    assert len(board.slots(NOW)) == 1


def test_closing_a_position_frees_the_slot(tmp_path):
    board = _board(tmp_path)
    board.publish("trend-btc", [_position("BTC/USDT")], NOW)
    assert board.can_open("other", 1, NOW)[0] is False
    board.publish("trend-btc", [], NOW)  # flat now
    assert board.can_open("other", 1, NOW) == (True, 0)


def test_separate_board_objects_share_the_file(tmp_path):
    # The real case: separate processes, each with their own board object.
    path = str(tmp_path / "portfolio.json")
    PortfolioBoard(path).publish("trend-btc", [_position("BTC/USDT")], NOW)
    PortfolioBoard(path).publish("trend-eth", [_position("ETH/USDT")], NOW)
    assert len(PortfolioBoard(path).slots(NOW)) == 2


def test_publishing_does_not_discard_other_instances(tmp_path):
    board = _board(tmp_path)
    board.publish("a", [_position("BTC/USDT")], NOW)
    board.publish("b", [_position("ETH/USDT")], NOW)
    board.publish("a", [_position("BTC/USDT"), _position("LTC/USDT")], NOW)
    assert sorted(s.symbol for s in board.slots(NOW)) == ["BTC/USDT", "ETH/USDT", "LTC/USDT"]


def test_a_corrupt_board_does_not_stop_trading(tmp_path):
    # A broken shared file must not take every bot down with it.
    path = tmp_path / "portfolio.json"
    path.write_text("{ not json")
    board = PortfolioBoard(str(path))
    assert board.can_open("trend-btc", 5, NOW) == (True, 0)
    board.publish("trend-btc", [_position("BTC/USDT")], NOW)
    assert len(board.slots(NOW)) == 1  # and it repairs itself on the next write


def test_no_temp_files_are_left_behind(tmp_path):
    board = _board(tmp_path)
    for i in range(3):
        board.publish(f"bot-{i}", [_position("BTC/USDT")], NOW)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["portfolio.json", "portfolio.json.lock"]
