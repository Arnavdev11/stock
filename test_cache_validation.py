"""
Tests for cache_validation.py and .github/workflows/cache_validation.yml. Offline: no network, no Actions, no provider; every file here is synthetic.

These tests do NOT validate any real cache. They check the controls around the one-off workflow: which cache is chosen, what is refused, and that the public
summary is built from an allowlist and cannot carry a value, identifier, symbol or error text.
"""
import ast
import contextlib
import copy
import datetime as dt
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

import cache_validation as V
import coverage_report as C
import test_cache_isolation as ISO
import test_coverage_report as T

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE / ".github" / "workflows" / "cache_validation.yml"
AS_OF = "2026-10-23"
HOSTILE = ("HOSTILE COMPANY NAME", "HOSTILE SECTOR LABEL", "HOSTILE ERROR TEXT", "INE999999990", "OUTSIDEXYZ")


REF = "refs/heads/main"
NOW = "2026-10-23T12:00:00Z"
DAILY_WF_ID, BATCH_WF_ID = 111, 222
BATCH_PATH = ".github/workflows/fundamentals_batch.yml"


REPOSITORY = "acme/stock"
MARKER_SOURCE_ENV = {"CV_VERSION": "d" * 64, "CV_CACHE_REF": REF, "CV_SOURCE_RUN_ID": "123", "CV_SOURCE_RUN_EVENT": "schedule", "CV_SOURCE_RUN_HEAD_SHA": "e" * 40,
                     "CV_SOURCE_RUN_ATTEMPT": "1", "CV_SOURCE_RUN_CREATED_AT": "2026-10-20T09:50:00Z", "CV_SOURCE_RUN_UPDATED_AT": "2026-10-20T10:20:00Z",
                     "CV_SKIPPED_BATCH_CACHES": "2", "CV_REPOSITORY": REPOSITORY}


def good_marker(files=None, **kw):
    """What cache_provenance.py writes for the run the Provenance fixtures describe (run 123, attempt 1, cache created 10:00)."""
    m = {"schema": 1, "kind": "cache_provenance", "repository": REPOSITORY, "workflow": V.DAILY_WORKFLOW_NAME,
         "workflow_ref": "%s/%s@%s" % (REPOSITORY, V.DAILY_WORKFLOW_PATH, REF), "run_id": "123", "run_attempt": 1, "event": "schedule", "ref": REF, "sha": "e" * 40,
         "cache_key": "nse-data-123", "paths": ["data", "out"], "written_at": "2026-10-20T09:58:00Z", "data_status": "complete", "data_ok": True,
         "step_outcomes": {n: "success" for n in V.MARKER_STEPS}, "files": files or {}}
    m.update(kw)
    return m


