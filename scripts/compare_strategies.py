"""Compare a handful of ICTStrategyConfig variants against real historical
data, with a chronological train/test split to guard against curve-fitting
a strategy change to one lucky window.

Usage (from the repo root, venv activated, on a host with real exchange
access - e.g. the deployment VPS, not necessarily this dev machine):

    python scripts/compare_strategies.py --config config/config.yaml
    python scripts/compare_strategies.py --config config/config.yaml --since 2024-01-01 --history-bars 20000

Reuses config.yaml's exchange/market/risk settings so this always compares
apples to apples with what's actually deployed. The variant list below is
deliberately short - each one is a specific, reasoned hypothesis, not a
blind grid search. A variant only means something if it holds up on the
*test* split too: looking great on train but falling apart on test is
curve-fitting, not an edge, and should not be deployed.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

# Make the repo root importable regardless of the current working directory
# or how this script is invoked (`python scripts/compare_strategies.py`
# only puts `scripts/` on sys.path, not the repo root where `ict_bot` lives).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from ict_bot.backtest.engine import BacktestConfig as EngineBacktestConfig
from ict_bot.backtest.engine import BacktestEngine
from ict_bot.backtest.metrics import compute_metrics, pair_trades
from ict_bot.config import load_config
from ict_bot.data.feed import fetch_ohlcv_history, make_exchange
from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig
from ict_bot.strategy.risk import RiskConfig, RiskManager


def build_variants(base: ICTStrategyConfig) -> dict[str, ICTStrategyConfig]:
    def clone(**overrides) -> ICTStrategyConfig:
        return dataclasses.replace(base, **overrides)

    return {
        "A) Baseline (config.yaml as-is)": clone(),
        "B) min_risk_reward -> 1.5": clone(min_risk_reward=1.5),
        "C) sweep_lookback_bars -> 20": clone(sweep_lookback_bars=20),
        "D) B + C combined": clone(min_risk_reward=1.5, sweep_lookback_bars=20),
        "E) liquidity_tolerance_pct -> 0.08": clone(liquidity_tolerance_pct=0.08),
    }


def run_backtest(df: pd.DataFrame, strategy_cfg: ICTStrategyConfig, risk_cfg: RiskConfig, engine_cfg: EngineBacktestConfig) -> dict:
    strategy = ICTStrategy(strategy_cfg)
    risk_manager = RiskManager(risk_cfg)
    engine = BacktestEngine(df, strategy, risk_manager, engine_cfg)
    result = engine.run()
    trades = pair_trades(result.fills)
    return compute_metrics(trades, result.equity_curve, engine_cfg.starting_balance)


def fmt(metrics: dict) -> str:
    pf = metrics["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
    return (
        f"{metrics['num_trades']:>3d} trades | WR {metrics['win_rate_pct']:>5.1f}% | "
        f"PF {pf_str:>5s} | Return {metrics['total_return_pct']:>7.2f}% | MaxDD {metrics['max_drawdown_pct']:>6.2f}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ICT strategy variants on a train/test split of real historical data.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--since", default=None, help="ISO date to start history from, e.g. 2024-01-01")
    parser.add_argument("--history-bars", type=int, default=None, help="override backtest.history_bars from the config")
    parser.add_argument("--train-split", type=float, default=0.7, help="fraction of the fetched history used for training (rest = held-out test)")
    args = parser.parse_args()

    config = load_config(args.config)
    history_bars = args.history_bars or config.backtest.history_bars

    exchange = make_exchange(config.exchange.id, config.exchange.api_key, config.exchange.api_secret, sandbox=config.exchange.sandbox)
    if args.since:
        since_ms = int(pd.Timestamp(args.since, tz="UTC").timestamp() * 1000)
    else:
        tf_ms = exchange.parse_timeframe(config.market.timeframe) * 1000
        since_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000) - tf_ms * history_bars

    df = fetch_ohlcv_history(exchange, config.market.symbol, config.market.timeframe, since_ms=since_ms, max_bars=history_bars)
    if df.empty:
        print("No data loaded, aborting.")
        return

    engine_cfg = EngineBacktestConfig(
        symbol=config.market.symbol,
        starting_balance=config.backtest.starting_balance,
        window_size=config.backtest.window_size,
        pending_order_expiry_bars=config.backtest.pending_order_expiry_bars,
    )

    split_i = int(len(df) * args.train_split)
    train_df, test_df = df.iloc[:split_i], df.iloc[split_i:]
    print(f"Loaded {len(df)} bars ({config.market.symbol} {config.market.timeframe}): {df.index[0]} -> {df.index[-1]}")
    print(f"Train: {len(train_df)} bars ({train_df.index[0]} -> {train_df.index[-1]})")
    print(f"Test:  {len(test_df)} bars ({test_df.index[0]} -> {test_df.index[-1]})")
    if len(train_df) < engine_cfg.window_size * 2 or len(test_df) < engine_cfg.window_size * 2:
        print(
            f"\nWARNING: a slice has fewer than {engine_cfg.window_size * 2} bars - too little history for a "
            "meaningful comparison. Fetch more data (--history-bars) or use a coarser --train-split."
        )
    print()

    variants = build_variants(config.strategy)
    name_width = max(len(n) for n in variants) + 2
    for name, cfg in variants.items():
        train_metrics = run_backtest(train_df, cfg, config.risk, engine_cfg)
        test_metrics = run_backtest(test_df, cfg, config.risk, engine_cfg)
        print(f"{name:{name_width}s}")
        print(f"  train: {fmt(train_metrics)}")
        print(f"  test:  {fmt(test_metrics)}")
        print()

    print("A variant only means something if train AND test both look reasonable.")
    print("Good on train but bad/flat on test = curve-fitting, not a real edge - don't deploy it.")


if __name__ == "__main__":
    main()
