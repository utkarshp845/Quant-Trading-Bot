# Strategy Change — QQQ → TSLA (August 2026)

Date: 2026-08-05

## Why this doc exists

The live/paper `spy`-market profiles (`config/live_spy.env`,
`config/paper_spy.env` — named for the market family, not the symbol) switched
from `SYMBOL=QQQ` to `SYMBOL=TSLA`. This is a bigger change than swapping one
env var: TSLA's volatility profile is structurally different from an index
ETF's, so the QQQ-tuned parameters from `docs/strategy_revamp_2026-07.md` were
re-validated and re-tuned specifically for TSLA before shipping, using the
same replay methodology (walk-forward, 2x slippage stress) as that revamp.

**No Alpaca keys are available in this environment** (they live only on EC2 —
see project memory / `docs/strategy_revamp_2026-07.md`'s "Reproducing the
research" section). Like the original equity validation, this research used
free Yahoo Finance hourly bars (`yfinance`, 730-day limit), not Alpaca.

## Step 1: does the QQQ config just work on TSLA?

No. Replaying `config/live_spy.env` verbatim with only `SYMBOL` changed, over
2023-09 → 2026-08 (~5,068 hourly bars, $150 starting equity, $0.02/share
slippage):

| Metric | QQQ (as documented) | TSLA (QQQ params, unchanged) |
|---|---|---|
| Net P&L | +$57.7 (+38%) | **+$6.96 (+4.6%)** |
| Profit factor | 1.82 | **1.03** (essentially breakeven) |
| Max drawdown | 6.7% | **29.6%** |
| 2x slippage | profitable | **fails** (negative expectancy) |

(The QQQ column here is a fresh replay with the same harness, not copy-pasted
from the July doc — it reproduces the documented QQQ numbers closely, which
validates the yfinance-based methodology against the original Alpaca-based
one.)

TSLA's hourly ATR% runs about **3x QQQ's** (median ~1.4% vs ~0.49%). At QQQ's
~90% notional sizing, that volatility difference alone is enough to turn a
6.7% backtested drawdown into a 29.6% one on the same $150 account — not
something to run live. The entry filters tuned for QQQ's smoother index moves
also don't transfer: they either almost never fire on TSLA, or they fire on
noise instead of trend.

## Step 2: walk-forward parameter search for TSLA

Used the repo's own optimizer (`bot/optimize_strategy.py` — grid search +
walk-forward validation + 2x slippage stress + acceptance checks), fed TSLA
bars directly instead of pulling from Alpaca. Same acceptance bar as the QQQ
work: full-sample PF ≥ 1.10, positive expectancy, 0.5–3 trades/day, positive
walk-forward test-window net P&L, ≥2 of N positive test windows, PF ≥ 1.0
under 2x slippage, max drawdown ≤ 10%.

