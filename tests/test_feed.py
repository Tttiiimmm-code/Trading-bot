"""load_ohlcv_csv must not trust a CSV's row order or uniqueness: every
detector and the backtest engine assume strictly increasing timestamps,
and a CSV commonly isn't (newest-first exports, concatenated overlapping
pages) - feeding them out-of-order or duplicate rows would silently
produce a nonsense backtest instead of an error.
"""
from __future__ import annotations

from ict_bot.data.feed import load_ohlcv_csv


def test_load_ohlcv_csv_sorts_out_of_order_rows(tmp_path):
    csv_path = tmp_path / "reversed.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01T00:30:00Z,3,3,3,3,1\n"
        "2024-01-01T00:15:00Z,2,2,2,2,1\n"
        "2024-01-01T00:00:00Z,1,1,1,1,1\n"
    )

    df = load_ohlcv_csv(str(csv_path))

    assert df.index.is_monotonic_increasing
    assert list(df["close"]) == [1, 2, 3]


def test_load_ohlcv_csv_drops_duplicate_timestamps(tmp_path):
    csv_path = tmp_path / "dupes.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01T00:00:00Z,1,1,1,1,1\n"
        "2024-01-01T00:00:00Z,1,1,1,99,1\n"  # revised/overlapping row for the same candle
        "2024-01-01T00:15:00Z,2,2,2,2,1\n"
    )

    df = load_ohlcv_csv(str(csv_path))

    assert len(df) == 2
    assert not df.index.has_duplicates
    assert df.loc["2024-01-01T00:00:00Z", "close"] == 99  # last row for that timestamp wins
