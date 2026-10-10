import contextlib, io, json, os, sys, tempfile, unittest
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


class ParserTests(unittest.TestCase):
    def test_income_picks_latest_year_and_upstox_growth(self):
        r = fu.parse_income(income())
        self.assertEqual((r["period"], r["revenue"], r["revenue_growth"]), ("Mar 2026", 1086181.0, 10.53))
        self.assertEqual(r["net_profit"], -500.5)
        self.assertEqual(r["net_profit_growth"], -105.2)

    def test_income_bad_values_become_null(self):
        b = income(); b["data"]["income_statement"][0]["history"][1].update(value="NaN", change="-")
        r = fu.parse_income(b)
        self.assertIsNone(r["revenue"]); self.assertIsNone(r["revenue_growth"])

    def test_income_never_mixes_years(self):
        b = income(); b["data"]["income_statement"][1]["history"] = [{"value": 1, "period": "Mar 2025"}]
        r = fu.parse_income(b)
        self.assertIsNone(r["operating_profit"]); self.assertEqual(r["revenue"], 1086181.0)

    def test_quarterly_and_empty_rejected(self):
        self.assertIsNone(fu.parse_income(income(time_period="quarterly")))
        self.assertIsNone(fu.parse_income({"data": {}}))

    def test_balance_equity_and_ratio(self):
        r = fu.parse_balance(balance())
        self.assertEqual((r["period"], r["total_equity"]), ("Mar 2025", 1009626.0))
        self.assertAlmostEqual(r["liabilities_to_equity"], 0.93, places=2)
        self.assertIsNone(r["total_debt"]); self.assertIsNone(r["debt_to_equity"])

    def test_balance_negative_equity_has_no_ratio(self):
        r = fu.parse_balance(balance(history=[{"total_asset": 100, "total_liability": 150, "period": "Mar 2025"}]))
        self.assertEqual(r["total_equity"], -50.0); self.assertIsNone(r["liabilities_to_equity"])

    def test_cashflow_without_capex_has_no_fcf(self):
        r = fu.parse_cashflow(cashflow())
        self.assertEqual((r["operating"], r["investing"], r["financing"]), (150000.0, -90000.0, -20000.0))
        self.assertIsNone(r["free_cash_flow"])

    def test_cashflow_never_uses_operating_plus_investing(self):
        r = fu.parse_cashflow(cashflow(capex_lines=[("Net Change in Cash", "Mar 2026", 40000)]))
        self.assertIsNone(r["free_cash_flow"])

    def test_cashflow_picks_annual_period_from_mixed_history(self):
        b = cashflow()
        for c, vals in zip(b["data"]["cash_flow"], (111, 222, 333)):
            c["history"] = [{"value": vals + 1, "period": "Dec 2025"}, {"value": vals, "period": "Mar 2026"},
                            {"value": vals - 1, "period": "Sep 2025"}]
        self.assertIsNone(fu.parse_cashflow(b))                        # mixed months, no reference -> rejected
        r = fu.parse_cashflow(b, fy_month=3)
        self.assertEqual((r["period"], r["operating"], r["financing"]), ("Mar 2026", 111.0, 333.0))
        self.assertIsNone(fu.parse_cashflow(cashflow(time_period="quarterly")))

    def test_cashflow_single_capex_line(self):
        r = fu.parse_cashflow(cashflow(capex_lines=[("Purchase of Fixed Assets", "Mar 2026", -60000)]))
        self.assertEqual((r["capex"], r["free_cash_flow"]), (60000.0, 90000.0))

    def test_cashflow_ambiguous_or_wrong_lines_give_no_fcf(self):
        two = [("Purchase of Fixed Assets", "Mar 2026", -1), ("Purchase of Property, Plant & Equipment", "Mar 2026", -2)]
        self.assertIsNone(fu.parse_cashflow(cashflow(capex_lines=two))["free_cash_flow"])
        for label in ("Purchase of Investments", "Sale of Fixed Assets", "Purchase of Intangible Assets"):
            self.assertIsNone(fu.parse_cashflow(cashflow(capex_lines=[(label, "Mar 2026", -5)]))["free_cash_flow"], label)
        other_year = [("Purchase of Fixed Assets", "Mar 2025", -60000)]
        self.assertIsNone(fu.parse_cashflow(cashflow(capex_lines=other_year))["free_cash_flow"])


class Resp:
    def __init__(self, code, body=None): self.status_code, self._b, self.content = code, body, b"[]"
    def json(self): return self._b
    def raise_for_status(self): pass


class FlowTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.cache, self.out = d / "cache.json", d / "out" / "financials.json"
        self.calls, self.params, self.rules = [], [], {}
        self.insts = [{"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": s, "isin": "INE_" + s, "name": s + " LTD"}
                      for s in uc.SYMBOLS if s != "MARUTI"]
        outer = self
        class Sess:
            def __init__(s): s.headers = {}
            def get(s, url, params=None, timeout=0):
                isin, ep = url.split("/")[-2], url.split("/")[-1]
                t = (params or {}).get("type")
                outer.calls.append((isin, ep, t)); outer.params.append((ep, dict(params or {}))); outer.params.append((ep, dict(params or {})))
                rule = outer.rules.get((isin, ep, t)) or outer.rules.get((isin, ep))
                if rule is not None: return rule() if callable(rule) else rule
                return Resp(200, {"income-statement": income, "balance-sheet": balance, "cash-flow": lambda: cashflow(
                    capex_lines=[("Purchase of Fixed Assets", "Mar 2026", -60000)])}[ep]())
        self.patches = [mock.patch.object(fu, "CACHE_FILE", self.cache), mock.patch.object(fu, "OUT_FILE", self.out),
                        mock.patch.object(uc.requests, "Session", Sess), mock.patch.object(uc.time, "sleep", lambda s: None),
                        mock.patch.object(fu, "load_instruments", lambda: {i["trading_symbol"]: i and {"isin": i["isin"], "name": i["name"]} for i in self.insts}),
                        mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": TOKEN})]
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def run_main(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): fu.main()
        return buf.getvalue()

    def strict(self):
        return json.loads(self.out.read_text(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))

    def test_full_run_valid_json_no_token_and_cache_reuse(self):
        log = self.run_main(); doc = self.strict()
        self.assertEqual(len(doc["stocks"]), 9)                     # MARUTI missing from instruments
        self.assertEqual(len(self.calls), 27)
        self.assertNotIn(TOKEN, log + self.out.read_text() + self.cache.read_text())
        self.assertEqual(vo.check_financials(doc), [])
        self.calls.clear(); self.run_main()
        self.assertEqual(self.calls, [])                             # fresh cache -> no new API calls

    def test_cashflow_does_not_force_time_period(self):
        self.run_main()
        cf = [p for ep, p in self.params if ep == "cash-flow"]; inc = [p for ep, p in self.params if ep == "income-statement"]
        self.assertTrue(cf and all("time_period" not in p and p.get("fs") == "true" for p in cf))
        self.assertTrue(inc and all(p.get("time_period") == "yearly" for p in inc))

    def test_cashflow_call_sends_no_time_period(self):
        self.run_main()
        cf = [p for ep, p in self.params if ep == "cash-flow"]; inc = [p for ep, p in self.params if ep == "income-statement"]
        self.assertTrue(cf and all("time_period" not in p and p.get("fs") == "true" for p in cf))
        self.assertTrue(inc and all(p.get("time_period") == "yearly" for p in inc))

    def test_standalone_fallback_records_basis(self):
        self.rules[("INE_RELIANCE", "income-statement", "consolidated")] = Resp(404, {})
        self.run_main(); doc = self.strict()
        rel = next(s for s in doc["stocks"] if s["symbol"] == "RELIANCE")
        self.assertEqual((rel["income"]["basis"], rel["balance_sheet"]["basis"], rel["basis"]), ("standalone", "consolidated", "mixed"))

    def test_server_error_isolated_and_recorded(self):
        self.rules[("INE_INFY", "cash-flow")] = Resp(500, {})
        self.run_main(); doc = self.strict()
        infy = next(s for s in doc["stocks"] if s["symbol"] == "INFY")
        self.assertIsNone(infy["cash_flow"]["operating"]); self.assertIsNotNone(infy["income"]["revenue"])
        self.assertTrue(any(e["symbol"] == "INFY" for e in doc["errors"]))
        self.assertFalse(any(c[0] == "INE_INFY" and c[1] == "cash-flow" and c[2] == "standalone" for c in self.calls))

    def test_rejected_token_aborts_and_keeps_old_output(self):
        self.out.parent.mkdir(parents=True); self.out.write_text('{"old": true}')
        self.rules[("INE_RELIANCE", "income-statement")] = Resp(401, {})
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()): fu.main()
        self.assertEqual(self.out.read_text(), '{"old": true}')

    def test_missing_token_fails_clearly(self):
        with mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": ""}):
            buf = io.StringIO()
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(buf): fu.main()
            self.assertIn("UPSTOX_ANALYTICS_TOKEN is not set", buf.getvalue())


class ValidatorTests(unittest.TestCase):
    def good(self):
        return {"as_of": "2026-09-30", "source": "Upstox", "stocks": [{"symbol": "TCS", "basis": "consolidated",
                "income": {"basis": "consolidated", "revenue": 1.0, "revenue_growth": None, "operating_profit": None,
                           "operating_profit_growth": None, "net_profit": 2.0, "net_profit_growth": 3.0},
                "balance_sheet": {"basis": "consolidated", "total_assets": 100.0, "total_liabilities": 40.0, "total_equity": 60.0,
                                  "liabilities_to_equity": 0.67, "total_debt": None, "debt_to_equity": None},
                "cash_flow": {"basis": "consolidated", "operating": 10.0, "investing": -5.0, "financing": -1.0,
                              "capex": 4.0, "free_cash_flow": 6.0}}]}

    def test_good_passes_and_bad_arithmetic_fails(self):
        d = self.good(); self.assertEqual(vo.check_financials(d), [])
        d["stocks"][0]["balance_sheet"]["total_equity"] = 99.0
        self.assertTrue(vo.check_financials(d))
        d = self.good(); d["stocks"][0]["cash_flow"]["capex"] = None
        self.assertTrue(vo.check_financials(d))                      # FCF without a capex line
        d = self.good(); d["stocks"][0]["income"]["revenue"] = "12"
        self.assertTrue(vo.check_financials(d))

    def test_nan_file_is_set_aside(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "out"; out.mkdir()
            (out / "financials.json").write_text('{"stocks": [NaN]}'); (out / "scans.json").write_text('{"a": NaN}')
            with mock.patch.object(vo, "OUT", out), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(vo.main(), 1)
            self.assertFalse((out / "financials.json").exists()); self.assertTrue((out / "scans.json").exists())


if __name__ == "__main__":
    unittest.main()
