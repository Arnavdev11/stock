"""
The offline universe classifier (live/reference.py, live/classifier.py, `--classify-universe`).

Every test here uses SYNTHETIC fixtures. They prove the rules, the precedence, the audit and the determinism; they do NOT verify any real-world count or hash.
The real-data gate at the bottom is skipped unless the real files are supplied, and then it checks structure (partition, determinism), never a hard-coded count.
"""
import ast
import gzip
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from live import classifier as C
from live import reference as R
from live_service import __main__ as cli

ROOT = Path(__file__).parent


def isin(i, p="INE"):
    return "%s%06dA01" % (p, i)


def up(i, symbol=None, sec="NORMAL", itype="EQ", name=None, **kw):
    r = {"segment": "NSE_EQ", "instrument_key": "NSE_EQ|%s" % isin(i), "trading_symbol": symbol or "S%d" % i, "isin": isin(i), "instrument_type": itype,
         "security_type": sec, "name": name or "Company %d" % i}
    r.update(kw)
    return r


EQL_HEAD = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"


def eql(*recs):
    """recs: (symbol, series, isin)"""
    return EQL_HEAD + "".join("%s,Some Company Limited,%s,01-JAN-2000,1,1,%s,1\n" % r for r in recs)


def sme(*recs):
    return "SYMBOL,NAME OF COMPANY,SERIES,DATE OF LISTING,PAID UP VALUE,MARKET LOT,ISIN NUMBER,FACE VALUE\n" + "".join("%s,Small Co,SM,01-JAN-2020,10,1000,%s,10\n" % r for r in recs)


def etf(*recs):
    return "Symbol,Underlying,Series,ISIN\n" + "".join("%s,NIFTY,EQ,%s\n" % r for r in recs)


def refs(eq="", sm="", et=""):
    return R.parse("equity", eql(*eq)), R.parse("sme", sme(*sm)), R.parse("etf", etf(*et))


def run(rows, eq=(), sm=(), et=()):
    e, s, t = refs(eq, sm, et)
    return C.classify_universe(rows, e, s, t)


def by_key(c):
    return {r["key"]: r for r in c.records}


def status_of(c, i):
    return by_key(c)["NSE_EQ|%s" % isin(i)]["status"]


class World:
    """One row for every outcome."""

    def __init__(self):
        self.rows = [up(1), up(2), up(3), up(4), up(5), up(6), up(7), up(8, sec="PCA"), up(9, sec="PCA"), up(10, sec="PCA"), up(11, sec="PCA"), up(12), up(13), up(14), up(15),
                     {"segment": "NSE_EQ"}, up(16, isin="", ), "junk", {"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50"}, up(17, sec="PCA", isin="")]
        self.eq = [("S1", "EQ", isin(1)), ("S2", "BE", isin(2)), ("S3", "BZ", isin(3)), ("S6", "EQ", isin(6)), ("S8", "BE", isin(8)), ("S9", "BZ", isin(9)), ("S10", "EQ", isin(10)),
                   ("S12", "XX", isin(12)), ("S13", "EQ", isin(13)), ("S13", "BE", isin(13)), ("GHOST", "EQ", isin(900))]
        self.sm = [("S4", isin(4)), ("S10", isin(10)), ("S11", isin(11)), ("GHOSTSME", isin(901))]
        self.et = [("S5", isin(5)), ("S6", isin(6)), ("S11", isin(11)), ("GHOSTETF", isin(902))]

    def run(self):
        return run(self.rows, self.eq, self.sm, self.et)


