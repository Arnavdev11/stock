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


SECRET_FIN = 987654.321
SECRET_Q = 555444.333
SECRET_SECTOR = "ZZ PRIVATE SECTOR LABEL"
SECRET_ERROR = "ZZ PROVIDER ERROR TEXT"
METRIC_NAMES = ("pe", "pb", "roa", "roe", "roce", "ev_ebitda")


def f_entry(i, fetched="2026-10-22", **kw):
    e = {"isin": isin(i), "company_name": sym(i) + " LTD", "sector": SECRET_SECTOR, "ratios": {m: 20.5 + k for k, m in enumerate(METRIC_NAMES)},
         "ratios_fetched": fetched, "profile_fetched": fetched, "last_attempt": None, "last_error": None}
    e.update(kw)
    return e


def fin_section(fetched="2026-10-20", **kw):
    s = {"data": {"revenue": SECRET_FIN, "net_profit": SECRET_FIN}, "basis": "consolidated", "fetched": fetched, "schema": 2, "error": None, "attempted": fetched, "failures": 0}
    s.update(kw)
    return s


def fin_entry(i, fetched="2026-10-20", **kw):
    e = {"isin": isin(i), "company_name": sym(i) + " LTD", "last_error": None, "income": fin_section(fetched), "balance_sheet": fin_section(fetched), "cash_flow": fin_section(fetched)}
    e.update(kw)
    return e


def q_entry(checked="2026-10-20", **kw):
    e = {"basis": "consolidated", "last_attempt": checked, "last_checked": checked, "last_success": checked, "problem": None,
         "quarters": {"2026-06-30": {"quarter": "Q1", "retrieved_on": checked, "values": {"revenue": SECRET_Q, "profit_after_tax": None}, "checks": {}, "revisions": []}}}
    e.update(kw)
    return e


def q_doc(stocks):
    return {"schema_version": 1, "kind": "quarterly_cache", "stocks": stocks}


class StateDirCase(Case):
    def setUp(self):
        super().setUp()
        self.state = self.root / "state"
        self.state.mkdir()

    def put(self, name, doc):
        write(self.state / name, doc)

    def build_s(self, scope="all"):
        return C.build_report(self.data, AS_OF, scope, None, self.state)

    def sm(self, r, name, scope="all"):
        return self.mod(r, scope, name)

    def fixture10(self):
        self.make(10)
        self.put("fundamentals_cache.json", {"stocks": {
            sym(0): f_entry(0), sym(1): f_entry(1, "2026-10-10"), sym(3): {}, sym(4): {"last_error": SECRET_ERROR, "last_attempt": "2026-10-22"},
            sym(5): {"last_error": C.NOT_FOUND_ERROR}, sym(6): f_entry(6, ratios={"pe": 1.0}), sym(7): f_entry(7, "2026-12-01"),
            sym(8): {"held_isin_change": {"isin": "INE000000001", "listed_isin": "INE000000002"}}, sym(9): f_entry(9, ratios={m: None for m in METRIC_NAMES})}})
        self.put("quarterly_cache.json", q_doc({
            sym(0): q_entry(), sym(1): q_entry("2026-09-01"), sym(3): q_entry(quarters={}), sym(4): q_entry(quarters={}, last_checked=None, last_success=None, last_attempt=None),
            sym(5): q_entry(quarters={}, problem=SECRET_ERROR, failures=2), sym(6): q_entry(quarters={"2026-06-30": {"quarter": "Q1", "values": {"revenue": "12"}}}),
            sym(7): q_entry("2026-12-01"), sym(8): q_entry(last_checked=None), sym(9): q_entry(basis="weird")}))
        self.put("financials_cache.json", {"stocks": {
            sym(0): fin_entry(0), sym(1): fin_entry(1, income=fin_section("2026-09-01")), sym(3): {"income": fin_section()},
            sym(4): {"last_error": SECRET_ERROR, "income": {"error": SECRET_ERROR, "attempted": "2026-10-22", "failures": 1}},
            sym(5): {"last_error": C.NOT_FOUND_ERROR}, sym(6): fin_entry(6, balance_sheet=fin_section(schema=1)), sym(7): {},
            sym(8): fin_entry(8, cash_flow=fin_section("2027-01-01")), sym(9): fin_entry(9)}})


