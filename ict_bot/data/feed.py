"""OHLCV market data via ccxt (works with any exchange ccxt supports -
Binance, Bybit, Kraken, ... - configured through ``config.yaml``).
"""
from __future__ import annotations

import time

import ccxt
import pandas as pd


def make_exchange(exchange_id: str, api_key: str = "", api_secret: str = "", sandbox: bool = False, extra: dict | None = None) -> ccxt.Exchange:
    exchange_class = getattr(ccxt, exchange_id)
    params = {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True}
    params.update(extra or {})
    exchange = exchange_class(params)
    # ccxt's underlying requests.Session defaults trust_env=False, so it
    # silently ignores standard HTTP(S)_PROXY/NO_PROXY and CA-bundle
    # env vars - breaking any environment that routes outbound HTTPS
    # through a corporate or sandboxed proxy. Restore the normal requests
    # behavior of honoring those env vars.
    exchange.session.trust_env = True
    if sandbox and hasattr(exchange, "set_sandbox_mode"):
        exchange.set_sandbox_mode(True)
    return exchange


def fetch_ohlcv(
    exchange: ccxt.Exchange, symbol: str, timeframe: str = "15m", since_ms: int | None = None, limit: int = 500
) -> pd.DataFrame:
    """Fetch a single OHLCV page and return it as a DataFrame indexed by a
    UTC ``DatetimeIndex``.
    """
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    return df


def fetch_ohlcv_history(
    exchange: ccxt.Exchange, symbol: str, timeframe: str = "15m", since_ms: int | None = None, max_bars: int = 5000
) -> pd.DataFrame:
    """Page through ccxt's fetch_ohlcv to build a longer history than a
    single request allows (used for backtesting).
    """
    all_rows: list[list] = []
    limit = 1000
    cursor = since_ms
    tf_ms = exchange.parse_timeframe(timeframe) * 1000

    while len(all_rows) < max_bars:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit)
        if not batch:
            break
        all_rows.extend(batch)
        cursor = batch[-1][0] + tf_ms
        if len(batch) < limit:
            break
        time.sleep(exchange.rateLimit / 1000.0)

    df = pd.DataFrame(all_rows[:max_bars], columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").drop_duplicates()
    return df


def load_ohlcv_csv(path: str) -> pd.DataFrame:
    """Load OHLCV data previously saved to CSV (columns: timestamp, open,
    high, low, close, volume). Useful for offline backtesting without
    hitting an exchange API.

    Sorts by timestamp and drops duplicate timestamps (keeping the last):
    unlike ``fetch_ohlcv_history``, a CSV's row order and uniqueness aren't
    guaranteed (a common export format is newest-first, or a file gets
    concatenated from overlapping pages). Every detector and the backtest
    engine assume strictly increasing timestamps - feeding them
    out-of-order or duplicate rows silently produces a nonsense backtest
    rather than an error.
    """
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    return df[~df.index.duplicated(keep="last")]
