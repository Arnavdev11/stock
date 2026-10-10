"""
historical_batch.py (the resumable batch selector), the changed-files-only hand-over, the storage limits, and an updater run through the whole flow.
SYNTHETIC data only (invented prices and symbols). No network, no token, no workflow is run: the Upstox client and instrument file are replaced by fakes.
"""
import contextlib
import datetime as dt
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import historical_batch as B
import historical_handoff as H
import historical_updater as U
import shards
import stock_directory
import test_stock_directory as TSD
from test_historical_handoff import META, candles, entry, make_set

ROOT = Path(__file__).parent
WF = ROOT / ".github" / "workflows"
END = dt.date(2026, 10, 9)                  # a Friday: the last complete day in these tests
TODAY = dt.date(2026, 10, 10)


def syms(n):
    return ["S%04d" % i for i in range(n)]


def day(offset):
    return (END - dt.timedelta(days=offset)).isoformat()


class Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = Path(self._t.name)
        self.addCleanup(self._t.cleanup)

    def plan(self, directory, last=None, **kw):
        a = dict(damaged=[], attempts={}, end=END, today=TODAY, limit=10, budget=150, include=(), supported=None, bench_calls=1)
        a.update(kw)
        return B.plan(directory, last or {}, a["damaged"], a["attempts"], a["end"], a["today"], a["limit"], a["budget"], a["include"], a["supported"], a["bench_calls"])


class Order(Tmp):
    def test_never_fetched_first_in_directory_order_then_the_stalest(self):
        d = syms(6)
        last = {"S0001": day(30), "S0002": day(90), "S0003": day(60)}                  # S0000, S0004, S0005 were never fetched
        r = self.plan(d, last, limit=6)
        self.assertEqual(r["auto"], ["S0000", "S0004", "S0005", "S0002", "S0003", "S0001"])

    def test_up_to_date_stocks_are_left_alone(self):
        d = syms(4)
        last = {"S0000": day(0), "S0001": day(B.FRESH_DAYS), "S0002": day(B.FRESH_DAYS + 1)}
        r = self.plan(d, last, limit=10)
        self.assertEqual(r["auto"], ["S0003", "S0002"])                                  # S0000 and S0001 are fresh
        self.assertEqual(r["counts"]["fresh"], 2)

    def test_named_stocks_come_first_and_are_updated_even_when_fresh(self):
        d = syms(5)
        r = self.plan(d, {"S0003": day(0)}, include=["s0003", "S0004", " S0003 "], limit=2)
        self.assertEqual(r["include"], ["S0003", "S0004"])
        self.assertEqual(r["symbols"][:2], ["S0003", "S0004"])
        self.assertEqual(r["auto"], ["S0000", "S0001"])
        self.assertEqual(len(set(r["symbols"])), len(r["symbols"]))

    def test_a_named_stock_must_be_in_the_directory_and_valid(self):
        with self.assertRaises(B.BatchError):
            self.plan(syms(3), include=["NOTLISTED"])
        with self.assertRaises(B.BatchError):
            self.plan(syms(3), include=["bad symbol!"])

    def test_batch_size_bounds(self):
        for bad in (0, -1, B.MAX_BATCH + 1, True, "5", 2.5):
            with self.assertRaises(B.BatchError, msg=repr(bad)):
                self.plan(syms(3), limit=bad)
        stale = {x: day(20) for x in syms(2000)}                                          # updates cost 1 call each, so the batch limit (not the budget) is what binds
        self.assertEqual(len(self.plan(syms(2000), stale, limit=B.MAX_BATCH, budget=10 ** 6)["auto"]), B.MAX_BATCH)
        self.assertEqual(len(self.plan(syms(2000), limit=B.MAX_BATCH, budget=10 ** 6)["auto"]), 449)      # first fills: the call ceiling binds ((3,000 x 0.9 - 1) / 6)

    def test_the_batch_limit_is_respected(self):
        self.assertEqual(len(self.plan(syms(100), limit=7)["auto"]), 7)

    def test_unsupported_cooling_and_damaged_stocks_are_skipped_and_listed_individually(self):
        d = syms(6)
        att = {"S0001": {"last": TODAY.isoformat(), "count": 1}, "S0002": {"last": (TODAY - dt.timedelta(days=B.COOLDOWN_DAYS)).isoformat(), "count": 2}}
        r = self.plan(d, damaged=["S0003"], attempts=att, supported=set(d) - {"S0004"}, limit=10)
        self.assertEqual(r["unsupported"], ["S0004"])
        self.assertEqual(r["cooling_down"], ["S0001"])                                   # S0002's rest is over, it is tried again
        self.assertEqual(r["damaged"], ["S0003"])
        self.assertEqual(r["auto"], ["S0000", "S0002", "S0005"])

    def test_a_named_unsupported_stock_still_goes_to_the_updater_so_it_is_reported_there(self):
        d = syms(3)
        r = self.plan(d, supported={"S0000", "S0001"}, include=["S0002"])
        self.assertIn("S0002", r["symbols"])


