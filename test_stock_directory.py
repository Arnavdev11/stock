"""
stock_directory.py - the searchable stock-directory builder. SYNTHETIC fixtures only: these tests prove the rules of the builder (fail-closed checks, withholding,
metadata, determinism). They verify no real-world count, hash or source, and no count is hard-coded as an expected production figure.
"""
import copy
import gzip
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import stock_directory as D
from live import classifier as C

ROOT = Path(__file__).parent
DAY = "2026-01-15"


# synthetic fixture builders (self-contained, so the test file runs wherever stock_directory.py and live/ are)
def isin(i, p="INE"):
    return "%s%06dA01" % (p, i)


def up(i, symbol=None, sec="NORMAL", itype="EQ", name=None, **kw):
    r = {"segment": "NSE_EQ", "instrument_key": "NSE_EQ|%s" % isin(i), "trading_symbol": symbol or "S%d" % i, "isin": isin(i), "instrument_type": itype,
         "security_type": sec, "name": name or "Company %d" % i}
    r.update(kw)
    return r


def eql(*recs):
    return "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n" + "".join(
        "%s,Some Company Limited,%s,01-JAN-2000,1,1,%s,1\n" % r for r in recs)


def sme(*recs):
    return "SYMBOL,NAME OF COMPANY,SERIES,DATE OF LISTING,PAID UP VALUE,MARKET LOT,ISIN NUMBER,FACE VALUE\n" + "".join("%s,Small Co,SM,01-JAN-2020,10,1000,%s,10\n" % r for r in recs)


def etf(*recs):
    return "Symbol,Underlying,Series,ISIN\n" + "".join("%s,NIFTY,EQ,%s\n" % r for r in recs)


def inputs(**over):
    d = {n: D.describe_input(("file-" + n).encode()) for n in D.INPUT_NAMES}
    d.update(over)
    return d


APPROVAL = {"min_published": 3}
LOW = {"min_published": 1}


class Fx:
    """A small world: EQ and BE stocks, plus one of every exclusion/review outcome. Built fresh per test."""

    def __init__(self):
        self.rows = [up(1), up(2), up(3), up(4), up(5), up(6, sec="PCA"), up(7), up(8), up(9)]
        self.eq = [("S1", "EQ", isin(1)), ("S2", "BE", isin(2)), ("S3", "EQ", isin(3)), ("S5", "BZ", isin(5)), ("S6", "EQ", isin(6)), ("S7", "EQ", isin(7)), ("S7", "BE", isin(7))]
        self.sm = [("S4", isin(4)), ("SX", isin(700))]
        self.et = [("S8", isin(8)), ("EX", isin(701))]
        # S9 is in no reference -> review_unmatched

    def build(self, approval=APPROVAL, previous=None, inp=None, **kw):
        if isinstance(approval, dict) and "acknowledged_conflict_isins" not in approval:
            approval = dict(approval, acknowledged_conflict_isins=[isin(7)])        # the fixture's S7 is a deliberate conflict
        return D.build_directory(kw.get("rows", self.rows), kw.get("eq", eql(*self.eq)), kw.get("sm", sme(*self.sm)), kw.get("et", etf(*self.et)),
                                 inp or inputs(), approval, kw.get("built_on", DAY), previous)


