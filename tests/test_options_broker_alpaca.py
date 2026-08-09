from datetime import date
import unittest

from bot.options_broker_alpaca import ContractCandidate, select_option_contract


def _candidate(
    symbol: str,
    option_type: str = "call",
    strike: float = 100.0,
    dte: int = 35,
    delta: float | None = 0.65,
    mid: float | None = 5.0,
    spread_pct: float | None = 0.05,
) -> ContractCandidate:
    return ContractCandidate(
        symbol=symbol,
        underlying_symbol="NVDA",
        option_type=option_type,
        strike=strike,
        expiration=date(2026, 12, 18),
        dte=dte,
        delta=delta,
        iv=0.45,
        bid=mid - (mid * spread_pct / 2) if mid and spread_pct else None,
        ask=mid + (mid * spread_pct / 2) if mid and spread_pct else None,
        mid=mid,
        spread_pct=spread_pct,
    )


class SelectOptionContractTests(unittest.TestCase):
    def test_picks_candidate_closest_to_target_delta(self):
        candidates = [
            _candidate("A", delta=0.50),
            _candidate("B", delta=0.68),
            _candidate("C", delta=0.85),
        ]
        selected = select_option_contract(
            candidates,
            option_type="call",
            target_delta=0.65,
            delta_tolerance=0.20,
            min_dte=25,
            max_dte=45,
            max_spread_pct=0.10,
            max_premium_budget=1000.0,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.symbol, "B")

    def test_filters_wrong_option_type(self):
        candidates = [_candidate("PUT1", option_type="put", delta=-0.65)]
        selected = select_option_contract(
            candidates, "call", 0.65, 0.15, 25, 45, 0.10, 1000.0,
        )
        self.assertIsNone(selected)

    def test_filters_outside_dte_band(self):
        candidates = [_candidate("TOOSOON", dte=5), _candidate("TOOLATE", dte=90)]
        selected = select_option_contract(
            candidates, "call", 0.65, 0.15, 25, 45, 0.10, 1000.0,
        )
        self.assertIsNone(selected)

    def test_filters_delta_outside_tolerance(self):
        candidates = [_candidate("FAROTM", delta=0.10)]
        selected = select_option_contract(
            candidates, "call", 0.65, 0.15, 25, 45, 0.10, 1000.0,
        )
        self.assertIsNone(selected)

    def test_filters_wide_spread(self):
        candidates = [_candidate("WIDE", spread_pct=0.25)]
        selected = select_option_contract(
            candidates, "call", 0.65, 0.15, 25, 45, 0.10, 1000.0,
        )
        self.assertIsNone(selected)

    def test_filters_over_budget_returns_none_instead_of_looser_contract(self):
        # $500 mid * 100 multiplier = $50,000 premium — nothing close to a
        # $500 account should ever "fall back" to this; it should sit out.
        candidates = [_candidate("EXPENSIVE", mid=500.0)]
        selected = select_option_contract(
            candidates, "call", 0.65, 0.15, 25, 45, 0.10, max_premium_budget=500.0,
        )
        self.assertIsNone(selected)

    def test_missing_delta_is_skipped_not_treated_as_zero(self):
        candidates = [_candidate("NODELTA", delta=None)]
        selected = select_option_contract(
            candidates, "call", 0.65, 0.15, 25, 45, 0.10, 1000.0,
        )
        self.assertIsNone(selected)

    def test_empty_candidates_returns_none(self):
        selected = select_option_contract([], "call", 0.65, 0.15, 25, 45, 0.10, 1000.0)
        self.assertIsNone(selected)

    def test_put_selection_uses_absolute_delta(self):
        candidates = [_candidate("P1", option_type="put", delta=-0.63)]
        selected = select_option_contract(
            candidates, "put", 0.65, 0.15, 25, 45, 0.10, 1000.0,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.symbol, "P1")


if __name__ == "__main__":
    unittest.main()
