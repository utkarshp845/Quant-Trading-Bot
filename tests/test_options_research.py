from datetime import datetime, date
import os
from unittest.mock import patch
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.options_research import OptionStrategyParams, _is_in_earnings_blackout, run_options_replay
from bot.strategy_ma import build_strategy_config_from_env


UTC = ZoneInfo("UTC")


def _bars(periods: int = 5) -> pd.DataFrame:
    index = pd.date_range(start=datetime(2026, 3, 2, 14, 30, tzinfo=UTC), periods=periods, freq="60min")
    close = [100.0 + i for i in range(periods)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [50000 + i * 100 for i in range(periods)],
        },
        index=index,
    )


def _params(**overrides) -> OptionStrategyParams:
    base = dict(
        target_delta=0.65,
        delta_tolerance=0.15,
        min_dte=25,
        max_dte=45,
        exit_min_dte=12,
        max_premium_pct_of_equity=0.55,
        max_bid_ask_spread_pct=0.08,
        earnings_blackout_days=3,
        earnings_blackout_dates=(),
        iv_lookback_bars=20,
        iv_floor=0.25,
        iv_fallback=0.45,
    )
    base.update(overrides)
    return OptionStrategyParams(**base)


BASE_ENV = {
    "MAX_TRADES_PER_DAY": "5",
    "MAX_DAILY_DRAWDOWN_PCT": "0.5",
    "MAX_DAILY_LOSS": "0",
    "MAX_CONSECUTIVE_LOSSES": "5",
    "COOLDOWN_BARS": "0",
    "HARD_STOP_ATR_MULT": "0",
    "MAX_BARS_IN_TRADE": "50",
    "RESEARCH_COMMISSION_PER_TRADE": "0",
    "ALLOW_SHORTS": "true",
}


class OptionsReplayTests(unittest.TestCase):
    def test_regime_invalidation_closes_option_position(self):
        bars = _bars(periods=3)
        metrics_sequence = [
            {"price": 100.0, "atr": 1.0, "bar_ts": bars.index[0].isoformat(), "signal_strength": 50.0, "regime_on": True, "regime_bearish": False},
            {"price": 101.0, "atr": 1.0, "bar_ts": bars.index[1].isoformat(), "signal_strength": 25.0, "regime_on": True, "regime_bearish": False},
            {"price": 100.5, "atr": 1.0, "bar_ts": bars.index[2].isoformat(), "signal_strength": 15.0, "regime_on": False, "regime_bearish": False},
        ]
        signals = [
            ("LONG", ["long_entry_filters_passed"]),
            ("HOLD", ["trend_up_no_entry"]),
            ("HOLD", ["regime_filter_failed"]),
        ]

        def fake_generate_signal(_slice_df, _cfg):
            idx = len(_slice_df) - 1
            signal, reasons = signals[idx]
            return signal, dict(metrics_sequence[idx]), reasons

        with patch("bot.options_research.compute_indicators", return_value=bars), patch(
            "bot.options_research.generate_signal", side_effect=fake_generate_signal
        ), patch("bot.options_research.historical_volatility", return_value=0.40):
            with patch.dict(os.environ, BASE_ENV, clear=False):
                cfg = build_strategy_config_from_env(60)
                # Deliberately well above the $500 target account: this test
                # is about the exit-decision logic, not the premium-budget
                # filter (that's covered by test_insufficient_budget_sits_out_*
                # below) — a $150-$500 account frequently can't afford a
                # 0.65-delta/35-DTE NVDA/TSLA contract at all, which is the
                # documented, intended "sit out" behavior, not a bug.
                equity_df, trades = run_options_replay(bars, cfg, _params(), starting_equity=5000.0)

        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["side"], "long")
        self.assertEqual(trade["option_type"], "call")
        self.assertEqual(trade["exit_reason"], "regime_invalidation")
        self.assertGreater(trade["contracts"], 0)
        self.assertGreater(trade["entry_premium"], 0.0)
        self.assertFalse(equity_df.empty)

    def test_insufficient_budget_sits_out_instead_of_forcing_a_trade(self):
        bars = _bars(periods=2)
        metrics_sequence = [
            {"price": 100.0, "atr": 1.0, "bar_ts": bars.index[0].isoformat(), "signal_strength": 50.0, "regime_on": True, "regime_bearish": False},
            {"price": 100.5, "atr": 1.0, "bar_ts": bars.index[1].isoformat(), "signal_strength": 30.0, "regime_on": True, "regime_bearish": False},
        ]
        signals = [("LONG", ["long_entry_filters_passed"]), ("HOLD", ["trend_up_no_entry"])]

        def fake_generate_signal(_slice_df, _cfg):
            idx = len(_slice_df) - 1
            signal, reasons = signals[idx]
            return signal, dict(metrics_sequence[idx]), reasons

        with patch("bot.options_research.compute_indicators", return_value=bars), patch(
            "bot.options_research.generate_signal", side_effect=fake_generate_signal
        ), patch("bot.options_research.historical_volatility", return_value=0.40):
            with patch.dict(os.environ, BASE_ENV, clear=False):
                cfg = build_strategy_config_from_env(60)
                # $1 of equity can't afford even a cheap contract at 55% budget.
                _equity_df, trades = run_options_replay(bars, cfg, _params(), starting_equity=1.0)

        self.assertEqual(len(trades), 0)

    def test_earnings_blackout_blocks_entry(self):
        bars = _bars(periods=1)
        params = _params(earnings_blackout_dates=(date(2026, 3, 2),), earnings_blackout_days=3)

        def fake_generate_signal(_slice_df, _cfg):
            return "LONG", {
                "price": 100.0,
                "atr": 1.0,
                "bar_ts": bars.index[0].isoformat(),
                "signal_strength": 50.0,
                "regime_on": True,
                "regime_bearish": False,
            }, ["long_entry_filters_passed"]

        with patch("bot.options_research.compute_indicators", return_value=bars), patch(
            "bot.options_research.generate_signal", side_effect=fake_generate_signal
        ):
            with patch.dict(os.environ, BASE_ENV, clear=False):
                cfg = build_strategy_config_from_env(60)
                _equity_df, trades = run_options_replay(bars, cfg, params, starting_equity=500.0)

        self.assertEqual(len(trades), 0)


class EarningsBlackoutHelperTests(unittest.TestCase):
    def test_within_window_is_blackout(self):
        params = _params(earnings_blackout_dates=(date(2026, 3, 2),), earnings_blackout_days=3)
        ts = pd.Timestamp(datetime(2026, 3, 3, 15, 0, tzinfo=UTC))
        self.assertTrue(_is_in_earnings_blackout(ts, params))

    def test_outside_window_is_not_blackout(self):
        params = _params(earnings_blackout_dates=(date(2026, 3, 2),), earnings_blackout_days=3)
        ts = pd.Timestamp(datetime(2026, 3, 20, 15, 0, tzinfo=UTC))
        self.assertFalse(_is_in_earnings_blackout(ts, params))

    def test_no_dates_never_blocks(self):
        params = _params(earnings_blackout_dates=())
        ts = pd.Timestamp(datetime(2026, 3, 2, 15, 0, tzinfo=UTC))
        self.assertFalse(_is_in_earnings_blackout(ts, params))


if __name__ == "__main__":
    unittest.main()
