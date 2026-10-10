"""
validate_market_outputs.py - StockLens Phase 5K: schema and consistency checks for out/market_*.json.

validate(docs) takes the documents (a dict of name -> parsed JSON) and returns a list of problems; an empty list means they may be published.
market_data_updater.py runs it before writing anything. As a script it checks the files that exist in out/ (used by the workflow before publishing):
    python validate_market_outputs.py            exit 1 on any problem, 0 otherwise
"""
import datetime as dt
import json
import math
import os
import re
import sys
from pathlib import Path

STATUSES = {"safe", "insufficient_history", "requires_adjustment", "unavailable"}
KINDS = {"breadth": "market_breadth", "movers": "market_movers", "activity": "market_activity", "dma": "market_dma"}
ENVELOPE = ["schema_version", "kind", "as_of", "source", "freshness", "freshness_detail", "generated_at", "universe", "universe_count", "filters", "data_quality"]


def _bad_const(c):
    raise ValueError("invalid number " + c)


def strict_loads(text):
    return json.loads(text, parse_constant=_bad_const)


def _walk(x, path, out):
    """Every number must be finite; no string may call the data live."""
    if isinstance(x, float) and not math.isfinite(x):
        out.append("%s is not a finite number" % path)
    elif isinstance(x, dict):
        for k, v in x.items():
            _walk(v, path + "." + str(k), out)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _walk(v, "%s[%d]" % (path, i), out)
    elif isinstance(x, str) and re.search(r"\blive\b", x, re.I):
        out.append("%s calls the data live: %r" % (path, x[:60]))


def _iso(s):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _pct(v, lo, hi):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi)


def _ranks(rows, path, p):
    for i, r in enumerate(rows):
        if r.get("rank") != i + 1:
            p.append("%s: rank is not 1..n in order at row %d" % (path, i + 1))
            return


def _check_rows(rows, path, p, key=None, desc=True, positive=None):
    if not isinstance(rows, list):
        p.append(path + " is not a list")
        return
    _ranks(rows, path, p)
    seen = set()
    for r in rows:
        s = r.get("symbol")
        if not isinstance(s, str) or not s:
            p.append(path + ": a row has no symbol")
        elif s in seen:
            p.append("%s: %s appears twice" % (path, s))
        seen.add(s)
        if not _pct(r.get("delivery_pct"), 0, 100):
            p.append("%s: %s delivery_pct outside 0-100" % (path, s))
        for f in ("volume", "turnover_lakhs"):
            if r.get(f) is not None and (not isinstance(r[f], (int, float)) or r[f] < 0):
                p.append("%s: %s has a negative or invalid %s" % (path, s, f))
        if positive is True and not (r.get("change_pct") is not None and r["change_pct"] > 0):
            p.append("%s: %s is not a gainer" % (path, s))
        if positive is False and not (r.get("change_pct") is not None and r["change_pct"] < 0):
            p.append("%s: %s is not a loser" % (path, s))
    if key:
        vals = [r.get(key) for r in rows]
        if any(v is None for v in vals) or any((a < b) if desc else (a > b) for a, b in zip(vals, vals[1:])):
            p.append("%s is not sorted by %s" % (path, key))


def _check_dma_block(b, path, p):
    for scope in ("all_eq", "liquid"):
        d = b.get(scope)
        if not isinstance(d, dict):
            p.append("%s.%s missing" % (path, scope))
            continue
        n, m = d.get("numerator"), d.get("denominator")
        if not (isinstance(n, int) and isinstance(m, int) and 0 <= n <= m):
            p.append("%s.%s numerator/denominator invalid" % (path, scope))
            continue
        if d.get("status") not in STATUSES:
            p.append("%s.%s status invalid" % (path, scope))
        if m == 0:
            if d.get("pct") is not None:
                p.append("%s.%s has a percentage but no denominator" % (path, scope))
        elif not _pct(d.get("pct"), 0, 100) or d.get("pct") is None or abs(d["pct"] - round(n / m * 100, 2)) > 0.011:
            p.append("%s.%s percentage does not equal numerator/denominator" % (path, scope))


