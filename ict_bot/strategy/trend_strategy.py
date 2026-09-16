"""Donchian breakout trend following.

Why this exists next to the ICT model: measured over two years and eight
crypto markets, the ICT setup's edge before costs was indistinguishable
from zero, and after realistic fees it lost significantly. The cause was
structural - its stop sits ~0.3% from entry, so sizing to 1% account risk
implies ~3x notional, and fees are charged on notional while the edge is
earned on the stop distance. See the README.

Trend following attacks exactly that constraint from the other side:

* the stop is a multiple of ATR, so it is percent-wide rather than
  basis-point-wide, and the same fee is a far smaller share of the risk;
* trades are rare and held for days, so there are few round trips to pay;
* the exit trails instead of targeting a fixed level, because this style
  earns its living from a small number of very large winners and capping
  them removes the edge.

None of that makes it profitable - it makes it *testable* under a cost
structure that does not doom it in advance.

The interface matches :class:`~ict_bot.strategy.ict_strategy.ICTStrategy`
deliberately, so the same backtest engine, brokers, risk manager and live
loop drive it unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from ict_bot.indicators import atr, donchian
from ict_bot.strategy.ict_strategy import Side, Signal


@dataclass
class TrendStrategyConfig:
    entry_period: int = 20  # breakout channel length
    exit_period: int = 10  # opposite channel that defines the trailing distance
    atr_period: int = 14
    atr_stop_multiple: float = 2.0  # initial stop distance, in ATRs
    trail_atr_multiple: float = 3.0  # how far the stop follows behind, in ATRs
    # Only trade in the direction of a longer-term filter (0 disables it).
    # Classic trend following does badly when it fights the primary trend.
    regime_period: int = 100
    allow_long: bool = True
    allow_short: bool = True
    min_atr_pct: float = 0.0  # skip dead-quiet markets, ATR as % of price


class TrendStrategy:
    """Breakout entry, ATR stop, trailing exit.

    Pass a trailing OHLCV window (oldest to newest, last bar = the just
    closed candle). Returns a :class:`Signal` when that bar closes beyond
    the breakout channel, else ``None``.
    """

    def __init__(self, config: TrendStrategyConfig | None = None):
        self.config = config or TrendStrategyConfig()

    def generate_signal(self, df: pd.DataFrame, trace: list[str] | None = None,
                        correlated: pd.DataFrame | None = None) -> Signal | None:
        def note(msg: str) -> None:
            if trace is not None:
                trace.append(msg)

        cfg = self.config
        needed = max(cfg.entry_period, cfg.exit_period, cfg.atr_period, cfg.regime_period) + 2
        if len(df) < needed:
            note(f"not enough bar history ({len(df)} < {needed} required)")
            return None

        upper, lower = donchian(df, cfg.entry_period)
        atr_values = atr(df, cfg.atr_period)
        close = df["close"].to_numpy(dtype=float)
        i = len(df) - 1

        channel_high, channel_low, current_atr, price = upper[i], lower[i], float(atr_values[i]), float(close[i])
        if math.isnan(channel_high) or math.isnan(channel_low) or current_atr <= 0:
            note("breakout channel or ATR not established yet")
            return None

        if cfg.min_atr_pct and (current_atr / price) * 100 < cfg.min_atr_pct:
            note(f"volatility too low (ATR {current_atr / price * 100:.2f}% < {cfg.min_atr_pct:.2f}%)")
            return None

        broke_up = price > channel_high
        broke_down = price < channel_low
        if not broke_up and not broke_down:
            note(f"no breakout: close {price:.4f} inside the {cfg.entry_period}-bar channel "
                 f"[{channel_low:.4f}, {channel_high:.4f}]")
            return None

        side = Side.LONG if broke_up else Side.SHORT
        if side == Side.LONG and not cfg.allow_long:
            note("long breakouts disabled")
            return None
        if side == Side.SHORT and not cfg.allow_short:
            note("short breakouts disabled")
            return None

        if cfg.regime_period:
            regime = float(pd.Series(close).rolling(cfg.regime_period).mean().to_numpy()[i])
            if not math.isnan(regime):
                if side == Side.LONG and price < regime:
                    note(f"long breakout against the {cfg.regime_period}-bar regime filter")
                    return None
                if side == Side.SHORT and price > regime:
                    note(f"short breakout against the {cfg.regime_period}-bar regime filter")
                    return None

        stop_distance = cfg.atr_stop_multiple * current_atr
        trail_distance = cfg.trail_atr_multiple * current_atr
        stop_loss = price - stop_distance if side == Side.LONG else price + stop_distance

        return Signal(
            index=df.index[-1],
            side=side,
            entry=price,
            stop_loss=stop_loss,
            take_profit=None,  # open ended: the trail decides when it's over
            reason=(f"{cfg.entry_period}-bar {'high' if side == Side.LONG else 'low'} breakout, "
                    f"{cfg.atr_stop_multiple:g}xATR stop, {cfg.trail_atr_multiple:g}xATR trail"),
            trail_distance=trail_distance,
        )
