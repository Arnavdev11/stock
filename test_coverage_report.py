"""Tests for coverage_report.py (the offline coverage report). Run:  python3 -m unittest test_coverage_report
Every test builds its own small data-branch folder in a temporary directory; nothing here reads a real ledger, a cache or the network."""
import ast
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coverage_report as C

HERE = Path(__file__).resolve().parent
AS_OF = dt.date(2026, 10, 23)
FH_FIELDS = list(C.FH_VALUE_FIELDS)
CATS = list(C.SH_CATEGORIES)
SECRET_PCT = 61.23          # a distinctive shareholding value: it must never appear in any report
SECRET_HOLDER = "ZZ SECRET HOLDER LTD"


def isin(i):
    return "INE%08d0" % i


def sym(i):
    return "S%04d" % i


def write(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def universe_doc(symbols):
    return {"schema_version": 1, "kind": "universe", "stage": "directory", "source": "test", "as_of": "2026-10-15", "count": len(symbols),
            "symbols": [{"symbol": s, "isin": isin(int(s[1:])) if s[1:].isdigit() else None, "name": s + " LTD"} for s in symbols]}


def directory_doc(symbols):
    return {"schema_version": 1, "kind": "stock_directory", "built_on": "2026-10-10", "counts": {"published": len(symbols)},
            "classification": {"counts": {"eligible_eq": len(symbols)}},
            "symbols": [{"symbol": s, "isin": isin(int(s[1:])), "name": s + " LTD"} for s in symbols]}


def year(fy, **vals):
    v = {f: None for f in FH_FIELDS}
    v.update(vals)
    return {"fy": fy, "period": "FY%d" % fy, "period_end": "%d-03-31" % fy, "basis": "consolidated", "values": v, "missing": [],
            "verification": {"status": "verified"}, "source": {}, "first_seen": "2026-10-15", "last_confirmed": "2026-10-15", "revisions": []}


def fh_entry(i, years, layout="standard", sector=None):
    return {"symbol": sym(i), "isin": isin(i), "company_name": sym(i), "sector": sector, "statement_layout": layout, "fiscal_year_end_month": 3,
            "years": years, "gaps": [], "fetch_state": {"income": {"basis": "consolidated", "error": None, "fetched": "2026-10-20"},
                                                         "cash_flow": {"basis": "consolidated", "error": None, "fetched": "2026-10-21"}}}


def candle(date, close=100.0, volume=10):
    return {"date": date, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": volume}


def sh_quarter(qe, status="available", values=None):
    v = values if values is not None else {c: SECRET_PCT for c in CATS}
    if status == "unavailable":
        v = {c: None for c in CATS}
    return {"quarter_end": qe, "status": status, "reason": None if status == "available" else "x", "values": v,
            "quality": {"status": "ok", "flags": []}, "named_holders": {"holders": [{"holder_name": SECRET_HOLDER}]}}


def fund_row(i, fetched="2026-10-22", **kw):
    r = {"symbol": sym(i), "isin": isin(i), "company_name": sym(i), "sector": None, "pe": 20.5, "pb": 3.1, "roe": 11.0, "roa": 5.0, "roce": 14.0,
         "ev_ebitda": 9.0, "revenue_growth": None, "profit_growth": None, "missing": [], "fetched": fetched}
    r.update(kw)
    return r


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "branch"
        self.data.mkdir()
        self.out = self.root / "report"
        self.addCleanup(self.tmp.cleanup)

    def make(self, n=600, with_directory=True):
        self.symbols = [sym(i) for i in range(n)]
        write(self.data / "universe.json", universe_doc(self.symbols))
        if with_directory:
            write(self.data / "stock_directory.json", directory_doc(self.symbols))

    def build(self, scope="both", fundamentals=None):
        return C.build_report(self.data, AS_OF, scope, fundamentals)

    def mod(self, report, scope, name):
        return [m for m in report["scopes"][scope]["modules"] if m["module"] == name][0]

    def tree(self, root):
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob("*")) if p.is_file()}

    def run_main(self, *args):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = C.main(list(args))
        return rc, out.getvalue(), err.getvalue()


class SelectionAndCounts(Case):
    def test_first_500_is_exactly_the_first_500_symbols_in_file_order(self):
        self.make(600)
        r = self.build()
        s = r["scopes"]["first_500"]
        self.assertEqual((s["denominator"], s["first_symbol"], s["last_symbol"]), (500, "S0000", "S0499"))
        self.assertEqual(r["scopes"]["all"]["denominator"], 600)
        self.assertEqual(r["scopes"]["all"]["last_symbol"], "S0599")
        self.assertTrue(r["universe"]["sorted_ascending"])
        self.assertIn("symbols[:500]", s["selection"])

    def test_file_order_wins_over_alphabetical_order(self):
        self.make(0 + 3)
        syms = ["S0002", "S0000", "S0001"]
        write(self.data / "universe.json", universe_doc(syms))
        r = self.build()
        self.assertFalse(r["universe"]["sorted_ascending"])
        self.assertEqual(r["scopes"]["first_500"]["first_symbol"], "S0002")      # file order, never re-sorted
        self.assertTrue(r["scopes"]["first_500"]["fewer_than_500_in_universe"])
        self.assertEqual(r["scopes"]["first_500"]["denominator"], 3)

    def test_scope_option_limits_the_scopes(self):
        self.make(600)
        self.assertEqual(list(self.build("first500")["scopes"]), ["first_500"])
        self.assertEqual(list(self.build("all")["scopes"]), ["all"])

    def test_every_module_adds_up_to_its_denominator_in_both_scopes(self):
        self.make(600)
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {sym(1): fh_entry(1, [year(2026, total_revenue=5.0)]), sym(550): fh_entry(550, [year(2026)])}})
        r = self.build()
        for label, sc in r["scopes"].items():
            self.assertEqual([m["module"] for m in sc["modules"]], ["directory", "fundamentals", "financial_history", "growth", "historical_candles", "technical_indicators",
                                                                       "sector", "shareholding", "quarterly_results", "annual_financials_and_balance_sheet", "stock_detail_data"])
            for m in sc["modules"]:
                self.assertEqual(sum(v for v in m["counts"].values() if v is not None), m["denominator"]["value"], (label, m["module"]))

    def test_financial_history_counts_per_scope(self):
        self.make(600)
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {
            sym(1): fh_entry(1, [year(2025, total_revenue=1.0), year(2026, total_revenue=2.0)]), sym(2): fh_entry(2, []), sym(550): fh_entry(550, [year(2026, total_revenue=3.0)])}})
        r = self.build()
        a, b = self.mod(r, "first_500", "financial_history"), self.mod(r, "all", "financial_history")
        self.assertEqual((a["counts"]["valid"], a["counts"]["missing"], a["counts"]["invalid"]), (1, 499, 0))
        self.assertEqual(a["missing_reasons"], {"entry_without_years": 1, "not_in_ledger": 498})
        self.assertEqual((b["counts"]["valid"], b["counts"]["missing"]), (2, 598))
        self.assertEqual(a["coverage_pct"], 0.2)
        self.assertEqual(b["coverage_pct"], round(100.0 * 2 / 600, 4))
        self.assertEqual(a["denominator"]["value"], 500)

    def test_directory_module_counts_and_cross_check(self):
        self.make(10)
        d = directory_doc(self.symbols)
        d["symbols"][3]["isin"] = isin(999)         # differs from the universe
        d["symbols"].pop()                          # one symbol absent from the directory
        write(self.data / "stock_directory.json", d)
        m = self.mod(self.build(), "all", "directory")
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"]), (8, 2))
        self.assertEqual(m["invalid_reasons"], {"isin_differs_from_directory": 1, "not_in_directory": 1})
        self.assertEqual((m["facts"]["duplicate_symbols"], m["facts"]["duplicate_isins"]), (0, 0))


