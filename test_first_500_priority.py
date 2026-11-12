"""Offline regression tests for first-500 refresh prioritization.

These tests call ordering helpers only. They do not invoke an updater main(), make provider
requests, change workflow settings, or publish data.
"""
import datetime as dt
import unittest

import financials_updater as financials
import fundamentals_updater as fundamentals
import research_test_batch
import universe


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


DAY = dt.date(2026, 10, 24)
METRICS = fundamentals.METRICS


def sym(i):
    return "S%04d" % i


def universe_symbols(n):
    return [sym(i) for i in range(n)]


def instruments_for(symbols):
    return {s: {"isin": "ISIN_" + s, "name": s} for s in symbols}


def ago(days):
    return (DAY - dt.timedelta(days=days)).isoformat()


def sound_record(ratios_days_ago, profile_days_ago=1, **extra):
    """A cached fundamentals record that passes record_problems, fetched that many days before DAY."""
    e = {"isin": None, "ratios": {m: 1.0 for m in METRICS}, "ratios_fetched": ago(ratios_days_ago), "profile_fetched": ago(profile_days_ago)}
    e.update(extra)
    return e


def old_priority(stocks, symbols, instruments, day):
    """The ordering before this change, kept here as the reference for universes of at most 500 symbols."""
    by = {s: fundamentals.state_of(stocks.get(s), instruments.get(s), day) for s in symbols}
    out = []
    for st in fundamentals.ELIGIBLE:
        group = [s for s in symbols if by[s] == st]
        out += universe.order_stalest(group, {s: fundamentals.staleness_key(stocks.get(s)) for s in group})
    return out, by