class Publishes(unittest.TestCase):
    def test_eq_and_be_are_published_and_nothing_else(self):
        o = Fx().build()
        self.assertTrue(o.ok, o.problems)
        self.assertEqual([e["symbol"] for e in o.doc["symbols"]], ["S1", "S2", "S3"])       # S2 is BE, S1/S3 EQ
        self.assertEqual(o.doc["counts"]["published"], 3)

    def test_each_exclusion_is_absent(self):
        o = Fx().build()
        have = {e["isin"] for e in o.doc["symbols"]}
        for i, why in ((4, "SME"), (5, "BZ"), (6, "PCA"), (7, "conflict"), (8, "ETF"), (9, "unmatched")):
            self.assertNotIn(isin(i), have, why)

    def test_conflict_is_not_published_even_when_acknowledged(self):
        f = Fx()
        o = f.build({"min_published": 3, "acknowledged_conflict_isins": [isin(7)]})
        self.assertTrue(o.ok, o.problems)
        self.assertNotIn(isin(7), {e["isin"] for e in o.doc["symbols"]})
        self.assertEqual(o.doc["counts"]["conflicts_acknowledged"], 1)

    def test_entries_hold_only_symbol_isin_name_and_come_from_the_rows(self):
        f = Fx()
        o = f.build()
        for e in o.doc["symbols"]:
            self.assertEqual(set(e), {"symbol", "isin", "name"})
        self.assertEqual(o.doc["symbols"][0]["name"], "Company 1")
        txt = D.dumps(o.doc)
        for bad in ("instrument_key", "NSE_EQ|", "sector", "price", "rating", "target"):
            self.assertNotIn(bad, txt)

    def test_a_missing_name_is_withheld_not_invented(self):
        f = Fx()
        f.rows[0]["name"] = "   "
        o = f.build(LOW)
        self.assertTrue(o.ok, o.problems)
        self.assertNotIn("S1", [e["symbol"] for e in o.doc["symbols"]])
        h = o.report["held_out_candidates"]["records"]
        self.assertEqual([x["symbol"] for x in h], ["S1"])
        self.assertIn("company_name_missing_or_unusable", h[0]["reasons"])

    def test_deterministic_and_row_order_independent(self):
        f = Fx()
        a = D.dumps(f.build().doc)
        f.rows.reverse()
        self.assertEqual(a, D.dumps(f.build().doc))

    def test_the_count_is_not_hard_coded(self):
        f = Fx()
        for i in range(100, 130):
            f.rows.append(up(i))
            f.eq.append(("S%d" % i, "EQ", isin(i)))
        o = f.build({"min_published": 10})
        self.assertEqual(o.doc["counts"]["published"], 33)
        src = (ROOT / "stock_directory.py").read_text()
        self.assertNotRegex(src, r"\b2,?550\b")


class Withholding(unittest.TestCase):
    def held(self, o):
        return {x["symbol"]: x["reasons"] for x in o.report["held_out_candidates"]["records"]}

    def test_duplicate_symbol_withholds_every_claimant(self):
        f = Fx()
        f.rows.append(up(20, symbol="S1"))
        f.eq.append(("S1", "EQ", isin(20)))
        o = f.build(LOW)
        self.assertTrue(o.ok, o.problems)
        self.assertNotIn("S1", [e["symbol"] for e in o.doc["symbols"]])
        self.assertEqual(len(o.report["held_out_candidates"]["records"]), 2)
        self.assertTrue(all("duplicate_symbol" in r for r in self.held(o).values()))

    def test_duplicate_isin_withholds(self):
        f = Fx()
        dup = up(1, symbol="S1B")
        dup["instrument_key"] = "NSE_EQ|OTHER"
        f.rows.append(dup)
        o = f.build(LOW)
        self.assertNotIn(isin(1), {e["isin"] for e in o.doc["symbols"]})
        self.assertTrue(all("duplicate_isin" in r for r in self.held(o).values()))

    def test_duplicate_instrument_key_withholds(self):
        f = Fx()
        dup = up(30)
        dup["instrument_key"] = f.rows[0]["instrument_key"]
        f.rows.append(dup)
        f.eq.append(("S30", "EQ", isin(30)))
        o = f.build(LOW)
        self.assertNotIn("S1", [e["symbol"] for e in o.doc["symbols"]])
        self.assertIn("duplicate_instrument_key", self.held(o)["S1"])

    def test_symbol_disagreeing_with_reference_is_withheld(self):
        f = Fx()
        f.eq[0] = ("OTHERNAME", "EQ", isin(1))
        o = f.build(LOW)
        self.assertNotIn("S1", [e["symbol"] for e in o.doc["symbols"]])
        self.assertIn("symbol_differs_from_EQL_record", self.held(o)["S1"])

    def test_symbol_not_accepted_is_withheld(self):
        f = Fx()
        f.rows[0]["trading_symbol"] = "bad symbol!"
        f.eq[0] = ("BAD SYMBOL!", "EQ", isin(1))
        o = f.build(LOW)
        self.assertIn("symbol_not_accepted", next(iter(self.held(o).values())))

    def test_cap_on_withheld_candidates_fails_closed(self):
        f = Fx()
        f.rows[0]["name"] = ""
        o = f.build({"min_published": 1, "max_held_out_candidates": 0})
        self.assertFalse(o.ok)
        self.assertIsNone(o.doc)