class DirectoryCrossCheck(Case):
    """stock_directory.json: an unusable directory is never reported as verified coverage, and duplicate entries are reported, not overwritten away."""

    def directory_row(self, report, scope="all"):
        return self.mod(report, scope, "directory")

    def test_a_usable_directory_is_the_positive_control(self):
        self.make(6)
        m = self.directory_row(self.build())
        self.assertEqual((m["counts"]["valid"], m["counts"]["unverified"], m["coverage_pct"]), (6, None, 100.0))
        self.assertEqual(m["measurability"], "offline")
        self.assertEqual(m["facts"]["directory_cross_check"], {"performed": True, "reason": None, "stocks_cross_checked": 6})
        self.assertEqual(m["facts"]["universe_identifier_check"]["well_formed"], 6)
        self.assertEqual(m["warnings"], [])

    def test_a_missing_directory_is_unverified_never_one_hundred_percent(self):
        self.make(600, with_directory=False)
        r = self.build()
        for scope, n in (("first_500", 500), ("all", 600)):
            m = self.directory_row(r, scope)
            self.assertEqual(m["counts"], {"valid": None, "stale": None, "missing": 0, "invalid": 0, "not_applicable": None, "unverified": n})
            self.assertIsNone(m["coverage_pct"])
            self.assertIsNone(m["fresh_pct"])
            self.assertEqual(m["denominator"]["value"], n)
            self.assertEqual(m["measurability"], "partial")
            self.assertEqual(m["facts"]["directory_cross_check"], {"performed": False, "reason": "missing", "stocks_cross_checked": 0})
            u = m["facts"]["universe_identifier_check"]
            self.assertEqual((u["well_formed"], u["denominator"], u["pct"]), (n, n, 100.0))      # the universe-only figure is kept, and labelled as such
            self.assertIn("not a directory cross-check", u["note"])
            self.assertEqual(m["unverified"][0]["count"], n)
            self.assertIn("NOT reported as verified", m["unverified"][0]["reason"])
            self.assertIn("stock_directory.json is missing", m["problems"][0])
        row = [ln for ln in C.render_markdown(r).splitlines() if ln.startswith("| directory |")][0]
        self.assertEqual(row.split("|")[3].strip(), "n/a")          # Valid
        self.assertEqual(row.split("|")[8].strip(), "n/a")          # Coverage %
        self.assertNotIn("100", row)

    def test_every_way_the_directory_can_be_unusable_gives_the_same_unverified_result(self):
        cases = {"directory_is_a_folder": None, "invalid_json": "{ not json", "not_an_object": "[1, 2]", "wrong_kind": json.dumps({"kind": "something_else", "symbols": [{"symbol": "S0000"}]}),
                 "no_symbols_list": json.dumps({"kind": "stock_directory", "symbols": {"S0000": "x"}}), "empty_symbols_list": json.dumps({"kind": "stock_directory", "symbols": []})}
        for reason, text in cases.items():
            with self.subTest(reason=reason):
                self.make(4, with_directory=False)
                path = self.data / "stock_directory.json"
                if text is None:
                    path.mkdir()          # a folder where the file should be: not readable as a file
                    expect = "missing"      # is_file() is False for a folder, so it is reported as missing
                else:
                    path.write_text(text, encoding="utf-8")
                    expect = reason
                m = self.directory_row(self.build())
                self.assertEqual(m["facts"]["directory_cross_check"]["reason"], expect)
                self.assertEqual((m["counts"]["valid"], m["counts"]["unverified"], m["coverage_pct"]), (None, 4, None))
                self.assertEqual(sum(v for v in m["counts"].values() if v is not None), 4)
                self.assertEqual(m["facts"]["universe_identifier_check"]["well_formed"], 4)
                if text is None:
                    path.rmdir()

    def test_a_genuine_read_failure_is_unreadable_not_missing_and_not_verified(self):
        self.make(5)                                                    # a valid directory is on disk; only the read of it is made to fail
        target = (self.data / "stock_directory.json")
        real = Path.read_bytes
        def flaky(path_self, *a, **k):
            if path_self.name == "stock_directory.json":
                raise PermissionError(13, "simulated read failure")      # an OSError subclass; no dependence on OS permissions or on running as root
            return real(path_self, *a, **k)
        self.assertTrue(target.is_file())                               # so this is not the 'missing' path
        with mock.patch.object(Path, "read_bytes", flaky):
            doc, problem = C.Inputs(self.data).read(target)
            self.assertEqual((doc, problem), (None, "unreadable"))
            r = self.build()
        for scope in ("first_500", "all"):
            m = self.directory_row(r, scope)
            self.assertEqual(m["facts"]["directory_cross_check"], {"performed": False, "reason": "unreadable", "stocks_cross_checked": 0})
            self.assertEqual(m["counts"], {"valid": None, "stale": None, "missing": 0, "invalid": 0, "not_applicable": None, "unverified": 5})
            self.assertIsNone(m["coverage_pct"])
            self.assertIsNone(m["fresh_pct"])
            self.assertEqual(m["denominator"]["value"], 5)
            self.assertEqual(sum(v for v in m["counts"].values() if v is not None), 5)
            self.assertIn("stock_directory.json is unreadable", m["problems"][0])
            self.assertIn("NOT reported as verified", m["unverified"][0]["reason"])

    def test_a_stock_without_an_isin_is_missing_not_unverified(self):
        self.make(5, with_directory=False)
        doc = universe_doc(self.symbols)
        doc["symbols"][1]["isin"] = None                  # no ISIN: there is nothing to cross-check
        write(self.data / "universe.json", doc)
        write(self.data / "stock_directory.json", {"kind": "stock_directory", "symbols": []})      # unusable
        m = self.directory_row(self.build())
        self.assertEqual(m["counts"], {"valid": None, "stale": None, "missing": 1, "invalid": 0, "not_applicable": None, "unverified": 4})
        self.assertEqual(m["facts"]["universe_identifier_check"]["well_formed"], 4)
        self.assertEqual(m["facts"]["directory_cross_check"]["performed"], False)

    def test_duplicate_directory_symbols_are_reported_not_resolved_by_overwrite(self):
        self.make(6)
        d = directory_doc(self.symbols)
        d["symbols"].append({"symbol": "S0001", "isin": isin(1), "name": "again"})               # an identical duplicate
        d["symbols"].insert(0, {"symbol": "S0002", "isin": isin(888), "name": "first is wrong"})      # earlier entry wrong, later entry right
        write(self.data / "stock_directory.json", d)
        m = self.directory_row(self.build())
        self.assertEqual(m["invalid_reasons"], {"duplicate_symbol_in_directory": 2})
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"]), (4, 2))      # neither entry is chosen: S0001 and S0002 are not valid
        dd = m["facts"]["directory_duplicates"]
        self.assertEqual(dd["duplicate_symbols"], {"distinct": 2, "extra_entries": 2, "symbols": ["S0001", "S0002"]})
        self.assertEqual(dd["duplicate_isins"], {"distinct": 1, "extra_entries": 1, "isins": [isin(1)]})      # the identical S0001 entry repeats its ISIN too
        w = [x for x in m["warnings"] if x["code"] == "duplicate_symbols_in_directory"][0]
        self.assertEqual((w["count"], w["identifiers"]), (2, ["S0001", "S0002"]))
        self.assertEqual(m["coverage_pct"], round(100.0 * 4 / 6, 4))
        md = C.render_markdown(self.build())
        self.assertIn("more than once", md)
        self.assertIn("Identifiers: S0001, S0002", md)

    def test_duplicate_directory_isins_are_reported(self):
        self.make(6)
        d = directory_doc(self.symbols)
        d["symbols"][2]["isin"] = isin(1)             # S0002 now carries S0001's ISIN in the directory
        write(self.data / "stock_directory.json", d)
        m = self.directory_row(self.build())
        self.assertEqual(m["invalid_reasons"], {"duplicate_isin_in_directory": 1, "isin_differs_from_directory": 1})
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"]), (4, 2))
        dd = m["facts"]["directory_duplicates"]
        self.assertEqual(dd["duplicate_isins"], {"distinct": 1, "extra_entries": 1, "isins": [isin(1)]})
        self.assertEqual(dd["duplicate_symbols"]["distinct"], 0)
        w = [x for x in m["warnings"] if x["code"] == "duplicate_isins_in_directory"][0]
        self.assertEqual((w["count"], w["identifiers"]), (1, [isin(1)]))

    def test_null_directory_isins_are_not_duplicates(self):
        self.make(5)
        d = directory_doc(self.symbols)
        d["symbols"][1]["isin"] = None
        d["symbols"][2]["isin"] = None
        write(self.data / "stock_directory.json", d)
        m = self.directory_row(self.build())
        self.assertEqual(m["facts"]["directory_duplicates"]["duplicate_isins"], {"distinct": 0, "extra_entries": 0, "isins": []})
        self.assertEqual(m["invalid_reasons"], {"isin_differs_from_directory": 2})      # a null directory ISIN does not match the universe's ISIN
        self.assertFalse([x for x in m["warnings"] if x["code"] == "duplicate_isins_in_directory"])

    def test_malformed_directory_entries_are_counted_and_the_rest_still_used(self):
        self.make(5)
        d = directory_doc(self.symbols)
        d["symbols"] += ["junk", {"isin": isin(1)}, {"symbol": "S0001", "isin": 12345}]
        write(self.data / "stock_directory.json", d)
        m = self.directory_row(self.build())
        self.assertEqual(m["facts"]["directory_duplicates"]["malformed_entries"], 3)
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"]), (5, 0))      # the 3 unusable entries neither help nor hurt a stock
        self.assertEqual([x["code"] for x in m["warnings"]], ["malformed_entries_in_directory"])

    def test_duplicates_are_counted_per_scope_but_reported_for_the_whole_directory(self):
        self.make(600)
        d = directory_doc(self.symbols)
        d["symbols"].append({"symbol": "S0550", "isin": isin(550), "name": "dup"})      # outside the first 500
        write(self.data / "stock_directory.json", d)
        r = self.build()
        a, b = self.directory_row(r, "first_500"), self.directory_row(r, "all")
        self.assertEqual((a["counts"]["valid"], a["counts"]["invalid"]), (500, 0))
        self.assertEqual((b["counts"]["valid"], b["counts"]["invalid"]), (599, 1))
        for m in (a, b):
            self.assertEqual(m["facts"]["directory_duplicates"]["duplicate_symbols"]["symbols"], ["S0550"])      # the directory-level fact is the same in both
            self.assertEqual(sum(v for v in m["counts"].values() if v is not None), m["denominator"]["value"])

    def test_directory_checks_leave_universe_validation_and_existing_mappings_alone(self):
        self.make(4)
        d = directory_doc(self.symbols)
        d["symbols"][3]["isin"] = isin(999)
        d["symbols"].pop(0)
        write(self.data / "stock_directory.json", d)
        m = self.directory_row(self.build())
        self.assertEqual(m["invalid_reasons"], {"isin_differs_from_directory": 1, "not_in_directory": 1})      # the same two reasons as before duplicate detection existed
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"]), (2, 2))
        write(self.data / "universe.json", {**universe_doc(self.symbols), "count": 99})            # an invalid universe still stops the run
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))[0], 2)