class StateDirBackwardCompatible(StateDirCase):
    def test_without_state_dir_nothing_changes_even_when_a_state_folder_exists(self):
        self.fixture10()
        r = self.build("all")
        self.assertNotIn("state_files", r)
        names = [m["module"] for m in r["scopes"]["all"]["modules"]]
        self.assertEqual(names, ["directory", "fundamentals", "financial_history", "growth", "historical_candles", "technical_indicators", "sector", "shareholding",
                                 "quarterly_results", "annual_financials_and_balance_sheet", "stock_detail_data"])
        for n in ("quarterly_results", "annual_financials_and_balance_sheet"):
            m = self.sm(r, n)
            self.assertEqual((m["measurability"], m["counts"]["valid"], m["counts"]["unverified"], m["coverage_pct"]), ("not_measurable", None, 10, None))
        self.assertNotIn("state:", json.dumps(r["inputs"]))
        self.assertEqual(C.render_json(r), C.render_json(C.build_report(self.data, AS_OF, "all", None, None)))

    def test_the_command_line_without_the_flag_is_unchanged(self):
        self.fixture10()
        a, b = self.root / "a", self.root / "b"
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(a))[0], 0)
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(b), "--scope", "both")[0], 0)
        for n in C.OUTPUT_NAMES:
            self.assertEqual((a / n).read_bytes(), (b / n).read_bytes())
        self.assertNotIn("fundamentals_state", (a / "coverage_report.json").read_text())


class StateDirClassification(StateDirCase):
    def test_fundamentals_state_counts_and_reasons(self):
        self.fixture10()
        m = self.sm(self.build_s(), "fundamentals_state")
        self.assertEqual(m["counts"], {"valid": 2, "stale": 1, "missing": 4, "invalid": 2, "not_applicable": None, "unverified": 1})
        self.assertEqual(m["missing_reasons"], {"failed_last_attempt": 1, "never_fetched": 1, "no_cache_entry": 1, "not_in_upstox": 1})
        self.assertEqual(m["invalid_reasons"], {"ratios_not_exactly_the_six_metrics": 1, "profile_fetched_not_a_past_date": 1})
        self.assertEqual(m["denominator"]["value"], 10)
        self.assertEqual((m["coverage_pct"], m["fresh_pct"]), (30.0, 20.0))
        self.assertEqual(m["freshness"]["threshold_days"], 7)
        self.assertEqual(m["facts"]["ratio_completeness_of_stocks_with_ratios"]["all_six_metrics"], 2)
        self.assertEqual(m["facts"]["ratio_completeness_of_stocks_with_ratios"]["no_metrics"], 1)
        self.assertEqual(m["facts"]["held_aside_after_isin_change"], 1)
        self.assertTrue(any("not proof of publication" in u["reason"] for u in m["unverified"]))

    def test_quarterly_counts_and_reasons(self):
        self.fixture10()
        m = self.sm(self.build_s(), "quarterly_results")
        self.assertEqual(m["counts"], {"valid": 1, "stale": 2, "missing": 4, "invalid": 3, "not_applicable": None, "unverified": 0})
        self.assertEqual(m["missing_reasons"], {"checked_no_quarters_stored": 1, "failed_last_attempt": 1, "never_checked": 1, "no_cache_entry": 1})
        self.assertEqual(m["invalid_reasons"], {"basis_not_recognised": 1, "last_attempt_not_a_past_date": 1, "quarter_value_not_a_number_or_null": 1})
        self.assertEqual(m["freshness"]["threshold_days"], 30)
        self.assertEqual(m["facts"]["stocks_with_quarters"], 3)
        self.assertEqual(m["facts"]["newest_quarter_end_counts"], {"2026-06-30": 3})
        self.assertEqual(m["measurability"], "partial")

    def test_financials_counts_and_reasons(self):
        self.fixture10()
        m = self.sm(self.build_s(), "annual_financials_and_balance_sheet")
        self.assertEqual(m["counts"], {"valid": 2, "stale": 2, "missing": 5, "invalid": 1, "not_applicable": None, "unverified": 0})
        self.assertEqual(m["missing_reasons"], {"failed_last_attempt": 1, "incomplete_sections": 1, "never_fetched": 1, "no_cache_entry": 1, "not_in_upstox": 1})
        self.assertEqual(m["invalid_reasons"], {"cash_flow:fetched_not_a_past_date": 1})
        self.assertEqual(m["facts"]["stale_reasons"], {"a_section_has_an_older_schema": 1, "a_section_older_than_threshold": 1})
        self.assertEqual(m["freshness"]["threshold_days"], 30)
        self.assertEqual(m["facts"]["required_sections"], ["income", "balance_sheet", "cash_flow"])
        self.assertEqual(m["facts"]["stocks_with_all_three_sections"], 4)          # 2 valid + 2 stale (one by age, one by an older schema)

    def test_fundamentals_state_is_added_after_fundamentals_and_the_other_modules_are_kept(self):
        self.fixture10()
        names = [m["module"] for m in self.build_s()["scopes"]["all"]["modules"]]
        self.assertEqual(names[names.index("fundamentals") + 1], "fundamentals_state")
        self.assertEqual(len(names), 12)
        self.assertEqual(names.count("quarterly_results"), 1)
        self.assertEqual(names.count("annual_financials_and_balance_sheet"), 1)

    def test_counts_reconcile_in_both_scopes_with_the_right_denominators(self):
        self.make(600)
        self.put("fundamentals_cache.json", {"stocks": {sym(i): f_entry(i) for i in range(0, 600, 2)}})
        self.put("quarterly_cache.json", q_doc({sym(i): q_entry() for i in range(0, 600, 3)}))
        self.put("financials_cache.json", {"stocks": {sym(i): fin_entry(i) for i in range(0, 600, 5)}})
        r = self.build_s("both")
        for scope, n in (("first_500", 500), ("all", 600)):
            for name in ("fundamentals_state", "quarterly_results", "annual_financials_and_balance_sheet"):
                m = self.sm(r, name, scope)
                self.assertEqual(m["denominator"]["value"], n, name)
                self.assertEqual(sum(v for v in m["counts"].values() if v is not None), n, (scope, name))
        self.assertEqual(self.sm(r, "fundamentals_state", "first_500")["counts"]["valid"], 250)
        self.assertEqual(self.sm(r, "quarterly_results", "all")["counts"]["valid"], 200)
        self.assertEqual(self.sm(r, "annual_financials_and_balance_sheet", "first_500")["counts"]["valid"], 100)
        self.assertEqual(self.sm(r, "annual_financials_and_balance_sheet", "first_500")["coverage_pct"], 20.0)

    def test_entries_for_stocks_outside_the_universe_are_listed_not_counted(self):
        self.make(5)
        self.put("quarterly_cache.json", q_doc({sym(0): q_entry(), "ZZOUT": q_entry(), "bad key!": q_entry()}))
        r = self.build_s()
        m = self.sm(r, "quarterly_results")
        self.assertEqual((m["counts"]["valid"], m["counts"]["missing"], m["denominator"]["value"]), (1, 4, 5))
        self.assertEqual(r["reconciliation"]["present_in_stored_data_but_not_in_universe"]["state:quarterly_cache.json"], {"count": 1, "first": ["ZZOUT"]})
        self.assertEqual(m["facts"]["state_file"]["keys_that_are_not_valid_symbols"], 1)
        self.assertNotIn("bad key!", json.dumps(r))