class FailsClosed(unittest.TestCase):
    def refused(self, o, text):
        self.assertIsNone(o.doc)
        self.assertFalse(o.ok)
        self.assertTrue(any(text in p for p in o.problems), o.problems)

    def test_unacknowledged_conflict(self):
        self.refused(Fx().build({"min_published": 1, "acknowledged_conflict_isins": [isin(1)]}), "conflicting ISIN")

    def test_unresolved_equity_records_are_held_out_and_reported_not_a_refusal(self):
        f = Fx()
        f.eq += [("GHOST", "EQ", isin(900)), ("GHOSTBE", "BE", isin(901))]
        o = f.build()
        self.assertTrue(o.ok, o.problems)
        u = o.report["unresolved_reference_records"]
        self.assertEqual((u["count"], u["held_out"]), (2, True))
        self.assertEqual(sorted(r["symbol"] for r in u["records"]), ["GHOST", "GHOSTBE"])
        self.assertEqual(o.doc["counts"]["unresolved_reference_records_held_out"], 2)
        self.assertEqual([e["symbol"] for e in o.doc["symbols"]], ["S1", "S2", "S3"])           # never approved, never published
        txt = D.dumps(o.doc)
        for bad in ("GHOST", isin(900)):
            self.assertNotIn(bad, txt)

    def test_an_approval_cannot_publish_an_unresolved_record(self):
        f = Fx()
        f.eq.append(("GHOST", "EQ", isin(900)))
        o = f.build({"min_published": 3, "acknowledged_unresolved_isins": [isin(900)]})
        self.assertNotIn("GHOST", D.dumps(o.doc))

    def test_other_series_unmatched_records_are_not_counted_as_unresolved(self):
        g = Fx()
        g.eq.append(("GHOSTDEBT", "N1", isin(902)))
        self.assertEqual(g.build().report["unresolved_reference_records"]["count"], 0)

    def test_unresolved_records_do_not_hide_a_genuine_conflict(self):
        f = Fx()
        f.eq.append(("GHOST", "EQ", isin(900)))
        self.refused(f.build({"min_published": 1, "acknowledged_conflict_isins": []}), "conflicting ISIN")

    def test_unresolved_records_do_not_hide_malformed_inputs(self):
        f = Fx()
        f.eq.append(("GHOST", "EQ", isin(900)))
        self.refused(f.build(sm=sme(("S4", "NOT-AN-ISIN"))), "malformed ISIN")
        self.refused(f.build(eq=eql(*f.eq) .replace("SERIES", "SER")), "no SERIES column")
        self.refused(f.build(rows=f.rows + ["junk"]), "not objects")
        self.refused(f.build(None), "no approval record")

    def test_unresolved_records_do_not_hide_duplicate_candidate_identifiers(self):
        f = Fx()
        f.eq.append(("GHOST", "EQ", isin(900)))
        f.rows.append(up(20, symbol="S1"))
        f.eq.append(("S1", "EQ", isin(20)))
        o = f.build(LOW)
        self.assertNotIn("S1", [e["symbol"] for e in o.doc["symbols"]])
        self.assertEqual(o.report["unresolved_reference_records"]["count"], 1)

    def test_no_approval_record(self):
        self.refused(Fx().build(None), "no approval record")
        self.refused(Fx().build({}), "min_published")
        self.refused(Fx().build({"min_published": 0}), "min_published")
        self.refused(Fx().build({"min_published": True}), "min_published")

    def test_below_the_approved_minimum(self):
        self.refused(Fx().build({"min_published": 4}), "fewer than the approved minimum")

    def test_nothing_to_publish(self):
        f = Fx()
        f.rows = [up(9)]
        f.eq, f.sm, f.et = [("S1", "EQ", isin(1))], [("S4", isin(4))], [("S8", isin(8))]
        self.refused(f.build({"min_published": 1}), "nothing would be published")

    def test_no_nse_eq_rows(self):
        f = Fx()
        self.refused(f.build(rows=[{"segment": "NSE_INDEX"}], approval={"min_published": 1}), "no NSE_EQ rows")

    def test_non_object_rows(self):
        f = Fx()
        self.refused(f.build(rows=f.rows + ["junk"]), "not objects")

    def test_rows_not_a_list(self):
        self.refused(Fx().build(rows={"a": 1}), "not a list")

    def test_invalid_reference_files(self):
        f = Fx()
        self.refused(f.build(eq="SYMBOL,SERIES\nA,EQ\n"), "no ISIN column")
        self.refused(f.build(sm=""), "empty")
        self.refused(f.build(et=None), "not a string")
        self.refused(f.build(eq="SYMBOL,ISIN NUMBER\nA,%s\n" % isin(1)), "no SERIES column")
        self.refused(f.build(sm="SYMBOL,ISIN\n"), "no data rows")
        self.refused(f.build(sm=sme(("S4", "NOT-AN-ISIN"))), "malformed ISIN")
        self.refused(f.build(et="Symbol,ISIN\nE,\n"), "without an ISIN")

    def test_every_malformed_reference_still_returns_no_document(self):
        for kw in ({"eq": "x"}, {"sm": "\n\n"}, {"et": "a,b\n1,2\n"}):
            o = Fx().build(**kw)
            self.assertIsNone(o.doc)

    def test_input_descriptions(self):
        f = Fx()
        bad = inputs()
        del bad["sme"]
        self.refused(f.build(inp=bad), "input sme is not described")
        bad = inputs(etf={"bytes": 0, "sha256": "zz", "as_of": None, "source": None})
        o = f.build(inp=bad)
        self.refused(o, "size missing")
        self.refused(o, "sha256 missing")
        self.refused(f.build(inp=inputs(etf=D.describe_input(b"x", as_of="2026-02-30"))), "not a real")
        self.refused(f.build(inp=inputs(etf=D.describe_input(b"x", as_of="15/01/2026"))), "not a real")
        self.refused(f.build(inp=inputs(etf=D.describe_input(b"x", source="  "))), "source")
        self.refused(f.build(inp=inputs(extra={})), "unknown input")

    def test_build_date_must_be_supplied_and_real(self):
        self.refused(Fx().build(built_on="today"), "built_on")
        self.refused(Fx().build(built_on=None), "built_on")

    def test_incomplete_partition_is_refused(self):
        real = C.classify_universe

        def broken(*a):
            c = real(*a)
            c.records = c.records[:-1]
            return c
        D.classifier.classify_universe = broken
        try:
            self.refused(Fx().build(), "complete partition")
        finally:
            D.classifier.classify_universe = real

    def test_a_partition_flag_that_is_false_is_refused(self):
        real = C.classify_universe

        def flag(*a):
            c = real(*a)
            c.partition_ok = False
            return c
        D.classifier.classify_universe = flag
        try:
            self.refused(Fx().build(), "complete partition")
        finally:
            D.classifier.classify_universe = real

    def test_unknown_status_is_refused(self):
        real = C.classify_universe

        def odd(*a):
            c = real(*a)
            c.records[0]["status"] = "approved_by_magic"
            return c
        D.classifier.classify_universe = odd
        try:
            self.refused(Fx().build(), "status the classifier does not define")
        finally:
            D.classifier.classify_universe = real

    def test_every_refusal_has_no_document(self):
        for ap in (None, {}, {"min_published": 99}):
            self.assertIsNone(Fx().build(ap).doc)


