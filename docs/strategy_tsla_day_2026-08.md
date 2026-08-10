# New Strategy — Same-Day TSLA Intraday (August 2026)

Date: 2026-08-05

> **RETIRED (2026-08-10).** The `tsladay` market profile
> (`config/paper_tsladay.env`, `config/live_tsladay.env`) and its code
> wiring in `bot/profile.py` have been removed from the codebase as part of
> a pivot to focus the active strategy work on NVDA/TSLA options
> (`docs/strategy_options_2026-08.md`). This doc is kept as historical
> record of the research behind it — see `docs/BUILD_LOG.md` for the
> removal entry.

## Why this doc exists

The `tsladay` market profiles (`config/paper_tsladay.env`,
`config/live_tsladay.env`) are a new, separate strategy from the `spy`-market
swing profile (`config/live_spy.env`, `SYMBOL=TSLA`, `v4-tsla-trend`,
see `docs/strategy_tsla_2026-08.md`). The swing config trades roughly once
every 5-6 weeks and holds for days — it does not capture TSLA's frequent
intraday up/down swings by design. This doc covers a same-day-only variant
built specifically to do that.

**No Alpaca keys are available in this environment** (they live only on EC2).
Unlike the swing research (which used 730 days of free Yahoo Finance hourly
bars), an intraday timeframe needs intraday history, and Yahoo's free feed
caps 15-minute bars at **~60 trading days** (2026-05-11 → 2026-08-05 at fetch
time). That is a materially thinner, single-market-regime sample compared to
the swing config's 2.9-year, multi-regime validation — this is flagged
throughout and is the main reason this ships paper-only for now.

## Why a separate strategy instead of tuning the swing config

Two design choices only make sense for a same-day system, not a multi-day
swing:

1. **Bidirectional.** The swing config is long-only because a short held
   overnight is exposed to an uncapped gap risk on a single volatile stock.
   A position that is always flat by the close carries no overnight risk, so
   shorting TSLA's down-legs is exactly as safe as going long its up-legs —
   and backtests about as profitable (see below).
2. **1 trade/day hard cap, enforced by `MAX_TRADES_PER_DAY=1`.** The account
   is $150 in a **cash account**. Cash accounts aren't subject to PDT, but
   are subject to **T+1 settlement**: buying, selling, then re-buying with
   those still-unsettled proceeds same-day is a **good-faith violation** (3 in
   a rolling 12 months restricts the account to settled-cash-only trading for
   90 days). With only one "unit" of capital, more than about one full
   buy+sell cycle per day isn't actually available without tripping this —
   so the strategy is designed around that constraint from the start, not
   just tested against it after the fact.

## Replay/live parity gap this surfaced

`bot/research.py::run_replay` (the offline backtest harness) never simulated
`ALLOW_OVERNIGHT_HOLDING=false` / `FLATTEN_BEFORE_CLOSE_MINUTES` — only the
live loop (`bot/main.py`, via `bot/trade_controls.py::evaluate_session_exit`)
did. Every prior backtest (including the QQQ and TSLA swing research) had
`ALLOW_OVERNIGHT_HOLDING=true` set, so this never mattered before. It matters
a great deal for a same-day design, so `run_replay` now calls
`evaluate_session_exit` on every bar a position is open, forcing the same
flatten-before-close and no-overnight-holding behavior the live bot would
apply. Regression tests: `tests/test_research_replay.py`
(`test_flatten_before_close_forces_intraday_exit`,
`test_overnight_position_force_closed_next_session`).

## Methodology

Used the repo's own optimizer (`bot/optimize_strategy.py`), fed TSLA 15-minute
bars directly (`yfinance`) instead of pulling from Alpaca — same walk-forward
+ 2x-slippage-stress + acceptance-check machinery as the swing research, with
two small, generically-useful extensions added rather than one-off hacks:

- `ALLOW_SHORTS` is now itself a configurable grid dimension
  (`OPT_ALLOW_SHORTS_VALUES`, default still `["false"]` — no behavior change
  for existing configs) instead of hardcoded off.
- The optimizer's trades/day acceptance band (default 0.5-3.0/day, tuned for
  swing-style signals) is now configurable
  (`OPT_SCORE_MIN_TRADES_PER_DAY` / `OPT_SCORE_MAX_TRADES_PER_DAY` /
  `OPT_ACCEPT_MIN_TRADES_PER_DAY` / `OPT_ACCEPT_MAX_TRADES_PER_DAY`), since a
  1-trade/day-capped, filter-heavy intraday config legitimately trades less
  than half the time.

Grid: SMA fast/slow spans, ADX threshold, 15m ATR% ceiling, trailing-stop
multiples, max bars in trade — kept **symmetric between long and short**
deliberately (fewer free parameters to fit against only 60 days of data).
Walk-forward: 24 train days / 8 test days → 4 windows. Base structure (not
grid-searched): 15m bars, 2-hour "intraday regime" gate in place of the swing
config's daily-EMA gate (a multi-day lookback doesn't fit a same-day system),
entry window 09:45-15:30 ET (skips opening-range noise and leaves time to
develop before the flatten window), `FLATTEN_BEFORE_CLOSE_MINUTES=15`.

