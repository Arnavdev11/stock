import contextlib, copy, io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import financials_updater as fu
import upstox_common as uc
import validate_outputs as vo
from mock_responses import balance, cashflow, income

TOKEN = "TOKEN-xyz-SECRET-123"
FS = lambda label, **by: {"particular": label, "history": [{"period": p.replace("_", " "), "value": v} for p, v in by.items()]}


class IncomeTests(unittest.TestCase):
    def test_every_value_comes_from_the_detail_line(self):
        r = fu.parse_income(income())
        self.assertEqual(r["period"], "Mar 2025")                      # detail lines end Mar 2025 (summary reaches Mar 2026)
        self.assertEqual((r["revenue"], r["other_income"], r["total_revenue"]), (964693.0, 17978.0, 982671.0))
        self.assertEqual((r["profit_before_tax"], r["tax"], r["profit_after_tax"]), (106017.0, 25230.0, 80787.0))
        self.assertEqual((r["eps_basic"], r["eps_diluted"]), (51.47, 51.47))
        self.assertNotIn("operating_profit", r)                         # summary category is not exposed as operating profit

    def test_profit_before_tax_ignores_a_different_summary_operating_profit(self):
        b = income(); b["data"]["income_statement"][1]["history"][1]["value"] = 100000       # Mar 2025 summary differs
        r = fu.parse_income(b)
        self.assertEqual(r["profit_before_tax"], 106017.0)
        self.assertIs(r["checks"]["summary_operating_profit_equals_pbt"], False)
        self.assertIsNone(r["profit_before_tax_growth"])                # unverified -> no growth

    def test_growth_only_when_summary_equals_detail_for_the_same_year(self):
        r = fu.parse_income(income())
        self.assertEqual((r["total_revenue_growth"], r["profit_before_tax_growth"], r["profit_after_tax_growth"]), (7.15, 1.61, 2.74))
        self.assertTrue(all(v is True for v in r["checks"].values()))
        self.assertIsNone(r.get("revenue_growth"))                      # no growth is stored for the plain Revenue line

    def test_summary_net_profit_mismatch_is_flagged_not_trusted(self):
        b = income(); b["data"]["income_statement"][2]["history"][1]["value"] = 80000
        r = fu.parse_income(b)
        self.assertIs(r["checks"]["summary_net_profit_equals_pat"], False)
        self.assertIsNone(r["profit_after_tax_growth"]); self.assertEqual(r["profit_after_tax"], 80787.0)

    def test_total_revenue_is_never_taken_as_revenue(self):
        b = income(); b["data"]["full_statement"] = [x for x in b["data"]["full_statement"] if x["particular"] != "Revenue"]
        r = fu.parse_income(b)
        self.assertIsNone(r["revenue"]); self.assertEqual(r["total_revenue"], 982671.0)

    def test_repeated_label_is_ambiguous_and_dropped(self):
        b = income(); b["data"]["full_statement"].append(FS("Profit Before Tax", Mar_2025=1))
        self.assertIsNone(fu.parse_income(b)["profit_before_tax"])

    def test_without_detail_lines_nothing_is_invented(self):
        b = income(); del b["data"]["full_statement"]
        r = fu.parse_income(b)
        self.assertEqual((r["period"], r["revenue"], r["profit_before_tax"], r["total_revenue"]), ("Mar 2026", None, None, None))
        self.assertEqual(r["net_profit"], 95610.0)

    def test_bad_values_and_quarterly(self):
        b = income(); b["data"]["full_statement"][0]["history"][0]["value"] = "NaN"
        self.assertIsNone(fu.parse_income(b)["revenue"])
        self.assertIsNone(fu.parse_income(income(time_period="quarterly")))


