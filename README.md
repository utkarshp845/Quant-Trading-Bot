# Trading Bot

[![CI](https://github.com/utkarshp845/trading-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarshp845/trading-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)

A systematic trading bot for Alpaca, built and run against a real ~$150 live
account. This repo is as much a record of the mistakes and the evidence that
corrected them as it is working code — see [What I've Learned](#what-ive-learned)
before the code itself.

**Not financial advice.** This is a personal project for learning and
experimentation with a small amount of real money. Nothing here is a
recommendation to trade, and past replay/backtest performance does not
predict future results. See [Disclaimer](#disclaimer).

## Contents

- [What This Is](#what-this-is)
- [What I've Learned](#what-ives-learned)
- [Current Strategy & Results](#current-strategy--results)
- [How It Works](#how-it-works)
- [What It Uses](#what-it-uses)
- [Setup](#setup)
- [Profiles](#profiles)
- [Run / Validate / Monitor](#run)
- [Deployment](#deployment)
- [Risk Controls](#risk-controls)
- [Options (LCID)](#options-lcid)
- [Small Equity Accounts](#small-equity-accounts)
- [Docs](#docs)
- [Disclaimer](#disclaimer)

## What This Is

A trend-following bot that pulls bars from Alpaca, evaluates a rules-based
signal (moving averages, ADX, ATR, volume, and a higher-timeframe regime
filter), sizes and places orders through Alpaca's API, and tracks everything
— fills, P&L, risk-limit hits, rejection reasons — in SQLite so it can be
audited after the fact instead of trusted blindly.

It currently:

- pulls bars from Alpaca for stocks (IEX feed) and options underlyings
- generates trend-following signals on a configurable timeframe
- applies layered risk checks before entering trades (daily drawdown, consecutive losses, cooldown, hard stop)
- manages exits via trailing stop, hard stop, breakeven stop, profit lock, time stop, and trend reversal
- trades that same signal as either equity shares or long calls/puts (see [Options (LCID)](#options-lcid)) — the active focus right now is the daily LCID options paper-trading cycle, run as an explicit learning exercise
- records runs, orders, events, and closed trades in SQLite
- writes daily and monitoring reports to `reports/`

## What I've Learned

The interesting part of this project isn't the code, it's what running it
live with real (small) money exposed:

- **A strategy that looks reasonable on paper can be structurally unable to
  make money at a given account size.** The original live setup traded
  BTC/USD on 5-minute bars with $150 of capital. Over 120 days it made
  exactly 2 trades — the entry filter stack required ~14 conditions to align
  at once. Position sizes were capped around $45, and Alpaca's crypto
  round-trip friction (~0.6%, taker fee + spread) was often larger than the
  trade's entire expected profit. It wasn't unlucky — it was unwinnable by
  construction.
- **"Trade more, smaller" made it worse, not better — I tested it instead of
  assuming.** A loosened, higher-frequency BTC variant made 184 trades over a
  year and lost $95, of which ~$99 was pure fee friction. On a small crypto
  account, activity itself is the cost. This only became obvious by replaying
  it against real historical data rather than reasoning about it in the
  abstract.
- **The venue mattered more than the signal.** The same trend logic, run on
  equities (near-zero commission) instead of crypto (~0.6% round trip),
  turned from a loser into a net-positive strategy in replay — without
  changing the core idea, just the friction it had to overcome.
- **Backtests can lie quietly through infrastructure bugs, not just overfitting.**
  While diagnosing this, I found the replay harness never reset its
  consecutive-loss counter on day rollover the way the live code does — so
  any backtest that hit its loss-streak limit silently stopped trading for
  the rest of the test period. Every prior research report in this repo was
  undercounting trades because of it. Fixed in
  [`bot/trade_controls.py`](bot/trade_controls.py); full writeup in
  [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).
- **Cron intervals need to match the strategy's timeframe, not just "run
  often."** Moving from 5-minute to hourly bars and leaving the deploy cron
  at every 5 minutes would have meant ~12x redundant invocations per hour —
  harmless (guarded by cooldown checks) but a real signal that deploy
  config and strategy config can silently drift apart if nobody checks.

Full investigation with numbers: [`docs/strategy_revamp_2026-07.md`](docs/strategy_revamp_2026-07.md).
Change-by-change history: [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

## Current Strategy & Results

The default live/paper deployment is an **hourly TSLA trend-following
strategy** sized for a small account: long-only, fractional, ~60% notional
per position, a daily-EMA regime filter, and trailing exits that hold
winning trends for days rather than minutes. Position sizing is trimmed
relative to the earlier QQQ profile because TSLA's hourly volatility runs
~3x QQQ's.

**Active focus right now: LCID options, paper only.** The same trend
signal is also expressed as long calls/puts. Symbol history: NVDA+TSLA
(2026-08-10) → TSLA-only (2026-08-21) → **LCID** (2026-08-21, same day) —
TSLA never had an affordable contract at this account's real ~$500 equity,
so after screening 10+ cheaper symbols and walk-forward-optimizing the top
3, LCID was picked as the only one showing a positive modeled edge. That
edge is thin (concentrated in 2 trades in one year), so this is run
explicitly as a learning/paper exercise, not a validated strategy — see
[Options (LCID)](#options-lcid). Two earlier strategies — a defensive
BTC/USD profile and an experimental same-day TSLA intraday variant
(`tsladay`) — were retired and removed from the active codebase; see
[`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) for why, and
`docs/strategy_revamp_2026-07.md` / `docs/strategy_tsla_day_2026-08.md` for
the retired evidence.

Replay results, `$150` starting capital, real historical bars, realistic
slippage assumptions (see the strategy docs for methodology — **these are
backtest results, not live account performance**):

| Profile | Period | Net P&L | Profit Factor | Max Drawdown | Trades |
|---|---|---|---|---|---|
| TSLA hourly trend (live default) | 2023-09 → 2026-08 (~2.9 yrs) | +$33.0 (+22%) | 1.56 | 9.4% | 28 (~9/yr) |
| *(retired)* QQQ hourly trend | 2023-08 → 2026-07 (~3 yrs) | +$55.3 (+37%) | 1.76 | 6.7% | 74 (~2/mo) |
| *(retired)* BTC hourly, strict uptrend gate | 2025-26 (bear year, BTC −46%) | $0.00 | — | 0% | 0 (stayed flat) |
| *(retired)* BTC hourly, strict uptrend gate | 2024-25 (bull year) | +$3.1 | 1.14 | 10.6% | 12 |
| *(retired)* BTC 5m scalp, live config | 2026-03 → 2026-07 (real bars) | −$0.85 | 0.0 | 0.6% | 2 |

LCID options isn't in this table yet — there's no free historical
options-chain data, so its "backtest" is a modeled Black-Scholes replay over
stock prices rather than real historical options fills; see
[Options (LCID)](#options-lcid) for why that's not comparable to
the rows above, and why paper trading is the actual validation step for it.

**Caveat on the TSLA numbers**: almost all of that +$33.0 came from one
strong trend year (2024: +$52.0); 2023, 2025, and 2026 were each roughly flat
to slightly negative. See [`docs/strategy_tsla_2026-08.md`](docs/strategy_tsla_2026-08.md)
for the full breakdown — this is a lumpier, thinner-sample edge than the QQQ
config it replaced.

## How It Works

```
Alpaca bars → indicators (SMA/EMA/ADX/ATR/volume) → regime filter (higher timeframe)
           → signal (LONG/HOLD) → risk gate (drawdown, loss streak, cooldown, staleness)
           → position sizing → order submission → SQLite (runs, orders, closed trades, events)
           → reports (daily / monitor / research / optimize)
```

- **Signal:** [`bot/strategy_ma.py`](bot/strategy_ma.py) — SMA trend cross confirmed by ADX, ATR-bounded volatility, a trend EMA, and a higher-timeframe regime filter (e.g. daily EMA for the equity profile).
- **Risk:** [`bot/risk.py`](bot/risk.py), [`bot/trade_controls.py`](bot/trade_controls.py) — daily drawdown/loss caps, consecutive-loss halt, entry cooldown, stale-data rejection, position-notional caps.
- **Execution:** [`bot/broker_alpaca.py`](bot/broker_alpaca.py), [`bot/main.py`](bot/main.py) — order submission, fill reconciliation, broker position sync.
- **Persistence:** [`bot/store.py`](bot/store.py) — SQLite schema for runs, orders, position state, closed trades, and events, so every decision (including ones that resulted in *no* trade) is auditable later.
- **Research:** [`bot/research.py`](bot/research.py), [`bot/optimize_strategy.py`](bot/optimize_strategy.py) — replay/backtest harness and walk-forward parameter search, used to generate the results above.

## What It Uses

- Python 3.11
- Alpaca API (`alpaca-py`)
- SQLite
- Docker / Docker Compose
- GitHub Actions (CI + EC2 deploy)

## Setup

1. Copy `.env.example` to `.env`
2. Add your Alpaca keys (paper or live)
3. Choose a profile config from `config/` or adjust settings directly in `.env`

Key env values:

| Variable | Default | Description |
|---|---|---|
| `ALPACA_API_KEY` | — | Alpaca API key |
| `ALPACA_SECRET_KEY` | — | Alpaca secret key |
| `ALPACA_PAPER` | `true` | Set `false` for live trading |
| `SYMBOL` | `SPY` | Any equity or `BTC/USD` for crypto |
| `IS_CRYPTO` | `false` | Set `true` to enable crypto mode (24/7 market, fractional qty, GTC orders) |
| `TIMEFRAME_MINUTES` | `5` | Bar timeframe in minutes |
| `POSITION_SIZING_MODE` | `fixed` | `fixed`, `notional_cap`, or `atr_risk` |
| `ALLOW_FRACTIONAL_EQUITIES` | `false` | Allow fractional equity quantities for small live stock accounts |
| `MIN_ORDER_NOTIONAL` | `1.0` | Minimum dollar exposure for fractional stock or crypto entries |
| `HARD_STOP_ATR_MULT` | `0` | Hard stop distance in ATR units from entry (0 = disabled) |
| `ENABLE_BREAKEVEN_STOP` | `false` | Move stop to breakeven after N ATR of profit |
| `ENABLE_PROFIT_LOCK` | `false` | Lock in partial profit after N ATR move |
| `MAX_DAILY_DRAWDOWN_PCT` | `0.01` | Halt entries after this % daily loss |
| `MAX_DAILY_LOSS` | `0` | Dollar daily loss cap (0 = disabled) |
| `ALLOW_OVERNIGHT_HOLDING` | `false` | Keep positions overnight (set `true` for crypto) |
| `FLATTEN_BEFORE_CLOSE_MINUTES` | `5` | Force flat this many minutes before 4 PM ET (set `0` for crypto) |

These are the raw code-level defaults (used only if you run `bot.main`
directly without a profile). Every shipped profile in `config/` overrides
the relevant ones — see [Profiles](#profiles).

## Profiles

Pre-built configs live in `config/`:

| File | Symbol | Use |
|---|---|---|
| `config/paper_spy.env` | TSLA | Paper trading equities (hourly trend, multi-day holds) |
| `config/live_spy.env` | TSLA | Small-account live equities — the recommended live profile |
| `config/paper_options.env` | LCID | **Active focus.** Long calls/puts on the equity trend signal, paper only, explicitly a learning exercise — see [Options (LCID)](#options-lcid) below and `docs/strategy_options_lcid_2026-08.md` |
| `config/live_options.env` | LCID | Same as above; not recommended live yet — running the paper profile for a real stretch first is the whole point |

A defensive BTC/USD profile and an experimental same-day TSLA intraday
profile (`tsladay`) previously shipped here and have been retired — see
`docs/strategy_revamp_2026-07.md` and `docs/strategy_tsla_day_2026-08.md`
for the evidence behind why, kept as historical record.

See `docs/strategy_tsla_2026-08.md` for the replay evidence behind the
`spy`-market equity profile (and `docs/strategy_revamp_2026-07.md` for the
earlier QQQ revamp it replaced).

Load a profile by setting `BOT_PROFILE` / `BOT_MARKET`, by sourcing the file before running, or with the profile runner:

```powershell
python -m bot.profile_runner paper trade spy
python -m bot.profile_runner live trade spy
python -m bot.profile_runner paper trade options
```

Before relying on a schedule, verify both the paper account and market-data feed (this never places an order):

```bash
python -m bot.profile_runner paper connectivity options
```

## Run

```bash
# Paper trade (equity config, Alpaca paper account)
docker compose run --rm paper

# Live trade (equity config, Alpaca live account)
docker compose run --rm trade

# LCID long calls/puts (paper only for now, a learning exercise — see
# Options section below)
docker compose run --rm paper-options

# Generate monitor report
docker compose run --rm monitor

# Generate paper equity monitor report
docker compose run --rm paper-monitor

# Generate paper options monitor report
docker compose run --rm paper-options-monitor

# Generate today's real (non-synthetic) daily Markdown report for paper-options
docker compose run --rm paper-options-daily

# Validate the full build and runtime setup
docker compose run --rm validate
```

## Validate

Run the built-in runtime validation:

```powershell
docker compose run --rm validate
```

This checks:

- runtime directories
- database setup
- signal generation
- risk evaluation
- report generation

## Monitor

Generate the latest monitor report:

```powershell
docker compose run --rm monitor
```

Or use the profile-specific monitors:

```powershell
docker compose run --rm paper-monitor
docker compose run --rm live-monitor
```

The monitor report includes recent rejection counts, near-miss entry bars, and the latest strategy metrics (`regime_side`, `momentum_pct`, `pullback_depth_atr`, `bar_range_atr`, `volume_ratio`, and `signal_strength`) so quiet live periods can be diagnosed from recorded bot state.

Run the research / replay report:

```powershell
docker compose run --rm research
```

Run the walk-forward optimizer:

```powershell
docker compose run --rm optimize
```

The optimizer isn't implemented for the options market yet — use `research`
to generate a report there instead (`docker compose run --rm
research-options-tsla`).

The optimizer now logs progress while it runs. For a quick smoke test, cap the search first:

```powershell
$env:OPT_MAX_CANDIDATES="10"
docker compose run --rm optimize
```

Main outputs:

- `reports/monitor_latest.md`
- `reports/monitor_latest.json`
- `reports/daily_YYYY-MM-DD.md`
- `reports/research_latest.md`
- `reports/research_latest.json`
- `reports/optimize_latest.md`
- `reports/optimize_latest.json`

The optimizer compares candidates against the loaded live baseline over the same bars, for either market. A candidate is marked accepted only if it clears the full replay, walk-forward, baseline-improvement, and 2x-slippage checks in the report.

## Deployment

The bot deploys to a plain EC2 instance via GitHub Actions: the workflow at
`.github/workflows/deploy-ec2.yml` rsyncs the repo over SSH, builds the
Docker image on the instance, and installs cron schedules — no container
registry or AWS IAM setup involved. Full setup steps, required secrets, and
verification commands are in `docs/github_actions_ec2.md`.

It triggers automatically on pushes to `master`, or manually from the
Actions tab with a chosen profile (`live`/`paper`); the deploy market is
fixed at `spy` (the live equity strategy). Every deploy also validates and
schedules the `paper-options` profile on its own independent cron (default
on; `install_options_cron: false` on a manual dispatch to skip it) — that's
what keeps the daily LCID options paper evaluation running unattended.

### Security Notes

- Restrict EC2 security groups to only allow SSH from your IP
- Generate a dedicated SSH key pair for deployment
- Store sensitive credentials only in GitHub secrets and the `.env` file on EC2
- Regularly rotate API keys and SSH keys
- Never commit `.env`, `.pem`, or any real API key — `.gitignore` blocks the common patterns, but double-check before pushing

## Project Structure

- `bot/` - trading logic, broker integration, storage, reporting
- `config/` - per-profile env files (`live_spy`, `paper_spy`, `live_options`, `paper_options`)
- `data/` - SQLite database
- `logs/` - runtime logs and CSV snapshots
- `reports/` - generated reports
- `docs/` - strategy audits, build log, and deployment guides

## Risk Controls

The bot has multiple independent safety layers:

| Control | Config Key | Default |
|---|---|---|
| Hard stop loss | `HARD_STOP_ATR_MULT` | disabled |
| Trailing stop | `TRAIL_ATR_MULTIPLIER` | 1.5x ATR |
| Breakeven stop | `ENABLE_BREAKEVEN_STOP` | false |
| Profit lock | `ENABLE_PROFIT_LOCK` | false |
| Time-based stop | `MAX_BARS_IN_TRADE` | 12 bars |
| Regime-invalidation exit | `EXIT_ON_REGIME_INVALIDATION` | true |
| Daily drawdown halt | `MAX_DAILY_DRAWDOWN_PCT` | 1% |
| Dollar loss cap | `MAX_DAILY_LOSS` | disabled |
| Consecutive loss halt | `MAX_CONSECUTIVE_LOSSES` | 3 |
| Max trades per day | `MAX_TRADES_PER_DAY` | 5 |
| Entry cooldown | `COOLDOWN_BARS` | 2 bars |
| Stale data check | `ENABLE_STALE_BAR_CHECK` | false |

## Options (LCID)

**This is the active strategy focus right now — run explicitly as a
learning exercise, not a validated strategy.** `config/paper_options.env`
/ `config/live_options.env` trade **actual option contracts** (long
calls/puts) on LCID, reusing (unmodified) the same trend signal already
validated for TSLA equities. Symbol history: NVDA+TSLA → TSLA-only → LCID,
all on 2026-08-21/thereabouts, because TSLA (and NVDA before it) never had
an affordable contract at this account's real ~$500 equity — see
[`docs/strategy_options_lcid_2026-08.md`](docs/strategy_options_lcid_2026-08.md)
for the full screening evidence, including why LCID's backtested edge
should be treated with real caution (net profit concentrated in 2 trades in
one year). Highlights:

- **Both directions, bounded risk.** Unlike the equity profiles
  (`ALLOW_SHORTS=false`, because naked stock shorting has undefined risk on
  a small cash account), the options profile buys puts in confirmed
  downtrends and calls in confirmed uptrends — a long option's risk is
  capped at the premium paid either way.
- **Contract selection is the strategy.** Targets a moderate delta (~0.65)
  and 30–45 days to expiration, exits by 12 DTE remaining, skips contracts
  with wide bid-ask spreads, and blocks new entries near earnings. The goal
  is to keep the contract's price behavior close to the underlying's real
  (modest) edge, not to buy cheap far-OTM lottery tickets.
- **One position at a time.** (`bot/options_engine.py` still loops over
  `OPTION_SYMBOLS`, kept generic in case a second symbol is added back
  later, but the profile only lists LCID today.)
- **Why LCID, not TSLA.** At the target 0.65 delta / 25-45 DTE / 55%-budget
  targets, TSLA produced **zero affordable trades** in a full 2.9-year
  modeled replay at the account's real ~$500 equity. LCID's much lower
  share price makes contracts affordable at the same targets — ~20 trades
  in the same replay window — which is why it was picked after screening
  10+ cheaper symbols.
- **Thin, concentrated backtest edge — treat with real caution.** LCID's
  modeled net profit (+$2,810, PF 1.72 over 20 trades) is driven almost
  entirely by 2 trades in 2024; 2025 was flat and 2026 (partial) was
  negative. A hard-stop-widening and delta/DTE sweep specifically on LCID
  found the unmodified signal already outperforms every variant tried, so
  no further tuning was applied — but that also means this hasn't been
  independently validated the way the TSLA equity signal has. This is run
  as a learning exercise: expect real losses are possible, not just a
  technicality.
- **Backtest caveat:** there's no free historical options-chain data, so
  `bot/options_research.py` models contract prices with Black-Scholes over
  historical stock prices rather than replaying real historical options
  quotes. Every report is headed accordingly — treat it as a directional
  sanity check, not proof of live viability.

Requires options trading to be enabled on the Alpaca account first (a
compliance approval in Alpaca's own dashboard, separate for paper and
live) — verify with:

```bash
python -m bot.profile_runner paper connectivity options
```

`config/live_options.env` exists so the live path is ready, but it's **not
recommended yet**: run `docker compose run --rm paper-options` for a real
stretch first.

### One-week paper evaluation

The current plan: run `paper-options` for about a week to gather fills,
contract-selection quality, and P&L before tuning anything or considering
live capital. This now runs **unattended on EC2** — the deploy workflow
installs an independent cron schedule for it (trade hourly, monitor hourly,
daily report once a day) alongside whatever profile/market it's deploying
for the equity strategy, see [Deployment](#deployment) and
`docs/github_actions_ec2.md`. No need to trigger cycles by hand.

To run it manually instead (e.g. to smoke-test locally with working paper
keys, before or between EC2 deploys):

```bash
docker compose run --rm paper-options
docker compose run --rm paper-options-daily
docker compose run --rm paper-options-monitor
```

Read `reports/daily_YYYY-MM-DD.md` (per-day activity, including any option
contracts opened/closed that day) and `reports/monitor_latest.md` (rolling
health/rejection view) each day. At the end of the week, use what those
reports show — trade frequency, contract selection quality (delta/DTE
actually achieved vs. targeted), fills, and any rejection patterns — to
decide whether to tune the contract-selection or signal parameters in
`config/paper_options.env` before running another stretch, rather than
moving to `config/live_options.env`.

## Small Equity Accounts

For a roughly `$150` account, one whole share of most stocks is too large a
chunk of the account to size or diversify sensibly. `config/live_spy.env`
(despite the filename, it trades `TSLA`) already sets this up:

- `ALLOW_FRACTIONAL_EQUITIES=true`
- `ALLOW_SHORTS=false`
- `POSITION_SIZING_MODE=notional_cap` with `TARGET_POSITION_NOTIONAL_PCT=0.60` — most, but not all, of the account in a single position; trimmed down from the 0.90 used for the lower-volatility QQQ profile it replaced (see `docs/strategy_tsla_2026-08.md`)
- `MAX_POSITION_NOTIONAL_PCT` above the target as a hard ceiling

## Docs

- [`docs/strategy_options_lcid_2026-08.md`](docs/strategy_options_lcid_2026-08.md) — **the active strategy.** LCID long calls/puts: the full symbol-screening evidence (10+ candidates, walk-forward optimization, why TSLA/F/SOFI/NIO were ruled out), and why LCID's edge is thin and this runs as a learning exercise
- [`docs/strategy_tsla_options_2026-08.md`](docs/strategy_tsla_options_2026-08.md) — TSLA-only interim step (2026-08-21): why NVDA was dropped, baseline modeled-backtest results; superseded by the LCID doc above, kept as historical record
- [`docs/strategy_options_2026-08.md`](docs/strategy_options_2026-08.md) — original NVDA/TSLA rationale (contract selection rules, friction, affordability, modeled-backtest caveat); partially superseded, kept as historical record
- [`docs/strategy_tsla_2026-08.md`](docs/strategy_tsla_2026-08.md) — the investigation and evidence behind the live TSLA equity strategy this signal is seeded from
- [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) — running log of strategy and infrastructure changes
- [`docs/github_actions_ec2.md`](docs/github_actions_ec2.md) — EC2 deployment setup
- [`OPERATIONS.md`](OPERATIONS.md) — day-to-day commands (run, monitor, daily report, research, optimize)
- **Retired, kept as historical record:** `docs/strategy_revamp_2026-07.md` (BTC/QQQ, superseded by the TSLA equity strategy), `docs/strategy_tsla_day_2026-08.md` (the `tsladay` intraday variant, removed from the codebase), `docs/strategy_audit_current.md`, `docs/live_account_path_100usd.md` (earlier, now-superseded audits)

## Notes

- Keep real API keys only in your local `.env` — never commit them.
- Without a profile, the raw `bot.main` default is an intraday equity system: it flattens inherited overnight positions on the next session and exits before market close. `config/live_spy.env` and `config/live_options.env` both set `ALLOW_OVERNIGHT_HOLDING=true` and `FLATTEN_BEFORE_CLOSE_MINUTES=0`, since the current strategy is a multi-day trend hold, not an intraday one.
- The `spy`-market runners write to `runtime/paper` and `runtime/live`; `options`-market runners write to `runtime/paper_options` and `runtime/live_options`.
- Use the optimizer to rank parameter sets on walk-forward windows before going live.
- Run `scripts/validate.ps1` (Windows) or `scripts/validate.sh` (Unix) for a full local validation pass.

## Disclaimer

This is a personal, educational project. It is not investment advice, and
nothing in this repository — including the replay results above — is a
recommendation to buy, sell, or hold any security or asset. Backtest and
replay performance do not guarantee future results; they use simplified
fill/slippage assumptions and a limited historical window. Trading involves
risk of loss, including total loss of capital. The author is not a
registered investment advisor. Use at your own risk, and never trade with
money you cannot afford to lose.
