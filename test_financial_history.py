"""
test_financial_history.py - Phase 4 Step 4A tests for financial_history_updater.py (the annual financial-history ledger).
Offline: mocked Upstox responses only, no network, no token.   Run: python3 test_financial_history.py
Written BEFORE the implementation; the implementation was then made to satisfy them.
"""
import copy
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("UPSTOX_ANALYTICS_TOKEN", None)
import financial_history_updater as fh            # importing must work without a token
from mock_responses import income as mock_income  # shapes follow the official docs sample

TODAY = "2026-10-04"
TOKEN = "TOKEN-xyz-SECRET-123"


# ---------------------------------------------------------------- fixture builders (Upstox response shapes)
def P(label):
    return label.replace("_", " ")


def line(label, **by_period):
    return {"particular": label, "history": [{"period": P(p), "value": v} for p, v in by_period.items()]}


def income_body(lines, summary=None, time_period="yearly", typ="consolidated"):
    d = {"type": typ, "time_period": time_period, "units_in": "crore", "full_statement": lines}
    if summary is not None:
        d["income_statement"] = [{"category": c, "history": [{"period": P(p), "value": v} for p, v in h.items()]} for c, h in summary.items()]
    return {"status": "success", "data": d}


def std_lines(years, base=1000.0):
    """Four-year standard (non-bank) detail lines. years: ['Mar_2026', ...]"""
    mk = lambda f: {y: round(base * f + i * 10, 2) for i, y in enumerate(reversed(years))}
    return [line("Revenue", **mk(1)), line("Other Income", **mk(0.1)), line("Total Revenue", **mk(1.1)), line("Total Expenses", **mk(0.8)),
            line("Profit Before Tax", **mk(0.3)), line("Tax", **mk(0.08)), line("Profit After Tax", **mk(0.22)),
            line("EPS - Basic", **mk(0.0125)), line("EPS - Diluted", **mk(0.0124))]


YEARS4 = ["Mar_2026", "Mar_2025", "Mar_2024", "Mar_2023"]


def cash_body(ocf, icf, fin, time_period="yearly", extra_lines=(), typ="consolidated"):
    cat = lambda name, h: {"category": name, "history": [{"period": P(p), "value": v} for p, v in h.items()]}
    d = {"type": typ, "time_period": time_period, "units_in": "crore",
         "cash_flow": [c for c in (cat("operating", ocf) if ocf is not None else None, cat("investing", icf) if icf is not None else None,
                                   cat("financing", fin) if fin is not None else None) if c],
         "full_statement": [dict(l) for l in extra_lines]}
    return {"status": "success", "data": d}


CF4 = dict(ocf={"Mar_2026": 520.5, "Mar_2025": 500.0, "Mar_2024": 480.25, "Mar_2023": 450.0},
           icf={"Mar_2026": -128.0, "Mar_2025": -120.0, "Mar_2024": -110.0, "Mar_2023": -100.0},
           fin={"Mar_2026": -421.0, "Mar_2025": -400.0, "Mar_2024": -390.0, "Mar_2023": -380.0})


class FakeApi:
    """Stands in for upstox_common.Upstox. responses: {(path-suffix, type): (body, err)}"""
    def __init__(self, responses, max_calls=1000):
        self.responses, self.calls, self.log, self.max_calls = responses, 0, [], max_calls

    def get(self, path, params=None):
        self.calls += 1
        self.log.append((path, dict(params or {})))
        if self.calls > self.max_calls:
            return None, "call limit for this run reached"
        key = (path.split("/", 1)[1], (params or {}).get("type"))
        return self.responses.get(key, (None, "HTTP 404"))


# ---------------------------------------------------------------- A. periods
class PeriodTests(unittest.TestCase):
    def test_period_info(self):
        self.assertEqual(fh.period_info("Mar 2026"), {"fy": 2026, "month": 3, "period_end": "2026-03-31"})
        self.assertEqual(fh.period_info("Dec 2025"), {"fy": 2025, "month": 12, "period_end": "2025-12-31"})
        self.assertEqual(fh.period_info("Feb 2024")["period_end"], "2024-02-29")      # leap year
        self.assertEqual(fh.period_info("Feb 2025")["period_end"], "2025-02-28")
        self.assertEqual(fh.period_info("Sep 2024")["period_end"], "2024-09-30")

    def test_period_info_rejects_junk(self):
        for bad in (None, "", "2026", "Mar", "Mar 26", "March-2026", "Foo 2026", "Q1 FY26", 5, "Mar 2026 extra", "Mar 0000"):
            self.assertIsNone(fh.period_info(bad), repr(bad))