class PrecedenceAndPartition(unittest.TestCase):
    def setUp(self):
        self.c = World().run()

    def test_every_row_has_exactly_one_status_and_the_counts_close(self):
        c = self.c
        self.assertTrue(c.partition_ok)
        self.assertEqual(sum(c.counts.values()), c.nse_eq_rows)
        self.assertEqual(c.nse_eq_rows, 18, "seventeen full NSE_EQ rows + the bare NSE_EQ row; the index row and the junk entry are not NSE_EQ")
        self.assertEqual(sum(len(v) for v in c.lines.values()), c.nse_eq_rows)
        self.assertEqual(c.segments, {"NSE_EQ": 18, "NSE_INDEX": 1})
        self.assertEqual(c.non_object_rows, 1, "a row that is not an object is counted, not silently lost")

    def test_the_precedence(self):
        c = self.c
        self.assertEqual(status_of(c, 1), "eligible_eq")
        self.assertEqual(status_of(c, 2), "eligible_be")
        self.assertEqual(status_of(c, 3), "excluded_bz")
        self.assertEqual(status_of(c, 4), "excluded_sme")
        self.assertEqual(status_of(c, 5), "excluded_etf")
        self.assertEqual(status_of(c, 6), "excluded_etf", "ETF membership wins over the equity-list series EQ")
        self.assertEqual(status_of(c, 7), "review_unmatched")
        self.assertEqual(status_of(c, 8), "review_pca", "PCA wins over series BE")
        self.assertEqual(status_of(c, 9), "review_pca", "PCA wins over series BZ")
        self.assertEqual(status_of(c, 10), "excluded_sme", "SME wins over PCA")
        self.assertEqual(status_of(c, 11), "excluded_sme", "SME wins over ETF and PCA")
        self.assertEqual(status_of(c, 12), "review_other_series")
        self.assertEqual(status_of(c, 13), "review_conflict", "one ISIN under two series in the equity list: the series cannot be chosen")

    def test_additional_memberships_are_kept_in_the_audit(self):
        r = by_key(self.c)
        self.assertEqual(r["NSE_EQ|%s" % isin(10)]["memberships"], ["EQL:EQ", "PCA", "SME"])
        self.assertEqual(r["NSE_EQ|%s" % isin(11)]["memberships"], ["ETF", "PCA", "SME"])
        self.assertIn("multi_reference", r["NSE_EQ|%s" % isin(11)]["flags"])
        self.assertEqual(r["NSE_EQ|%s" % isin(6)]["memberships"], ["EQL:EQ", "ETF"])
        self.assertEqual(r["NSE_EQ|%s" % isin(9)]["memberships"], ["EQL:BZ", "PCA"])
        self.assertEqual(self.c.overlap["EQL:EQ+PCA+SME"], {"excluded_sme": 1})

    def test_pca_is_security_type_only_and_never_a_series(self):
        c = World().run()
        self.assertEqual(c.counts["review_pca"], 3, "ISIN 8, 9 and the PCA row without an ISIN")
        rows = [up(1, sec=" pca "), up(2, sec="PCAX"), up(3, sec="NORMAL", name="PCA Holdings")]
        cc = run(rows, eq=[("S1", "EQ", isin(1)), ("S2", "EQ", isin(2)), ("S3", "EQ", isin(3))])
        self.assertEqual([status_of(cc, i) for i in (1, 2, 3)], ["review_pca", "eligible_eq", "eligible_eq"])

    def test_missing_identifiers_are_explicit_and_nothing_is_dropped(self):
        r = {x["key"]: x for x in self.c.records}
        nokey = [x for x in self.c.records if x["key"] == ""]
        self.assertEqual([x["status"] for x in nokey], ["review_structural"])
        self.assertIn("no_isin", r["NSE_EQ|%s" % isin(16)]["flags"])
        self.assertEqual(r["NSE_EQ|%s" % isin(16)]["status"], "review_unmatched")
        self.assertEqual(r["NSE_EQ|%s" % isin(17)]["status"], "review_pca", "PCA comes from the snapshot field, so a missing ISIN does not hide it")
        bad = run([up(1, isin="NOT-AN-ISIN")], eq=[("S1", "EQ", isin(1))])
        self.assertEqual(bad.records[0]["status"], "review_unmatched")
        self.assertIn("malformed_isin", bad.records[0]["flags"])
        self.assertEqual(bad.records[0]["isin"], "")

    def test_same_isin_under_one_series_twice_is_not_a_conflict(self):
        c = run([up(1)], eq=[("S1", "EQ", isin(1)), ("S1", "EQ", isin(1))])
        self.assertEqual(c.counts["eligible_eq"], 1)
        self.assertEqual(c.counts["review_conflict"], 0)

    def test_the_unknown_is_never_called_debt(self):
        c = World().run()
        for r in c.records:
            self.assertNotIn("debt", r["status"] + r["reason"])
        self.assertEqual(status_of(c, 7), "review_unmatched")


