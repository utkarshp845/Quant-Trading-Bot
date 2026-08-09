import unittest

from bot.options_pricing import historical_volatility, price_option, solve_strike_for_delta


class OptionsPricingTests(unittest.TestCase):
    def test_atm_call_delta_near_half(self):
        result = price_option(spot=100.0, strike=100.0, t_years=30 / 365.0, vol=0.40, option_type="call")
        # ATM delta is slightly above 0.5 due to the (rate + 0.5*vol^2) drift term.
        self.assertGreater(result.delta, 0.45)
        self.assertLess(result.delta, 0.65)
        self.assertGreater(result.price, 0.0)

    def test_call_price_increases_with_spot(self):
        low = price_option(spot=95.0, strike=100.0, t_years=30 / 365.0, vol=0.40, option_type="call")
        high = price_option(spot=110.0, strike=100.0, t_years=30 / 365.0, vol=0.40, option_type="call")
        self.assertGreater(high.price, low.price)
        self.assertGreater(high.delta, low.delta)

    def test_put_delta_is_negative(self):
        result = price_option(spot=100.0, strike=100.0, t_years=30 / 365.0, vol=0.40, option_type="put")
        self.assertLess(result.delta, 0.0)
        self.assertGreater(result.delta, -1.0)

    def test_deep_itm_call_delta_near_one(self):
        result = price_option(spot=200.0, strike=100.0, t_years=30 / 365.0, vol=0.30, option_type="call")
        self.assertGreater(result.delta, 0.9)

    def test_deep_otm_call_delta_near_zero(self):
        result = price_option(spot=60.0, strike=100.0, t_years=30 / 365.0, vol=0.30, option_type="call")
        self.assertLess(result.delta, 0.1)

    def test_zero_time_returns_intrinsic_value(self):
        result = price_option(spot=110.0, strike=100.0, t_years=0.0, vol=0.30, option_type="call")
        self.assertAlmostEqual(result.price, 10.0, places=6)
        self.assertEqual(result.theta, 0.0)

    def test_invalid_option_type_raises(self):
        with self.assertRaises(ValueError):
            price_option(spot=100.0, strike=100.0, t_years=0.1, vol=0.3, option_type="straddle")

    def test_solve_strike_for_delta_round_trips(self):
        spot, iv, t_years = 150.0, 0.45, 35 / 365.0
        strike = solve_strike_for_delta(spot, 0.65, t_years, iv, "call")
        result = price_option(spot, strike, t_years, iv, "call")
        self.assertAlmostEqual(result.delta, 0.65, delta=0.01)

    def test_solve_strike_for_delta_put(self):
        spot, iv, t_years = 250.0, 0.55, 35 / 365.0
        strike = solve_strike_for_delta(spot, 0.65, t_years, iv, "put")
        result = price_option(spot, strike, t_years, iv, "put")
        self.assertAlmostEqual(abs(result.delta), 0.65, delta=0.01)

    def test_historical_volatility_zero_for_flat_prices(self):
        closes = [100.0] * 25
        vol = historical_volatility(closes, window=20)
        self.assertIsNotNone(vol)
        self.assertAlmostEqual(vol, 0.0, places=6)

    def test_historical_volatility_positive_for_moving_prices(self):
        closes = [100.0 + (i % 5) * 2 for i in range(30)]
        vol = historical_volatility(closes, window=20)
        self.assertIsNotNone(vol)
        self.assertGreater(vol, 0.0)

    def test_historical_volatility_none_with_insufficient_data(self):
        self.assertIsNone(historical_volatility([100.0, 101.0], window=20))


if __name__ == "__main__":
    unittest.main()
