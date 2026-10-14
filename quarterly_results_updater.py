"""
quarterly_results_updater.py - StockLens QUARTERLY RESULTS (data only; no page change in this step).

What it does
  * For every stock in the existing STOCK DIRECTORY (out/stock_directory.json: symbol, ISIN, name - the same identity the Search section and the company page use) it asks Upstox
    for the QUARTERLY income statement and keeps every quarter it returns in a persistent, revision-aware cache (data/quarterly_cache.json).
  * It publishes one small file per stock, out/by_symbol/quarterly_results/<SYMBOL>.json, and a summary with coverage, out/quarterly_results.json (no per-stock figures).
  * It is separate from the annual updaters: fundamentals_updater.py, financials_updater.py and financial_history_updater.py and their files are not read or changed.
  * It has nothing to do with live prices.

Identity: the symbol (as in the stock directory and in "#stock=SYMBOL" on the page) is the only key. ISIN and company name are COPIED from the directory entry at output time.
No separate stock-name mapping is created.

What is shown, and what is deliberately NOT invented
  * The real quarterly response (verified) has data.full_statement = null and only the summary income_statement categories revenue, operating_profit and net_profit, each with
    history entries {period, value, change}. Revenue and Net profit therefore come from those summary categories WHEN the detailed full_statement has no value for the quarter; a
    detailed value always wins, but only from a full_statement line that is itself a quarterly series (the real response carried ANNUAL periods there, which are ignored).
    checks.revenue_source / profit_after_tax_source say which was used. Operating profit stays its own field. The `change` figure is never used.
  * Revenue (Upstox 'Revenue' line) and Total revenue (its 'Total Revenue' line) are kept apart. Total revenue is never renamed Revenue; banks and financial companies may have only the second.
  * Net profit = Upstox 'Profit After Tax' line. EPS = 'EPS Basic' (and 'EPS Diluted' when given).
  * EBITDA is NOT calculated and never will be from this source: Upstox returns no depreciation line that could be relied on. What Upstox calls 'operating_profit' is stored as
    operating_profit with the check operating_profit_equals_pbt; when it equals profit before tax it is not a real operating profit and the page must not show it as one.
  * YoY and QoQ growth are computed here, from two STORED quarters exactly 12 or 3 months apart. The percentage Upstox attaches to a period is not used (for quarterly data it is
    not documented whether it means QoQ or YoY). A growth is null when either quarter is missing, when the base is zero or negative (a growth from a loss is not meaningful), or
    across a gap. null is never shown as 0.
  * published_on (the date the company declared the result) is null: Upstox returns no publication date. retrieved_on is the date this run first stored the quarter, and is NOT
    a declaration date. last_success is the last day a response for the stock was parsed.
  * A quarter with no valid value at all is not stored. A later response that omits a value never erases an existing one. Quarters are never deleted. If a later fetch CHANGES a
    stored value, the old value is kept in revisions.
  * A response whose time_period is not 'quarterly' (including a missing one), whose periods are not all the same month of a quarter, or that lists a period twice is rejected.

When a stock is re-checked (so results appear soon after they are declared, without calling for 2,300 stocks every day)
  * never checked: now;
  * a result is probably pending (from 10 days after the end of the quarter that follows the newest stored one, for 90 days): every 2 days;
  * otherwise: every 30 days.
  A capped run (QUARTERLY_MAX_CALLS) serves the stalest due stocks first. QUARTERLY_SYMBOLS=A,B,C limits a run to those directory stocks (test batches). Nothing here is a promise that a result has been declared; a stock simply shows its newest stored quarter.

Source status (see the report in the review): Upstox documents time_period=quarterly for /v2/fundamentals/{isin}/income-statement, but its only sample is yearly and its quarterly
response has not been observed by StockLens yet. This parser is written to the documented shape and refuses anything else; the first real run on the test batch must be inspected.

Token: environment only (UPSTOX_ANALYTICS_TOKEN), never printed or written.
"""
import calendar
import datetime as dt
import json
import os
import sys

import shards
import stock_directory
import universe
from financial_history_updater import lines_by_label, period_info
from upstox_common import ROOT, Upstox, fail, get_token, log, num, today, write_json