class StateDirUnavailable(StateDirCase):
    def assert_unmeasured(self, m, n, text):
        self.assertEqual((m["measurability"], m["counts"]["valid"], m["counts"]["stale"], m["counts"]["unverified"]), ("not_measurable", None, None, n))
        self.assertIsNone(m["coverage_pct"])
        self.assertIsNone(m["fresh_pct"])
        self.assertEqual(sum(v for v in m["counts"].values() if v is not None), n)
        self.assertTrue(any(text in p for p in m["problems"]), m["problems"])
        self.assertIn("NOT measured", m["unverified"][0]["reason"])

    def test_missing_files_are_unverified_never_zero_coverage(self):
        self.make(7)
        r = self.build_s()
        for name in ("fundamentals_state", "quarterly_results", "annual_financials_and_balance_sheet"):
            self.assert_unmeasured(self.sm(r, name), 7, "is missing")

    def test_one_usable_file_does_not_rescue_the_others(self):
        self.make(7)
        self.put("quarterly_cache.json", q_doc({sym(0): q_entry()}))
        r = self.build_s()
        self.assertEqual(self.sm(r, "quarterly_results")["counts"]["valid"], 1)
        self.assert_unmeasured(self.sm(r, "fundamentals_state"), 7, "fundamentals_cache.json is missing")
        self.assert_unmeasured(self.sm(r, "annual_financials_and_balance_sheet"), 7, "financials_cache.json is missing")

    def test_a_genuine_read_failure_is_unreadable(self):
        self.make(4)
        self.put("quarterly_cache.json", q_doc({sym(0): q_entry()}))
        real = Path.read_bytes

        def flaky(p, *a, **k):
            if p.name == "quarterly_cache.json":
                raise PermissionError(13, "simulated")
            return real(p, *a, **k)
        with mock.patch.object(Path, "read_bytes", flaky):
            r = self.build_s()
        self.assert_unmeasured(self.sm(r, "quarterly_results"), 4, "quarterly_cache.json is unreadable")
        self.assertFalse(r["state_files"]["quarterly_cache.json"]["usable"])

    def test_malformed_json_is_reported(self):
        self.make(4)
        (self.state / "fundamentals_cache.json").write_text("{ not json", encoding="utf-8")
        (self.state / "quarterly_cache.json").write_text('{"stocks": {"S0000": NaN}}', encoding="utf-8")
        (self.state / "financials_cache.json").write_bytes(b"\xff\xfe\x00")
        r = self.build_s()
        for name, f in (("fundamentals_state", "fundamentals_cache.json"), ("quarterly_results", "quarterly_cache.json"), ("annual_financials_and_balance_sheet", "financials_cache.json")):
            self.assert_unmeasured(self.sm(r, name), 4, f + " is invalid_json")

    def test_unsupported_schemas_are_reported(self):
        self.make(4)
        cases = [("fundamentals_cache.json", [1, 2], "not a JSON object"), ("financials_cache.json", {"stocks": [1]}, "no stocks object"),
                 ("quarterly_cache.json", {"kind": "something_else", "stocks": {}}, "wrong kind")]
        for fname, doc, why in cases:
            self.put(fname, doc)
        r = self.build_s()
        self.assert_unmeasured(self.sm(r, "fundamentals_state"), 4, "unsupported_schema: not a JSON object")
        self.assert_unmeasured(self.sm(r, "annual_financials_and_balance_sheet"), 4, "unsupported_schema: no stocks object")
        self.assert_unmeasured(self.sm(r, "quarterly_results"), 4, "unsupported_schema: wrong kind")
        for doc in ({"schema_version": 2, "kind": "quarterly_cache", "stocks": {}}, {"schema_version": True, "kind": "quarterly_cache", "stocks": {}}):
            self.put("quarterly_cache.json", doc)
            self.assert_unmeasured(self.sm(self.build_s(), "quarterly_results"), 4, "schema_version is not 1")

    def test_an_empty_stocks_object_is_a_supported_schema_with_every_stock_missing(self):
        self.make(4)
        self.put("quarterly_cache.json", q_doc({}))
        m = self.sm(self.build_s(), "quarterly_results")
        self.assertEqual(m["counts"], {"valid": 0, "stale": 0, "missing": 4, "invalid": 0, "not_applicable": None, "unverified": 0})
        self.assertEqual(m["missing_reasons"], {"no_cache_entry": 4})

    def test_the_published_financials_file_is_not_mistaken_for_the_state(self):
        self.make(4)
        self.put("financials.json", {"schema": 2, "stocks": [{"symbol": sym(0)}]})
        m = self.sm(self.build_s(), "annual_financials_and_balance_sheet")
        self.assertEqual(m["measurability"], "not_measurable")
        self.assertTrue(any("published output, not the saved state" in p for p in m["problems"]))
        self.assertNotIn("state:financials.json", json.dumps(self.build_s()["inputs"]))      # it is not read or hashed

    def test_a_state_folder_that_is_not_a_folder_is_refused(self):
        self.make(3)
        rc, _, err = self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--state-dir", str(self.root / "nope"))
        self.assertEqual(rc, 2)
        self.assertIn("refused", err)
        self.assertFalse(self.out.exists())


