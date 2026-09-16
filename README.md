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

### Breadth: the one change that measurably helped

Every other idea tested here was a way to pick *better* trades. The one
that worked was taking *more* of the same ones.

First, a genuine out-of-sample test. The strategy and its parameters were
settled on ten markets; nineteen others were then fetched and run
untouched - every market with data included, including the ones that make
it look bad:

| | trades | mean per trade | t | quarter-clustered t | markets positive |
|---|---|---|---|---|---|
| the ten it was built on | 4,050 | +0.169R | 5.69 | 4.56 | 10/10 |
| **nineteen never examined** | 7,039 | **+0.096R** | 4.79 | **4.13** | **17/19** |

The edge replicates - and at **57% of the size**. That shrinkage is the
honest correction to every other number on this page: the original ten
were partly favourable by chance, and +0.10R is a better estimate of what
to expect than +0.17R.

Second, what breadth does to an account. One shared balance, positions
competing for the same capital, 0.5% risked per trade:

| universe | max open | CAGR | max drawdown | Sharpe |
|---|---|---|---|---|
| 10 markets | 5 | 33.3% | -37.5% | 1.25 |
| **29 markets** | **5** | **39.6%** | **-31.7%** | **1.39** |
| 29 markets | 10 | 55.9% | -45.6% | 1.22 |

More return *and* a smaller drawdown, from the same strategy with no new
parameters - diversification doing what it is supposed to do.

**But only with shorts, and spot cannot short.** Repeating the same
measurement long only, which is what a spot account actually runs:

| universe | max open | CAGR | max drawdown | Sharpe |
|---|---|---|---|---|
| 10 markets, long+short | 5 | 33.3% | -37.5% | 1.25 |
| 10 markets, **long only** | 5 | 18.6% | -28.4% | 0.98 |
| 29 markets, long+short | 5 | 39.6% | -31.7% | 1.39 |
| 29 markets, **long only** | 5 | 17.4% | **-43.1%** | **0.78** |

Long only, going from 10 markets to 29 earns nothing extra and lifts the
drawdown by half. The reason is plain once stated: without shorts,
twenty-nine positions are not twenty-nine bets, they are one bet in
twenty-nine pieces. The shorts were the other side that made breadth
diversifying rather than concentrating.

So the breadth result splits in two. On futures, more markets is the best
lever available. On spot it is not - and the long-only optimum is narrow.
Chosen on 2018-2023 and checked once on 2024-2026:

| markets (long only) | cap | train Sharpe | test CAGR | test drawdown | test Sharpe |
|---|---|---|---|---|---|
| 3 | 3 | 0.92 | 6.0% | -16.6% | 0.56 |
| 5 | 5 | **1.32** | 10.5% | -19.7% | 0.68 |
| 8 | 3 | 1.24 | 11.8% | -17.1% | 0.72 |
| 10 | 3 | 1.18 | 11.8% | -20.5% | 0.73 |
| 15 | 5 | 1.00 | 13.3% | -32.7% | 0.58 |
| 29 | 5 | 0.91 | 11.2% | -24.1% | 0.55 |

Eight to ten markets with a shared cap of three is where long-only
flattens out, and nothing there reaches a held-out Sharpe above 0.73 -
against 1.54 for the same strategy allowed to short. Half the edge really
does live on the short side, and no amount of portfolio construction
recovers it.

The cap on simultaneous positions was chosen the disciplined way - on
2018-2023, then looked at once on 2024-2026:

| max open | train Sharpe | test CAGR | test drawdown | test Sharpe |
|---|---|---|---|---|
| 3 | 1.06 | 33.9% | -15.3% | 1.48 |
| **5 (chosen on train)** | **1.31** | **42.8%** | **-23.6%** | **1.54** |
| 10 | 1.22 | 70.3% | -45.6% | 1.25 |
| 15 | 1.16 | 104.7% | -59.7% | 1.31 |

The cap matters because crypto markets move together: ten open positions
is not ten bets, it is closer to one bet in ten pieces. Note what the
larger caps do - the headline return keeps climbing while the drawdown
climbs faster.

**This is what makes the shared `portfolio:` section necessary.** Each bot
is its own process with its own risk manager, so `risk.max_open_positions`
limits one instance only. Running a market per instance, as these results
assume, means nothing counts the total - twenty-nine bots at 0.5% each can
have 14.5% at risk with no part of the system noticing. The board in
`ict_bot/execution/portfolio.py` is how they see each other.

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

## The benchmark this project spent too long ignoring

Every figure above is measured against zero: does the strategy make money.
That is the wrong question. The question anyone actually faces is whether
it beats what they could have done by buying the asset and doing nothing.

