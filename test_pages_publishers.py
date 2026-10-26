"""
Every workflow that publishes the site to GitHub Pages must publish the SAVED daily prices: the historical master file and the per-stock price files that
"Save historical price data" keeps under historical/ on the data branch (stocklens-data). Without them the Stock Detail price chart and Technical Snapshot
have no candles for any stock, so a second publisher that leaves them out silently empties the charts of the whole site.

Run:  python3 -m unittest test_pages_publishers -v      (offline: reads the workflow files and runs their "Build site folder" step with bash in a temporary folder;
                                                          no network, no credential, nothing is published)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
MASTER = "if [ -f ledger-branch/historical/historical.json ]; then cp ledger-branch/historical/historical.json _site/out/historical.json; fi"
SHARDS = ("if ls ledger-branch/historical/shards/*.json >/dev/null 2>&1; then mkdir -p _site/out/by_symbol/historical; "
          "cp ledger-branch/historical/shards/*.json _site/out/by_symbol/historical/; fi")


def publishers():
    """Every workflow that deploys Pages, found by what it does (not by a list), so a new publisher is covered without editing this test."""
    out = []
    for p in sorted(WORKFLOWS.glob("*.yml")):
        t = p.read_text(encoding="utf-8")
        if "actions/deploy-pages@" in t or "actions/upload-pages-artifact@" in t:
            out.append(p.name)
    return out


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def site_job(wf):
    """The job that builds the site folder (the one that uploads the Pages artifact)."""
    jobs = [j for j in wf["jobs"].values() if any(str(s.get("uses", "")).startswith("actions/upload-pages-artifact@") for s in j.get("steps", []))]
    assert len(jobs) == 1, "exactly one job uploads the Pages artifact"
    return jobs[0]


def run_lines(step):
    return [l.strip() for l in str(step.get("run", "")).splitlines() if l.strip() and not l.strip().startswith("#")]


BY_SYMBOL = "if [ -d out/by_symbol ]; then cp -r out/by_symbol _site/out/by_symbol; fi"


def build_problems(wf):
    """What is wrong with a publisher's "Build site folder" step, as a list (empty = it publishes the saved price history correctly)."""
    steps = site_job(wf)["steps"]
    build = [s for s in steps if s.get("name") == "Build site folder"]
    if len(build) != 1:
        return ["no single Build site folder step"]
    lines, out = run_lines(build[0]), []
    if MASTER not in lines:
        out.append("the historical master file is not copied from the data branch")
    if SHARDS not in lines:
        out.append("the per-stock historical files are not copied from the data branch")
    if out:
        return out
    if [l for l in lines if "historical" in l] != [MASTER, SHARDS]:
        out.append("another historical copy or removal is present")
    if "cp out/*.json _site/out/" not in lines or lines.index("cp out/*.json _site/out/") > lines.index(MASTER):
        out.append("the cached files are copied after the saved master (the older copy would win)")
    if BY_SYMBOL not in lines or lines.index(BY_SYMBOL) > lines.index(SHARDS):
        out.append("the cached per-stock folder is copied after the saved shards")
    if not any("sensex_publish.py guard" in l for l in lines[lines.index(SHARDS):]):
        out.append("the SENSEX guard no longer runs after every copy")
    if any(l.startswith("rm") and "historical" in l for l in lines):
        out.append("something removes the price history")
    return out


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")


def candles(n, start=100.0):
    return [{"date": "2026-01-%02d" % (i + 1), "open": start + i, "high": start + i + 1, "low": start + i - 1, "close": start + i, "volume": 1000 + i} for i in range(n)]


class Discovery(unittest.TestCase):
    def test_both_known_publishers_are_found(self):
        found = publishers()
        self.assertIn("update.yml", found)
        self.assertIn("market_data.yml", found)                          # the manual market-data pipeline publishes the whole site too


class Wiring(unittest.TestCase):
    """Static checks on each publisher: the data branch is checked out read-only, and the build copies the master file and the shards, after the cache copies."""

    def test_every_publisher_checks_out_the_data_branch_read_only_before_the_build(self):
        for name in publishers():
            with self.subTest(name):
                steps = site_job(load(name))["steps"]
                look = [i for i, s in enumerate(steps) if s.get("id") == "databranch"]
                self.assertEqual(len(look), 1, "one data-branch lookup step")
                self.assertIn("git ls-remote --exit-code --heads origin stocklens-data", steps[look[0]]["run"])
                self.assertIn('2) echo "exists=false"', steps[look[0]]["run"])          # a missing branch is told apart from a network failure
                co = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout@") and (s.get("with") or {}).get("ref") == "stocklens-data"]
                self.assertEqual(len(co), 1, "one checkout of the data branch")
                w = steps[co[0]]["with"]
                self.assertEqual(w.get("path"), "ledger-branch")
                self.assertIs(w.get("persist-credentials"), False)                      # read-only: no token is left behind for later steps
                self.assertEqual(steps[co[0]].get("if"), "steps.databranch.outputs.exists == 'true'")
                build = [i for i, s in enumerate(steps) if s.get("name") == "Build site folder"]
                self.assertEqual(len(build), 1)
                self.assertTrue(look[0] < co[0] < build[0], "lookup, then checkout, then the site build")

    def test_every_publisher_copies_the_master_file_and_the_shards_after_the_cache_copies(self):
        for name in publishers():
            with self.subTest(name):
                self.assertEqual(build_problems(load(name)), [])

    def test_publishers_build_the_same_historical_lines(self):
        got = {}
        for name in publishers():
            steps = site_job(load(name))["steps"]
            got[name] = [l for l in run_lines([s for s in steps if s.get("name") == "Build site folder"][0]) if "historical" in l]
        self.assertEqual(len({json.dumps(v) for v in got.values()}), 1, got)


