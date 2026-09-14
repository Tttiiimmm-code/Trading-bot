import pandas as pd

from ict_bot.strategy.risk import RiskConfig, RiskManager


def test_position_size_matches_risk_amount():
    rm = RiskManager(RiskConfig(risk_per_trade_pct=1.0))
    size = rm.position_size(balance=10_000, entry=100, stop_loss=95)
    # risking 1% of 10,000 = 100, stop distance = 5 -> size = 20
    assert size == 20


def test_position_size_zero_for_zero_stop_distance():
    rm = RiskManager(RiskConfig())
    assert rm.position_size(balance=10_000, entry=100, stop_loss=100) == 0.0


def test_daily_loss_circuit_breaker_blocks_new_trades():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=3.0, max_open_positions=5))
    ts = pd.Timestamp("2024-01-01 10:00", tz="UTC")
    assert rm.can_open_trade(ts, balance=10_000)  # establishes day_start_balance = 10,000
    assert rm.can_open_trade(ts, balance=9_800)  # -2%, still fine
    assert not rm.can_open_trade(ts, balance=9_600)  # -4%, breaker trips


def test_daily_loss_resets_on_new_day():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=3.0))
    day1 = pd.Timestamp("2024-01-01 10:00", tz="UTC")
    day2 = pd.Timestamp("2024-01-02 10:00", tz="UTC")
    assert rm.can_open_trade(day1, balance=10_000)
    assert not rm.can_open_trade(day1, balance=9_500)
    assert rm.can_open_trade(day2, balance=9_500)  # new day, fresh baseline


def test_max_open_positions_enforced():
    rm = RiskManager(RiskConfig(max_open_positions=1))
    ts = pd.Timestamp("2024-01-01 10:00", tz="UTC")
    assert rm.can_open_trade(ts, balance=10_000)
    rm.register_open()
    assert not rm.can_open_trade(ts, balance=10_000)
    rm.register_close()
    assert rm.can_open_trade(ts, balance=10_000)