class Drift(unittest.TestCase):
    def previous(self, n=10, start=100):
        f = Fx()
        for i in range(start, start + n):
            f.rows.append(up(i))
            f.eq.append(("S%d" % i, "EQ", isin(i)))
        return f, f.build({"min_published": 1}).doc

    def test_small_change_passes_and_is_reported(self):
        f, prev = self.previous(30)
        f.rows = [r for r in f.rows if r["isin"] != isin(100)]
        f.eq = [e for e in f.eq if e[2] != isin(100)]
        o = f.build({"min_published": 1}, previous=prev)
        self.assertTrue(o.ok, o.problems)
        self.assertEqual(o.report["drift"]["removed"], 1)
        self.assertEqual(o.report["drift"]["removed_isins"], [isin(100)])

    def test_large_drop_is_refused(self):
        f, prev = self.previous(30)
        f.rows = f.rows[:10]
        f.eq = [e for e in f.eq if e[2] in {r["isin"] for r in f.rows}]
        o = f.build({"min_published": 1}, previous=prev)
        self.assertIsNone(o.doc)
        self.assertTrue(any("vanished" in p for p in o.problems), o.problems)

    def test_large_growth_is_refused(self):
        f, prev = self.previous(10)
        g = Fx()
        for i in range(100, 160):
            g.rows.append(up(i))
            g.eq.append(("S%d" % i, "EQ", isin(i)))
        o = g.build({"min_published": 1}, previous=prev)
        self.assertTrue(any("were added" in p for p in o.problems), o.problems)

    def test_the_tolerance_comes_from_the_approval_record(self):
        f, prev = self.previous(30)
        f.rows = [r for r in f.rows if r["isin"] not in (isin(100), isin(101))]
        f.eq = [e for e in f.eq if e[2] not in (isin(100), isin(101))]
        self.assertTrue(f.build({"min_published": 1, "max_drop_fraction": 0.5}, previous=prev).ok)
        self.assertIsNone(f.build({"min_published": 1, "max_drop_fraction": 0.0}, previous=prev).doc)
        self.assertIsNone(f.build({"min_published": 1, "max_drop_fraction": 7}, previous=prev).doc)

    def test_invalid_previous_is_refused(self):
        f, prev = self.previous(5)
        bad = copy.deepcopy(prev)
        bad["symbols"][0]["name"] = "tampered"
        o = f.build({"min_published": 1}, previous=bad)
        self.assertTrue(any("previous directory is not valid" in p for p in o.problems), o.problems)

    def test_symbol_change_is_reported_not_refused(self):
        f, prev = self.previous(30)
        i = f.rows.index(next(r for r in f.rows if r["isin"] == isin(100)))
        f.rows[i]["trading_symbol"] = "RENAMED"
        f.eq = [("RENAMED", s, n) if n == isin(100) else (a, s, n) for a, s, n in f.eq]
        o = f.build({"min_published": 1}, previous=prev)
        self.assertTrue(o.ok, o.problems)
        self.assertEqual(o.report["drift"]["symbol_changed"], 1)

    def test_first_build_has_no_drift_section(self):
        self.assertIsNone(Fx().build().report["drift"])


