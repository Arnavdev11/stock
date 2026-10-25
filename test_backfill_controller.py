"""
Tests for backfill_controller.py: the planner/verifier of bounded backfill runs. SYNTHETIC data only. No network, no provider, no token, no workflow is run;
the controller fetches nothing and starts nothing, and these tests prove it (sockets and subprocesses are blocked while it runs).
"""
import ast
import contextlib
import copy
import datetime as dt
import io
import json
import shutil
import socket
import unittest
from pathlib import Path
from unittest import mock

import backfill_controller as K
import financial_history_updater as FH
import historical_batch as HB
import test_backfill_plan as TB
import test_coverage_report as T

HERE = Path(__file__).resolve().parent
AS_OF = T.AS_OF                                   # 2026-10-23: the last complete day is 2026-10-22; a candle on/after 2026-10-18 is fresh
WORKFLOW = HERE / ".github" / "workflows" / "backfill_next.yml"


class Base(TB.Case):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(HB, "load_directory", side_effect=lambda path: list(self.symbols))
        p.start()
        self.addCleanup(p.stop)

    def shard(self, i, dates, root=None):
        s = T.sym(i)
        T.write((root or self.data) / "historical" / "shards" / (s + ".json"), {"stocks": {s: {"candles": [T.candle(d) for d in dates]}}})

    def attempts(self, failed, root=None):
        T.write((root or self.data) / "historical" / "attempts.json", {"kind": "historical_attempts", "updated": "2026-10-22", "failed": failed})

    def ledger(self, entries, root=None):
        T.write((root or self.data) / "financial_history_ledger.json", {"schema": 1, "stocks": {T.sym(i): e for i, e in entries.items()}, "updated": "2026-10-22"})

    def fh(self, i, years=(2025,), inc="2026-10-20", cf="2026-10-21", error=None):
        e = T.fh_entry(i, [T.year(y, revenue=1.0) for y in years])
        e["fetch_state"]["income"].update(fetched=inc, error=error)
        e["fetch_state"]["cash_flow"].update(fetched=cf)
        return e

    def nxt(self, dataset="historical_candles", **kw):
        kw.setdefault("batch_size", 500)
        return K.next_plan(self.data, dataset, AS_OF, **kw)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = K.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def two_copies(self):
        """before/after snapshots of the data branch, so a run can be 'applied' to the after copy."""
        before, after = self.root / "before", self.root / "after"
        shutil.copytree(self.data, before)
        shutil.copytree(self.data, after)
        return before, after


