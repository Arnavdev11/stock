"""
backfill_controller.py - plans and verifies the next bounded backfill run. It FETCHES NOTHING and STARTS NOTHING.

The existing updaters and workflows keep doing all the fetching, pacing, retrying, ledger writing and saving. This tool only
  next    reads a checkout of the data branch and says what the next bounded run should be: which workflow, which inputs, which cohort (up to 500),
          or why there is nothing to do (nothing left, paused, everything left is cooling down, the dataset is not safe to continue automatically);
  verify  compares the data branch BEFORE and AFTER a run and classifies each stock of the cohort (improved, refreshed, unchanged, needs review, regressed),
          fails when anything saved was lost, and reports the next cohort or the stopping reason.
Nothing here is a ledger: progress is always derived from the data (and the candle updater's own attempts.json), never from a marker.

Datasets that can continue automatically (their state is saved on the data branch by the existing hand-over): historical_candles, financial_history.
Datasets that cannot (their only state is the evictable Actions cache, or the collector is fixed by design): fundamentals, quarterly_results,
annual_financials, shareholding. `next` refuses them and says why; it never guesses their coverage.

    python3 backfill_controller.py next   --data-branch DIR --dataset historical_candles --as-of YYYY-MM-DD --out OUTDIR [--batch-size 500] [--max-calls N]
                                          [--priority-file FILE] [--paused] [--dry-run]
    python3 backfill_controller.py verify --before DIR --after DIR (--plan OUTDIR/backfill_next.json | --dataset NAME --cohort A,B,C) --as-of YYYY-MM-DD --out OUTDIR2
                                          [--calls-used N] [--dry-run]

Exit codes: 0 ok (including every graceful "nothing to do / paused / waiting" result: read "status"), 1 verification failed (data was lost), 2 refused or bad usage.
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import sys
from pathlib import Path

import backfill_plan as BP
import coverage_report as C
import historical_batch as HB
import ledger_storage

SCHEMA_VERSION = 1
KIND_NEXT = "backfill_next"
KIND_VERIFY = "backfill_verify"
MAX_COHORT = 500
MAX_PRIORITY = 500
REVIEW_AFTER = 3                       # a candle batch that failed this many times for one stock needs a person to look (attempts.json count)
FH_DEFAULT_CALLS = 120                 # financial_history_updater.DEFAULT_MAX_CALLS
FH_CALL_CEILING = 1500                 # this tool's own refusal limit (about 50 minutes at 2 s per call, the figure update.yml documents for financials); not an updater limit
MAX_PLAN_BYTES = 2_000_000

AUTOMATIC = {
    "historical_candles": {"workflow": "historical.yml", "default_calls": HB.DEFAULT_BUDGET, "call_ceiling": HB.CALL_CEILING,
                           "persistence": "data branch: historical/shards/*.json, historical/historical.json and historical/attempts.json (saved by save_historical.yml after a checked hand-over)"},
    "financial_history": {"workflow": "financial_history.yml", "default_calls": FH_DEFAULT_CALLS, "call_ceiling": FH_CALL_CEILING,
                          "persistence": "data branch: financial_history_ledger.json (saved by save_ledger.yml; the updater's no-loss check runs before anything is written)"},
}
MANUAL_ONLY = {
    "fundamentals": "its state lives only in the GitHub Actions cache, which is immutable and can be evicted; use fundamentals_batch.yml by hand",
    "quarterly_results": "its state lives only in the GitHub Actions cache; it runs inside the daily update.yml with its own re-check windows",
    "annual_financials": "its state lives only in the GitHub Actions cache; it runs inside the daily update.yml",
    "shareholding": "a fixed 15-stock NSE/XBRL collector by design; widening it needs a separate approval",
}


class ControllerError(Exception):
    pass


# ---------------------------------------------------------------- helpers
def _cap_dataset(dataset):
    if dataset in AUTOMATIC:
        return AUTOMATIC[dataset]
    if dataset in MANUAL_ONLY:
        return None
    raise ControllerError("--dataset must be one of: " + ", ".join(list(AUTOMATIC) + list(MANUAL_ONLY)))


def _int_arg(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ControllerError("%s must be a whole number from %d to %d" % (name, low, high))
    return value


def _read_json(path, limit=MAX_PLAN_BYTES):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > limit:
        raise ControllerError("%s is not a readable regular file of reasonable size" % p.name)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        raise ControllerError("%s is not valid JSON" % p.name) from None


def _coverage(data_dir, as_of, dataset):
    """The planner's own measurement of one dataset (separate coverage_report module run). Raises ControllerError when the files cannot be read."""
    try:
        return BP.build_plan(data_dir, as_of, None, MAX_COHORT, [dataset])
    except BP.PlanError as e:
        raise ControllerError(str(e)) from None


