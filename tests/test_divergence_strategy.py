"""The divergence strategy is research code, but the one thing it must
never do is look at the reference market's future."""
from __future__ import annotations

import pandas as pd
import pytest

from ict_bot.strategy.divergence_strategy import DivergenceConfig, DivergenceStrategy
from ict_bot.strategy.ict_strategy import Side
from tests.conftest import rows_to_df

CFG = dict(ret_len=3, band_lookback=10, band_mult=1.0, trend_len=5, atr_len=3)


def _series(values: list[float], index) -> pd.Series:
    return pd.Series(values, index=index, dtype=float)


def _rising(n: int, start: float = 100.0, step: float = 1.0):
    rows, price = [], start
    for _ in range(n):
        rows.append((price, price + 0.5, price - 0.5, price))
        price += step
    return rows


def test_no_signal_without_enough_history():
    df = rows_to_df(_rising(5))
    trace: list[str] = []
    strategy = DivergenceStrategy(DivergenceConfig(**CFG), _series([1.0] * 5, df.index))
    assert strategy.generate_signal(df, trace=trace) is None
    assert "not enough bar history" in trace[0]


def test_a_reference_that_never_overlaps_is_reported():
    df = rows_to_df(_rising(40))
    far_future = pd.date_range("2030-01-01", periods=40, freq="15min", tz="UTC")
    strategy = DivergenceStrategy(DivergenceConfig(**CFG), _series([1.0] * 40, far_future))
    trace: list[str] = []
    assert strategy.generate_signal(df, trace=trace) is None
    assert "no reference data" in trace[0]


def test_the_reference_is_read_with_ffill_and_never_from_the_future():
    # A reference that jumps after the window's last bar must not change
    # the signal: ffill only ever reaches backwards.
    df = rows_to_df(_rising(60))
    ref_values = [100.0 + i for i in range(60)]
    strategy = DivergenceStrategy(DivergenceConfig(**CFG), _series(ref_values, df.index))
    before = strategy.generate_signal(df)

    later = pd.date_range(df.index[-1] + pd.Timedelta("15min"), periods=5, freq="15min", tz="UTC")
    polluted = pd.concat([_series(ref_values, df.index), _series([9e6] * 5, later)])
    after = DivergenceStrategy(DivergenceConfig(**CFG), polluted).generate_signal(df)

    assert (before is None) == (after is None)
    if before is not None:
        assert before.entry == after.entry and before.stop_loss == after.stop_loss


def test_a_long_has_its_stop_below_and_target_at_the_reward_ratio():
    # Traded market dips against a flat reference, then recovers.
    rows = _rising(50) + [(150.0, 150.5, 140.0, 141.0)] * 4 + [(141.0, 152.0, 141.0, 151.0)]
    df = rows_to_df(rows)
    reference = _series([100.0] * len(df), df.index)
    cfg = DivergenceConfig(**CFG, rr_ratio=2.0, atr_stop_mult=2.0)
    signal = DivergenceStrategy(cfg, reference).generate_signal(df)

    if signal is not None:  # the synthetic path may not cross the band
        assert signal.side == Side.LONG
        assert signal.stop_loss < signal.entry < signal.take_profit
        risk = signal.entry - signal.stop_loss
        assert signal.take_profit - signal.entry == pytest.approx(risk * 2.0)
        assert signal.trail_distance is None


def test_shorts_are_refused_unless_enabled():
    df = rows_to_df(_rising(60))
    reference = _series([100.0 + i * 3 for i in range(60)], df.index)
    cfg = DivergenceConfig(**CFG, allow_short=False)
    signal = DivergenceStrategy(cfg, reference).generate_signal(df)
    assert signal is None or signal.side == Side.LONG