SCHEMA_VERSION = 1
KIND = "quarterly_results"
CACHE_FILE = ROOT / "data" / "quarterly_cache.json"
OUT_FILE = ROOT / "out" / "quarterly_results.json"
DIRECTORY_FILE = ROOT / "out" / "stock_directory.json"
SHARD_KIND = "quarterly_results"
SOURCE = "Upstox fundamentals API (income statement, quarterly)"
DEFAULT_MAX_CALLS = 120
PENDING_FROM_DAYS = 10
PENDING_FOR_DAYS = 90
PENDING_EVERY_DAYS = 2
STEADY_EVERY_DAYS = 30
BASES = ("consolidated", "standalone")

INCOME_MAP = {"revenue": "revenue", "total revenue": "total_revenue", "profit before tax": "profit_before_tax",
              "profit after tax": "profit_after_tax", "eps basic": "eps_basic", "eps diluted": "eps_diluted"}
FIELDS = ["revenue", "total_revenue", "operating_profit", "profit_before_tax", "profit_after_tax", "eps_basic", "eps_diluted"]
GROWTH_FIELDS = ["revenue", "total_revenue", "operating_profit", "profit_after_tax", "eps_basic"]

NOTES = {
    "units": "INR crore; EPS in INR. Quarterly figures: each value is for the single quarter, not year to date.",
    "identity": "symbol, isin and name are copied from the stock directory (out/stock_directory.json), the same identity the Search section uses.",
    "null": "null means unavailable. Never 0 as a substitute, never carried forward, never interpolated. A quarter missing from the list is a gap, not a zero.",
    "revenue": "revenue is Upstox's 'Revenue' line; total_revenue is its 'Total Revenue' line. They are never merged or renamed.",
    "ebitda": "EBITDA is not provided and not calculated. operating_profit is Upstox's summary 'operating_profit'; when checks.operating_profit_equals_pbt is true it equals profit before tax and is not an operating profit.",
    "growth": "yoy compares with the stored quarter 12 months earlier, qoq with the one 3 months earlier, both computed here from stored values. null when either is missing or the base is zero or negative.",
    "dates": "published_on is the company's declaration date and is null: the source gives none. retrieved_on is when StockLens first stored the quarter. last_success is the last day a response was parsed.",
    "revisions": "values are the latest retrieved. If a later fetch changes a stored value, the previous value is kept in revisions.",
    "scope": "Data only. Not live market data. No rating, target or advice.",
}


# ---------------------------------------------------------------- small helpers
def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def add_months(day, n):
    """The month-end n months after the month-end `day` (a date)."""
    m = day.month - 1 + n
    y, m = day.year + m // 12, m % 12 + 1
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _date(s):
    try:
        return dt.date.fromisoformat(s) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None


def same(a, b):
    return None if a is None or b is None else abs(a - b) <= 0.01


# ---------------------------------------------------------------- parsing one Upstox quarterly response
def _rejected(problem):
    return {"quarters": {}, "problem": problem}


SUMMARY_CATEGORIES = ("revenue", "operating_profit", "net_profit")


def summary_categories(inc):
    """data.income_statement -> {category: [history, ...]}. Accepts the list form [{"category", "history"}] and a mapping {category: {"history"}}; anything else gives nothing.
    The `change` figure attached to each history entry is not read anywhere: growth is computed from stored quarterly values only."""
    out = {}
    if isinstance(inc, list):
        for c in inc:
            if isinstance(c, dict) and isinstance(c.get("category"), str):
                out.setdefault(c["category"].strip().lower(), []).append(c.get("history"))
    elif isinstance(inc, dict):
        for k, c in inc.items():
            if isinstance(k, str) and isinstance(c, dict):
                out.setdefault(k.strip().lower(), []).append(c.get("history"))
    return out


def _period_infos(history):
    return [x for x in (period_info(h.get("period")) for h in history or [] if isinstance(h, dict)) if x]


def _looks_annual(history):
    """Two or more different periods that all fall in the SAME month of the year (Mar 2024, Mar 2025, ...): a yearly series."""
    infos = _period_infos(history)
    return len({i["period_end"] for i in infos}) >= 2 and len({i["month"] for i in infos}) == 1


def _shows_quarters(history):
    """Two or more different periods on at least two different months of the year: the cadence of a quarterly series."""
    infos = _period_infos(history)
    return len({i["period_end"] for i in infos}) >= 2 and len({i["month"] for i in infos}) >= 2


