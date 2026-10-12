"""Tests for sensex_publish.py - the SENSEX publication guard of the site folder. No network, no token, no real clock. Run: python3 -m unittest test_sensex_publish -v

They check the module on its own AND the real "Build site folder" steps of update.yml and market_data.yml: the run block of each step is executed with bash
on a throw-away copy layout, so what is proved is what the workflow text really does.
"""
import contextlib
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import sensex_data_updater as sx
import sensex_publish as sp

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ["update.yml", "market_data.yml"]
NOW = dt.datetime(2026, 10, 6, 21, 0, tzinfo=sx.mu.IST)


def candles():
    out, d, i = [], dt.date(2026, 9, 28), 0
    while d <= dt.date(2026, 10, 6):
        if d.weekday() < 5:
            c = 81000.0 + 10 * i
            out.append({"date": d.isoformat(), "open": c, "high": c + 5, "low": c - 5, "close": c, "volume": None})
            i += 1
        d += dt.timedelta(days=1)
    return out


def good_doc(approved=True):
    return sx.build_doc({"instrument_key": "BSE_INDEX|SENSEX", "instrument_name": "SENSEX"}, candles(), NOW, "2026-10-06", approved)


def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")


def quiet(fn, *a, **k):
    lines = []
    code = fn(*a, out=lines.append, **k)
    return code, lines


class Env(unittest.TestCase):
    def test_default_and_every_non_true_value_is_not_approved(self):
        self.assertIs(sp.PUBLISH_DEFAULT, False)
        self.assertIs(sp.approved({}), False)
        for v in ["", " ", "false", "False", "0", "1", "yes", "on", "truee", "tru", "true false", "t", "null", None, 1, True]:
            self.assertIs(sp.approved({sp.ENV_FLAG: v}), False, repr(v))

    def test_only_the_text_true_approves(self):
        for v in ["true", "TRUE", "True", " true ", "true\n"]:
            self.assertIs(sp.approved({sp.ENV_FLAG: v}), True, repr(v))

    def test_it_reads_the_real_environment_when_none_is_given(self):
        with mock.patch.dict(os.environ, {sp.ENV_FLAG: "true"}):
            self.assertIs(sp.approved(), True)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(sp.ENV_FLAG, None)
            self.assertIs(sp.approved(), False)

    def test_the_constants_agree_with_the_updater(self):
        self.assertEqual(sp.ENV_FLAG, sx.ENV_FLAG)
        self.assertEqual((sp.KIND, sp.SCHEMA_VERSION, sp.KEY, sp.LABEL), (sx.KIND, sx.SCHEMA_VERSION, sx.VERIFIED_KEY, sx.LABEL))
        self.assertEqual(sp.ALLOWED_PATH, Path("out") / sx.OUT_FILE.name)
        self.assertIs(sx.PUBLIC_DISPLAY_DEFAULT, False)


