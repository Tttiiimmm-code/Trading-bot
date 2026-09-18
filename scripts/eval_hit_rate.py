"""What does a higher hit rate cost?

    python scripts/eval_hit_rate.py

Needs a local 4h OHLCV cache in data/ (see scripts/eval_entry_rsi.py).

35% of trades win, which reads like something to fix. It is not a property
of the strategy - it is a dial. A fixed take profit raises it immediately,
because no trade is allowed to hand back an open gain any more. This
measures what each turn of that dial costs, on the five markets actually
running, long only, with the live cost model.

The same trade set is used in every row; only the exit rule differs. So
nothing is being selected here and there is no train/test question to
answer - this is the price of a choice, not a search for a better one.

The take-profit variants get the benefit of every doubt: a target counts
as filled whenever the bar's high reached it, even if that same bar also
touched the stop. If they lose anyway, they lose for real.
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from ict_bot.backtest.engine import BacktestConfig, BacktestEngine
from ict_bot.data.feed import load_ohlcv_csv
from ict_bot.data.sanity import check as check_data
from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.risk import RiskConfig, RiskManager
from ict_bot.strategy.trend_strategy import TrendStrategy, TrendStrategyConfig

CACHE = Path("data")
MARKETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"]
MAKER, TAKER, SLIP = 0.02, 0.05, 0.02
RISK = RiskConfig(risk_per_trade_pct=1.0, max_daily_loss_pct=100.0, max_open_positions=1)
TREND = dict(entry_period=20, atr_stop_multiple=2.0, trail_atr_multiple=3.0,
             regime_period=100, allow_short=False)
TARGETS = (1.0, 1.5, 2.0, 3.0, 5.0)

# A stop-out crosses the spread; a resting target does not.
STOP_COST = MAKER + TAKER + SLIP
TARGET_COST = MAKER + MAKER


class Recorder(PaperBroker):
    """A PaperBroker that keeps the stop each position was opened with.

    Trade only carries entry and exit, and the risked distance is what
    every number here is expressed in.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: list[dict] = []

    def open_position(self, symbol, side, amount, price, stop_loss, take_profit, ts, **kwargs):
        position = super().open_position(symbol, side, amount, price, stop_loss,
                                         take_profit, ts, **kwargs)
        self.records.append({"entry": price, "stop": stop_loss, "opened_at": ts,
                             "exit": None, "closed_at": None})
        return position

    def close_position(self, symbol, price, ts, reason="manual_close"):
        fill = super().close_position(symbol, price, ts, reason=reason)
        if fill is not None:
            for record in reversed(self.records):
                if record["closed_at"] is None:
                    record.update(exit=price, closed_at=ts)
                    break
        return fill


def _run(symbol: str) -> list[dict]:
    for name in ("ict_bot.strategy.risk", "ict_bot.data.sanity"):
        logging.getLogger(name).setLevel(logging.ERROR)
    df = check_data(load_ohlcv_csv(CACHE / f"{symbol.replace('/', '')}_4h.csv"),
                    symbol, pd.Timedelta(hours=4), 5.0)
    broker = Recorder(10_000.0, maker_fee_pct=MAKER, taker_fee_pct=TAKER,
                      stop_slippage_pct=SLIP, trail_on="close")
    engine = BacktestEngine(
        df, TrendStrategy(TrendStrategyConfig(**TREND)), RiskManager(RISK),
        BacktestConfig(symbol=symbol, starting_balance=10_000.0, window_size=400,
                       pending_order_expiry_bars=2, maker_fee_pct=MAKER,
                       taker_fee_pct=TAKER, stop_slippage_pct=SLIP, trail_on="close"))
    engine.broker = broker
    engine.run()

    rows = []
    for record in broker.records:
        if record["closed_at"] is None:
            continue
        risk = record["entry"] - record["stop"]
        if risk <= 0:
            continue
        during = df.loc[record["opened_at"]:record["closed_at"]]
        if during.empty:
            continue
        rows.append({
            "symbol": symbol,
            "opened_at": record["opened_at"],
            # Cost in R: the same percentage fee is a large share of a
            # narrow stop and a rounding error on a wide one.
            "cost_unit": record["entry"] / risk / 100.0,
            "gross_r": (record["exit"] - record["entry"]) / risk,
            "mfe_r": (float(during["high"].max()) - record["entry"]) / risk,
        })
    return rows


def account(rs: list[float], opened: list[pd.Timestamp], risk_pct: float) -> tuple[float, float]:
    """One account taking these trades in order, and its worst trough.

    The trade set is identical across variants, so this compares exits
    rather than selection: no position cap, no sizing difference.
    """
    equity = peak = 10_000.0
    worst = 0.0
    for i in sorted(range(len(rs)), key=lambda i: opened[i]):
        equity *= 1 + rs[i] * risk_pct / 100.0
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return equity, worst * 100


def line(label: str, rs: list[float], opened: list[pd.Timestamp], risk_pct: float) -> None:
    # Simultaneous positions across markets are not independent, so each
    # quarter counts once.
    by_quarter: dict[str, list[float]] = {}
    for r, ts in zip(rs, opened):
        key = str(pd.Timestamp(ts).tz_localize(None).to_period("Q"))
        by_quarter.setdefault(key, []).append(r)
    quarters = [statistics.mean(v) for v in by_quarter.values()]
    t = (statistics.mean(quarters) / (statistics.stdev(quarters) / len(quarters) ** 0.5)
         if len(quarters) > 1 else float("nan"))
    equity, drawdown = account(rs, opened, risk_pct)
    print(f"{label:22s} {len(rs):>7d} {sum(1 for r in rs if r > 0) / len(rs) * 100:>8.1f} "
          f"{statistics.mean(rs):>+9.3f} {sum(rs):>+9.1f} {t:>7.2f} {equity:>11,.0f} "
          f"{drawdown:>8.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--risk-pct", type=float, default=0.5,
                        help="risk per trade for the equity column (default 0.5)")
    args = parser.parse_args()

    rows = [row for symbol in MARKETS for row in _run(symbol)]
    if not rows:
        sys.exit("No trades - is the 4h cache in data/ populated?")
    opened = [row["opened_at"] for row in rows]
    print(f"{len(rows)} trades, {len(MARKETS)} markets, long only, 4h\n")
    print(f"{'exit rule':22s} {'trades':>7s} {'win%':>8s} {'mean R':>9s} "
          f"{'total R':>9s} {'q-t':>7s} {'equity':>11s} {'maxDD%':>8s}")
    print("-" * 86)

    stopped = [row["gross_r"] - row["cost_unit"] * STOP_COST for row in rows]
    line("trailing stop (live)", stopped, opened, args.risk_pct)
    for target in TARGETS:
        capped = [target - row["cost_unit"] * TARGET_COST if row["mfe_r"] >= target else base
                  for row, base in zip(rows, stopped)]
        line(f"take profit at {target:g}R", capped, opened, args.risk_pct)

    gross = sorted((row["gross_r"] for row in rows), reverse=True)
    total = sum(gross)
    print()
    for share in (0.05, 0.10, 0.25):
        n = max(1, int(len(gross) * share))
        print(f"top {share:.0%} of trades carry {sum(gross[:n]) / total * 100:4.0f}% "
              f"of the gross profit ({n} trades)")
    print(f"\nbiggest single winner {gross[0]:+.1f}R, median trade {statistics.median(gross):+.2f}R")
    print("Over 100% means the remaining trades lose money in aggregate.")


if __name__ == "__main__":
    main()
