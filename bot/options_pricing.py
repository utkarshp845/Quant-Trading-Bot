"""Stdlib-only Black-Scholes pricing and Greeks for European-style equity options.

Used by `bot/options_research.py` to value modeled contracts over historical
stock bars (there is no free historical options-chain data source, unlike the
yfinance stock bars the rest of this repo's research leans on) and, as a
fallback/sanity-check, anywhere live Alpaca option Greeks aren't available.

This is a simplified model: no dividend yield, European exercise (US equity
options are technically American-style, but the difference is small for the
long-only, non-dividend-focused names this bot trades and isn't worth adding
a binomial tree for). Treat it as directional, not exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class OptionPricingResult:
    price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float


def _d1_d2(spot: float, strike: float, t_years: float, vol: float, rate: float) -> tuple[float, float]:
    vol_sqrt_t = vol * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def price_option(
    spot: float,
    strike: float,
    t_years: float,
    vol: float,
    option_type: str,
    rate: float = 0.04,
) -> OptionPricingResult:
    """Black-Scholes price + Greeks for a call or put.

    `t_years` is time to expiration in years (must be > 0). `vol` is
    annualized volatility (e.g. 0.45 for 45%). `option_type` is "call" or
    "put". Returns intrinsic value with zero Greeks if `t_years` or `vol`
    isn't usable (e.g. at/after expiration), rather than raising, since
    replay loops call this once per bar and expiration edge cases are
    routine, not exceptional.
    """
    option_type = option_type.lower()
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

    if spot <= 0 or strike <= 0:
        return OptionPricingResult(0.0, 0.0, 0.0, 0.0, 0.0)

    if t_years <= 0 or vol <= 0:
        intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
        delta = (1.0 if spot > strike else 0.0) if option_type == "call" else (-1.0 if spot < strike else 0.0)
        return OptionPricingResult(intrinsic, delta, 0.0, 0.0, 0.0)

    d1, d2 = _d1_d2(spot, strike, t_years, vol, rate)
    discount = math.exp(-rate * t_years)
    pdf_d1 = _norm_pdf(d1)

    if option_type == "call":
        price = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_annual = (
            -(spot * pdf_d1 * vol) / (2.0 * math.sqrt(t_years))
            - rate * strike * discount * _norm_cdf(d2)
        )
    else:
        price = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta_annual = (
            -(spot * pdf_d1 * vol) / (2.0 * math.sqrt(t_years))
            + rate * strike * discount * _norm_cdf(-d2)
        )

    gamma = pdf_d1 / (spot * vol * math.sqrt(t_years))
    vega = spot * pdf_d1 * math.sqrt(t_years) / 100.0  # per 1 vol-point (1.00 = 100%)
    theta_per_day = theta_annual / 365.0

    return OptionPricingResult(
        price=max(0.0, price),
        delta=delta,
        gamma=gamma,
        theta=theta_per_day,
        vega=vega,
    )


def solve_strike_for_delta(
    spot: float,
    target_delta: float,
    t_years: float,
    vol: float,
    option_type: str,
    rate: float = 0.04,
    tolerance: float = 1e-4,
    max_iterations: int = 60,
) -> float:
    """Find the strike whose Black-Scholes delta is closest to `target_delta`.

    `target_delta` should be positive for calls (e.g. 0.65) and negative for
    puts (e.g. -0.65) — callers typically pass the same positive magnitude
    for both and let this function apply the sign via `option_type`.
    Bisection over strike, since delta is monotonic in strike for a fixed
    spot/vol/expiry.
    """
    option_type = option_type.lower()
    signed_target = target_delta if option_type == "call" else -abs(target_delta)
    if option_type == "call":
        signed_target = abs(target_delta)

    low, high = spot * 0.3, spot * 3.0
    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        result = price_option(spot, mid, t_years, vol, option_type, rate)
        if abs(result.delta - signed_target) < tolerance:
            return mid
        # Delta decreases as strike increases, for both calls and puts.
        if result.delta > signed_target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def historical_volatility(closes: "list[float] | object", window: int = 20, trading_days_per_year: int = 252) -> float | None:
    """Annualized realized volatility from a rolling window of close prices.

    Used as an implied-volatility proxy for modeled backtests, since no
    historical options IV data is available. This systematically
    understates real option prices around events (earnings, macro
    surprises) where IV runs richer than trailing realized vol — one more
    reason the options replay is a directional sanity check, not a precise
    backtest.
    """
    values = list(closes)
    if len(values) < window + 1:
        return None

    recent = values[-(window + 1):]
    log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent)) if recent[i - 1] > 0 and recent[i] > 0]
    if len(log_returns) < 2:
        return None

    mean_return = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_return) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(max(0.0, variance))
    return daily_vol * math.sqrt(trading_days_per_year)
