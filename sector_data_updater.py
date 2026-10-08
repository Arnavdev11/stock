"""
sector_data_updater.py - StockLens sector architecture, Phase 1: SECTOR PERFORMANCE (market_sectors.json).

Reads (1) the saved NSE end-of-day files through the EXISTING market loader/quality gate and (2) the private sector master.
Writes PRIVATE files only:  private/market_sectors.json  and  private/market_sector_stocks.json   (never out/, never _site).
Publication is a separate, gated step (sector_publish.py, SECTOR_PUBLIC_DISPLAY_APPROVED, default false).

UNIVERSE DEFINITIONS (they are deliberately different)
  breadth universe  (market_breadth.json, unchanged)  every NSE EQ-series stock with a valid close. Nothing in market_data_updater.clean_day excludes ETFs
                    that trade in the EQ series.
  sector universe   EQ-series stocks that the master classifies as operating_equity AND that carry a provider sector label.
                    ETFs (from an authoritative list), unclassified instruments and stocks without a sector are NOT in any sector.
  The "reconciliation" block of the output counts, for the day, how many breadth-universe rows fall into each bucket, so the difference is measured, not assumed.

CALCULATION  (existing functions: market_derive.change_of, market_derive.breadth, market_derive.is_liquid)
  per sector:  stock_count, counted (valid 1-day change), advances, declines, unchanged, breadth_pct = advances / counted * 100,
               median_1d_pct (headline, equal weight), mean_1d_pct (equal weight), volume (sum of shares traded), eligible + reason.
  No market-cap weighting anywhere. Nothing is estimated: a sector below the minimum size is listed as not eligible and gets no headline figure.

FAIL-CLOSED: if the master is missing, stale, or covers too little of the day, the file says available=false and carries no sector rows.
"""
import datetime as dt
import math
import os
import statistics
import sys
from pathlib import Path

import market_derive as md
import market_data_updater as mdu
import sector_master as sm

ROOT = Path(os.environ.get("DATA_DIR", "."))
SECTORS_FILE, STOCKS_FILE = "market_sectors.json", "market_sector_stocks.json"
SCHEMA_VERSION = 1
CFG = {
    "min_sector_stocks": 5,          # counted stocks needed for a sector to get a headline figure
    "min_counted_share": 0.80,       # of a sector's stocks, at least this share must have a valid 1-day change
    "min_coverage": 0.80,            # sector members / (EQ rows that are not authoritative ETFs) must reach this
    "min_eligible_sectors": 3,
    "master_max_age_days": 60,       # a label older than this (last_confirmed) is stale
    "min_fresh_share": 0.90,         # share of members with a fresh label
    "rank_n": 5,
    "stock_rank_n": 5,
}
SECTOR_SOURCE_NOTE = "Sector labels come from the private sector master (provider recorded per record)."


def _r(x, n=2):
    return None if x is None or not math.isfinite(x) else round(x, n)


def _unavailable(reason, recon, cfg):
    return {"available": False, "unavailable_reason": reason, "reconciliation": recon, "config": cfg, "sectors": [], "leaders": [], "laggards": []}


def reconcile(rows, by_symbol):
    """Counts of the breadth-universe rows by master bucket (symbols only; no sector text)."""
    c = {"eq_rows": len(rows), "not_in_master": 0, "etf": 0, "unclassified": 0, "operating_missing_sector": 0, "in_sector": 0}
    members = []
    for r in rows:
        rec = by_symbol.get(r["symbol"])
        if rec is None:
            c["not_in_master"] += 1
        elif rec["instrument_class"] == "etf":
            c["etf"] += 1
        elif rec["instrument_class"] != "operating_equity":
            c["unclassified"] += 1
        elif not rec["sector_key"]:
            c["operating_missing_sector"] += 1
        else:
            c["in_sector"] += 1
            members.append((r, rec))
    return c, members