class StateDirDuplicatesAndPrivacy(StateDirCase):
    def test_duplicate_symbol_keys_are_invalid_and_reported(self):
        self.make(5)
        e0, e1 = json.dumps(q_entry()), json.dumps(q_entry("2026-09-01"))
        (self.state / "quarterly_cache.json").write_text('{"schema_version": 1, "kind": "quarterly_cache", "stocks": {"S0001": %s, "S0001": %s, "S0002": %s}}' % (e0, e1, e0), encoding="utf-8")
        r = self.build_s()
        m = self.sm(r, "quarterly_results")
        self.assertEqual(m["counts"], {"valid": 1, "stale": 0, "missing": 3, "invalid": 1, "not_applicable": None, "unverified": 0})
        self.assertEqual(m["invalid_reasons"], {"duplicate_symbol_key": 1})
        self.assertEqual(m["facts"]["state_file"]["duplicate_symbol_keys"], {"distinct": 1, "in_this_scope": 1, "symbols": ["S0001"]})
        w = [x for x in m["warnings"] if x["code"] == "duplicate_symbol_keys_in_state_file"][0]
        self.assertEqual((w["count"], w["identifiers"]), (1, ["S0001"]))
        self.assertEqual(m["facts"]["state_file"]["entries"], 2)          # the parsed object holds one entry per distinct key
        self.assertEqual(sum(v for v in m["counts"].values() if v is not None), 5)
        md = C.render_markdown(r)
        self.assertIn("S0001", md)

    def test_duplicates_in_fundamentals_and_financials_are_invalid_too(self):
        self.make(3)
        (self.state / "fundamentals_cache.json").write_text('{"stocks": {"S0000": %s, "S0000": {}}}' % json.dumps(f_entry(0)), encoding="utf-8")
        (self.state / "financials_cache.json").write_text('{"stocks": {"S0000": %s, "S0000": {}}}' % json.dumps(fin_entry(0)), encoding="utf-8")
        r = self.build_s()
        for n in ("fundamentals_state", "annual_financials_and_balance_sheet"):
            m = self.sm(r, n)
            self.assertEqual(m["invalid_reasons"], {"duplicate_symbol_key": 1}, n)
            self.assertEqual(m["counts"]["valid"], 0)

    def test_the_report_holds_no_cache_value_error_text_or_record(self):
        self.fixture10()
        self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--state-dir", str(self.state))[0], 0)
        for n in C.OUTPUT_NAMES:
            t = (self.out / n).read_text(encoding="utf-8")
            for secret in (str(SECRET_FIN), "987654", str(SECRET_Q), "555444", SECRET_SECTOR, SECRET_ERROR, "company_name", " LTD", "20.5"):
                self.assertNotIn(secret, t, (n, secret))
            self.assertNotIn(isin(1), t)                  # no ISIN is copied from the cache
        self.assertIn("fundamentals_state", (self.out / "coverage_report.md").read_text())

    def test_no_network_or_process_call_is_made(self):
        self.fixture10()

        def boom(*a, **k):
            raise AssertionError("a network or process call was attempted")
        with mock.patch.object(socket.socket, "connect", boom), mock.patch.object(socket, "create_connection", boom), mock.patch.object(socket, "getaddrinfo", boom), \
                mock.patch.object(subprocess, "Popen", boom):
            rc, out, err = self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(self.out), "--state-dir", str(self.state))
        self.assertEqual(rc, 0, err)

    def test_state_files_are_hashed_unchanged_and_runs_are_identical(self):
        self.fixture10()
        before = self.tree(self.state)
        a, b = self.root / "a", self.root / "b"
        for o in (a, b):
            self.assertEqual(self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(o), "--state-dir", str(self.state))[0], 0)
        self.assertEqual(self.tree(self.state), before)
        for n in C.OUTPUT_NAMES:
            self.assertEqual((a / n).read_bytes(), (b / n).read_bytes())
        by = {i["path"]: i for i in json.loads((a / "coverage_report.json").read_text())["inputs"]}
        for fname in C.STATE_FILES.values():
            self.assertEqual(by["state:" + fname]["sha256"], hashlib.sha256((self.state / fname).read_bytes()).hexdigest())
        self.assertNotIn(str(self.state), (a / "coverage_report.json").read_text())      # no local path

    def test_an_output_folder_inside_or_equal_to_the_state_folder_is_refused(self):
        self.fixture10()
        for bad in (self.state, self.state / "out", self.root):
            rc, _, err = self.run_main("--data-branch", str(self.data), "--as-of", "2026-10-23", "--out", str(bad), "--state-dir", str(self.state))
            self.assertEqual(rc, 2, str(bad))
            self.assertIn("refused", err)

    def test_the_state_module_imports_stay_standard_library(self):
        tree = ast.parse((HERE / "coverage_report.py").read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertEqual(names & {"requests", "urllib", "socket", "subprocess", "Popen", "urlopen", "environ", "getenv", "Upstox"}, set())


def _updater():
    def boom(*a, **k):
        raise AssertionError("network attempted")
    with mock.patch.object(socket.socket, "connect", boom), mock.patch.object(socket, "getaddrinfo", boom):
        try:
            import quarterly_results_updater as qu
        except ImportError as e:
            raise unittest.SkipTest("the quarterly updater could not be imported here (%s)" % e)
    return qu


def q_body(*periods):
    """A response the real parse_quarterly accepts: quarterly summary lines for the given 'Mon YYYY' periods."""
    return {"data": {"time_period": "quarterly", "income_statement": [{"category": "revenue", "history": [{"period": p, "value": "100"} for p in periods]}]}}


class StateDirRealUpdaterPaths(StateDirCase):
    """The quarterly cache here is written by the updater's own run_batch / parse_quarterly with a fake fetch, not typed by hand."""

    def run_updater(self, qu, cache, bodies, day, n=4):
        entries = {sym(i): {"symbol": sym(i), "isin": isin(i), "name": sym(i)} for i in range(n)}
        calls = []

        def fetch(isin_, basis):
            calls.append((isin_, basis))
            return bodies.get(isin_, (None, "HTTP 500"))

        def blocked(*a, **k):
            raise AssertionError("network attempted")
        with mock.patch.object(socket.socket, "connect", blocked), mock.patch.object(socket, "getaddrinfo", blocked):
            summary = qu.run_batch(entries, cache, fetch, day, 50)
        return summary, calls

    def save(self, qu, cache):
        self.put("quarterly_cache.json", {"schema_version": qu.SCHEMA_VERSION, "kind": "quarterly_cache", "stocks": cache})

    def test_failure_no_data_rejected_and_success_are_four_different_things(self):
        qu = _updater()
        self.make(4)
        cache = {}
        bodies = {isin(0): (None, "HTTP 500"), isin(1): ({"data": None}, None), isin(2): ({"data": {"time_period": "yearly"}}, None), isin(3): (q_body("Mar 2026", "Jun 2026"), None)}
        self.run_updater(qu, cache, bodies, dt.date(2026, 10, 20))
        self.assertEqual((cache[sym(0)]["failures"], cache[sym(0)]["problem"], cache[sym(0)]["last_checked"]), (1, None, None))          # what the updater really writes
        self.assertEqual((cache[sym(1)]["failures"], cache[sym(1)]["problem"]), (0, "no_data"))
        self.assertEqual((cache[sym(2)]["failures"], cache[sym(2)]["problem"]), (0, "not_quarterly"))
        self.assertEqual((cache[sym(3)]["failures"], cache[sym(3)]["problem"], len(cache[sym(3)]["quarters"])), (0, None, 2))
        self.save(qu, cache)
        r = self.build_s()
        m = self.sm(r, "quarterly_results")
        self.assertEqual(m["counts"], {"valid": 1, "stale": 0, "missing": 3, "invalid": 0, "not_applicable": None, "unverified": 0})
        self.assertEqual(m["missing_reasons"], {"failed_last_attempt": 1, "checked_no_quarters_stored": 1, "checked_result_rejected": 1})
        self.assertEqual((m["facts"]["stocks_with_failed_requests"], m["facts"]["stocks_whose_last_check_returned_no_data"], m["facts"]["stocks_whose_last_check_was_rejected"]), (1, 1, 1))
        self.assertNotIn("with_a_recorded_problem", m["facts"])
        self.assertNotIn("not_quarterly", json.dumps(r))          # the updater's problem text is never copied

    def test_back_off_after_failures_with_old_quarters_is_reported_beside_stale(self):
        qu = _updater()
        self.make(1)
        cache = {}
        ok, bad = {isin(0): (q_body("Mar 2026", "Jun 2026"), None)}, {}
        self.run_updater(qu, cache, ok, dt.date(2026, 8, 1), 1)
        self.run_updater(qu, cache, bad, dt.date(2026, 10, 19), 1)
        self.run_updater(qu, cache, bad, dt.date(2026, 10, 20), 1)
        rec = cache[sym(0)]
        self.assertEqual((rec["failures"], rec["last_checked"], rec["last_attempt"], len(rec["quarters"])), (2, "2026-08-01", "2026-10-20", 2))      # old quarters kept
        _, calls = self.run_updater(qu, cache, bad, dt.date(2026, 10, 20), 1)
        self.assertEqual(calls, [])          # the updater itself waits: the stock is in back-off today
        self.save(qu, cache)
        m = self.sm(C.build_report(self.data, dt.date(2026, 10, 21), "all", None, self.state), "quarterly_results")
        self.assertEqual((m["counts"]["valid"], m["counts"]["stale"]), (0, 1))
        self.assertEqual(m["facts"]["stale_reasons"], {"stale_in_retry_backoff": 1})
        self.assertEqual(m["facts"]["refresh_states_of_stocks_with_quarters"], {"in_retry_backoff": 1, "results_pending_window": 0, "neither": 0})
        self.assertEqual(m["facts"]["due_for_recheck_under_the_updaters_rule"], 0)
        self.assertEqual(m["facts"]["stocks_with_failed_requests"], 1)
        later = self.sm(C.build_report(self.data, dt.date(2026, 10, 25), "all", None, self.state), "quarterly_results")      # the retry date has passed
        self.assertEqual(later["facts"]["refresh_states_of_stocks_with_quarters"]["in_retry_backoff"], 0)
        self.assertEqual(later["facts"]["due_for_recheck_under_the_updaters_rule"], 1)

    def test_results_pending_window_is_derived_from_stored_quarter_ends(self):
        qu = _updater()
        self.make(3)
        cache = {}
        self.run_updater(qu, cache, {isin(0): (q_body("Jun 2026"), None), isin(1): (q_body("Jun 2026"), None), isin(2): (q_body("Mar 2026"), None)}, dt.date(2026, 10, 15), 3)
        cache[sym(1)]["last_checked"] = cache[sym(1)]["last_attempt"] = "2026-10-22"          # checked yesterday
        self.save(qu, cache)
        m = self.sm(self.build_s(), "quarterly_results")           # as of 2026-10-23
        self.assertEqual(m["counts"]["valid"], 3)          # all checked within 30 days: the steady rule alone
        self.assertEqual(m["facts"]["refresh_states_of_stocks_with_quarters"], {"in_retry_backoff": 0, "results_pending_window": 2, "neither": 1})      # Jun-2026 stocks: window 2026-10-10..2027-01-08
        self.assertEqual(m["facts"]["due_for_recheck_under_the_updaters_rule"], 1)          # 8 days since the check, 2-day rule inside the window; yesterday's is not due; the Mar stock is not due
        self.assertTrue(any("whether a new quarter has arrived" in u["what"] for u in m["unverified"]))
        for sy in (sym(0), sym(1), sym(2)):
            rec = cache[sy]
            self.assertEqual(qu.results_pending(rec, AS_OF), sy in (sym(0), sym(1)))      # the updater agrees on the window

    def test_the_copied_refresh_rules_agree_with_the_updater_over_a_grid(self):
        qu = _updater()
        for ends in (["2026-03-31"], ["2026-06-30"], ["2025-12-31", "2026-03-31"], ["2026-09-30"]):
            for failures in (0, 1, 2, 3, 6):
                for att in (0, 1, 2, 3, 5, 8, 40):
                    for chk in (None, 0, 1, 2, 3, 29, 30, 31):
                        rec = {"basis": "consolidated", "failures": failures, "problem": None, "quarters": {e: {"values": {}} for e in ends},
                               "last_attempt": (AS_OF - dt.timedelta(days=att)).isoformat(),
                               "last_checked": None if chk is None else (AS_OF - dt.timedelta(days=chk)).isoformat()}
                        backoff, pending, due = C._quarterly_refresh_state(rec, AS_OF)
                        self.assertEqual(backoff, bool(qu.in_backoff(rec, AS_OF)), rec)
                        self.assertEqual(pending, bool(qu.results_pending(rec, AS_OF)), rec)
                        self.assertEqual(due, bool(qu.needs_refresh(rec, AS_OF)), rec)


class StateDirBoundariesAndFinancials(StateDirCase):
    def days_ago(self, n):
        return (AS_OF - dt.timedelta(days=n)).isoformat()

    def test_freshness_boundaries_are_6_7_and_29_30_days(self):
        self.make(6)
        self.put("fundamentals_cache.json", {"stocks": {sym(0): f_entry(0, self.days_ago(6)), sym(1): f_entry(1, self.days_ago(7))}})
        self.put("financials_cache.json", {"stocks": {sym(0): fin_entry(0, self.days_ago(29)), sym(1): fin_entry(1, self.days_ago(30))}})
        self.put("quarterly_cache.json", q_doc({sym(0): q_entry(self.days_ago(29), quarters={"2026-03-31": q_entry()["quarters"]["2026-06-30"]}),
                                                 sym(1): q_entry(self.days_ago(30), quarters={"2026-03-31": q_entry()["quarters"]["2026-06-30"]})}))
        r = self.build_s()
        for name in ("fundamentals_state", "annual_financials_and_balance_sheet", "quarterly_results"):
            m = self.sm(r, name)
            self.assertEqual((m["counts"]["valid"], m["counts"]["stale"], m["counts"]["missing"]), (1, 1, 4), name)
        self.assertEqual(self.sm(r, "quarterly_results")["facts"]["stale_reasons"], {"last_check_older_than_interval": 1})

    def test_an_older_financial_schema_is_stale_not_invalid(self):
        self.make(6)
        old, absent = fin_entry(0), fin_entry(1)
        old["income"]["schema"] = 1
        del absent["balance_sheet"]["schema"]
        newer, odd = fin_entry(2), fin_entry(3)
        newer["cash_flow"]["schema"] = 3
        odd["income"]["schema"] = True
        self.put("financials_cache.json", {"stocks": {sym(0): old, sym(1): absent, sym(2): newer, sym(3): odd, sym(4): fin_entry(4)}})
        m = self.sm(self.build_s(), "annual_financials_and_balance_sheet")
        self.assertEqual(m["counts"], {"valid": 1, "stale": 2, "missing": 1, "invalid": 2, "not_applicable": None, "unverified": 0})
        self.assertEqual(m["facts"]["stale_reasons"], {"a_section_has_an_older_schema": 2})
        self.assertEqual(m["invalid_reasons"], {"cash_flow:schema_not_recognised": 1, "income:schema_not_recognised": 1})
        self.assertEqual(m["facts"]["section_states_counted_over_stocks_with_an_entry"]["income"]["outdated_schema"], 1)
        self.assertEqual(sum(v for v in m["counts"].values() if v is not None), 6)
        self.assertEqual(m["coverage_pct"], 50.0)

    def test_incomplete_statements_are_distinguishable_and_make_no_publication_claim(self):
        self.make(5)
        two = fin_entry(1)
        del two["balance_sheet"]
        self.put("financials_cache.json", {"stocks": {sym(0): fin_entry(0), sym(1): two, sym(2): {"income": fin_section()}, sym(3): {"cash_flow": fin_section()}}})
        m = self.sm(self.build_s(), "annual_financials_and_balance_sheet")
        self.assertEqual(m["counts"], {"valid": 1, "stale": 0, "missing": 4, "invalid": 0, "not_applicable": None, "unverified": 0})
        self.assertEqual(m["missing_reasons"], {"incomplete_sections": 3, "no_cache_entry": 1})
        inc = m["facts"]["incomplete_statements"]
        self.assertEqual((inc["stocks"], inc["sections_held"]), (3, {"cash_flow": 1, "income": 1, "income+cash_flow": 1}))
        self.assertEqual(m["facts"]["stocks_with_all_three_sections"], 1)
        note = [u for u in m["unverified"] if "incomplete" in u["what"]][0]
        self.assertEqual(note["count"], 3)
        self.assertIn("may still be published", note["reason"])
        self.assertIn("not a statement about publication", inc["meaning"])
        self.assertNotIn("unpublished", json.dumps(m))

    def test_a_repeated_top_level_stocks_key_makes_the_file_unusable(self):
        self.make(3)
        body = json.dumps(q_entry())
        (self.state / "quarterly_cache.json").write_text('{"schema_version": 1, "kind": "quarterly_cache", "stocks": {"S0000": %s}, "stocks": {}}' % body, encoding="utf-8")
        (self.state / "fundamentals_cache.json").write_text('{"stocks": {}, "stocks": {"S0000": %s}}' % json.dumps(f_entry(0)), encoding="utf-8")
        (self.state / "financials_cache.json").write_text('{"stocks": {"S0000": %s}, "stocks": {}}' % json.dumps(fin_entry(0)), encoding="utf-8")
        r = self.build_s()
        for name, f in (("quarterly_results", "quarterly_cache.json"), ("fundamentals_state", "fundamentals_cache.json"), ("annual_financials_and_balance_sheet", "financials_cache.json")):
            m = self.sm(r, name)
            self.assertEqual((m["measurability"], m["counts"]["valid"], m["counts"]["unverified"], m["coverage_pct"]), ("not_measurable", None, 3, None), name)
            self.assertTrue(any("the stocks key appears more than once" in p for p in m["problems"]), m["problems"])


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

    def test_state_constants_match_the_updaters(self):
        def boom(*a, **k):
            raise AssertionError("network attempted")
        with mock.patch.object(socket.socket, "connect", boom), mock.patch.object(socket, "getaddrinfo", boom):
            try:
                import financials_updater as fin
                import processing_state as ps
                import quarterly_results_updater as qu
            except ImportError as e:
                self.skipTest("an updater could not be imported here (%s)" % e)
        self.assertEqual(C.STATE_FILES, {k: v for k, v in ps.KINDS.items()})
        self.assertEqual(C.FIN_SCHEMA, fin.SCHEMA_VERSION)
        self.assertEqual(C.STATEMENT_MAX_AGE_DAYS, fin.STATEMENT_MAX_AGE_DAYS)
        self.assertEqual(C.FIN_SECTIONS, tuple(x[0] for x in fin.SECTIONS))
        self.assertEqual(C.QUARTERLY_SCHEMA, qu.SCHEMA_VERSION)
        self.assertEqual(C.QUARTERLY_STEADY_DAYS, qu.STEADY_EVERY_DAYS)
        self.assertEqual(C.BASES, qu.BASES)
        self.assertEqual((C.QUARTERLY_PENDING_FROM_DAYS, C.QUARTERLY_PENDING_FOR_DAYS, C.QUARTERLY_PENDING_EVERY_DAYS), (qu.PENDING_FROM_DAYS, qu.PENDING_FOR_DAYS, qu.PENDING_EVERY_DAYS))
        self.assertEqual((C.MAX_RETRY_DAYS, C.MAX_RETRY_DAYS), (qu.MAX_RETRY_DAYS, fin.MAX_RETRY_DAYS))
        for y in (2024, 2025, 2026, 2027):
            for mo in range(1, 13):
                for n in (0, 1, 3, 9, 12):
                    day = dt.date(y, mo, 15)
                    self.assertEqual(C._add_months(day, n), qu.add_months(day, n), (day, n))
        for f in range(0, 9):
            self.assertEqual(C._retry_days(f), qu.retry_days(f))


if __name__ == "__main__":
    unittest.main()
