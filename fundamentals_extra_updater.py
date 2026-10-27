"""
fundamentals_extra_updater.py - the two Upstox fundamentals endpoints StockLens has not used yet: corporate actions and share holdings.

  GET /v2/fundamentals/{ISIN}/corporate-actions   dividends, bonuses, splits, rights: ex-date, amount, ratio, record date ...
  GET /v2/fundamentals/{ISIN}/share-holdings      quarterly % held by promoters, FII, other DII, mutual funds, retail and other

Field names follow Upstox's documentation (Company Fundamentals API, May 2026). The parsers accept only that documented shape; anything else is
recorded as "unusable" with the reason, never guessed. Real responses have NOT been captured yet: before this runs in production, the approved probe
(DATA_PIPELINE_PROGRESS.md) confirms the shape on real responses. This module is NOT called by any workflow yet.

What it writes (all under DATA_DIR, default "."):
  data/fundamentals_extra_cache.json                  the state: per stock and kind, status, fetch/attempt dates, failures, parsed data
  out/by_symbol/<kind>/<SYMBOL>.json                  one small file per stock with an answer (ok or none_reported; {"stocks": {SYMBOL: ...}})
  out/<kind>.json                                     a summary per kind: coverage counts and errors, no per-stock values

Statuses, never mixed up:
  ok              the endpoint answered with usable data
  none_reported   the endpoint answered with a valid, empty list (e.g. a company with no corporate action on record)
  unusable        the endpoint answered 200, but not in the documented shape (reason recorded)
  failed          the request failed (HTTP error, network, invalid JSON; reason recorded); retried after 1, 2, 4, 8, 16, then every 30 days
  not_in_upstox   the stock's symbol is not in the Upstox NSE instrument list (no request is made)
  never_fetched   not reached yet (budget); it goes first next run
Nothing missing is replaced by zero. A dividend without an amount stays without an amount, and the trailing-12-month dividend is then left empty
with its reason instead of being under-counted.

Environment: UPSTOX_ANALYTICS_TOKEN (required to fetch), EXTRA_KINDS (comma list; default both), EXTRA_MAX_CALLS (default 100),
             EXTRA_SYMBOLS (optional comma list, else the configured universe), DATA_DIR.
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import universe
import upstox_common as uc

ROOT = Path(os.environ.get("DATA_DIR", "."))
SCHEMA_VERSION = 1
DEFAULT_MAX_CALLS = 100
MAX_RETRY_DAYS = 30
KINDS = {
    "corporate_actions": {"path": "corporate-actions", "refresh_days": 14},     # new dividends are announced throughout the year
    "share_holdings": {"path": "share-holdings", "refresh_days": 30},            # quarterly data
}
STATUSES = ("ok", "none_reported", "unusable", "failed", "not_in_upstox", "never_fetched")
CATEGORIES = ("promoters", "fii", "other_dii", "mutual_funds", "retail_and_other")   # documented share-holdings categories
ACTION_TYPES = ("dividend", "bonus", "split", "rights")
RATIO_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$")
SUM_TOLERANCE = 1.0          # share-holding categories of one quarter must add up to 100 +/- this, or the quarter is flagged (never rescaled)
TTM_DAYS = 365
ERRORS_LISTED = 50


def cache_path(root=None):
    return Path(root or ROOT) / "data" / "fundamentals_extra_cache.json"


def shard_path(root, kind, symbol):
    if kind not in KINDS or not universe.SYMBOL_RE.match(symbol):
        raise ValueError("bad kind or symbol")
    return Path(root) / "out" / "by_symbol" / kind / (symbol + ".json")


def summary_path(root, kind):
    return Path(root) / "out" / (kind + ".json")


# ---------------------------------------------------------------- dates
def parse_date(v):
    """'14 Aug 2025', '14-Aug-2025', '2025-08-14' -> '2025-08-14'; anything else -> None (never guessed)."""
    if not isinstance(v, str):
        return None
    t = v.strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d %B %Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def quarter_end(label):
    """'Mar 2026' -> '2026-03-31'. Only quarter-end months are accepted; anything else -> None."""
    k = uc.period_key(label)
    if not k or k[1] not in (3, 6, 9, 12):
        return None
    y, m = k
    last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m, last).isoformat()


# ---------------------------------------------------------------- parsers (documented shape only)
def _details(items):
    out = {}
    for x in items if isinstance(items, list) else []:
        if isinstance(x, dict) and isinstance(x.get("name"), str):
            out[x["name"].strip()] = x.get("value")
    return out


def _detail(details, *words):
    for k, v in details.items():
        if all(w in k.lower() for w in words):
            return v
    return None


def action_type(name):
    n = str(name or "").strip().lower()
    for t in ACTION_TYPES:
        if t in n:
            return t
    return "other"


def parse_corporate_actions(body):
    """-> ({"events": [...]}, None) or (None, reason). An empty list is valid (none reported)."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None, "data is not a list"
    events = []
    for x in data:
        if not isinstance(x, dict) or not isinstance(x.get("name"), str) or not x["name"].strip():
            return None, "an event has no name"
        det = _details(x.get("event_details"))
        ratio = x.get("ratio")
        m = RATIO_RE.match(ratio) if isinstance(ratio, str) else None
        amount = uc.num(x.get("amount"))
        if amount is not None and amount < 0:
            amount = None
        events.append({
            "type": action_type(x["name"]), "name": x["name"].strip(),
            "ex_date": parse_date(x.get("expiry_date")) or parse_date(_detail(det, "ex")),
            "record_date": parse_date(_detail(det, "record")),
            "announcement_date": parse_date(_detail(det, "announcement")),
            "amount": amount,
            "ratio": ratio.strip() if m else None,
            "ratio_parts": [float(m.group(1)), float(m.group(2))] if m else None,
            "dividend_type": _detail(det, "dividend", "type") if isinstance(_detail(det, "dividend", "type"), str) else None,
            "details": {k: v for k, v in det.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)},
        })
    events.sort(key=lambda e: (e["ex_date"] or "", e["name"]), reverse=True)
    return {"events": events}, None


