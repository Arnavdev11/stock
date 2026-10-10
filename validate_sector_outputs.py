"""
validate_sector_outputs.py - StockLens sector architecture, Phase 1: schema and consistency checks for market_sectors.json and market_sector_stocks.json.

validate(docs) takes {"sectors": doc, "stocks": doc} and returns a list of problems (empty = fine).
As a script it checks the PRIVATE files (private/), or a published folder:
    python validate_sector_outputs.py                 checks private/market_sectors.json and private/market_sector_stocks.json
    python validate_sector_outputs.py --dir DIR       checks the two files in DIR (a missing file is an error)
Exit 1 on any problem.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import validate_market_outputs as vmo

NAMES = {"sectors": ("market_sectors.json", "sectors"), "stocks": ("market_sector_stocks.json", "sector_stocks")}
ENVELOPE = vmo.ENVELOPE + ["public_display_approved"]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _doc(name, doc, p):
    kind = NAMES[name][1]
    if not isinstance(doc, dict):
        p.append(name + ": not an object")
        return False
    for k in ENVELOPE:
        if k not in doc:
            p.append("%s: missing %s" % (name, k))
    if doc.get("schema_version") != 1:
        p.append(name + ": schema_version must be 1")
    if doc.get("kind") != kind:
        p.append(name + ": wrong kind")
    if not isinstance(doc.get("public_display_approved"), bool):
        p.append(name + ": public_display_approved must be true or false")
    d = vmo._iso(doc.get("as_of"))
    if d is None:
        p.append(name + ": as_of is not a date")
    elif d > dt.date.today() + dt.timedelta(days=1):
        p.append(name + ": as_of is in the future")
    elif d.weekday() >= 5:
        p.append(name + ": as_of is a weekend")
    if doc.get("freshness") != "EOD":
        p.append(name + ": freshness must be EOD")
    vmo._walk(doc, name, p)
    return True


def _sectors(doc, p):
    rc = doc.get("reconciliation") or {}
    keys = ["eq_rows", "not_in_master", "etf", "unclassified", "operating_missing_sector", "in_sector"]
    if not all(isinstance(rc.get(k), int) and rc[k] >= 0 for k in keys):
        p.append("sectors: reconciliation counts are missing or negative")
    elif sum(rc[k] for k in keys[1:]) != rc["eq_rows"]:
        p.append("sectors: reconciliation buckets do not add up to eq_rows")
    if not isinstance(doc.get("available"), bool):
        p.append("sectors: available must be true or false")
        return
    rows = doc.get("sectors")
    if not isinstance(rows, list):
        p.append("sectors: sectors must be a list")
        return
    if not doc["available"]:
        if rows or doc.get("leaders") or doc.get("laggards"):
            p.append("sectors: unavailable data must carry no sector rows")
        if not doc.get("unavailable_reason"):
            p.append("sectors: unavailable without a reason")
        return
    seen, total = set(), 0
    for s in rows:
        k = s.get("key")
        if not k or k in seen:
            p.append("sectors: missing or duplicate key " + str(k))
        seen.add(k)
        if not all(isinstance(s.get(f), int) for f in ("stock_count", "counted", "advances", "declines", "unchanged")):
            p.append("sectors[%s]: counts must be integers" % k)
            continue
        total += s["stock_count"]
        if s["advances"] + s["declines"] + s["unchanged"] != s["counted"] or s["counted"] > s["stock_count"]:
            p.append("sectors[%s]: advances+declines+unchanged must equal counted, counted <= stock_count" % k)
        if s["counted"] and not (_num(s.get("breadth_pct")) and 0 <= s["breadth_pct"] <= 100):
            p.append("sectors[%s]: breadth_pct outside 0-100" % k)
        if s.get("eligible"):
            for f in ("median_1d_pct", "mean_1d_pct"):
                if not (_num(s.get(f)) and -100 <= s[f] <= 1000):
                    p.append("sectors[%s]: %s missing or implausible" % (k, f))
        else:
            if s.get("median_1d_pct") is not None or s.get("mean_1d_pct") is not None:
                p.append("sectors[%s]: an ineligible sector must carry no headline figure" % k)
            if not s.get("ineligible_reason"):
                p.append("sectors[%s]: ineligible without a reason" % k)
        if s.get("volume") is not None and (not isinstance(s["volume"], int) or s["volume"] < 0):
            p.append("sectors[%s]: bad volume" % k)
    if total != (doc.get("reconciliation") or {}).get("in_sector"):
        p.append("sectors: stock counts do not add up to reconciliation.in_sector")
    elig = {s["key"]: s for s in rows if s.get("eligible")}
    for name in ("leaders", "laggards"):
        lst = doc.get(name) or []
        if len(lst) > 5:
            p.append("sectors: %s has more than 5 rows" % name)
        for x in lst:
            if x.get("key") not in elig or elig[x["key"]]["median_1d_pct"] != x.get("median_1d_pct"):
                p.append("sectors: %s row does not match an eligible sector" % name)
    vals = [x["median_1d_pct"] for x in doc.get("leaders") or []]
    if vals != sorted(vals, reverse=True):
        p.append("sectors: leaders are not in descending order")
    vals = [x["median_1d_pct"] for x in doc.get("laggards") or []]
    if vals != sorted(vals):
        p.append("sectors: laggards are not in ascending order")


def _stocks(doc, sectors_doc, p):
    sec = doc.get("sectors")
    if not isinstance(sec, dict):
        p.append("stocks: sectors must be an object")
        return
    if sectors_doc.get("available") is False:
        if sec:
            p.append("stocks: unavailable data must carry no stock rows")
        return
    if set(sec) != {s["key"] for s in sectors_doc.get("sectors", [])}:
        p.append("stocks: sector keys differ from market_sectors.json")
        return
    count = {s["key"]: s["stock_count"] for s in sectors_doc["sectors"]}
    seen = set()
    for k, v in sec.items():
        if len(v.get("members", [])) != count[k]:
            p.append("stocks[%s]: member count differs from stock_count" % k)
        for m in v.get("members", []):
            if m["symbol"] in seen:
                p.append("stocks: symbol %s is in two sectors" % m["symbol"])
            seen.add(m["symbol"])
        for lst in ("top", "bottom"):
            if len(v.get(lst, [])) > 5:
                p.append("stocks[%s]: %s has more than 5 rows" % (k, lst))


def validate(docs):
    p = []
    ok = [_doc(n, docs.get(n), p) for n in NAMES]
    if all(ok):
        _sectors(docs["sectors"], p)
        _stocks(docs["stocks"], docs["sectors"], p)
        if docs["sectors"].get("as_of") != docs["stocks"].get("as_of"):
            p.append("the two sector files have different as_of dates")
        if docs["sectors"].get("public_display_approved") != docs["stocks"].get("public_display_approved"):
            p.append("the two sector files disagree on public_display_approved")
    return p


def load_dir(d):
    docs, p = {}, []
    for n, (fname, _) in NAMES.items():
        path = Path(d) / fname
        if not path.exists():
            p.append("missing " + str(path))
            continue
        try:
            docs[n] = vmo.strict_loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            p.append("%s unreadable: %s" % (fname, type(e).__name__))
    return docs, p


def main(argv):
    d = argv[argv.index("--dir") + 1] if "--dir" in argv else "private"
    docs, p = load_dir(d)
    if not p:
        p = validate(docs)
    for x in p:
        print("::error::" + x)
    if not p:
        print("sector files in %s are valid" % d)
    return 1 if p else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
