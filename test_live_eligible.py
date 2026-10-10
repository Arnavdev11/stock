"""Phase 4, Stage A - the eligible-universe filter and the --count-only command. No network, no token."""
import ast
import gzip
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from live import eligible
from live_service import __main__ as cli

ROOT = Path(__file__).parent



def row(i, **kw):
    r = {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE%06dA01" % i, "trading_symbol": "STK%d" % i, "name": "Company %d Limited" % i,
         "isin": "INE%06dA01" % i, "security_type": "NORMAL"}
    r.update(kw)
    return r


class ClassifyTests(unittest.TestCase):
    def test_a_clean_equity_is_eligible(self):
        e = eligible.classify([row(1), row(2)])
        self.assertEqual(e.equities, {"NSE_EQ|INE000001A01": "STK1", "NSE_EQ|INE000002A01": "STK2"})
        self.assertEqual(e.report["eligible_count"], 2)
        self.assertEqual(e.report["excluded_count"], 0)

    def test_every_exclusion_is_counted_by_reason(self):
        rows = [row(1), row(2, instrument_type="BE"), row(3, instrument_type="SME"), row(4, security_type="ODD"), row(5, isin="BAD"), row(6, isin=None),
                row(7, trading_symbol=""), row(8, trading_symbol="bad symbol!"), row(9, instrument_key=""), row(10, instrument_key=None), {"segment": "NSE_EQ"}, "junk", None, 5]
        e = eligible.classify(rows)
        ex = e.report["excluded"]
        self.assertEqual(e.report["eligible_count"], 1)
        self.assertEqual(ex["instrument_type:BE"], 1)
        self.assertEqual(ex["instrument_type:SME"], 1)
        self.assertEqual(ex["security_type:ODD"], 1)
        self.assertEqual(ex["no_valid_isin"], 2)
        self.assertEqual(ex["no_trading_symbol"], 1)
        self.assertEqual(ex["symbol_not_accepted"], 1)
        self.assertEqual(ex["no_instrument_key"], 3)               # "" , None, and the bare {"segment": "NSE_EQ"} row
        self.assertEqual(e.report["segments"]["(not an object)"], 3)
        self.assertEqual(e.report["excluded_count"] + e.report["eligible_count"], e.report["nse_eq_rows"])

    def test_other_segments_never_count(self):
        e = eligible.classify([row(1), {"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "NIFTY 50"}, {"segment": "NSE_FO", "instrument_key": "x"}])
        self.assertEqual(e.report["eligible_count"], 1)
        self.assertEqual(e.report["segments"], {"NSE_EQ": 1, "NSE_FO": 1, "NSE_INDEX": 1})

    def test_duplicates_are_not_trusted_either_way(self):
        e = eligible.classify([row(1), row(2, trading_symbol="STK1"), row(3), row(3)])
        self.assertEqual(sorted(e.equities.values()), [], "both claimants of STK1 and both of the repeated key are dropped")
        self.assertEqual(e.report["excluded"], {"duplicate_symbol": 4}, "STK1 claimed by two rows, STK3 by the same row twice: all four are dropped")
        e2 = eligible.classify([row(1), row(2, instrument_key="NSE_EQ|INE000001A01")])
        self.assertEqual(e2.report["excluded"], {"duplicate_instrument_key": 2}, "one key claimed by two different symbols")
        self.assertEqual(e2.equities, {})

    def test_symbol_is_normalised_like_the_relay(self):
        e = eligible.classify([row(1, trading_symbol=" m&m ")])
        self.assertEqual(list(e.equities.values()), ["M&M"])

    def test_fund_like_names_are_flagged_never_excluded(self):
        rows = [row(1), row(2, name="Nippon India ETF Gold BeES"), row(3, trading_symbol="LIQUIDBEES", name="Liquid Fund"), row(4, name="Fundamental Foods Limited")]
        e = eligible.classify(rows)
        self.assertEqual(e.report["eligible_count"], 4, "nothing is dropped on a name")
        self.assertEqual(e.report["flags_not_excluded"]["name_looks_like_fund_or_etf"], 2)
        self.assertFalse(any("Fundamental" in x for x in e.report["flag_examples"]["name_looks_like_fund_or_etf"]), "whole words only")

    def test_a_shared_isin_is_flagged_not_excluded(self):
        e = eligible.classify([row(1), row(2, isin="INE000001A01")])
        self.assertEqual(e.report["eligible_count"], 2)
        self.assertEqual(e.report["flags_not_excluded"]["isin_shared_by_several_symbols"], 2)

    def test_the_report_is_json_safe_and_bounded(self):
        rows = [row(i, instrument_type="BE") for i in range(500)]
        e = eligible.classify(rows)
        json.dumps(e.report)
        self.assertLessEqual(len(e.report["excluded_examples"]["instrument_type:BE"]), eligible.EXAMPLES)

    def test_a_large_file_is_fast_and_exact(self):
        rows = [row(i) for i in range(2400)] + [row(10000 + i, instrument_type="BE") for i in range(300)]
        e = eligible.classify(rows)
        self.assertEqual((e.report["eligible_count"], e.report["excluded_count"]), (2400, 300))

    def test_the_module_is_pure(self):
        tree = ast.parse((ROOT / "live" / "eligible.py").read_text(encoding="utf-8"))
        mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(mods, {"re", "json"})
        self.assertNotRegex((ROOT / "live" / "eligible.py").read_text(encoding="utf-8"), r"\bopen\(|socket|requests|urllib|environ|getenv")


def mixed_rows():
    rows = [row(i) for i in range(30)]
    rows += [row(100, name="Nippon India ETF Gold BeES", trading_symbol="GOLDBEES", isin="INF204KB17I5", lot_size=1),
             row(101, name="Liquid Fund Units", trading_symbol="LIQUIDETF", isin="INF000000101", lot_size=1),
             row(102, instrument_type="BE", trading_symbol="TTSTK-BE", lot_size=1), row(103, instrument_type="SM", trading_symbol="SMESTK-SM", lot_size=1200),
             row(104, instrument_type="SM", trading_symbol="SMESTK2-SM", lot_size=800), row(105, isin=None), row(106, trading_symbol="bad symbol!"),
             {"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "NIFTY 50"}, "junk", {"segment": "NSE_EQ"}]
    return rows


class ReviewTests(unittest.TestCase):
    def test_collecting_never_changes_the_rules(self):
        rows = mixed_rows()
        a, b = eligible.classify(rows), eligible.classify(rows, collect=True)
        self.assertEqual(a.report, b.report)
        self.assertEqual(a.equities, b.equities)
        self.assertIsNone(a.collected)

    def test_review_needs_collected_rows(self):
        with self.assertRaises(ValueError):
            eligible.review(mixed_rows(), eligible.classify(mixed_rows()))

    def test_every_flagged_instrument_is_printed_with_complete_metadata(self):
        rows = mixed_rows()
        res = eligible.classify(rows, collect=True)
        text = eligible.review(rows, res)
        n = res.report["flags_not_excluded"]["name_looks_like_fund_or_etf"]
        self.assertEqual(n, 2)
        self.assertIn("1. FLAGGED instruments (name looks like a fund/ETF; still counted as eligible): %d" % n, text)
        for r in rows[30:32]:
            self.assertIn(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")), text, "the complete row, every field")
        self.assertIn("INF204KB17I5", text)

    def test_flagged_count_matches_the_report_on_a_big_file(self):
        rows = [row(i) for i in range(3000)] + [row(5000 + i, name="Some ETF %d" % i) for i in range(43)]
        res = eligible.classify(rows, collect=True)
        self.assertEqual(len(res.collected["flagged_rows"]), 43)
        self.assertEqual(res.report["flags_not_excluded"]["name_looks_like_fund_or_etf"], 43)
        text = eligible.review(rows, res)
        self.assertEqual(text.count('"name":"Some ETF'), 43)

    def test_it_shows_which_fields_separate_the_types(self):
        rows = mixed_rows()
        text = eligible.review(rows, eligible.classify(rows, collect=True))
        self.assertIn("lot_size", text)
        self.assertIn('instrument_type="SM"  (2 rows)', text)
        sm = text.split('instrument_type="SM"  (2 rows)')[1].split("instrument_type=")[0]
        self.assertIn("1200", sm)
        self.assertIn("800", sm)

    def test_it_classifies_nothing(self):
        rows = mixed_rows()
        text = eligible.review(rows, eligible.classify(rows, collect=True))
        self.assertIn("NOT a validated classification", text)
        self.assertNotRegex(text, r"(?i)\b(is an etf|is sme|is a mutual fund|is debt)\b")
        self.assertIn("SYMBOL SUFFIX", text)
        self.assertIn("-SM", text)
        self.assertIn("-BE", text)

    def test_isin_structure_is_reported_as_observed(self):
        rows = mixed_rows()
        text = eligible.review(rows, eligible.classify(rows, collect=True))
        self.assertIn("INF", text)
        self.assertIn("INE", text)
        self.assertIn("characters 8-9", text)
        part = text.split("4. ISIN STRUCTURE")[1].split("5. SYMBOL SUFFIX")[0]
        self.assertIn("01", part, "INE000001A01 has 01 at characters 8-9")
        self.assertEqual(eligible._isin_parts({"isin": "ine002a01018"}), ("INE", "01"))

    def test_the_output_is_bounded_and_deterministic(self):
        rows = [row(i, instrument_type="T%d" % (i % 200), trading_symbol="S%d-%d" % (i, i % 90)) for i in range(4000)]
        res = eligible.classify(rows, collect=True)
        t1 = eligible.review(rows, res)
        t2 = eligible.review(list(reversed(rows)), eligible.classify(list(reversed(rows)), collect=True))
        self.assertLess(len(t1.splitlines()), 4000, "bounded however many reasons and values there are")
        self.assertEqual(len(t1.splitlines()), len(t2.splitlines()))
        flagged_a = [l for l in t1.splitlines() if l.startswith("   {")]
        flagged_b = [l for l in t2.splitlines() if l.startswith("   {")]
        self.assertEqual(flagged_a, flagged_b)

    def test_high_cardinality_fields_are_only_summarised(self):
        rows = [row(i) for i in range(400)]
        text = eligible.review(rows, eligible.classify(rows, collect=True))
        self.assertIn("high-cardinality, not tabulated", text)


class CountOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "NSE.json.gz")
        rows = [row(i) for i in range(20)] + [row(100, instrument_type="BE"), row(101, name="Gold ETF"), {"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "NIFTY 50"}]
        with open(self.path, "wb") as f:
            f.write(gzip.compress(json.dumps(rows).encode()))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        args = cli.parse_args(list(argv))
        rc = cli.count_only(args, out, err)
        return rc, out.getvalue(), err.getvalue()

    def test_prints_the_counts(self):
        rc, out, err = self.run_cli("--count-only", "--instruments-file", self.path)
        self.assertEqual(rc, 0, err)
        self.assertIn("ELIGIBLE EQUITIES: 21", out)
        self.assertIn("instrument_type:BE", out)
        self.assertIn("name_looks_like_fund_or_etf", out)
        self.assertIn("sha256", out)

    def test_it_writes_nothing_itself(self):
        before = sorted(os.listdir(self.tmp.name))
        rc, out, err = self.run_cli("--count-only", "--instruments-file", self.path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(os.listdir(self.tmp.name)), before)
        with self.assertRaises(SystemExit):
            cli.parse_args(["--count-only", "--report-file", "x.json"])

    def test_review_flag_prints_the_evidence(self):
        rc, out, err = self.run_cli("--count-only", "--review-flagged", "--instruments-file", self.path)
        self.assertEqual(rc, 0, err)
        self.assertIn("ELIGIBLE EQUITIES: 21", out)
        self.assertIn("REVIEW OF THE INSTRUMENT METADATA", out)
        self.assertIn("Gold ETF", out)

    def test_review_flag_alone_is_refused(self):
        self.assertEqual(cli.main(["--review-flagged"], env={}), 2)

    def test_it_never_downloads_a_file(self):
        rc, out, err = self.run_cli("--count-only")
        self.assertEqual(rc, 2)
        self.assertIn("never downloads", err)

    def test_a_bad_file_is_a_clean_error(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w") as f:
            f.write("{not json")
        rc, out, err = self.run_cli("--count-only", "--instruments-file", bad)
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err)
        rc, out, err = self.run_cli("--count-only", "--instruments-file", os.path.join(self.tmp.name, "missing.json"))
        self.assertEqual(rc, 2)

    def test_no_token_no_network_no_http_libraries(self):
        """A fresh interpreter with every network door shut and no token anywhere: --count-only must still work, and must not even import requests or websockets."""
        code = r'''
import builtins, socket, sys, os
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
rc = cli.main(["--count-only", "--review-flagged", "--instruments-file", sys.argv[1]], env=NoEnv())
sys.exit(rc)
'''
        env = {k: v for k, v in os.environ.items() if k != "UPSTOX_ANALYTICS_TOKEN"}
        p = subprocess.run([sys.executable, "-B", "-I", "-c", "import sys; sys.path.insert(0, %r)\n%s" % (str(ROOT), code), self.path], capture_output=True, text=True, env=env, cwd=self.tmp.name)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ELIGIBLE EQUITIES: 21", p.stdout)
        self.assertIn("REVIEW OF THE INSTRUMENT METADATA", p.stdout)

    def test_a_token_in_the_environment_is_ignored_and_never_printed(self):
        env = dict(os.environ, UPSTOX_ANALYTICS_TOKEN="SECRET-TOKEN-abc123XYZ")
        p = subprocess.run([sys.executable, "-B", "-m", "live_service", "--count-only", "--instruments-file", self.path], capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("SECRET-TOKEN", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
