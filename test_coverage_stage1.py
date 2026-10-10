"""Stage 1 coverage tests (NIFTY 500): the universe, per-stock files, batching, budgets, failure isolation and the workflow wiring.
Mocks only; no token, no network, no git.   Run: python3 test_coverage_stage1.py"""
import contextlib
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import financial_history_updater as fh          # noqa: E402
import fundamentals_updater as fu                # noqa: E402
import historical_updater as hu                  # noqa: E402
import shards                                    # noqa: E402
import shareholding_updater as shu               # noqa: E402
import universe                                  # noqa: E402
import upstox_common                             # noqa: E402

CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
       "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n"
       "Alpha Ltd.,Chemicals,ALPHA,EQ,INE000A01011\n"
       "Beta Ltd.,Metals,BETA,EQ,INE000B01010\n"
       "Gamma Ltd.,Metals,GAMMA,BE,INE000C01019\n"
       "Delta Ltd.,Metals,DELTA,EQ,INE000D01018\n")
INSTR = {"TCS": {"isin": "INE467B01029", "name": "TATA CONSULTANCY SERVICES LTD"},
         "ALPHA": {"isin": "INE000A01011", "name": "ALPHA LTD"},
         "BETA": {"isin": "INE000B01010", "name": "BETA LTD"},
         "DELTA": {"isin": "INE999D01018", "name": "DELTA LTD"}}          # a different ISIN from the index list


def uni_doc(symbols):
    syms = [{"symbol": s, "isin": None, "name": None} for s in symbols]
    return {"schema_version": 1, "kind": "universe", "stage": "test", "source": "test", "as_of": "2026-10-08", "count": len(syms), "symbols": syms}


class Env:
    """Temporarily sets environment variables."""
    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------- the universe
