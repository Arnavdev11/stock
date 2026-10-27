"""Offline tests for fundamentals_extra_updater.py (Upstox corporate actions and share holdings).

No network: a fake client stands in for upstox_common.Upstox. The response bodies follow the field names in Upstox's documentation; every VALUE in
them is invented for the test (they are not real company data).
"""
import json
import tempfile
import unittest
from pathlib import Path

import fundamentals_extra_updater as fx

TODAY = "2026-10-28"

# ---- documented shape, invented values
ACTIONS_BODY = {"status": "success", "data": [
    {"name": "Dividend", "expiry_date": "14 Aug 2026", "amount": "5.5", "ratio": None,
     "event_details": [{"name": "Record Date", "value": "14 Aug 2026"}, {"name": "Announcement Date", "value": "20 May 2026"},
                       {"name": "Dividend Type", "value": "Final"}]},
    {"name": "Dividend", "expiry_date": "05 Feb 2026", "amount": 2, "event_details": [{"name": "Dividend Type", "value": "Interim"}]},
    {"name": "Dividend", "expiry_date": "10 Aug 2025", "amount": "4", "event_details": []},          # outside the 12-month window
    {"name": "Bonus", "expiry_date": "28 Oct 2024", "amount": None, "ratio": "1:1", "event_details": []},
    {"name": "Stock Split", "expiry_date": "02 Jan 2023", "ratio": "10 : 2", "event_details": []},
]}
HOLDINGS_BODY = {"status": "success", "data": [
    {"category": "promoters", "history": [{"period": "Sep 2026", "value": "50.1"}, {"period": "Jun 2026", "value": 50.3}]},
    {"category": "fii", "history": [{"period": "Sep 2026", "value": "20.0"}, {"period": "Jun 2026", "value": "19.7"}]},
    {"category": "other_dii", "history": [{"period": "Sep 2026", "value": "5.4"}, {"period": "Jun 2026", "value": "5.0"}]},
    {"category": "mutual_funds", "history": [{"period": "Sep 2026", "value": "9.5"}, {"period": "Jun 2026", "value": "9.0"}]},
    {"category": "retail_and_other", "history": [{"period": "Sep 2026", "value": "15.0"}, {"period": "Jun 2026", "value": "16.0"}]},
]}


class FakeApi:
    """Answers like upstox_common.Upstox.get: (body, None) or (None, error). responses: {path: body or ("err", text)}."""
    def __init__(self, responses, max_calls=100):
        self.responses, self.calls, self.max_calls, self.paths = responses, 0, max_calls, []

    def get(self, path, params=None):
        if self.calls >= self.max_calls:
            return None, "call limit for this run reached"
        self.calls += 1
        self.paths.append(path)
        r = self.responses.get(path, ("err", "HTTP 404"))
        return (None, r[1]) if isinstance(r, tuple) else (json.loads(json.dumps(r)), None)


def new_cache():
    return {"schema_version": fx.SCHEMA_VERSION, "kind": "fundamentals_extra_cache", "stocks": {}}


INSTR = {"AAA": {"isin": "INE000A01011"}, "BBB": {"isin": "INE000B01019"}, "CCC": {"isin": "INE000C01017"}}


