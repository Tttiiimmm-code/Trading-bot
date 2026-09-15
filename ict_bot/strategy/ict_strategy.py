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
from ict_bot.ict import htf
from ict_bot.ict import killzones
from ict_bot.ict import liquidity as liq_mod
from ict_bot.ict import order_blocks as ob_mod
from ict_bot.ict import premium_discount as pd_mod
from ict_bot.ict import session_levels
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
    # Higher-timeframe bias: e.g. "4h" only allows longs while the 4h
    # structure is bullish. None disables the filter entirely.
    htf_bias_timeframe: str | None = None
    htf_bias_allow_unknown: bool = True
    htf_swing_left: int = 2
    htf_swing_right: int = 2
    # Treat previous day/week highs and lows as liquidity too, not just
    # equal-highs/lows clusters - in ICT these are the primary pools.
    use_session_liquidity: bool = False
    session_liquidity_rules: tuple[str, ...] = ("1D", "1W")
    # Where inside the entry zone the limit order rests:
    #   "ob_midpoint"   - middle of the order block (ICT's default reading)
    #   "fvg_ce"        - consequent encroachment, i.e. 50% of the fair value
    #                     gap, falling back to the order block without one
    #   "zone_midpoint" - middle of the combined OB/FVG overlap
    entry_mode: str = "ob_midpoint"


def _combine_entry_zone(ob: ob_mod.OrderBlock, fvg: fvg_mod.FairValueGap | None) -> tuple[float, float]:
    """Intersect the order block's range with the FVG's range (if any) to
    tighten the entry zone to where both agree - a plain interval
    intersection, which doesn't depend on trade side. Falls back to the
    order block alone if the two don't overlap.
    """
    if fvg is None:
        return ob.top, ob.bottom
    top = min(ob.top, fvg.top)
    bottom = max(ob.bottom, fvg.bottom)
    if top < bottom:
        return ob.top, ob.bottom  # non-overlapping OB/FVG, fall back to OB
    return top, bottom


def _entry_price(mode: str, ob: ob_mod.OrderBlock, fvg: fvg_mod.FairValueGap | None,
                 zone_top: float, zone_bottom: float) -> float:
    if mode == "fvg_ce" and fvg is not None:
        return fvg.midpoint
    if mode == "zone_midpoint":
        return (zone_top + zone_bottom) / 2.0
    return ob.midpoint


class ICTStrategy:
    """Stateless-per-call strategy: pass a trailing OHLCV window (oldest to
    newest, most recent bar = the just-confirmed candle) and get back a
    :class:`Signal` if the full confluence lines up on that bar, else
    ``None``. Recomputing detectors on the whole window each call keeps the
    exact same code path correct for both backtesting and live trading.
    """

    def __init__(self, config: ICTStrategyConfig | None = None):
        self.config = config or ICTStrategyConfig()

    def generate_signal(self, df: pd.DataFrame, trace: list[str] | None = None) -> Signal | None:
        """``trace``, if given, gets one human-readable note appended
        explaining exactly which confluence stage blocked a signal (or
        nothing appended if a signal was produced) - purely for
        observability (e.g. an hourly "why no trade yet" status log), it
        never affects the returned :class:`Signal`.
        """

        def note(msg: str) -> None:
            if trace is not None:
                trace.append(msg)

        cfg = self.config
        min_len = max(20, cfg.swing_left + cfg.swing_right + 5)
        if len(df) < min_len:
            note(f"not enough bar history ({len(df)} < {min_len} required)")
            return None

        last_ts = df.index[-1]
        zones = cfg.kill_zones if cfg.kill_zones is not None else killzones.DEFAULT_KILL_ZONES
        if cfg.require_kill_zone and not killzones.is_in_kill_zone(last_ts, zones):
            note("outside the configured kill zone")
            return None

        events = structure.detect_structure(df, left=cfg.swing_left, right=cfg.swing_right)
        if not events:
            note("no market structure (BOS/CHoCH) detected yet")
            return None
        last_event = events[-1]
        if last_event.index != last_ts:
            note("no structure event confirmed on the latest bar")
            return None  # only trade fresh reversals confirmed on this exact bar
        if last_event.event != structure.EventType.CHOCH:
            note("latest structure event is a BOS (trend continuation), not a fresh CHoCH reversal")
            return None

        side = Side.LONG if last_event.direction == structure.Trend.BULLISH else Side.SHORT
        direction_str = "bullish" if side == Side.LONG else "bearish"

        if cfg.htf_bias_timeframe is not None:
            bias = htf.htf_bias(df, cfg.htf_bias_timeframe, left=cfg.htf_swing_left, right=cfg.htf_swing_right)
            if not htf.bias_allows(bias, last_event.direction, allow_unknown=cfg.htf_bias_allow_unknown):
                note(f"{cfg.htf_bias_timeframe} bias is {bias.value}, opposing this {direction_str} setup")
                return None

        pools = liq_mod.find_liquidity_pools(
            df,
            tolerance_pct=cfg.liquidity_tolerance_pct,
            min_touches=cfg.liquidity_min_touches,
            left=cfg.swing_left,
            right=cfg.swing_right,
        )
        if cfg.use_session_liquidity:
            pools = pools + session_levels.reference_pools(df, rules=cfg.session_liquidity_rules)
        sweep_kind = "sell_side" if side == Side.LONG else "buy_side"
        sweep = liq_mod.recent_sweep(pools, as_of=last_ts, within_bars=cfg.sweep_lookback_bars, df=df)
        if sweep is None or sweep.kind != sweep_kind:
            note(f"no matching {sweep_kind} liquidity sweep within the last {cfg.sweep_lookback_bars} bars")
            return None

        obs = ob_mod.detect_order_blocks(df, events)
        candidate_obs = [o for o in obs if o.direction == direction_str and o.broken_at == last_event.index]
        if not candidate_obs:
            note("no order block found for this CHoCH's displacement leg")
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

        fvg = candidate_fvgs[-1] if candidate_fvgs else None
        entry_zone_top, entry_zone_bottom = _combine_entry_zone(ob, fvg)

        if cfg.require_ote and not (ote.bottom <= entry_zone_top and ote.top >= entry_zone_bottom):
            note("order block/FVG entry zone does not overlap the OTE (61.8%-79%) retracement")
            return None

        # A take-profit must be a genuine resting liquidity target - never
        # synthesize one from min_risk_reward, or that floor would always
        # trivially pass its own check on the fabricated target.
        entry = _entry_price(cfg.entry_mode, ob, fvg, entry_zone_top, entry_zone_bottom)
        if side == Side.LONG:
            stop_loss = min(ob.bottom, sweep.price) * 0.999
            candidates = [p for p in pools if p.kind == "buy_side" and p.price > entry and not p.swept]
            target_pool = min(candidates, key=lambda p: p.price, default=None)
        else:
            stop_loss = max(ob.top, sweep.price) * 1.001
            candidates = [p for p in pools if p.kind == "sell_side" and p.price < entry and not p.swept]
            target_pool = min(candidates, key=lambda p: entry - p.price, default=None)

        # The entry can sit outside the order block (an FVG's consequent
        # encroachment is a level of its own), so the stop is not
        # automatically on the right side of it.
        if (side == Side.LONG and entry <= stop_loss) or (side == Side.SHORT and entry >= stop_loss):
            note(f"entry {entry:.4f} is on the wrong side of the stop {stop_loss:.4f}")
            return None

        if target_pool is None:
            note("no resting opposite liquidity pool available as a take-profit target")
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
            note(f"risk/reward {signal.risk_reward:.2f} below configured minimum {cfg.min_risk_reward:.2f}")
            return None
        return signal
