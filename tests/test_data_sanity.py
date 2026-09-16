"""Guards against a lying price feed.

Not hypothetical: pulling the same markets from a second exchange found a
KuCoin LTC candle whose high was 43% above OKX's for the same period, and
several series missing whole stretches of bars. A false high sets the
breakout channel for the next twenty bars; a gap makes a "20-bar channel"
span more than twenty bars of real time.
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from ict_bot.data.sanity import check, clip_bad_wicks, clip_with_context, find_gaps

TF = pd.Timedelta(hours=4)


def _frame(rows, start="2026-01-01", freq="4h"):
    index = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    df["volume"] = 1.0
    return df


def _calm(n=60, price=100.0):
    return [(price, price + 1, price - 1, price)] * n


def test_an_ordinary_series_is_left_alone():
    df = _frame(_calm())
    cleaned, clipped = clip_bad_wicks(df)
    assert clipped == 0
    pd.testing.assert_frame_equal(cleaned, df)


def test_a_forty_percent_spike_is_clipped_back():
    rows = _calm()
    rows[30] = (100.0, 144.0, 99.0, 100.0)      # the LTC print, in miniature
    df = _frame(rows)
    cleaned, clipped = clip_bad_wicks(df)

    assert clipped == 1
    assert cleaned["high"].iloc[30] < 144.0
    assert cleaned["high"].iloc[30] >= 100.0     # never below the bar's own body
    assert cleaned["close"].iloc[30] == 100.0    # open and close are struck prices
    assert cleaned["open"].iloc[30] == 100.0


def test_a_false_low_is_clipped_too():
    rows = _calm()
    rows[30] = (100.0, 101.0, 55.0, 100.0)
    cleaned, clipped = clip_bad_wicks(_frame(rows))
    assert clipped == 1 and cleaned["low"].iloc[30] > 55.0


def test_real_volatility_survives():
    # A genuinely wild but plausible candle - a 6% range where the typical
    # bar is 2% - must not be touched, or the guard eats the strategy.
    rows = [(100.0, 101.0, 99.0, 100.0)] * 60
    rows[30] = (100.0, 103.0, 97.0, 102.0)
    cleaned, clipped = clip_bad_wicks(_frame(rows))
    assert clipped == 0


def test_a_limit_of_zero_disables_clipping():
    rows = _calm()
    rows[30] = (100.0, 500.0, 99.0, 100.0)
    _, clipped = clip_bad_wicks(_frame(rows), limit=0)
    assert clipped == 0


def test_gaps_are_found_with_their_size():
    df = _frame(_calm(10))
    df = pd.concat([df.iloc[:4], df.iloc[7:]])   # three bars removed
    gaps = find_gaps(df, TF)
    assert len(gaps) == 1
    assert gaps[0][1] == 3


def test_a_contiguous_series_has_no_gaps():
    assert find_gaps(_frame(_calm(20)), TF) == []


def test_incoming_bars_are_judged_against_the_history_before_them():
    # One or two fresh bars carry no idea of what a normal range is. The
    # window already held does.
    history = _frame(_calm(60))
    fresh = _frame([(100.0, 150.0, 99.0, 100.0)],
                   start=history.index[-1] + TF)
    cleaned, clipped = clip_with_context(history, fresh)

    assert clipped == 1
    assert cleaned["high"].iloc[0] < 150.0
    assert list(cleaned.index) == list(fresh.index)


def test_a_normal_incoming_bar_passes_untouched():
    history = _frame(_calm(60))
    fresh = _frame([(100.0, 101.5, 98.5, 101.0)], start=history.index[-1] + TF)
    cleaned, clipped = clip_with_context(history, fresh)
    assert clipped == 0
    pd.testing.assert_frame_equal(cleaned, fresh)


def test_check_reports_problems_without_refusing_to_run(caplog):
    # A feed with one bad print is still worth trading on; a bot that
    # refuses to start over a blemish is less useful than one that says
    # what it found.
    rows = _calm()
    rows[30] = (100.0, 144.0, 99.0, 100.0)
    df = _frame(rows)
    df = pd.concat([df.iloc[:10], df.iloc[13:]])     # and a hole

    with caplog.at_level(logging.WARNING):
        cleaned = check(df, "TEST/USD", TF)

    message = caplog.text
    assert "gap" in message and "missing" in message
    assert "wick" in message
    assert len(cleaned) == len(df)                   # it still returns usable data


def test_check_is_quiet_when_nothing_is_wrong(caplog):
    with caplog.at_level(logging.WARNING):
        check(_frame(_calm(40)), "TEST/USD", TF)
    assert caplog.text == ""


def test_impossible_bars_are_reported(caplog):
    rows = _calm()
    rows[5] = (100.0, 95.0, 99.0, 100.0)             # high below low
    with caplog.at_level(logging.WARNING):
        check(_frame(rows), "TEST/USD", TF)
    assert "contradict" in caplog.text
