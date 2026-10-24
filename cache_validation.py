"""
cache_validation.py - the helper behind the one-off workflow "Validate cache coverage" (.github/workflows/cache_validation.yml).

It does a few small jobs and nothing else. It makes no network call, imports no provider client and writes only the one file it is told to.

  candidates CACHE_LIST_JSON REF OUT
                               Write to OUT the run ids of the newest caches of REF whose key is exactly nse-data-<digits> (metadata only). The workflow fetches those runs.
  select CACHE_LIST_JSON WORKFLOW_JSON RUNS_DIR REF NOW
                               Choose the cache to restore: the newest exact nse-data-<digits> cache of REF that was saved by a SUCCESSFUL run of the exact daily workflow
                               "Update data and publish site" (update.yml) on the default branch. GitHub's cache list does not say which run created a cache, and the
                               fundamentals batch workflow names its caches the same way, so the number alone proves nothing. The owner is corroborated: the run with
                               that id must be a completed, successful run of the workflow whose id and path match update.yml, on the default branch, started by schedule
                               or workflow_dispatch, and the cache must have been created during that run and be at most 7 days old. A cache whose run is positively a
                               fundamentals-batch run is skipped; anything else that does not check out refuses the run. The list must be complete and a key may appear once.
  check-files DATA_DIR         Refuse unless the three saved-state files exist as ordinary, non-empty files.
  verify-marker DATA_DIR       Refuse unless data/cache_provenance.json (written by the daily run as its last data step) agrees with the selected run: repository, workflow,
                               ref, run id, ATTEMPT (it must be the run's latest), commit, cache key, event, times, data status, the steps that fill the state files, and
                               the byte size and SHA-256 of all three state files. The marker is unsigned (Tier 1): it is not a defence against a malicious workflow author.
  summarize REPORT_JSON DATA_DIR OUT_MD
                               Build the public job summary from the coverage report. The summary is built NEW from an explicit allowlist: every number and every
                               name is checked and copied one by one. Nothing from the report is passed through. Anything unexpected (a missing field, a wrong type, a
                               number that does not add up, a name that is not a plain lower-case token) refuses the whole run and nothing is written.

What the summary contains: the scope sizes, for each of the three saved-state modules its measurability, its counts (valid, stale, missing, invalid, unverified),
its reason counts and a few counts of its own, which state files were usable, and the provenance (cache key, dates, sizes, file hashes).
What it never contains: a record, a ratio, a company name, an ISIN, a sector label, an error text, a symbol (so no outside-the-universe list either), a path.

THE RESULT IS CACHE COVERAGE, NOT PUBLICATION STATUS. A record in a saved cache is not proof that anything reached the website.

A refusal prints only a fixed code. Never the text of an exception, never a value from the cache or the report.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import coverage_report as C

CACHE_KEY_RE = re.compile(r"nse-data-[0-9]{1,20}", re.ASCII)       # matched with fullmatch: no prefix match, no trailing text, no newline
STATE_FILES = ("fundamentals_cache.json", "financials_cache.json", "quarterly_cache.json")
STATE_MODULES = ("fundamentals_state", "quarterly_results", "annual_financials_and_balance_sheet")
SCOPES = ("first_500", "all")
COUNT_KEYS = ("valid", "stale", "missing", "invalid", "not_applicable", "unverified")
MEASURABILITY = ("partial", "not_measurable")
TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,79}", re.ASCII)
SCALAR_FACTS = ("stocks_with_ratios", "stocks_with_quarters", "stocks_with_all_three_sections", "stocks_with_failed_requests",
                "stocks_whose_last_check_returned_no_data", "stocks_whose_last_check_was_rejected", "due_for_recheck_under_the_updaters_rule")
DICT_FACTS = ("unverified_reasons", "stale_reasons", "refresh_states_of_stocks_with_quarters")
MAX_COUNT = 10 ** 7
MAX_TOKENS = 40
MAX_WARNINGS = 20
MAX_REPORT_BYTES = 64 * 1024 * 1024
PROVENANCE_ENV = {                                                      # name -> pattern the value must fully match
    "CV_MATCHED_KEY": CACHE_KEY_RE.pattern, "CV_RESTORE_HIT": r"true", "CV_RUN_ID": r"[0-9]{1,20}", "CV_MAIN_SHA": r"[0-9a-f]{40}", "CV_DATA_BRANCH_SHA": r"[0-9a-f]{40}",
    "CV_AS_OF": r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "CV_CREATED_AT": r"[0-9T:.+\-Z]{10,40}", "CV_LAST_ACCESSED_AT": r"[0-9T:.+\-Z]{10,40}", "CV_SIZE": r"[0-9]{1,15}",
    "CV_VERSION": r"[0-9a-f]{64}", "CV_CACHE_REF": r"refs/heads/[A-Za-z0-9._/\-]{1,100}", "CV_SOURCE_RUN_ID": r"[0-9]{1,20}", "CV_SOURCE_RUN_EVENT": r"schedule|workflow_dispatch",
    "CV_SOURCE_RUN_HEAD_SHA": r"[0-9a-f]{40}", "CV_SOURCE_RUN_ATTEMPT": r"[0-9]{1,3}", "CV_SOURCE_RUN_CREATED_AT": r"[0-9T:.+\-Z]{10,40}",
    "CV_SOURCE_RUN_UPDATED_AT": r"[0-9T:.+\-Z]{10,40}", "CV_SKIPPED_BATCH_CACHES": r"[0-9]{1,2}", "CV_REPOSITORY": r"[A-Za-z0-9._\-]{1,100}/[A-Za-z0-9._\-]{1,100}",
}


MARKER_NAME = "cache_provenance.json"                                       # written by cache_provenance.py (feat/cache-provenance-marker) as the daily run's last data step
MARKER_MAX_BYTES = 16 * 1024
MARKER_FUTURE_SLACK = dt.timedelta(minutes=5)
MARKER_TO_CACHE_MAX = dt.timedelta(minutes=60)                              # the marker is written, then the job ends and the cache is saved
MARKER_STEPS = ("nse", "fundamentals", "financials", "financial_history", "shareholding", "quarterly", "validate", "market")
FEEDING_STEPS = ("fundamentals", "financials", "quarterly")                 # the three steps that fill the three state files; each must have succeeded
MARKER_OUTCOMES = ("success", "failure", "cancelled", "skipped", "unknown")
MARKER_FIELDS = ("schema", "kind", "repository", "workflow", "workflow_ref", "run_id", "run_attempt", "event", "ref", "sha", "cache_key", "paths", "written_at",
                 "data_status", "data_ok", "step_outcomes", "files")
REPO_RE = re.compile(r"[A-Za-z0-9._\-]{1,100}/[A-Za-z0-9._\-]{1,100}", re.ASCII)


class Refused(Exception):
    """A fixed code, never data."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- choosing the cache (metadata only)