class FundamentalsEdgeCases(unittest.TestCase):
    def setUp(self):
        self.symbols = universe_symbols(520)
        self.inst = instruments_for(self.symbols)

    def order(self, stocks, symbols=None, inst=None):
        symbols = symbols or self.symbols
        return fundamentals.priority(stocks, symbols, inst or instruments_for(symbols), DAY)

    def test_a_very_stale_outsider_does_not_displace_a_core_stock_in_the_same_group(self):
        stocks = {sym(10): sound_record(8), sym(20): sound_record(9), sym(515): sound_record(400, profile_days_ago=400)}
        ordered, by = self.order(stocks)
        self.assertEqual({by[sym(10)], by[sym(20)], by[sym(515)]}, {"stale"})
        stale = [s for s in ordered if by[s] == "stale"]
        self.assertEqual(stale, [sym(20), sym(10), sym(515)])           # core first (oldest first), the 400-day-old outsider last

    def test_state_groups_still_outrank_cohorts(self):
        stocks = {sym(5): sound_record(10),                                                  # core, stale
                  sym(510): sound_record(10, last_error="HTTP 500", last_attempt=ago(5), failures=1),   # outsider, failed and due
                  sym(511): {"ratios_fetched": "2026-13-45"},                                # outsider, invalid
                  sym(512): sound_record(1)}                                                 # outsider, fresh: not eligible
        ordered, by = self.order(stocks)
        self.assertEqual((by[sym(511)], by[sym(510)], by[sym(5)], by[sym(512)]), ("invalid", "failed_due", "stale", "fresh"))
        self.assertEqual(ordered[0], sym(511))                                               # invalid first, though outside the core
        states = [by[s] for s in ordered]
        self.assertEqual(states, sorted(states, key=fundamentals.ELIGIBLE.index))           # group order is exactly invalid, missing, failed_due, stale
        self.assertNotIn(sym(512), ordered)

    def test_oldest_first_and_alphabetical_ties_inside_the_core_and_inside_the_remainder(self):
        stocks = {sym(3): sound_record(8), sym(2): sound_record(8), sym(7): sound_record(30), sym(505): sound_record(9), sym(501): sound_record(9), sym(506): sound_record(50)}
        ordered, by = self.order(stocks)
        stale = [s for s in ordered if by[s] == "stale"]
        self.assertEqual(stale, [sym(7), sym(2), sym(3), sym(506), sym(501), sym(505)])

    def test_a_failed_core_stock_goes_behind_an_older_core_stock_of_its_group(self):
        stocks = {sym(1): sound_record(40, last_error="HTTP 500", last_attempt=ago(2), failures=1),
                  sym(2): sound_record(40, last_error="HTTP 500", last_attempt=ago(20), failures=1)}
        ordered, by = self.order(stocks)
        self.assertEqual([by[sym(1)], by[sym(2)]], ["failed_due", "failed_due"])
        self.assertEqual([s for s in ordered if by[s] == "failed_due"], [sym(2), sym(1)])      # the failure counts as a refresh on the day of the attempt

    def test_missing_cache_entries_are_missing_and_a_backed_off_failure_is_left_alone(self):
        stocks = {sym(4): sound_record(10, last_error="HTTP 500", last_attempt=ago(0), failures=3)}
        ordered, by = self.order(stocks)
        self.assertEqual(by[sym(4)], "failed_backoff")
        self.assertNotIn(sym(4), ordered)
        self.assertEqual(by[sym(0)], "missing")
        self.assertEqual(len(ordered), len(self.symbols) - 1)

    def test_a_universe_of_500_or_fewer_is_ordered_exactly_as_before(self):
        for n in (0, 1, 15, 499, 500):
            symbols = universe_symbols(n)
            inst = instruments_for(symbols)
            stocks = {}
            for i, s in enumerate(symbols):
                if i % 5 == 1:
                    stocks[s] = sound_record(8 + (i * 7) % 60)
                elif i % 5 == 2:
                    stocks[s] = sound_record(10, last_error="HTTP 500", last_attempt=ago(i % 9 + 2), failures=1)
                elif i % 5 == 3:
                    stocks[s] = {"ratios_fetched": "bad"}
                elif i % 5 == 4:
                    stocks[s] = sound_record(1)
            with self.subTest(n=n):
                self.assertEqual(fundamentals.priority(stocks, symbols, inst, DAY), old_priority(stocks, symbols, inst, DAY))

    def test_the_cohort_is_the_first_500_in_input_order_not_alphabetical_or_by_name(self):
        symbols = list(reversed(universe_symbols(520)))                      # file order is the reverse of alphabetical
        stocks = {s: sound_record(10) for s in symbols}
        ordered, _ = self.order(stocks, symbols)
        self.assertEqual(set(ordered[:500]), set(symbols[:500]))
        self.assertEqual(ordered[:500], sorted(symbols[:500]))               # all equally old: alphabetical inside the cohort
        self.assertEqual(ordered[500:], sorted(symbols[500:]))

    def test_repeated_budgeted_runs_finish_the_core_first_and_never_repeat_a_stock(self):
        stocks, seen = {}, []
        for _ in range(200):                                                 # each run refreshes at most 7 stocks
            ordered, _ = self.order(stocks)
            if not ordered:
                break
            todo = ordered[:7]
            seen += todo
            for s in todo:
                stocks[s] = sound_record(0)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(seen), set(self.symbols))
        self.assertEqual(set(seen[:500]), set(self.symbols[:500]))           # every core stock before the first outsider


class DevelopmentFallback(unittest.TestCase):
    def test_the_test_batch_never_changes_the_first_500_of_a_real_universe(self):
        real = universe_symbols(520)
        self.assertEqual(research_test_batch.extend(real, {"STOCKLENS_UNIVERSE_FILE": "/some/universe.json"}), real)

    def test_without_a_universe_file_everything_is_inside_the_core_and_the_order_is_the_old_one(self):
        symbols = research_test_batch.extend(universe.load({}), {})
        self.assertEqual(len(symbols), 15)
        self.assertEqual(symbols[:10], list(universe.TEST_SYMBOLS))           # the test batch is appended, so it can never shift the first 500
        inst = instruments_for(symbols)
        self.assertEqual(fundamentals.priority({}, symbols, inst, DAY), old_priority({}, symbols, inst, DAY))
        stocks = {s: {name: {"fetched": ago(40 + i)} for name in ("income", "balance_sheet", "cash_flow")} for i, s in enumerate(symbols)}
        old = universe.order_stalest(symbols, {s: financials.staleness_key(stocks.get(s)) for s in symbols})
        self.assertEqual(financials.order_symbols(symbols, stocks), old)


