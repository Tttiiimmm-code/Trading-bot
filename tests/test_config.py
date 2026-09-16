import textwrap

import pytest

from ict_bot.config import load_config
from ict_bot.ict.killzones import KILL_ZONE_PRESETS

MINIMAL = """
exchange:
  id: binance
  sandbox: true
market:
  symbol: "BTC/USDT"
  timeframe: "15m"
"""


def _write(tmp_path, extra: str = "") -> str:
    path = tmp_path / "config.yaml"
    path.write_text(MINIMAL + textwrap.dedent(extra))
    return str(path)


def test_defaults_when_strategy_section_is_absent(tmp_path):
    config = load_config(_write(tmp_path), env_path=str(tmp_path / "missing.env"))
    assert config.strategy.htf_bias_timeframe is None
    assert config.strategy.use_session_liquidity is False
    assert config.strategy.entry_mode == "ob_midpoint"
    assert config.strategy.kill_zones == KILL_ZONE_PRESETS["default"]
    assert config.live.status_log_interval_minutes == 60


def test_loads_the_new_strategy_options(tmp_path):
    config = load_config(_write(tmp_path, """
        strategy:
          htf_bias_timeframe: "4h"
          htf_bias_allow_unknown: false
          use_session_liquidity: true
          session_liquidity_rules: ["1D"]
          entry_mode: "fvg_ce"
          kill_zone_preset: "silver_bullet"
        """), env_path=str(tmp_path / "missing.env"))

    assert config.strategy.htf_bias_timeframe == "4h"
    assert config.strategy.htf_bias_allow_unknown is False
    assert config.strategy.use_session_liquidity is True
    assert config.strategy.session_liquidity_rules == ("1D",)
    assert config.strategy.entry_mode == "fvg_ce"
    assert config.strategy.kill_zones == KILL_ZONE_PRESETS["silver_bullet"]


def test_unknown_kill_zone_preset_is_rejected_with_a_useful_message(tmp_path):
    path = _write(tmp_path, """
        strategy:
          kill_zone_preset: "nonsense"
        """)
    with pytest.raises(ValueError, match="unknown kill_zone_preset"):
        load_config(path, env_path=str(tmp_path / "missing.env"))


def test_defaults_to_the_ict_strategy(tmp_path):
    from ict_bot.config import build_strategy
    from ict_bot.strategy.ict_strategy import ICTStrategy

    config = load_config(_write(tmp_path), env_path=str(tmp_path / "missing.env"))
    assert config.strategy_type == "ict"
    assert isinstance(build_strategy(config), ICTStrategy)


def test_selects_the_trend_strategy_and_its_options(tmp_path):
    from ict_bot.config import build_strategy
    from ict_bot.strategy.trend_strategy import TrendStrategy

    config = load_config(_write(tmp_path, """
        strategy:
          type: "trend"
          trend:
            entry_period: 55
            atr_stop_multiple: 3.0
            allow_short: false
    """), env_path=str(tmp_path / "missing.env"))

    assert config.strategy_type == "trend"
    assert config.trend.entry_period == 55
    assert config.trend.atr_stop_multiple == 3.0
    assert config.trend.allow_short is False
    assert config.trend.trail_atr_multiple == 3.0  # untouched default
    strategy = build_strategy(config)
    assert isinstance(strategy, TrendStrategy)
    assert strategy.config.entry_period == 55


def test_an_unknown_strategy_type_is_rejected(tmp_path):
    path = _write(tmp_path, """
        strategy:
          type: "wyckoff"
    """)
    with pytest.raises(ValueError, match="unknown strategy type"):
        load_config(path, env_path=str(tmp_path / "missing.env"))


def test_an_unusable_window_size_is_caught_at_load_time(tmp_path):
    # Without this the bot starts happily and simply never trades, logging
    # "not enough bar history" into a file nobody reads for a fortnight.
    path = _write(tmp_path, """
        strategy:
          type: "trend"
          trend:
            regime_period: 500
        backtest:
          window_size: 300
    """)
    with pytest.raises(ValueError, match="window_size is 300"):
        load_config(path, env_path=str(tmp_path / "missing.env"))


def test_both_directions_disabled_is_caught(tmp_path):
    path = _write(tmp_path, """
        strategy:
          type: "trend"
          trend:
            allow_long: false
            allow_short: false
    """)
    with pytest.raises(ValueError, match="could never trade"):
        load_config(path, env_path=str(tmp_path / "missing.env"))


def test_an_impossible_risk_percentage_is_caught(tmp_path):
    path = _write(tmp_path, """
        risk:
          risk_per_trade_pct: 0
    """)
    with pytest.raises(ValueError, match="risk_per_trade_pct is 0"):
        load_config(path, env_path=str(tmp_path / "missing.env"))


def test_a_target_below_the_risk_reward_floor_is_caught(tmp_path):
    # take_profit_mode "fixed_r" caps the reward, so a target below the
    # floor means no signal can ever pass - a silent no-trade bot.
    path = _write(tmp_path, """
        strategy:
          min_risk_reward: 3.0
          take_profit_mode: "fixed_r"
          take_profit_r: 2.0
    """)
    with pytest.raises(ValueError, match="below strategy.min_risk_reward"):
        load_config(path, env_path=str(tmp_path / "missing.env"))


def test_every_problem_is_reported_at_once(tmp_path):
    # One error per run would mean fixing a config file by trial and error.
    path = _write(tmp_path, """
        risk:
          risk_per_trade_pct: 0
          max_open_positions: 0
        live:
          poll_interval_seconds: 0
    """)
    with pytest.raises(ValueError) as excinfo:
        load_config(path, env_path=str(tmp_path / "missing.env"))
    message = str(excinfo.value)
    assert "risk_per_trade_pct" in message
    assert "max_open_positions" in message
    assert "poll_interval_seconds" in message


def test_negative_costs_are_refused(tmp_path):
    path = _write(tmp_path, """
        backtest:
          taker_fee_pct: -0.05
    """)
    with pytest.raises(ValueError, match="pay you to trade"):
        load_config(path, env_path=str(tmp_path / "missing.env"))


def test_the_default_bot_id_is_derived_from_the_config_filename(tmp_path):
    # Two instances on one exchange account must not share a tag, or the
    # shared-account check cannot tell their orders apart.
    path = tmp_path / "config-trend-btc.yaml"
    path.write_text(MINIMAL)
    config = load_config(str(path), env_path=str(tmp_path / "missing.env"))
    assert config.live.bot_id == "config-trend-btc"


def test_a_filename_an_exchange_would_reject_is_cleaned_up(tmp_path):
    # Binance allows only [A-Za-z0-9_-] in a client order id, and
    # "config.example.yaml" has a dot in its stem.
    path = tmp_path / "config.example.yaml"
    path.write_text(MINIMAL)
    config = load_config(str(path), env_path=str(tmp_path / "missing.env"))
    assert config.live.bot_id == "config-example"


def test_an_unusable_bot_id_is_caught_at_load_time(tmp_path):
    with pytest.raises(ValueError, match="live.bot_id"):
        load_config(_write(tmp_path, """
            live:
              bot_id: "my bot!"
        """), env_path=str(tmp_path / "missing.env"))