def full_statement_is_usable(lines, summaries):
    """Whether the detailed full_statement lines may be read as QUARTERLY values. The real response labelled time_period=quarterly carried ANNUAL periods in full_statement (one period per
    year, all the same month) beside quarterly summary categories, and an annual 'Mar 2025' value must never become the Mar 2025 quarter. So the whole full_statement is ignored when
    any line looks annual, or when the summary is clearly quarterly and no detailed line shows a quarterly cadence of its own. Without a quarterly summary to contradict it, a full_statement
    with no annual sign is read as before. Nothing is guessed: an ignored full_statement leaves the quarter to the summary or unavailable."""
    if any(_looks_annual(h) for h in lines):
        return False
    if any(_shows_quarters(h) for h in summaries) and not any(_shows_quarters(h) for h in lines):
        return False
    return True


def parse_quarterly(body):
    """-> {"quarters": {period_end: {"quarter", "values", "checks"}}, "problem": None | short text}. Refuses anything that is not a clean quarterly statement."""
    d = body.get("data") if isinstance(body, dict) else None
    if not isinstance(d, dict):
        return _rejected("no_data")
    if str(d.get("time_period", "")).lower() != "quarterly":
        return _rejected("not_quarterly")
    found = lines_by_label(d.get("full_statement"))
    per, infos, dups = {}, {}, set()

    def collect(history, field):
        vals = per.setdefault(field, {})
        for h in history or []:
            if not isinstance(h, dict):
                continue
            info = period_info(h.get("period"))
            if not info:
                continue
            pe = info["period_end"]
            if pe in vals:
                dups.add((field, pe))                          # a period listed twice is ambiguous
            vals[pe] = num(h.get("value"))
            infos[pe] = info

    cats = summary_categories(d.get("income_statement"))
    summaries = [h for c in SUMMARY_CATEGORIES for h in cats.get(c, [])]
    if full_statement_is_usable([found[l][1] for l in INCOME_MAP if l in found], summaries):
        for label, field in INCOME_MAP.items():
            if label in found:
                collect(found[label][1], field)                 # an annual or unverifiable full_statement is skipped entirely (see full_statement_is_usable)
    for cat in SUMMARY_CATEGORIES:                              # the summary lines are kept under their own names, so they can never collide with a full_statement line
        for history in cats.get(cat, []):
            collect(history, "summary_" + cat)                  # a category listed twice repeats its periods and is therefore ambiguous
    if not infos:
        return _rejected("no_data")
    if len({i["month"] % 3 for i in infos.values()}) > 1:
        return _rejected("mixed_month_ends")
    quarters = {}
    for pe, info in infos.items():
        def get(f):
            return None if (f, pe) in dups else (per.get(f) or {}).get(pe)

        def pick(detailed, summary):
            """(value, source). A detailed full_statement value wins. The summary line is used only when there is no detailed value for that quarter; a detailed value that is
            ambiguous (the period listed twice) is not replaced by the summary, it stays unavailable."""
            if (detailed, pe) in dups:
                return None, None
            v = get(detailed)
            if v is not None:
                return v, "full_statement"
            v = get(summary)
            return (v, "summary") if v is not None else (None, None)
        revenue, revenue_src = pick("revenue", "summary_revenue")
        pat, pat_src = pick("profit_after_tax", "summary_net_profit")
        vals = {"revenue": revenue, "total_revenue": get("total_revenue"), "operating_profit": get("summary_operating_profit"),
                "profit_before_tax": get("profit_before_tax"), "profit_after_tax": pat,
                "eps_basic": get("eps_basic"), "eps_diluted": get("eps_diluted")}
        if all(v is None for v in vals.values()):
            continue
        quarters[pe] = {"quarter": "%s %d" % (calendar.month_abbr[info["month"]], info["fy"]), "values": vals,
                        "checks": {"operating_profit_equals_pbt": same(vals["operating_profit"], vals["profit_before_tax"]),
                                   "summary_net_profit_equals_pat": same(get("summary_net_profit"), get("profit_after_tax")),     # only when both exist
                                   "revenue_source": revenue_src, "profit_after_tax_source": pat_src}}
    if not quarters:
        return _rejected("no_data")
    return {"quarters": quarters, "problem": None}


# ---------------------------------------------------------------- the cache (revision-aware, additive)
def empty_record():
    return {"basis": None, "last_attempt": None, "last_checked": None, "last_success": None, "problem": None, "quarters": {}}