def compute(rows, records, as_of, today, cfg=CFG):
    """Pure. rows: cleaned EQ rows of the latest day; records: {isin: master record}. Returns (sectors_body, stocks_body)."""
    by_symbol = {}
    for rec in records.values():
        if rec["status"] != "absent":
            by_symbol[rec["symbol"]] = rec
    recon, members = reconcile(rows, by_symbol)
    if not records:
        return _unavailable("sector master missing or empty", recon, cfg), {"sectors": {}}
    non_etf = recon["eq_rows"] - recon["etf"]
    coverage = recon["in_sector"] / non_etf if non_etf else 0.0
    recon["coverage_of_non_etf_rows"] = _r(coverage, 4)
    fresh = 0
    for _, rec in members:
        a = sm.age_days(rec["last_confirmed"], today)
        if a is not None and 0 <= a <= cfg["master_max_age_days"]:
            fresh += 1
    fresh_share = fresh / len(members) if members else 0.0
    recon["fresh_label_share"] = _r(fresh_share, 4)
    if coverage < cfg["min_coverage"]:
        return _unavailable("sector coverage of the day's stocks is too low", recon, cfg), {"sectors": {}}
    if fresh_share < cfg["min_fresh_share"]:
        return _unavailable("sector master is stale", recon, cfg), {"sectors": {}}
    groups, labels = {}, {}
    for r, rec in members:
        groups.setdefault(rec["sector_key"], []).append(r)
        labels.setdefault(rec["sector_key"], set()).add(rec["sector_source_label"])
    sectors, stocks = [], {}
    for key in sorted(groups):
        g = groups[key]
        b = md.breadth(g)
        pcts = [md.change_of(r)[1] for r in g]
        pcts = [p for p in pcts if p is not None]
        vols = [r["volume"] for r in g if r["volume"] is not None]
        counted = b["counted"]
        reason = None
        if counted < cfg["min_sector_stocks"]:
            reason = "fewer than %d stocks with a valid 1-day change" % cfg["min_sector_stocks"]
        elif counted / len(g) < cfg["min_counted_share"]:
            reason = "too many stocks without a valid 1-day change"
        ok = reason is None
        sectors.append({
            "key": key, "label": sorted(labels[key])[0], "stock_count": len(g), "counted": counted,
            "advances": b["advancing"], "declines": b["declining"], "unchanged": b["unchanged"],
            "breadth_pct": _r(b["advancing"] / counted * 100) if counted else None,
            "median_1d_pct": _r(statistics.median(pcts)) if ok else None,
            "mean_1d_pct": _r(statistics.fmean(pcts)) if ok else None,
            "volume": int(sum(vols)) if vols else None, "volume_stock_count": len(vols),
            "eligible": ok, "ineligible_reason": reason})
        ranked = sorted(((md.change_of(r)[1], r) for r in g if md.change_of(r)[1] is not None and md.is_liquid(r, md.DEFAULTS)),
                        key=lambda t: (-t[0], t[1]["symbol"]))
        stocks[key] = {"label": sorted(labels[key])[0],
                       "top": [{"symbol": r["symbol"], "close": r["close"], "change_pct": p, "volume": r["volume"]} for p, r in ranked[:cfg["stock_rank_n"]]],
                       "bottom": [{"symbol": r["symbol"], "close": r["close"], "change_pct": p, "volume": r["volume"]}
                                  for p, r in sorted(ranked, key=lambda t: (t[0], t[1]["symbol"]))[:cfg["stock_rank_n"]]],
                       "members": sorted(({"symbol": r["symbol"], "close": r["close"], "change_pct": md.change_of(r)[1], "volume": r["volume"]} for r in g),
                                         key=lambda m: m["symbol"])}
    elig = [s for s in sectors if s["eligible"]]
    if len(elig) < cfg["min_eligible_sectors"]:
        return _unavailable("too few sectors are large enough to rank", recon, cfg), {"sectors": {}}
    order = sorted(elig, key=lambda s: (-s["median_1d_pct"], s["key"]))
    pick = lambda s: {"key": s["key"], "label": s["label"], "median_1d_pct": s["median_1d_pct"], "stock_count": s["stock_count"]}
    body = {"available": True, "unavailable_reason": None, "reconciliation": recon, "config": cfg,
            "headline_method": "equal-weight median of each stock's 1-day % change (no market-cap weighting)",
            "sectors": sorted(sectors, key=lambda s: s["key"]),
            "leaders": [pick(s) for s in order[:cfg["rank_n"]]],
            "laggards": [pick(s) for s in sorted(order, key=lambda s: (s["median_1d_pct"], s["key"]))[:cfg["rank_n"]]]}
    return body, {"sectors": stocks}