class Metadata(unittest.TestCase):
    def test_inputs_hashes_counts_and_build_date(self):
        raws = {n: ("content of %s" % n).encode() for n in D.INPUT_NAMES}
        inp = {n: D.describe_input(raws[n]) for n in D.INPUT_NAMES}
        inp["equity"] = D.describe_input(raws["equity"], as_of="2026-01-10", source="a source the person supplied")
        o = Fx().build(inp=inp)
        d = o.doc
        self.assertEqual(d["built_on"], DAY)
        self.assertEqual([i["name"] for i in d["inputs"]], list(D.INPUT_NAMES))
        for i in d["inputs"]:
            self.assertEqual(i["bytes"], len(raws[i["name"]]))
            self.assertEqual(i["sha256"], hashlib.sha256(raws[i["name"]]).hexdigest())
        by = {i["name"]: i for i in d["inputs"]}
        self.assertEqual(by["equity"]["as_of"], "2026-01-10")
        self.assertEqual(by["equity"]["source"], "a source the person supplied")
        for n in ("instruments", "sme", "etf"):
            self.assertIsNone(by[n]["as_of"])            # never inferred
            self.assertIsNone(by[n]["source"])

    def test_classification_matches_the_classifier(self):
        f = Fx()
        o = f.build()
        c = C.classify_universe(f.rows, *[D.reference.parse(l, t) for l, t in (("equity", eql(*f.eq)), ("sme", sme(*f.sm)), ("etf", etf(*f.et)))])
        cl = o.doc["classification"]
        self.assertEqual(cl["counts"], c.counts)
        self.assertEqual(cl["hashes"], c.hashes)
        self.assertEqual(cl["global_hash"], c.global_hash)
        self.assertEqual(cl["nse_eq_rows"], len(f.rows))
        self.assertEqual(sum(cl["counts"].values()), cl["nse_eq_rows"])
        self.assertEqual(o.doc["counts"]["candidates"], c.candidates)
        self.assertEqual(o.doc["rules"]["eligible_statuses"], list(C.CANDIDATES))
        self.assertEqual(o.doc["rules"]["precedence"], list(C.STATUS_ORDER))

    def test_the_document_passes_its_own_check_and_a_tampered_one_does_not(self):
        d = Fx().build().doc
        self.assertEqual(D.check_directory_doc(d), [])
        self.assertEqual(D.check_directory_doc(json.loads(D.dumps(d))), [])
        for mutate in (lambda x: x["symbols"].reverse(), lambda x: x["symbols"][0].update(sector="X"), lambda x: x.update(extra=1),
                       lambda x: x["counts"].update(published=99), lambda x: x["symbols"].append(dict(x["symbols"][0])), lambda x: x.update(kind="universe"),
                       lambda x: x["inputs"][0].update(as_of="soon"), lambda x: x["classification"]["counts"].update(eligible_eq=999)):
            t = copy.deepcopy(d)
            mutate(t)
            self.assertNotEqual(D.check_directory_doc(t), [])

    def test_held_out_records_are_not_in_the_published_document(self):
        f = Fx()
        f.rows[0]["name"] = ""
        o = f.build(LOW)
        self.assertNotIn("reasons", D.dumps(o.doc))
        self.assertEqual(o.doc["counts"]["withheld_candidates"], 1)

    def test_no_language_of_advice(self):
        txt = D.dumps(Fx().build().doc).lower()
        for w in ("buy", "sell", "target", "rating", "recommend"):
            self.assertNotRegex(txt, r"\b%s\b" % w)