def ttm_dividend(events, as_of):
    """Trailing-12-month dividend per share from dividends with an ex-date in (as_of - 365 days, as_of]. If any such dividend has no amount, the
    total is left empty with the reason: a partial sum would understate it."""
    start = (dt.date.fromisoformat(as_of) - dt.timedelta(days=TTM_DAYS)).isoformat()
    window = [e for e in events if e["type"] == "dividend" and e["ex_date"] and start < e["ex_date"] <= as_of]
    if not window:
        return {"value": None, "reason": "no_dividend_in_the_last_12_months", "events": 0, "from": start, "to": as_of}
    if any(e["amount"] is None for e in window):
        return {"value": None, "reason": "a_dividend_amount_is_missing", "events": len(window), "from": start, "to": as_of}
    return {"value": round(sum(e["amount"] for e in window), 2), "reason": None, "events": len(window), "from": start, "to": as_of}


def parse_share_holdings(body):
    """-> ({"quarters": [{quarter_end, period, values{category: pct}, total, flags}]}, None) or (None, reason). Newest quarter first."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None, "data is not a list"
    by_q = {}
    for x in data:
        if not isinstance(x, dict) or x.get("category") not in CATEGORIES or not isinstance(x.get("history"), list):
            return None, "unknown category or no history: %r" % (x.get("category") if isinstance(x, dict) else x,)
        for h in x["history"]:
            q = quarter_end(h.get("period")) if isinstance(h, dict) else None
            if q is None:
                return None, "a period is not a quarter label"
            v = uc.num(h.get("value"))
            if v is not None and not 0 <= v <= 100:
                return None, "a percentage is outside 0-100"
            rec = by_q.setdefault(q, {"quarter_end": q, "period": h.get("period"), "values": {c: None for c in CATEGORIES}})
            if rec["values"][x["category"]] is not None:
                return None, "a category is listed twice for one quarter"
            rec["values"][x["category"]] = v
    quarters = []
    for q in sorted(by_q, reverse=True):
        rec = by_q[q]
        vals = [v for v in rec["values"].values() if v is not None]
        rec["total"] = round(sum(vals), 2) if vals else None
        flags = []
        if any(v is None for v in rec["values"].values()):
            flags.append("a_category_is_missing")
        if rec["total"] is not None and abs(rec["total"] - 100) > SUM_TOLERANCE:
            flags.append("does_not_add_up_to_100")
        rec["flags"] = flags
        quarters.append(rec)
    return {"quarters": quarters}, None


PARSERS = {"corporate_actions": parse_corporate_actions, "share_holdings": parse_share_holdings}


# ---------------------------------------------------------------- state
def load_cache(root=None):
    p = cache_path(root)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "kind": "fundamentals_extra_cache", "stocks": {}}
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("kind") != "fundamentals_extra_cache" or doc.get("schema_version") != SCHEMA_VERSION or not isinstance(doc.get("stocks"), dict):
        uc.fail("data/fundamentals_extra_cache.json is not a fundamentals_extra_cache of schema %d; it was left untouched." % SCHEMA_VERSION)
    return doc


def retry_days(failures):
    return min(MAX_RETRY_DAYS, 2 ** max(0, failures - 1))


def due(entry, kind, today_):
    """Whether a stock's kind should be requested today: never fetched, older than the refresh interval, or a failure whose back-off has passed."""
    st = (entry or {}).get(kind) or {}
    day = dt.date.fromisoformat(today_)
    if st.get("status") in ("failed", "unusable"):
        last = st.get("attempted")
        return not last or (day - dt.date.fromisoformat(last)).days >= retry_days(st.get("failures") or 1)
    if not st.get("fetched"):
        return True
    return (day - dt.date.fromisoformat(st["fetched"])).days >= KINDS[kind]["refresh_days"]