# ---------------------------------------------------------------- B. annual income extraction
class IncomeTests(unittest.TestCase):
    def setUp(self):
        self.r = fh.parse_income(income_body(std_lines(YEARS4)))

    def test_four_annual_periods_extracted(self):
        self.assertIsNone(self.r["problem"])
        self.assertEqual(sorted(self.r["years"]), [2023, 2024, 2025, 2026])
        self.assertEqual(self.r["month"], 3)
        y = self.r["years"][2026]
        self.assertEqual(y["period"], "Mar 2026")
        self.assertEqual(y["values"], {"revenue": 1000.0 + 30, "total_revenue": 1100.0 + 30, "profit_before_tax": 300.0 + 30,
                                       "profit_after_tax": 220.0 + 30, "eps_basic": 12.5 + 30, "eps_diluted": 12.4 + 30})
        self.assertEqual(self.r["years"][2023]["values"]["revenue"], 1000.0)

    def test_only_detail_lines_feed_values_not_other_lines(self):
        for y in self.r["years"].values():
            self.assertNotIn("total_expenses", y["values"]); self.assertNotIn("tax", y["values"]); self.assertNotIn("other_income", y["values"])

    def test_summary_checks_verified_and_failed(self):
        summ = {"revenue": {"Mar_2026": 1130.0, "Mar_2025": 1120.0}, "net_profit": {"Mar_2026": 250.0, "Mar_2025": 999.0},
                "operating_profit": {"Mar_2026": 330.0}}
        r = fh.parse_income(income_body(std_lines(YEARS4), summary=summ))
        c26, c25, c24 = (r["years"][y]["checks"] for y in (2026, 2025, 2024))
        self.assertTrue(c26["summary_net_profit_equals_pat"]); self.assertTrue(c26["summary_revenue_equals_total_revenue"]); self.assertTrue(c26["summary_operating_profit_equals_pbt"])
        self.assertFalse(c25["summary_net_profit_equals_pat"])                    # 999 != PAT
        self.assertIsNone(c24["summary_net_profit_equals_pat"])                   # no summary for that year: could not check

    def test_not_yearly_rejected(self):
        r = fh.parse_income(income_body(std_lines(YEARS4), time_period="quarterly"))
        self.assertEqual((r["years"], r["problem"]), ({}, "not_yearly"))

    def test_mixed_month_ends_rejected(self):
        lines = [line("Revenue", Mar_2026=1.0, Dec_2025=2.0, Sep_2025=3.0), line("Profit After Tax", Mar_2026=1.0, Dec_2025=1.0)]
        r = fh.parse_income(income_body(lines))
        self.assertEqual((r["years"], r["problem"]), ({}, "mixed_month_ends"))

    def test_missing_years_stay_missing(self):
        lines = [line("Revenue", Mar_2026=5.0, Mar_2024=3.0), line("Total Revenue", Mar_2026=6.0, Mar_2024=4.0)]
        r = fh.parse_income(income_body(lines))
        self.assertEqual(sorted(r["years"]), [2024, 2026])                        # 2025 is NOT created or bridged

    def test_year_exists_only_with_a_valid_value(self):
        lines = [line("Revenue", Mar_2026=5.0, Mar_2025=None, Mar_2024="-", Mar_2023="nan")]
        r = fh.parse_income(income_body(lines))
        self.assertEqual(sorted(r["years"]), [2026])

    def test_null_never_becomes_zero(self):
        lines = [line("Revenue", Mar_2026=5.0), line("Profit After Tax", Mar_2026="--")]
        v = fh.parse_income(income_body(lines))["years"][2026]["values"]
        self.assertEqual(v["revenue"], 5.0); self.assertIsNone(v["profit_after_tax"]); self.assertIsNone(v["total_revenue"]); self.assertIsNone(v["eps_basic"])

    def test_string_numbers_parsed_and_zero_kept_as_zero(self):
        lines = [line("Revenue", Mar_2026="1,234.5"), line("Profit After Tax", Mar_2026=0)]
        v = fh.parse_income(income_body(lines))["years"][2026]["values"]
        self.assertEqual(v["revenue"], 1234.5); self.assertEqual(v["profit_after_tax"], 0.0)       # a real zero stays a zero

    def test_repeated_label_is_ambiguous_and_dropped(self):
        lines = std_lines(["Mar_2026"]) + [line("Revenue", Mar_2026=777.0)]
        v = fh.parse_income(income_body(lines))["years"][2026]["values"]
        self.assertIsNone(v["revenue"]); self.assertIsNotNone(v["total_revenue"])

    def test_duplicate_period_within_a_line_is_dropped(self):
        l = {"particular": "Revenue", "history": [{"period": "Mar 2026", "value": 1.0}, {"period": "Mar 2026", "value": 2.0}, {"period": "Mar 2025", "value": 3.0}]}
        r = fh.parse_income(income_body([l, line("Total Revenue", Mar_2026=9.0)]))
        self.assertIsNone(r["years"][2026]["values"]["revenue"]); self.assertEqual(r["years"][2026]["values"]["total_revenue"], 9.0)
        self.assertEqual(r["years"][2025]["values"]["revenue"], 3.0)

    def test_unparsable_periods_ignored(self):
        l = {"particular": "Revenue", "history": [{"period": "FY26", "value": 1.0}, {"period": "Mar 2026", "value": 2.0}, {"period": None, "value": 3.0}]}
        r = fh.parse_income(income_body([l]))
        self.assertEqual(sorted(r["years"]), [2026])

    def test_no_usable_data(self):
        for body in (income_body([]), {"status": "success", "data": {}}, {"status": "success"}, {}):
            r = fh.parse_income(body)
            self.assertEqual((r["years"], r["problem"]), ({}, "no_data"))

    def test_mock_docs_sample_shape_parses(self):
        r = fh.parse_income(mock_income())
        self.assertEqual(sorted(r["years"]), [2024, 2025])                         # the docs sample's detail lines cover Mar 2025 and Mar 2024
        self.assertEqual(r["years"][2025]["values"]["revenue"], 964693.0)
        self.assertEqual(r["years"][2025]["values"]["eps_basic"], 51.47)


# ---------------------------------------------------------------- C. banks / financial institutions
class BankTests(unittest.TestCase):
    BANK = [line("Total Revenue", Mar_2026=900.0, Mar_2025=800.0), line("Interest Earned", Mar_2026=700.0, Mar_2025=600.0),
            line("Other Income", Mar_2026=200.0, Mar_2025=200.0), line("Profit Before Tax", Mar_2026=300.0, Mar_2025=250.0),
            line("Profit After Tax", Mar_2026=220.0, Mar_2025=190.0), line("EPS - Basic", Mar_2026=11.0, Mar_2025=9.5)]

    def test_revenue_is_never_invented_for_a_bank(self):
        v = fh.parse_income(income_body(self.BANK), layout="financial")["years"][2026]["values"]
        self.assertIsNone(v["revenue"]); self.assertEqual(v["total_revenue"], 900.0)
        self.assertNotEqual(v["revenue"], v["total_revenue"])

    def test_same_rule_for_standard_layout(self):
        v = fh.parse_income(income_body(self.BANK), layout="standard")["years"][2026]["values"]
        self.assertIsNone(v["revenue"]); self.assertEqual(v["total_revenue"], 900.0)

    def test_financial_layout_preserves_other_reported_lines_exactly(self):
        y = fh.parse_income(income_body(self.BANK), layout="financial")["years"][2026]
        self.assertEqual(y["other_lines"], {"Interest Earned": 700.0, "Other Income": 200.0})
        self.assertEqual(fh.parse_income(income_body(self.BANK), layout="financial")["years"][2025]["other_lines"]["Interest Earned"], 600.0)

    def test_standard_layout_stores_no_other_lines(self):
        y = fh.parse_income(income_body(std_lines(YEARS4)), layout="standard")["years"][2026]
        self.assertEqual(y["other_lines"], {})

    def test_bank_with_a_real_revenue_line_keeps_it(self):
        lines = self.BANK + [line("Revenue", Mar_2026=650.0)]
        v = fh.parse_income(income_body(lines), layout="financial")["years"][2026]["values"]
        self.assertEqual(v["revenue"], 650.0); self.assertEqual(v["total_revenue"], 900.0)

    def test_bank_does_not_get_ordinary_company_summary_checks(self):
        summ = {"revenue": {"Mar_2026": 111.0}, "operating_profit": {"Mar_2026": 222.0}, "net_profit": {"Mar_2026": 220.0}}
        c = fh.parse_income(income_body(self.BANK, summary=summ), layout="financial")["years"][2026]["checks"]
        self.assertIsNone(c["summary_revenue_equals_total_revenue"]); self.assertIsNone(c["summary_operating_profit_equals_pbt"])
        self.assertTrue(c["summary_net_profit_equals_pat"])                                # the one check that applies to every layout
        c2 = fh.parse_income(income_body(self.BANK, summary=summ), layout="standard")["years"][2026]["checks"]
        self.assertFalse(c2["summary_revenue_equals_total_revenue"]); self.assertFalse(c2["summary_operating_profit_equals_pbt"])

    def test_bank_summary_difference_gives_no_warning_and_keeps_revenue_null(self):
        s = fh.new_stock("ICICIBANK", "INE2", "ICICI Bank", "Financial Services")
        summ = {"revenue": {"Mar_2026": 111.0}, "operating_profit": {"Mar_2026": 222.0}, "net_profit": {"Mar_2026": 220.0}}
        p = fh.parse_income(income_body(self.BANK, summary=summ), layout="financial")
        fh.upsert(s, p["years"], "income", "consolidated", "e", TODAY, TODAY)
        r = rec(s, 2026)
        self.assertEqual((r["verification"]["status"], r["verification"]["reconciliation"], r["verification"]["warnings"]), ("verified", "matched", []))
        self.assertIsNone(r["values"]["revenue"]); self.assertEqual(r["values"]["total_revenue"], 900.0)

    def test_classify_layout(self):
        for s in ("Financial Services", "Banks", "Private Sector Bank", "Insurance", "NBFC - Finance", "financial services"):
            self.assertEqual(fh.classify_layout(s), "financial", s)
        for s in ("IT Services", "Automobile", "Oil & Gas", "FMCG", "Telecom"):
            self.assertEqual(fh.classify_layout(s), "standard", s)
        for s in (None, "", "   ", 5):
            self.assertEqual(fh.classify_layout(s), "unclassified", repr(s))


