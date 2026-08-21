# NVDA/TSLA Long Calls/Puts — Strategy & Rationale (August 2026)

Date: 2026-08-09

> **PARTIALLY SUPERSEDED (2026-08-21).** The options profile was narrowed
> from NVDA+TSLA to **TSLA only** — see
> `docs/strategy_tsla_options_2026-08.md` for why and what changed.
> Everything below about contract selection, friction, affordability, and
> the modeled-backtest caveat still applies unchanged; only the symbol list
> is out of date. Kept as historical record of the original NVDA/TSLA
> rationale.

References:
- `bot/options_pricing.py`, `bot/options_broker_alpaca.py`, `bot/options_engine.py`, `bot/options_research.py`
- `config/paper_options.env`, `config/live_options.env`
- `docs/strategy_tsla_2026-08.md` (the equity trend signal this reuses)

## Why this doc exists

The account is starting options trading on NVDA and TSLA — the live equity
account is going from ~$150 to ~$500 (the user is adding $350) to fund it,
with more capital planned as it (hopefully) grows. This is a new asset
class for the bot, not a config tweak: there was no options code in this
repo before this change. This doc is the same kind of evidence-and-rationale
record as `docs/strategy_tsla_2026-08.md` and
`docs/strategy_revamp_2026-07.md` — read it before trusting the numbers.

## The core idea

Reuse the trend signal that's already live for TSLA equities
(`bot/strategy_ma.py` — SMA trend cross, ADX, ATR-bounded volatility, a
daily-EMA regime filter), but express the trade as a **long call or long
put** instead of buying/shorting the stock. Two things change versus the
equity version:

1. **Both directions are usable.** `ALLOW_SHORTS=false` on the equity
   profile exists because naked-shorting a stock on a small cash account has
   undefined risk. A long put doesn't have that problem — its risk is
   capped at the premium paid, same as a long call. So the options profile
   sets `ALLOW_SHORTS=true`: calls in confirmed uptrends, puts in confirmed
   downtrends, using the exact same regime/ADX/pullback filters already
   tuned for TSLA (`config/live_spy.env`).
2. **Contract selection replaces share sizing**, and it matters more than
   people expect.

## Contract selection is the strategy

Getting the direction right is necessary but not sufficient. The TSLA
equity backtest's edge is modest — profit factor ≈ 1.56, and most of the
profit came from one strong trend year (see
`docs/strategy_tsla_2026-08.md`). Buying a cheap, far out-of-the-money
option on that same signal would very likely turn a small real edge into a
coin flip, because far-OTM premium is dominated by implied volatility and
theta decay, not by the direction of the underlying. The rules in
`bot/options_broker_alpaca.py::select_option_contract` exist specifically to
avoid that:

| Rule | Setting | Why |
|---|---|---|
| Target delta | 0.65 (±0.15 tolerance) | Moderately ITM — the contract moves close to dollar-for-dollar with the stock, so the underlying's real (if modest) edge actually transfers into the option's P&L. |
| DTE at entry | 25–45 days | Enough runway for a multi-day trend (the equity signal holds for days, sometimes weeks) to play out before time decay dominates. |
| Exit by DTE | ≤12 days remaining | Forces a close before the theta/gamma cliff in an option's final one-to-two weeks, regardless of what the trend signal says. |
| Max bid-ask spread | 8% of mid | Skip the trade if the spread itself would eat the edge — see "Friction" below. |
| Earnings blackout | ±3 days of any listed date | Avoid opening new positions into elevated pre-earnings IV or getting run over by a post-earnings gap the signal wasn't built to survive. |

If nothing in the chain satisfies all of these **and** fits the premium
budget, the bot does not trade — see "Affordability" below.

## Friction: the options analog of the BTC-fee lesson

`docs/strategy_revamp_2026-07.md` documented that BTC's ~0.6% round-trip
fee/spread was larger than the entire expected edge of the original
crypto strategy at $150 — the strategy wasn't unlucky, it was structurally
unwinnable at that size. Options have the same failure mode, potentially
worse: bid-ask spreads on individual option contracts, especially away from
the most liquid at-the-money strikes, routinely run in the high single
digits to double-digit percent of premium. The `OPTION_MAX_BID_ASK_SPREAD_PCT`
filter exists to refuse trades where that friction alone would swamp the
edge, the same way the BTC profile refuses to trade when fees dominate.

## Affordability at ~$500

NVDA and TSLA both trade in the hundreds of dollars. A single contract at
the target ~0.65 delta and 30–45 DTE commonly costs **$300–$1,500+ in
premium** (one option contract = 100 shares of exposure). At a ~$500
account:

