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
  ict/            Pure detectors: structure, fvg, order_blocks, liquidity,
                   killzones, premium_discount - each independently testable.
  strategy/       ict_strategy.py wires the detectors into entry signals;
                   risk.py does position sizing + daily loss circuit breaker.
  data/           OHLCV fetching via ccxt, or from a local CSV.
  execution/      Broker abstraction: PaperBroker (sim) and CCXTBroker (live).
  backtest/       Bar-by-bar backtest engine + performance metrics.
  config.py       Loads config/config.yaml (+ .env for API keys).
  main.py         CLI entry point (backtest / live).
tests/            pytest suite, including a hand-crafted end-to-end
                   confluence scenario (sweep -> CHoCH -> OB -> OTE -> Signal).
```

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

- Single-timeframe structure only; no higher-timeframe bias filter.
- The backtest fill model is a simplification (limit order fills the
  instant a future bar's range touches the entry price - no partial
  fills, no slippage/fees).
- Live order management is intentionally minimal (market entry + best
  effort native SL/TP); it does not manage complex order lifecycles
  (partial fills, order amendment, etc).
