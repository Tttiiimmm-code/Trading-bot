"""ICT kill zones: recurring intraday session windows (UTC) where volatility
and directional moves are statistically concentrated. Trading only inside
these windows filters out low-quality chop.

Default windows follow the commonly used ICT definitions:
- Asian:        00:00 - 04:00 UTC
- London:       07:00 - 10:00 UTC
- NY AM:        12:00 - 15:00 UTC (overlaps London close / NY open)
- London Close: 15:00 - 16:30 UTC
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class KillZone:
    name: str
    start_hour_utc: float
    end_hour_utc: float

    def contains(self, ts: pd.Timestamp) -> bool:
        ts_utc = ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
        hour = ts_utc.hour + ts_utc.minute / 60.0
        if self.start_hour_utc <= self.end_hour_utc:
            return self.start_hour_utc <= hour < self.end_hour_utc
        # window wraps midnight
        return hour >= self.start_hour_utc or hour < self.end_hour_utc


DEFAULT_KILL_ZONES: list[KillZone] = [
    KillZone("asian", 0.0, 4.0),
    KillZone("london", 7.0, 10.0),
    KillZone("ny_am", 12.0, 15.0),
    KillZone("london_close", 15.0, 16.5),
]

# ICT's "Silver Bullet": a deliberately narrow one-hour window per session
# where he expects the algorithm to deliver an FVG entry. Much stricter
# than the broad kill zones above - far fewer opportunities, in theory
# higher quality.
SILVER_BULLET_ZONES: list[KillZone] = [
    KillZone("london_sb", 8.0, 9.0),
    KillZone("ny_am_sb", 14.0, 15.0),
    KillZone("ny_pm_sb", 18.0, 19.0),
]

# The two sessions ICT treats as the primary drivers, without the Asian
# range (which he mostly uses for context/accumulation rather than entries).
LONDON_NY_ZONES: list[KillZone] = [
    KillZone("london", 7.0, 10.0),
    KillZone("ny_am", 12.0, 15.0),
]

KILL_ZONE_PRESETS: dict[str, list[KillZone]] = {
    "default": DEFAULT_KILL_ZONES,
    "silver_bullet": SILVER_BULLET_ZONES,
    "london_ny": LONDON_NY_ZONES,
}


def active_kill_zones(ts: pd.Timestamp, zones: list[KillZone] = DEFAULT_KILL_ZONES) -> list[str]:
    return [z.name for z in zones if z.contains(ts)]


def is_in_kill_zone(ts: pd.Timestamp, zones: list[KillZone] = DEFAULT_KILL_ZONES) -> bool:
    return any(z.contains(ts) for z in zones)


def tag_kill_zones(df: pd.DataFrame, zones: list[KillZone] = DEFAULT_KILL_ZONES) -> pd.Series:
    """Return a boolean Series aligned to ``df.index`` marking bars whose
    timestamp falls inside any configured kill zone.
    """
    return pd.Series([is_in_kill_zone(ts, zones) for ts in df.index], index=df.index, name="in_kill_zone")
