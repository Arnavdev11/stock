"""Fundamentals for the whole universe in controlled batches: plan, gate, checkpoint, resume, back-off, validation and the coverage report.
Offline only: the scripted provider of test_fundamentals_full_universe stands in for Upstox (letters: A ok, B bank without ROCE/EV-EBITDA, C empty, E 404, F 500).
Run: python3 -m unittest test_fundamentals_batches"""
import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import fundamentals_updater as fu
from test_fundamentals_full_universe import START, FakeDate, Harness

HERE = Path(__file__).resolve().parent


class BatchHarness(Harness):
    """Harness.run plus the batch settings; validate() and report() run the offline commands the workflow runs."""
    def go(self, batch=None, max_stocks=500, cap=1200, day=None):
        env = {"FUNDAMENTALS_REPORT_DIR": str(self.t / "report")}
        if batch:
            env["FUNDAMENTALS_BATCH_ID"] = batch
        with mock.patch.object(fu, "MAX_STOCKS_PER_RUN", max_stocks), mock.patch.dict(os.environ, env):
            return self.run(cap=cap, day=day)

    def _offline(self, argv, previous=None):
        if previous is not None:
            (self.t / "before.json").write_text(json.dumps(previous))
            argv = argv + ["--previous", str(self.t / "before.json")]
        p = [mock.patch.object(fu, "CACHE_FILE", self.t / "data" / "c.json"), mock.patch.object(fu, "OUT_FILE", self.t / "out" / "fundamentals.json"),
             mock.patch.object(fu, "dt", __import__("types").SimpleNamespace(date=FakeDate)),
             mock.patch.object(fu, "load_instruments", lambda: (_ for _ in ()).throw(AssertionError("the offline commands must not download anything"))),
             mock.patch.object(fu.requests, "Session", lambda: (_ for _ in ()).throw(AssertionError("the offline commands must not call the API"))),
             mock.patch.dict(os.environ, {"STOCKLENS_UNIVERSE_FILE": str(self.t / "u.json"), "FUNDAMENTALS_REPORT_DIR": str(self.t / "report"), "UPSTOX_ANALYTICS_TOKEN": ""})]
        with contextlib.ExitStack() as st:
            for x in p:
                st.enter_context(x)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    rc = fu.main(argv)
                except SystemExit as e:
                    rc = e.code
        self.offline_log = buf.getvalue()
        return rc

    def validate(self, batch, previous=None, rate=None):
        return self._offline(["validate", "--batch", batch] + (["--max-failure-rate", str(rate)] if rate is not None else []), previous)

    def accept(self, batch, by="reviewer", reason="accepted after review of the listed problems"):
        return self._offline(["accept", "--batch", batch, "--by", by, "--reason", reason])

    def report(self):
        self._offline(["report"])
        return json.loads((self.t / "report" / "fundamentals_coverage.json").read_text())

    def batch(self, bid):
        return self.cache()["batches"][bid]


class FullUniverseBatches(unittest.TestCase):
    def test_2548_stocks_in_batches_of_500_with_validation_between_them(self):
        h = BatchHarness(self, 2548)
        before = None
        planned = []
        for i in range(1, 7):
            out = h.go(batch=str(i))
            self.assertEqual(h.rc, 0, h.log)
            b = h.batch(str(i))
            self.assertEqual(b["status"], "completed")
            self.assertLessEqual(len(b["planned"]), 500)
            self.assertFalse(set(b["planned"]) & set(planned), "a stock is planned in two batches")
            planned += b["planned"]
            self.assertEqual(h.validate(str(i), before), 0, h.offline_log)
            self.assertEqual(h.batch(str(i))["status"], "validated")
            before = out
            if len(set(planned)) >= 2548:
                break
        self.assertEqual(len(planned), 2548)                                       # 2,548 stocks: five batches of 500 and one of 48
        self.assertEqual(i, 6)
        rep = h.report()
        self.assertEqual(rep["states"]["fresh"], 2548)
        self.assertEqual(rep["states"]["not_in_upstox"], 0)                          # with a universe file the five research stocks are not added (research_test_batch.extend)
        self.assertEqual(rep["next"]["eligible"], 0)
        self.assertEqual(h.go(batch="7")["coverage"]["published"], 2548)              # nothing left: the next batch plans nothing and calls nothing
        self.assertEqual(h.requests_this_run, 0)

    def test_each_batch_needs_two_calls_per_new_stock_and_the_report_estimates_it(self):
        h = BatchHarness(self, 1200)
        rep0 = None
        h.go(batch="1", max_stocks=500)
        self.assertEqual(h.requests_this_run, 1000)                                  # profile + key ratios for each of 500 new stocks
        rep0 = json.loads((h.t / "report" / "fundamentals_coverage.json").read_text())
        self.assertEqual(rep0["next"]["eligible"], 700)
        self.assertEqual(rep0["next"]["calls_nominal"], 1400)
        self.assertEqual(rep0["next"]["batches_of_500"], 2)
        self.assertAlmostEqual(rep0["next"]["minutes_nominal"], round(1400 * fu.SECONDS_PER_CALL / 60, 1))


