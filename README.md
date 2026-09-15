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
