"""ICT (Inner Circle Trader) concept detectors: market structure, order
blocks, fair value gaps, liquidity, kill zones and premium/discount zones.

Every detector works on a plain pandas OHLCV DataFrame with columns
``open, high, low, close, volume`` and a ``DatetimeIndex`` (UTC). Detectors
are pure functions/classes with no I/O and no side effects, so they can be
unit tested with synthetic data and reused identically in backtesting and
live trading.
"""
