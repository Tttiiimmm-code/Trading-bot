"""Load config/config.yaml (+ .env for secrets) into typed config objects."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv

from ict_bot.execution.ccxt_broker import BOT_ID_PATTERN, MAX_BOT_ID
from ict_bot.ict.killzones import KILL_ZONE_PRESETS
from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig
from ict_bot.strategy.risk import RiskConfig
from ict_bot.strategy.trend_strategy import TrendStrategy, TrendStrategyConfig


@dataclass
class ExchangeConfig:
    id: str
    sandbox: bool
    api_key: str
    api_secret: str


@dataclass
class MarketConfig:
    symbol: str
    timeframe: str


@dataclass
class BacktestConfig:
    starting_balance: float
    window_size: int
    pending_order_expiry_bars: int
    history_bars: int
    maker_fee_pct: float
    taker_fee_pct: float
    stop_slippage_pct: float
    trail_on: str


@dataclass
class LiveConfig:
    poll_interval_seconds: int
    use_native_sl_tp: bool
    status_log_interval_minutes: int
    # Where the live loop keeps its state across restarts, and the
    # append-only CSV of closed trades. Both default to a name derived
    # from the config filename so instances cannot collide. Empty string
    # disables that half.
    state_file: str
    trade_log: str
    # Stamped onto every live order so several bots can share one exchange
    # account and still tell their orders apart. Defaults to the config
    # filename. Empty disables tagging.
    bot_id: str


@dataclass
class PortfolioConfig:
    # Cap on positions held across ALL instances sharing this file.
    # 0 disables the shared cap (each instance then limits only itself).
    max_open_positions: int
    board_file: str
    stale_after_minutes: float


@dataclass
class AppConfig:
    exchange: ExchangeConfig
    market: MarketConfig
    strategy: ICTStrategyConfig
    trend: TrendStrategyConfig
    risk: RiskConfig
    backtest: BacktestConfig
    live: LiveConfig
    portfolio: PortfolioConfig
    strategy_type: str = "ict"


STRATEGY_TYPES = ("ict", "trend")


def build_strategy(config: AppConfig):
    """Return the strategy object the config asks for.

    Both strategies expose the same ``generate_signal(df, trace=...)``
    interface, so everything downstream - engine, brokers, live loop -
    stays the same whichever one is selected.
    """
    if config.strategy_type == "trend":
        return TrendStrategy(config.trend)
    return ICTStrategy(config.strategy)


def _validate(config: AppConfig) -> None:
    """Catch settings that would leave the bot silently unable to trade.

    A typo in a YAML file is otherwise only visible as a bot that never
    takes a trade - which looks exactly like a quiet market. Every message
    here says what is wrong and what the number needs to be, because the
    person reading it is looking at a config file, not at this source.
    """
    problems: list[str] = []

    if not 0 < config.risk.risk_per_trade_pct <= 10:
        problems.append(
            f"risk.risk_per_trade_pct is {config.risk.risk_per_trade_pct}; usual values are 0.5 to 2, "
            f"and it must be above 0 or no position can ever be sized")
    if config.risk.max_open_positions < 1:
        problems.append(f"risk.max_open_positions is {config.risk.max_open_positions}; it must be at least 1")
    if config.risk.max_daily_loss_pct <= 0:
        problems.append(f"risk.max_daily_loss_pct is {config.risk.max_daily_loss_pct}; "
                        f"use a large number such as 100 to disable it, not 0")
    for name, value in (("maker_fee_pct", config.backtest.maker_fee_pct),
                        ("taker_fee_pct", config.backtest.taker_fee_pct),
                        ("stop_slippage_pct", config.backtest.stop_slippage_pct)):
        if value < 0:
            problems.append(f"backtest.{name} is {value}; a negative cost would pay you to trade")
    if config.live.bot_id and not BOT_ID_PATTERN.match(config.live.bot_id):
        problems.append(
            f"live.bot_id is {config.live.bot_id!r}; it must be 1-{MAX_BOT_ID} characters of letters, digits, "
            f"'-' or '_'. Exchanges reject anything else in a client order id, so every order would fail")
    if config.portfolio.max_open_positions < 0:
        problems.append(f"portfolio.max_open_positions is {config.portfolio.max_open_positions}; "
                        f"use 0 to disable the shared cap, never a negative number")
    if config.portfolio.stale_after_minutes <= 0:
        problems.append(f"portfolio.stale_after_minutes is {config.portfolio.stale_after_minutes}; "
                        f"it must be above 0, and comfortably longer than one bar of the traded "
                        f"timeframe or live instances would drop off the board between bars")
    if config.backtest.trail_on not in ("close", "high"):
        problems.append(f"backtest.trail_on is {config.backtest.trail_on!r}; expected 'close' or 'high'")
    if config.live.poll_interval_seconds < 1:
        problems.append(f"live.poll_interval_seconds is {config.live.poll_interval_seconds}; it must be at least 1")

    if config.strategy_type == "trend":
        t = config.trend
        for name, value in (("entry_period", t.entry_period), ("atr_period", t.atr_period)):
            if value < 2:
                problems.append(f"strategy.trend.{name} is {value}; it must be at least 2")
        if t.atr_stop_multiple <= 0:
            problems.append(f"strategy.trend.atr_stop_multiple is {t.atr_stop_multiple}; it must be above 0 "
                            f"or the stop would sit on the entry price")
        if t.trail_atr_multiple <= 0:
            problems.append(f"strategy.trend.trail_atr_multiple is {t.trail_atr_multiple}; it must be above 0")
        if not (t.allow_long or t.allow_short):
            problems.append("strategy.trend.allow_long and allow_short are both false; the bot could never trade")
        # The strategy refuses to act until the window holds every
        # indicator's full lookback. Too small a window is invisible except
        # as a status line nobody reads for weeks.
        needed = max(t.entry_period, t.exit_period, t.atr_period, t.regime_period) + 2
        if config.backtest.window_size < needed:
            problems.append(
                f"backtest.window_size is {config.backtest.window_size}, but the trend settings need at least "
                f"{needed} bars (the longest lookback is {needed - 2}). The bot would never trade and would only "
                f"log 'not enough bar history'")
    else:
        if config.strategy.min_risk_reward <= 0:
            problems.append(f"strategy.min_risk_reward is {config.strategy.min_risk_reward}; it must be above 0")
        if config.strategy.take_profit_mode in ("fixed_r", "capped") and \
                config.strategy.take_profit_r < config.strategy.min_risk_reward:
            problems.append(
                f"strategy.take_profit_r ({config.strategy.take_profit_r}) is below strategy.min_risk_reward "
                f"({config.strategy.min_risk_reward}); with take_profit_mode "
                f"'{config.strategy.take_profit_mode}' the target caps the reward, so no signal could ever "
                f"clear the floor")

    if problems:
        raise ValueError("config problems:\n  - " + "\n  - ".join(problems))


def _default_bot_id(stem: str) -> str:
    """A config filename turned into something an exchange will accept as
    part of a client order id: letters, digits, '-' and '_' only. A
    filename like "config.example" has a dot in it, which Binance rejects.
    """
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", stem).strip("-")
    return cleaned[:MAX_BOT_ID] or "bot"


def _kill_zones(preset: str) -> list:
    try:
        return KILL_ZONE_PRESETS[preset]
    except KeyError:
        valid = ", ".join(sorted(KILL_ZONE_PRESETS))
        raise ValueError(f"unknown kill_zone_preset {preset!r}; expected one of: {valid}") from None


def load_config(path: str = "config/config.yaml", env_path: str = ".env") -> AppConfig:
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()

    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    exchange = ExchangeConfig(
        id=raw["exchange"]["id"],
        sandbox=bool(raw["exchange"].get("sandbox", True)),
        api_key=os.environ.get("EXCHANGE_API_KEY", ""),
        api_secret=os.environ.get("EXCHANGE_API_SECRET", ""),
    )
    market = MarketConfig(symbol=raw["market"]["symbol"], timeframe=raw["market"]["timeframe"])

    s = raw.get("strategy", {})
    strategy_type = s.get("type", "ict")
    if strategy_type not in STRATEGY_TYPES:
        raise ValueError(f"unknown strategy type {strategy_type!r}; expected one of: {', '.join(STRATEGY_TYPES)}")
    strategy = ICTStrategyConfig(
        swing_left=s.get("swing_left", 2),
        swing_right=s.get("swing_right", 2),
        liquidity_tolerance_pct=s.get("liquidity_tolerance_pct", 0.05),
        liquidity_min_touches=s.get("liquidity_min_touches", 2),
        sweep_lookback_bars=s.get("sweep_lookback_bars", 10),
        min_risk_reward=s.get("min_risk_reward", 2.0),
        require_kill_zone=s.get("require_kill_zone", True),
        require_ote=s.get("require_ote", True),
        ote_low_ratio=s.get("ote_low_ratio", 0.618),
        ote_high_ratio=s.get("ote_high_ratio", 0.79),
        kill_zones=_kill_zones(s.get("kill_zone_preset", "default")),
        htf_bias_timeframe=s.get("htf_bias_timeframe"),
        htf_bias_allow_unknown=s.get("htf_bias_allow_unknown", True),
        htf_swing_left=s.get("htf_swing_left", 2),
        htf_swing_right=s.get("htf_swing_right", 2),
        use_session_liquidity=s.get("use_session_liquidity", False),
        session_liquidity_rules=tuple(s.get("session_liquidity_rules", ("1D", "1W"))),
        entry_mode=s.get("entry_mode", "ob_midpoint"),
        take_profit_mode=s.get("take_profit_mode", "liquidity"),
        take_profit_r=s.get("take_profit_r", 2.0),
    )

    t = s.get("trend", {})
    trend = TrendStrategyConfig(
        entry_period=t.get("entry_period", 20),
        exit_period=t.get("exit_period", 10),
        atr_period=t.get("atr_period", 14),
        atr_stop_multiple=t.get("atr_stop_multiple", 2.0),
        trail_atr_multiple=t.get("trail_atr_multiple", 3.0),
        regime_period=t.get("regime_period", 100),
        allow_long=t.get("allow_long", True),
        allow_short=t.get("allow_short", True),
        min_atr_pct=t.get("min_atr_pct", 0.0),
    )

    pf = raw.get("portfolio", {})
    portfolio = PortfolioConfig(
        max_open_positions=int(pf.get("max_open_positions", 0)),
        board_file=pf.get("board_file", "state/portfolio.json"),
        stale_after_minutes=float(pf.get("stale_after_minutes", 30.0)),
    )

    r = raw.get("risk", {})
    risk = RiskConfig(
        risk_per_trade_pct=r.get("risk_per_trade_pct", 1.0),
        max_daily_loss_pct=r.get("max_daily_loss_pct", 3.0),
        max_open_positions=r.get("max_open_positions", 1),
    )

    b = raw.get("backtest", {})
    backtest = BacktestConfig(
        starting_balance=b.get("starting_balance", 10_000.0),
        window_size=b.get("window_size", 300),
        pending_order_expiry_bars=b.get("pending_order_expiry_bars", 8),
        history_bars=b.get("history_bars", 5000),
        maker_fee_pct=b.get("maker_fee_pct", 0.0),
        taker_fee_pct=b.get("taker_fee_pct", 0.0),
        stop_slippage_pct=b.get("stop_slippage_pct", 0.0),
        trail_on=str(b.get("trail_on", "close")).lower(),
    )

    # Default state/journal paths are derived from the config filename, so
    # instances started from different configs never share them. Sharing
    # would be silent and destructive: two bots writing one state file
    # would each restore the other's positions.
    stem = os.path.splitext(os.path.basename(path))[0]
    l = raw.get("live", {})
    live = LiveConfig(
        poll_interval_seconds=l.get("poll_interval_seconds", 30),
        use_native_sl_tp=l.get("use_native_sl_tp", True),
        status_log_interval_minutes=l.get("status_log_interval_minutes", 60),
        state_file=l.get("state_file", f"state/{stem}-state.json"),
        trade_log=l.get("trade_log", f"state/{stem}-trades.csv"),
        bot_id=str(l.get("bot_id", _default_bot_id(stem)))[:MAX_BOT_ID],
    )

    config = AppConfig(exchange=exchange, market=market, strategy=strategy, trend=trend, risk=risk,
                       backtest=backtest, live=live, portfolio=portfolio, strategy_type=strategy_type)
    _validate(config)
    return config