class NextCandles(Base):
    def test_cohort_is_never_more_than_500_and_follows_the_budget(self):
        self.make(900)
        p = self.nxt(max_calls=150)
        self.assertEqual(p["status"], "ready")
        self.assertLessEqual(p["cohort_size"], K.MAX_COHORT)
        self.assertEqual(p["cohort"], self.symbols[:p["cohort_size"]])                       # never fetched, in directory order
        self.assertLessEqual(p["run"]["estimated_calls"], 150)
        self.assertEqual(p["run"]["workflow"], "historical.yml")
        self.assertEqual(p["run"]["inputs"]["max_calls"], "150")

    def test_a_large_budget_still_cannot_make_more_than_500(self):
        self.make(900)
        p = self.nxt(max_calls=HB.CALL_CEILING)
        self.assertLessEqual(p["cohort_size"], K.MAX_COHORT)
        self.assertEqual(p["cohort_size"], 449)                                          # the 2,700-call ceiling at 6 calls per first fill, less the benchmark: the real limit
        self.assertLessEqual(p["run"]["estimated_calls"], int(HB.CALL_CEILING * HB.HEADROOM))
        self.assertLessEqual(int(p["run"]["inputs"]["batch_size"]), K.MAX_COHORT)

    def test_bad_sizes_and_budgets_are_refused(self):
        self.make(10)
        for kw in ({"batch_size": 0}, {"batch_size": 501}, {"batch_size": True}, {"max_calls": 0}, {"max_calls": HB.CALL_CEILING + 1}, {"max_calls": True}):
            with self.subTest(kw=kw), self.assertRaises(K.ControllerError):
                self.nxt(**kw)

    def test_priority_symbols_are_sent_first_and_only_when_they_need_work(self):
        self.make(300)
        self.shard(250, ["2026-10-21"])                                              # fresh: must be skipped
        pf = self.root / "prio.txt"
        pf.write_text("S0200\nS0250\nS0100\n", encoding="utf-8")
        p = self.nxt(priority_file=pf, max_calls=150)
        self.assertEqual(p["cohort"][:2], ["S0200", "S0100"])
        self.assertEqual(p["run"]["inputs"]["symbols"], "S0200,S0100")
        self.assertNotIn("S0250", p["cohort"])
        self.assertEqual(p["priority"], {"supplied": 3, "included": 2, "skipped": {"not_in_directory": 0, "damaged": 0, "fresh": 1, "cooling_down": 0}})

    def test_a_priority_stock_that_is_cooling_down_is_not_forced_into_the_run(self):
        self.make(10)
        self.attempts({T.sym(5): {"last": "2026-10-22", "count": 1}})
        pf = self.root / "prio.txt"
        pf.write_text("S0005\nS0007\n", encoding="utf-8")
        p = self.nxt(priority_file=pf, max_calls=150)
        self.assertNotIn("S0005", p["cohort"])
        self.assertEqual(p["cohort"][0], "S0007")
        self.assertEqual(p["priority"]["skipped"]["cooling_down"], 1)

    def test_the_priority_part_never_exceeds_the_budget(self):
        self.make(300)
        pf = self.root / "prio.txt"
        pf.write_text("".join("S%04d\n" % i for i in range(100, 200)), encoding="utf-8")
        p = self.nxt(priority_file=pf, max_calls=60)
        self.assertLessEqual(p["run"]["estimated_calls"], 60)
        self.assertGreaterEqual(int(p["run"]["inputs"]["batch_size"]), 1)

    def test_up_to_date_stocks_are_not_planned_and_nothing_left_stops_cleanly(self):
        self.make(5)
        for i in range(5):
            self.shard(i, ["2026-10-21"])
        p = self.nxt()
        self.assertEqual((p["status"], p["cohort"], p["run"]), ("nothing_to_do", [], None))
        self.assertTrue(p["stop_reason"])

    def test_recently_failed_stocks_wait_in_cooldown_and_the_wake_date_is_reported(self):
        self.make(4)
        for i in (0, 1):
            self.shard(i, ["2026-10-21"])
        self.attempts({T.sym(2): {"last": "2026-10-22", "count": 1}, T.sym(3): {"last": "2026-10-21", "count": 2}})
        p = self.nxt()
        self.assertEqual((p["status"], p["cohort"]), ("waiting_cooldown", []))
        self.assertIn("2026-10-24", p["stop_reason"])
        later = K.next_plan(self.data, "historical_candles", dt.date(2026, 10, 30), 500)
        self.assertTrue({T.sym(2), T.sym(3)} <= set(later["cohort"]))

    def test_a_stock_that_keeps_failing_is_listed_for_review(self):
        self.make(3)
        self.assertEqual(K.REVIEW_AFTER, 3)
        self.attempts({T.sym(1): {"last": "2026-10-01", "count": 2}})
        self.assertEqual(self.nxt()["queue"]["needs_review_after_3_failed_attempts"], [])
        self.attempts({T.sym(1): {"last": "2026-10-01", "count": 3}})
        p = self.nxt()
        self.assertEqual(p["queue"]["needs_review_after_%d_failed_attempts" % K.REVIEW_AFTER], [T.sym(1)])

    def test_a_damaged_saved_file_is_skipped_and_reported_not_overwritten(self):
        self.make(3)
        (self.data / "historical" / "shards").mkdir(parents=True)
        (self.data / "historical" / "shards" / "S0001.json").write_text("{broken", encoding="utf-8")
        p = self.nxt()
        self.assertNotIn(T.sym(1), p["cohort"])
        self.assertEqual(p["queue"]["damaged"], 1)

    def test_estimate_of_runs_left_comes_from_the_measured_gap(self):
        self.make(60)
        p = self.nxt(max_calls=150)
        self.assertEqual(p["queue"]["calls_still_needed_estimate"], 60 * 6)
        self.assertEqual(p["queue"]["runs_still_needed_estimate"], -(-60 * 6 // p["run"]["planned_call_limit"]))

    def test_the_plan_is_reproducible(self):
        self.make(80)
        self.assertEqual(K.render_json(self.nxt()), K.render_json(self.nxt()))


class NextFinancialHistory(Base):
    def test_due_and_missing_stocks_are_planned_and_fresh_ones_are_not(self):
        self.make(6)
        self.ledger({0: self.fh(0), 1: self.fh(1, inc="2026-08-01", cf="2026-08-01"), 2: self.fh(2)})
        p = self.nxt("financial_history", max_calls=120)
        self.assertEqual(p["status"], "ready")
        self.assertEqual(p["cohort"], [T.sym(1), T.sym(3), T.sym(4), T.sym(5)])           # due (universe order), then never fetched
        self.assertEqual(p["run"]["workflow"], "financial_history.yml")
        self.assertEqual(p["run"]["inputs"]["symbols"], ",".join(p["cohort"]))
        self.assertEqual(p["run"]["inputs"]["max_calls"], "120")

    def test_priority_stocks_come_first_and_the_cohort_is_capped(self):
        self.make(700)
        self.ledger({})
        pf = self.root / "prio.txt"
        pf.write_text("S0650\nS0001\n", encoding="utf-8")
        p = self.nxt("financial_history", priority_file=pf)
        self.assertEqual(p["cohort"][:3], ["S0650", "S0001", "S0000"])
        self.assertEqual(p["cohort_size"], K.MAX_COHORT)

    def test_failed_records_are_deferred_behind_the_others_and_retried_only_when_alone(self):
        self.make(4)
        self.ledger({0: self.fh(0, inc="2026-08-01", cf="2026-08-01", error="HTTP 500"), 1: self.fh(1)})
        p = self.nxt("financial_history")
        self.assertEqual(p["cohort"], [T.sym(2), T.sym(3)])
        self.assertEqual(p["queue"]["recorded_error_deferred"], 1)
        self.ledger({0: self.fh(0, inc="2026-08-01", cf="2026-08-01", error="HTTP 500"), 1: self.fh(1), 2: self.fh(2), 3: self.fh(3)})
        alone = self.nxt("financial_history")
        self.assertEqual((alone["status"], alone["cohort"], alone["queue"]["retry_pass"]), ("ready", [T.sym(0)], True))

    def test_everything_fresh_stops_cleanly(self):
        self.make(3)
        self.ledger({i: self.fh(i) for i in range(3)})
        p = self.nxt("financial_history")
        self.assertEqual((p["status"], p["cohort"]), ("nothing_to_do", []))

    def test_a_missing_or_broken_ledger_is_a_refusal_never_nothing_to_do(self):
        self.make(3)
        with self.assertRaises(K.ControllerError):
            self.nxt("financial_history")
        (self.data / "financial_history_ledger.json").write_text("[1]", encoding="utf-8")
        with self.assertRaises(K.ControllerError):
            self.nxt("financial_history")

    def test_the_default_budget_is_the_updaters_own_and_cannot_be_raised_past_the_cap(self):
        self.make(3)
        self.ledger({})
        self.assertEqual(FH.DEFAULT_MAX_CALLS, K.FH_DEFAULT_CALLS)
        self.assertEqual(self.nxt("financial_history")["max_calls"], FH.DEFAULT_MAX_CALLS)
        with self.assertRaises(K.ControllerError):
            self.nxt("financial_history", max_calls=K.FH_CALL_CEILING + 1)


class NotAutomatic(Base):
    def test_cache_only_datasets_are_refused_with_the_reason_and_no_guess(self):
        self.make(5)
        for ds in K.MANUAL_ONLY:
            p = self.nxt(ds)
            with self.subTest(ds=ds):
                self.assertEqual((p["status"], p["automatic_continuation"], p["cohort"], p["run"]), ("not_automatic", False, [], None))
                self.assertNotIn("coverage", p)
                self.assertTrue(p["stop_reason"])

    def test_an_unknown_dataset_is_refused(self):
        self.make(5)
        with self.assertRaises(K.ControllerError):
            self.nxt("nope")

    def test_pause_stops_everything_and_plans_nothing(self):
        self.make(50)
        p = self.nxt(paused=True)
        self.assertEqual((p["status"], p["cohort"], p["run"]), ("paused", [], None))
        rc, out, err = self.cli("next", "--data-branch", str(self.data), "--dataset", "historical_candles", "--as-of", "2026-10-23", "--out", str(self.out), "--dry-run")
        self.assertEqual(rc, 0)
        self.assertNotIn("paused", out.split("Status")[1][:20])
        with mock.patch.dict("os.environ", {"BACKFILL_PAUSED": "true"}):
            rc, out, err = self.cli("next", "--data-branch", str(self.data), "--dataset", "historical_candles", "--as-of", "2026-10-23", "--out", str(self.out), "--dry-run")
        self.assertIn("Status: paused", out)


class Verify(Base):
    def plan_for(self, dataset="historical_candles", **kw):
        plan = self.nxt(dataset, **kw)
        pf = self.root / ("plan_%s.json" % dataset)
        pf.write_text(K.render_json(plan), encoding="utf-8")
        return plan, pf

    def test_candles_are_classified_and_the_run_passes(self):
        self.make(6)
        for i in (1, 2, 3):
            self.shard(i, ["2026-09-01", "2026-09-02"])
        self.attempts({T.sym(4): {"last": "2026-10-20", "count": 1}})
        plan = self.nxt()
        plan["cohort"] = [T.sym(i) for i in range(6)]
        before, after = self.two_copies()
        self.shard(0, ["2026-10-21"], after)                                            # new stock: improved
        self.shard(1, ["2026-09-01", "2026-09-02", "2026-10-21"], after)                # gained a later candle: refreshed
        self.attempts({T.sym(4): {"last": "2026-10-23", "count": 2}, T.sym(5): {"last": "2026-10-23", "count": 1}}, after)
        r = K.verify_run(before, after, plan, AS_OF, 120)
        self.assertEqual(r["verification"], "passed")
        per = r["per_stock"]
        self.assertEqual((per["S0000"], per["S0001"], per["S0002"], per["S0004"], per["S0005"]), ("improved", "refreshed", "not_reached", "retryable_failed", "retryable_failed"))
        self.assertEqual(r["calls_used"], 120)
        self.assertEqual(r["valid_gained"], 1)
        self.assertIn(r["next"]["status"], ("ready", "waiting_cooldown"))
        self.assertFalse(r["completion"]["platform_complete"])

    def test_a_stock_that_failed_too_often_is_flagged_for_review(self):
        self.make(3)
        plan = self.nxt()
        before, after = self.two_copies()
        self.attempts({T.sym(1): {"last": "2026-10-23", "count": K.REVIEW_AFTER}}, after)
        self.assertEqual(K.verify_run(before, after, plan, AS_OF)["per_stock"]["S0001"], "needs_review")

    def test_lost_candles_fail_verification_and_block_the_next_step(self):
        self.make(3)
        for i in range(3):
            self.shard(i, ["2026-10-01", "2026-10-02", "2026-10-03"])
        plan = self.nxt()
        plan["cohort"] = self.symbols[:]
        before, after = self.two_copies()
        self.shard(1, ["2026-10-03"], after)                                           # history shortened
        (after / "historical" / "shards" / "S0002.json").write_text("{x", encoding="utf-8")   # damaged
        r = K.verify_run(before, after, plan, AS_OF)
        self.assertEqual(r["verification"], "failed")
        self.assertEqual((r["per_stock"]["S0001"], r["per_stock"]["S0002"]), ("regressed", "regressed"))
        self.assertIsNone(r["next"])
        self.assertIn("BLOCKED", r["publish_or_continue"])
        pf = self.root / "plan.json"
        pf.write_text(K.render_json(plan), encoding="utf-8")
        rc, out, err = self.cli("verify", "--before", str(before), "--after", str(after), "--plan", str(pf), "--as-of", "2026-10-23", "--out", str(self.out))
        self.assertEqual(rc, 1)

    def test_financial_history_new_years_are_improved_and_lost_years_are_regressions(self):
        self.make(4)
        self.ledger({0: self.fh(0, years=(2024, 2025)), 1: self.fh(1), 2: self.fh(2, inc="2026-08-01", cf="2026-08-01")})
        plan = self.nxt("financial_history")
        plan["cohort"] = self.symbols[:]
        before, after = self.two_copies()
        self.ledger({0: self.fh(0, years=(2024, 2025)), 1: self.fh(1, years=(2024, 2025)), 2: self.fh(2), 3: self.fh(3)}, after)
        r = K.verify_run(before, after, plan, AS_OF)
        self.assertEqual(r["verification"], "passed", r["problems"])
        self.assertEqual([r["per_stock"][s] for s in self.symbols], ["not_reached", "improved", "refreshed", "improved"])
        self.ledger({0: self.fh(0, years=(2025,)), 1: self.fh(1, years=(2024, 2025)), 2: self.fh(2), 3: self.fh(3)}, after)      # 2024 vanished from stock 0
        bad = K.verify_run(before, after, plan, AS_OF)
        self.assertEqual(bad["verification"], "failed")
        self.assertEqual(bad["per_stock"]["S0000"], "regressed")

    def test_a_revised_value_is_not_a_regression_but_a_value_turning_null_is(self):
        self.make(2)
        self.ledger({0: self.fh(0), 1: self.fh(1)})
        plan = self.nxt("financial_history")
        plan["cohort"] = self.symbols[:]
        before, after = self.two_copies()
        revised = self.fh(0)
        revised["years"][0]["values"]["revenue"] = 2.0
        revised["years"][0]["revisions"] = [{"values": {"revenue": 1.0}, "superseded": "2026-10-22"}]
        self.ledger({0: revised, 1: self.fh(1)}, after)
        self.assertEqual(K.verify_run(before, after, plan, AS_OF)["verification"], "passed")
        nulled = self.fh(0)
        nulled["years"][0]["values"]["revenue"] = None
        self.ledger({0: nulled, 1: self.fh(1)}, after)
        self.assertEqual(K.verify_run(before, after, plan, AS_OF)["verification"], "failed")

    def test_a_forged_or_foreign_plan_is_refused(self):
        self.make(3)
        before, after = self.two_copies()
        good = self.nxt()
        good["cohort"] = self.symbols[:]
        mutations = [lambda p: p.update(kind="x"), lambda p: p.update(schema_version=9), lambda p: p.update(dataset="fundamentals"), lambda p: p.update(cohort=[]),
                     lambda p: p.update(cohort=["S%04d" % i for i in range(501)]), lambda p: p.update(cohort=["../x"]), lambda p: p.update(cohort=["S0001", "S0001"]),
                     lambda p: p.update(cohort="S0001")]
        for i, m in enumerate(mutations):
            bad = copy.deepcopy(good)
            m(bad)
            with self.subTest(i=i), self.assertRaises(K.ControllerError):
                K.verify_run(before, after, bad, AS_OF)
        with self.assertRaises(K.ControllerError):
            K.verify_run(before, after, good, AS_OF, calls_used=-1)
        self.assertIsNone(K.verify_run(before, after, good, AS_OF)["calls_used"])
        self.assertIn("not recorded", K.verify_run(before, after, good, AS_OF)["calls_used_note"])

    def test_verification_is_idempotent_and_changes_neither_snapshot(self):
        self.make(3)
        plan = self.nxt()
        before, after = self.two_copies()
        snap = self.tree(before), self.tree(after)
        a = K.render_json(K.verify_run(before, after, plan, AS_OF))
        b = K.render_json(K.verify_run(before, after, plan, AS_OF))
        self.assertEqual(a, b)
        self.assertEqual((self.tree(before), self.tree(after)), snap)



class VerifyScope(Base):
    """What the review found missing: verification must look at every saved stock, say what was really processed, and bind the run to exact snapshots."""

    def cohort_plan(self, symbols, dataset="historical_candles", **extra):
        plan = self.nxt(dataset)
        plan["cohort"] = list(symbols)
        plan.update(extra)
        return plan

    def test_a_loss_outside_the_cohort_fails_verification(self):
        self.make(4)
        for i in range(4):
            self.shard(i, ["2026-10-01", "2026-10-02", "2026-10-03"])
        plan = self.cohort_plan(["S0000"])
        before, after = self.two_copies()
        self.shard(2, ["2026-10-03"], after)                                           # a stock NOT in the cohort lost history
        r = K.verify_run(before, after, plan, AS_OF)
        self.assertEqual(r["verification"], "failed")
        self.assertTrue(any(p.startswith("S0002") for p in r["problems"]))
        (after / "historical" / "shards" / "S0003.json").unlink()                      # and one whose file vanished
        self.assertTrue(any(p.startswith("S0003") for p in K.verify_run(before, after, plan, AS_OF)["problems"]))

    def test_a_lost_or_shortened_benchmark_fails_verification(self):
        self.make(2)
        self.shard(0, ["2026-10-20"])
        bench = lambda dates: {"stocks": {}, "benchmark": {"candles": [T.candle(d) for d in dates]}}
        T.write(self.data / "historical" / "historical.json", bench(["2026-10-19", "2026-10-20"]))
        plan = self.cohort_plan(["S0000"])
        before, after = self.two_copies()
        self.assertEqual(K.verify_run(before, after, plan, AS_OF)["verification"], "passed")
        T.write(after / "historical" / "historical.json", bench(["2026-10-19"]))
        self.assertEqual(K.verify_run(before, after, plan, AS_OF)["verification"], "failed")
        T.write(after / "historical" / "historical.json", {"stocks": {}})
        self.assertEqual(K.verify_run(before, after, plan, AS_OF)["verification"], "failed")

    def test_stocks_changed_outside_the_printed_cohort_are_reported_and_unreached_ones_are_not_failures(self):
        self.make(5)
        plan = self.cohort_plan(["S0000", "S0001"])
        before, after = self.two_copies()
        self.shard(0, ["2026-10-21"], after)                                           # in the cohort: processed
        self.shard(3, ["2026-10-21"], after)                                           # the workflow re-planned and also did this one
        r = K.verify_run(before, after, plan, AS_OF)
        self.assertEqual(r["per_stock"], {"S0000": "improved", "S0001": "not_reached"})
        self.assertEqual(r["processed"]["cohort_stocks_with_evidence_of_processing"], 1)
        self.assertEqual(r["processed"]["cohort_stocks_not_reached"], 1)
        self.assertEqual((r["processed"]["changed_outside_the_planned_cohort"], r["processed"]["changed_outside_examples"]), (1, ["S0003"]))
        self.assertEqual(r["failures_in_cohort"], 0)

    def test_financial_history_changes_outside_the_cohort_are_seen_and_unreached_stocks_are_not_failures(self):
        self.make(4)
        self.ledger({0: self.fh(0, inc="2026-08-01", cf="2026-08-01"), 1: self.fh(1, inc="2026-08-01", cf="2026-08-01")})
        plan = self.cohort_plan(["S0000", "S0001", "S0002"], "financial_history")
        before, after = self.two_copies()
        self.ledger({0: self.fh(0), 1: self.fh(1, inc="2026-08-01", cf="2026-08-01"), 3: self.fh(3)}, after)
        r = K.verify_run(before, after, plan, AS_OF)
        self.assertEqual(r["per_stock"], {"S0000": "refreshed", "S0001": "not_reached", "S0002": "not_reached"})
        self.assertEqual(r["processed"]["changed_outside_examples"], ["S0003"])
        self.assertEqual(r["failures_in_cohort"], 0)

    def test_a_snapshot_sha_must_match_the_plan_and_be_a_real_sha(self):
        self.make(3)
        sha, other = "a" * 40, "b" * 40
        plan = K.next_plan(self.data, "historical_candles", AS_OF, 500, None, None, False, None, sha)
        self.assertEqual(plan["data_branch_sha"], sha)
        before, after = self.two_copies()
        self.assertEqual(K.verify_run(before, after, plan, AS_OF, before_sha=sha, after_sha=other)["data_branch_sha_before"], sha)
        with self.assertRaises(K.ControllerError):
            K.verify_run(before, after, plan, AS_OF, before_sha=other)
        for bad in ("abc", "A" * 40, "g" * 40, "a" * 41, 12):
            with self.subTest(bad=bad):
                with self.assertRaises(K.ControllerError):
                    K.next_plan(self.data, "historical_candles", AS_OF, 500, None, None, False, None, bad)

    def test_pause_also_applies_to_the_next_step_reported_by_verify(self):
        self.make(3)
        plan = self.nxt()
        before, after = self.two_copies()
        self.assertEqual(K.verify_run(before, after, plan, AS_OF)["next"]["status"], "ready")
        self.assertEqual(K.verify_run(before, after, plan, AS_OF, paused=True)["next"]["status"], "paused")


class PriorityInput(Base):
    def test_a_valid_list_is_planned_first_and_blank_entries_are_ignored(self):
        self.make(300)
        p = K.next_plan(self.data, "historical_candles", AS_OF, 500, 150, None, False, "S0200, S0100,,S0150\n")
        self.assertEqual(p["cohort"][:3], ["S0200", "S0100", "S0150"])
        self.assertEqual(p["run"]["inputs"]["symbols"], "S0200,S0100,S0150")

    def test_every_bad_entry_refuses_the_whole_plan_by_position_without_echoing_the_text(self):
        self.make(20)
        bad = {"lower": "S0001,s0002", "unknown": "S0001,ZZNOPE", "repeat": "S0001,S0002,S0001", "option": "S0001,--max-calls", "comment": "S0001,#S0002",
               "shell": "S0001;rm", "dollar": "S0001,$(id)", "backtick": "S0001,`id`", "long": "X" * 31, "unicode": "S0001,\u0661"}
        for name, text in bad.items():
            with self.subTest(name=name):
                with self.assertRaises(K.ControllerError) as cm:
                    K.next_plan(self.data, "historical_candles", AS_OF, 500, None, None, False, text)
                self.assertRegex(str(cm.exception), r"entry \d+")
                for piece in ("ZZNOPE", "--max-calls", ";rm", "$(id)", "`id`", "#S0002", "s0002"):
                    if piece in text:
                        self.assertNotIn(piece, str(cm.exception))

    def test_more_than_500_entries_and_giving_both_a_file_and_a_list_are_refused(self):
        self.make(600)
        with self.assertRaises(K.ControllerError) as cm:
            K.next_plan(self.data, "historical_candles", AS_OF, 500, None, None, False, ",".join("S%04d" % i for i in range(501)))
        self.assertIn("more than 500", str(cm.exception))
        K.next_plan(self.data, "historical_candles", AS_OF, 500, None, None, False, ",".join("S%04d" % i for i in range(500)))
        pf = self.root / "p.txt"
        pf.write_text("S0001\n", encoding="utf-8")
        with self.assertRaises(K.ControllerError):
            K.next_plan(self.data, "historical_candles", AS_OF, 500, None, pf, False, "S0002")

    def test_the_cli_takes_the_list_and_a_bad_one_exits_2(self):
        self.make(30)
        base = ["next", "--data-branch", str(self.data), "--dataset", "financial_history", "--as-of", "2026-10-23", "--dry-run", "--out", str(self.out)]
        self.ledger({})
        rc, out, err = self.cli(*base, "--priority-symbols", "S0020,S0010")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.cli(*base, "--priority-symbols", "S0020,SECRET-TEXT")
        self.assertEqual(rc, 2)
        self.assertNotIn("SECRET", out + err)
        rc, out, err = self.cli(*base, "--data-sha", "nope")
        self.assertEqual(rc, 2)


class InterfaceParity(Base):
    """The printed workflow and input names must be the real production workflows' own."""

    @staticmethod
    def dispatch_inputs(name):
        import yaml
        doc = yaml.safe_load((HERE / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        return doc, set(((doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]))

    def test_the_printed_workflow_file_and_input_names_exist_in_the_real_workflows(self):
        self.make(30)
        self.ledger({})
        pri = self.root / "p.txt"
        pri.write_text("S0020\n", encoding="utf-8")
        for dataset in K.AUTOMATIC:
            run = self.nxt(dataset, priority_file=pri)["run"]
            self.assertTrue((HERE / ".github" / "workflows" / run["workflow"]).is_file(), run["workflow"])
            _, real = self.dispatch_inputs(run["workflow"])
            self.assertTrue(set(run["inputs"]) <= real, (dataset, set(run["inputs"]) - real))
            self.assertEqual(run["workflow"], K.AUTOMATIC[dataset]["workflow"])

    def test_the_ceilings_and_runtimes_fit_the_real_job_timeouts(self):
        import historical_batch as HBt
        hdoc, _ = self.dispatch_inputs("historical.yml")
        fdoc, _ = self.dispatch_inputs("financial_history.yml")
        self.assertLessEqual(HBt.MAX_MINUTES, hdoc["jobs"]["historical"]["timeout-minutes"])
        self.assertLessEqual(HBt.CALL_CEILING * HBt.SECONDS_PER_CALL / 60.0, HBt.MAX_MINUTES)
        from backfill_plan import SECONDS_PER_CALL
        worst_retry_minutes = K.FH_CALL_CEILING * SECONDS_PER_CALL * 3 / 60.0          # every call retried three times
        self.assertLessEqual(worst_retry_minutes, fdoc["jobs"]["history"]["timeout-minutes"])
        for dataset, spec in K.AUTOMATIC.items():
            with self.assertRaises(K.ControllerError):
                self.nxt(dataset, max_calls=spec["call_ceiling"] + 1)
            self.make(3)
            self.ledger({})
            self.assertEqual(self.nxt(dataset, max_calls=spec["call_ceiling"])["max_calls"], spec["call_ceiling"])



class SafeOutput(Base):
    def test_files_go_only_to_a_separate_folder_and_dry_run_writes_nothing(self):
        self.make(30)
        before = self.tree(self.data)
        rc, out, err = self.cli("next", "--data-branch", str(self.data), "--dataset", "historical_candles", "--as-of", "2026-10-23", "--out", str(self.out), "--dry-run")
        self.assertEqual((rc, err), (0, ""))
        self.assertFalse(self.out.exists())
        rc, out, err = self.cli("next", "--data-branch", str(self.data), "--dataset", "historical_candles", "--as-of", "2026-10-23", "--out", str(self.out))
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["backfill_next.json", "backfill_next.md"])
        self.assertEqual(self.tree(self.data), before)
        doc = json.loads((self.out / "backfill_next.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["tool"], {"name": "backfill_controller.py", "reads_only": True, "provider_calls": 0, "dispatches_workflows": False})

    def test_unsafe_output_locations_and_bad_arguments_are_refused(self):
        self.make(10)
        base = ["next", "--data-branch", str(self.data), "--dataset", "historical_candles", "--as-of", "2026-10-23"]
        for out in (self.data, self.data / "sub"):
            rc, o, e = self.cli(*base, "--out", str(out))
            self.assertEqual(rc, 2, out)
        for key, bad in (("--as-of", "SECRET-TEXT"), ("--batch-size", "501"), ("--max-calls", "0"), ("--dataset", "SECRET-TEXT")):
            args = base[:]
            if key in args:
                args[args.index(key) + 1] = bad
            else:
                args += [key, bad]
            extra = [key, bad]
            rc, o, e = self.cli(*args, "--out", str(self.out))
            with self.subTest(extra=extra):
                self.assertEqual(rc, 2)
                self.assertNotIn("SECRET", o + e)
        self.out.mkdir()
        victim = self.root / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        (self.out / "backfill_next.json").symlink_to(victim)
        rc, o, e = self.cli(*base, "--out", str(self.out))
        self.assertEqual((rc, victim.read_text(encoding="utf-8")), (2, "keep"))


class NothingIsFetchedOrStarted(Base):
    def test_a_full_next_and_verify_make_no_connection_and_start_no_process(self):
        self.make(8)
        self.ledger({0: self.fh(0)})
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), mock.patch("subprocess.run", side_effect=AssertionError("process")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process")), mock.patch("socket.create_connection", side_effect=AssertionError("network used")):
            for ds in ("historical_candles", "financial_history", "fundamentals"):
                plan = self.nxt(ds)
            plan = self.nxt()
            before, after = self.two_copies()
            K.verify_run(before, after, plan, AS_OF)

    def test_the_controller_has_no_network_or_process_imports_of_its_own(self):
        tree = ast.parse((HERE / "backfill_controller.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported & {"requests", "socket", "subprocess", "urllib", "http", "ssl", "asyncio"}, set())
        self.assertEqual(imported - {"argparse", "datetime", "json", "math", "os", "re", "sys", "pathlib", "backfill_plan", "coverage_report", "historical_batch", "ledger_storage", "financial_history_updater"}, set())

    def test_it_never_writes_to_the_data_it_reads_and_the_cost_model_is_the_updaters(self):
        self.assertEqual(K.AUTOMATIC["historical_candles"]["default_calls"], HB.DEFAULT_BUDGET)
        self.assertEqual(K.AUTOMATIC["historical_candles"]["call_ceiling"], HB.CALL_CEILING)
        self.assertLessEqual(K.MAX_COHORT, HB.MAX_BATCH)
        self.assertEqual(set(K.AUTOMATIC) & set(K.MANUAL_ONLY), set())


class WorkflowSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import yaml
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)
        cls.on = cls.doc.get("on", cls.doc.get(True))

    def test_it_can_only_be_started_by_hand_and_has_no_schedule(self):
        self.assertEqual(set(self.on), {"workflow_dispatch"})
        for word in ("schedule", "cron", "push:", "pull_request", "workflow_run", "workflow_call"):
            self.assertNotIn(word, "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#")), word)

    def test_it_is_read_only_has_no_secret_and_dispatches_nothing(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read"})
        for job in self.doc["jobs"].values():
            self.assertNotIn("permissions", job)
        body = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))
        for word in ("secrets.", "UPSTOX", "actions: write", "contents: write", "gh workflow", "gh api", "/dispatches", "actions/cache", "git push", "git commit", "workflow_dispatch(" ):
            self.assertNotIn(word, body, word)
        for updater in ("historical_updater.py", "financial_history_updater.py", "fundamentals_updater.py", "financials_updater.py", "quarterly_results_updater.py", "historical_batch.py choose"):
            self.assertNotIn(updater, body, updater)

    def test_no_user_input_is_expanded_inside_a_shell_script_and_commands_are_arrays(self):
        for job in self.doc["jobs"].values():
            for step in job["steps"]:
                if "run" in step:
                    self.assertNotIn("${{", step["run"], step.get("name"))                 # inputs reach the shell only as environment variables
        body = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))
        self.assertIn('"${cmd[@]}"', body)
        self.assertNotIn("$args", body)
        self.assertNotIn("eval ", body)
        for flag in ("--priority-symbols", "--max-calls", "--cohort", "--before-sha", "--data-sha"):
            self.assertIn(flag, body)
        self.assertNotIn("priority.txt", body)

    def test_the_before_sha_must_be_a_real_ancestor_on_the_data_branch(self):
        body = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))
        self.assertIn("merge-base --is-ancestor", body)
        self.assertIn("cat-file -e", body)
        self.assertIn("fetch-depth: 0", body)
        self.assertIn("[0-9a-f]{40}", body)

    def test_it_has_a_concurrency_group_that_never_cancels_and_a_timeout(self):
        self.assertFalse(self.doc["concurrency"]["cancel-in-progress"])
        self.assertTrue(self.doc["concurrency"]["group"])
        for job in self.doc["jobs"].values():
            self.assertLessEqual(job["timeout-minutes"], 15)

    def test_the_dataset_input_offers_only_the_automatic_datasets_and_the_budget_has_a_default(self):
        inputs = self.on["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs["dataset"]["options"]), set(K.AUTOMATIC))
        self.assertEqual(inputs["dataset"]["type"], "choice")
        self.assertIn("pause", self.text.lower())

    def test_no_other_workflow_runs_or_mentions_the_controller(self):
        for wf in sorted((HERE / ".github" / "workflows").glob("*.yml")):
            if wf.name != WORKFLOW.name:
                self.assertNotIn("backfill_controller", wf.read_text(encoding="utf-8"), wf.name)


if __name__ == "__main__":
    unittest.main()