class Safety(unittest.TestCase):
    SRC = (ROOT / "stock_directory.py").read_text()

    def test_no_eligibility_logic_is_duplicated(self):
        for needle in ('"BE"', '"BZ"', "excluded_sme", "excluded_etf", "review_pca", "== \"EQ\""):
            self.assertNotIn(needle, re.sub(r'""".*?"""', "", self.SRC, flags=re.S).replace('UNRESOLVED_SERIES = ("EQ", "BE")', ""), needle)

    def test_the_module_cannot_reach_the_network_a_token_or_all_eq(self):
        code = re.sub(r'""".*?"""', "", self.SRC, flags=re.S)
        for needle in ("requests", "urllib", "socket", "http", "environ", "getenv", "token", "TOKEN", "subprocess", "all_eq", "nifty", "NIFTY500"):
            self.assertNotIn(needle, code, needle)

    def test_all_eq_is_not_wired_into_any_workflow(self):
        for p in (ROOT / ".github" / "workflows").glob("*.yml"):
            t = p.read_text()
            self.assertNotRegex(t, r"--mode\s+all_eq|mode:\s*all_eq", p.name)

    def test_protected_files_do_not_import_the_builder(self):
        for name in ("universe.py", "nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py",
                     "shareholding_updater.py", "historical_updater.py"):           # update.yml and index.html name the file on purpose; the Wiring tests pin exactly how
            p = ROOT / name
            if p.exists():
                self.assertNotIn("stock_directory", p.read_text(), name)

    @unittest.skipUnless((ROOT / "universe.py").exists(), "universe.py is not part of the validation package")
    def test_reference_sources_file_claims_nothing_unverified(self):
        d = json.loads((ROOT / "reference_sources.json").read_text())
        self.assertEqual([i["name"] for i in d["inputs"]], list(D.INPUT_NAMES))
        for i in d["inputs"]:
            self.assertEqual(i["reachable_from_github_actions"], "not verified")
            self.assertEqual(i["usage_terms"], "not read")
            self.assertNotIn("as_of", i)
            if i["name"] != "instruments":
                self.assertIsNone(i["url"])
                self.assertIn("not verified", i["url_status"])
            else:
                self.assertEqual(i["url"], __import__("universe").INSTRUMENTS_URL)
                self.assertIn("not fetched", i["url_status"])
        self.assertNotRegex(json.dumps(d), r"nseindia|nsearchives")


