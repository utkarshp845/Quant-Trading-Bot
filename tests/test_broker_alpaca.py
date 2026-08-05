from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
import unittest

import pandas as pd
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from bot.broker_alpaca import _resolve_timeframe, get_recent_bars


UTC = ZoneInfo("UTC")


class ResolveTimeframeTests(unittest.TestCase):
    def test_sub_hour_minutes_use_minute_unit(self):
        tf = _resolve_timeframe(5)
        self.assertEqual(tf.amount_value, 5)
        self.assertEqual(tf.unit_value, TimeFrameUnit.Minute)

    def test_sixty_minutes_uses_one_hour_unit(self):
        # Regression: Alpaca rejects TimeFrame(60, Minute) — "Second or Minute
        # units can only be used with amounts between 1-59." Hourly bars must
        # be requested as TimeFrame(1, Hour) instead.
        tf = _resolve_timeframe(60)
        self.assertEqual(tf.amount_value, 1)
        self.assertEqual(tf.unit_value, TimeFrameUnit.Hour)

    def test_multi_hour_minutes_use_hour_unit(self):
        tf = _resolve_timeframe(120)
        self.assertEqual(tf.amount_value, 2)
        self.assertEqual(tf.unit_value, TimeFrameUnit.Hour)

    def test_daily_minutes_use_day_unit(self):
        tf = _resolve_timeframe(1440)
        self.assertEqual(tf.amount_value, 1)
        self.assertEqual(tf.unit_value, TimeFrameUnit.Day)

    def test_unsupported_timeframe_raises(self):
        with self.assertRaises(ValueError):
            _resolve_timeframe(90)

    def test_non_positive_timeframe_raises(self):
        with self.assertRaises(ValueError):
            _resolve_timeframe(0)


class BrokerAlpacaTests(unittest.TestCase):
    def test_get_recent_bars_returns_latest_rows_from_fetched_window(self):
        index = pd.date_range(start=datetime(2026, 4, 7, 13, 30, tzinfo=UTC), periods=300, freq="5min")
        bars = pd.DataFrame(
            {
                "open": range(300),
                "high": range(300),
                "low": range(300),
                "close": range(300),
                "volume": [1000] * 300,
            },
            index=index,
        )

        with patch("bot.broker_alpaca.get_historical_bars", return_value=bars) as mocked:
            recent = get_recent_bars(object(), "SPY", 5, limit=220)

        self.assertEqual(len(recent), 220)
        self.assertEqual(recent.index[0], bars.index[-220])
        self.assertEqual(recent.index[-1], bars.index[-1])
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