def _fh_sections(entry):
    fs = entry.get("fetch_state") if isinstance(entry, dict) else None
    return fs if isinstance(fs, dict) else {}


SHA_RE = re.compile(r"[0-9a-f]{40}")


def _sha_arg(value, name):
    if value is None:
        return None
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ControllerError("%s must be a 40-character lowercase commit SHA" % name)
    return value


def parse_priority(tokens, universe_symbols, what):
    """Validated priority symbols, in the order given. Every refusal names the position, never the text: a bad entry cannot be echoed or mistaken for an option."""
    known, out, seen = set(universe_symbols), [], set()
    if len(tokens) > MAX_PRIORITY:
        raise ControllerError("%s names more than %d symbols" % (what, MAX_PRIORITY))
    for i, t in enumerate(tokens, 1):
        if not isinstance(t, str) or not BP.SYMBOL_RE.fullmatch(t):
            raise ControllerError("%s: entry %d is not a valid upper-case symbol" % (what, i))
        if t not in known:
            raise ControllerError("%s: entry %d is not in the universe" % (what, i))
        if t in seen:
            raise ControllerError("%s: entry %d repeats an earlier symbol" % (what, i))
        seen.add(t)
        out.append(t)
    return out


def priority_from_text(text, universe_symbols):
    """A comma/space/newline separated list (the workflow input). An empty entry between commas is ignored; anything else must be a symbol."""
    return parse_priority([t for t in re.split(r"[,\s]+", (text or "").strip()) if t], universe_symbols, "--priority-symbols")


def priority_from_file(path, universe_symbols):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > BP.MAX_PRIORITY_BYTES:
        raise ControllerError("--priority-file is not a readable regular file of reasonable size")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise ControllerError("--priority-file cannot be read as text") from None
    return parse_priority([l.strip() for l in lines if l.strip() and not l.strip().startswith("#")], universe_symbols, "--priority-file")


# ---------------------------------------------------------------- next
def _next_candles(data_dir, as_of, batch_size, max_calls, priority):
    directory = HB.load_directory(Path(data_dir) / "stock_directory.json")
    saved = Path(data_dir) / "historical"
    last, bench_last, damaged = HB.saved_state(saved)
    attempts = HB.load_attempts(saved)
    end = as_of - dt.timedelta(days=1)                      # the last complete day before 16:00 IST; the workflow recomputes the real value
    ceiling = min(max_calls, HB.CALL_CEILING)
    bench_calls = HB._cost(bench_last, end)
    usable = int(ceiling * HB.HEADROOM) - bench_calls
    if usable < 1:
        raise ControllerError("--max-calls is too small to plan any stock")
    in_dir, bad = set(directory), set(damaged)
    cutoff = (end - dt.timedelta(days=HB.FRESH_DAYS)).isoformat()
    cool = {s: dt.date.fromisoformat(v["last"]) + dt.timedelta(days=HB.COOLDOWN_DAYS) for s, v in attempts.items()}
    skipped = {"not_in_directory": 0, "damaged": 0, "fresh": 0, "cooling_down": 0}
    include, used = [], 0
    for s in priority:
        if s not in in_dir:
            skipped["not_in_directory"] += 1
        elif s in bad:
            skipped["damaged"] += 1
        elif last.get(s) and last[s] >= cutoff:
            skipped["fresh"] += 1
        elif s in cool and as_of < cool[s]:
            skipped["cooling_down"] += 1
        else:
            c = HB._cost(last.get(s), end)
            if used + c > usable or len(include) >= batch_size - 1:
                break
            include.append(s)
            used += c
    rep = HB.plan(directory, last, damaged, attempts, end, as_of, max(1, batch_size - len(include)), max_calls, include, None, bench_calls)
    cohort = rep["symbols"]
    c = rep["counts"]
    remaining = c["never_fetched_left_after_this_batch"] + c["stale_left_after_this_batch"]
    left_calls = sum(HB._cost(last.get(s), end) for s in directory if s not in bad and not (last.get(s) and last[s] >= cutoff))
    run = None
    status, reason = "ready", None
    if not cohort:
        if c["cooling_down"]:
            wake = min(cool[s] for s in rep["cooling_down"])
            status, reason = "waiting_cooldown", "every stock that still needs data failed recently and is cooling down; the earliest can be planned again on %s" % wake.isoformat()
        else:
            status, reason = "nothing_to_do", "every stock in the directory is up to date, damaged (needs a look) or not planned"
    else:
        run = {"workflow": "historical.yml", "inputs": {"symbols": ",".join(include) if include else "TCS", "batch_size": str(max(1, batch_size - len(include))), "max_calls": str(max_calls)},
               "note": "TCS is the workflow's own always-updated test stock; it is sent only when no priority stock needs work. The workflow re-plans on the day it runs and also skips stocks Upstox does not serve as NSE equity."
               if not include else "The listed symbols are updated first, then the workflow picks the rest of the batch itself (never fetched first, then the stalest).",
               "estimated_calls": rep["estimated_calls"], "estimated_minutes": rep["estimated_minutes"], "call_budget": max_calls, "planned_call_limit": rep["planned_call_limit"]}
    review = sorted(s for s, v in attempts.items() if isinstance(v, dict) and v.get("count", 0) >= REVIEW_AFTER)
    queue = {"never_fetched": c["never_fetched"] if "never_fetched" in c else None, "never_fetched_left_after_this_run": c["never_fetched_left_after_this_batch"],
             "stale_left_after_this_run": c["stale_left_after_this_batch"], "cooling_down": c["cooling_down"], "damaged": c["damaged"], "fresh": c["fresh"],
             "needs_review_after_%d_failed_attempts" % REVIEW_AFTER: review[:100], "unsupported_by_upstox": "unknown offline (the workflow filters these with the Upstox instrument file)",
             "calls_still_needed_estimate": left_calls, "runs_still_needed_estimate": math.ceil(left_calls / usable) if left_calls else 0}
    return status, reason, run, cohort, queue, {"included": len(include), "skipped": skipped}


