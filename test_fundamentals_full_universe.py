"""Tests for the full-universe fundamentals rollout: incremental first fill, call caps, honest coverage counts, explicit missing metrics, failure isolation and back-off,
output compatibility, and the guarantee that a bigger universe.json cannot widen the NSE/XBRL shareholding collector.
Offline only: a scripted fake of the Upstox session stands in for the provider, so these tests prove the updater's accounting and limits, NOT how many real stocks Upstox can serve.
Run: python3 -m unittest test_fundamentals_full_universe"""
import contextlib
import copy
import datetime as dt
import io
import json
import os
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import fundamentals_updater as fu
import universe
import validate_outputs as vo

HERE = Path(__file__).resolve().parent
START = dt.date(2026, 10, 19)                                  # a Monday


class FakeDate(dt.date):
    now = START

    @classmethod
    def today(cls):
        return cls.now


class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body

    def json(self):
        return self._body


def ratio_body(skip=()):
    names = {"P/E": "12.50", "P/B": "2.1", "ROA": "1.2%", "ROE": "18", "ROCE": "21.4", "EV/EBITDA": "9.9"}
    return {"status": "success", "data": [{"name": k, "company_value": v} for k, v in names.items() if k not in skip]}


# behaviour of a symbol is encoded in its first letter: A ok, B bank (no ROCE / EV/EBITDA), C empty ratios, E always 404, F always 500
BEHAVIOUR = {"A": "ok", "B": "bank", "C": "empty", "E": "http404", "F": "http500"}


class Provider:
    """Scripted Upstox: routes by ISIN. Records every request so the tests can count them."""
    def __init__(self, isin_to_behaviour):
        self.by_isin, self.requests = isin_to_behaviour, []
        self.headers = {}

    def get(self, url, timeout=30):
        self.requests.append(url)
        m = re.search(r"/fundamentals/([A-Z0-9]+)/(profile|key-ratios)$", url)
        isin, kind = m.group(1), m.group(2)
        how = self.by_isin[isin]
        if how == "http404":
            return Resp(404, {})
        if how == "http500":
            return Resp(500, {})
        if kind == "profile":
            return Resp(200, {"status": "success", "data": {"sector": "Some Sector"}})
        if how == "bank":
            return Resp(200, ratio_body(skip=("ROCE", "EV/EBITDA")))
        if how == "empty":
            return Resp(200, {"status": "success", "data": []})
        return Resp(200, ratio_body())


def isin_of(i):
    return "INE%08d1" % i


def make_universe(n, letters="A"):
    syms, instruments, behaviours = [], {}, {}
    for i in range(n):
        letter = letters[i % len(letters)]
        sym = "%s%04d" % (letter, i)
        syms.append({"symbol": sym, "isin": isin_of(i), "name": "STOCK " + sym})
        instruments[sym] = {"isin": isin_of(i), "name": "STOCK " + sym}
        behaviours[isin_of(i)] = BEHAVIOUR[letter]
    doc = {"schema_version": 1, "kind": "universe", "stage": "directory", "source": "test", "as_of": "2026-10-14", "count": n, "symbols": syms}
    return doc, instruments, behaviours


