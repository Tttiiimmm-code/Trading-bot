"""The core ICT playbook, wired together from the individual detectors.

Model implemented (a simplified version of ICT's "2022 model"):

1. Only look for trades inside a kill zone (session liquidity is when ICT
   expects the "real" directional move).
2. Require a liquidity sweep: sell-side liquidity taken (for longs) or
   buy-side liquidity taken (for shorts) - the stop-hunt that fuels the
   reversal.
3. Require a Change of Character (CHoCH) confirming the reversal right
   after the sweep - continuation breaks (BOS) are not traded as fresh
   entries in this model, only used to track the prevailing trend.
4. Locate the order block / fair value gap left behind by the
   displacement leg that caused the CHoCH - that is the entry zone.
5. Require the entry zone to overlap the Optimal Trade Entry (61.8%-79%)
   retracement of that displacement leg.
6. Stop loss beyond the swept liquidity; take profit at the next opposing
   liquidity pool, with a minimum risk/reward floor.

Every step is optional/tunable via :class:`ICTStrategyConfig` so the model
can be relaxed (e.g. drop the kill-zone or OTE requirement) or tightened.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from ict_bot.ict import fvg as fvg_mod
from ict_bot.ict import killzones
from ict_bot.ict import liquidity as liq_mod
from ict_bot.ict import order_blocks as ob_mod
from ict_bot.ict import premium_discount as pd_mod
from ict_bot.ict import structure


class Side(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Signal:
    index: pd.Timestamp
    side: Side
    entry: float
    stop_loss: float
    take_profit: float
    reason: str

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.take_profit - self.entry)
        return reward / risk if risk else 0.0


@dataclass
class ICTStrategyConfig:
    swing_left: int = 2
    swing_right: int = 2
    liquidity_tolerance_pct: float = 0.05
    liquidity_min_touches: int = 2
    sweep_lookback_bars: int = 10
    min_risk_reward: float = 2.0
    require_kill_zone: bool = True
    require_ote: bool = True
    ote_low_ratio: float = 0.618
    ote_high_ratio: float = 0.79
    kill_zones: list[killzones.KillZone] | None = None


class ICTStrategy:
    """Stateless-per-call strategy: pass a trailing OHLCV window (oldest to
    newest, most recent bar = the just-confirmed candle) and get back a
    :class:`Signal` if the full confluence lines up on that bar, else
    ``None``. Recomputing detectors on the whole window each call keeps the
    exact same code path correct for both backtesting and live trading.
    """

    def __init__(self, config: ICTStrategyConfig | None = None):
        self.config = config or ICTStrategyConfig()

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        cfg = self.config
        min_len = max(20, cfg.swing_left + cfg.swing_right + 5)
        if len(df) < min_len:
            return None

        last_ts = df.index[-1]
        zones = cfg.kill_zones if cfg.kill_zones is not None else killzones.DEFAULT_KILL_ZONES
        if cfg.require_kill_zone and not killzones.is_in_kill_zone(last_ts, zones):
            return None

        events = structure.detect_structure(df, left=cfg.swing_left, right=cfg.swing_right)
        if not events:
            return None
        last_event = events[-1]
        if last_event.index != last_ts or last_event.event != structure.EventType.CHOCH:
            return None  # only trade fresh reversals confirmed on this exact bar

        side = Side.LONG if last_event.direction == structure.Trend.BULLISH else Side.SHORT
        direction_str = "bullish" if side == Side.LONG else "bearish"

        pools = liq_mod.find_liquidity_pools(
            df,
            tolerance_pct=cfg.liquidity_tolerance_pct,
            min_touches=cfg.liquidity_min_touches,
            left=cfg.swing_left,
            right=cfg.swing_right,
        )
        sweep_kind = "sell_side" if side == Side.LONG else "buy_side"
        sweep = liq_mod.recent_sweep(pools, as_of=last_ts, within_bars=cfg.sweep_lookback_bars, df=df)
        if sweep is None or sweep.kind != sweep_kind:
            return None

        obs = ob_mod.detect_order_blocks(df, events)
        candidate_obs = [o for o in obs if o.direction == direction_str and o.broken_at == last_event.index]
        if not candidate_obs:
            return None
        ob = candidate_obs[-1]

        gaps = fvg_mod.detect_fvgs(df)
        candidate_fvgs = [
            g for g in gaps if g.direction == direction_str and ob.index <= g.index <= last_event.index
        ]

        if side == Side.LONG:
            rng = pd_mod.DealingRange(low=sweep.price, high=last_event.price)
        else:
            rng = pd_mod.DealingRange(low=last_event.price, high=sweep.price)
        ote = pd_mod.optimal_trade_entry(rng, direction_str, cfg.ote_low_ratio, cfg.ote_high_ratio)

        entry_zone_top, entry_zone_bottom = ob.top, ob.bottom
        if candidate_fvgs:
            fvg = candidate_fvgs[-1]
            entry_zone_top = min(entry_zone_top, fvg.top) if side == Side.LONG else max(entry_zone_top, fvg.top)
            entry_zone_bottom = max(entry_zone_bottom, fvg.bottom) if side == Side.LONG else min(entry_zone_bottom, fvg.bottom)
            if entry_zone_top < entry_zone_bottom:
                entry_zone_top, entry_zone_bottom = ob.top, ob.bottom  # non-overlapping OB/FVG, fall back to OB

        if cfg.require_ote and not (ote.bottom <= entry_zone_top and ote.top >= entry_zone_bottom):
            return None

        # A take-profit must be a genuine resting liquidity target - never
        # synthesize one from min_risk_reward, or that floor would always
        # trivially pass its own check on the fabricated target.
        entry = ob.midpoint
        if side == Side.LONG:
            stop_loss = min(ob.bottom, sweep.price) * 0.999
            candidates = [p for p in pools if p.kind == "buy_side" and p.price > entry and not p.swept]
            target_pool = min(candidates, key=lambda p: p.price, default=None)
        else:
            stop_loss = max(ob.top, sweep.price) * 1.001
            candidates = [p for p in pools if p.kind == "sell_side" and p.price < entry and not p.swept]
            target_pool = min(candidates, key=lambda p: entry - p.price, default=None)

        if target_pool is None:
            return None
        take_profit = target_pool.price

        signal = Signal(
            index=last_ts,
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=f"{sweep.kind} liquidity sweep -> CHoCH -> {direction_str} order block entry",
        )
        if signal.risk_reward < cfg.min_risk_reward:
            return None
        return signal
