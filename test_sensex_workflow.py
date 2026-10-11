"""Tests for .github/workflows/sensex_data.yml (the manual, non-publishing SENSEX pipeline) and validate_sensex_output.py. No network, no token.
Run: python3 -m unittest test_sensex_workflow -v

The workflow is checked as text/YAML AND by running its real `run:` commands with bash against stand-ins, using GitHub's own rule that a step after a failed step
is skipped (no step here may override that with if: / continue-on-error:).
"""
import copy
import datetime as dt
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
import validate_sensex_output as v

ROOT = Path(__file__).resolve().parent
WF_PATH = ROOT / ".github" / "workflows" / "sensex_data.yml"
FULL = WF_PATH.read_text(encoding="utf-8")
TEXT = "\n".join(l for l in FULL.splitlines() if not l.lstrip().startswith("#"))          # the YAML without its comment lines: the rules apply to what runs, not to what is explained
WF = yaml.safe_load(FULL)
STEPS = WF["jobs"]["update"]["steps"]
TODAY = dt.date(2026, 10, 7)
NOW = dt.datetime(2026, 10, 6, 21, 0, tzinfo=sx.mu.IST)


def step(name_part):
    found = [s for s in STEPS if name_part in (s.get("name") or "")]
    assert len(found) == 1, name_part
    return found[0]


def candles():
    out, d, i = [], dt.date(2026, 9, 28), 0
    while d <= dt.date(2026, 10, 6):
        if d.weekday() < 5:
            c = 81000.0 + 10 * i
            out.append({"date": d.isoformat(), "open": c, "high": c + 5, "low": c - 5, "close": c, "volume": None})
            i += 1
        d += dt.timedelta(days=1)
    return out


def fresh_doc():
    """A valid file whose newest candle is the latest weekday on or before the real today, so the validator's real clock accepts it."""
    end = dt.datetime.now(sx.mu.IST).date()
    while end.weekday() >= 5:
        end -= dt.timedelta(days=1)
    out, d, i = [], end - dt.timedelta(days=9), 0
    while d <= end:
        if d.weekday() < 5:
            c = 81000.0 + 10 * i
            out.append({"date": d.isoformat(), "open": c, "high": c + 5, "low": c - 5, "close": c, "volume": None})
            i += 1
        d += dt.timedelta(days=1)
    return sx.build_doc({"instrument_key": "BSE_INDEX|SENSEX", "instrument_name": "SENSEX"}, out, dt.datetime.now(sx.mu.IST), end.isoformat(), False)


def good(approved=False):
    return sx.build_doc({"instrument_key": "BSE_INDEX|SENSEX", "instrument_name": "SENSEX"}, candles(), NOW, "2026-10-06", approved)


class Triggers(unittest.TestCase):
    def test_dispatch_only_and_no_schedule_push_or_pull_request(self):
        self.assertEqual(WF["on"], {"workflow_dispatch": {}})                        # the key is quoted in the file, so it stays the text "on"
        for banned in ("schedule", "cron", "push:", "pull_request", "workflow_run", "workflow_call", "repository_dispatch"):
            self.assertNotIn(banned, TEXT, banned)

    def test_permissions_concurrency_timeout_and_runner(self):
        self.assertEqual(WF["permissions"], {"contents": "read"})
        self.assertEqual(WF["concurrency"], {"group": "stocklens-sensex", "cancel-in-progress": False})
        self.assertEqual(list(WF["jobs"]), ["update"])
        job = WF["jobs"]["update"]
        self.assertEqual(job["timeout-minutes"], 15)
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("permissions", job, "no job-level widening of the read-only permission")
        self.assertNotIn("needs", job)
        for other in ("stocklens-ledger", "stocklens-market", "stocklens-historical", "stocklens-sector"):
            self.assertNotEqual(WF["concurrency"]["group"], other)

    def test_python_311_and_only_requests_is_installed(self):
        sp_ = [s for s in STEPS if s.get("uses", "").startswith("actions/setup-python@")]
        self.assertEqual(len(sp_), 1)
        self.assertEqual(sp_[0]["with"], {"python-version": "3.11"})
        installs = [s["run"] for s in STEPS if "pip" in s.get("run", "")]
        self.assertEqual(installs, ["pip install requests"])