class Harness:
    """One data directory, a universe file and a provider; run() = one scheduled run on the current FakeDate day."""
    def __init__(self, test, n, letters="A", missing_from_upstox=(), cap=150):
        self.t = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__("shutil").rmtree(self.t, ignore_errors=True))
        self.doc, self.instruments, behaviours = make_universe(n, letters)
        for s in missing_from_upstox:
            self.instruments.pop(s, None)
        self.provider = Provider(behaviours)
        (self.t / "u.json").write_text(json.dumps(self.doc), encoding="utf-8")
        self.cap, self.rc = cap, None
        FakeDate.now = START

    def run(self, cap=None, day=None):
        if day is not None:
            FakeDate.now = day
        cap = self.cap if cap is None else cap
        p = [mock.patch.object(fu, "CACHE_FILE", self.t / "data" / "c.json"), mock.patch.object(fu, "OUT_FILE", self.t / "out" / "fundamentals.json"),
             mock.patch.object(fu, "MAX_CALLS_PER_RUN", cap), mock.patch.object(fu, "load_instruments", lambda: self.instruments),
             mock.patch.object(fu, "dt", types.SimpleNamespace(date=FakeDate)), mock.patch.object(fu.time, "sleep"),
             mock.patch.object(fu.requests, "Session", lambda: self.provider),
             mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": "T", "STOCKLENS_UNIVERSE_FILE": str(self.t / "u.json")})]
        before = len(self.provider.requests)
        out = io.StringIO()
        try:
            with contextlib.ExitStack() as st:
                for x in p:
                    st.enter_context(x)
                with contextlib.redirect_stdout(out):
                    fu.main()
            self.rc = 0
        except SystemExit as e:
            self.rc = e.code if e.code is not None else 0
        self.requests_this_run = len(self.provider.requests) - before
        self.log = out.getvalue()
        return self.output()

    def output(self):
        f = self.t / "out" / "fundamentals.json"
        return json.loads(f.read_text()) if f.exists() else None

    def cache(self):
        return json.loads((self.t / "data" / "c.json").read_text())


class IncrementalFirstFill(unittest.TestCase):
    def test_a_full_size_first_run_stops_at_its_cap_and_reports_the_rest_as_remaining(self):
        h = Harness(self, 2548, cap=150)
        out = h.run()
        self.assertEqual(h.rc, 0, h.log)
        cov = out["coverage"]
        self.assertEqual(h.requests_this_run, 150)                                     # exactly the cap: two requests per stock, nothing more
        self.assertEqual((cov["universe"], cov["published"], cov["not_yet_fetched"]), (2548, 75, 2548 - 75))
        self.assertEqual(cov["last_run"]["processed"], 75)
        self.assertEqual(cov["last_run"]["not_reached"], 2548 - 75)
        self.assertEqual((cov["last_run"]["calls"], cov["last_run"]["call_cap"]), (150, 150))
        self.assertEqual(vo.check_fundamentals(out), [])
        self.assertEqual(len(out["stocks"]), 75)                                       # only stocks with real ratios are published

    def test_repeated_runs_resume_and_finish_the_universe_without_refetching_fresh_stocks(self):
        h = Harness(self, 300, cap=100)
        seen = []
        for k in range(8):
            out = h.run(day=START + dt.timedelta(days=k % 5 if k < 5 else 5))          # six days: nothing is old enough for a refresh
            self.assertLessEqual(h.requests_this_run, 100)
            seen.append(out["coverage"]["published"])
            if out["coverage"]["published"] == 300:
                break
        self.assertEqual(seen, [50, 100, 150, 200, 250, 300])                          # 50 stocks per 100-request run, no repeats, no gaps
        total_before = len(h.provider.requests)
        self.assertEqual(total_before, 600)                                             # two requests per stock, once
        out = h.run(day=START + dt.timedelta(days=5))                                   # everything is fresh: nothing to ask
        self.assertEqual(h.requests_this_run, 0)
        self.assertEqual(out["coverage"]["last_run"]["up_to_date"], 300)
        self.assertEqual(out["coverage"]["not_yet_fetched"], 0)
        self.assertEqual(vo.check_fundamentals(out), [])

    def test_the_stalest_stocks_are_refreshed_first_when_the_ratios_age_out(self):
        h = Harness(self, 40, cap=1000)
        h.run(day=START)
        cache = h.cache()
        for i, sym in enumerate(sorted(cache["stocks"])):                               # stagger the fetch dates
            cache["stocks"][sym]["ratios_fetched"] = (START - dt.timedelta(days=i % 4)).isoformat()
        (h.t / "data" / "c.json").write_text(json.dumps(cache))
        h.run(cap=20, day=START + dt.timedelta(days=9))                                 # 9 days later all are stale; only 10 stocks fit (ratios only: profile is fresh)
        fetched_today = {s for s, e in h.cache()["stocks"].items() if e["ratios_fetched"] == (START + dt.timedelta(days=9)).isoformat()}
        self.assertEqual(len(fetched_today), 20)                                        # the profile (90 days) is still fresh, so one request per stock
        oldest = {s for s, e in cache["stocks"].items() if e["ratios_fetched"] == (START - dt.timedelta(days=3)).isoformat()}
        self.assertTrue(oldest <= fetched_today)                                        # the oldest group was refreshed first

    def test_every_retry_counts_against_the_cap(self):
        h = Harness(self, 30, letters="F", cap=40)                                      # every request answers HTTP 500: three attempts each
        h.run()
        self.assertEqual(h.rc, 1)                                                       # nothing could be fetched: the run says so (and keeps the previous output)
        self.assertEqual(h.requests_this_run, 40)                                       # the cap is exact: the third attempt of a retry is not made once it is reached


class CoverageAccounting(unittest.TestCase):
    def setUp(self):
        # 40 stocks: A ok x10, B bank x10, C empty x10, E 404 x5, plus 5 symbols Upstox does not serve (they cycle through the letters: i % 4)
        self.h = Harness(self, 40, letters="ABCE", cap=1000)

    def test_counts_are_exact_and_separate_inclusion_from_completeness(self):
        h = self.h
        missing = {"A0004", "B0001"}                                                   # two symbols Upstox does not serve
        h.instruments = {s: v for s, v in h.instruments.items() if s not in missing}
        out = h.run()
        cov, rows = out["coverage"], {r["symbol"]: r for r in out["stocks"]}
        self.assertEqual(cov["universe"], 40)
        # 40 symbols: letters cycle A,B,C,E -> 10 each; E (404) never publishes; two removed from Upstox
        self.assertEqual(cov["not_in_upstox"], 2)
        self.assertEqual(cov["with_error"], 10)                                         # the ten HTTP 404 stocks
        self.assertEqual(cov["published"], 28)                                          # 10 A + 10 B + 10 C minus the two not served
        self.assertEqual((cov["metrics_complete"], cov["metrics_partial"], cov["metrics_none"]), (9, 9, 10))
        self.assertEqual(cov["metric_available"], {"pe": 18, "pb": 18, "roa": 18, "roe": 18, "roce": 9, "ev_ebitda": 9})
        self.assertEqual(cov["not_yet_fetched"], 10)                                    # the 404 stocks: in the universe, no row, still pending
        run = cov["last_run"]
        self.assertEqual((run["succeeded"], run["partial"], run["failed"], run["not_reached"]), (9, 19, 10, 0))
        self.assertEqual(run["processed"], 38)
        self.assertEqual(vo.check_fundamentals(out), [])
        self.assertFalse(any(s in rows for s in missing))

    def test_a_missing_metric_stays_null_and_is_listed_never_filled_in(self):
        out = self.h.run()
        rows = {r["symbol"]: r for r in out["stocks"]}
        ok, bank, empty = rows["A0000"], rows["B0001"], rows["C0002"]
        self.assertEqual((ok["missing"], ok["pe"], ok["roce"], ok["ev_ebitda"]), ([], 12.5, 21.4, 9.9))
        self.assertEqual((bank["missing"], bank["roce"], bank["ev_ebitda"], bank["pe"]), (["roce", "ev_ebitda"], None, None, 12.5))
        self.assertEqual(empty["missing"], ["pe", "pb", "roa", "roe", "roce", "ev_ebitda"])
        for k in ("pe", "pb", "roa", "roe", "roce", "ev_ebitda"):
            self.assertIsNone(empty[k], k)
        for r in out["stocks"]:
            self.assertIsNone(r["revenue_growth"])                                      # still not fetched: null, not invented
            self.assertIsNone(r["profit_growth"])
        self.assertIn("never filled in", out["coverage"]["note"])

    def test_one_failing_stock_does_not_stop_the_others_and_is_listed_as_a_problem(self):
        out = self.h.run()
        self.assertEqual(self.h.rc, 0, self.h.log)
        bad = {e["symbol"] for e in out["errors"]}
        self.assertEqual(len(bad), 10)
        self.assertTrue(all(s.startswith("E") for s in bad))
        self.assertTrue(any(r["symbol"].startswith("A") for r in out["stocks"]))
        self.assertTrue(all("HTTP 404" in e["error"] for e in out["errors"]))

    def test_the_sector_gate_is_unchanged_for_stocks_outside_the_development_set(self):
        out = self.h.run()
        self.assertTrue(all(r["sector"] is None for r in out["stocks"]))
        self.assertEqual(json.dumps(out).count("Some Sector"), 0)

    def test_a_run_log_reports_the_counts(self):
        self.h.run()
        self.assertRegex(self.h.log, r"Coverage: universe 40 \| published 30 \(all metrics 10, some missing 10, none 10\)")
        self.assertRegex(self.h.log, r"This run: processed 40 \(succeeded 10, partial 20, failed 10\)")


class Backoff(unittest.TestCase):
    def test_stocks_that_keep_failing_cannot_starve_the_rest_of_the_universe(self):
        # 20 always-404 stocks sort first alphabetically ("E..." < "G..."), 30 good ones follow; the cap fits 20 stocks a run
        h = Harness(self, 50, cap=40)
        renamed = {}
        for i in range(50):
            sym = ("E%04d" if i < 20 else "G%04d") % i
            old = [s for s, v in h.instruments.items() if v["isin"] == isin_of(i)][0]
            h.instruments[sym] = h.instruments.pop(old)
            h.provider.by_isin[isin_of(i)] = "http404" if i < 20 else "ok"
            renamed[old] = sym
        h.doc["symbols"] = [dict(x, symbol=renamed[x["symbol"]]) for x in h.doc["symbols"]]
        (h.t / "u.json").write_text(json.dumps(h.doc), encoding="utf-8")
        h.run(day=START)                                                                # run 1: the 20 failing stocks use the whole cap
        self.assertEqual(h.output() and h.output()["coverage"]["published"] if h.rc == 0 else 0, 0)
        for k in (1, 2):
            out = h.run(day=START + dt.timedelta(days=k))
        self.assertEqual(h.rc, 0, h.log)
        self.assertEqual(out["coverage"]["published"], 30)                              # all 30 good stocks are in by the third run
        self.assertEqual(out["coverage"]["with_error"], 20)

    def test_the_back_off_key_only_moves_a_failed_stock_back(self):
        self.assertEqual(fu.staleness_key(None), "")
        self.assertEqual(fu.staleness_key({"profile_fetched": "2026-10-01", "ratios_fetched": "2026-10-05"}), "2026-10-01")
        self.assertEqual(fu.staleness_key({"last_error": "HTTP 404", "last_attempt": "2026-10-19"}), "2026-10-19")      # failed today: goes to the back
        self.assertEqual(fu.staleness_key({"last_error": None, "last_attempt": "2026-10-19", "ratios_fetched": "2026-10-02", "profile_fetched": "2026-10-02"}), "2026-10-02")
        self.assertEqual(fu.staleness_key({"last_error": "x", "last_attempt": "2026-09-01", "ratios_fetched": "2026-10-02", "profile_fetched": "2026-10-02"}), "2026-10-02")


class OutputCompatibility(unittest.TestCase):
    OLD_ROW_KEYS = {"symbol", "isin", "company_name", "sector", "pe", "pb", "roa", "roe", "roce", "ev_ebitda", "revenue_growth", "profit_growth"}

    def test_the_old_fields_are_all_still_there_and_the_new_ones_are_additive(self):
        h = Harness(self, 12, letters="AB", cap=1000)
        out = h.run()
        self.assertEqual(set(out), {"as_of", "source", "stocks", "errors", "coverage"})
        for r in out["stocks"]:
            self.assertEqual(set(r) - self.OLD_ROW_KEYS, {"missing", "fetched"})
            self.assertTrue(self.OLD_ROW_KEYS <= set(r))
        self.assertEqual(out["source"], "Upstox")
        self.assertRegex(out["as_of"], r"^\d{4}-\d{2}-\d{2}$")
        text = (h.t / "out" / "fundamentals.json").read_text()
        json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))   # strict JSON: no NaN or Infinity

    def test_the_page_reads_rows_by_field_name_only(self):
        src = (HERE / "index.html").read_text(encoding="utf-8")
        self.assertIn("FD=j.stocks", src)
        self.assertNotRegex(src, r"Object\.keys\((x|r|row|FD\[\d\])\)")                 # the screener never enumerates a row's keys, so extra keys cannot break it

    def test_the_validator_rejects_an_inconsistent_missing_list_or_coverage_block(self):
        h = Harness(self, 12, letters="AB", cap=1000)
        good = h.run()
        self.assertEqual(vo.check_fundamentals(good), [])

        def broken(fn):
            d = copy.deepcopy(good)
            fn(d)
            return vo.check_fundamentals(d)
        bank = next(i for i, r in enumerate(good["stocks"]) if r["missing"])
        self.assertTrue(broken(lambda d: d["stocks"][bank].update(missing=[])))
        self.assertTrue(broken(lambda d: d["stocks"][0].update(missing=["pe"])))
        self.assertTrue(broken(lambda d: d["stocks"][bank].update(missing=["ev_ebitda", "roce"])))        # wrong order
        self.assertTrue(broken(lambda d: d["coverage"].update(published=d["coverage"]["published"] + 1)))
        self.assertTrue(broken(lambda d: d["coverage"].update(metrics_complete=d["coverage"]["metrics_complete"] + 1)))
        self.assertTrue(broken(lambda d: d["coverage"].update(universe=1)))
        self.assertTrue(broken(lambda d: d["coverage"]["metric_available"].update(pe=0)))
        self.assertTrue(broken(lambda d: d["coverage"].update(not_yet_fetched=-1)))
        self.assertTrue(broken(lambda d: d.update(coverage="x")))
        legacy = copy.deepcopy(good)
        for r in legacy["stocks"]:
            r.pop("missing"), r.pop("fetched")
        legacy.pop("coverage")
        self.assertEqual(vo.check_fundamentals(legacy), [])                             # an output from the previous version is still valid


