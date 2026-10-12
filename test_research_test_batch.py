"""
research_test_batch.py: the five-stock test batch for the fundamentals and financial-statement updaters ONLY.
SYNTHETIC responses only (invented numbers, fake Upstox client, fake instrument list). No network, no token, no workflow is run, so nothing here shows what Upstox
really returns for DOMS: that can only be seen on a real run.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import financials_updater as FIN
import fundamentals_updater as FUN
import guard_update_yml as G
import research_test_batch as RB
import universe

ROOT = Path(__file__).parent
DEV = list(universe.TEST_SYMBOLS)
FIVE = ["DOMS", "20MICRONS", "21STCENMGM", "360ONE", "3BBLACKBIO"]


def isin(sym):
    return "INE" + (re.sub(r"[^A-Z0-9]", "", sym.upper()) + "00000000")[:8] + "0"


def instruments(skip=()):
    return {s: {"isin": isin(s), "name": s + " LIMITED"} for s in DEV + FIVE if s not in skip}


def ratio_body(**kw):
    names = {"pe": "P/E", "pb": "P/B", "roa": "ROA", "roe": "ROE", "roce": "ROCE", "ev_ebitda": "EV/EBITDA"}
    return {"status": "success", "data": [{"name": names[k], "company_value": str(v)} for k, v in kw.items()]}


class FakeFundamentals:
    """Replaces fundamentals_updater.Upstox. DOMS returns only P/E and ROE (the rest is absent, as an incomplete real response could be)."""
    paths = []

    def __init__(self, token):
        self.calls = 0

    def get(self, path):
        if self.calls >= FUN.MAX_CALLS_PER_RUN:
            return None, "call limit for this run reached"
        self.calls += 1
        FakeFundamentals.paths.append(path)
        i, kind = path.split("/")
        if kind == "profile":
            return {"status": "success", "data": {"sector": "Industrials"}}, None
        if i == isin("DOMS"):
            return ratio_body(pe=55.5, roe=31.2), None
        return ratio_body(pe=20, pb=3, roa=7, roe=15, roce=19, ev_ebitda=11), None


INCOME = {"data": {"time_period": "yearly", "income_statement": [], "full_statement": [
    {"particular": "Revenue", "history": [{"period": "Mar 2025", "value": "1200"}]}, {"particular": "Profit After Tax", "history": [{"period": "Mar 2025", "value": "150"}]}]}}
BALANCE = {"data": {"time_period": "yearly", "history": [{"period": "Mar 2025", "total_asset": "900", "total_liability": "400"}], "full_statement": []}}
CASH = {"data": {"cash_flow": [{"category": c, "history": [{"period": "Mar 2025", "value": v}]} for c, v in (("operating", "80"), ("investing", "-30"), ("financing", "-20"))], "full_statement": []}}


class FakeFinancials:
    paths = []

    def __init__(self, token, max_calls):
        self.calls, self.max_calls = 0, max_calls

    def get(self, path, params=None):
        if self.calls >= self.max_calls:
            return None, "call limit for this run reached"
        self.calls += 1
        FakeFinancials.paths.append(path)
        kind = path.split("/")[1]
        return copy.deepcopy({"income-statement": INCOME, "balance-sheet": BALANCE, "cash-flow": CASH}[kind]), None


class Run(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        FakeFundamentals.paths, FakeFinancials.paths = [], []
        env = {k: v for k, v in os.environ.items() if k != "STOCKLENS_UNIVERSE_FILE"}
        env["UPSTOX_ANALYTICS_TOKEN"] = "dummy-not-a-real-token-value"
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def run_fun(self, skip=(), budget=500, cache=None):
        cache = cache or self.t / "fcache.json"
        out = self.t / "fundamentals.json"
        with mock.patch.object(FUN, "CACHE_FILE", cache), mock.patch.object(FUN, "OUT_FILE", out), mock.patch.object(FUN, "Upstox", FakeFundamentals), \
                mock.patch.object(FUN, "load_instruments", return_value=instruments(skip)), mock.patch.object(FUN, "MAX_CALLS_PER_RUN", budget), \
                contextlib.redirect_stdout(io.StringIO()):
            FUN.main()
        return json.loads(out.read_text())

    def run_fin(self, skip=(), budget=500, cache=None):
        cache = cache or self.t / "ccache.json"
        out = self.t / "financials.json"
        with mock.patch.object(FIN, "CACHE_FILE", cache), mock.patch.object(FIN, "OUT_FILE", out), mock.patch.object(FIN, "Upstox", FakeFinancials), \
                mock.patch.object(FIN, "load_instruments", return_value=instruments(skip)), mock.patch.object(FIN, "MAX_CALLS_PER_RUN", budget), \
                contextlib.redirect_stdout(io.StringIO()):
            FIN.main()
        return json.loads(out.read_text())


class Batch(unittest.TestCase):
    def test_the_five_symbols_are_exactly_the_requested_ones_and_not_development_stocks(self):
        self.assertEqual(list(RB.SYMBOLS), FIVE)
        self.assertEqual(len(set(RB.SYMBOLS)), 5)
        self.assertFalse(set(RB.SYMBOLS) & set(DEV))
        for s in RB.SYMBOLS:
            self.assertRegex(s, universe.SYMBOL_RE.pattern)

    def test_extend_adds_the_five_after_the_ten_without_duplicates_or_mutation(self):
        base = list(DEV)
        got = RB.extend(base, {})
        self.assertEqual(got, DEV + FIVE)
        self.assertEqual(base, DEV)                                                       # the input is not changed
        self.assertEqual(RB.extend(got, {}), got)                                         # idempotent
        self.assertEqual(RB.extend(["DOMS", "TCS"], {}), ["DOMS", "TCS", "20MICRONS", "21STCENMGM", "360ONE", "3BBLACKBIO"])

    def test_a_configured_universe_always_wins(self):
        self.assertEqual(RB.extend(["TCS", "INFY"], {"STOCKLENS_UNIVERSE_FILE": "/some/universe.json"}), ["TCS", "INFY"])

    def test_the_shared_universe_is_untouched(self):
        self.assertEqual(universe.load({}), DEV)                                          # every other updater still gets exactly the ten
        self.assertEqual(universe.fallback_symbols(), DEV)
        self.assertEqual(len(universe.TEST_SYMBOLS), 10)


class Fundamentals(Run):
    def test_the_five_and_the_ten_are_all_requested(self):
        doc = self.run_fun()
        asked = {p.split("/")[0] for p in FakeFundamentals.paths}
        self.assertEqual(asked, {isin(s) for s in DEV + FIVE})
        for s in DEV + FIVE:
            self.assertIn(isin(s) + "/profile", FakeFundamentals.paths)
            self.assertIn(isin(s) + "/key-ratios", FakeFundamentals.paths)
        self.assertEqual(len(FakeFundamentals.paths), 30)                                 # 15 stocks x 2 calls, as before
        self.assertEqual(sorted(r["symbol"] for r in doc["stocks"]), sorted(DEV + FIVE))

    def test_the_development_stocks_are_published_exactly_as_without_the_batch(self):
        with_batch = self.run_fun()
        FakeFundamentals.paths = []
        (self.t / "fundamentals.json").unlink()
        with mock.patch.object(RB, "SYMBOLS", ()):
            without = self.run_fun(cache=self.t / "other_cache.json")
        pick = lambda d: sorted((r for r in d["stocks"] if r["symbol"] in DEV), key=lambda r: r["symbol"])
        self.assertEqual(pick(with_batch), pick(without))
        self.assertEqual(len(FakeFundamentals.paths), 20)                                 # without the batch: the old 10 x 2

    def test_sector_stays_gated_and_missing_fields_stay_missing(self):
        doc = self.run_fun()
        rows = {r["symbol"]: r for r in doc["stocks"]}
        for s in DEV:
            self.assertEqual(rows[s]["sector"], "Industrials")                          # development stocks keep their sector, as before
        for s in FIVE:
            self.assertIsNone(rows[s]["sector"], s)                                       # the sector gate: unpublished for any other stock
        d = rows["DOMS"]
        self.assertEqual((d["pe"], d["roe"]), (55.5, 31.2))
        for k in ("pb", "roa", "roce", "ev_ebitda", "revenue_growth", "profit_growth"):
            self.assertIsNone(d[k], k)                                                    # Upstox returned no such value: it stays missing, nothing is derived
        self.assertEqual(d["company_name"], "DOMS LIMITED")

    def test_a_stock_upstox_does_not_serve_is_reported_alone(self):
        doc = self.run_fun(skip=("360ONE",))
        self.assertNotIn("360ONE", [r["symbol"] for r in doc["stocks"]])
        self.assertEqual([e for e in doc["errors"] if e["symbol"] == "360ONE"], [{"symbol": "360ONE", "error": "Symbol not found in Upstox NSE equity instruments"}])
        self.assertEqual(sorted(r["symbol"] for r in doc["stocks"]), sorted([s for s in DEV + FIVE if s != "360ONE"]))

    def test_the_call_budget_is_unchanged_and_new_stocks_go_first(self):
        self.run_fun(budget=12)
        self.assertEqual(len(FakeFundamentals.paths), 12)
        asked = [p.split("/")[0] for p in FakeFundamentals.paths]
        for s in FIVE:
            self.assertIn(isin(s), asked, s)                                              # never-fetched stocks come first (stalest first), so DOMS is reached

    def test_a_second_run_fetches_nothing_new(self):
        self.run_fun()
        FakeFundamentals.paths = []
        self.run_fun()
        self.assertEqual(FakeFundamentals.paths, [])                                      # the cache and the refresh ages work as before

    def test_a_configured_universe_replaces_the_batch(self):
        u = self.t / "universe.json"
        universe.write_doc(u, {"schema_version": 1, "kind": "universe", "stage": "x", "source": "t", "as_of": "2026-10-10", "count": 2,
                               "symbols": [{"symbol": "TCS", "isin": isin("TCS"), "name": "TCS"}, {"symbol": "INFY", "isin": isin("INFY"), "name": "INFY"}]})
        with mock.patch.dict(os.environ, {"STOCKLENS_UNIVERSE_FILE": str(u)}):
            doc = self.run_fun()
        self.assertEqual(sorted(r["symbol"] for r in doc["stocks"]), ["INFY", "TCS"])


class Financials(Run):
    def test_the_five_and_the_ten_are_all_requested(self):
        doc = self.run_fin()
        asked = {p.split("/")[0] for p in FakeFinancials.paths}
        self.assertEqual(asked, {isin(s) for s in DEV + FIVE})
        for s in FIVE:
            for kind in ("income-statement", "balance-sheet", "cash-flow"):
                self.assertIn(isin(s) + "/" + kind, FakeFinancials.paths, s)
        self.assertEqual(len(FakeFinancials.paths), 45)                                   # 15 stocks x 3 sections (consolidated answered)
        self.assertEqual(sorted(r["symbol"] for r in doc["stocks"]), sorted(DEV + FIVE))

    def test_the_development_stocks_are_published_exactly_as_without_the_batch(self):
        with_batch = self.run_fin()
        FakeFinancials.paths = []
        (self.t / "financials.json").unlink()
        with mock.patch.object(RB, "SYMBOLS", ()):
            without = self.run_fin(cache=self.t / "other_cache.json")
        pick = lambda d: sorted((r for r in d["stocks"] if r["symbol"] in DEV), key=lambda r: r["symbol"])
        self.assertEqual(pick(with_batch), pick(without))
        self.assertEqual(len(FakeFinancials.paths), 30)

    def test_values_come_only_from_the_response_and_gaps_stay_empty(self):
        rows = {r["symbol"]: r for r in self.run_fin()["stocks"]}
        d = rows["DOMS"]
        self.assertEqual((d["income"]["revenue"], d["income"]["profit_after_tax"]), (1200.0, 150.0))
        self.assertIsNone(d["income"]["eps_basic"])                                       # not in the response: not estimated
        self.assertIsNone(d["income"]["total_revenue"])
        self.assertEqual((d["balance_sheet"]["total_assets"], d["balance_sheet"]["total_equity"]), (900.0, 500.0))
        self.assertIsNone(d["balance_sheet"]["total_debt"])
        self.assertIsNone(d["cash_flow"]["free_cash_flow"])                               # no capex line: never derived

    def test_a_stock_upstox_does_not_serve_is_reported_alone(self):
        doc = self.run_fin(skip=("3BBLACKBIO",))
        self.assertNotIn("3BBLACKBIO", [r["symbol"] for r in doc["stocks"]])
        self.assertEqual([e["error"] for e in doc["errors"] if e["symbol"] == "3BBLACKBIO"], ["Symbol not found in Upstox NSE equity instruments"])
        self.assertEqual(len(doc["stocks"]), 14)

    def test_the_call_budget_is_unchanged(self):
        self.run_fin(budget=9)
        self.assertEqual(len(FakeFinancials.paths), 9)                                    # 3 stocks x 3 sections, then "not reached"
        self.assertEqual({p.split("/")[0] for p in FakeFinancials.paths}, {isin(s) for s in ("20MICRONS", "21STCENMGM", "360ONE")})

    def test_a_second_run_fetches_nothing_new(self):
        self.run_fin()
        FakeFinancials.paths = []
        self.run_fin()
        self.assertEqual(FakeFinancials.paths, [])


class Isolation(unittest.TestCase):
    OTHERS = ["historical_updater.py", "historical_batch.py", "historical_handoff.py", "shards.py",
              "nse_updater.py", "universe.py", "upstox_common.py", "stock_directory.py", "sector_publish.py", "sector_master.py", "sector_data_updater.py", "validate_outputs.py",
              ".github/workflows/update.yml", ".github/workflows/historical.yml", ".github/workflows/save_historical.yml", ".github/workflows/save_ledger.yml",
              ".github/workflows/financial_history.yml", ".github/workflows/shareholding.yml", ".github/workflows/universe.yml", ".github/workflows/sector_data.yml",
              ".github/workflows/market_data.yml", "company_profiles.json"]

    def test_only_the_four_updaters_import_the_batch(self):
        users = sorted(p.name for p in ROOT.glob("*.py") if "research_test_batch" in p.read_text() and not p.name.startswith("test_") and p.name != "research_test_batch.py")
        self.assertEqual(users, ["financial_history_updater.py", "financials_updater.py", "fundamentals_updater.py", "shareholding_updater.py"])
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            self.assertNotIn("research_test_batch", wf.read_text(), wf.name)

    def test_every_other_updater_workflow_and_page_is_identical_to_the_commit(self):
        r = subprocess.run(["git", "diff", "--name-only", "HEAD", "--"] + self.OTHERS, cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertEqual([f for f in r.stdout.split() if not G.is_approved(ROOT, f)], [])      # update.yml / market_data.yml may carry only the approved SENSEX guard lines

    def test_index_html_differs_from_the_commit_only_by_the_market_cap_header(self):
        head = subprocess.run(["git", "show", "HEAD:index.html"], cwd=str(ROOT), capture_output=True)
        if head.returncode != 0:
            self.skipTest("not a git checkout")
        live = re.compile(r'<script type="module" id="stocklens-live">[\s\S]*?</script>\n')                 # the live layer has its own tests (test_live_client.js, test_live_parity.py)
        now, old = live.sub("", (ROOT / "index.html").read_text(encoding="utf-8"), count=1), live.sub("", head.stdout.decode("utf-8"), count=1)
        self.assertEqual(G.without_market_cap(now), G.without_market_cap(old))           # nothing but the approved edits differs
        self.assertIn('data-mk-tabs="index"', now)                                         # the Market Today index tabs are in the page ...
        self.assertNotIn('data-mk-tabs="index"', G.without_market_cap(now))                # ... and reversing them restores the single-card panel
        self.assertNotEqual(G.without_market_cap(now + "<!-- x -->"), G.without_market_cap(old))      # and any other edit is still seen
        self.assertNotEqual(G.without_market_cap(now.replace("Overview", "Overview2", 1)), G.without_market_cap(old))
        self.assertIn('tile("Market Cap (\u20b9 crore)",cr(mc.cap))', now)               # the edit is in the page ...
        self.assertNotIn('tile("Sector",sector', now)                                       # ... and the header Sector tile is gone
        self.assertEqual(now.count('tile("Sector"'), 0)
        self.assertIn('Sector', now)                                                        # the Business-section Sector, the table column and the checklist row are untouched

    # --- Stage 8D: the Stage 8C market-activity edit is reversed exactly, and only that edit
    def _page_pair(self):
        head = subprocess.run(["git", "show", "HEAD:index.html"], cwd=str(ROOT), capture_output=True)
        if head.returncode != 0:
            self.skipTest("not a git checkout")
        return (ROOT / "index.html").read_text(encoding="utf-8"), head.stdout.decode("utf-8")

    def test_stage_8c_reversal_round_trips_to_the_pinned_phase2_page(self):
        now, _ = self._page_pair()
        if "market_activity.json" not in now:
            self.skipTest("the Stage 8C edit is not in the working page")
        live = re.compile(r'<script type="module" id="stocklens-live">[\s\S]*?</script>\n')
        search = re.compile(r'<script type="module" id="stocklens-search">[\s\S]*?</script>\n')
        pin = subprocess.run(["git", "show", "f682bbb:index.html"], cwd=str(ROOT), capture_output=True)
        if pin.returncode != 0:
            self.skipTest("the pinned commit is not available")
        strip = lambda h: search.sub("", live.sub("", h, count=1), count=1)
        reverted = G.without_market_cap(strip(now))
        self.assertNotIn("market_activity", reverted)
        self.assertEqual(reverted.encode("utf-8"), strip(pin.stdout.decode("utf-8")).encode("utf-8"))      # byte for byte the Phase 2 page
        self.assertEqual(G.without_market_activity(G.without_market_activity(now)), G.without_market_activity(now))   # idempotent

    def test_stage_8c_reversal_is_a_no_op_without_the_edit(self):
        pin = subprocess.run(["git", "show", "f682bbb:index.html"], cwd=str(ROOT), capture_output=True)       # the Phase 2 page never carried the edit (HEAD does, once committed)
        if pin.returncode != 0:
            self.skipTest("the pinned commit is not available")
        head = pin.stdout.decode("utf-8")
        self.assertNotIn("market_activity.json", head)
        self.assertEqual(G.without_market_activity(head), head)
        self.assertEqual(G.without_market_activity("<html>nothing</html>"), "<html>nothing</html>")

    def test_stage_8c_reversal_fails_loudly_on_partial_or_duplicate_text(self):
        now, _ = self._page_pair()
        if "market_activity.json" not in now:
            self.skipTest("the Stage 8C edit is not in the working page")
        for first, _last in G._ACT_BLOCKS:                                             # a duplicated block
            i = now.index(first.lstrip("\n"))
            with self.assertRaises(ValueError):
                G.without_market_activity(now + now[i:i + 200])
        for new, _old in G._ACT_EDITS:                                                 # an edit that was changed or is missing
            with self.assertRaises(ValueError):
                G.without_market_activity(now.replace(new, new[:len(new) // 2] + "~" + new[len(new) // 2:], 1))
        with self.assertRaises(ValueError):                                            # only a trace of the edit
            G.without_market_activity("<script>window.SLMarket={activityOf:activityOf,perfOf:</script>")

    def test_any_edit_inside_either_stage_8c_block_fails_the_integrity_check(self):
        now, _ = self._page_pair()
        if "market_activity.json" not in now:
            self.skipTest("the Stage 8C edit is not in the working page")
        self.assertEqual(len(G._ACT_BLOCK_SHA256), len(G._ACT_BLOCKS))
        G.without_market_activity(now)                                                 # the approved content passes
        for (first, last), pin in zip(G._ACT_BLOCKS, G._ACT_BLOCK_SHA256):
            i = now.index(first)
            block = now[i:now.index(last, i) + len(last)]
            self.assertEqual(hashlib.sha256(block.encode("utf-8")).hexdigest(), pin)
            mid = i + len(first) + 20                                                  # strictly inside the block, past its first line
            for mutated in (now[:mid] + "x" + now[mid:],                               # an inserted character
                            now[:mid] + now[mid + 1:],                                 # a deleted character
                            now[:mid] + ("Z" if now[mid] != "Z" else "Y") + now[mid + 1:],   # a replaced character
                            now.replace(last, "/*x*/" + last, 1) if last in now else now):   # a change just before the block's last line
                self.assertNotEqual(mutated, now)
                with self.assertRaises(ValueError):
                    G.without_market_activity(mutated)
                with self.assertRaises(ValueError):
                    G.without_market_cap(mutated)                                      # and the guards that use the reversal fail too

    def test_an_unrelated_change_is_still_detected_after_the_stage_8c_reversal(self):
        now, head = self._page_pair()
        want = G.without_market_cap(head)
        self.assertEqual(G.without_market_cap(now), want)
        self.assertNotEqual(G.without_market_cap(now + "<!-- x -->"), want)
        self.assertNotEqual(G.without_market_cap(now.replace("Overview", "Overview2", 1)), want)
        if "market_activity.json" in now:                                              # a change OUTSIDE the approved edits, next to them, is still seen
            self.assertNotEqual(G.without_market_cap(now.replace("var NEED_ALL=", "var NEED_ALL2=", 1)), want)
            self.assertNotEqual(G.without_market_cap(now.replace("function events(S){", "function events(S){ ", 1)), want)

    def test_the_two_updaters_differ_from_the_commit_only_by_the_approved_lines(self):
        for f in ("fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py", "shareholding_updater.py"):
            diff = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", f], cwd=str(ROOT), capture_output=True, text=True)
            if diff.returncode != 0:
                self.skipTest("not a git checkout")
            if diff.stdout.strip():                                                        # uncommitted: it must be exactly the approved diff
                self.assertTrue(G.is_approved(ROOT, f), f)

    def test_the_guard_rejects_any_other_edit_to_those_updaters(self):
        with tempfile.TemporaryDirectory() as d:
            for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
                subprocess.run(cmd, cwd=d, check=True)
            f = Path(d) / "fundamentals_updater.py"
            f.write_text("import universe\n\ndef main():\n        symbols = universe.load()\n        return symbols\n")
            subprocess.run(["git", "add", "."], cwd=d, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=d, check=True)
            ok = G.UPDATERS["fundamentals_updater.py"]
            f.write_text("import research_test_batch\nimport universe\n\ndef main():\n" + ok[2][1:] + "\n        return symbols\n")
            self.assertTrue(G.exact_diff(d, "fundamentals_updater.py", ok))
            f.write_text(f.read_text() + "# one more line\n")
            self.assertFalse(G.exact_diff(d, "fundamentals_updater.py", ok))
            f.write_text("import universe\n\ndef main():\n        symbols = universe.load()\n        return symbols\n# x\n")
            self.assertFalse(G.exact_diff(d, "fundamentals_updater.py", ok))

    def test_the_sector_gate_is_untouched(self):
        t = (ROOT / "fundamentals_updater.py").read_text()
        self.assertIn('"sector": e.get("sector") if sym in universe.TEST_SYMBOLS else None', t)
        for f in ROOT.glob("*.py"):
            if f.name.startswith("sector_") or f.name == "research_test_batch.py":
                self.assertNotIn("DEFAULT_APPROVED = True", f.read_text())
        self.assertNotIn("SECTOR_PUBLIC_DISPLAY_APPROVED", (ROOT / "research_test_batch.py").read_text())

    def test_the_module_cannot_reach_the_network_the_environment_or_the_files(self):
        code = re.sub(r'""".*?"""', "", (ROOT / "research_test_batch.py").read_text(), flags=re.S)
        for needle in ("requests", "urllib", "socket", "environ", "getenv", "open(", "write", "subprocess", "UPSTOX"):
            self.assertNotIn(needle, code, needle)


if __name__ == "__main__":
    unittest.main()