class UniverseTests(unittest.TestCase):
    def test_default_is_the_ten_development_stocks_in_every_place(self):
        self.assertEqual(len(universe.TEST_SYMBOLS), 10)
        self.assertEqual(universe.TEST_SYMBOLS, upstox_common.SYMBOLS)
        self.assertEqual(universe.TEST_SYMBOLS, fu.SYMBOLS)
        with Env(STOCKLENS_UNIVERSE_FILE=None):
            self.assertEqual(universe.load(), universe.TEST_SYMBOLS)
            self.assertEqual(universe.describe()["count"], 10)

    def test_the_index_csv_keeps_eq_series_and_ignores_industry(self):
        rows = universe.parse_index_csv(CSV)
        self.assertEqual([r["symbol"] for r in rows], ["TCS", "ALPHA", "BETA", "DELTA"])    # GAMMA is series BE
        self.assertTrue(all(set(r) == {"symbol", "isin", "name"} for r in rows))             # no sector column is carried
        with self.assertRaises(universe.UniverseError):
            universe.parse_index_csv("Name,Code\nx,y\n")

    def test_build_keeps_only_stocks_upstox_serves_with_a_matching_isin(self):
        doc, dropped = universe.build_doc(universe.parse_index_csv(CSV), INSTR, "2026-10-08", "src", "nifty500")
        self.assertEqual([x["symbol"] for x in doc["symbols"]], ["ALPHA", "BETA", "TCS"])
        self.assertEqual([d[0] for d in dropped], ["DELTA"])                                 # ISIN differs: not guessed
        self.assertEqual(universe.check_doc(doc), [])
        self.assertNotIn("sector", json.dumps(doc).lower())

    def test_check_rejects_bad_files(self):
        d = uni_doc(["A", "B"])
        self.assertEqual(universe.check_doc(d), [])
        dup = uni_doc(["A", "A"])
        self.assertTrue(any("duplicate" in p for p in universe.check_doc(dup)))
        extra = uni_doc(["A"]); extra["symbols"][0]["sector"] = "x"
        self.assertTrue(any("unexpected field" in p for p in universe.check_doc(extra)))
        bad = uni_doc(["A"]); bad["count"] = 5
        self.assertTrue(universe.check_doc(bad))
        self.assertTrue(universe.check_doc({"kind": "x"}))
        self.assertTrue(universe.check_doc(uni_doc([])))

    def test_a_configured_but_broken_universe_stops_the_run(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "u.json"
            with Env(STOCKLENS_UNIVERSE_FILE=str(p)):
                with self.assertRaises(universe.UniverseError):          # missing
                    universe.load()
                p.write_text("{not json", encoding="utf-8")
                with self.assertRaises(universe.UniverseError):          # unreadable
                    universe.load()
                p.write_text(json.dumps(uni_doc(["A", "B"])), encoding="utf-8")
                self.assertEqual(universe.load(), ["A", "B"])

    def test_a_short_download_or_a_collapse_is_refused(self):
        small = uni_doc(["A", "B"])
        self.assertTrue(universe.build_problems(small, minimum=450))
        prev = uni_doc(["S%d" % i for i in range(100)])
        new = uni_doc(["S%d" % i for i in range(80)])
        self.assertTrue(universe.build_problems(new, prev))
        self.assertEqual(universe.build_problems(uni_doc(["S%d" % i for i in range(95)]), prev), [])

    def test_stage2_is_the_same_file_shape(self):
        doc = universe.build_all_eq({"A": {"isin": "INE000A01011", "name": "A"}, "ETF1": {"isin": "INF000A01011", "name": "E"}}, ["etf1"], "2026-10-08", "src")
        self.assertEqual([x["symbol"] for x in doc["symbols"]], ["A"])
        self.assertEqual(universe.check_doc(doc), [])
        self.assertEqual(set(doc), set(universe.build_doc([], {}, "d", "s", "nifty500")[0]))     # identical keys: Stage 2 is a configuration change

    def test_stalest_first_and_budget(self):
        self.assertEqual(universe.order_stalest(["B", "A", "C"], {"A": "2026-10-01", "B": "2026-09-01"}), ["C", "B", "A"])   # never-done first, then oldest
        self.assertEqual(universe.budget("X_NOT_SET_BUDGET", 7), 7)
        for bad in ("0", "-3", "abc", ""):
            with Env(X_B=bad):
                self.assertEqual(universe.budget("X_B", 7), 7)
        with Env(X_B="42"):
            self.assertEqual(universe.budget("X_B", 7), 42)


# ---------------------------------------------------------------- per-stock files
class ShardTests(unittest.TestCase):
    def test_write_read_index(self):
        with tempfile.TemporaryDirectory() as t:
            n = shards.write_shard(t, "historical", "TCS", {"updated": "2026-10-08", "source": "x", "errors": []}, {"symbol": "TCS", "candles": []})
            self.assertGreater(n, 10)
            d = shards.read_shard(t, "historical", "TCS")
            self.assertEqual(list(d["stocks"]), ["TCS"])
            self.assertEqual(d["updated"], "2026-10-08")
            self.assertIsNone(shards.read_shard(t, "historical", "NOPE"))
            shards.write_index(t, "2026-10-08")
            idx = json.loads((Path(t) / "out" / "by_symbol" / "index.json").read_text())
            self.assertEqual(idx["counts"], {"historical": 1, "financial_history": 0, "shareholding": 0})
            self.assertFalse(list((Path(t) / "out" / "by_symbol").rglob("*.tmp")))      # atomic writes leave no temporary file

    def test_unsafe_names_and_kinds_are_refused(self):
        with tempfile.TemporaryDirectory() as t:
            for sym in ("../x", "a/b", "", "A B", "x" * 40):
                with self.assertRaises(ValueError):
                    shards.write_shard(t, "historical", sym, {}, {})
            with self.assertRaises(ValueError):
                shards.write_shard(t, "scans", "TCS", {}, {})

    def test_a_damaged_file_reads_as_missing(self):
        with tempfile.TemporaryDirectory() as t:
            shards.write_shard(t, "shareholding", "ITC", {}, {})
            shards.shard_path(t, "shareholding", "ITC").write_text("{broken", encoding="utf-8")
            self.assertIsNone(shards.read_shard(t, "shareholding", "ITC"))


# ---------------------------------------------------------------- historical prices
END = dt.date(2026, 10, 2)


def candle(day, close=100.0):
    return {"date": day, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}


class FakeClient:
    """Stands in for the Upstox candle client: one candle per window, optional failing keys. Counts calls like the real one."""
    failing = set()
    last = None

    def __init__(self, token):
        self.calls = 0
        FakeClient.last = self

    def candles(self, key, frm, to):
        if self.calls >= hu.MAX_CALLS_PER_RUN:
            return None, "call limit for this run reached"
        self.calls += 1
        if key in FakeClient.failing:
            return None, "HTTP 500"
        return [candle(to, 100.0 + self.calls)], None


def run_historical(tmp, symbols_in_universe, equities, prior=None, budget=150, failing=(), env_symbols=None, bench=False):
    t = Path(tmp)
    if bench:
        (t / "out").mkdir(parents=True, exist_ok=True)
        (t / "out" / "historical.json").write_text(json.dumps({"benchmark": {"symbol": "NIFTY 50", "instrument_key": "k", "candles": [candle("2026-10-01")]}, "stocks": {}}), encoding="utf-8")
    up = t / "u.json"
    up.write_text(json.dumps(uni_doc(symbols_in_universe)), encoding="utf-8")
    saved = {k: getattr(hu, k) for k in ("OUT_FILE", "Client", "load_instrument_file", "last_complete_day", "get_token", "MAX_CALLS_PER_RUN")}
    hu.OUT_FILE, hu.Client, hu.get_token, hu.MAX_CALLS_PER_RUN = t / "out" / "historical.json", FakeClient, (lambda: "TOKEN"), budget
    hu.load_instrument_file = lambda: (equities, [{"instrument_key": "NSE_INDEX|Nifty 50", "name": "Nifty 50"}])
    hu.last_complete_day = lambda: END
    FakeClient.failing = set(failing)
    for sym, days in (prior or {}).items():
        shards.write_shard(t, "historical", sym, {"updated": "2026-09-01"}, {"symbol": sym, "isin": "X", "instrument_key": "NSE_EQ|" + sym, "candles": [candle(d) for d in days]})
    out, code = io.StringIO(), 0
    try:
        with Env(STOCKLENS_UNIVERSE_FILE=str(up), HISTORICAL_SYMBOLS=env_symbols, GITHUB_STEP_SUMMARY=None):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                hu.main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    finally:
        for k, v in saved.items():
            setattr(hu, k, v)
    return code, out.getvalue(), FakeClient.last


def eq_meta(*syms):
    return {s: {"isin": "INE%s" % s, "name": s, "instrument_key": "NSE_EQ|" + s} for s in syms}


class HistoricalCoverageTests(unittest.TestCase):
    def test_every_stock_gets_its_file_the_single_file_keeps_only_the_development_stocks(self):
        with tempfile.TemporaryDirectory() as t:
            code, log, _ = run_historical(t, ["TCS", "ALPHA"], eq_meta("TCS", "ALPHA"))
            self.assertEqual(code, 0, log)
            self.assertEqual(sorted(shards.build_index(t)["historical"]), ["ALPHA", "TCS"])
            single = json.loads((Path(t) / "out" / "historical.json").read_text())
            self.assertEqual(list(single["stocks"]), ["TCS"])                      # ALPHA is not a development stock
            self.assertTrue(single["benchmark"]["candles"])                        # Relative Strength keeps working
            one = shards.read_shard(t, "historical", "ALPHA")
            self.assertEqual(list(one["stocks"]), ["ALPHA"])
            self.assertEqual(one["interval"], "1day")                              # same document shape as the single file

    def test_one_failing_stock_does_not_stop_or_discard_the_others(self):
        with tempfile.TemporaryDirectory() as t:
            code, log, _ = run_historical(t, ["ALPHA", "BETA", "TCS"], eq_meta("ALPHA", "BETA", "TCS"), failing={"NSE_EQ|BETA"})
            self.assertEqual(code, 0, log)
            self.assertEqual(sorted(shards.build_index(t)["historical"]), ["ALPHA", "TCS"])
            self.assertIn("BETA", log)                                              # the problem is reported ...
            self.assertIsNone(shards.read_shard(t, "historical", "BETA"))           # ... and nothing is invented for it

    def test_a_failing_stock_keeps_its_old_candles(self):
        with tempfile.TemporaryDirectory() as t:
            code, log, _ = run_historical(t, ["ALPHA", "TCS"], eq_meta("ALPHA", "TCS"), prior={"ALPHA": ["2026-09-01", "2026-09-02"]}, failing={"NSE_EQ|ALPHA"})
            self.assertEqual(code, 0, log)
            d = shards.read_shard(t, "historical", "ALPHA")
            self.assertEqual([c["date"] for c in d["stocks"]["ALPHA"]["candles"]], ["2026-09-01", "2026-09-02"])

    def test_nothing_updated_at_all_is_a_failed_run(self):
        with tempfile.TemporaryDirectory() as t:
            code, log, _ = run_historical(t, ["ALPHA"], eq_meta("ALPHA"), failing={"NSE_EQ|ALPHA"})
            self.assertNotEqual(code, 0)

    def test_a_first_fill_is_never_started_without_the_budget_to_finish_it(self):
        with tempfile.TemporaryDirectory() as t:
            code, log, client = run_historical(t, ["ALPHA"], eq_meta("ALPHA"), budget=3)      # a 5-year first fill needs about 6 requests
            self.assertEqual(code, 0, log)
            self.assertIsNone(shards.read_shard(t, "historical", "ALPHA"))                   # no half-fetched history
            self.assertIn("not reached", log.lower())
            self.assertLessEqual(client.calls, 3)

    def test_the_stalest_stock_goes_first_when_the_budget_is_short(self):
        with tempfile.TemporaryDirectory() as t:
            prior = {"AAA": ["2026-10-01"], "ZZZ": ["2026-08-01"]}                           # ZZZ is far staler although it sorts last
            code, log, client = run_historical(t, ["AAA", "ZZZ"], eq_meta("AAA", "ZZZ"), prior=prior, budget=2, bench=True)   # benchmark + one stock
            self.assertEqual(code, 0, log)
            self.assertEqual(shards.read_shard(t, "historical", "ZZZ")["stocks"]["ZZZ"]["candles"][-1]["date"], END.isoformat())
            self.assertEqual(shards.read_shard(t, "historical", "AAA")["stocks"]["AAA"]["candles"][-1]["date"], "2026-10-01")   # untouched this run

    def test_a_symbol_override_still_works(self):
        with tempfile.TemporaryDirectory() as t:
            code, log, _ = run_historical(t, ["ALPHA", "BETA"], eq_meta("ALPHA", "BETA"), env_symbols="BETA")
            self.assertEqual(code, 0, log)
            self.assertEqual(list(shards.build_index(t)["historical"]), ["BETA"])


# ---------------------------------------------------------------- fundamentals
class FakeFund:
    def __init__(self, token):
        self.calls = 0

    def get(self, path):
        if self.calls >= fu.MAX_CALLS_PER_RUN:
            return None, "call limit for this run reached"
        self.calls += 1
        if path.endswith("/profile"):
            return {"data": {"sector": "Secret Sector"}}, None
        return {"data": [{"name": "P/E", "company_value": "12.5"}, {"name": "ROE", "company_value": "18"}]}, None


def run_fund(tmp, symbols, budget):
    t = Path(tmp)
    up = t / "u.json"
    up.write_text(json.dumps(uni_doc(symbols)), encoding="utf-8")
    saved = {k: getattr(fu, k) for k in ("CACHE_FILE", "OUT_FILE", "Upstox", "load_instruments", "MAX_CALLS_PER_RUN")}
    fu.CACHE_FILE, fu.OUT_FILE, fu.Upstox, fu.MAX_CALLS_PER_RUN = t / "data" / "c.json", t / "out" / "fundamentals.json", FakeFund, budget
    fu.load_instruments = lambda: {s: {"isin": "INE" + s, "name": s} for s in symbols}
    code = 0
    try:
        with Env(UPSTOX_ANALYTICS_TOKEN="T", STOCKLENS_UNIVERSE_FILE=str(up)):
            with contextlib.redirect_stdout(io.StringIO()):
                fu.main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    finally:
        for k, v in saved.items():
            setattr(fu, k, v)
    out = json.loads((t / "out" / "fundamentals.json").read_text()) if (t / "out" / "fundamentals.json").exists() else None
    cache = json.loads((t / "data" / "c.json").read_text()) if (t / "data" / "c.json").exists() else None
    return code, out, cache


class FundamentalsCoverageTests(unittest.TestCase):
    def test_sector_stays_public_only_for_the_development_stocks(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, cache = run_fund(t, ["TCS", "ALPHA"], 20)
            self.assertEqual(code, 0)
            rows = {r["symbol"]: r for r in out["stocks"]}
            self.assertEqual(rows["TCS"]["sector"], "Secret Sector")             # unchanged behaviour for the 10
            self.assertIsNone(rows["ALPHA"]["sector"])                           # held back until the sector gate is opened
            self.assertEqual(json.dumps(out).count("Secret Sector"), 1)           # only in the development stock's row
            self.assertEqual(cache["stocks"]["ALPHA"]["sector"], "Secret Sector")   # kept privately (the cache is never published)

    def test_running_out_of_budget_is_not_an_error_and_the_rest_goes_next_time(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, cache = run_fund(t, ["ALPHA", "BETA", "GAMMA"], 4)         # 2 calls per stock: only two stocks fit
            self.assertEqual(code, 0)
            self.assertEqual(out["errors"], [])
            self.assertEqual(sorted(r["symbol"] for r in out["stocks"]), ["ALPHA", "BETA"])
            code, out, cache = run_fund(t, ["ALPHA", "BETA", "GAMMA"], 4)         # second run: the never-fetched stock is first
            self.assertIn("GAMMA", {r["symbol"] for r in out["stocks"]})


# ---------------------------------------------------------------- shareholding / financial history
class OutputTests(unittest.TestCase):
    def test_shareholding_output_covers_the_requested_symbols_only(self):
        ledger = {"stocks": {s: {"symbol": s, "isin": None, "name": s, "quarters": []} for s in ("TCS", "ALPHA")}}
        doc = shu.build_output(ledger, {}, "2026-10-08", ["TCS", "ALPHA"])
        self.assertEqual(sorted(doc["stocks"]), ["ALPHA", "TCS"])
        self.assertEqual(sorted(shu.build_output(ledger, {}, "2026-10-08")["stocks"]), ["TCS"])     # default = the 10 development stocks, as before

    def test_shareholding_rejects_symbols_outside_the_universe(self):
        import shareholding_ledger as sl
        with tempfile.TemporaryDirectory() as t:
            up = Path(t) / "u.json"
            up.write_text(json.dumps(uni_doc(["TCS"])), encoding="utf-8")
            (Path(t) / "ledger").mkdir()
            (Path(t) / "ledger" / "shareholding_ledger.json").write_text(json.dumps(sl.new_ledger()), encoding="utf-8")
            with Env(STOCKLENS_UNIVERSE_FILE=str(up), SHAREHOLDING_SYMBOLS="ZZZ", SHAREHOLDING_LEDGER_DIR=str(Path(t) / "ledger"), SHAREHOLDING_INIT="false"):
                with contextlib.redirect_stdout(io.StringIO()) as o:
                    code = shu.main()
            self.assertEqual(code, 1)
            self.assertIn("Unknown symbols", o.getvalue())


class FinancialHistoryCoverageTests(unittest.TestCase):
    def test_each_stock_gets_its_own_file_and_the_private_sector_is_still_used(self):
        import test_financial_history_storage as tfs
        def private_cache(m):
            c = Path(tempfile.mkdtemp()) / "fundamentals_cache.json"
            c.write_text(json.dumps({"stocks": {"TCS": {"sector": "Banking Services"}}}), encoding="utf-8")
            m.FUNDAMENTALS_CACHE = c
        saved = fh.FUNDAMENTALS_CACHE
        try:
            r = tfs.Run(branch_ledger=tfs.old_ledger(), patch=private_cache)
        finally:
            fh.FUNDAMENTALS_CACHE = saved
        self.addCleanup(r.close)
        self.assertEqual(r.code, 0, r.stdout)
        self.assertEqual(r.ledger()["stocks"]["TCS"]["statement_layout"], "financial")        # the sector came from the private cache, not from the public file
        one = shards.read_shard(r.tmp.name, "financial_history", "TCS")
        self.assertEqual(list(one["stocks"]), ["TCS"])
        self.assertNotIn("sector", json.dumps(one["errors"]))
        single = json.loads(r.out_doc.read_text())
        self.assertEqual(list(single["stocks"]), ["TCS"])


# ---------------------------------------------------------------- workflow wiring (text checks)
class WorkflowTests(unittest.TestCase):
    def wf(self, name):
        return (HERE / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_the_universe_workflow_is_manual_stage1_only_and_commits_one_file(self):
        t = self.wf("universe.yml")
        self.assertIn("workflow_dispatch", t)
        for banned in ("schedule", "push:", "pull_request", "deploy-pages", "UPSTOX_ANALYTICS_TOKEN", "secrets."):
            self.assertNotIn(banned, t)
        self.assertIn("nifty500", t)
        self.assertIn('!= "universe.json"', t)
        self.assertNotRegex(t, r"git push (-f|--force)")

    def test_every_updater_workflow_selects_the_universe_from_the_data_branch(self):
        for name in ("update.yml", "historical.yml", "financial_history.yml", "shareholding.yml"):
            t = self.wf(name)
            self.assertIn("python universe.py check ledger-branch/universe.json", t, name)
            self.assertIn("STOCKLENS_UNIVERSE_FILE", t, name)
            self.assertIn("if [ -f ledger-branch/universe.json ]", t, name)

    def test_the_site_builds_publish_the_per_stock_files(self):
        for name in ("update.yml", "market_data.yml"):
            self.assertIn("cp -r out/by_symbol _site/out/by_symbol", self.wf(name), name)

    def test_long_jobs_have_explicit_time_limits(self):
        for name in ("update.yml", "historical.yml", "financial_history.yml", "shareholding.yml", "universe.yml"):
            self.assertRegex(self.wf(name), r"timeout-minutes: \d+", name)

    def test_daily_run_budgets_are_bounded(self):
        t = self.wf("update.yml")
        self.assertIn("FUNDAMENTALS_MAX_CALLS", t)
        self.assertIn("FINANCIALS_MAX_CALLS", t)
        self.assertIn("SHAREHOLDING_MAX_STOCKS", t)


if __name__ == "__main__":
    unittest.main()
