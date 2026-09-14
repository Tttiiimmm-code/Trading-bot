from ict_bot.ict.liquidity import find_liquidity_pools, recent_sweep
from tests.conftest import zigzag_rows, rows_to_df


def test_equal_lows_form_sell_side_pool_and_get_swept():
    rows = zigzag_rows([95, 90, 93, 90.03, 92], bars_per_leg=3)
    rows.append((92.0, 92.3, 89.5, 91.5))  # sweep: wicks below ~90 pool, closes back above
    rows.extend(zigzag_rows([91.5, 96], bars_per_leg=3)[1:])
    df = rows_to_df(rows)

    pools = find_liquidity_pools(df, tolerance_pct=0.1, min_touches=2, left=2, right=2)
    sell_side = [p for p in pools if p.kind == "sell_side"]
    assert sell_side
    pool = sell_side[0]
    # pivots are close prices; the swing-low fractal is detected on the low
    # column, which sits `epsilon` (default 0.3) below the close pivot.
    assert 89.6 < pool.price < 89.8
    assert pool.swept
    assert pool.swept_at == df.index[len(zigzag_rows([95, 90, 93, 90.03, 92], bars_per_leg=3))]


def test_recent_sweep_respects_lookback_window():
    rows = zigzag_rows([95, 90, 93, 90.03, 92], bars_per_leg=3)
    sweep_i = len(rows)
    rows.append((92.0, 92.3, 89.5, 91.5))
    rows.extend(zigzag_rows([91.5, 96], bars_per_leg=3)[1:])
    df = rows_to_df(rows)
    pools = find_liquidity_pools(df, tolerance_pct=0.1, min_touches=2, left=2, right=2)

    as_of = df.index[-1]
    assert recent_sweep(pools, as_of=as_of, within_bars=30, df=df) is not None
    assert recent_sweep(pools, as_of=as_of, within_bars=1, df=df) is None


def test_no_pool_when_lows_are_not_equal():
    rows = zigzag_rows([100, 90, 95, 70, 96], bars_per_leg=3)  # very different lows
    df = rows_to_df(rows)
    pools = find_liquidity_pools(df, tolerance_pct=0.05, min_touches=2, left=2, right=2)
    assert pools == []
