"""Offline regression tests for first-500 refresh prioritization.

These tests call ordering helpers only. They do not invoke an updater main(), make provider
requests, change workflow settings, or publish data.
"""
import datetime as dt
import unittest

import financials_updater as financials
import fundamentals_updater as fundamentals


class FundamentalsPriorityTests(unittest.TestCase):
    def test_first_500_are_prioritized_inside_each_existing_state_group(self):
        symbols = [f"S{i:04d}" for i in range(502)]
        instruments = {s: {"isin": f"ISIN_{s}"} for s in symbols}
        # A malformed record outside the core cohort is still in the higher-priority
        # "invalid" group; group priority must remain stronger than cohort priority.
        stocks = {
            symbols[501]: {
                "ratios_fetched": dt.date.today().isoformat(),
                "ratios": {},
            }
        }

        ordered, states = fundamentals.priority(
            stocks, symbols, instruments, dt.date.today()
        )

        self.assertEqual(states[symbols[501]], "invalid")
        self.assertEqual(ordered[0], symbols[501])
        missing = [s for s in ordered if states[s] == "missing"]
        self.assertEqual(set(missing[:500]), set(symbols[:500]))
        self.assertEqual(set(missing[500:]), {symbols[500]})

    def test_first_500_cohort_is_defined_by_input_order(self):
        symbols = [f"S{i:04d}" for i in range(502)]
        instruments = {s: {"isin": f"ISIN_{s}"} for s in symbols}
        ordered, _ = fundamentals.priority({}, symbols, instruments, dt.date.today())

        self.assertEqual(set(ordered[:500]), set(symbols[:500]))
        self.assertEqual(set(ordered[500:]), set(symbols[500:]))


class FinancialsPriorityTests(unittest.TestCase):
    def test_first_500_precede_remainder_but_staleness_order_is_preserved_within_cohort(self):
        symbols = [f"S{i:04d}" for i in range(502)]
        stocks = {}
        for i, symbol in enumerate(symbols):
            # The remainder is deliberately much staler. The first-500 cohort still
            # comes first, while the oldest core record remains first within the core.
            fetched = "2020-01-01" if i == 0 else (
                "2021-01-01" if i == 1 else (
                    "2010-01-01" if i >= 500 else "2022-01-01"
                )
            )
            stocks[symbol] = {
                name: {"fetched": fetched}
                for name in ("income", "balance_sheet", "cash_flow")
            }

        ordered = financials.order_symbols(symbols, stocks)

        self.assertEqual(set(ordered[:500]), set(symbols[:500]))
        self.assertEqual(set(ordered[500:]), set(symbols[500:]))
        self.assertLess(ordered.index(symbols[0]), ordered.index(symbols[1]))


if __name__ == "__main__":
    unittest.main()