class UniverseGuards(unittest.TestCase):
    def test_a_broken_universe_file_stops_the_run_and_never_shrinks_coverage(self):
        h = Harness(self, 10, cap=100)
        (h.t / "u.json").write_text("{not json")
        h.run()
        self.assertEqual(h.rc, 1)
        self.assertIn("universe file unreadable", h.log)
        self.assertEqual(h.provider.requests, [])

    def test_duplicate_symbols_duplicate_isins_and_malformed_records_are_rejected_at_full_size(self):
        doc, _, _ = make_universe(2548)
        self.assertEqual(universe.check_doc(doc), [])
        dup_sym = copy.deepcopy(doc)
        dup_sym["symbols"][5]["symbol"] = dup_sym["symbols"][4]["symbol"]
        self.assertTrue(any("duplicate symbol" in p for p in universe.check_doc(dup_sym)))
        dup_isin = copy.deepcopy(doc)
        dup_isin["symbols"][7]["isin"] = dup_isin["symbols"][6]["isin"]
        self.assertTrue(any("duplicate ISIN" in p for p in universe.check_doc(dup_isin)))
        for mutate in (lambda d: d["symbols"][3].update(isin="BAD"), lambda d: d["symbols"][3].update(symbol="lower"), lambda d: d["symbols"][3].update(sector="X"),
                       lambda d: d.update(count=2547), lambda d: d.update(symbols=[]), lambda d: d["symbols"].append("junk")):
            bad = copy.deepcopy(doc)
            mutate(bad)
            self.assertTrue(universe.check_doc(bad))

    def test_the_universe_is_built_from_the_directory_with_the_existing_protections(self):
        import directory_universe as DU
        self.assertTrue(hasattr(DU, "MIN_DIRECTORY"))
        src = (HERE / "directory_universe.py").read_text(encoding="utf-8")
        self.assertIn("build_problems", src)                                           # the 10% maximum-drop protection is still applied
        self.assertIn("check_doc", src)