class NullVersusZero(Case):
    def test_zero_is_present_and_counted_separately_null_is_never_zero(self):
        self.make(5)
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {
            sym(0): fh_entry(0, [year(2026, total_revenue=0, profit_after_tax=None)]), sym(1): fh_entry(1, [year(2026, total_revenue=7.5, profit_after_tax=None)])}})
        f = self.mod(self.build(), "all", "financial_history")["fields"]
        self.assertEqual((f["total_revenue"]["stocks_with_latest_year_present"], f["total_revenue"]["stocks_with_latest_year_zero"]), (2, 1))
        self.assertEqual((f["profit_after_tax"]["stocks_with_latest_year_present"], f["profit_after_tax"]["stocks_with_latest_year_null"], f["profit_after_tax"]["stocks_with_latest_year_zero"]), (0, 2, 0))
        self.assertEqual(f["capex"]["stocks_with_any_year_present"], 0)

    def test_fundamentals_zero_pe_is_present_and_a_missing_key_is_null(self):
        self.make(4)
        rows = [fund_row(0, pe=0), fund_row(1)]
        del rows[1]["roce"]
        fp = self.root / "fund.json"
        write(fp, {"as_of": "2026-10-22", "source": "Upstox", "stocks": rows, "errors": []})
        f = self.mod(self.build("all", fp), "all", "fundamentals")["fields"]
        self.assertEqual((f["pe"]["present"], f["pe"]["zero"], f["pe"]["null"]), (2, 1, 0))
        self.assertEqual((f["roce"]["present"], f["roce"]["null"], f["roce"]["zero"]), (1, 1, 0))
        self.assertEqual(f["pe"]["pct_of_denominator"], 50.0)

    def test_candle_zero_volume_is_not_null_volume(self):
        self.make(3)
        write(self.data / "historical" / "shards" / "S0000.json", {"stocks": {"S0000": {"candles": [candle("2026-10-21", volume=0), candle("2026-10-22", volume=None)]}}})
        m = self.mod(self.build(), "all", "historical_candles")
        self.assertEqual((m["fields"]["volume"]["zero"], m["fields"]["volume"]["null"], m["fields"]["volume"]["present"]), (1, 1, 1))
        self.assertEqual(m["facts"]["stocks_with_some_null_volume_rows"], 1)


