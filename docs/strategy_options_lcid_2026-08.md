# LCID Options — Symbol Screening & Learning-First Decision (August 2026)

Date: 2026-08-21

References:
- `docs/strategy_tsla_options_2026-08.md` (the TSLA-only baseline this
  supersedes — zero affordable trades at $500 is what triggered this search)
- `docs/strategy_options_2026-08.md` (original NVDA/TSLA rationale —
  contract selection rules, friction, and the modeled-backtest caveat all
  still apply unchanged)
- `docs/strategy_tsla_2026-08.md` (the TSLA equity trend signal this reuses,
  unmodified, as the underlying decision logic)
- `config/paper_options.env`, `config/live_options.env`

## Why this doc exists

`docs/strategy_tsla_options_2026-08.md` found that narrowing the options
profile to TSLA-only didn't fix the real problem: at the account's actual
~$500 equity, TSLA's contracts are never affordable at the target delta/DTE
band — **zero trades** in the full 2.9-year modeled backtest. User decision
(2026-08-21): stop insisting on TSLA and pick a cheaper underlying instead,
explicitly prioritizing "make and analyze trades" (learning) over forcing a
specific symbol. This doc records that search and where it landed: **LCID**,
run as a deliberate learning exercise rather than a validated strategy.

## Step 1: screen 10 cheaper, liquid, optionable candidates

Same modeled-replay methodology as prior docs (`bot/options_research.py::run_options_replay`,
real yfinance hourly bars, 2023-09→2026-08), unmodified TSLA-derived signal
and contract-selection params, at the real $500 starting equity:

| Symbol | Price | Trades | Net P&L | Profit Factor | Win Rate | Max DD |
|---|---|---|---|---|---|---|
| **LCID** | $5.57 | 20 | +$2,810 | **1.72** | 45% | -44% |
| F | $14.51 | 31 | -$407 | 0.66 | 29% | -92% |
| SOFI | $18.99 | 22 | -$343 | 0.39 | 32% | -72% |
| NIO | $4.62 | 30 | -$435 | 0.23 | 27% | -91% |
| CCL | $25.66 | 18 | -$271 | 0.51 | 28% | -63% |
| AAL | $13.77 | 9 | -$405 | 0.01 | 11% | -82% |
| RIVN | $16.98 | 7 | -$354 | 0.00 | 0% | -72% |
| PLTR | $180.46 | 5 | -$251 | 0.00 | 0% | -51% |
| INTC | $90.25 | 4 | -$288 | 0.00 | 0% | -58% |
| PLUG | $2.34 | 2 | -$73 | 0.35 | 50% | -21% |

**Caveat flagged at the time:** this reuses TSLA's signal completely
unmodified on every symbol — exactly the mistake `docs/strategy_tsla_2026-08.md`
itself warns against ("does the QQQ config just work on TSLA? No"). Picking
the best of 10 untuned guesses is a textbook multiple-comparison setup, and
LCID's -44% max drawdown is alarming on its own regardless of the average
return. This screen alone was **not** treated as sufficient evidence.

## Step 2: walk-forward optimize the top 3 (LCID, F, SOFI)

Used the repo's real optimizer (`bot/optimize_strategy.py` — grid search +
walk-forward validation + acceptance checks), fed each symbol's own bars
directly, ~60 candidates per symbol with grids calibrated to each symbol's
own ATR% (looser ADX/SMA/ATR ranges than the TSLA-era defaults, since these
symbols are differently volatile).

**Result: none of the ~180 loosened-grid candidates beat that symbol's own
unmodified (TSLA-derived) baseline** — not on LCID, not on F, not on SOFI.
Baselines (equity-level replay, $10k notional, for a clean sample size):

| Symbol | Baseline trades | Baseline PF | Best "optimized" candidate PF |
|---|---|---|---|
| LCID | 28 | **3.29** | 0.17 (worse) |
| F | 61 | 0.93 | 2.06 on 8 trades, but failed every walk-forward acceptance check |
| SOFI | 53 | 0.75 | 2.81 on 4 trades, same walk-forward failure |

Two takeaways:
1. **The unmodified TSLA-derived signal already transfers best to LCID** of
   the three — it wasn't fragile to nearby parameter choices being
   obviously better, which is at least mildly reassuring.
2. **The walk-forward acceptance framework wasn't discriminating well here.**
   With this few full-sample trades, a 15-day test window essentially never
   contains an entry, so `positive_test_windows` was 0 for nearly every
   candidate on every symbol, including baseline-beating ones. This is a
   real limitation of the check at this trade frequency, not a sign nothing
   works — but it also means **nothing here carries the same walk-forward
   validation rigor `docs/strategy_tsla_2026-08.md` established for the
   equity signal.** Treat everything below as full-sample-backtest evidence
   only.

## Step 3: does "fix the hard stop" (the TSLA finding) generalize?

`docs/strategy_tsla_options_2026-08.md` found TSLA's `HARD_STOP_ATR_MULT=2.5`
catastrophic in premium terms (-$20,798 across 12 trades, the dominant loss
source). Tested directly on LCID and on F/SOFI/NIO — **it does not
generalize**:

- **LCID: widening/removing the hard stop makes things *worse*.**
  PF drops from 1.72 (at 2.5x, the current default) to 1.49 (removed). The
  stop is already net-beneficial on LCID — leave it alone.
- **F: widening the hard stop clearly helps** (PF 0.66 → 0.85 as the stop
  widens toward 5-7x or is removed, net -$407 → -$355). Same direction as
  the TSLA finding.