class Gate(unittest.TestCase):
    def test_a_new_batch_cannot_start_until_the_previous_one_is_validated(self):
        h = BatchHarness(self, 1200)
        h.go(batch="1")
        h.go(batch="2")
        self.assertEqual(h.rc, 1)
        self.assertIn("batch 2 cannot start: batch 1 is completed, not validated", h.log)
        self.assertEqual(h.requests_this_run, 0)                                    # refused before any request
        self.assertEqual(h.validate("1"), 0)
        h.go(batch="2")
        self.assertEqual(h.rc, 0, h.log)
        self.assertEqual(len(h.batch("2")["planned"]), 500)

    def test_a_batch_that_failed_validation_blocks_the_next_one_and_cannot_be_rerun(self):
        h = BatchHarness(self, 600)
        h.go(batch="1")
        c = h.cache()
        s = c["batches"]["1"]["planned"][0]
        c["stocks"][s]["last_error"] = "key-ratios: HTTP 500"                       # a failure recorded as a success
        (h.t / "data" / "c.json").write_text(json.dumps(c))
        self.assertEqual(h.validate("1"), 1)
        self.assertEqual(h.batch("1")["status"], "failed_validation")
        self.assertIn("recorded as ok but its last request failed", h.offline_log)
        h.go(batch="2")
        self.assertEqual(h.rc, 1)
        h.go(batch="1")
        self.assertEqual(h.rc, 1)
        self.assertIn("already failed_validation", h.log)
        self.assertEqual(h.accept("1", by="pushp", reason="the failed stock was checked by hand"), 0, h.offline_log)   # only an explicit, recorded acceptance of THIS batch
        a = h.batch("1")["acceptance"]
        self.assertEqual((h.batch("1")["status"], a["by"], a["status_before"], a["validation_result"]), ("accepted", "pushp", "failed_validation", "failed"))
        h.go(batch="2")
        self.assertEqual(h.rc, 0, h.log)

    def test_batch_ids_are_checked(self):
        h = BatchHarness(self, 10)
        h.go(batch="../x")
        self.assertEqual(h.rc, 1)
        self.assertEqual(h.requests_this_run, 0)


class ResumeAndCheckpoints(unittest.TestCase):
    def test_a_batch_cut_short_by_its_call_cap_resumes_without_refetching(self):
        h = BatchHarness(self, 800)
        h.go(batch="1", max_stocks=500, cap=400)                                     # 400 calls = 200 stocks
        b = h.batch("1")
        self.assertEqual(b["status"], "interrupted")
        done = [s for s in b["planned"] if b["outcomes"].get(s) == "ok"]
        self.assertEqual(len(done), 200)
        self.assertEqual(h.validate("1"), 1)                                         # not completed: cannot be validated yet
        self.assertEqual(h.batch("1")["status"], "interrupted")
        asked_before = list(h.provider.requests)
        h.go(batch="1", max_stocks=500, cap=1200)
        self.assertEqual(h.batch("1")["status"], "completed")
        self.assertEqual(h.requests_this_run, 600)                                   # only the 300 unfinished stocks
        isins_done = {h.instruments[s]["isin"] for s in done}
        self.assertFalse(any(any(i in u for i in isins_done) for u in h.provider.requests[len(asked_before):]))
        self.assertEqual(len(h.batch("1")["runs"]), 2)
        self.assertEqual(h.validate("1"), 0, h.offline_log)

    def test_an_interrupted_run_keeps_the_progress_of_its_last_checkpoint(self):
        h = BatchHarness(self, 300)
        real_get = h.provider.get
        def dying(url, timeout=30):
            if len(h.provider.requests) >= 120:                                       # the runner is killed after 120 requests (60 stocks)
                raise KeyboardInterrupt
            return real_get(url, timeout)
        h.provider.get = dying
        with self.assertRaises(KeyboardInterrupt):
            h.go(batch="1", max_stocks=200)
        c = h.cache()
        b = c["batches"]["1"]
        ok = [s for s in b["planned"] if b["outcomes"].get(s) == "ok"]
        self.assertEqual(len(ok), 50)                                                 # the checkpoint after 50 processed stocks (every 25) survived
        self.assertTrue(all(c["stocks"][s]["ratios_fetched"] == START.isoformat() for s in ok))
        h.provider.get = real_get
        h.go(batch="1", max_stocks=200)                                               # the same id resumes
        self.assertEqual(h.rc, 0, h.log)
        self.assertEqual(h.batch("1")["status"], "completed")
        self.assertEqual(h.validate("1"), 0, h.offline_log)


