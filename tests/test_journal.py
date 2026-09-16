"""The trade journal is what makes a paper run measurable rather than
anecdotal, so it has to be append-only and restart-safe."""
from __future__ import annotations

import csv

import pandas as pd

from ict_bot.backtest.metrics import pair_trades
from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.ict_strategy import Side
from ict_bot.utils.journal import TradeJournal

TS = pd.Timestamp("2026-01-01 12:00", tz="UTC")


def _closed_trade_broker() -> PaperBroker:
    broker = PaperBroker(10_000.0, maker_fee_pct=0.02, taker_fee_pct=0.05)
    broker.open_position("BTC/USDT", Side.LONG, 0.1, 100_000.0, 96_000.0, None, TS)
    broker.close_position("BTC/USDT", 110_000.0, TS + pd.Timedelta(hours=8), reason="take_profit")
    return broker


def _rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def test_a_closed_trade_is_written_with_net_and_gross_pnl(tmp_path):
    broker = _closed_trade_broker()
    path = str(tmp_path / "trades.csv")
    added = TradeJournal(path).catch_up(pair_trades(broker.fills), lambda: broker.balance)

    assert added == 1
    row = _rows(path)[0]
    assert row["symbol"] == "BTC/USDT"
    assert row["side"] == "long"
    assert row["exit_reason"] == "take_profit"
    assert float(row["pnl"]) < float(row["gross_pnl"])  # net is after fees
    assert float(row["fees"]) > 0
    assert float(row["balance_after"]) == broker.balance


def test_already_written_trades_are_not_duplicated(tmp_path):
    broker = _closed_trade_broker()
    path = str(tmp_path / "trades.csv")
    journal = TradeJournal(path)
    trades = pair_trades(broker.fills)
    journal.catch_up(trades, lambda: broker.balance)
    journal.catch_up(trades, lambda: broker.balance)
    journal.catch_up(trades, lambda: broker.balance)
    assert len(_rows(path)) == 1


def test_a_restart_does_not_rewrite_history(tmp_path):
    broker = _closed_trade_broker()
    path = str(tmp_path / "trades.csv")
    TradeJournal(path).catch_up(pair_trades(broker.fills), lambda: broker.balance)

    # New process, state restored: the old fills are back in the broker, but
    # they were journalled by the process that made them.
    resumed = TradeJournal(path)
    resumed.skip(len(pair_trades(broker.fills)))
    resumed.catch_up(pair_trades(broker.fills), lambda: broker.balance)
    assert len(_rows(path)) == 1

    broker.open_position("BTC/USDT", Side.LONG, 0.1, 111_000.0, 108_000.0, None, TS)
    broker.close_position("BTC/USDT", 105_000.0, TS + pd.Timedelta(hours=12), reason="stop_loss")
    resumed.catch_up(pair_trades(broker.fills), lambda: broker.balance)

    rows = _rows(path)
    assert len(rows) == 2
    assert rows[1]["exit_reason"] == "stop_loss"


def test_the_balance_is_not_read_when_there_is_nothing_to_write(tmp_path):
    calls = []

    def balance():
        calls.append(1)
        return 10_000.0

    TradeJournal(str(tmp_path / "trades.csv")).catch_up([], balance)
    assert calls == []  # a network round trip on the live broker


def test_the_header_is_written_once(tmp_path):
    broker = _closed_trade_broker()
    path = str(tmp_path / "trades.csv")
    journal = TradeJournal(path)
    journal.catch_up(pair_trades(broker.fills), lambda: broker.balance)
    broker.open_position("BTC/USDT", Side.SHORT, 0.1, 110_000.0, 114_000.0, None, TS)
    broker.close_position("BTC/USDT", 100_000.0, TS + pd.Timedelta(hours=20), reason="stop_loss")
    journal.catch_up(pair_trades(broker.fills), lambda: broker.balance)

    with open(path) as f:
        assert f.read().count("symbol,side,opened_at") == 1