# GitHub's cache list records NO workflow, run, job or actor for a cache: only id, ref, key, version, last_accessed_at, created_at and size_in_bytes. So the owner of a
# cache cannot be read from it. This repository names its caches nse-data-<github.run_id>, and both "Update data and publish site" (update.yml) and "Fundamentals
# coverage batch" (fundamentals_batch.yml) do it, so the key alone cannot say which one made a cache. The owner is therefore CORROBORATED, never taken from the number
# alone: the run with that id is fetched and must be a successful run of the exact daily workflow, on the default branch, whose lifetime contains the cache's creation.
# A cache whose run is positively a fundamentals-batch run is skipped. Everything else that does not check out stops the run (fail closed).
DAILY_WORKFLOW_PATH = ".github/workflows/update.yml"
DAILY_WORKFLOW_NAME = "Update data and publish site"
EXCLUDED_WORKFLOW_PATHS = (".github/workflows/fundamentals_batch.yml",)      # the one other workflow that saves nse-data-<run id>
EXPECTED_EVENTS = ("schedule", "workflow_dispatch")
MAX_CANDIDATES = 10                                                         # exact-key caches examined, newest first; more batch caches than this in a row stops the run
MAX_CACHE_AGE = dt.timedelta(days=7)                                        # caches unused for 7 days are evicted; an older one is not today's saved state
WINDOW_SLACK = dt.timedelta(minutes=5)
REF_RE = re.compile(r"refs/heads/[A-Za-z0-9._/\-]{1,100}", re.ASCII)
SHA_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
VERSION_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)