The bot, eight markets, long only (what a spot account runs), against
simply holding:

**2018-2026, the full history**

| | CAGR | max drawdown | Sharpe |
|---|---|---|---|
| bot, cap 3 | 14.6% | **-18.9%** | **0.99** |
| bot, cap 5 | 16.7% | -24.8% | 0.94 |
| buy and hold BTC | **22.5%** | -81.6% | 0.66 |
| buy and hold ETH | 14.7% | -94.1% | 0.52 |
| buy and hold all eight, equal weight | -2.4% | -78.8% | 0.28 |

**2024-2026, the held-out period**

| | CAGR | max drawdown | Sharpe |
|---|---|---|---|
| bot, cap 3 | 3.8% | -18.9% | 0.31 |
| bot, cap 5 | 9.5% | -24.8% | 0.54 |
| buy and hold BTC | **24.1%** | -53.4% | **0.66** |
| buy and hold all eight, equal weight | 9.8% | -64.2% | 0.43 |

Read honestly:

- **The bot does not beat holding BTC.** Not over the full history on
  return, and over the held-out period it loses on return *and* on
  Sharpe. Anyone who bought BTC in 2018 and did nothing has more money
  than this bot would have made them.
- **What it delivers is a quarter of the drawdown.** -19% against -82% is
  not a detail; it is the difference between a position most people can
  hold and one most people sell at the bottom. The full-history Sharpe of
  0.99 against 0.66 is that, expressed as a number.
- **Against the basket it actually trades, it wins clearly.** Equal-weight
  those eight coins and hold them and you end 2018-2026 *down* 2.4% a
  year. The bot's job is not to beat the single best asset of the era; it
  is to extract something from a set of assets that collectively did
  nothing.
- **But picking BTC as the benchmark is hindsight too.** Nobody knew in
  2018 which coin would be the one. The equal-weight row is closer to the
  honest alternative, and the single-asset row is closer to what people
  remember.

### The same strategy on metals, equities and bonds

Twenty-one ETFs, daily bars, 2005-2026 - the classic managed-futures
spread. Same parameters; on daily bars the 20-bar Donchian *is* the
original Turtle breakout, so nothing needed re-tuning
(`scripts/` has no runner for this; the measurement lived in a scratch
script and is recorded here).

| | trades | mean per trade | quarter-clustered t | instruments positive |
|---|---|---|---|---|
| long and short | 2,660 | +0.085R | 2.25 | 16/21 |
| **long only** | 1,522 | **+0.232R** | **4.17** | 17/21 |

As an account, and against what anyone can buy instead:

| | CAGR | max drawdown | Sharpe |
|---|---|---|---|
| trend long only, cap 8 | 7.8% | **-12.7%** | **1.02** |
| buy and hold SPY | **10.9%** | -55.2% | 0.78 |
| buy and hold GLD | 10.8% | -45.6% | 0.69 |
| 60/40 SPY+IEF, monthly | 8.1% | -29.5% | 0.90 |

The same shape of answer. Better risk-adjusted than anything on offer,
roughly a better 60/40 - and still short of just owning the S&P on
return. It also held together through 2022-2026, when long bonds lost 8%
a year: 5.7% at a -6.8% drawdown.

So "would another market be better" has a narrow answer and a wide one.
Narrow: ETFs give a steadier ride (Sharpe 1.02 against 0.72 for long-only
crypto) and a smaller one. Wide: changing the market does not change what
this strategy is. It is a drawdown-reduction machine, not a
money-multiplication machine, and it is worth running only for someone
who would otherwise not hold the asset at all, or could not sit through
an 80% decline.

One practical note before anyone acts on the ETF table: this bot cannot
trade it. `ccxt` connects to crypto exchanges. Metals and equities need a
different broker entirely - a funded brokerage account and an
`ict_bot/execution/` implementation that does not exist yet.

## Timeframe, and why 4h rather than higher

Higher timeframes mean wider stops, and wider stops are exactly what made
this strategy viable where ICT was not. So the obvious move is up. Same
parameters, only the bar size changing, eight markets:

| timeframe | trades | median stop | mean per trade | quarter-clustered t | CAGR | Sharpe |
|---|---|---|---|---|---|---|
| **4h** | 1,863 | 4.17% | +0.176R | **2.61** | **14.6%** | **0.99** |
| 8h | 970 | 6.03% | +0.199R | 1.59 | 7.3% | 0.75 |
| 12h | 653 | 7.28% | +0.238R | 0.55 | 4.2% | 0.63 |
| 1D | 338 | 10.11% | +0.235R | -0.54 | 3.9% | 0.78 |
| 2D | 174 | 14.97% | **+0.424R** | 0.06 | 2.8% | 0.74 |