class Dates(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(fx.parse_date("14 Aug 2025"), "2025-08-14")
        self.assertEqual(fx.parse_date("14-Aug-2025"), "2025-08-14")
        self.assertEqual(fx.parse_date("2025-08-14"), "2025-08-14")
        for bad in (None, "", "soon", "31 Feb 2025", 20250814):
            self.assertIsNone(fx.parse_date(bad))

    def test_quarter_end(self):
        self.assertEqual(fx.quarter_end("Mar 2026"), "2026-03-31")
        self.assertEqual(fx.quarter_end("Jun 2026"), "2026-06-30")
        self.assertEqual(fx.quarter_end("Dec 2025"), "2025-12-31")
        self.assertIsNone(fx.quarter_end("Feb 2026"))        # not a quarter end
        self.assertIsNone(fx.quarter_end("FY26"))


class CorporateActions(unittest.TestCase):
    def test_documented_shape(self):
        d, err = fx.parse_corporate_actions(ACTIONS_BODY)
        self.assertIsNone(err)
        ev = d["events"]
        self.assertEqual([e["ex_date"] for e in ev], ["2026-08-14", "2026-02-05", "2025-08-10", "2024-10-28", "2023-01-02"])   # newest first
        first = ev[0]
        self.assertEqual((first["type"], first["amount"], first["record_date"], first["announcement_date"], first["dividend_type"]),
                         ("dividend", 5.5, "2026-08-14", "2026-05-20", "Final"))
        self.assertEqual((ev[3]["type"], ev[3]["ratio"], ev[3]["ratio_parts"], ev[3]["amount"]), ("bonus", "1:1", [1.0, 1.0], None))
        self.assertEqual((ev[4]["type"], ev[4]["ratio_parts"]), ("split", [10.0, 2.0]))

    def test_missing_amount_is_never_zero(self):
        body = {"data": [{"name": "Dividend", "expiry_date": "01 Oct 2026", "amount": "-", "event_details": []}]}
        d, _ = fx.parse_corporate_actions(body)
        self.assertIsNone(d["events"][0]["amount"])

    def test_bad_ratio_and_date_stay_empty(self):
        d, _ = fx.parse_corporate_actions({"data": [{"name": "Bonus", "expiry_date": "someday", "ratio": "one for one"}]})
        e = d["events"][0]
        self.assertEqual((e["ex_date"], e["ratio"], e["ratio_parts"]), (None, None, None))

    def test_empty_list_is_valid(self):
        self.assertEqual(fx.parse_corporate_actions({"data": []}), ({"events": []}, None))

    def test_unknown_shape_is_unusable(self):
        for body in ({}, {"data": {}}, {"data": [{"amount": 1}]}, {"data": ["x"]}, None):
            d, err = fx.parse_corporate_actions(body)
            self.assertIsNone(d)
            self.assertTrue(err)

    def test_ttm_dividend(self):
        ev = fx.parse_corporate_actions(ACTIONS_BODY)[0]["events"]
        t = fx.ttm_dividend(ev, TODAY)
        self.assertEqual((t["value"], t["events"], t["reason"]), (7.5, 2, None))     # 5.5 + 2; the Aug 2025 dividend is outside the window

    def test_ttm_left_empty_when_an_amount_is_missing(self):
        ev = fx.parse_corporate_actions({"data": [
            {"name": "Dividend", "expiry_date": "01 Oct 2026", "amount": "3"},
            {"name": "Dividend", "expiry_date": "01 Mar 2026", "amount": None}]})[0]["events"]
        t = fx.ttm_dividend(ev, TODAY)
        self.assertEqual((t["value"], t["reason"], t["events"]), (None, "a_dividend_amount_is_missing", 2))

    def test_ttm_none_in_window(self):
        t = fx.ttm_dividend([], TODAY)
        self.assertEqual((t["value"], t["reason"]), (None, "no_dividend_in_the_last_12_months"))


class ShareHoldings(unittest.TestCase):
    def test_documented_shape(self):
        d, err = fx.parse_share_holdings(HOLDINGS_BODY)
        self.assertIsNone(err)
        q = d["quarters"]
        self.assertEqual([x["quarter_end"] for x in q], ["2026-09-30", "2026-06-30"])
        self.assertEqual(q[0]["values"], {"promoters": 50.1, "fii": 20.0, "other_dii": 5.4, "mutual_funds": 9.5, "retail_and_other": 15.0})
        self.assertEqual((q[0]["total"], q[0]["flags"]), (100.0, []))

    def test_missing_category_is_flagged_not_filled(self):
        body = {"data": [x for x in HOLDINGS_BODY["data"] if x["category"] != "fii"]}
        q = fx.parse_share_holdings(body)[0]["quarters"][0]
        self.assertIsNone(q["values"]["fii"])
        self.assertIn("a_category_is_missing", q["flags"])
        self.assertIn("does_not_add_up_to_100", q["flags"])     # flagged, never rescaled

    def test_rejects(self):
        bad = [{"data": [{"category": "public", "history": []}]},
               {"data": [{"category": "fii", "history": [{"period": "Feb 2026", "value": 1}]}]},
               {"data": [{"category": "fii", "history": [{"period": "Mar 2026", "value": 140}]}]},
               {"data": [{"category": "fii", "history": [{"period": "Mar 2026", "value": 1}, {"period": "Mar 2026", "value": 2}]}]},
               {"data": None}]
        for body in bad:
            d, err = fx.parse_share_holdings(body)
            self.assertIsNone(d)
            self.assertTrue(err)


class RunAndState(unittest.TestCase):
    def responses(self):
        return {"INE000A01011/corporate-actions": ACTIONS_BODY, "INE000A01011/share-holdings": HOLDINGS_BODY,
                "INE000B01019/corporate-actions": {"status": "success", "data": []},
                "INE000B01019/share-holdings": ("err", "HTTP 500"),
                "INE000C01017/corporate-actions": {"status": "success", "data": {"odd": 1}}}

    def test_every_stock_gets_a_status(self):
        cache = new_cache()
        api = FakeApi(self.responses())
        fx.update(api, ["AAA", "BBB", "CCC", "ZZZ"], INSTR, cache, list(fx.KINDS), TODAY)
        st = {s: {k: (cache["stocks"][s].get(k) or {}).get("status") for k in fx.KINDS} for s in cache["stocks"]}
        self.assertEqual(st["AAA"], {"corporate_actions": "ok", "share_holdings": "ok"})
        self.assertEqual(st["BBB"], {"corporate_actions": "none_reported", "share_holdings": "failed"})
        self.assertEqual(st["CCC"]["corporate_actions"], "unusable")
        self.assertEqual(st["ZZZ"], {"corporate_actions": "not_in_upstox", "share_holdings": "not_in_upstox"})
        self.assertNotIn("ZZZ", " ".join(api.paths))        # no request for an unmapped symbol
        self.assertEqual(cache["stocks"]["BBB"]["share_holdings"]["error"], "HTTP 500")

    def test_budget_stop_leaves_the_rest_for_next_run(self):
        cache = new_cache()
        api = FakeApi(self.responses(), max_calls=2)
        rep = fx.update(api, ["AAA", "BBB", "CCC"], INSTR, cache, ["corporate_actions"], TODAY)
        self.assertEqual(rep["corporate_actions"], {"requested": 2, "not_reached": 1})
        self.assertNotIn("corporate_actions", cache["stocks"].get("CCC", {}))
        api2 = FakeApi(self.responses())
        fx.update(api2, ["AAA", "BBB", "CCC"], INSTR, cache, ["corporate_actions"], TODAY)
        self.assertEqual(api2.paths, ["INE000C01017/corporate-actions"])      # only the one not reached; verified data is not downloaded again

    def test_refresh_and_backoff(self):
        cache = new_cache()
        fx.update(FakeApi(self.responses()), ["AAA", "BBB"], INSTR, cache, list(fx.KINDS), TODAY)
        e = cache["stocks"]
        self.assertFalse(fx.due(e["AAA"], "corporate_actions", "2026-11-10"))
        self.assertTrue(fx.due(e["AAA"], "corporate_actions", "2026-11-11"))        # 14 days
        self.assertFalse(fx.due(e["AAA"], "share_holdings", "2026-11-26"))
        self.assertTrue(fx.due(e["AAA"], "share_holdings", "2026-11-27"))           # 30 days
        self.assertTrue(fx.due(e["BBB"], "share_holdings", "2026-10-29"))           # first failure: retry after 1 day
        e["BBB"]["share_holdings"]["failures"] = 4
        self.assertFalse(fx.due(e["BBB"], "share_holdings", "2026-11-04"))
        self.assertTrue(fx.due(e["BBB"], "share_holdings", "2026-11-05"))           # 8 days after the 4th
        self.assertEqual(fx.retry_days(20), fx.MAX_RETRY_DAYS)

    def test_failure_keeps_earlier_data(self):
        cache = new_cache()
        fx.update(FakeApi(self.responses()), ["AAA"], INSTR, cache, ["share_holdings"], TODAY)
        failing = {"INE000A01011/share-holdings": ("err", "HTTP 503")}
        fx.update(FakeApi(failing), ["AAA"], INSTR, cache, ["share_holdings"], "2026-12-01")
        st = cache["stocks"]["AAA"]["share_holdings"]
        self.assertEqual((st["status"], st["fetched"], st["failures"]), ("failed", TODAY, 1))
        self.assertEqual(len(st["data"]["quarters"]), 2)

    def test_order_never_fetched_first(self):
        cache = new_cache()
        cache["stocks"]["AAA"] = {"corporate_actions": {"status": "ok", "fetched": "2026-09-01", "attempted": "2026-09-01", "failures": 0}}
        cache["stocks"]["BBB"] = {"corporate_actions": {"status": "failed", "fetched": None, "attempted": "2026-09-01", "failures": 1}}
        self.assertEqual(fx.run_order(["AAA", "BBB", "CCC"], cache, "corporate_actions", TODAY), ["CCC", "AAA", "BBB"])


class Outputs(unittest.TestCase):
    def test_files_and_summary(self):
        cache = new_cache()
        fx.update(FakeApi(RunAndState().responses()), ["AAA", "BBB", "CCC", "ZZZ"], INSTR, cache, list(fx.KINDS), TODAY)
        with tempfile.TemporaryDirectory() as d:
            n = fx.write_outputs(d, ["AAA", "BBB", "CCC", "ZZZ"], cache, list(fx.KINDS), TODAY)
            root = Path(d)
            files = sorted(str(p.relative_to(root)) for p in root.rglob("*.json"))
            self.assertEqual(files, ["out/by_symbol/corporate_actions/AAA.json", "out/by_symbol/corporate_actions/BBB.json",
                                     "out/by_symbol/share_holdings/AAA.json", "out/corporate_actions.json", "out/share_holdings.json"])
            self.assertEqual(n, 3)
            a = json.loads((root / "out/by_symbol/corporate_actions/AAA.json").read_text())
            self.assertEqual(a["stocks"]["AAA"]["ttm_dividend_per_share"]["value"], 7.5)
            b = json.loads((root / "out/by_symbol/corporate_actions/BBB.json").read_text())
            self.assertEqual((b["stocks"]["BBB"]["status"], b["stocks"]["BBB"]["events"]), ("none_reported", []))
            s = json.loads((root / "out/share_holdings.json").read_text())
            self.assertEqual(s["coverage"]["ok"], 1)
            self.assertEqual(s["coverage"]["failed"], 2)                   # BBB HTTP 500, CCC HTTP 404
            self.assertEqual(s["coverage"]["not_in_upstox"], 1)
            self.assertEqual(s["errors_total"], 3)                     # two request failures and one mapping failure, all listed
            self.assertEqual(sum(s["coverage"].values()), 4)               # no stock silently skipped
            self.assertNotIn("quarters", json.dumps(s))                    # summaries carry counts, not values

    def test_validation_blocks_bad_output(self):
        cache = new_cache()
        cache["stocks"]["AAA"] = {"isin": "INE000A01011", "corporate_actions": {"status": "ok", "fetched": TODAY, "attempted": TODAY, "failures": 0,
                                  "error": None, "data": {"events": [{"type": "dividend", "name": "Dividend", "ex_date": "14/08/2026", "amount": -1}]}}}
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit):
            fx.write_outputs(d, ["AAA"], cache, ["corporate_actions"], TODAY)
        self.assertFalse(any(Path(d).rglob("*.json")) if Path(d).exists() else False)

    def test_bad_symbol_path_refused(self):
        with self.assertRaises(ValueError):
            fx.shard_path("/x", "corporate_actions", "../etc")


class NotWired(unittest.TestCase):
    def test_no_workflow_runs_it_yet(self):
        wf = "".join(p.read_text(encoding="utf-8") for p in Path(".github/workflows").glob("*.yml"))
        self.assertNotIn("fundamentals_extra_updater", wf)


if __name__ == "__main__":
    unittest.main()