- **Only one options position is allowed open at a time, across both
  symbols combined** (`OPTION_MAX_CONCURRENT_POSITIONS=1`) — the budget
  doesn't support two "real" positions without concentrating too much in
  one or degrading both toward lottery tickets.
- Premium budget per trade is capped at 55% of current equity
  (`OPTION_MAX_PREMIUM_PCT_OF_EQUITY=0.55`), leaving a buffer instead of
  betting the whole account on one contract.
- NVDA is more likely than TSLA to have an affordable setup near the target
  delta/DTE band at this size, purely because of its lower absolute share
  price — expect TSLA setups to get skipped on cost more often, independent
  of signal quality.
- **Expect this to trade rarely at first.** `bot/options_broker_alpaca.py::select_option_contract`
  returns `None` (no trade) rather than falling back to a cheaper, more
  lottery-like contract when nothing fits the budget — the same "sit out
  rather than force a worse trade" design already used by the BTC profile
  for downtrends. This was confirmed empirically while building this
  feature: `tests/test_options_research.py`'s first replay test needed
  $5,000 of starting equity, not $500, before a 0.65-delta/35-DTE NVDA
  contract was even affordable at the 55% budget — a live demonstration of
  exactly this constraint, not a hypothetical one. As the account grows
  (the plan going in), the affordable set of contracts widens and trade
  frequency should rise on its own; loosening delta/DTE targets to force
  more trades sooner would defeat the point of targeting them in the first
  place.

## Backtesting reality check — read this before trusting any report number

**There is no free historical options-chain data source.** The stock-bar
research the rest of this repo relies on (yfinance, Alpaca IEX bars) has no
equivalent for historical options quotes at usable depth. `bot/options_research.py`
therefore **models** contract prices with Black-Scholes
(`bot/options_pricing.py`) over real historical *stock* prices, using
trailing realized volatility as an implied-volatility proxy.

This is a real limitation, not a rounding error:
- Realized volatility systematically understates implied volatility around
  events (earnings, macro surprises) — the exact situations the earnings
  blackout tries to route around, but any residual event-driven IV richness
  elsewhere in the sample won't show up in the modeled backtest at all.
  European-style pricing is used (no early-exercise modeling) even though
  US equity options are technically American-style — a small approximation
  for the non-dividend-focused, long-only-per-leg trades this strategy
  places.
- The reported "spread cost" is a modeled friction assumption
  (`OPTION_MAX_BID_ASK_SPREAD_PCT`), not a measurement of real historical
  quoted spreads.

Every `options_research` report is headed "MODELED — Black-Scholes over
historical stock bars" for this reason. **Treat it as a directional sanity
check on the delta/DTE/sizing rules, not proof of live viability.** The
entry/exit *decision* logic itself (trend signal, hard stop, trailing stop,
regime invalidation, time stop) is deliberately identical to the proven
equity replay logic in `bot/research.py::run_replay` — only the fill/PnL
mechanics differ — so the backtest is at least testing the same signal that
already has real (if modest, if lumpy) equity evidence behind it.

## What actually validates this before real money

Paper trading against live Alpaca options quotes for a real stretch —
`config/paper_options.env` (`docker compose run --rm paper-options`) — the
same standard already applied to `config/paper_tsladay.env` before that
strategy is trusted with live capital. `config/live_options.env` exists so
the live path is ready, but it is explicitly **not recommended yet** and is
not wired into the default EC2 deploy target
(`docker-compose.yml`'s `trade-options` service exists but mirrors
`trade-tsladay`'s "present but not the default" treatment).

## Prerequisite this repo cannot satisfy for you

Options trading must be enabled on the Alpaca account — a compliance
approval in Alpaca's own dashboard, separate for paper and live accounts.
`python -m bot.profile_runner paper connectivity options` will fail with a
clear message if this hasn't been done; no amount of code here can grant
that approval.

## Non-goals for this version

- Multi-leg spreads (credit/debit verticals, iron condors, etc.) — the
  strategy is long single-leg calls/puts by choice, to keep risk capped at
  premium paid with no margin requirement. `select_option_contract` is
  written so a spread order path could reuse its filtering logic later, but
  no spread execution path exists yet.
- A live, automated earnings calendar — `OPTION_EARNINGS_BLACKOUT_DATES` is
  an operator-maintained, comma-separated list of ISO dates in the profile
  env files. Update it each quarter from NVDA's and TSLA's investor
  relations pages. It is not pulled from any live feed.
