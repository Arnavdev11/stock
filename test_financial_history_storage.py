"""Phase 4 Step 4D tests: durable ledger storage on the `stocklens-data` branch. Mocks only; no token, no network, no git.
Run: python3 test_financial_history_storage.py"""
import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import financial_history_updater as fh                       # noqa: E402
import ledger_storage as ls                                   # noqa: E402
from test_financial_history import (CF4, FakeApi, TODAY, YEARS4, cash_body, income_body, rec, std_lines, stock, up_cash, up_income)   # noqa: E402

NAME = "financial_history_ledger.json"
YEARS_NEW = ["Mar_2027", "Mar_2026", "Mar_2025", "Mar_2024"]          # Upstox's window has moved on: FY2023 is no longer returned
CF_NEW = {"ocf": {y: 100.0 + i for i, y in enumerate(YEARS_NEW)}, "icf": {y: -50.0 - i for i, y in enumerate(YEARS_NEW)}, "fin": {y: -40.0 - i for i, y in enumerate(YEARS_NEW)}}


def real_official():
    return json.loads((ROOT / "official_financial_records.json").read_text(encoding="utf-8"))


def old_ledger():
    """TCS with Upstox FY2023-FY2026 plus the official FY2022 record, as the ledger holds it today."""
    s = stock("TCS"); up_income(s, YEARS4); up_cash(s)
    off = [r for r in real_official()["records"] if r["symbol"] == "TCS"][0]
    fh.apply_official(s, off, TODAY)
    return {"schema": 1, "stocks": {"TCS": s}}


def write(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj), encoding="utf-8")


def resp(years, base=1000.0, cf=None):
    return {("income-statement", "consolidated"): (income_body(std_lines(years, base=base)), None),
            ("cash-flow", "consolidated"): (cash_body(**(cf or CF_NEW)), None)}


class Run:
    """Runs fh.main() against temporary files with the network patched out. Returns exit code, and keeps the paths."""

    def __init__(self, branch_ledger=None, legacy_ledger=None, legacy_raw=None, branch_dir_set=None, responses=None, day="2026-12-01", symbols="TCS", patch=None):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.branch_dir, self.legacy, self.out_ledger, self.out_doc = t / "ledger-branch", t / "data" / NAME, t / "ledger-out" / NAME, t / "out" / "financial_history.json"
        if branch_ledger is not None:
            write(self.branch_dir / NAME, branch_ledger)
        if legacy_ledger is not None:
            write(self.legacy, legacy_ledger)
        if legacy_raw is not None:
            self.legacy.parent.mkdir(parents=True, exist_ok=True); self.legacy.write_text(legacy_raw, encoding="utf-8")
        env = {"UPSTOX_ANALYTICS_TOKEN": "TOKEN-xyz-SECRET-123", "HISTORY_SYMBOLS": symbols, "FINANCIAL_HISTORY_LEDGER_OUT": str(self.out_ledger),
               "FINANCIAL_HISTORY_LEDGER_DIR": str(self.branch_dir) if (branch_dir_set if branch_dir_set is not None else branch_ledger is not None) else ""}
        saved = {k: getattr(fh, k) for k in ("LEDGER_FILE", "OUT_FILE", "FINANCIALS_FILE", "FUNDAMENTALS_FILE", "OFFICIAL_FILE", "Upstox", "load_instruments", "today", "process_stock")}
        old_env = {k: os.environ.get(k) for k in list(env) + ["DATA_DIR"]}
        fh.LEDGER_FILE, fh.OUT_FILE, fh.FINANCIALS_FILE, fh.FUNDAMENTALS_FILE = self.legacy, self.out_doc, t / "out" / "financials.json", t / "out" / "fundamentals.json"
        fh.OFFICIAL_FILE = ROOT / "official_financial_records.json"
        fh.Upstox = lambda token, max_calls: FakeApi(responses or resp(YEARS_NEW), max_calls)
        fh.load_instruments = lambda: {"TCS": {"isin": "INE000A00000", "name": "TCS Ltd"}}
        fh.today = lambda: day
        os.environ.update(env)
        if patch:
            patch(fh)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                fh.main()
            self.code = 0
        except SystemExit as e:
            self.code = e.code if e.code is not None else 0
        finally:
            for k, v in saved.items():
                setattr(fh, k, v)
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.stdout = out.getvalue()

    def ledger(self):
        return json.loads(self.out_ledger.read_text(encoding="utf-8"))

    def close(self):
        self.tmp.cleanup()


