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
