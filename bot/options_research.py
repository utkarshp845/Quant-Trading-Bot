"""Modeled options backtest for the LCID long-calls/puts strategy.

**This is a modeled backtest, not a real historical options backtest.**
There is no free historical options-chain data source (unlike the stock
bars `bot/research.py` replays via Alpaca/yfinance), so contract prices here
are computed with Black-Scholes (`bot/options_pricing.py`) over real
historical *stock* prices, using trailing realized volatility as an
implied-volatility proxy. That systematically understates real option
premiums around events (earnings, macro surprises) where implied vol runs
richer than trailing realized vol. Treat this report as a directional sanity
check on the delta/DTE/sizing rules, not proof of live viability — the real
validation step is paper trading against live Alpaca options quotes for a
real stretch (the active evaluation right now) before it's trusted live.

The entry/exit *decision* logic (trend signal, hard stop, trailing stop,
regime invalidation, time stop) is intentionally identical to
`bot/research.py::run_replay`'s equity logic, evaluated on the underlying's
price/ATR — only the fill/PnL mechanics change to option premiums. That
keeps this replay testing the same proven signal, not a different one.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.broker_alpaca import get_historical_bars, make_clients
from bot.metrics import closed_trade_summary, max_drawdown, summarize_by_group
from bot.options_pricing import historical_volatility, price_option, solve_strike_for_delta
from bot.paths import REPORTS_DIR, ensure_runtime_dirs
from bot.risk import RiskConfig
from bot.strategy_ma import StrategyConfig, build_strategy_config_from_env, compute_indicators, generate_signal
from bot.trade_controls import (
    ReplayState,
    evaluate_replay_entry,
    record_replay_entry,
    record_replay_exit,
    sync_replay_day,
)


ET = ZoneInfo("America/New_York")
CONTRACT_MULTIPLIER = 100
DEFAULT_RATE = 0.04


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class OptionStrategyParams:
    target_delta: float
    delta_tolerance: float
    min_dte: int
    max_dte: int
    exit_min_dte: int
    max_premium_pct_of_equity: float
    max_bid_ask_spread_pct: float
    earnings_blackout_days: int
    earnings_blackout_dates: tuple[date, ...]
    iv_lookback_bars: int
    iv_floor: float
    iv_fallback: float


def build_option_params_from_env() -> OptionStrategyParams:
    raw_dates = os.getenv("OPTION_EARNINGS_BLACKOUT_DATES", "")
    blackout_dates: list[date] = []
    for token in raw_dates.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            blackout_dates.append(datetime.strptime(token, "%Y-%m-%d").date())
        except ValueError:
            continue

    return OptionStrategyParams(
        target_delta=float(os.getenv("OPTION_TARGET_DELTA", "0.65")),
        delta_tolerance=float(os.getenv("OPTION_DELTA_TOLERANCE", "0.15")),
        min_dte=int(os.getenv("OPTION_MIN_DTE", "25")),
        max_dte=int(os.getenv("OPTION_MAX_DTE", "45")),
        exit_min_dte=int(os.getenv("OPTION_EXIT_MIN_DTE", "12")),
        max_premium_pct_of_equity=float(os.getenv("OPTION_MAX_PREMIUM_PCT_OF_EQUITY", "0.55")),
        max_bid_ask_spread_pct=float(os.getenv("OPTION_MAX_BID_ASK_SPREAD_PCT", "0.08")),
        earnings_blackout_days=int(os.getenv("OPTION_EARNINGS_BLACKOUT_DAYS", "3")),
        earnings_blackout_dates=tuple(blackout_dates),
        iv_lookback_bars=int(os.getenv("OPTION_IV_LOOKBACK_DAYS", "20")),
        iv_floor=float(os.getenv("OPTION_IV_FLOOR", "0.25")),
        iv_fallback=float(os.getenv("OPTION_IV_FALLBACK", "0.45")),
    )


def _is_in_earnings_blackout(ts: pd.Timestamp, params: OptionStrategyParams) -> bool:
    if not params.earnings_blackout_dates:
        return False
    trade_date = ts.tz_convert(ET).date() if ts.tzinfo is not None else ts.date()
    window = timedelta(days=params.earnings_blackout_days)
    return any(abs((trade_date - blackout).days) <= window.days for blackout in params.earnings_blackout_dates)


def _daily_closes_up_to(bars: pd.DataFrame, ts: pd.Timestamp) -> list[float]:
    slice_df = bars[bars.index <= ts]
    if slice_df.empty:
        return []
    daily = slice_df["close"].resample("1D").last().dropna()
    return daily.tolist()


@dataclass
class OptionReplayPosition:
    side: str  # "long" (call) or "short" (put)
    option_type: str
    contracts: int
    strike: float
    chosen_dte: int
    entry_ts: object
    entry_spot: float
    entry_premium: float
    iv: float
    high_water: float
    low_water: float
    entry_metrics: dict


def _mark_option(
    position: OptionReplayPosition,
    spot: float,
    ts,
    entry_ts,
) -> tuple[float, float]:
    elapsed_days = (pd.Timestamp(ts) - pd.Timestamp(entry_ts)).total_seconds() / 86400.0
    remaining_days = max(0.0, position.chosen_dte - elapsed_days)
    t_years = remaining_days / 365.0
    result = price_option(spot, position.strike, t_years, position.iv, position.option_type, DEFAULT_RATE)
    return result.price, remaining_days


def run_options_replay(
    bars: pd.DataFrame,
    cfg: StrategyConfig,
    params: OptionStrategyParams,
    starting_equity: float,
    slippage_multiplier: float = 1.0,
) -> tuple[pd.DataFrame, list[dict]]:
    commission = float(os.getenv("RESEARCH_COMMISSION_PER_TRADE", "0.65"))
    cooldown_bars = int(os.getenv("COOLDOWN_BARS", "4"))
    allow_shorts = cfg.allow_shorts
    hard_stop_atr_mult = float(os.getenv("HARD_STOP_ATR_MULT", "0"))
    spread_friction = params.max_bid_ask_spread_pct * slippage_multiplier
    chosen_dte = int(round((params.min_dte + params.max_dte) / 2.0))

    risk_config = RiskConfig(
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "2")),
        max_daily_drawdown_pct=float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.04")),
        max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "0")),
        max_consecutive_losses=int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3")),
        max_bar_age_seconds=0,
        max_position_notional_pct=params.max_premium_pct_of_equity,
    )

    bars2 = compute_indicators(bars, cfg)
    equity = starting_equity
    position: OptionReplayPosition | None = None
    state = ReplayState(daily_start_equity=starting_equity)
    equity_rows: list[dict] = []
    trades: list[dict] = []

    for i in range(len(bars2)):
        slice_df = bars2.iloc[: i + 1]
        signal, metrics, reasons = generate_signal(slice_df, cfg)
        row = bars2.iloc[i]
        ts = bars2.index[i]
        spot = float(metrics.get("price") or row["close"])
        atr_value = float(metrics.get("atr") or 0.0) if metrics.get("atr") is not None else None
        signal_strength = float(metrics.get("signal_strength") or 0.0)
        sync_replay_day(state, ts, equity)
        exited_this_bar = False

        if position is not None:
            mark_price, remaining_days = _mark_option(position, spot, ts, position.entry_ts)
            position.high_water = max(position.high_water, spot)
            position.low_water = min(position.low_water, spot)

            should_exit = False
            exit_reason = None

            if remaining_days <= params.exit_min_dte:
                should_exit, exit_reason = True, "dte_exit"

            if not should_exit and position.side == "long":
                if hard_stop_atr_mult > 0 and atr_value is not None and spot < position.entry_spot - (hard_stop_atr_mult * atr_value):
                    should_exit, exit_reason = True, "hard_stop"
                elif cfg.exit_on_regime_invalidation and metrics.get("regime_on") is False:
                    should_exit, exit_reason = True, "regime_invalidation"
                elif (
                    atr_value is not None
                    and position.high_water >= position.entry_spot + (cfg.trail_after_atr_multiple * atr_value)
                    and spot < position.high_water - (cfg.trail_atr_multiplier_for("long") * atr_value)
                ):
                    should_exit, exit_reason = True, "trailing_stop"
            elif not should_exit and position.side == "short":
                if hard_stop_atr_mult > 0 and atr_value is not None and spot > position.entry_spot + (hard_stop_atr_mult * atr_value):
                    should_exit, exit_reason = True, "hard_stop"
                elif cfg.exit_on_regime_invalidation and metrics.get("regime_bearish") is False:
                    should_exit, exit_reason = True, "regime_invalidation"
                elif (
                    atr_value is not None
                    and position.low_water <= position.entry_spot - (cfg.trail_after_atr_multiple * atr_value)
                    and spot > position.low_water + (cfg.trail_atr_multiplier_for("short") * atr_value)
                ):
                    should_exit, exit_reason = True, "trailing_stop"

            bars_held = int((bars2.index[: i + 1] > position.entry_ts).sum())
            if not should_exit and bars_held >= cfg.max_bars_in_trade_for(position.side):
                should_exit, exit_reason = True, "time_stop"

            if should_exit:
                exit_fill_premium = mark_price * (1.0 - spread_friction / 2.0)
                pnl = (exit_fill_premium - position.entry_premium) * position.contracts * CONTRACT_MULTIPLIER - commission
                equity += pnl
                record_replay_exit(state, ts, pnl, count_as_entry_failure=(pnl <= 0))
                trades.append(
                    {
                        "entry_ts": str(position.entry_ts),
                        "exit_ts": str(ts),
                        "side": position.side,
                        "option_type": position.option_type,
                        "strike": position.strike,
                        "contracts": position.contracts,
                        "entry_spot": position.entry_spot,
                        "exit_spot": spot,
                        "entry_premium": position.entry_premium,
                        "exit_premium": exit_fill_premium,
                        "pnl": pnl,
                        "return_pct": (exit_fill_premium - position.entry_premium) / position.entry_premium if position.entry_premium else None,
                        "exit_reason": exit_reason,
                        "entry_signal_side": position.entry_metrics.get("entry_signal_side"),
                        "entry_adx": position.entry_metrics.get("adx"),
                        "entry_atr_pct": position.entry_metrics.get("atr_pct"),
                        "entry_volume_ratio": position.entry_metrics.get("volume_ratio"),
                        "entry_iv": position.iv,
                        "entry_dte": position.chosen_dte,
                        "hold_seconds": (pd.Timestamp(ts) - pd.Timestamp(position.entry_ts)).total_seconds(),
                    }
                )
                position = None
                exited_this_bar = True

        can_attempt_short = allow_shorts and signal == "SHORT"
        can_attempt_long = signal == "LONG"
        if position is None and not exited_this_bar and (can_attempt_long or can_attempt_short):
            entry_blockers = evaluate_replay_entry(
                state,
                slice_df,
                ts,
                signal,
                signal_strength,
                equity,
                metrics.get("bar_close_ts") or metrics.get("bar_ts"),
                position_notional=None,
                cooldown_bars=cooldown_bars,
                risk_config=risk_config,
                require_signal_strength_improvement=False,
                min_signal_strength_delta=0.0,
                max_consecutive_entry_failures_per_day=int(os.getenv("MAX_CONSECUTIVE_ENTRY_FAILURES_PER_DAY", "0")),
            )

            if _is_in_earnings_blackout(pd.Timestamp(ts), params):
                entry_blockers.append("earnings_blackout")

            option_type = "call" if signal == "LONG" else "put"
            side = "long" if signal == "LONG" else "short"
            iv = None
            contracts = 0
            entry_premium = 0.0
            strike = 0.0

            if not entry_blockers:
                closes = _daily_closes_up_to(bars2[["close"]], ts)
                iv = historical_volatility(closes, window=params.iv_lookback_bars)
                iv = max(params.iv_floor, iv) if iv is not None else params.iv_fallback

                t_years_entry = chosen_dte / 365.0
                strike = solve_strike_for_delta(spot, params.target_delta, t_years_entry, iv, option_type, DEFAULT_RATE)
                entry_result = price_option(spot, strike, t_years_entry, iv, option_type, DEFAULT_RATE)
                mid_premium = entry_result.price
                entry_premium = mid_premium * (1.0 + spread_friction / 2.0)

                budget = equity * params.max_premium_pct_of_equity
                contracts = math.floor(budget / (entry_premium * CONTRACT_MULTIPLIER)) if entry_premium > 0 else 0
                if contracts <= 0:
                    entry_blockers.append("premium_budget_insufficient")

            if not entry_blockers and contracts > 0:
                position = OptionReplayPosition(
                    side=side,
                    option_type=option_type,
                    contracts=contracts,
                    strike=strike,
                    chosen_dte=chosen_dte,
                    entry_ts=ts,
                    entry_spot=spot,
                    entry_premium=entry_premium,
                    iv=iv,
                    high_water=spot,
                    low_water=spot,
                    entry_metrics={
                        "entry_signal_side": side,
                        "adx": metrics.get("adx"),
                        "atr_pct": metrics.get("atr_pct"),
                        "volume_ratio": metrics.get("volume_ratio"),
                    },
                )
                equity -= commission
                record_replay_entry(state, ts, signal, signal_strength)
                reasons = list(reasons) + entry_blockers
            else:
                reasons = list(reasons) + entry_blockers
        elif position is None and not exited_this_bar and signal == "SHORT" and not allow_shorts:
            reasons = list(reasons) + ["short_signal_diagnostic_only"]

        equity_rows.append({"ts": str(ts), "equity": equity, "signal": signal, "reasons": ";".join(reasons)})

    return pd.DataFrame(equity_rows), trades


def summarize_option_replay(trades_df: pd.DataFrame, equity_df: pd.DataFrame) -> dict:
    summary = closed_trade_summary(trades_df)
    summary["max_drawdown"] = max_drawdown(equity_df["equity"]) if not equity_df.empty else None
    if not trades_df.empty and "entry_ts" in trades_df.columns:
        entry_ts = pd.to_datetime(trades_df["entry_ts"], utc=True, errors="coerce")
        days = max(1, entry_ts.dt.tz_convert(ET).dt.date.nunique())
        summary["trades_per_day"] = float(len(trades_df) / days)
    else:
        summary["trades_per_day"] = 0.0
    return summary


def write_report(path_md, path_json, payload: dict) -> None:
    lines = [
        "# Options Research Report (MODELED — Black-Scholes over historical stock bars)",
        "",
        "**This is not a real historical options backtest.** Contract prices are",
        "modeled with Black-Scholes over real historical stock prices using",
        "trailing realized volatility as an implied-vol proxy — there is no free",
        "historical options-chain data source. Treat this as a directional sanity",
        "check on the delta/DTE/sizing rules, not proof of live viability. See",
        "`docs/strategy_options_2026-08.md`.",
        "",
        f"Generated: {datetime.now(timezone.utc).astimezone(ET)}",
        f"- Symbol: {payload['symbol']}",
        f"- Timeframe: {payload['timeframe_minutes']}m",
        f"- Target delta: {payload['params']['target_delta']} (+/- {payload['params']['delta_tolerance']})",
        f"- DTE band: {payload['params']['min_dte']}-{payload['params']['max_dte']}, exit at {payload['params']['exit_min_dte']} DTE remaining",
        f"- Premium budget: {payload['params']['max_premium_pct_of_equity'] * 100:.0f}% of equity per trade",
        "",
        "## Full Sample Summary",
    ]
    full = payload["full_summary"]
    lines.extend(
        [
            f"- Trades: {full['trade_count']}",
            f"- Net P&L: {full['net_pnl']}",
            f"- Profit factor: {full['profit_factor']}",
            f"- Win rate: {full['win_rate']}",
            f"- Avg trade: {full['avg_pnl']}",
            f"- Expectancy: {full['expectancy']}",
            f"- Max drawdown: {full['max_drawdown']}",
            f"- Trades per day: {full['trades_per_day']}",
            "",
            "## Slippage Stress (modeled spread widened)",
        ]
    )
    for row in payload.get("slippage_stress", []):
        summary = row["summary"]
        lines.append(
            f"- {row['slippage_multiplier']}x modeled spread: net={summary['net_pnl']} pf={summary['profit_factor']} trades/day={summary['trades_per_day']}"
        )

    for title, rows in (
        ("## By Side", payload["by_side"]),
        ("## By Exit Reason", payload["by_exit_reason"]),
    ):
        lines.extend(["", title])
        if rows:
            for r in rows[:8]:
                lines.append(f"- {r['bucket']}: trades={r['trade_count']} net={r['net_pnl']} avg={r['avg_pnl']} pf={r['profit_factor']}")
        else:
            lines.append("- No data.")

    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
    load_dotenv()
    ensure_runtime_dirs()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    symbol = os.getenv("SYMBOL", "LCID").strip().upper()
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "60"))
    lookback_days = int(os.getenv("RESEARCH_LOOKBACK_DAYS", "365"))
    starting_equity = float(os.getenv("RESEARCH_STARTING_EQUITY", "500"))

    trading, data = make_clients()
    del trading
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    bars = get_historical_bars(data, symbol, timeframe_minutes, start=start, end=end, limit=None)
    if bars.empty:
        raise RuntimeError(
            "Options research received no historical bars from Alpaca. Check credentials, symbol, "
            "market-data access, and the selected lookback window."
        )

    cfg = build_strategy_config_from_env(timeframe_minutes)
    params = build_option_params_from_env()
    equity_df, trades = run_options_replay(bars, cfg, params, starting_equity)
    trades_df = pd.DataFrame(trades)
    if not trades_df.empty and "exit_reason" in trades_df.columns:
        trades_df["exit_reason_bucket"] = trades_df["exit_reason"]

    full_summary = summarize_option_replay(trades_df, equity_df)

    slippage_stress = []
    for multiplier in (1.0, 2.0):
        stress_equity, stress_trades = run_options_replay(bars, cfg, params, starting_equity, slippage_multiplier=multiplier)
        slippage_stress.append(
            {
                "slippage_multiplier": multiplier,
                "summary": summarize_option_replay(pd.DataFrame(stress_trades), stress_equity),
            }
        )

    payload = {
        "symbol": symbol,
        "timeframe_minutes": timeframe_minutes,
        "params": asdict(params),
        "strategy_config": asdict(cfg),
        "full_summary": full_summary,
        "slippage_stress": slippage_stress,
        "by_side": summarize_by_group(trades_df, "entry_signal_side") if not trades_df.empty else [],
        "by_exit_reason": summarize_by_group(trades_df, "exit_reason_bucket") if not trades_df.empty and "exit_reason_bucket" in trades_df.columns else [],
    }

    stem = os.getenv("RESEARCH_OUTPUT_STEM", f"research_options_{symbol.lower()}").strip()
    write_report(REPORTS_DIR / f"{stem}.md", REPORTS_DIR / f"{stem}.json", payload)
    print(f"Wrote {REPORTS_DIR / f'{stem}.md'}")
    print(f"Wrote {REPORTS_DIR / f'{stem}.json'}")


if __name__ == "__main__":
    main()