class FinancialsEdgeCases(unittest.TestCase):
    NAMES = ("income", "balance_sheet", "cash_flow")

    def rec(self, fetched, **sec):
        return {n: dict({"fetched": fetched}, **sec) for n in self.NAMES}

    def test_exact_order_core_first_then_oldest_then_alphabetical(self):
        symbols = universe_symbols(504)
        stocks = {sym(1): self.rec("2026-09-01"), sym(2): self.rec("2026-08-01"), sym(3): self.rec("2026-09-01"), sym(502): self.rec("2020-01-01"), sym(503): self.rec("2021-01-01")}
        ordered = financials.order_symbols(symbols, stocks)
        never_core = [s for s in symbols[:500] if s not in (sym(1), sym(2), sym(3))]
        self.assertEqual(ordered[:len(never_core)], never_core)               # never-fetched core stocks come first, alphabetical, as before
        self.assertEqual(ordered[len(never_core):500], [sym(2), sym(1), sym(3)])
        self.assertEqual(ordered[500:], [sym(500), sym(501), sym(502), sym(503)])    # never-fetched outsiders, then the older of the two dated ones

    def test_the_cohort_is_the_first_500_in_input_order_not_alphabetical(self):
        symbols = list(reversed(universe_symbols(520)))                      # file order is the reverse of alphabetical
        ordered = financials.order_symbols(symbols, {})
        self.assertEqual(ordered[:500], sorted(symbols[:500]))               # nothing fetched yet: alphabetical inside each cohort
        self.assertEqual(ordered[500:], sorted(symbols[500:]))

    def test_a_failed_section_counts_as_refreshed_on_the_day_of_the_attempt(self):
        symbols = universe_symbols(3)
        stocks = {sym(0): self.rec("2026-01-01"), sym(1): self.rec("2026-01-01")}
        for sec in stocks[sym(0)].values():                                   # the stock's key is its OLDEST section date, so every section must have failed
            sec.update(error="HTTP 500", attempted="2026-10-23")
        ordered = financials.order_symbols(symbols, stocks)
        self.assertEqual(ordered, [sym(2), sym(1), sym(0)])                   # sym(2) was never fetched; the recently failed stock goes last

    def test_fresh_core_stocks_cost_no_budget_so_the_cohort_order_cannot_starve_outsiders(self):
        fresh = self.rec(dt.date.today().isoformat(), schema=financials.SCHEMA_VERSION, attempted=dt.date.today().isoformat())
        for name, sec in fresh.items():
            self.assertFalse(financials.needs_refresh(sec), name)             # main() skips a stock with nothing due before it looks at the call budget

    def test_the_call_budget_and_pacing_are_unchanged(self):
        self.assertEqual(fundamentals.DELAY_SECONDS, 2.0)
        self.assertEqual(fundamentals.RETRIES, 3)
        self.assertEqual(fundamentals.RATIOS_MAX_AGE_DAYS, 7)
        self.assertEqual(fundamentals.PROFILE_MAX_AGE_DAYS, 90)
        self.assertEqual(financials.STATEMENT_MAX_AGE_DAYS, 30)
        import upstox_common
        self.assertEqual((upstox_common.DELAY_SECONDS, upstox_common.RETRIES), (2.0, 3))


if __name__ == "__main__":
    unittest.main()