class NamesAndTypesNeverDecide(unittest.TestCase):
    def test_names_are_ignored_even_when_they_look_like_funds_or_sme(self):
        rows = [up(1, name="Gold ETF Fund Units SME PCA BZ"), up(2, name="Plain Industries Limited")]
        c = run(rows, eq=[("S1", "EQ", isin(1))], et=[("S2", isin(2))])
        self.assertEqual((status_of(c, 1), status_of(c, 2)), ("eligible_eq", "excluded_etf"))

    def test_changing_every_name_changes_no_status_and_no_hash(self):
        w = World()
        a = w.run()
        for r in w.rows:
            if isinstance(r, dict):
                r["name"] = "ZZZ ETF FUND %d" % random.Random(5).randint(0, 9)
        b = w.run()
        self.assertEqual((a.counts, a.hashes, a.global_hash), (b.counts, b.hashes, b.global_hash))

    def test_the_instrument_type_is_recorded_but_does_not_decide(self):
        rows = [up(1, itype="BE"), up(2, itype="EQ"), up(3, itype="NG")]
        c = run(rows, eq=[("S1", "EQ", isin(1)), ("S2", "BE", isin(2)), ("S3", "EQ", isin(3))])
        self.assertEqual([status_of(c, i) for i in (1, 2, 3)], ["eligible_eq", "eligible_be", "eligible_eq"])
        self.assertEqual(by_key(c)["NSE_EQ|%s" % isin(1)]["instrument_type"], "BE")


class Matching(unittest.TestCase):
    def test_isin_is_the_key_and_the_symbol_is_only_a_diagnostic(self):
        rows = [up(1, symbol="NEWNAME"), up(2, symbol="SAMESYM"), up(3, symbol="ONLYSYM")]
        c = run(rows, eq=[("OLDNAME", "EQ", isin(1)), ("SAMESYM", "BE", isin(2)), ("ONLYSYM", "EQ", isin(99))])
        r = by_key(c)
        self.assertEqual(r["NSE_EQ|%s" % isin(1)]["status"], "eligible_eq", "matched by ISIN although the symbol differs")
        self.assertIn("symbol_differs_from_EQL_record", r["NSE_EQ|%s" % isin(1)]["flags"])
        self.assertEqual(r["NSE_EQ|%s" % isin(2)]["status"], "eligible_be")
        self.assertEqual(r["NSE_EQ|%s" % isin(3)]["status"], "review_unmatched", "a symbol-only match never classifies")
        self.assertIn("symbol_only_match_EQL", r["NSE_EQ|%s" % isin(3)]["flags"])

    def test_isin_matching_is_case_and_space_insensitive(self):
        c = run([up(1, isin="  " + isin(1).lower() + " ")], eq=[("S1", "EQ", " " + isin(1).lower())])
        self.assertEqual(c.records[0]["status"], "eligible_eq")

    def test_reference_records_without_an_upstox_match_are_listed_with_a_symbol_diagnostic(self):
        rows = [up(1), up(2, symbol="NATCOX")]
        c = run(rows, eq=[("S1", "EQ", isin(1)), ("NATCOX", "BE", isin(50)), ("AGOLX", "EQ", isin(51)), ("NOISIN", "EQ", "")])
        d = c.reference_side["equity"]
        self.assertEqual((d["records"], d["matched_by_isin"]), (4, 1))
        self.assertEqual([(u["symbol"], u["series"]) for u in d["unmatched"]], [("NATCOX", "BE"), ("AGOLX", "EQ")], "listed in ISIN order")
        nat = [u for u in d["unmatched"] if u["symbol"] == "NATCOX"][0]
        self.assertEqual(nat["symbol_in_upstox"], ["NSE_EQ|%s" % isin(2)], "the symbol exists in the snapshot under another ISIN: shown, not used")
        self.assertEqual([u["symbol_in_upstox"] for u in d["unmatched"] if u["symbol"] == "AGOLX"], [[]])
        self.assertEqual([x["symbol"] for x in d["without_valid_isin"]], ["NOISIN"])

    def test_upstox_rows_with_no_match_are_reported_by_stratum_with_a_bounded_sample(self):
        rows = [up(i, itype="NG") for i in range(100, 130)] + [up(200 + i, itype="NH", sec="X") for i in range(3)] + [up(300, isin="")]
        c = run(rows)
        strata = {(s["instrument_type"], s["security_type"], s["isin"]): s for s in c.upstox_unmatched}
        self.assertEqual(strata[("NG", "NORMAL", "has_isin")]["rows"], 30)
        self.assertEqual(len(strata[("NG", "NORMAL", "has_isin")]["sample"]), C.SAMPLE_PER_STRATUM)
        self.assertEqual(strata[("NH", "X", "has_isin")]["rows"], 3)
        self.assertEqual(strata[("EQ", "NORMAL", "no_isin")]["rows"], 1)
        keys = [r["key"] for r in strata[("NG", "NORMAL", "has_isin")]["sample"]]
        self.assertEqual(keys, sorted(keys), "the sample is deterministic: the first rows by ISIN")