def merge_quarters(record, parsed, basis, day):
    """Merge a parsed response into a stock's record, in place. Returns the number of quarters added or changed."""
    changed = 0
    if record.get("basis") not in (None, basis):
        # the basis changed (consolidated <-> standalone): never mix. The old basis is kept only if the new response has nothing usable.
        if not parsed["quarters"]:
            return 0
        record["quarters"] = {}
    record["basis"] = basis
    for pe, q in parsed["quarters"].items():
        old = record["quarters"].get(pe)
        if old is None:
            record["quarters"][pe] = {"quarter": q["quarter"], "retrieved_on": day, "values": dict(q["values"]), "checks": dict(q["checks"]), "revisions": []}
            changed += 1
            continue
        touched = False
        for f, v in q["values"].items():
            if v is None:
                continue                                    # an omitted value never erases a stored one
            if old["values"].get(f) is None:
                old["values"][f] = v
                touched = True
            elif abs(old["values"][f] - v) > 0.005:
                old["revisions"].append({"field": f, "previous": old["values"][f], "changed_on": day})
                old["values"][f] = v
                touched = True
        old["checks"] = dict(q["checks"])
        changed += 1 if touched else 0
    return changed


def needs_refresh(record, day):
    """Whether a stock is due. day: a date."""
    if not record or not record.get("last_checked"):
        return True
    last = _date(record["last_checked"])
    if last is None:
        return True
    ends = sorted(k for k in (record.get("quarters") or {}) if _date(k))
    if ends:
        nxt = add_months(_date(ends[-1]), 3)
        start = nxt + dt.timedelta(days=PENDING_FROM_DAYS)
        if start <= day <= start + dt.timedelta(days=PENDING_FOR_DAYS):
            return (day - last).days >= PENDING_EVERY_DAYS
    return (day - last).days >= STEADY_EVERY_DAYS


# ---------------------------------------------------------------- growth (derived, never stored in the cache)
def growth_pct(cur, prev):
    if not isnum(cur) or not isnum(prev) or prev <= 0:
        return None
    return round((cur - prev) / prev * 100, 2)


def growth_for(quarters, pe):
    """{"yoy": {field: pct|None}, "qoq": {...}} for the stored quarter ending `pe`."""
    d = _date(pe)
    out = {}
    for name, n in (("yoy", 12), ("qoq", 3)):
        prev = quarters.get(add_months(d, -n).isoformat())
        out[name] = {f: growth_pct(quarters[pe]["values"].get(f), prev["values"].get(f) if prev else None) for f in GROWTH_FIELDS}
    return out


# ---------------------------------------------------------------- the published per-stock document
def stock_object(entry, record):
    qs = record["quarters"]
    ends = sorted((k for k in qs if _date(k)), reverse=True)
    rows = []
    for pe in ends:
        q = qs[pe]
        rows.append({"quarter": q["quarter"], "quarter_end": pe, "published_on": None, "retrieved_on": q["retrieved_on"], "values": {f: q["values"].get(f) for f in FIELDS},
                     "growth": growth_for(qs, pe), "checks": q["checks"], "revisions": q["revisions"]})
    gaps = []
    for newer, older in zip(ends, ends[1:]):
        if add_months(_date(older), 3) != _date(newer):
            gaps.append({"between": [older, newer]})
    return {"symbol": entry["symbol"], "isin": entry["isin"], "name": entry["name"], "basis": record["basis"], "last_success": record["last_success"],
            "latest_quarter_end": ends[0] if ends else None, "quarters": rows, "gaps": gaps}


def document(stock, as_of):
    return {"schema_version": SCHEMA_VERSION, "kind": KIND, "as_of": as_of, "source": SOURCE, "notes": NOTES, "stocks": {stock["symbol"]: stock}}


def check_stock_doc(doc, entry=None):
    """Problems (a list of short texts) that would stop a shard being written. entry: the directory entry the shard must match."""
    p = []
    if not isinstance(doc, dict) or doc.get("kind") != KIND or doc.get("schema_version") != SCHEMA_VERSION:
        return ["bad header"]
    stocks = doc.get("stocks")
    if not isinstance(stocks, dict) or len(stocks) != 1:
        return ["a shard holds exactly one stock"]
    (sym, s), = stocks.items()
    if s.get("symbol") != sym or not shards.SYMBOL_RE.match(sym or ""):
        p.append("symbol")
    if entry is not None and (s.get("symbol"), s.get("isin"), s.get("name")) != (entry["symbol"], entry["isin"], entry["name"]):
        p.append("identity differs from the stock directory")
    seen = set()
    for r in s.get("quarters") or []:
        pe = r.get("quarter_end")
        if _date(pe) is None or pe in seen:
            p.append("bad or duplicate quarter_end " + str(pe))
            continue
        seen.add(pe)
        if not isinstance(r.get("values"), dict) or set(r["values"]) != set(FIELDS):
            p.append("values keys " + str(pe))
            continue
        if all(v is None for v in r["values"].values()):
            p.append("empty quarter " + pe)
        if any(v is not None and not isnum(v) for v in r["values"].values()):
            p.append("non-numeric value " + pe)
        if r.get("published_on") is not None and _date(r["published_on"]) is None:
            p.append("bad published_on " + pe)
    ends = [r["quarter_end"] for r in s.get("quarters") or [] if _date(r.get("quarter_end"))]
    if ends != sorted(ends, reverse=True):
        p.append("quarters are not newest first")
    return p


