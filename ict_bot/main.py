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
from ict_bot.data.feed import fetch_ohlcv, fetch_ohlcv_history, load_ohlcv_csv, make_exchange
from ict_bot.execution.broker import Broker
from ict_bot.execution.ccxt_broker import CCXTBroker
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
            since_ms = int(pd.Timestamp.utcnow().timestamp() * 1000) - tf_ms * config.backtest.history_bars
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


def _no_conflicting_position_exists(exchange, symbol: str) -> bool:
    """Guard against starting a live run while the exchange already has an
    open position this process has no local record of.

    CCXTBroker only tracks positions (and their protective SL/TP order
    ids) in local process memory - it never calls fetch_positions. If a
    previous run crashed or was restarted while a position was open, a
    fresh start would see get_open_position() as None, happily open a
    second, uncoordinated position on top of the existing one, and never
    be able to detect the first one's stop-loss/take-profit fills at all.

    Returns True if it's safe to proceed (flat, or the exchange doesn't
    support checking), False if an open position was found and the
    caller should refuse to start.
    """
    if not exchange.has.get("fetchPositions"):
        logger.warning(
            "%s does not support fetchPositions; cannot check for a pre-existing open position on startup.",
            exchange.id,
        )
        return True

    positions = exchange.fetch_positions([symbol])
    if any(abs(p.get("contracts") or 0) > 0 for p in positions):
        logger.error(
            "Refusing to start: %s already has an open position on the exchange that this process has no local "
            "record of (likely a crash or restart while a position was open). Starting fresh would risk opening a "
            "second, uncoordinated position on top of it and losing track of the first one's stop-loss/take-profit "
            "entirely. Close or otherwise resolve it manually before running live again.",
            symbol,
        )
        return False
    return True


def _process_closed_bar(
    broker: Broker,
    risk_manager: RiskManager,
    strategy: ICTStrategy,
    config: AppConfig,
    symbol: str,
    window: pd.DataFrame,
    pending: Signal | None,
    pending_bars_left: int,
    bar: pd.Series,
    ts: pd.Timestamp,
) -> tuple[Signal | None, int]:
    """Apply one confirmed-closed candle to the broker/strategy/risk state -
    stop/target check, pending-limit-order fill/expiry, then a fresh signal
    if nothing is open. Mirrors one iteration of the backtest engine's
    bar loop; must be called once per closed candle in chronological
    order (never only the most recent of several) or a stop-loss/take-
    profit hit or a limit-order fill on an earlier candle silently never
    gets detected.
    """
    fill = broker.check_stop_and_target(symbol, bar, ts)
    if fill is not None:
        risk_manager.register_close()
        logger.info("Position closed: %s @ %.4f (%s)", fill.side.value, fill.price, fill.reason)

    if pending is not None:
        pending_bars_left -= 1
        touched = bar["low"] <= pending.entry <= bar["high"]
        if touched and broker.get_open_position(symbol) is None:
            amount = risk_manager.position_size(broker.get_balance(), pending.entry, pending.stop_loss)
            if amount > 0:
                try:
                    broker.open_position(symbol, pending.side, amount, pending.entry, pending.stop_loss, pending.take_profit, ts)
                    risk_manager.register_open()
                    logger.info("Entered %s @ %.4f (SL %.4f / TP %.4f) - %s", pending.side.value, pending.entry, pending.stop_loss, pending.take_profit, pending.reason)
                    pending = None
                except Exception:
                    # Leave `pending` in place (already-decremented
                    # pending_bars_left included) rather than letting the
                    # exception propagate: that would abort this whole
                    # closed-candle loop AND discard the decrement/clear
                    # this function already computed, since the caller
                    # never gets this function's return value on a raise.
                    logger.exception("Failed to open position for %s at %s; will retry until the pending signal expires.", symbol, ts)
                    if pending_bars_left <= 0:
                        # Bound the retries to pending_order_expiry_bars
                        # even though this branch keeps re-entering (price
                        # still touches entry every bar) rather than the
                        # elif below, which a persistently touched price
                        # would never fall through to.
                        logger.info("Pending signal expired after repeated failures to open: %s", pending.reason)
                        pending = None
            else:
                pending = None
        elif pending_bars_left <= 0:
            logger.info("Pending signal expired unfilled: %s", pending.reason)
            pending = None

    if pending is None and broker.get_open_position(symbol) is None:
        if risk_manager.can_open_trade(ts, broker.get_balance()):
            signal = strategy.generate_signal(window)
            if signal is not None:
                logger.info("New signal: %s entry=%.4f sl=%.4f tp=%.4f (%s)", signal.side.value, signal.entry, signal.stop_loss, signal.take_profit, signal.reason)
                pending = signal
                pending_bars_left = config.backtest.pending_order_expiry_bars

    return pending, pending_bars_left


def cmd_live(args: argparse.Namespace) -> None:
    config: AppConfig = load_config(args.config)
    strategy = ICTStrategy(config.strategy)
    risk_manager = RiskManager(config.risk)
    symbol = config.market.symbol
    timeframe = config.market.timeframe

    exchange = make_exchange(config.exchange.id, config.exchange.api_key, config.exchange.api_secret, sandbox=config.exchange.sandbox)
    tf_ms = exchange.parse_timeframe(timeframe) * 1000

    broker: Broker
    if args.mode == "live":
        if not config.exchange.sandbox:
            logger.warning("LIVE mode with sandbox=false: this will place REAL orders with REAL funds on %s.", config.exchange.id)
        if not _no_conflicting_position_exists(exchange, symbol):
            return
        broker = CCXTBroker(exchange, symbol, use_native_sl_tp=config.live.use_native_sl_tp)
    else:
        starting_balance = config.backtest.starting_balance
        logger.info("Paper trading mode: simulated balance %.2f", starting_balance)
        broker = PaperBroker(starting_balance)

    window = fetch_ohlcv(exchange, symbol, timeframe, limit=max(config.backtest.window_size, 100))
    pending: Signal | None = None
    pending_bars_left = 0

    logger.info("Starting live loop (%s) on %s %s. Ctrl+C to stop.", args.mode, symbol, timeframe)
    while True:
        try:
            # since_ms + a generous limit (not the single/last-bar fetch this
            # used to be) so a poll after any delay - a slow response, the
            # exception backoff below, downtime - still picks up *every*
            # candle that closed in the gap, not just the newest one.
            since_ms = int(window.index[-1].timestamp() * 1000) + tf_ms
            latest = fetch_ohlcv(exchange, symbol, timeframe, since_ms=since_ms, limit=100)
            now = pd.Timestamp.now("UTC")
            # fetch_ohlcv can include the still-forming current candle;
            # only feed the strategy/risk logic candles whose interval has
            # actually elapsed.
            closed = latest[(latest.index > window.index[-1]) & (latest.index + pd.Timedelta(milliseconds=tf_ms) <= now)]

            for ts, bar in closed.iterrows():
                window = pd.concat([window, closed.loc[[ts]]]).iloc[-config.backtest.window_size :]
                pending, pending_bars_left = _process_closed_bar(
                    broker, risk_manager, strategy, config, symbol, window, pending, pending_bars_left, bar, ts
                )

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