class MissingFilesAndInvalidRecords(Case):
    def test_missing_files_are_not_measurable_never_zero_coverage(self):
        self.make(5, with_directory=False)
        r = self.build()
        for name in ("financial_history", "shareholding", "fundamentals", "growth", "quarterly_results", "annual_financials_and_balance_sheet", "technical_indicators", "sector", "historical_candles"):
            m = self.mod(r, "all", name)
            self.assertEqual(m["measurability"], "not_measurable", name)
            self.assertEqual((m["counts"]["unverified"], m["counts"]["valid"], m["coverage_pct"]), (5, None, None), name)
            self.assertTrue(m["unverified"][0]["reason"], name)
        d = self.mod(r, "all", "directory")
        self.assertEqual(d["measurability"], "partial")
        self.assertIn("stock_directory.json is missing", d["problems"][0])

    def test_a_missing_or_broken_supplied_fundamentals_file_is_reported_with_its_reason(self):
        self.make(3)
        m = self.mod(self.build("all", self.root / "nope.json"), "all", "fundamentals")
        self.assertIn("missing", m["unverified"][0]["reason"])
        bad = self.root / "bad.json"
        bad.write_text("{ not json", encoding="utf-8")
        self.assertIn("invalid_json", self.mod(self.build("all", bad), "all", "fundamentals")["unverified"][0]["reason"])
        nan = self.root / "nan.json"
        nan.write_text('{"stocks": [{"symbol": "S0000", "pe": NaN}]}', encoding="utf-8")
        self.assertIn("invalid_json", self.mod(self.build("all", nan), "all", "fundamentals")["unverified"][0]["reason"])      # NaN is refused, not read as a number

    def test_invalid_financial_history_entries_are_counted_not_covered(self):
        self.make(8)
        dup = fh_entry(1, [year(2026), year(2026)])
        text = fh_entry(2, [year(2026, total_revenue="12")])
        wrong = fh_entry(3, [year(2026)])
        wrong["isin"] = isin(777)
        notlist = fh_entry(4, [])
        notlist["years"] = {"2026": year(2026)}
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {sym(0): fh_entry(0, [year(2026, total_revenue=1.0)]),
                                                                                                         sym(1): dup, sym(2): text, sym(3): wrong, sym(4): notlist}})
        m = self.mod(self.build(), "all", "financial_history")
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"], m["counts"]["missing"]), (2, 3, 3))
        self.assertEqual(m["invalid_reasons"], {"duplicate_fiscal_year": 1, "non_numeric_value": 1, "years_not_a_list": 1})
        f = m["flags"]["isin_differs_from_universe"]
        self.assertEqual((f["count"], f["symbols"]), (1, ["S0003"]))      # still counted, but named
        self.assertEqual(f["details"], [{"symbol": "S0003", "stored_isin": isin(777), "universe_isin": isin(3)}])

    def test_invalid_candles(self):
        self.make(6)
        sh = self.data / "historical" / "shards"
        write(sh / "S0000.json", {"stocks": {"S0000": {"candles": [candle("2026-10-21"), candle("2026-10-22")]}}})
        write(sh / "S0001.json", {"stocks": {"S0001": {"candles": [candle("2026-10-22"), candle("2026-10-21")]}}})              # not ascending
        bad = candle("2026-10-22")
        bad["high"], bad["low"] = 90.0, 99.0
        write(sh / "S0002.json", {"stocks": {"S0002": {"candles": [bad]}}})                                                        # high below low
        write(sh / "S0003.json", {"stocks": {"S0003": {"candles": [candle("2026-10-24")]}}})                                        # after as-of
        write(sh / "S0004.json", {"stocks": {"S0004": {"candles": []}}})
        (sh / "S0005.json").write_text("{", encoding="utf-8")
        m = self.mod(self.build(), "all", "historical_candles")
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"], m["counts"]["missing"]), (1, 4, 1))
        self.assertEqual(m["invalid_reasons"], {"dates_not_strictly_ascending": 1, "inconsistent_or_non_positive_price": 1, "newest_candle_after_as_of": 1, "shard_invalid_json": 1})
        self.assertEqual(m["missing_reasons"], {"empty_candle_list": 1})

    def test_a_ledger_isin_that_differs_from_the_universe_is_flagged_not_dropped(self):
        self.make(4)
        e = {"isin": isin(999), "quarters": [sh_quarter("2026-09-30")]}
        write(self.data / "shareholding_ledger.json", {"schema": 1, "stocks": {sym(0): e, sym(1): {"isin": None, "quarters": [sh_quarter("2026-09-30")]}}})
        m = self.mod(self.build(), "all", "shareholding")
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"]), (2, 0))
        self.assertEqual(m["flags"]["isin_differs_from_universe"]["symbols"], ["S0000"])
        self.assertEqual(m["warnings"][0]["code"], "isin_differs_from_universe")
        md = C.render_markdown(self.build())
        self.assertIn("### Warnings", md)
        self.assertIn("ISIN that differs from the universe", md)

    def test_shareholding_separates_outside_the_collector_scope_from_missing_in_scope(self):
        syms = ["3BBLACKBIO", "DOMS", "S0001", "S0002", "TCS"]
        write(self.data / "universe.json", {"schema_version": 1, "kind": "universe", "stage": "directory", "source": "t", "as_of": "2026-10-15", "count": 5,
                                            "symbols": [{"symbol": s, "isin": isin(i), "name": s} for i, s in enumerate(syms)]})
        write(self.data / "shareholding_ledger.json", {"schema": 1, "stocks": {"TCS": {"isin": None, "quarters": [sh_quarter("2026-09-30")]},
                                                                               "DOMS": {"isin": None, "quarters": [sh_quarter("2026-09-30")]},
                                                                               "S0002": {"isin": None, "quarters": [sh_quarter("2026-09-30")]}}})
        m = self.mod(self.build("all"), "all", "shareholding")
        self.assertEqual((m["counts"]["valid"], m["counts"]["missing"], m["counts"]["not_applicable"], m["counts"]["invalid"]), (3, 1, 1, 0))
        self.assertEqual(m["missing_reasons"], {"in_collector_scope_but_not_in_ledger": 1})
        self.assertEqual(m["facts"]["in_scope_symbols_missing_from_ledger"], ["3BBLACKBIO"])
        self.assertEqual(m["facts"]["ledger_stocks_outside_collector_scope"], ["S0002"])      # present data is still counted
        self.assertEqual(m["facts"]["collector_scope"]["size"], 15)
        self.assertEqual(m["facts"]["collector_scope"]["in_this_scope"], 3)
        self.assertEqual(m["coverage_pct"], 60.0)
        self.assertEqual(m["coverage_pct_of_applicable"], 75.0)      # 3 of the 4 stocks the collector is meant to read
        self.assertEqual(m["unverified"][0]["count"], 1)

    def test_every_stock_outside_the_collector_scope_is_not_applicable_never_missing(self):
        self.make(5)
        write(self.data / "shareholding_ledger.json", {"schema": 1, "stocks": {}})
        m = self.mod(self.build(), "all", "shareholding")
        self.assertEqual((m["counts"]["not_applicable"], m["counts"]["missing"], m["counts"]["valid"]), (5, 0, 0))
        self.assertEqual(m["unverified"], [])

    def test_isin_differences_stay_visible_as_warnings_in_both_scopes(self):
        self.make(600)
        write(self.data / "shareholding_ledger.json", {"schema": 1, "stocks": {sym(1): {"isin": isin(9001), "quarters": [sh_quarter("2026-09-30")]},
                                                                               sym(550): {"isin": isin(9002), "quarters": [sh_quarter("2026-09-30")]}}})
        e = fh_entry(1, [year(2026)])
        e["isin"] = isin(9003)
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {sym(1): e}})
        fp = self.root / "f.json"
        write(fp, {"as_of": "2026-10-22", "stocks": [fund_row(1, isin=isin(9004))], "errors": []})
        r = self.build("both", fp)
        a, b = r["scopes"]["first_500"], r["scopes"]["all"]
        self.assertEqual(a["warning_summary"]["isin_differs_from_universe"], {"records": 3, "distinct_symbols": 1})
        self.assertEqual(b["warning_summary"]["isin_differs_from_universe"], {"records": 4, "distinct_symbols": 2})
        self.assertEqual(sorted(w["module"] for w in b["warnings"] if w["code"] == "isin_differs_from_universe"), ["financial_history", "fundamentals", "shareholding"])
        for m in b["modules"]:
            if m["module"] in ("financial_history", "fundamentals", "shareholding"):
                self.assertEqual(m["counts"]["invalid"], 0, m["module"])      # never classified as invalid without evidence
        md = C.render_markdown(r)
        self.assertEqual(md.count("### Warnings"), 2)
        self.assertIn("S0550", md)

    def test_a_stale_fundamentals_snapshot_is_labelled_as_such(self):
        self.make(4)
        fp = self.root / "f.json"
        write(fp, {"as_of": "2026-10-16", "stocks": [fund_row(0, fetched="2026-10-15")], "errors": []})
        r = self.build("all", fp)
        m = self.mod(r, "all", "fundamentals")
        codes = [w["code"] for w in m["warnings"]]
        self.assertEqual(codes, ["supplied_file_is_a_snapshot", "supplied_file_older_than_the_weekly_threshold"])
        text = m["warnings"][0]["message"]
        self.assertIn("2026-10-16", text)
        self.assertIn("7 day(s) before the reference date 2026-10-23", text)
        self.assertIn("does not represent current pipeline health", text)
        self.assertIn("does not mean a refresh failed", m["warnings"][1]["message"])
        self.assertEqual(m["facts"]["supplied_file_age_days"], 7)
        self.assertEqual(m["counts"]["stale"], 1)
        self.assertEqual(m["unverified"][0]["what"], "current fundamentals pipeline health")
        self.assertIn("does not represent current pipeline health", C.render_markdown(r))
        self.assertEqual([w["code"] for w in self.mod(r, "all", "growth")["warnings"]], ["supplied_file_is_a_snapshot"])

    def test_a_current_fundamentals_snapshot_still_says_it_is_only_a_snapshot(self):
        self.make(2)
        fp = self.root / "f.json"
        write(fp, {"as_of": "2026-10-22", "stocks": [fund_row(0)], "errors": []})
        self.assertEqual([w["code"] for w in self.mod(self.build("all", fp), "all", "fundamentals")["warnings"]], ["supplied_file_is_a_snapshot"])

    def test_invalid_shareholding_quarters(self):
        self.make(8)
        good = {"isin": None, "quarters": [sh_quarter("2026-09-30"), sh_quarter("2026-06-30", "unavailable")]}
        odd_date = {"isin": None, "quarters": [sh_quarter("2026-08-15")]}
        dup = {"isin": None, "quarters": [sh_quarter("2026-09-30"), sh_quarter("2026-09-30")]}
        big = {"isin": None, "quarters": [sh_quarter("2026-09-30", values={**{c: 1.0 for c in CATS}, "fii": 150.0})]}
        short = {"isin": None, "quarters": [sh_quarter("2026-09-30", values={"promoters": 1.0})]}
        none_av = {"isin": None, "quarters": [sh_quarter("2026-06-30", "unavailable")]}
        write(self.data / "shareholding_ledger.json", {"schema": 1, "stocks": {sym(0): good, sym(1): odd_date, sym(2): dup, sym(3): big, sym(4): short, sym(5): none_av}})
        m = self.mod(self.build(), "all", "shareholding")
        self.assertEqual((m["counts"]["valid"], m["counts"]["invalid"], m["counts"]["missing"], m["counts"]["not_applicable"]), (1, 4, 1, 2))
        self.assertEqual(m["invalid_reasons"], {"category_value_out_of_range": 1, "duplicate_quarter": 1, "quarter_end_not_a_quarter_end": 1, "values_do_not_hold_exactly_the_categories": 1})
        self.assertEqual(m["missing_reasons"], {"no_available_quarter": 1})      # S0006 and S0007 are outside the collector's fixed scope: not applicable, not missing

    def test_fundamentals_invalid_and_stale_rows(self):
        self.make(10)
        rows = [fund_row(0, fetched="2026-10-22"),                  # 1 day old: valid
                fund_row(1, fetched="2026-10-17"),                  # 6 days: still valid
                fund_row(2, fetched="2026-10-16"),                  # 7 days: stale (the documented weekly rule)
                fund_row(3, isin=isin(999)),                        # ISIN differs
                fund_row(4, fetched="2026-13-40"),                  # not a date
                fund_row(5, fetched="2026-10-24"),                  # after the reference date
                fund_row(6, pe="n/a"),                              # not a number
                fund_row(7), fund_row(7)]                           # duplicate rows
        fp = self.root / "fund.json"
        write(fp, {"as_of": "2026-10-22", "source": "Upstox", "stocks": rows,
                   "errors": [{"symbol": "S0008", "error": C.NOT_FOUND_ERROR}, {"symbol": "S0009", "error": "HTTP 500"}]})
        m = self.mod(self.build("all", fp), "all", "fundamentals")
        self.assertEqual((m["counts"]["valid"], m["counts"]["stale"], m["counts"]["invalid"], m["counts"]["missing"]), (3, 1, 4, 2))
        self.assertEqual(m["invalid_reasons"], {"bad_fetched_date": 1, "duplicate_rows": 1, "fetched_after_as_of": 1, "non_numeric_metric": 1})
        self.assertEqual((m["flags"]["isin_differs_from_universe"]["count"], m["flags"]["isin_differs_from_universe"]["symbols"]), (1, ["S0003"]))
        self.assertEqual(m["missing_reasons"], {"recorded_not_in_upstox_list": 1, "recorded_other_error": 1})
        self.assertEqual((m["coverage_pct"], m["fresh_pct"]), (40.0, 30.0))
        self.assertEqual(m["freshness"]["threshold_days"], 7)

    def test_growth_is_measured_not_derived(self):
        self.make(4)
        fp = self.root / "fund.json"
        write(fp, {"as_of": "2026-10-22", "stocks": [fund_row(0, revenue_growth=12.5, profit_growth=0), fund_row(1)], "errors": []})
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {sym(0): fh_entry(0, [year(2025, total_revenue=1.0), year(2026, total_revenue=2.0)])}})
        m = self.mod(self.build("all", fp), "all", "growth")
        self.assertEqual((m["counts"]["valid"], m["counts"]["missing"]), (1, 3))
        self.assertEqual(m["missing_reasons"], {"growth_field_null": 1, "no_usable_fundamentals_row": 2})
        self.assertEqual(m["fields"]["profit_growth"]["zero"], 1)
        self.assertEqual(m["facts"]["growth_fields_stored_in_financial_history_ledger"], [])
        self.assertIn("not performed", m["facts"]["derivation"])

    def test_outside_universe_records_are_reported(self):
        self.make(3)
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {"ZZZ": fh_entry(1, [year(2026)])}})
        write(self.data / "historical" / "shards" / "QQQ.json", {"stocks": {"QQQ": {"candles": [candle("2026-10-22")]}}})
        o = self.build()["reconciliation"]["present_in_stored_data_but_not_in_universe"]
        self.assertEqual(o["financial_history"], {"count": 1, "first": ["ZZZ"]})
        self.assertEqual(o["historical_shards"], {"count": 1, "first": ["QQQ"]})


