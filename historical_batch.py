"""
historical_batch.py - choose which stocks the historical-price run updates next, so the whole stock directory can be filled in small, resumable batches.

It is read-only on the saved data (it never writes under ledger-branch/) and never calls the Upstox candle API. The only network use is the public Upstox
instrument file, to skip stocks Upstox does not serve as NSE equity (the same rule historical_updater.py applies), and only when asked to.

Inputs : the stock directory (`stock_directory.json`, the authoritative list of eligible stocks, validated with its own checks and hash)
         the saved history (`historical/`: shards/<SYMBOL>.json and historical.json) and the optional attempts file (attempts.json)
Order  : 1. stocks named by the person (always included, whatever their state)
         2. never-fetched stocks, in directory (alphabetical) order
         3. stale stocks, the stalest last candle first, ties alphabetical
         Fresh stocks (last candle within FRESH_DAYS of the last complete trading day) are left alone: no repeated work. A stock a recent batch could not save is
         left alone for COOLDOWN_DAYS (attempts.json), so one broken symbol cannot take a slot in every batch.
Budget : every stock is costed with historical_updater.calls_needed (a first fill is about 6 calls, an update is 1), so the plan matches what the updater will do.
         The plan keeps HEADROOM of the call budget free for retries, reserves the NIFTY 50 benchmark's calls, and never exceeds CALL_CEILING (what fits the
         120-minute job at SECONDS_PER_CALL). It stops at the first stock that does not fit, so the order is never skipped around.
Commands:
    choose --directory FILE --saved DIR --out DIR    reads INPUT_SYMBOLS, INPUT_BATCH and HISTORICAL_MAX_CALLS from the environment, writes DIR/report.json and
                                                     exports CHOSEN_SYMBOLS (to the file named by GITHUB_ENV, or to the screen when run by hand)
    record --report FILE --out OUTDIR --saved DIR --attempts-out FILE
                                                     after the update: writes the new attempts file (stocks that were planned but gained no candle)
Exit codes: 0 ok, 1 nothing to do or a refusal, 2 usage.
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import historical_handoff as HH
import historical_updater as HU
import shards
import stock_directory

MAX_BATCH = 500                 # stocks picked automatically in one run. The call ceiling is the tighter limit for first fills (about 449 x 6 calls); the hand-over file cap is sized to this
FRESH_DAYS = 4                  # a stock whose last candle is this close to the last complete day is up to date (weekends and single holidays)
COOLDOWN_DAYS = 3               # a stock a batch could not save is not planned again for this long
SECONDS_PER_CALL = 2.0          # 1 s pause plus request time, with room (the observed rate is about 1.5 s)
MAX_MINUTES = 100               # of the 120-minute job: the rest is checkout, seed, stage, upload
CALL_CEILING = int(MAX_MINUTES * 60 / SECONDS_PER_CALL)       # 3,000
HEADROOM = 0.9                  # share of the budget that is planned; the rest absorbs retries
DEFAULT_BUDGET = 150


class BatchError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------- reading
def load_directory(path):
    """The directory's symbols, after the directory's own structural and hash checks. Raises BatchError for anything else."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BatchError("the stock directory cannot be read (" + type(e).__name__ + ")")
    problems = stock_directory.check_directory_doc(doc)
    if problems:
        raise BatchError("the stock directory is not valid: " + problems[0])
    return [e["symbol"] for e in doc["symbols"]]


def _last_date(candles):
    d = candles[-1]["date"]
    dt.date.fromisoformat(d)
    return d


def saved_state(saved_dir):
    """-> ({symbol: last saved candle date}, benchmark last date or None, [symbols whose saved file is damaged]).
    Mirrors historical_updater.prior_candles: a stock's own file wins, else the single file."""
    saved = Path(saved_dir)
    last, damaged, from_shard = {}, [], set()
    sdir = saved / HH.SHARD_DIR
    if sdir.is_dir():
        for f in sorted(sdir.glob("*.json")):
            sym = f.stem
            if not shards.SYMBOL_RE.match(sym):
                continue
            try:
                last[sym] = _last_date(json.loads(f.read_text(encoding="utf-8"))["stocks"][sym]["candles"])
                from_shard.add(sym)
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                damaged.append(sym)
    bench = None
    main = saved / HH.MAIN_FILE
    if main.is_file():
        try:
            doc = json.loads(main.read_text(encoding="utf-8"))
            for sym, e in (doc.get("stocks") or {}).items():
                if sym not in from_shard and sym not in damaged:
                    try:
                        last[sym] = _last_date(e["candles"])
                    except (KeyError, IndexError, TypeError, ValueError):
                        pass
            b = doc.get("benchmark")
            if isinstance(b, dict) and b.get("candles"):
                bench = _last_date(b["candles"])
        except (OSError, ValueError, AttributeError, KeyError, IndexError, TypeError):
            pass
    return last, bench, damaged


def load_attempts(saved_dir):
    """{symbol: {"last", "count"}} from attempts.json; a missing or invalid file counts as no attempts."""
    path = Path(saved_dir) / HH.ATTEMPTS_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc["failed"] if not HH.attempts_problems(doc, "attempts") else {}


