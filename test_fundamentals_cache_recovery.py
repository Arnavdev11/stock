"""Recovery of the fundamentals state after a cache miss (fundamentals_updater.py seed, and the steps of update.yml / fundamentals_batch.yml that use it).
The workflow steps' own shell scripts are executed here with stub `gh` and `curl` commands (the real `jq` filter is applied to each stubbed page), so the
five conditions without a usable state (a restored state, a cache restored without the state, no cache at all, an unusable cache list, a cache that exists
but was not restored) and the stop-instead-of-publish paths are exercised offline. No network, no provider call, no real GitHub Actions cache.
Run: python3 -m unittest test_fundamentals_cache_recovery"""
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

import fundamentals_updater as fu
import validate_outputs as vo
from test_fundamentals_full_universe import FakeDate, Harness, Resp

HERE = Path(__file__).resolve().parent
D = __import__("datetime").timedelta(days=1)
BASE = __import__("datetime").date.today() - 2 * D   # the published state is built a little in the past: the seed step runs as a real process on the real date,
#                                                      and it refuses a file older than RATIOS_MAX_AGE_DAYS


def step(workflow, name):
    w = yaml.safe_load((HERE / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    for job in w["jobs"].values():
        for s in job.get("steps") or []:
            if s.get("name") == name:
                return s
    raise AssertionError("no step %r in %s" % (name, workflow))


GH_STUB = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
cfg = json.load(open(os.environ["GH_STUB_CONFIG"]))
args = sys.argv[1:]
open(cfg["log"], "a").write("gh " + " ".join(args) + "\n")
jq = args[args.index("--jq") + 1]
url = next(a for a in args if a.startswith("repos/"))
ref = [q.split("=", 1)[1] for q in url.split("?")[1].split("&") if q.startswith("ref=")][0]
if cfg["fail_all"] or ref in cfg["fail_refs"]:
    sys.exit(1)
pages = cfg["refs"].get(ref, cfg["default"]) or [{"total_count": 0, "actions_caches": []}]
for i, pg in enumerate(pages):
    if cfg.get("fail_after") is not None and i >= cfg["fail_after"]:
        sys.exit(1)
    r = subprocess.run(["jq", "-r", jq], input=json.dumps(pg), text=True, capture_output=True)
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.exit(r.returncode)
"""


def pages_of(keys, total=None, size=100):
    """The pages the caches API would return for these keys (total_count = the number of caches that match, on every page)."""
    total = len(keys) if total is None else total
    chunks = [keys[i:i + size] for i in range(0, len(keys), size)] or [[]]
    return [{"total_count": total, "actions_caches": [{"key": k, "ref": "refs/heads/main"} for k in c]} for c in chunks]


class Site:
    """A published site, a runner workspace and a universe: everything the recovery reads, in one temporary folder."""
    def __init__(self, test, n=40, letters="ABE", missing_from_upstox=("A0004",), base=None):
        self.h = Harness(test, n, letters=letters, missing_from_upstox=missing_from_upstox, cap=10 ** 6)
        self.h.run(day=base or BASE)                                            # a normal published state (complete, partial, failed, not listed)
        self.t = self.h.t
        self.published_bytes = (self.t / "out" / "fundamentals.json").read_bytes()
        self.published = json.loads(self.published_bytes)
        self.ws = Path(tempfile.mkdtemp())                              # the runner's workspace after a cache miss: no data/, no out/
        test.addCleanup(lambda: shutil.rmtree(self.ws, ignore_errors=True))
        (self.ws / "site.json").write_bytes(self.published_bytes)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        self.calls = self.ws / "stub_calls.log"

    def stub(self, refs=None, default=None, fail_all=False, fail_refs=(), fail_after=None, curl_fails=False, curl_payload=None):
        """gh: the cache list. refs = {ref: [page, page, ...]} (each page the JSON the API returns); default = the pages of any other ref; fail_refs = refs
        whose listing fails (fail_all: every listing; fail_after=N: the listing breaks after N pages, like a failing second request of --paginate). curl: serves the published file (or fails)."""
        if curl_payload is not None:
            (self.ws / "site.json").write_bytes(curl_payload)
        (self.ws / "gh_stub.json").write_text(json.dumps({"log": str(self.calls), "refs": refs or {}, "default": default or [], "fail_all": fail_all, "fail_refs": list(fail_refs), "fail_after": fail_after}))
        (self.bin / "gh").write_text(GH_STUB)
        (self.bin / "curl").write_text("#!/bin/bash\necho \"curl $*\" >> %s\n%s\nout=''; while [ $# -gt 0 ]; do [ \"$1\" = -o ] && { out=\"$2\"; shift; }; shift; done\ncp %s \"$out\"\n"
                                       % (self.calls, "exit 22" if curl_fails else "", self.ws / "site.json"))
        for f in ("gh", "curl"):
            os.chmod(self.bin / f, 0o755)

    def run_step(self, workflow, name, extra_env=None, outputs=None):
        """Runs a step's `run:` script like the runner would (bash -e, its env, GITHUB_OUTPUT); returns (exit code, outputs, stdout)."""
        s = step(workflow, name)
        out_file = self.ws / ("out_%s.txt" % abs(hash(name)))
        out_file.write_text("")
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN",)}
        env.update({"PATH": "%s:%s" % (self.bin, os.environ["PATH"]), "GITHUB_OUTPUT": str(out_file), "PYTHONPATH": str(HERE), "PYTHONDONTWRITEBYTECODE": "1",
                    "DATA_DIR": str(self.ws), "STOCKLENS_UNIVERSE_FILE": str(self.t / "u.json"),
                    "GH_TOKEN": "x", "REPO": "Arnavdev11/stock", "REF": "refs/heads/main", "DEFAULT_BRANCH": "main", "GH_STUB_CONFIG": str(self.ws / "gh_stub.json"),
                    "LOOKUP_OUTCOME": "success", "LOOKUP_KEY": "", "RESTORE_HIT": ""})
        env.update(extra_env or {})
        script = s["run"].replace("${{ inputs.mode }}", "run")
        (self.ws / "fundamentals_updater.py").unlink(missing_ok=True)
        os.symlink(HERE / "fundamentals_updater.py", self.ws / "fundamentals_updater.py")
        r = subprocess.run(["bash", "-e", "-c", script], cwd=str(self.ws), env=env, capture_output=True, text=True)
        outs = dict(line.split("=", 1) for line in out_file.read_text().splitlines() if "=" in line)
        return r.returncode, outs, r.stdout + r.stderr

    def stub_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def written(self):
        return sorted(str(p.relative_to(self.ws)) for d in ("data", "out") for p in (self.ws / d).rglob("*") if p.is_file()) if any((self.ws / d).exists() for d in ("data", "out")) else []

    def daily_run(self, day=BASE, cap=150, provider=None):
        """The fundamentals step of the scheduled run on the recovered workspace (scripted provider)."""
        h = self.h
        FakeDate.now = day
        prov = provider or h.provider
        before = len(prov.requests)
        ps = [mock.patch.object(fu, "CACHE_FILE", self.ws / "data" / "fundamentals_cache.json"), mock.patch.object(fu, "OUT_FILE", self.ws / "out" / "fundamentals.json"),
              mock.patch.object(fu, "MAX_CALLS_PER_RUN", cap), mock.patch.object(fu, "load_instruments", lambda: h.instruments),
              mock.patch.object(fu, "dt", types.SimpleNamespace(date=FakeDate)), mock.patch.object(fu.time, "sleep"),
              mock.patch.object(fu.requests, "Session", lambda: prov),
              mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": "T", "STOCKLENS_UNIVERSE_FILE": str(self.t / "u.json")})]
        with contextlib.ExitStack() as st:
            for p in ps:
                st.enter_context(p)
            with contextlib.redirect_stdout(io.StringIO()):
                fu.main()
        return json.loads((self.ws / "out" / "fundamentals.json").read_text()), len(prov.requests) - before


CHECK, RECOVER, REQUIRE = "Check the restored fundamentals state", "Recover the fundamentals state from the published site (cache miss only)", "Require the saved fundamentals state"
LOOKUP = "Look up which saved cache the restore resolved to (no download)"


def put_state(site, drop=0, keep_output=True):
    """The workspace as a restore leaves it: the saved state (optionally without its first `drop` records) next to the published output."""
    (site.ws / "data").mkdir(exist_ok=True); (site.ws / "out").mkdir(exist_ok=True)
    cache = json.loads((site.t / "data" / "c.json").read_text())
    for k in list(cache["stocks"])[:drop]:
        del cache["stocks"][k]
    (site.ws / "data" / "fundamentals_cache.json").write_text(json.dumps(cache))
    if keep_output:
        (site.ws / "out" / "fundamentals.json").write_bytes(site.published_bytes)
    return cache


class RestoredState(unittest.TestCase):
    """Condition 1: a usable state was restored (and conditions that make a restored state unusable)."""
    def test_a_restored_state_is_never_touched_even_an_empty_one(self):
        s = Site(self); s.stub()
        (s.ws / "data").mkdir(); (s.ws / "data" / "fundamentals_cache.json").write_text('{"stocks": {}}')
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual((rc, outs.get("state")), (0, "restored"), log)
        self.assertEqual(s.stub_calls(), [])                                   # no cache listing, no download
        self.assertEqual((s.ws / "data" / "fundamentals_cache.json").read_text(), '{"stocks": {}}')

    def test_a_complete_restored_state_next_to_its_output_is_accepted_unchanged(self):
        s = Site(self); s.stub(); put_state(s)
        before = (s.ws / "data" / "fundamentals_cache.json").read_bytes()
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual((rc, outs.get("state")), (0, "restored"), log)
        self.assertEqual(s.stub_calls(), [])
        self.assertEqual((s.ws / "data" / "fundamentals_cache.json").read_bytes(), before)

    def test_an_empty_state_next_to_published_rows_is_not_a_valid_empty_cache(self):
        s = Site(self); s.stub()
        (s.ws / "data").mkdir(); (s.ws / "data" / "fundamentals_cache.json").write_text('{"stocks": {}}')
        (s.ws / "out").mkdir(); (s.ws / "out" / "fundamentals.json").write_bytes(s.published_bytes)
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual(rc, 1, log)
        self.assertIn("holds no stock but the saved output has", log)
        self.assertEqual(s.stub_calls(), [])
        self.assertEqual((s.ws / "data" / "fundamentals_cache.json").read_text(), '{"stocks": {}}')

    def test_an_unreadable_restored_state_stops_the_run(self):
        s = Site(self); s.stub()
        (s.ws / "data").mkdir(); (s.ws / "data" / "fundamentals_cache.json").write_text('{"stocks": {"A0000": {}')   # cut off
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual(rc, 1)
        self.assertIn("not valid JSON", log)
        self.assertNotIn("state", outs)

    def test_a_partial_state_next_to_a_larger_output_stops_the_run(self):
        """The state lost records its own output still lists: the next publish would silently drop those stocks (the reduced-universe failure)."""
        n_rows = None
        for drop in (1, 5, 30):                                           # one record, a few, most of them: no threshold, the exact rule
            with self.subTest(drop=drop):
                s = Site(self); s.stub(); cache = put_state(s, drop=drop)
                n_rows = len(s.published["stocks"])
                before = (s.ws / "data" / "fundamentals_cache.json").read_bytes()
                rc, outs, log = s.run_step("update.yml", CHECK)
                self.assertEqual(rc, 1, log)
                self.assertIn("have no usable saved record", log)
                self.assertIn("a partial saved state", log)
                self.assertNotIn("state", outs)
                self.assertEqual(s.stub_calls(), [])                      # nothing recovered, nothing listed
                self.assertEqual((s.ws / "data" / "fundamentals_cache.json").read_bytes(), before)
        self.assertGreater(n_rows, 5)

    def test_a_state_with_more_records_than_rows_is_legitimate(self):
        """Failed, not-listed and held records are in the state but not in the output: that difference is normal, not a partial state."""
        s = Site(self); s.stub(); cache = put_state(s)
        self.assertGreater(len(cache["stocks"]), len(s.published["stocks"]))
        self.assertEqual(s.run_step("update.yml", CHECK)[0], 0)

    def test_a_row_whose_record_cannot_be_published_again_counts_as_lost(self):
        s = Site(self); s.stub(); cache = put_state(s)
        sym = s.published["stocks"][0]["symbol"]
        cache["stocks"][sym]["ratios_fetched"] = None                    # build_rows never publishes a stock without its date
        (s.ws / "data" / "fundamentals_cache.json").write_text(json.dumps(cache))
        rc, _, log = s.run_step("update.yml", CHECK)
        self.assertEqual(rc, 1, log)
        self.assertIn(sym, log)


def lookup(**env):
    return dict({"LOOKUP_OUTCOME": "success", "LOOKUP_KEY": "", "RESTORE_HIT": ""}, **env)


class NoStateInTheWorkspace(unittest.TestCase):
    """Conditions 2-5: no state was restored. The run recovers only after a genuine miss; every other case stops and says what it is."""
    def stops(self, s, env=None, ref_env=None):
        rc, outs, log = s.run_step("update.yml", CHECK, extra_env=dict(env or {}, **(ref_env or {})))
        self.assertEqual(rc, 1, log)
        self.assertNotIn("state", outs)                                    # no output: the recover step (if: state == 'miss') cannot run
        self.assertEqual(s.written(), [])
        self.assertIn("Nothing was published and nothing was changed", log)
        self.assertFalse(any(c.startswith("curl") for c in s.stub_calls()))   # the published file is not even downloaded
        return log

    def test_a_cache_without_the_state_stops_and_says_so(self):
        """Condition 2 (the shadowing case): the newest cache the restore can see belongs to another workflow and holds no state."""
        s = Site(self); s.stub(default=pages_of(["nse-data-shp-77", "nse-data-123456"]))
        log = self.stops(s, lookup(LOOKUP_KEY="nse-data-shp-77", RESTORE_HIT="false"))
        self.assertIn("nse-data-shp-77", log)
        self.assertIn("another workflow", log)
        self.assertIn("shadows the daily state cache", log)
        self.assertIn("Re-running cannot help", log)                       # not the old "re-run later"
        self.assertNotIn("Re-run later", log)
        self.assertEqual(s.stub_calls(), [])                               # decided by the lookup, no listing needed

    def test_a_resolved_state_cache_that_was_not_restored_stops_and_names_both_possibilities(self):
        """Condition 5: the restore resolved to a daily state cache but the workspace has no state (a failed restore, or an empty cache)."""
        s = Site(self); s.stub()
        log = self.stops(s, lookup(LOOKUP_KEY="nse-data-123456"))
        self.assertIn("resolved to the saved cache nse-data-123456", log)
        self.assertIn("the restore failed", log)
        self.assertIn("holds no fundamentals state", log)
        self.assertIn("never recovers over an existing cache and never deletes one", log)
        self.assertNotIn("Re-run later", log)

    def test_no_cache_resolved_but_caches_listed_stops(self):
        """Condition 5 again: nothing was resolved yet the complete list shows caches (the restore failed, or could not see them)."""
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(["nse-data-123456"])})
        log = self.stops(s, lookup())
        self.assertIn("1 saved cache(s) with the prefix nse-data- exist", log)
        self.assertIn("a maintainer must decide", log)

    def test_a_genuine_miss_is_recognised_only_when_the_complete_list_is_empty(self):
        """Condition 3: nothing resolved and nothing listed in any scope a restore can read."""
        s = Site(self); s.stub()
        rc, outs, log = s.run_step("update.yml", CHECK, extra_env=lookup())
        self.assertEqual((rc, outs.get("state")), (0, "miss"), log)
        calls = s.stub_calls()
        self.assertTrue(all("--paginate" in c and "per_page=100" in c and "ref=refs/heads/main" in c for c in calls), calls)
        self.assertFalse(any(re.search(r"[?&]key=", c) for c in calls), calls)            # no `key` filter: its exact-vs-prefix meaning is not documented
        self.assertEqual(len(calls), 1)                                    # ref == default branch: one scope, listed once
        self.assertIn("a genuine cache miss", log)

    def test_the_lookup_not_succeeding_never_counts_as_a_miss(self):
        """Condition 4a: the lookup step failed, was cancelled or skipped: absence of a matched key proves nothing."""
        for outcome in ("failure", "cancelled", "skipped", ""):
            with self.subTest(outcome=outcome):
                s = Site(self); s.stub()
                log = self.stops(s, lookup(LOOKUP_OUTCOME=outcome))
                self.assertIn("could not be looked up", log)
                self.assertIn("re-run this workflow once the GitHub cache service responds", log)
                self.assertEqual(s.stub_calls(), [])

    def test_a_failed_cache_listing_never_counts_as_a_miss(self):
        """Condition 4b: the listing fails (the old test: only 'gh failed' was covered, and only for one scope)."""
        s = Site(self); s.stub(fail_all=True)
        log = self.stops(s, lookup())
        self.assertIn("could not be listed", log)
        self.assertIn("cannot be told apart from a failed restore", log)

    def test_an_incomplete_cache_listing_never_counts_as_a_miss(self):
        """Condition 4c: fewer caches were read than the API says exist (a page missing), or the total is unusable."""
        keys = ["nse-data-hist-%d" % i for i in range(250)]
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(keys)[:2]})            # the third page never arrived
        log = self.stops(s, lookup())
        self.assertIn("incomplete (200 of 250 caches read)", log)
        s = Site(self); s.stub(refs={"refs/heads/main": [{"total_count": 0, "actions_caches": [{"key": "nse-data-1"}]}]})   # more read than counted
        self.assertIn("incomplete (1 of 0 caches read)", self.stops(s, lookup()))
        s = Site(self); s.stub(refs={"refs/heads/main": [{"total_count": 2, "actions_caches": [{"key": "nse-data-1"}]}, {"total_count": 3, "actions_caches": [{"key": "nse-data-2"}]}]})
        self.assertIn("unusable (no single total)", self.stops(s, lookup()))            # the total changed while paging

    def test_the_prefix_is_filtered_client_side_exactly(self):
        """Only keys that start with 'nse-data-' count; every other cache is read (for the total) but never proves anything."""
        others = ["side-historical-1", "side-financial-history-2", "sector-private-5", "nse-data", "xnse-data-3", "NSE-DATA-4", "sensex-history-7", "nse_data-8"]
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(others)})
        rc, outs, log = s.run_step("update.yml", CHECK, extra_env=lookup())
        self.assertEqual((rc, outs.get("state")), (0, "miss"), log)             # caches exist, none in the restore family: a genuine miss
        for key in ("nse-data-shp-9", "nse-data-123456", "nse-data-"):
            with self.subTest(key=key):
                s = Site(self); s.stub(refs={"refs/heads/main": pages_of(others + [key])})
                log = self.stops(s, lookup())
                self.assertIn("1 saved cache(s) with the prefix nse-data- exist", log)

    def test_a_miss_is_proven_across_every_page_of_other_caches(self):
        keys = ["side-historical-%d" % i for i in range(250)]                    # three pages, none of them in the restore family
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(keys)})
        rc, outs, log = s.run_step("update.yml", CHECK, extra_env=lookup())
        self.assertEqual((rc, outs.get("state")), (0, "miss"), log)
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(keys[:200] + ["nse-data-shp-1"])})   # the cache is on the 3rd page
        self.assertIn("1 saved cache(s) with the prefix nse-data- exist", self.stops(s, lookup()))
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(keys + ["nse-data-9"])[:2]})                       # ... but only two pages were read
        self.assertIn("incomplete (200 of 251 caches read)", self.stops(s, lookup()))

    def test_completeness_counts_every_cache_not_only_the_prefix_ones(self):
        """total_count covers all caches of the ref, so the check compares it with everything that was read: a page of other caches missing is incomplete."""
        keys = ["side-market-%d" % i for i in range(150)]
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(keys)[:1]})
        self.assertIn("incomplete (100 of 150 caches read)", self.stops(s, lookup()))

    def test_a_total_that_counts_pages_is_unusable_not_a_miss(self):
        """If total_count were the size of each page (not documented either way), a long list shows several totals: never a miss."""
        pages = [{"total_count": 100, "actions_caches": [{"key": "side-x-%d" % i} for i in range(100)]},
                 {"total_count": 100, "actions_caches": [{"key": "side-y-%d" % i} for i in range(100)]},
                 {"total_count": 50, "actions_caches": [{"key": "side-z-%d" % i} for i in range(50)]}]
        s = Site(self); s.stub(refs={"refs/heads/main": pages})
        self.assertIn("unusable (no single total)", self.stops(s, lookup()))

    def test_a_listing_that_breaks_while_paging_is_never_a_miss(self):
        keys = ["side-historical-%d" % i for i in range(250)]
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(keys)}, fail_after=1)       # the second request fails after a good first page
        log = self.stops(s, lookup())
        self.assertIn("could not be listed", log)

    def test_every_scope_is_filtered_and_judged_separately(self):
        other = {"REF": "refs/heads/feature", "DEFAULT_BRANCH": "main"}
        plain = pages_of(["side-historical-1", "sector-private-2"])
        for name, refs, stop in (("only the dispatch branch has a daily cache", {"refs/heads/feature": pages_of(["nse-data-5"]), "refs/heads/main": plain}, True),
                                 ("only the default branch has a daily cache", {"refs/heads/feature": plain, "refs/heads/main": pages_of(["nse-data-shp-5"])}, True),
                                 ("neither has one", {"refs/heads/feature": plain, "refs/heads/main": plain}, False)):
            with self.subTest(name):
                s = Site(self); s.stub(refs=refs)
                if stop:
                    self.assertIn("saved cache(s) with the prefix nse-data- exist", self.stops(s, lookup(**other)))
                else:
                    rc, outs, log = s.run_step("update.yml", CHECK, extra_env=lookup(**other))
                    self.assertEqual((rc, outs.get("state")), (0, "miss"), log)
        s = Site(self); s.stub(refs={"refs/heads/feature": plain, "refs/heads/main": [{"total_count": 5, "actions_caches": [{"key": "side-a"}]}]})   # incomplete on the second scope
        self.assertIn("The cache list of refs/heads/main is incomplete (1 of 5", self.stops(s, lookup(**other)))
        s = Site(self); s.stub(refs={"refs/heads/main": plain}, fail_refs=["refs/heads/feature"])                                                   # failing on the first scope
        self.assertIn("refs/heads/feature could not be listed", self.stops(s, lookup(**other)))

    def test_a_long_cache_list_is_read_completely_across_pages(self):
        keys = ["nse-data-hist-%d" % i for i in range(250)] + ["nse-data-998877"]       # the daily cache is on the last page
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(keys)})
        log = self.stops(s, lookup())
        self.assertIn("251 saved cache(s) with the prefix nse-data- exist", log)
        self.assertTrue(any("--paginate" in c for c in s.stub_calls()))

    def test_a_dispatch_from_another_branch_lists_its_own_and_the_default_branch(self):
        """The restore reads this ref's caches and the default branch's: the list covers both, so a default-branch cache is never mistaken for a miss."""
        other = {"REF": "refs/heads/feature", "DEFAULT_BRANCH": "main"}
        s = Site(self); s.stub(refs={"refs/heads/main": pages_of(["nse-data-123456"])})
        log = self.stops(s, lookup(**other))
        self.assertIn("(checked: refs/heads/feature refs/heads/main)", log)
        self.assertTrue(any("ref=refs/heads/feature" in c for c in s.stub_calls()) and any("ref=refs/heads/main" in c for c in s.stub_calls()))
        s = Site(self); s.stub()                                                         # nothing on either: a genuine miss from a branch too
        rc, outs, log = s.run_step("update.yml", CHECK, extra_env=lookup(**other))
        self.assertEqual((rc, outs.get("state")), (0, "miss"), log)
        self.assertEqual(len(s.stub_calls()), 2)
        s = Site(self); s.stub(fail_refs=["refs/heads/main"])                          # the default branch's list fails: not a miss
        self.assertIn("refs/heads/main could not be listed", self.stops(s, lookup(**other)))

    def test_no_call_changes_anything(self):
        """Only GET listings: no -X, no DELETE, no cache removal, whatever the outcome."""
        for refs in ({}, {"refs/heads/main": pages_of(["nse-data-1"])}):
            s = Site(self); s.stub(refs=refs)
            s.run_step("update.yml", CHECK, extra_env=lookup())
            for c in s.stub_calls():
                self.assertNotRegex(c, r"-X|--method|DELETE|-f |-F |--input")
        text = (HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        self.assertNotIn("actions/cache/save", text)
        self.assertNotRegex(text, r"gh (api|cache)[^\n]*(-X|--method|delete)")


class WorkflowWiring(unittest.TestCase):
    def test_the_restore_is_identified_and_the_lookup_reads_the_same_caches_without_downloading(self):
        w = yaml.safe_load((HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8"))
        steps = w["jobs"]["build"]["steps"]
        names = [x.get("name") or x.get("run") for x in steps]
        r, lk = step("update.yml", "Restore saved data"), step("update.yml", LOOKUP)
        self.assertEqual((r["uses"], r["id"]), ("actions/cache@v4", "restore"))
        self.assertEqual(lk["uses"], "actions/cache/restore@v4")                                      # the action that has the documented cache-matched-key output
        self.assertIs(lk["with"].pop("lookup-only"), True)                                           # nothing is downloaded or written
        self.assertEqual(lk["with"], r["with"])                                                      # the same path, key and restore-keys: the same resolution
        self.assertIs(lk["continue-on-error"], True)                                                 # its failure is classified, not hidden by a bare failure
        i = names.index("Restore saved data")
        self.assertEqual(names[i:i + 4], ["Restore saved data", LOOKUP, CHECK, RECOVER])
        env = step("update.yml", CHECK)["env"]
        self.assertEqual(env["LOOKUP_KEY"], "${{ steps.lookup.outputs.cache-matched-key }}")
        self.assertEqual(env["LOOKUP_OUTCOME"], "${{ steps.lookup.outcome }}")
        self.assertEqual(env["RESTORE_HIT"], "${{ steps.restore.outputs.cache-hit }}")
        self.assertEqual(env["REF"], "${{ github.ref }}")
        self.assertIn("default_branch", env["DEFAULT_BRANCH"])
        self.assertEqual(env["GH_TOKEN"], "${{ github.token }}")                                    # the token only reaches this step, as an env var
        for x in steps:
            if x.get("name") != CHECK:
                self.assertNotIn("github.token", str(x.get("env", "")) + str(x.get("with", "")))
        for n in (CHECK, RECOVER):                                                                  # no expression is interpolated into a script
            self.assertNotIn("${{", step("update.yml", n)["run"])
        self.assertEqual(step("update.yml", RECOVER)["if"], "steps.fstate.outputs.state == 'miss'")

    def test_the_schedule_the_budget_and_the_permissions_are_unchanged(self):
        text = (HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        base = subprocess.run(["git", "show", "b6c058b:.github/workflows/update.yml"], cwd=str(HERE), capture_output=True, text=True)
        if base.returncode != 0:
            self.skipTest("the base commit is not available")
        w, b = yaml.safe_load(text), yaml.safe_load(base.stdout)
        on = lambda d: d.get("on", d.get(True))
        self.assertEqual(on(w)["schedule"], on(b)["schedule"])
        self.assertEqual(on(w)["schedule"], [{"cron": "15 15 * * 1-5"}, {"cron": "30 4 * * 2-6"}])
        self.assertEqual(step("update.yml", "Update fundamentals")["env"]["FUNDAMENTALS_MAX_CALLS"], "${{ inputs.fundamentals_calls || '150' }}")
        self.assertEqual(w["permissions"], {"contents": "read", "actions": "read"})                  # read-only, and only the one addition
        self.assertEqual(w["concurrency"], b["concurrency"])


class Recovery(unittest.TestCase):
    def test_a_miss_recovers_every_published_record_and_the_next_publish_is_identical(self):
        s = Site(self); s.stub()
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual(outs.get("state"), "miss")
        rc, _, log = s.run_step("update.yml", RECOVER)
        self.assertEqual(rc, 0, log)
        self.assertTrue(any("https://arnavdev11.github.io/stock/out/fundamentals.json" in c for c in s.stub_calls()))
        self.assertEqual((s.ws / "out" / "fundamentals.json").read_bytes(), s.published_bytes)      # the site keeps exactly what it showed
        cache = json.loads((s.ws / "data" / "fundamentals_cache.json").read_text())
        for r in s.published["stocks"]:
            e = cache["stocks"][r["symbol"]]
            self.assertEqual((e["isin"], e["ratios"], e["ratios_fetched"]), (r["isin"], {m: r[m] for m in fu.METRICS}, r["fetched"]))
            self.assertNotIn("profile_fetched", e)                                                   # not published: not invented
            self.assertNotIn("failures", e)
        for x in s.published["errors"]:
            self.assertEqual(cache["stocks"][x["symbol"]]["last_error"], x["error"])
        self.assertTrue(cache["recovered_from_published"]["not_recovered"])
        failed = {x["symbol"] for x in s.published["errors"] if x["error"] != fu.NOT_FOUND_ERROR}
        asked_before = len(s.h.provider.requests)
        out, calls = s.daily_run(day=BASE, cap=150)                                                  # the fundamentals step of the same run
        asked = {u.split("/fundamentals/")[1].split("/")[0] for u in s.h.provider.requests[asked_before:]}
        isin_of = {k: v["isin"] for k, v in s.h.instruments.items()}
        self.assertEqual(asked, {isin_of[x] for x in failed})                # only the stocks the file shows as failed are retried (no back-off state to
        self.assertEqual(calls, 2 * len(failed))                             # recover); the recovered records are fresh: no profile churn, no refetch
        self.assertEqual({r["symbol"]: r for r in out["stocks"]}, {r["symbol"]: r for r in s.published["stocks"]})
        self.assertEqual(out["errors"], s.published["errors"])
        self.assertEqual(sum(out["coverage"]["categories"].values()), out["coverage"]["universe"])
        self.assertEqual(vo.check_fundamentals(out), [])

    def test_without_recovery_the_same_miss_would_publish_a_reduced_universe(self):
        s = Site(self, n=400, letters="A", missing_from_upstox=())
        (s.ws / "data").mkdir()
        out, calls = s.daily_run(day=BASE + D, cap=150)                                              # what main does today after a miss
        self.assertEqual((len(s.published["stocks"]), len(out["stocks"]), calls), (400, 75, 150))

    def test_recovered_records_are_refreshed_normally_and_keep_their_values_on_failure(self):
        s = Site(self, n=20, letters="A", missing_from_upstox=()); s.stub()
        s.run_step("update.yml", RECOVER)
        out, calls = s.daily_run(day=BASE + 7 * D, cap=10)                                          # stale: profile + ratios, 2 calls per stock
        self.assertEqual(calls, 10)
        cache = json.loads((s.ws / "data" / "fundamentals_cache.json").read_text())
        refreshed = [k for k, e in cache["stocks"].items() if e.get("profile_fetched")]
        self.assertEqual(len(refreshed), 5)
        prov = s.h.provider
        prov.get = lambda url, timeout=30: (prov.requests.append(url), Resp(200, {"status": "success", "data": []}))[1]
        out, calls = s.daily_run(day=BASE + 7 * D, cap=10)                                          # empty answers: the recovered values stay
        rows = {r["symbol"]: r for r in out["stocks"]}
        self.assertEqual(len(rows), 20)
        for r in s.published["stocks"]:
            if r["symbol"] not in refreshed:
                self.assertEqual(rows[r["symbol"]], r)

    def test_the_seed_writes_the_state_before_the_output(self):
        s = Site(self); s.stub()
        (s.ws / "out").write_text("not a folder")                                                   # the output cannot be written
        rc, _, log = s.run_step("update.yml", RECOVER)
        self.assertNotEqual(rc, 0)
        cache = json.loads((s.ws / "data" / "fundamentals_cache.json").read_text())               # the state is complete: the next run republishes it all
        self.assertEqual(len(cache["stocks"]), len({r["symbol"] for r in s.published["stocks"]} | {x["symbol"] for x in s.published["errors"]}))

    def test_a_restored_state_is_never_overwritten_by_seed(self):
        s = Site(self); s.stub()
        (s.ws / "data").mkdir(); (s.ws / "data" / "fundamentals_cache.json").write_text('{"stocks": {"X": {}}}')
        rc, _, log = s.run_step("update.yml", RECOVER)                                              # even if someone ran it by hand
        self.assertEqual(rc, 1)
        self.assertIn("seeding never overwrites it", log)
        self.assertEqual((s.ws / "data" / "fundamentals_cache.json").read_text(), '{"stocks": {"X": {}}}')
        self.assertFalse((s.ws / "out").exists())


class StopsInsteadOfPublishingLess(unittest.TestCase):
    def bad_variants(self, site):
        p = site.published
        def mut(f):
            d = copy.deepcopy(p); f(d); return json.dumps(d).encode()
        return {
            "download fails": None,
            "not JSON": b"<html>404</html>",
            "NaN": json.dumps(p).replace("12.5", "NaN", 1).encode(),
            "empty stocks": mut(lambda d: d.update(stocks=[])),
            "other source": mut(lambda d: d.update(source="X")),
            "duplicate row": mut(lambda d: d["stocks"].append(copy.deepcopy(d["stocks"][0]))),
            "row outside universe": mut(lambda d: d["stocks"][0].update(symbol="ZZZZ")),
            "bad ISIN": mut(lambda d: d["stocks"][0].update(isin="123")),
            "future date": mut(lambda d: d["stocks"][0].update(fetched="2099-01-01")),
            "missing list wrong": mut(lambda d: d["stocks"][0].update(missing=["pe"])),
            "metric as text": mut(lambda d: (d["stocks"][0].update(pe="12"))),
            "coverage inconsistent": mut(lambda d: d["coverage"].update(published=1)),
            "no coverage block": mut(lambda d: d.pop("coverage")),
            "rows dropped, coverage kept": mut(lambda d: d.update(stocks=d["stocks"][: len(d["stocks"]) // 2])),   # a reduced file that still describes the full run
            "as_of is not the newest row date": mut(lambda d: d.update(as_of=(BASE - D).isoformat())),
            "stale: every row older than the ratio age": mut(lambda d: (d.update(as_of=(BASE - 9 * D).isoformat()), [r.update(fetched=(BASE - 9 * D).isoformat()) for r in d["stocks"]])),
            "an error entry without text": mut(lambda d: d.update(errors=[{"symbol": "A0001", "error": ""}])),
        }

    def test_an_unavailable_or_invalid_published_file_stops_without_writing_anything(self):
        base = Site(self)
        for name, payload in self.bad_variants(base).items():
            with self.subTest(name):
                s = Site(self)
                s.stub(curl_fails=payload is None, curl_payload=payload)
                rc, _, log = s.run_step("update.yml", RECOVER)
                self.assertEqual(rc, 1, (name, log))
                self.assertEqual(s.written(), [], name)                                              # no state, no output: the job stops, nothing is deployed

    def test_published_symbols_outside_the_universe_keep_the_safe_stop(self):
        """A published stock that is no longer in the universe is not recovered automatically (it would be dropped or kept on a guess): stop, write nothing."""
        s = Site(self)
        d = copy.deepcopy(s.published); d["stocks"][0]["symbol"] = "ZZ9999"; d["stocks"][1]["symbol"] = "ZZ9998"
        s.stub(curl_payload=json.dumps(d).encode())
        rc, _, log = s.run_step("update.yml", RECOVER)
        self.assertEqual(rc, 1, log)
        self.assertIn("stocks outside the universe: ZZ9998, ZZ9999", log)
        self.assertIn("Nothing was changed", log)
        self.assertEqual(s.written(), [])

    def test_a_stale_published_file_is_not_recovered_but_a_week_boundary_file_is_judged_by_the_project_rule(self):
        """RATIOS_MAX_AGE_DAYS (7) is the project's own definition of stale ratios: a file whose newest row is that old is stale throughout."""
        s = Site(self, base=BASE)                                      # newest row 2 days old: current
        s.stub(); self.assertEqual(s.run_step("update.yml", RECOVER)[0], 0)
        old = __import__("datetime").date.today() - fu.RATIOS_MAX_AGE_DAYS * D
        s = Site(self, base=old)                                       # exactly 7 days old: stale by is_stale
        s.stub(); rc, _, log = s.run_step("update.yml", RECOVER)
        self.assertEqual(rc, 1, log)
        self.assertIn("days old (RATIOS_MAX_AGE_DAYS)", log)
        self.assertEqual(s.written(), [])
        s = Site(self, base=old + D)                                   # 6 days: still current
        s.stub(); self.assertEqual(s.run_step("update.yml", RECOVER)[0], 0)

    def test_the_build_stops_before_deploying_when_recovery_fails(self):
        w = yaml.safe_load((HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8"))
        names = [s.get("name") or s.get("run") for s in w["jobs"]["build"]["steps"]]
        i_restore, i_lookup, i_check, i_recover = names.index("Restore saved data"), names.index(LOOKUP), names.index(CHECK), names.index(RECOVER)
        self.assertEqual((i_lookup, i_check, i_recover), (i_restore + 1, i_restore + 2, i_restore + 3))   # right after the restore, before any updater
        self.assertLess(i_recover, names.index("python nse_updater.py"))
        for n in (CHECK, RECOVER):
            self.assertNotIn("continue-on-error", step("update.yml", n))                             # a failure fails the build job ...
        self.assertEqual(w["jobs"]["deploy"]["needs"], "build")                                      # ... and the deploy job needs it
        self.assertNotIn("if", w["jobs"]["deploy"])
        self.assertEqual(w["permissions"], {"contents": "read", "actions": "read"})


class BatchesNeverStartEmpty(unittest.TestCase):
    def test_a_batch_without_saved_state_stops_before_any_call(self):
        s = Site(self)
        rc, _, log = s.run_step("fundamentals_batch.yml", REQUIRE)
        self.assertEqual(rc, 1)
        self.assertIn("No saved fundamentals state was restored (the restore resolved: 'no cache')", log)
        self.assertIn("A batch never recovers the state", log)
        self.assertNotIn("Run (or wait for) the scheduled update first", log)                        # the old advice: that run can itself stop
        rc, _, log = s.run_step("fundamentals_batch.yml", REQUIRE, extra_env={"MATCHED": "nse-data-shp-9"})
        self.assertIn("the restore resolved: 'nse-data-shp-9'", log)                                 # a cache restored without the state is named
        self.assertEqual(step("fundamentals_batch.yml", REQUIRE)["env"]["MATCHED"], "${{ steps.restore.outputs.cache-matched-key }}")
        self.assertEqual(step("fundamentals_batch.yml", "Restore saved data")["id"], "restore")
        (s.ws / "data").mkdir(); (s.ws / "data" / "fundamentals_cache.json").write_text('{"stocks": {}}')
        rc, _, log = s.run_step("fundamentals_batch.yml", REQUIRE)                # an empty state is not a valid start for a batch
        self.assertEqual(rc, 1)
        self.assertIn("holds no stock", log)
        (s.ws / "data" / "fundamentals_cache.json").write_text("{bad")
        self.assertEqual(s.run_step("fundamentals_batch.yml", REQUIRE)[0], 1)
        (s.ws / "data" / "fundamentals_cache.json").write_text('{"stocks": {"A0000": {"isin": "INE000000011"}}}')
        self.assertEqual(s.run_step("fundamentals_batch.yml", REQUIRE)[0], 0)
        st = step("fundamentals_batch.yml", REQUIRE)
        self.assertEqual(st["if"], "inputs.mode != 'audit'")
        w = yaml.safe_load((HERE / ".github" / "workflows" / "fundamentals_batch.yml").read_text(encoding="utf-8"))
        names = [x.get("name") for x in w["jobs"]["batch"]["steps"]]
        self.assertLess(names.index(REQUIRE), names.index("Run the batch"))
        self.assertLess(names.index("Restore saved data"), names.index(REQUIRE))


if __name__ == "__main__":
    unittest.main()