# ---------------------------------------------------------------- 1-3. where the ledger is read from
class SourceTests(unittest.TestCase):
    def test_1_the_data_branch_ledger_is_the_source_of_truth(self):
        r = Run(branch_ledger=old_ledger(), legacy_ledger={"schema": 1, "stocks": {}})                 # a different, empty cached copy must be ignored
        self.addCleanup(r.close)
        self.assertEqual(r.code, 0, r.stdout)
        self.assertIn(2023, [y["fy"] for y in r.ledger()["stocks"]["TCS"]["years"]])                     # came from the branch copy
        self.assertEqual(ls.choose_source(str(r.branch_dir), r.legacy), (r.branch_dir / NAME, "branch"))

    def test_1_the_checked_out_branch_file_is_never_written_in_place(self):
        r = Run(branch_ledger=old_ledger())
        self.addCleanup(r.close)
        before = json.dumps(old_ledger(), sort_keys=True)
        self.assertEqual(json.dumps(json.loads((r.branch_dir / NAME).read_text()), sort_keys=True), before)
        self.assertTrue(r.out_ledger.exists())                                                          # the update goes to the hand-off file only

    def test_2_legacy_cache_is_used_when_there_is_no_data_branch(self):
        r = Run(legacy_ledger=old_ledger(), branch_dir_set=False)
        self.addCleanup(r.close)
        self.assertEqual(r.code, 0, r.stdout)
        self.assertEqual(ls.choose_source("", r.legacy), (r.legacy, "legacy-cache"))
        self.assertEqual(ls.choose_source(None, r.legacy), (r.legacy, "legacy-cache"))
        self.assertIn(2022, [y["fy"] for y in r.ledger()["stocks"]["TCS"]["years"]])

    def test_3_neither_source_fails_and_creates_nothing(self):
        r = Run(branch_dir_set=False)
        self.addCleanup(r.close)
        self.assertEqual(r.code, 1)
        self.assertFalse(r.out_ledger.exists()); self.assertFalse(r.legacy.exists()); self.assertFalse(r.out_doc.exists())
        self.assertNotIn("TOKEN-xyz-SECRET", r.stdout)
        with self.assertRaises(ls.StorageError):
            ls.choose_source("", Path(r.tmp.name) / "nowhere" / NAME)

    def test_3_a_branch_without_a_ledger_never_falls_back_to_the_cache(self):
        r = Run(legacy_ledger=old_ledger(), branch_dir_set=True)                                        # branch exists, but holds no ledger file
        self.addCleanup(r.close)
        self.assertEqual(r.code, 1); self.assertFalse(r.out_ledger.exists()); self.assertFalse(r.out_doc.exists())

    def test_3_an_unreadable_ledger_is_never_replaced_by_an_empty_one(self):
        for bad in ("not json", "[]", json.dumps({"schema": 2, "stocks": {}}), json.dumps({"schema": 1}), ""):
            r = Run(branch_dir_set=False, legacy_raw=bad)
            self.addCleanup(r.close)
            self.assertEqual(r.code, 1, bad)
            self.assertFalse(r.out_ledger.exists(), bad); self.assertFalse(r.out_doc.exists(), bad)
            self.assertEqual(r.legacy.read_text(encoding="utf-8"), bad)                                  # and the bad file itself is left alone
            with self.assertRaises(ls.StorageError):
                ls.load_ledger(r.legacy)