class Budget(Tmp):
    def test_the_cost_is_the_updaters_own(self):
        self.assertEqual(B._cost(None, END), U.calls_needed([], END))
        self.assertEqual(B._cost(None, END), 6)                                          # a five-year first fill
        self.assertEqual(B._cost(day(5), END), 1)                                        # an update
        self.assertEqual(B._cost(day(5), END), U.calls_needed([{"date": day(5)}], END))

    def test_a_batch_never_plans_more_than_the_budget_and_leaves_room_for_retries(self):
        r = self.plan(syms(500), limit=500, budget=150, bench_calls=1)
        self.assertEqual(r["planned_call_limit"], 134)                                   # 90% of 150 less the benchmark
        self.assertLessEqual(r["estimated_calls"], 150)
        self.assertLessEqual(r["estimated_calls"] - 1, 134)
        self.assertEqual(len(r["auto"]), 22)                                              # 22 x 6 = 132
        self.assertTrue(r["stopped_by_budget"])

    def test_the_call_ceiling_keeps_a_run_inside_the_job_time_limit(self):
        self.assertEqual(B.CALL_CEILING, 3000)
        r = self.plan(syms(2000), limit=500, budget=10 ** 6, bench_calls=1)
        self.assertEqual(r["call_ceiling"], 3000)
        self.assertLessEqual(r["estimated_calls"] * B.SECONDS_PER_CALL / 60, B.MAX_MINUTES)
        self.assertLessEqual(r["estimated_minutes"], 120 - 20)                           # the job has 120 minutes (historical.yml)

    def test_it_stops_at_the_first_stock_that_does_not_fit_and_never_skips_around(self):
        d = syms(4)
        last = {"S0003": day(40)}                                                          # S0003 is stale (cheap) but behind three never-fetched stocks
        r = self.plan(d, last, limit=10, budget=21, bench_calls=1)                         # usable = 18 - 1 = 17: two first fills (12) fit, the third (18) does not
        self.assertEqual(r["auto"], ["S0000", "S0001"])
        self.assertTrue(r["stopped_by_budget"])

    def test_a_stale_stock_is_not_costed_as_a_first_fill(self):
        r = self.plan(syms(30), {s: day(20) for s in syms(30)}, limit=30, budget=150)
        self.assertEqual(len(r["auto"]), 30)                                              # 30 x 1 call
        self.assertEqual(r["estimated_calls"], 31)

    def test_the_workflow_timeout_covers_the_ceiling(self):
        wf = (WF / "historical.yml").read_text()
        minutes = int(re.search(r"timeout-minutes: (\d+)", wf).group(1))
        self.assertGreaterEqual(minutes, B.MAX_MINUTES + 15)


class Resume(Tmp):
    def test_batches_cover_the_whole_directory_exactly_once_and_then_only_refresh(self):
        d = syms(137)
        last = {}
        seen = []
        for _ in range(20):
            r = self.plan(d, last, limit=25, budget=10 ** 6)
            if not r["auto"]:
                break
            seen += r["auto"]
            for s in r["auto"]:
                last[s] = END.isoformat()                                                  # the batch saved them
        self.assertEqual(sorted(seen), d)                                                  # every stock once ...
        self.assertEqual(len(seen), len(set(seen)))                                        # ... and none twice (no repeated work)
        self.assertEqual(len(seen), 137)
        self.assertEqual(self.plan(d, last)["auto"], [])                                   # all fresh: nothing left to do

    def test_a_stock_that_failed_does_not_block_the_following_batches(self):
        d = syms(10)
        last, att = {}, {}
        r1 = self.plan(d, last, limit=4)
        self.assertEqual(r1["auto"], ["S0000", "S0001", "S0002", "S0003"])
        out = self.t / "out"
        for s in ("S0001", "S0002", "S0003"):                                              # S0000 failed: the batch saved the other three
            last[s] = END.isoformat()
            shards.write_shard(self.t, "historical", s, META, entry(s, [dict(c, date=END.isoformat()) for c in candles(1)]))
        doc, lost = B.record(r1, out, att, TODAY)
        self.assertEqual(lost, ["S0000"])
        att = doc["failed"]
        r2 = self.plan(d, last, attempts=att, limit=4)
        self.assertNotIn("S0000", r2["auto"])                                              # resting
        self.assertEqual(r2["auto"], ["S0004", "S0005", "S0006", "S0007"])
        later = self.plan(d, last, attempts=att, today=TODAY + dt.timedelta(days=B.COOLDOWN_DAYS), limit=4)
        self.assertEqual(later["auto"][0], "S0000")                                        # tried again after the rest

    def test_the_stalest_are_refreshed_after_the_new_ones(self):
        d = syms(5)
        last = {"S0000": day(100), "S0001": day(10), "S0002": day(0), "S0003": day(50)}
        self.assertEqual(self.plan(d, last, limit=5)["auto"], ["S0004", "S0000", "S0003", "S0001"])


class SavedState(Tmp):
    def write_shard(self, root, sym, cs):
        (root / "shards").mkdir(parents=True, exist_ok=True)
        (root / "shards" / (sym + ".json")).write_text(json.dumps(dict(META, stocks={sym: entry(sym, cs)})), encoding="utf-8")

    def test_a_stocks_own_file_wins_over_the_single_file_and_a_stock_only_in_the_single_file_counts(self):
        root = self.t / "saved"
        make_set(root, {"TCS": candles(60, start="2026-06-01")}, bench=candles(80, start="2026-06-01", base=500))
        self.write_shard(root, "DOMS", candles(40, start="2026-07-01"))
        doc = json.loads((root / "historical.json").read_text())
        doc["stocks"]["TCS"]["candles"] = candles(10, start="2026-01-01")                  # the single file is older: the shard must win
        doc["stocks"]["ITC"] = entry("ITC", candles(20, start="2026-05-01"))                  # only in the single file (a development stock)
        (root / "historical.json").write_text(json.dumps(doc))
        last, bench, damaged = B.saved_state(root)
        self.assertEqual(last["TCS"], candles(60, start="2026-06-01")[-1]["date"])
        self.assertEqual(last["ITC"], candles(20, start="2026-05-01")[-1]["date"])
        self.assertEqual(last["DOMS"], candles(40, start="2026-07-01")[-1]["date"])
        self.assertEqual(bench, candles(80, start="2026-06-01")[-1]["date"])
        self.assertEqual(damaged, [])

    def test_damaged_files_are_reported_not_treated_as_never_fetched(self):
        root = self.t / "saved"
        self.write_shard(root, "TCS", candles(10))
        (root / "shards" / "BAD.json").write_text("{broken")
        (root / "shards" / "EMPTY.json").write_text(json.dumps(dict(META, stocks={"EMPTY": entry("EMPTY", [])})))
        last, _, damaged = B.saved_state(root)
        self.assertEqual(sorted(damaged), ["BAD", "EMPTY"])
        self.assertEqual(list(last), ["TCS"])

    def test_nothing_saved_yet(self):
        self.assertEqual(B.saved_state(self.t / "none"), ({}, None, []))
        self.assertEqual(B.load_attempts(self.t / "none"), {})

    def test_attempts_that_are_invalid_are_ignored(self):
        root = self.t / "saved"
        root.mkdir()
        (root / "attempts.json").write_text(json.dumps({"kind": "historical_attempts", "updated": "2026-10-01", "failed": {"X": {"last": "no", "count": 1}}}))
        self.assertEqual(B.load_attempts(root), {})


