# Build Log

Running log of strategy and infrastructure changes to this bot. Newest entry
first. Each entry should say what changed, why (with evidence where
possible), and what to watch after deploying it.

---

## 2026-08-21 — Options underlying switched TSLA → LCID after symbol screen; explicit learning exercise

**What:** Same-day follow-up to the TSLA-only narrowing below. The TSLA-only
baseline found **zero affordable trades** at the account's real ~$500
equity — user directed switching to a cheaper underlying instead. Screened
10 cheaper, liquid, optionable candidates; walk-forward-optimized the top 3
(LCID/F/SOFI); deep-dove F/SOFI/NIO to test whether the TSLA hard-stop fix
generalized. Full record: `docs/strategy_options_lcid_2026-08.md`.

**Findings:**
- LCID was the only candidate (of 10 screened) showing a positive modeled
  edge (PF 1.72, 20 trades, net +$2,810) — but its net profit is
  concentrated in 2 trades in 2024; 2025 was flat and 2026 was negative.
  LCID also fell ~$27→$6 over the sample, a real structural decline, not
  noise.
- The TSLA hard-stop finding (widening it helps) does **not** generalize:
  it helps F, hurts LCID, is mixed on SOFI, and doesn't matter for NIO.
- F, SOFI, NIO all remained net-negative even after real tuning (walk-forward
  grid search + hard-stop/delta/DTE sweeps) — not shipped.
- A dedicated sweep on LCID confirmed the already-shipped, unmodified
  parameters (hard_stop=2.5, delta=0.65, DTE 25-45) already outperform every
  variant tried — no signal/contract-selection changes were made.

**Decision:** ship LCID anyway, explicitly framed as a learning/paper
exercise (generate and analyze real option trades) rather than a validated
strategy — user's call after seeing the full evidence above.

**Changes:**
- `config/paper_options.env` / `config/live_options.env`: `SYMBOL` /
  `OPTION_SYMBOLS` → `LCID`; `STRATEGY_VERSION` → `v3-options-lcid-learning`.
  No numeric parameter changes.
- `bot/profile.py`, `bot/options_engine.py`, `bot/options_research.py`,
  `bot/profile_runner.py`, `bot/report_daily.py`, `docker-compose.yml`
  (renamed `research-options-tsla` → `research-options-lcid`): defaults and
  docstrings updated.
- `README.md`, `OPERATIONS.md`, `docs/github_actions_ec2.md`,
  `.github/workflows/deploy-ec2.yml`: updated to LCID, framed as a learning
  exercise.
- `tests/test_profile.py`: updated `SYMBOL`/`OPTION_SYMBOLS` assertions.
- `docs/strategy_tsla_options_2026-08.md`: flagged superseded.

**What to watch:** real Alpaca fills vs. modeled premiums (LCID is more
volatile/lower-priced than TSLA, so the Black-Scholes/realized-vol proxy
could be further off), whether existing risk limits are adequate for a more
volatile underlying, and — the actual point — trade frequency and variety
for learning purposes over raw P&L.

---

## 2026-08-21 — Options strategy narrowed to TSLA-only; refinement baseline established

**What:** Per user request, narrowed the options profile from NVDA+TSLA to
TSLA only, and shifted from the original fixed one-week paper evaluation to
an open-ended "refine, then run daily" cadence. Full writeup:
`docs/strategy_tsla_options_2026-08.md`.

**Changes:**
- `config/paper_options.env` / `config/live_options.env`: `OPTION_SYMBOLS`
  and `SYMBOL` narrowed to `TSLA`; `STRATEGY_VERSION` →
  `v2-options-tsla-only`. No signal/contract-selection/risk parameters
  changed yet.
- `bot/profile.py` options-market defaults, `bot/options_engine.py`,
  `bot/options_research.py`, `bot/profile_runner.py`, `bot/report_daily.py`:
  default fallbacks and docstrings updated to TSLA-only; the multi-symbol
  loop in `bot/options_engine.py` was kept generic rather than hard-coded.
- `docker-compose.yml`: removed the `research-options-nvda` service.
- `README.md`, `OPERATIONS.md`, `docs/github_actions_ec2.md`,
  `.github/workflows/deploy-ec2.yml`: updated to TSLA-only and to the
  ongoing-daily-evaluation framing (was "one-week").