class DuplicatesAndIdentifiers(Case):
    def test_a_universe_with_a_duplicate_symbol_or_isin_stops_the_run_and_writes_nothing(self):
        self.make(4)
        doc = universe_doc(self.symbols)
        doc["symbols"][1]["symbol"] = doc["symbols"][0]["symbol"]
        write(self.data / "universe.json", doc)
        rc, _, err = self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))
        self.assertEqual(rc, 2)
        self.assertIn("duplicate symbol", err)
        self.assertFalse(self.out.exists())
        doc = universe_doc(self.symbols)
        doc["symbols"][2]["isin"] = doc["symbols"][3]["isin"]
        write(self.data / "universe.json", doc)
        rc, _, err = self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))
        self.assertEqual(rc, 2)
        self.assertIn("duplicate ISIN", err)
        self.assertFalse(self.out.exists())

    def test_invalid_identifier_in_the_universe_stops_the_run(self):
        self.make(3)
        doc = universe_doc(self.symbols)
        doc["symbols"][1]["isin"] = "BADISIN"
        write(self.data / "universe.json", doc)
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))[0], 2)
        self.assertFalse(self.out.exists())

    def test_a_missing_or_unreadable_universe_stops_the_run(self):
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))[0], 2)
        (self.data / "universe.json").write_text("[", encoding="utf-8")
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out))[0], 2)
        self.assertFalse(self.out.exists())

    def test_a_universe_stock_without_an_isin_is_missing_not_covered(self):
        self.make(3)
        doc = universe_doc(self.symbols)
        doc["symbols"][1]["isin"] = None
        write(self.data / "universe.json", doc)
        write(self.data / "stock_directory.json", directory_doc(self.symbols))
        m = self.mod(self.build(), "all", "directory")
        self.assertEqual((m["counts"]["valid"], m["counts"]["missing"]), (2, 1))
        self.assertEqual(m["missing_reasons"], {"no_isin": 1})