class Directory(Tmp):
    def doc(self):
        o = TSD.Fx().build(TSD.LOW)
        self.assertTrue(o.ok, o.problems)
        return o.doc

    def test_a_valid_directory_is_read_in_order(self):
        p = self.t / "stock_directory.json"
        p.write_text(stock_directory.dumps(self.doc()), encoding="utf-8")
        self.assertEqual(B.load_directory(p), ["S1", "S2", "S3"])

    def test_a_tampered_or_wrong_file_is_refused(self):
        d = self.doc()
        d["symbols"][0]["name"] = "Changed"
        p = self.t / "d.json"
        p.write_text(json.dumps(d))
        with self.assertRaises(B.BatchError):
            B.load_directory(p)
        d = self.doc()
        d["kind"] = "universe"
        p.write_text(json.dumps(d))
        with self.assertRaises(B.BatchError):
            B.load_directory(p)
        p.write_text("{broken")
        with self.assertRaises(B.BatchError):
            B.load_directory(p)
        with self.assertRaises(B.BatchError):
            B.load_directory(self.t / "missing.json")


class Record(Tmp):
    def test_failures_are_counted_successes_are_cleared_and_other_entries_are_kept(self):
        out = self.t / "out"
        shards.write_shard(self.t, "historical", "AAA", META, entry("AAA", candles(10, start="2026-10-01")))      # gained candles (prior was older)
        shards.write_shard(self.t, "historical", "BBB", META, entry("BBB", candles(10, start="2026-08-01")))      # no new candle
        rep = {"symbols": ["AAA", "BBB", "CCC"], "prior": {"AAA": "2026-09-01", "BBB": candles(10, start="2026-08-01")[-1]["date"], "CCC": None}}
        old = {"AAA": {"last": "2026-09-30", "count": 2}, "ZZZ": {"last": "2026-09-30", "count": 5}, "BBB": {"last": "2026-09-30", "count": 1}}
        doc, lost = B.record(rep, out, old, TODAY)
        self.assertEqual(lost, ["BBB", "CCC"])
        self.assertNotIn("AAA", doc["failed"])
        self.assertEqual(doc["failed"]["BBB"], {"last": TODAY.isoformat(), "count": 2})
        self.assertEqual(doc["failed"]["CCC"], {"last": TODAY.isoformat(), "count": 1})
        self.assertEqual(doc["failed"]["ZZZ"], {"last": "2026-09-30", "count": 5})
        self.assertEqual(H.attempts_problems(doc, "x"), [])

    def test_the_count_is_capped_so_the_file_stays_valid(self):
        rep = {"symbols": ["CCC"], "prior": {"CCC": None}}
        doc, _ = B.record(rep, self.t / "out", {"CCC": {"last": "2026-10-01", "count": 1000}}, TODAY)
        self.assertEqual(doc["failed"]["CCC"]["count"], 1000)
        self.assertEqual(H.attempts_problems(doc, "x"), [])