class HonestOutcomes(unittest.TestCase):
    def test_failures_are_never_recorded_as_success_and_back_off(self):
        h = BatchHarness(self, 40, letters="AEF")                                   # A ok, E always 404, F always 500 (retried, each attempt counted)
        out = h.go(batch="1", cap=1000)
        b = h.batch("1")
        oc = {s: b["outcomes"][s] for s in b["planned"]}
        self.assertTrue(all(o == "failed" for s, o in oc.items() if s[0] in "EF"))
        self.assertTrue(all(o == "ok" for s, o in oc.items() if s[0] == "A"))
        c = h.cache()["stocks"]
        for s in oc:
            if s[0] in "EF":
                self.assertTrue(c[s]["last_error"]); self.assertEqual(c[s]["failures"], 1); self.assertNotIn("ratios_fetched", c[s])
        published = {r["symbol"] for r in out["stocks"]}
        self.assertFalse(any(s[0] in "EF" for s in published))                       # never published
        self.assertEqual(h.validate("1"), 1)                                         # 26 of 40 failed: above the default 10% maximum
        self.assertIn("failure rate 65.0% (26 of 40 attempted) is above the maximum of 10.0%", h.offline_log)
        self.assertEqual(h.validate("1", rate=0.7), 0, h.offline_log)                # honest failures within a configured maximum pass (re-validation is allowed)
        rep = h.report()
        self.assertEqual(rep["states"]["failed_backoff"], 26)
        self.assertEqual(sorted(rep["batch"]["failed"]), sorted(s for s in oc if s[0] in "EF"))
        # back-off: 1 day after the first failure, 2 after the second, ...
        e = c[next(s for s in oc if s[0] == "E")]
        self.assertEqual(fu.state_of(e, {"isin": e.get("isin")}, START), "failed_backoff")
        self.assertEqual(fu.state_of(e, {"isin": e.get("isin")}, START + dt.timedelta(days=1)), "failed_due")
        self.assertEqual([fu.retry_days(n) for n in (1, 2, 3, 4, 5, 6, 9)], [1, 2, 4, 8, 16, 30, 30])

    def test_missing_metrics_stay_null_and_are_listed(self):
        h = BatchHarness(self, 30, letters="ABC")
        out = h.go(batch="1")
        rows = {r["symbol"]: r for r in out["stocks"]}
        b = next(r for s, r in rows.items() if s[0] == "B")
        self.assertEqual((b["roce"], b["ev_ebitda"], b["missing"]), (None, None, ["roce", "ev_ebitda"]))
        self.assertFalse(any(s[0] == "C" for s in rows))                             # an empty answer is a failed read: never an invented all-null row
        oc = h.batch("1")["outcomes"]
        self.assertEqual({oc[s] for s in oc if s[0] == "B"}, {"partial"}); self.assertEqual({oc[s] for s in oc if s[0] == "C"}, {"failed"})
        self.assertEqual(h.validate("1"), 1)                                         # 10 of 30 failed: above the default 10%
        self.assertEqual(h.validate("1", rate=0.4), 0, h.offline_log)
        rep = h.report()
        self.assertEqual(rep["quality"], {"complete": 10, "partial": 10, "none": 0})
        self.assertEqual(len(rep["lists"]["metric_missing"]["roce"]), 10)
        self.assertNotIn(0, [r[m] for r in out["stocks"] for m in fu.METRICS if r[m] is not None and r["symbol"][0] == "C"])