# ---------------------------------------------------------------- one run
def run_batch(entries, cache, fetch, day, max_calls, priority=()):
    """entries: {symbol: {symbol, isin, name}}. cache: {symbol: record} (changed in place). fetch(isin, basis) -> (body, error_text).
    Returns a summary dict. Never raises for a bad response; a failed fetch leaves the stock's data untouched."""
    due = [s for s, e in entries.items() if needs_refresh(cache.get(s), day)]
    last = {s: ((cache.get(s) or {}).get("last_checked")) for s in due}
    order = universe.order_stalest(due, last)
    if priority:                                              # the coverage universe (stocks the site has detail pages for) goes first; the rest of the directory follows (stable: keeps the stalest-first order)
        first = set(priority)
        order = [x for x in order if x in first] + [x for x in order if x not in first]
    calls = 0
    summary = {"due": len(due), "checked": 0, "stored": 0, "no_data": 0, "rejected": {}, "errors": 0, "skipped_by_call_cap": 0, "quarters_added_or_changed": 0}
    for sym in order:
        e = entries[sym]
        rec = cache.setdefault(sym, empty_record())
        if not e.get("isin"):
            continue
        got = None
        for basis in BASES:
            if calls >= max_calls:
                break
            calls += 1
            body, err = fetch(e["isin"], basis)
            rec["last_attempt"] = day.isoformat()
            if err or body is None:
                summary["errors"] += 1
                got = "error"
                break
            parsed = parse_quarterly(body)
            if parsed["quarters"]:
                n = merge_quarters(rec, parsed, basis, day.isoformat())
                summary["quarters_added_or_changed"] += n
                rec["last_checked"] = rec["last_success"] = day.isoformat()
                rec["problem"] = None
                summary["stored"] += 1
                got = "ok"
                break
            rec["problem"] = parsed["problem"]
            if parsed["problem"] != "no_data":
                summary["rejected"][parsed["problem"]] = summary["rejected"].get(parsed["problem"], 0) + 1
                rec["last_checked"] = day.isoformat()
                got = "rejected"
                break
            got = "no_data"                                   # nothing usable on this basis: try the next one
        if got == "no_data":
            rec["last_checked"] = day.isoformat()
            summary["no_data"] += 1
        if got is not None:
            summary["checked"] += 1
        elif calls >= max_calls:
            summary["skipped_by_call_cap"] += 1
    summary["calls"] = calls
    return summary


def build_outputs(entries, cache, as_of):
    """-> ({symbol: shard document}, summary document, problems). A stock with no stored quarter has no shard. Identity comes from `entries` only."""
    docs, problems = {}, []
    for sym, e in entries.items():
        rec = cache.get(sym)
        if not rec or not rec.get("quarters"):
            continue
        doc = document(stock_object(e, rec), as_of)
        bad = check_stock_doc(doc, e)
        if bad:
            problems.append((sym, bad))
            continue
        docs[sym] = doc
    latest = {}
    for d in docs.values():
        s = next(iter(d["stocks"].values()))
        latest[s["latest_quarter_end"]] = latest.get(s["latest_quarter_end"], 0) + 1
    meta = {"schema_version": SCHEMA_VERSION, "kind": "quarterly_results_summary", "as_of": as_of, "source": SOURCE, "notes": NOTES,
            "coverage": {"directory_stocks": len(entries), "with_quarterly_results": len(docs), "without": len(entries) - len(docs),
                         "by_latest_quarter_end": dict(sorted(latest.items(), reverse=True))}}
    return docs, meta, problems


def write_outputs(root, docs, meta):
    for sym, doc in docs.items():
        shards._write(_shard_path(root, sym), doc)
    write_json(root / "out" / "quarterly_results.json", meta)