class Commands(Tmp):
    def setUp(self):
        super().setUp()
        d = syms(40)
        self.dir_path = self.t / "stock_directory.json"
        self.dir_path.write_text("{}")
        self.saved = self.t / "saved"
        make_set(self.saved, {"S0000": candles(30, start="2026-08-01")}, bench=candles(30, start="2026-08-01", base=500))
        self.genv = self.t / "github_env"
        patches = [mock.patch.object(stock_directory, "check_directory_doc", return_value=[]),
                   mock.patch.object(B, "load_directory", return_value=d),
                   mock.patch.object(U, "last_complete_day", return_value=END),
                   mock.patch.object(U, "load_instrument_file", return_value=({s: {} for s in d if s != "S0005"}, [])),
                   mock.patch.dict(os.environ, {"GITHUB_ENV": str(self.genv)}), mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.t / "summary.md")})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_choose(self, **env):
        e = {"INPUT_BATCH": "5", "INPUT_SYMBOLS": "", "HISTORICAL_MAX_CALLS": ""}
        e.update(env)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = B.main(["choose", "--directory", str(self.dir_path), "--saved", str(self.saved), "--out", str(self.t / "batch")], env=e)
        return code, out.getvalue(), err.getvalue()

    def test_choose_writes_the_report_and_exports_only_the_symbols(self):
        code, out, err = self.run_choose(INPUT_SYMBOLS="S0007")
        self.assertEqual(code, 0, err)
        env = self.genv.read_text().strip().splitlines()
        self.assertEqual(len(env), 1)
        self.assertEqual(env[0], "CHOSEN_SYMBOLS=S0007,S0001,S0002,S0003,S0004,S0006")        # named first; S0000 is up to date, S0005 is not served
        rep = json.loads((self.t / "batch" / "report.json").read_text())
        self.assertEqual(rep["unsupported"], ["S0005"])
        self.assertIn("Historical batch plan", (self.t / "summary.md").read_text())
        self.assertNotIn("UPSTOX", json.dumps(rep) + out + err)

    def test_choose_never_writes_into_the_saved_folder(self):
        before = {p.name: p.read_bytes() for p in self.saved.rglob("*") if p.is_file()}
        self.run_choose()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.saved.rglob("*") if p.is_file()})

    def test_choose_refuses_a_bad_batch_size(self):
        for bad in ("", "0", "501", "-3", "abc", "5; rm -rf /", "1e3"):
            code, _, err = self.run_choose(INPUT_BATCH=bad)
            self.assertEqual(code, 1, bad)
            self.assertFalse(self.genv.exists(), bad)

    def test_choose_with_nothing_to_do_fails_loudly_and_exports_nothing(self):
        with mock.patch.object(B, "plan", side_effect=lambda *a, **k: dict(B.plan.__wrapped__(*a, **k)) if False else {**_empty_plan()}):
            code, out, err = self.run_choose()
        self.assertEqual(code, 1)
        self.assertIn("Nothing to update", err)
        self.assertFalse(self.genv.exists())

    def test_choose_survives_an_unreadable_instrument_file(self):
        with mock.patch.object(U, "load_instrument_file", side_effect=SystemExit(1)):
            code, _, err = self.run_choose()
        self.assertEqual(code, 0, err)
        self.assertIn("S0005", self.genv.read_text())                                           # nothing could be filtered in advance
        self.assertIn("could not be read", (self.t / "summary.md").read_text())

    def test_an_unknown_named_stock_is_refused_before_anything_runs(self):
        code, _, err = self.run_choose(INPUT_SYMBOLS="NOPE")
        self.assertEqual(code, 1)
        self.assertIn("not in the stock directory", err)
        self.assertFalse(self.genv.exists())

    def test_the_budget_comes_from_the_environment_and_a_bad_value_means_the_default(self):
        _, _, _ = self.run_choose(INPUT_BATCH="40", HISTORICAL_MAX_CALLS="not a number")
        self.assertEqual(json.loads((self.t / "batch" / "report.json").read_text())["call_budget"], 150)
        self.genv.unlink()
        self.run_choose(INPUT_BATCH="40", HISTORICAL_MAX_CALLS="3000")
        self.assertEqual(json.loads((self.t / "batch" / "report.json").read_text())["call_budget"], 3000)

    def test_record_writes_a_valid_attempts_file(self):
        self.run_choose()
        out = self.t / "out"
        out.mkdir()
        e = io.StringIO()
        with contextlib.redirect_stdout(e):
            code = B.main(["record", "--report", str(self.t / "batch" / "report.json"), "--out", str(out), "--saved", str(self.saved), "--attempts-out", str(self.t / "batch" / "attempts.json")])
        self.assertEqual(code, 0)
        doc = json.loads((self.t / "batch" / "attempts.json").read_text())
        self.assertEqual(H.attempts_problems(doc, "x"), [])
        self.assertEqual(sorted(doc["failed"]), sorted(json.loads((self.t / "batch" / "report.json").read_text())["symbols"]))      # nothing was saved in `out`

    def test_usage(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(B.main([]), 2)
            self.assertEqual(B.main(["choose", "--directory", "x"], env={}), 2)
            self.assertEqual(B.main(["frobnicate"]), 2)

    def test_exported_values_are_symbols_only(self):
        with self.assertRaises(B.BatchError):
            B._export("CHOSEN_SYMBOLS", "ABC\nX=1")
        with self.assertRaises(B.BatchError):
            B._export("CHOSEN_SYMBOLS", "A B")
        B._export("CHOSEN_SYMBOLS", "M&M,BAJAJ-AUTO,GMRP&UI")


def _empty_plan():
    return {"symbols": [], "include": [], "auto": [], "estimated_calls": 1, "estimated_minutes": 0.0, "planned_call_limit": 134, "benchmark_calls": 1, "call_budget": 150,
            "counts": {"directory": 40, "with_history": 40, "never_fetched": 0, "stale": 0, "fresh": 40, "cooling_down": 0, "unsupported": 0, "damaged": 0,
                       "never_fetched_left_after_this_batch": 0, "stale_left_after_this_batch": 0}, "unsupported": [], "prior": {}}


# ------------------------------------------------------------------------------------------------ the updater, run for real against fakes
def fake_candle(d):
    """A deterministic price for a date, so a re-fetch of an old day gives exactly the saved candle."""
    o = 100.0 + (d.toordinal() % 40)
    return {"date": d.isoformat(), "open": o, "high": o + 2.0, "low": o - 1.0, "close": o + 1.0, "volume": 1000 + d.toordinal() % 500}


def weekdays(frm, to):
    d = frm
    while d <= to:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


class FakeClient:
    instances = []

    def __init__(self, token):
        self.calls = 0
        self.log = []
        FakeClient.instances.append(self)

    def candles(self, key, frm, to):
        if self.calls >= U.MAX_CALLS_PER_RUN:
            return None, "call limit for this run reached"
        self.calls += 1
        self.log.append(key)
        if key.endswith("EMPTYCO"):
            return [], None
        if key.endswith("FAILCO"):
            return None, "HTTP 500"
        rows = [fake_candle(d) for d in weekdays(dt.date.fromisoformat(frm), dt.date.fromisoformat(to))]
        return rows, None


class UpdaterFlow(Tmp):
    """historical_updater.py itself (unchanged), seeded from a saved copy, extended by a batch, handed over as a delta and installed."""

    def setUp(self):
        super().setUp()
        FakeClient.instances = []
        self.work = self.t / "work"
        (self.work / "out").mkdir(parents=True)
        eq = {s: {"isin": "INE000000000", "name": s, "instrument_key": "NSE_EQ|" + s} for s in ("TCS", "INFY", "DOMS", "NEWCO", "EMPTYCO", "FAILCO")}
        self.patches = [mock.patch.object(U, "OUT_FILE", self.work / "out" / "historical.json"), mock.patch.object(U, "Client", FakeClient),
                        mock.patch.object(U, "load_instrument_file", return_value=(eq, [{"instrument_key": "NSE_INDEX|Nifty 50", "name": "Nifty 50"}])),
                        mock.patch.object(U, "last_complete_day", return_value=END), mock.patch.object(U.time, "sleep"),
                        mock.patch.object(U, "MAX_CALLS_PER_RUN", 150),
                        mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": "dummy-not-a-real-token-value"})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        # the saved copy on the data branch: TCS and INFY, with history up to a week ago, and the benchmark
        frm = END - dt.timedelta(days=700)
        last_saved = END - dt.timedelta(days=7)
        self.saved = self.t / "branch" / "historical"
        tcs = [fake_candle(d) for d in weekdays(frm, last_saved)]
        infy = [fake_candle(d) for d in weekdays(frm, last_saved)]
        bench = [fake_candle(d) for d in weekdays(frm, last_saved)]
        make_set(self.saved, {"TCS": tcs, "INFY": infy}, bench=bench)
        self.old_bytes = {p.relative_to(self.saved).as_posix(): p.read_bytes() for p in self.saved.rglob("*") if p.is_file()}
        self.tcs_old = tcs

    def run_updater(self, symbols):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HISTORICAL_SYMBOLS": symbols}), contextlib.redirect_stdout(out):
            try:
                U.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_a_batch_extends_saved_history_fetches_new_stocks_in_full_and_reports_bad_ones_individually(self):
        self.assertGreater(H.seed(self.saved, self.work / "out"), 0)
        code, text = self.run_updater("TCS,DOMS,NEWCO,EMPTYCO,FAILCO,GHOST")
        self.assertEqual(code, 0)
        log = FakeClient.instances[0].log
        self.assertEqual(log.count("NSE_EQ|TCS"), 1)                                      # a saved stock: one incremental request, not five years again
        self.assertEqual(log.count("NSE_EQ|DOMS"), 6)                                     # a new stock: the five-year fill
        self.assertEqual(log.count("NSE_EQ|NEWCO"), 6)
        self.assertEqual(log.count("NSE_INDEX|Nifty 50"), 1)
        self.assertEqual(log.count("NSE_EQ|INFY"), 0)                                     # not asked for: not fetched
        for bad, why in (("EMPTYCO", "no valid candles"), ("FAILCO", "HTTP 500"), ("GHOST", "not found in Upstox NSE equity instruments")):
            self.assertRegex(text, r"::warning::%s: .*%s" % (bad, why))
        sd = self.work / "out" / "by_symbol" / "historical"
        self.assertTrue((sd / "DOMS.json").is_file() and (sd / "NEWCO.json").is_file())
        for bad in ("EMPTYCO", "FAILCO", "GHOST"):
            self.assertFalse((sd / (bad + ".json")).exists(), bad)                          # no shard for a stock that has nothing; the others were still saved
        self.assertIn("API calls: 21", text)                                               # 1 benchmark + 1 TCS + 6 DOMS + 6 NEWCO + 6 EMPTYCO (six empty windows) + 1 FAILCO

    def test_every_saved_candle_survives_and_the_shard_has_the_shape_the_page_loads(self):
        H.seed(self.saved, self.work / "out")
        self.run_updater("TCS,DOMS")
        sd = self.work / "out" / "by_symbol" / "historical"
        tcs = json.loads((sd / "TCS.json").read_text())["stocks"]["TCS"]["candles"]
        by = {c["date"]: c for c in tcs}
        for c in self.tcs_old:
            self.assertEqual(by[c["date"]], c)
        self.assertGreater(tcs[-1]["date"], self.tcs_old[-1]["date"])
        doc = json.loads((sd / "DOMS.json").read_text())                                    # index.html oneShard(): d.stocks[SYM] is the stock object
        self.assertEqual(list(doc["stocks"]), ["DOMS"])
        self.assertEqual(doc["stocks"]["DOMS"]["symbol"], "DOMS")
        self.assertTrue(doc["stocks"]["DOMS"]["candles"])
        self.assertEqual(H._candle_problems(doc["stocks"]["DOMS"]["candles"], "x"), [])

    def test_a_cache_less_run_that_is_not_seeded_cannot_overwrite_the_saved_history(self):
        self.run_updater("TCS,DOMS")                                                        # no seed: this run never saw the saved copy (INFY is not in its single file)
        with self.assertRaises(H.HandoffError):
            H.stage(self.work / "out", self.t / "hand", self.saved)
        self.assertFalse((self.t / "hand").exists())                                        # nothing is left behind to be uploaded
        self.assertEqual({p.relative_to(self.saved).as_posix(): p.read_bytes() for p in self.saved.rglob("*") if p.is_file()}, self.old_bytes)

    def test_the_hand_over_is_a_delta_and_installs_without_touching_other_saved_files(self):
        H.seed(self.saved, self.work / "out")
        self.run_updater("TCS,DOMS")
        n = H.stage(self.work / "out", self.t / "hand", self.saved)
        self.assertEqual(n, 2)                                                              # TCS (extended) and DOMS (new); INFY is not in it
        names = sorted(p.relative_to(self.t / "hand").as_posix() for p in (self.t / "hand").rglob("*") if p.is_file())
        self.assertEqual(names, ["historical.json", "shards/DOMS.json", "shards/TCS.json"])
        self.assertEqual(H.verify(self.t / "hand", self.saved), [])
        H.install(self.t / "hand", self.saved)
        after = {p.relative_to(self.saved).as_posix(): p.read_bytes() for p in self.saved.rglob("*") if p.is_file()}
        self.assertEqual(after["shards/INFY.json"], self.old_bytes["shards/INFY.json"])     # untouched, byte for byte
        self.assertEqual(set(after), set(self.old_bytes) | {"shards/DOMS.json"})            # only added; nothing deleted
        saved_tcs = {c["date"]: c for c in json.loads(after["shards/TCS.json"])["stocks"]["TCS"]["candles"]}
        for c in self.tcs_old:
            self.assertEqual(saved_tcs[c["date"]], c)
        main = json.loads(after["historical.json"])
        self.assertIn("INFY", main["stocks"])                                                # the single file still holds every stock it held

    def test_a_second_identical_run_stages_no_stock_files(self):
        H.seed(self.saved, self.work / "out")
        self.run_updater("TCS,DOMS")
        H.stage(self.work / "out", self.t / "hand", self.saved)
        H.install(self.t / "hand", self.saved)
        work2 = self.t / "work2"
        H.seed(self.saved, work2 / "out")
        n = H.stage(work2 / "out", self.t / "hand2", self.saved)
        self.assertEqual(n, 0)                                                              # nothing changed: only historical.json would travel
        self.assertEqual(H.verify(self.t / "hand2", self.saved), [])
        self.assertEqual(H.install(self.t / "hand2", self.saved), 0)                       # the same documents: nothing is rewritten, so nothing is committed

    def test_no_token_reaches_any_file_or_the_plan(self):
        H.seed(self.saved, self.work / "out")
        self.run_updater("TCS,DOMS")
        H.stage(self.work / "out", self.t / "hand", self.saved)
        for f in list((self.t / "hand").rglob("*")) + list((self.work / "out").rglob("*")):
            if f.is_file():
                self.assertNotIn("dummy-not-a-real-token-value", f.read_text(errors="replace"), f.name)


class WorkflowWiring(unittest.TestCase):
    H = (WF / "historical.yml").read_text()

    def seg(self, name):
        return self.H.split("- name: " + name)[1].split("\n      - name:")[0]

    def test_batch_mode_is_manual_only_and_holds_no_secret(self):
        for name in ("Choose the next batch of stocks", "Record which stocks gained candles"):
            seg = self.seg(name)
            self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.batch_size != ''", seg, name)
            self.assertNotIn("UPSTOX", seg)
            self.assertNotIn("secrets", seg)
        self.assertEqual(self.H.count("UPSTOX_ANALYTICS_TOKEN"), 2)                         # still only the update step

    def test_the_order_is_seed_choose_update_record_stage_upload(self):
        order = [self.H.index(x) for x in ("historical_handoff.py seed", "historical_batch.py choose", "python historical_updater.py", "historical_batch.py record",
                                            "historical_handoff.py stage out handoff", "upload-artifact@v4")]
        self.assertEqual(order, sorted(order))

    def test_a_push_can_never_choose_a_batch(self):
        self.assertIn("HISTORICAL_SYMBOLS: \"${{ env.CHOSEN_SYMBOLS || (github.event_name == 'push' && 'TCS' || inputs.symbols || 'TCS') }}\"", self.H)
        self.assertNotIn("batch_size", self.seg("Update historical prices"))
        self.assertIn("event_name == 'workflow_dispatch'", self.seg("Choose the next batch of stocks"))

    def test_the_inputs(self):
        self.assertRegex(self.H, r"batch_size:\n(?:.*\n)*?\s+default: \"\"")
        self.assertRegex(self.H, r'symbols:\n(?:.*\n)*?\s+default: "TCS"')                  # the first test is still TCS only

    def test_the_stage_step_passes_the_saved_folder_as_the_baseline_and_only_dispatch_stages(self):
        seg = self.seg("Check the result and prepare the hand-over")
        self.assertIn("--baseline ledger-branch/historical", seg)
        self.assertIn("--attempts batch/attempts.json", seg)
        self.assertIn("if: github.event_name == 'workflow_dispatch'", seg)

    def test_the_saved_data_cache_no_longer_carries_the_per_stock_files(self):
        cache = self.H.split("- name: Restore saved data")[1].split("- name:")[0]
        self.assertIn("!out/by_symbol/historical", cache)                                    # 2,500 shards (~300 MB) must not be re-cached on every run

    def test_the_save_workflow_is_unchanged_and_still_writes_only_under_historical(self):
        import guard_update_yml as G
        self.assertTrue(G.is_approved(ROOT, ".github/workflows/save_historical.yml"))

    def test_the_updater_and_the_other_updaters_are_untouched(self):
        import subprocess
        files = ["historical_updater.py", "shards.py", "financial_history_updater.py", "shareholding_updater.py",
                 "nse_updater.py", "universe.py", "upstox_common.py", "stock_directory.py", "sector_publish.py", "sector_master.py", ".github/workflows/update.yml",
                 ".github/workflows/save_historical.yml"]
        r = subprocess.run(["git", "diff", "--name-only", "HEAD", "--"] + files, cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertEqual(r.stdout.split(), [])

    def test_the_sector_gate_is_not_referenced(self):
        for f in ("historical_batch.py", "historical_handoff.py"):
            self.assertNotIn("SECTOR_PUBLIC_DISPLAY_APPROVED", (ROOT / f).read_text())

    def test_the_selector_has_no_write_path_into_the_saved_folder_and_no_candle_api(self):
        code = re.sub(r'""".*?"""', "", (ROOT / "historical_batch.py").read_text(), flags=re.S)
        for needle in ("CANDLE_URL", "Client(", "requests.", "UPSTOX_ANALYTICS_TOKEN", "get_token", "subprocess", "unlink", "rmtree", "git "):
            self.assertNotIn(needle, code, needle)


class StorageLimits(Tmp):
    def test_the_caps_hold_the_whole_directory_with_room_to_spare(self):
        one = shards.write_shard(self.t, "historical", "TCS", META, entry("TCS", [dict(c, open=1234.55, high=1240.1, low=1230.05, close=1238.9) for c in candles(1250)]))
        per_stock = one                                                                      # bytes for a full five-year history (about 1,250 candles)
        self.assertLess(per_stock, 125_000)
        directory = 2548
        self.assertGreaterEqual(H.MAX_SAVED_FILES, directory + 2)                           # + historical.json + attempts.json
        self.assertGreaterEqual(H.MAX_SAVED_BYTES, directory * per_stock)
        self.assertLess(directory * per_stock, 0.85 * H.MAX_SAVED_BYTES)                    # and some growth
        self.assertGreaterEqual(H.MAX_FILES, B.MAX_BATCH + 2)                                # one hand-over holds the largest batch
        self.assertGreaterEqual(H.MAX_TOTAL_BYTES, (B.MAX_BATCH + 2) * per_stock)
        self.assertLessEqual(H.MAX_FILE_BYTES, 6_000_000)

    def test_a_large_saved_copy_is_not_loaded_to_check_a_small_delta(self):
        saved = self.t / "saved"
        (saved / "shards").mkdir(parents=True)
        for s in syms(2548):
            (saved / "shards" / (s + ".json")).write_text(json.dumps(dict(META, stocks={s: entry(s, candles(5))})), encoding="utf-8")
        make_set(saved, {}, with_main=False)
        doc = dict(META, benchmark=None, stocks={s: entry(s, candles(5)) for s in ("S0001", "S0002")}, errors=[])
        (saved / "historical.json").write_text(json.dumps(doc))
        make_set(self.t / "new", {"S0001": candles(8), "S0002": candles(5), "S9999": candles(5)}, with_main=False)
        (self.t / "new" / "historical.json").write_text(json.dumps(doc))
        reads = []
        real = H._read_json
        with mock.patch.object(H, "_read_json", side_effect=lambda p, *a, **k: (reads.append(p.name), real(p, *a, **k))[1]):
            self.assertEqual(H.verify(self.t / "new", saved), [])
        self.assertLessEqual(len([r for r in reads if r.startswith("S")]), 6)               # 3 new files + their 2 saved twins, never the 2,548
        self.assertNotIn("S0100.json", reads)

    def test_the_saved_folder_cap_is_enforced_for_files_and_bytes_and_a_replaced_file_counts_once(self):
        saved = self.t / "saved"
        make_set(saved, {"AAA": candles(), "BBB": candles()})
        make_set(self.t / "new", {"AAA": candles(70), "BBB": candles(), "CCC": candles()})
        with mock.patch.object(H, "MAX_SAVED_FILES", 4):                                    # saved: 3 files; AAA replaced, CCC added -> 4
            self.assertEqual(H.verify(self.t / "new", saved), [])
        with mock.patch.object(H, "MAX_SAVED_FILES", 3):
            p = H.verify(self.t / "new", saved)
            self.assertTrue(any("saved folder would hold more than 3 files" in x for x in p), p)
        total = sum(f.stat().st_size for f in saved.rglob("*") if f.is_file())
        with mock.patch.object(H, "MAX_SAVED_BYTES", total + 10):
            p = H.verify(self.t / "new", saved)
            self.assertTrue(any("more than" in x and "bytes" in x for x in p), p)

    def test_a_saved_folder_already_over_the_cap_blocks_the_save(self):
        saved = self.t / "saved"
        make_set(saved, {"AAA": candles()})
        make_set(self.t / "new", {"AAA": candles(70)})
        with mock.patch.object(H, "MAX_SAVED_FILES", 1):
            self.assertNotEqual(H.verify(self.t / "new", saved), [])

    def test_a_hand_over_over_the_per_run_cap_is_refused_before_anything_is_installed(self):
        saved = self.t / "saved"
        make_set(saved, {"AAA": candles()})
        make_set(self.t / "new", {s: candles() for s in ("AAA", "BBB", "CCC")})
        with mock.patch.object(H, "MAX_FILES", 3):
            with self.assertRaises(H.HandoffError):
                H.install(self.t / "new", saved)
        self.assertFalse((saved / "shards" / "BBB.json").exists())


class SeedStreaming(Tmp):
    def test_a_damaged_stock_file_among_good_ones_seeds_nothing_at_all(self):
        saved = self.t / "saved"
        make_set(saved, {"AAA": candles(), "ZZZ": candles()})
        (saved / "shards" / "MMM.json").write_text("{broken")                              # sorts between the good ones, so a one-pass seed would already have written AAA
        with self.assertRaises(H.HandoffError):
            H.seed(saved, self.t / "out")
        self.assertFalse((self.t / "out").exists())

    def test_the_seed_reads_one_stock_file_at_a_time_in_two_passes(self):
        from collections import Counter
        saved = self.t / "saved"
        make_set(saved, {s: candles() for s in syms(30)})
        seen = []
        real = H._read_json
        def spy(path, problems, label):
            seen.append(path.name)
            return real(path, problems, label)
        with mock.patch.object(H, "_read_json", side_effect=spy):
            self.assertEqual(H.seed(saved, self.t / "out"), 31)
        self.assertEqual(Counter(x for x in seen if x.startswith("S")), Counter({s + ".json": 2 for s in syms(30)}))      # a check pass, then a merge pass
        self.assertEqual(len(list((self.t / "out" / "by_symbol" / "historical").glob("*.json"))), 30)

    def test_seeding_merges_by_date_and_never_drops_a_cached_day(self):
        saved = self.t / "saved"
        make_set(saved, {"AAA": candles(60)})
        shards.write_shard(self.t, "historical", "AAA", META, entry("AAA", candles(80)))            # a cache holding a longer history (written under self.t/out)
        H.seed(saved, self.t / "out")
        got = json.loads((self.t / "out" / "by_symbol" / "historical" / "AAA.json").read_text())["stocks"]["AAA"]["candles"]
        self.assertEqual(got, candles(80))


class StageDelta(Tmp):
    def build_out(self, stocks):
        out = self.t / "out"
        make_set(self.t / "src", stocks, bench=candles(60, base=500))
        out.mkdir(exist_ok=True)
        (out / "historical.json").write_bytes((self.t / "src" / "historical.json").read_bytes())
        (out / "by_symbol" / "historical").mkdir(parents=True, exist_ok=True)
        for f in (self.t / "src" / "shards").glob("*.json"):
            (out / "by_symbol" / "historical" / f.name).write_bytes(f.read_bytes())
        return out

    def test_an_unchanged_stock_file_is_left_out_even_if_its_bytes_differ_by_a_newline(self):
        saved = self.t / "saved"
        make_set(saved, {"AAA": candles(60), "BBB": candles(60)}, bench=candles(60, base=500))
        out = self.build_out({"AAA": candles(60), "BBB": candles(65)})
        p = out / "by_symbol" / "historical" / "AAA.json"
        p.write_text(p.read_text() + "\n")
        self.assertEqual(H.stage(out, self.t / "hand", saved), 1)
        self.assertEqual(sorted(x.name for x in (self.t / "hand" / "shards").glob("*")), ["BBB.json"])

    def test_without_a_baseline_everything_is_staged_as_before(self):
        out = self.build_out({"AAA": candles(), "BBB": candles()})
        self.assertEqual(H.stage(out, self.t / "hand"), 2)

    def test_the_attempts_file_travels_and_is_validated(self):
        out = self.build_out({"AAA": candles()})
        good = self.t / "attempts.json"
        good.write_text(json.dumps({"kind": "historical_attempts", "updated": "2026-10-10", "failed": {"M&M": {"last": "2026-10-10", "count": 1}}}))
        H.stage(out, self.t / "hand", None, good)
        self.assertEqual(json.loads((self.t / "hand" / "attempts.json").read_text())["failed"]["M&M"]["count"], 1)
        saved = self.t / "saved"
        H.install(self.t / "hand", saved)
        self.assertTrue((saved / "attempts.json").is_file())
        for i, bad in enumerate(({"kind": "x", "updated": "2026-10-10", "failed": {}},
                                 {"kind": "historical_attempts", "updated": "nope", "failed": {}},
                                 {"kind": "historical_attempts", "updated": "2026-10-10", "failed": {"a b": {"last": "2026-10-10", "count": 1}}},
                                 {"kind": "historical_attempts", "updated": "2026-10-10", "failed": {"AAA": {"last": "2026-10-10", "count": 0}}},
                                 {"kind": "historical_attempts", "updated": "2026-10-10", "failed": {"AAA": {"last": "2026-10-10", "count": 1, "token": "x"}}},
                                 {"kind": "historical_attempts", "updated": "2026-10-10", "failed": {"AAA": {"last": "2026-10-10", "count": 1}}, "extra": 1}, [])):
            f = self.t / ("bad%d.json" % i)
            f.write_text(json.dumps(bad))
            with self.assertRaises(H.HandoffError, msg=str(bad)):
                H.stage(self.build_out({"AAA": candles()}) if False else out, self.t / ("h%d" % i), None, f)
            self.assertFalse((self.t / ("h%d" % i)).exists())

    def test_a_credential_in_the_attempts_file_is_refused(self):
        out = self.build_out({"AAA": candles()})
        f = self.t / "attempts.json"
        f.write_text(json.dumps({"kind": "historical_attempts", "updated": "2026-10-10", "failed": {"AAA": {"last": "2026-10-10", "count": 1}}, "note": "Bearer abc"}))
        with self.assertRaises(H.HandoffError):
            H.stage(out, self.t / "hand", None, f)

    def test_the_command_line_accepts_the_options_only_for_stage(self):
        out = self.build_out({"AAA": candles()})
        o, e = io.StringIO(), io.StringIO()
        self.assertEqual(H.main(["stage", str(out), str(self.t / "hand"), "--baseline", str(self.t / "nobranch")], out=o, err=e), 0)
        self.assertEqual(H.main(["verify", str(self.t / "hand"), str(self.t / "nobranch"), "--baseline", "x"], out=o, err=e), 2)
        self.assertEqual(H.main(["stage", str(out), "--baseline", "x"], out=o, err=e), 2)


if __name__ == "__main__":
    unittest.main()
