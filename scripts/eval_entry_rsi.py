"""Does a stretched entry predict a worse trade?

    python scripts/eval_entry_rsi.py

Needs a local 4h OHLCV cache in data/. Written to check a claim made about
a third party's "M1-RSI spike filter": that entering while short-term
momentum is overheated causes losses. Their threshold was chosen against
three trades; this asks the same question of every trade in the history.

It also demonstrates a trap worth remembering. Bucketing trades by the RSI
on their *entry* bar shows a huge effect - and turning that into a filter
at *signal* time recovers almost none of it, because the entry happens a
bar after the signal. Information that only exists once you are filled is
not information you can trade on.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from ict_bot.backtest.engine import BacktestConfig, BacktestEngine
from ict_bot.backtest.metrics import pair_trades
from ict_bot.data.feed import load_ohlcv_csv
from ict_bot.strategy.risk import RiskConfig, RiskManager
from ict_bot.strategy.trend_strategy import TrendStrategy, TrendStrategyConfig

CACHE = Path("data")
MARKETS = ["BTC/USDT", "ETH/USDT", "LTC/USDT", "XRP/USDT", "ADA/USDT",
           "LINK/USDT", "BCH/USDT", "SOL/USDT", "DOGE/USDT", "AVAX/USDT"]
MAKER, TAKER, SLIP = 0.02, 0.05, 0.02
RISK = RiskConfig(risk_per_trade_pct=1.0, max_daily_loss_pct=100.0, max_open_positions=1)
TREND = dict(entry_period=20, atr_stop_multiple=2.0, trail_atr_multiple=3.0, regime_period=100)


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    avg_gain = delta.clip(lower=0.0).ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0.0).ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain.divide(avg_loss.where(avg_loss > 0))
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _run(symbol: str) -> list[dict]:
    logging.getLogger("ict_bot.strategy.risk").setLevel(logging.ERROR)
    df = load_ohlcv_csv(CACHE / f"{symbol.replace('/', '')}_4h.csv")
    entry_rsi = rsi(df["close"])
    engine = BacktestEngine(
        df, TrendStrategy(TrendStrategyConfig(**TREND)), RiskManager(RISK),
        BacktestConfig(symbol=symbol, starting_balance=10_000.0, window_size=400,
                       pending_order_expiry_bars=2, maker_fee_pct=MAKER,
                       taker_fee_pct=TAKER, stop_slippage_pct=SLIP))
    result = engine.run()
    rows = []
    for trade in pair_trades(result.fills):
        try:
            equity = float(result.equity_curve.loc[trade.opened_at])
            value = float(entry_rsi.loc[trade.opened_at])
        except KeyError:
            continue
        rows.append({"side": trade.side.value, "rsi": value,
                     "r": trade.pnl / (equity * RISK.risk_per_trade_pct / 100.0)})
    return rows


def _line(label: str, rows: pd.DataFrame) -> None:
    n = len(rows)
    if n < 2:
        print(f"{label:24s} {n:>5d}      -        -      -")
        return
    mean = rows["r"].mean()
    t = mean / (rows["r"].std() / math.sqrt(n))
    print(f"{label:24s} {n:>5d} {(rows['r'] > 0).mean() * 100:>6.1f} {mean:>+8.3f} {t:>6.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for part in pool.map(_run, MARKETS):
            rows.extend(part)
    df = pd.DataFrame(rows)
    print(f"{len(df)} trades\n")

    for side in ("long", "short"):
        sub = df[df["side"] == side]
        print(f"=== {side} entries by RSI on the entry bar ===")
        print(f"{'bucket':24s} {'n':>5s} {'WR%':>6s} {'meanR':>8s} {'t':>6s}")
        print("-" * 54)
        edges = [0, 40, 50, 60, 65, 70, 80, 101]
        for lo, hi in zip(edges, edges[1:]):
            _line(f"RSI {lo}-{hi - 1}", sub[(sub["rsi"] >= lo) & (sub["rsi"] < hi)])
        _line("all", sub)
        print(f"correlation RSI vs result: {sub['rsi'].corr(sub['r']):+.3f}\n")


if __name__ == "__main__":
    main()
