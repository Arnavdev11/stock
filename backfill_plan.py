"""
backfill_plan.py - StockLens READ-ONLY backfill planner: how much data is still missing, in what batches, and what finishing it would cost.

It answers, from the files only: for each supported dataset and for each batch of at most 500 stocks (in the order the batches should be worked), how many stocks are complete,
how many still need work, how many provider calls that work would cost at the documented per-stock cost, and how many runs of the EXISTING workflows it would take.

It runs nothing. It makes no network request and calls no provider, imports no provider client, starts no workflow, and writes only the two report files into a separate --out
folder (never the data branch, never a cache, never the site). The coverage numbers are not computed here: every count comes from coverage_report.py's own module functions, run
on each batch's symbols, so this planner can never disagree with the coverage report about what "valid", "missing" or "stale" means.

WHY IT IS A PLANNER AND NOT A NEW DOWNLOADER. The repository already has the machinery that fetches and stores each dataset, each with its own budget, pacing, retry/back-off, call
log and ledger: historical.yml (historical_batch.py: batches of up to 500, attempts file, cool-down), fundamentals_batch.yml (batches of up to 600 with validation and acceptance),
financial_history.yml, and the budgets in update.yml for financial statements and quarterly results. A second downloader would be a second writer to the same ledgers and caches.
The durable truth is therefore the data itself (the ledgers on the stocklens-data branch and the saved caches); a separate "progress manifest" that said "done" would be able to
disagree with the data. This planner's manifest is DERIVED from the data on every run and can never claim more than the data shows. With --previous it also lists every batch that an
earlier plan called complete and that the data no longer supports (reconciliation).

Datasets (what can be measured, and from what):
  historical_candles     data branch (historical/ shards)                      always measurable
  financial_history      data branch (financial_history_ledger.json)           always measurable
  fundamentals           saved state (fundamentals_cache.json), --state-dir    only with --state-dir
  quarterly_results      saved state (quarterly_cache.json), --state-dir       only with --state-dir
  annual_financials      saved state (financials_cache.json), --state-dir      only with --state-dir
Not planned, by design: shareholding (a fixed 15-stock NSE/XBRL collector; widening it is a separate approval), sector and anything the coverage report cannot measure.

A dataset that cannot be measured is reported as not measurable and is NEVER counted complete or zero. "All complete" is stated only when every requested dataset is measurable and
every stock of every batch is valid.

ORDER. Batches follow universe.json file order (or --priority-file first). universe.json is sorted ALPHABETICALLY, so the first batch is the first 500 names alphabetically: it is not
the largest or the most important companies. The report says so whenever it is true.

Usage:
  python backfill_plan.py --data-branch DIR --as-of YYYY-MM-DD --out DIR [--state-dir DIR] [--batch-size N<=500] [--datasets a,b,...] [--priority-file FILE]
                          [--previous FILE] [--dry-run]
  --dry-run prints the summary and writes nothing. Exit codes: 0 ok, 2 refused (the reason is printed; no input text is echoed).
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import coverage_report as C

SCHEMA_VERSION = 1
KIND = "backfill_plan"
MAX_BATCH = 500
OUTPUT_NAMES = ("backfill_plan.json", "backfill_plan.md")
MAX_PREVIOUS_BYTES = 8 * 1024 * 1024
MAX_PRIORITY_BYTES = 1024 * 1024
RETRY_FACTOR = 3                 # upstox_common.RETRIES: one request may be attempted up to three times
SECONDS_PER_CALL = 2.3           # fundamentals_updater / quarterly_results_updater comments: measured; the pacing delay itself is 2.0 s

# Per-dataset cost model. Every number is read from the updaters and workflows named in "source" (tests pin them to the code). "calls_*" are first attempts: retries add up to RETRY_FACTOR.
# default_calls_per_run: what the normal scheduled or default run spends. ceiling_calls_per_run: the largest run the code or workflow enforces or documents (None = none documented).
DATASETS = {
    "historical_candles": {
        "workflow": "historical.yml (manual, batch_size up to 500, max_calls)", "module": "historical_candles",
        "calls_missing": 6, "calls_stale": 1, "calls_invalid": 6, "worst_missing": 12,
        "default_calls_per_run": 150, "ceiling_calls_per_run": 2700,
        "source": "historical_batch.py (CALL_CEILING 3000 x HEADROOM 0.9; a first fill is about 6 calls) and historical_updater.MAX_CALLS_PER_RUN 150"},
    "financial_history": {
        "workflow": "financial_history.yml (manual, max_calls) and the daily run", "module": "financial_history",
        "calls_missing": 2, "calls_stale": 2, "calls_invalid": 2, "worst_missing": 4,
        "default_calls_per_run": 120, "ceiling_calls_per_run": None,
        "source": "financial_history_updater.py (2 calls, 4 when consolidated has no data; DEFAULT_MAX_CALLS 120; no maximum is documented)"},
    "fundamentals": {
        "workflow": "fundamentals_batch.yml (manual, batches with validation) and the daily run", "module": "fundamentals_state",
        "calls_missing": 2, "calls_stale": 1, "calls_invalid": 2, "worst_missing": 2,
        "default_calls_per_run": 150, "ceiling_calls_per_run": 1500,
        "source": "fundamentals_updater.calls_needed (profile + key-ratios; stale = 1) and fundamentals_batch.yml (max_calls 1-1500)"},
    "quarterly_results": {
        "workflow": "the daily run (QUARTERLY_MAX_CALLS) or update.yml dispatch with quarterly_calls", "module": "quarterly_results",
        "calls_missing": 1, "calls_stale": 1, "calls_invalid": 1, "worst_missing": 2,
        "default_calls_per_run": 100, "ceiling_calls_per_run": 330,
        "source": "quarterly_results_updater.py (consolidated, then standalone when empty; MAX_ALLOWED_CALLS 330 from the step's 15-minute timeout)"},
    "annual_financials": {
        "workflow": "the daily run (FINANCIALS_MAX_CALLS) or update.yml dispatch with financials_calls", "module": "annual_financials_and_balance_sheet",
        "calls_missing": 3, "calls_stale": 3, "calls_invalid": 3, "worst_missing": 6,
        "default_calls_per_run": 120, "ceiling_calls_per_run": 1500,
        "source": "financials_updater.py (income, balance sheet, cash flow; consolidated then standalone) and update.yml's financials_calls description (about 1500 / 50 minutes; documentation, not enforced)"},
}
STATE_DATASETS = ("fundamentals", "quarterly_results", "annual_financials")
DEFAULT_DATASETS = tuple(DATASETS)
NOT_PLANNED = {"shareholding": "a fixed 15-stock NSE/XBRL collector; widening it needs a separate terms-and-volume approval",
               "sector": "a separate classification pipeline, not a provider backfill"}
SYMBOL_RE = re.compile(r"[A-Z0-9&\-_.]{1,30}", re.ASCII)


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- ordering and batches
def read_priority(path, universe_symbols):
    """Symbols named in the priority file, in file order, restricted to the universe. Unknown or repeated names are refused (a typo must not silently shrink the priority list)."""
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_PRIORITY_BYTES:
        raise PlanError("--priority-file is not a readable regular file of reasonable size")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise PlanError("--priority-file cannot be read as text") from None
    known, out, seen = set(universe_symbols), [], set()
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if not SYMBOL_RE.fullmatch(s):
            raise PlanError("--priority-file has a line that is not a symbol")
        if s not in known:
            raise PlanError("--priority-file names a symbol that is not in the universe")
        if s in seen:
            raise PlanError("--priority-file names a symbol twice")
        seen.add(s)
        out.append(s)
    return out


def ordered_symbols(universe_symbols, priority=()):
    first = list(priority)
    rest = [s for s in universe_symbols if s not in set(first)]
    return first + rest


def partition(symbols, batch_size):
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_BATCH:
        raise PlanError("--batch-size must be a whole number from 1 to %d" % MAX_BATCH)
    return [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]


def symbols_hash(symbols):
    return hashlib.sha256("\n".join(symbols).encode("ascii")).hexdigest()


# ---------------------------------------------------------------- measuring one batch
def measure_batch(d, syms, wanted, with_state):
    """{dataset: module dict from coverage_report} for one batch, or {dataset: None} where the dataset cannot be measured."""
    out = {}
    cf = None
    for name in wanted:
        if name == "historical_candles":
            cf = cf or C.candle_facts(d, syms)
            out[name] = C.mod_historical(d, syms, "all", cf)
        elif name == "financial_history":
            out[name] = C.mod_financial_history(d, syms, "all")
        elif not with_state:
            out[name] = None
        elif name == "fundamentals":
            out[name] = C.mod_fundamentals_state(d, syms, "all")
        elif name == "quarterly_results":
            out[name] = C.mod_quarterly_state(d, syms, "all")
        elif name == "annual_financials":
            out[name] = C.mod_financials_state(d, syms, "all")
    return out


def _n(counts, key):
    v = counts.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def batch_dataset(name, module, size):
    """The plan entry of one dataset in one batch. Never calls an unmeasurable dataset complete."""
    model = DATASETS[name]
    if module is None or module.get("measurability") == "not_measurable":
        return {"measurable": False, "complete": False, "denominator": size, "counts": None, "remaining": None,
                "calls_nominal": None, "calls_worst_case": None,
                "reason": "the saved state is not supplied (--state-dir)" if module is None else "the coverage report cannot measure it from the supplied files"}
    c = module["counts"]
    valid, stale, missing, invalid, unverified = (_n(c, k) for k in ("valid", "stale", "missing", "invalid", "unverified"))
    unmeasured = max(size - (valid + stale + missing + invalid + unverified + _n(c, "not_applicable")), 0)   # nothing is counted complete because it was not counted
    complete = bool(valid == size and size > 0 and not (stale or missing or invalid or unverified or unmeasured))
    nominal = missing * model["calls_missing"] + stale * model["calls_stale"] + invalid * model["calls_invalid"]
    worst = missing * model["worst_missing"] * RETRY_FACTOR + stale * model["calls_stale"] * RETRY_FACTOR + invalid * model["calls_invalid"] * RETRY_FACTOR
    return {"measurable": True, "complete": complete, "denominator": size,
            "counts": {"valid": valid, "stale": stale, "missing": missing, "invalid": invalid, "unverified": unverified, "not_applicable": _n(c, "not_applicable")},
            "remaining": missing + stale + invalid, "calls_nominal": nominal, "calls_worst_case": worst, "reason": None}


def runs_needed(calls, default_per_run, ceiling):
    """(runs at the default budget, runs at the ceiling or None). Zero calls need zero runs."""
    if not calls:
        return 0, (0 if ceiling else None)
    return math.ceil(calls / default_per_run), (math.ceil(calls / ceiling) if ceiling else None)


# ---------------------------------------------------------------- the plan
def build_plan(data_dir, as_of, state_dir=None, batch_size=MAX_BATCH, datasets=DEFAULT_DATASETS, priority_file=None, previous=None):
    if not isinstance(as_of, dt.date):
        raise PlanError("as_of must be a date")
    if not Path(data_dir).is_dir():
        raise PlanError("--data-branch is not a folder")
    if state_dir is not None and not Path(state_dir).is_dir():
        raise PlanError("--state-dir is not a folder")
    wanted = list(datasets)
    unknown = [x for x in wanted if x not in DATASETS]
    if unknown or not wanted or len(set(wanted)) != len(wanted):
        raise PlanError("--datasets must be a non-repeating list of: " + ", ".join(DATASETS))
    try:
        d = C.Data(data_dir, None, as_of, state_dir)
        d.load_universe()
        d.list_shards()
    except C.ReportError as e:
        raise PlanError(str(e)) from None
    universe_symbols = d.symbols
    priority = read_priority(priority_file, universe_symbols) if priority_file else []
    order = ordered_symbols(universe_symbols, priority)
    batches_syms = partition(order, batch_size)
    with_state = state_dir is not None
    state_ok = {}
    if with_state:
        for kind, (doc, prob) in d.state.items():
            state_ok[kind] = doc is not None
    batches = []
    for idx, syms in enumerate(batches_syms, 1):
        measured = measure_batch(d, syms, wanted, with_state)
        entry = {}
        for name in wanted:
            entry[name] = batch_dataset(name, measured[name], len(syms))
        batches.append({"batch": idx, "count": len(syms), "first": syms[0], "last": syms[-1], "symbols_sha256": symbols_hash(syms), "symbols": syms, "datasets": entry})
    totals = {}
    for name in wanted:
        model = DATASETS[name]
        parts = [b["datasets"][name] for b in batches]
        measurable = all(p["measurable"] for p in parts) and bool(parts)
        t = {"measurable": measurable, "stocks": len(order)}
        if measurable:
            for k in ("valid", "stale", "missing", "invalid", "unverified"):
                t[k] = sum(p["counts"][k] for p in parts)
            nominal = sum(p["calls_nominal"] for p in parts)
            worst = sum(p["calls_worst_case"] for p in parts)
            r_def, r_ceil = runs_needed(nominal, model["default_calls_per_run"], model["ceiling_calls_per_run"])
            t.update({"complete": all(p["complete"] for p in parts), "remaining": sum(p["remaining"] for p in parts), "calls_nominal": nominal, "calls_worst_case": worst,
                      "coverage_pct": round(100.0 * t["valid"] / len(order), 2) if order else None,
                      "hours_at_%.1fs_per_call" % SECONDS_PER_CALL: round(nominal * SECONDS_PER_CALL / 3600.0, 2),
                      "runs_at_default_budget": r_def, "runs_at_ceiling_budget": r_ceil})
        else:
            t.update({"complete": False, "reason": next((p["reason"] for p in parts if not p["measurable"]), "no stocks"),
                      "calls_if_nothing_were_stored_nominal": len(order) * model["calls_missing"]})
        t["cost_model"] = {k: model[k] for k in ("workflow", "calls_missing", "calls_stale", "calls_invalid", "default_calls_per_run", "ceiling_calls_per_run", "source")}
        totals[name] = t
    all_complete = bool(order) and all(totals[n]["measurable"] and totals[n]["complete"] for n in wanted)
    blocking = [n for n in wanted if not totals[n]["measurable"]] + [n for n in wanted if totals[n]["measurable"] and not totals[n]["complete"]]
    plan = {
        "schema_version": SCHEMA_VERSION, "kind": KIND, "generated_for": as_of.isoformat(),
        "tool": {"name": "backfill_plan.py", "reads_only": True, "provider_calls": 0, "starts_workflows": False},
        "universe": {"count": len(universe_symbols), "as_of": d.universe_doc.get("as_of"), "stage": d.universe_doc.get("stage"), "symbols_sha256": symbols_hash(universe_symbols),
                     "sorted_alphabetically": universe_symbols == sorted(universe_symbols)},
        "order": {"basis": "priority file, then universe file order" if priority else "universe file order", "priority_symbols": len(priority), "batch_size": batch_size,
                  "batches": len(batches),
                  "note": ("universe.json is sorted alphabetically, so batch 1 is the first %d names alphabetically; it is not the largest or most important companies." % min(batch_size, len(order)))
                  if universe_symbols == sorted(universe_symbols) and not priority else None},
        "inputs": d.inputs.listing() + [{"path": "historical/shards/", "files": len(d.shard_names), "sha256": d.shard_hash}],
        "state_files": {k: ("usable" if ok else "unusable") for k, ok in sorted(state_ok.items())} if with_state else None,
        "datasets_requested": wanted, "not_planned": NOT_PLANNED,
        "totals": totals, "batches": batches,
        "completion": {"all_requested_datasets_verified_complete": all_complete, "blocking": blocking,
                       "criterion": "every requested dataset measurable and every stock of every batch counted valid by coverage_report.py; nothing unmeasured or unverified is counted"},
        "reconciliation": reconcile(previous, batches, symbols_hash(universe_symbols), batch_size, wanted),
        "limits": ["Counts are what the files hold, not what the provider could supply: a stock the provider does not serve stays missing and may never complete.",
                   "Calls are nominal first attempts at the documented per-stock cost; retries can multiply them (calls_worst_case uses %dx)." % RETRY_FACTOR,
                   "Batch-level counts only: per-stock states stay in coverage_report.py and the updaters' own logs.",
                   "Estimates assume the existing workflows and budgets; they do not start anything."],
    }
    return plan


def reconcile(previous, batches, universe_hash, batch_size, wanted):
    """Compare a previous plan with the current data: every (batch, dataset) the previous plan called complete that the data no longer supports."""
    if previous is None:
        return {"previous_supplied": False}
    if not isinstance(previous, dict) or previous.get("kind") != KIND or previous.get("schema_version") != SCHEMA_VERSION or not isinstance(previous.get("batches"), list):
        raise PlanError("--previous is not a plan written by this tool")
    same_universe = (previous.get("universe") or {}).get("symbols_sha256") == universe_hash and (previous.get("order") or {}).get("batch_size") == batch_size
    out = {"previous_supplied": True, "previous_generated_for": previous.get("generated_for") if isinstance(previous.get("generated_for"), str) else None,
           "same_universe_and_batch_size": bool(same_universe), "claimed_complete_but_not_supported_now": []}
    if not same_universe:
        out["note"] = "the universe or batch size changed, so batches are not compared one to one"
        return out
    now = {b["batch"]: b for b in batches}
    for pb in previous["batches"]:
        if not isinstance(pb, dict) or not isinstance(pb.get("batch"), int) or pb["batch"] not in now:
            continue
        cur = now[pb["batch"]]
        if pb.get("symbols_sha256") != cur["symbols_sha256"]:
            continue
        for name in wanted:
            prev_ds = ((pb.get("datasets") or {}).get(name) or {})
            if prev_ds.get("complete") is True and not cur["datasets"][name]["complete"]:
                out["claimed_complete_but_not_supported_now"].append({"batch": pb["batch"], "dataset": name})
    return out


# ---------------------------------------------------------------- rendering
def render_json(plan):
    return json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def render_markdown(plan):
    u, o, c = plan["universe"], plan["order"], plan["completion"]
    L = ["# StockLens backfill plan", "",
         "Generated for %s. Read-only: no provider was called and no workflow was started. Counts come from coverage_report.py run on each batch." % plan["generated_for"], "",
         "Universe: %d stocks (as of %s, stage %s). %d batch(es) of up to %d, order: %s." % (u["count"], u["as_of"], u["stage"], o["batches"], o["batch_size"], o["basis"])]
    if o["note"]:
        L += ["", "**Order note:** " + o["note"]]
    L += ["", "**All requested datasets verified complete: %s.**" % ("YES" if c["all_requested_datasets_verified_complete"] else "NO"),
          "Blocking: %s." % (", ".join(c["blocking"]) if c["blocking"] else "none"), "", "## Totals by dataset", "",
          "| Dataset | Measurable | Valid | Stale | Missing | Invalid | Unverified | Coverage % | Calls (nominal) | Calls (worst case) | Hours | Runs at default | Runs at ceiling |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in plan["datasets_requested"]:
        t = plan["totals"][name]
        if t["measurable"]:
            hours = t["hours_at_%.1fs_per_call" % SECONDS_PER_CALL]
            L.append("| %s | yes | %d | %d | %d | %d | %d | %s | %d | %d | %s | %s | %s |" % (name, t["valid"], t["stale"], t["missing"], t["invalid"], t["unverified"], t["coverage_pct"],
                                                                                         t["calls_nominal"], t["calls_worst_case"], hours, t["runs_at_default_budget"],
                                                                                         "n/a" if t["runs_at_ceiling_budget"] is None else t["runs_at_ceiling_budget"]))
        else:
            L.append("| %s | NO (%s) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |" % (name, t.get("reason")))
    L += ["", "## Batches", "", "| Batch | Stocks | First | Last | " + " | ".join(plan["datasets_requested"]) + " |", "|---|---|---|---|" + "---|" * len(plan["datasets_requested"])]
    for b in plan["batches"]:
        cells = []
        for name in plan["datasets_requested"]:
            e = b["datasets"][name]
            cells.append("complete" if e["complete"] else ("%d to do" % e["remaining"] if e["measurable"] else "not measurable"))
        L.append("| %d | %d | %s | %s | %s |" % (b["batch"], b["count"], b["first"], b["last"], " | ".join(cells)))
    r = plan["reconciliation"]
    if r.get("previous_supplied"):
        L += ["", "## Reconciliation with the previous plan", "", "Same universe and batch size: %s." % r["same_universe_and_batch_size"]]
        bad = r.get("claimed_complete_but_not_supported_now") or []
        L.append("Batches the previous plan called complete that the data no longer supports: %s." % (", ".join("%d/%s" % (x["batch"], x["dataset"]) for x in bad) if bad else "none"))
    L += ["", "## Cost model and how a batch is worked", ""]
    for name in plan["datasets_requested"]:
        m = plan["totals"][name]["cost_model"]
        L.append("- **%s**: %s. Missing %d call(s), stale %d, default %d calls/run, ceiling %s. Source: %s." % (
            name, m["workflow"], m["calls_missing"], m["calls_stale"], m["default_calls_per_run"], m["ceiling_calls_per_run"] if m["ceiling_calls_per_run"] else "none documented", m["source"]))
    L += ["", "Not planned: " + "; ".join("%s (%s)" % kv for kv in sorted(plan["not_planned"].items())) + ".", "", "## Limits", ""] + ["- " + x for x in plan["limits"]]
    return "\n".join(L) + "\n"


def write_outputs(out_dir, plan):
    out_dir.mkdir(exist_ok=True)
    for name, text in zip(OUTPUT_NAMES, (render_json(plan), render_markdown(plan))):
        target = out_dir / name
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise PlanError("%s exists and is not a regular file" % name)
        tmp = out_dir / (name + ".tmp")
        if tmp.is_symlink():
            tmp.unlink()
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(str(tmp), str(target))


def read_previous(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_PREVIOUS_BYTES:
        raise PlanError("--previous is not a readable regular file of reasonable size")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        raise PlanError("--previous is not valid JSON") from None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backfill_plan.py", description="Read-only StockLens backfill planner (reads local files, writes only --out).")
    ap.add_argument("--data-branch", required=True)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--batch-size", type=int, default=MAX_BATCH)
    ap.add_argument("--datasets", default=",".join(DEFAULT_DATASETS))
    ap.add_argument("--priority-file", default=None)
    ap.add_argument("--previous", default=None)
    ap.add_argument("--dry-run", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        as_of = C._date(a.as_of)
        if as_of is None:
            raise PlanError("--as-of must be a real date written YYYY-MM-DD")
        protected = [a.data_branch] + ([a.state_dir] if a.state_dir else []) + ([a.priority_file] if a.priority_file else []) + ([a.previous] if a.previous else [])
        try:
            out_dir = C.check_output_dir(a.out, protected)
        except C.ReportError as e:
            raise PlanError(str(e)) from None
        previous = read_previous(a.previous) if a.previous else None
        plan = build_plan(a.data_branch, as_of, a.state_dir, a.batch_size, [x.strip() for x in a.datasets.split(",") if x.strip()], a.priority_file, previous)
        if not a.dry_run:
            write_outputs(out_dir, plan)
    except (PlanError, OSError) as e:
        print("backfill_plan: refused: %s" % e, file=sys.stderr)
        return 2
    print(render_markdown(plan))
    print("Dry run: nothing was written." if a.dry_run else "Written: %s, %s" % tuple(str(out_dir / n) for n in OUTPUT_NAMES))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
