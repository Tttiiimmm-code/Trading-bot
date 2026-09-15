"""CLI entry point.

    python -m ict_bot.main backtest --config config/config.yaml
    python -m ict_bot.main live --config config/config.yaml --mode paper
    python -m ict_bot.main live --config config/config.yaml --mode live   # real orders, use with care

``live --mode live`` places real orders on the configured exchange (real
money unless the exchange's sandbox/testnet is enabled in config.yaml).
``live --mode paper`` and ``backtest`` never touch a real exchange
balance.
"""
from __future__ import annotations

import argparse
import time

import pandas as pd

from ict_bot.backtest.engine import BacktestEngine, BacktestConfig as EngineBacktestConfig
from ict_bot.backtest.metrics import compute_metrics, pair_trades
from ict_bot.config import AppConfig, load_config
from ict_bot.data.feed import fetch_ohlcv_closed, fetch_ohlcv_history, load_ohlcv_csv, make_exchange
from ict_bot.execution.broker import Broker
from ict_bot.execution.ccxt_broker import CCXTBroker, quote_currency_from_symbol
from ict_bot.execution.paper import PaperBroker
from ict_bot.strategy.ict_strategy import ICTStrategy, Signal
from ict_bot.strategy.risk import RiskManager
from ict_bot.utils.logger import setup_logger

logger = setup_logger()