class NotInUpstoxClassification(unittest.TestCase):
    def test_unserved_symbols_are_classified_even_when_the_budget_is_spent(self):
        h = Harness(self, 40, letters="A", missing_from_upstox=("A0038", "A0039"), cap=4)       # 4 calls = 2 stocks; the unserved ones sort last
        out = h.run()
        cov = out["coverage"]
        self.assertEqual(cov["not_in_upstox"], 2)                                               # local data: no call needed, so not left as "not yet fetched"
        self.assertEqual(cov["not_yet_fetched"], 40 - 2 - 2)
        self.assertEqual(h.requests_this_run, 4)
        self.assertEqual(vo.check_fundamentals(out), [])

    def test_a_stale_not_found_is_cleared_once_upstox_lists_the_symbol(self):
        h = Harness(self, 6, letters="A", missing_from_upstox=("A0005",), cap=1000)
        h.run()
        read = lambda: json.loads((h.t / "data" / "c.json").read_text())["stocks"]["A0005"].get("last_error")
        self.assertEqual(read(), fu.NOT_FOUND_ERROR)
        h.instruments = dict(h.instruments, **{"A0005": {"isin": "INE000000051", "name": "Late Listed"}})
        h.run(cap=0)                                          # no budget at all: the stale "not found" must still be dropped, not carried
        self.assertIsNone(read())


