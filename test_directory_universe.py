"""Tests for the directory-based coverage universe (universe.py --mode directory) and the universe workflow's confirmation gate.  Run: python3 -m unittest test_directory_universe"""
import contextlib
import copy
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import quarterly_results_updater as q
import stock_directory
import test_stock_directory as T
import universe

HERE = Path(__file__).resolve().parent
WF = (HERE / ".github" / "workflows" / "universe.yml").read_text(encoding="utf-8")


def directory_doc():
    return T.Fx().build().doc


def cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = universe.main(list(argv))
    return rc, out.getvalue()


class BuildTests(unittest.TestCase):
    def test_every_directory_stock_becomes_a_universe_entry_with_the_directory_identity(self):
        d = directory_doc()
        u = universe.build_from_directory(d, "2026-10-14", "src")
        self.assertEqual(universe.check_doc(u), [])
        self.assertEqual((u["stage"], u["kind"], u["count"]), ("directory", "universe", len(d["symbols"])))
        self.assertEqual([x["symbol"] for x in u["symbols"]], sorted(x["symbol"] for x in d["symbols"]))
        by = {x["symbol"]: x for x in d["symbols"]}
        for x in u["symbols"]:
            self.assertEqual(set(x), {"symbol", "isin", "name"})                       # no sector, price, rating or series
            self.assertEqual((x["isin"], x["name"]), (by[x["symbol"]]["isin"], by[x["symbol"]]["name"]))

    def test_the_universe_has_the_same_file_shape_as_the_other_stages(self):
        d = directory_doc()
        self.assertEqual(set(universe.build_from_directory(d, "d", "s")), set(universe.build_doc([], {}, "d", "s", "nifty500")[0]))

    def test_a_damaged_directory_never_becomes_a_universe(self):
        d = directory_doc()
        for mutate in (lambda x: x["symbols"].pop(), lambda x: x["symbols"][0].update(isin="INE000X01011"), lambda x: x.update(directory_sha256="0" * 64),
                       lambda x: x.update(kind="other"), lambda x: x.pop("symbols")):
            bad = copy.deepcopy(d)
            try:
                mutate(bad)
            except Exception:
                pass
            with self.assertRaises(universe.UniverseError):
                universe.build_from_directory(bad, "d", "s")
        with self.assertRaises(universe.UniverseError):
            universe.build_from_directory("not a directory", "d", "s")

    def test_the_same_identity_is_seen_by_the_universe_updaters_and_quarterly_results(self):
        d = directory_doc()
        with tempfile.TemporaryDirectory() as t:
            dp, up = Path(t) / "stock_directory.json", Path(t) / "universe.json"
            dp.write_text(stock_directory.dumps(d), encoding="utf-8")
            self.assertEqual(cli("build", "--mode", "directory", "--directory", str(dp), "--out", str(up), "--as-of", "2026-10-14")[0], 1)     # fixture is below the minimum size
            with mock.patch.object(universe, "MIN_DIRECTORY", 1):
                rc, out = cli("build", "--mode", "directory", "--directory", str(dp), "--out", str(up), "--as-of", "2026-10-14")
            self.assertEqual(rc, 0, out)
            env = {"STOCKLENS_UNIVERSE_FILE": str(up)}
            entries = universe.load_entries(env)
            self.assertEqual(sorted(universe.load(env)), sorted(entries))                  # every updater that calls universe.load() sees the same symbols
            self.assertEqual({s: (e["isin"], e["name"]) for s, e in entries.items()}, {s: (e["isin"], e["name"]) for s, e in q.load_entries(dp).items()})   # and the quarterly/search identity
            self.assertEqual(universe.describe(env)["stage"], "directory")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.dir, self.out = Path(self.t.name) / "stock_directory.json", Path(self.t.name) / "universe.json"
        self.dir.write_text(stock_directory.dumps(directory_doc()), encoding="utf-8")
        p = mock.patch.object(universe, "MIN_DIRECTORY", 1)
        p.start()
        self.addCleanup(p.stop)

    def build(self):
        return cli("build", "--mode", "directory", "--directory", str(self.dir), "--out", str(self.out), "--as-of", "2026-10-14")

    def test_writes_a_valid_file_and_check_accepts_it(self):
        rc, out = self.build()
        self.assertEqual(rc, 0, out)
        self.assertIn("(directory)", out)
        self.assertEqual(cli("check", str(self.out))[0], 0)
        self.assertEqual(sorted(x.name for x in Path(self.t.name).iterdir()), ["stock_directory.json", "universe.json"])         # no temp file left behind

    def test_it_needs_no_download_and_no_instrument_file(self):
        with mock.patch.object(universe, "_fetch_instruments", side_effect=AssertionError("download")), mock.patch.object(universe, "_fetch_csv", side_effect=AssertionError("download")):
            self.assertEqual(self.build()[0], 0)

    def test_a_missing_or_broken_directory_writes_nothing(self):
        self.dir.write_text("{}", encoding="utf-8")
        rc, out = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        self.assertFalse(self.out.exists())
        self.assertEqual(cli("build", "--mode", "directory", "--out", str(self.out))[0], 1)       # --directory is required
        self.assertFalse(self.out.exists())

    def test_a_directory_below_the_minimum_size_is_refused(self):
        with mock.patch.object(universe, "MIN_DIRECTORY", 10 ** 6):
            rc, out = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("only", out)
        self.assertFalse(self.out.exists())

    def test_a_collapse_against_the_previous_universe_is_refused_and_the_old_file_is_kept(self):
        self.assertEqual(self.build()[0], 0)
        old = json.loads(self.out.read_text(encoding="utf-8"))
        big = copy.deepcopy(old)
        extra = [dict(x, symbol="Z%d" % i, isin="INE%03dZ01011" % i) for i, x in enumerate(old["symbols"][:1] * 30)]
        big["symbols"] += extra
        big["count"] = len(big["symbols"])
        self.out.write_text(json.dumps(big), encoding="utf-8")
        rc, out = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("vanished", out)
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["count"], big["count"])

    def test_the_other_modes_still_work_and_all_eq_still_needs_its_inputs(self):
        self.assertEqual(cli("build", "--mode", "bogus", "--instruments", "x", "--out", str(self.out))[0], 1)
        self.assertEqual(cli("build", "--mode", "all_eq", "--out", str(self.out))[0], 1)


