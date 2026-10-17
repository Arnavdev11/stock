"""
test_shareholding_cache_save.py - the manual shareholding run must not leave a cache without the fundamentals state behind.

Background: shareholding.yml restores with the prefix nse-data- and saves nse-data-shp-<run>. "Update data and publish site" restores the newest cache whose key
starts with nse-data- (and whose path set is the same). A shareholding run that started without data/fundamentals_cache.json (a cold start, or a restore that
failed) used to save its state-less workspace anyway (the combined actions/cache step saves whatever it finds), and that cache then became the newest one and
hid the last daily cache. Now the data is saved by a separate actions/cache/save step, only when the job succeeded and data/fundamentals_cache.json exists.

Offline: reads the workflow files only (and, for the "before" comparison, the committed baseline). No network, no GitHub Actions.
Run: python3 -m unittest test_shareholding_cache_save
"""
import re
import subprocess
import unittest
from pathlib import Path

import yaml

import test_cache_isolation as iso

HERE = Path(__file__).resolve().parent
WF = HERE / ".github" / "workflows"
STATE = "data/fundamentals_cache.json"
BASELINE = "b6c058b7770da31fe7aff8e845fe3c4ce19202ea"      # shareholding.yml before the split (the combined actions/cache step)


def steps_of(text, job="shareholding"):
    return yaml.safe_load(text)["jobs"][job]["steps"]


def cache_steps(steps):
    return [s for s in steps if str(s.get("uses") or "").startswith("actions/cache")]


def condition(step):
    """The step's `if:` without the ${{ }} wrapper."""
    m = re.fullmatch(r"\s*\$\{\{\s*(.*?)\s*\}\}\s*", str(step.get("if", "")), re.S)
    return m.group(1) if m else str(step.get("if", ""))


def evaluate(expr, job_succeeded, state_exists):
    """Evaluates the only expression shapes these steps may use: success(), hashFiles('<path>') compared with '', joined by &&."""
    parts = [p.strip() for p in expr.split("&&")]
    ok = True
    for p in parts:
        if p == "success()":
            ok = ok and job_succeeded
            continue
        m = re.fullmatch(r"hashFiles\('([^']+)'\)\s*(!=|==)\s*''", p)
        if not m or m.group(1) != STATE:
            raise AssertionError("unexpected condition part: %r" % p)
        digest = "0123abcd" if state_exists else ""
        ok = ok and ((digest != "") if m.group(2) == "!=" else (digest == ""))
    return ok


class Model:
    """The cache store with GitHub's restore rule: the newest cache whose key starts with a restore prefix and whose path set is the same."""

    def __init__(self):
        self.store = []                  # (sequence, key, paths, holds_state)
        self.seq = 0

    def add(self, key, paths, holds_state):
        self.seq += 1
        self.store.append((self.seq, key, frozenset(paths), holds_state))

    def restore(self, prefixes, paths):
        hits = [c for c in self.store if any(c[1].startswith(p) for p in prefixes) and c[2] == frozenset(paths)]
        return max(hits) if hits else None


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (WF / "shareholding.yml").read_text(encoding="utf-8")
        cls.steps = steps_of(cls.text)
        cls.cs = cache_steps(cls.steps)
        cls.daily = iso.caches(yaml.safe_load((WF / "update.yml").read_text(encoding="utf-8")))

    def by_name(self, start):
        hit = [s for s in self.steps if str(s.get("name", "")).startswith(start)]
        self.assertEqual(len(hit), 1, start)
        return hit[0]

    def test_there_is_no_combined_cache_step_any_more(self):
        self.assertEqual([s["uses"] for s in self.cs], ["actions/cache/restore@v4", "actions/cache/save@v4"])
        self.assertNotIn("actions/cache@", self.text)

    def test_the_restore_is_unchanged_in_key_prefix_and_paths(self):
        r = self.cs[0]["with"]
        self.assertEqual(r["key"], "nse-data-shp-${{ github.run_id }}")
        self.assertEqual(r["restore-keys"].strip(), "nse-data-")
        self.assertEqual({p.strip() for p in r["path"].splitlines() if p.strip()}, {"data", "out"})

    def test_the_save_uses_the_same_key_and_paths_so_the_daily_restore_can_match_it(self):
        r, s = self.cs[0]["with"], self.cs[1]["with"]
        self.assertEqual(s["key"], r["key"])
        self.assertEqual(s["path"], r["path"])
        self.assertNotIn("restore-keys", s)
        daily = [c for c in self.daily if c[1] in ("both", "restore")]
        self.assertTrue(daily)
        self.assertEqual({c[4] for c in daily}, {frozenset({"data", "out"})})                    # the daily restore has the very path set that is saved here
        self.assertTrue(all(c[3] == ["nse-data-"] for c in daily))

    def test_the_save_runs_only_when_the_job_succeeded_and_the_fundamentals_state_exists(self):
        c = condition(self.cs[1])
        self.assertEqual([p.strip() for p in c.split("&&")], ["success()", "hashFiles('%s') != ''" % STATE])
        for ok, state, expected in [(True, True, True), (True, False, False), (False, True, False), (False, False, False)]:
            self.assertEqual(evaluate(c, ok, state), expected, (ok, state))

    def test_without_the_state_exactly_one_clear_warning_step_runs_instead(self):
        save, warn = self.cs[1], self.by_name("Warn that the data was not saved")
        self.assertNotIn("uses", warn)
        self.assertIn("::warning::", warn["run"])
        self.assertIn("no nse-data-shp cache is saved", warn["run"])
        self.assertNotIn("exit 1", warn["run"])                                                  # a warning, never a failure of the ledger run
        wc = condition(warn)
        for ok, state in [(True, True), (True, False), (False, True), (False, False)]:
            self.assertFalse(evaluate(condition(save), ok, state) and evaluate(wc, ok, state), "both run")
            self.assertEqual(evaluate(wc, ok, state), ok and not state, (ok, state))

    def test_the_cache_steps_come_after_the_ledger_hand_off_and_cannot_affect_it(self):
        names = [s.get("name", "") for s in self.steps]
        i_restore = names.index("Restore saved data")
        i_update = names.index("Update the shareholding ledger")
        i_handoff = [i for i, s in enumerate(self.steps) if s.get("uses", "").startswith("actions/upload-artifact")]
        self.assertEqual(len(i_handoff), 2)
        i_save = names.index("Save the data for the next site build (only with the fundamentals state)")
        i_warn = names.index("Warn that the data was not saved (no fundamentals state)")
        self.assertLess(i_restore, i_update)
        self.assertLess(max(i_handoff), min(i_save, i_warn))
        self.assertEqual(max(i_save, i_warn), len(self.steps) - 1)                              # last in the job, where the old post-step ran

    def test_the_ledger_and_the_rest_of_the_workflow_are_unchanged_in_substance(self):
        self.assertEqual(self.text.count("git push"), 1)
        uploads = [s["with"]["name"] for s in self.steps if str(s.get("uses", "")).startswith("actions/upload-artifact")]
        self.assertEqual(uploads, ["shareholding-ledger", "shareholding-output"])
        doc = yaml.safe_load(self.text)
        self.assertEqual(doc["concurrency"], {"group": "stocklens-ledger", "cancel-in-progress": False})
        self.assertEqual(doc["permissions"], {"contents": "read"})
        self.assertEqual(doc["jobs"]["shareholding"]["timeout-minutes"], 300)
        self.assertEqual(list(doc[True]), ["workflow_dispatch"])                                # manual only; no schedule (YAML reads `on` as True)

    def test_the_isolation_rule_still_holds(self):
        self.assertEqual(iso.violations(iso.load_all(lambda n: (WF / n).read_text(encoding="utf-8"))), [])