class NothingIsPublished(unittest.TestCase):
    def test_no_publishing_building_or_artifact_text_anywhere(self):
        for banned in ("upload-pages-artifact", "deploy-pages", "configure-pages", "upload-artifact", "download-artifact", "pages:", "id-token", "_site",
                       "cp ", "git push", "git commit", "git add", "contents: write", "gh api", "gh run", "curl", "wget", "environment:", "index.html", "company_profiles"):
            self.assertNotIn(banned, TEXT, banned)

    def test_no_step_can_run_after_a_failure_or_hide_one(self):
        for s in STEPS:
            for k in ("if", "continue-on-error"):
                self.assertNotIn(k, s, (s.get("name"), k))
        self.assertNotRegex(TEXT, r"always\(\)|failure\(\)|cancelled\(\)|\|\| *true|set \+e")
        self.assertNotIn("continue-on-error", TEXT)

    def test_only_the_expected_actions_are_used(self):
        used = sorted(s["uses"] for s in STEPS if "uses" in s)
        self.assertEqual(used, ["actions/cache/restore@v4", "actions/cache/save@v4", "actions/checkout@v4", "actions/setup-python@v5"])

    def test_the_commands_are_exactly_the_expected_ones_in_order(self):
        self.assertEqual([s["run"] for s in STEPS if "run" in s],
                         ["pip install requests", "python sensex_data_updater.py", "python validate_sensex_output.py out/sensex_history.json"])
        self.assertEqual([s.get("name") or s.get("uses") or s.get("run") for s in STEPS],
                         ["actions/checkout@v4", "actions/setup-python@v5", "pip install requests", "Restore the saved SENSEX history",
                          "Update SENSEX end-of-day data", "Validate the SENSEX file", "Save the SENSEX history"])

    def test_no_other_script_can_write_a_dataset(self):
        runs = " ".join(s.get("run", "") for s in STEPS)
        for other in ("historical_updater", "index_data_updater", "market_data_updater", "fundamentals_updater", "financials_updater", "nse_updater",
                      "sector_", "shareholding", "stock_directory", "universe"):
            self.assertNotIn(other, runs, other)


class SecretsAndGate(unittest.TestCase):
    def test_the_token_reaches_only_the_update_step(self):
        self.assertEqual(TEXT.count("secrets."), 1)
        for s in STEPS:
            has = "secrets." in json.dumps(s)
            self.assertEqual(has, s.get("name") == "Update SENSEX end-of-day data", s.get("name"))
        self.assertEqual(step("Update SENSEX")["env"]["UPSTOX_ANALYTICS_TOKEN"], "${{ secrets.UPSTOX_ANALYTICS_TOKEN }}")
        self.assertNotIn("env", WF["jobs"]["update"], "no job-level environment (it would reach every step)")
        self.assertNotIn("env", WF)

    def test_the_approval_flag_is_the_literal_string_false_on_the_update_step_only(self):
        self.assertEqual(step("Update SENSEX")["env"], {"UPSTOX_ANALYTICS_TOKEN": "${{ secrets.UPSTOX_ANALYTICS_TOKEN }}", sx.ENV_FLAG: "false"})
        self.assertIsInstance(step("Update SENSEX")["env"][sx.ENV_FLAG], str)
        self.assertEqual(TEXT.count(sx.ENV_FLAG + ": "), 1)
        self.assertNotIn(sx.ENV_FLAG + ': "true"', TEXT)
        self.assertNotIn(sx.ENV_FLAG + ": true", TEXT)
        self.assertNotRegex(TEXT, sx.ENV_FLAG + r": *\$\{\{")                                 # never taken from a variable or secret

    def test_every_gate_is_still_closed(self):
        self.assertIs(sx.PUBLIC_DISPLAY_DEFAULT, False)
        self.assertIs(sp.PUBLISH_DEFAULT, False)
        self.assertEqual((ROOT / "index.html").read_text(encoding="utf-8").count("var SENSEX_PAGE_APPROVED=false;"), 1)
        self.assertIn("DEFAULT_APPROVED = False", (ROOT / "sector_publish.py").read_text())
        self.assertIn('SECTOR_PUBLIC_DISPLAY_APPROVED: "false"', (ROOT / ".github" / "workflows" / "sector_data.yml").read_text())