class FreshnessRules(Case):
    def test_no_threshold_means_threshold_not_configured_never_fresh_or_stale(self):
        self.make(4)
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {sym(0): fh_entry(0, [year(2026, total_revenue=1.0)])}})
        write(self.data / "historical" / "shards" / "S0001.json", {"stocks": {"S0001": {"candles": [candle("2026-10-20"), candle("2026-10-21")]}}})
        write(self.data / "shareholding_ledger.json", {"schema": 1, "stocks": {sym(2): {"isin": None, "quarters": [sh_quarter("2026-09-30")]}}})
        r = self.build()
        for name in ("financial_history", "historical_candles", "shareholding"):
            m = self.mod(r, "all", name)
            self.assertEqual(m["freshness"]["status"], C.THRESHOLD_NOT_CONFIGURED, name)
            self.assertIsNone(m["counts"]["stale"], name)
            self.assertIsNone(m["fresh_pct"], name)
        c = self.mod(r, "all", "historical_candles")
        self.assertEqual(c["freshness"]["newest_candle_age_days"]["min"], 2)          # 2026-10-21 against 2026-10-23
        self.assertEqual(c["facts"]["candle_rows_per_stock"]["max"], 2)
        s = self.mod(r, "all", "shareholding")
        self.assertEqual(s["freshness"]["newest_available_quarter_counts"], {"2026-09-30": 1})
        self.assertEqual(s["freshness"]["newest_quarter_age_days"]["min"], 23)
        f = self.mod(r, "all", "financial_history")
        self.assertEqual((f["freshness"]["oldest_section_fetched"], f["freshness"]["newest_section_fetched"]), ("2026-10-20", "2026-10-21"))
        i = self.mod(r, "all", "technical_indicators")
        self.assertEqual(i["facts"]["readiness"], C.THRESHOLD_NOT_CONFIGURED)
        self.assertEqual(i["measurability"], "not_measurable")

    def test_cache_only_modules_are_unmeasurable_with_a_reason(self):
        self.make(3)
        r = self.build()
        for name in ("quarterly_results", "annual_financials_and_balance_sheet"):
            m = self.mod(r, "all", name)
            self.assertEqual((m["measurability"], m["counts"]["unverified"]), ("not_measurable", 3))
            self.assertIn("Actions cache", m["unverified"][0]["reason"])