# ---------------------------------------------------------------- 4. FY2023 is kept
class PreservationTests(unittest.TestCase):
    def test_4_fy2023_is_kept_when_upstox_no_longer_returns_it(self):
        old = old_ledger()
        r = Run(branch_ledger=old)
        self.addCleanup(r.close)
        self.assertEqual(r.code, 0, r.stdout)
        new = r.ledger()["stocks"]["TCS"]
        self.assertEqual([y["fy"] for y in new["years"]], [2027, 2026, 2025, 2024, 2023, 2022])          # six years now; nothing dropped
        before = [y for y in old["stocks"]["TCS"]["years"] if y["fy"] == 2023][0]
        after = [y for y in new["years"] if y["fy"] == 2023][0]
        self.assertEqual(after, before)                                                                  # the FY2023 record is untouched
        self.assertEqual(ls.no_loss_problems(old, r.ledger()), [])

    def test_4_the_published_output_still_lists_every_year(self):
        r = Run(branch_ledger=old_ledger())
        self.addCleanup(r.close)
        doc = json.loads(r.out_doc.read_text(encoding="utf-8"))
        self.assertEqual([y["fy"] for y in doc["stocks"]["TCS"]["years"]], [2027, 2026, 2025, 2024, 2023, 2022])
        self.assertEqual(fh.validate_doc(doc), [])

    def test_7_official_tcs_and_itc_fy2022_records_survive(self):
        old = old_ledger()
        itc = stock("ITC"); up_income(itc, YEARS4); up_cash(itc)
        fh.apply_official(itc, [x for x in real_official()["records"] if x["symbol"] == "ITC"][0], TODAY)
        old["stocks"]["ITC"] = itc
        r = Run(branch_ledger=old, symbols="TCS")
        self.addCleanup(r.close)
        led = r.ledger()["stocks"]
        for sym in ("TCS", "ITC"):
            y = [x for x in led[sym]["years"] if x["fy"] == 2022][0]
            self.assertEqual(y["source"]["provider"], fh.OFFICIAL_PROVIDER)
            was = [x for x in old["stocks"][sym]["years"] if x["fy"] == 2022][0]
            self.assertEqual({k: v for k, v in y.items() if k != "last_confirmed"}, {k: v for k, v in was.items() if k != "last_confirmed"})    # only the confirmation date moves
        strip = lambda years: [{k: v for k, v in y.items() if k != "last_confirmed"} for y in years]
        self.assertEqual(strip(led["ITC"]["years"]), strip(old["stocks"]["ITC"]["years"]))                # a stock not run this time is carried over whole
        self.assertEqual(ls.no_loss_problems(old, r.ledger()), [])

    def test_7_the_guard_refuses_to_drop_an_official_record_or_its_provenance(self):
        old = old_ledger(); new = copy.deepcopy(old)
        new["stocks"]["TCS"]["years"] = [y for y in new["stocks"]["TCS"]["years"] if y["fy"] != 2022]
        self.assertTrue(any("record removed" in p for p in ls.no_loss_problems(old, new)))
        new = copy.deepcopy(old); y = [y for y in new["stocks"]["TCS"]["years"] if y["fy"] == 2022][0]
        y["source"]["provider"] = "Upstox"
        self.assertTrue(any("provider changed" in p for p in ls.no_loss_problems(old, new)))
        new = copy.deepcopy(old); y = [y for y in new["stocks"]["TCS"]["years"] if y["fy"] == 2022][0]
        del y["source"]["fields"]["revenue"]
        self.assertTrue(any("provenance" in p for p in ls.no_loss_problems(old, new)))


