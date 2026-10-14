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
            q.merge_quarters(rec, q.parse_quarterly(body(P5, [10, 1, 1, 1, 20], [base, 1, 1, 1, 4])), "consolidated", "2026-08-01")      # a full quarterly run: two periods in the same month alone would look annual
            self.assertIsNone(q.growth_for(rec["quarters"], "2026-06-30")["yoy"]["profit_after_tax"])

    def test_growth_ignores_the_percentage_upstox_attaches(self):
        b = body(P5, [100, 1, 1, 1, 120], [1] * 5)
        b["data"]["full_statement"][0]["history"][4]["change"] = "+99.00%"
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

    def test_priority_stocks_are_fetched_first_so_a_capped_run_reaches_them(self):
        # Regression: a 2,548-stock directory with a 100-call cap, alphabetical among never-checked stocks, never reached TCS (position ~2,250), so its shard was never published.
        entries = {"AAA": ENTRIES["AAA"], "BBB": ENTRIES["BBB"], "CCC": ENTRIES["CCC"]}
        log = []
        q.run_batch(entries, {}, self.fetcher({}, log), D(2026, 8, 1), 1, priority=["CCC"])
        self.assertEqual([c[0] for c in log], [ENTRIES["CCC"]["isin"]])
        log2 = []
        q.run_batch(entries, {}, self.fetcher({}, log2), D(2026, 8, 1), 1)                    # no priority: unchanged alphabetical order
        self.assertEqual([c[0] for c in log2], [ENTRIES["AAA"]["isin"]])
        # a priority stock that is not due is not fetched again; the rest keep stalest-first order
        cache = {"CCC": {**q.empty_record(), "last_checked": "2026-08-01"}}
        log3 = []
        q.run_batch(entries, cache, self.fetcher({}, log3), D(2026, 8, 1), 1, priority=["CCC", "BBB"])
        self.assertEqual([c[0] for c in log3], [ENTRIES["BBB"]["isin"]])

    def test_main_passes_the_coverage_universe_as_priority(self):
        import inspect
        src = inspect.getsource(q.main)
        self.assertIn("universe.load_entries()", src)
        self.assertIn("api.max_calls, priority", src)

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


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# The REAL quarterly response shape (verified from an actual Upstox answer): data.time_period = "quarterly", data.full_statement = null, and data.income_statement holding
# the summary categories revenue, operating_profit and net_profit, each with history entries {period, value, change}. The NUMBERS below are made-up test values (SYNTHETIC,
# not company data); only the structure is real. The `change` values are deliberately NOT the real growth, to prove they are never used.
def real_shape(periods, revenue, operating, net, change=999.0):
    def cat(name, vals):
        return {"category": name, "history": [{"period": p, "value": v, "change": change} for p, v in zip(periods, vals)]}
    return {"status": "success", "data": {"type": "consolidated", "time_period": "quarterly", "units_in": "crore",
                                           "income_statement": [cat("revenue", revenue), cat("operating_profit", operating), cat("net_profit", net)], "full_statement": None}}