class ReproducibleAndSafe(Case):
    def fixture(self):
        self.make(600)
        write(self.data / "financial_history_ledger.json", {"schema": 1, "updated": "2026-10-23", "stocks": {sym(1): fh_entry(1, [year(2026, total_revenue=2.0)], sector="Some Sector")}})
        write(self.data / "shareholding_ledger.json", {"schema": 1, "stocks": {sym(2): {"isin": None, "quarters": [sh_quarter("2026-09-30")]}}})
        write(self.data / "historical" / "shards" / "S0003.json", {"stocks": {"S0003": {"candles": [candle("2026-10-22")]}}})
        write(self.data / "historical" / "historical.json", {"updated": "2026-10-23", "stocks": {}, "errors": []})
        write(self.data / "historical" / "attempts.json", {"failed": {}})
        fp = self.root / "fund.json"
        write(fp, {"as_of": "2026-10-22", "stocks": [fund_row(1)], "errors": []})
        return fp

    def test_two_runs_give_byte_identical_reports(self):
        fp = self.fixture()
        a, b = self.root / "a", self.root / "b"
        for o in (a, b):
            self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(o), "--fundamentals", str(fp))[0], 0)
        for n in C.OUTPUT_NAMES:
            self.assertEqual((a / n).read_bytes(), (b / n).read_bytes(), n)
        self.assertEqual(C.render_json(self.build("both", fp)), C.render_json(self.build("both", fp)))
        self.assertEqual(sorted(p.name for p in a.iterdir()), sorted(C.OUTPUT_NAMES))      # no temporary file is left behind

    def test_percentages_are_stable_and_use_an_explicit_denominator(self):
        fp = self.fixture()
        m = self.mod(self.build("both", fp), "first_500", "financial_history")
        self.assertEqual((m["denominator"]["value"], m["denominator"]["basis"], m["coverage_pct"]), (500, "first_500_of_universe", 0.2))
        self.assertEqual(self.mod(self.build("both", fp), "all", "financial_history")["denominator"]["basis"], "all_universe")

    def test_the_report_records_input_hashes(self):
        fp = self.fixture()
        r = self.build("both", fp)
        by = {i["path"]: i for i in r["inputs"]}
        self.assertEqual(by["universe.json"]["sha256"], hashlib.sha256((self.data / "universe.json").read_bytes()).hexdigest())
        self.assertIn("supplied:fundamentals_file", by)
        self.assertNotIn("fund.json", json.dumps(r))      # the local file name never reaches a report
        self.assertEqual(by["historical/shards/"]["files"], 1)

    def test_inputs_are_never_changed_and_only_two_files_are_written(self):
        fp = self.fixture()
        before = (self.tree(self.data), self.tree(self.root / "fund.json") if False else hashlib.sha256(fp.read_bytes()).hexdigest())
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--fundamentals", str(fp))[0], 0)
        self.assertEqual((self.tree(self.data), hashlib.sha256(fp.read_bytes()).hexdigest()), before)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(C.OUTPUT_NAMES))

    def test_no_network_no_subprocess_during_a_full_run(self):
        fp = self.fixture()

        def boom(*a, **k):
            raise AssertionError("a network or process call was attempted")
        with mock.patch.object(socket.socket, "connect", boom), mock.patch.object(socket, "create_connection", boom), mock.patch.object(socket, "getaddrinfo", boom), \
                mock.patch.object(subprocess, "Popen", boom):
            rc, out, err = self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--fundamentals", str(fp))
        self.assertEqual(rc, 0, err)

    def test_the_module_imports_nothing_that_can_reach_a_provider(self):
        tree = ast.parse((HERE / "coverage_report.py").read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        self.assertEqual(imported - {"argparse", "datetime", "hashlib", "json", "math", "os", "re", "sys", "pathlib", "shareholding_ledger", "universe"}, set())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertEqual(names & {"requests", "urllib", "socket", "subprocess", "Popen", "urlopen", "environ", "getenv", "system", "popen", "Upstox", "Nse", "connect"}, set())
        self.assertEqual([n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.lower().startswith(("http://", "https://"))], [])
        text = (HERE / "coverage_report.py").read_text(encoding="utf-8")
        for other in ("research_test_batch", "historical_handoff", "SECTOR_PUBLIC_DISPLAY_APPROVED"):      # names other guard tests watch for in top-level files
            self.assertNotIn(other, text)

    def test_the_report_holds_no_shareholding_value_or_holder(self):
        fp = self.fixture()
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--fundamentals", str(fp))[0], 0)
        for n in C.OUTPUT_NAMES:
            t = (self.out / n).read_text(encoding="utf-8")
            self.assertNotIn(str(SECRET_PCT), t)
            self.assertNotIn(SECRET_HOLDER, t)
            self.assertNotIn("Some Sector", t)        # sector names are not listed either
        sh = self.mod(json.loads((self.out / "coverage_report.json").read_text()), "all", "shareholding")
        self.assertEqual(sh["facts"]["newest_quarter_categories_present_counts"], {"5": 1})

    def test_unsafe_output_folders_are_refused_and_nothing_is_written(self):
        fp = self.fixture()
        outside = self.root / "elsewhere"
        outside.mkdir()
        link = self.root / "link"
        link.symlink_to(outside, target_is_directory=True)
        a_file = self.root / "afile"
        a_file.write_text("x", encoding="utf-8")
        bad = [self.data, self.data / "inside", self.root, link, a_file, self.root / "no_parent" / "out", HERE, HERE / "reports", fp.parent / ".git"]
        before = self.tree(self.data)
        for b in bad:
            rc, _, err = self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(b), "--fundamentals", str(fp))
            self.assertEqual(rc, 2, str(b))
            self.assertIn("refused", err)
        self.assertEqual(self.tree(self.data), before)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((HERE / "reports").exists())
        self.assertFalse((HERE / "coverage_report.json").exists())

    def test_bad_arguments_are_refused(self):
        self.make(3)
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-02-30", "--out", str(self.out))[0], 2)
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--out", str(self.out))[0], 2)          # --as-of is required: no clock
        self.assertEqual(self.run_main("--data-branch", str(self.root / "none"), "--as-of", "2026-10-23", "--out", str(self.out))[0], 2)
        self.assertFalse(self.out.exists())