- `tests/test_profile.py`: updated `SYMBOL`/`OPTION_SYMBOLS` assertions.
- `docs/strategy_options_2026-08.md`: flagged partially superseded, pointing
  to the new doc; kept as historical record (contract-selection rules,
  friction, and affordability rationale in it are all still accurate).

**Baseline finding (modeled backtest, real TSLA hourly bars, unchanged
inherited parameters):** at the account's actual ~$500 starting equity, the
current delta/DTE/budget targets find **zero affordable contracts** in the
full 2.9-year sample. At $10,000 (large enough to see a sample), the
inherited config is unprofitable — profit factor 0.76, driven almost
entirely by `hard_stop` exits (-$20,798 across 12 trades) that are far more
punishing in premium terms than the same ATR-based stop is on the equity
signal, and by puts underperforming calls badly (-$505/trade avg vs
-$69/trade avg). One large winner supplies nearly all the (still negative)
net result. Full breakdown and candidate refinements in
`docs/strategy_tsla_options_2026-08.md`.

**What to watch:** this is a starting point, not a shipped change to the
signal — no parameters were tuned in this pass. Next step is backtest-driven
iteration on the hard-stop sizing, calls-only vs. calls+puts, and
delta/DTE/budget targets before any of it runs against real paper fills
again.

---

## 2026-08-10 — Retired BTC and tsladay; options is now the active strategy focus

**What:** Removed the BTC/USD profile (`config/paper_btc.env`,
`config/live_btc.env`, the `btc` market in `bot/profile.py` including
`LIVE_BTC_SAFETY_ENV`, and the associated docker-compose services, CI steps,
and tests) and the experimental same-day TSLA intraday profile (`tsladay`,
same treatment) from the active codebase. Both are kept as historical record
in `docs/strategy_revamp_2026-07.md` and `docs/strategy_tsla_day_2026-08.md`
(now flagged retired at the top of each). The live TSLA equity strategy
(`spy` market, `config/live_spy.env`) is unaffected and keeps running live.

**Why:** The user is starting a week-long paper-only evaluation of the
NVDA/TSLA long-calls/puts options strategy (`docs/strategy_options_2026-08.md`)
and asked to remove strategies that are no longer the focus rather than
carry them forward as unused surface area.

**Also added — daily reporting for the options profile:**
- New `daily` action in `bot/profile_runner.py`, wired to
  `bot/report_daily.py`, so a real (non-synthetic) daily report can be
  generated for any profile/market on demand
  (`python -m bot.profile_runner paper daily options`, or
  `docker compose run --rm paper-options-daily`). Previously `report_daily`
  was only invoked internally by `bot/validate_runtime.py` against synthetic
  sample data.
- `bot/report_daily.py` now labels the report with `OPTION_SYMBOLS` (not
  just the single `SYMBOL` env) and adds an "Options Positions Today"
  section — contract symbol, delta, DTE, and P&L for anything opened/closed
  that day — sourced from the `option_position_opened` /
  `option_position_closed` events `bot/options_engine.py` already records.

**What to watch:** `.github/workflows/ci.yml` now validates the `paper` and
`live` `options` profiles instead of `btc`; `docs/github_actions_ec2.md` /
`deploy-ec2.yml` are unchanged (deploy market stays fixed at `spy`) since the
options profile is still run manually during its paper evaluation, not
deployed via that workflow.

## 2026-08-05 — Fixed hourly-bar fetch crash; switched equity profile QQQ → TSLA

**Bug fix:** `bot/broker_alpaca.py::get_historical_bars` built Alpaca's
`TimeFrame` as `TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)`
unconditionally. Alpaca's SDK rejects Minute-unit amounts outside 1-59, so
`TIMEFRAME_MINUTES=60` (every hourly-profile config) crashed every run after
the 20s startup sleep with `ValueError: Second or Minute units can only be
used with amounts between 1-59`, wiping the whole trade cycle instead of
recording a HOLD. Added `_resolve_timeframe()` to express 60 minutes as
`TimeFrame(1, Hour)` and any exact multiple of a day as `TimeFrame(1, Day)`,
falling back to Minute only for genuinely sub-hour timeframes. Regression
tests in `tests/test_broker_alpaca.py`.