# ---------------------------------------------------------------- D. cash flow
class CashFlowTests(unittest.TestCase):
    def test_annual_extraction(self):
        r = fh.parse_cashflow(cash_body(**CF4), fy_month=3)
        self.assertIsNone(r["problem"]); self.assertEqual(sorted(r["years"]), [2023, 2024, 2025, 2026])
        v = r["years"][2026]["values"]
        self.assertEqual((v["operating_cash_flow"], v["investing_cash_flow"], v["financing_cash_flow"]), (520.5, -128.0, -421.0))
        self.assertIsNone(v["capex"]); self.assertIsNone(v["free_cash_flow"])

    def test_no_reference_month_but_consistent_months_ok(self):
        r = fh.parse_cashflow(cash_body(**CF4), fy_month=None)
        self.assertEqual(sorted(r["years"]), [2023, 2024, 2025, 2026]); self.assertEqual(r["month"], 3)

    def test_quarterly_rejected(self):
        r = fh.parse_cashflow(cash_body(**CF4, time_period="quarterly"), fy_month=3)
        self.assertEqual((r["years"], r["problem"]), ({}, "quarterly"))

    def test_mixed_months_without_reference_rejected(self):
        b = cash_body({"Mar_2026": 5.0, "Dec_2025": 4.0, "Sep_2025": 3.0}, None, None)
        r = fh.parse_cashflow(b, fy_month=None)
        self.assertEqual((r["years"], r["problem"]), ({}, "mixed_month_ends"))

    def test_mixed_months_with_reference_keeps_only_the_annual_month(self):
        b = cash_body({"Mar_2026": 5.0, "Dec_2025": 4.0, "Mar_2025": 3.0, "Sep_2024": 2.0}, None, None)
        r = fh.parse_cashflow(b, fy_month=3)
        self.assertEqual(sorted(r["years"]), [2025, 2026]); self.assertIsNone(r["problem"])

    def test_missing_category_is_null_not_zero(self):
        r = fh.parse_cashflow(cash_body(CF4["ocf"], None, None), fy_month=3)
        v = r["years"][2026]["values"]
        self.assertEqual(v["operating_cash_flow"], 520.5); self.assertIsNone(v["investing_cash_flow"]); self.assertIsNone(v["financing_cash_flow"])

    def test_no_data(self):
        r = fh.parse_cashflow(cash_body(None, None, None), fy_month=3)
        self.assertEqual((r["years"], r["problem"]), ({}, "no_data"))

    def test_fcf_is_never_operating_minus_investing(self):
        r = fh.parse_cashflow(cash_body(**CF4), fy_month=3)
        for y in r["years"].values():
            self.assertIsNone(y["values"]["free_cash_flow"]); self.assertIsNone(y["values"]["capex"])

    def test_fcf_only_from_an_explicit_unambiguous_capex_line(self):
        extra = [line("Capital Expenditure", Mar_2026=-100.0, Mar_2025=-90.0)]
        r = fh.parse_cashflow(cash_body(**CF4, extra_lines=extra), fy_month=3)
        v26, v25, v24 = (r["years"][y]["values"] for y in (2026, 2025, 2024))
        self.assertEqual((v26["capex"], v26["free_cash_flow"]), (100.0, 420.5))      # 520.5 - 100
        self.assertEqual((v25["capex"], v25["free_cash_flow"]), (90.0, 410.0))
        self.assertEqual((v24["capex"], v24["free_cash_flow"]), (None, None))         # no capex value for that year -> no FCF

    def test_partial_capex_lines_never_give_fcf(self):
        extra = [line("Purchase of Property, Plant and Equipment", Mar_2026=-60.0), line("Purchase of Intangible Assets", Mar_2026=-10.0)]
        v = fh.parse_cashflow(cash_body(**CF4, extra_lines=extra), fy_month=3)["years"][2026]["values"]
        self.assertEqual((v["capex"], v["free_cash_flow"]), (None, None))

    def test_two_total_capex_lines_are_ambiguous(self):
        extra = [line("Capital Expenditure", Mar_2026=-100.0), line("Capex", Mar_2026=-105.0)]
        v = fh.parse_cashflow(cash_body(**CF4, extra_lines=extra), fy_month=3)["years"][2026]["values"]
        self.assertEqual((v["capex"], v["free_cash_flow"]), (None, None))

    def test_sale_and_investment_lines_are_not_capex(self):
        extra = [line("Proceeds from Sale of Fixed Assets", Mar_2026=40.0), line("Purchase of Investments", Mar_2026=-50.0)]
        v = fh.parse_cashflow(cash_body(**CF4, extra_lines=extra), fy_month=3)["years"][2026]["values"]
        self.assertEqual((v["capex"], v["free_cash_flow"]), (None, None))


