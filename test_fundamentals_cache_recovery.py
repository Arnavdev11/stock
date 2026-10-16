"""Recovery of the fundamentals state after a cache miss (fundamentals_updater.py seed, and the steps of update.yml / fundamentals_batch.yml that use it).
The workflow steps' own shell scripts are executed here with stub `gh` and `curl` commands (the real `jq` filter is applied), so the decisions
restored / miss / failed restore and the stop-instead-of-publish paths are exercised offline. No network, no provider call.
Run: python3 -m unittest test_fundamentals_cache_recovery"""
import contextlib
import copy
import io
import json
import os
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
BASE = __import__("datetime").date(2026, 10, 10)   # the published state is built on a past day: the seed step runs as a real process on the real date


def step(workflow, name):
    w = yaml.safe_load((HERE / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    for job in w["jobs"].values():
        for s in job.get("steps") or []:
            if s.get("name") == name:
                return s
    raise AssertionError("no step %r in %s" % (name, workflow))


class Site:
    """A published site, a runner workspace and a universe: everything the recovery reads, in one temporary folder."""
    def __init__(self, test, n=40, letters="ABE", missing_from_upstox=("A0004",)):
        self.h = Harness(test, n, letters=letters, missing_from_upstox=missing_from_upstox, cap=10 ** 6)
        self.h.run(day=BASE)                                                    # a normal published state (complete, partial, failed, not listed)
        self.t = self.h.t
        self.published_bytes = (self.t / "out" / "fundamentals.json").read_bytes()
        self.published = json.loads(self.published_bytes)
        self.ws = Path(tempfile.mkdtemp())                              # the runner's workspace after a cache miss: no data/, no out/
        test.addCleanup(lambda: shutil.rmtree(self.ws, ignore_errors=True))
        (self.ws / "site.json").write_bytes(self.published_bytes)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        self.calls = self.ws / "stub_calls.log"

    def stub(self, caches_json=None, gh_fails=False, curl_fails=False, curl_payload=None):
        if curl_payload is not None:
            (self.ws / "site.json").write_bytes(curl_payload)
        (self.bin / "gh").write_text("#!/bin/bash\necho \"gh $*\" >> %s\n%s\njq=''; while [ $# -gt 0 ]; do [ \"$1\" = --jq ] && { jq=\"$2\"; shift; }; shift; done\n"
                                     "echo '%s' | jq -r \"$jq\"\n" % (self.calls, "exit 1" if gh_fails else "", json.dumps(caches_json or {"total_count": 0, "actions_caches": []})))
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
                    "GH_TOKEN": "x", "REPO": "o/r", "REF": "refs/heads/main", "OWNER": "Arnavdev11", "NAME": "stock"})
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


class Classification(unittest.TestCase):
    def test_a_restored_state_is_never_touched_even_an_empty_one(self):
        s = Site(self); s.stub()
        (s.ws / "data").mkdir(); (s.ws / "data" / "fundamentals_cache.json").write_text('{"stocks": {}}')
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual((rc, outs.get("state")), (0, "restored"), log)
        self.assertEqual(s.stub_calls(), [])                                   # no cache listing, no download
        self.assertEqual((s.ws / "data" / "fundamentals_cache.json").read_text(), '{"stocks": {}}')

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

    def test_a_long_cache_list_is_read_completely(self):
        s = Site(self)
        caches = [{"key": "nse-data-hist-%d" % i} for i in range(150)] + [{"key": "nse-data-998877"}]   # the publishing run's cache comes last
        s.stub(caches_json={"total_count": len(caches), "actions_caches": caches})
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual(rc, 1, log)
        self.assertIn("1 saved cache(s) exist", log)
        self.assertTrue(any("--paginate" in c for c in s.stub_calls()))

    def test_a_genuine_miss_is_recognised(self):
        s = Site(self)
        s.stub(caches_json={"total_count": 3, "actions_caches": [{"key": "nse-data-hist-5"}, {"key": "side-historical-6"}, {"key": "nse-data-shp-7"}]})
        rc, outs, log = s.run_step("update.yml", CHECK)                        # only caches of other shapes exist: none of the publishing run's own
        self.assertEqual((rc, outs.get("state")), (0, "miss"), log)
        self.assertTrue(any("ref=refs/heads/main" in c for c in s.stub_calls()))

    def test_a_failed_restore_stops_the_run(self):
        s = Site(self); s.stub(caches_json={"total_count": 1, "actions_caches": [{"key": "nse-data-123456"}]})
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual(rc, 1)
        self.assertIn("a failed restore", log)
        self.assertNotIn("state", outs)
        self.assertEqual(s.written(), [])

    def test_an_unlistable_cache_store_stops_the_run(self):
        s = Site(self); s.stub(gh_fails=True)
        rc, outs, log = s.run_step("update.yml", CHECK)
        self.assertEqual(rc, 1)
        self.assertIn("cannot be told apart from a failed restore", log)
        self.assertEqual(s.written(), [])


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

    def test_the_build_stops_before_deploying_when_recovery_fails(self):
        w = yaml.safe_load((HERE / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8"))
        names = [s.get("name") or s.get("run") for s in w["jobs"]["build"]["steps"]]
        i_restore, i_check, i_recover = names.index("Restore saved data"), names.index(CHECK), names.index(RECOVER)
        self.assertEqual((i_check, i_recover), (i_restore + 1, i_restore + 2))                       # right after the restore, before any updater
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
        self.assertIn("No saved fundamentals state was restored", log)
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
