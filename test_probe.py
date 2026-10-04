"""Offline test of probe_financials.py (no network, no token). Run: python3 test_probe.py"""
import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import probe_financials as pf
from mock_responses import balance, cashflow, income

SECRET = "TOKEN-xyz-SECRET-123"
VALUES = ["1086181", "982671", "917121", "889569", "123162", "106017", "104340", "95610", "80787", "78633", "74088", "964693", "901064", "17978",
          "16057", "876654", "25230", "25707", "51.47", "51.45", "1950121", "940495", "1755986", "830198", "1450851", "1009626", "150000", "140000",
          "90000", "20000", "10.53", "18.35", "16.17"]


class FakeApi:
    calls = 0
    def __init__(self, bodies): self.bodies = bodies
    def get(self, path, params=None):
        FakeApi.calls += 1
        k = path.split("/")[-1] + ":" + (params or {}).get("type", "")
        return self.bodies.get(k, (None, "HTTP 404"))


def full(**over):
    return {"income-statement:consolidated": (income(**over.get("inc", {})), None), "balance-sheet:consolidated": (balance(), None),
            "cash-flow:consolidated": (cashflow(), None)}


class ProbeTests(unittest.TestCase):
    def run_probe(self, bodies):
        res = pf.probe_one(FakeApi(bodies), "INE000000000")
        return res, json.dumps(res)

    def test_no_financial_value_and_no_token_in_the_report(self):
        res, text = self.run_probe(full())
        for v in VALUES:
            self.assertNotIn(v, text, "value leaked: " + v)
        self.assertNotIn(SECRET, text)
        out = "\n".join(pf.summary_lines("TCS", res))
        for v in VALUES:
            self.assertNotIn(v, out, "value leaked into the log lines: " + v)

    def test_income_periods_and_summary_detail_difference(self):
        res, _ = self.run_probe(full())
        i = res["consolidated"]["income"]
        self.assertEqual(i["status"], "ok")
        self.assertEqual(i["summary_periods"], ["Mar 2026", "Mar 2025", "Mar 2024", "Mar 2023"])
        self.assertEqual(i["detail_periods"], ["Mar 2025", "Mar 2024"])
        self.assertEqual(i["only_in_summary"], ["Mar 2026", "Mar 2023"])
        self.assertEqual(i["only_in_detail"], [])
        self.assertEqual(i["years_with_all_four_core_lines"], ["Mar 2025", "Mar 2024"])
        self.assertEqual(i["years_with_eps"], ["Mar 2025", "Mar 2024"])
        self.assertEqual(i["gaps_in_core_years"]["missing_years"], [])
        self.assertEqual(i["summary"]["revenue"]["periods_with_growth_change"], ["Mar 2026", "Mar 2025", "Mar 2024"])
        self.assertTrue(all(v["line_exists"] for v in i["detail_lines"].values()))

    def test_standalone_without_data_is_reported_not_invented(self):
        res, _ = self.run_probe(full())
        self.assertEqual(res["standalone"]["income"]["status"], "no data")
        self.assertEqual(res["standalone"]["income"]["reason"], "HTTP 404")

    def test_missing_line_and_gap_are_reported(self):
        inc = income()
        inc["data"]["full_statement"] = [x for x in inc["data"]["full_statement"] if x["particular"] != "Profit Before Tax"]
        inc["data"]["full_statement"].append({"particular": "Revenue", "history": [{"period": "Mar 2021", "value": 5}]})   # repeated label
        res, _ = self.run_probe({"income-statement:consolidated": (inc, None)})
        i = res["consolidated"]["income"]
        self.assertFalse(i["detail_lines"]["Profit Before Tax"]["line_exists"])
        self.assertEqual(i["repeated_labels_in_detail"], ["Revenue"])
        self.assertEqual(i["years_with_all_four_core_lines"], [])
        g = pf.gaps(["Mar 2026", "Mar 2024", "Mar 2022"])
        self.assertEqual(g["missing_years"], [2023, 2025])

    def test_mixed_month_ends_flagged(self):
        g = pf.gaps(["Mar 2026", "Dec 2025", "Sep 2025"])
        self.assertIsNone(g["missing_years"]); self.assertEqual(g["months"], [3, 9, 12])
        cf = cashflow()
        cf["data"]["time_period"] = "quarterly"
        res, _ = self.run_probe({"cash-flow:consolidated": (cf, None)})
        self.assertEqual(res["consolidated"]["cash_flow"]["time_period_field"], "quarterly")

    def test_unparsable_period_and_odd_text_are_not_echoed(self):
        inc = income()
        inc["data"]["income_statement"][0]["history"].append({"value": 777777, "period": "<script>x</script>", "change": "+1%"})
        res, text = self.run_probe({"income-statement:consolidated": (inc, None)})
        self.assertNotIn("<script>", text); self.assertNotIn("777777", text)
        self.assertEqual(res["consolidated"]["income"]["summary"]["revenue"]["unparsable_entries"], 1)

    def test_balance_and_cash_flow(self):
        res, _ = self.run_probe(full())
        b, c = res["consolidated"]["balance_sheet"], res["consolidated"]["cash_flow"]
        self.assertEqual(b["summary_periods"], ["Mar 2025", "Mar 2024"]); self.assertEqual(b["summary_periods_with_assets_and_liabilities"], ["Mar 2025", "Mar 2024"])
        self.assertEqual(c["summary_periods"], ["Mar 2026", "Mar 2025"]); self.assertEqual(c["operating_cash_flow_years"], ["Mar 2026", "Mar 2025"])
        self.assertEqual(c["period_month_ends"], [3])

    def test_unexpected_shape_does_not_crash(self):
        res, _ = self.run_probe({"income-statement:consolidated": ({"status": "success", "data": {"income_statement": "oops", "full_statement": 5}}, None)})
        self.assertIn(res["consolidated"]["income"]["status"], ("ok", "unexpected response shape"))

    def test_source_never_prints_values_or_raw_bodies(self):
        src = (HERE / "probe_financials.py").read_text()
        for bad in ("print(body", "log(body", "json.dumps(body", "UPSTOX_ANALYTICS_TOKEN\")", "Authorization"):
            self.assertNotIn(bad, src)
        wf = (HERE / ".github/workflows/probe_financials.yml").read_text()
        self.assertIn("workflow_dispatch", wf); self.assertNotIn("push:", wf); self.assertNotIn("schedule", wf)
        self.assertNotIn("deploy-pages", wf); self.assertNotIn("upload-pages-artifact", wf); self.assertNotIn("actions/cache", wf); self.assertIn("contents: read", wf)

    def test_symbols_are_the_ten_development_stocks(self):
        self.assertEqual(sorted(pf.SYMBOLS), sorted("RELIANCE TCS INFY HDFCBANK ICICIBANK SBIN ITC BHARTIARTL LT MARUTI".split()))


if __name__ == "__main__":
    unittest.main()