# ---------------------------------------------------------------- E. ledger: upsert, revisions, basis separation, gaps
def stock(sym="TCS"):
    return fh.new_stock(sym, "INE000A00000", sym + " Ltd", "IT Services")


def up_income(s, years, basis="consolidated", fetched=TODAY, **kw):
    parsed = fh.parse_income(income_body(std_lines(years, **kw), typ=basis))
    return fh.upsert(s, parsed["years"], "income", basis, "income-statement?type=%s&time_period=yearly&fs=true" % basis, fetched, fetched)


def up_cash(s, basis="consolidated", fetched=TODAY, **cf):
    parsed = fh.parse_cashflow(cash_body(**(cf or CF4), typ=basis), fy_month=3)
    return fh.upsert(s, parsed["years"], "cash_flow", basis, "cash-flow?type=%s&fs=true" % basis, fetched, fetched)


def rec(s, fy, basis="consolidated"):
    r = [x for x in s["years"] if x["fy"] == fy and x["basis"] == basis]
    return r[0] if r else None


class LedgerTests(unittest.TestCase):
    def test_new_stock_shape(self):
        s = stock()
        self.assertEqual(s["years"], []); self.assertEqual(s["gaps"], {}); self.assertEqual(s["statement_layout"], "standard")
        self.assertIsNone(s["fiscal_year_end_month"])

    def test_create_records_with_full_metadata(self):
        s = stock(); stats = up_income(s, YEARS4)
        self.assertEqual(stats["created"], 4)
        self.assertEqual([r["fy"] for r in s["years"]], [2026, 2025, 2024, 2023])          # newest first, a plain years array
        r = rec(s, 2026)
        self.assertEqual((r["period"], r["period_end"], r["basis"]), ("Mar 2026", "2026-03-31", "consolidated"))
        self.assertEqual(r["source"]["provider"], "Upstox")
        self.assertEqual(r["source"]["income"], {"endpoint": "income-statement?type=consolidated&time_period=yearly&fs=true", "fetched": TODAY})
        self.assertIsNone(r["source"]["cash_flow"])
        self.assertEqual((r["first_seen"], r["last_confirmed"], r["revisions"]), (TODAY, TODAY, []))
        self.assertEqual(r["verification"]["status"], "verified")
        self.assertEqual(set(r["values"]), set(fh.VALUE_FIELDS))
        self.assertEqual(s["fiscal_year_end_month"], 3)

    def test_missing_list_names_null_core_fields(self):
        s = stock(); up_income(s, ["Mar_2026"])
        self.assertEqual(sorted(rec(s, 2026)["missing"]), ["financing_cash_flow", "investing_cash_flow", "operating_cash_flow"])
        up_cash(s)
        self.assertEqual(rec(s, 2026)["missing"], [])

    def test_income_and_cash_flow_merge_into_one_record(self):
        s = stock(); up_income(s, YEARS4); up_cash(s)
        self.assertEqual(len(s["years"]), 4)
        r = rec(s, 2026)
        self.assertEqual(r["values"]["operating_cash_flow"], 520.5); self.assertEqual(r["values"]["revenue"], 1030.0)
        self.assertIsNotNone(r["source"]["income"]); self.assertEqual(r["source"]["cash_flow"]["endpoint"], "cash-flow?type=consolidated&fs=true")

    def test_identical_refetch_makes_no_revision_and_updates_last_confirmed(self):
        s = stock(); up_income(s, YEARS4, fetched="2026-09-01")
        before = copy.deepcopy(rec(s, 2025)["values"])
        stats = up_income(s, YEARS4, fetched="2026-10-04")
        self.assertEqual((stats["revised"], stats["created"]), (0, 0))
        r = rec(s, 2025)
        self.assertEqual(r["values"], before); self.assertEqual(r["revisions"], [])
        self.assertEqual((r["first_seen"], r["last_confirmed"]), ("2026-09-01", "2026-10-04"))

    def test_changed_value_is_a_revision_old_value_preserved(self):
        s = stock(); up_income(s, YEARS4, fetched="2026-05-01")
        old = rec(s, 2025)["values"]["profit_after_tax"]
        stats = up_income(s, YEARS4, fetched="2026-10-04", base=1100.0)                  # every number changes
        self.assertGreater(stats["revised"], 0)
        r = rec(s, 2025)
        self.assertNotEqual(r["values"]["profit_after_tax"], old)                        # active record = latest
        self.assertEqual(len(r["revisions"]), 1)
        rv = r["revisions"][0]
        self.assertEqual(rv["superseded_on"], "2026-10-04")
        self.assertEqual(rv["values"]["profit_after_tax"], old)                          # the previous value survives
        self.assertEqual(rv["source_fetched"], "2026-05-01")
        self.assertEqual(r["first_seen"], "2026-05-01")

    def test_revision_holds_only_changed_fields(self):
        s = stock(); up_income(s, ["Mar_2026"], fetched="2026-05-01")
        lines = std_lines(["Mar_2026"]); lines[6] = line("Profit After Tax", Mar_2026=999.0)       # only PAT changes
        parsed = fh.parse_income(income_body(lines))
        fh.upsert(s, parsed["years"], "income", "consolidated", "e", "2026-10-04", "2026-10-04")
        self.assertEqual(list(rec(s, 2026)["revisions"][0]["values"]), ["profit_after_tax"])
        self.assertEqual(rec(s, 2026)["values"]["profit_after_tax"], 999.0)

    def test_two_revisions_are_both_kept_in_order(self):
        s = stock(); up_income(s, ["Mar_2026"], fetched="2026-01-01")
        up_income(s, ["Mar_2026"], fetched="2026-04-01", base=1100.0)
        up_income(s, ["Mar_2026"], fetched="2026-07-01", base=1200.0)
        revs = rec(s, 2026)["revisions"]
        self.assertEqual([r["superseded_on"] for r in revs], ["2026-04-01", "2026-07-01"])
        self.assertEqual(revs[0]["values"]["revenue"], 1000.0); self.assertEqual(revs[1]["values"]["revenue"], 1100.0)

    def test_incoming_null_never_erases_an_existing_value(self):
        s = stock(); up_income(s, ["Mar_2026"])
        parsed = fh.parse_income(income_body([line("Revenue", Mar_2026=1000.0)]))        # source now omits PAT etc.
        fh.upsert(s, parsed["years"], "income", "consolidated", "e", "2026-10-05", "2026-10-05")
        r = rec(s, 2026)
        self.assertEqual(r["values"]["profit_after_tax"], 220.0); self.assertEqual(r["revisions"], [])

    def test_filling_a_null_is_not_a_revision(self):
        s = stock()
        fh.upsert(s, fh.parse_income(income_body([line("Revenue", Mar_2026=1000.0)]))["years"], "income", "consolidated", "e", TODAY, TODAY)
        up_income(s, ["Mar_2026"])
        r = rec(s, 2026)
        self.assertEqual(r["revisions"], []); self.assertEqual(r["values"]["profit_after_tax"], 220.0)

    def test_income_refresh_never_touches_cash_flow_fields(self):
        s = stock(); up_income(s, YEARS4); up_cash(s)
        up_income(s, YEARS4, fetched="2026-11-01", base=1200.0)
        r = rec(s, 2026)
        self.assertEqual(r["values"]["operating_cash_flow"], 520.5); self.assertEqual(r["source"]["cash_flow"]["fetched"], TODAY)

    def test_history_is_preserved_when_the_source_window_moves(self):
        s = stock(); up_income(s, YEARS4)                                                # FY2023..FY2026
        up_income(s, ["Mar_2027", "Mar_2026", "Mar_2025", "Mar_2024"], fetched="2027-06-01")   # source now returns FY2024..FY2027
        self.assertEqual([r["fy"] for r in s["years"]], [2027, 2026, 2025, 2024, 2023])  # FY2023 is kept
        self.assertEqual(rec(s, 2023)["last_confirmed"], TODAY)

    def test_records_are_never_deleted_when_a_later_response_is_empty(self):
        s = stock(); up_income(s, YEARS4)
        fh.upsert(s, {}, "income", "consolidated", "e", "2026-11-01", "2026-11-01")
        self.assertEqual(len(s["years"]), 4)

    def test_basis_separation_no_leakage(self):
        s = stock()
        up_income(s, YEARS4, basis="consolidated", base=1000.0)
        up_income(s, YEARS4, basis="standalone", base=500.0)
        c, a = rec(s, 2026, "consolidated"), rec(s, 2026, "standalone")
        self.assertEqual((c["values"]["revenue"], a["values"]["revenue"]), (1030.0, 530.0))
        self.assertEqual(len(s["years"]), 8)
        up_cash(s, basis="standalone", ocf={"Mar_2026": 77.0}, icf={"Mar_2026": -7.0}, fin={"Mar_2026": -70.0})
        self.assertEqual(rec(s, 2026, "standalone")["values"]["operating_cash_flow"], 77.0)
        self.assertIsNone(rec(s, 2026, "consolidated")["values"]["operating_cash_flow"])      # standalone cash flow did not leak
        self.assertEqual(rec(s, 2026, "consolidated")["missing"][:1], ["operating_cash_flow"] if "operating_cash_flow" in rec(s, 2026, "consolidated")["missing"] else [])

    def test_revision_in_one_basis_does_not_touch_the_other(self):
        s = stock(); up_income(s, YEARS4, basis="consolidated"); up_income(s, YEARS4, basis="standalone", base=500.0)
        up_income(s, YEARS4, basis="consolidated", base=1300.0, fetched="2026-11-01")
        self.assertEqual(rec(s, 2026, "standalone")["revisions"], []); self.assertEqual(rec(s, 2026, "standalone")["values"]["revenue"], 530.0)

    def test_years_order_newest_first_consolidated_before_standalone(self):
        s = stock(); up_income(s, ["Mar_2025"], basis="standalone"); up_income(s, ["Mar_2026", "Mar_2025"], basis="consolidated")
        self.assertEqual([(r["fy"], r["basis"]) for r in s["years"]], [(2026, "consolidated"), (2025, "consolidated"), (2025, "standalone")])

    def test_gaps_are_reported_never_bridged(self):
        s = stock()
        fh.upsert(s, fh.parse_income(income_body([line("Revenue", Mar_2026=5.0, Mar_2024=3.0, Mar_2022=1.0)]))["years"], "income", "consolidated", "e", TODAY, TODAY)
        self.assertEqual([r["fy"] for r in s["years"]], [2026, 2024, 2022])
        self.assertEqual(s["gaps"], {"consolidated": [2025, 2023]})
        self.assertIsNone(rec(s, 2025))

    def test_no_gaps_when_complete(self):
        s = stock(); up_income(s, YEARS4)
        self.assertEqual(s["gaps"], {})

    def test_reconciliation_mismatch_is_a_warning_not_an_invalid_record(self):
        s = stock()
        parsed = fh.parse_income(income_body(std_lines(["Mar_2026"]), summary={"net_profit": {"Mar_2026": 1.0}}))
        before = copy.deepcopy(parsed["years"][2026]["values"])
        fh.upsert(s, parsed["years"], "income", "consolidated", "e", TODAY, TODAY)
        v = rec(s, 2026)["verification"]
        self.assertEqual(v["status"], "verified")                                          # required detail was extracted
        self.assertEqual(v["reconciliation"], "mismatch"); self.assertEqual(v["warnings"], ["summary_net_profit_equals_pat"])
        self.assertFalse(v["checks"]["summary_net_profit_equals_pat"])
        self.assertEqual({k: x for k, x in rec(s, 2026)["values"].items() if k in before and before[k] is not None}, {k: x for k, x in before.items() if x is not None})
        self.assertEqual(fh.validate_doc(fh.build_output({"stocks": {"TCS": s}}, [], [], TODAY)), [])                                   # and the document is still valid

    def test_main_normalises_the_saved_ledger_before_processing(self):
        import inspect
        body = inspect.getsource(fh.main)
        self.assertIn("normalise_ledger(ledger)", body)
        self.assertLess(body.index("normalise_ledger(ledger)"), body.index("process_stock"))

    def test_reconciliation_states(self):
        self.assertEqual(fh._reconcile({"a": True, "b": None}), ("matched", []))
        self.assertEqual(fh._reconcile({"a": None, "b": None}), ("not_checked", []))
        self.assertEqual(fh._reconcile({"a": True, "b": False}), ("mismatch", ["b"]))

    def test_status_depends_on_required_detail_not_on_checks(self):
        self.assertEqual(fh._status({f: None for f in fh.VALUE_FIELDS}), "incomplete")      # genuinely nothing extracted
        only_optional = {f: None for f in fh.VALUE_FIELDS}; only_optional["eps_diluted"] = 1.0
        self.assertEqual(fh._status(only_optional), "incomplete")                          # optional fields alone are not required detail
        one_core = {f: None for f in fh.VALUE_FIELDS}; one_core["profit_after_tax"] = 1.0
        self.assertEqual(fh._status(one_core), "verified")

    def test_incomplete_record_is_still_valid_and_flagged(self):
        s = stock(); up_income(s, ["Mar_2026"])
        r = rec(s, 2026)
        for f in fh.VALUE_FIELDS:
            r["values"][f] = None
        r["verification"]["status"] = "incomplete"; r["missing"] = fh.missing_of(r["values"])
        self.assertEqual(fh._status(r["values"]), "incomplete")

    def test_old_check_failed_ledger_is_normalised_without_touching_values(self):
        s = stock(); up_income(s, ["Mar_2026"])
        r = rec(s, 2026); r["verification"]["status"] = "check_failed"; r["verification"]["checks"]["summary_net_profit_equals_pat"] = False
        r["verification"].pop("reconciliation"); r["verification"].pop("warnings")
        before = copy.deepcopy((r["values"], r["revisions"], r.get("other_lines")))
        fh.normalise_ledger({"stocks": {"TCS": s}})
        self.assertEqual(r["verification"]["status"], "verified"); self.assertEqual(r["verification"]["reconciliation"], "mismatch")
        self.assertEqual((r["values"], r["revisions"], r.get("other_lines")), before)

    def test_financial_layout_other_lines_stored_in_the_record(self):
        s = fh.new_stock("HDFCBANK", "INE1", "HDFC Bank", "Financial Services")
        self.assertEqual(s["statement_layout"], "financial")
        parsed = fh.parse_income(income_body(BankTests.BANK), layout="financial")
        fh.upsert(s, parsed["years"], "income", "consolidated", "e", TODAY, TODAY)
        r = rec(s, 2026)
        self.assertIsNone(r["values"]["revenue"]); self.assertEqual(r["values"]["total_revenue"], 900.0)
        self.assertEqual(r["other_lines"]["income"]["Interest Earned"], 700.0)

    def test_fiscal_year_end_month_conflict_is_refused(self):
        s = stock(); up_income(s, YEARS4)
        parsed = fh.parse_income(income_body([line("Revenue", Dec_2026=5.0)]))
        stats = fh.upsert(s, parsed["years"], "income", "consolidated", "e", TODAY, TODAY, month=parsed["month"])
        self.assertEqual(stats["refused"], "fiscal_year_end_changed"); self.assertEqual(len(s["years"]), 4)