class Cache(unittest.TestCase):
    def test_only_the_one_file_is_restored_and_saved_under_its_own_prefix(self):
        restore, save = step("Restore the saved"), step("Save the SENSEX")
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertEqual(restore["with"], {"path": "out/sensex_history.json", "key": "sensex-history-${{ github.run_id }}", "restore-keys": "sensex-history-"})
        self.assertEqual(save["with"], {"path": "out/sensex_history.json", "key": "sensex-history-${{ github.run_id }}"})
        self.assertEqual(len([s for s in STEPS if "cache" in s.get("uses", "")]), 2)

    def test_no_wider_path_and_no_shared_prefix(self):
        paths = re.findall(r"path:\s*(.+)", TEXT)
        self.assertEqual(sorted(paths), ["out/sensex_history.json", "out/sensex_history.json"])
        for bad in ("path: out\n", "path: data", "path: |", "nse-data-", "out/*", "out/historical", "path: .", "**"):
            self.assertNotIn(bad, TEXT, bad)
        self.assertEqual(sx.OUT_FILE.as_posix().split("out/")[-1], "sensex_history.json")

    def test_the_cache_is_saved_last_and_only_after_the_update_and_the_validation(self):
        names = [s.get("name") or s.get("uses") for s in STEPS]
        self.assertEqual(names[-1], "Save the SENSEX history")
        self.assertLess(names.index("Update SENSEX end-of-day data"), names.index("Validate the SENSEX file"))
        self.assertLess(names.index("Validate the SENSEX file"), names.index("Save the SENSEX history"))
        self.assertLess(names.index("Restore the saved SENSEX history"), names.index("Update SENSEX end-of-day data"))