class BalanceTests(unittest.TestCase):
    def test_detail_lines_and_equity_capital(self):
        r = fu.parse_balance(balance())
        self.assertEqual((r["period"], r["total_assets"], r["total_liabilities"]), ("Mar 2025", 1950121.0, 940495.0))
        self.assertEqual((r["equity_capital"], r["total_equity_and_liabilities"]), (1009626.0, 1950121.0))
        self.assertEqual((r["current_assets"], r["non_current_assets"], r["current_liabilities"], r["non_current_liabilities"]),
                         (499270.0, 1450851.0, 453737.0, 486758.0))
        self.assertEqual(r["total_equity"], 1009626.0)
        self.assertIs(r["checks"]["equity_capital_equals_derived_equity"], True)
        self.assertIs(r["checks"]["total_assets_equals_detail"], True)

    def test_debt_is_never_derived(self):
        r = fu.parse_balance(balance())
        self.assertAlmostEqual(r["liabilities_to_equity"], 0.93, places=2)     # separate ratio exists...
        self.assertIsNone(r["debt_to_equity"]); self.assertIsNone(r["total_debt"])   # ...but is NOT Debt/Equity
        self.assertEqual(r["debt_lines_found"], [])

    def test_a_borrowings_line_is_listed_for_review_but_not_used(self):
        r = fu.parse_balance(balance(extra_lines=[FS("Long Term Borrowings", Mar_2025=5000)]))
        self.assertEqual(r["debt_lines_found"], ["Long Term Borrowings"])
        self.assertIsNone(r["total_debt"]); self.assertIsNone(r["debt_to_equity"])

    def test_negative_equity_has_no_ratio(self):
        r = fu.parse_balance(balance(history=[{"total_asset": 100, "total_liability": 150, "period": "Mar 2025"}]))
        self.assertEqual(r["total_equity"], -50.0); self.assertIsNone(r["liabilities_to_equity"])

    def test_mismatching_equity_capital_is_flagged(self):
        b = balance(); b["data"]["full_statement"][6]["history"][0]["value"] = 1
        self.assertIs(fu.parse_balance(b)["checks"]["equity_capital_equals_derived_equity"], False)


class CashFlowTests(unittest.TestCase):
    def fcf(self, *lines):
        return fu.parse_cashflow(cashflow(lines=lines))

    def test_line_items_are_recorded_and_no_capex_means_no_fcf(self):
        r = self.fcf()
        self.assertEqual(r["line_items"][0], "Net Cash from Operating Activities")
        self.assertEqual((r["capex_status"], r["free_cash_flow"], r["capex"]), ("none", None, None))
        self.assertEqual((r["operating"], r["investing"], r["financing"]), (150000.0, -90000.0, -20000.0))

    def test_unambiguous_total_capex_lines_give_fcf(self):
        for label in ("Purchase of Fixed Assets", "Capital Expenditure", "Purchase of Property, Plant and Equipment and Intangible Assets"):
            r = self.fcf((label, {"Mar 2026": -60000}))
            self.assertEqual((r["capex_status"], r["capex"], r["free_cash_flow"]), ("found", 60000.0, 90000.0), label)

    def test_part_lines_and_cwip_are_never_capex(self):
        for label in ("Purchase of Property, Plant and Equipment", "Purchase of Intangible Assets", "Capital Work in Progress",
                      "Purchase of Capital Work-in-Progress"):
            r = self.fcf((label, {"Mar 2026": -60000}))
            self.assertEqual((r["capex_status"], r["free_cash_flow"]), ("partial_only", None), label)
            self.assertEqual(r["capex_candidates"][0]["class"], "partial")

    def test_not_capex_lines_are_ignored(self):
        for label in ("Purchase of Investments", "Sale of Fixed Assets", "Proceeds from Sale of Property, Plant and Equipment",
                      "Net Change in Cash"):
            r = self.fcf((label, {"Mar 2026": 5}))
            self.assertEqual((r["capex_status"], r["capex_candidates"], r["free_cash_flow"]), ("none", [], None), label)

    def test_two_total_lines_or_wrong_year_give_no_fcf(self):
        r = self.fcf(("Purchase of Fixed Assets", {"Mar 2026": -1}), ("Capital Expenditure", {"Mar 2026": -2}))
        self.assertEqual((r["capex_status"], r["free_cash_flow"]), ("ambiguous", None))
        r = self.fcf(("Purchase of Fixed Assets", {"Mar 2025": -60000}))
        self.assertEqual((r["capex_status"], r["free_cash_flow"]), ("no_value_for_period", None))

    def test_annual_period_selection_without_time_period(self):
        b = cashflow()
        for c, v in zip(b["data"]["cash_flow"], (111, 222, 333)):
            c["history"] = [{"value": v + 1, "period": "Dec 2025"}, {"value": v, "period": "Mar 2026"}, {"value": v - 1, "period": "Sep 2025"}]
        self.assertIsNone(fu.parse_cashflow(b))
        r = fu.parse_cashflow(b, fy_month=3)
        self.assertEqual((r["period"], r["operating"], r["financing"]), ("Mar 2026", 111.0, 333.0))