# ---------------------------------------------------------------- F. fetching one stock (mocked API)
RESP_OK = {("income-statement", "consolidated"): (income_body(std_lines(YEARS4)), None),
           ("cash-flow", "consolidated"): (cash_body(**CF4), None)}


class ProcessTests(unittest.TestCase):
    def run_one(self, responses, s=None, today=TODAY, max_calls=1000):
        s = s or stock(); api = FakeApi(responses, max_calls)
        problems = fh.process_stock(api, s, today)
        return s, api, problems

    def test_four_years_end_to_end(self):
        s, api, problems = self.run_one(RESP_OK)
        self.assertEqual(problems, []); self.assertEqual(api.calls, 2)
        self.assertEqual([r["fy"] for r in s["years"]], [2026, 2025, 2024, 2023])
        self.assertTrue(all(r["basis"] == "consolidated" for r in s["years"]))
        self.assertEqual(rec(s, 2024)["values"]["operating_cash_flow"], 480.25)
        paths = [p for p, _ in api.log]
        self.assertEqual(paths, ["INE000A00000/income-statement", "INE000A00000/cash-flow"])
        self.assertEqual(api.log[0][1], {"type": "consolidated", "time_period": "yearly", "fs": "true"})
        self.assertEqual(api.log[1][1], {"type": "consolidated", "fs": "true"})                # no time_period for cash flow, as in production

    def test_exactly_four_years_stored_honestly_no_fifth(self):
        s, _, _ = self.run_one(RESP_OK)
        self.assertEqual(len({r["fy"] for r in s["years"]}), 4)

    def test_fallback_to_standalone_only_when_consolidated_has_no_data(self):
        resp = {("income-statement", "consolidated"): (None, "HTTP 404"), ("income-statement", "standalone"): (income_body(std_lines(YEARS4), typ="standalone"), None),
                ("cash-flow", "consolidated"): (None, "HTTP 404"), ("cash-flow", "standalone"): (cash_body(**CF4, typ="standalone"), None)}
        s, api, problems = self.run_one(resp)
        self.assertEqual(problems, []); self.assertTrue(all(r["basis"] == "standalone" for r in s["years"]))
        self.assertEqual(api.calls, 4)

    def test_no_standalone_call_when_consolidated_works(self):
        _, api, _ = self.run_one(RESP_OK)
        self.assertFalse(any(p["type"] == "standalone" for _, p in api.log))

    def test_network_error_does_not_switch_basis_and_keeps_ledger(self):
        s, _, _ = self.run_one(RESP_OK)
        before = copy.deepcopy(s["years"])
        resp = {("income-statement", "consolidated"): (None, "network error: ConnectionError"), ("cash-flow", "consolidated"): (None, "HTTP 500")}
        s2, api, problems = self.run_one(resp, s=s, today="2026-12-01")
        self.assertEqual(s2["years"], before)
        self.assertEqual(len(problems), 2); self.assertFalse(any(p["type"] == "standalone" for _, p in api.log))
        self.assertEqual(s2["fetch_state"]["income"]["error"], "network error: ConnectionError")
        self.assertEqual(s2["fetch_state"]["income"]["fetched"], TODAY)                            # the earlier success date is kept

    def test_quarterly_response_is_rejected_and_not_stored(self):
        resp = {("income-statement", "consolidated"): (income_body(std_lines(YEARS4), time_period="quarterly"), None),
                ("cash-flow", "consolidated"): (cash_body(**CF4, time_period="quarterly"), None)}
        s, api, problems = self.run_one(resp)
        self.assertEqual(s["years"], [])
        self.assertTrue(any("quarterly" in p or "not_yearly" in p for p in problems))

    def test_mixed_period_income_is_rejected(self):
        bad = income_body([line("Revenue", Mar_2026=1.0, Dec_2025=1.0, Sep_2025=1.0)])
        s, _, problems = self.run_one({("income-statement", "consolidated"): (bad, None), ("cash-flow", "consolidated"): (cash_body(**CF4), None)})
        self.assertFalse([r for r in s["years"] if r["values"]["revenue"] is not None])
        self.assertTrue(any("mixed_month_ends" in p for p in problems))

    def test_cash_flow_uses_the_income_fiscal_year_month_as_reference(self):
        cf = cash_body({"Mar_2026": 5.0, "Dec_2025": 4.0, "Mar_2025": 3.0}, None, None)
        s, _, _ = self.run_one({("income-statement", "consolidated"): (income_body(std_lines(YEARS4)), None), ("cash-flow", "consolidated"): (cf, None)})
        self.assertEqual(rec(s, 2026)["values"]["operating_cash_flow"], 5.0); self.assertEqual(rec(s, 2025)["values"]["operating_cash_flow"], 3.0)

    def test_fresh_data_is_not_refetched(self):
        s, _, _ = self.run_one(RESP_OK)
        _, api, problems = self.run_one(RESP_OK, s=s, today="2026-10-20")
        self.assertEqual((api.calls, problems), (0, []))

    def test_stale_data_is_refetched(self):
        s, _, _ = self.run_one(RESP_OK)
        _, api, _ = self.run_one(RESP_OK, s=s, today="2026-11-10")
        self.assertEqual(api.calls, 2)

    def test_call_limit_is_reported_not_crashed(self):
        s, api, problems = self.run_one(RESP_OK, max_calls=1)
        self.assertTrue(any("call limit" in p for p in problems))
        self.assertEqual(len(rec(s, 2026) or {}) > 0, True)                                     # income (call 1) was stored

    def test_stale_helper(self):
        self.assertTrue(fh.is_fresh({"fetched": "2026-10-01"}, "2026-10-04") and not fh.is_fresh({"fetched": "2026-09-01"}, "2026-10-04"))
        self.assertFalse(fh.is_fresh({}, TODAY)); self.assertFalse(fh.is_fresh(None, TODAY)); self.assertFalse(fh.is_fresh({"fetched": "junk"}, TODAY))