## Result

Best-scoring candidate that passed every acceptance check (full-sample PF ≥
1.10, positive expectancy, trades/day in band, positive walk-forward test
net, ≥2 of 4 positive test windows, PF ≥ 1.0 under 2x slippage, max drawdown
≤ 10%, beats the loaded-profile baseline):

`SMA_FAST=8 SMA_SLOW=34 ADX_THRESHOLD=15 ATR_MAX_PCT=0.016 TRAIL_ATR_MULTIPLIER=1.2 TRAIL_AFTER_ATR_MULTIPLE=1.0 MAX_BARS_IN_TRADE=8`

At the grid search's sizing (80%/85% target/max notional, full sample, 60
trading days, $150 starting equity, default slippage):

| Metric | Value |
|---|---|
| Trades | 55 (30 short / 25 long) |
| Win rate | 53% (avg win $1.70 / avg loss -$0.94) |
| Profit factor | 2.02 |
| Net P&L | +$24.87 (+16.6% over ~3 months) |
| Max drawdown | 4.5% |
| Walk-forward test windows | 3/4 positive, test net +$21.87, test PF 7.26 |
| 2x slippage | net +$24.47, PF 1.995 (barely moves — low cost-sensitivity) |

Sanity checks before trusting this: it isn't a lucky-outlier artifact — the
single largest trade is 26% of total net P&L (top 3 trades 61%), against 55
total trades with a >50% win rate and a tightly bounded largest loss (-$2.42,
~1.6% of the $150 account). Both sides contribute real edge (short side
actually outperformed: $16.29 across 30 trades vs long's $8.58 across 25).

## Sizing: shipped smaller than the grid search validated

The grid search's 80%/85% notional sizing is the same aggressiveness as the
swing config's *original* QQQ setting — appropriate there because it had 2.9
years of evidence behind it. This strategy has ~3 months, one market regime.
Swept position sizing down on the winning candidate as an explicit margin of
safety, full sample:

| Target / Max notional | Net P&L | Profit factor | Max DD | 2x slippage PF |
|---|---|---|---|---|
| 80% / 85% (as found) | +$24.87 | 2.017 | 4.5% | 1.995 |
| **65% / 70% (shipped)** | **+$20.01** | **2.014** | **3.7%** | **1.992** |
| 50% / 55% | +$15.24 | 2.010 | 2.9% | 1.989 |
| 35% / 40% | +$10.56 | 2.007 | 2.0% | 1.986 |

Profit factor and slippage-robustness barely move with size (as expected —
sizing doesn't change which trades win or lose, just how much rides on each),
so this is purely a drawdown/absolute-P&L tradeoff. 65%/70% was picked as a
reasonable middle: still meaningful in dollar terms on $150, without pushing
single-trade risk to the top of what a ~60-day, single-regime backtest can
actually vouch for.

## What this is *not* validated against

- **Regime diversity.** 60 days is one slice of 2026 — some mix of trending
  and choppy sessions, but not a recession, a volatility spike, an
  earnings-driven single-day TSLA gap, or a low-liquidity holiday-week
  stretch. The swing config's 2.9-year backtest crossed multiple regimes;
  this hasn't.
- **Real intraday execution quality.** The backtest uses Yahoo's 15m bars and
  a flat per-share slippage assumption. Alpaca's free IEX feed
  (`bot/broker_alpaca.py`, `DataFeed.IEX`) is a single exchange's view, not
  the consolidated tape — real intraday fills, especially near the open/close
  or during fast moves, can differ more from the backtest than an hourly
  swing trade's fills would.
- **Walk-forward window count.** 4 windows is enough to catch a config that's
  wildly overfit, not enough to be confident about the split between "real
  edge" and "this particular 3 months."

## Recommendation

Paper only for now — `config/paper_tsladay.env` and the `paper-tsladay`
docker-compose service. Let it run a real stretch (multiple weeks, ideally
crossing different TSLA behavior — a trend leg, a chop stretch, an earnings
date) before considering `config/live_tsladay.env`. The live config file
exists for symmetry with the rest of the profile system and is fully wired
(`bot/profile.py` market `tsladay`, `docker-compose.yml`
`trade-tsladay`/`paper-tsladay`), but nothing deploys it automatically —
GitHub Actions / EC2 still default to the `spy` market
(`docs/github_actions_ec2.md`).

## Reproducing the research

`bot/optimize_strategy.py` (grid search + walk-forward + 2x-slippage stress +
acceptance checks) fed 15-minute TSLA bars from Yahoo Finance (`yfinance`,
`interval="15m"`, ~60 trading-day limit) instead of through
`bot/broker_alpaca.py`'s Alpaca client. Friction model: default
`RESEARCH_SLIPPAGE_PER_SHARE` (~$0.01/share), 2x-slippage stress test for the
acceptance bar.
