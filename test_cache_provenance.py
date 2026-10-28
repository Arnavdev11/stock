"""
Tests for cache_provenance.py and for the two steps it adds to .github/workflows/update.yml. Offline and synthetic: no network, no Actions, no provider, no real cache.
"""
import contextlib
import datetime as dt
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

import cache_provenance as P
import report_run_status

HERE = Path(__file__).resolve().parent
UPDATE = HERE / ".github" / "workflows" / "update.yml"
NOW = dt.datetime(2026, 10, 23, 10, 0, 5, 999, tzinfo=dt.timezone.utc)
HOSTILE = "HOSTILE COMPANY NAME INE999999990 S0001"


def env(**kw):
    e = {"GITHUB_REPOSITORY": "Arnavdev11/stock", "GITHUB_WORKFLOW": "Update data and publish site", "GITHUB_REF": "refs/heads/main",
         "GITHUB_WORKFLOW_REF": "Arnavdev11/stock/.github/workflows/update.yml@refs/heads/main", "GITHUB_RUN_ID": "1234567890", "GITHUB_RUN_ATTEMPT": "1",
         "GITHUB_EVENT_NAME": "schedule", "GITHUB_SHA": "a" * 40, "CP_DATA_STATUS": "complete", "CP_DATA_OK": "true"}
    e.update({"CP_OUTCOME_" + s.upper(): "success" for s in P.STEP_NAMES})
    e.update(kw)
    return e


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        for i, n in enumerate(P.STATE_FILES):
            (self.data / n).write_text(json.dumps({"stocks": {"X": {"company_name": HOSTILE, "sector": HOSTILE, "n": i}}}), encoding="utf-8")

    def run_cli(self, argv, e=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = P.main(["cache_provenance.py"] + argv, env() if e is None else e)
        return rc, out.getvalue()


class WriteMarker(Folder):
    def test_a_valid_marker_binds_run_attempt_commit_key_and_the_three_files(self):
        m = P.write_marker(self.data, env(), NOW)
        self.assertEqual(json.loads((self.data / P.MARKER_NAME).read_text()), m)
        self.assertEqual((m["schema"], m["kind"], m["repository"], m["workflow"], m["run_id"], m["run_attempt"], m["event"], m["ref"], m["sha"], m["cache_key"], m["paths"]),
                         (1, "cache_provenance", "Arnavdev11/stock", "Update data and publish site", "1234567890", 1, "schedule", "refs/heads/main", "a" * 40,
                          "nse-data-1234567890", ["data", "out"]))
        self.assertEqual(m["workflow_ref"], "Arnavdev11/stock/.github/workflows/update.yml@refs/heads/main")
        self.assertEqual(m["written_at"], "2026-10-23T10:00:05Z")
        self.assertEqual((m["data_status"], m["data_ok"]), ("complete", True))
        self.assertEqual(set(m["step_outcomes"]), set(P.STEP_NAMES))
        for n in P.STATE_FILES:
            b = (self.data / n).read_bytes()
            self.assertEqual(m["files"][n], {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()})

    def test_the_marker_holds_no_value_from_the_state_files(self):
        text = (self.data / P.MARKER_NAME).read_text() if P.write_marker(self.data, env(), NOW) else ""
        for bad in ("HOSTILE", "INE999999990", "S0001", "company_name", "sector", "stocks"):
            self.assertNotIn(bad, text)

    def test_the_file_is_deterministic_compact_and_ends_with_a_newline(self):
        P.write_marker(self.data, env(), NOW)
        a = (self.data / P.MARKER_NAME).read_bytes()
        P.write_marker(self.data, env(), NOW)
        self.assertEqual(a, (self.data / P.MARKER_NAME).read_bytes())
        self.assertTrue(a.endswith(b"}\n"))
        self.assertNotIn(b" ", a.replace(b"Update data and publish site", b""))

    def test_it_is_atomic_leaves_no_temp_file_and_replaces_an_existing_marker(self):
        (self.data / P.MARKER_NAME).write_text("old", encoding="utf-8")
        (self.data / (P.MARKER_NAME + ".tmp")).symlink_to(self.root / "elsewhere")
        P.write_marker(self.data, env(), NOW)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), sorted(list(P.STATE_FILES) + [P.MARKER_NAME]))
        self.assertEqual(json.loads((self.data / P.MARKER_NAME).read_text())["run_id"], "1234567890")
        self.assertFalse((self.root / "elsewhere").exists())

    def test_a_marker_symlink_is_replaced_not_followed(self):
        (self.root / "target.json").write_text("untouched", encoding="utf-8")
        (self.data / P.MARKER_NAME).symlink_to(self.root / "target.json")
        P.write_marker(self.data, env(), NOW)
        self.assertEqual((self.root / "target.json").read_text(), "untouched")
        self.assertFalse((self.data / P.MARKER_NAME).is_symlink())

    def test_a_missing_or_linked_state_file_is_recorded_as_absent(self):
        (self.data / P.STATE_FILES[0]).unlink()
        (self.data / P.STATE_FILES[1]).unlink()
        (self.data / P.STATE_FILES[1]).symlink_to(self.data / P.STATE_FILES[2])
        m = P.write_marker(self.data, env(), NOW)
        self.assertEqual((m["files"][P.STATE_FILES[0]], m["files"][P.STATE_FILES[1]]), (None, None))
        self.assertIsNotNone(m["files"][P.STATE_FILES[2]])

    def test_unknown_status_and_outcomes_are_recorded_honestly_not_as_success(self):
        m = P.build_marker(self.data, env(CP_DATA_STATUS="", CP_DATA_OK="", CP_OUTCOME_QUARTERLY="weird", CP_OUTCOME_MARKET="failure"), NOW)
        self.assertEqual((m["data_status"], m["data_ok"], m["step_outcomes"]["quarterly"], m["step_outcomes"]["market"]), ("unknown", False, "unknown", "failure"))
        m = P.build_marker(self.data, env(CP_DATA_STATUS="incomplete", CP_DATA_OK="true"), NOW)
        self.assertEqual((m["data_status"], m["data_ok"]), ("incomplete", True))
        e = env()
        del e["CP_OUTCOME_FUNDAMENTALS"]
        self.assertEqual(P.build_marker(self.data, e, NOW)["step_outcomes"]["fundamentals"], "unknown")

    def test_a_run_that_is_not_the_daily_workflow_is_refused(self):
        bad = [env(GITHUB_WORKFLOW="Fundamentals coverage batch"), env(GITHUB_WORKFLOW="update data and publish site"),
               env(GITHUB_WORKFLOW_REF="Arnavdev11/stock/.github/workflows/fundamentals_batch.yml@refs/heads/main"),
               env(GITHUB_WORKFLOW_REF="Arnavdev11/stock/.github/workflows/update.yml@refs/heads/other"),
               env(GITHUB_WORKFLOW_REF="Other/stock/.github/workflows/update.yml@refs/heads/main"), env(GITHUB_REF="refs/pull/1/merge"),
               env(GITHUB_REF="main"), env(GITHUB_EVENT_NAME="push"), env(GITHUB_EVENT_NAME="pull_request"), env(GITHUB_SHA="abc"), env(GITHUB_SHA="A" * 40),
               env(GITHUB_RUN_ID="12x"), env(GITHUB_RUN_ID=""), env(GITHUB_RUN_ATTEMPT="0"), env(GITHUB_RUN_ATTEMPT="x"), env(GITHUB_REPOSITORY="no-slash"),
               env(GITHUB_REPOSITORY="a/b c")]
        for e in bad:
            with self.subTest(e={k: v for k, v in e.items() if k.startswith("GITHUB") and v != env()[k]}):
                with self.assertRaises(P.Refused):
                    P.write_marker(self.data, e, NOW)
                self.assertFalse((self.data / P.MARKER_NAME).exists())
        for name in ("GITHUB_REPOSITORY", "GITHUB_WORKFLOW", "GITHUB_REF", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME", "GITHUB_SHA"):
            e = env()
            del e[name]
            with self.assertRaises(P.Refused):
                P.write_marker(self.data, e, NOW)

    def test_a_manual_run_is_accepted_and_a_later_attempt_is_recorded(self):
        m = P.build_marker(self.data, env(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_RUN_ATTEMPT="2"), NOW)
        self.assertEqual((m["event"], m["run_attempt"]), ("workflow_dispatch", 2))

    def test_the_marker_is_never_written_to_the_published_folder(self):
        out = self.root / "out"
        out.mkdir()
        with self.assertRaises(P.Refused) as cm:
            P.write_marker(out, env(), NOW)
        self.assertEqual(cm.exception.code, "data_dir_is_the_published_folder")
        self.assertEqual(list(out.iterdir()), [])
        with self.assertRaises(P.Refused):
            P.remove_marker(out)

    def test_a_folder_that_is_a_link_or_missing_is_refused(self):
        link = self.root / "link"
        link.symlink_to(self.data)
        for d in (link, self.root / "nothing"):
            with self.assertRaises(P.Refused):
                P.write_marker(d, env(), NOW)


class RemoveMarker(Folder):
    def test_it_removes_the_marker_a_temp_file_and_a_link_and_is_quiet_when_none_exists(self):
        P.remove_marker(self.data)
        P.write_marker(self.data, env(), NOW)
        (self.data / (P.MARKER_NAME + ".tmp")).write_text("x", encoding="utf-8")
        P.remove_marker(self.data)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), sorted(P.STATE_FILES))
        (self.root / "t").write_text("keep", encoding="utf-8")
        (self.data / P.MARKER_NAME).symlink_to(self.root / "t")
        P.remove_marker(self.data)
        self.assertFalse((self.data / P.MARKER_NAME).is_symlink())
        self.assertEqual((self.root / "t").read_text(), "keep")

    def test_a_folder_in_its_place_is_refused(self):
        (self.data / P.MARKER_NAME).mkdir()
        with self.assertRaises(P.Refused):
            P.remove_marker(self.data)


