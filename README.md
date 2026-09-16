# ICT Trading Bot

A Python trading bot that trades using ICT (Inner Circle Trader) concepts:
market structure (BOS/CHoCH), fair value gaps, order blocks, liquidity
sweeps, kill zones and premium/discount (OTE). It can backtest a strategy
against historical data and run paper or live trading against any
exchange supported by [ccxt](https://github.com/ccxt/ccxt) (Binance by
default, configurable).

**This is not financial advice and comes with no guarantee of
profitability.** Trading carries real risk of loss. Always start with
`live --mode paper` and/or an exchange testnet before risking real funds,
and never risk more than you can afford to lose.

## How it trades: the model

This implements a simplified version of ICT's "2022 model":

1. **Kill zone** - only look for entries inside a session window
   (Asian/London/NY AM/London Close, UTC) where ICT expects the real
   directional move.
2. **Liquidity sweep** - require sell-side liquidity (equal lows) to be
   swept for longs, or buy-side liquidity (equal highs) for shorts - the
   stop-hunt that is expected to fuel the reversal.
3. **Change of Character (CHoCH)** - require a confirmed structure
   reversal right after the sweep. Break of Structure (BOS) is tracked
   for trend context but is not traded as a fresh entry in this model.
4. **Order block / FVG entry zone** - locate the order block (and fair
   value gap, if any) left behind by the displacement leg that caused the
   CHoCH.
5. **Optimal Trade Entry (OTE)** - require that entry zone to overlap the
   61.8%-79% Fibonacci retracement of the displacement leg.
6. **Risk** - stop loss beyond the swept liquidity; take profit at the
   next real resting liquidity pool (never a synthesized target), subject
   to a minimum risk/reward floor.

Every step is tunable (or can be disabled) via `ICTStrategyConfig` in
`config.yaml`. See `ict_bot/strategy/ict_strategy.py` for the exact logic.

## Project layout

```
ict_bot/
  ict/            Pure detectors, each independently testable:
                   structure (BOS/CHoCH), fvg, order_blocks, liquidity,
                   killzones, premium_discount (OTE), htf (higher-timeframe
                   bias), session_levels (PDH/PDL/PWH/PWL + opens),
                   breakers, opening_gaps (NDOG/NWOG), smt (divergence
                   against a correlated market).
  strategy/       ict_strategy.py wires the detectors into entry signals;
                   risk.py does position sizing + daily loss circuit breaker.
  data/           OHLCV fetching via ccxt, or from a local CSV.
  execution/      Broker abstraction: PaperBroker (sim) and CCXTBroker (live).
  backtest/       Bar-by-bar backtest engine + performance metrics.
  config.py       Loads config/config.yaml (+ .env for API keys).
  main.py         CLI entry point (backtest / live).
scripts/          compare_strategies.py: train/test comparison of strategy
                   variants against real history.
deploy/           cloud-init + systemd units for running it on a VPS.
tests/            pytest suite, including a hand-crafted end-to-end
                   confluence scenario (sweep -> CHoCH -> OB -> OTE -> Signal).
```

