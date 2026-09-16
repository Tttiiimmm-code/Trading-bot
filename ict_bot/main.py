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
from ict_bot.config import AppConfig, build_strategy, load_config
from ict_bot.data.feed import fetch_ohlcv_closed, fetch_ohlcv_history, load_ohlcv_csv, make_exchange
from ict_bot.execution.broker import Broker
from ict_bot.execution.ccxt_broker import CCXTBroker, quote_currency_from_symbol
from ict_bot.execution.paper import PaperBroker
from ict_bot.execution.portfolio import PortfolioBoard
from ict_bot.execution.state import restore_state, save_state
from ict_bot.strategy.ict_strategy import Signal
from ict_bot.strategy.risk import RiskManager
from ict_bot.utils.journal import TradeJournal
from ict_bot.utils.logger import setup_logger

logger = setup_logger()


def cmd_backtest(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    strategy = build_strategy(config)
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
        maker_fee_pct=config.backtest.maker_fee_pct,
        taker_fee_pct=config.backtest.taker_fee_pct,
        stop_slippage_pct=config.backtest.stop_slippage_pct,
        trail_on=config.backtest.trail_on,
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


def fetch_new_closed_bars(exchange, symbol: str, timeframe: str, last_seen: pd.Timestamp,
                          tf_delta: pd.Timedelta, window_size: int) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Closed bars after ``last_seen``, with no holes.

    The poll only asks for the last couple of candles, which is enough
    while the loop keeps up. It does not while the exchange is unreachable:
    the loop retries, and when it recovers the short response no longer
    reaches back to where we left off. Appending it anyway would splice a
    gap into the window, and the indicators - Donchian channel, ATR, the
    regime average - work positionally, so a "20-bar channel" would quietly
    span more than 20 bars of real time with nothing in the log to say so.

    Returns the bars to process and, when the outage outlasted the whole
    window, a replacement window (the old one is too stale to extend).
    """
    latest = fetch_ohlcv_closed(exchange, symbol, timeframe, limit=2)
    new = latest[latest.index > last_seen]
    if new.empty or new.index[0] <= last_seen + tf_delta:
        return new, None

    missed = max(0, int((new.index[0] - last_seen) / tf_delta) - 1)
    logger.warning("Missed %d closed bar(s) on %s %s (last seen %s); refetching to close the gap.",
                   missed, symbol, timeframe, last_seen)
    full = fetch_ohlcv_closed(exchange, symbol, timeframe, limit=window_size)
    caught_up = full[full.index > last_seen]
    if not caught_up.empty and caught_up.index[0] <= last_seen + tf_delta:
        return caught_up, None

    logger.warning("Outage outlasted the %d-bar window; restarting from fresh history.", window_size)
    return full.iloc[[-1]], full


def cmd_live(args: argparse.Namespace) -> None:
    config: AppConfig = load_config(args.config)
    strategy = build_strategy(config)
    risk_manager = RiskManager(config.risk)
    symbol = config.market.symbol
    timeframe = config.market.timeframe

    exchange = make_exchange(config.exchange.id, config.exchange.api_key, config.exchange.api_secret, sandbox=config.exchange.sandbox)

    broker: Broker
    if args.mode == "live":
        if not config.exchange.sandbox:
            logger.warning("LIVE mode with sandbox=false: this will place REAL orders with REAL funds on %s.", config.exchange.id)
        broker = CCXTBroker(exchange, quote_currency=quote_currency_from_symbol(symbol),
                            use_native_sl_tp=config.live.use_native_sl_tp, bot_id=config.live.bot_id,
                            trail_on=config.backtest.trail_on)
        # On a shared account someone else's stop can close a position this
        # bot believes it controls. Say so once, loudly, rather than let it
        # look like an exit the strategy chose.
        foreign = broker.find_foreign_orders(symbol)
        if foreign:
            logger.warning(
                "%d order(s) on %s were not placed by this bot (id %r). If another bot or a manual order "
                "is managing the same symbol, its stop can close a position this one thinks it owns. "
                "Order ids: %s", len(foreign), symbol, config.live.bot_id,
                ", ".join(str(o.get("id")) for o in foreign[:10]))
    else:
        starting_balance = config.backtest.starting_balance
        logger.info("Paper trading mode: simulated balance %.2f", starting_balance)
        broker = PaperBroker(starting_balance,
                             maker_fee_pct=config.backtest.maker_fee_pct,
                             taker_fee_pct=config.backtest.taker_fee_pct,
                             stop_slippage_pct=config.backtest.stop_slippage_pct,
                             trail_on=config.backtest.trail_on)

    # Pick up where a previous process left off. Without this a restart -
    # systemd's Restart=always, a reboot, a git pull - comes back believing
    # it is flat, resetting the paper balance and, live, abandoning a real
    # open position while opening a second one.
    journal = TradeJournal(config.live.trade_log) if config.live.trade_log else None
    if config.live.state_file and restore_state(config.live.state_file, broker, risk_manager, symbol, args.mode):
        if journal is not None:
            journal.skip(len(pair_trades(broker.fills)))

    # Instances know nothing about each other, so without this board a
    # market per instance means the total at risk is however many bots
    # happen to be running times their individual risk.
    board = PortfolioBoard(config.portfolio.board_file, config.portfolio.stale_after_minutes) \
        if config.portfolio.max_open_positions > 0 else None

    def persist() -> None:
        if config.live.state_file:
            save_state(config.live.state_file, broker, risk_manager, symbol, args.mode)
        if board is not None:
            position = broker.get_open_position(symbol)
            board.publish(config.live.bot_id, [position] if position else [], pd.Timestamp.now("UTC"))
        if journal is not None:
            # get_balance() is a network call on the live broker, so only
            # reach for it when there is actually a trade to write.
            journal.catch_up(pair_trades(broker.fills), broker.get_balance)

    window_size = max(config.backtest.window_size, 100)
    tf_delta = pd.Timedelta(seconds=exchange.parse_timeframe(timeframe))
    window = fetch_ohlcv_closed(exchange, symbol, timeframe, limit=window_size)
    while window.empty:
        # Starting before the exchange returns anything would leave the loop
        # dereferencing window.index[-1] forever, retrying a window it never
        # refetches.
        logger.warning("No closed %s candles for %s yet; retrying in %ds.",
                       timeframe, symbol, config.live.poll_interval_seconds)
        time.sleep(config.live.poll_interval_seconds)
        window = fetch_ohlcv_closed(exchange, symbol, timeframe, limit=window_size)

    pending: Signal | None = None
    pending_bars_left = 0
    last_status_log: pd.Timestamp | None = None
    status_interval = pd.Timedelta(minutes=config.live.status_log_interval_minutes)

    logger.info("Starting live loop (%s) on %s %s using the %s strategy. Ctrl+C to stop.",
                args.mode, symbol, timeframe, config.strategy_type)
    while True:
        try:
            new_closed, replacement = fetch_new_closed_bars(
                exchange, symbol, timeframe, window.index[-1], tf_delta, window_size)
            if replacement is not None:
                window = replacement.iloc[:-1]
            # Every new bar is processed, in order. Taking only the newest
            # would skip the stop check on the others - and a poll can
            # legitimately return two at once, quite apart from outages.
            for ts, bar in new_closed.iterrows():
                window = pd.concat([window, new_closed.loc[[ts]]]).iloc[-window_size:]

                fill = broker.check_stop_and_target(symbol, bar, ts)
                if fill is not None:
                    risk_manager.register_close()
                    logger.info("Position closed: %s @ %.4f (%s)", fill.side.value, fill.price, fill.reason)
                persist()  # also captures a stop trailed by the bar just closed

                if pending is not None:
                    pending_bars_left -= 1
                    touched = bar["low"] <= pending.entry <= bar["high"]
                    filled = False
                    if touched and broker.get_open_position(symbol) is None:
                        amount = risk_manager.position_size(broker.get_balance(), pending.entry, pending.stop_loss)
                        if amount > 0:
                            broker.open_position(symbol, pending.side, amount, pending.entry, pending.stop_loss,
                                                 pending.take_profit, ts, trail_distance=pending.trail_distance)
                            risk_manager.register_open()
                            target = f"{pending.take_profit:.4f}" if pending.take_profit is not None else "trailing"
                            logger.info("Entered %s @ %.4f (SL %.4f / TP %s) - %s", pending.side.value, pending.entry, pending.stop_loss, target, pending.reason)
                            persist()
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
                    allowed, held = (True, 0) if board is None else board.can_open(
                        config.live.bot_id, config.portfolio.max_open_positions, pd.Timestamp.now("UTC"))
                    if not allowed:
                        status_reason = (f"portfolio cap reached: {held} position(s) open across all instances "
                                         f"(limit {config.portfolio.max_open_positions})")
                    elif risk_manager.can_open_trade(ts, broker.get_balance()):
                        trace: list[str] = []
                        signal = strategy.generate_signal(window, trace=trace)
                        if signal is not None:
                            tp = f"{signal.take_profit:.4f}" if signal.take_profit is not None else "trailing"
                            logger.info("New signal: %s entry=%.4f sl=%.4f tp=%s (%s)", signal.side.value, signal.entry, signal.stop_loss, tp, signal.reason)
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
