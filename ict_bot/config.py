"""Load config/config.yaml (+ .env for secrets) into typed config objects."""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv

from ict_bot.ict.killzones import KILL_ZONE_PRESETS
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
    maker_fee_pct: float
    taker_fee_pct: float
    stop_slippage_pct: float


@dataclass
class LiveConfig:
    poll_interval_seconds: int
    use_native_sl_tp: bool
    status_log_interval_minutes: int


@dataclass
class AppConfig:
    exchange: ExchangeConfig
    market: MarketConfig
    strategy: ICTStrategyConfig
    risk: RiskConfig
    backtest: BacktestConfig
    live: LiveConfig


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
    )

    l = raw.get("live", {})
    live = LiveConfig(
        poll_interval_seconds=l.get("poll_interval_seconds", 30),
        use_native_sl_tp=l.get("use_native_sl_tp", True),
        status_log_interval_minutes=l.get("status_log_interval_minutes", 60),
    )

    return AppConfig(exchange=exchange, market=market, strategy=strategy, risk=risk, backtest=backtest, live=live)