class Site(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.site = Path(self._t.name) / "_site"
        (self.site / "out").mkdir(parents=True)
        self.others = {"index.html": "<html>page</html>", "out/historical.json": '{"benchmark":{"symbol":"NIFTY 50","candles":[1,2,3]}}',
                       "out/scans.json": "{}", "out/index_history.json": '{"kind":"index_history"}', "out/by_symbol/AAA.json": "{}",
                       "out/by_symbol/HDFCSENSEX.json": '{"symbol":"HDFCSENSEX"}', "out/company_profiles.json": "{}"}
        for rel, text in self.others.items():
            write(self.site / rel, text)
        self.sx = self.site / "out" / "sensex_history.json"

    def others_intact(self):
        for rel, text in self.others.items():
            self.assertEqual((self.site / rel).read_text(encoding="utf-8"), text, rel)

    def guard(self, env):
        return quiet(sp.guard, self.site, env)

    def test_no_sensex_file_is_always_fine(self):
        for env in ({}, {sp.ENV_FLAG: "true"}, {sp.ENV_FLAG: "false"}):
            code, lines = self.guard(env)
            self.assertEqual((code, lines), (0, []))
        self.others_intact()

    def test_not_approved_removes_the_file_whatever_it_says(self):
        for env in ({}, {sp.ENV_FLAG: "false"}, {sp.ENV_FLAG: ""}, {sp.ENV_FLAG: "yes"}, {sp.ENV_FLAG: "1"}):
            for doc in (good_doc(True), good_doc(False), "not json at all", {"kind": "x"}):
                write(self.sx, doc)
                code, lines = self.guard(env)
                self.assertEqual(code, 0, (env, lines))
                self.assertFalse(self.sx.exists(), (env, doc if isinstance(doc, str) else list(doc)[:3]))
                self.assertTrue(any("removed" in l for l in lines))
        self.others_intact()

    def test_not_approved_removes_sensex_files_wherever_they_are_and_whatever_they_are_called(self):
        write(self.site / "out" / "by_symbol" / "sensex_history.json", good_doc(True))
        write(self.site / "out" / "sensex_history.json.bak", good_doc(True))
        write(self.site / "SENSEX_History.JSON", good_doc(True))
        write(self.site / "out" / "renamed.dat", good_doc(True))                         # a SENSEX file under an innocent name is found by what it says
        write(self.site / "out" / "again.txt", '{"instrument": {"instrument_key": "BSE_INDEX|SENSEX"}}')
        write(self.site / "deep" / "er" / "sensex.json", "{}")
        link = self.site / "out" / "sensex_link.json"
        link.symlink_to(self.site / "index.html")
        code, lines = self.guard({})
        self.assertEqual(code, 0, lines)
        self.assertEqual(sp.find_sensex_files(self.site), [])
        self.assertTrue((self.site / "index.html").exists(), "a link to another file removes only the link")
        self.others_intact()

    def test_similar_names_of_real_stock_files_are_never_touched(self):
        for rel in ("out/by_symbol/SENSEXETF.json", "out/by_symbol/ABCSENSEX.json"):
            write(self.site / rel, '{"symbol": "X"}')
        self.assertEqual(self.guard({})[0], 0)
        for rel in ("out/by_symbol/HDFCSENSEX.json", "out/by_symbol/SENSEXETF.json", "out/by_symbol/ABCSENSEX.json"):
            self.assertTrue((self.site / rel).exists(), rel)
        self.others_intact()

    def test_approved_keeps_only_a_valid_approved_file_at_the_one_allowed_path(self):
        write(self.sx, good_doc(True))
        before = self.sx.read_bytes()
        code, lines = self.guard({sp.ENV_FLAG: "true"})
        self.assertEqual((code, lines), (0, []))
        self.assertEqual(self.sx.read_bytes(), before)
        self.others_intact()
        self.assertEqual(quiet(sp.check, self.site, {sp.ENV_FLAG: "true"}), (0, []))

    def test_approved_but_the_file_is_not_fit_is_removed_and_fails(self):
        def mutate(f):
            d = good_doc(True)
            f(d)
            return d
        bad = {
            "flag false": good_doc(False),
            "flag missing": mutate(lambda d: d.pop("public_display_approved")),
            "flag is the text true": mutate(lambda d: d.update(public_display_approved="true")),
            "flag is 1": mutate(lambda d: d.update(public_display_approved=1)),
            "wrong kind": mutate(lambda d: d.update(kind="index_history")),
            "wrong schema": mutate(lambda d: d.update(schema_version=2)),
            "wrong key": mutate(lambda d: d["instrument"].update(instrument_key="BSE_INDEX|BSE500")),
            "wrong label": mutate(lambda d: d["instrument"].update(label="NIFTY 50")),
            "no instrument": mutate(lambda d: d.pop("instrument")),
            "one candle": mutate(lambda d: d.update(candles=d["candles"][:1])),
            "no candles": mutate(lambda d: d.pop("candles")),
            "candles not a list": mutate(lambda d: d.update(candles={"a": 1})),
            "descending": mutate(lambda d: d.update(candles=list(reversed(d["candles"])))),
            "duplicate date": mutate(lambda d: d["candles"].append(dict(d["candles"][-1]))),
            "zero close": mutate(lambda d: d["candles"][2].update(close=0)),
            "text close": mutate(lambda d: d["candles"][2].update(close="81000")),
            "bool close": mutate(lambda d: d["candles"][2].update(close=True)),
            "bad date": mutate(lambda d: d["candles"][2].update(date="2026-13-45x")),
            "last_date mismatch": mutate(lambda d: d.update(last_date="2026-09-30")),
            "as_of mismatch": mutate(lambda d: d.update(as_of="2026-09-30")),
            "a list": [1, 2],
            "not json": "{{{",
            "empty": "",
            "json null": "null",
        }
        for name, doc in bad.items():
            write(self.sx, doc)
            code, lines = self.guard({sp.ENV_FLAG: "true"})
            self.assertEqual(code, 1, name)
            self.assertFalse(self.sx.exists(), name + ": the unfit file must be removed")
            self.assertTrue(any("::error::" in l for l in lines), name)
        self.others_intact()

    def test_approved_still_removes_a_second_copy_and_fails(self):
        write(self.sx, good_doc(True))
        write(self.site / "out" / "by_symbol" / "sensex_history.json", good_doc(True))
        code, lines = self.guard({sp.ENV_FLAG: "true"})
        self.assertEqual(code, 1, lines)
        self.assertTrue(self.sx.exists(), "the file at the allowed path was valid and stays")
        self.assertFalse((self.site / "out" / "by_symbol" / "sensex_history.json").exists())
        write(self.site / "sensex_history.json", good_doc(True))                         # the site root is not the allowed path either
        self.assertEqual(self.guard({sp.ENV_FLAG: "true"})[0], 1)
        self.assertFalse((self.site / "sensex_history.json").exists())

    def test_a_symlinked_directory_is_never_followed_and_nothing_outside_the_site_is_touched(self):
        outside = Path(self._t.name) / "outside"                                          # a folder next to the site, reachable only through links
        outside.mkdir()
        inner = outside / "sensex_history.json"
        write(inner, good_doc(True))
        keep = outside / "notes.json"
        write(keep, '{"keep": 1}')
        snapshot = {f.name: f.read_bytes() for f in outside.iterdir()}
        (self.site / "out" / "linked_dir").symlink_to(outside, target_is_directory=True)                 # an ordinary name: the link is left alone
        (self.site / "out" / "sensex_dir").symlink_to(outside, target_is_directory=True)                 # a SENSEX name: only the link itself may go
        self.assertEqual(sp.find_sensex_files(self.site), [self.site / "out" / "sensex_dir"], "the walk does not enter the linked folder")
        for env in ({}, {sp.ENV_FLAG: "false"}, {sp.ENV_FLAG: "true"}):
            code, lines = quiet(sp.guard, self.site, env)
            self.assertEqual(code, 1 if env.get(sp.ENV_FLAG) == "true" else 0, (env, lines))                  # approved: a SENSEX file anywhere but the allowed path fails the step
            self.assertEqual({f.name: f.read_bytes() for f in outside.iterdir()}, snapshot, "nothing outside the site folder was changed")
            self.assertTrue(inner.exists() and keep.exists())
            self.assertTrue((self.site / "out" / "linked_dir").is_symlink(), "a link with an ordinary name stays")
            self.assertFalse((self.site / "out" / "sensex_dir").is_symlink() or (self.site / "out" / "sensex_dir").exists(), "a SENSEX-named link is removed as a link")
            (self.site / "out" / "sensex_dir").symlink_to(outside, target_is_directory=True)
        self.assertEqual(quiet(sp.check, self.site, {})[0], 1, "check sees the SENSEX-named link")
        self.assertEqual({f.name: f.read_bytes() for f in outside.iterdir()}, snapshot)
        # a SENSEX file reachable only through an ordinary-named link is not inspected (documented limit: the build steps create no links)
        (self.site / "out" / "sensex_dir").unlink()
        self.assertEqual(sp.find_sensex_files(self.site), [])

    def test_a_link_at_the_allowed_path_is_not_accepted(self):
        real = self.site / "elsewhere.dat"
        write(real, good_doc(True))
        self.sx.symlink_to(real)
        code, _ = self.guard({sp.ENV_FLAG: "true"})
        self.assertEqual(code, 1)
        self.assertFalse(self.sx.exists() or self.sx.is_symlink())

    def test_check_is_read_only(self):
        write(self.sx, good_doc(True))
        before = self.sx.read_bytes()
        code, lines = quiet(sp.check, self.site, {})
        self.assertEqual(code, 1)
        self.assertTrue(any("sensex_history.json" in l for l in lines))
        self.assertEqual(self.sx.read_bytes(), before, "check never changes anything")
        write(self.sx, good_doc(False))
        self.assertEqual(quiet(sp.check, self.site, {sp.ENV_FLAG: "true"})[0], 1)
        self.sx.unlink()
        self.assertEqual(quiet(sp.check, self.site, {})[0], 0)


class FailSafe(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.site = Path(self._t.name) / "_site"
        (self.site / "out").mkdir(parents=True)
        self.sx = self.site / "out" / "sensex_history.json"
        write(self.sx, good_doc(True))

    def test_a_missing_site_folder_fails_in_both_modes(self):
        gone = Path(self._t.name) / "nope"
        for fn in (sp.guard, sp.check):
            for env in ({}, {sp.ENV_FLAG: "true"}):
                code, lines = quiet(fn, gone, env)
                self.assertEqual(code, 1)
                self.assertTrue(any("does not exist" in l for l in lines))

    def test_a_file_given_as_the_site_fails(self):
        self.assertEqual(quiet(sp.guard, self.sx, {})[0], 1)
        self.assertTrue(self.sx.exists(), "nothing was changed")

    def test_a_folder_that_cannot_be_read_fails_without_deleting_anything(self):
        def boom(*a, **k):
            raise PermissionError("no")
        with mock.patch.object(sp.os, "walk", boom):
            self.assertEqual(quiet(sp.guard, self.site, {})[0], 1)
            self.assertEqual(quiet(sp.check, self.site, {})[0], 1)
        self.assertTrue(self.sx.exists())

    def test_a_file_that_cannot_be_removed_fails(self):
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("no")):
            code, lines = quiet(sp.guard, self.site, {})
        self.assertEqual(code, 1)
        self.assertTrue(any("could not remove" in l for l in lines))
        self.assertTrue(self.sx.exists())

    def test_a_removal_that_silently_did_nothing_is_caught_by_the_recheck(self):
        with mock.patch.object(Path, "unlink", lambda self_, *a, **k: None):
            code, lines = quiet(sp.guard, self.site, {})
        self.assertEqual(code, 1)
        self.assertTrue(any("still in the site folder" in l for l in lines))

    def test_main_rejects_bad_arguments(self):
        for argv in ([], ["guard"], ["guard", "--site"], ["guard", "--site", ""], ["publish", "--site", str(self.site)], ["guard", "_site"],
                     ["guard", "--site", str(self.site), "extra"], ["--site", str(self.site), "guard"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(sp.main(argv), 2, argv)
            self.assertIn("usage", err.getvalue())
        self.assertTrue(self.sx.exists(), "bad arguments never change anything")

    def test_main_runs_the_two_modes(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(sp.ENV_FLAG, None)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sp.main(["check", "--site", str(self.site)]), 1)
                self.assertEqual(sp.main(["guard", "--site", str(self.site)]), 0)
        self.assertFalse(self.sx.exists())

    def test_a_non_utf8_or_huge_unrelated_file_does_not_break_the_scan(self):
        (self.site / "out" / "binary.json").write_bytes(b"\xff\xfe\x00garbage" * 2000)
        self.sx.unlink()
        self.assertEqual(quiet(sp.guard, self.site, {})[0], 0)
        self.assertTrue((self.site / "out" / "binary.json").exists())


def step_of(name):
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    steps = [s for s in wf["jobs"]["build"]["steps"] if s.get("name") == "Build site folder"]
    assert len(steps) == 1
    return steps[0]


class Workflows(unittest.TestCase):
    """The real build steps, run with bash in a throw-away working folder."""

    def lay_out(self, root, with_sensex, flag_doc=True):
        (root / "out").mkdir()
        files = {"index.html": "<html>page</html>", "company_profiles.json": '{"profiles":1}', "out/historical.json": '{"benchmark":{"symbol":"NIFTY 50"}}',
                 "out/scans.json": '{"scans":1}', "out/fiidii_history.json": "{}", "out/index_history.json": "{}", "out/by_symbol/AAA.json": '{"a":1}'}
        for rel, text in files.items():
            write(root / rel, text)
        if with_sensex:
            write(root / "out" / "sensex_history.json", good_doc(flag_doc))
        shutil.copy(ROOT / "sensex_publish.py", root / "sensex_publish.py")
        return files

    def run_step(self, name, root, flag):
        step = step_of(name)
        env = {k: str(v) for k, v in (step.get("env") or {}).items()}
        if flag is not None:
            env[sp.ENV_FLAG] = flag
        script = step["run"].replace("python sensex_publish.py", '"%s" sensex_publish.py' % sys.executable)
        e = dict(os.environ)
        e.pop(sp.ENV_FLAG, None)
        e.update(env)
        return subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=str(root), env=e, capture_output=True, text=True, timeout=120)

    def test_the_file_is_excluded_by_default_even_when_it_sits_in_out(self):
        for name in WORKFLOWS:
            with tempfile.TemporaryDirectory() as t:
                root = Path(t)
                files = self.lay_out(root, True)
                r = self.run_step(name, root, None)
                self.assertEqual(r.returncode, 0, name + r.stdout + r.stderr)
                self.assertFalse((root / "_site" / "out" / "sensex_history.json").exists(), name)
                self.assertTrue((root / "out" / "sensex_history.json").exists(), "the guard touches only the site folder, never out/")
                for rel in files:                                                          # everything else is copied exactly as before
                    site_rel = "_site/" + rel if rel == "index.html" else "_site/out/" + (rel[4:] if rel.startswith("out/") else rel)
                    self.assertEqual((root / site_rel).read_bytes(), (root / rel).read_bytes(), name + " " + rel)

    def test_the_file_is_excluded_when_the_variable_is_removed_or_garbled(self):
        for name in WORKFLOWS:
            for flag in (None, "", "false", "no", "tru"):
                with tempfile.TemporaryDirectory() as t:
                    root = Path(t)
                    self.lay_out(root, True)
                    step = step_of(name)
                    script = step["run"].replace("python sensex_publish.py", '"%s" sensex_publish.py' % sys.executable)
                    e = dict(os.environ)
                    e.pop(sp.ENV_FLAG, None)
                    if flag is not None:
                        e[sp.ENV_FLAG] = flag                                              # no workflow-level value at all, or a wrong one
                    r = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=str(root), env=e, capture_output=True, text=True, timeout=120)
                    self.assertEqual(r.returncode, 0, (name, flag, r.stdout + r.stderr))
                    self.assertFalse((root / "_site" / "out" / "sensex_history.json").exists(), (name, flag))

    def test_without_a_sensex_file_nothing_changes_and_the_step_succeeds(self):
        for name in WORKFLOWS:
            with tempfile.TemporaryDirectory() as t:
                root = Path(t)
                files = self.lay_out(root, False)
                r = self.run_step(name, root, None)
                self.assertEqual(r.returncode, 0, name + r.stdout + r.stderr)
                copied = sorted(p.relative_to(root / "_site").as_posix() for p in (root / "_site").rglob("*") if p.is_file())
                self.assertEqual(copied, sorted(["index.html", "out/historical.json", "out/scans.json", "out/fiidii_history.json", "out/index_history.json",
                                                 "out/by_symbol/AAA.json", "out/company_profiles.json"]), name)

    def test_the_file_can_only_get_in_through_the_explicit_approval(self):
        for name in WORKFLOWS:
            with tempfile.TemporaryDirectory() as t:                                       # approved by a deliberate edit of the variable AND a file that says so
                root = Path(t)
                self.lay_out(root, True, flag_doc=True)
                r = self.run_step(name, root, "true")
                self.assertEqual(r.returncode, 0, name + r.stdout + r.stderr)
                self.assertEqual((root / "_site" / "out" / "sensex_history.json").read_bytes(), (root / "out" / "sensex_history.json").read_bytes(), name)
            with tempfile.TemporaryDirectory() as t:                                       # approved by the variable, but the data file does not approve itself
                root = Path(t)
                self.lay_out(root, True, flag_doc=False)
                r = self.run_step(name, root, "true")
                self.assertNotEqual(r.returncode, 0, name)
                self.assertFalse((root / "_site" / "out" / "sensex_history.json").exists(), name)

    def test_a_broken_guard_fails_the_step_instead_of_publishing(self):
        for name in WORKFLOWS:
            with tempfile.TemporaryDirectory() as t:
                root = Path(t)
                self.lay_out(root, True)
                (root / "sensex_publish.py").unlink()                                      # the guard script is missing: bash -e stops the step
                r = self.run_step(name, root, None)
                self.assertNotEqual(r.returncode, 0, name)

    def test_the_guard_is_the_last_command_after_every_copy_and_the_variable_ships_false(self):
        for name in WORKFLOWS:
            step = step_of(name)
            self.assertEqual(step["env"], {sp.ENV_FLAG: "false"}, name)                   # the string "false", not a YAML boolean
            commands = [l.strip() for l in step["run"].splitlines() if l.strip() and not l.strip().startswith("#")]
            self.assertEqual(commands[-1], "python sensex_publish.py guard --site _site", name)
            self.assertEqual(sum("sensex_publish.py" in c for c in commands), 1, name)
            self.assertTrue(all("sensex" not in c.lower() for c in commands[:-1]), name)
            text = (ROOT / ".github" / "workflows" / name).read_text()
            build, after = text.split("- name: Build site folder")[1].split("- name: Upload Pages artifact")[0], text.split("- name: Upload Pages artifact")[1]
            self.assertIn("python sensex_publish.py guard --site _site", build)
            self.assertNotIn("sensex", after.lower(), name)
            self.assertLess(build.index("cp out/*.json _site/out/"), build.index("sensex_publish.py"))
            self.assertLess(build.index("cp company_profiles.json _site/out/"), build.index("sensex_publish.py"))
            self.assertIn("cp out/*.json _site/out/", build)                                # the existing copy command itself is unchanged

    def test_no_other_workflow_mentions_the_guard_or_runs_the_updater(self):
        for p in (ROOT / ".github" / "workflows").glob("*.yml"):
            t = p.read_text().lower()
            if p.name not in WORKFLOWS + ["sensex_data.yml"]:
                self.assertNotIn("sensex", t, p.name)
            if p.name != "sensex_data.yml":                                              # the manual, non-publishing pipeline is the one place that runs the updater (test_sensex_workflow.py)
                self.assertNotIn("sensex_data_updater", t, p.name)
                self.assertNotIn("sensex_history", t, p.name)


class Gates(unittest.TestCase):
    def test_every_gate_is_still_closed(self):
        self.assertIs(sp.PUBLISH_DEFAULT, False)
        self.assertIs(sx.PUBLIC_DISPLAY_DEFAULT, False)
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertEqual(page.count("var SENSEX_PAGE_APPROVED=false;"), 1)
        self.assertNotIn("SENSEX_PAGE_APPROVED=true", page)
        self.assertIn("DEFAULT_APPROVED = False", (ROOT / "sector_publish.py").read_text())
        self.assertIn('SECTOR_PUBLIC_DISPLAY_APPROVED: "false"', (ROOT / ".github" / "workflows" / "sector_data.yml").read_text())
        for name in WORKFLOWS:
            self.assertNotIn(sp.ENV_FLAG + ': "true"', (ROOT / ".github" / "workflows" / name).read_text(), name)

    def test_the_updater_writes_false_unless_its_variable_is_true_and_the_guard_then_refuses_it(self):
        self.assertIs(good_doc(approved=sx.public_display_approved({}))["public_display_approved"], False)
        with tempfile.TemporaryDirectory() as t:
            site = Path(t)
            write(site / "out" / "sensex_history.json", good_doc(approved=sx.public_display_approved({})))
            self.assertEqual(quiet(sp.guard, site, {sp.ENV_FLAG: "true"})[0], 1)           # an unapproved file never passes, even with the variable open
            self.assertFalse((site / "out" / "sensex_history.json").exists())

    def test_the_module_is_standard_library_only_and_never_touches_the_network(self):
        code = re.sub(r'""".*?"""', "", (ROOT / "sensex_publish.py").read_text(), flags=re.S)
        imports = sorted(re.findall(r"^(?:import|from) (\w+)", code, flags=re.M))
        self.assertEqual(imports, ["json", "math", "os", "pathlib", "re", "sys"])
        for needle in ("requests", "urllib", "socket", "http", "subprocess", "token", "TOKEN", "UPSTOX", "shutil"):
            self.assertNotIn(needle, code, needle)


if __name__ == "__main__":
    unittest.main()