def run_order(symbols, cache, kind, today_):
    """Never fetched first, then the oldest successful fetch, then failures by their last attempt. Only due stocks are listed."""
    def key(s):
        st = (cache["stocks"].get(s) or {}).get(kind) or {}
        if not st.get("status"):
            return (0, "", s)
        if st["status"] in ("ok", "none_reported"):
            return (1, st.get("fetched") or "", s)
        return (2, st.get("attempted") or "", s)
    return sorted([s for s in symbols if due(cache["stocks"].get(s), kind, today_)], key=key)


def fetch_one(api, isin, kind):
    """-> (status, data, error). Statuses as in the module notes."""
    body, err = api.get(isin + "/" + KINDS[kind]["path"])
    if err:
        return "failed", None, err
    data, problem = PARSERS[kind](body)
    if problem:
        return "unusable", None, problem
    empty = not (data.get("events") if kind == "corporate_actions" else data.get("quarters"))
    return ("none_reported" if empty else "ok"), data, None


def update(api, symbols, instruments, cache, kinds, today_):
    """One run. Returns {kind: {"requested": n, "not_reached": n}}. A budget stop leaves the rest due; nothing is dropped."""
    report = {}
    for kind in kinds:
        report[kind] = {"requested": 0, "not_reached": 0}
        for sym in run_order(symbols, cache, kind, today_):
            entry = cache["stocks"].setdefault(sym, {})
            meta = instruments.get(sym)
            if not meta:
                entry["isin"] = entry.get("isin")
                entry[kind] = {"status": "not_in_upstox", "fetched": None, "attempted": today_, "failures": 0,
                               "error": "symbol not in the Upstox NSE instrument list", "data": None}
                continue
            if api.calls >= api.max_calls:
                report[kind]["not_reached"] += 1
                continue
            entry["isin"] = meta["isin"]
            prev = entry.get(kind) or {}
            status, data, err = fetch_one(api, meta["isin"], kind)
            if err == "call limit for this run reached":
                report[kind]["not_reached"] += 1
                continue
            report[kind]["requested"] += 1
            if status in ("ok", "none_reported"):
                entry[kind] = {"status": status, "fetched": today_, "attempted": today_, "failures": 0, "error": None, "data": data}
            else:
                entry[kind] = {"status": status, "fetched": prev.get("fetched"), "attempted": today_, "failures": (prev.get("failures") or 0) + 1,
                               "error": err, "data": prev.get("data")}          # a failure never removes data saved earlier
    return report


# ---------------------------------------------------------------- outputs
def stock_doc(kind, sym, entry, as_of):
    st = entry[kind]
    obj = {"symbol": sym, "isin": entry.get("isin"), "status": st["status"], "fetched": st["fetched"]}
    if kind == "corporate_actions":
        obj["events"] = st["data"]["events"]
        obj["ttm_dividend_per_share"] = ttm_dividend(st["data"]["events"], as_of)
    else:
        obj["quarters"] = st["data"]["quarters"]
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "source": "Upstox", "endpoint": "/v2/fundamentals/{isin}/" + KINDS[kind]["path"],
            "as_of": as_of, "stocks": {sym: obj}}