class Duplicates(unittest.TestCase):
    def test_duplicate_symbols_are_flagged_and_every_row_is_kept(self):
        rows = [up(1, symbol="DUPE"), up(2, symbol="DUPE", sec="WARRANT"), up(3, symbol="DUPE"), up(4)]
        c = run(rows, eq=[("DUPE", "EQ", isin(1)), ("S4", "EQ", isin(4))])
        self.assertEqual(c.nse_eq_rows, 4)
        self.assertEqual(sum(c.counts.values()), 4)
        self.assertEqual([len(v) for v in c.duplicates["symbol"].values()], [3])
        for r in c.records:
            if r["symbol"] == "DUPE":
                self.assertIn("duplicate_symbol", r["flags"])
        self.assertEqual(status_of(c, 1), "eligible_eq", "a namesake never removes an eligible row")
        self.assertNotIn("duplicate_symbol", by_key(c)["NSE_EQ|%s" % isin(4)]["flags"])

    def test_duplicate_isin_and_duplicate_key_are_flagged_and_kept(self):
        rows = [up(1), up(1, symbol="OTHER", **{"instrument_key": "NSE_EQ|ALT"}), up(2), up(3, **{"instrument_key": "NSE_EQ|%s" % isin(2)})]
        c = run(rows)
        self.assertEqual(c.nse_eq_rows, 4)
        self.assertEqual(sorted(len(v) for v in c.duplicates["isin"].values()), [2])
        self.assertEqual(sorted(len(v) for v in c.duplicates["instrument_key"].values()), [2])
        self.assertTrue(all("duplicate_isin" in r["flags"] for r in c.records if r["isin"] == isin(1)))

    def test_the_report_lists_duplicate_groups_with_their_statuses(self):
        rows = [up(1, symbol="DUPE"), up(2, symbol="DUPE")]
        c = run(rows, eq=[("DUPE", "EQ", isin(1))])
        text = C.format_classification(c, [])
        self.assertIn("duplicate symbol groups: 1 (2 rows)", text)
        self.assertIn("status=eligible_eq", text)
        self.assertIn("status=review_unmatched", text)


class DeterminismAndHashes(unittest.TestCase):
    def test_shuffled_and_repeated_inputs_give_identical_everything(self):
        w = World()
        base = w.run()
        base_text = C.format_classification(base, [])
        for seed in range(12):
            rows = list(w.rows)
            random.Random(seed).shuffle(rows)
            eq, sm, et = list(w.eq), list(w.sm), list(w.et)
            for lst in (eq, sm, et):
                random.Random(seed + 100).shuffle(lst)
            c = run(rows, eq, sm, et)
            self.assertEqual((c.counts, c.hashes, c.global_hash, c.overlap, c.reference_side, c.upstox_unmatched), (base.counts, base.hashes, base.global_hash, base.overlap, base.reference_side, base.upstox_unmatched), seed)
            self.assertEqual(C.format_classification(c, []), base_text, "the whole report is identical, not only the hashes")
        self.assertEqual(w.run().global_hash, base.global_hash)

    def test_the_serialization_is_exactly_as_documented(self):
        rows = [up(2), up(1)]
        c = run(rows, eq=[("S1", "EQ", isin(1)), ("S2", "EQ", isin(2))])
        lines = sorted(["%s|S%d|NSE_EQ|%s|eligible_eq" % (isin(i), i, isin(i)) for i in (1, 2)])
        self.assertEqual(sorted(c.lines["eligible_eq"]), lines)
        self.assertEqual(c.hashes["eligible_eq"], hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest())
        self.assertEqual(c.hashes["excluded_sme"], hashlib.sha256(b"").hexdigest(), "an empty group hashes zero bytes")
        self.assertEqual(c.global_hash, hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest())

    def test_the_hash_does_not_depend_on_audit_only_fields(self):
        a = run([up(1, name="A", itype="EQ")], eq=[("S1", "EQ", isin(1))])
        b = run([up(1, name="B", itype="NG", extra="x")], eq=[("S1", "EQ", isin(1))])
        self.assertEqual(a.global_hash, b.global_hash)

    def test_serialization_variants_really_differ_and_are_distinguishable(self):
        lines = ["AB|X|k|s", "A|X|k|s"]
        self.assertEqual(len({h for _, h in C._variants(lines)}), 4)
        self.assertNotEqual(C.hash_lines(lines), C.hash_lines(lines, sort_mode="tuple"))
        self.assertNotEqual(C.hash_lines(lines), C.hash_lines(lines, trailing_lf=False))


