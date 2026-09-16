# Deploying the bot on a fresh VPS

For running the bot unattended (paper trading) on a cheap VPS you don't
want to babysit from a terminal. Tested against Ubuntu 24.04 LTS; any
cloud-init-compatible provider (Hetzner Cloud, DigitalOcean, ...) works.

## Setup (one-time, no SSH needed)

1. Create the smallest/cheapest server your provider offers (1 vCPU /
   512MB-1GB RAM is plenty - see the project README for why).
2. Pick Ubuntu 24.04 LTS as the image.
3. Paste the contents of [`cloud-init.yaml`](./cloud-init.yaml) into the
   provider's "Cloud config" / "User data" field during creation.
4. Create the server. By the time it finishes booting (~1-2 minutes), the
   bot is already running in **paper mode**: real market data, simulated
   balance, no real orders are ever placed.

No SSH key is required for this - everything runs automatically. You only
need to log in (via the provider's web console, or SSH if you set up a
key) to check on it or change the config.

## Checking on it

```bash
# live log tail
tail -f /var/log/ict-bot.log

# service status (should say "active (running)")
systemctl status ict-bot

# recent systemd journal (same content, different view)
journalctl -u ict-bot -n 100 --no-pager
```

## Troubleshooting: `systemctl status ict-bot` shows `code=exited, status=203/EXEC`

This means `/opt/ict-bot/Trading-bot/.venv/bin/python` doesn't exist, i.e.
the `runcmd` steps in cloud-init never ran (only `systemctl enable --now`
did, which is why the service exists but can't start). Check what actually
happened during boot:

```bash
cat /var/log/cloud-init-output.log
```

If you see `sudo: Account or password is expired` there, your provider
forced a password reset on first login and it broke a `sudo`/`su` call
inside `runcmd` (this cloud-init file no longer uses one, but a modified
copy might). Fix by running the missing setup steps manually, as root:

```bash
rm -rf /opt/ict-bot
mkdir -p /opt/ict-bot
git clone --depth 1 https://github.com/Tttiiimmm-code/Trading-bot.git /opt/ict-bot/Trading-bot
python3 -m venv /opt/ict-bot/Trading-bot/.venv
/opt/ict-bot/Trading-bot/.venv/bin/pip install --upgrade pip
/opt/ict-bot/Trading-bot/.venv/bin/pip install -r /opt/ict-bot/Trading-bot/requirements.txt
cp /opt/ict-bot/Trading-bot/config/config.example.yaml /opt/ict-bot/Trading-bot/config/config.yaml
systemctl daemon-reload
systemctl restart ict-bot
systemctl status ict-bot
```

## Changing the config

```bash
nano /opt/ict-bot/Trading-bot/config/config.yaml
systemctl restart ict-bot
```

## Running multiple instances

Instead of loosening one bot's entry criteria to trade more often (which
trades away signal quality - see the project README's "known
limitations"), run several independent instances side by side: different
markets and/or timeframes, each with its own simulated balance, own risk
tracking, own log file. One instance crashing or hitting its daily loss
limit never affects the others.

The [`ict-bot@.service`](./ict-bot@.service) systemd template unit (`%i`
is the instance name) makes this a config file plus two commands. Two
ready-made examples ship in `config/`:
[`config-ethusdt.example.yaml`](../config/config-ethusdt.example.yaml)
(same settings, ETH/USDT instead of BTC/USDT) and
[`config-btc-5m.example.yaml`](../config/config-btc-5m.example.yaml)
(same market, 5m instead of 15m).

```bash
cd /opt/ict-bot/Trading-bot
cp config/config-ethusdt.example.yaml config/config-ethusdt.yaml
systemctl enable --now ict-bot@ethusdt.service
systemctl status ict-bot@ethusdt.service
tail -f /var/log/ict-bot-ethusdt.log
```

The instance name (`ethusdt` above) just has to match between the config
filename (`config-<name>.yaml`) and the service name (`ict-bot@<name>`) -
pick whatever name describes it. Repeat for any other market/timeframe
you want to add (e.g. `config-btc-5m.yaml` -> `ict-bot@btc-5m.service`).
Each instance is fully independent of the original `ict-bot.service` and
of each other.

### Trend-following instances

[`config-trend-btc.example.yaml`](../config/config-trend-btc.example.yaml),
[`config-trend-eth.example.yaml`](../config/config-trend-eth.example.yaml)
and [`config-trend-sol.example.yaml`](../config/config-trend-sol.example.yaml)
run the **trend-following** strategy on 4h bars instead of ICT. That is the
one the long backtest found an edge in - see "The trend strategy, measured
the same way" in the main README before choosing. Because both strategies
share the same live loop, switching is a config file, not a different
program.

Run several markets: most of the measured return came from trading several
at once, since any single market spends long stretches without a breakout
worth taking - one market averaged roughly 50 trades a year.

```bash
cd /opt/ict-bot/Trading-bot
for m in btc eth sol; do
  cp config/config-trend-$m.example.yaml config/config-trend-$m.yaml
  systemctl enable --now ict-bot@trend-$m.service
done
systemctl status 'ict-bot*' --no-pager | grep -E 'ict-bot@|Active'
tail -f /var/log/ict-bot-trend-*.log
```

## Judging a paper run

Each instance appends every closed trade to `state/config-<name>-trades.csv`
and keeps its state in `state/config-<name>-state.json`. The state file is
what makes a restart harmless - without it, systemd's `Restart=always`, a
reboot or a `git pull` would bring the bot back believing it is flat, with
the simulated balance reset and (in live mode) a real position left open
and no longer trailed.

The trade CSV is append-only, so it is the durable record, not the log.
Score it with the backtest's own metrics:

```bash
cd /opt/ict-bot/Trading-bot
.venv/bin/python scripts/score_trades.py state/config-trend-*-trades.csv --risk-pct 0.5
```

That prints win rate, profit factor, mean R and a confidence interval per
instance, so a paper result can be held against what the backtest
predicted instead of judged by eye. Expect the interval to straddle zero
for a long time: at roughly 50 trades per market per year, a few weeks is
a handful of trades, which says nothing either way. The script says so
when that is the case.

## Independence between instances

The instances stay independent: each tracks its own risk and its own
simulated balance. So three markets at 0.5% risk each can put 1.5% of a
real account at risk simultaneously, and in paper mode the three balances
are three separate 10k accounts, not one portfolio - don't add the
returns together and read them as a portfolio result.

Stopping one instance:
```bash
systemctl stop ict-bot@ethusdt.service
systemctl disable ict-bot@ethusdt.service
```

## Stopping it

```bash
systemctl stop ict-bot        # stop now
systemctl disable ict-bot     # also don't start on next reboot
```

## Going from paper to real trading

**Never automate this step.** The cloud-init above only ever sets up
*paper* trading (`--mode paper` in the systemd unit, no API keys). To go
live, on the server:

1. Create `/opt/ict-bot/Trading-bot/.env` with your exchange API
   key/secret (see `.env.example`), and make sure it's not
   world-readable (`chmod 600 .env`).
2. Keep `exchange.sandbox: true` in `config.yaml` and validate against the
   exchange's testnet first.
3. Only then, deliberately edit
   `/etc/systemd/system/ict-bot.service`'s `ExecStart` line to
   `--mode live`, `systemctl daemon-reload`, and
   `systemctl restart ict-bot`.

Do this as a conscious, manual decision after you've watched the paper
run behave sensibly - not as part of any automated setup.