# ---------------------------------------------------------------- G. latest-year consistency against financials.json (warnings only)
def fin_row(sym="TCS", period="Mar 2026", basis="consolidated", **over):
    inc = {"period": period, "basis": basis, "revenue": 1030.0, "total_revenue": 1130.0, "profit_before_tax": 330.0, "profit_after_tax": 250.0,
           "eps_basic": 42.5, "eps_diluted": 42.4}
    cf = {"period": period, "basis": basis, "operating": 520.5, "investing": -128.0, "financing": -421.0, "capex": None, "free_cash_flow": None}
    inc.update(over.get("income", {})); cf.update(over.get("cash_flow", {}))
    return {"symbol": sym, "basis": basis, "income": inc, "cash_flow": cf}


def ledger_with_data():
    s = stock(); up_income(s, YEARS4); up_cash(s)
    return {"TCS": s}


class ConsistencyTests(unittest.TestCase):
    def test_equal_data_gives_no_warning(self):
        self.assertEqual(fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row()]}), [])

    def test_difference_is_a_warning_with_both_values(self):
        w = fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row(income={"profit_after_tax": 251.0})]})
        self.assertEqual(len(w), 1)
        self.assertEqual({k: w[0][k] for k in ("symbol", "type", "period", "basis", "field", "ledger", "financials")},
                         {"symbol": "TCS", "type": "value_difference", "period": "Mar 2026", "basis": "consolidated", "field": "profit_after_tax", "ledger": 250.0, "financials": 251.0})

    def test_cash_flow_difference_uses_ledger_field_names(self):
        w = fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row(cash_flow={"operating": 1.0})]})
        self.assertEqual([(x["field"], x["ledger"], x["financials"]) for x in w], [("operating_cash_flow", 520.5, 1.0)])

    def test_rounding_tolerance(self):
        self.assertEqual(fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row(income={"profit_after_tax": 250.005})]}), [])

    def test_different_basis_is_not_compared_value_by_value(self):
        w = fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row(basis="standalone")]})
        self.assertEqual({x["type"] for x in w}, {"no_common_period"}); self.assertTrue(all(x["basis"] == "standalone" for x in w))

    def test_period_not_in_ledger_is_reported(self):
        w = fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row(period="Mar 2027")]})
        self.assertTrue(w and all(x["type"] == "no_common_period" for x in w))

    def test_older_common_period_is_used_not_the_latest_ledger_year(self):
        # financials.json still shows Mar 2025: compare against the ledger's Mar 2025 record
        s = ledger_with_data()
        r25 = rec(s["TCS"], 2025)["values"]
        row = fin_row(period="Mar 2025", income={"revenue": r25["revenue"], "total_revenue": r25["total_revenue"], "profit_before_tax": r25["profit_before_tax"],
                                                "profit_after_tax": r25["profit_after_tax"], "eps_basic": r25["eps_basic"], "eps_diluted": r25["eps_diluted"]},
                      cash_flow={"operating": 500.0, "investing": -120.0, "financing": -400.0})
        self.assertEqual(fh.compare_with_financials(s, {"stocks": [row]}), [])

    def test_null_on_either_side_is_not_a_difference(self):
        w = fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row(income={"eps_diluted": None, "revenue": None})]})
        self.assertEqual(w, [])

    def test_missing_financials_file_is_one_warning(self):
        for doc in (None, {}, {"stocks": "x"}):
            w = fh.compare_with_financials(ledger_with_data(), doc)
            self.assertEqual([x["type"] for x in w], ["financials_unavailable"])

    def test_stock_absent_from_financials_is_skipped_quietly(self):
        self.assertEqual(fh.compare_with_financials(ledger_with_data(), {"stocks": [fin_row(sym="INFY")]}), [])

    def test_dict_keyed_financials_also_accepted(self):
        self.assertEqual(fh.compare_with_financials(ledger_with_data(), {"stocks": {"TCS": fin_row()}}), [])

    def test_neither_dataset_is_modified(self):
        led, fin = ledger_with_data(), {"stocks": [fin_row(income={"profit_after_tax": 999.0})]}
        l0, f0 = copy.deepcopy(led), copy.deepcopy(fin)
        fh.compare_with_financials(led, fin)
        self.assertEqual((led, fin), (l0, f0))


