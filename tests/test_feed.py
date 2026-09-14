"""Tests for the OHLCV fetch helpers against a fake exchange double - no
network needed.
"""
from __future__ import annotations

from ict_bot.data.feed import fetch_ohlcv, fetch_ohlcv_closed


class FakeExchange:
    """Mimics a real exchange's fetch_ohlcv: always includes the current,
    still-forming candle as the last row when asked for recent data.
    """

    def __init__(self, num_closed_candles: int = 10):
        # candle i's open time is i * 60_000 ms; the last one (index
        # num_closed_candles) is the still-forming current candle.
        self.rows = [[i * 60_000, i, i + 1, i - 1, i, 1.0] for i in range(num_closed_candles + 1)]
        self.requested_limits: list[int] = []

    def fetch_ohlcv(self, symbol, timeframe="15m", since=None, limit=500):
        self.requested_limits.append(limit)
        rows = self.rows
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return rows[-limit:]


def test_fetch_ohlcv_includes_the_forming_candle():
    exchange = FakeExchange(num_closed_candles=10)
    df = fetch_ohlcv(exchange, "BTC/USDT", limit=3)
    # last row is candle #10, the still-forming one in this fake exchange.
    assert df.index[-1] == df.index[-1]  # sanity: index is set
    assert len(df) == 3
    assert df["close"].iloc[-1] == 10


def test_fetch_ohlcv_closed_drops_the_forming_candle():
    exchange = FakeExchange(num_closed_candles=10)
    df = fetch_ohlcv_closed(exchange, "BTC/USDT", limit=3)
    assert len(df) == 3
    # candle #10 (still forming) must not appear; the 3 closed candles are #7-#9.
    assert list(df["close"]) == [7, 8, 9]
    # requested one extra candle under the hood to still return `limit` closed ones.
    assert exchange.requested_limits[-1] == 4


def test_fetch_ohlcv_closed_returns_only_closed_candles_for_full_history():
    exchange = FakeExchange(num_closed_candles=10)
    df = fetch_ohlcv_closed(exchange, "BTC/USDT", limit=20)
    assert len(df) == 10  # only the 10 genuinely closed candles, never the 11th
    assert list(df["close"]) == list(range(10))