class Behaviour(unittest.TestCase):
    """Runs the real run: commands in order with GitHub's rule that a failed step stops the job (no step here overrides it)."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        shutil.copy(ROOT / "validate_sensex_output.py", self.root / "validate_sensex_output.py")
        (self.root / "out").mkdir()

    def fake_updater(self, body):
        (self.root / "sensex_data_updater.py").write_text(body, encoding="utf-8")

    def run_job(self, restored=None, flag_seen=None):
        if restored is not None:
            (self.root / "out" / "sensex_history.json").write_text(json.dumps(restored), encoding="utf-8")
        executed = []
        env = dict(os.environ)
        env.pop("UPSTOX_ANALYTICS_TOKEN", None)
        for s in STEPS:
            if "run" in s:
                if s["run"].startswith("pip "):
                    continue
                script = s["run"].replace("python ", '"%s" ' % sys.executable)
                e = dict(env)
                e.update({k: str(x) for k, x in (s.get("env") or {}).items() if "secrets." not in str(x)})
                r = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=str(self.root), env=e, capture_output=True, text=True, timeout=120)
                executed.append((s["name"], r.returncode))
                if r.returncode != 0:
                    return executed, False, r
            elif s["uses"].startswith("actions/cache/save"):
                executed.append(("SAVE", 0))
        return executed, True, None

    def test_success_saves_and_a_good_file_validates(self):
        self.fake_updater("import json, pathlib\npathlib.Path('out').mkdir(exist_ok=True)\npathlib.Path('out/sensex_history.json').write_text(json.dumps(%r))\n" % fresh_doc())
        ex, ok, r = self.run_job()
        self.assertTrue(ok, r.stdout if r else "")
        self.assertEqual(ex, [("Update SENSEX end-of-day data", 0), ("Validate the SENSEX file", 0), ("SAVE", 0)])

    def test_a_failing_updater_stops_before_validation_and_before_the_save(self):
        self.fake_updater("import sys\nsys.exit(1)\n")
        ex, ok, r = self.run_job(restored=good())
        self.assertFalse(ok)
        self.assertEqual(ex, [("Update SENSEX end-of-day data", 1)])
        self.assertNotIn("SAVE", [n for n, _ in ex])

    def test_a_failing_validation_stops_before_the_save(self):
        bad = good()
        bad["kind"] = "something_else"
        self.fake_updater("import json, pathlib\npathlib.Path('out/sensex_history.json').write_text(json.dumps(%r))\n" % bad)
        ex, ok, r = self.run_job()
        self.assertFalse(ok)
        self.assertEqual(ex, [("Update SENSEX end-of-day data", 0), ("Validate the SENSEX file", 1)])
        self.assertNotIn("SAVE", [n for n, _ in ex])

    def test_an_updater_that_writes_nothing_fails_validation(self):
        self.fake_updater("pass\n")
        ex, ok, _ = self.run_job()
        self.assertFalse(ok)
        self.assertEqual(ex[-1], ("Validate the SENSEX file", 1))

    def test_the_update_step_sees_the_false_flag_and_no_token(self):
        self.fake_updater("import os, sys\nsys.exit(0 if os.environ.get('SENSEX_PUBLIC_DISPLAY_APPROVED') == 'false' and not os.environ.get('UPSTOX_ANALYTICS_TOKEN') else 3)\n")
        ex, ok, _ = self.run_job()
        self.assertEqual(ex[0], ("Update SENSEX end-of-day data", 0))                 # exit 3 would mean a wrong flag or a token leaked into the test run

    def test_the_validation_step_gets_no_secret_and_no_flag(self):
        self.assertNotIn("env", step("Validate the SENSEX"))
        self.assertNotIn("env", step("Restore"))
        self.assertNotIn("env", step("Save the SENSEX"))


class Validator(unittest.TestCase):
    def check(self, doc, today=TODAY):
        return v.problems(doc, today)

    def test_the_updaters_own_output_is_valid(self):
        self.assertEqual(self.check(good()), [])

    def test_constants_agree_with_the_updater(self):
        self.assertEqual((v.KIND, v.SCHEMA_VERSION, v.KEY, v.LABEL), (sx.KIND, sx.SCHEMA_VERSION, sx.VERIFIED_KEY, sx.LABEL))
        self.assertEqual(v.TOP_KEYS, set(good()))
        self.assertEqual(v.MAX_STALE_DAYS, sx.MAX_STALE_DAYS)
        self.assertEqual(v.DEFAULT_PATH.as_posix().split("/out/")[-1], "sensex_history.json")
        self.assertEqual(sx.OUT_FILE.name, "sensex_history.json")
        self.assertEqual(v.DEFAULT_PATH.parent.name, sx.OUT_FILE.parent.name)

    def test_an_approved_file_is_refused(self):
        for val in (True, "true", "false", 1, 0, None):
            d = good()
            d["public_display_approved"] = val
            self.assertTrue(any("public_display_approved" in m for m in self.check(d)), repr(val))
        d = good(approved=True)
        self.assertTrue(any("public_display_approved" in m for m in self.check(d)))
        d = good()
        del d["public_display_approved"]
        self.assertTrue(self.check(d))

    def test_wrong_identity_is_refused(self):
        def bad(f):
            d = good()
            f(d)
            return d
        cases = {
            "kind": bad(lambda d: d.update(kind="index_history")), "schema": bad(lambda d: d.update(schema_version=2)),
            "schema bool": bad(lambda d: d.update(schema_version=True)),
            "interval": bad(lambda d: d.update(interval="1min")), "freshness": bad(lambda d: d.update(freshness="LIVE")),
            "key": bad(lambda d: d["instrument"].update(instrument_key="BSE_INDEX|BSE500")),
            "key nse": bad(lambda d: d["instrument"].update(instrument_key="NSE_INDEX|Nifty 50")),
            "label": bad(lambda d: d["instrument"].update(label="NIFTY 50")), "exchange": bad(lambda d: d["instrument"].update(exchange="NSE")),
            "segment": bad(lambda d: d["instrument"].update(segment="NSE_INDEX")), "type": bad(lambda d: d["instrument"].update(instrument_type="EQ")),
            "no instrument": bad(lambda d: d.pop("instrument")), "instrument list": bad(lambda d: d.update(instrument=[])),
            "extra field": bad(lambda d: d.update(extra=1)), "missing field": bad(lambda d: d.pop("source")),
            "generated_at": bad(lambda d: d.update(generated_at="yesterday")),
        }
        for name, d in cases.items():
            self.assertTrue(self.check(d), name)

    def test_malformed_candles_are_refused(self):
        def bad(f):
            d = good()
            f(d["candles"])
            return d
        cases = {
            "empty": bad(lambda c: c.clear()), "not a dict": bad(lambda c: c.__setitem__(1, [1, 2])), "bad date": bad(lambda c: c[1].update(date="2026-02-30")),
            "date text": bad(lambda c: c[1].update(date="Oct 1")), "zero close": bad(lambda c: c[2].update(close=0)), "negative open": bad(lambda c: c[2].update(open=-1)),
            "text close": bad(lambda c: c[2].update(close="81000")), "bool close": bad(lambda c: c[2].update(close=True)),
            "nan": bad(lambda c: c[2].update(close=float("nan"))), "inf": bad(lambda c: c[2].update(high=float("inf"))),
            "high below low": bad(lambda c: c[2].update(high=1.0, low=5.0)), "zero volume": bad(lambda c: c[2].update(volume=0)),
            "float volume": bad(lambda c: c[2].update(volume=1.5)), "extra key": bad(lambda c: c[2].update(x=1)), "missing key": bad(lambda c: c[2].pop("low")),
            "duplicate": bad(lambda c: c.append(dict(c[-1]))), "descending": bad(lambda c: c.reverse()), "swapped": bad(lambda c: c.insert(0, c.pop())),
            "candles not a list": None,
        }
        for name, d in cases.items():
            if d is None:
                d = good()
                d["candles"] = {"a": 1}
            self.assertTrue(self.check(d), name)

    def test_stale_and_future_data_is_refused(self):
        self.assertEqual(self.check(good(), dt.date(2026, 10, 13)), [])                    # exactly 7 days after the last candle
        self.assertTrue(any("days old" in m for m in self.check(good(), dt.date(2026, 10, 14))))
        self.assertTrue(any("future" in m for m in self.check(good(), dt.date(2026, 10, 5))))

    def test_inconsistent_summary_fields_are_refused(self):
        def bad(f):
            d = good()
            f(d)
            return d
        cases = {
            "count": bad(lambda d: d.update(candle_count=3)), "count bool": bad(lambda d: d.update(candle_count=True)),
            "first_date": bad(lambda d: d.update(first_date="2026-09-01")), "last_date": bad(lambda d: d.update(last_date="2026-10-02")),
            "as_of": bad(lambda d: d.update(as_of="2026-10-02")),
            "latest date": bad(lambda d: d["latest"].update(date="2026-10-02")), "latest close": bad(lambda d: d["latest"].update(close=1.0)),
            "latest prev": bad(lambda d: d["latest"].update(prev_close=1.0)), "latest change": bad(lambda d: d["latest"].update(change_pct=9.99)),
            "latest fields": bad(lambda d: d["latest"].pop("change_pct")), "latest not dict": bad(lambda d: d.update(latest=5)),
            "freshness source": bad(lambda d: d["freshness_detail"].update(source_date="2026-10-02")),
        }
        for name, d in cases.items():
            self.assertTrue(self.check(d), name)

    def test_a_non_object_is_refused(self):
        for d in ([], "x", 5, None, [good()]):
            self.assertTrue(self.check(d))


class ValidatorCommand(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.dir = Path(self._t.name)

    def run_main(self, args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "validate_sensex_output.py")] + args, capture_output=True, text=True, timeout=60, cwd=str(self.dir))

    def test_a_missing_file_is_a_failure(self):
        r = self.run_main([str(self.dir / "nope.json")])
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not exist", r.stdout)

    def test_unreadable_oversized_and_nonstrict_files_fail(self):
        p = self.dir / "f.json"
        for text in ("", "{{{", "[1,2", '{"a": NaN}', '{"a": Infinity}', "﻿"):
            p.write_text(text, encoding="utf-8")
            self.assertEqual(self.run_main([str(p)]).returncode, 1, repr(text))
        p.write_bytes(b"\xff\xfe\x00bad")
        self.assertEqual(self.run_main([str(p)]).returncode, 1)
        p.write_text(" " * (v.MAX_BYTES + 1), encoding="utf-8")
        r = self.run_main([str(p)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("larger than the safety limit", r.stdout)

    def test_a_valid_file_passes_and_prints_no_prices_or_contents(self):
        d = good()
        p = self.dir / "ok.json"
        p.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(v.check_file(p, TODAY), [])
        real = v.check_file
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.object(v, "check_file", lambda path: real(path, TODAY)), contextlib.redirect_stdout(buf):
            code = v.main([str(p)])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue().strip(), "SENSEX output is valid.")
        p.write_text(json.dumps(fresh_doc()), encoding="utf-8")                    # and through the real command with the real clock
        r = self.run_main([str(p)])
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "SENSEX output is valid."), r.stdout + r.stderr)

    def test_failures_print_no_prices_credentials_or_contents(self):
        d = good()
        d["public_display_approved"] = True
        d["candles"][3]["close"] = 0
        d["secret_note"] = "SECRET-TOKEN-abc123"
        p = self.dir / "bad.json"
        p.write_text(json.dumps(d), encoding="utf-8")
        r = self.run_main([str(p)])
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        self.assertNotIn("SECRET-TOKEN-abc123", out)
        for c in d["candles"]:
            self.assertNotIn(str(int(c["close"])), out)
        self.assertNotRegex(out, r"\b8\d{4}(\.\d+)?\b")
        self.assertIn("public_display_approved", out)

    def test_bad_arguments_are_rejected(self):
        self.assertEqual(self.run_main(["a", "b"]).returncode, 2)

    def test_it_is_standard_library_only_and_read_only(self):
        code = re.sub(r'""".*?"""', "", (ROOT / "validate_sensex_output.py").read_text(), flags=re.S)
        self.assertEqual(sorted(re.findall(r"^(?:import|from) (\w+)", code, flags=re.M)), ["datetime", "json", "math", "pathlib", "re", "sys"])
        for needle in ("requests", "urllib", "socket", "http", "subprocess", "environ", "getenv", "token", "TOKEN", "write_text", "write_bytes", "open(", "unlink", "rename", "replace("):
            self.assertNotIn(needle, code, needle)


class Protection(unittest.TestCase):
    def test_exactly_one_workflow_runs_the_updater_and_the_publishing_workflows_do_not(self):
        runners = [p.name for p in (ROOT / ".github" / "workflows").glob("*.yml") if "sensex_data_updater" in p.read_text()]
        self.assertEqual(runners, ["sensex_data.yml"])
        for name in ("update.yml", "market_data.yml"):
            t = (ROOT / ".github" / "workflows" / name).read_text()
            self.assertNotIn("sensex_data_updater", t)
            self.assertNotIn("validate_sensex_output", t)
            self.assertNotIn("sensex_history", t)

    def test_the_publishing_workflows_still_carry_only_the_guard(self):
        for name in ("update.yml", "market_data.yml"):
            t = (ROOT / ".github" / "workflows" / name).read_text()
            self.assertEqual(t.count("python sensex_publish.py guard --site _site"), 1)

    def test_other_workflows_do_not_mention_sensex_at_all(self):
        for p in (ROOT / ".github" / "workflows").glob("*.yml"):
            if p.name not in ("update.yml", "market_data.yml", "sensex_data.yml"):
                self.assertNotIn("sensex", p.read_text().lower(), p.name)


if __name__ == "__main__":
    unittest.main()