class StaleMarkerSequence(Folder):
    """The life of data/ across runs: a restore brings back the last run's marker."""

    def marker(self):
        p = self.data / P.MARKER_NAME
        return json.loads(p.read_text()) if p.exists() else None

    def test_an_old_marker_never_survives_into_the_next_runs_cache(self):
        P.write_marker(self.data, env(GITHUB_RUN_ID="100"), NOW)                    # run 100 saved its cache; run 101 restores it
        self.assertEqual(self.marker()["run_id"], "100")
        P.remove_marker(self.data)                                                  # step 1 of run 101, before any updater
        self.assertIsNone(self.marker())
        with self.assertRaises(P.Refused):                                          # the last step of run 101 fails: no marker at all, never run 100's
            P.write_marker(self.data, env(GITHUB_RUN_ID="101", GITHUB_SHA="zz"), NOW)
        self.assertIsNone(self.marker())
        P.write_marker(self.data, env(GITHUB_RUN_ID="101"), NOW)
        self.assertEqual(self.marker()["cache_key"], "nse-data-101")

    def test_if_the_removal_step_itself_failed_the_stale_marker_still_names_another_run(self):
        P.write_marker(self.data, env(GITHUB_RUN_ID="100"), NOW)
        self.assertNotEqual(self.marker()["cache_key"], "nse-data-101")             # what a check against key nse-data-101 would see