class SameRulesAsTheUpdaters(unittest.TestCase):
    """Drift guard: the constants copied into the report must still equal the updaters' own. The updaters are imported here only, with the network blocked."""

    def test_constants_match_the_updaters(self):
        def boom(*a, **k):
            raise AssertionError("network attempted")
        with mock.patch.object(socket.socket, "connect", boom), mock.patch.object(socket, "getaddrinfo", boom):
            try:
                import financial_history_updater as fh
                import fundamentals_updater as fu
                import shareholding_updater as su
            except ImportError as e:
                self.skipTest("an updater could not be imported here (%s)" % e)
        try:
            import stock_directory as sd
        except ImportError:
            sd = None
        if sd is not None:
            self.assertEqual(C.DIRECTORY_KIND, sd.KIND)
        self.assertEqual(C.FUNDAMENTALS_MAX_AGE_DAYS, fu.RATIOS_MAX_AGE_DAYS)
        self.assertEqual(C.NOT_FOUND_ERROR, fu.NOT_FOUND_ERROR)
        self.assertEqual(list(C.FUNDAMENTALS_METRICS), sorted(fu.METRICS, key=list(C.FUNDAMENTALS_METRICS).index))
        self.assertEqual(set(C.FUNDAMENTALS_METRICS), set(fu.METRICS))
        self.assertEqual(list(C.FH_VALUE_FIELDS), fh.VALUE_FIELDS)
        self.assertEqual(C.SH_QUARTER_ENDS, su.QUARTER_ENDS)
        self.assertEqual(C.SH_STATUS, su.STATUS)
        self.assertEqual(list(C.SH_CATEGORIES), su.CATEGORIES)
        self.assertEqual(list(C.SH_COLLECTOR_SCOPE), su.collection_symbols({}))      # the collector's own default scope (no FOLLOW_UNIVERSE), 15 stocks
        self.assertEqual(len(C.SH_COLLECTOR_SCOPE), 15)


if __name__ == "__main__":
    unittest.main()
