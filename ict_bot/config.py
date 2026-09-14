"""Load config/config.yaml (+ .env for secrets) into typed config objects."""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv

from ict_bot.ict.killzones import DEFAULT_KILL_ZONES
from ict_bot.strategy.ict_strategy import ICTStrategyConfig
from ict_bot.strategy.risk import RiskConfig


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


@dataclass
class LiveConfig:
    poll_interval_seconds: int
    use_native_sl_tp: bool


@dataclass
class AppConfig:
    exchange: ExchangeConfig
    market: MarketConfig
    strategy: ICTStrategyConfig
    risk: RiskConfig
    backtest: BacktestConfig
    live: LiveConfig


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
        kill_zones=DEFAULT_KILL_ZONES,
    )

    r = raw.get("risk", {})
    risk = RiskConfig(
        risk_per_trade_pct=r.get("risk_per_trade_pct", 1.0),
        max_daily_loss_pct=r.get("max_daily_loss_pct", 3.0),
        max_open_positions=r.get("max_open_positions", 1),
        max_position_pct=r.get("max_position_pct", 100.0),
    )

    b = raw.get("backtest", {})
    backtest = BacktestConfig(
        starting_balance=b.get("starting_balance", 10_000.0),
        window_size=b.get("window_size", 300),
        pending_order_expiry_bars=b.get("pending_order_expiry_bars", 8),
        history_bars=b.get("history_bars", 5000),
    )

    l = raw.get("live", {})
    live = LiveConfig(
        poll_interval_seconds=l.get("poll_interval_seconds", 30),
        use_native_sl_tp=l.get("use_native_sl_tp", True),
    )

    return AppConfig(exchange=exchange, market=market, strategy=strategy, risk=risk, backtest=backtest, live=live)