def summary_doc(kind, symbols, cache, as_of):
    counts = {s: 0 for s in STATUSES}
    errors = []
    for sym in symbols:
        st = (cache["stocks"].get(sym) or {}).get(kind) or {}
        status = st.get("status") or "never_fetched"
        if status in ("failed", "unusable") and st.get("data"):
            status = "ok_with_a_later_failure"
        counts[status] = counts.get(status, 0) + 1
        if st.get("error"):
            errors.append({"symbol": sym, "status": st.get("status"), "error": st["error"], "attempted": st.get("attempted"), "failures": st.get("failures")})
    return {"schema_version": SCHEMA_VERSION, "kind": kind + "_summary", "source": "Upstox", "as_of": as_of, "universe": len(symbols),
            "coverage": counts, "with_data": counts["ok"] + counts.get("ok_with_a_later_failure", 0),
            "errors": errors[:ERRORS_LISTED], "errors_total": len(errors),
            "notes": "Counts only; per-stock data is in out/by_symbol/%s/<SYMBOL>.json. none_reported = a valid empty answer; never_fetched = not reached yet." % kind}


def validate_stock_doc(doc):
    p = []
    if not isinstance(doc, dict) or doc.get("kind") not in KINDS or doc.get("schema_version") != SCHEMA_VERSION:
        return ["not a fundamentals-extra document"]
    stocks = doc.get("stocks")
    if not isinstance(stocks, dict) or len(stocks) != 1:
        return ["stocks must hold exactly one stock"]
    (sym, s), = stocks.items()
    if s.get("status") not in ("ok", "none_reported", "failed", "unusable") or not s.get("fetched"):
        p.append(sym + ": only a stock with fetched data is written")
    if doc["kind"] == "corporate_actions":
        for e in s.get("events") or []:
            if e["type"] not in ACTION_TYPES + ("other",):
                p.append(sym + ": bad event type")
            if e["amount"] is not None and (not isinstance(e["amount"], (int, float)) or e["amount"] < 0):
                p.append(sym + ": bad amount")
            if e["ex_date"] is not None and parse_date(e["ex_date"]) != e["ex_date"]:
                p.append(sym + ": bad ex_date")
    else:
        for q in s.get("quarters") or []:
            if quarter_end(q.get("period")) != q.get("quarter_end"):
                p.append(sym + ": quarter_end does not match its period")
            for c, v in (q.get("values") or {}).items():
                if c not in CATEGORIES or (v is not None and not (isinstance(v, (int, float)) and 0 <= v <= 100)):
                    p.append(sym + ": bad category or value")
    return p


def write_outputs(root, symbols, cache, kinds, as_of):
    """Per-stock files for stocks with data (status ok, or a later failure that kept earlier data), then the summaries. Every file is checked first;
    a problem stops the run before anything is written."""
    docs = []
    for kind in kinds:
        for sym in symbols:
            st = (cache["stocks"].get(sym) or {}).get(kind) or {}
            if st.get("data") and st.get("fetched") and st.get("status") in ("ok", "none_reported", "failed", "unusable"):
                docs.append((shard_path(root, kind, sym), stock_doc(kind, sym, cache["stocks"][sym], as_of)))
    problems = [x for _, d in docs for x in validate_stock_doc(d)]
    if problems:
        uc.fail("fundamentals-extra output failed its checks: " + "; ".join(problems[:5]))
    for path, d in docs:
        uc.write_json(path, d)
    for kind in kinds:
        uc.write_json(summary_path(root, kind), summary_doc(kind, symbols, cache, as_of))
    return len(docs)


def main(argv=None):
    token = uc.get_token()
    kinds = [k.strip() for k in (os.environ.get("EXTRA_KINDS") or ",".join(KINDS)).split(",") if k.strip()]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        uc.fail("unknown EXTRA_KINDS: " + ", ".join(bad))
    raw = os.environ.get("EXTRA_SYMBOLS", "").strip()
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()] if raw else universe.load()
    budget = universe.budget("EXTRA_MAX_CALLS", DEFAULT_MAX_CALLS)
    today_ = uc.today()
    cache = load_cache()
    instruments = uc.load_instruments()
    api = uc.Upstox(token, budget)
    uc.log("Universe:", len(symbols), "stocks | kinds:", ", ".join(kinds), "| call budget:", budget)
    report = update(api, symbols, instruments, cache, kinds, today_)
    uc.write_json(cache_path(), cache)                       # first the state, then the outputs
    files = write_outputs(ROOT, symbols, cache, kinds, today_)
    for k, r in report.items():
        uc.log(k, "- requested:", r["requested"], "| not reached (next run):", r["not_reached"])
    uc.log("Calls made:", api.calls, "| per-stock files written:", files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