class BaselineComparison(unittest.TestCase):
    def baseline_text(self, c, tweak=None):
        out = []
        for s in C.STATUS_ORDER:
            h = c.hashes[s] if s != tweak else "0" * 64
            out.append("- `%s` (%s): `%s`" % (s, format(c.counts[s], ","), h))
        out.append("- Global: `%s`" % c.global_hash)
        return "\n".join(out)

    def test_the_reported_layout_is_parsed(self):
        sample = "- `eligible_eq` (2,333): `%s`\n- Global: `%s`\nnoise\n" % ("a" * 64, "b" * 64)
        self.assertEqual(C.parse_baseline(sample), {"eligible_eq": (2333, "a" * 64), "global": (None, "b" * 64)})

    def test_a_matching_baseline_passes_and_a_changed_hash_fails_without_being_adopted(self):
        c = World().run()
        lines, ok = C.compare_baseline(c, C.parse_baseline(self.baseline_text(c)))
        self.assertTrue(ok, lines)
        self.assertTrue(all(l.strip().startswith("PASS") for l in lines))
        before = dict(c.hashes)
        lines, ok = C.compare_baseline(c, C.parse_baseline(self.baseline_text(c, tweak="eligible_be")))
        self.assertFalse(ok)
        self.assertTrue(any(l.strip().startswith("FAIL  eligible_be") for l in lines))
        self.assertEqual(c.hashes, before, "the comparison never changes what was computed")

    def test_a_serialization_difference_is_named_as_a_diagnostic_only(self):
        c = World().run()
        want = C.hash_lines(c.lines["eligible_eq"], trailing_lf=False)
        lines, ok = C.compare_baseline(c, {"eligible_eq": (c.counts["eligible_eq"], want)})
        self.assertFalse(ok)
        self.assertTrue(any("DIAGNOSTIC ONLY" in l and "without the final LF" in l for l in lines))

    def test_the_merged_unmatched_bucket_is_recognised_but_marked(self):
        c = World().run()
        merged = []
        for s in C.COMPAT_MERGE:
            merged += [l.rsplit("|", 1)[0] + "|review_unmatched" for l in c.lines[s]]
        want = {"review_unmatched": (len(merged), C.hash_lines(merged))}
        lines, ok = C.compare_baseline(c, want)
        self.assertTrue(ok)
        self.assertTrue(lines[0].strip().startswith("PASS*"), lines)

    def test_an_empty_or_unreadable_baseline_is_not_a_pass(self):
        lines, ok = C.compare_baseline(World().run(), C.parse_baseline("nothing useful here"))
        self.assertFalse(ok)