class WorkflowGateTests(unittest.TestCase):
    def test_directory_mode_needs_an_explicit_yes_and_all_eq_stays_refused(self):
        self.assertIn("confirm_full_universe", WF)
        gate = WF[WF.index("- name: Refuse anything but nifty500"):WF.index("- name: Look for the data branch")]
        self.assertIn('[ "$MODE" = "nifty500" ]', gate)
        self.assertIn('[ "$MODE" = "directory" ] && [ "$CONFIRM" = "yes" ]', gate)
        self.assertIn("exit 1", gate)
        self.assertNotIn("all_eq\" ]", gate)
        self.assertNotIn("all_eq' ]", gate)

    def test_the_workflow_is_still_manual_secret_free_and_commits_exactly_one_file(self):
        for banned in ("schedule", "push:", "pull_request", "deploy-pages", "UPSTOX_ANALYTICS_TOKEN", "secrets.", "upload-pages-artifact", "actions/cache"):
            self.assertNotIn(banned, WF, banned)
        self.assertIn('!= "universe.json"', WF)
        self.assertNotRegex(WF, r"git push (-f|--force)")
        self.assertIn("--directory ledger-branch/stock_directory.json", WF)

    def test_inputs_reach_the_shell_only_through_environment_variables(self):
        for m in re.finditer(r"\$\{\{\s*inputs\.[^}]*\}\}", WF):
            line = WF[WF.rfind("\n", 0, m.start()) + 1:WF.index("\n", m.end())]
            self.assertRegex(line, r"^\s+(MODE|CONFIRM): ", line)

    def test_the_default_is_still_nifty500(self):
        self.assertRegex(WF, r'mode:\n\s+description:.*\n\s+required: false\n\s+default: "nifty500"')


if __name__ == "__main__":
    unittest.main()