**Strategy change:** `config/live_spy.env` and `config/paper_spy.env` switched
from `SYMBOL=QQQ` to `SYMBOL=TSLA`. This was not a drop-in swap — TSLA's
hourly ATR% runs ~3x QQQ's, and replaying the QQQ-tuned config unchanged on
TSLA produced a 29.6% max drawdown (vs QQQ's 6.7%) for essentially breakeven
P&L. Re-ran the repo's walk-forward optimizer (`bot/optimize_strategy.py`)
against TSLA bars (yfinance, no Alpaca keys available outside EC2) and
re-tuned entry filters and position sizing specifically for TSLA. Shipped
config: 28 trades / ~2.9yr, PF 1.56 (1.55 under 2x slippage), max DD 9.4%,
net +$33.0 (+22%) on $150 — but almost all of that P&L came from the 2024
rally year; 2023/2025/2026 were each roughly flat-to-negative. Full
methodology and every acceptance-check number: `docs/strategy_tsla_2026-08.md`.
**Watch:** run paper alongside live for a while before trusting this fully —
the sample (28 trades) is much thinner than QQQ's (74), and the edge is
concentrated in one good year.

---

## 2026-07-12 — Repo alignment checkover

**What:** Swept the repo for leftover references to the old BTC-5m-default
setup after the strategy revamp below, since several docs/scripts still
assumed BTC was the primary deployed strategy and that bars were 5 minutes.

**Found and fixed:**
- Cron schedule (`deploy/ec2/install_cron.sh`) still defaulted to
  `*/5 * * * *`, i.e. every 5 minutes, but every profile now trades hourly
  bars (`TIMEFRAME_MINUTES=60` in all four `config/*.env` files). Left as-is,
  a fresh EC2 deploy would have invoked the bot ~12x per hour for one hourly
  decision — harmless (cooldown/pending-order guards prevent duplicate
  orders) but wasteful API calls and log noise. Changed the default to
  `5 * * * *` (once per hour, 5 minutes after each bar closes).
- `docs/github_actions_ec2.md` explicitly said "the deployed profile is BTC,
  24/7 scheduling is the correct default" — no longer true now that `spy`
  (QQQ) is the default deploy market. Updated.
- `OPERATIONS.md`: paper research equity doc said `$250`; both paper profiles
  now use `$150` to match the real account. Also removed a line claiming the
  BTC paper profile uses "larger sizing" than live — it's an exact mirror of
  `live_btc.env` now, by design, so paper fills validate the same strategy
  that runs live.
- `README.md`:
  - Opening description and Crypto section still framed BTC as the always-on
    24/7 default with 5-minute cron; corrected.
  - The "Deployment" section described AWS ECR, IAM roles, `run.sh`,
    `deploy.sh`, and pushing to a `main` branch — none of which exist in this
    repo. The actual deploy path is GitHub Actions → SSH/rsync → EC2 cron,
    already documented in `docs/github_actions_ec2.md`. Replaced the stale
    section with a pointer to that doc.
  - Removed the `REVERSAL_SIGNAL_STRENGTH_MIN` row from the risk-controls
    table — grepping `bot/` shows no code reads that variable (only test
    fixtures set it). It's vestigial from an earlier strategy iteration;
    flagged separately rather than touched here since removing it fully
    means editing `tests/test_research_replay.py`, which is out of scope for
    a docs pass.
- Added "superseded" banners to `docs/strategy_audit_current.md` and
  `docs/live_account_path_100usd.md` (both dated 2026-04-14, both centered on
  an intraday SPY strategy that no longer exists) pointing at
  `docs/strategy_revamp_2026-07.md`. Kept their original content intact as a
  historical record rather than rewriting them.

**Not changed:** `bot/main.py`'s hardcoded `SYMBOL` default of `SPY` and
`TIMEFRAME_MINUTES` default of `5` — these are fallbacks for running the bot
without a profile at all (e.g. `python -m bot.main` with a bare `.env`).
Every shipped profile overrides both, so this doesn't affect deployed
behavior, but it means an operator who skips the profile runner entirely
still gets the old defaults. Worth a follow-up if that path is ever used for
real.

**Verification:** `pytest` (66 passed), `bot.validate_runtime`,
`bot.validate_profile_env` for all four profiles — all still pass; this
entry was docs/config/deploy-script only, no strategy logic touched.

---

## 2026-07-12 — Strategy revamp: QQQ hourly trend replaces BTC 5m scalp

**What:** Investigated why the live BTC bot ($150 account, 5-minute bars) was
making no valuable trades, then replaced the live deployment strategy.

**Root cause (full writeup: `docs/strategy_revamp_2026-07.md`):**
- The live filter stack required ~14 conditions to align at once; replayed
  over 120 days of real bars it produced 2 trades, both losses.
- Position sizing capped trades at ~$45; Alpaca crypto's ~0.6% round-trip
  friction exceeds the gross P&L of most 5-minute trades, so even a trade
  that fired couldn't clear its own costs.
- BTC fell ~46% over the trailing year and Alpaca doesn't support shorting
  crypto, so a long-only bot had no tailwind.
- Directly tested the "more, smaller trades" hypothesis: a loosened
  high-frequency BTC config made 184 trades/year and lost $95, ~$99 of it
  pure fee friction. Activity was the cost, not the fix, on this venue/size.

**Changes:**
- `config/live_spy.env` + `config/paper_spy.env`: switched to `SYMBOL=QQQ`,
  hourly bars, long-only trend-following, ~90% notional fractional sizing,
  daily-EMA(20) regime gate, wide trailing exits, multi-day holds. Replay
  2023-08→2026-07 at $150 start: net +$55.3 (+37%), profit factor 1.76, win
  rate 45%, max drawdown 6.7%, positive every calendar year, stable under 3x
  slippage stress.
- `config/live_btc.env` + `config/paper_btc.env`: kept BTC live but made it
  defensive — hourly bars, strict 4h-EMA(120) uptrend gate
  (`REGIME_MIN_SLOPE_PCT=0.008`). Zero trades in the 2025-26 bear-year replay
  (capital preserved through a 46% market decline); mildly positive
  (+$3, PF 1.14) in the 2024-25 bull-year replay.
- `bot/trade_controls.py::sync_replay_day`: fixed the replay harness to reset
  `consecutive_losses` on ET day rollover, matching `bot/store.py`'s live
  behavior. Previously the replay never reset this counter, so any backtest
  that hit `MAX_CONSECUTIVE_LOSSES` stopped trading for the rest of the
  replay — every prior research report in this repo undercounted trades and
  is unreliable.
- `bot/broker_alpaca.py::get_recent_bars`: lookback window now scales with
  `timeframe_minutes` and asset session hours instead of a fixed 7 days.
  The old fixed window could never warm up hourly/daily-regime indicators
  for equities (only ~5 session-days per 7 calendar days).
- `bot/profile.py`: profile env file values now take precedence over market
  defaults (`SYMBOL`, `ALLOW_OVERNIGHT_HOLDING`, `FLATTEN_BEFORE_CLOSE_MINUTES`)
  instead of being silently overwritten by them — this is what let
  `config/live_spy.env` actually set `SYMBOL=QQQ` and hold overnight.
- `docker-compose.yml`, `deploy/ec2/deploy_remote.sh`,
  `.github/workflows/deploy-ec2.yml`: default deploy market switched from
  `btc` to `spy`. BTC remains reachable via `trade-btc` / `paper-btc` /
  `research-btc` compose services.
- Tests: updated profile-contract assertions in `tests/test_profile.py` to
  match the new defaults, added regression coverage for the loss-streak
  reset and profile-env precedence.

**Verification:** `pytest` (66 passed), `bot.validate_runtime`,
`bot.validate_profile_env` for all four profiles, and a replay of the exact
committed `config/live_spy.env` / `config/live_btc.env` files against real
historical bars (QQQ, SPY, BTC bull year, BTC bear year) to confirm the
numbers in `docs/strategy_revamp_2026-07.md` match what's actually deployed.

**What to watch after deploy:** run `docker compose run --rm paper` for a
week or two before trusting live fills; compare paper fills against the
replay assumptions (slippage, fill price) before increasing size.
