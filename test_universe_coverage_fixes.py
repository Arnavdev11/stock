"""Regression tests for the universe-wide coverage fixes found by the Batch A/B audit (Oct 2026). Offline only: every provider is a fake.
Each class names the defect it pins.  Run: python3 -m unittest test_universe_coverage_fixes"""
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

import financial_history_updater as fh
import financials_updater as fu
import processing_state as ps
import quarterly_results_updater as q
import report_run_status as rr
import run_status
import upstox_common as uc
from test_financial_history import CF4, FakeApi, YEARS4, cash_body, income_body, std_lines
from test_quarterly_results import ENTRIES, GOOD, P5, body as qbody

HERE = Path(__file__).resolve().parent
D = dt.date


# ---------------------------------------------------------------- 1. financial statements: a failing section no longer goes first in every run
class Resp:
    def __init__(self, code, b=None):
        self.status_code, self._b, self.content = code, b, b"[]"

    def json(self):
        return self._b


def fs_income():
    from mock_responses import income
    return income()


class FinancialsBackoff(unittest.TestCase):
    """CANHLIFE (insurance) returned no usable income or balance sheet; with no fetch date it sorted first and was retried in every run."""

    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.cache, self.out = d / "cache.json", d / "out" / "financials.json"
        self.calls, self.fail_eps = [], {}
        from mock_responses import balance, cashflow, income
        outer = self

        class Sess:
            def __init__(s):
                s.headers = {}

            def get(s, url, params=None, timeout=0):
                isin, ep = url.split("/")[-2], url.split("/")[-1]
                outer.calls.append((isin, ep))
                if (isin, ep) in outer.fail_eps:
                    return Resp(200, {"status": "success", "data": {}})             # a 200 with nothing usable: "no usable data"
                return Resp(200, {"income-statement": income, "balance-sheet": balance, "cash-flow": cashflow}[ep]())
        self.syms = ["AAA", "BBB", "CCC"]
        insts = {s: {"isin": "INE_" + s, "name": s + " LTD"} for s in self.syms}
        self.day = ["2026-10-16"]
        self.patches = [mock.patch.object(fu, "CACHE_FILE", self.cache), mock.patch.object(fu, "OUT_FILE", self.out),
                        mock.patch.object(uc.requests, "Session", Sess), mock.patch.object(uc.time, "sleep", lambda s: None),
                        mock.patch.object(fu, "load_instruments", lambda: insts), mock.patch.object(fu.universe, "load", lambda: list(self.syms)),
                        mock.patch.object(fu.research_test_batch, "extend", lambda s: list(s)), mock.patch.object(fu, "today", lambda: self.day[0]),
                        mock.patch.object(fu, "is_stale", lambda d, n: not d or (dt.date.fromisoformat(self.day[0]) - dt.date.fromisoformat(d)).days >= n),
                        mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": "T"})]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def run_main(self, cap):
        with mock.patch.object(fu, "MAX_CALLS_PER_RUN", cap), contextlib.redirect_stdout(io.StringIO()) as buf:
            fu.main()
        return buf.getvalue()

    def test_failing_section_backs_off_and_moves_to_the_back(self):
        self.fail_eps = {("INE_AAA", "income-statement"), ("INE_AAA", "balance-sheet")}
        self.run_main(1000)
        aaa = json.loads(self.cache.read_text())["stocks"]["AAA"]
        self.assertEqual((aaa["income"]["failures"], aaa["income"]["attempted"]), (1, "2026-10-16"))
        self.assertIsNotNone(aaa["cash_flow"].get("data"))                                       # the section that worked is kept
        # same day: nothing is due (back-off), so no call at all
        self.calls.clear(); self.run_main(1000)
        self.assertEqual(self.calls, [])
        # it is still reported, not silently dropped
        doc = json.loads(self.out.read_text())
        self.assertTrue(any(e["symbol"] == "AAA" and "income" in e["error"] for e in doc["errors"]))
        # next day: retried once (1-day back-off), and it now sorts AFTER a stock that needs work (stalest-first by attempt date)
        self.day[0] = "2026-10-17"
        st = json.loads(self.cache.read_text())
        st["stocks"]["CCC"]["income"]["fetched"] = "2026-09-01"                                # CCC's income is stale (older than 30 days)
        self.cache.write_text(json.dumps(st))
        self.calls.clear(); self.run_main(1)
        self.assertEqual(self.calls, [("INE_CCC", "income-statement")])                          # the stale good stock goes first, not the failing one
        self.assertEqual(fu.retry_days(1), 1); self.assertEqual(fu.retry_days(3), 4); self.assertEqual(fu.retry_days(99), 30)

    def test_success_resets_the_failure_count(self):
        self.fail_eps = {("INE_BBB", "balance-sheet")}
        self.run_main(1000)
        self.fail_eps = set(); self.day[0] = "2026-10-18"
        self.run_main(1000)
        bbb = json.loads(self.cache.read_text())["stocks"]["BBB"]["balance_sheet"]
        self.assertEqual((bbb["failures"], bbb["error"], bbb["fetched"]), (0, None, "2026-10-18"))

    def test_not_in_upstox_is_classified_without_a_call_even_after_the_budget(self):
        self.syms.append("ZZZ")                                                                    # not in the instrument list
        self.run_main(3)                                                                           # budget for one stock only
        st = json.loads(self.cache.read_text())["stocks"]
        self.assertEqual(st["ZZZ"]["last_error"], fu.NOT_FOUND_ERROR)
        self.assertEqual(len(self.calls), 3)

    def test_run_record_reports_incomplete_when_the_budget_stops_it(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {run_status.ENV: d}):
            self.run_main(3)
            rec = json.loads((Path(d) / "financials.json").read_text())
        self.assertEqual(rec["status"], "incomplete"); self.assertEqual(rec["tally"]["not_reached"], 2); self.assertEqual(rec["calls"], 3)


# ---------------------------------------------------------------- 2. quarterly results: due re-checks are not starved; failures back off; the budget is capped
class QuarterlyOrder(unittest.TestCase):
    def fetcher(self, table, log):
        def f(isin, basis):
            log.append(isin)
            return table.get((isin, basis), (GOOD, None))
        return f

    def with_results(self, last_checked="2026-10-14"):
        return {**q.empty_record(), "basis": "consolidated", "last_checked": last_checked, "last_success": last_checked,
                "quarters": {"2026-06-30": {"quarter": "Jun 2026", "retrieved_on": "2026-07-20", "values": {"revenue": 1.0}, "checks": {}, "revisions": []}}}

    def test_a_due_recheck_is_not_starved_by_a_never_checked_backlog(self):
        # INFY/RELIANCE: September results due (window opened 10 Oct), last checked 14 Oct, behind ~1,876 never-checked stocks
        entries = {s: {"symbol": s, "isin": "INE" + s, "name": s} for s in ["AAA", "BBB", "CCC", "DDD", "INFY"]}
        cache = {"INFY": self.with_results()}
        log = []
        s = q.run_batch(entries, cache, self.fetcher({}, log), D(2026, 10, 16), 4)
        self.assertEqual(log[0], "INEINFY")                       # the re-check goes first ...
        self.assertEqual(log[1:], ["INEAAA", "INEBBB", "INECCC"])  # ... and the never-checked backlog still progresses
        self.assertEqual((s["rechecked"], s["due_rechecks"], s["due_never_checked"]), (1, 1, 4))

    def test_rechecks_cannot_take_the_whole_budget_while_never_checked_stocks_wait(self):
        entries = {s: {"symbol": s, "isin": "INE" + s, "name": s} for s in ["R1", "R2", "R3", "R4", "N1", "N2"]}
        cache = {s: self.with_results() for s in ["R1", "R2", "R3", "R4"]}
        log = []
        q.run_batch(entries, cache, self.fetcher({}, log), D(2026, 10, 16), 4)
        self.assertEqual(sorted(log), sorted(["INER1", "INER2", "INEN1", "INEN2"]))   # half the budget each

    def test_with_no_backlog_rechecks_use_the_whole_budget(self):
        entries = {s: {"symbol": s, "isin": "INE" + s, "name": s} for s in ["R1", "R2", "R3"]}
        cache = {s: self.with_results() for s in entries}
        log = []
        q.run_batch(entries, cache, self.fetcher({}, log), D(2026, 10, 16), 3)
        self.assertEqual(len(log), 3)

    def test_a_failed_request_backs_off_and_keeps_the_stored_quarters(self):
        entries = {"AAA": ENTRIES["AAA"]}
        cache = {"AAA": self.with_results()}
        log = []
        q.run_batch(entries, cache, self.fetcher({(ENTRIES["AAA"]["isin"], "consolidated"): (None, "HTTP 500")}, log), D(2026, 10, 16), 10)
        self.assertEqual(cache["AAA"]["failures"], 1)
        self.assertEqual(cache["AAA"]["quarters"]["2026-06-30"]["values"]["revenue"], 1.0)   # untouched, never zeroed
        self.assertFalse(q.needs_refresh(cache["AAA"], D(2026, 10, 16)))                    # same day: backed off
        self.assertTrue(q.needs_refresh(cache["AAA"], D(2026, 10, 17)))                     # next day: retried (results window, every 2 days)
        log.clear()
        q.run_batch(entries, cache, self.fetcher({}, log), D(2026, 10, 18), 10)
        self.assertEqual(cache["AAA"]["failures"], 0)                                        # success resets it

    def test_the_call_budget_is_capped_to_fit_the_step_timeout(self):
        seen = {}

        class FakeApi2:
            def __init__(self, token, cap):
                self.max_calls = seen["cap"] = cap

            def get(self, *a, **k):
                return None, "HTTP 404"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(q, "get_token", lambda: "t"), mock.patch.object(q, "load_entries", lambda: {"AAA": ENTRIES["AAA"]}), \
                mock.patch.object(q, "Upstox", FakeApi2), mock.patch.object(q, "CACHE_FILE", Path(d) / "c.json"), mock.patch.object(q, "write_outputs", lambda *a: None), \
                mock.patch.object(q.universe, "load_entries", lambda: {"AAA": {}}), mock.patch.dict(os.environ, {"QUARTERLY_MAX_CALLS": "100", "QUARTERLY_MAX_CALLS_OVERRIDE": "5000", "QUARTERLY_SYMBOLS": ""}), \
                contextlib.redirect_stdout(io.StringIO()) as buf:
            q.main([])
        self.assertEqual(seen["cap"], q.MAX_ALLOWED_CALLS)
        self.assertIn("is above the 330 calls", buf.getvalue())


# ---------------------------------------------------------------- 3. financial history: layout, sector privacy, newer basis
class FinancialHistoryLayoutAndSector(unittest.TestCase):
    def test_brokers_investment_and_asset_managers_are_financial(self):
        for s in ("Stock Broking", "Stock/ Commodity Brokers", "Investment", "Asset Management", "Finance", "Bank", "NBFC", "Housing Finance"):
            self.assertEqual(fh.classify_layout(s), "financial", s)
        for s in ("Capital Goods-Non Electrical Equipment", "Capital Goods - Electrical Equipment", "IT - Software", "Pharmaceuticals", "Trading"):
            self.assertEqual(fh.classify_layout(s), "standard", s)

    def test_the_wider_universe_sector_is_never_stored_or_published(self):
        s = fh.new_stock("5PAISA", "INE618L01018", "5PAISA CAPITAL LIMITED", "Stock Broking")
        self.assertIsNone(s["sector"]); self.assertEqual(s["statement_layout"], "financial")
        t = fh.new_stock("TCS", "INE467B01029", "TCS", "IT - Software")
        self.assertEqual(t["sector"], "IT - Software")

    def test_an_old_ledger_is_scrubbed_and_reclassified_on_load(self):
        led = {"schema": 1, "stocks": {"5PAISA": {"symbol": "5PAISA", "sector": "Stock Broking", "statement_layout": "standard", "years": []},
                                       "TCS": {"symbol": "TCS", "sector": "IT - Software", "statement_layout": "standard", "years": []}}}
        fh.normalise_ledger(led)
        self.assertEqual((led["stocks"]["5PAISA"]["sector"], led["stocks"]["5PAISA"]["statement_layout"]), (None, "financial"))
        self.assertEqual(led["stocks"]["TCS"]["sector"], "IT - Software")

    def test_build_output_never_carries_a_private_sector(self):
        led = {"stocks": {"ABC": {"symbol": "ABC", "sector": "Steel", "statement_layout": "standard", "years": [{"fy": 2026}], "fetch_state": {}}}}
        self.assertIsNone(fh.build_output(led, [], [], "2026-10-16")["stocks"]["ABC"]["sector"])


class FinancialHistoryBasis(unittest.TestCase):
    """AUBANK's consolidated history stopped at FY2017 while standalone is current; consolidated always won."""
    OLD = ["Mar_2017", "Mar_2016", "Mar_2015", "Mar_2014"]

    def responses(self, cons_years, sa_years):
        r = {("income-statement", "consolidated"): (income_body(std_lines(cons_years)), None),
             ("cash-flow", "consolidated"): (cash_body(**{k: dict(zip(cons_years, h.values())) for k, h in CF4.items()}), None)}   # same years as the income statement
        if sa_years:
            r[("income-statement", "standalone")] = (income_body(std_lines(sa_years), typ="standalone"), None)
        return r

    def stock(self):
        return fh.new_stock("AUBANK", "INE949L01017", "AU SMALL FINANCE BANK", None)

    def test_a_stale_consolidated_basis_gives_way_to_a_newer_standalone_one(self):
        api = FakeApi(self.responses(self.OLD, YEARS4))
        p, basis, _, err = fh.fetch_statement(api, "INE949L01017", "income", "standard", None, "2026-10-16")
        self.assertIsNone(err); self.assertEqual(basis, "standalone"); self.assertEqual(fh.newest_fy(p), 2026)

    def test_an_older_or_missing_standalone_keeps_consolidated(self):
        api = FakeApi(self.responses(self.OLD, None))
        p, basis, _, err = fh.fetch_statement(api, "X", "income", "standard", None, "2026-10-16")
        self.assertEqual((basis, err, fh.newest_fy(p)), ("consolidated", None, 2017))

    def test_a_current_consolidated_basis_costs_no_extra_call(self):
        api = FakeApi(self.responses(YEARS4, YEARS4))
        fh.fetch_statement(api, "X", "income", "standard", None, "2026-10-16")
        self.assertEqual(api.calls, 1)

    def test_a_stored_stale_basis_is_rechecked_once_inside_the_refresh_window(self):
        s = self.stock()
        api = FakeApi(self.responses(self.OLD, None))
        fh.process_stock(api, s, "2026-10-16")
        self.assertEqual(s["fetch_state"]["income"]["standalone_checked"], "2026-10-16")
        api2 = FakeApi(self.responses(self.OLD, YEARS4))
        fh.process_stock(api2, s, "2026-10-20")
        self.assertEqual(api2.calls, 0)                                          # checked once: not repeated every run
        s["fetch_state"]["income"].pop("standalone_checked")                       # an older ledger without the flag ...
        fh.process_stock(api2, s, "2026-10-20")
        self.assertIn("standalone", {r["basis"] for r in s["years"]})             # ... is checked, and the current standalone years are added
        self.assertIn("consolidated", {r["basis"] for r in s["years"]})           # nothing is deleted


class FinancialHistoryRunOrder(unittest.TestCase):
    """A due standalone check (stale consolidated basis) goes before never-fetched stocks; otherwise the order is the oldest fetch first."""
    def stock(self, fetched, fys, checked=None):
        st = {"basis": "consolidated", "fetched": fetched, "error": None}
        if checked:
            st["standalone_checked"] = checked
        return {"years": [{"fy": y, "basis": "consolidated"} for y in fys], "fetch_state": {"income": dict(st), "cash_flow": dict(st)}}

    def test_due_basis_checks_first_then_never_fetched_then_oldest(self):
        stocks = {"STALEBANK": self.stock("2026-10-16", [2014, 2015, 2016, 2017]),          # consolidated ends FY2017: due its check
                  "FRESH": self.stock("2026-10-20", [2025, 2026]),
                  "OLDER": self.stock("2026-10-01", [2025, 2026]),
                  "CHECKED": self.stock("2026-10-16", [2017], checked="2026-10-17")}          # its one check was made: not due again
        order = fh.run_order(["NEWA", "FRESH", "OLDER", "STALEBANK", "CHECKED", "NEWB"], stocks, "2026-10-28")
        self.assertEqual(order, ["STALEBANK", "NEWA", "NEWB", "OLDER", "CHECKED", "FRESH"])

    def test_without_any_due_check_the_order_is_the_previous_one(self):
        stocks = {"A": self.stock("2026-10-10", [2026]), "B": self.stock("2026-10-01", [2026])}
        self.assertEqual(fh.run_order(["A", "B", "C"], stocks, "2026-10-28"), ["C", "B", "A"])


# ---------------------------------------------------------------- 4. durable processing state
class ProcessingState(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()

    def write_cache(self, kind, stocks):
        doc = {"stocks": stocks} if kind != "quarterly" else {"schema_version": 1, "kind": "quarterly_cache", "stocks": stocks}
        (self.root / "data" / ps.KINDS[kind]).write_text(json.dumps(doc))

    def cache(self, kind):
        return json.loads((self.root / "data" / ps.KINDS[kind]).read_text())["stocks"]

    def test_export_removes_the_private_sector_and_skips_untouched_stocks(self):
        self.write_cache("fundamentals", {"5PAISA": {"sector": "Stock Broking", "ratios_fetched": "2026-10-15"}, "TCS": {"sector": "IT", "ratios_fetched": "2026-10-14"},
                                          "NEW": {}})
        out = self.root / "state"
        counts = ps.export(self.root, out)
        self.assertEqual(counts["fundamentals"], 2)
        rec = json.loads((out / "fundamentals" / "5PAISA.json").read_text())["record"]
        self.assertNotIn("sector", rec)
        self.assertEqual(json.loads((out / "fundamentals" / "TCS.json").read_text())["record"]["sector"], "IT")
        self.assertFalse((out / "fundamentals" / "NEW.json").exists())

    def test_seed_moves_each_stock_forward_never_back(self):
        state = self.root / "state"
        self.write_cache("financials", {"A": {"income": {"fetched": "2026-10-10"}}, "B": {"income": {"fetched": "2026-10-16"}}})
        self.write_cache("fundamentals", {"A": {"sector": "Private label", "ratios_fetched": "2026-10-01"}})
        ps.export(self.root, state)
        # durable copy is newer for A, older for B; the cache then gets older (an evicted / stale snapshot)
        for kind, sym, rec in (("financials", "A", {"income": {"fetched": "2026-10-15"}}), ("fundamentals", "A", {"ratios_fetched": "2026-10-15", "ratios": {"pe": 1}})):
            (state / kind / (sym + ".json")).write_text(json.dumps({"schema": 1, "kind": kind, "symbol": sym, "record": rec}))
        self.write_cache("financials", {"A": {"income": {"fetched": "2026-10-10"}}, "B": {"income": {"fetched": "2026-10-16"}}})
        ps.seed(state, self.root)
        fin = self.cache("financials")
        self.assertEqual(fin["A"]["income"]["fetched"], "2026-10-15")            # the durable copy was newer
        self.assertEqual(fin["B"]["income"]["fetched"], "2026-10-16")            # the cache was newer
        fund = self.cache("fundamentals")["A"]
        self.assertEqual((fund["ratios_fetched"], fund["sector"]), ("2026-10-15", "Private label"))   # private label kept from the cache

    def test_seed_after_cache_loss_restores_everything(self):
        state = self.root / "state"
        self.write_cache("quarterly", {"A": {"basis": "consolidated", "last_checked": "2026-10-15", "quarters": {"2026-06-30": {"values": {"revenue": 1}}}}})
        ps.export(self.root, state)
        (self.root / "data" / ps.KINDS["quarterly"]).unlink()                   # the Actions cache was evicted
        ps.seed(state, self.root)
        self.assertEqual(self.cache("quarterly")["A"]["quarters"]["2026-06-30"]["values"]["revenue"], 1)

    def test_quarters_are_never_dropped_in_a_merge(self):
        a = {"basis": "consolidated", "last_checked": "2026-10-16", "quarters": {"2026-06-30": {"v": 2}}}
        b = {"basis": "consolidated", "last_checked": "2026-09-01", "quarters": {"2026-03-31": {"v": 1}, "2026-06-30": {"v": 9}}}
        m = ps.merge_record("quarterly", a, b)
        self.assertEqual(sorted(m["quarters"]), ["2026-03-31", "2026-06-30"]); self.assertEqual(m["quarters"]["2026-06-30"], {"v": 2})

    def test_verify_refuses_loss_regression_and_private_data(self):
        old, new = self.root / "old", self.root / "new"
        for root, rec in ((old, {"ratios_fetched": "2026-10-15"}), (new, {"ratios_fetched": "2026-10-10", "sector": "X"})):
            (root / "fundamentals").mkdir(parents=True)
            (root / "fundamentals" / "ABC.json").write_text(json.dumps({"schema": 1, "kind": "fundamentals", "symbol": "ABC", "record": rec}))
        (old / "fundamentals" / "GONE.json").write_text(json.dumps({"schema": 1, "kind": "fundamentals", "symbol": "GONE", "record": {"ratios_fetched": "2026-10-01"}}))
        p = ps.no_loss_problems(old, new)
        self.assertTrue(any("GONE" in x and "missing" in x for x in p))
        self.assertTrue(any("older" in x for x in p))
        self.assertTrue(any("private sector" in x for x in p))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ps.main(["verify", str(old), str(new)]), 1)
            self.assertEqual(ps.main(["verify", str(old), str(old)]), 0)


class ProcessingStateDurability(unittest.TestCase):
    """Edge cases of the durable copy: missing, malformed or half-written files; failed attempts; bases; stale snapshots; what the save job may write."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()
        self.state = self.root / "state"

    def put(self, kind, sym, rec, raw=None):
        f = self.state / kind / (sym + ".json")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(raw if raw is not None else json.dumps({"schema": 1, "kind": kind, "symbol": sym, "record": rec}))

    def cache_doc(self, kind, stocks):
        (self.root / "data" / ps.KINDS[kind]).write_text(json.dumps({"stocks": stocks}))

    def test_no_state_directory_leaves_every_cache_untouched(self):
        self.cache_doc("financials", {"A": {"income": {"fetched": "2026-10-10"}}})
        before = (self.root / "data" / ps.KINDS["financials"]).read_bytes()
        rep = ps.seed(self.root / "nowhere", self.root)
        self.assertEqual((self.root / "data" / ps.KINDS["financials"]).read_bytes(), before)
        self.assertFalse((self.root / "data" / ps.KINDS["quarterly"]).exists())          # nothing is created from nothing
        self.assertEqual(rep["financials"]["from_state"], 0)

    def test_malformed_and_half_written_state_files_are_skipped_and_reported(self):
        self.cache_doc("fundamentals", {"A": {"ratios_fetched": "2026-10-10", "ratios": {"pe": 1}}})
        self.put("fundamentals", "A", None, raw='{"schema": 1, "kind": "fundamen')               # truncated (half-written)
        self.put("fundamentals", "B", None, raw="[1, 2]")                                       # wrong shape
        self.put("fundamentals", "C", {"ratios_fetched": "2026-10-15"})
        rep = ps.seed(self.state, self.root)
        st = json.loads((self.root / "data" / ps.KINDS["fundamentals"]).read_text())["stocks"]
        self.assertEqual(st["A"]["ratios"], {"pe": 1})                                          # the cache's copy is kept, not damaged
        self.assertNotIn("B", st); self.assertIn("C", st)
        self.assertEqual(rep["fundamentals"]["damaged_files"], 2)
        self.assertTrue(any("damaged file" in p for p in ps.no_loss_problems(self.root / "empty", self.state)))   # a damaged file can never be saved

    def test_an_unreadable_cache_is_kept_aside_not_replaced(self):
        (self.root / "data" / ps.KINDS["quarterly"]).write_text("{not json")
        self.put("quarterly", "A", {"basis": "consolidated", "last_success": "2026-10-15", "quarters": {"2026-06-30": {"v": 1}}})
        ps.seed(self.state, self.root)
        self.assertEqual((self.root / "data" / (ps.KINDS["quarterly"] + ".unreadable")).read_text(), "{not json")
        self.assertIn("A", json.loads((self.root / "data" / ps.KINDS["quarterly"]).read_text())["stocks"])

    def test_a_newer_failed_attempt_never_replaces_older_successful_data(self):
        cache = {"ratios_fetched": "2026-10-08", "ratios": {"pe": 9}, "last_attempt": "2026-10-16", "last_error": "key-ratios: HTTP 500", "sector": "Private"}
        durable = {"ratios_fetched": "2026-10-15", "ratios": {"pe": 1}, "last_attempt": "2026-10-15", "last_error": None}
        m = ps.merge_record("fundamentals", cache, durable)
        self.assertEqual((m["ratios_fetched"], m["ratios"]), ("2026-10-15", {"pe": 1}))          # the newer success
        self.assertEqual((m["last_attempt"], m["last_error"]), ("2026-10-16", "key-ratios: HTTP 500"))   # the newer attempt is still reported
        self.assertEqual(m["sector"], "Private")
        fc = {"income": {"fetched": "2026-10-01", "data": {"x": 1}, "attempted": "2026-10-16", "error": "HTTP 500", "failures": 2}}
        fd = {"income": {"fetched": "2026-10-14", "data": {"x": 2}, "attempted": "2026-10-14", "error": None, "failures": 0}}
        s = ps.merge_record("financials", fc, fd)["income"]
        self.assertEqual((s["data"], s["fetched"], s["error"], s["failures"]), ({"x": 2}, "2026-10-14", "HTTP 500", 2))

    def test_quarterly_bases_are_never_mixed_and_dates_only_move_forward(self):
        a = {"basis": "consolidated", "last_success": "2026-10-10", "last_checked": "2026-10-16", "quarters": {"2026-06-30": {"v": 1}}}
        b = {"basis": "standalone", "last_success": "2026-10-15", "last_checked": "2026-10-15", "quarters": {"2026-06-30": {"v": 2}}}
        self.assertEqual(ps.merge_record("quarterly", a, b)["basis"], "standalone")              # the later success, kept whole
        self.assertEqual(ps.merge_record("quarterly", a, b)["quarters"], {"2026-06-30": {"v": 2}})
        c = {"basis": "consolidated", "last_success": "2026-10-15", "last_checked": "2026-10-15", "quarters": {"2026-09-30": {"v": 3}}}
        m = ps.merge_record("quarterly", a, c)
        self.assertEqual(sorted(m["quarters"]), ["2026-06-30", "2026-09-30"])
        self.assertEqual((m["last_success"], m["last_checked"]), ("2026-10-15", "2026-10-16"))

    def test_an_older_snapshot_restored_over_newer_progress_is_corrected_by_the_seed(self):
        # a side workflow's (or an evicted-then-older) snapshot restored into the publishing run: the durable copy is newer and wins part by part
        self.cache_doc("financials", {"A": {"income": {"fetched": "2026-09-01", "data": {"old": 1}}, "cash_flow": {"fetched": "2026-10-16", "data": {"new": 1}}}})
        self.put("financials", "A", {"income": {"fetched": "2026-10-15", "data": {"new": 2}}, "cash_flow": {"fetched": "2026-10-01", "data": {"old": 2}}})
        ps.seed(self.state, self.root)
        a = json.loads((self.root / "data" / ps.KINDS["financials"]).read_text())["stocks"]["A"]
        self.assertEqual((a["income"]["data"], a["cash_flow"]["data"]), ({"new": 2}, {"new": 1}))

    def test_the_save_job_may_write_only_state_files(self):
        self.put("fundamentals", "TCS", {"ratios_fetched": "2026-10-15"})
        (self.state / "fundamentals" / "TCS.tmp").write_text("x")
        (self.state / "notes.md").write_text("x")
        (self.state / "unknownkind").mkdir(); (self.state / "unknownkind" / "A.json").write_text("{}")
        p = ps.no_loss_problems(self.root / "empty", self.state)
        for name in ("fundamentals/TCS.tmp", "notes.md", "unknownkind/A.json"):
            self.assertTrue(any(name in x for x in p), name)

    def test_the_export_never_contains_a_private_sector_and_is_atomic(self):
        self.cache_doc("fundamentals", {"ABC": {"sector": "Private", "ratios_fetched": "2026-10-15"}})
        ps.export(self.root, self.state)
        self.assertEqual([f.name for f in (self.state / "fundamentals").iterdir()], ["ABC.json"])   # no .tmp left behind
        self.assertNotIn("Private", (self.state / "fundamentals" / "ABC.json").read_text())
        self.assertEqual(ps.no_loss_problems(self.root / "empty", self.state), [])


# ---------------------------------------------------------------- 5. reporting: continue-on-error can no longer hide a failed data step
class Reporting(unittest.TestCase):
    def test_judging_outcomes_and_records(self):
        self.assertEqual(rr.judge("failure", None), "failed")
        self.assertEqual(rr.judge("skipped", None), "skipped")
        self.assertEqual(rr.judge("success", {"tally": {"not_reached": 5}}), "incomplete")
        self.assertEqual(rr.judge("success", {"tally": {"failed": 2, "succeeded": 10}}), "incomplete")
        self.assertEqual(rr.judge("success", {"tally": {"failed": 2}}), "failed")
        self.assertEqual(rr.judge("success", {"tally": {"succeeded": 3}}), "complete")

    def test_summary_and_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            run_status.write.__globals__["os"].environ  # noqa: keep the import used
            Path(d, "fundamentals.json").write_text(json.dumps({"tally": {"not_reached": 3}, "calls": 150, "call_cap": 150}))
            out, summ = Path(d, "out.txt"), Path(d, "summary.md")
            env = {"RUN_STATUS_DIR": d, "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summ), "OUTCOME_NSE": "success", "OUTCOME_FUNDAMENTALS": "success",
                   "OUTCOME_FINANCIALS": "failure"}
            with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(io.StringIO()):
                rr.main()
            o = out.read_text()
            self.assertIn("data_status=failed", o); self.assertIn("data_ok=false", o)
            s = summ.read_text()
            self.assertIn("| Fundamentals | success | incomplete | 150 calls of 150; not reached 3 |", s)
            self.assertIn("| Financial statements | failure | failed |", s)
            env["OUTCOME_FINANCIALS"] = "success"; out.write_text("")
            with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(io.StringIO()):
                rr.main()
            self.assertIn("data_ok=true", out.read_text())                       # incomplete (budget) is a warning, not a failed run
            self.assertIn("data_status=incomplete", out.read_text())

    def test_no_record_without_the_environment(self):
        with mock.patch.dict(os.environ, {run_status.ENV: ""}):
            self.assertIsNone(run_status.write("financials", {"succeeded": 1}))


# ---------------------------------------------------------------- 6. workflows: wiring of the above (structure only; nothing is run)
class Workflows(unittest.TestCase):
    def load(self, name):
        return yaml.safe_load((HERE / ".github" / "workflows" / name).read_text(encoding="utf-8"))

    def test_update_yml_wiring(self):
        w = self.load("update.yml")
        self.assertIn("quarterly_calls", w["on"]["workflow_dispatch"]["inputs"])
        steps = {s.get("id"): s for s in w["jobs"]["build"]["steps"] if s.get("id")}
        self.assertEqual(steps["quarterly"]["env"]["QUARTERLY_MAX_CALLS"], "100")                                 # the default is unchanged
        self.assertEqual(steps["quarterly"]["env"]["QUARTERLY_MAX_CALLS_OVERRIDE"], "${{ inputs.quarterly_calls }}")   # a hand-set budget, capped by the updater
        for sid in ("nse", "fundamentals", "financials", "financial_history", "shareholding", "quarterly", "validate", "market"):
            self.assertIn("OUTCOME_" + sid.upper(), steps["datastatus"]["env"], sid)
        self.assertEqual(steps["datastatus"]["if"], "always()")
        self.assertEqual(w["jobs"]["build"]["env"]["RUN_STATUS_DIR"], "run-status")
        self.assertIn("data-status", w["jobs"]); self.assertEqual(w["jobs"]["deploy"]["needs"], "build")   # the site is still published
        self.assertNotIn("save-state", w["jobs"])                                   # the durable-state wiring is decision D1 (separate patch)

    def test_side_workflows_cannot_be_restored_by_the_publishing_run(self):
        for name in ("historical.yml", "market_data.yml", "financial_history.yml"):        # shareholding.yml: decision D2 (separate patch)
            w = self.load(name)
            for job in w["jobs"].values():
                for s in job.get("steps", []):
                    if str(s.get("uses", "")).startswith("actions/cache@"):
                        self.assertFalse(s["with"]["key"].startswith("nse-data-"), name)


if __name__ == "__main__":
    unittest.main()
