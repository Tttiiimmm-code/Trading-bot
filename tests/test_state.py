"""State survives a restart.

The bug these cover: systemd restarts the bot (Restart=always), the new
process starts with an empty position dict and a fresh simulated balance,
and so believes it is flat while a real position is still open.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from ict_bot.execution.paper import PaperBroker
from ict_bot.execution.state import restore_state, save_state
from ict_bot.strategy.ict_strategy import Side
from ict_bot.strategy.risk import RiskConfig, RiskManager

TS = pd.Timestamp("2026-01-01 12:00", tz="UTC")


def _broker_with_open_position() -> tuple[PaperBroker, RiskManager]:
    broker = PaperBroker(10_000.0, maker_fee_pct=0.02, taker_fee_pct=0.05)
    risk = RiskManager(RiskConfig(risk_per_trade_pct=0.5))
    broker.open_position("BTC/USDT", Side.LONG, 0.1, 100_000.0, 96_000.0, None, TS, trail_distance=6_000.0)
    risk.register_open()
    return broker, risk


def test_an_open_position_survives_a_restart(tmp_path):
    broker, risk = _broker_with_open_position()
    # The trail moves the stop; that trailed level is what must come back.
    broker.check_stop_and_target("BTC/USDT", pd.Series({"open": 100_000.0, "high": 110_000.0,
                                                        "low": 99_000.0, "close": 109_000.0}), TS)
    trailed_stop = broker.get_open_position("BTC/USDT").stop_loss
    assert trailed_stop > 96_000.0
    path = str(tmp_path / "state.json")
    save_state(path, broker, risk, "BTC/USDT", "paper")

    fresh_broker = PaperBroker(10_000.0)
    fresh_risk = RiskManager(RiskConfig(risk_per_trade_pct=0.5))
    assert restore_state(path, fresh_broker, fresh_risk, "BTC/USDT", "paper") is True

    position = fresh_broker.get_open_position("BTC/USDT")
    assert position is not None
    assert position.side == Side.LONG
    assert position.stop_loss == pytest.approx(trailed_stop)
    assert position.trail_distance == pytest.approx(6_000.0)
    assert position.entry_price == pytest.approx(100_000.0)
    # And the restored risk manager still counts it, so the bot cannot open
    # a second position on top of the one it already has.
    assert fresh_risk.open_positions == 1
    assert fresh_risk.can_open_trade(TS, 10_000.0) is False


def test_the_simulated_balance_is_not_reset_by_a_restart(tmp_path):
    broker, risk = _broker_with_open_position()
    broker.close_position("BTC/USDT", 110_000.0, TS, reason="take_profit")
    risk.register_close()
    balance = broker.balance
    assert balance != 10_000.0
    path = str(tmp_path / "state.json")
    save_state(path, broker, risk, "BTC/USDT", "paper")

    fresh = PaperBroker(10_000.0)
    restore_state(path, fresh, RiskManager(RiskConfig()), "BTC/USDT", "paper")
    assert fresh.balance == pytest.approx(balance)
    assert fresh.fees_paid == pytest.approx(broker.fees_paid)
    assert len(fresh.fills) == 2  # entry + exit, so trades can still be paired


def test_state_from_another_market_is_refused(tmp_path):
    broker, risk = _broker_with_open_position()
    path = str(tmp_path / "state.json")
    save_state(path, broker, risk, "BTC/USDT", "paper")

    fresh = PaperBroker(10_000.0)
    assert restore_state(path, fresh, RiskManager(RiskConfig()), "ETH/USDT", "paper") is False
    assert fresh.get_open_position("BTC/USDT") is None
    assert fresh.balance == 10_000.0


def test_paper_state_is_never_applied_to_a_live_run(tmp_path):
    broker, risk = _broker_with_open_position()
    path = str(tmp_path / "state.json")
    save_state(path, broker, risk, "BTC/USDT", "paper")
    fresh = PaperBroker(10_000.0)
    assert restore_state(path, fresh, RiskManager(RiskConfig()), "BTC/USDT", "live") is False


def test_a_corrupt_state_file_starts_flat_instead_of_crashing(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ this is not json")
    fresh = PaperBroker(10_000.0)
    assert restore_state(str(path), fresh, RiskManager(RiskConfig()), "BTC/USDT", "paper") is False
    assert fresh.balance == 10_000.0


def test_a_missing_state_file_is_not_an_error(tmp_path):
    fresh = PaperBroker(10_000.0)
    assert restore_state(str(tmp_path / "nope.json"), fresh, RiskManager(RiskConfig()), "BTC/USDT", "paper") is False


def test_the_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    broker, risk = _broker_with_open_position()
    path = str(tmp_path / "state.json")
    save_state(path, broker, risk, "BTC/USDT", "paper")
    save_state(path, broker, risk, "BTC/USDT", "paper")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]
    json.loads((tmp_path / "state.json").read_text())  # parses