class Resp:
    def __init__(self, code, body=None): self.status_code, self._b, self.content = code, body, b"[]"
    def json(self): return self._b
    def raise_for_status(self): pass


class FlowTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.cache, self.out = d / "cache.json", d / "out" / "financials.json"
        self.calls, self.rules, self.params = [], {}, []
        self.insts = {s: {"isin": "INE_" + s, "name": s + " LTD"} for s in uc.SYMBOLS if s != "MARUTI"}
        outer = self
        class Sess:
            def __init__(s): s.headers = {}
            def get(s, url, params=None, timeout=0):
                isin, ep = url.split("/")[-2], url.split("/")[-1]
                t = (params or {}).get("type")
                outer.calls.append((isin, ep, t)); outer.params.append((ep, dict(params or {})))
                rule = outer.rules.get((isin, ep, t)) or outer.rules.get((isin, ep))
                if rule is not None: return rule() if callable(rule) else rule
                return Resp(200, {"income-statement": income, "balance-sheet": balance, "cash-flow": lambda: cashflow(
                    lines=[("Purchase of Fixed Assets", {"Mar 2026": -60000})])}[ep]())
        self.patches = [mock.patch.object(fu, "CACHE_FILE", self.cache), mock.patch.object(fu, "OUT_FILE", self.out),
                        mock.patch.object(uc.requests, "Session", Sess), mock.patch.object(uc.time, "sleep", lambda s: None),
                        mock.patch.object(fu, "load_instruments", lambda: self.insts),
                        mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": TOKEN})]
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def run_main(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): fu.main()
        return buf.getvalue()

    def strict(self):
        return json.loads(self.out.read_text(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))

    def test_full_run_valid_json_no_token_cache_reuse_and_call_count(self):
        log = self.run_main(); doc = self.strict()
        self.assertEqual((len(doc["stocks"]), len(self.calls), doc["schema"]), (9, 27, 2))        # 9 stocks x 3 calls
        self.assertNotIn(TOKEN, log + self.out.read_text() + self.cache.read_text())
        self.assertEqual(vo.check_financials(doc), [])
        self.assertIn("CAPEX CHECK", log); self.assertIn("capex_status=found", log)
        s = doc["stocks"][0]
        self.assertEqual((s["income"]["profit_before_tax"], s["income"]["revenue"], s["income"]["total_revenue"]), (106017.0, 964693.0, 982671.0))
        self.assertEqual(s["balance_sheet"]["equity_capital"], 1009626.0); self.assertIsNone(s["balance_sheet"]["debt_to_equity"])
        self.assertEqual(s["cash_flow"]["free_cash_flow"], 90000.0)
        self.calls.clear(); self.run_main(); self.assertEqual(self.calls, [])                    # fresh cache -> 0 calls

    def test_all_three_statements_are_requested_with_fs_and_cashflow_without_time_period(self):
        self.run_main()
        by = {}
        for ep, p in self.params: by.setdefault(ep, []).append(p)
        self.assertTrue(all(p.get("fs") == "true" for ps in by.values() for p in ps))
        self.assertTrue(all("time_period" not in p for p in by["cash-flow"]))
        self.assertTrue(all(p.get("time_period") == "yearly" for p in by["income-statement"]))

    def test_old_schema_cache_is_refetched_once(self):
        today = uc.today()
        self.cache.write_text(json.dumps({"stocks": {"RELIANCE": {"isin": "INE_RELIANCE", "income": {
            "fetched": today, "basis": "consolidated", "data": {"period": "Mar 2025", "revenue": 1.0, "operating_profit": 2.0}}}}}))
        self.run_main()
        self.assertTrue(any(c[0] == "INE_RELIANCE" and c[1] == "income-statement" for c in self.calls))
        inc = self.strict()["stocks"][0]["income"]
        self.assertNotIn("operating_profit", inc); self.assertNotIn("revenue_growth", inc); self.assertIn("profit_before_tax", inc)

    def test_standalone_fallback_server_error_and_auth(self):
        self.rules[("INE_RELIANCE", "income-statement", "consolidated")] = Resp(404, {})
        self.rules[("INE_INFY", "cash-flow")] = Resp(500, {})
        self.run_main(); doc = self.strict()
        rel = next(s for s in doc["stocks"] if s["symbol"] == "RELIANCE"); infy = next(s for s in doc["stocks"] if s["symbol"] == "INFY")
        self.assertEqual((rel["income"]["basis"], rel["basis"]), ("standalone", "mixed"))
        self.assertIsNone(infy["cash_flow"]["operating"]); self.assertIsNotNone(infy["income"]["total_revenue"])
        self.assertTrue(any(e["symbol"] == "INFY" for e in doc["errors"]))
        self.out.write_text('{"old": true}'); self.cache.unlink(); self.calls.clear()
        self.rules[("INE_RELIANCE", "income-statement")] = Resp(401, {})
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()): fu.main()
        self.assertEqual(self.out.read_text(), '{"old": true}')
        with mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": ""}):
            buf = io.StringIO()
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(buf): fu.main()
            self.assertIn("UPSTOX_ANALYTICS_TOKEN is not set", buf.getvalue())


