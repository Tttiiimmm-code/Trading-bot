"""Take-profit placement modes."""
from ict_bot.ict.liquidity import LiquidityPool
from ict_bot.strategy.ict_strategy import ICTStrategyConfig, Side, _take_profit


def _pool(price: float, kind: str) -> LiquidityPool:
    return LiquidityPool(price=price, kind=kind, touches=())


def test_liquidity_mode_uses_the_pool():
    cfg = ICTStrategyConfig(take_profit_mode="liquidity")
    assert _take_profit(cfg, Side.LONG, entry=100.0, stop_loss=95.0, target_pool=_pool(130.0, "buy_side")) == 130.0


def test_liquidity_mode_needs_a_pool():
    cfg = ICTStrategyConfig(take_profit_mode="liquidity")
    assert _take_profit(cfg, Side.LONG, entry=100.0, stop_loss=95.0, target_pool=None) is None


def test_fixed_r_ignores_the_pool_entirely():
    cfg = ICTStrategyConfig(take_profit_mode="fixed_r", take_profit_r=2.0)
    # risk = 5, so a 2R target is 10 above entry
    assert _take_profit(cfg, Side.LONG, entry=100.0, stop_loss=95.0, target_pool=None) == 110.0
    assert _take_profit(cfg, Side.LONG, entry=100.0, stop_loss=95.0, target_pool=_pool(130.0, "buy_side")) == 110.0


def test_fixed_r_mirrors_for_shorts():
    cfg = ICTStrategyConfig(take_profit_mode="fixed_r", take_profit_r=2.0)
    assert _take_profit(cfg, Side.SHORT, entry=100.0, stop_loss=105.0, target_pool=None) == 90.0


def test_capped_mode_takes_whichever_is_nearer():
    cfg = ICTStrategyConfig(take_profit_mode="capped", take_profit_r=2.0)
    # pool at 130 is further than the 2R cap (110) -> capped
    assert _take_profit(cfg, Side.LONG, entry=100.0, stop_loss=95.0, target_pool=_pool(130.0, "buy_side")) == 110.0
    # pool at 104 is nearer than the cap -> keep the real level
    assert _take_profit(cfg, Side.LONG, entry=100.0, stop_loss=95.0, target_pool=_pool(104.0, "buy_side")) == 104.0


def test_capped_mode_mirrors_for_shorts():
    cfg = ICTStrategyConfig(take_profit_mode="capped", take_profit_r=2.0)
    assert _take_profit(cfg, Side.SHORT, entry=100.0, stop_loss=105.0, target_pool=_pool(70.0, "sell_side")) == 90.0
    assert _take_profit(cfg, Side.SHORT, entry=100.0, stop_loss=105.0, target_pool=_pool(96.0, "sell_side")) == 96.0


def test_capped_mode_still_needs_a_pool():
    cfg = ICTStrategyConfig(take_profit_mode="capped", take_profit_r=2.0)
    assert _take_profit(cfg, Side.LONG, entry=100.0, stop_loss=95.0, target_pool=None) is None


def test_liquidity_is_the_default():
    assert ICTStrategyConfig().take_profit_mode == "liquidity"


def test_fixed_r_target_produces_exactly_that_risk_reward():
    from ict_bot.strategy.ict_strategy import ICTStrategy, ICTStrategyConfig as C
    from tests.test_ict_strategy import _long_setup_df

    cfg = C(require_kill_zone=True, require_ote=True, min_risk_reward=1.0,
            take_profit_mode="fixed_r", take_profit_r=1.5)
    signal = ICTStrategy(cfg).generate_signal(_long_setup_df())
    assert signal is not None
    assert round(signal.risk_reward, 6) == 1.5