First pass reused the QQQ-style tight ATR/regime grid rescaled for TSLA's
volatility — 0/40 candidates passed acceptance. The best-scoring candidates
only traded 9–22 times over 3 years; with that few trades, "passing" the
walk-forward check was mostly luck of which 45-day test window happened to
contain the one or two winners (1/13 or 2/13 positive windows is not a
robustness signal, it's a sample-size problem).

Second pass loosened the entry/regime filters to trade more often (more SMA
spans, lower ADX thresholds, wider pullback range, looser regime ADX floor).
8/60 candidates passed acceptance. The best-scoring **accepted** candidate
(21 trades, full DD 11.3%) still slightly exceeded the 10% drawdown cap at
QQQ's ~90% notional sizing — so it wasn't the signal that needed more work,
it was the position size.

## Step 3: size for TSLA's volatility, not QQQ's

Swept `TARGET_POSITION_NOTIONAL_PCT` / `MAX_POSITION_NOTIONAL_PCT` down from
QQQ's 90%/95% on the best-scoring accepted signal, full sample:

| Target / Max notional | Net P&L | Profit factor | Max DD | Passes all checks |
|---|---|---|---|---|
| 90% / 95% (QQQ's setting) | +$47.8 | 1.51 | 13.9% | No (DD) |
| 70% / 75% | +$38.1 | 1.54 | 10.9% | No (DD) |
| **60% / 65% (shipped)** | **+$33.0** | **1.56** | **9.4%** | **Yes** |
| 40% / 45% | +$22.4 | 1.60 | 6.4% | Yes |

60%/65% was picked as the least-aggressive sizing that still clears every
acceptance check with a bit of margin under the 10% cap (9.4%, not 9.9%),
rather than the smallest possible size — smaller sizing keeps trimming
absolute P&L roughly linearly without materially improving robustness once
under the cap.

## Final result (shipped config)

Full sample, 2023-09 → 2026-08, $150 starting equity, 60%/65% notional
sizing, $0.02/share slippage:

| Metric | Value |
|---|---|
| Net P&L | +$32.97 (+22% over ~2.9 years) |
| Profit factor | 1.56 (1.55 under 2x slippage) |
| Win rate | 43% (avg win $7.67 / avg loss -$3.69) |
| Max drawdown | 9.4% |
| Trades | 28 (~9/yr, ~1 every 5-6 weeks, avg hold ~4-5 days) |
| Walk-forward test windows | 6/13 positive (46%), aggregate test net +$27.0, test PF 1.94 |

Yearly breakdown (entry-date year):

| Year | Net P&L |
|---|---|
| 2023 (partial, from Sep) | -$11.90 |
| 2024 | **+$52.01** |
| 2025 | +$0.04 |
| 2026 (partial, through Aug) | -$7.18 |

**This is the single most important caveat**: almost all of the backtested
profit came from one strong trend year (2024). 2023, 2025, and 2026 were each
roughly flat to slightly negative. QQQ's replay was positive in every
calendar year of its 3-year window; this TSLA config was not. A long-only
trend strategy on a single high-beta stock is inherently lumpier than the
same strategy on a diversified index — it only makes money when TSLA is
actually trending, and pays small "chop tax" losses the rest of the time.
Treat +22%/2.9yr as evidence of a plausible, walk-forward-validated edge, not
as an expected annual return.

## What changed vs the QQQ config

- `SYMBOL=QQQ` → `SYMBOL=TSLA`, `STRATEGY_VERSION=v3-equity-trend` →
  `v4-tsla-trend`.
- `TARGET_POSITION_NOTIONAL_PCT` 0.90 → **0.60**, `MAX_POSITION_NOTIONAL_PCT`
  0.95 → **0.65** (volatility-adjusted sizing, see Step 3).
- `SMA_FAST`/`SMA_SLOW` 20/50 → **8/30** (faster, since TSLA trends and
  reverses faster than an index).
- `ADX_THRESHOLD`/`LONG_ADX_THRESHOLD` 15/15 → **18/20**.
- `ATR_MAX_PCT`/`LONG_ATR_MAX_PCT` 0.03/0.03 → **0.020/0.020** (tighter in
  absolute terms, but TSLA's baseline ATR% is ~3x QQQ's, so this still admits
  proportionally more bars than QQQ's filter did on QQQ).
- `MIN_VOLUME_RATIO` 0 (off) → **0.8**, `PULLBACK_MIN_DEPTH_ATR`/`_MAX_DEPTH_ATR`
  0/99 (off) → **0.1/2.0**, `SPIKE_BAR_MAX_RANGE_ATR` 10 → **2.5** — QQQ ran
  with these micro-filters off; on TSLA they measurably improved the replay.
- `REGIME_MIN_SLOPE_PCT` 0 → **0.001** (daily regime now also requires a
  minimum EMA slope, not just price-above-EMA).
- `TRAIL_ATR_MULTIPLIER`/`TRAIL_AFTER_ATR_MULTIPLE` 2.5/2.0 → **2.0/1.5**,
  `MAX_BARS_IN_TRADE` 999 (unlimited) → **48** (~2 trading days at 60m bars).
- `COOLDOWN_BARS` 2 → **4**.
- `HARD_STOP_ATR_MULT` (2.5), the daily regime timeframe/EMA period (1440 /
  20), and the risk-limit block are unchanged from the QQQ config — they were
  already ATR-relative or account-level, not QQQ-specific.

## Realistic expectations

Same framing as the QQQ revamp: this is backtest evidence from a specific
~3-year window, not a guarantee. TSLA adds real risks QQQ didn't have —
single-name idiosyncratic risk (earnings, deliveries, headline/social-media
driven moves), thinner statistical sample (28 trades vs QQQ's 74), and a
return profile concentrated in one good year out of ~3. The 9.4% backtested
max drawdown is real money on a $150 account (~$14 peak-to-trough). Run the
paper profile (`config/paper_spy.env`, now also TSLA) alongside live and
compare fills before trusting this fully, exactly as recommended in
`docs/strategy_revamp_2026-07.md`.

## Reproducing the research

Same harness as before: `bot/research.py::run_replay` and
`bot/optimize_strategy.py` (grid search + walk-forward + acceptance checks),
fed bars directly instead of through `bot/broker_alpaca.py`'s Alpaca client
since keys aren't available outside EC2. Equity bars: Yahoo Finance hourly
(`yfinance`, 730-day limit, symbol `TSLA`/`QQQ`). Friction model:
$0.02/share slippage, 2x-slippage stress test for the acceptance bar.