@unittest.skipUnless((ROOT / "index.html").exists() and (ROOT / ".github" / "workflows" / "update.yml").exists(), "the site and workflows are not part of the validation package")
class Wiring(unittest.TestCase):
    """The searchable stock directory (stock_directory.json) is separate from the research-data universe (universe.json)."""
    HTML = (ROOT / "index.html").read_text(encoding="utf-8") if (ROOT / "index.html").exists() else ""
    WF = ROOT / ".github" / "workflows"

    def search_module(self):
        m = re.search(r'<script type="module" id="stocklens-search">(.*?)</script>', self.HTML, re.S)
        self.assertIsNotNone(m)
        return m.group(1)

    def code_only(self, src):
        return re.sub(r"/\*.*?\*/", "", src, flags=re.S)

    def test_search_reads_the_directory_and_never_the_research_universe(self):
        code = self.code_only(self.search_module())
        self.assertEqual(sorted(set(re.findall(r"out/[a-z_]+\.json", code))), ["out/company_profiles.json", "out/fundamentals.json", "out/stock_directory.json"])
        self.assertNotIn("universe", code)
        self.assertIn('kind==="stock_directory"', code)
        self.assertEqual(len(re.findall(r"fetch\(", code)), 1)

    def test_the_page_outside_the_search_module_never_reads_the_directory(self):
        rest = self.HTML.replace(self.search_module(), "")
        self.assertNotIn("stock_directory", rest)

    def test_update_workflow_publishes_the_directory_with_one_guarded_copy(self):
        t = (self.WF / "update.yml").read_text()
        copies = [l for l in t.splitlines() if "stock_directory.json" in l and "cp " in l]
        self.assertEqual(copies, ["          if [ -f ledger-branch/stock_directory.json ]; then cp ledger-branch/stock_directory.json _site/out/stock_directory.json; fi"])
        self.assertLess(t.index("cp out/*.json _site/out/"), t.index("stock_directory.json"))
        self.assertLess(t.index("stock_directory.json"), t.index("Upload Pages artifact"))
        self.assertEqual(len(re.findall(r"stock_directory\.json", t)), 3)          # only the one copy line names the file (three times)
        self.assertNotRegex(t, r"STOCKLENS_UNIVERSE_FILE=[^\n]*stock_directory")
        self.assertNotIn("universe.json _site", t)                                  # the research universe is not published by this step

    def test_research_updaters_and_their_workflow_inputs_stay_on_universe_json(self):
        t = (self.WF / "update.yml").read_text()
        self.assertIn("python universe.py check ledger-branch/universe.json", t)
        self.assertIn("STOCKLENS_UNIVERSE_FILE=$GITHUB_WORKSPACE/ledger-branch/universe.json", t)
        for name in ("nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py", "shareholding_updater.py", "historical_updater.py", "universe.py", "upstox_common.py"):
            p = ROOT / name
            if p.exists():
                self.assertNotIn("stock_directory", p.read_text(), name)
        u = (self.WF / "universe.yml").read_text()
        self.assertNotIn("stock_directory", u)

    def test_no_other_workflow_mentions_the_directory_and_no_builder_workflow_exists(self):
        for p in self.WF.glob("*.yml"):
            if p.name not in ("update.yml", "historical.yml"):
                self.assertNotIn("stock_directory", p.read_text(), p.name)
        # historical.yml READS the directory (read-only checkout of the data branch) to choose the next batch of stocks, in exactly one command and nowhere else
        lines = [l.strip() for l in (self.WF / "historical.yml").read_text().splitlines() if "stock_directory" in l]
        self.assertEqual(lines, ["run: python historical_batch.py choose --directory ledger-branch/stock_directory.json --saved ledger-branch/historical --out batch"])
        self.assertFalse((self.WF / "stock_directory.yml").exists())

    def test_the_document_the_builder_writes_is_what_the_search_accepts(self):
        d = Fx().build().doc
        self.assertEqual(d["kind"], "stock_directory")
        self.assertIsInstance(d["symbols"], list)
        for e in d["symbols"]:
            self.assertEqual(set(e), {"symbol", "isin", "name"})            # the search reads symbol and name only; there is no sector field to read