# ---------------------------------------------------------------- H. schema validation and output
def valid_doc():
    s = stock(); up_income(s, YEARS4); up_cash(s)
    return fh.build_output({"stocks": {"TCS": s}}, [], [], TODAY)


class SchemaTests(unittest.TestCase):
    def mutate(self, fn):
        d = copy.deepcopy(valid_doc()); fn(d)
        return fh.validate_doc(d)

    def test_valid_document_passes(self):
        self.assertEqual(fh.validate_doc(valid_doc()), [])

    def test_output_shape(self):
        d = valid_doc()
        self.assertEqual(d["schema"], fh.SCHEMA_VERSION); self.assertEqual(d["as_of"], TODAY); self.assertIn("Upstox", d["source"]); self.assertEqual(d["interval"], "annual")
        self.assertIn("TCS", d["stocks"]); self.assertNotIn("fetch_state", d["stocks"]["TCS"])
        self.assertIsInstance(d["stocks"]["TCS"]["years"], list); self.assertIn("notes", d)
        json.dumps(d, allow_nan=False)

    def test_schema_example_is_serialisable_and_has_no_token(self):
        self.assertNotIn(TOKEN, json.dumps(valid_doc()))

    def test_detects_each_violation(self):
        r = lambda d: d["stocks"]["TCS"]["years"][0]
        cases = {
            "schema version": lambda d: d.update(schema=99),
            "stocks not dict": lambda d: d.update(stocks=[]),
            "symbol mismatch": lambda d: d["stocks"]["TCS"].update(symbol="X"),
            "bad layout": lambda d: d["stocks"]["TCS"].update(statement_layout="bank"),
            "bad fy month": lambda d: d["stocks"]["TCS"].update(fiscal_year_end_month=13),
            "years not list": lambda d: d["stocks"]["TCS"].update(years={}),
            "fy not int": lambda d: r(d).update(fy="2026"),
            "fy bool": lambda d: r(d).update(fy=True),
            "fy/period mismatch": lambda d: r(d).update(fy=2020),
            "period_end wrong": lambda d: r(d).update(period_end="2026-03-30"),
            "bad basis": lambda d: r(d).update(basis="mixed"),
            "value is string": lambda d: r(d)["values"].update(revenue="5"),
            "value is bool": lambda d: r(d)["values"].update(revenue=True),
            "value is nan": lambda d: r(d)["values"].update(revenue=float("nan")),
            "value is inf": lambda d: r(d)["values"].update(revenue=float("inf")),
            "missing value key": lambda d: r(d)["values"].pop("revenue"),
            "extra value key": lambda d: r(d)["values"].update(ebitda=1.0),
            "all null record": lambda d: r(d).update(values={k: None for k in fh.VALUE_FIELDS}),
            "bad verification": lambda d: r(d)["verification"].update(status="great"),
            "bad provider": lambda d: r(d)["source"].update(provider="Other"),
            "bad date": lambda d: r(d).update(first_seen="yesterday"),
            "revision shape": lambda d: r(d).update(revisions=[{"superseded_on": TODAY, "values": {"ebitda": 1}}]),
            "revision not list": lambda d: r(d).update(revisions="x"),
            "duplicate year": lambda d: d["stocks"]["TCS"]["years"].append(copy.deepcopy(r(d))),
            "years unsorted": lambda d: d["stocks"]["TCS"]["years"].reverse(),
            "gaps stale": lambda d: d["stocks"]["TCS"].update(gaps={"consolidated": [2020]}),
            "fcf without capex": lambda d: r(d)["values"].update(free_cash_flow=1.0, capex=None),
            "fcf is ocf minus icf": lambda d: r(d)["values"].update(free_cash_flow=r(d)["values"]["operating_cash_flow"] - r(d)["values"]["investing_cash_flow"], capex=5.0),
            "missing list wrong": lambda d: r(d).update(missing=["revenue"]),
        }
        for name, fn in cases.items():
            self.assertTrue(self.mutate(fn), "validator missed: " + name)

    def test_valid_fcf_with_capex_passes(self):
        s = stock(); up_income(s, YEARS4)
        parsed = fh.parse_cashflow(cash_body(**CF4, extra_lines=[line("Capital Expenditure", Mar_2026=-100.0)]), fy_month=3)
        fh.upsert(s, parsed["years"], "cash_flow", "consolidated", "e", TODAY, TODAY)
        self.assertEqual(rec(s, 2026)["values"]["free_cash_flow"], 420.5)
        self.assertEqual(fh.validate_doc(fh.build_output({"stocks": {"TCS": s}}, [], [], TODAY)), [])

    def test_warnings_and_errors_are_carried(self):
        d = fh.build_output({"stocks": {"TCS": stock()}}, [{"symbol": "X", "error": "e"}], [{"type": "value_difference"}], TODAY)
        self.assertEqual((d["errors"], d["warnings"]), ([{"symbol": "X", "error": "e"}], [{"type": "value_difference"}]))

    def test_stock_without_years_is_not_published(self):
        d = fh.build_output({"stocks": {"TCS": stock()}}, [], [], TODAY)
        self.assertEqual(d["stocks"], {})

    def test_main_needs_the_token(self):
        os.environ.pop("UPSTOX_ANALYTICS_TOKEN", None)
        with self.assertRaises(SystemExit):
            fh.main()

    def test_source_never_prints_a_value_or_the_token(self):
        src = (HERE / "financial_history_updater.py").read_text()
        for bad in ("print(token", "log(token", "Bearer", "UPSTOX_ANALYTICS_TOKEN\""):
            self.assertNotIn(bad, src.replace('os.environ.get("UPSTOX_ANALYTICS_TOKEN"', ""), bad)

    def test_protected_files_do_not_import_the_new_module(self):
        for f in ("financials_updater.py", "fundamentals_updater.py", "historical_updater.py", "nse_updater.py", "validate_outputs.py", "upstox_common.py"):
            self.assertNotIn("financial_history", (HERE / f).read_text(), f)

    def test_new_workflow_is_manual_and_does_not_publish(self):
        wf = (HERE / ".github/workflows/financial_history.yml").read_text()
        self.assertIn("workflow_dispatch", wf); self.assertNotIn("push:", wf); self.assertNotIn("schedule", wf)
        self.assertNotIn("deploy-pages", wf); self.assertNotIn("upload-pages-artifact", wf); self.assertIn("contents: read", wf)
        self.assertIn("python financial_history_updater.py", wf)


if __name__ == "__main__":
    unittest.main()
