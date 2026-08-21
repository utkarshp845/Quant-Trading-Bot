# TSLA-Only Options — Scope Narrowing & Refinement Baseline (August 2026)

Date: 2026-08-21

> **SUPERSEDED (2026-08-21, same day).** The TSLA-only affordability finding
> below (zero trades at $500) led to switching the options profile's symbol
> again, this time to **LCID** — see `docs/strategy_options_lcid_2026-08.md`
> for the full symbol-screening evidence and why. This doc's baseline
> backtest and refinement-question analysis are kept as the historical
> record of why TSLA specifically doesn't work at this account size; the
> config it describes is no longer what's deployed.

References:
- `docs/strategy_options_2026-08.md` (original NVDA/TSLA rationale — contract
  selection rules, friction, affordability math, modeled-backtest caveat all
  still apply unchanged)
- `docs/strategy_tsla_2026-08.md` (the TSLA equity trend signal this reuses)
- `config/paper_options.env`, `config/live_options.env`
- `bot/options_engine.py`, `bot/options_research.py`

## Why this doc exists

User request (2026-08-21): narrow the options bot to **TSLA only** — drop
NVDA — and treat this as a strategy to actively refine (backtest-driven
iteration) before settling into an ongoing daily paper-trading cadence,
rather than the original fixed one-week evaluation window.

## What changed

- `config/paper_options.env` / `config/live_options.env`: `OPTION_SYMBOLS`
  and `SYMBOL` narrowed from `NVDA,TSLA` / `NVDA` to `TSLA`. `STRATEGY_VERSION`
  bumped to `v2-options-tsla-only`.
- `bot/profile.py`'s `options` market defaults now default to TSLA-only too.
- `bot/options_engine.py`, `bot/options_research.py`, `bot/profile_runner.py`,
  `bot/report_daily.py`: default fallbacks and docstrings updated to match;
  the multi-symbol loop in `bot/options_engine.py` is left in place
  (`OPTION_SYMBOLS` could hold more than one symbol again later) rather than
  hard-coded to a single symbol.
- Removed the `research-options-nvda` service from `docker-compose.yml`
  (`research-options-tsla` remains).
- No signal, contract-selection, or risk parameters changed in this pass —
  this doc's job is to narrow scope and establish a real baseline before any
  of those get tuned.

## Baseline: does the inherited (NVDA/TSLA-derived) config work on TSLA alone?

Same modeled-backtest methodology as `docs/strategy_options_2026-08.md` and
`docs/strategy_tsla_2026-08.md`: `bot/options_research.py::run_options_replay`
against real TSLA hourly bars (yfinance, 2023-09-25 → 2026-08-21, ~5,069
bars — Alpaca keys aren't available in this environment, same limitation as
prior research docs), Black-Scholes-modeled contract prices, using the
exact parameters currently shipped in `config/paper_options.env`.
**Reminder: this is a modeled backtest, not a real options-chain replay —
see the "Backtesting reality check" section of `docs/strategy_options_2026-08.md`
before trusting these numbers as more than directional.**

### Affordability at the account's actual size

| Starting equity | Trades | Net P&L | Profit factor |
|---|---|---|---|
| $500 (current account) | **0** | — | — |
| $1,000 | 0 | — | — |
| $2,000 | 0 | — | — |
| $5,000 | 18 | -$1,604.52 | 0.84 |
| $10,000 | 24 | -$6,010.49 | 0.76 |
| $20,000 | 52 | -$14,517.49 | 0.82 |

At the account's real ~$500 starting equity, the current 0.65-delta /
25-45-DTE / 55%-of-equity-budget contract-selection rules find **zero
affordable TSLA contracts in the entire 2.9-year sample** — the same
"sits out rather than force a worse trade" behavior documented for
NVDA/TSLA, just more extreme now that NVDA (which was more often affordable
due to its lower share price) is off the table. This isn't a bug; it's the
same design already described in `docs/strategy_options_2026-08.md`'s
"Affordability" section — but it does mean: **as currently configured, this
strategy will not trade paper or live until the account grows well past
$500, or the delta/DTE/budget targets change.**

### Is it actually profitable where it *can* afford to trade?

No — not with the parameters inherited unchanged from the NVDA/TSLA config.
At $10,000 starting equity (large enough to see a real sample):

| Metric | Value |
|---|---|
| Trades | 24 |
| Net P&L | -$6,010.49 |
| Profit factor | 0.76 |
| By side | long (calls): 14 trades, -$960.75 net (-$68.62 avg) — roughly breakeven<br>short (puts): 10 trades, **-$5,049.74 net (-$504.97 avg)** — the main loss driver |
| By exit reason | `hard_stop`: 12 trades, **-$20,797.82 net** (-$1,733.15 avg) — catastrophic<br>`trailing_stop`: 9 trades, +$265.27 net<br>`time_stop`: 3 trades, **+$14,522.07 net** (+$4,840.69 avg) — this is where essentially all the profit comes from |
| By year | 2023: -$5,228.67 (6 trades) · 2024: +$1,621.27 (15 trades) · 2025: -$2,403.09 (3 trades) |

This is a different picture than the TSLA **equity** signal's own replay
(PF 1.56, `docs/strategy_tsla_2026-08.md`) — same entry/exit decision logic,
but expressed as options the friction and convexity change everything:

- **`hard_stop` exits are the dominant loss source.** `HARD_STOP_ATR_MULT=2.5`
  on the *underlying* price maps to a much larger percentage loss in
  *premium* at ~0.65 delta with ~35 DTE remaining (gamma/theta both work
  against the position on the way to the stop), so the options hard stop
  is far more punishing than the same stop is on the equity version.
- **`time_stop` exits (letting a trade run the full `MAX_BARS_IN_TRADE`
  without being stopped out) supply nearly all the gains** — the one large
  winner (+$11,088.52 on a call opened 2024-06-28) alone exceeds the
  strategy's total net loss.
- **Puts are structurally worse than calls in this sample** — not just a
  smaller edge, a real net loss (-$505/trade average vs -$69/trade for
  calls). TSLA's downtrends in this window were apparently choppier/faster
  relative to the hard-stop distance than its uptrends were.

## Open questions for refinement

This is the starting point for the "refine until we both agree it's in a
good place" process, not a finished config. Candidates worth backtesting
next, roughly in order of expected impact based on the breakdown above:

1. **Hard stop is too tight for how it behaves in premium terms.** Worth
   testing a wider `HARD_STOP_ATR_MULT`, or replacing the underlying-ATR
   hard stop with a premium-percentage stop that's actually calibrated to
   options convexity instead of inherited unchanged from the equity signal.
2. **Puts underperform calls badly in this sample.** Worth backtesting a
   calls-only variant (bullish-signal-only, sit out on bearish signals) as
   a direct comparison against keeping both sides.
3. **Affordability at $500 blocks trading entirely.** Either accept zero
   trades until the account grows (consistent with the original design
   philosophy), or backtest what loosening delta/DTE targets or the premium
   budget actually costs in edge quality before doing it — not obviously
   worth it just to force trade frequency.
4. **Small full-sample trade count (24 at $10k, fewer at realistic size)**
   means single trades swing the numbers a lot (one winner is most of the
   total profit) — any change should be judged on a walk-forward split, not
   just the full-sample profit factor, the same standard already applied to
   the TSLA equity signal in `docs/strategy_tsla_2026-08.md`.

## Status

Not yet refined — this doc will be updated as parameter changes are tested
and agreed on, then the resulting config is what runs against real TSLA
paper fills every trading day going forward.
