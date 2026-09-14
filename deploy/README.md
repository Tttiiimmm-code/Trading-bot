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

## Changing the config

```bash
sudo -u ictbot nano /opt/ict-bot/Trading-bot/config/config.yaml
sudo systemctl restart ict-bot
```

## Stopping it

```bash
sudo systemctl stop ict-bot        # stop now
sudo systemctl disable ict-bot     # also don't start on next reboot
```

## Going from paper to real trading

**Never automate this step.** The cloud-init above only ever sets up
*paper* trading (`--mode paper` in the systemd unit, no API keys). To go
live, on the server:

1. Create `/opt/ict-bot/Trading-bot/.env` with your exchange API
   key/secret (see `.env.example`) - as the `ictbot` user, and make sure
   it's not world-readable (`chmod 600`).
2. Keep `exchange.sandbox: true` in `config.yaml` and validate against the
   exchange's testnet first.
3. Only then, deliberately edit
   `/etc/systemd/system/ict-bot.service`'s `ExecStart` line to
   `--mode live`, `sudo systemctl daemon-reload`, and
   `sudo systemctl restart ict-bot`.

Do this as a conscious, manual decision after you've watched the paper
run behave sensibly - not as part of any automated setup.