def make_data_dir(root, marker=True, contents=None, **marker_kw):
    """A synthetic restored data folder: the three state files and, unless MARKER is False, a marker that matches them (then changed by MARKER_KW)."""
    import hashlib
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    files = {}
    for n in V.STATE_FILES:
        b = (contents or {}).get(n, ('{"synthetic": "%s"}' % n).encode())
        (d / n).write_bytes(b)
        files[n] = {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    if marker:
        m = good_marker(files)
        m.update(marker_kw)
        (d / V.MARKER_NAME).write_text(json.dumps(m), encoding="utf-8")
    return d


def marker_selection():
    return V.selection_from_env(dict(Provenance.OK))[0]


def page(caches, total=None):
    return json.dumps({"total_count": len(caches) if total is None else total, "actions_caches": caches})


def cache(key, created="2026-10-20T10:00:00Z", accessed="2026-10-22T10:00:00Z", size=1000, ref=REF, version="a" * 64):
    return {"id": 1, "ref": ref, "key": key, "version": version, "last_accessed_at": accessed, "created_at": created, "size_in_bytes": size}


def workflow(**kw):
    return dict({"id": DAILY_WF_ID, "name": V.DAILY_WORKFLOW_NAME, "path": V.DAILY_WORKFLOW_PATH, "state": "active"}, **kw)


def daily_run(run_id, **kw):
    return dict({"id": int(run_id), "workflow_id": DAILY_WF_ID, "path": V.DAILY_WORKFLOW_PATH, "head_branch": "main", "head_sha": "c" * 40, "event": "schedule", "status": "completed",
                 "conclusion": "success", "run_attempt": 1, "created_at": "2026-10-20T09:50:00Z", "updated_at": "2026-10-20T10:20:00Z"}, **kw)


def batch_run(run_id, **kw):
    return daily_run(run_id, **dict({"workflow_id": BATCH_WF_ID, "path": BATCH_PATH, "event": "workflow_dispatch"}, **kw))


def select(caches, runs, wf=None, ref=REF, now=NOW, total=None):
    """runs: {run id: dict | str | None}. A dict is serialised; None means the run could not be fetched."""
    texts = {str(k): (None if v is None else v if isinstance(v, str) else json.dumps(v)) for k, v in runs.items()}
    wtext = json.dumps(workflow() if wf is None else wf) if not isinstance(wf, str) else wf
    return V.select_daily_cache(page(caches, total), wtext, texts, ref, now)


class ExactKeyMatching(unittest.TestCase):
    def test_only_exact_nse_data_digit_keys_of_the_ref_are_candidates_newest_first(self):
        keys = ["nse-data-shp-300", "nse-data-sector-301", "nse-data-", "nse-data-12x", "nse-data-1-2", "side-historical-5", "sector-private-9", "NSE-DATA-77",
                "nse-data-1\n", " nse-data-1", "nse-data-1 ", "xnse-data-1", "nse-data-\u0661\u0662", "nse-data-" + "9" * 21]
        caches = [cache(k, "2026-10-22T00:00:00Z") for k in keys] + [cache("nse-data-5", "2026-10-01T00:00:00Z"), cache("nse-data-6", "2026-10-02T00:00:00Z"),
                                                                     cache("nse-data-7", "2026-10-03T00:00:00Z", ref="refs/heads/other")]
        self.assertEqual(V.candidate_run_ids(page(caches), REF), ["6", "5"])

    def test_a_tie_in_time_takes_the_higher_run_id_first(self):
        self.assertEqual(V.candidate_run_ids(page([cache("nse-data-9"), cache("nse-data-10")]), REF), ["10", "9"])

    def test_no_candidate_fails_closed(self):
        for caches in ([], [cache("nse-data-shp-1")], [cache("side-market-2")], [cache("nse-data-3", ref="refs/heads/feature")], [cache("nse-data-4", ref="refs/pull/1/merge")]):
            with self.assertRaises(V.Refused) as cm:
                V.candidate_run_ids(page(caches), REF)
            self.assertEqual(cm.exception.code, "no_cache_with_an_exact_nse_data_key_on_the_ref")

    def test_the_walk_is_limited(self):
        ids = V.candidate_run_ids(page([cache("nse-data-%d" % i, "2026-10-%02dT00:00:00Z" % (1 + i)) for i in range(15)]), REF)
        self.assertEqual(len(ids), V.MAX_CANDIDATES)
        self.assertEqual(ids[0], "14")

    def test_pages_are_joined_and_the_list_must_be_complete(self):
        both = page([cache("nse-data-1")], total=2) + "\n" + page([cache("nse-data-2", "2026-10-21T00:00:00Z")], total=2)
        self.assertEqual(V.candidate_run_ids(both, REF), ["2", "1"])
        for bad in (page([cache("nse-data-1")], total=3), page([cache("nse-data-1")], total=0), page([cache("nse-data-1")], total=2) + page([cache("nse-data-2")], total=3)):
            with self.assertRaises(V.Refused) as cm:
                V.candidate_run_ids(bad, REF)
            self.assertEqual(cm.exception.code, "cache_list_incomplete")

    def test_unusable_lists_are_refused(self):
        cases = {"": "cache_list_empty_response", "   \n": "cache_list_empty_response", "not json": "cache_list_not_json", "[]": "cache_list_unexpected_shape",
                 '{"total_count": 1}': "cache_list_unexpected_shape", '{"total_count": "1", "actions_caches": []}': "cache_list_unexpected_shape",
                 '{"total_count": true, "actions_caches": []}': "cache_list_unexpected_shape", "[" * 100000: "cache_list_not_json",
                 page(["nse-data-1"]): "cache_list_unexpected_shape", page([{"id": 1}]): "cache_list_unexpected_shape"}
        for text, code in cases.items():
            with self.subTest(text=text[:30]):
                with self.assertRaises(V.Refused) as cm:
                    V.candidate_run_ids(text, REF)
                self.assertEqual(cm.exception.code, code)

    def test_a_candidate_with_bad_metadata_is_refused_not_skipped(self):
        for bad in (cache("nse-data-1", created="yesterday"), cache("nse-data-1", created="2026-10-20T10:00:00"), cache("nse-data-1", size=-1), cache("nse-data-1", version="short"),
                    dict(cache("nse-data-1"), version=None), dict(cache("nse-data-1"), size_in_bytes="9"), dict(cache("nse-data-1"), size_in_bytes=True),
                    dict(cache("nse-data-1"), created_at=None)):
            with self.subTest(bad=str(bad)[:60]):
                with self.assertRaises(V.Refused):
                    V.candidate_run_ids(page([bad]), REF)

    def test_a_missing_access_time_falls_back_to_the_creation_time(self):
        c = select([dict(cache("nse-data-1"), last_accessed_at=None)], {1: daily_run(1)})
        self.assertEqual(c["last_accessed_at"], c["created_at"])

    def test_the_same_key_twice_on_the_ref_is_ambiguous(self):
        with self.assertRaises(V.Refused) as cm:
            select([cache("nse-data-1"), cache("nse-data-1", version="b" * 64)], {1: daily_run(1)})
        self.assertEqual(cm.exception.code, "cache_key_listed_more_than_once")
        select([cache("nse-data-1"), cache("nse-data-1", ref="refs/heads/other", version="b" * 64)], {1: daily_run(1)})     # another branch's copy is not this ref's

    def test_the_ref_must_be_a_branch(self):
        for ref in ("refs/pull/1/merge", "main", "refs/heads/", "refs/heads/a b", "refs/tags/v1"):
            with self.subTest(ref=ref):
                with self.assertRaises(V.Refused) as cm:
                    V.candidate_run_ids(page([cache("nse-data-1")]), ref)
                self.assertEqual(cm.exception.code, "ref_not_a_branch")


class DailyWorkflowIdentification(unittest.TestCase):
    def test_a_cache_of_a_successful_daily_run_is_selected_with_its_origin(self):
        c = select([cache("nse-data-200")], {200: daily_run(200, event="workflow_dispatch")})
        self.assertEqual((c["key"], c["run_id"], c["run_event"], c["run_head_sha"], c["ref"], c["version"], c["skipped_batch_caches"]),
                         ("nse-data-200", "200", "workflow_dispatch", "c" * 40, REF, "a" * 64, 0))
        self.assertEqual(c["size_in_bytes"], 1000)

    def test_a_newer_fundamentals_batch_cache_is_excluded_and_counted(self):
        caches = [cache("nse-data-300", "2026-10-22T10:00:00Z"), cache("nse-data-301", "2026-10-22T12:00:00Z"), cache("nse-data-200", "2026-10-20T10:00:00Z")]
        runs = {300: batch_run(300, created_at="2026-10-22T09:50:00Z", updated_at="2026-10-22T10:20:00Z"),
                301: batch_run(301, created_at="2026-10-22T11:50:00Z", updated_at="2026-10-22T12:20:00Z"), 200: daily_run(200)}
        c = select(caches, runs)
        self.assertEqual((c["key"], c["skipped_batch_caches"]), ("nse-data-200", 2))

    def test_the_batch_workflow_path_with_a_ref_suffix_is_still_the_batch(self):
        c = select([cache("nse-data-300", "2026-10-22T10:00:00Z"), cache("nse-data-200")], {300: batch_run(300, path=BATCH_PATH + "@refs/heads/main"), 200: daily_run(200)})
        self.assertEqual(c["key"], "nse-data-200")

    def test_only_batch_caches_means_no_daily_cache(self):
        with self.assertRaises(V.Refused) as cm:
            select([cache("nse-data-300"), cache("nse-data-301", "2026-10-21T00:00:00Z")], {300: batch_run(300), 301: batch_run(301)})
        self.assertEqual(cm.exception.code, "no_daily_cache_among_the_newest_candidates")

    def test_a_daily_cache_beyond_the_walk_limit_is_not_reached(self):
        caches = [cache("nse-data-%d" % (500 + i), "2026-10-22T%02d:00:00Z" % i) for i in range(V.MAX_CANDIDATES)] + [cache("nse-data-200", "2026-10-20T10:00:00Z")]
        runs = {500 + i: batch_run(500 + i, created_at="2026-10-22T00:00:00Z", updated_at="2026-10-22T23:59:00Z") for i in range(V.MAX_CANDIDATES)}
        runs[200] = daily_run(200)
        with self.assertRaises(V.Refused) as cm:
            select(caches, runs)
        self.assertEqual(cm.exception.code, "no_daily_cache_among_the_newest_candidates")

    def refuses(self, code, caches, runs, **kw):
        with self.assertRaises(V.Refused) as cm:
            select(caches, runs, **kw)
        self.assertEqual(cm.exception.code, code)

    def test_missing_run_metadata_stops_the_run_it_does_not_skip_to_an_older_cache(self):
        caches = [cache("nse-data-300", "2026-10-22T10:00:00Z"), cache("nse-data-200")]
        self.refuses("run_metadata_missing", caches, {300: None, 200: daily_run(200)})
        self.refuses("run_metadata_missing", caches, {200: daily_run(200)})

    def test_ambiguous_or_inconsistent_metadata_stops_the_run(self):
        c = [cache("nse-data-200")]
        self.refuses("run_metadata_id_mismatch", c, {200: daily_run(201)})
        self.refuses("run_workflow_identity_inconsistent", c, {200: daily_run(200, workflow_id=999)})
        self.refuses("run_workflow_identity_inconsistent", c, {200: daily_run(200)}, wf=workflow(id=999))
        self.refuses("run_from_an_unexpected_workflow", c, {200: daily_run(200, path=".github/workflows/shareholding.yml")})
        self.refuses("run_from_an_unexpected_workflow", c, {200: daily_run(200, path="update.yml")})
        self.refuses("run_from_an_unexpected_workflow", c, {200: daily_run(200, path=".github/workflows/update.yml.bak")})

    def test_the_daily_workflow_itself_must_be_confirmed(self):
        c, r = [cache("nse-data-200")], {200: daily_run(200)}
        for bad in (workflow(name="Update shareholding pattern ledger"), workflow(path=".github/workflows/other.yml"), workflow(state="disabled_manually"), workflow(id="111"),
                    workflow(id=True), {"id": 111}):
            with self.subTest(bad=str(bad)[:50]):
                self.refuses("daily_workflow_identity_not_confirmed", c, r, wf=bad)
        for bad in ("not json", "[]", "null", "", "[" * 100000):
            with self.subTest(bad=bad[:10]):
                self.refuses("workflow_metadata_unreadable", c, r, wf=bad)

    def test_wrong_branch(self):
        self.refuses("run_on_another_branch", [cache("nse-data-200")], {200: daily_run(200, head_branch="feature")})
        self.refuses("run_on_another_branch", [cache("nse-data-200")], {200: daily_run(200, head_branch=None)})
        self.refuses("no_cache_with_an_exact_nse_data_key_on_the_ref", [cache("nse-data-200", ref="refs/heads/feature")], {200: daily_run(200, head_branch="feature")})
        self.refuses("no_cache_with_an_exact_nse_data_key_on_the_ref", [cache("nse-data-200", ref="refs/pull/3/merge")], {200: daily_run(200)})

    def test_an_unsuccessful_run_is_refused_not_skipped(self):
        for status, conclusion in (("completed", "failure"), ("completed", "cancelled"), ("completed", "skipped"), ("completed", None), ("in_progress", None), ("queued", "success"),
                                   ("in_progress", "success")):
            with self.subTest(status=status, conclusion=conclusion):
                caches = [cache("nse-data-300", "2026-10-22T10:00:00Z"), cache("nse-data-200")]
                self.refuses("run_not_successful", caches, {300: daily_run(300, status=status, conclusion=conclusion, created_at="2026-10-22T09:50:00Z", updated_at="2026-10-22T10:20:00Z"),
                                                            200: daily_run(200)})

    def test_only_scheduled_or_manual_daily_runs_count(self):
        for ev in ("push", "pull_request", "workflow_run", None):
            self.refuses("run_event_not_expected", [cache("nse-data-200")], {200: daily_run(200, event=ev)})

    def test_a_stale_future_or_foreign_timed_cache(self):
        run = {200: daily_run(200)}
        self.refuses("cache_too_old", [cache("nse-data-200")], run, now="2026-10-27T10:00:01Z")
        select([cache("nse-data-200")], run, now="2026-10-27T10:00:00Z")                         # exactly 7 days is still fresh
        self.refuses("cache_from_the_future", [cache("nse-data-200")], run, now="2026-10-20T09:00:00Z")
        self.refuses("cache_created_outside_the_run", [cache("nse-data-200", "2026-10-20T09:40:00Z")], run)
        self.refuses("cache_created_outside_the_run", [cache("nse-data-200", "2026-10-20T10:26:00Z")], run)
        select([cache("nse-data-200", "2026-10-20T10:24:00Z")], run)                             # within the 5 minute slack after the run's last update

    def test_unexpected_run_shapes_fail_closed(self):
        c = [cache("nse-data-200")]
        shape = "run_metadata_unexpected_shape"
        for bad in (dict(daily_run(200), path=None), dict(daily_run(200), workflow_id="111"), dict(daily_run(200), workflow_id=True), dict(daily_run(200), id="200"),
                    dict(daily_run(200), head_sha="abc"), dict(daily_run(200), head_sha=None), dict(daily_run(200), run_attempt=0), dict(daily_run(200), run_attempt=True),
                    "not json", "[]", "[" * 100000):
            with self.subTest(bad=str(bad)[:50]):
                self.refuses(shape, c, {200: bad})
        for bad in (dict(daily_run(200), created_at="soon"), dict(daily_run(200), updated_at=None), dict(daily_run(200), created_at="2026-10-20T09:50:00")):
            with self.subTest(bad=str(bad)[:50]):
                self.refuses("metadata_bad_date", c, {200: bad})
        with self.assertRaises(V.Refused):
            select(c, {200: daily_run(200)}, now="yesterday")

    def test_the_numeric_key_alone_is_never_enough(self):
        # a key that looks right but whose run is not a daily run, or whose run does not contain the cache, is not accepted
        self.refuses("run_metadata_missing", [cache("nse-data-200")], {})
        self.refuses("cache_created_outside_the_run", [cache("nse-data-200", "2026-10-01T00:00:00Z")], {200: daily_run(200)}, now="2026-10-02T00:00:00Z")

    def test_the_repository_facts_the_rule_rests_on_still_hold(self):
        docs = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in (HERE / ".github" / "workflows").glob("*.yml")}
        self.assertEqual(docs["update.yml"]["name"], V.DAILY_WORKFLOW_NAME)
        self.assertTrue((HERE / V.DAILY_WORKFLOW_PATH).is_file())
        for path in V.EXCLUDED_WORKFLOW_PATHS:
            self.assertTrue((HERE / path).is_file())
        saving = set()
        for name, doc in docs.items():
            for _, kind, key, _, _, _ in ISO.caches(doc):
                if kind in ("both", "save") and V.CACHE_KEY_RE.fullmatch(key.replace("${{ github.run_id }}", "123")):
                    saving.add(name)
        # exactly the daily workflow and the batch name their caches nse-data-<run id>: that is the ambiguity this selection resolves. A third one must be handled here.
        self.assertEqual(saving, {"update.yml", "fundamentals_batch.yml"})
        self.assertEqual({"/".join(("", ".github/workflows", n)).lstrip("/") for n in ("update.yml",)} | set(V.EXCLUDED_WORKFLOW_PATHS),
                         {V.DAILY_WORKFLOW_PATH, ".github/workflows/fundamentals_batch.yml"})


class StateFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def put_all(self):
        for n in V.STATE_FILES:
            (self.d / n).write_text("{}", encoding="utf-8")

    def test_all_three_present_passes(self):
        self.put_all()
        V.check_files(self.d)

    def test_each_missing_file_is_refused_by_kind_only(self):
        for n in V.STATE_FILES:
            with self.subTest(missing=n):
                self.put_all()
                (self.d / n).unlink()
                with self.assertRaises(V.Refused) as cm:
                    V.check_files(self.d)
                self.assertEqual(cm.exception.code, "state_file_missing_" + n.split("_")[0])

    def test_empty_directory_symlink_are_refused(self):
        self.put_all()
        (self.d / V.STATE_FILES[1]).write_text("", encoding="utf-8")
        with self.assertRaises(V.Refused) as cm:
            V.check_files(self.d)
        self.assertEqual(cm.exception.code, "state_file_empty_financials")
        (self.d / V.STATE_FILES[1]).unlink()
        (self.d / V.STATE_FILES[1]).mkdir()
        with self.assertRaises(V.Refused):
            V.check_files(self.d)
        (self.d / V.STATE_FILES[1]).rmdir()
        (self.d / "elsewhere.json").write_text("{}", encoding="utf-8")
        (self.d / V.STATE_FILES[1]).symlink_to(self.d / "elsewhere.json")
        with self.assertRaises(V.Refused):
            V.check_files(self.d)

    def test_the_published_output_is_not_the_state(self):
        self.put_all()
        (self.d / V.STATE_FILES[1]).unlink()
        (self.d / "financials.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(V.Refused):
            V.check_files(self.d)

    def test_file_facts_are_a_size_and_a_hash_only(self):
        self.put_all()
        f = V.file_facts(self.d)
        self.assertEqual(set(f), set(V.STATE_FILES))
        self.assertEqual(set(f[V.STATE_FILES[0]]), {"bytes", "sha256"})
        self.assertEqual(f[V.STATE_FILES[0]]["bytes"], 2)


class RealReportCase(unittest.TestCase):
    """A real coverage report (made by the merged tool from SYNTHETIC state full of hostile strings), then the allowlist."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data, self.state, self.out = self.root / "branch", self.root / "state", self.root / "report"
        self.data.mkdir()
        self.state.mkdir()
        syms = [T.sym(i) for i in range(10)]
        T.write(self.data / "universe.json", T.universe_doc(syms))
        T.write(self.data / "stock_directory.json", T.directory_doc(syms))
        hostile = {"company_name": HOSTILE[0], "sector": HOSTILE[1]}
        T.write(self.state / "fundamentals_cache.json", {"stocks": {
            T.sym(0): dict(T.f_entry(0), **hostile), T.sym(1): dict(T.f_entry(1, "2026-10-10"), isin=HOSTILE[3], **hostile),
            T.sym(4): {"last_error": HOSTILE[2], "last_attempt": "2026-10-22"}, HOSTILE[4]: dict(T.f_entry(2), **hostile)}})
        T.write(self.state / "quarterly_cache.json", T.q_doc({T.sym(0): T.q_entry(), T.sym(5): T.q_entry(quarters={}, problem=HOSTILE[2], failures=2),
                                                              HOSTILE[4]: T.q_entry()}))
        T.write(self.state / "financials_cache.json", {"stocks": {T.sym(0): T.fin_entry(0), T.sym(4): {"last_error": HOSTILE[2], "income": {"error": HOSTILE[2], "attempted": "2026-10-22", "failures": 1}},
                                                                  T.sym(1): dict(T.fin_entry(1), isin=HOSTILE[3])}})
        self.report = C.build_report(self.data, T.AS_OF, "both", None, self.state)

    def prov(self, **kw):
        p = {"CV_MATCHED_KEY": "nse-data-123", "CV_RESTORE_HIT": "true", "CV_RUN_ID": "456", "CV_MAIN_SHA": "a" * 40, "CV_DATA_BRANCH_SHA": "b" * 40, "CV_AS_OF": AS_OF,
             "CV_CREATED_AT": "2026-10-20T10:00:00Z", "CV_LAST_ACCESSED_AT": "2026-10-22T10:00:00Z", "CV_SIZE": "1234", **Provenance.SOURCE}
        p.update(kw)
        return p

    def render(self, report=None):
        s = V.sanitize(report if report is not None else self.report, AS_OF)
        return s, V.render(s, V.provenance_from_env(self.prov()), {n: {"bytes": 1, "sha256": "c" * 64} for n in V.STATE_FILES},
                                 {"written_at": "2026-10-20T09:58:00Z", "run_attempt": 1, "data_status": "complete", "feeding": {n: "success" for n in V.FEEDING_STEPS}})


class Allowlist(RealReportCase):
    def test_the_synthetic_report_really_holds_the_hostile_values(self):
        raw = C.render_json(self.report)
        self.assertIn(HOSTILE[4], raw)                                  # the outside-universe list carries a hostile key
        self.assertIn("S0001", raw)                                     # symbols and ISIN-mismatch lists are in the full report
        self.assertNotIn(HOSTILE[3], raw)                               # (the report itself never writes an ISIN value; the allowlist does not rely on that)

    def test_no_hostile_value_symbol_identifier_or_error_reaches_the_summary(self):
        s, text = self.render()
        both = json.dumps(s) + text
        for bad in HOSTILE + ("S0000", "S0001", "S0004", "S0005", "INE", "PROVIDER"):
            self.assertNotIn(bad, both, bad)
        for key in ("company", "sector", '"isin"', "last_error", "reconciliation", '"symbols"', '"identifiers"', '"message"', '"source_paths"', '"path"', '"details"'):
            self.assertNotIn(key, json.dumps(s), key)               # no such field in the allowlisted object (the prose of the Markdown may use some words)
        self.assertIsNone(re.search(r"\bS\d{4}\b", both))

    def test_the_summary_keeps_the_aggregates_that_matter(self):
        s, text = self.render()
        q = s["scopes"]["all"]["modules"]["quarterly_results"]
        self.assertEqual(q["denominator"], 10)
        self.assertEqual(sum(v for v in q["counts"].values() if v is not None), 10)
        self.assertEqual(q["facts"]["stocks_with_failed_requests"], 1)
        self.assertIn("failed_last_attempt", q["missing_reasons"])
        f = s["scopes"]["all"]["modules"]["fundamentals_state"]
        self.assertIn("isin_differs_from_universe", f["facts"]["unverified_reasons"])
        self.assertEqual(set(s["state_files"]), set(V.STATE_FILES))
        self.assertTrue(all(v["usable"] for v in s["state_files"].values()))
        self.assertIn("cache coverage, not publication status", text)
        self.assertIn("nse-data-123", text)

    def test_only_the_three_state_modules_appear(self):
        s, _ = self.render()
        for label in V.SCOPES:
            self.assertEqual(list(s["scopes"][label]["modules"]), list(V.STATE_MODULES))
        self.assertEqual(set(s), {"generated_for", "state_files", "scopes"})

    def test_extra_fields_are_dropped_not_passed_through(self):
        r = copy.deepcopy(self.report)
        for m in r["scopes"]["all"]["modules"]:
            m["company_name"] = HOSTILE[0]
            m["facts"]["raw_record"] = {"sector": HOSTILE[1]}
            m["unverified"] = [{"reason": HOSTILE[2]}]
        r["raw_cache"] = HOSTILE[3]
        r["scopes"]["all"]["extra"] = HOSTILE[0]
        s, text = self.render(r)
        for bad in HOSTILE:
            self.assertNotIn(bad, json.dumps(s) + text)

    def test_a_problem_text_is_cut_to_its_one_word_code(self):
        r = copy.deepcopy(self.report)
        r["state_files"]["quarterly_cache.json"] = {"usable": False, "problem": "unsupported_schema: %s %s" % (HOSTILE[3], HOSTILE[0]), "entries": None}
        r["state_files"]["financials_cache.json"] = {"usable": False, "problem": "Has Spaces And " + HOSTILE[1], "entries": None}
        s, text = self.render(r)
        self.assertEqual(s["state_files"]["quarterly_cache.json"]["problem"], "unsupported_schema")
        self.assertEqual(s["state_files"]["financials_cache.json"]["problem"], "withheld")
        for bad in HOSTILE:
            self.assertNotIn(bad, text)

    def test_warning_messages_and_symbol_lists_are_dropped_and_only_codes_and_counts_remain(self):
        r = copy.deepcopy(self.report)
        w = [x for m in r["scopes"]["all"]["modules"] for x in m["warnings"]]
        self.assertTrue(w and all("message" in x for x in w))
        s, text = self.render(r)
        self.assertEqual(set(k for sc in s["scopes"].values() for m in sc["modules"].values() for x in m["warnings"] for k in x), {"code", "count"})

    def test_an_unusable_state_file_is_reported_as_unverified_never_as_zero_coverage(self):
        (self.state / "quarterly_cache.json").write_text("{not json " + HOSTILE[0], encoding="utf-8")
        r = C.build_report(self.data, T.AS_OF, "both", None, self.state)
        s, text = self.render(r)
        q = s["scopes"]["all"]["modules"]["quarterly_results"]
        self.assertEqual((q["measurability"], q["counts"]["unverified"], q["counts"]["valid"]), ("not_measurable", 10, None))
        self.assertFalse(s["state_files"]["quarterly_cache.json"]["usable"])
        self.assertEqual(s["state_files"]["quarterly_cache.json"]["problem"], "invalid_json")
        self.assertNotIn(HOSTILE[0], text)


class SchemaFailsClosed(RealReportCase):
    def refuses(self, report, code=None):
        with self.assertRaises(V.Refused) as cm:
            V.sanitize(report, AS_OF)
        if code:
            self.assertEqual(cm.exception.code, code)

    def mods(self, r, label="all"):
        return {m["module"]: m for m in r["scopes"][label]["modules"]}

    def test_wrong_kind_version_date_or_shape(self):
        for k, v in (("kind", "other"), ("schema_version", 999), ("generated_for", "2026-10-22")):
            r = copy.deepcopy(self.report)
            r[k] = v
            self.refuses(r)
        for bad in (None, [], "x", 5):
            self.refuses(bad)
        r = copy.deepcopy(self.report)
        del r["state_files"]
        self.refuses(r, "report_has_no_state_files_block")
        r = copy.deepcopy(self.report)
        r["state_files"]["extra.json"] = {"usable": True}
        self.refuses(r, "report_has_no_state_files_block")
        r = copy.deepcopy(self.report)
        r["state_files"]["quarterly_cache.json"]["usable"] = "yes"
        self.refuses(r, "report_bad_state_files_block")
        r = copy.deepcopy(self.report)
        del r["scopes"]["first_500"]
        self.refuses(r, "report_unexpected_scopes")

    def test_a_missing_or_repeated_state_module(self):
        r = copy.deepcopy(self.report)
        r["scopes"]["all"]["modules"] = [m for m in r["scopes"]["all"]["modules"] if m["module"] != "fundamentals_state"]
        self.refuses(r, "report_state_modules_missing")
        r = copy.deepcopy(self.report)
        r["scopes"]["all"]["modules"].append(copy.deepcopy(self.mods(r)["quarterly_results"]))
        self.refuses(r, "report_repeated_module")

    def test_counts_must_be_plain_non_negative_integers_that_add_up(self):
        def mutate(f):
            r = copy.deepcopy(self.report)
            f(self.mods(r)["quarterly_results"])
            return r
        self.refuses(mutate(lambda m: m["counts"].update(valid=True)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=-1)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid="3")), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=1.5)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=10 ** 9)), "report_bad_number")
        self.refuses(mutate(lambda m: m["counts"].update(valid=(m["counts"]["valid"] or 0) + 1)), "report_counts_do_not_add_up")
        self.refuses(mutate(lambda m: m["counts"].pop("stale")), "report_bad_counts")
        self.refuses(mutate(lambda m: m["counts"].update(extra=1)), "report_bad_counts")
        self.refuses(mutate(lambda m: m["denominator"].update(value=99)), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(measurability="complete")), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(coverage_pct=float("nan"))), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(coverage_pct=150)), "report_bad_number")

    def test_reason_names_must_be_plain_tokens(self):
        for key in ("S0001 has a problem", "INE999999990", "Has Capitals", "1starts_with_digit", "with-dash", "", "x" * 81, "a\nb", HOSTILE[0]):
            r = copy.deepcopy(self.report)
            self.mods(r)["quarterly_results"]["missing_reasons"][key] = 1
            with self.subTest(key=key[:20]):
                self.refuses(r, "report_bad_reason_name")
        r = copy.deepcopy(self.report)
        self.mods(r)["quarterly_results"]["invalid_reasons"] = {"r%d" % i: 1 for i in range(41)}
        self.refuses(r, "report_bad_reason_counts")
        r = copy.deepcopy(self.report)
        self.mods(r)["quarterly_results"]["missing_reasons"] = ["failed_last_attempt"]
        self.refuses(r, "report_bad_reason_counts")
        r = copy.deepcopy(self.report)
        self.mods(r)["fundamentals_state"]["facts"]["unverified_reasons"]["BAD KEY"] = 1
        self.refuses(r, "report_bad_reason_name")

    def test_facts_warnings_and_problems_must_have_the_reviewed_types(self):
        def mutate(f):
            r = copy.deepcopy(self.report)
            f(self.mods(r)["fundamentals_state"])
            return r
        self.refuses(mutate(lambda m: m["facts"].update(stocks_with_ratios="many")), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(facts=[])), "report_bad_module")
        self.refuses(mutate(lambda m: m["facts"].pop("state_file")), "report_bad_module")
        self.refuses(mutate(lambda m: m["facts"]["state_file"].update(usable="yes")), "report_bad_module")
        self.refuses(mutate(lambda m: m["facts"]["state_file"].update(entries=None)), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(warnings="none")), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(warnings=[{"code": "Has Spaces", "count": 1}])), "report_bad_warning")
        self.refuses(mutate(lambda m: m.update(warnings=[{"code": "ok_code", "count": "1"}])), "report_bad_number")
        self.refuses(mutate(lambda m: m.update(warnings=[{"code": "ok_code", "count": 1}] * 21)), "report_bad_module")
        self.refuses(mutate(lambda m: m.update(problems="none")), "report_bad_module")


class Provenance(unittest.TestCase):
    SOURCE = {"CV_VERSION": "d" * 64, "CV_CACHE_REF": REF, "CV_SOURCE_RUN_ID": "123", "CV_SOURCE_RUN_EVENT": "schedule", "CV_SOURCE_RUN_HEAD_SHA": "e" * 40, "CV_SOURCE_RUN_ATTEMPT": "1",
              "CV_SOURCE_RUN_CREATED_AT": "2026-10-20T09:50:00Z", "CV_SOURCE_RUN_UPDATED_AT": "2026-10-20T10:20:00Z", "CV_SKIPPED_BATCH_CACHES": "2", "CV_REPOSITORY": REPOSITORY}
    OK = {"CV_MATCHED_KEY": "nse-data-123", "CV_RESTORE_HIT": "true", "CV_RUN_ID": "456", "CV_MAIN_SHA": "a" * 40, "CV_DATA_BRANCH_SHA": "b" * 40, "CV_AS_OF": AS_OF,
          "CV_CREATED_AT": "2026-10-20T10:00:00Z", "CV_LAST_ACCESSED_AT": "2026-10-22T10:00:00.123Z", "CV_SIZE": "1234", **SOURCE}

    def test_a_good_set_passes_and_any_odd_value_is_refused(self):
        self.assertEqual(V.provenance_from_env(self.OK), self.OK)
        for name, bad in (("CV_MATCHED_KEY", "nse-data-shp-1"), ("CV_MATCHED_KEY", "nse-data-1\n"), ("CV_RESTORE_HIT", "false"), ("CV_RUN_ID", "12 34"), ("CV_MAIN_SHA", "A" * 40),
                          ("CV_DATA_BRANCH_SHA", "b" * 39), ("CV_AS_OF", "yesterday"), ("CV_CREATED_AT", "<script>"), ("CV_SIZE", "12.5"), ("CV_SIZE", ""), ("CV_SOURCE_RUN_EVENT", "push"), ("CV_SOURCE_RUN_HEAD_SHA", "zz"), ("CV_VERSION", "d" * 63),
                          ("CV_CACHE_REF", "refs/pull/1/merge"), ("CV_SKIPPED_BATCH_CACHES", "-1"), ("CV_SOURCE_RUN_ATTEMPT", "x"), ("CV_REPOSITORY", "no-slash"), ("CV_REPOSITORY", "a/b\n")):
            with self.subTest(name=name, bad=bad):
                with self.assertRaises(V.Refused):
                    V.provenance_from_env(dict(self.OK, **{name: bad}))
        env = dict(self.OK)
        del env["CV_RUN_ID"]
        with self.assertRaises(V.Refused):
            V.provenance_from_env(env)


class MarkerVerification(unittest.TestCase):
    """The cache's own provenance marker must agree with the run the cache was selected from, and with the three restored files. All data here is synthetic."""
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sel = marker_selection()

    def verify(self, d, sel=None, repository=REPOSITORY, ref=REF):
        return V.verify_marker(d, sel or self.sel, repository, ref)

    def refuses(self, d, code, **kw):
        with self.assertRaises(V.Refused) as cm:
            self.verify(d, **kw)
        self.assertEqual(cm.exception.code, code)

    def case(self, name, code, **marker_kw):
        with self.subTest(name=name):
            d = make_data_dir(self.root / name, **marker_kw)
            self.refuses(d, code)

    def test_a_valid_marker_passes_and_returns_only_the_allowed_facts(self):
        facts = self.verify(make_data_dir(self.root / "ok"))
        self.assertEqual(facts, {"written_at": "2026-10-20T09:58:00Z", "run_attempt": 1, "data_status": "complete", "feeding": {n: "success" for n in V.FEEDING_STEPS}})

    def test_an_incomplete_but_ok_status_passes_and_is_reported_as_incomplete(self):
        facts = self.verify(make_data_dir(self.root / "inc", data_status="incomplete"))
        self.assertEqual(facts["data_status"], "incomplete")

    def test_a_missing_marker_is_refused(self):
        self.refuses(make_data_dir(self.root / "none", marker=False), "marker_missing")

    def test_a_marker_from_an_earlier_run_or_with_the_wrong_run_id_is_refused(self):
        self.case("old_run", "marker_wrong_run_id", run_id="122", cache_key="nse-data-122")
        self.case("run_id_only", "marker_wrong_run_id", run_id="124")
        self.case("key_only", "marker_wrong_cache_key", cache_key="nse-data-124")
        self.case("run_id_int", "marker_wrong_run_id", run_id=123)

    def test_attempts_other_than_the_runs_latest_are_refused(self):
        sel = dict(self.sel, run_attempt=2)
        self.refuses(make_data_dir(self.root / "rerun", run_attempt=1), "marker_attempt_not_the_latest", sel=sel)
        self.verify(make_data_dir(self.root / "same", run_attempt=2), sel=sel)
        self.refuses(make_data_dir(self.root / "higher", run_attempt=3), "marker_attempt_not_the_latest", sel=sel)
        for bad in (0, -1, "1", True, 1.0, None):
            with self.subTest(attempt=bad):
                self.refuses(make_data_dir(self.root / ("a%r" % (bad,)).replace(".", "_").replace("-", "m"), run_attempt=bad), "marker_wrong_run_attempt")

    def test_wrong_workflow_ref_sha_repository_event_or_paths_are_refused(self):
        self.case("workflow", "marker_wrong_workflow", workflow="Fundamentals coverage batch")
        self.case("wf_ref", "marker_wrong_workflow_ref", workflow_ref="%s/.github/workflows/fundamentals_batch.yml@%s" % (REPOSITORY, REF))
        self.case("wf_ref_branch", "marker_wrong_workflow_ref", workflow_ref="%s/%s@refs/heads/other" % (REPOSITORY, V.DAILY_WORKFLOW_PATH))
        self.case("ref", "marker_wrong_ref", ref="refs/heads/other")
        self.case("sha", "marker_wrong_sha", sha="f" * 40)
        self.case("repo", "marker_wrong_repository", repository="evil/stock")
        self.case("event", "marker_wrong_event", event="workflow_dispatch")
        self.case("paths", "marker_wrong_paths", paths=["data"])
        self.case("schema", "marker_wrong_schema", schema=2)
        self.case("schema_bool", "marker_wrong_schema", schema=True)
        self.case("kind", "marker_wrong_schema", kind="other")

    def test_the_verifier_also_judges_the_repository_and_ref_it_is_given(self):
        d = make_data_dir(self.root / "r")
        self.refuses(d, "marker_wrong_repository", repository="acme/other")
        self.refuses(d, "marker_wrong_workflow_ref", ref="refs/heads/other")

    def test_missing_modified_or_absent_state_files_are_refused(self):
        d = make_data_dir(self.root / "mod")
        (d / "financials_cache.json").write_bytes(b'{"changed": true}')
        self.refuses(d, "marker_state_file_changed_financials")
        d = make_data_dir(self.root / "same_size")
        raw = (d / "quarterly_cache.json").read_bytes()
        (d / "quarterly_cache.json").write_bytes(raw[:-2] + b"X}")             # same length, different bytes
        self.assertEqual(len(raw), len((d / "quarterly_cache.json").read_bytes()))
        self.refuses(d, "marker_state_file_changed_quarterly")
        d = make_data_dir(self.root / "gone")
        (d / "fundamentals_cache.json").unlink()
        with self.assertRaises((V.Refused, OSError)):
            self.verify(d)                                                       # check-files runs first in the workflow; the verifier must not pass either
        d = make_data_dir(self.root / "null")
        m = json.loads((d / V.MARKER_NAME).read_text())
        m["files"]["quarterly_cache.json"] = None
        (d / V.MARKER_NAME).write_text(json.dumps(m))
        self.refuses(d, "marker_state_file_absent_at_write_quarterly")

    def test_a_malformed_files_block_is_refused(self):
        d = make_data_dir(self.root / "f")
        good = json.loads((d / V.MARKER_NAME).read_text())["files"]
        for name, files in (("list", []), ("short", {k: good[k] for k in list(good)[:2]}), ("extra", dict(good, **{"other.json": good["fundamentals_cache.json"]})),
                            ("entry_shape", dict(good, **{"quarterly_cache.json": {"bytes": 1}})), ("bytes_type", dict(good, **{"quarterly_cache.json": {"bytes": "1", "sha256": "0" * 64}})),
                            ("sha_type", dict(good, **{"quarterly_cache.json": {"bytes": 1, "sha256": "Z" * 64}})), ("entry_str", dict(good, **{"quarterly_cache.json": "x"}))):
            self.case("files_" + name, "marker_bad_files_block", files=files)

    def test_invalid_oversized_or_symlinked_markers_are_refused(self):
        d = make_data_dir(self.root / "bad_json")
        (d / V.MARKER_NAME).write_text("{not json", encoding="utf-8")
        self.refuses(d, "marker_not_json")
        d = make_data_dir(self.root / "bad_utf8")
        (d / V.MARKER_NAME).write_bytes(b"\xff\xfe{}")
        self.refuses(d, "marker_not_json")
        d = make_data_dir(self.root / "deep")
        (d / V.MARKER_NAME).write_text("[" * 5000, encoding="utf-8")
        self.refuses(d, "marker_not_json")
        d = make_data_dir(self.root / "big")
        (d / V.MARKER_NAME).write_text(" " * (V.MARKER_MAX_BYTES + 1) + "{}", encoding="utf-8")
        self.refuses(d, "marker_too_large")
        d = make_data_dir(self.root / "list")
        (d / V.MARKER_NAME).write_text("[]", encoding="utf-8")
        self.refuses(d, "marker_unexpected_shape")
        d = make_data_dir(self.root / "link", marker=False)
        real = self.root / "elsewhere.json"
        real.write_text(json.dumps(good_marker({})), encoding="utf-8")
        (d / V.MARKER_NAME).symlink_to(real)
        self.refuses(d, "marker_missing")
        d = make_data_dir(self.root / "dir", marker=False)
        (d / V.MARKER_NAME).mkdir()
        self.refuses(d, "marker_missing")

    def test_duplicate_extra_or_missing_fields_are_refused(self):
        d = make_data_dir(self.root / "dup")
        text = (d / V.MARKER_NAME).read_text()
        (d / V.MARKER_NAME).write_text(text.replace('"run_id": "123"', '"run_id": "122", "run_id": "123"'), encoding="utf-8")
        self.refuses(d, "marker_not_json")
        d = make_data_dir(self.root / "extra", extra_field="x")
        self.refuses(d, "marker_unexpected_shape")
        d = make_data_dir(self.root / "missing")
        m = json.loads((d / V.MARKER_NAME).read_text())
        del m["sha"]
        (d / V.MARKER_NAME).write_text(json.dumps(m))
        self.refuses(d, "marker_unexpected_shape")

    def test_time_checks(self):
        self.case("before_run", "marker_written_outside_the_run", written_at="2026-10-20T09:00:00Z")
        self.case("after_run", "marker_written_outside_the_run", written_at="2026-10-21T10:00:00Z")
        self.case("after_cache", "marker_newer_than_the_cache", written_at="2026-10-20T10:06:00Z")
        self.verify(make_data_dir(self.root / "slack", written_at="2026-10-20T10:04:00Z"))
        self.case("not_a_time", "marker_time_invalid", written_at="yesterday")
        self.case("no_zone", "marker_time_invalid", written_at="2026-10-20T09:58:00")
        self.case("offset_form", "marker_time_invalid", written_at="2026-10-20T09:58:00+00:00")
        self.case("int_time", "marker_time_invalid", written_at=5)
        sel = dict(self.sel, run_created_at="2026-10-20T07:00:00Z", created_at="2026-10-20T10:00:00Z")
        self.refuses(make_data_dir(self.root / "too_old", written_at="2026-10-20T08:30:00Z"), "marker_older_than_the_cache_by_more_than_an_hour", sel=sel)

    def test_a_failed_partial_or_unknown_update_is_not_a_successful_refresh(self):
        for status in ("failed", "unknown", "complete!", None, 1):
            self.case("status_%s" % (status,), "marker_data_status_not_usable", data_status=status)
        for ok in (False, "true", 1, None):
            self.case("ok_%r" % (ok,), "marker_data_status_not_usable", data_ok=ok)
        for step in V.FEEDING_STEPS:
            for outcome in ("failure", "cancelled", "skipped", "unknown"):
                out = {n: "success" for n in V.MARKER_STEPS}
                out[step] = outcome
                self.case("step_%s_%s" % (step, outcome), "marker_data_step_not_successful", step_outcomes=out)

    def test_a_step_that_does_not_feed_the_state_files_may_have_failed(self):
        out = {n: "success" for n in V.MARKER_STEPS}
        out.update(shareholding="failure", market="skipped", validate="failure")
        self.verify(make_data_dir(self.root / "side", step_outcomes=out))

    def test_bad_step_outcome_blocks_are_refused(self):
        full = {n: "success" for n in V.MARKER_STEPS}
        for name, so in (("list", []), ("short", {k: "success" for k in V.FEEDING_STEPS}), ("extra", dict(full, other="success")), ("bad_value", dict(full, market="ok")),
                         ("non_string", dict(full, market=None))):
            self.case("so_" + name, "marker_bad_step_outcomes", step_outcomes=so)

    def test_the_marker_of_an_earlier_cache_cannot_be_accepted_for_a_newer_one(self):
        old = make_data_dir(self.root / "old", run_id="100", cache_key="nse-data-100", sha="d" * 40)
        self.refuses(old, "marker_wrong_run_id")
        newer = dict(self.sel, key="nse-data-124", run_id="124")
        self.refuses(make_data_dir(self.root / "same_content"), "marker_wrong_run_id", sel=newer)

    def test_refusal_codes_carry_no_marker_text(self):
        d = make_data_dir(self.root / "hostile", repository=HOSTILE[0], run_id=HOSTILE[1])
        with self.assertRaises(V.Refused) as cm:
            self.verify(d)
        self.assertRegex(cm.exception.code, r"^[a-z_]+$")

    def test_the_real_writer_and_this_verifier_agree(self):
        """Compatibility with cache_provenance.py (added by the daily-workflow pull request). Skipped where that file is not in the tree."""
        try:
            import cache_provenance as P
        except ImportError:
            self.skipTest("cache_provenance.py is not in this tree")
        d = make_data_dir(self.root / "real", marker=False)
        env = {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_WORKFLOW": V.DAILY_WORKFLOW_NAME, "GITHUB_REF": REF, "GITHUB_WORKFLOW_REF": "%s/%s@%s" % (REPOSITORY, V.DAILY_WORKFLOW_PATH, REF),
               "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "schedule", "GITHUB_SHA": "e" * 40, "CP_DATA_STATUS": "complete", "CP_DATA_OK": "true",
               **{"CP_OUTCOME_" + n.upper(): "success" for n in V.MARKER_STEPS}}
        when = dt.datetime(2026, 10, 20, 9, 58, tzinfo=dt.timezone.utc)
        P.write_marker(d, env, when)
        self.assertEqual(self.verify(d)["data_status"], "complete")
        self.assertEqual(set(json.loads((d / V.MARKER_NAME).read_text())), set(V.MARKER_FIELDS))


class CommandLine(RealReportCase):
    def run_cli(self, argv, env=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = V.main(["cache_validation.py"] + argv, env if env is not None else {})
        return rc, out.getvalue()

    def test_summarize_writes_the_allowlisted_summary_and_nothing_else(self):
        rep = self.root / "r"
        rep.mkdir()
        (rep / "coverage_report.json").write_text(C.render_json(self.report), encoding="utf-8")
        make_data_dir(self.root / "datadir")
        summary = self.root / "summary.md"
        rc, out = self.run_cli(["summarize", str(rep / "coverage_report.json"), str(self.root / "datadir"), str(summary)], self.prov())
        self.assertEqual((rc, out.strip()), (0, "cache_validation: summary written"))
        text = summary.read_text(encoding="utf-8")
        for bad in HOSTILE + ("S0001", "INE"):
            self.assertNotIn(bad, text + out)
        self.assertIn("not publication status", text)

    def test_verify_marker_command(self):
        d = make_data_dir(self.root / "vm")
        rc, out = self.run_cli(["verify-marker", str(d)], Provenance.OK)
        self.assertEqual(rc, 0, out)
        d2 = make_data_dir(self.root / "vm2", marker=False)
        rc, out = self.run_cli(["verify-marker", str(d2)], Provenance.OK)
        self.assertEqual((rc, out.strip()), (1, "cache_validation: refused (marker_missing)"))
        rc, out = self.run_cli(["verify-marker", str(d)], dict(Provenance.OK, CV_REPOSITORY="evil/stock"))
        self.assertEqual((rc, out.strip()), (1, "cache_validation: refused (marker_wrong_repository)"))
        rc, out = self.run_cli(["verify-marker", str(d)], {})
        self.assertEqual((rc, out.strip()), (1, "cache_validation: refused (provenance_missing_or_malformed)"))

    def test_summarize_refuses_without_a_valid_marker_and_writes_nothing(self):
        rep = self.root / "rm"
        rep.mkdir()
        (rep / "coverage_report.json").write_text(C.render_json(self.report), encoding="utf-8")
        for name, d in (("nomarker", make_data_dir(self.root / "dn", marker=False)), ("stale", make_data_dir(self.root / "ds", run_id="122", cache_key="nse-data-122"))):
            out_md = self.root / (name + ".md")
            rc, out = self.run_cli(["summarize", str(rep / "coverage_report.json"), str(d), str(out_md)], self.prov())
            self.assertEqual(rc, 1)
            self.assertRegex(out.strip(), r"^cache_validation: refused \(marker_[a-z_]+\)$")
            self.assertFalse(out_md.exists())

    def test_the_summary_states_the_marker_facts_and_flags_an_incomplete_refresh(self):
        rep = self.root / "ri"
        rep.mkdir()
        (rep / "coverage_report.json").write_text(C.render_json(self.report), encoding="utf-8")
        d = make_data_dir(self.root / "di", data_status="incomplete")
        out_md = self.root / "i.md"
        rc, _ = self.run_cli(["summarize", str(rep / "coverage_report.json"), str(d), str(out_md)], self.prov())
        self.assertEqual(rc, 0)
        text = out_md.read_text(encoding="utf-8")
        self.assertIn("verified by the cache's own marker (not signed)", text)
        self.assertIn("NOT a full data refresh", text)
        self.assertIn("fundamentals=success", text)

    def test_a_refusal_writes_nothing_and_prints_only_a_fixed_code(self):
        rep = self.root / "r.json"
        bad = copy.deepcopy(self.report)
        bad["scopes"]["all"]["modules"][0]["counts"] = HOSTILE[0]
        rep.write_text(json.dumps(bad), encoding="utf-8")
        make_data_dir(self.root / "d")
        summary = self.root / "summary.md"
        rc, out = self.run_cli(["summarize", str(rep), str(self.root / "d"), str(summary)], self.prov())
        self.assertEqual(rc, 0)                                         # the unrelated module was not read: only the three state modules are
        rc, out = self.run_cli(["summarize", str(rep), str(self.root / "d"), str(self.root / "s2.md")], self.prov(CV_AS_OF="2026-10-22"))
        self.assertEqual((rc, out.strip()), (1, "cache_validation: refused (report_date_mismatch)"))
        self.assertFalse((self.root / "s2.md").exists())

    def files(self, caches, runs=None, wf=None):
        d = self.root / "cli"
        (d / "runs").mkdir(parents=True, exist_ok=True)
        (d / "caches.json").write_text(page(caches), encoding="utf-8")
        (d / "workflow.json").write_text(json.dumps(workflow() if wf is None else wf), encoding="utf-8")
        for rid, run in (runs or {}).items():
            (d / "runs" / ("%s.json" % rid)).write_text(json.dumps(run), encoding="utf-8")
        return d

    def test_candidates_then_select_end_to_end(self):
        d = self.files([cache("nse-data-300", "2026-10-22T10:00:00Z"), cache("nse-data-200")],
                       {300: batch_run(300, created_at="2026-10-22T09:50:00Z", updated_at="2026-10-22T10:20:00Z"), 200: daily_run(200)})
        rc, out = self.run_cli(["candidates", str(d / "caches.json"), REF, str(d / "ids.txt")])
        self.assertEqual((rc, (d / "ids.txt").read_text()), (0, "300\n200\n"))
        args = ["select", str(d / "caches.json"), str(d / "workflow.json"), str(d / "runs"), REF, NOW]
        rc, out = self.run_cli(args)
        self.assertEqual(rc, 0)
        self.assertIn("key=nse-data-200", out.split("\n"))
        self.assertIn("skipped_batch_caches=1", out.split("\n"))
        go = self.root / "gh_output"
        rc, out = self.run_cli(args, {"GITHUB_OUTPUT": str(go)})
        self.assertEqual((rc, out), (0, ""))
        lines = go.read_text().split("\n")
        self.assertEqual(lines[0], "key=nse-data-200")
        self.assertTrue(all(re.fullmatch(r"[a-z_]+=[0-9A-Za-z:.+/\- ]*", l) for l in lines if l), lines)

    def test_a_run_file_that_is_missing_is_a_fixed_refusal(self):
        d = self.files([cache("nse-data-200")])
        rc, out = self.run_cli(["select", str(d / "caches.json"), str(d / "workflow.json"), str(d / "runs"), REF, NOW])
        self.assertEqual((rc, out.strip()), (1, "cache_validation: refused (run_metadata_missing)"))

    def test_garbage_input_prints_no_input_text(self):
        junk = self.root / "junk.json"
        junk.write_text("SECRET-LOOKING TEXT " + HOSTILE[0], encoding="utf-8")
        d = self.files([cache("nse-data-200")], {200: daily_run(200)})
        (d / "runs" / "200.json").write_text("SECRET-LOOKING TEXT " + HOSTILE[0], encoding="utf-8")
        for argv in (["candidates", str(junk), REF, str(self.root / "o.txt")], ["select", str(junk), str(d / "workflow.json"), str(d / "runs"), REF, NOW],
                     ["select", str(d / "caches.json"), str(junk), str(d / "runs"), REF, NOW], ["select", str(d / "caches.json"), str(d / "workflow.json"), str(d / "runs"), REF, NOW],
                     ["select", str(d / "caches.json"), str(d / "workflow.json"), str(d / "runs"), REF, HOSTILE[0]],
                     ["summarize", str(junk), str(self.root), str(self.root / "o.md")], ["select", str(self.root / "missing.json"), str(junk), str(d / "runs"), REF, NOW]):
            rc, out = self.run_cli(argv, self.prov())
            self.assertEqual(rc, 1, argv)
            self.assertRegex(out.strip(), r"^cache_validation: refused \([a-z_]+\)$")
            self.assertNotIn("SECRET", out)
            self.assertNotIn(HOSTILE[0], out)

    def test_usage_error(self):
        self.assertEqual(self.run_cli([])[0], 2)
        self.assertEqual(self.run_cli(["select"])[0], 2)


class WorkflowControls(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.doc = yaml.safe_load(self.text)
        self.steps = self.doc["jobs"]["validate"]["steps"]
        self.code = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))

    def test_manual_only_with_minimal_permissions_and_the_ledger_group(self):
        self.assertEqual(self.doc["on"], {"workflow_dispatch": None})
        self.assertEqual(self.doc["permissions"], {"contents": "read", "actions": "read"})
        self.assertEqual(self.doc["concurrency"], {"group": "stocklens-ledger", "cancel-in-progress": False})
        self.assertEqual(list(self.doc["jobs"]), ["validate"])
        self.assertNotIn("permissions", self.doc["jobs"]["validate"])

    def test_only_these_actions_are_used_and_none_saves_uploads_or_deploys(self):
        uses = [s["uses"] for s in self.steps if "uses" in s]
        self.assertEqual(sorted(set(uses)), ["actions/cache/restore@v4", "actions/checkout@v4", "actions/setup-python@v5"])
        for banned in ("cache/save", "actions/cache@", "upload-artifact", "upload-pages", "deploy-pages", "configure-pages", "download-artifact", "gh-pages",
                       "git push", "git commit", "gh workflow", "gh run", "gh cache", "gh api -X", "--method", "curl", "wget", "pip install", "UPSTOX", "secrets.", "id-token"):
            self.assertNotIn(banned, self.code, banned)

    def test_the_restore_is_one_exact_key_with_the_daily_path_set_and_no_prefix(self):
        r = [s for s in self.steps if s.get("uses") == "actions/cache/restore@v4"]
        self.assertEqual(len(r), 1)
        w = r[0]["with"]
        self.assertEqual(set(w), {"path", "key", "fail-on-cache-miss"})
        self.assertNotIn("restore-keys", self.code)
        self.assertIs(w["fail-on-cache-miss"], True)
        self.assertEqual(w["key"], "${{ steps.select.outputs.key }}")
        self.assertEqual([p.strip() for p in w["path"].splitlines() if p.strip()], ["data", "out"])
        self.assertNotIn("lookup-only", self.code)

    def test_the_restore_paths_equal_the_daily_runs_so_the_cache_version_matches(self):
        daily = {p for name in ("update.yml", "fundamentals_batch.yml") for _, kind, _, _, p, _ in ISO.caches(yaml.safe_load((WORKFLOW.parent / name).read_text(encoding="utf-8")))
                 if kind in ("both", "restore")}
        mine = {p for _, kind, _, _, p, _ in ISO.caches(self.doc)}
        self.assertEqual(mine, daily)
        self.assertEqual(mine, {frozenset({"data", "out"})})

    def test_the_marker_is_verified_after_the_restore_and_before_the_report_and_cannot_be_skipped(self):
        names = [st.get("name", "") for st in self.steps]
        i = next(k for k, n in enumerate(names) if n.startswith("Verify the provenance marker"))
        self.assertLess(names.index("Check the three state files"), i)
        self.assertLess(i, next(k for k, n in enumerate(names) if n.startswith("Coverage report")))
        self.assertLess(i, next(k for k, n in enumerate(names) if n.startswith("Job summary")))
        st = self.steps[i]
        self.assertNotIn("continue-on-error", st)
        self.assertNotIn("if", st)
        self.assertIn("python cache_validation.py verify-marker data", st["run"])
        self.assertTrue(all("continue-on-error" not in s2 and "if" not in s2 for s2 in self.steps))

    def test_every_step_that_reads_the_provenance_passes_the_same_values(self):
        by = {st.get("name", ""): st for st in self.steps}
        verify = next(v for n, v in by.items() if n.startswith("Verify the provenance marker"))
        summary = next(v for n, v in by.items() if n.startswith("Job summary"))
        derived_in_script = {"CV_AS_OF", "CV_DATA_BRANCH_SHA"}
        wanted = set(V.PROVENANCE_ENV) - derived_in_script
        self.assertEqual(set(verify["env"]), wanted)
        self.assertEqual(set(summary["env"]), wanted)
        self.assertEqual(verify["env"], summary["env"])
        self.assertEqual(summary["env"]["CV_REPOSITORY"], "${{ github.repository }}")
        self.assertIn("CV_AS_OF", verify["run"])
        self.assertIn("CV_DATA_BRANCH_SHA", verify["run"])

    def test_the_marker_is_never_uploaded_or_written_anywhere(self):
        self.assertNotIn("cache_provenance.json", self.code)
        self.assertNotIn("cache_provenance.py", self.code)

    def test_it_is_a_restore_only_member_of_the_ledger_group_and_breaks_the_isolation_rules_nowhere(self):
        mine = ISO.caches(self.doc)
        self.assertEqual([(kind, prefixes, group) for _, kind, _, prefixes, _, group in mine], [("restore", [], ISO.LEDGER)])
        allc = ISO.load_all(lambda n: (WORKFLOW.parent / n).read_text(encoding="utf-8"))
        self.assertEqual(ISO.violations(allc), [])

    def test_the_selection_pattern_is_the_exact_one(self):
        self.assertEqual(V.CACHE_KEY_RE.pattern.replace("{1,20}", "+"), "nse-data-[0-9]+")
        self.assertIn("python cache_validation.py candidates", self.code)
        self.assertIn("python cache_validation.py select", self.code)
        self.assertIn("python cache_validation.py check-files data", self.code)
        self.assertIn("python cache_validation.py summarize", self.code)

    def test_the_report_is_offline_quiet_and_written_outside_the_workspace(self):
        run = [s["run"] for s in self.steps if "coverage_report.py" in s.get("run", "")]
        self.assertEqual(len(run), 1)
        self.assertIn('--out "$RUNNER_TEMP/report"', run[0])
        self.assertIn("--state-dir data", run[0])
        self.assertIn("> /dev/null 2>&1", run[0])

    def test_the_summary_goes_only_to_the_job_summary(self):
        self.assertIn('"$GITHUB_STEP_SUMMARY"', self.code)
        self.assertEqual(self.code.count("GITHUB_STEP_SUMMARY"), 1)

    def test_the_default_branch_guard_comes_first(self):
        self.assertIn("default branch", self.steps[0]["name"].lower())

    def test_the_only_token_is_the_read_only_workflow_token_and_it_only_reads_metadata(self):
        tokens = [(s.get("name"), s["env"]["GH_TOKEN"]) for s in self.steps if "GH_TOKEN" in s.get("env", {})]
        self.assertEqual(tokens, [("Read the daily workflow's metadata", "${{ github.token }}"), ("List cache metadata (no content)", "${{ github.token }}"),
                                  ("Read those runs' metadata", "${{ github.token }}")])
        calls = re.findall(r"gh api[^\n]*", self.code)
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[0].startswith('gh api "repos/$REPO/actions/workflows/update.yml"'))
        self.assertTrue(calls[1].startswith('gh api --paginate "repos/$REPO/actions/caches?ref=refs/heads/$DEFAULT_BRANCH&per_page=100"'))
        self.assertTrue(calls[2].startswith('gh api "repos/$REPO/actions/runs/$id"'))
        for c in calls:
            c = c.split(">")[0]                                         # the command, not its redirection or the clean-up after it
            for flag in (" -X", "--method", " -f ", " -F ", "--field", "--raw-field", "--input"):
                self.assertNotIn(flag, c + " ")

    def test_the_selection_is_a_chain_of_the_daily_workflow_check(self):
        names = [s.get("name") for s in self.steps]
        order = ["Read the daily workflow's metadata", "List cache metadata (no content)", "Name the runs behind the newest exact-key caches", "Read those runs' metadata",
                 "Select the newest cache saved by a successful daily run", "Restore that cache only (never saved)", "Check that exactly the selected cache was restored"]
        self.assertEqual([names.index(n) for n in order], sorted(names.index(n) for n in order))
        sel = [s for s in self.steps if s.get("id") == "select"][0]["run"]
        for part in ('"$RUNNER_TEMP/caches.json"', '"$RUNNER_TEMP/workflow.json"', '"$RUNNER_TEMP/runs"', '"refs/heads/$DEFAULT_BRANCH"', "date -u"):
            self.assertIn(part, sel)
        self.assertIn("update.yml", self.code)

    def test_no_step_prints_a_file_or_record(self):
        for banned in ("cat ", "head ", "tail ", "jq ", "echo \"$(", "python -c", "print("):
            self.assertNotIn(banned, self.code, banned)

    def test_the_helper_is_stdlib_only_and_offline(self):
        tree = ast.parse((HERE / "cache_validation.py").read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names.add((n.module or "").split(".")[0])
        self.assertEqual(names, {"datetime", "hashlib", "json", "math", "os", "re", "sys", "pathlib", "coverage_report"})
        self.assertIn("provider_calls", (HERE / "coverage_report.py").read_text(encoding="utf-8"))


class DocumentationStatesTheLimits(unittest.TestCase):
    def test_the_document_says_what_the_result_is_and_is_not(self):
        text = (HERE / "CACHE_VALIDATION.md").read_text(encoding="utf-8")
        for needle in ("cache coverage, not publication status", "has not been run", "default branch", "No artifact", "nse-data-", "fundamentals_batch.yml", "corroborated",
                       "does not record which run", "Update data and publish site", "7 days", "fail", "marker",
                       "cache_provenance.json", "not signed", "attempt", "first attempt", "Tier 2", "No existing cache"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