def wrap(kind, body, as_of, now, dq, universe_count):
    env = mdu.envelope(kind, as_of, now, universe_count,
                       {"universe_filter": "operating_equity with a provider sector label; authoritative ETFs, unclassified and sector-less instruments excluded"}, dq,
                       dq.get("source_is_latest_trading_day", False))
    env["schema_version"] = SCHEMA_VERSION
    env["universe"] = "NSE EQ series, sector-classified operating equity"
    env["source"] = "NSE EOD bhav data (saved copy) combined with the private sector master. " + SECTOR_SOURCE_NOTE
    env["public_display_approved"] = False        # only sector_publish.py may write true into a published copy
    env.update(body)
    return env


def build_docs(rows, records, as_of, now, dq, cfg=CFG):
    body, stocks = compute(rows, records, as_of, now.date().isoformat(), cfg)
    n = body["reconciliation"]["in_sector"] if body.get("available") else 0
    return {"sectors": wrap("sectors", body, as_of, now, dq, n), "stocks": wrap("sector_stocks", stocks, as_of, now, dq, n)}


def write_private(root, docs):
    d = Path(root) / "private"
    d.mkdir(parents=True, exist_ok=True)
    pairs = [(SECTORS_FILE, docs["sectors"]), (STOCKS_FILE, docs["stocks"])]
    tmp = []
    try:
        for name, doc in pairs:
            t = d / (name + ".tmp")
            t.write_text(mdu.dumps(doc) + "\n", encoding="utf-8")
            tmp.append((t, d / name))
        for t, final in tmp:
            os.replace(t, final)
    finally:
        for t, _ in tmp:
            if t.exists():
                t.unlink()


def run(root=ROOT, now=None, cfg=CFG, gate=mdu.GATE):
    """Returns (exit_code, messages). A missing/unusable master is NOT an error: an 'unavailable' file is written."""
    root = Path(root)
    now = now or dt.datetime.now(mdu.IST)
    holidays = mdu.read_holidays(root / "data" / "holidays.txt")
    days, reports, counts = mdu.load_days(root / "data" / "raw", now.date(), gate)
    dq, latest = mdu.assess(days, reports, counts, now, holidays, gate)
    if dq["status"] != "pass":
        return 1, ["market data-quality gate FAILED: " + e for e in dq["errors"]]
    records, probs = sm.load(root / "private" / "sector_master.json")
    docs = build_docs(days[latest], records, latest, now, dq, cfg)
    if probs:
        docs["sectors"].update(available=False, unavailable_reason="sector master unreadable", sectors=[], leaders=[], laggards=[])
    import validate_sector_outputs as vso
    problems = vso.validate(docs)
    if problems:
        return 1, ["sector output check FAILED: " + p for p in problems]
    write_private(root, docs)
    s = docs["sectors"]
    rc = s["reconciliation"]
    msg = "wrote 2 private files for %s: available=%s; %d EQ rows, %d in a sector, %d ETF, %d unclassified, %d without sector, %d not in master" % (
        latest, s["available"], rc["eq_rows"], rc["in_sector"], rc["etf"], rc["unclassified"], rc["operating_missing_sector"], rc["not_in_master"])
    return 0, [msg]


def main():
    code, msgs = run()
    for m in msgs:
        print(("::error::" if code else "") + m, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(["### Sector performance (private): " + ("FAILED" if code else "OK"), ""] + ["- " + m for m in msgs]) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