class Behaviour(unittest.TestCase):
    """Each publisher's real "Build site folder" step, run with bash in a temporary folder that stands in for the runner."""

    def run_build(self, name, root):
        step = [s for s in site_job(load(name))["steps"] if s.get("name") == "Build site folder"][0]
        script = step["run"].replace("python sensex_publish.py", '"%s" sensex_publish.py' % sys.executable)
        env = dict(os.environ)
        env.pop("SENSEX_PUBLIC_DISPLAY_APPROVED", None)
        env.update({k: str(v) for k, v in (step.get("env") or {}).items()})
        return subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=str(root), env=env, capture_output=True, text=True, timeout=120)

    def lay_out(self, root, ledger=True, shards=True):
        write(root / "index.html", "<html>page</html>")
        write(root / "company_profiles.json", '{"profiles":1}')
        write(root / "out" / "scans.json", '{"scans":1}')
        write(root / "out" / "historical.json", {"updated": "OLD-CACHE", "stocks": {}})                      # an older copy from the saved-data cache
        write(root / "out" / "by_symbol" / "quarterly_results" / "TCS.json", '{"q":1}')
        shutil.copy(ROOT / "sensex_publish.py", root / "sensex_publish.py")
        master = {"updated": "2026-10-24", "interval": "1day", "benchmark": {"symbol": "NIFTY 50", "candles": candles(40, 20000)},
                  "stocks": {"TCS": {"symbol": "TCS", "candles": candles(40)}}}
        shard = {"updated": "2026-10-24", "interval": "1day", "stocks": {"DOMS": {"symbol": "DOMS", "candles": candles(35)}}}
        if ledger:
            write(root / "ledger-branch" / "historical" / "historical.json", master)
            if shards:
                write(root / "ledger-branch" / "historical" / "shards" / "DOMS.json", shard)
                write(root / "ledger-branch" / "historical" / "shards" / "M&M.json", {"updated": "2026-10-24", "stocks": {"M&M": {"symbol": "M&M", "candles": candles(31)}}})
        return master, shard

    def test_the_saved_history_and_every_shard_reach_the_site(self):
        for name in publishers():
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                root = Path(t)
                master, shard = self.lay_out(root)
                r = self.run_build(name, root)
                self.assertEqual(r.returncode, 0, r.stderr)
                site = root / "_site" / "out"
                self.assertEqual(json.loads((site / "historical.json").read_text()), master, "the data-branch master replaces the cached copy")
                self.assertEqual(json.loads((site / "by_symbol" / "historical" / "DOMS.json").read_text()), shard)
                self.assertTrue((site / "by_symbol" / "historical" / "M&M.json").exists(), "symbols with & keep their file name")
                self.assertEqual(sorted(p.name for p in (site / "by_symbol" / "historical").iterdir()), ["DOMS.json", "M&M.json"])
                self.assertTrue((site / "by_symbol" / "quarterly_results" / "TCS.json").exists(), "the other per-stock files are still published")
                for p in (site / "by_symbol" / "historical").iterdir():                                       # what the page's shard loader expects
                    d = json.loads(p.read_text())
                    self.assertEqual(list(d["stocks"]), [p.stem])

    def test_without_shards_the_master_file_is_still_published(self):
        for name in publishers():
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                root = Path(t)
                master, _ = self.lay_out(root, shards=False)
                self.assertEqual(self.run_build(name, root).returncode, 0)
                self.assertEqual(json.loads((root / "_site" / "out" / "historical.json").read_text()), master)
                self.assertFalse((root / "_site" / "out" / "by_symbol" / "historical").exists(), "no folder is invented")

    def test_without_the_data_branch_the_build_still_succeeds_and_invents_nothing(self):
        for name in publishers():
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                root = Path(t)
                self.lay_out(root, ledger=False)
                self.assertEqual(self.run_build(name, root).returncode, 0)
                self.assertEqual(json.loads((root / "_site" / "out" / "historical.json").read_text())["updated"], "OLD-CACHE")   # only what was there
                self.assertFalse((root / "_site" / "out" / "by_symbol" / "historical").exists())

    def test_a_publisher_missing_either_copy_is_rejected(self):
        # mutation check on the real workflow text: deleting either copy line must be caught
        for name in publishers():
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            for drop in (MASTER, SHARDS):
                with self.subTest(name=name, drop=drop[:45]):
                    mutated = "\n".join(l for l in text.splitlines() if l.strip() != drop)
                    self.assertNotEqual(mutated, text)
                    self.assertNotEqual(build_problems(yaml.safe_load(mutated)), [])

if __name__ == "__main__":
    unittest.main()
