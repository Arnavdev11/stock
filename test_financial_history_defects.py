"""Regression tests for the Phase 2 audit defects (28 Oct 2026). Offline only: every provider response is a fixture.
  A. parse_income rejected every year of a company that changed its fiscal year-end once (COCKERILL, ENRIN, GILLETTE: "mixed_month_ends").
  B. coverage_report counted a fiscal year held on two bases (income consolidated, cash flow standalone: HARDWYN, IMPAL) as a duplicate.
(The third defect, run order, is pinned in test_universe_coverage_fixes.FinancialHistoryRunOrder.)
Run: python3 -m unittest test_financial_history_defects"""
import unittest

import coverage_report as cr
import financial_history_updater as fh
from test_financial_history import CF4, FakeApi, cash_body, income_body, line

TODAY = "2026-10-28"


def lines(**periods):
    """Standard detail lines whose values are distinct per period, so a kept value can be traced to its period."""
    rev = {p: v for p, v in periods.items()}
    return [line("Revenue", **rev), line("Total Revenue", **{p: v + 1 for p, v in rev.items()}),
            line("Profit Before Tax", **{p: v / 4 for p, v in rev.items()}), line("Profit After Tax", **{p: v / 5 for p, v in rev.items()})]


class YearEndChange(unittest.TestCase):
    def test_dec_to_mar_keeps_only_full_years_under_the_new_year_end(self):
        # Dec year-ends until 2023, then a 15-month transition period to Mar 2025, then the full year to Mar 2026
        r = fh.parse_income(income_body(lines(Dec_2021=100.0, Dec_2022=110.0, Dec_2023=120.0, Mar_2025=150.0, Mar_2026=160.0)))
        self.assertIsNone(r["problem"])
        self.assertEqual((sorted(r["years"]), r["month"]), ([2026], 3))
        self.assertEqual(r["years"][2026]["values"]["revenue"], 160.0)
        self.assertEqual(r["years"][2026]["period"], "Mar 2026")

    def test_mar_to_dec_with_a_shared_calendar_year_drops_the_transition(self):
        # Mar 2024 and the 9-month Dec 2024 transition share calendar year 2024; neither may be stored
        r = fh.parse_income(income_body(lines(Mar_2022=90.0, Mar_2023=95.0, Mar_2024=100.0, Dec_2024=80.0, Dec_2025=130.0)))
        self.assertIsNone(r["problem"])
        self.assertEqual((sorted(r["years"]), r["month"]), ([2025], 12))
        self.assertEqual(r["years"][2025]["values"]["revenue"], 130.0)

    def test_several_full_years_after_the_change_are_all_kept(self):
        r = fh.parse_income(income_body(lines(Dec_2020=1.0, Dec_2021=2.0, Mar_2023=3.0, Mar_2024=4.0, Mar_2025=5.0, Mar_2026=6.0)))
        self.assertEqual(sorted(r["years"]), [2024, 2025, 2026])
        self.assertEqual([r["years"][y]["values"]["revenue"] for y in (2024, 2025, 2026)], [4.0, 5.0, 6.0])

    def test_ambiguous_shapes_stay_rejected(self):
        for name, periods in {
                "quarter-like spacing": dict(Mar_2026=1.0, Dec_2025=2.0, Sep_2025=3.0),
                "three month-ends": dict(Mar_2023=1.0, Jun_2024=2.0, Dec_2025=3.0, Dec_2026=4.0),
                "interleaved": dict(Mar_2023=1.0, Dec_2023=2.0, Mar_2024=3.0, Dec_2024=4.0),
                "gap in the old run": dict(Dec_2019=1.0, Dec_2021=2.0, Mar_2023=3.0, Mar_2024=4.0),
                "gap in the new run": dict(Dec_2021=1.0, Mar_2023=3.0, Mar_2025=4.0),
                "only the transition under the new year-end": dict(Dec_2023=1.0, Dec_2024=2.0, Mar_2026=3.0)}.items():
            with self.subTest(name):
                r = fh.parse_income(income_body(lines(**periods)))
                self.assertEqual((r["years"], r["problem"]), ({}, "mixed_month_ends"))

    def test_a_single_year_end_is_unchanged(self):
        r = fh.parse_income(income_body(lines(Mar_2024=1.0, Mar_2025=2.0, Mar_2026=3.0)))
        self.assertEqual((sorted(r["years"]), r["month"], r["problem"]), ([2024, 2025, 2026], 3, None))

    def test_end_to_end_a_new_stock_gets_its_full_years_and_cash_flow_follows(self):
        inc = income_body(lines(Dec_2022=110.0, Dec_2023=120.0, Mar_2025=150.0, Mar_2026=160.0))
        cf = cash_body({"Dec_2023": 1.0, "Mar_2025": 2.0, "Mar_2026": 3.0}, None, None)
        s = fh.new_stock("NEWYE", "INE000000001", "New Year End Ltd", "Industrials")
        api = FakeApi({("income-statement", "consolidated"): (inc, None), ("cash-flow", "consolidated"): (cf, None)})
        problems = fh.process_stock(api, s, TODAY)
        self.assertEqual(problems, [])
        self.assertEqual(s["fiscal_year_end_month"], 3)
        got = {(r["fy"], r["basis"]): r for r in s["years"]}
        self.assertEqual(got[(2026, "consolidated")]["values"]["revenue"], 160.0)
        self.assertEqual(got[(2026, "consolidated")]["values"]["operating_cash_flow"], 3.0)
        self.assertNotIn(2023, {r["fy"] for r in s["years"]})                       # the Dec years are never stored beside Mar years
        self.assertIsNone(got.get((2025, "consolidated"), {"values": {"revenue": None}})["values"]["revenue"])   # transition revenue not stored

    def test_a_stock_already_stored_under_another_year_end_is_not_overwritten(self):
        s = fh.new_stock("OLDYE", "INE000000002", "Old Year End Ltd", "Industrials")
        api = FakeApi({("income-statement", "consolidated"): (income_body(lines(Dec_2021=1.0, Dec_2022=2.0, Dec_2023=3.0)), None),
                       ("cash-flow", "consolidated"): (cash_body(**CF4), None)})
        fh.process_stock(api, s, "2024-02-01")
        before = [dict(r, values=dict(r["values"])) for r in s["years"]]
        self.assertEqual(s["fiscal_year_end_month"], 12)
        api = FakeApi({("income-statement", "consolidated"): (income_body(lines(Dec_2022=2.0, Dec_2023=3.0, Mar_2025=4.0, Mar_2026=5.0)), None),
                       ("cash-flow", "consolidated"): (cash_body(**CF4), None)})
        problems = fh.process_stock(api, s, TODAY)
        self.assertTrue(any("fiscal_year_end_changed" in p for p in problems))       # refused, reported, nothing replaced
        self.assertEqual([(r["fy"], r["values"]) for r in s["years"]], [(r["fy"], r["values"]) for r in before])


def year(fy, basis, **vals):
    v = {f: None for f in cr.FH_VALUE_FIELDS}
    v.update(vals)
    return {"fy": fy, "period": "Mar %d" % fy, "period_end": "%d-03-31" % fy, "basis": basis, "values": v}


class CoverageMixedBasis(unittest.TestCase):
    def entry(self, years):
        return {"symbol": "HARDWYN", "isin": "INE000000003", "years": years}

    def test_one_fiscal_year_on_two_bases_is_valid(self):
        ys = [year(2026, "consolidated", revenue=10.0), year(2026, "standalone", operating_cash_flow=2.0), year(2025, "consolidated", revenue=9.0)]
        got, prob = cr._fh_entry("HARDWYN", self.entry(ys), "INE000000003")
        self.assertIsNone(prob)
        self.assertEqual(len(got), 3)

    def test_the_same_year_and_basis_twice_is_still_a_duplicate(self):
        ys = [year(2026, "standalone"), year(2026, "standalone")]
        self.assertEqual(cr._fh_entry("HARDWYN", self.entry(ys), "INE000000003"), (None, "duplicate_fiscal_year"))


if __name__ == "__main__":
    unittest.main()