R5 = ["Jun 2025", "Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]
REAL = real_shape(R5, [1000, 1100, 1200, 1300, 1500], [200, 210, 230, 240, 300], [100, 110, 120, 130, 150])


class RealShapeTests(unittest.TestCase):
    def test_revenue_net_profit_and_operating_profit_come_from_the_summary_when_full_statement_is_null(self):
        r = q.parse_quarterly(REAL)
        self.assertIsNone(r["problem"])
        self.assertEqual(sorted(r["quarters"]), ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])
        j = r["quarters"]["2026-06-30"]
        self.assertEqual(j["quarter"], "Jun 2026")
        self.assertEqual((j["values"]["revenue"], j["values"]["profit_after_tax"], j["values"]["operating_profit"]), (1500.0, 150.0, 300.0))
        for f in ("total_revenue", "profit_before_tax", "eps_basic", "eps_diluted"):
            self.assertIsNone(j["values"][f], f)                  # nothing is filled in from another line, and EPS is not invented
        self.assertEqual((j["checks"]["revenue_source"], j["checks"]["profit_after_tax_source"]), ("summary", "summary"))
        self.assertIsNone(j["checks"]["summary_net_profit_equals_pat"])        # there is no detailed PAT to compare with
        self.assertIsNone(j["checks"]["operating_profit_equals_pbt"])          # no PBT, so nothing is claimed about operating profit

    def test_operating_profit_stays_its_own_field_and_is_never_called_ebitda(self):
        j = q.parse_quarterly(REAL)["quarters"]["2026-06-30"]
        self.assertEqual(set(j["values"]), set(q.FIELDS))
        self.assertNotIn("ebitda", json.dumps(j).lower())
        self.assertEqual(j["values"]["operating_profit"], 300.0)
        self.assertNotEqual(j["values"]["operating_profit"], j["values"]["profit_after_tax"])
        self.assertIn("EBITDA is not provided", q.NOTES["ebitda"])

    def test_detailed_full_statement_wins_over_the_summary_without_a_duplicate_period_error(self):
        b = real_shape(R5, [1000, 1100, 1200, 1300, 1500], [200, 210, 230, 240, 300], [100, 110, 120, 130, 150])
        b["data"]["full_statement"] = [line("Revenue", [("Mar 2026", 1310), ("Jun 2026", 1510)]), line("Profit After Tax", [("Jun 2026", 151)])]
        qs = q.parse_quarterly(b)["quarters"]
        self.assertEqual(qs["2026-06-30"]["values"]["revenue"], 1510.0)          # detailed, although the summary also has Jun 2026
        self.assertEqual(qs["2026-03-31"]["values"]["revenue"], 1310.0)
        self.assertEqual(qs["2025-12-31"]["values"]["revenue"], 1200.0)          # no detailed value for this quarter: the summary fills it
        self.assertEqual(qs["2026-06-30"]["values"]["profit_after_tax"], 151.0)
        self.assertEqual(qs["2026-03-31"]["values"]["profit_after_tax"], 130.0)  # no detailed PAT for Mar 2026: summary
        self.assertEqual((qs["2026-06-30"]["checks"]["revenue_source"], qs["2026-06-30"]["checks"]["profit_after_tax_source"]), ("full_statement", "full_statement"))
        self.assertEqual((qs["2025-12-31"]["checks"]["revenue_source"], qs["2026-03-31"]["checks"]["profit_after_tax_source"]), ("summary", "summary"))
        self.assertFalse(qs["2026-06-30"]["checks"]["summary_net_profit_equals_pat"])      # detailed 151 vs summary 150: reported as a difference, not hidden

    def test_a_null_detailed_value_falls_back_but_an_ambiguous_one_does_not(self):
        b = real_shape(["Jun 2026", "Mar 2026"], [1500, 1300], [300, 240], [150, 130])
        b["data"]["full_statement"] = [line("Revenue", [("Jun 2026", None), ("Mar 2026", None)]), line("Profit After Tax", [("Mar 2026", 1), ("Mar 2026", 2), ("Jun 2026", 3)])]
        qs = q.parse_quarterly(b)["quarters"]
        self.assertEqual(qs["2026-06-30"]["values"]["revenue"], 1500.0)          # the detailed value is null -> no detailed value -> summary
        self.assertEqual(qs["2026-06-30"]["values"]["profit_after_tax"], 3.0)    # a usable detailed PAT beats the summary 150
        self.assertIsNone(qs["2026-03-31"]["values"]["profit_after_tax"])        # listed twice: unknown, not replaced by the summary
        self.assertEqual(qs["2026-03-31"]["values"]["revenue"], 1300.0)

    def test_a_summary_category_listed_twice_is_ambiguous(self):
        b = real_shape(["Jun 2026", "Mar 2026"], [1500, 1300], [300, 240], [150, 130])
        b["data"]["income_statement"].append({"category": "revenue", "history": [{"period": "Jun 2026", "value": 7, "change": 1}]})
        qs = q.parse_quarterly(b)["quarters"]
        self.assertIsNone(qs["2026-06-30"]["values"]["revenue"])
        self.assertEqual(qs["2026-03-31"]["values"]["revenue"], 1300.0)

    def test_the_mapping_form_of_income_statement_is_read_the_same_way(self):
        b = real_shape(["Jun 2026", "Mar 2026"], [1500, 1300], [300, 240], [150, 130])
        b["data"]["income_statement"] = {c["category"]: {"history": c["history"]} for c in b["data"]["income_statement"]}
        self.assertEqual(q.parse_quarterly(b), q.parse_quarterly(real_shape(["Jun 2026", "Mar 2026"], [1500, 1300], [300, 240], [150, 130])))
        for bad in ("x", 5, [None, "x", {"category": 5}], {"revenue": "x"}):
            b["data"]["income_statement"] = bad
            self.assertEqual(q.parse_quarterly(b), {"quarters": {}, "problem": "no_data"})

    def test_a_summary_without_a_quarterly_time_period_is_still_rejected(self):
        for tp in ("yearly", None):
            b = real_shape(R5, [1] * 5, [1] * 5, [1] * 5)
            b["data"]["time_period"] = tp
            self.assertEqual(q.parse_quarterly(b)["problem"], "not_quarterly")