class Cli(unittest.TestCase):
    def files(self, d, f, approval=dict(APPROVAL, acknowledged_conflict_isins=[isin(7)])):
        p = Path(d)
        (p / "i.json.gz").write_bytes(gzip.compress(json.dumps(f.rows).encode()))
        (p / "e.csv").write_text(eql(*f.eq))
        (p / "s.csv").write_text(sme(*f.sm))
        (p / "t.csv").write_text(etf(*f.et))
        (p / "a.json").write_text(json.dumps(approval))
        return ["--instruments-file", str(p / "i.json.gz"), "--equity-ref", str(p / "e.csv"), "--sme-ref", str(p / "s.csv"), "--etf-ref", str(p / "t.csv"),
                "--approval", str(p / "a.json"), "--built-on", DAY, "--out", str(p / "out.json")]

    def test_writes_one_valid_file_with_real_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            args = self.files(d, Fx()) + ["--asof", "equity=2026-01-10"]
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(D.main(args, out, err), 0, err.getvalue())
            doc = json.loads((Path(d) / "out.json").read_text())
            self.assertEqual(D.check_directory_doc(doc), [])
            self.assertEqual(doc["inputs"][0]["sha256"], hashlib.sha256((Path(d) / "i.json.gz").read_bytes()).hexdigest())
            self.assertEqual({i["name"]: i["as_of"] for i in doc["inputs"]}, {"instruments": None, "equity": "2026-01-10", "sme": None, "etf": None})

    def test_refusal_writes_nothing_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            args = self.files(d, Fx(), {"min_published": 50})
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(D.main(args, out, err), 3)
            self.assertFalse((Path(d) / "out.json").exists())
            self.assertIn("REFUSED", err.getvalue())

    def test_bad_arguments_and_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            args = self.files(d, Fx())
            self.assertEqual(D.main(args + ["--asof", "nope=2026-01-01"], io.StringIO(), io.StringIO()), 2)
            self.assertEqual(D.main(args + ["--source", "equity="], io.StringIO(), io.StringIO()), 2)
            (Path(d) / "e.csv").unlink()
            self.assertEqual(D.main(args, io.StringIO(), io.StringIO()), 2)
            self.assertFalse((Path(d) / "out.json").exists())

    def test_reports_are_written_even_on_refusal_and_hold_the_real_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fx()
            f.eq.append(("GHOST", "EQ", isin(900)))
            args = self.files(d, f, {"min_published": 50}) + ["--report-json", str(Path(d) / "r.json"), "--classifier-report", str(Path(d) / "c.txt")]
            self.assertEqual(D.main(args, io.StringIO(), io.StringIO()), 3)
            self.assertFalse((Path(d) / "out.json").exists())
            r = json.loads((Path(d) / "r.json").read_text())
            self.assertEqual(r["inputs"]["equity"]["sha256"], hashlib.sha256((Path(d) / "e.csv").read_bytes()).hexdigest())
            self.assertEqual(r["unresolved_reference_records"]["count"], 1)
            self.assertTrue(r["partition_ok"])
            self.assertEqual(sum(r["classification_counts"].values()), r["nse_eq_rows"])
            txt = (Path(d) / "c.txt").read_text()
            self.assertIn(r["classification_global_hash"], txt)
            self.assertIn(r["inputs"]["instruments"]["sha256"], txt)
            self.assertIn("not recorded", txt)             # no source date was supplied, none is invented

    def test_the_report_is_json_safe_for_every_outcome(self):
        for o in (Fx().build(), Fx().build(None), Fx().build(eq="x")):
            json.dumps(o.report)

    def test_output_never_prints_a_secret_shaped_value(self):
        with tempfile.TemporaryDirectory() as d:
            out, err = io.StringIO(), io.StringIO()
            D.main(self.files(d, Fx()), out, err)
            self.assertNotRegex(out.getvalue() + err.getvalue(), r"wss://|Bearer|access_token")


if __name__ == "__main__":
    unittest.main()