# ---------------------------------------------------------------------------------------------------- the plan
def _cost(last, end):
    return HU.calls_needed([{"date": last}] if last else [], end)


def plan(directory, last, damaged, attempts, end, today, limit, budget, include=(), supported=None, bench_calls=1):
    """The batch. directory: symbols in directory order. last: {symbol: last saved date}. Returns a report dict; 'symbols' is what the updater is given."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_BATCH:
        raise BatchError("batch size must be a whole number from 1 to %d" % MAX_BATCH)
    in_dir = set(directory)
    inc, seen = [], set()
    for raw in include:
        s = str(raw).strip().upper()
        if not s or s in seen:
            continue
        if not shards.SYMBOL_RE.match(s):
            raise BatchError("%r is not a valid symbol" % s[:30])
        if s not in in_dir:
            raise BatchError("%s is not in the stock directory" % s)
        seen.add(s)
        inc.append(s)
    ceiling = min(int(budget), CALL_CEILING)
    usable = int(ceiling * HEADROOM) - bench_calls
    bad = set(damaged)
    cutoff = (end - dt.timedelta(days=FRESH_DAYS)).isoformat()
    cool_until = {s: (dt.date.fromisoformat(v["last"]) + dt.timedelta(days=COOLDOWN_DAYS)) for s, v in attempts.items()}
    unsupported, cooling, damaged_skipped, never, stale, fresh = [], [], [], [], [], []
    for s in directory:
        if s in seen:
            continue
        if s in bad:
            damaged_skipped.append(s)
        elif supported is not None and s not in supported:
            unsupported.append(s)
        elif last.get(s) and last[s] >= cutoff:
            fresh.append(s)
        elif s in cool_until and today < cool_until[s]:
            cooling.append(s)
        elif last.get(s):
            stale.append(s)
        else:
            never.append(s)
    stale.sort(key=lambda s: (last[s], s))
    inc_cost = sum(_cost(last.get(s), end) for s in inc)
    used, auto, stopped = inc_cost, [], False
    for s in never + stale:
        if len(auto) >= limit:
            break
        c = _cost(last.get(s), end)
        if used + c > usable:
            stopped = True
            break
        used += c
        auto.append(s)
    chosen = set(auto)
    return {
        "end_date": end.isoformat(), "today": today.isoformat(), "limit": limit, "call_budget": int(budget), "call_ceiling": ceiling, "planned_call_limit": usable,
        "benchmark_calls": bench_calls, "estimated_calls": used + bench_calls, "estimated_minutes": round((used + bench_calls) * SECONDS_PER_CALL / 60, 1),
        "stopped_by_budget": stopped, "include": inc, "auto": auto, "symbols": inc + auto,
        "prior": {s: last.get(s) for s in inc + auto},
        "counts": {"directory": len(directory), "with_history": sum(1 for s in directory if last.get(s)), "never_fetched": len(never) + sum(1 for s in inc if not last.get(s)),
                   "stale": len(stale), "fresh": len(fresh), "cooling_down": len(cooling), "unsupported": len(unsupported), "damaged": len(damaged_skipped),
                   "never_fetched_left_after_this_batch": len([s for s in never if s not in chosen]), "stale_left_after_this_batch": len([s for s in stale if s not in chosen])},
        "never_fetched": [s for s in never if s in chosen], "unsupported": unsupported, "cooling_down": cooling, "damaged": damaged_skipped,
    }


# ---------------------------------------------------------------------------------------------------- after the update
def after_last(out_dir, sym):
    """The last candle date the run left for one stock (its own file, else the single file), or None."""
    out = Path(out_dir)
    try:
        return _last_date(json.loads((out / "by_symbol" / "historical" / (sym + ".json")).read_text(encoding="utf-8"))["stocks"][sym]["candles"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    try:
        return _last_date(json.loads((out / "historical.json").read_text(encoding="utf-8"))["stocks"][sym]["candles"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def record(report, out_dir, old_attempts, today):
    """The new attempts document: a planned stock that gained no candle is added (or its count raised); one that did is removed; the rest is kept."""
    failed = {s: dict(v) for s, v in old_attempts.items()}
    lost = []
    for s in report["symbols"]:
        prior, after = report["prior"].get(s), after_last(out_dir, s)
        if after is not None and (prior is None or after > prior):
            failed.pop(s, None)
        else:
            lost.append(s)
            failed[s] = {"last": today.isoformat(), "count": min(int(failed.get(s, {}).get("count", 0)) + 1, 1000)}
    return {"kind": "historical_attempts", "updated": today.isoformat(), "failed": dict(sorted(failed.items()))}, lost


# ---------------------------------------------------------------------------------------------------- command line
def _summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def _export(name, value):
    """Make a value available to the later steps of the job (GITHUB_ENV) or, run by hand, show it. Symbols only: they were validated against a strict pattern."""
    if not re.fullmatch(r"[A-Za-z0-9&_.,\-]*", value):
        raise BatchError("refusing to export an unexpected value")
    path = os.environ.get("GITHUB_ENV")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (name, value))
    else:
        print("%s=%s" % (name, value))


def _opts(argv, names):
    opts, i = {}, 0
    while i < len(argv):
        if argv[i] in names and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            raise BatchError("usage")
    if set(opts) != set(names):
        raise BatchError("usage")
    return opts


def cmd_choose(argv, env, today=None):
    o = _opts(argv, ("--directory", "--saved", "--out"))
    raw = (env.get("INPUT_BATCH") or "").strip()
    if not re.fullmatch(r"[0-9]{1,4}", raw):
        raise BatchError("batch size must be a whole number from 1 to %d" % MAX_BATCH)
    include = [x for x in re.split(r"[,\s]+", (env.get("INPUT_SYMBOLS") or "").strip()) if x]
    budget = DEFAULT_BUDGET
    try:
        v = int((env.get("HISTORICAL_MAX_CALLS") or "").strip())
        budget = v if v > 0 else DEFAULT_BUDGET
    except ValueError:
        pass
    directory = load_directory(o["--directory"])
    last, bench_last, damaged = saved_state(o["--saved"])
    end = HU.last_complete_day()
    today = today or dt.date.today()
    note = ""
    supported = None
    try:
        supported = set(HU.load_instrument_file()[0])
    except SystemExit:
        note = "The Upstox instrument file could not be read, so stocks Upstox does not serve were not filtered out in advance."
    bench_calls = _cost(bench_last, end)
    rep = plan(directory, last, damaged, load_attempts(o["--saved"]), end, today, int(raw), budget, include, supported, bench_calls)
    out = Path(o["--out"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    c = rep["counts"]
    lines = ["### Historical batch plan", "",
             "| | |", "|---|---|",
             "| Stocks in the directory | %d |" % c["directory"], "| Already have history | %d |" % c["with_history"],
             "| Never fetched (left after this batch) | %d |" % c["never_fetched_left_after_this_batch"], "| Stale (left after this batch) | %d |" % c["stale_left_after_this_batch"],
             "| Fresh, left alone | %d |" % c["fresh"], "| Cooling down after a failed attempt | %d |" % c["cooling_down"],
             "| Not served by Upstox as NSE equity (skipped) | %d |" % c["unsupported"], "| Damaged saved file (skipped, needs a look) | %d |" % c["damaged"],
             "| In this batch | %d (%s) |" % (len(rep["symbols"]), ", ".join(rep["symbols"][:40]) + (" ..." if len(rep["symbols"]) > 40 else "")),
             "| Estimated calls / minutes | %d of %d / %.1f |" % (rep["estimated_calls"], rep["planned_call_limit"] + rep["benchmark_calls"], rep["estimated_minutes"])]
    if rep["unsupported"]:
        lines += ["", "Not served as NSE equity by Upstox: " + ", ".join(rep["unsupported"][:60]) + (" ..." if len(rep["unsupported"]) > 60 else "")]
    if note:
        lines += ["", note]
    _summary(lines)
    print("Plan: %d stock(s), about %d call(s), about %.1f minute(s). Left after this batch: %d never fetched, %d stale." %
          (len(rep["symbols"]), rep["estimated_calls"], rep["estimated_minutes"], c["never_fetched_left_after_this_batch"], c["stale_left_after_this_batch"]))
    if not rep["symbols"]:
        raise BatchError("Nothing to update: every stock in the directory is up to date, cooling down, or not served by Upstox. Nothing was fetched or saved.")
    _export("CHOSEN_SYMBOLS", ",".join(rep["symbols"]))
    return 0


def cmd_record(argv, today=None):
    o = _opts(argv, ("--report", "--out", "--saved", "--attempts-out"))
    try:
        rep = json.loads(Path(o["--report"]).read_text(encoding="utf-8"))
        rep["symbols"], rep["prior"]
    except (OSError, ValueError, KeyError, TypeError):
        raise BatchError("the batch report cannot be read")
    today = today or dt.date.today()
    doc, lost = record(rep, o["--out"], load_attempts(o["--saved"]), today)
    bad = HH.attempts_problems(doc, "attempts")
    if bad:
        raise BatchError(bad[0])
    Path(o["--attempts-out"]).parent.mkdir(parents=True, exist_ok=True)
    Path(o["--attempts-out"]).write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    _summary(["", "Batch result: %d of %d planned stock(s) gained candles; %d did not and will rest for %d days: %s" %
              (len(rep["symbols"]) - len(lost), len(rep["symbols"]), len(lost), COOLDOWN_DAYS, ", ".join(lost[:60]) or "-")])
    print("Recorded: %d planned, %d gained candles, %d did not." % (len(rep["symbols"]), len(rep["symbols"]) - len(lost), len(lost)))
    return 0


def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    try:
        if not argv or argv[0] not in ("choose", "record"):
            raise BatchError("usage")
        return cmd_choose(argv[1:], env) if argv[0] == "choose" else cmd_record(argv[1:])
    except BatchError as e:
        if str(e) == "usage":
            print("usage: historical_batch.py choose --directory FILE --saved DIR --out DIR | record --report FILE --out OUTDIR --saved DIR --attempts-out FILE", file=sys.stderr)
            return 2
        print("::error::" + str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
