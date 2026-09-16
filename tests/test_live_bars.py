"""Bar intake for the live loop.

The bug these cover: the poll only asks for the last two closed candles.
That is enough while the loop keeps up, but not after an outage - the
short response no longer reaches back to where the loop left off, and
appending it splices a hole into the window. The indicators work
positionally, so a "20-bar channel" would then span more than 20 bars of
real time with nothing in the log to say so.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ict_bot.main import fetch_new_closed_bars

TF = pd.Timedelta(hours=4)
START = pd.Timestamp("2026-01-01 00:00", tz="UTC")


class FakeExchange:
    """Serves a contiguous 4h history and records what was asked for."""

    def __init__(self, bars: int = 300):
        index = pd.date_range(START, periods=bars, freq="4h", tz="UTC")
        self.history = pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}, index=index)
        self.requests: list[int] = []

    def fetch_ohlcv(self, symbol, timeframe="4h", since=None, limit=500):
        self.requests.append(limit)
        # fetch_ohlcv_closed asks for limit+1 and drops the still-forming last
        # row, so serve one extra to mimic a real exchange.
        rows = self.history.iloc[-limit:]
        return [[int(ts.timestamp() * 1000), r.open, r.high, r.low, r.close, r.volume]
                for ts, r in rows.iterrows()]


def _call(exchange, last_seen, window_size=200):
    return fetch_new_closed_bars(exchange, "BTC/USDT", "4h", last_seen, TF, window_size)


def test_the_normal_case_returns_only_the_new_bar():
    exchange = FakeExchange()
    last_seen = exchange.history.index[-3]
    new, replacement = _call(exchange, last_seen)

    assert replacement is None
    assert list(new.index) == [exchange.history.index[-2]]
    assert exchange.requests == [3]  # the cheap poll only


def test_two_bars_arriving_at_once_are_both_returned():
    # A slow poll can legitimately fall one bar behind without any outage,
    # and the skipped bar still has to be checked against the stop.
    exchange = FakeExchange()
    new, replacement = _call(exchange, exchange.history.index[-4])

    assert replacement is None
    assert len(new) == 2


def test_a_gap_triggers_a_refetch_that_closes_it():
    exchange = FakeExchange()
    last_seen = exchange.history.index[-8]  # five bars were missed
    new, replacement = _call(exchange, last_seen)

    assert replacement is None
    # Contiguous from the bar right after last_seen, with no hole.
    assert new.index[0] == last_seen + TF
    assert list(new.index) == list(pd.date_range(last_seen + TF, exchange.history.index[-2], freq="4h"))
    assert len(exchange.requests) == 2 and exchange.requests[1] == 201  # full window


def test_an_outage_longer_than_the_window_replaces_it():
    exchange = FakeExchange(bars=300)
    last_seen = exchange.history.index[0]  # ancient - the window cannot reach back
    new, replacement = _call(exchange, last_seen, window_size=50)

    assert replacement is not None
    assert len(replacement) == 50
    assert len(new) == 1 and new.index[-1] == replacement.index[-1]
    # The replacement is contiguous, which is the point: no spliced hole.
    assert (replacement.index.to_series().diff().dropna() == TF).all()


def test_no_new_bar_yields_nothing_and_does_not_refetch():
    exchange = FakeExchange()
    new, replacement = _call(exchange, exchange.history.index[-2])

    assert new.empty and replacement is None
    assert exchange.requests == [3]


def test_the_window_stays_contiguous_when_bars_are_appended():
    # What the loop does with the result: appending must not create a hole.
    exchange = FakeExchange()
    last_seen = exchange.history.index[-8]
    window = exchange.history.loc[:last_seen]
    new, replacement = _call(exchange, last_seen)
    if replacement is not None:
        window = replacement.iloc[:-1]
    for ts in new.index:
        window = pd.concat([window, new.loc[[ts]]]).iloc[-200:]

    gaps = window.index.to_series().diff().dropna()
    assert (gaps == TF).all(), f"window has holes: {sorted(set(gaps[gaps != TF]))}"
    assert window.index[-1] == exchange.history.index[-2]
