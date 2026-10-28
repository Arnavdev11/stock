"""
report_run_status.py - the honest summary of the data steps in "Update data and publish site".

The data steps run with continue-on-error, so the build job (and the deploy that follows it) succeeds even when an updater failed or stopped at
its call budget. This script, run at the end of the build job with if: always(), combines

  * each step's own outcome   (environment OUTCOME_<NAME>, from steps.<id>.outcome: success | failure | cancelled | skipped), and
  * each updater's run record ($RUN_STATUS_DIR/<name>.json written by run_status.write: counts, never data)

into one table in the step summary, and sets the job outputs  data_status=complete|incomplete|failed  and  data_ok (false only when a step FAILED).
A separate job ("Data steps status") fails when data_ok is false, so the run shows as failed while the site is still published. An incomplete
run (a call budget reached, which is normal during a fill) is a warning and a row in the table, not a failed run.

  failed      the step failed, or its record says nothing succeeded
  incomplete  the step passed but stopped at its call budget, or some stocks failed
  complete    the step passed and its record reports nothing left undone
  skipped     the step did not run
Steps without a run record (NSE download, shareholding, validation, market activity) are judged by their outcome only.
"""
import json
import os
import sys
from pathlib import Path

import run_status

STEPS = (("nse", "NSE end-of-day files", None), ("fundamentals", "Fundamentals", "fundamentals"), ("financials", "Financial statements", "financials"),
         ("financial_history", "Financial history", "financial_history"), ("shareholding", "Shareholding", None),
         ("quarterly", "Quarterly results", "quarterly_results"), ("validate", "Validate outputs", None), ("market", "Market activity", None))


def judge(outcome, record):
    outcome = (outcome or "").strip().lower() or "unknown"
    if outcome in ("failure", "cancelled"):
        return "failed"
    if outcome == "skipped":
        return "skipped"
    if outcome != "success":
        return "unknown"
    return run_status.status_of(record) if record is not None else "complete"


def load(dir_, name):
    if not dir_ or not name:
        return None
    try:
        return json.loads((Path(dir_) / (name + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build(env):
    d = env.get(run_status.ENV, "")
    rows, worst = [], "complete"
    rank = {"complete": 0, "skipped": 1, "incomplete": 2, "unknown": 2, "failed": 3}
    for key, label, rec_name in STEPS:
        outcome = env.get("OUTCOME_" + key.upper(), "")
        rec = load(d, rec_name)
        if rec_name and rec is None and (outcome or "").lower() == "success":
            st = "incomplete"                                  # it passed but left no record: cannot be called complete
        else:
            st = judge(outcome, rec)
        t = (rec or {}).get("tally") or {}
        detail = ", ".join("%s %s" % (k.replace("_", " "), v) for k, v in sorted(t.items()) if v)
        if rec and rec.get("calls") is not None:
            detail = ("%s calls of %s; " % (rec.get("calls"), rec.get("call_cap", "?"))) + detail
        rows.append((label, outcome or "-", st, detail or "-"))
        if rank.get(st, 2) > rank.get(worst, 0) and st != "skipped":
            worst = st
    lines = ["### Data steps", "", "| Step | Step outcome | Data status | Details |", "|---|---|---|---|"]
    lines += ["| %s | %s | %s | %s |" % r for r in rows]
    lines += ["", "A step marked incomplete or failed did not finish its work; the site keeps the previous data for what it did not reach."]
    return "\n".join(lines) + "\n", worst


def main():
    text, worst = build(os.environ)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("data_status=%s\ndata_ok=%s\n" % (worst, "false" if worst == "failed" else "true"))
    if worst != "complete":
        print("::warning::Data steps finished %s. See the Data steps table in the run summary." % worst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