def sample_doc():
    stock = {"symbol": "TCS", "basis": "consolidated"}
    for name, parsed in (("income", fu.parse_income(income())), ("balance_sheet", fu.parse_balance(balance())),
                         ("cash_flow", fu.parse_cashflow(cashflow(lines=[("Purchase of Fixed Assets", {"Mar 2026": -60000})])))):
        stock[name] = dict(parsed, basis="consolidated")
    return {"schema": 2, "as_of": "2026-09-30", "source": "Upstox", "stocks": [stock]}


class ValidatorTests(unittest.TestCase):
    def test_good_doc_passes(self):
        self.assertEqual(vo.check_financials(sample_doc()), []); self.assertEqual(vo.financial_warnings(sample_doc()), [])

    def test_derived_debt_growth_without_check_and_fake_fcf_are_rejected(self):
        d = sample_doc(); d["stocks"][0]["balance_sheet"]["debt_to_equity"] = 0.93
        self.assertTrue(vo.check_financials(d))
        d = sample_doc(); d["stocks"][0]["income"]["checks"]["summary_operating_profit_equals_pbt"] = False
        self.assertTrue(vo.check_financials(d))                             # growth present but its check is false
        d = sample_doc(); d["stocks"][0]["cash_flow"]["capex"] = None
        self.assertTrue(vo.check_financials(d))
        d = sample_doc(); d["stocks"][0]["balance_sheet"]["total_equity"] = 5.0
        self.assertTrue(vo.check_financials(d))
        d = sample_doc(); d["stocks"][0]["income"]["revenue"] = "12"
        self.assertTrue(vo.check_financials(d))

    def test_legacy_file_is_not_set_aside_and_warnings_are_soft(self):
        self.assertEqual(vo.check_financials({"stocks": [{"symbol": "X"}], "as_of": "x", "source": "Upstox"}), [])
        d = sample_doc(); d["stocks"][0]["income"]["other_income"] = 99999.0
        self.assertTrue(vo.financial_warnings(d)); self.assertEqual(vo.check_financials(d), [])

    def test_nan_file_is_set_aside(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "out"; out.mkdir()
            (out / "financials.json").write_text('{"stocks": [NaN]}'); (out / "scans.json").write_text('{"a": NaN}')
            with mock.patch.object(vo, "OUT", out), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(vo.main(), 1)
            self.assertFalse((out / "financials.json").exists()); self.assertTrue((out / "scans.json").exists())


if __name__ == "__main__":
    unittest.main()