class RealShapeGrowthTests(unittest.TestCase):
    def doc(self, body_, drop=()):
        rec = q.empty_record()
        q.merge_quarters(rec, q.parse_quarterly(body_), "consolidated", "2026-08-01")
        for pe in drop:
            del rec["quarters"][pe]
        rec["last_checked"] = rec["last_success"] = "2026-08-01"
        docs, _, problems = q.build_outputs({"AAA": ENTRIES["AAA"]}, {"AAA": rec}, "2026-08-01")
        self.assertEqual(problems, [])
        return docs["AAA"]["stocks"]["AAA"]

    def row(self, s, pe):
        return next(r for r in s["quarters"] if r["quarter_end"] == pe)

    def test_qoq_and_yoy_are_computed_from_the_stored_quarterly_values_not_from_change(self):
        s = self.doc(REAL)
        top = self.row(s, "2026-06-30")
        self.assertEqual(top["growth"]["yoy"]["revenue"], 50.0)                  # 1500 vs 1000 (Jun 2025), not the attached 999
        self.assertEqual(top["growth"]["qoq"]["revenue"], 15.38)                 # 1500 vs 1300 (Mar 2026)
        self.assertEqual((top["growth"]["yoy"]["profit_after_tax"], top["growth"]["qoq"]["profit_after_tax"]), (50.0, 15.38))
        self.assertEqual((top["growth"]["yoy"]["operating_profit"], top["growth"]["qoq"]["operating_profit"]), (50.0, 25.0))
        self.assertNotIn("999", json.dumps(s))                                   # the source's change figure is not stored or published anywhere
        self.assertEqual(top["values"]["revenue"], 1500.0)

    def test_missing_comparison_quarters_give_null_growth(self):
        s = self.doc(REAL)
        first = self.row(s, "2025-06-30")
        self.assertTrue(all(v is None for v in first["growth"]["yoy"].values()) and all(v is None for v in first["growth"]["qoq"].values()))
        second = self.row(s, "2025-09-30")
        self.assertIsNone(second["growth"]["yoy"]["revenue"])                    # no Sep 2024 stored
        self.assertEqual(second["growth"]["qoq"]["revenue"], 10.0)
        s2 = self.doc(REAL, drop=("2025-12-31",))                                # a gap: Mar 2026 has no stored Dec 2025 to compare with
        self.assertIsNone(self.row(s2, "2026-03-31")["growth"]["qoq"]["revenue"])
        self.assertEqual(s2["gaps"], [{"between": ["2025-09-30", "2026-03-31"]}])

    def test_the_published_shard_is_valid_json_with_null_not_zero_where_data_is_missing(self):
        s = self.doc(REAL)
        text = json.dumps(s, allow_nan=False)
        top = self.row(json.loads(text), "2026-06-30")
        self.assertIsNone(top["values"]["eps_basic"])
        self.assertIsNone(top["values"]["total_revenue"])
        doc = q.document(s, "2026-08-01")
        self.assertEqual(q.check_stock_doc(doc, ENTRIES["AAA"]), [])

    def test_a_run_with_a_real_shaped_response_stores_and_publishes_it(self):
        cache, log = {}, []
        def fetch(isin, basis):
            log.append((isin, basis))
            return REAL, None
        q.run_batch({"AAA": ENTRIES["AAA"]}, cache, fetch, D(2026, 8, 1), 10)
        self.assertEqual(log, [(ENTRIES["AAA"]["isin"], "consolidated")])
        self.assertEqual(cache["AAA"]["quarters"]["2026-06-30"]["values"]["revenue"], 1500.0)
        self.assertEqual(cache["AAA"]["problem"], None)