- **SOFI: mixed, doesn't cleanly help either way** (best variant found was
  still net -$319, worse than doing nothing).
- **NIO: unaffected by the stop at all** — every variant stays deeply
  negative (PF 0.18-0.23). The signal doesn't fit NIO's price action,
  independent of exit tuning.

**Lesson: the hard-stop miscalibration is symbol-specific, not universal.**
It doesn't transfer as a blanket "fix" the way it might seem to from the
TSLA case alone.

## Step 4: deep dive on F, SOFI, NIO (LCID's closest competitors)

Yearly and exit-reason breakdown at $500, unmodified signal:

- **F**: losses spread reasonably across years (2023 +$432, 2024 -$765,
  2025 -$73) — not a lucky/unlucky single-trade story. Top winners/losers
  are comparably sized ($317/$165/$120 vs -$180/-$136/-$132), i.e. no
  extreme concentration. Even after the hard-stop fix and a combined
  hard-stop + trail-multiplier + delta grid search (54 combinations), **F
  never crosses into net-positive territory** — best case PF ≈0.85, net
  ≈-$355 to -$410. It plateaus rather than degrading further, but the edge
  genuinely isn't there yet with this signal.
- **SOFI**: hard-stop-driven losses (-$391 across 6 trades) dominate; not
  responsive to tuning.
- **NIO**: deeply negative regardless of any exit tuning tried — the trend
  signal doesn't fit NIO's gap-driven price action (likely overnight
  China-ADR-specific news risk this signal wasn't built to handle).

**Conclusion: F is the best-behaved of the three (predictable response to
tuning, no luck-driven concentration, and by far the most liquid/transparent
real-world options market of any candidate here — a genuine blue-chip name)
but it is still net-negative even after real tuning.** Not a strategy to
ship as-is.

## Step 5: LCID's trade-level concentration risk (read before trusting the PF)

Pulled all 20 of LCID's modeled trades individually:

| Year | Trades | Net P&L |
|---|---|---|
| 2024 | 5 | **+$3,373.70** (2 trades: +$1,410.06 and +$2,059.36) |
| 2025 | 11 | +$46.10 (essentially flat) |
| 2026 (partial) | 4 | **-$609.59** |

Remove the 2 large 2024 winners and the remaining 18 trades net to roughly
**-$563**. LCID's own price also fell from ~$27 to ~$6 over this window — a
real, multi-year structural decline (Lucid Motors has been persistently
unprofitable and dilutive), not statistical noise. That means the backtest's
edge is substantially "being short a stock in a structural downtrend" during
part of the sample, not a generic, repeatable technical signal. Sub-$10
stock / sub-$2 option premiums likely also carry worse real-world bid-ask
spreads than the model's fixed friction assumption captures.

A further hard-stop-widening + delta/DTE-loosening sweep specifically on
LCID confirmed the **currently shipped, unmodified parameters are already
the best combination found** (hard_stop=2.5, delta=0.65, DTE 25-45): every
attempt to loosen delta/DTE for more trade frequency increased drawdown
(-44% → -60-80%) while lowering PF (1.72 → 1.26-1.45), so no changes were
made.

## Decision: LCID, eyes open

User's explicit call after seeing all of the above: **go with LCID anyway**,
treating the backtest as weak evidence rather than proof, as a deliberate
learning/paper exercise where losing money is an accepted, expected possible
outcome — not evidence something is broken. This is different from how
every other symbol/strategy change in this repo has been framed (which all
required a walk-forward-validated edge before shipping) and that's
intentional here: the goal changed from "find a profitable strategy" to
"generate real option trades to learn the mechanics from."

## What changed

- `config/paper_options.env` / `config/live_options.env`: `SYMBOL` /
  `OPTION_SYMBOLS` → `LCID` (was `TSLA`). `STRATEGY_VERSION` →
  `v3-options-lcid-learning`. **No signal or contract-selection parameters
  changed** — the sweeps in Step 5 confirmed the existing values already
  outperform every alternative tried on LCID's own bars.
- `bot/profile.py`, `bot/options_engine.py`, `bot/options_research.py`,
  `bot/profile_runner.py`, `bot/report_daily.py`, `docker-compose.yml`:
  default fallbacks and docstrings/comments updated to LCID (renamed
  `research-options-tsla` → `research-options-lcid`).
- `README.md`, `OPERATIONS.md`, `docs/github_actions_ec2.md`,
  `.github/workflows/deploy-ec2.yml`: updated to LCID and to explicitly
  frame this as a learning exercise, not a validated strategy.
- `tests/test_profile.py`: updated `SYMBOL`/`OPTION_SYMBOLS` assertions.
- `docs/strategy_tsla_options_2026-08.md`: flagged superseded, pointing
  here; kept as historical record of why TSLA doesn't work at this size.

## Status and what to watch

Not a validated strategy — an explicit, eyes-open learning exercise. Watch
for in the daily paper reports:
- Whether real Alpaca fills roughly match the modeled premiums (the
  Black-Scholes/realized-vol proxy could be meaningfully off for a
  volatile, low-priced name like LCID — spreads especially).
- Whether losses stay within the account's risk limits
  (`MAX_DAILY_LOSS=25`, `MAX_CONSECUTIVE_LOSSES=3`, etc. — unchanged from
  the TSLA/NVDA-era config) rather than needing tighter caps for a more
  volatile underlying.
- Trade frequency and variety (calls vs. puts, different exit reasons) —
  the actual goal here, more than raw P&L.