class ReferenceParsing(unittest.TestCase):
    def test_header_variants_bom_quotes_and_malformed_isins(self):
        text = "﻿ symbol , Name , SERIES ,isin_number\n\"A,B\",\"X, Y\",eq,%s\nC,Z,BE,%s\nD,W,EQ,BADISIN\nE,V,EQ,\n" % (isin(1).lower(), isin(2))
        ref = R.parse("equity", text)
        self.assertEqual([(r["symbol"], r["series"], r["isin"]) for r in ref.records], [("A,B", "EQ", isin(1)), ("C", "BE", isin(2)), ("D", "EQ", ""), ("E", "EQ", "")])
        self.assertEqual((ref.stats["rows_without_isin"], ref.stats["rows_with_malformed_isin"]), (1, 1))
        self.assertEqual(ref.columns, {"isin": "isin_number", "symbol": "symbol", "series": "SERIES"})

    def test_a_file_without_an_isin_column_or_with_no_content_is_refused(self):
        for bad in ("SYMBOL,SERIES\nA,EQ\n", "", "\n\n"):
            with self.assertRaises(R.ReferenceError):
                R.parse("x", bad)

    def test_a_reference_without_a_series_column_still_matches_by_isin(self):
        ref = R.parse("etf", "ISIN\n%s\n" % isin(1))
        self.assertEqual(ref.series_of(isin(1)), [])
        self.assertIn(isin(1), ref.by_isin)

    def test_isins_listed_more_than_once_are_counted(self):
        ref = R.parse("equity", eql(("A", "EQ", isin(1)), ("B", "BE", isin(1)), ("C", "EQ", isin(2))))
        self.assertEqual(ref.stats["isins_listed_more_than_once"], 1)
        self.assertEqual(ref.series_of(isin(1)), ["BE", "EQ"])


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        w = World()
        self.paths = {}
        self.write("NSE.json.gz", gzip.compress(json.dumps(w.rows).encode()))
        self.write("EQUITY_L.csv", eql(*w.eq).encode())
        self.write("SME_EQUITY_L.csv", sme(*w.sm).encode())
        self.write("nse_etfs.csv", etf(*w.et).encode())
        self.world = w.run()

    def write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        self.paths[name] = p

    @staticmethod
    def read(path):
        with open(path, "rb") as f:
            return f.read()

    def argv(self, *extra):
        return ["--classify-universe", "--instruments-file", self.paths["NSE.json.gz"], "--equity-ref", self.paths["EQUITY_L.csv"], "--sme-ref", self.paths["SME_EQUITY_L.csv"],
                "--etf-ref", self.paths["nse_etfs.csv"]] + list(extra)

    def go(self, argv):
        out, err = io.StringIO(), io.StringIO()
        rc = cli.classify_universe_cmd(cli.parse_args(argv), out, err)
        return rc, out.getvalue(), err.getvalue()

    def test_a_full_run_prints_every_section_and_the_partition_check(self):
        rc, out, err = self.go(self.argv())
        self.assertEqual(rc, 0, err)
        for needle in ("1. INPUTS", "3. STATUS COUNTS", "PARTITION OK", "4. REFERENCE OVERLAPS", "5. DUPLICATES", "6. REFERENCE RECORDS WITH NO UPSTOX MATCH",
                       "7. UPSTOX ROWS WITH NO EQUITY-REFERENCE MATCH", "8. AUDIT HASHES", self.world.global_hash, "NOT production approval"):
            self.assertIn(needle, out)
        self.assertEqual(out.count("sha256 "), 4, "each of the four inputs is recorded with its size and SHA-256")
        self.assertIn("as-of: not recorded", out)

    def test_it_writes_nothing_and_changes_no_input(self):
        digest = lambda: {n: hashlib.sha256(self.read(p)).hexdigest() for n, p in self.paths.items()}
        before = digest()
        listing = sorted(os.listdir(self.tmp.name))
        self.go(self.argv("--list", "eligible_eq"))
        self.assertEqual(sorted(os.listdir(self.tmp.name)), listing)
        self.assertEqual(before, digest())

    def test_dates_are_recorded_only_when_given_and_validated(self):
        rc, out, err = self.go(self.argv("--asof", "equity=2026-10-08", "--asof", "etf=2026-10-07"))
        self.assertEqual(rc, 0, err)
        self.assertIn("as-of: 2026-10-08", out)
        self.assertEqual(out.count("as-of: not recorded"), 2)
        for bad in ("equity=2026-13-45", "equity=", "nothing", "other=2026-10-08", "equity=26-10-08"):
            rc, out, err = self.go(self.argv("--asof", bad))
            self.assertEqual(rc, 2, bad)

    def test_the_file_contents_never_supply_a_date(self):
        rc, out, err = self.go(self.argv())
        self.assertNotIn("01-JAN-2000", out)
        self.assertEqual(out.count("as-of: not recorded"), 4)

    def test_list_prints_the_sorted_audit_lines_of_a_status(self):
        rc, out, err = self.go(self.argv("--list", "eligible_eq", "--list", "review_pca"))
        self.assertEqual(rc, 0, err)
        tail = out.split("LIST eligible_eq")[1]
        self.assertIn(sorted(self.world.lines["eligible_eq"])[0], tail)
        self.assertIn("LIST review_pca (3 lines)", out)
        rc, out, err = self.go(self.argv("--list", "not_a_status"))
        self.assertEqual(rc, 2)

    def test_missing_inputs_and_bad_files_are_clean_errors(self):
        rc, out, err = self.go(["--classify-universe", "--instruments-file", self.paths["NSE.json.gz"]])
        self.assertEqual(rc, 2)
        self.assertIn("never downloads", err)
        for name in ("EQUITY_L.csv", "SME_EQUITY_L.csv", "nse_etfs.csv"):
            saved = self.read(self.paths[name])
            self.write(name, b"SYMBOL,SERIES\nA,EQ\n")
            rc, out, err = self.go(self.argv())
            self.assertEqual(rc, 2, name)
            self.assertIn("no ISIN column", err)
            self.write(name, saved)
        rc, out, err = self.go(self.argv()[:-1] + [os.path.join(self.tmp.name, "missing.csv")])
        self.assertEqual(rc, 2)

    def test_the_baseline_comparison_sets_the_exit_code(self):
        good = "\n".join("- `%s` (%d): `%s`" % (s, self.world.counts[s], self.world.hashes[s]) for s in C.STATUS_ORDER) + "\n- Global: `%s`\n" % self.world.global_hash
        self.write("good.txt", good.encode())
        rc, out, err = self.go(self.argv("--baseline", self.paths["good.txt"]))
        self.assertEqual(rc, 0, out)
        self.assertIn("9. COMPARISON WITH THE SUPPLIED BASELINE", out)
        self.write("bad.txt", good.replace(self.world.hashes["eligible_eq"], "f" * 64).encode())
        rc, out, err = self.go(self.argv("--baseline", self.paths["bad.txt"]))
        self.assertEqual(rc, 3)
        self.assertIn("FAIL  eligible_eq", out)

    def test_it_refuses_to_be_combined_with_count_only_and_never_reads_the_token(self):
        self.assertEqual(cli.main(self.argv("--count-only"), env={}), 2)

    def test_no_network_no_token_no_environment_and_no_http_libraries(self):
        code = r'''
import builtins, socket, sys
def boom(*a, **k): raise AssertionError("network used")
socket.socket.connect = boom; socket.create_connection = boom; socket.getaddrinfo = boom; socket.gethostbyname = boom
real = builtins.__import__
def guard(name, *a, **k):
    if name.split(".")[0] in ("requests", "websockets", "urllib3", "aiohttp", "httpx"): raise AssertionError("imported " + name)
    return real(name, *a, **k)
builtins.__import__ = guard
class NoEnv(dict):
    def get(self, *a, **k): raise AssertionError("environment read")
    def __getitem__(self, *a): raise AssertionError("environment read")
from live_service import __main__ as cli
sys.exit(cli.main(sys.argv[1:], env=NoEnv()))
'''
        env = {k: v for k, v in os.environ.items() if k != "UPSTOX_ANALYTICS_TOKEN"}
        p = subprocess.run([sys.executable, "-B", "-I", "-c", "import sys; sys.path.insert(0, %r)\n%s" % (str(ROOT), code)] + self.argv(), capture_output=True, text=True, env=env, cwd=self.tmp.name)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PARTITION OK", p.stdout)
        p2 = subprocess.run([sys.executable, "-B", "-m", "live_service"] + self.argv(), capture_output=True, text=True, env=dict(os.environ, UPSTOX_ANALYTICS_TOKEN="SECRET-TOKEN-abc123XYZ"), cwd=str(ROOT))
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertNotIn("SECRET-TOKEN", p2.stdout + p2.stderr)