class AnnualIsNotQuarterlyTests(unittest.TestCase):
    """The real response labelled time_period=quarterly carried ANNUAL periods in full_statement (populated) beside quarterly summary categories. Annual values must never become quarters.
    The annual numbers below are SYNTHETIC; only the structure is real."""
    ANNUAL = [line("Revenue", [("Mar 2023", 90000), ("Mar 2024", 95000), ("Mar 2025", 100000)]),
              line("Profit After Tax", [("Mar 2023", 9000), ("Mar 2024", 9500), ("Mar 2025", 10000)]),
              line("Total Revenue", [("Mar 2023", 91000), ("Mar 2024", 96000), ("Mar 2025", 101000)]),
              line("EPS Basic", [("Mar 2023", 90.0), ("Mar 2024", 95.0), ("Mar 2025", 100.0)])]

    def real(self, full):
        b = real_shape(R5, [1000, 1100, 1200, 1300, 1500], [200, 210, 230, 240, 300], [100, 110, 120, 130, 150])
        b["data"]["full_statement"] = full
        return b

    def test_an_annual_full_statement_is_ignored_and_the_quarters_come_from_the_summary(self):
        r = q.parse_quarterly(self.real(self.ANNUAL))
        self.assertIsNone(r["problem"])
        self.assertEqual(sorted(r["quarters"]), ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])      # no quarter is created from an annual period
        m = r["quarters"]["2026-03-31"]
        self.assertEqual((m["values"]["revenue"], m["values"]["profit_after_tax"]), (1300.0, 130.0))                         # the summary quarter, not the annual figure
        for pe, quarter in r["quarters"].items():
            for f in ("total_revenue", "eps_basic", "profit_before_tax"):
                self.assertIsNone(quarter["values"][f], (pe, f))                                                              # annual EPS, total revenue etc. are not shown as quarterly
            self.assertEqual((quarter["checks"]["revenue_source"], quarter["checks"]["profit_after_tax_source"]), ("summary", "summary"))
        self.assertNotIn("100000", json.dumps(r))

    def test_one_annual_line_among_quarterly_lines_discards_the_full_statement(self):
        full = [line("Revenue", list(zip(R5, [1, 2, 3, 4, 5]))), line("Profit After Tax", [("Mar 2024", 9500), ("Mar 2025", 10000)])]
        r = q.parse_quarterly(self.real(full))
        self.assertEqual(r["quarters"]["2026-06-30"]["values"]["revenue"], 1500.0)           # not the detailed 5: the response mixes annual and quarterly lines, so none of it is trusted
        self.assertEqual(r["quarters"]["2026-06-30"]["values"]["profit_after_tax"], 150.0)

    def test_an_unverifiable_single_period_detailed_line_does_not_override_a_quarterly_summary(self):
        r = q.parse_quarterly(self.real([line("Revenue", [("Mar 2026", 777)]), line("Profit After Tax", [("Mar 2026", 77)])]))
        self.assertEqual(r["quarters"]["2026-03-31"]["values"]["revenue"], 1300.0)
        self.assertEqual(r["quarters"]["2026-03-31"]["values"]["profit_after_tax"], 130.0)

    def test_genuine_quarterly_detailed_lines_still_take_precedence(self):
        full = [line("Revenue", list(zip(R5, [1001, 1101, 1201, 1301, 1501]))), line("Total Revenue", list(zip(R5, [2001, 2101, 2201, 2301, 2501])))]
        r = q.parse_quarterly(self.real(full))
        top = r["quarters"]["2026-06-30"]
        self.assertEqual((top["values"]["revenue"], top["values"]["total_revenue"]), (1501.0, 2501.0))
        self.assertEqual((top["checks"]["revenue_source"], top["values"]["profit_after_tax"], top["checks"]["profit_after_tax_source"]), ("full_statement", 150.0, "summary"))

    def test_two_periods_in_the_same_month_with_nothing_else_are_treated_as_annual(self):
        r = q.parse_quarterly(body(["Mar 2024", "Mar 2025"], [1, 2], [1, 2]))
        self.assertEqual((r["quarters"], r["problem"]), ({}, "no_data"))                      # annual-looking and no quarterly summary: nothing is published

    def test_annual_data_never_produces_growth_figures_for_the_quarters(self):
        rec = q.empty_record()
        q.merge_quarters(rec, q.parse_quarterly(self.real(self.ANNUAL)), "consolidated", "2026-08-01")
        g = q.growth_for(rec["quarters"], "2026-06-30")
        self.assertEqual((g["yoy"]["revenue"], g["qoq"]["revenue"]), (50.0, 15.38))          # from the summary quarters, exactly as without the annual lines


if __name__ == "__main__":
    unittest.main()
