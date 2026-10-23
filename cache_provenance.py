"""
cache_provenance.py - writes the provenance marker data/cache_provenance.json for the daily workflow ("Update data and publish site", update.yml).

Why it exists. GitHub's cache list does not say which run or workflow created a cache, and "Fundamentals coverage batch" names its caches the same way
(nse-data-<run id>). A small file INSIDE the saved data lets a later check read what the cache itself says about where it came from.

  remove DATA_DIR    Delete a marker that came back with the restored cache (it describes an EARLIER run). Run right after the restore, before any updater.
  write DATA_DIR     Write this run's marker, atomically (temp file, then replace). Run as the LAST step of the build job, after the last change to the data and
                     the data-step report, because it records the hashes and sizes of the three state files as they are at that moment.

The marker holds identifiers, times, step outcomes and file hashes. It holds no cache value, name, ISIN or symbol. It lives in data/, never in out/, and the site
build copies only out/ and a few named files, so it is never published.

What it is and is not. It binds the cache to a run id, an attempt, a commit, a branch, a time and the exact bytes of the three state files; a check that restores the
exact cache key can therefore reject a marker from another run, another attempt or a changed file. It is NOT signed: a workflow that can write caches on this branch
could write a marker too. GitHub caches are immutable: an existing key is never overwritten, so after a re-run of the same run id the cache keeps the FIRST attempt's
content and marker (the check refuses a marker whose attempt is not the run's latest).

Standard library only. Failures print a fixed code, never a value. The workflow step that runs this is continue-on-error: a failed marker step never changes the site
build, and a missing or invalid marker makes the validation refuse.
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

MARKER_NAME = "cache_provenance.json"
SCHEMA = 1
KIND = "cache_provenance"
DAILY_WORKFLOW_NAME = "Update data and publish site"
DAILY_WORKFLOW_FILE = ".github/workflows/update.yml"
CACHE_PATHS = ["data", "out"]
STATE_FILES = ("fundamentals_cache.json", "financials_cache.json", "quarterly_cache.json")
STEP_NAMES = ("nse", "fundamentals", "financials", "financial_history", "shareholding", "quarterly", "validate", "market")      # the ids report_run_status.py judges
EVENTS = ("schedule", "workflow_dispatch")
DATA_STATUSES = ("complete", "incomplete", "failed", "unknown")
OUTCOMES = ("success", "failure", "cancelled", "skipped", "unknown")
REPO_RE = re.compile(r"[A-Za-z0-9_.\-]{1,100}/[A-Za-z0-9_.\-]{1,100}", re.ASCII)
REF_RE = re.compile(r"refs/heads/[A-Za-z0-9._/\-]{1,100}", re.ASCII)
DIGITS_RE = re.compile(r"[0-9]{1,20}", re.ASCII)
SHA_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _file_facts(data_dir):
    out = {}
    for name in STATE_FILES:
        p = Path(data_dir) / name
        if p.is_symlink() or not p.is_file():
            out[name] = None                                   # recorded as absent; the check refuses a marker that says so
            continue
        h, n = hashlib.sha256(), 0
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                n += len(chunk)
        out[name] = {"bytes": n, "sha256": h.hexdigest()}
    return out


def _env(env, name, pattern=None, allowed=None):
    v = env.get(name)
    if not isinstance(v, str) or (pattern is not None and not pattern.fullmatch(v)) or (allowed is not None and v not in allowed):
        raise Refused("environment_" + name.lower())
    return v


def build_marker(data_dir, env, now=None):
    """The marker for the run described by the GITHUB_* environment (and the CP_* values the workflow passes), or Refused."""
    repo = _env(env, "GITHUB_REPOSITORY", REPO_RE)
    if env.get("GITHUB_WORKFLOW") != DAILY_WORKFLOW_NAME:
        raise Refused("not_the_daily_workflow")
    ref = _env(env, "GITHUB_REF", REF_RE)
    if env.get("GITHUB_WORKFLOW_REF") != "%s/%s@%s" % (repo, DAILY_WORKFLOW_FILE, ref):
        raise Refused("not_the_daily_workflow")
    run_id = _env(env, "GITHUB_RUN_ID", DIGITS_RE)
    attempt = int(_env(env, "GITHUB_RUN_ATTEMPT", DIGITS_RE))
    if attempt < 1:
        raise Refused("environment_github_run_attempt")
    status = env.get("CP_DATA_STATUS") or "unknown"
    if status not in DATA_STATUSES:
        status = "unknown"
    outcomes = {}
    for s in STEP_NAMES:
        v = env.get("CP_OUTCOME_" + s.upper()) or "unknown"
        outcomes[s] = v if v in OUTCOMES else "unknown"
    when = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)
    return {
        "schema": SCHEMA, "kind": KIND, "repository": repo, "workflow": DAILY_WORKFLOW_NAME, "workflow_ref": env["GITHUB_WORKFLOW_REF"],
        "run_id": run_id, "run_attempt": attempt, "event": _env(env, "GITHUB_EVENT_NAME", allowed=EVENTS), "ref": ref, "sha": _env(env, "GITHUB_SHA", SHA_RE),
        "cache_key": "nse-data-" + run_id, "paths": list(CACHE_PATHS), "written_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_status": status, "data_ok": env.get("CP_DATA_OK") == "true", "step_outcomes": outcomes, "files": _file_facts(data_dir),
    }


def _checked_dir(data_dir):
    d = Path(data_dir)
    if d.is_symlink() or not d.is_dir():
        raise Refused("data_dir_not_a_folder")
    if d.resolve().name == "out":
        raise Refused("data_dir_is_the_published_folder")      # the marker must never be placed where the site copies from
    return d


def write_marker(data_dir, env, now=None):
    d = _checked_dir(data_dir)
    marker = build_marker(d, env, now)
    text = json.dumps(marker, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
    target, tmp = d / MARKER_NAME, d / (MARKER_NAME + ".tmp")
    try:
        if tmp.is_symlink() or tmp.is_file():
            tmp.unlink()                                       # a stale temp file (or a link planted in its place) is never written through
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)       # exclusive: refuses anything that exists, follows no link
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)                                # atomic: a crash never leaves a half-written marker
    finally:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
    return marker


def remove_marker(data_dir):
    d = _checked_dir(data_dir)
    for name in (MARKER_NAME, MARKER_NAME + ".tmp"):
        p = d / name
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.exists():
            raise Refused("marker_path_is_not_a_file")


def main(argv, env=None):
    env = os.environ if env is None else env
    try:
        if len(argv) == 3 and argv[1] == "write":
            write_marker(argv[2], env)
            print("cache_provenance: marker written")
            return 0
        if len(argv) == 3 and argv[1] == "remove":
            remove_marker(argv[2])
            print("cache_provenance: no earlier marker remains")
            return 0
        print("cache_provenance: usage: write DATA_DIR | remove DATA_DIR")
        return 2
    except Refused as e:
        print("cache_provenance: refused (%s)" % e.code)
        return 1
    except Exception:                                          # never print the exception
        print("cache_provenance: refused (internal_error)")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