# ---------------------------------------------------------------- 5-6. the no-loss guard and revisions
class GuardTests(unittest.TestCase):
    def mutate(self, fn):
        old = old_ledger(); new = copy.deepcopy(old)
        fn(new["stocks"]["TCS"], [y for y in new["stocks"]["TCS"]["years"] if y["fy"] == 2024][0])
        return ls.no_loss_problems(old, new)

    def test_5_identical_ledgers_pass(self):
        self.assertEqual(ls.no_loss_problems(old_ledger(), old_ledger()), [])

    def test_5_a_removed_year_is_refused(self):
        self.assertTrue(any("record removed" in p for p in self.mutate(lambda s, y: s["years"].remove(y))))

    def test_5_a_removed_stock_is_refused_but_an_empty_one_is_not(self):
        old = old_ledger(); new = copy.deepcopy(old); del new["stocks"]["TCS"]
        self.assertTrue(any("stock removed" in p for p in ls.no_loss_problems(old, new)))
        old["stocks"]["EMPTY"] = {"symbol": "EMPTY", "years": []}
        new = copy.deepcopy(old_ledger())
        self.assertEqual(ls.no_loss_problems(old, new), [])

    def test_5_a_lost_value_is_refused(self):
        self.assertTrue(any("lost" in p for p in self.mutate(lambda s, y: y["values"].update(revenue=None))))

    def test_5_an_unrecorded_value_change_is_refused(self):
        self.assertTrue(any("without a revision" in p for p in self.mutate(lambda s, y: y["values"].update(revenue=y["values"]["revenue"] + 1))))

    def _field(self, old_v, new_v, revise=False):
        def fn(s, y):
            y["values"]["capex"] = new_v
        old = old_ledger(); y0 = [y for y in old["stocks"]["TCS"]["years"] if y["fy"] == 2024][0]; y0["values"]["capex"] = old_v
        new = copy.deepcopy(old); y1 = [y for y in new["stocks"]["TCS"]["years"] if y["fy"] == 2024][0]; y1["values"]["capex"] = new_v
        if revise:
            y1["revisions"].append({"superseded_on": TODAY, "source_fetched": TODAY, "values": {"capex": old_v}})
        return ls.no_loss_problems(old, new)

    def test_zero_1_null_to_zero_is_allowed(self):
        self.assertEqual(self._field(None, 0), [])
        self.assertEqual(self._field(None, 0.0), [])

    def test_zero_2_null_to_a_number_is_allowed(self):
        self.assertEqual(self._field(None, 12.5), [])

    def test_zero_3_zero_to_null_is_rejected(self):
        self.assertTrue(any("lost" in p for p in self._field(0, None)))
        self.assertTrue(any("lost" in p for p in self._field(0.0, None)))

    def test_zero_4_a_number_to_null_is_rejected(self):
        self.assertTrue(any("lost" in p for p in self._field(12.5, None)))

    def test_zero_5_a_change_without_a_revision_is_rejected(self):
        self.assertTrue(any("without a revision" in p for p in self._field(12.5, 15.0)))

    def test_zero_6_a_change_with_a_revision_is_allowed(self):
        self.assertEqual(self._field(12.5, 15.0, revise=True), [])

    def test_5_a_wrong_revision_does_not_excuse_a_change(self):
        def fn(s, y):
            old = y["values"]["revenue"]; y["values"]["revenue"] = old + 1
            y["revisions"].append({"superseded_on": TODAY, "source_fetched": TODAY, "values": {"revenue": old + 99}})      # records the wrong old value
        self.assertTrue(any("without a revision" in p for p in self.mutate(fn)))

    def test_5_earlier_revisions_may_not_be_altered_or_removed(self):
        old = old_ledger(); y = [y for y in old["stocks"]["TCS"]["years"] if y["fy"] == 2024][0]
        y["revisions"].append({"superseded_on": TODAY, "source_fetched": TODAY, "values": {"revenue": 1.0}})
        new = copy.deepcopy(old); [z for z in new["stocks"]["TCS"]["years"] if z["fy"] == 2024][0]["revisions"] = []
        self.assertTrue(any("revisions" in p for p in ls.no_loss_problems(old, new)))

    def test_6_a_value_change_recorded_in_revisions_is_allowed(self):
        def fn(s, y):
            old = y["values"]["revenue"]; y["values"]["revenue"] = old + 1
            y["revisions"].append({"superseded_on": TODAY, "source_fetched": TODAY, "values": {"revenue": old}})
        self.assertEqual(self.mutate(fn), [])

    def test_6_end_to_end_an_upstox_revision_is_recorded_and_passes_the_guard(self):
        old = old_ledger()
        r = Run(branch_ledger=old, responses=resp(YEARS_NEW, base=2000.0))                              # Upstox now reports different numbers
        self.addCleanup(r.close)
        self.assertEqual(r.code, 0, r.stdout)
        y = [y for y in r.ledger()["stocks"]["TCS"]["years"] if y["fy"] == 2026][0]
        was = [y for y in old["stocks"]["TCS"]["years"] if y["fy"] == 2026][0]
        self.assertTrue(y["revisions"]); self.assertEqual(y["revisions"][0]["values"]["revenue"], was["values"]["revenue"])
        self.assertEqual(ls.no_loss_problems(old, r.ledger()), [])

    def test_5_a_failed_guard_prevents_every_write(self):
        def drop_a_year(m):
            real = m.process_stock

            def broken(api, s, day):
                out = real(api, s, day)
                s["years"] = [y for y in s["years"] if y["fy"] != 2023]
                s["gaps"] = m.compute_gaps(s["years"])
                return out
            m.process_stock = broken
        r = Run(branch_ledger=old_ledger(), patch=drop_a_year)
        self.addCleanup(r.close)
        self.assertEqual(r.code, 1)
        self.assertFalse(r.out_ledger.exists()); self.assertFalse(r.out_doc.exists())
        self.assertIn("record removed", r.stdout)

    def test_5_the_command_line_check_matches(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        old, new = Path(t.name) / "old.json", Path(t.name) / "new.json"
        write(old, old_ledger()); write(new, old_ledger())
        run = lambda *a: subprocess.run([sys.executable, str(ROOT / "ledger_storage.py"), *a], capture_output=True, text=True)
        self.assertEqual(run("verify", str(old), str(new)).returncode, 0)
        bad = old_ledger(); bad["stocks"]["TCS"]["years"].pop()
        write(new, bad)
        r = run("verify", str(old), str(new))
        self.assertEqual(r.returncode, 1); self.assertIn("record removed", r.stdout + r.stderr)
        self.assertEqual(run("verify-first", str(old)).returncode, 0)
        write(new, {"schema": 1, "stocks": {}})
        self.assertEqual(run("verify-first", str(new)).returncode, 1)                                    # a first ledger may never be empty


# ---------------------------------------------------------------- unchanged things
class UnchangedTests(unittest.TestCase):
    def test_the_published_output_schema_is_unchanged(self):
        d = fh.build_output(old_ledger(), [], [], TODAY)
        self.assertEqual(set(d), {"schema", "as_of", "source", "interval", "notes", "stocks", "errors", "warnings"})
        self.assertEqual(d["schema"], 1); self.assertEqual(fh.SCHEMA_VERSION, 1)
        self.assertEqual(fh.validate_doc(d), [])

    def test_protected_files_do_not_use_the_new_module(self):
        for name in ("index.html", "nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "historical_updater.py", "validate_outputs.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("ledger_storage", text, name); self.assertNotIn("stocklens-data", text, name)

    def test_the_storage_module_needs_no_third_party_packages(self):
        src = (ROOT / "ledger_storage.py").read_text(encoding="utf-8")
        imports = re.findall(r"^(?:import|from)\s+([A-Za-z_][\w]*)", src, re.M)
        self.assertTrue(set(imports) <= {"json", "sys", "pathlib", "os", "copy"}, imports)               # the save job installs nothing


# ---------------------------------------------------------------- 8-9. workflows
def wf(name):
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def job_block(text, job):
    m = re.search(r"^  %s:\n(.*?)(?=^  \S[\w-]*:\n|\Z)" % re.escape(job), text, re.M | re.S)
    assert m, job
    return m.group(1)


class WorkflowTests(unittest.TestCase):
    def test_8_both_workflows_share_one_non_cancelling_ledger_group(self):
        groups = []
        for name in ("update.yml", "financial_history.yml"):
            t = wf(name)
            m = re.search(r"^concurrency:\n  group: (\S+)\n  cancel-in-progress: false\n", t, re.M)
            self.assertTrue(m, name); groups.append(m.group(1))
            self.assertNotIn("cancel-in-progress: true", t)
        self.assertEqual(groups[0], groups[1]); self.assertEqual(groups[0], "stocklens-ledger")

    def test_8_the_reusable_save_workflow_sets_no_concurrency_of_its_own(self):
        self.assertNotIn("concurrency", wf("save_ledger.yml"))                                           # it would deadlock against the caller's group

    def test_8_historical_workflow_never_writes_the_ledger_branch(self):
        # Stage 1 coverage: historical.yml may READ universe.json from the data branch (read-only checkout, no credentials kept), but it must not join the ledger
        # concurrency group, push, or hold write permission.
        t = wf("historical.yml")
        self.assertNotIn("stocklens-ledger", t); self.assertNotIn("git push", t)
        self.assertIn("persist-credentials: false", t)
        # Historical hand-over (approved): the ONLY write permission in historical.yml is on the job that calls the reusable save workflow, which writes under historical/ only.
        # The job that holds the Upstox token has none, and the historical.yml itself never pushes.
        self.assertEqual(t.count("contents: write"), 1)
        save = job_block(t, "save-historical")
        self.assertIn("contents: write", save); self.assertIn("uses: ./.github/workflows/save_historical.yml", save)
        main = job_block(t, "historical")
        for banned in ("contents: write", "git push", "uses:  ./.github/workflows/save"):
            self.assertNotIn(banned, main)
        self.assertNotIn("UPSTOX", save); self.assertNotIn("secrets", save)

    def test_9_the_save_job_never_sees_the_upstox_token(self):
        reusable = wf("save_ledger.yml")
        self.assertNotIn("UPSTOX", reusable); self.assertNotIn("secrets", reusable.lower())
        for name, job in (("update.yml", "save-history"), ("financial_history.yml", "save-history")):
            block = job_block(wf(name), job)
            for banned in ("UPSTOX", "secrets", "env:", "steps:"):
                self.assertNotIn(banned, block, name + " " + banned)
            self.assertIn("uses: ./.github/workflows/save_ledger.yml", block)
            self.assertRegex(block, r"permissions:\n      contents: write\n")
            self.assertNotIn("inherit", block)

    def test_9_the_token_is_only_given_to_upstox_updater_steps(self):
        for name in ("update.yml", "financial_history.yml"):
            t = wf(name)
            for step in re.split(r"\n      - ", t):
                if "UPSTOX_ANALYTICS_TOKEN" in step:
                    self.assertRegex(step, r"python (fundamentals_updater|financials_updater|financial_history_updater|shareholding_updater)\.py", name)
        self.assertNotIn("UPSTOX", job_block(wf("update.yml"), "deploy"))

    def test_9_only_the_save_job_can_write(self):
        upd = wf("update.yml")
        self.assertRegex(upd, r'(?m)^permissions:\n  contents: read\n')                                    # the workflow-level default is unchanged
        self.assertNotIn("contents: write", job_block(upd, "build")); self.assertNotIn("contents: write", job_block(upd, "deploy"))
        self.assertIn("pages: write", job_block(upd, "deploy")); self.assertIn("id-token: write", job_block(upd, "deploy"))
        self.assertEqual(upd.count("contents: write"), 2)                                                   # save-history (reusable call) and, since Phase 5H.5, save-shareholding: nothing else can write
        self.assertIn("contents: write", job_block(upd, "save-shareholding"))
        hist = wf("financial_history.yml")
        self.assertNotIn("contents: write", job_block(hist, "history"))
        self.assertEqual(hist.count("contents: write"), 1)
        reusable = wf("save_ledger.yml")
        self.assertEqual(reusable.count("contents: write"), 1)
        self.assertRegex(reusable, r"(?m)^permissions:\n  contents: read\n")

    def test_the_updater_step_gets_the_branch_and_handoff_paths(self):
        for name in ("update.yml", "financial_history.yml"):
            t = wf(name)
            self.assertIn("FINANCIAL_HISTORY_LEDGER_DIR", t); self.assertIn("FINANCIAL_HISTORY_LEDGER_OUT: ledger-out/financial_history_ledger.json", t)
            self.assertIn("ref: stocklens-data", t); self.assertIn("persist-credentials: false", t)
            self.assertIn("name: financial-history-ledger", t)

    def test_the_build_still_publishes_out_json_unchanged(self):
        upd = wf("update.yml")
        self.assertIn("cp out/*.json _site/out/", upd); self.assertIn("cp company_profiles.json _site/out/", upd)
        self.assertEqual(job_block(upd, "deploy").count("deploy-pages@v4"), 1)
        self.assertIn("continue-on-error: true", job_block(upd, "build").split("Update financial history (Upstox)")[1].split("- name:")[0])
        self.assertNotIn("ledger-out", upd.split("Build site folder")[1].split("Upload Pages artifact")[0])    # the ledger is never copied into the site

    def test_the_branch_check_never_treats_an_error_as_absent(self):
        for name in ("update.yml", "save_ledger.yml"):
            t = wf(name)
            self.assertIn("ls-remote --exit-code --heads origin stocklens-data", t)
            self.assertRegex(t, r'case "\$rc" in\s+0\).*?\s+2\).*?\s+\*\).*?exit 1', name)

    def test_migration_never_overwrites_an_existing_branch(self):
        t = wf("save_ledger.yml")
        self.assertNotIn("--force", t); self.assertNotIn("push -f", t); self.assertNotIn("+refs", t); self.assertNotIn("--force-with-lease", t)
        self.assertIn("create_branch", t)
        create = t.split("Create the data branch")[1]
        self.assertIn("steps.databranch.outputs.exists != 'true'", create); self.assertIn("inputs.create_branch", create)
        self.assertIn("ls-remote --exit-code", create)                                                    # re-checked right before the push
        self.assertIn("git switch --orphan stocklens-data", create)
        self.assertEqual(wf("update.yml").count("create_branch: false"), 1)                                 # the daily run can never create the branch

    def test_first_migration_really_creates_a_branch_holding_exactly_one_file(self):
        """Runs the real 'Create the data branch' script in a scratch clone (orphan branch starts with an empty index)."""
        step = wf("save_ledger.yml").split("name: Create the data branch")[1]
        script = re.search(r"run: \|\n(.*)", step, re.S).group(1)
        script = "\n".join(l[10:] if l.startswith(" " * 10) else l for l in script.splitlines()).replace("${{ github.run_id }}", "42")
        self.assertNotIn("git rm", script)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0")

        def git(cwd, *a):
            return subprocess.run(["git", *a], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); remote = d / "remote.git"; work = d / "work"
            git(d, "init", "-q", "--bare", str(remote))
            git(d, "init", "-q", "-b", "phase1", str(work))
            git(work, "config", "user.email", "t@example.com"); git(work, "config", "user.name", "t")
            (work / "index.html").write_text("page"); (work / ".github" / "workflows").mkdir(parents=True)
            (work / ".github" / "workflows" / "x.yml").write_text("x")
            git(work, "add", "-A"); git(work, "commit", "-q", "-m", "code"); git(work, "remote", "add", "origin", str(remote))
            git(work, "push", "-q", "origin", "phase1")
            (work / "ledger-new").mkdir(); (work / "ledger-new" / "financial_history_ledger.json").write_text('{"schema": 1, "stocks": {}}')   # the downloaded artifact, untracked
            r = subprocess.run(["bash", "-e", "-c", script], cwd=work, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(git(remote, "ls-tree", "-r", "--name-only", "stocklens-data").split(), ["financial_history_ledger.json"])
            self.assertEqual(git(remote, "rev-list", "--count", "stocklens-data").strip(), "1")                  # one commit, no code history
            self.assertEqual(git(remote, "rev-parse", "phase1").strip(), git(work, "rev-parse", "phase1").strip())   # the code branch is untouched
            r2 = subprocess.run(["bash", "-e", "-c", script], cwd=work, env=env, capture_output=True, text=True)    # a second run must refuse: the branch now exists
            self.assertNotEqual(r2.returncode, 0)
            self.assertEqual(git(remote, "rev-list", "--count", "stocklens-data").strip(), "1")

    def test_the_manual_workflow_can_create_the_branch_only_when_asked(self):
        t = wf("financial_history.yml")
        self.assertRegex(t, r"create_data_branch:\n(?:.*\n)*?\s+type: boolean\n(?:\s+required: false\n)?\s+default: false")
        self.assertIn("create_branch: ${{ inputs.create_data_branch }}", t)

    def test_the_manual_workflow_stays_manual_only(self):
        t = wf("financial_history.yml")
        self.assertIn("workflow_dispatch", t); self.assertNotIn("push:", t); self.assertNotIn("schedule", t); self.assertNotIn("deploy-pages", t)

    def test_the_save_workflow_is_only_callable(self):
        t = wf("save_ledger.yml")
        self.assertIn("workflow_call:", t)
        for banned in ("push:", "schedule", "workflow_dispatch", "pull_request"):
            self.assertNotIn(banned, t)

    def test_the_save_workflow_verifies_before_it_writes(self):
        t = wf("save_ledger.yml")
        self.assertLess(t.index("ledger_storage.py verify "), t.index("git push origin HEAD:stocklens-data"))
        self.assertLess(t.index("ledger_storage.py verify-first"), t.index("git push origin stocklens-data"))
        self.assertIn("git diff --cached --quiet", t)                                                       # no commit when nothing changed


if __name__ == "__main__":
    unittest.main(verbosity=0)