def _next_fh(data_dir, as_of, batch_size, max_calls, priority):
    try:
        d = C.Data(data_dir, None, as_of, None)
        d.load_universe()
    except C.ReportError as e:
        raise ControllerError(str(e)) from None
    fh = d.fh
    if not (isinstance(fh, dict) and isinstance(fh.get("stocks"), dict)):
        raise ControllerError("financial_history_ledger.json is " + str(d.fh_problem or "not shaped like a ledger"))
    stocks = fh["stocks"]
    import financial_history_updater as FH
    today = as_of.isoformat()

    def classify(sym):
        e = stocks.get(sym)
        if e is None:
            return "missing"
        ys, prob = C._fh_entry(sym, e, d.isin.get(sym))
        if prob:
            return "invalid"
        if not ys:
            return "missing_no_years"
        sec = _fh_sections(e)
        states = [sec.get(k) for k in ("income", "cash_flow")]
        if all(isinstance(x, dict) and FH.is_fresh(x, today) for x in states):
            return "fresh"
        if any(isinstance(x, dict) and x.get("error") for x in states):
            return "recorded_error"
        return "due"

    kinds = {s: classify(s) for s in d.symbols}
    counts = {}
    for k in kinds.values():
        counts[k] = counts.get(k, 0) + 1
    needy = [s for s in d.symbols if kinds[s] in ("missing", "missing_no_years", "due", "invalid")]
    errored = [s for s in d.symbols if kinds[s] == "recorded_error"]
    pri = [s for s in priority if s in set(needy)]
    rest = [s for s in needy if s not in set(pri)]
    pool = pri + rest
    retry_pass = False
    if not pool and errored:
        pool, retry_pass = list(errored), True
    cohort = pool[:batch_size]
    run, status, reason = None, "ready", None
    per_stock = 2
    runs_calls = len(needy) * per_stock
    ok_per_run = max_calls // per_stock
    if not cohort:
        status, reason = "nothing_to_do", "every stock has income and cash-flow data fetched within the last %d days" % FH.REFRESH_DAYS
    else:
        run = {"workflow": "financial_history.yml", "inputs": {"symbols": ",".join(cohort), "max_calls": str(max_calls)},
               "note": ("Only stocks whose last fetch recorded an error remain, so this is a retry pass." if retry_pass else
                        "The updater processes the listed symbols stalest first and stops at its call budget; the rest go first next time."),
               "estimated_calls": min(len(cohort) * per_stock, max_calls), "estimated_minutes": round(min(len(cohort) * per_stock, max_calls) * BP.SECONDS_PER_CALL / 60, 1),
               "call_budget": max_calls, "stocks_reachable_this_run": min(len(cohort), ok_per_run)}
    queue = {"counts": counts, "recorded_error_deferred": 0 if retry_pass else len(errored), "retry_pass": retry_pass,
             "calls_still_needed_estimate": runs_calls, "runs_still_needed_estimate": math.ceil(runs_calls / max_calls) if runs_calls else 0,
             "note": "financial_history has no attempts counter: a stock that keeps failing is only deferred behind the others, never classified permanently unavailable"}
    return status, reason, run, cohort, queue, {"included": len(pri[:batch_size]), "skipped": {"not_needing_work_or_not_in_universe": len([s for s in priority if s not in set(needy)])}}


