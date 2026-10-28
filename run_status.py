"""
run_status.py - a small per-updater run record, so a workflow summary can tell a COMPLETE run from an INCOMPLETE or FAILED one.

Why: the data steps of "Update data and publish site" run with continue-on-error, so the job shows success even when an updater failed or
stopped at its call budget. Each updater writes one JSON record here; report_run_status.py turns the records plus each step's outcome into
the step summary and a separate failing job.

  write(name, tally, **extra)   -> $RUN_STATUS_DIR/<name>.json   (does nothing when RUN_STATUS_DIR is not set: local runs and tests)
  status_of(record)             -> "complete" | "incomplete" | "failed"

A record is only counts. It never holds a token, a header or a provider response.
"""
import json
import os
from pathlib import Path

ENV = "RUN_STATUS_DIR"
NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_-")


def write(name, tally, **extra):
    d = (os.environ.get(ENV) or "").strip()
    if not d or not name or set(name) - NAME_OK:
        return None
    rec = {"updater": name, "tally": {k: int(v) for k, v in (tally or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}}
    for k, v in extra.items():
        if isinstance(v, (int, float, str)) and not isinstance(v, bool):
            rec[k] = v
    rec["status"] = status_of(rec)
    path = Path(d) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def status_of(rec):
    t = (rec or {}).get("tally") or {}
    if t.get("failed", 0) or t.get("errors", 0):
        return "incomplete" if (t.get("succeeded", 0) or t.get("stored", 0)) else "failed"
    if t.get("not_reached", 0) or t.get("skipped_by_call_cap", 0):
        return "incomplete"
    return "complete"
