"""Re-run the intermarket divergence measurement.

    python scripts/eval_divergence.py

Needs a local 4h OHLCV cache (see scripts/fetch_history.py). Reports per
pair and pooled, with the quarter-clustered t that decides the question -
ten pairs all referenced to BTC or ETH trade together, so counting each
trade as an independent observation overstates the evidence badly. See
"Intermarket divergence" in the README for what this found.
"""
import logging, math, statistics, sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
from ict_bot.strategy.divergence_strategy import DivergenceConfig, DivergenceStrategy
from ict_bot.data.feed import load_ohlcv_csv
from ict_bot.strategy.risk import RiskConfig

CACHE = Path("data")
MAKER, TAKER, SLIP = 0.02, 0.05, 0.02
RISK = RiskConfig(risk_per_trade_pct=1.0, max_daily_loss_pct=100.0, max_open_positions=1)


def load_deep(symbol: str):
    return load_ohlcv_csv(CACHE / f"{symbol.replace('/', '')}_4h.csv")
from ict_bot.backtest.engine import BacktestConfig as EC, BacktestEngine
from ict_bot.backtest.metrics import pair_trades
from ict_bot.strategy.risk import RiskManager

# (traded, reference) - the pair the user asked for first, then others to
# see whether anything found is specific to one pair or general.
PAIRS = [("ETH/USDT", "BTC/USDT"), ("SOL/USDT", "ETH/USDT"), ("LTC/USDT", "BTC/USDT"),
         ("XRP/USDT", "BTC/USDT"), ("ADA/USDT", "ETH/USDT"), ("AVAX/USDT", "SOL/USDT"),
         ("LINK/USDT", "ETH/USDT"), ("BCH/USDT", "BTC/USDT"), ("DOGE/USDT", "BTC/USDT"),
         ("BTC/USDT", "ETH/USDT")]


def _run(args):
    traded, reference, kwargs = args
    logging.getLogger("ict_bot.strategy.risk").setLevel(logging.ERROR)
    df = load_deep(traded)
    ref = load_deep(reference)["close"]
    strategy = DivergenceStrategy(DivergenceConfig(**kwargs), ref)
    engine = BacktestEngine(
        df, strategy, RiskManager(RISK),
        EC(symbol=traded, starting_balance=10_000.0, window_size=400, pending_order_expiry_bars=2,
           maker_fee_pct=MAKER, taker_fee_pct=TAKER, stop_slippage_pct=SLIP))
    result = engine.run()
    rows = []
    for t in pair_trades(result.fills):
        try:
            eq = float(result.equity_curve.loc[t.opened_at])
        except KeyError:
            eq = 10_000.0
        rows.append({"pair": f"{traded.replace('/USDT','')}/{reference.replace('/USDT','')}",
                     "opened_at": t.opened_at, "r": t.pnl / (eq * RISK.risk_per_trade_pct / 100.0)})
    return rows


def stats(rs):
    n = len(rs)
    if n < 2:
        return n, 0.0, 0.0, 0.0
    mean = sum(rs) / n
    t = mean / (statistics.stdev(rs) / math.sqrt(n))
    return n, sum(1 for x in rs if x > 0) / n * 100, mean, t


def evaluate(label, pairs=None, **kwargs):
    pairs = pairs or PAIRS
    rows = []
    with ProcessPoolExecutor(max_workers=5) as pool:
        for r in pool.map(_run, [(a, b, kwargs) for a, b in pairs]):
            rows.extend(r)
    df = pd.DataFrame(rows)
    print(f"\n=== {label} ===")
    print(f"{'pair':14s} {'n':>5s} {'WR%':>6s} {'meanR':>8s} {'t':>6s}")
    print("-" * 44)
    for pair in df["pair"].unique() if not df.empty else []:
        n, wr, mean, t = stats(df.loc[df["pair"] == pair, "r"].tolist())
        print(f"{pair:14s} {n:>5d} {wr:>6.1f} {mean:>+8.3f} {t:>6.2f}")
    if df.empty:
        print("no trades at all")
        return df
    n, wr, mean, t = stats(df["r"].tolist())
    se = statistics.stdev(df["r"]) / math.sqrt(n)
    print("-" * 44)
    print(f"{'POOLED':14s} {n:>5d} {wr:>6.1f} {mean:>+8.3f} {t:>6.2f}   "
          f"95% CI [{mean - 1.96 * se:+.3f}, {mean + 1.96 * se:+.3f}]")
    return df


if __name__ == "__main__":
    # His settings exactly, long only, as the Gold/Silver bot ships.
    eth_btc = evaluate("ETH vs BTC only - his parameters, long only", pairs=[("ETH/USDT", "BTC/USDT")])
    all_pairs = evaluate("All 10 pairs - his parameters, long only")
    both = evaluate("All 10 pairs - mirrored to allow shorts too", allow_short=True)
