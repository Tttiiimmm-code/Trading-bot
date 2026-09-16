"""Intermarket divergence, ported from a friend's Gold/Silver MT5 bot.

NOT WIRED INTO THE LIVE LOOP, on purpose. Measured on ten crypto pairs
over 2018-2026 with realistic costs, it does not earn its place next to
the trend strategy - see "Intermarket divergence" in the README for the
numbers. It lives here because the measurement is worth keeping and
re-running, not because it should be deployed.

His idea, unchanged: two markets that normally move together. Take the
difference of their N-bar returns (momentum difference, NOT a price
spread), and band it at mean - k*sigma of its own recent history. When the
traded market has fallen behind its partner (difference below the band)
and then catches up (difference crosses back above), that is a long.
Plus a trend filter so it never buys in a real downtrend.

His parameters are used as they stand - RET_LEN 20, BAND_LOOKBACK 100,
BAND_MULT 1.5, EMA 150, ATR stop 2.0, reward 2.0 - because the honest
first test of someone else's idea is their settings, not ones fitted to
my data.

Here ETH is the traded market and BTC the reference, per the user's ask.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ict_bot.indicators import atr as atr_of
from ict_bot.strategy.ict_strategy import Side, Signal


@dataclass
class DivergenceConfig:
    ret_len: int = 20          # bars for the return that forms the momentum
    band_lookback: int = 100   # bars of history the band is built from
    band_mult: float = 1.5     # how many sigma below the mean the band sits
    trend_len: int = 150       # EMA trend filter on the traded market
    atr_len: int = 14
    atr_stop_mult: float = 2.0
    rr_ratio: float = 2.0      # take profit = stop distance * this
    allow_short: bool = False  # his bot is long only; mirrored for testing


class DivergenceStrategy:
    """Needs a reference series as well as the traded market.

    The reference is held on the instance rather than passed per call
    because the backtest engine only hands the strategy the traded
    market's window. Values are read with ffill onto the window's
    timestamps, so a bar only ever sees the last reference price at or
    before it - never a later one.
    """

    def __init__(self, config: DivergenceConfig, reference: pd.Series):
        self.config = config
        self.reference = reference

    def _diff_and_band(self, df: pd.DataFrame):
        cfg = self.config
        ref = self.reference.reindex(df.index, method="ffill")
        if ref.isna().all():
            return None, None
        close = df["close"]
        ret_traded = close / close.shift(cfg.ret_len) - 1.0
        ret_ref = ref / ref.shift(cfg.ret_len) - 1.0
        d = ret_traded - ret_ref
        mean = d.rolling(cfg.band_lookback, min_periods=cfg.band_lookback).mean()
        std = d.rolling(cfg.band_lookback, min_periods=cfg.band_lookback).std()
        return d, (mean - cfg.band_mult * std, mean + cfg.band_mult * std)

    def generate_signal(self, df: pd.DataFrame, trace=None, correlated=None) -> Signal | None:
        def note(msg):
            if trace is not None:
                trace.append(msg)

        cfg = self.config
        needed = max(cfg.trend_len, cfg.ret_len + cfg.band_lookback) + 5
        if len(df) < needed:
            note(f"not enough bar history ({len(df)} < {needed})")
            return None

        d, bands = self._diff_and_band(df)
        if d is None:
            note("no reference data")
            return None
        lower, upper = bands
        if any(pd.isna(x.iloc[-1]) or pd.isna(x.iloc[-2]) for x in (d, lower, upper)):
            note("band not established yet")
            return None

        close = df["close"]
        ema = close.ewm(span=cfg.trend_len, adjust=False).mean()
        price = float(close.iloc[-1])
        atr_now = float(atr_of(df, cfg.atr_len)[-1])
        if not math.isfinite(atr_now) or atr_now <= 0:
            return None

        # Lagged behind, now catching up.
        caught_up = d.iloc[-2] <= lower.iloc[-2] and d.iloc[-1] > lower.iloc[-1]
        ran_ahead = cfg.allow_short and d.iloc[-2] >= upper.iloc[-2] and d.iloc[-1] < upper.iloc[-1]

        if caught_up and price > ema.iloc[-1]:
            side = Side.LONG
        elif ran_ahead and price < ema.iloc[-1]:
            side = Side.SHORT
        else:
            gap = float(d.iloc[-1] - lower.iloc[-1])
            note(f"no divergence signal: momentum difference {d.iloc[-1]:+.4f}, "
                 f"band {lower.iloc[-1]:+.4f} (gap {gap:+.4f})")
            return None

        stop_distance = cfg.atr_stop_mult * atr_now
        stop = price - stop_distance if side == Side.LONG else price + stop_distance
        target = price + cfg.rr_ratio * stop_distance if side == Side.LONG else price - cfg.rr_ratio * stop_distance
        return Signal(index=df.index[-1], side=side, entry=price, stop_loss=stop,
                      take_profit=target, reason=f"{'lagged' if side == Side.LONG else 'led'} then reverted",
                      trail_distance=None)