def _shard_path(root, symbol):
    if not shards.SYMBOL_RE.match(symbol or ""):
        raise ValueError("unsafe symbol for a file name")
    return root / "out" / "by_symbol" / SHARD_KIND / (symbol + ".json")


# ---------------------------------------------------------------- the entry point
def load_entries(path=DIRECTORY_FILE):
    """{symbol: {symbol, isin, name}} from the published stock directory (validated by stock_directory.check_directory_doc). Falls back to the configured universe when there is no
    directory file; an entry without ISIN or name is then not requested."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if doc is not None:
        bad = stock_directory.check_directory_doc(doc)
        if bad:
            fail("out/stock_directory.json failed its own checks: " + "; ".join(str(x) for x in bad[:3]))
        return {x["symbol"]: {"symbol": x["symbol"], "isin": x["isin"], "name": x["name"]} for x in doc["symbols"]}
    out = {}
    for s, x in universe.load_entries().items():
        if x.get("isin") and x.get("name"):
            out[s] = {"symbol": s, "isin": x["isin"], "name": x["name"]}
    return out


def resolve_priority(entries, loader=None):
    """The coverage-universe symbols that are in the stock directory, fetched first. Never silent:
    - the universe cannot be loaded -> the run FAILS (exception type only, no detail) rather than quietly falling back to alphabetical order, which is what left TCS without a shard;
    - some priority symbols are not in the directory -> a warning with the count and at most 5 names;
    - priority symbols were loaded but NONE is in the directory -> the run FAILS (the coverage guarantee would be empty)."""
    loader = loader or universe.load_entries
    try:
        wanted = list(loader())
    except Exception as err:
        fail("the coverage universe could not be loaded (%s); refusing to fall back to alphabetical order. Nothing was fetched or published." % type(err).__name__)
    found = [x for x in wanted if x in entries]
    missing = [x for x in wanted if x not in entries]
    log("priority coverage: loaded %d, in the stock directory %d, missing %d" % (len(wanted), len(found), len(missing)))
    if missing:
        log("::warning::%d priority symbol(s) are not in the stock directory and will not be fetched: %s%s" % (len(missing), ", ".join(missing[:5]), " ..." if len(missing) > 5 else ""))
    if wanted and not found:
        fail("none of the %d priority symbols is in the stock directory; refusing to run without the coverage guarantee." % len(wanted))
    return found


def main(argv=None):
    token = get_token()
    entries = load_entries()
    only = [x.strip().upper() for x in (os.environ.get("QUARTERLY_SYMBOLS") or "").split(",") if x.strip()]
    if only:                                                  # a small test batch: only these directory stocks, and only if they are in the directory
        missing = [x for x in only if x not in entries]
        if missing:
            fail("QUARTERLY_SYMBOLS names stocks that are not in the stock directory: " + ", ".join(missing))
        entries = {x: entries[x] for x in only}
    if not entries:
        fail("no stocks to request: out/stock_directory.json is missing and the universe has no ISIN/name entries")
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")).get("stocks", {})
    except (OSError, ValueError):
        cache = {}
    api = Upstox(token, universe.budget("QUARTERLY_MAX_CALLS", DEFAULT_MAX_CALLS))

    raw_dir = os.environ.get("QUARTERLY_SAVE_RAW")           # test batches: keep each raw response (no credential is ever in it) so its real shape can be reviewed

    def fetch(isin, basis):
        body, err = api.get(isin + "/income-statement", {"type": basis, "time_period": "quarterly", "fs": "true"})
        if raw_dir and body is not None:
            os.makedirs(raw_dir, exist_ok=True)
            with open(os.path.join(raw_dir, "%s_%s.json" % (isin, basis)), "w", encoding="utf-8") as f:
                json.dump(body, f, indent=1)
        return body, err

    day = dt.date.fromisoformat(today())
    priority = resolve_priority(entries)
    summary = run_batch(entries, cache, fetch, day, api.max_calls, priority)
    docs, meta, problems = build_outputs(entries, cache, day.isoformat())
    for sym, bad in problems:
        log("not published", sym, "; ".join(bad))
    write_json(CACHE_FILE, {"schema_version": SCHEMA_VERSION, "kind": "quarterly_cache", "stocks": cache})
    write_outputs(ROOT, docs, meta)
    log("quarterly results:", json.dumps(summary, sort_keys=True), "published", len(docs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
