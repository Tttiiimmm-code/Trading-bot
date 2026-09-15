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