def _when(text):
    if not isinstance(text, str):
        raise Refused("metadata_bad_date")
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Refused("metadata_bad_date") from None
    if t.tzinfo is None:
        raise Refused("metadata_bad_date")
    return t


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _object(text, code):
    try:
        doc = json.loads(text) if isinstance(text, str) else None
    except (ValueError, RecursionError):
        raise Refused(code) from None
    if not isinstance(doc, dict):
        raise Refused(code)
    return doc


def _entries(text):
    """Every entry of the (possibly paginated, concatenated) cache list; refused unless the list is complete."""
    dec, i, pages = json.JSONDecoder(), 0, []
    text = text if isinstance(text, str) else ""
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            doc, i = dec.raw_decode(text, i)
        except (ValueError, RecursionError):
            raise Refused("cache_list_not_json") from None
        pages.append(doc)
    if not pages:
        raise Refused("cache_list_empty_response")
    totals, entries = set(), []
    for p in pages:
        if not isinstance(p, dict) or not isinstance(p.get("actions_caches"), list):
            raise Refused("cache_list_unexpected_shape")
        t = p.get("total_count")
        if not _int(t) or t < 0:
            raise Refused("cache_list_unexpected_shape")
        totals.add(t)
        entries.extend(p["actions_caches"])
    if len(totals) != 1 or len(entries) != next(iter(totals)):
        raise Refused("cache_list_incomplete")
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("key"), str):
            raise Refused("cache_list_unexpected_shape")
    return entries


def _exact_key_caches(text, ref):
    """The caches of REF whose key is exactly nse-data-<digits>, newest first, each with checked metadata. A key listed twice on the ref is ambiguous and refused."""
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        raise Refused("ref_not_a_branch")
    found, seen = [], set()
    for e in _entries(text):
        if not CACHE_KEY_RE.fullmatch(e["key"]) or e.get("ref") != ref:
            continue                                                         # another prefix, or another branch's cache: never a candidate
        if e["key"] in seen:
            raise Refused("cache_key_listed_more_than_once")
        seen.add(e["key"])
        if not _int(e.get("size_in_bytes")) or e["size_in_bytes"] < 0 or not isinstance(e.get("version"), str) or not VERSION_RE.fullmatch(e["version"]):
            raise Refused("cache_list_unexpected_shape")
        created = _when(e.get("created_at"))
        accessed = e.get("last_accessed_at") or e["created_at"]            # a cache never restored may carry no access time
        _when(accessed)
        found.append({"key": e["key"], "run_id": e["key"][len("nse-data-"):], "created": created, "created_at": e["created_at"], "last_accessed_at": accessed,
                      "size_in_bytes": e["size_in_bytes"], "version": e["version"], "ref": ref})
    found.sort(key=lambda c: (c["created"], int(c["run_id"])), reverse=True)
    return found


def candidate_run_ids(text, ref):
    """The run ids (as in the keys) of the newest exact-key caches of REF, newest first. These are the runs whose metadata must be fetched."""
    found = _exact_key_caches(text, ref)
    if not found:
        raise Refused("no_cache_with_an_exact_nse_data_key_on_the_ref")
    return [c["run_id"] for c in found[:MAX_CANDIDATES]]