class ModuleGuards(unittest.TestCase):
    FILES = ("live/reference.py", "live/classifier.py")

    def src(self, f):
        return (ROOT / f).read_text(encoding="utf-8")

    def test_pure_modules_import_only_the_standard_library_basics(self):
        for f in self.FILES:
            tree = ast.parse(self.src(f))
            mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertTrue(mods <= {"csv", "io", "re", "hashlib", "json"}, (f, mods))

    def test_no_file_access_environment_network_or_secret_in_the_modules(self):
        for f in self.FILES:
            self.assertNotRegex(self.src(f), r"\bopen\(|os\.environ|getenv|socket|requests|urllib|Bearer|access_token|UPSTOX_|wss://|write_text|write_bytes", f)

    def test_no_name_based_classification_and_no_advice_words(self):
        for f in self.FILES:
            src = self.src(f)
            self.assertNotRegex(src, r"\[\"name\"\]\s*\)?\.(lower|upper)|\"name\"\]\s*\.|re\.search\([^)]*name", f)
            self.assertNotRegex(src.lower(), r"\b(buy|sell|target price|recommend|rating)\b", f)

    def test_protected_and_live_relay_files_are_untouched(self):
        import shutil
        if shutil.which("git") is None:
            self.skipTest("git is not installed here")
        protected = ["index.html", "nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py", "shareholding_updater.py", "historical_updater.py",
                     "update.yml", ".gitignore", "live/instruments.py", "live_service/upstox.py", "live_service/relay.py", "live_service/server.py", "live_service/safety.py"]
        out = subprocess.run(["git", "diff", "--name-only", "HEAD", "--"] + protected + [".github"], cwd=str(ROOT), capture_output=True, text=True).stdout.split()
        import guard_update_yml
        out = [f for f in out if not guard_update_yml.is_approved(ROOT, f)]      # the approved workflow changes only (tests/approved_workflow_changes.json)
        if "index.html" in out:
            # the live module (Phase 3) and the approved search module (universe search) may differ; with both removed from both sides the page must be byte-identical to HEAD,
            # and the search module must be exactly the version pinned in tests/search_module_pin.json
            import hashlib
            import re
            pats = (r'<script type="module" id="stocklens-live">[\s\S]*?</script>\n', r'<script type="module" id="stocklens-search">[\s\S]*?</script>\n')
            head = subprocess.run(["git", "show", "HEAD:index.html"], cwd=str(ROOT), capture_output=True).stdout.decode("utf-8")
            now = (ROOT / "index.html").read_bytes().decode("utf-8")
            pin = json.loads((ROOT / "tests" / "search_module_pin.json").read_text(encoding="utf-8"))["page_search_module_sha256"]
            a, b = now, head
            for pat in pats:
                a, b = re.sub(pat, "", a, count=1), re.sub(pat, "", b, count=1)
            if a == b and hashlib.sha256(now.split('<script type="module" id="stocklens-search">')[1].split("</script>")[0].encode("utf-8")).hexdigest() == pin:
                out.remove("index.html")
        self.assertEqual(out, [])


@unittest.skipUnless(os.environ.get("STOCKLENS_REAL_DATA_DIR"), "BLOCKED GATE: the real NSE.json.gz / EQUITY_L.csv / SME_EQUITY_L.csv / nse_etfs.csv are not available here, so real-data counts and hashes are NOT validated by this suite")
class RealDataGate(unittest.TestCase):
    def test_partition_and_determinism_on_the_real_files(self):
        d = Path(os.environ["STOCKLENS_REAL_DATA_DIR"])
        rows = json.loads(gzip.decompress((d / "NSE.json.gz").read_bytes()))
        e, s, t = (R.parse(n, (d / f).read_text(encoding="utf-8-sig")) for n, f in (("equity", "EQUITY_L.csv"), ("sme", "SME_EQUITY_L.csv"), ("etf", "nse_etfs.csv")))
        a = C.classify_universe(rows, e, s, t)
        shuffled = list(rows)
        random.Random(1).shuffle(shuffled)
        b = C.classify_universe(shuffled, e, s, t)
        self.assertTrue(a.partition_ok)
        self.assertEqual((a.counts, a.hashes, a.global_hash), (b.counts, b.hashes, b.global_hash))


if __name__ == "__main__":
    unittest.main()