def next_plan(data_dir, dataset, as_of, batch_size=MAX_COHORT, max_calls=None, priority_file=None, paused=False, priority_text=None, data_sha=None):
    if not isinstance(as_of, dt.date):
        raise ControllerError("as_of must be a date")
    _int_arg(batch_size, "--batch-size", 1, MAX_COHORT)
    _sha_arg(data_sha, "--data-sha")
    if priority_file and priority_text:
        raise ControllerError("give --priority-file or --priority-symbols, not both")
    spec = _cap_dataset(dataset)
    base = {"schema_version": SCHEMA_VERSION, "kind": KIND_NEXT, "dataset": dataset, "generated_for": as_of.isoformat(),
            "tool": {"name": "backfill_controller.py", "reads_only": True, "provider_calls": 0, "dispatches_workflows": False}, "data_branch_sha": data_sha}
    if spec is None:
        base.update({"status": "not_automatic", "stop_reason": MANUAL_ONLY[dataset], "automatic_continuation": False, "run": None, "cohort": []})
        return base
    if max_calls is None:
        max_calls = spec["default_calls"]
    _int_arg(max_calls, "--max-calls", 1, spec["call_ceiling"])
    if not Path(data_dir).is_dir():
        raise ControllerError("--data-branch is not a folder")
    cover = _coverage(data_dir, as_of, dataset)
    base.update({"automatic_continuation": True, "persistence": spec["persistence"], "batch_size": batch_size, "max_calls": max_calls, "paused": bool(paused),
                 "coverage": cover["totals"][dataset], "universe": cover["universe"]})
    if paused:
        base.update({"status": "paused", "stop_reason": "paused by the operator (--paused / BACKFILL_PAUSED); nothing was planned", "run": None, "cohort": []})
        return base
    symbols = cover["batches"] and [s for b in cover["batches"] for s in b["symbols"]] or []
    priority = priority_from_file(priority_file, symbols) if priority_file else (priority_from_text(priority_text, symbols) if priority_text else [])
    try:
        fn = _next_candles if dataset == "historical_candles" else _next_fh
        status, reason, run, cohort, queue, pri = fn(data_dir, as_of, batch_size, max_calls, priority)
    except HB.BatchError as e:
        raise ControllerError(str(e)) from None
    base.update({"status": status, "stop_reason": reason, "run": run, "cohort": cohort, "cohort_size": len(cohort), "queue": queue, "priority": {"supplied": len(priority), **pri},
                 "limits": ["A batch of up to %d stocks is a cohort, not simultaneous requests: the updater still paces, retries and stops at its own call budget." % MAX_COHORT,
                            "Counts are what the files hold, not what the provider can supply.",
                            "Nothing is dispatched by this tool: the printed workflow and inputs are for a person (or a later, separately approved scheduler) to start."]})
    return base


# ---------------------------------------------------------------- verify
GAP_WARN_DAYS = 10             # a calendar gap this long between two saved candles is reported (exchange holidays are never this long); a warning, never a failure


