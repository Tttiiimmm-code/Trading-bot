import pytest

from ict_bot.ict.premium_discount import DealingRange, Zone, optimal_trade_entry


def test_zone_of_classifies_premium_and_discount():
    rng = DealingRange(low=100, high=200)
    assert rng.midpoint == 150
    assert rng.zone_of(180) == Zone.PREMIUM
    assert rng.zone_of(120) == Zone.DISCOUNT
    assert rng.zone_of(150) == Zone.EQUILIBRIUM


def test_fib_level_up_and_down():
    rng = DealingRange(low=100, high=200)
    assert rng.fib_level(0.5, "up") == 150
    assert rng.fib_level(0.618, "up") == pytest.approx(200 - 100 * 0.618)
    assert rng.fib_level(0.5, "down") == 150


def test_optimal_trade_entry_bullish():
    rng = DealingRange(low=100, high=200)
    ote = optimal_trade_entry(rng, "bullish", low_ratio=0.618, high_ratio=0.79)
    assert ote.direction == "bullish"
    assert ote.bottom < ote.top
    assert ote.bottom == pytest.approx(200 - 100 * 0.79)
    assert ote.top == pytest.approx(200 - 100 * 0.618)
    assert ote.contains((ote.top + ote.bottom) / 2)


def test_optimal_trade_entry_bearish_mirrors_bullish():
    rng = DealingRange(low=100, high=200)
    ote = optimal_trade_entry(rng, "bearish", low_ratio=0.618, high_ratio=0.79)
    assert ote.bottom == pytest.approx(100 + 100 * 0.618)
    assert ote.top == pytest.approx(100 + 100 * 0.79)
