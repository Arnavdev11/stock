"""
sector_master_updater.py - StockLens sector architecture, Phase 1: the SECTOR MASTER UPDATER (slow-changing reference data).

Standalone: it does not touch fundamentals_updater.py or any market file. Run by hand or on a slow schedule (the sector of a company changes rarely).

  provider.universe()          the instruments to classify (Upstox NSE_EQ / EQ list through upstox_common)
  provider.sector_for(isin)    the provider's sector label, a limited number of ISINs per run (never-fetched first, then the oldest)
  sector_classify.classify     instrument class from authoritative evidence only (an ETF list file), never from symbol or name patterns
  sector_master.merge          no-loss merge into the master; the master is written only if every check passes

Privacy: the master lives OUTSIDE out/ (default private/sector_master.json). This repository is public, so the master is not committed and
not uploaded as an artifact; the workflow keeps it in the Actions cache. The console and step summary show AGGREGATE COUNTS ONLY
(no sector labels, no symbols). Nothing here publishes anything.

Environment:  DATA_DIR (default .), SECTOR_ETF_LIST (default private/etf_list.json), SECTOR_MAX_CALLS (default 1500), SECTOR_REFRESH_DAYS (default 30)
"""
import datetime as dt
import os
import sys
from collections import Counter
from pathlib import Path

import sector_classify as sc
import sector_master as sm

ROOT = Path(os.environ.get("DATA_DIR", "."))
PRIVATE = ROOT / "private"
MASTER_FILE = "sector_master.json"
MIN_UNIVERSE_SHARE = 0.5      # a provider universe less than half the size of the master is not believable


def plan_fetches(universe, old, today, refresh_days, budget):
    """ISINs to ask the provider about, in order: never fetched, then the oldest fetched_on. At most `budget`."""
    due = []
    for u in universe:
        rec = old.get(u["isin"])
        if rec is None or not rec.get("fetched_on"):
            due.append((0, "", u["isin"]))
            continue
        age = sm.age_days(rec["fetched_on"], today)
        if age is None or age >= refresh_days:
            due.append((1, rec["fetched_on"], u["isin"]))
    due.sort()
    return [d[2] for d in due[:max(0, budget)]]


def build_observations(universe, old, evidence, fetched, attempted, provider_name, today):
    """fetched: {isin: label or None} for answered calls; attempted: set of ISINs asked."""
    obs = []
    for u in universe:
        isin = sm.norm_isin(u.get("isin"))
        if not isin:
            continue
        answered = isin in fetched
        label = fetched.get(isin) if answered else None
        label_for_class = label if answered else (old.get(isin) or {}).get("sector_source_label")
        cls, src = sc.classify(u.get("symbol"), label_for_class, evidence)
        o = {"isin": isin, "symbol": u.get("symbol"), "company_name": u.get("company_name"), "exchange": u.get("exchange") or "NSE",
             "instrument_class": cls, "class_source": src, "sector_source_label": label, "provider_answered": answered,
             "attempted": isin in attempted}
        if answered:
            o["provider"], o["fetched_on"] = provider_name, today
        obs.append(o)
    return obs


def summarize(records):
    c = Counter(r["status"] for r in records.values())
    k = Counter(r["instrument_class"] for r in records.values())
    return {"records": len(records), "status": dict(sorted(c.items())), "class": dict(sorted(k.items()))}


def run(provider, root=ROOT, today=None, etf_path=None, budget=1500, refresh_days=30):
    """Returns (exit_code, messages, summary). Writes the master only if every check passes."""
    today = today or dt.date.today().isoformat()
    master_path = Path(root) / "private" / MASTER_FILE
    old, probs = sm.load(master_path)
    if probs:
        return 1, ["master check FAILED: " + probs[0] + " - nothing was written"], None
    evidence, eprobs = sc.load_etf_evidence(etf_path or Path(root) / "private" / "etf_list.json")
    if eprobs:
        return 1, ["ETF evidence FAILED: " + eprobs[0] + " - nothing was written"], None
    universe = provider.universe()
    first_build = not old
    isins = [sm.norm_isin(u.get("isin")) for u in universe]
    if len([i for i in isins if i]) != len({i for i in isins if i}):
        return 1, ["provider universe has duplicate ISINs - nothing was written"], None
    if not universe or (old and len(universe) < len(old) * MIN_UNIVERSE_SHARE):
        return 1, ["provider universe is empty or far smaller than the master - nothing was written"], None
    todo = plan_fetches(universe, old, today, refresh_days, budget)
    fetched, attempted = {}, set(todo)
    for isin in todo:
        label, answered = provider.sector_for(isin)
        if answered:
            fetched[isin] = label
    obs = build_observations(universe, old, evidence, fetched, attempted, provider.name, today)
    merged, skipped = sm.merge(old, obs, today)
    recs = sorted(merged.values(), key=lambda r: r["isin"])
    problems = sm.validate_records(recs) + sm.no_loss_problems(list(old.values()), recs)
    if problems:
        return 1, ["master check FAILED: " + problems[0] + " - nothing was written"], None
    sm.write(master_path, merged, today)
    s = summarize(merged)
    msgs = (["no previous sector master was found (first run, or the saved copy expired): building from the provider's list; earlier history, if any, is gone"] if first_build else []) + ["asked the provider about %d instrument(s), %d answered, %d skipped as unusable" % (len(todo), len(fetched), skipped),
            "ETF evidence: " + ("loaded" if evidence else "none (instruments without a sector stay unclassified)"),
            "master: %d record(s); status %s; class %s" % (s["records"], s["status"], s["class"])]
    return 0, msgs, s


def summary_out(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main():
    from sector_provider import UpstoxProvider
    budget = int(os.environ.get("SECTOR_MAX_CALLS") or "1500")
    code, msgs, _ = run(UpstoxProvider(max_calls=budget + 50), ROOT, etf_path=os.environ.get("SECTOR_ETF_LIST") or None,
                        budget=budget, refresh_days=int(os.environ.get("SECTOR_REFRESH_DAYS") or "30"))
    for m in msgs:
        print(("::error::" if code else "") + m, flush=True)
    summary_out(["### Sector master: " + ("FAILED - nothing was written" if code else "OK"), ""] + ["- " + m for m in msgs])
    return code


if __name__ == "__main__":
    sys.exit(main())