def _candle_rows(data_dir, sym):
    """The saved candle rows of one stock from its own file, or None when the file is missing or unreadable."""
    try:
        rows = json.loads((Path(data_dir) / "historical" / "shards" / (sym + ".json")).read_text(encoding="utf-8"))["stocks"][sym]["candles"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return rows if isinstance(rows, list) else None


def _max_gap_days(dates):
    best = 0
    for x, y in zip(dates, dates[1:]):
        try:
            best = max(best, (dt.date.fromisoformat(y) - dt.date.fromisoformat(x)).days)
        except (TypeError, ValueError):
            return None
    return best


def _verify_candles(before, after, cohort):
    """Data LOSS check over every saved stock (not completeness: see the 'completeness' note in the report)."""
    bsaved, asaved = Path(before) / "historical", Path(after) / "historical"
    b_last, b_bench, b_damaged = HB.saved_state(bsaved)
    a_last, a_bench, a_damaged = HB.saved_state(asaved)
    b_att, a_att = HB.load_attempts(bsaved), HB.load_attempts(asaved)
    names = set()
    for root in (bsaved, asaved):
        if (root / "shards").is_dir():
            names |= {p.stem for p in (root / "shards").glob("*.json") if BP.SYMBOL_RE.fullmatch(p.stem)}
    lost, regressed, changed, warnings = [], set(), set(), []
    for s in sorted(names | set(b_last) | set(a_last) | set(b_damaged) | set(a_damaged)):
        brows, arows = _candle_rows(before, s), _candle_rows(after, s)
        if brows is None and arows is None:                          # only in the single legacy file (or damaged in both): compare the last date it reports
            bl, al = b_last.get(s), a_last.get(s)
            if bl and (not al or al < bl):
                regressed.add(s)
                lost.append("%s: the saved history is missing or was shortened" % s)
            if bl != al:
                changed.add(s)
            continue
        bdates = [c.get("date") for c in brows or [] if isinstance(c, dict)]
        if brows and arows is None:
            regressed.add(s)
            lost.append("%s: the saved candle file is missing or damaged" % s)
            continue
        if arows is not None:
            prob = C._candle_problem(arows) if arows else "empty_candle_list"
            if prob and (brows or arows):
                regressed.add(s)
                lost.append("%s: the saved candles are malformed (%s)" % (s, prob))
                continue
        adates = [c.get("date") for c in arows or [] if isinstance(c, dict)]
        gone = set(bdates) - set(adates)
        if gone:
            regressed.add(s)
            lost.append("%s: %d saved candle date(s) are gone (history shortened or rewritten)" % (s, len(gone)))
        if bdates != adates:
            changed.add(s)
        if s in cohort and adates:
            ga, gb = _max_gap_days(adates), (_max_gap_days(bdates) if bdates else 0)
            if ga and ga > GAP_WARN_DAYS and ga > (gb or 0):
                warnings.append("%s: a %d-day gap between two saved candles (not checked against the exchange calendar)" % (s, ga))
    if b_bench and (not a_bench or a_bench < b_bench):
        lost.append("benchmark: its saved history was lost or shortened")
    for s in set(b_att) | set(a_att):
        if b_att.get(s) != a_att.get(s):
            changed.add(s)
    out = {}
    for s in cohort:
        bl, al = b_last.get(s), a_last.get(s)
        att = a_att.get(s)
        if s in regressed:
            out[s] = "regressed"
        elif al and (not bl or al > bl):
            out[s] = "improved" if not bl else "refreshed"
        elif att and att.get("count", 0) >= REVIEW_AFTER and att != b_att.get(s):
            out[s] = "needs_review"
        elif att and att != b_att.get(s):
            out[s] = "retryable_failed"
        else:
            out[s] = "not_reached"
    return out, lost, changed, warnings


def _verify_fh(before, after, cohort):
    def load(d):
        doc = _read_json(Path(d) / "financial_history_ledger.json", 200_000_000)
        if not isinstance(doc, dict) or not isinstance(doc.get("stocks"), dict):
            raise ControllerError("financial_history_ledger.json is not shaped like a ledger")
        return doc
    b, a = load(before), load(after)
    lost = list(ledger_storage.no_loss_problems(b, a))[:50]             # checks EVERY stock, not just the cohort
    bad = {x.split(" ")[0].rstrip(":") for x in lost}
    changed = {s for s in set(b["stocks"]) | set(a["stocks"]) if b["stocks"].get(s) != a["stocks"].get(s)}
    out = {}
    for s in cohort:
        be, ae = b["stocks"].get(s), a["stocks"].get(s)
        by = {y.get("fy") for y in (be or {}).get("years") or [] if isinstance(y, dict)}
        ay = {y.get("fy") for y in (ae or {}).get("years") or [] if isinstance(y, dict)}
        if s in bad or (by - ay):
            out[s] = "regressed"
        elif ay - by:
            out[s] = "improved"
        elif s not in changed:
            out[s] = "not_reached"
        elif any(isinstance(x, dict) and x.get("error") for x in _fh_sections(ae).values()):
            out[s] = "retryable_failed"
        else:
            out[s] = "refreshed"
    return out, lost, changed, []


def verify_run(before, after, plan, as_of, calls_used=None, paused=False, before_sha=None, after_sha=None):
    if not isinstance(plan, dict) or plan.get("kind") != KIND_NEXT or plan.get("schema_version") != SCHEMA_VERSION or plan.get("dataset") not in AUTOMATIC:
        raise ControllerError("--plan is not a plan written by `next` for an automatic dataset")
    cohort = plan.get("cohort")
    if not isinstance(cohort, list) or not cohort or len(cohort) > MAX_COHORT or any(not isinstance(s, str) or not BP.SYMBOL_RE.fullmatch(s) for s in cohort) or len(set(cohort)) != len(cohort):
        raise ControllerError("--plan has no valid cohort of up to %d symbols" % MAX_COHORT)
    if calls_used is not None:
        _int_arg(calls_used, "--calls-used", 0, 10_000_000)
    _sha_arg(before_sha, "--before-sha")
    _sha_arg(after_sha, "--after-sha")
    planned_sha = plan.get("data_branch_sha")
    if before_sha and planned_sha and before_sha != planned_sha:
        raise ControllerError("--before-sha differs from the data-branch SHA recorded in the plan: the plan was made for a different snapshot")
    dataset = plan["dataset"]
    outcomes, lost, changed, warnings = (_verify_candles if dataset == "historical_candles" else _verify_fh)(before, after, cohort)
    tally = {}
    for v in outcomes.values():
        tally[v] = tally.get(v, 0) + 1
    in_cohort = set(cohort)
    outside = sorted(changed - in_cohort)
    cover_before, cover_after = _coverage(before, as_of, dataset)["totals"][dataset], _coverage(after, as_of, dataset)["totals"][dataset]
    if cover_before.get("measurable") and cover_after.get("measurable") and cover_after["invalid"] > cover_before["invalid"]:
        lost.append("%d more record(s) fail coverage_report.py's structural checks than before the run" % (cover_after["invalid"] - cover_before["invalid"]))
    ok = not lost and not tally.get("regressed")
    saved_anything = bool(changed)
    nxt = next_plan(after, dataset, as_of, plan.get("batch_size", MAX_COHORT), plan.get("max_calls"), None, paused, data_sha=after_sha) if (ok and saved_anything) else None
    gained = cover_after["valid"] - cover_before["valid"] if cover_before.get("measurable") and cover_after.get("measurable") else None
    processed = sum(v for k, v in tally.items() if k != "not_reached")
    return {"schema_version": SCHEMA_VERSION, "kind": KIND_VERIFY, "dataset": dataset, "generated_for": as_of.isoformat(),
            "tool": {"name": "backfill_controller.py", "reads_only": True, "provider_calls": 0, "dispatches_workflows": False},
            "data_branch_sha_before": before_sha, "data_branch_sha_after": after_sha,
            "verification": "passed" if ok else "failed", "problems": lost[:100],
            "checked": "every saved stock and the benchmark (not only the cohort): lost or damaged files, any saved candle date gone, malformed candles, lost years or values (DATA LOSS, not completeness)",
            "saved_anything": saved_anything, "warnings": warnings[:50],
            "publish_or_continue": ("BLOCKED: saved data was lost, shortened or malformed; do not continue, do not publish; inspect the data branch history" if not ok else
                                    "allowed: no saved data was lost" if saved_anything else
                                    "NOTHING CHANGED between the two snapshots: the run may not have finished saving, or processed nothing. Do not plan the next run from this snapshot; check the run and verify again"),
            "completeness": {"checked_here": "coverage counts before and after (coverage_report.py's own structural checks: ascending dates, numeric positive prices, newest date, required fields)",
                             "not_checked_by_any_tool": "missing trading days inside a candle history: the exchange holiday calendar is not in the repository, so only long gaps (over %d days) are reported as warnings" % GAP_WARN_DAYS,
                             "also_run": "python3 coverage_report.py --data-branch AFTER --as-of DATE --out OUT  (read-only; see BACKFILL.md)"},
            "cohort_size": len(cohort), "outcomes": dict(sorted(tally.items())), "per_stock": outcomes,
            "processed": {"cohort_stocks_with_evidence_of_processing": processed, "cohort_stocks_not_reached": tally.get("not_reached", 0),
                          "changed_outside_the_planned_cohort": len(outside), "changed_outside_examples": outside[:20],
                          "note": "The workflow re-plans on its own day (and filters stocks Upstox does not serve), so its real cohort can differ from the printed one. This compares what actually changed in the data with the printed list."},
            "calls_used": calls_used if calls_used is not None else None,
            "calls_used_note": None if calls_used is not None else "not recorded in the data; read it from the run's own status summary and pass --calls-used",
            "failures_in_cohort": tally.get("retryable_failed", 0) + tally.get("needs_review", 0),
            "coverage_before": cover_before, "coverage_after": cover_after, "valid_gained": gained,
            "progress_pct": cover_after.get("coverage_pct") if cover_after.get("measurable") else None,
            "next": ({"status": nxt["status"], "stop_reason": nxt["stop_reason"], "cohort_size": nxt.get("cohort_size", 0), "run": nxt.get("run"), "queue": nxt.get("queue")} if nxt else None),
            "completion": {"dataset_verified_complete": bool(cover_after.get("measurable") and cover_after.get("complete")),
                           "platform_complete": False, "note": "the platform is never declared complete while any dataset is unmeasurable"}}


# ---------------------------------------------------------------- rendering and output
def render_json(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def render_markdown(doc):
    if doc["kind"] == KIND_NEXT:
        L = ["# StockLens backfill - next run", "", "Dataset: **%s**. Generated for %s. Read-only: nothing was fetched or dispatched." % (doc["dataset"], doc["generated_for"]), "",
             "**Status: %s.**%s" % (doc["status"], (" " + doc["stop_reason"]) if doc.get("stop_reason") else ""),
             "Data branch checked: %s." % (doc.get("data_branch_sha") or "SHA not supplied (pass --data-sha to record it)")]
        run = doc.get("run")
        if run:
            L += ["", "Run the existing workflow **%s** with these inputs:" % run["workflow"], ""] + ["- `%s`: %s" % (k, v if len(str(v)) < 160 else str(v)[:157] + "...") for k, v in sorted(run["inputs"].items())]
            L += ["", "Cohort: %d stock(s). Estimated %d call(s), about %s minute(s), within a budget of %d." % (doc["cohort_size"], run["estimated_calls"], run["estimated_minutes"], run["call_budget"]), run["note"],
                  "", "Cohort symbols (copy into `verify` as the cohort):", "", ",".join(doc["cohort"])]
        q = doc.get("queue")
        if q:
            L += ["", "Queue after this run: " + json.dumps(q, sort_keys=True)[:900]]
        c = doc.get("coverage")
        if c and c.get("measurable"):
            L += ["", "Coverage now: %s of %s valid (%s%%), %s missing, %s stale, %s invalid." % (c["valid"], c["stocks"], c["coverage_pct"], c["missing"], c["stale"], c["invalid"])]
        L += ["", "## Limits", ""] + ["- " + x for x in doc.get("limits", [])]
    else:
        L = ["# StockLens backfill - run verification", "", "Dataset: **%s**. Verification: **%s**. %s" % (doc["dataset"], doc["verification"], doc["publish_or_continue"]), ""]
        L += ["Data branch before: %s; after: %s." % (doc.get("data_branch_sha_before") or "not supplied", doc.get("data_branch_sha_after") or "not supplied"),
              "Cohort %d stock(s): %s." % (doc["cohort_size"], ", ".join("%d %s" % (v, k) for k, v in doc["outcomes"].items())),
              "Actually processed: %d of the cohort; %d not reached; %d stock(s) changed outside the printed cohort." % (doc["processed"]["cohort_stocks_with_evidence_of_processing"], doc["processed"]["cohort_stocks_not_reached"], doc["processed"]["changed_outside_the_planned_cohort"]),
              "Calls used: %s. Failures in the cohort: %d. Valid records gained: %s. Progress: %s%%." % (doc["calls_used"] if doc["calls_used"] is not None else "unknown (%s)" % doc["calls_used_note"], doc["failures_in_cohort"], doc["valid_gained"], doc["progress_pct"])]
        if doc["problems"]:
            L += ["", "Problems:"] + ["- " + p for p in doc["problems"][:30]]
        if doc.get("warnings"):
            L += ["", "Warnings (not failures):"] + ["- " + p for p in doc["warnings"][:30]]
        L += ["", "Completeness: " + doc["completeness"]["not_checked_by_any_tool"] + ". Also run: " + doc["completeness"]["also_run"] + "."]
        n = doc.get("next")
        if n:
            L += ["", "Next: **%s**%s" % (n["status"], (" - " + n["stop_reason"]) if n.get("stop_reason") else ""), "Next cohort: %d stock(s)." % n["cohort_size"]]
        L += ["", "Platform complete: no. " + doc["completion"]["note"] + "."]
    return "\n".join(L) + "\n"


def write_outputs(out_dir, files):
    out_dir.mkdir(exist_ok=True)
    for name, text in files.items():
        target = out_dir / name
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise ControllerError("%s exists and is not a regular file" % name)
        tmp = out_dir / (name + ".tmp")
        if tmp.is_symlink():
            tmp.unlink()
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(str(tmp), str(target))


def _is_paused(flag):
    return bool(flag) or os.environ.get("BACKFILL_PAUSED", "").strip().lower() == "true"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backfill_controller.py", description="Plans and verifies bounded backfill runs. Fetches nothing, starts nothing.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next")
    n.add_argument("--data-branch", required=True)
    n.add_argument("--dataset", required=True)
    n.add_argument("--as-of", required=True)
    n.add_argument("--out", required=True)
    n.add_argument("--batch-size", type=int, default=MAX_COHORT)
    n.add_argument("--max-calls", type=int, default=None)
    n.add_argument("--priority-file", default=None)
    n.add_argument("--priority-symbols", default=None, help="comma list of symbols to plan first (validated against the universe)")
    n.add_argument("--data-sha", default=None, help="the 40-character stocklens-data commit SHA this checkout is at; recorded in the plan")
    n.add_argument("--paused", action="store_true")
    n.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("--before", required=True)
    v.add_argument("--after", required=True)
    v.add_argument("--plan", default=None)
    v.add_argument("--dataset", default=None)
    v.add_argument("--cohort", default=None, help="comma list of the symbols the run was given (instead of --plan)")
    v.add_argument("--as-of", required=True)
    v.add_argument("--out", required=True)
    v.add_argument("--calls-used", type=int, default=None)
    v.add_argument("--before-sha", default=None)
    v.add_argument("--after-sha", default=None)
    v.add_argument("--paused", action="store_true")
    v.add_argument("--dry-run", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        as_of = C._date(a.as_of)
        if as_of is None:
            raise ControllerError("--as-of must be a real date written YYYY-MM-DD")
        if a.cmd == "next":
            protected = [a.data_branch] + ([a.priority_file] if a.priority_file else [])
            doc = None
        else:
            protected = [a.before, a.after] + ([a.plan] if a.plan else [])
            if bool(a.plan) == bool(a.cohort):
                raise ControllerError("give exactly one of --plan or --cohort")
        try:
            out_dir = C.check_output_dir(a.out, protected)
        except C.ReportError as e:
            raise ControllerError(str(e)) from None
        if a.cmd == "next":
            doc = next_plan(a.data_branch, a.dataset, as_of, a.batch_size, a.max_calls, a.priority_file, _is_paused(a.paused), a.priority_symbols, a.data_sha)
            name = "backfill_next"
        else:
            if a.plan:
                plan = _read_json(a.plan)
            else:
                _cap_dataset(a.dataset or "")
                plan = {"schema_version": SCHEMA_VERSION, "kind": KIND_NEXT, "dataset": a.dataset, "cohort": [x.strip().upper() for x in a.cohort.split(",") if x.strip()]}
            doc = verify_run(a.before, a.after, plan, as_of, a.calls_used, _is_paused(a.paused), a.before_sha, a.after_sha)
            name = "backfill_verify"
        if not a.dry_run:
            write_outputs(out_dir, {name + ".json": render_json(doc), name + ".md": render_markdown(doc)})
    except (ControllerError, BP.PlanError, HB.BatchError, OSError) as e:
        print("backfill_controller: refused: %s" % e, file=sys.stderr)
        return 2
    print(render_markdown(doc))
    print("Dry run: nothing was written." if a.dry_run else "Written: %s.json, %s.md in %s" % (name, name, out_dir))
    return 1 if doc["kind"] == KIND_VERIFY and doc["verification"] != "passed" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