def cmd_backtest(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    strategy = ICTStrategy(config.strategy)
    risk_manager = RiskManager(config.risk)

    if args.csv:
        df = load_ohlcv_csv(args.csv)
    else:
        exchange = make_exchange(config.exchange.id, config.exchange.api_key, config.exchange.api_secret, sandbox=config.exchange.sandbox)
        since_ms = None
        if args.since:
            since_ms = int(pd.Timestamp(args.since, tz="UTC").timestamp() * 1000)
        elif config.backtest.history_bars:
            tf_ms = exchange.parse_timeframe(config.market.timeframe) * 1000
            since_ms = int(pd.Timestamp.now("UTC").timestamp() * 1000) - tf_ms * config.backtest.history_bars
        df = fetch_ohlcv_history(exchange, config.market.symbol, config.market.timeframe, since_ms=since_ms, max_bars=config.backtest.history_bars)

    if df.empty:
        logger.error("No OHLCV data loaded, aborting backtest.")
        return
    logger.info("Loaded %d bars from %s to %s", len(df), df.index[0], df.index[-1])

    engine_cfg = EngineBacktestConfig(
        symbol=config.market.symbol,
        starting_balance=config.backtest.starting_balance,
        window_size=config.backtest.window_size,
        pending_order_expiry_bars=config.backtest.pending_order_expiry_bars,
        fee_pct=config.backtest.fee_pct,
        stop_slippage_pct=config.backtest.stop_slippage_pct,
    )
    engine = BacktestEngine(df, strategy, risk_manager, engine_cfg)
    result = engine.run()

    trades = pair_trades(result.fills)
    metrics = compute_metrics(trades, result.equity_curve, config.backtest.starting_balance)

    print("\n=== Backtest results ===")
    for key, value in metrics.items():
        if isinstance(value, float):
            print(f"{key:20s}: {value:,.2f}")
        else:
            print(f"{key:20s}: {value}")


def cmd_live(args: argparse.Namespace) -> None:
    config: AppConfig = load_config(args.config)
    strategy = ICTStrategy(config.strategy)
    risk_manager = RiskManager(config.risk)
    symbol = config.market.symbol
    timeframe = config.market.timeframe

    exchange = make_exchange(config.exchange.id, config.exchange.api_key, config.exchange.api_secret, sandbox=config.exchange.sandbox)

    broker: Broker
    if args.mode == "live":
        if not config.exchange.sandbox:
            logger.warning("LIVE mode with sandbox=false: this will place REAL orders with REAL funds on %s.", config.exchange.id)
        broker = CCXTBroker(exchange, quote_currency=quote_currency_from_symbol(symbol), use_native_sl_tp=config.live.use_native_sl_tp)
    else:
        starting_balance = config.backtest.starting_balance
        logger.info("Paper trading mode: simulated balance %.2f", starting_balance)
        broker = PaperBroker(starting_balance, fee_pct=config.backtest.fee_pct,
                             stop_slippage_pct=config.backtest.stop_slippage_pct)

    window = fetch_ohlcv_closed(exchange, symbol, timeframe, limit=max(config.backtest.window_size, 100))
    pending: Signal | None = None
    pending_bars_left = 0
    last_status_log: pd.Timestamp | None = None
    status_interval = pd.Timedelta(minutes=config.live.status_log_interval_minutes)

    logger.info("Starting live loop (%s) on %s %s. Ctrl+C to stop.", args.mode, symbol, timeframe)
    while True:
        try:
            latest = fetch_ohlcv_closed(exchange, symbol, timeframe, limit=2)
            new_closed = latest[latest.index > window.index[-1]]
            if not new_closed.empty:
                window = pd.concat([window, new_closed]).iloc[-config.backtest.window_size :]
                bar = new_closed.iloc[-1]
                ts = new_closed.index[-1]

                fill = broker.check_stop_and_target(symbol, bar, ts)
                if fill is not None:
                    risk_manager.register_close()
                    logger.info("Position closed: %s @ %.4f (%s)", fill.side.value, fill.price, fill.reason)

                if pending is not None:
                    pending_bars_left -= 1
                    touched = bar["low"] <= pending.entry <= bar["high"]
                    filled = False
                    if touched and broker.get_open_position(symbol) is None:
                        amount = risk_manager.position_size(broker.get_balance(), pending.entry, pending.stop_loss)
                        if amount > 0:
                            broker.open_position(symbol, pending.side, amount, pending.entry, pending.stop_loss, pending.take_profit, ts)
                            risk_manager.register_open()
                            logger.info("Entered %s @ %.4f (SL %.4f / TP %.4f) - %s", pending.side.value, pending.entry, pending.stop_loss, pending.take_profit, pending.reason)
                            filled = True
                    # Match the backtest engine: a touch that couldn't be sized
                    # (e.g. balance too low) keeps the order pending until it
                    # genuinely expires, instead of discarding it early.
                    if filled:
                        pending = None
                    elif pending_bars_left <= 0:
                        logger.info("Pending signal expired unfilled: %s", pending.reason)
                        pending = None

                status_reason: str | None = None
                if pending is None and broker.get_open_position(symbol) is None:
                    if risk_manager.can_open_trade(ts, broker.get_balance()):
                        trace: list[str] = []
                        signal = strategy.generate_signal(window, trace=trace)
                        if signal is not None:
                            logger.info("New signal: %s entry=%.4f sl=%.4f tp=%.4f (%s)", signal.side.value, signal.entry, signal.stop_loss, signal.take_profit, signal.reason)
                            pending = signal
                            pending_bars_left = config.backtest.pending_order_expiry_bars
                            last_status_log = ts  # something happened - restart the quiet-period clock
                        else:
                            status_reason = trace[-1] if trace else "no signal"
                    else:
                        status_reason = "blocked: daily loss limit reached or max open positions"
                elif pending is not None:
                    status_reason = f"pending order waiting to fill (expires in {pending_bars_left} bars): {pending.reason}"
                else:
                    open_position = broker.get_open_position(symbol)
                    status_reason = f"position open: {open_position.side.value} @ {open_position.entry_price:.4f}"

                if status_reason is not None and (last_status_log is None or ts - last_status_log >= status_interval):
                    logger.info("Status: no trade yet - %s", status_reason)
                    last_status_log = ts

            time.sleep(config.live.poll_interval_seconds)
        except KeyboardInterrupt:
            logger.info("Stopping live loop.")
            break
        except Exception:
            logger.exception("Error in live loop, retrying after backoff.")
            time.sleep(min(60, config.live.poll_interval_seconds * 4))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ict_bot", description="ICT-style trading bot")
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="Run a historical backtest")
    bt.add_argument("--config", default="config/config.yaml")
    bt.add_argument("--csv", default=None, help="Load OHLCV from a CSV file instead of the exchange")
    bt.add_argument("--since", default=None, help="ISO date to start history from, e.g. 2024-01-01")
    bt.set_defaults(func=cmd_backtest)

    live = sub.add_parser("live", help="Run the bot against live/streaming data")
    live.add_argument("--config", default="config/config.yaml")
    live.add_argument("--mode", choices=["paper", "live"], default="paper")
    live.set_defaults(func=cmd_live)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