Detectors beyond the core six are **off by default** - each is a config
flag, so enabling one is a deliberate choice you can measure rather than
something that silently changes how the bot trades. See the commented
options in `config/config.example.yaml`.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config/config.example.yaml config/config.yaml
cp .env.example .env   # fill in EXCHANGE_API_KEY / EXCHANGE_API_SECRET if trading live
```

Edit `config/config.yaml`: set `exchange.id` to any ccxt exchange id
(`binance`, `bybit`, `kraken`, ...), the `market.symbol` /
`market.timeframe` to trade, and tune the `strategy`/`risk` sections.

## Backtesting

```bash
python -m ict_bot.main backtest --config config/config.yaml
# or against a local CSV (columns: timestamp, open, high, low, close, volume)
python -m ict_bot.main backtest --config config/config.yaml --csv path/to/data.csv
```

Prints trade count, win rate, profit factor, total return and max
drawdown. The backtest engine treats a signal's entry price as a resting
limit order: it fills on the first future bar that trades through the
order block, and expires unfilled after
`backtest.pending_order_expiry_bars` bars.

## Paper trading (no real orders)

```bash
python -m ict_bot.main live --config config/config.yaml --mode paper
```

Polls the exchange for new closed candles on the configured interval and
runs the exact same strategy/risk code as the backtest, but against a
simulated balance - nothing is sent to the exchange.

Real signals can be rare (the full kill-zone/sweep/CHoCH/OTE confluence
doesn't line up often), so every `live.status_log_interval_minutes`
(default 60) the bot logs a `Status: no trade yet - ...` line explaining
exactly what's currently blocking a trade (outside kill zone, no sweep
yet, risk/reward too low, a pending order still waiting to fill, etc.) -
so a quiet log doesn't have to mean "is this even working".

## Live trading (real orders)

```bash
python -m ict_bot.main live --config config/config.yaml --mode live
```

Keep `exchange.sandbox: true` in `config.yaml` until you've validated
behaviour on the exchange's testnet. Live orders are placed at market
once price trades through the signal's entry zone; protective stop-loss
and take-profit orders are attempted natively where the exchange supports
them (`live.use_native_sl_tp`), with a polling fallback otherwise.
Conditional-order behaviour varies a lot between exchanges - test
thoroughly before using real funds.

## Tests

```bash
pip install -r requirements.txt
pytest
```

The suite unit-tests every ICT detector against hand-built synthetic
price data with known, verifiable structure, plus an end-to-end test that
walks a full sweep -> CHoCH -> order block -> OTE -> signal sequence
through the strategy and the backtest engine.

## What the backtests actually show

Measured over ~2 years of 15m data across 8 crypto markets (BTC, ETH, SOL,
XRP, ADA, LINK, DOGE, AVAX), with a chronological train/test split and
variants selected on train only:

| | trades | mean result per trade | t |
|---|---|---|---|
| no trading costs | 719 / 372 | +0.03R / +0.01R | 0.6 / 0.2 |
| maker 0.02%, taker 0.05%, 0.02% stop slippage | 719 / 372 | **-0.18R / -0.24R** | -3.2 / -3.1 |

Read that carefully, because the two rows say different things:

- **Before costs the edge is not distinguishable from zero.** +0.03R sounds
  positive, but with a ~1.4R spread over 719 trades the 95% interval is
  [-0.07, +0.14]. There is no evidence of an edge here, in either
  direction.
- **After realistic costs the loss *is* statistically significant** (t
  ≈ -3.1, interval entirely below zero). That is not noise.

The reason is structural rather than a matter of tuning. The stop sits just
beyond the swept level, so the median risked distance is **0.308% of
price**. Sizing that to risk 1% of the account implies **~3.2x notional
exposure** - and fees are charged on notional while the edge is earned on
the stop distance. A 0.06% round trip therefore costs ~0.19R per trade,
roughly six times the entire measured edge. Breaking even would need stops
wider than ~1.8%, which is a different strategy, not a different parameter.

Things that did **not** fix it, each measured rather than assumed:
higher-timeframe bias, session liquidity (PDH/PDL/PWH/PWL), consequent
encroachment entries, wider/narrower sweep lookbacks, different swing
sensitivities, and risk/reward floors from 1.5 to 3.0 - none beat the
plain baseline on train.

One change did help materially, just not enough: the original exit takes
profit at the next liquidity pool, which sat a median **3.97R** away and
was reached 19% of the time, while 71% of trades were up +1R at some point
and 64% of the *losers* had been +1R before turning around. Switching to a
fixed 2R target lifts the win rate from 19% to ~35% and per-trade
expectancy by ~0.2R - enough to reach break-even before costs, not enough
to clear them.

None of this proves ICT "doesn't work" - it is one implementation, on
crypto, at 15m, executed mechanically. It does mean **this** configuration
should not be run with real money.

### The trend strategy, measured the same way

`strategy.type: "trend"` is a Donchian channel breakout with an ATR stop and
an ATR trailing exit - no fixed target, winners run until the trail takes
them out. Measured on **4h bars, ten markets, 2018-2026 (4,053 trades),
with the same maker/taker/slippage costs applied**:

| | trades | mean result per trade | t | 95% CI |
|---|---|---|---|---|
| all trades | 4,053 | **+0.169R** | 5.68 | [+0.110, +0.227] |
| longs only | 2,169 | +0.161R | 4.00 | |
| shorts only | 1,884 | +0.178R | 4.04 | |

- **30 of 35 quarters** and **8 of 9 years** were profitable, as were
  **10 of 10 markets**. Clustering trades by quarter (so that simultaneous
  positions across markets are not counted as independent) still gives
  t = 4.6.
- Longs and shorts earn the same edge. That matters: it rules out "crypto
  went up during the sample" as the explanation.
- Seven parameter variants - faster and slower channels, wider stops,
  tighter trails, no regime filter, long-only - were **all** positive
  (t between 3.8 and 5.8). The result is not one lucky setting.

The first version of this test, on the same 2 years of data used for ICT,
looked like a failure: strongly positive on train, negative on test. That
"test period" was two quarters long. Over 8.7 years those two quarters are
an ordinary flat patch, and the parameters were in fact chosen on
2024-2026, making all of 2018-2024 genuinely out-of-sample.

Why costs do not kill this one, when they killed ICT:

| | ICT (15m) | trend (4h) |
|---|---|---|
| median stop distance | 0.308% of price | **4.46% of price** |
| notional at 1% risk | ~3.2x equity | ~0.22x equity |
| round-trip cost in R | ~0.19R | **~0.020R** |
| measured edge | +0.03R | +0.169R |
| cost as share of edge | ~600% | **12%** |

The stop is wide enough that fees are a rounding error instead of the whole
result. Median holding time is 1.7 days, so on perpetual futures even
0.02%/8h funding only takes the edge from +0.169R to +0.133R.

Replayed as **one account trading all ten markets**, risking 0.5% per trade
with at most 5 positions open:

| | CAGR | max drawdown | Sharpe |
|---|---|---|---|
| 2018-2026 | +34.5% | -35.1% | 1.29 |
| 2022-2026 only | +27.6% | -35.1% | 0.95 |

Worst year -8.6%, best +96.5%. Note the drawdown: a -35% trough is the
price of that return, and doubling the risk per trade roughly doubles both.

Caveats that no amount of backtesting removes: the ten markets all still
exist today, so there is a survivorship tilt (smaller than usual here,
because shorts profit from coins that collapse); shorts need futures, since
spot cannot be sold short; and the fill model still assumes a limit order
fills the moment price touches it.

One modelling choice is worth naming because it flatters the result, and
worth quantifying so it is a known bias rather than an unknown one. The
engine checks the stop *before* filling a pending order, so the entry bar
itself is never tested against its own stop - a bar that entered and then
ran through the stop is only noticed on the next one. The live loop does
the same, so the two agree, but reality does not. Re-running the whole
study with every entry bar also checked against the stop, using the bar's
full range (the worst case, since without tick data there is no way to
know how much of that range came after the fill):

| | trades | mean per trade | t |
|---|---|---|---|
| as measured | 4,053 | +0.169R | 5.68 |
| worst-case entry-bar stops | 4,103 | +0.164R | 5.56 |

A 3% haircut on the edge. Worth knowing, not worth restructuring for.

### A filter that would have inverted the edge

A third party added an "M1-RSI spike filter" to the same friend's bot after
one losing trade whose 1-minute RSI read 80.6 at entry: block longs when
short-term RSI is above 65, shorts when it is below 35. Don't buy the top
of a spike.

Asked of 4,050 trades instead of three (`scripts/eval_entry_rsi.py`), the
effect runs the other way here:

| long entries, RSI on the entry bar | trades | WR | mean per trade | t |
|---|---|---|---|---|
| RSI 50-59 | 381 | 20.7% | **-0.308R** | -2.74 |
| RSI 60-64 | 567 | 30.7% | -0.037R | -0.49 |
| RSI 65-69 | 571 | 40.3% | +0.227R | 3.38 |
| RSI 70-79 | 530 | 51.1% | +0.554R | 7.24 |
| RSI 80-100 | 90 | 55.6% | **+0.942R** | 3.74 |

Applying their rule to this strategy would block the 1,191 trades that
made +0.426R each and keep the 979 that lost -0.162R each. Shorts mirror
it exactly. The correlation between entry RSI and result is +0.205 for
longs and -0.162 for shorts: the more stretched the entry, the better the
trade.

That is not a contradiction of their filter, it is the difference between
two kinds of strategy. Theirs buys a *pullback* inside a trend, so an
overheated entry means the pullback never happened. This one buys a
*breakout*, where an overheated reading is the signal working. A filter is
only meaningful relative to what the strategy is trying to catch.

**And the obvious conclusion from that table is wrong too.** Turning it
into a rule - only take longs above RSI 60 - recovers almost nothing:

| | trades | mean per trade | quarter-clustered t | equity |
|---|---|---|---|---|
| no gate | 4,050 | +0.169R | 4.56 | 208,726 |
| long >= 60, short <= 40 | 3,974 | +0.166R | 5.07 | 205,302 |
| long >= 65, short <= 35 | 3,381 | +0.164R | 4.86 | 182,507 |
| long >= 70, short <= 30 | 2,336 | +0.155R | 4.01 | 152,880 |

The gate at 60 removes 76 trades, not the 412 the buckets suggested,
because the entry happens a bar *after* the signal: a breakout that
immediately pulls back has a high RSI when the signal fires and a low one
when the order fills. The bucketed table is measuring which trades turned
against us straight away, which is only knowable once you are already in.
Information that exists only at fill time is not information you can
trade on.

### Intermarket divergence

Ported from a friend's Gold/Silver MT5 bot: two markets that normally move
together, banded on the difference of their 20-bar returns at
mean - 1.5 sigma. When the traded market has fallen behind its partner and
then catches up, that is a long. His parameters were used unchanged - the
honest first test of someone else's idea is their settings, not ones
fitted to my data. Code in `ict_bot/strategy/divergence_strategy.py`,
re-runnable via `scripts/eval_divergence.py`.

**It is not wired into the live loop, and should not be run.** Here is why:

| | trades | mean per trade | per-trade t | **quarter-clustered t** | profitable years |
|---|---|---|---|---|---|
| ETH vs BTC alone | 88 | +0.208R | 1.31 | - | - |
| 10 pairs, long only (his design) | 744 | +0.124R | 2.31 | **-0.17** | 5/9 |
| 10 pairs, mirrored for shorts | 1,542 | +0.097R | 2.62 | **2.70** | 8/9 |
| *trend strategy, for comparison* | *4,050* | *+0.169R* | *5.69* | ***4.63*** | *8/9* |

Three things to read out of that:

1. **ETH vs BTC on its own is not evidence.** 88 trades, and the 95%
   interval is [-0.102, +0.519] - it contains zero comfortably.
2. **The long-only version - the one his bot actually runs - has no edge
   here at all.** Its per-trade t of 2.31 looks respectable and is an
   illusion: ten pairs all referenced to BTC or ETH trade *together*, so
   counting each trade as an independent observation inflates the
   evidence. Treating each quarter as one observation gives t = -0.17.
   This is the single most useful number on this page for reading any
   other: a pooled statistic over correlated positions is not what it
   appears to be.
3. **The mirrored version survives clustering** (t = 2.70) but is still
   the weaker idea: it needs futures, since shorts are half of it and spot
   cannot sell short; its quarterly returns correlate +0.38 with the trend
   strategy, so it is not a clean diversifier; and it was found after many
   tests on this same data, which is exactly the setting in which a t near
   2.7 should not be trusted.

None of that says the idea is bad - on Gold and Silver, two metals with a
genuine economic link, it may well be sound. It says crypto pairs that all
rise and fall with BTC are a different problem.

## Known limitations

- The higher-timeframe bias filter derives its HTF candles by resampling
  the trading window, so the usable HTF is bounded by `window_size`: a
  300-bar 15m window is only ~18 4h candles, not enough to read 4h
  structure. Raise `window_size` (at a proportional backtest cost) before
  expecting a 4h bias to do anything.
- SMT divergence only applies when a correlated market's data is passed to
  `generate_signal`; the live loop fetches a single symbol, so it is
  currently reachable from backtests/research only.
- The backtest fill model is a simplification: a limit order fills the
  instant a future bar's range touches the entry price, and there are no
  partial fills. Fees and stop slippage *are* modelled but default to
  zero - set `backtest.fee_pct` / `backtest.stop_slippage_pct` to your
  exchange's real numbers, because with stops this tight they are not a
  rounding error: at ~0.3% risk per trade, 0.05% per side is roughly a
  third of the amount risked.
- Live order management is intentionally minimal (market entry + best
  effort native SL/TP); it does not manage complex order lifecycles
  (partial fills, order amendment, etc).
- When native SL/TP orders are used, the bot detects a fill by polling
  `fetch_order` on those two order ids - it does not reconcile the full
  account/position state against the exchange, so a position closed by any
  other means (manual intervention, exchange-side liquidation) will not be
  noticed until the next native-fill or bar-range check.
