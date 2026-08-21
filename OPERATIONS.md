# Operations

## Validation

Run the non-trading runtime validation inside Docker:

```powershell
docker compose run --rm validate
```

What it checks:

- runtime directories
- database schema and migrations
- indicator and signal generation on synthetic bars
- risk evaluation on a healthy sample
- report generation
- monitor report generation

Optional live broker validation:

```powershell
$env:VALIDATE_BROKER="1"
docker compose run --rm validate
```

This only validates broker connectivity and account snapshot retrieval. It does not place trades.

## Monitoring

Generate the latest monitoring artifacts:

```powershell
docker compose run --rm monitor
```

Profile-specific monitors:

```powershell
docker compose run --rm paper-monitor
docker compose run --rm live-monitor
docker compose run --rm paper-options-monitor
```

Outputs:

- `reports/monitor_latest.md`
- `reports/monitor_latest.json`

These summarize:

- latest run and action
- recent events
- bot state and loss streak
- pending orders
- open position state
- closed-trade summary
- P&L by exit hour
- recent closed trades

## Daily Report

Generate today's real (non-synthetic) daily Markdown report for a profile's
actual runtime database:

```powershell
python -m bot.profile_runner paper daily spy
python -m bot.profile_runner paper daily options
```

Or via docker compose:

```powershell
docker compose run --rm paper-options-daily
```

Output: `reports/daily_YYYY-MM-DD.md`. For the options profile this includes
an "Options Positions Today" section (contract symbol, delta, DTE, and P&L
for anything opened/closed that day), pulled from the events table
recorded by `bot/options_engine.py`.

(`docker compose run --rm validate` also calls the same report generator,
but against synthetic sample data — use the `daily` action above for a
report that reflects real trading activity.)

## Research

Run the historical replay / walk-forward report:

```powershell
docker compose run --rm research
```

Options research (modeled Black-Scholes backtest — see
`docs/strategy_options_2026-08.md` before trusting these numbers):

```powershell
docker compose run --rm research-options-nvda
docker compose run --rm research-options-tsla
```

Outputs:

- `reports/research_latest.md`
- `reports/research_latest.json`

Run the parameter optimizer (equity `spy` market only — not implemented yet
for options):

```powershell
docker compose run --rm optimize
```

If you want a quicker sanity check first:

```powershell
$env:OPT_MAX_CANDIDATES="10"
docker compose run --rm optimize
```

Outputs:

- `reports/optimize_latest.md`
- `reports/optimize_latest.json`

Useful optional env controls:

- `OPT_MAX_CANDIDATES` to cap grid size. Default is `50`.
- `OPT_PROGRESS_EVERY` to control how often progress lines print
- `OPT_REPORT_TOP_N` to control how many ranked setups are shown
- `OPT_*_VALUES` to narrow or widen the search grid for individual parameters

## Normal Bot Run

Run one bot cycle (equity profile, the default live deployment target):

```powershell
docker compose run --rm paper
docker compose run --rm trade
```

LCID options (paper only for now — the active strategy focus, a learning exercise):

```powershell
docker compose run --rm paper-options
```

Direct profile runner equivalents:

```powershell
python -m bot.profile_runner paper trade spy
python -m bot.profile_runner live trade spy
python -m bot.profile_runner paper trade options
```

Profile-specific validation:

```powershell
python -m bot.profile_runner paper validate spy
python -m bot.profile_runner live validate spy
python -m bot.profile_runner paper validate options
```

Validate actual paper broker and market-data access without placing an order:

```bash
python -m bot.profile_runner paper connectivity options
```

Options connectivity additionally requires options trading to be enabled on
the Alpaca paper account first (a compliance approval in Alpaca's own
dashboard, separate from live) — the connectivity check fails with a clear
message if it isn't.

EC2 deployment runs this connectivity check before installing cron. Invalid or expired credentials therefore fail deployment instead of producing a validation-only database that looks healthy.

Current runtime defaults:

- entry stale-bar blocking is disabled unless `ENABLE_STALE_BAR_CHECK=1`
- bot startup waits `20` seconds by default before broker/data checks; override with `STARTUP_DELAY_SECONDS`
- overnight carrying is disabled by default; override with `ALLOW_OVERNIGHT_HOLDING=true`
- end-of-day flattening starts `5` minutes before the close by default; override with `FLATTEN_BEFORE_CLOSE_MINUTES`
- `paper` and `trade` select separate Alpaca key pairs from `.env` when `ALPACA_PAPER_*` and `ALPACA_LIVE_*` variables are set
- `config/live_spy.env` trades TSLA on hourly bars (fractional, long-only, ~60% notional) and is the recommended default live profile for a small account; see `docs/strategy_tsla_2026-08.md` for the replay evidence. Replay any change to it before relying on it live.
- `config/paper_options.env` / `config/live_options.env` (market `options`) trade LCID long calls/puts on the same TSLA-seeded trend signal, unmodified — see `docs/strategy_options_lcid_2026-08.md`. Paper only for now; this is the active, ongoing daily evaluation (see "Daily LCID Options Evaluation" below), run explicitly as a learning exercise since LCID's own backtested edge is thin.

## Daily LCID Options Evaluation

`paper-options` runs unattended on EC2 via its own cron schedule, installed
by `deploy/ec2/deploy_remote.sh` / `deploy/ec2/install_cron.sh` (see
`docs/github_actions_ec2.md`) — no need to trigger cycles by hand once
deployed. As of 2026-08-21 this replaced the original fixed one-week
evaluation window: the plan now is to keep refining `config/paper_options.env`
against the modeled backtest, then run the current best config against real
LCID paper fills every trading day indefinitely, rather than stopping after
a single week. The operating plan:

1. Confirm the schedule is installed: `crontab -l` on EC2 should show three
   `# trading-bot-paper-options` entries (trade, monitor, daily report).
2. Each day, read the reports it's already generating on EC2:
   - `reports/daily_YYYY-MM-DD.md` (written once daily at 23:55 ET)
   - `reports/monitor_latest.md` (refreshed hourly)
   - Pull them locally with `scp`, or `docker compose run --rm
     paper-options-daily` / `paper-options-monitor` to regenerate on demand
     (locally, this needs working paper Alpaca keys in your `.env`).
3. Periodically review: trade frequency, whether selected contracts actually
   landed near the target delta/DTE band, fill quality, rejection patterns
   (near-misses in the monitor report), and realized P&L.
4. Feed that evidence back into `config/paper_options.env` tuning
   (contract-selection or signal parameters) — don't move to
   `config/live_options.env` until the paper results support it.

## Notes

- Start Docker Desktop before using the commands above.
- Keep real Alpaca keys only in the local untracked `.env`.
- Review `reports/monitor_latest.md` after each trading session if you want a concise explanation of what the bot did and why.

## EC2 Deploy

The repo includes a GitHub Actions workflow for EC2 deploys:

- `workflow: .github/workflows/deploy-ec2.yml`
- `docs: docs/github_actions_ec2.md`

The deployment path syncs the repo to EC2, uploads the server `.env`, validates the selected profile, and installs a cron schedule for repeated runs. The deploy market is fixed at `spy` (the live equity strategy). It also validates and installs an **independent** cron schedule for `paper-options` (TSLA, the active ongoing daily paper evaluation) by default — see `docs/github_actions_ec2.md` for the schedule and how to opt out (`install_options_cron: false` on a manual dispatch).

## Validation Script

For the full local validation pass, run:

```powershell
./scripts/validate.ps1
```

This runs pytest plus the base, paper, and live runtime validators.
