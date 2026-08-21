# GitHub Actions EC2 Deploy

This repo now includes an EC2 deployment workflow at `.github/workflows/deploy-ec2.yml`.

What it does:

- triggers automatically on pushes to `master`
- can also be run manually from the GitHub Actions tab
- connects to your EC2 instance over SSH
- syncs the repo to the server
- uploads the runtime `.env`
- builds the Docker image on EC2
- runs `python -m bot.profile_runner <paper|live> validate spy` on EC2 (the deploy market is fixed at `spy`)
- installs a cron schedule for the chosen profile in `America/New_York`
- also validates and installs an independent cron schedule for the
  `paper-options` profile (LCID, the active ongoing daily paper
  evaluation) unless `install_options_cron` is turned off

## Required GitHub Secrets

Create these repository or environment secrets:

- `EC2_HOST`: public DNS name or public IP of the instance
- `EC2_USER`: SSH user, usually `ubuntu`
- `EC2_SSH_KEY`: private key used by GitHub Actions to SSH into EC2
- `EC2_ENV_FILE`: full contents of the server-side `.env`

Example `EC2_ENV_FILE` source:

- start from `.env.example`
- fill in your Alpaca keys
- keep `ALPACA_PAPER_*` and `ALPACA_LIVE_*` values there
- keep only real secrets in this GitHub secret, not in the repo

Optional repository variables:

- `EC2_APP_DIR`: defaults to `/home/ubuntu/trading-bot`
- `EC2_PORT`: defaults to `22`

## One-Time EC2 Setup

Use Ubuntu 22.04 or 24.04 on EC2.

1. SSH into the instance.
2. Install Docker, the Compose plugin, and cron:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin cron
sudo systemctl enable --now docker
sudo systemctl enable --now cron
sudo usermod -aG docker "$USER"
newgrp docker
```

3. Create the app directory:

```bash
mkdir -p /home/ubuntu/trading-bot
```

4. Make sure the SSH user from `EC2_USER` can log in non-interactively from GitHub Actions.

Recommended path:

- generate a dedicated SSH key pair for deployment
- add the public key to `~/.ssh/authorized_keys` on EC2
- store the private key in the `EC2_SSH_KEY` GitHub secret

5. Confirm the instance user can run Docker without `sudo`:

```bash
docker version
docker compose version
```

## First Deployment

1. Push this repo to GitHub with the new workflow files.
2. Add the secrets listed above.
3. Open `Actions` in GitHub.
4. Run `Deploy To EC2`.
5. Choose `live` or `paper`.
6. Leave `install_cron` enabled unless you want a code-only deploy.
7. Leave `install_options_cron` enabled to also validate and schedule the
   `paper-options` profile — this is independent of the profile/market chosen
   above and requires options trading to already be enabled on the Alpaca
   *paper* account (Alpaca's own dashboard approval; the deploy fails with a
   clear message otherwise).

On every later push to `master`, the workflow will auto-deploy the `live`
profile with both cron schedules enabled by default (`install_cron` and
`install_options_cron` both default to `true` outside of a manual
`workflow_dispatch` run).

## Verify on EC2

Check the installed cron entries:

```bash
crontab -l
```

Check the last deploy logs from GitHub Actions in the Actions tab.

Check runtime output on the server:

```bash
ls -la /home/ubuntu/trading-bot/logs
tail -n 50 /home/ubuntu/trading-bot/logs/live_cron.log
tail -n 50 /home/ubuntu/trading-bot/logs/paper_cron.log
tail -n 50 /home/ubuntu/trading-bot/logs/paper_options_cron.log
tail -n 50 /home/ubuntu/trading-bot/logs/paper_options_monitor_cron.log
tail -n 50 /home/ubuntu/trading-bot/logs/paper_options_daily_cron.log
```

If you want to run one profile manually on the server after a deploy:

```bash
cd /home/ubuntu/trading-bot
docker compose run --rm trade
docker compose run --rm paper
docker compose run --rm paper-options
```

If you want a different schedule, set `CRON_SCHEDULE` / `MONITOR_CRON_SCHEDULE` / `RESEARCH_CRON_SCHEDULE` (main profile) or `OPTIONS_CRON_SCHEDULE` / `OPTIONS_MONITOR_CRON_SCHEDULE` / `OPTIONS_DAILY_CRON_SCHEDULE` (options) before running `deploy/ec2/install_cron.sh`, or edit the script defaults.

Current default schedules (`America/New_York`, every day):

| Job | Schedule | Env override |
|---|---|---|
| Main profile trade cycle | every 5 minutes | `CRON_SCHEDULE` |
| Main profile monitor report | `:17` past the hour | `MONITOR_CRON_SCHEDULE` |
| Main profile research report (paper only) | `00:42` | `RESEARCH_CRON_SCHEDULE` |
| `paper-options` trade cycle | `:20` past the hour | `OPTIONS_CRON_SCHEDULE` |
| `paper-options` monitor report | `:35` past the hour | `OPTIONS_MONITOR_CRON_SCHEDULE` |
| `paper-options` daily report | `23:55` | `OPTIONS_DAILY_CRON_SCHEDULE` |

The deploy market is fixed at `spy` (`SYMBOL=TSLA`, see
`config/live_spy.env`), which trades hourly bars during the equity session —
the bot itself checks market hours and holds outside them, so running the
cron job around the clock is harmless, just a no-op most of the day. The
`paper-options` schedule is installed independently (its own cron job
marker), so a normal `live`/`spy` deploy doesn't disturb it, and it survives
across deploys unless `install_options_cron` is explicitly turned off. If
you change a profile's `TIMEFRAME_MINUTES`, update its corresponding
`*_CRON_SCHEDULE` to match — running the bot much more often than its bar
interval just wastes API calls and log lines, since cooldown and
pending-order checks will no-op the extra invocations (options-chain lookups
are a heavier call than an equity bar fetch, so this matters more for
`paper-options` than for the equity schedule).
