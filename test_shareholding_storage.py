"""
test_shareholding_storage.py - Phase 5H.3 tests for shareholding_ledger.py (source choice, strict loading, the no-loss guard, the command line)
and static checks of .github/workflows/shareholding.yml. Standard unittest, no network.
Run:  python3 test_shareholding_storage.py
"""
import contextlib
import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import shareholding_ledger as sl

ROOT = Path(__file__).resolve().parent
WF = ROOT / ".github" / "workflows" / "shareholding.yml"


def rec(q="2025-12-31", rid="1", **vals):
    v = {"promoters": 50.0, "fii": 15.0, "other_dii": 4.0, "mutual_funds": 6.0, "retail_other": 25.0}
    v.update(vals)
    return {"quarter_end": q, "status": "available", "values": v, "revisions": [],
            "source": {"provider": "NSE", "record_id": rid, "xbrl_url": "https://nsearchives.nseindia.com/x_%s.xml" % rid}}


def ledger(*recs, sym="ITC"):
    return {"schema": 1, "stocks": {sym: {"symbol": sym, "quarters": list(recs)}}}


class Source(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_no_branch_is_an_error(self):
        for b in ("", None):
            with self.assertRaises(sl.StorageError):
                sl.choose_source(b)

    def test_missing_file_is_an_error_unless_init(self):
        with self.assertRaises(sl.StorageError):
            sl.choose_source(self.d)
        self.assertEqual(sl.choose_source(self.d, True), (None, "init"))
        self.assertEqual(list(self.d.iterdir()), [])            # nothing is created

    def test_existing_file_is_the_source_and_init_never_replaces_it(self):
        (self.d / sl.LEDGER_NAME).write_text(json.dumps(ledger(rec())))
        self.assertEqual(sl.choose_source(self.d), (self.d / sl.LEDGER_NAME, "branch"))
        with self.assertRaises(sl.StorageError):
            sl.choose_source(self.d, True)

    def test_strict_load(self):
        p = self.d / "l.json"
        for text in ("", "not json", "[]", '{"schema": 2, "stocks": {}}', '{"schema": 1}', '{"schema": 1, "stocks": []}'):
            p.write_text(text)
            with self.assertRaises(sl.StorageError):
                sl.load_ledger(p)
        with self.assertRaises(sl.StorageError):
            sl.load_ledger(self.d / "missing.json")
        p.write_text(json.dumps(ledger(rec())))
        self.assertEqual(sl.load_ledger(p)["schema"], 1)


class NoLoss(unittest.TestCase):
    def test_identical_and_additions_pass(self):
        a = ledger(rec())
        self.assertEqual(sl.no_loss_problems(a, copy.deepcopy(a)), [])
        b = ledger(rec(), rec("2025-09-30", "2"))
        self.assertEqual(sl.no_loss_problems(a, b), [])
        c = copy.deepcopy(b)
        c["stocks"]["TCS"] = {"quarters": [rec(sym := "2025-12-31", "9")]}
        self.assertEqual(sl.no_loss_problems(a, c), [])

    def test_stock_or_quarter_removed(self):
        a = ledger(rec(), rec("2025-09-30", "2"))
        self.assertIn("ITC: stock removed", sl.no_loss_problems(a, {"schema": 1, "stocks": {}}))
        self.assertTrue(any("record removed" in x for x in sl.no_loss_problems(a, ledger(rec()))))
        self.assertEqual(sl.no_loss_problems({"schema": 1, "stocks": {"ITC": {"quarters": []}}}, {"schema": 1, "stocks": {}}), [])

    def test_available_never_becomes_unavailable(self):
        n = ledger(dict(rec(), status="unavailable"))
        self.assertTrue(any("became unavailable" in x for x in sl.no_loss_problems(ledger(rec()), n)))

    def test_unavailable_may_become_available(self):
        o = ledger(dict(rec(), status="unavailable", values={c: None for c in sl.CATEGORIES}))
        self.assertEqual(sl.no_loss_problems(o, ledger(rec())), [])

    def test_value_lost_or_changed_silently(self):
        a = ledger(rec())
        self.assertTrue(any("fii: a value was lost" in x for x in sl.no_loss_problems(a, ledger(rec(fii=None)))))
        self.assertTrue(any("fii changed without a revision" in x for x in sl.no_loss_problems(a, ledger(rec(fii=16.0)))))
        self.assertEqual(sl.no_loss_problems(a, ledger(rec(fii=15.004))), [])       # inside the tolerance

    def test_null_may_be_filled(self):
        self.assertEqual(sl.no_loss_problems(ledger(rec(fii=None)), ledger(rec(fii=15.0))), [])
        self.assertEqual(sl.no_loss_problems(ledger(rec(fii=None)), ledger(rec(fii=0.0))), [])

    def test_revision_recording_the_old_value_is_allowed(self):
        new = rec(fii=16.0, rid="2")
        new["revisions"] = [{"values": {"fii": 15.0}, "source": {"record_id": "1"}}]
        self.assertEqual(sl.no_loss_problems(ledger(rec()), ledger(new)), [])

    def test_revision_with_the_wrong_old_value_is_not_enough(self):
        new = rec(fii=16.0, rid="1")
        new["revisions"] = [{"values": {"fii": 12.0}, "source": {"record_id": "1"}}]
        self.assertTrue(sl.no_loss_problems(ledger(rec()), ledger(new)))

    def test_changed_record_id_needs_a_revision_for_the_old_one(self):
        self.assertTrue(any("record id changed" in x for x in sl.no_loss_problems(ledger(rec()), ledger(rec(rid="2")))))
        new = rec(rid="2")
        new["revisions"] = [{"values": dict(rec()["values"]), "source": {"record_id": "1"}}]
        self.assertEqual(sl.no_loss_problems(ledger(rec()), ledger(new)), [])

    def test_source_fields_are_never_removed(self):
        for f in ("record_id", "xbrl_url"):
            n = rec()
            n["source"].pop(f)
            self.assertTrue(any("source %s was removed" % f in x for x in sl.no_loss_problems(ledger(rec()), ledger(n))), f)

    def test_earlier_revisions_are_append_only(self):
        o = rec()
        o["revisions"] = [{"values": {"fii": 14.0}, "source": {"record_id": "0"}}]
        n = copy.deepcopy(o)
        n["revisions"] = []
        self.assertTrue(any("earlier revisions" in x for x in sl.no_loss_problems(ledger(o), ledger(n))))
        n["revisions"] = [{"values": {"fii": 13.0}, "source": {"record_id": "0"}}]
        self.assertTrue(sl.no_loss_problems(ledger(o), ledger(n)))
        n = copy.deepcopy(o)
        n["revisions"].append({"values": {"fii": 15.0}, "source": {"record_id": "1"}})
        self.assertEqual(sl.no_loss_problems(ledger(o), ledger(n)), [])

    def test_bad_shapes(self):
        self.assertTrue(sl.no_loss_problems({}, ledger(rec())))
        self.assertTrue(sl.no_loss_problems(ledger(rec()), {"schema": 1}))


class First(unittest.TestCase):
    def test_verify_first(self):
        self.assertEqual(sl.verify_first(ledger(rec())), [])
        self.assertTrue(sl.verify_first({"schema": 1, "stocks": {}}))
        self.assertTrue(sl.verify_first({"schema": 1, "stocks": {"ITC": {"quarters": []}}}))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def w(self, name, doc):
        p = self.d / name
        p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return str(p)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = sl.main(["x"] + list(args))
        return rc, out.getvalue()

    def test_verify(self):
        a, b = self.w("a.json", ledger(rec())), self.w("b.json", ledger(rec(), rec("2025-09-30", "2")))
        self.assertEqual(self.run_cli("verify", a, b), (0, "ledger check passed\n"))
        rc, text = self.run_cli("verify", b, a)
        self.assertEqual(rc, 1)
        self.assertIn("record removed", text)

    def test_unreadable_and_usage(self):
        self.assertEqual(self.run_cli("verify", self.w("a.json", "junk"), self.w("b.json", ledger(rec())))[0], 1)
        self.assertEqual(self.run_cli("bogus")[0], 2)
        self.assertEqual(self.run_cli("verify-first", self.w("e.json", {"schema": 1, "stocks": {}}))[0], 1)
        self.assertEqual(self.run_cli("verify-first", self.w("f.json", ledger(rec())))[0], 0)


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = WF.read_text()

    def test_manual_only_and_inputs(self):
        self.assertIn("workflow_dispatch:", self.t)
        self.assertNotIn("schedule:", self.t)
        for i in ("symbols:", "init_ledger:", "refresh_all:"):
            self.assertIn(i, self.t)

    def test_shares_the_ledger_concurrency_group_and_never_cancels(self):
        self.assertRegex(self.t, r"concurrency:\n  group: stocklens-ledger\n  cancel-in-progress: false")

    def test_least_privilege(self):
        self.assertRegex(self.t, re.compile(r"^permissions:\n  contents: read", re.M))
        self.assertEqual(self.t.count("contents: write"), 1)
        save = self.t[self.t.index("\n  save:"):]
        self.assertIn("contents: write", save)
        self.assertNotIn("UPSTOX_ANALYTICS_TOKEN", save)

    def test_token_only_as_a_secret_in_the_updater_step(self):
        self.assertEqual(self.t.count("secrets.UPSTOX_ANALYTICS_TOKEN"), 1)
        self.assertEqual(len(re.findall(r"^\s+UPSTOX_ANALYTICS_TOKEN:", self.t, re.M)), 1)
        self.assertIn("UPSTOX_ANALYTICS_TOKEN: ${{ secrets.UPSTOX_ANALYTICS_TOKEN }}", self.t)
        self.assertNotRegex(self.t, r"echo[^\n]*TOKEN")

    def test_data_branch_is_never_created_and_only_the_ledger_is_committed(self):
        self.assertNotIn("--orphan", self.t)
        self.assertNotIn("--force", self.t)
        self.assertNotIn("push -f", self.t)
        self.assertEqual(self.t.count("git push"), 1)
        self.assertIn("git push origin HEAD:stocklens-data", self.t)
        self.assertIn('!= "shareholding_ledger.json"', self.t)
        self.assertIn("python shareholding_ledger.py verify ledger-branch/shareholding_ledger.json ledger-new/shareholding_ledger.json", self.t)
        self.assertIn("python shareholding_ledger.py verify-first", self.t)
        self.assertIn("init_ledger must not replace", self.t)

    def test_cache_key_and_restore_prefix(self):
        self.assertIn("key: nse-data-shp-${{ github.run_id }}", self.t)
        self.assertIn("restore-keys: nse-data-", self.t)

    def test_uses_the_new_updater_only(self):
        self.assertIn("python shareholding_updater.py", self.t)
        self.assertNotIn("nse_updater", self.t)
        self.assertNotIn("save_ledger.yml", self.t)

    def test_existing_workflows_do_not_mention_the_new_ledger(self):
        for n in ("update.yml", "financial_history.yml", "save_ledger.yml", "historical.yml"):
            self.assertNotIn("shareholding", (ROOT / ".github" / "workflows" / n).read_text(), n)

    def test_workflow_parses_as_yaml_when_pyyaml_is_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        d = yaml.safe_load(self.t)
        self.assertEqual(set(d["jobs"]), {"shareholding", "save"})
        self.assertEqual(d["jobs"]["save"]["needs"], "shareholding")


if __name__ == "__main__":
    unittest.main()
