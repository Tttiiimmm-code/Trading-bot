"""Score a live/paper run's trade journal with the backtest's own metrics.

    python scripts/score_trades.py state/config-trend-btc-trades.csv

A paper run is only worth doing if you can compare it to what the backtest
predicted, and "the log looked fine" is not a comparison. This reads the
append-only CSV the live loop writes and reports the same figures the
backtest prints, plus the per-trade R statistics used throughout the
README - so a paper result and a backtest result can be put side by side.

Pass several files to pool instances (they are separate simulated
accounts, so balances are not added - only the per-trade statistics are).
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(path: Path) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))


def summarise(rows: list[dict], risk_pct: float, label: str, pooled: bool = False) -> None:
    if not rows:
        print(f"{label}: no closed trades yet")
        return
    # Sorting matters for more than tidiness: the first/last line below reads
    # the ends of this list, and a pooled list arrives grouped by file.
    rows = sorted(rows, key=lambda r: r["closed_at"])

    pnls = [float(r["pnl"]) for r in rows]
    fees = sum(float(r["fees"]) for r in rows)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # R per trade: the balance *before* the trade is the balance after it,
    # minus what it made, which is what the risked amount was a % of.
    r_values = []
    for row in rows:
        pnl = float(row["pnl"])
        balance_before = float(row["balance_after"]) - pnl
        risked = balance_before * risk_pct / 100.0
        if risked > 0:
            r_values.append(pnl / risked)

    print(f"\n=== {label} ===")
    print(f"trades          : {len(rows)}")
    print(f"first / last    : {_stamp(min(r['opened_at'] for r in rows))} -> {_stamp(rows[-1]['closed_at'])}")
    print(f"win rate        : {len(wins) / len(pnls) * 100:.1f}%")
    print(f"net PnL         : {sum(pnls):+,.2f}   (fees paid {fees:,.2f})")
    if losses and sum(losses):
        print(f"profit factor   : {sum(wins) / abs(sum(losses)):.2f}")
    if pooled:
        # Each instance runs its own simulated account, so there is no single
        # balance to report - adding them would invent a portfolio that does
        # not exist.
        print("final balance   : n/a (separate accounts - see the per-instance figures)")
    else:
        print(f"final balance   : {float(rows[-1]['balance_after']):,.2f}")

    if len(r_values) >= 2:
        mean = statistics.mean(r_values)
        se = statistics.stdev(r_values) / math.sqrt(len(r_values))
        print(f"mean R          : {mean:+.3f}  (median {statistics.median(r_values):+.3f})")
        print(f"t / 95% CI      : {mean / se if se else 0:.2f}  "
              f"[{mean - 1.96 * se:+.3f}, {mean + 1.96 * se:+.3f}]")
        if mean - 1.96 * se <= 0 <= mean + 1.96 * se:
            print("                  interval straddles zero - not yet distinguishable from no edge")
    else:
        print("mean R          : need at least 2 trades")

    by_reason: dict[str, int] = {}
    for row in rows:
        by_reason[row["exit_reason"]] = by_reason.get(row["exit_reason"], 0) + 1
    print("exits           : " + ", ".join(f"{k} {v}" for k, v in sorted(by_reason.items())))


def _stamp(iso: str) -> str:
    """2026-09-20T00:00:00+00:00 -> 2026-09-20 00:00."""
    return iso[:16].replace("T", " ")


def list_trades(rows: list[dict], risk_pct: float, last: int | None) -> None:
    """Every trade in time order. Kept narrow on purpose - this gets read
    over SSH from a phone as often as from a desktop."""
    rows = sorted(rows, key=lambda r: r["closed_at"])
    shown = rows[-last:] if last else rows
    print(f"\n{'closed':16s} {'market':9s} {'side':5s} {'exit':6s} {'R':>6s} {'pnl':>10s}")
    print("-" * 57)
    for row in shown:
        pnl = float(row["pnl"])
        balance_before = float(row["balance_after"]) - pnl
        risked = balance_before * risk_pct / 100.0
        r = f"{pnl / risked:+.2f}" if risked > 0 else "n/a"
        # "stop_loss" / "take_profit" -> "stop" / "target", to stay narrow.
        exit_label = {"stop_loss": "stop", "take_profit": "target",
                      "manual_close": "manual"}.get(row["exit_reason"], row["exit_reason"])[:6]
        print(f"{_stamp(row['closed_at']):16s} {row['symbol'].replace('/USDT', ''):9s} "
              f"{row['side']:5s} {exit_label:6s} {r:>6s} {pnl:>+10.2f}")
    if last and len(rows) > last:
        print(f"({len(rows) - last} older trade(s) not shown - drop --last to see them)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("journals", nargs="+", type=Path, help="trade CSV file(s) written by the live loop")
    parser.add_argument("--risk-pct", type=float, default=0.5,
                        help="risk_per_trade_pct the run used, for the R figures (default 0.5)")
    parser.add_argument("--list", action="store_true", dest="list_trades",
                        help="list every trade in time order across all files, newest last")
    parser.add_argument("--last", type=int, metavar="N",
                        help="with --list, show only the N most recent trades")
    args = parser.parse_args()

    pooled: list[dict] = []
    for path in args.journals:
        if not path.exists():
            print(f"{path}: not found - the bot writes it after its first closed trade")
            continue
        rows = load(path)
        pooled.extend(rows)
        if not args.list_trades:
            summarise(rows, args.risk_pct, path.name)

    if args.list_trades:
        if not pooled:
            print("No closed trades yet.")
            return
        list_trades(pooled, args.risk_pct, args.last)
        summarise(pooled, args.risk_pct, f"all {len(args.journals)} instance(s)",
                  pooled=len(args.journals) > 1)
        return

    if len(args.journals) > 1 and pooled:
        pooled.sort(key=lambda r: r["closed_at"])
        summarise(pooled, args.risk_pct, "all instances pooled", pooled=True)
        print("\nNote: each instance is its own simulated account, so the net PnL above is a")
        print("sum over separate accounts, not one portfolio's result.")


if __name__ == "__main__":
    main()