(long only, shared cap 3)

Each step up **does** improve the result per trade - +0.176R at 4h against
+0.424R at 2D, exactly as the cost argument predicts. And each step up
makes the account worse, because it removes far more trades than it adds
edge. At 2D there are 174 trades in 8.6 years across eight markets: the
per-trade number is better and the quarter-clustered t is 0.06, which
means it is no longer distinguishable from luck.

So the answer is not "higher is better" but "as high as you can go while
still getting enough trades to matter". For this strategy on crypto that
is 4h.

## Position size, measured against the strategy's actual losing runs

A 37% win rate means long losing runs are normal rather than a
malfunction. How long? **27 consecutive losing trades** in the real
history. Not simulated - observed.

Block-bootstrapped from the actual trade results (resampling in runs of
twenty so clusters of losses stay clustered, because drawing single
trades independently would understate precisely the risk in question),
over an 8.6-year run:

| risk per trade | median drawdown | 5th percentile | worst seen |
|---|---|---|---|
| 0.25% | -12.8% | -20.9% | -41.9% |
| **0.5%** | **-23.8%** | **-38.3%** | -62.7% |
| 1.0% | -43.0% | -63.3% | -84.5% |
| 2.0% | -68.4% | -87.3% | -97.6% |

The 0.5% the configs ship with is defensible, not conservative: a -24%
drawdown is the *median* outcome, and one run in twenty goes past -38%.
At 1% the median outcome is losing nearly half the account at some point
along the way.

This is what "risk management for the strategy, not just the account"
means here. The edge lives in a handful of enormous winners - the best
single trade in the history is +34.6R - so the only way to collect it is
to still be trading when one arrives. Size for the losing runs, not for
the average.

## Two more strategies, measured: grid and open range breakout

### Open range breakout

Take the high and low of the first hour after a reference time, trade the
break, stop at the other side of the range. It comes from stocks and
futures, where an exchange opens after hours of no trading and the first
minutes carry real information. Eight markets, 15m bars, two years, one
attempt per day, every variant tried:

| | trades | WR | mean per trade | t |
|---|---|---|---|---|
| US open 13:30 UTC, 1h range, 2R | 5,935 | 38.1% | -0.077R | -4.70 |
| UTC midnight, 30min range, 2R | 5,951 | 33.9% | -0.214R | -12.31 |
| Europe open 08:00 UTC, 1h range, 2R | 5,950 | 37.7% | -0.059R | -3.30 |
| US open, 1h range, 1R target | 5,935 | 49.9% | -0.085R | -6.89 |

Every variant significantly negative, intervals entirely below zero. And
the reason is one already familiar from this project:

| | trades | mean per trade | t |
|---|---|---|---|
| before costs | 5,935 | +0.026R | 1.61 |
| after costs | 5,935 | **-0.077R** | **-4.70** |

No edge before costs, a significant loss after - exactly the ICT result,
for exactly the ICT reason. The median opening range is **1.125% of
price**, so the stop is narrow, so a 0.09% round trip costs **0.080R per
trade** against **0.022R** for the trend strategy's 4.17% stop.

That is now three strategies measured against the same rule. **Stop width
against cost decides whether a strategy can work at all**, before anything
about signal quality enters the picture. A tight stop is not a cheap stop.

### Grid

A grid places a ladder of buy orders below price and sell orders above.
Price oscillates, each round trip banks a small profit, no forecast
needed. One grid on BTC, 1% spacing, 20 levels, 10k committed, over 67
rolling six-month windows:

| | |
|---|---|
| windows ending in profit | **58%** |
| median outcome | +0.8% |
| best window | +13.9% |
| worst window | **-45.2%** |
| **mean of all windows** | **-4.0%** |

It wins most of the time and loses money on average. That is the whole
shape of it: many small wins funding a rare catastrophe, the exact mirror
of trend following, which loses 63% of the time and makes money.

The detail worth carrying away is what the three worst windows look like
split in two:

| realised ("earned") | unrealised (still holding) | total |
|---|---|---|
| +74 | -4,596 | **-4,522** |
| +84 | -4,566 | **-4,481** |
| +3 | -3,765 | **-3,762** |

**The trade log shows nothing but wins while the account bleeds.** Every
closed round trip was profitable. The loss sits in twenty levels of
inventory bought on the way down and never sold, and it does not appear in
any win-rate, profit-factor or per-trade statistic - only in the mark to
market.

This is why a grid bot's screenshots look extraordinary right up to the
moment they stop appearing. Not dishonesty, usually: the operator's own
statistics genuinely do look like that.

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