def validate_envelope(doc, name, p, today=None):
    for k in ENVELOPE:
        if k not in doc:
            p.append("%s: missing %s" % (name, k))
    if doc.get("schema_version") != 1:
        p.append(name + ": schema_version must be 1")
    if doc.get("kind") != KINDS.get(name):
        p.append(name + ": wrong kind")
    d = _iso(doc.get("as_of"))
    if d is None:
        p.append(name + ": as_of is not a date")
    elif d > (today or dt.date.today() + dt.timedelta(days=1)):
        p.append(name + ": as_of is in the future")
    elif d.weekday() >= 5:
        p.append(name + ": as_of is a weekend")
    if doc.get("freshness") != "EOD":
        p.append(name + ": freshness must be EOD")
    fd = doc.get("freshness_detail") or {}
    if fd.get("source_date") != doc.get("as_of") or not str(fd.get("label", "")).startswith("EOD as of "):
        p.append(name + ": freshness_detail does not match as_of")
    if not isinstance(fd.get("source_is_latest_trading_day"), bool):
        p.append(name + ": freshness_detail.source_is_latest_trading_day must be true or false")
    try:
        dt.datetime.fromisoformat(doc.get("generated_at"))
    except (TypeError, ValueError):
        p.append(name + ": generated_at is not a timestamp")
    uc = doc.get("universe_count")
    if not isinstance(uc, int) or isinstance(uc, bool) or uc <= 0:
        p.append(name + ": universe_count must be a positive integer")
    dq = doc.get("data_quality") or {}
    if dq.get("status") != "pass":
        p.append(name + ": data_quality.status must be pass to publish")
    for k in doc.get("metric_status", {}).values():
        if k not in STATUSES:
            p.append(name + ": metric_status has an unknown status " + str(k))
    if not isinstance(doc.get("metric_status"), dict):
        p.append(name + ": metric_status missing")