def select_daily_cache(cache_text, workflow_text, run_texts, ref, now):
    """The newest cache that was saved by a successful run of the exact daily workflow on REF, or Refused.
    run_texts: {run id: JSON text of that run, or None when it could not be fetched}."""
    now = _when(now)
    wf = _object(workflow_text, "workflow_metadata_unreadable")
    if not _int(wf.get("id")) or wf.get("path") != DAILY_WORKFLOW_PATH or wf.get("name") != DAILY_WORKFLOW_NAME or wf.get("state") != "active":
        raise Refused("daily_workflow_identity_not_confirmed")
    branch = ref[len("refs/heads/"):] if isinstance(ref, str) and REF_RE.fullmatch(ref) else None
    candidates = _exact_key_caches(cache_text, ref)[:MAX_CANDIDATES]
    if not candidates:
        raise Refused("no_cache_with_an_exact_nse_data_key_on_the_ref")
    skipped = 0
    for c in candidates:
        text = run_texts.get(c["run_id"])
        if text is None:
            raise Refused("run_metadata_missing")
        run = _object(text, "run_metadata_unexpected_shape")
        path, wid, rid = run.get("path"), run.get("workflow_id"), run.get("id")
        if not isinstance(path, str) or not _int(wid) or not _int(rid):
            raise Refused("run_metadata_unexpected_shape")
        if rid != int(c["run_id"]):
            raise Refused("run_metadata_id_mismatch")
        base = path.split("@", 1)[0]
        if base in EXCLUDED_WORKFLOW_PATHS:
            skipped += 1                                                     # positively another workflow's cache: not the daily state
            continue
        if base != DAILY_WORKFLOW_PATH:
            raise Refused("run_from_an_unexpected_workflow")
        if wid != wf["id"]:
            raise Refused("run_workflow_identity_inconsistent")
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            raise Refused("run_not_successful")
        if run.get("head_branch") != branch:
            raise Refused("run_on_another_branch")
        if run.get("event") not in EXPECTED_EVENTS:
            raise Refused("run_event_not_expected")
        if not isinstance(run.get("head_sha"), str) or not SHA_RE.fullmatch(run["head_sha"]) or not _int(run.get("run_attempt")) or run["run_attempt"] < 1:
            raise Refused("run_metadata_unexpected_shape")
        r_created, r_updated = _when(run.get("created_at")), _when(run.get("updated_at"))
        if not (r_created <= c["created"] <= r_updated + WINDOW_SLACK):
            raise Refused("cache_created_outside_the_run")
        if c["created"] > now + WINDOW_SLACK:
            raise Refused("cache_from_the_future")
        if now - c["created"] > MAX_CACHE_AGE:
            raise Refused("cache_too_old")
        return {"key": c["key"], "created_at": c["created_at"], "last_accessed_at": c["last_accessed_at"], "size_in_bytes": c["size_in_bytes"], "version": c["version"],
                "ref": c["ref"], "run_id": c["run_id"], "run_event": run["event"], "run_head_sha": run["head_sha"], "run_attempt": run["run_attempt"],
                "run_created_at": run["created_at"], "run_updated_at": run["updated_at"], "skipped_batch_caches": skipped}
    raise Refused("no_daily_cache_among_the_newest_candidates")


# ---------------------------------------------------------------- the restored files
def check_files(data_dir):
    root = Path(data_dir)
    for name in STATE_FILES:
        p = root / name
        if p.is_symlink() or not p.is_file():
            raise Refused("state_file_missing_" + name.split("_")[0])
        if p.stat().st_size == 0:
            raise Refused("state_file_empty_" + name.split("_")[0])