class CommandLine(Folder):
    def test_write_and_remove_print_only_fixed_lines(self):
        rc, out = self.run_cli(["write", str(self.data)])
        self.assertEqual((rc, out.strip()), (0, "cache_provenance: marker written"))
        rc, out = self.run_cli(["remove", str(self.data)])
        self.assertEqual((rc, out.strip()), (0, "cache_provenance: no earlier marker remains"))

    def test_refusals_print_a_code_and_no_value(self):
        for argv, e in ((["write", str(self.data)], env(GITHUB_SHA=HOSTILE)), (["write", str(self.root / "nothing")], env()), (["write", str(self.data)], {}),
                        (["remove", str(self.root / "nothing")], env())):
            rc, out = self.run_cli(argv, e)
            self.assertEqual(rc, 1)
            self.assertRegex(out.strip(), r"^cache_provenance: refused \([a-z_]+\)$")
            self.assertNotIn("HOSTILE", out)
        self.assertEqual(self.run_cli([])[0], 2)
        self.assertEqual(self.run_cli(["write"])[0], 2)


class UpdateWorkflowSteps(unittest.TestCase):
    def setUp(self):
        self.text = UPDATE.read_text(encoding="utf-8")
        self.doc = yaml.safe_load(self.text)
        self.steps = self.doc["jobs"]["build"]["steps"]
        self.names = [s.get("name") for s in self.steps]

    def step(self, name):
        return self.steps[self.names.index(name)]

    def test_the_marker_is_removed_before_any_updater_and_after_the_restore_and_its_recovery_checks(self):
        ids = [s.get("id") for s in self.steps]
        i_restore = self.names.index("Restore saved data")
        i_remove = self.names.index("Remove any restored provenance marker")
        self.assertEqual(i_remove, ids.index("nse") - 1)                # immediately before the first data step
        self.assertGreater(i_remove, i_restore)
        # the restore, the lookup, the state check and the recovery stay together and in order (other tests pin that); the removal comes after all of them
        for sid in ("restore", "lookup", "fstate"):
            self.assertLess(ids.index(sid), i_remove, sid)
        for updater in ("nse", "fundamentals", "financials", "financial_history", "shareholding", "quarterly"):
            self.assertLess(i_remove, ids.index(updater), updater)
        s = self.steps[i_remove]
        self.assertEqual((s["run"], s.get("continue-on-error")), ("python cache_provenance.py remove data", True))
        self.assertNotIn("if", s)
        self.assertEqual(self.names[i_restore + 1:i_restore + 4], [self.names[ids.index("lookup")], self.names[ids.index("fstate")], self.names[i_restore + 3]])

    def test_the_marker_is_written_by_the_last_step_of_the_job_after_the_data_step_report(self):
        self.assertEqual(self.names[-1], "Write the provenance marker (last step)")
        self.assertEqual(self.names[-2], "Report the data steps")
        s = self.steps[-1]
        self.assertEqual((s["run"], s.get("continue-on-error"), s.get("id")), ("python cache_provenance.py write data", True, "provenance"))
        self.assertNotIn("if", s)                                       # the default success(): after a failed job nothing is saved, so no marker is needed

    def test_nothing_modifies_data_after_the_marker_and_the_step_is_after_every_updater(self):
        ids = [s.get("id") for s in self.steps]
        for i in ("nse", "fundamentals", "financials", "financial_history", "shareholding", "quarterly", "validate", "market", "datastatus"):
            self.assertLess(ids.index(i), len(self.steps) - 1)

    def test_it_records_the_run_report_and_every_step_the_report_judges(self):
        env_ = self.steps[-1]["env"]
        self.assertEqual(env_["CP_DATA_STATUS"], "${{ steps.datastatus.outputs.data_status }}")
        self.assertEqual(env_["CP_DATA_OK"], "${{ steps.datastatus.outputs.data_ok }}")
        for s in P.STEP_NAMES:
            self.assertEqual(env_["CP_OUTCOME_" + s.upper()], "${{ steps.%s.outcome }}" % s)
        self.assertEqual(len(env_), 2 + len(P.STEP_NAMES))
        self.assertEqual([s[0] for s in report_run_status.STEPS], list(P.STEP_NAMES))
        for s in P.STEP_NAMES:
            self.assertIn(s, [x.get("id") for x in self.steps])

    def test_no_permission_changes_and_the_cache_step_is_untouched(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read", "actions": "read"})
        self.assertNotIn("permissions", self.doc["jobs"]["build"])
        r = self.step("Restore saved data")
        self.assertEqual((r["uses"], r["with"]["key"], r["with"]["restore-keys"], [p for p in r["with"]["path"].split() if p]),
                         ("actions/cache@v4", "nse-data-${{ github.run_id }}", "nse-data-", ["data", "out"]))
        self.assertEqual(len(self.steps), 30)                           # the 26 steps there were, these two, and the two coverage-report steps
        self.assertEqual(self.doc["concurrency"], {"group": "stocklens-ledger", "cancel-in-progress": False})

    def test_the_site_build_never_copies_from_data_and_never_names_the_marker(self):
        site = self.step("Build site folder")["run"]
        self.assertNotIn("cache_provenance", site)
        sources = [m.group(2) for m in re.finditer(r"\bcp (-r )?(\S+)", "\n".join(l for l in site.splitlines() if not l.lstrip().startswith("#")))]
        self.assertTrue(sources)
        for src in sources:
            self.assertTrue(src.startswith(("out/", "index.html", "ledger-branch/", "company_profiles.json")), src)
        for banned in ("data/", " data ", "cp -r data"):
            self.assertNotIn(banned, "\n".join(l for l in site.splitlines() if not l.lstrip().startswith("#")))
        self.assertNotIn("cache_provenance", yaml.safe_dump(self.step("Upload Pages artifact")))

    def test_the_new_steps_call_no_provider_and_use_no_secret(self):
        for name in ("Remove any restored provenance marker", "Write the provenance marker (last step)"):
            self.assertNotIn("secrets.", yaml.safe_dump(self.step(name)))
            self.assertNotIn("UPSTOX", yaml.safe_dump(self.step(name)))

    def test_the_helper_is_stdlib_only_and_offline(self):
        import ast
        tree = ast.parse((HERE / "cache_provenance.py").read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names.add((n.module or "").split(".")[0])
        self.assertEqual(names, {"datetime", "hashlib", "json", "os", "re", "sys", "pathlib"})


if __name__ == "__main__":
    unittest.main()