class Scenario(unittest.TestCase):
    """Replays the sequence: last daily save -> manual shareholding run -> next daily restore, with the rules read from the workflow files."""

    @classmethod
    def setUpClass(cls):
        cls.new_steps = steps_of((WF / "shareholding.yml").read_text(encoding="utf-8"))
        cls.daily = [c for c in iso.caches(yaml.safe_load((WF / "update.yml").read_text(encoding="utf-8"))) if c[1] in ("both", "restore")][0]
        r = subprocess.run(["git", "show", BASELINE + ":.github/workflows/shareholding.yml"], cwd=str(HERE), capture_output=True, text=True)
        cls.old_steps = steps_of(r.stdout) if r.returncode == 0 else None

    def run_shareholding(self, model, steps, restore_works=True, job_succeeded=True):
        """-> True when a nse-data-shp cache was saved. The workspace has the state iff the restored cache held it."""
        cs = cache_steps(steps)
        rest = cs[0]["with"]
        prefixes = [p.strip() for p in rest["restore-keys"].splitlines() if p.strip()]
        paths = {p.strip() for p in rest["path"].splitlines() if p.strip()}
        hit = model.restore(prefixes, paths) if restore_works else None
        has_state = bool(hit and hit[3])
        if len(cs) == 1:                                  # the old combined step: saves what the workspace holds, with or without the state
            saves = job_succeeded
        else:
            saves = evaluate(condition(cs[1]), job_succeeded, has_state)
        if saves:
            model.add("nse-data-shp-%d" % (model.seq + 1), paths, has_state)
        return saves

    def next_daily(self, model):
        return model.restore(self.daily[3], self.daily[4])

    def model_with_daily(self):
        m = Model()
        m.add("nse-data-100", {"data", "out"}, True)
        return m

    def test_a_normal_run_carries_the_state_forward(self):
        m = self.model_with_daily()
        self.assertTrue(self.run_shareholding(m, self.new_steps))
        hit = self.next_daily(m)
        self.assertTrue(hit[1].startswith("nse-data-shp-") and hit[3])                          # saved, newest, and it holds the state

    def test_a_run_whose_restore_failed_must_not_shadow_the_last_valid_daily_cache(self):
        m = self.model_with_daily()
        self.assertFalse(self.run_shareholding(m, self.new_steps, restore_works=False))
        hit = self.next_daily(m)
        self.assertEqual((hit[1], hit[3]), ("nse-data-100", True))

    def test_a_cold_start_run_saves_nothing_so_the_daily_run_still_sees_a_genuine_miss(self):
        m = Model()
        self.assertFalse(self.run_shareholding(m, self.new_steps))
        self.assertEqual(m.store, [])
        self.assertIsNone(self.next_daily(m))

    def test_a_failed_job_saves_nothing(self):
        m = self.model_with_daily()
        self.assertFalse(self.run_shareholding(m, self.new_steps, job_succeeded=False))
        self.assertEqual(len(m.store), 1)

    def test_before_the_split_the_same_failed_restore_did_shadow_the_daily_cache(self):
        if self.old_steps is None:
            self.skipTest("commit %s is not available in this checkout" % BASELINE[:7])
        m = self.model_with_daily()
        self.assertTrue(self.run_shareholding(m, self.old_steps, restore_works=False))
        hit = self.next_daily(m)
        self.assertEqual((hit[1].startswith("nse-data-shp-"), hit[3]), (True, False))           # the newest cache held no fundamentals state


if __name__ == "__main__":
    unittest.main()
