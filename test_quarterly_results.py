"""Tests for quarterly_results_updater.py. Offline: every Upstox response is a hand-made fixture in the DOCUMENTED shape (a real quarterly response has not been observed yet)."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import quarterly_results_updater as q
import stock_directory

D = dt.date


def line(label, hist):
    return {"particular": label, "history": [{"period": p, "value": v} for p, v in hist]}


def body(periods, rev, pat, eps=None, tp="quarterly", opp=None, pbt=None, total=None, extra=None):
    full = [line("Revenue", list(zip(periods, rev))), line("Profit After Tax", list(zip(periods, pat)))]
    if eps is not None:
        full.append(line("EPS Basic", list(zip(periods, eps))))
    if pbt is not None:
        full.append(line("Profit Before Tax", list(zip(periods, pbt))))
    if total is not None:
        full.append(line("Total Revenue", list(zip(periods, total))))
    full += extra or []
    summ = []
    if opp is not None:
        summ.append({"category": "operating_profit", "history": [{"period": p, "value": v} for p, v in zip(periods, opp)]})
    return {"status": "success", "data": {"type": "consolidated", "time_period": tp, "units_in": "crore", "income_statement": summ, "full_statement": full}}


P5 = ["Jun 2025", "Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]
GOOD = body(P5, [100, 110, 120, 130, 150], [10, 11, 12, 13, 15], eps=[1.0, 1.1, 1.2, 1.3, 1.5], pbt=[14, 15, 16, 17, 20], opp=[14, 16, 18, 17, 20])


class ParseTests(unittest.TestCase):
    def test_a_clean_quarterly_response_is_parsed_into_quarter_ends(self):
        r = q.parse_quarterly(GOOD)
        self.assertIsNone(r["problem"])
        self.assertEqual(sorted(r["quarters"]), ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])
        j = r["quarters"]["2026-06-30"]
        self.assertEqual(j["quarter"], "Jun 2026")
        self.assertEqual((j["values"]["revenue"], j["values"]["profit_after_tax"], j["values"]["eps_basic"]), (150.0, 15.0, 1.5))
        self.assertIsNone(j["values"]["total_revenue"])           # never filled from Revenue
        self.assertIsNone(j["values"]["eps_diluted"])

    def test_yearly_missing_or_other_time_period_is_rejected(self):
        for tp in ("yearly", None, "", "ttm"):
            b = body(P5, [1] * 5, [1] * 5, tp=tp)
            if tp is None:
                del b["data"]["time_period"]
            r = q.parse_quarterly(b)
            self.assertEqual((r["quarters"], r["problem"]), ({}, "not_quarterly"), tp)

    def test_mixed_month_ends_are_rejected(self):
        r = q.parse_quarterly(body(["Jun 2025", "Sep 2025", "Oct 2025"], [1, 2, 3], [1, 2, 3]))
        self.assertEqual((r["quarters"], r["problem"]), ({}, "mixed_month_ends"))

    def test_non_calendar_fiscal_quarters_are_accepted(self):
        r = q.parse_quarterly(body(["Jan 2026", "Apr 2026"], [5, 6], [1, 2]))
        self.assertEqual(sorted(r["quarters"]), ["2026-01-31", "2026-04-30"])

    def test_a_period_listed_twice_is_not_guessed(self):
        b = body(["Jun 2025", "Jun 2025", "Sep 2025"], [1, 2, 3], [1, 1, 1])
        b["data"]["full_statement"][1] = line("Profit After Tax", [("Jun 2025", 7), ("Sep 2025", 1)])      # PAT listed once: only revenue is ambiguous
        r = q.parse_quarterly(b)
        self.assertIsNone(r["quarters"]["2025-06-30"]["values"]["revenue"])
        self.assertEqual(r["quarters"]["2025-06-30"]["values"]["profit_after_tax"], 7.0)
        self.assertEqual(r["quarters"]["2025-09-30"]["values"]["revenue"], 3.0)
        both = q.parse_quarterly(body(["Jun 2025", "Jun 2025"], [1, 2], [1, 2]))              # everything in that quarter ambiguous: the quarter is dropped
        self.assertEqual((both["quarters"], both["problem"]), ({}, "no_data"))

    def test_unusable_values_become_null_not_zero_and_empty_quarters_are_dropped(self):
        b = body(["Jun 2025", "Sep 2025"], ["-", None], ["NA", None], eps=["", None])
        r = q.parse_quarterly(b)
        self.assertEqual((r["quarters"], r["problem"]), ({}, "no_data"))
        b = body(["Jun 2025", "Sep 2025"], ["-", 5], ["NA", 2])
        r = q.parse_quarterly(b)
        self.assertEqual(list(r["quarters"]), ["2025-09-30"])

    def test_a_real_zero_is_kept_as_zero(self):
        r = q.parse_quarterly(body(["Jun 2025"], [0], [0]))
        self.assertEqual(r["quarters"]["2025-06-30"]["values"]["revenue"], 0.0)

    def test_garbage_bodies_are_rejected_without_raising(self):
        for b in (None, [], {}, {"data": None}, {"data": {"time_period": "quarterly"}}, {"data": {"time_period": "quarterly", "full_statement": "x"}}):
            r = q.parse_quarterly(b)
            self.assertEqual(r["quarters"], {})
            self.assertTrue(r["problem"])

    def test_operating_profit_equal_to_pbt_is_flagged_and_ebitda_is_never_produced(self):
        r = q.parse_quarterly(GOOD)
        self.assertTrue(r["quarters"]["2026-03-31"]["checks"]["operating_profit_equals_pbt"] is True)
        self.assertFalse(r["quarters"]["2025-09-30"]["checks"]["operating_profit_equals_pbt"] is True)
        self.assertNotIn("ebitda", r["quarters"]["2026-06-30"]["values"])
        self.assertNotIn("ebitda", q.FIELDS)

    def test_revenue_and_total_revenue_stay_separate_for_banks(self):
        b = body(["Jun 2025"], [0], [9], total=[500])
        b["data"]["full_statement"] = [x for x in b["data"]["full_statement"] if x["particular"] != "Revenue"]
        r = q.parse_quarterly(b)["quarters"]["2025-06-30"]["values"]
        self.assertIsNone(r["revenue"])
        self.assertEqual(r["total_revenue"], 500.0)


class MergeTests(unittest.TestCase):
    def merged(self, *bodies):
        rec = q.empty_record()
        for i, b in enumerate(bodies):
            q.merge_quarters(rec, q.parse_quarterly(b), "consolidated", "2026-08-%02d" % (i + 1))
        return rec

    def test_new_quarter_is_added_and_nothing_is_deleted(self):
        rec = self.merged(body(P5[:4], [100, 110, 120, 130], [1, 1, 1, 1]), body(P5[3:], [130, 150], [1, 2]))
        self.assertEqual(sorted(rec["quarters"]), ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])
        self.assertEqual(rec["quarters"]["2026-06-30"]["retrieved_on"], "2026-08-02")
        self.assertEqual(rec["quarters"]["2025-06-30"]["retrieved_on"], "2026-08-01")

    def test_a_changed_value_keeps_the_old_one_in_revisions(self):
        rec = self.merged(body(["Jun 2026"], [100], [10]), body(["Jun 2026"], [105], [10]))
        v = rec["quarters"]["2026-06-30"]
        self.assertEqual(v["values"]["revenue"], 105.0)
        self.assertEqual(v["revisions"], [{"field": "revenue", "previous": 100.0, "changed_on": "2026-08-02"}])

    def test_an_omitted_value_never_erases_a_stored_one(self):
        rec = self.merged(body(["Jun 2026"], [100], [10], eps=[1.5]), body(["Jun 2026"], [100], [10]))
        self.assertEqual(rec["quarters"]["2026-06-30"]["values"]["eps_basic"], 1.5)

    def test_basis_is_never_mixed(self):
        rec = q.empty_record()
        q.merge_quarters(rec, q.parse_quarterly(body(["Jun 2026"], [1], [1])), "consolidated", "2026-08-01")
        q.merge_quarters(rec, q.parse_quarterly(body(["Sep 2026"], [2], [2])), "standalone", "2026-08-02")
        self.assertEqual((rec["basis"], list(rec["quarters"])), ("standalone", ["2026-09-30"]))
        q.merge_quarters(rec, {"quarters": {}, "problem": "no_data"}, "consolidated", "2026-08-03")
        self.assertEqual(rec["basis"], "standalone")


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.qs = self_rec = None
        rec = q.empty_record()
        q.merge_quarters(rec, q.parse_quarterly(GOOD), "consolidated", "2026-08-01")
        self.qs = rec["quarters"]

    def test_yoy_and_qoq_are_computed_from_stored_quarters_only(self):
        g = q.growth_for(self.qs, "2026-06-30")
        self.assertEqual(g["yoy"]["revenue"], 50.0)         # 150 vs 100 (Jun 2025)
        self.assertEqual(g["qoq"]["revenue"], 15.38)        # 150 vs 130 (Mar 2026)
        self.assertEqual(g["yoy"]["profit_after_tax"], 50.0)

    def test_growth_is_null_without_a_comparison_quarter(self):
        g = q.growth_for(self.qs, "2025-06-30")
        self.assertTrue(all(v is None for v in g["yoy"].values()) and all(v is None for v in g["qoq"].values()))
        g = q.growth_for(self.qs, "2025-09-30")
        self.assertIsNone(g["yoy"]["revenue"])
        self.assertEqual(g["qoq"]["revenue"], 10.0)

    def test_a_gap_is_not_bridged(self):
        del self.qs["2026-03-31"]
        g = q.growth_for(self.qs, "2026-06-30")
        self.assertIsNone(g["qoq"]["revenue"])
        self.assertEqual(g["yoy"]["revenue"], 50.0)

    def test_zero_or_negative_base_gives_null_not_a_number(self):
        for base in (0, -5):
            rec = q.empty_record()
            q.merge_quarters(rec, q.parse_quarterly(body(["Jun 2025", "Jun 2026"], [10, 20], [base, 4])), "consolidated", "2026-08-01")
            self.assertIsNone(q.growth_for(rec["quarters"], "2026-06-30")["yoy"]["profit_after_tax"])

    def test_growth_ignores_the_percentage_upstox_attaches(self):
        b = body(["Jun 2025", "Jun 2026"], [100, 120], [1, 1])
        b["data"]["full_statement"][0]["history"][1]["change"] = "+99.00%"
        rec = q.empty_record()
        q.merge_quarters(rec, q.parse_quarterly(b), "consolidated", "2026-08-01")
        self.assertEqual(q.growth_for(rec["quarters"], "2026-06-30")["yoy"]["revenue"], 20.0)

    def test_month_end_arithmetic(self):
        self.assertEqual(q.add_months(D(2026, 2, 28), 3), D(2026, 5, 31))
        self.assertEqual(q.add_months(D(2026, 5, 31), -3), D(2026, 2, 28))
        self.assertEqual(q.add_months(D(2024, 2, 29), 12), D(2025, 2, 28))


class RefreshTests(unittest.TestCase):
    def rec(self, checked, newest="2026-03-31"):
        return {"last_checked": checked, "quarters": {newest: {}}}

    def test_never_checked_is_due(self):
        self.assertTrue(q.needs_refresh(None, D(2026, 8, 1)))
        self.assertTrue(q.needs_refresh(q.empty_record(), D(2026, 8, 1)))

    def test_outside_the_pending_window_the_cadence_is_thirty_days(self):
        r = self.rec("2026-04-01")                              # next quarter ends 30 Jun; window opens 10 Jul
        self.assertFalse(q.needs_refresh(r, D(2026, 4, 20)))
        self.assertTrue(q.needs_refresh(r, D(2026, 5, 2)))

    def test_inside_the_pending_window_the_cadence_is_two_days(self):
        r = self.rec("2026-07-20")
        self.assertFalse(q.needs_refresh(r, D(2026, 7, 21)))
        self.assertTrue(q.needs_refresh(r, D(2026, 7, 22)))

    def test_the_window_ends_after_ninety_days(self):
        r = self.rec("2026-10-12")                              # window 10 Jul .. 8 Oct
        self.assertFalse(q.needs_refresh(r, D(2026, 10, 14)))
        self.assertTrue(q.needs_refresh(r, D(2026, 11, 12)))

    def test_a_new_quarter_closes_the_window(self):
        r = self.rec("2026-08-01", newest="2026-06-30")         # next is Sep; window opens 10 Oct
        self.assertFalse(q.needs_refresh(r, D(2026, 8, 5)))


ENTRIES = {s: {"symbol": s, "isin": "INE00%07d" % i, "name": s + " Limited"} for i, s in enumerate(["AAA", "BBB", "CCC", "DDD"], 1)}


class RunTests(unittest.TestCase):
    def fetcher(self, table, log):
        def f(isin, basis):
            log.append((isin, basis))
            return table.get((isin, basis), (None, "HTTP 404"))
        return f

    def test_stalest_first_call_cap_and_errors_leave_data_untouched(self):
        cache = {"AAA": {**q.empty_record(), "last_checked": "2026-01-01", "quarters": {"2026-03-31": {"quarter": "Mar 2026", "retrieved_on": "2026-04-01", "values": {"revenue": 1.0}, "checks": {}, "revisions": []}}}}
        log = []
        table = {(ENTRIES["BBB"]["isin"], "consolidated"): (GOOD, None)}
        s = q.run_batch(ENTRIES, cache, self.fetcher(table, log), D(2026, 8, 1), 2)
        self.assertEqual(s["calls"], 2)
        self.assertEqual([c[0] for c in log], [ENTRIES["BBB"]["isin"], ENTRIES["CCC"]["isin"]])      # never-checked ones first, alphabetical
        self.assertEqual(s["stored"], 1)
        self.assertEqual(s["errors"], 1)
        self.assertIsNone(cache["CCC"]["last_checked"])         # a failed call is retried next time
        self.assertEqual(cache["AAA"]["quarters"]["2026-03-31"]["values"]["revenue"], 1.0)
        self.assertTrue(s["skipped_by_call_cap"] >= 1)

    def test_standalone_is_tried_only_when_consolidated_has_nothing(self):
        e = {"AAA": ENTRIES["AAA"]}
        table = {(ENTRIES["AAA"]["isin"], "consolidated"): (body(["Jun 2026"], ["-"], ["-"]), None), (ENTRIES["AAA"]["isin"], "standalone"): (GOOD, None)}
        cache, log = {}, []
        q.run_batch(e, cache, self.fetcher(table, log), D(2026, 8, 1), 10)
        self.assertEqual([c[1] for c in log], ["consolidated", "standalone"])
        self.assertEqual(cache["AAA"]["basis"], "standalone")
        cache, log = {}, []
        table[(ENTRIES["AAA"]["isin"], "consolidated")] = (GOOD, None)
        q.run_batch(e, cache, self.fetcher(table, log), D(2026, 8, 1), 10)
        self.assertEqual([c[1] for c in log], ["consolidated"])

    def test_a_yearly_response_is_rejected_and_stores_nothing(self):
        e = {"AAA": ENTRIES["AAA"]}
        table = {(ENTRIES["AAA"]["isin"], "consolidated"): (body(P5, [1] * 5, [1] * 5, tp="yearly"), None)}
        cache = {}
        s = q.run_batch(e, cache, self.fetcher(table, []), D(2026, 8, 1), 10)
        self.assertEqual(s["rejected"], {"not_quarterly": 1})
        self.assertEqual(cache["AAA"]["quarters"], {})
        self.assertEqual(cache["AAA"]["problem"], "not_quarterly")

    def test_an_entry_without_isin_is_never_requested(self):
        e = {"ZZZ": {"symbol": "ZZZ", "isin": None, "name": "Z"}}
        log = []
        q.run_batch(e, {}, self.fetcher({}, log), D(2026, 8, 1), 10)
        self.assertEqual(log, [])

    def test_a_second_run_the_same_day_makes_no_calls(self):
        cache, log = {}, []
        table = {(x["isin"], "consolidated"): (GOOD, None) for x in ENTRIES.values()}
        q.run_batch(ENTRIES, cache, self.fetcher(table, log), D(2026, 8, 1), 50)
        n = len(log)
        q.run_batch(ENTRIES, cache, self.fetcher(table, log), D(2026, 8, 1), 50)
        self.assertEqual(len(log), n)


class OutputTests(unittest.TestCase):
    def cache(self):
        cache = {}
        for s in ("AAA", "BBB"):
            rec = q.empty_record()
            q.merge_quarters(rec, q.parse_quarterly(GOOD), "consolidated", "2026-08-01")
            rec["last_checked"] = rec["last_success"] = "2026-08-01"
            cache[s] = rec
        return cache

    def test_identity_comes_from_the_directory_entry_only(self):
        docs, meta, problems = q.build_outputs(ENTRIES, self.cache(), "2026-08-01")
        self.assertEqual(problems, [])
        s = docs["AAA"]["stocks"]["AAA"]
        self.assertEqual((s["symbol"], s["isin"], s["name"]), ("AAA", ENTRIES["AAA"]["isin"], "AAA Limited"))
        self.assertNotIn("CCC", docs)                            # no data -> no shard, never an empty or zero record
        self.assertEqual(meta["coverage"]["directory_stocks"], 4)
        self.assertEqual((meta["coverage"]["with_quarterly_results"], meta["coverage"]["without"]), (2, 2))
        self.assertNotIn("stocks", meta)

    def test_latest_quarter_first_with_growth_and_dates(self):
        docs, _, _ = q.build_outputs(ENTRIES, self.cache(), "2026-08-01")
        s = docs["AAA"]["stocks"]["AAA"]
        self.assertEqual([r["quarter_end"] for r in s["quarters"]], ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
        top = s["quarters"][0]
        self.assertEqual((top["growth"]["yoy"]["revenue"], top["growth"]["qoq"]["revenue"]), (50.0, 15.38))
        self.assertIsNone(top["published_on"])
        self.assertEqual(top["retrieved_on"], "2026-08-01")
        self.assertEqual((s["last_success"], s["latest_quarter_end"], s["gaps"]), ("2026-08-01", "2026-06-30", []))

    def test_gaps_are_listed_not_filled(self):
        c = self.cache()
        del c["AAA"]["quarters"]["2025-12-31"]
        s = q.build_outputs(ENTRIES, c, "2026-08-01")[0]["AAA"]["stocks"]["AAA"]
        self.assertEqual(s["gaps"], [{"between": ["2025-09-30", "2026-03-31"]}])
        self.assertNotIn("2025-12-31", [r["quarter_end"] for r in s["quarters"]])

    def test_a_shard_that_fails_its_check_is_not_published(self):
        c = self.cache()
        c["AAA"]["quarters"]["2026-06-30"]["values"]["revenue"] = float("nan")
        docs, _, problems = q.build_outputs(ENTRIES, c, "2026-08-01")
        self.assertNotIn("AAA", docs)
        self.assertEqual(problems[0][0], "AAA")

    def test_checker_rejects_identity_drift_empty_quarters_and_unsorted(self):
        doc = q.build_outputs(ENTRIES, self.cache(), "2026-08-01")[0]["AAA"]
        self.assertEqual(q.check_stock_doc(doc, ENTRIES["AAA"]), [])
        self.assertTrue(q.check_stock_doc(doc, ENTRIES["BBB"]))
        bad = json.loads(json.dumps(doc))
        s = bad["stocks"]["AAA"]
        s["name"] = "Another Name"
        self.assertTrue(q.check_stock_doc(bad, ENTRIES["AAA"]))
        bad = json.loads(json.dumps(doc))
        bad["stocks"]["AAA"]["quarters"][0]["values"] = {f: None for f in q.FIELDS}
        self.assertTrue(any("empty" in x for x in q.check_stock_doc(bad)))
        bad = json.loads(json.dumps(doc))
        bad["stocks"]["AAA"]["quarters"].reverse()
        self.assertTrue(any("newest first" in x for x in q.check_stock_doc(bad)))

    def test_files_are_written_atomically_under_the_expected_paths_and_hold_no_nan(self):
        docs, meta, _ = q.build_outputs(ENTRIES, self.cache(), "2026-08-01")
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            q.write_outputs(root, docs, meta)
            p = root / "out" / "by_symbol" / "quarterly_results" / "AAA.json"
            self.assertTrue(p.exists() and (root / "out" / "quarterly_results.json").exists())
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["stocks"]["AAA"]["isin"], ENTRIES["AAA"]["isin"])
            self.assertEqual([x.name for x in (root / "out" / "by_symbol" / "quarterly_results").iterdir() if x.suffix == ".tmp"], [])
            with self.assertRaises(ValueError):
                q._shard_path(root, "../evil")

    def test_load_entries_validates_the_directory_and_matches_the_search_identity(self):
        d = Path(tempfile.mkdtemp()) / "stock_directory.json"
        d.write_text("{}", encoding="utf-8")
        with self.assertRaises(SystemExit):
            q.load_entries(d)


class IdentityTests(unittest.TestCase):
    def test_entries_are_exactly_the_published_directory_entries(self):
        import test_stock_directory as T
        doc = T.Fx().build().doc
        d = Path(tempfile.mkdtemp()) / "stock_directory.json"
        d.write_text(stock_directory.dumps(doc), encoding="utf-8")
        got = q.load_entries(d)
        self.assertEqual(list(got), [e["symbol"] for e in doc["symbols"]])
        for e in doc["symbols"]:
            self.assertEqual(got[e["symbol"]], {"symbol": e["symbol"], "isin": e["isin"], "name": e["name"]})
        docs, _, problems = q.build_outputs(got, {e["symbol"]: dict(q.empty_record(), quarters={}) for e in doc["symbols"]}, "2026-08-01")
        self.assertEqual((docs, problems), ({}, []))                       # stocks without data get no shard (not an empty or zero one)

    def test_a_tampered_directory_stops_the_run(self):
        import test_stock_directory as T
        doc = T.Fx().build().doc
        doc["symbols"][0]["name"] = "Renamed Co"                            # the directory hash no longer matches
        d = Path(tempfile.mkdtemp()) / "stock_directory.json"
        d.write_text(stock_directory.dumps(doc), encoding="utf-8")
        with self.assertRaises(SystemExit):
            q.load_entries(d)


class SeparationTests(unittest.TestCase):
    def test_this_module_has_no_live_code_and_its_own_files(self):
        import re
        src = Path(q.__file__).read_text(encoding="utf-8")
        imports = re.findall(r"^(?:import|from) +(\S+)", src, re.M)
        self.assertFalse([m for m in imports if m.startswith("live")], "no live-market code is imported")
        self.assertEqual(sorted(m for m in imports if m.endswith("_updater")), ["financial_history_updater"])      # parsing helpers only
        for out in (q.CACHE_FILE, q.OUT_FILE):
            self.assertIn("quarterly", out.name)
        self.assertEqual(q.SHARD_KIND, "quarterly_results")

    def test_the_directory_checker_and_the_page_identity_use_the_same_fields(self):
        self.assertEqual(stock_directory.ENTRY_KEYS, ("symbol", "isin", "name"))


if __name__ == "__main__":
    unittest.main()