def file_facts(data_dir):
    out = {}
    for name in STATE_FILES:
        b = (Path(data_dir) / name).read_bytes()
        out[name] = {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    return out


# ---------------------------------------------------------------- the provenance marker
# GitHub does not record which run made a cache, so the cache carries its own evidence: cache_provenance.json, written by the daily run as its last data step. It is
# read here as untrusted input (size-capped, no symlink, duplicate keys refused, exact field set, exact types) and must agree with the cache that was selected from the
# run metadata. The marker is NOT signed: it binds a cache to a run for a repository whose workflows are trusted; it does not defend against a malicious workflow author.
def _no_duplicates(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate key")
        d[k] = v
    return d


def _load_marker(data_dir):
    p = Path(data_dir) / MARKER_NAME
    if p.is_symlink() or not p.is_file():
        raise Refused("marker_missing")
    if p.stat().st_size > MARKER_MAX_BYTES:
        raise Refused("marker_too_large")
    try:
        raw = p.read_bytes()
        if len(raw) > MARKER_MAX_BYTES:
            raise Refused("marker_too_large")
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except Refused:
        raise
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        raise Refused("marker_not_json") from None
    if not isinstance(doc, dict) or set(doc) != set(MARKER_FIELDS):
        raise Refused("marker_unexpected_shape")
    return doc


def verify_marker(data_dir, sel, repository, ref):
    """Check the restored cache's marker against the selection (SEL: the select outputs) and the three state files. Returns the facts the summary may show, or Refused."""
    m = _load_marker(data_dir)
    if m["schema"] != 1 or isinstance(m["schema"], bool) or m["kind"] != "cache_provenance":
        raise Refused("marker_wrong_schema")
    if m["repository"] != repository or not isinstance(repository, str) or not REPO_RE.fullmatch(repository):
        raise Refused("marker_wrong_repository")
    if m["workflow"] != DAILY_WORKFLOW_NAME:
        raise Refused("marker_wrong_workflow")
    if m["workflow_ref"] != "%s/%s@%s" % (repository, DAILY_WORKFLOW_PATH, ref):
        raise Refused("marker_wrong_workflow_ref")
    if m["ref"] != ref:
        raise Refused("marker_wrong_ref")
    if m["run_id"] != sel["run_id"] or not isinstance(m["run_id"], str):
        raise Refused("marker_wrong_run_id")
    if m["cache_key"] != sel["key"] or m["cache_key"] != "nse-data-" + m["run_id"]:
        raise Refused("marker_wrong_cache_key")
    if m["event"] != sel["run_event"]:
        raise Refused("marker_wrong_event")
    if m["sha"] != sel["run_head_sha"]:
        raise Refused("marker_wrong_sha")
    if m["paths"] != ["data", "out"]:
        raise Refused("marker_wrong_paths")
    attempt = m["run_attempt"]
    if not _int(attempt) or attempt < 1:
        raise Refused("marker_wrong_run_attempt")
    if attempt != sel["run_attempt"]:
        # A re-run of the same run id hits its own cache key, restores it and saves nothing, so the cache keeps the FIRST attempt's content. A marker whose attempt is not
        # the run's latest attempt therefore belongs to an earlier attempt and is never accepted.
        raise Refused("marker_attempt_not_the_latest")
    try:
        written = _when(m["written_at"])
    except Refused:
        raise Refused("marker_time_invalid") from None
    if not (isinstance(m["written_at"], str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", m["written_at"], re.ASCII)):
        raise Refused("marker_time_invalid")
    r_created, r_updated, c_created = _when(sel["run_created_at"]), _when(sel["run_updated_at"]), _when(sel["created_at"])
    if not (r_created <= written <= r_updated + WINDOW_SLACK):
        raise Refused("marker_written_outside_the_run")
    if written > c_created + MARKER_FUTURE_SLACK:
        raise Refused("marker_newer_than_the_cache")
    if c_created - written > MARKER_TO_CACHE_MAX:
        raise Refused("marker_older_than_the_cache_by_more_than_an_hour")
    status = m["data_status"]
    if m["data_ok"] is not True or status not in ("complete", "incomplete"):
        raise Refused("marker_data_status_not_usable")
    out = m["step_outcomes"]
    if not isinstance(out, dict) or set(out) != set(MARKER_STEPS) or any(v not in MARKER_OUTCOMES for v in out.values()):
        raise Refused("marker_bad_step_outcomes")
    for step in FEEDING_STEPS:
        if out[step] != "success":
            raise Refused("marker_data_step_not_successful")
    files = m["files"]
    if not isinstance(files, dict) or set(files) != set(STATE_FILES):
        raise Refused("marker_bad_files_block")
    now_facts = file_facts(data_dir)
    for name in STATE_FILES:
        f, kind = files[name], name.split("_")[0]
        if f is None:
            raise Refused("marker_state_file_absent_at_write_" + kind)
        if not isinstance(f, dict) or set(f) != {"bytes", "sha256"} or not _int(f["bytes"]) or not isinstance(f["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", f["sha256"], re.ASCII):
            raise Refused("marker_bad_files_block")
        if f["bytes"] != now_facts[name]["bytes"] or f["sha256"] != now_facts[name]["sha256"]:
            raise Refused("marker_state_file_changed_" + kind)
    return {"written_at": m["written_at"], "run_attempt": attempt, "data_status": status, "feeding": {s: out[s] for s in FEEDING_STEPS}}


def selection_from_env(env):
    """The select outputs, rebuilt from the validated environment so the marker is judged against exactly what the summary shows."""
    prov = provenance_from_env(env)
    return {"key": prov["CV_MATCHED_KEY"], "run_id": prov["CV_SOURCE_RUN_ID"], "run_event": prov["CV_SOURCE_RUN_EVENT"], "run_head_sha": prov["CV_SOURCE_RUN_HEAD_SHA"],
            "run_attempt": int(prov["CV_SOURCE_RUN_ATTEMPT"]), "run_created_at": prov["CV_SOURCE_RUN_CREATED_AT"], "run_updated_at": prov["CV_SOURCE_RUN_UPDATED_AT"],
            "created_at": prov["CV_CREATED_AT"]}, prov


# ---------------------------------------------------------------- the allowlist
def _count(v, hi=MAX_COUNT):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > hi:
        raise Refused("report_bad_number")
    return v


def _opt_count(v):
    return None if v is None else _count(v)


def _tokens(d):
    if not isinstance(d, dict) or len(d) > MAX_TOKENS:
        raise Refused("report_bad_reason_counts")
    out = {}
    for k in sorted(d):
        if not isinstance(k, str) or not TOKEN_RE.fullmatch(k):
            raise Refused("report_bad_reason_name")
        out[k] = _count(d[k])
    return out


def _token(v):
    """A plain lower-case token, or the fixed word 'withheld'. Used for the one-word problem of an unusable file."""
    head = v.split(":", 1)[0].strip() if isinstance(v, str) else ""
    return head if TOKEN_RE.fullmatch(head) else "withheld"


def _percent(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or v > 100:
        raise Refused("report_bad_number")
    return v


def _facts(f):
    if not isinstance(f, dict):
        raise Refused("report_bad_module")
    out = {}
    for k in SCALAR_FACTS:
        if k in f:
            out[k] = _count(f[k])
    for k in DICT_FACTS:
        if k in f:
            out[k] = _tokens(f[k])
    sf = f.get("state_file")
    if not isinstance(sf, dict) or not isinstance(sf.get("usable"), bool):
        raise Refused("report_bad_module")
    st = {"usable": sf["usable"]}
    if sf["usable"]:
        dup = sf.get("duplicate_symbol_keys")
        if not isinstance(dup, dict):
            raise Refused("report_bad_module")
        st.update(entries=_count(sf.get("entries")), keys_that_are_not_valid_symbols=_count(sf.get("keys_that_are_not_valid_symbols")),
                  duplicate_symbol_keys_distinct=_count(dup.get("distinct")), duplicate_symbol_keys_in_this_scope=_count(dup.get("in_this_scope")))
    else:
        st["problem"] = _token(sf.get("problem"))
    out["state_file"] = st
    return out


def _module(m, denominator):
    if not isinstance(m, dict) or not isinstance(m.get("counts"), dict) or not isinstance(m.get("denominator"), dict):
        raise Refused("report_bad_module")
    if m.get("measurability") not in MEASURABILITY or _count(m["denominator"].get("value")) != denominator:
        raise Refused("report_bad_module")
    c = m["counts"]
    if set(c) != set(COUNT_KEYS):
        raise Refused("report_bad_counts")
    counts = {k: _opt_count(c[k]) for k in COUNT_KEYS}
    if sum(v for v in counts.values() if v is not None) != denominator:
        raise Refused("report_counts_do_not_add_up")
    warns = m.get("warnings")
    if not isinstance(warns, list) or len(warns) > MAX_WARNINGS:
        raise Refused("report_bad_module")
    clean_warns = []
    for w in warns:
        if not isinstance(w, dict) or not isinstance(w.get("code"), str) or not TOKEN_RE.fullmatch(w["code"]):
            raise Refused("report_bad_warning")
        clean_warns.append({"code": w["code"], "count": _count(w.get("count", 0))})
    problems = m.get("problems")
    if not isinstance(problems, list):
        raise Refused("report_bad_module")
    return {"measurability": m["measurability"], "denominator": denominator, "counts": counts, "coverage_pct": _percent(m.get("coverage_pct")),
            "missing_reasons": _tokens(m.get("missing_reasons", {})), "invalid_reasons": _tokens(m.get("invalid_reasons", {})),
            "facts": _facts(m.get("facts", {})), "warnings": clean_warns, "problem_count": len(problems)}


def sanitize(report, as_of):
    """The allowlisted summary of a coverage report made with --state-dir. Raises Refused for anything that differs from the reviewed shape."""
    if not isinstance(report, dict) or report.get("kind") != C.KIND or report.get("schema_version") != C.SCHEMA_VERSION:
        raise Refused("report_unexpected_kind_or_version")
    if report.get("generated_for") != as_of:
        raise Refused("report_date_mismatch")
    sf = report.get("state_files")
    if not isinstance(sf, dict) or set(sf) != set(STATE_FILES):
        raise Refused("report_has_no_state_files_block")
    files = {}
    for name in STATE_FILES:
        e = sf[name]
        if not isinstance(e, dict) or not isinstance(e.get("usable"), bool):
            raise Refused("report_bad_state_files_block")
        files[name] = {"usable": e["usable"], "entries": _opt_count(e.get("entries")), "problem": None if e.get("problem") is None else _token(e["problem"])}
    scopes = report.get("scopes")
    if not isinstance(scopes, dict) or set(scopes) != set(SCOPES):
        raise Refused("report_unexpected_scopes")
    out_scopes = {}
    for label in SCOPES:
        sc = scopes[label]
        if not isinstance(sc, dict) or not isinstance(sc.get("modules"), list):
            raise Refused("report_bad_scope")
        den = _count(sc.get("denominator"))
        by_name = {}
        for m in sc["modules"]:
            if isinstance(m, dict) and m.get("module") in STATE_MODULES:
                if m["module"] in by_name:
                    raise Refused("report_repeated_module")
                by_name[m["module"]] = m
        if set(by_name) != set(STATE_MODULES):
            raise Refused("report_state_modules_missing")
        out_scopes[label] = {"denominator": den, "modules": {n: _module(by_name[n], den) for n in STATE_MODULES}}
    return {"generated_for": as_of, "state_files": files, "scopes": out_scopes}


def provenance_from_env(env):
    out = {}
    for name, pattern in PROVENANCE_ENV.items():
        v = env.get(name)
        if not isinstance(v, str) or not re.fullmatch(pattern, v, re.ASCII):
            raise Refused("provenance_missing_or_malformed")
        out[name] = v
    return out


def render(summary, prov, hashes, marker):
    """Markdown built from the sanitised object only: numbers and plain tokens."""
    L = ["# Cache coverage validation", "",
         "**This is cache coverage, not publication status.** It counts what the saved updater caches hold. A cached record is not proof that anything reached the website, "
         "and a stock the cache lacks may still be published from another source. Counts only: no record, value, name, identifier, sector or symbol appears here.", "",
         "Reference date %s. Raw cache files stayed on the runner and were not uploaded." % summary["generated_for"], "", "## Provenance", "",
         "- cache key: `%s` (restored by exact key, no prefix match, nothing saved); branch `%s`; cache version `%s`" % (prov["CV_MATCHED_KEY"], prov["CV_CACHE_REF"], prov["CV_VERSION"]),
         "- cache created %s, last accessed %s before this restore, %s bytes" % (prov["CV_CREATED_AT"], prov["CV_LAST_ACCESSED_AT"], prov["CV_SIZE"]),
         "- **origin (corroborated, not proven by GitHub):** the key's run %s is a successful run (attempt %s, event `%s`, commit `%s`, %s to %s) of the workflow `%s` on the default "
         "branch, and the cache was created during that run. GitHub's cache list does not record which run created a cache; this link rests on the run id in the key checked "
         "against that run. %s newer cache(s) with an exact key belonged to `fundamentals_batch.yml` runs and were skipped."
         % (prov["CV_SOURCE_RUN_ID"], prov["CV_SOURCE_RUN_ATTEMPT"], prov["CV_SOURCE_RUN_EVENT"], prov["CV_SOURCE_RUN_HEAD_SHA"], prov["CV_SOURCE_RUN_CREATED_AT"],
            prov["CV_SOURCE_RUN_UPDATED_AT"], DAILY_WORKFLOW_NAME, prov["CV_SKIPPED_BATCH_CACHES"]),
         "- **origin, verified by the cache's own marker (not signed):** `%s` was found in the restored cache and agreed with the run's metadata (run id, attempt %d, workflow, "
         "branch, commit, cache key) and with the byte size and SHA-256 of all three state files, written %s. Data status recorded by the run: **%s**%s. Steps feeding the state files: %s."
         % (MARKER_NAME, marker["run_attempt"], marker["written_at"], marker["data_status"],
            " - NOT a full data refresh" if marker["data_status"] != "complete" else "", ", ".join("%s=%s" % kv for kv in marker["feeding"].items())),
         "- this validation: run %s, repository commit `%s`, data branch commit `%s`" % (prov["CV_RUN_ID"], prov["CV_MAIN_SHA"], prov["CV_DATA_BRANCH_SHA"]), "",
         "| State file | Usable | Entries | Problem | Bytes | SHA-256 |", "|---|---|---|---|---|---|"]
    for name in STATE_FILES:
        f, h = summary["state_files"][name], hashes[name]
        L.append("| %s | %s | %s | %s | %d | `%s` |" % (name, f["usable"], "n/a" if f["entries"] is None else f["entries"], f["problem"] or "none", h["bytes"], h["sha256"]))
    for label in SCOPES:
        sc = summary["scopes"][label]
        L += ["", "## %s (%d stocks)" % (label, sc["denominator"]), "", "| Module | Measurability | Valid | Stale | Missing | Invalid | Unverified | Coverage % |", "|---|---|---|---|---|---|---|---|"]
        for n in STATE_MODULES:
            m = sc["modules"][n]
            cell = lambda v: "n/a" if v is None else str(v)
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (n, m["measurability"], *(cell(m["counts"][k]) for k in ("valid", "stale", "missing", "invalid", "unverified")), cell(m["coverage_pct"])))
        for n in STATE_MODULES:
            m = sc["modules"][n]
            parts = []
            for title, d in (("missing", m["missing_reasons"]), ("invalid", m["invalid_reasons"]), ("unverified", m["facts"].get("unverified_reasons", {})), ("stale", m["facts"].get("stale_reasons", {}))):
                if d:
                    parts.append("%s: %s" % (title, ", ".join("%s=%d" % kv for kv in sorted(d.items()))))
            extra = ", ".join("%s=%d" % (k, m["facts"][k]) for k in SCALAR_FACTS if k in m["facts"])
            if extra:
                parts.append(extra)
            if m["warnings"]:
                parts.append("warnings: " + ", ".join("%s=%d" % (w["code"], w["count"]) for w in m["warnings"]))
            if parts:
                L.append("- %s - %s" % (n, "; ".join(parts)))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- command line
def _read_report(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_REPORT_BYTES:
        raise Refused("report_unreadable")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        raise Refused("report_not_json") from None


def main(argv, env=None):
    env = os.environ if env is None else env
    try:
        cmd = argv[1] if len(argv) > 1 else ""
        if cmd == "candidates" and len(argv) == 5:
            ids = candidate_run_ids(Path(argv[2]).read_text(encoding="utf-8"), argv[3])
            Path(argv[4]).write_text("\n".join(ids) + "\n", encoding="utf-8")
            print("cache_validation: %d candidate run(s)" % len(ids))
            return 0
        if cmd == "select" and len(argv) == 7:
            cache_text = Path(argv[2]).read_text(encoding="utf-8")
            runs = {}
            for rid in candidate_run_ids(cache_text, argv[5]):
                f = Path(argv[4]) / (rid + ".json")
                runs[rid] = f.read_text(encoding="utf-8") if f.is_file() and not f.is_symlink() else None
            c = select_daily_cache(cache_text, Path(argv[3]).read_text(encoding="utf-8"), runs, argv[5], argv[6])
            names = ("key", "created_at", "last_accessed_at", "size_in_bytes", "version", "ref", "run_id", "run_event", "run_head_sha", "run_attempt", "run_created_at",
                     "run_updated_at", "skipped_batch_caches")
            lines = ["%s=%s" % (n, c[n]) for n in names]
            target = env.get("GITHUB_OUTPUT")
            if target:
                with open(target, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            else:
                print("\n".join(lines))
            return 0
        if cmd == "check-files" and len(argv) == 3:
            check_files(argv[2])
            print("cache_validation: the three state files are present")
            return 0
        if cmd == "verify-marker" and len(argv) == 3:
            sel, prov = selection_from_env(env)
            verify_marker(argv[2], sel, prov["CV_REPOSITORY"], prov["CV_CACHE_REF"])
            print("cache_validation: the provenance marker and the three state files agree with the selected run")
            return 0
        if cmd == "summarize" and len(argv) == 5:
            sel, prov = selection_from_env(env)
            marker = verify_marker(argv[3], sel, prov["CV_REPOSITORY"], prov["CV_CACHE_REF"])      # verified again here: the summary never states an origin it did not check
            summary = sanitize(_read_report(argv[2]), prov["CV_AS_OF"])
            text = render(summary, prov, file_facts(argv[3]), marker)
            with open(argv[4], "a", encoding="utf-8") as fh:
                fh.write(text)
            print("cache_validation: summary written")
            return 0
        print("cache_validation: usage: candidates LIST REF OUT | select LIST WORKFLOW RUNS_DIR REF NOW | check-files DIR | verify-marker DIR | summarize REPORT DIR OUT")
        return 2
    except Refused as e:
        print("cache_validation: refused (%s)" % e.code)
        return 1
    except Exception:                                                   # never print the exception: it could carry data
        print("cache_validation: refused (internal_error)")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