class ShareholdingIsolation(unittest.TestCase):
    def test_no_workflow_opts_the_shareholding_collector_into_the_universe(self):
        for wf in (HERE / ".github" / "workflows").glob("*.yml"):
            self.assertNotIn("SHAREHOLDING_FOLLOW_UNIVERSE", wf.read_text(encoding="utf-8"), wf.name)

    def test_the_collector_has_no_other_path_to_the_universe(self):
        src = (HERE / "shareholding_updater.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"universe\.load(_entries|_doc)?\(", src)), 1)   # one call, inside collection_symbols() and only on the explicit opt-in
        self.assertIn("collection_symbols()", src)

    def test_growing_the_universe_does_not_change_what_the_scheduled_workflow_collects(self):
        import shareholding_updater as su
        wf = (HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        step = wf[wf.index("- name: Update shareholding pattern (NSE/XBRL)"):wf.index("- name: Check the shareholding ledger output")]
        self.assertNotIn("SHAREHOLDING_FOLLOW_UNIVERSE", step)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "u.json"
            doc, _, _ = make_universe(2548)
            f.write_text(json.dumps(doc), encoding="utf-8")
            env = {"STOCKLENS_UNIVERSE_FILE": str(f)}
            self.assertEqual(su.collection_symbols(env), su.collection_symbols({}))
            self.assertEqual(len(su.collection_symbols(env)), 15)


if __name__ == "__main__":
    unittest.main()
