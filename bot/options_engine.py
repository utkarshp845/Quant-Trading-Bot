"""Live decision engine for the NVDA/TSLA long-calls/puts options strategy.

Parallel to `bot/main.py` (equities), but deliberately evaluates every
symbol in `OPTION_SYMBOLS` in one process/one run instead of one cron job
per symbol. That's required to enforce "at most
`OPTION_MAX_CONCURRENT_POSITIONS` options position(s) open at a time, across
both symbols combined" — two independent per-symbol cron jobs can't see each
other's state. See `docs/strategy_options_2026-08.md` for the full rationale.

This is a v1: it reuses the same signal engine, risk gate, and trade-control
bookkeeping as the equity bot, but its order-fill reconciliation is simpler
than `bot/main.py`'s (one status check per cycle, retried via
`get_orders_requiring_sync` on the next cycle rather than an in-cycle retry
loop). That's an intentional scope cut for a paper-first feature, not an
oversight — tighten it before ever pointing this at live capital.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.broker_alpaca import get_account_snapshot, get_order, get_recent_bars, make_clients, normalize_order_status
from bot.io_log import setup_logger
from bot.options_broker_alpaca import (
    ContractCandidate,
    CONTRACT_MULTIPLIER,
    build_candidates,
    get_option_chain,
    get_option_snapshots,
    make_options_data_client,
    place_option_order,
    select_option_contract,
)
from bot.options_pricing import historical_volatility, price_option
from bot.options_research import _daily_closes_up_to, _is_in_earnings_blackout, build_option_params_from_env
from bot.paths import LOGS_DIR, ensure_runtime_dirs
from bot.risk import RiskConfig, evaluate_entry_risk, parse_ts
from bot.store import (
    clear_position_state,
    connect,
    get_option_position_state,
    get_orders_requiring_sync,
    get_state,
    increment_trades_today,
    init_db,
    mark_order_processed,
    record_closed_trade,
    record_event,
    record_order_submission,
    record_run,
    set_consecutive_losses,
    set_last_entry_signal,
    set_last_trade,
    update_order_status,
    upsert_option_position_state,
)
from bot.strategy_ma import build_strategy_config_from_env, compute_indicators, generate_signal
from bot.trade_controls import bars_since


ET = ZoneInfo("America/New_York")
POSITION_KEY = "OPTIONS"  # single shared position_state row across all OPTION_SYMBOLS
TERMINAL_ORDER_STATUSES = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}


def _as_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbols_from_env() -> list[str]:
    raw = os.getenv("OPTION_SYMBOLS", "NVDA,TSLA")
    return [token.strip().upper() for token in raw.split(",") if token.strip()]


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_missing_deltas(candidates: list[ContractCandidate], spot: float, iv: float, rate: float = 0.04) -> list[ContractCandidate]:
    """Fill in a Black-Scholes-estimated delta for candidates whose live
    snapshot didn't include Greeks (e.g. an `indicative` feed without an
    options market-data subscription). Estimated, not authoritative — real
    Greeks from a subscribed feed should always be preferred when present.
    """
    filled: list[ContractCandidate] = []
    for candidate in candidates:
        if candidate.delta is not None:
            filled.append(candidate)
            continue
        t_years = max(candidate.dte, 0) / 365.0
        if t_years <= 0:
            filled.append(candidate)
            continue
        result = price_option(spot, candidate.strike, t_years, iv, candidate.option_type, rate)
        filled.append(
            ContractCandidate(
                symbol=candidate.symbol,
                underlying_symbol=candidate.underlying_symbol,
                option_type=candidate.option_type,
                strike=candidate.strike,
                expiration=candidate.expiration,
                dte=candidate.dte,
                delta=result.delta,
                iv=candidate.iv,
                bid=candidate.bid,
                ask=candidate.ask,
                mid=candidate.mid,
                spread_pct=candidate.spread_pct,
            )
        )
    return filled


def sync_pending_orders(conn, trading, logger) -> None:
    for order in get_orders_requiring_sync(conn, POSITION_KEY):
        try:
            live_order = get_order(trading, order.order_id)
        except Exception as exc:
            logger.warning(f"Could not fetch option order status for {order.order_id}: {exc}")
            continue

        status = normalize_order_status(getattr(live_order, "status", None))
        filled_avg = _as_float(getattr(live_order, "filled_avg_price", None))
        filled_qty = _as_float(getattr(live_order, "filled_qty", None))
        filled_at = getattr(live_order, "filled_at", None)
        update_order_status(conn, order.order_id, status, filled_avg, filled_qty, str(filled_at) if filled_at else None)

        if status in TERMINAL_ORDER_STATUSES:
            mark_order_processed(conn, order.order_id, _utc_iso_now())
            record_event(
                conn,
                _utc_iso_now(),
                "info",
                "option_order_sync",
                symbol=POSITION_KEY,
                message=f"order {order.order_id} -> {status}",
                payload={"order_id": order.order_id, "status": status, "filled_avg_price": filled_avg},
            )


def _evaluate_underlying(symbol: str, timeframe_minutes: int, cfg) -> tuple[str, dict, list[str], pd.DataFrame]:
    _trading, data = make_clients()
    bars = get_recent_bars(data, symbol, timeframe_minutes, limit=max(200, 300))
    if bars.empty:
        return "HOLD", {}, ["no_data"], bars
    indicators = compute_indicators(bars, cfg)
    signal, metrics, reasons = generate_signal(indicators, cfg)
    return signal, metrics, reasons, indicators


def _handle_exit(conn, trading, options_data, logger, position, equity: float | None) -> None:
    symbol = position.underlying_symbol
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "60"))
    cfg = build_strategy_config_from_env(timeframe_minutes)
    params = build_option_params_from_env()
    hard_stop_atr_mult = float(os.getenv("HARD_STOP_ATR_MULT", "0"))

    signal, metrics, reasons, bars = _evaluate_underlying(symbol, timeframe_minutes, cfg)
    spot = _as_float(metrics.get("price"))
    atr_value = _as_float(metrics.get("atr"))
    entry_dt = parse_ts(position.entry_ts)
    entry_spot = _as_float(position.entry_spot)
    now_utc = datetime.now(timezone.utc)
    remaining_days = None
    if position.expiration:
        expiration_dt = datetime.strptime(position.expiration, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        remaining_days = (expiration_dt - now_utc).total_seconds() / 86400.0

    should_exit = False
    exit_reason = None

    # Update (and persist) the underlying high/low-water marks every cycle,
    # even on cycles that don't exit — the trailing-stop check below needs
    # them to reflect the full life of the trade, not just this bar.
    high_water = max(position.highest_price or spot or 0.0, spot or 0.0) if spot is not None else position.highest_price
    low_water = min(position.lowest_price or spot or 0.0, spot or 0.0) if spot is not None else position.lowest_price

    if remaining_days is not None and remaining_days <= params.exit_min_dte:
        should_exit, exit_reason = True, "dte_exit"

    if not should_exit and spot is not None and entry_spot is not None and atr_value is not None:
        if position.side == "long":
            if hard_stop_atr_mult > 0 and spot < entry_spot - (hard_stop_atr_mult * atr_value):
                should_exit, exit_reason = True, "hard_stop"
            elif (
                high_water is not None
                and high_water >= entry_spot + (cfg.trail_after_atr_multiple * atr_value)
                and spot < high_water - (cfg.trail_atr_multiplier_for("long") * atr_value)
            ):
                should_exit, exit_reason = True, "trailing_stop"
        else:
            if hard_stop_atr_mult > 0 and spot > entry_spot + (hard_stop_atr_mult * atr_value):
                should_exit, exit_reason = True, "hard_stop"
            elif (
                low_water is not None
                and low_water <= entry_spot - (cfg.trail_after_atr_multiple * atr_value)
                and spot > low_water + (cfg.trail_atr_multiplier_for("short") * atr_value)
            ):
                should_exit, exit_reason = True, "trailing_stop"

    max_bars_in_trade = cfg.max_bars_in_trade_for(position.side or "long")
    bars_since_entry = bars_since(position.entry_ts, bars) if not bars.empty else None
    if not should_exit and bars_since_entry is not None and bars_since_entry >= max_bars_in_trade:
        should_exit, exit_reason = True, "time_stop"

    if not should_exit and cfg.exit_on_regime_invalidation:
        if position.side == "long" and metrics.get("regime_on") is False:
            should_exit, exit_reason = True, "regime_invalidation"
        elif position.side == "short" and metrics.get("regime_bearish") is False:
            should_exit, exit_reason = True, "regime_invalidation"

    if not should_exit:
        # Not exiting this cycle — still persist the updated water marks so
        # the trailing-stop check has accurate history on the next run.
        upsert_option_position_state(
            conn,
            POSITION_KEY,
            position.side,
            position.underlying_symbol,
            position.contract_symbol,
            position.option_type,
            position.strike,
            position.expiration,
            position.contracts,
            position.entry_price,
            position.entry_ts,
            position.entry_delta,
            position.entry_dte,
            entry_spot,
            high_water,
            low_water,
            entry_bar_ts=position.entry_bar_ts,
            entry_signal_side=position.entry_signal_side,
            entry_adx=position.entry_adx,
            entry_atr_pct=position.entry_atr_pct,
            entry_volume_ratio=position.entry_volume_ratio,
            entry_sma_spread_pct=position.entry_sma_spread_pct,
            entry_window_bucket=position.entry_window_bucket,
            entry_signal_strength=position.entry_signal_strength,
        )

    record_run(
        conn,
        _utc_iso_now(),
        symbol,
        spot,
        metrics.get("sma_fast"),
        metrics.get("sma_slow"),
        signal,
        "HOLD_MANAGE_OPTION_POSITION",
        position.contracts,
        equity,
        None,
        f"managing open {position.contract_symbol}",
        reasons=";".join(reasons),
        metrics_json=json.dumps(metrics, default=str),
        bar_ts=metrics.get("bar_ts"),
        strategy_version=os.getenv("STRATEGY_VERSION"),
    )

    if not should_exit:
        return

    snapshots = get_option_snapshots(options_data, [position.contract_symbol])
    snapshot = snapshots.get(position.contract_symbol)
    quote = getattr(snapshot, "latest_quote", None) if snapshot else None
    bid = _as_float(getattr(quote, "bid_price", None)) if quote else None
    ask = _as_float(getattr(quote, "ask_price", None)) if quote else None

    if bid is None or bid <= 0:
        logger.warning(f"No live bid for {position.contract_symbol}; skipping exit this cycle, will retry.")
        record_event(conn, _utc_iso_now(), "warning", "option_exit_skipped_no_quote", symbol=symbol, message="no bid available")
        return

    # Sell at (or a hair above) the bid rather than a naive market order —
    # options books are frequently thin, and a marketable limit protects
    # against an outlier fill on a wide spread. If we have an ask too, cross
    # a small fraction of the spread rather than resting exactly at the bid.
    limit_price = bid if not ask or ask <= bid else bid + (ask - bid) * 0.25

    order = place_option_order(
        trading,
        position.contract_symbol,
        int(position.contracts),
        "sell",
        limit_price,
        "sell_to_close",
    )
    order_id = str(getattr(order, "id", ""))
    record_order_submission(
        conn,
        _utc_iso_now(),
        POSITION_KEY,
        "sell",
        float(position.contracts),
        order_id,
        normalize_order_status(getattr(order, "status", None)),
        None,
        None,
        "sell_to_close",
        "close_option",
        f"exit_reason={exit_reason}",
        0,
        decision_signal=signal,
        entry_metrics_json=json.dumps({"exit_reason": exit_reason, "contract_symbol": position.contract_symbol}),
    )

    exit_price = _as_float(getattr(order, "filled_avg_price", None)) or limit_price
    pnl = (exit_price - float(position.entry_price)) * float(position.contracts) * CONTRACT_MULTIPLIER
    record_closed_trade(
        conn,
        symbol,
        position.side or "long",
        position.entry_ts,
        _utc_iso_now(),
        float(position.entry_price),
        exit_price,
        float(position.contracts),
        pnl,
        (exit_price - float(position.entry_price)) / float(position.entry_price) if position.entry_price else None,
        entry_reason=position.entry_signal_side,
        exit_reason=exit_reason,
        bars_held=bars_since_entry,
        entry_bar_ts=position.entry_bar_ts,
        exit_bar_ts=metrics.get("bar_ts"),
        entry_signal_side=position.entry_signal_side,
        entry_adx=position.entry_adx,
        entry_atr_pct=position.entry_atr_pct,
        entry_volume_ratio=position.entry_volume_ratio,
        entry_sma_spread_pct=position.entry_sma_spread_pct,
        entry_window_bucket=position.entry_window_bucket,
        hold_seconds=(datetime.now(timezone.utc) - entry_dt).total_seconds() if entry_dt else None,
    )
    set_last_trade(conn, _utc_iso_now())
    current_state = get_state(conn, current_equity=equity)
    set_consecutive_losses(conn, 0 if pnl > 0 else current_state.consecutive_losses + 1)
    clear_position_state(conn, POSITION_KEY)
    record_event(
        conn,
        _utc_iso_now(),
        "info",
        "option_position_closed",
        symbol=symbol,
        message=f"closed {position.contract_symbol} reason={exit_reason} pnl={pnl:.2f}",
        payload={"contract_symbol": position.contract_symbol, "exit_reason": exit_reason, "pnl": pnl},
    )


def _handle_entries(conn, trading, options_data, logger, equity: float | None, state) -> None:
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "60"))
    cfg = build_strategy_config_from_env(timeframe_minutes)
    params = build_option_params_from_env()
    symbols = _symbols_from_env()
    cooldown_bars = int(os.getenv("COOLDOWN_BARS", "4"))

    risk_config = RiskConfig(
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "2")),
        max_daily_drawdown_pct=float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.04")),
        max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "0")),
        max_consecutive_losses=int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3")),
        max_bar_age_seconds=int(os.getenv("MAX_BAR_AGE_SECONDS", "0")),
        max_position_notional_pct=params.max_premium_pct_of_equity,
    )

    candidates_by_symbol: dict[str, tuple[str, dict, list[str], pd.DataFrame]] = {}
    for symbol in symbols:
        signal, metrics, reasons, bars = _evaluate_underlying(symbol, timeframe_minutes, cfg)
        candidates_by_symbol[symbol] = (signal, metrics, reasons, bars)
        record_run(
            conn,
            _utc_iso_now(),
            symbol,
            metrics.get("price"),
            metrics.get("sma_fast"),
            metrics.get("sma_slow"),
            signal,
            "HOLD",
            0,
            equity,
            None,
            "flat; evaluating options entry",
            reasons=";".join(reasons),
            metrics_json=json.dumps(metrics, default=str),
            bar_ts=metrics.get("bar_ts"),
            strategy_version=os.getenv("STRATEGY_VERSION"),
        )

    qualifying: list[tuple[str, str, dict, pd.DataFrame]] = []  # (symbol, signal, metrics, bars)
    for symbol, (signal, metrics, reasons, bars) in candidates_by_symbol.items():
        if signal not in ("LONG", "SHORT"):
            continue
        if signal == "SHORT" and not cfg.allow_shorts:
            continue

        ts = pd.Timestamp(metrics.get("bar_ts") or datetime.now(timezone.utc))
        if _is_in_earnings_blackout(ts, params):
            record_event(conn, _utc_iso_now(), "info", "option_entry_skipped", symbol=symbol, message="earnings_blackout")
            continue

        risk_eval = evaluate_entry_risk(
            risk_config,
            trades_today=state.trades_today,
            consecutive_losses=state.consecutive_losses,
            daily_start_equity=state.daily_start_equity,
            current_equity=equity,
            last_bar_ts=metrics.get("bar_close_ts") or metrics.get("bar_ts"),
            position_notional=None,
        )
        if not risk_eval.allow_entries:
            record_event(conn, _utc_iso_now(), "info", "option_entry_blocked", symbol=symbol, message=";".join(risk_eval.reasons))
            continue

        # Cooldown re-entry override (require a stronger signal than last
        # time) isn't enabled for this v1 — always respect a plain cooldown.
        since = bars_since(state.last_trade_ts, bars) if not bars.empty else None
        if since is not None and since < cooldown_bars:
            continue

        qualifying.append((symbol, signal, metrics, bars))

    if not qualifying:
        return

    qualifying.sort(key=lambda item: item[2].get("signal_strength") or 0.0, reverse=True)

    for symbol, signal, metrics, bars in qualifying:
        option_type = "call" if signal == "LONG" else "put"
        side = "long" if signal == "LONG" else "short"
        spot = _as_float(metrics.get("price"))
        if spot is None:
            continue

        today = datetime.now(timezone.utc).date()
        expiration_gte = today + timedelta(days=params.min_dte)
        expiration_lte = today + timedelta(days=params.max_dte)

        try:
            contracts = get_option_chain(trading, symbol, expiration_gte, expiration_lte, option_type)
        except Exception as exc:
            logger.warning(f"Option chain lookup failed for {symbol}: {exc}")
            record_event(conn, _utc_iso_now(), "warning", "option_chain_lookup_failed", symbol=symbol, message=str(exc))
            continue
        if not contracts:
            record_event(conn, _utc_iso_now(), "info", "option_entry_skipped", symbol=symbol, message="no_contracts_in_dte_band")
            continue

        contract_symbols = [c.symbol for c in contracts]
        snapshots = get_option_snapshots(options_data, contract_symbols)
        raw_candidates = build_candidates(contracts, snapshots)

        closes = _daily_closes_up_to(bars[["close"]], pd.Timestamp(datetime.now(timezone.utc))) if not bars.empty else []
        iv_proxy = historical_volatility(closes, window=params.iv_lookback_bars) or params.iv_fallback
        iv_proxy = max(params.iv_floor, iv_proxy)
        enriched_candidates = _estimate_missing_deltas(raw_candidates, spot, iv_proxy)

        budget = (equity or 0.0) * params.max_premium_pct_of_equity
        selection = select_option_contract(
            enriched_candidates,
            option_type,
            params.target_delta,
            params.delta_tolerance,
            params.min_dte,
            params.max_dte,
            params.max_bid_ask_spread_pct,
            budget,
        )
        if selection is None:
            record_event(
                conn,
                _utc_iso_now(),
                "info",
                "option_entry_skipped",
                symbol=symbol,
                message="no_contract_fits_delta_dte_spread_budget",
                payload={"budget": budget, "candidate_count": len(enriched_candidates)},
            )
            continue

        qty = 1
        if selection.mid is not None and selection.mid > 0:
            qty = max(1, int(budget // (selection.mid * CONTRACT_MULTIPLIER)))
        # Marketable limit: cross partway into the spread rather than
        # resting at the bid/ask, so the order actually has a realistic
        # chance of filling without paying the full spread either.
        limit_price = selection.ask if selection.ask else selection.mid

        order = place_option_order(trading, selection.symbol, qty, "buy", limit_price, "buy_to_open")
        order_id = str(getattr(order, "id", ""))
        fill_price = _as_float(getattr(order, "filled_avg_price", None)) or limit_price

        record_order_submission(
            conn,
            _utc_iso_now(),
            POSITION_KEY,
            "buy",
            float(qty),
            order_id,
            normalize_order_status(getattr(order, "status", None)),
            fill_price,
            float(qty),
            "buy_to_open",
            "open_option",
            f"symbol={symbol}",
            0,
            decision_signal=signal,
            entry_metrics_json=json.dumps(metrics, default=str),
        )

        upsert_option_position_state(
            conn,
            POSITION_KEY,
            side,
            symbol,
            selection.symbol,
            option_type,
            selection.strike,
            str(selection.expiration),
            float(qty),
            fill_price,
            _utc_iso_now(),
            selection.delta,
            selection.dte,
            spot,
            spot,
            spot,
            entry_bar_ts=metrics.get("bar_ts"),
            entry_signal_side=side,
            entry_adx=metrics.get("adx"),
            entry_atr_pct=metrics.get("atr_pct"),
            entry_volume_ratio=metrics.get("volume_ratio"),
            entry_sma_spread_pct=metrics.get("sma_spread_pct"),
            entry_window_bucket=metrics.get("entry_window_bucket"),
            entry_signal_strength=metrics.get("signal_strength"),
        )
        increment_trades_today(conn)
        set_last_trade(conn, _utc_iso_now())
        set_last_entry_signal(conn, metrics.get("signal_strength"), side)
        record_event(
            conn,
            _utc_iso_now(),
            "info",
            "option_position_opened",
            symbol=symbol,
            message=f"bought {qty}x {selection.symbol} @ {fill_price}",
            payload={"contract_symbol": selection.symbol, "delta": selection.delta, "dte": selection.dte},
        )
        return  # OPTION_MAX_CONCURRENT_POSITIONS == 1 in v1: stop after the first fill


def main() -> None:
    load_dotenv()
    ensure_runtime_dirs()
    logger = setup_logger("options_engine", LOGS_DIR / "options_engine.log")

    conn = connect()
    init_db(conn)

    trading, _underlying_data = make_clients()
    options_data = make_options_data_client()
    equity, _cash = get_account_snapshot(trading)
    state = get_state(conn, current_equity=equity)

    sync_pending_orders(conn, trading, logger)
    position = get_option_position_state(conn, POSITION_KEY)

    if position.side is not None and position.contract_symbol:
        _handle_exit(conn, trading, options_data, logger, position, equity)
    else:
        _handle_entries(conn, trading, options_data, logger, equity, state)

    conn.close()


if __name__ == "__main__":
    main()