class OnlyWhatNeedsWork(unittest.TestCase):
    def test_fresh_stocks_are_never_requested_and_stale_ones_are_refreshed_with_one_call(self):
        h = BatchHarness(self, 100)
        h.go(batch="1"); h.validate("1")
        h.go(batch="2", day=START + dt.timedelta(days=3))
        self.assertEqual(h.requests_this_run, 0)                                     # everything is fresh: nothing planned, nothing asked
        self.assertNotIn("2", h.cache()["batches"])                                  # and no empty batch is created (it could never show a successful update)
        self.assertIn("Batch 2 not created: no stock is eligible", h.log)
        h.go(batch="3", day=START + dt.timedelta(days=7))                            # ratios are 7 days old: one call each (the profile is still fresh)
        self.assertEqual(h.requests_this_run, 100)
        self.assertTrue(all(u.endswith("/key-ratios") for u in h.provider.requests[-100:]))

    def test_invalid_records_are_refetched_and_a_changed_isin_is_held_aside(self):
        h = BatchHarness(self, 20)
        h.go(batch="1"); h.validate("1")
        c = h.cache()
        syms = sorted(c["stocks"])[:3]
        c["stocks"][syms[0]]["ratios"]["pe"] = "abc"                                  # not a number
        c["stocks"][syms[1]]["ratios_fetched"] = "2099-01-01"                         # in the future
        c["stocks"][syms[2]]["isin"] = "INE000000000"                                 # differs from the instrument list
        (h.t / "data" / "c.json").write_text(json.dumps(c))
        held_before = dict(c["stocks"][syms[2]])
        rep = h.report()
        self.assertEqual(rep["states"]["invalid"], 2)
        self.assertEqual(rep["states"]["isin_held"], 1)                               # offline too: the cache ISIN differs from the list's ISIN the last run saw
        out = h.go(batch="2", day=START + dt.timedelta(days=1))
        self.assertEqual(sorted(h.batch("2")["planned"]), syms[:2])                   # the two invalid ones; the changed ISIN is NOT refetched under the new ISIN
        self.assertEqual(h.requests_this_run, 4)
        rows = {r["symbol"]: r for r in out["stocks"]}
        self.assertEqual(rows[syms[0]]["pe"], 12.5)
        self.assertNotIn(syms[2], rows)                                               # not published while its identity is in doubt ...
        held = h.cache()["stocks"][syms[2]]["held_isin_change"]
        self.assertEqual(held["record"], {k: held_before[k] for k in fu.HELD_KEYS if k in held_before})   # ... and kept unchanged, nothing deleted
        self.assertEqual((held["isin"], held["listed_isin"]), ("INE000000000", h.instruments[syms[2]]["isin"]))
        self.assertEqual(h.validate("2"), 0, h.offline_log)                           # held aside is reported, not a disappearance
        self.assertIn("is held aside", h.offline_log)

    def test_the_daily_run_keeps_its_budget_and_uses_the_same_eligibility(self):
        h = BatchHarness(self, 400)
        h.go(batch=None, max_stocks=fu.UNLIMITED, cap=150)                            # no batch: the scheduled run, 150 calls
        self.assertEqual(h.requests_this_run, 150)
        self.assertNotIn("batches", h.cache())


class ValidationCatchesProblems(unittest.TestCase):
    def setUp(self):
        self.h = BatchHarness(self, 60)
        self.prev = self.h.go(batch="1")

    def test_a_published_row_that_does_not_match_the_cache(self):
        f = self.h.t / "out" / "fundamentals.json"
        d = json.loads(f.read_text()); d["stocks"][0]["pe"] = 999.0; f.write_text(json.dumps(d))
        self.assertEqual(self.h.validate("1"), 1)
        self.assertIn("does not match the cache", self.h.offline_log)

    def test_a_stock_that_disappeared_from_the_published_file(self):
        prev = json.loads(json.dumps(self.prev)); prev["stocks"].append(dict(prev["stocks"][0], symbol="GONE"))
        self.assertEqual(self.h.validate("1", previous=prev), 1)
        self.assertIn("published before are gone: GONE", self.h.offline_log)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.w = yaml.safe_load((HERE / ".github" / "workflows" / "fundamentals_batch.yml").read_text(encoding="utf-8"))
        self.steps = {s.get("name"): s for s in self.w["jobs"]["batch"]["steps"]}

    def test_manual_only_serialized_and_never_publishing(self):
        self.assertEqual(list(self.w["on"]), ["workflow_dispatch"])
        self.assertEqual(self.w["concurrency"], {"group": "stocklens-ledger", "cancel-in-progress": False})
        self.assertEqual(self.w["permissions"], {"contents": "read"})
        text = (HERE / ".github" / "workflows" / "fundamentals_batch.yml").read_text(encoding="utf-8")
        for bad in ("pages", "deploy", "git push", "contents: write"):
            self.assertNotIn(bad, text)
        self.assertEqual(self.w["on"]["workflow_dispatch"]["inputs"]["mode"]["default"], "audit")

    def test_the_audit_has_no_token_and_the_run_is_validated_and_always_saved(self):
        self.assertNotIn("env", {k: v for k, v in self.steps["Coverage report (no API call)"].items() if k == "env" and "UPSTOX_ANALYTICS_TOKEN" in v})
        self.assertIn("validate --batch", self.steps["Validate the batch"]["run"])
        self.assertTrue(self.steps["Save the data"]["if"].startswith("always()"))
        self.assertIn('[ "$MAX_STOCKS" -le 600 ]', self.steps["Check the inputs"]["run"])
        self.assertIn('[ "$MAX_CALLS" -le 1500 ]', self.steps["Check the inputs"]["run"])
        self.assertIn("exit 1", self.steps["Result"]["run"])

    def test_the_scheduled_budget_is_unchanged(self):
        upd = (HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        self.assertIn("FUNDAMENTALS_MAX_CALLS: ${{ inputs.fundamentals_calls || '150' }}", upd)
        self.assertNotIn("FUNDAMENTALS_MAX_STOCKS", upd)


if __name__ == "__main__":
    unittest.main()