def validate(docs, today=None):
    """docs: {'breadth','movers','activity','dma'} -> list of problems."""
    p = []
    for name in KINDS:
        if not isinstance(docs.get(name), dict):
            p.append(name + ": document missing")
    if p:
        return p
    for name in KINDS:
        validate_envelope(docs[name], name, p, today)
        _walk(docs[name], name, p)
    as_ofs = {docs[n].get("as_of") for n in KINDS}
    if len(as_ofs) != 1:
        p.append("the files are not all for the same date: " + ", ".join(sorted(map(str, as_ofs))))
    if len({docs[n].get("universe_count") for n in KINDS}) != 1:
        p.append("the files disagree on the universe size")
    b = docs["breadth"]
    for scope in ("all_eq", "liquid"):
        d = (b.get("breadth") or {}).get(scope)
        if not isinstance(d, dict):
            p.append("breadth.%s missing" % scope)
            continue
        a, c, u = d.get("advancing"), d.get("declining"), d.get("unchanged")
        if not all(isinstance(v, int) and v >= 0 for v in (a, c, u)):
            p.append("breadth.%s counts invalid" % scope)
            continue
        if d.get("counted") != a + c + u:
            p.append("breadth.%s: counted is not advancing + declining + unchanged" % scope)
        if scope == "all_eq" and a + c + u > b.get("universe_count", 0):
            p.append("breadth.all_eq counts more stocks than the universe")
        if c == 0 and d.get("ad_ratio") is not None:
            p.append("breadth.%s: ad_ratio must be null when there are no decliners" % scope)
        if c > 0 and (d.get("ad_ratio") is None or abs(d["ad_ratio"] - round(a / c, 2)) > 0.011):
            p.append("breadth.%s: ad_ratio is not advancing / declining" % scope)
    for per in ("20", "50", "200"):
        blk = (b.get("dma_breadth") or {}).get(per)
        if not isinstance(blk, dict):
            p.append("dma_breadth.%s missing" % per)
        else:
            _check_dma_block(blk, "dma_breadth." + per, p)
    for k, v in (b.get("high_low_52w_counts") or {}).items():
        if v.get("status") not in STATUSES:
            p.append("high_low_52w_counts.%s status invalid" % k)
        if v.get("status") != "safe" and v.get("count") is not None:
            p.append("high_low_52w_counts.%s has a count but is not safe" % k)
    if (b.get("metric_status") or {}).get("performance_30d_90d_all_market") == "safe":
        p.append("whole-market 30D/90D performance must not be published as safe from unadjusted closes")
    m = docs["movers"]
    _check_rows(m.get("gainers"), "movers.gainers", p, "change_pct", True, True)
    _check_rows(m.get("losers"), "movers.losers", p, "change_pct", False, False)
    a = docs["activity"]
    ma = a.get("most_active") or {}
    _check_rows((ma.get("by_value") or {}).get("rows"), "most_active.by_value", p, "turnover_lakhs", True)
    _check_rows((ma.get("by_volume") or {}).get("rows"), "most_active.by_volume", p, "volume", True)
    vol = a.get("volume") or {}
    for k in ("shockers", "high_volume_high_gain", "high_volume_top_losers"):
        _check_rows(vol.get(k), "volume." + k, p, "volume_multiple", True)
        for r in vol.get(k) or []:
            if r.get("volume_multiple") is None or r["volume_multiple"] < 0:
                p.append("volume.%s: invalid multiple" % k)
    hl = a.get("high_low_52w") or {}
    for k in ("new_high", "near_high", "new_low", "near_low"):
        blk = hl.get(k)
        if not isinstance(blk, dict) or not isinstance(blk.get("rows"), list) or not isinstance(blk.get("count"), int):
            p.append("high_low_52w.%s malformed" % k)
            continue
        _ranks(blk["rows"], "high_low_52w." + k, p)
        for r in blk["rows"]:
            hi = k.endswith("high")
            e = r.get("prior_high_52w" if hi else "prior_low_52w")
            if e is None or r.get("distance_pct") is None or r.get("close") is None:
                p.append("high_low_52w.%s: a row lacks its prior extreme or distance" % k)
            elif k == "new_high" and not r["close"] > e or k == "new_low" and not r["close"] < e:
                p.append("high_low_52w.%s: %s has not crossed its prior extreme" % (k, r.get("symbol")))
    dd = docs["dma"]
    if not isinstance(dd.get("stocks"), list):
        p.append("dma.stocks missing")
    else:
        if len(dd["stocks"]) != dd.get("universe_count"):
            p.append("dma.stocks does not list every stock in the universe")
        for r in dd["stocks"]:
            for per, v in (r.get("dma") or {}).items():
                if v.get("status") not in STATUSES:
                    p.append("dma: %s %s has an invalid status" % (r.get("symbol"), per))
                if v.get("status") == "safe" and (v.get("value") is None or v.get("observations") != int(per)):
                    p.append("dma: %s %s is 'safe' without a full set of observations" % (r.get("symbol"), per))
                if v.get("status") != "safe" and v.get("value") is not None:
                    p.append("dma: %s %s has a value but is not 'safe'" % (r.get("symbol"), per))
        for per in ("20", "50", "200"):
            blk = (b.get("dma_breadth") or {}).get(per)
            if isinstance(blk, dict) and isinstance(blk.get("all_eq"), dict):
                safe = sum(1 for r in dd["stocks"] if (r.get("dma") or {}).get(per, {}).get("status") == "safe")
                above = sum(1 for r in dd["stocks"] if (r.get("dma") or {}).get(per, {}).get("position") == "above" and r["dma"][per].get("status") == "safe")
                if blk["all_eq"].get("denominator") != safe or blk["all_eq"].get("numerator") != above:
                    p.append("dma_breadth.%s does not match the per-stock table" % per)
    return p


def validate_dir(out_dir):
    out, docs, p = Path(out_dir), {}, []
    names = {"breadth": "market_breadth.json", "movers": "market_movers.json", "activity": "market_activity.json", "dma": "market_dma.json"}
    present = [k for k, f in names.items() if (out / f).exists()]
    if not present:
        return ["no market_*.json files found in " + str(out)]
    for k, f in names.items():
        try:
            docs[k] = strict_loads((out / f).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            p.append("%s: %s" % (f, e))
    return p + (validate(docs) if not p else [])


def main():
    problems = validate_dir(Path(os.environ.get("DATA_DIR", ".")) / "out")
    for x in problems[:50]:
        print("::error::market outputs: " + x)
    print("market outputs: %s" % ("%d problem(s)" % len(problems) if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
