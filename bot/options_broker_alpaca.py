"""Alpaca options integration: chain lookup, contract selection, order placement.

Parallel to `bot/broker_alpaca.py` (which handles stock/crypto), this module
is options-specific. It requires the Alpaca account (paper and/or live) to
have options trading enabled in Alpaca's own dashboard first — that's an
account-level compliance approval this code cannot grant.

`select_option_contract` is the pure, unit-testable heart of the
contract-selection strategy documented in `docs/strategy_options_2026-08.md`:
target a moderate delta and a 30-45 DTE band, skip anything with too wide a
bid-ask spread, and skip the trade entirely (return None) if nothing fits
the premium budget — the bot should sit out rather than force a cheaper,
more lottery-like contract onto a signal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import ContractType, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest


CONTRACT_MULTIPLIER = 100


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, str(default)).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _as_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def make_options_data_client() -> OptionHistoricalDataClient:
    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    return OptionHistoricalDataClient(api_key=key, secret_key=secret)


@dataclass(frozen=True)
class ContractCandidate:
    symbol: str
    underlying_symbol: str
    option_type: str  # "call" or "put"
    strike: float
    expiration: date
    dte: int
    delta: float | None
    iv: float | None
    bid: float | None
    ask: float | None
    mid: float | None
    spread_pct: float | None  # (ask - bid) / mid


def get_option_chain(
    trading: TradingClient,
    underlying_symbol: str,
    expiration_gte: date,
    expiration_lte: date,
    option_type: str | None = None,
):
    """Fetch tradable option contracts (strikes + expirations) for a symbol."""
    request = GetOptionContractsRequest(
        underlying_symbols=[underlying_symbol],
        expiration_date_gte=expiration_gte,
        expiration_date_lte=expiration_lte,
        type=ContractType(option_type) if option_type else None,
        limit=1000,
    )
    response = trading.get_option_contracts(request)
    return list(getattr(response, "option_contracts", None) or [])


def get_option_snapshots(data_client: OptionHistoricalDataClient, contract_symbols: list[str]) -> dict:
    """Fetch latest quote + Greeks + IV for a list of option contract symbols."""
    if not contract_symbols:
        return {}
    request = OptionSnapshotRequest(symbol_or_symbols=contract_symbols)
    return data_client.get_option_snapshot(request) or {}


def build_candidates(
    contracts: list,
    snapshots: dict,
    now: datetime | None = None,
) -> list[ContractCandidate]:
    """Merge chain contracts with their live snapshots into scoreable candidates.

    Contracts with no snapshot (no quote yet, illiquid) are skipped rather
    than treated as zero-cost — an untraded quote is missing data, not a
    free option.
    """
    today = (now or datetime.now(timezone.utc)).date()
    candidates: list[ContractCandidate] = []

    for contract in contracts:
        snapshot = snapshots.get(contract.symbol)
        if snapshot is None:
            continue

        quote = getattr(snapshot, "latest_quote", None)
        bid = _as_float(getattr(quote, "bid_price", None)) if quote else None
        ask = _as_float(getattr(quote, "ask_price", None)) if quote else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None and bid > 0 and ask > 0 else None
        spread_pct = ((ask - bid) / mid) if mid and mid > 0 and ask is not None and bid is not None else None

        greeks = getattr(snapshot, "greeks", None)
        delta = _as_float(getattr(greeks, "delta", None)) if greeks else None
        iv = _as_float(getattr(snapshot, "implied_volatility", None))

        expiration = contract.expiration_date if isinstance(contract.expiration_date, date) else None
        dte = (expiration - today).days if expiration else None

        candidates.append(
            ContractCandidate(
                symbol=contract.symbol,
                underlying_symbol=contract.underlying_symbol,
                option_type=str(contract.type.value if hasattr(contract.type, "value") else contract.type).lower(),
                strike=float(contract.strike_price),
                expiration=expiration,
                dte=dte if dte is not None else -1,
                delta=delta,
                iv=iv,
                bid=bid,
                ask=ask,
                mid=mid,
                spread_pct=spread_pct,
            )
        )

    return candidates


def select_option_contract(
    candidates: list[ContractCandidate],
    option_type: str,
    target_delta: float,
    delta_tolerance: float,
    min_dte: int,
    max_dte: int,
    max_spread_pct: float,
    max_premium_budget: float,
    contract_multiplier: int = CONTRACT_MULTIPLIER,
) -> ContractCandidate | None:
    """Pick the contract closest to `target_delta` that fits every guardrail.

    Returns None (no trade) if nothing qualifies — the intended "sit out
    when nothing affordable fits the target band" behavior, not a fallback
    to a looser contract.
    """
    option_type = option_type.lower()
    survivors: list[ContractCandidate] = []

    for candidate in candidates:
        if candidate.option_type != option_type:
            continue
        if candidate.mid is None or candidate.mid <= 0:
            continue
        if not (min_dte <= candidate.dte <= max_dte):
            continue
        if candidate.delta is None:
            continue
        if abs(abs(candidate.delta) - target_delta) > delta_tolerance:
            continue
        if candidate.spread_pct is None or candidate.spread_pct > max_spread_pct:
            continue
        premium = candidate.mid * contract_multiplier
        if premium > max_premium_budget:
            continue
        survivors.append(candidate)

    if not survivors:
        return None

    return min(survivors, key=lambda c: abs(abs(c.delta) - target_delta))


def place_option_order(
    trading: TradingClient,
    contract_symbol: str,
    qty: int,
    side: str,
    limit_price: float,
    position_intent: str,
):
    """Submit a single-leg option limit order.

    Limit, not market: options bid-ask spreads are frequently wide relative
    to premium, and a naive market order on a thin options book is a direct
    way to give back edge on the fill itself. `limit_price` should already
    be pegged near the quote midpoint (with whatever buffer the caller
    wants) before reaching this function.
    """
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")

    order = LimitOrderRequest(
        symbol=contract_symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        limit_price=round(limit_price, 2),
        position_intent=PositionIntent(position_intent),
    )
    return trading.submit_order(order_data=order)


@dataclass(frozen=True)
class OptionsConnectivityResult:
    symbols: list[str]
    paper: bool
    equity: float
    chain_counts: dict[str, int]


def validate_options_connectivity(symbols: list[str]) -> OptionsConnectivityResult:
    """Verify options trading + options market-data auth without placing an order."""
    from bot.broker_alpaca import make_clients  # local import avoids a cycle at module load

    paper = _env_bool("ALPACA_PAPER", True)
    trading, _underlying_data = make_clients()

    try:
        account = trading.get_account()
        equity = float(account.equity)
    except Exception as exc:
        raise RuntimeError(
            f"Alpaca {'paper' if paper else 'live'} trading authentication failed. "
            "Refresh the profile credentials before installing or trusting the schedule."
        ) from exc

    options_data = make_options_data_client()
    today = datetime.now(timezone.utc).date()
    chain_counts: dict[str, int] = {}

    for symbol in symbols:
        try:
            contracts = get_option_chain(
                trading,
                symbol,
                expiration_gte=today + timedelta(days=1),
                expiration_lte=today + timedelta(days=60),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Options contract lookup failed for {symbol}. This usually means options trading "
                "isn't enabled on this Alpaca account yet — check Alpaca's dashboard "
                "(approval is separate for paper and live)."
            ) from exc
        chain_counts[symbol] = len(contracts)

        if contracts:
            sample_symbols = [c.symbol for c in contracts[:5]]
            try:
                get_option_snapshots(options_data, sample_symbols)
            except Exception as exc:
                raise RuntimeError(f"Options market-data connectivity failed for {symbol}.") from exc

    return OptionsConnectivityResult(symbols=symbols, paper=paper, equity=equity, chain_counts=chain_counts)
