"""
financial_history_updater.py - StockLens Phase 4 Step 4A: the annual financial-history LEDGER (data only; no UI).

What it does
  * Reads EVERY annual period that Upstox returns for the income statement and the cash flow (the production updater keeps only the newest year).
  * Keeps them in a persistent, revision-aware ledger:  data/financial_history_ledger.json  (source of truth)
    and publishes the same per-stock records as:       out/financial_history.json
  * financials_updater.py and out/financials.json are NOT touched. The latest common period is compared against financials.json and any
    difference is reported as a WARNING only; neither dataset is overwritten.

Rules (all enforced by test_financial_history.py)
  * years is an array of records, one per (fiscal year, basis). No fixed number of year slots. Nothing is trimmed.
  * A year record exists only if the source gave at least one valid value for that year. A missing fiscal year stays missing (listed in "gaps").
  * null = unavailable. Never 0 as a substitute, never carried forward, never interpolated, never bridged.
  * Consolidated and standalone are separate records and never mixed.
  * An active record holds the latest value; if a later fetch changes an existing value, the previous value is kept in "revisions".
    A later response that omits a value never erases an existing one, and records are never deleted.
  * Banks / financial institutions: Revenue is never invented. Total Revenue is never renamed Revenue. The other income-statement lines Upstox
    returns are preserved as returned (other_lines.income).
  * Free cash flow is stored only when a single unambiguous capital-expenditure line is provided (capex, free_cash_flow = operating - capex).
    It is never operating minus investing cash flow.
  * Quarterly responses and mixed month-end periods are rejected.
  * No EPS growth, no growth, no CAGR in this phase.

Calls per stock: income-statement (yearly, fs=true) and cash-flow (fs=true, no time_period) = 2, plus the same two for standalone only if
consolidated has no usable data. Each statement is refreshed at most every 30 days. Token: environment only, never printed or written.
"""
import calendar
import copy
import datetime as dt
import json
import os
import re
import sys

from financials_updater import capex_class, norm
from upstox_common import (MONTHS, ROOT, SYMBOLS, Upstox, fail, get_token, load_instruments, log, num, today, write_json)

SCHEMA_VERSION = 1
LEDGER_FILE = ROOT / "data" / "financial_history_ledger.json"
OUT_FILE = ROOT / "out" / "financial_history.json"
FINANCIALS_FILE = ROOT / "out" / "financials.json"
FUNDAMENTALS_FILE = ROOT / "out" / "fundamentals.json"
OFFICIAL_FILE = ROOT / "official_financial_records.json"       # committed, hand-verified company statements (Step 4B)
OFFICIAL_PROVIDER = "Official annual report"
PROVIDERS = ("Upstox", OFFICIAL_PROVIDER)
REFRESH_DAYS = 30
DEFAULT_MAX_CALLS = 120
DIFF_TOLERANCE = 0.01
MAX_OTHER_LINES = 40

INCOME_MAP = {"revenue": "revenue", "total revenue": "total_revenue", "profit before tax": "profit_before_tax",
              "profit after tax": "profit_after_tax", "eps basic": "eps_basic", "eps diluted": "eps_diluted"}
INCOME_FIELDS = ["revenue", "total_revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "eps_diluted"]
CASH_FIELDS = ["operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "capex", "free_cash_flow"]
VALUE_FIELDS = INCOME_FIELDS + CASH_FIELDS
CORE_FIELDS = ["revenue", "total_revenue", "profit_before_tax", "profit_after_tax", "eps_basic",
               "operating_cash_flow", "investing_cash_flow", "financing_cash_flow"]      # eps_diluted, capex, free_cash_flow are "when available"
STATEMENTS = {"income": ("income-statement", INCOME_FIELDS), "cash_flow": ("cash-flow", CASH_FIELDS)}
CHECK_KEYS = ["summary_revenue_equals_total_revenue", "summary_operating_profit_equals_pbt", "summary_net_profit_equals_pat"]
BASES = ("consolidated", "standalone")
LAYOUTS = ("standard", "financial", "unclassified")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NOTES = {
    "units": "INR crore; EPS in INR.",
    "years": "One record per (fiscal year, basis), newest first. fy is the calendar year of the period end (Mar 2026 = 2026). No fixed number of slots.",
    "null": "null means unavailable. Never zero, never carried forward, never interpolated, never bridged. A missing fiscal year has no record (see gaps).",
    "basis": "consolidated and standalone are separate records and are never mixed.",
    "revisions": "values hold the latest verified numbers. If a later fetch changes an existing value, the previous value is kept in revisions.",
    "banks": "Revenue is never invented for financial institutions and Total Revenue is never renamed Revenue. For statement_layout 'financial' the other income-statement lines Upstox returned are kept as returned in other_lines.income.",
    "free_cash_flow": "Only when one unambiguous capital-expenditure line is provided (operating cash flow minus that capex). Never operating minus investing cash flow.",
    "scope": "Data ledger only. No growth, CAGR or EPS growth is calculated in this file.",
}


# ---------------------------------------------------------------- periods and small helpers
def period_info(label):
    """'Mar 2026' -> {fy: 2026, month: 3, period_end: '2026-03-31'}; anything else -> None."""
    parts = str(label).split() if isinstance(label, str) else []
    if len(parts) != 2 or not parts[0].isalpha() or not re.fullmatch(r"[12]\d{3}", parts[1]):
        return None
    month = MONTHS.get(parts[0][:3].lower())
    if not month:
        return None
    year = int(parts[1])
    return {"fy": year, "month": month, "period_end": "%04d-%02d-%02d" % (year, month, calendar.monthrange(year, month)[1])}


def period_label(info):
    return "%s %d" % (calendar.month_abbr[info["month"]], info["fy"])


def isdate(x):
    if not isinstance(x, str) or not DATE_RE.match(x):
        return False
    try:
        dt.date.fromisoformat(x)
        return True
    except ValueError:
        return False


def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def same(a, b):
    return None if a is None or b is None else abs(a - b) <= 0.01


def classify_layout(sector):
    """statement_layout from the Upstox sector text. Metadata only: it never changes a stored value."""
    if not isinstance(sector, str) or not sector.strip():
        return "unclassified"
    s = sector.lower()
    return "financial" if any(w in s for w in ("financ", "bank", "insur", "nbfc")) else "standard"


def entries(history):
    return [h for h in (history or []) if isinstance(h, dict)]


def label_text(x):
    return " ".join(str(x).split())[:80]


def lines_by_label(full):
    """{normalised label: (original label, history entries)}. A repeated label is ambiguous and dropped (as in the production parser)."""
    found, seen = {}, set()
    for x in full or []:
        if not isinstance(x, dict) or not x.get("particular"):
            continue
        k = norm(x["particular"])
        if k in seen:
            found.pop(k, None)
            continue
        seen.add(k)
        found[k] = (label_text(x["particular"]), entries(x.get("history")))
    return found


def period_values(history):
    """{fy: value-or-None} for the parsable periods of one history. A period listed twice is ambiguous -> None. Also returns {fy: period info}."""
    vals, infos, dup = {}, {}, set()
    for h in entries(history):
        info = period_info(h.get("period"))
        if not info:
            continue
        if info["fy"] in vals:
            dup.add(info["fy"])
        vals[info["fy"]] = num(h.get("value"))
        infos[info["fy"]] = info
    for fy in dup:
        vals[fy] = None
    return vals, infos


def _rejected(problem):
    return {"years": {}, "problem": problem, "month": None}


# ---------------------------------------------------------------- parsing one Upstox response (all annual periods)
def parse_income(body, layout="standard"):
    d = (body or {}).get("data") if isinstance(body, dict) else None
    d = d if isinstance(d, dict) else {}
    tp = d.get("time_period")
    if tp is not None and str(tp).lower() != "yearly":
        return _rejected("not_yearly")
    found = lines_by_label(d.get("full_statement"))
    per, infos = {}, {}
    for label, field in INCOME_MAP.items():
        if label in found:
            per[field], i = period_values(found[label][1])
            infos.update(i)
    months = {i["month"] for i in infos.values()}
    if len(months) > 1:
        return _rejected("mixed_month_ends")
    summ = {}
    for c in d.get("income_statement") or []:
        if isinstance(c, dict):
            summ[str(c.get("category", "")).lower()] = period_values(c.get("history"))[0]
    years = {}
    for fy, info in infos.items():
        vals = {f: (per.get(f) or {}).get(fy) for f in INCOME_FIELDS}
        if all(v is None for v in vals.values()):
            continue
        s = lambda cat: (summ.get(cat) or {}).get(fy)
        ordinary = layout == "standard"           # revenue = total revenue and operating profit = PBT are ordinary-company assumptions
        checks = {"summary_revenue_equals_total_revenue": same(s("revenue"), vals["total_revenue"]) if ordinary else None,
                  "summary_operating_profit_equals_pbt": same(s("operating_profit"), vals["profit_before_tax"]) if ordinary else None,
                  "summary_net_profit_equals_pat": same(s("net_profit"), vals["profit_after_tax"])}
        other = {}
        if layout == "financial":
            for k, (label, hist) in found.items():
                if k in INCOME_MAP:
                    continue
                v = period_values(hist)[0].get(fy)
                if v is not None and len(other) < MAX_OTHER_LINES:
                    other[label] = v
        years[fy] = {"period": period_label(info), "values": vals, "checks": checks, "other_lines": other}
    if not years:
        return _rejected("no_data")
    return {"years": years, "problem": None, "month": next(iter(months))}


def parse_cashflow(body, fy_month=None):
    d = (body or {}).get("data") if isinstance(body, dict) else None
    d = d if isinstance(d, dict) else {}
    if str(d.get("time_period", "")).lower() == "quarterly":
        return _rejected("quarterly")
    cats = {str(c.get("category", "")).lower(): entries(c.get("history")) for c in d.get("cash_flow") or [] if isinstance(c, dict)}
    per, infos = {}, {}
    for cat, field in (("operating", "operating_cash_flow"), ("investing", "investing_cash_flow"), ("financing", "financing_cash_flow")):
        hist = cats.get(cat) or []
        if fy_month:
            hist = [h for h in hist if (period_info(h.get("period")) or {}).get("month") == fy_month]
        per[field], i = period_values(hist)
        infos.update(i)
    months = {i["month"] for i in infos.values()}
    if not fy_month and len(months) > 1:
        return _rejected("mixed_month_ends")
    full = [x for x in (d.get("full_statement") or []) if isinstance(x, dict) and x.get("particular")]
    totals = [x for x in full if capex_class(x["particular"]) == "total"]
    capex = period_values(totals[0].get("history"))[0] if len(totals) == 1 else {}
    years = {}
    for fy, info in infos.items():
        vals = {f: None for f in CASH_FIELDS}
        for f in ("operating_cash_flow", "investing_cash_flow", "financing_cash_flow"):
            vals[f] = per[f].get(fy)
        if all(vals[f] is None for f in ("operating_cash_flow", "investing_cash_flow", "financing_cash_flow")):
            continue
        cx = capex.get(fy)
        if cx is not None:
            vals["capex"] = abs(cx)
            if vals["operating_cash_flow"] is not None:                 # explicit capex only; never operating minus investing
                vals["free_cash_flow"] = round(vals["operating_cash_flow"] - vals["capex"], 2)
        years[fy] = {"period": period_label(info), "values": vals, "checks": {}, "other_lines": {}}
    if not years:
        return _rejected("no_data")
    return {"years": years, "problem": None, "month": fy_month or next(iter(months))}


# ---------------------------------------------------------------- the ledger
def new_stock(symbol, isin, name, sector):
    return {"symbol": symbol, "isin": isin, "company_name": name, "sector": sector, "statement_layout": classify_layout(sector),
            "fiscal_year_end_month": None, "years": [], "gaps": {}, "fetch_state": {}}


def missing_of(values):
    return [f for f in CORE_FIELDS if values.get(f) is None]


def sort_years(stock):
    stock["years"].sort(key=lambda r: (-r["fy"], BASES.index(r["basis"])))


def compute_gaps(years):
    out = {}
    for basis in BASES:
        fys = sorted({r["fy"] for r in years if r["basis"] == basis})
        gap = [y for y in range(fys[-1] - 1, fys[0], -1) if y not in fys] if fys else []
        if gap:
            out[basis] = gap
    return out


def _status(values):
    """Record status = was the required detail extracted? Summary/detail reconciliation never decides this (see _reconcile)."""
    return "verified" if any(values.get(f) is not None for f in CORE_FIELDS) else "incomplete"


def _reconcile(checks):
    """(reconciliation, warnings): an optional cross-check of Upstox's summary lines against its detail lines. Informational only."""
    bad = sorted(k for k, v in checks.items() if v is False)
    if bad:
        return "mismatch", bad
    return ("matched" if any(v is True for v in checks.values()) else "not_checked"), []


def _set_verification(r):
    ver = r["verification"]
    ver["status"] = _status(r["values"])
    ver["reconciliation"], ver["warnings"] = _reconcile(ver["checks"])


def upsert(stock, years, statement, basis, endpoint, fetched, today_, month=None):
    """Merge parsed years of ONE statement into the stock's ledger. Returns counters; 'refused' is set when nothing was merged."""
    stats = {"created": 0, "revised": 0, "filled": 0, "confirmed": 0, "refused": None}
    fields = STATEMENTS[statement][1]
    if month is None and years:
        month = (period_info(next(iter(years.values()))["period"]) or {}).get("month")
    have = stock.get("fiscal_year_end_month")
    if have and month and have != month:
        stats["refused"] = "fiscal_year_end_changed"
        return stats
    if month and not have:
        stock["fiscal_year_end_month"] = month
    for fy, y in years.items():
        info = period_info(y["period"])
        r = next((x for x in stock["years"] if x["fy"] == fy and x["basis"] == basis), None)
        if r is not None and (r.get("source") or {}).get("provider") != "Upstox":
            stats["skipped_other_provider"] = stats.get("skipped_other_provider", 0) + 1      # an official-report record keeps its own provenance
            continue
        if r is None:
            r = {"fy": fy, "period": y["period"], "period_end": info["period_end"], "basis": basis,
                 "values": {f: None for f in VALUE_FIELDS}, "missing": [],
                 "verification": {"status": "incomplete", "reconciliation": "not_checked", "warnings": [], "checks": {k: None for k in CHECK_KEYS}},
                 "source": {"provider": "Upstox", "income": None, "cash_flow": None},
                 "first_seen": today_, "last_confirmed": today_, "revisions": []}
            stock["years"].append(r)
            stats["created"] += 1
        prev_fetched = (r["source"].get(statement) or {}).get("fetched")
        old = {}
        for f in fields:
            v = y["values"].get(f)
            if v is None:
                continue                                     # a missing incoming value never erases an existing one
            cur = r["values"][f]
            if cur is None:
                r["values"][f] = v
                stats["filled"] += 1
            elif abs(cur - v) >= 0.005:
                old[f] = cur
                r["values"][f] = v
        old_other = {}
        if y.get("other_lines"):
            cur_other = r.setdefault("other_lines", {}).setdefault(statement, {})
            for label, v in y["other_lines"].items():
                if label in cur_other and abs(cur_other[label] - v) >= 0.005:
                    old_other[label] = cur_other[label]
                cur_other[label] = v
        if old or old_other:
            rev = {"superseded_on": today_, "source_fetched": prev_fetched, "values": old}
            if old_other:
                rev["other_lines"] = old_other
            r["revisions"].append(rev)
            stats["revised"] += len(old) + len(old_other)
        for k, v in (y.get("checks") or {}).items():
            if v is not None:
                r["verification"]["checks"][k] = v
        r["missing"] = missing_of(r["values"])
        _set_verification(r)
        r["source"][statement] = {"endpoint": endpoint, "fetched": fetched}
        r["last_confirmed"] = today_
        stats["confirmed"] += 1
    sort_years(stock)
    stock["gaps"] = compute_gaps(stock["years"])
    return stats


# ---------------------------------------------------------------- fetching one stock
def is_fresh(state, today_):
    try:
        return (dt.date.fromisoformat(today_) - dt.date.fromisoformat((state or {}).get("fetched"))).days < REFRESH_DAYS
    except (TypeError, ValueError):
        return False


def fetch_statement(api, isin, statement, layout, fy_month):
    """(parsed, basis, endpoint, error). Consolidated first; standalone only when consolidated has no usable data."""
    path = STATEMENTS[statement][0]
    reasons = []
    for basis in BASES:
        params = {"type": basis, "time_period": "yearly", "fs": "true"} if statement == "income" else {"type": basis, "fs": "true"}
        body, err = api.get(isin + "/" + path, params)
        if err:
            if basis == "consolidated" and err.startswith("HTTP 4") and err != "HTTP 429":
                reasons.append("%s (%s)" % (err, basis))
                continue                                      # consolidated not available -> try standalone
            return None, None, None, "; ".join(reasons + ["%s (%s)" % (err, basis)]) if reasons else err   # network / server / limit problem: never switch basis
        parsed = parse_income(body, layout) if statement == "income" else parse_cashflow(body, fy_month)
        if parsed["problem"] is None:
            endpoint = path + "?" + "&".join("%s=%s" % (k, v) for k, v in params.items())
            return parsed, basis, endpoint, None
        reasons.append("%s (%s)" % (parsed["problem"], basis))
    return None, None, None, "; ".join(reasons) or "no usable data"


def process_stock(api, stock, today_):
    """Fetch (when stale) and merge both statements for one stock. Returns a list of problem strings; the ledger is never damaged by a failure."""
    problems, state = [], stock.setdefault("fetch_state", {})
    for statement in ("income", "cash_flow"):
        if is_fresh(state.get(statement), today_):
            continue
        parsed, basis, endpoint, err = fetch_statement(api, stock["isin"], statement, stock.get("statement_layout", "standard"), stock.get("fiscal_year_end_month"))
        old = state.get(statement) or {}
        if err:
            state[statement] = {"fetched": old.get("fetched"), "basis": old.get("basis"), "error": err}
            problems.append("%s: %s" % (statement, err))
            continue
        stats = upsert(stock, parsed["years"], statement, basis, endpoint, today_, today_, month=parsed["month"])
        if stats["refused"]:
            state[statement] = {"fetched": old.get("fetched"), "basis": old.get("basis"), "error": stats["refused"]}
            problems.append("%s: %s" % (statement, stats["refused"]))
            continue
        state[statement] = {"fetched": today_, "basis": basis, "error": None}
    return problems


# ---------------------------------------------------------------- consistency with out/financials.json (warnings only)
def compare_with_financials(stocks, fin_doc):
    """Compare each stock's ledger record for the period financials.json shows (same period AND same basis). Differences are WARNINGS;
    nothing is modified. Exact equality is not required (different fetch times)."""
    rows = fin_doc.get("stocks") if isinstance(fin_doc, dict) else None
    if isinstance(rows, list):
        rows = {r.get("symbol"): r for r in rows if isinstance(r, dict)}
    if not isinstance(rows, dict) or not rows:
        return [{"type": "financials_unavailable"}]
    pairs = {"income": [(f, f) for f in INCOME_FIELDS],
             "cash_flow": [("operating", "operating_cash_flow"), ("investing", "investing_cash_flow"), ("financing", "financing_cash_flow"),
                           ("capex", "capex"), ("free_cash_flow", "free_cash_flow")]}
    warnings = []
    for sym, stock in sorted(stocks.items()):
        row = rows.get(sym)
        if not isinstance(row, dict):
            continue
        for section in ("income", "cash_flow"):
            sec = row.get(section)
            if not isinstance(sec, dict):
                continue
            info = period_info(sec.get("period"))
            if not info:
                continue
            basis = sec.get("basis") or row.get("basis")
            rec = next((r for r in stock["years"] if r["fy"] == info["fy"] and r["basis"] == basis and period_info(r["period"])["month"] == info["month"]), None)
            if rec is None:
                warnings.append({"symbol": sym, "type": "no_common_period", "statement": section, "period": sec.get("period"), "basis": basis})
                continue
            for fin_key, led_key in pairs[section]:
                a, b = rec["values"].get(led_key), sec.get(fin_key)
                if isnum(a) and isnum(b) and abs(a - b) > DIFF_TOLERANCE:
                    warnings.append({"symbol": sym, "type": "value_difference", "statement": section, "period": rec["period"], "basis": basis,
                                     "field": led_key, "ledger": a, "financials": b})
    return warnings


# ---------------------------------------------------------------- output and validation
def build_output(ledger, errors, warnings, today_):
    stocks = {}
    for sym, s in sorted(ledger["stocks"].items()):
        if s.get("years"):
            c = copy.deepcopy(s)
            c.pop("fetch_state", None)
            stocks[sym] = c
    return {"schema": SCHEMA_VERSION, "as_of": today_, "source": "Upstox (API) and official company annual reports - the provider is stated on every record", "interval": "annual", "notes": NOTES,
            "stocks": stocks, "errors": errors, "warnings": warnings}


def validate_doc(doc):
    """Structure checks. Returns a list of problems (empty = valid)."""
    p = []
    if not isinstance(doc, dict):
        return ["document is not an object"]
    if doc.get("schema") != SCHEMA_VERSION:
        p.append("schema version is not %d" % SCHEMA_VERSION)
    stocks = doc.get("stocks")
    if not isinstance(stocks, dict):
        return p + ["stocks is not an object"]
    for sym, s in stocks.items():
        w = sym + ": "
        if not isinstance(s, dict):
            p.append(w + "stock is not an object")
            continue
        if s.get("symbol") != sym:
            p.append(w + "symbol does not match its key")
        if s.get("statement_layout") not in LAYOUTS:
            p.append(w + "bad statement_layout")
        m = s.get("fiscal_year_end_month")
        if m is not None and not (isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12):
            p.append(w + "bad fiscal_year_end_month")
        years = s.get("years")
        if not isinstance(years, list):
            p.append(w + "years is not a list")
            continue
        keys = []
        for r in years:
            if not isinstance(r, dict):
                p.append(w + "a year record is not an object")
                continue
            fy, info = r.get("fy"), period_info(r.get("period"))
            x = w + "FY%s %s: " % (fy, r.get("basis"))
            if not (isinstance(fy, int) and not isinstance(fy, bool)):
                p.append(x + "fy is not an integer")
            if not info:
                p.append(x + "period does not parse")
            else:
                if info["fy"] != fy:
                    p.append(x + "fy does not match period")
                if r.get("period_end") != info["period_end"]:
                    p.append(x + "period_end does not match period")
            if r.get("basis") not in BASES:
                p.append(x + "bad basis")
            keys.append((fy, r.get("basis")))
            v = r.get("values")
            if not isinstance(v, dict) or set(v) != set(VALUE_FIELDS):
                p.append(x + "values must contain exactly the defined fields")
                continue
            for f, val in v.items():
                if val is not None and not isnum(val):
                    p.append(x + "%s is not a finite number or null" % f)
            if all(val is None for val in v.values()):
                p.append(x + "record has no value at all")
            if r.get("missing") != missing_of(v):
                p.append(x + "missing list does not match the null core fields")
            if v.get("free_cash_flow") is not None:
                cx, op, fc = v.get("capex"), v.get("operating_cash_flow"), v["free_cash_flow"]
                if not (isnum(cx) and isnum(op) and abs(fc - (op - cx)) <= DIFF_TOLERANCE):
                    p.append(x + "free_cash_flow is not operating cash flow minus an explicit capex value")
            ver = r.get("verification")
            if not isinstance(ver, dict) or ver.get("status") not in ("verified", "incomplete") or ver.get("reconciliation") not in ("matched", "mismatch", "not_checked") or not isinstance(ver.get("warnings"), list) or not isinstance(ver.get("checks"), dict):
                p.append(x + "bad verification")
            src = r.get("source")
            if not isinstance(src, dict) or src.get("provider") not in PROVIDERS:
                p.append(x + "bad source")
            elif src["provider"] == "Upstox":
                for st in ("income", "cash_flow"):
                    e = src.get(st)
                    if e is not None and not (isinstance(e, dict) and isinstance(e.get("endpoint"), str) and isdate(e.get("fetched"))):
                        p.append(x + "bad source metadata for " + st)
            else:
                p.extend(x + m for m in _official_source_problems(src, v))
            if not isdate(r.get("first_seen")) or not isdate(r.get("last_confirmed")):
                p.append(x + "first_seen / last_confirmed are not dates")
            revs = r.get("revisions")
            if not isinstance(revs, list):
                p.append(x + "revisions is not a list")
            else:
                for rv in revs:
                    if not (isinstance(rv, dict) and isdate(rv.get("superseded_on")) and isinstance(rv.get("values"), dict)
                            and set(rv["values"]) <= set(VALUE_FIELDS) and all(isnum(z) for z in rv["values"].values())):
                        p.append(x + "bad revision entry")
            if "notes" in r and not (isinstance(r["notes"], list) and all(isinstance(n, str) for n in r["notes"])):
                p.append(x + "notes is not a list of text")
            if "other_lines" in r and not isinstance(r["other_lines"], dict):
                p.append(x + "other_lines is not an object")
        if len(set(keys)) != len(keys):
            p.append(w + "duplicate (fiscal year, basis) records")
        elif keys != sorted(keys, key=lambda k: (-(k[0] if isinstance(k[0], int) else 0), BASES.index(k[1]) if k[1] in BASES else 9)):
            p.append(w + "years are not sorted newest first")
        if s.get("gaps") != compute_gaps([r for r in years if isinstance(r, dict) and r.get("basis") in BASES and isinstance(r.get("fy"), int)]):
            p.append(w + "gaps do not match the years")
    return p


# ---------------------------------------------------------------- official annual-report records (Step 4B)
def _https(u):
    return isinstance(u, str) and u.startswith("https://") and len(u) > 12 and not re.search(r"[\s\"'<>]", u)


def _field_meta_problems(f, m):
    if not isinstance(m, dict):
        return ["source.fields.%s is not an object" % f]
    out = []
    if not _https(m.get("document_url")):
        out.append("source.fields.%s needs an https document_url" % f)
    pg = m.get("page")
    if not ((isinstance(pg, int) and not isinstance(pg, bool) and pg >= 1) or (isinstance(pg, str) and pg.strip())):
        out.append("source.fields.%s needs a page" % f)
    if not (isinstance(m.get("exact_label"), str) and m["exact_label"].strip()):
        out.append("source.fields.%s needs an exact_label" % f)
    if "heading" in m and not (isinstance(m["heading"], str) and m["heading"].strip()):
        out.append("source.fields.%s heading must be text" % f)
    return out


def _official_source_problems(src, values):
    out = []
    for k in ("company", "accounting_basis", "units"):
        if not (isinstance(src.get(k), str) and src[k].strip()):
            out.append("official source needs " + k)
    if not isdate(src.get("retrieved_on")):
        out.append("official source needs a retrieved_on date")
    fields = src.get("fields")
    if not isinstance(fields, dict):
        return out + ["official source fields is not an object"]
    for f, m in fields.items():
        if f not in VALUE_FIELDS:
            out.append("source.fields.%s is not a defined field" % f)
        else:
            out.extend(_field_meta_problems(f, m))
    have = {f for f, val in values.items() if val is not None}
    if have != set(fields):
        out.append("source.fields must describe exactly the fields that hold a value")
    cc = src.get("cross_checks", [])
    if not (isinstance(cc, list) and all(isinstance(c, dict) and _https(c.get("document_url")) and isinstance(c.get("result"), str) for c in cc)):
        out.append("bad cross_checks")
    return out


def validate_official(doc):
    """Check the curated official-records file. Returns a list of problems (empty = valid)."""
    if not isinstance(doc, dict) or doc.get("schema") != 1 or not isinstance(doc.get("records"), list):
        return ["official records file has the wrong shape"]
    p, seen = [], set()
    for i, r in enumerate(doc["records"]):
        w = "record %d: " % i
        if not isinstance(r, dict):
            p.append(w + "not an object")
            continue
        info = period_info(r.get("period"))
        key = (r.get("symbol"), r.get("fy"), r.get("basis"))
        w = "%s FY%s %s: " % key
        if not (isinstance(r.get("symbol"), str) and r["symbol"].strip()):
            p.append(w + "symbol missing")
        if not info or info["fy"] != r.get("fy") or not (isinstance(r.get("fy"), int) and not isinstance(r.get("fy"), bool)):
            p.append(w + "fy does not match the period")
        if r.get("basis") not in BASES:
            p.append(w + "bad basis")
        if key in seen:
            p.append(w + "duplicate record")
        seen.add(key)
        v = r.get("values")
        if not isinstance(v, dict) or not set(v) <= set(VALUE_FIELDS):
            p.append(w + "values has unknown fields")
            continue
        if any(val is not None and not isnum(val) for val in v.values()) or all(val is None for val in v.values()):
            p.append(w + "values must be numbers or null, with at least one number")
            continue
        src = {"company": r.get("company"), "accounting_basis": r.get("accounting_basis"), "units": r.get("units"), "retrieved_on": r.get("retrieved_on"),
               "fields": r.get("fields"), "cross_checks": r.get("cross_checks", [])}
        p.extend(w + m for m in _official_source_problems(src, v))
        if "notes" in r and not (isinstance(r["notes"], list) and all(isinstance(n, str) for n in r["notes"])):
            p.append(w + "notes is not a list of text")
    return p


def apply_official(stock, r, today_):
    """Add or confirm ONE verified official record. Returns {"status": ...}. Never touches a record that came from another provider."""
    if r.get("symbol") != stock.get("symbol"):
        return {"status": "wrong_symbol"}
    info = period_info(r["period"])
    have = stock.get("fiscal_year_end_month")
    if have and have != info["month"]:
        return {"status": "refused", "reason": "fiscal_year_end_changed"}
    if not have:
        stock["fiscal_year_end_month"] = info["month"]
    new_vals = {f: r["values"].get(f) for f in VALUE_FIELDS}
    meta = {f: dict(r["fields"][f]) for f in VALUE_FIELDS if new_vals[f] is not None}
    ex = next((x for x in stock["years"] if x["fy"] == r["fy"] and x["basis"] == r["basis"]), None)
    if ex is not None and (ex.get("source") or {}).get("provider") != OFFICIAL_PROVIDER:
        return {"status": "kept_other_provider"}
    status = "confirmed"
    if ex is None:
        ex = {"fy": r["fy"], "period": r["period"], "period_end": info["period_end"], "basis": r["basis"],
              "values": {f: None for f in VALUE_FIELDS}, "missing": [],
              "verification": {"status": "incomplete", "reconciliation": "not_checked", "warnings": [], "checks": {k: None for k in CHECK_KEYS}},
              "source": {"provider": OFFICIAL_PROVIDER, "kind": "annual_report", "company": r["company"], "accounting_basis": r["accounting_basis"],
                         "units": r["units"], "retrieved_on": r["retrieved_on"], "fields": {}, "cross_checks": []},
              "first_seen": today_, "last_confirmed": today_, "revisions": []}
        stock["years"].append(ex)
        status = "created"
    old, old_meta = {}, {}
    for f, v in new_vals.items():
        if v is None:
            continue                                                  # a null never erases an existing value
        cur = ex["values"][f]
        if cur is not None and abs(cur - v) >= 0.005:
            old[f] = cur
            old_meta[f] = (ex["source"]["fields"] or {}).get(f)
        ex["values"][f] = v
    if old:
        ex["revisions"].append({"superseded_on": today_, "source_fetched": ex["source"].get("retrieved_on"), "values": old,
                                "source_fields": {f: m for f, m in old_meta.items() if m}})
        status = "revised"
    ex["source"]["fields"].update(meta)
    ex["source"].update(company=r["company"], accounting_basis=r["accounting_basis"], units=r["units"], retrieved_on=r["retrieved_on"],
                        cross_checks=copy.deepcopy(r.get("cross_checks") or []))
    if r.get("notes"):
        ex["notes"] = list(r["notes"])
    ex["last_confirmed"] = today_
    ex["missing"] = missing_of(ex["values"])
    _set_verification(ex)
    sort_years(stock)
    stock["gaps"] = compute_gaps(stock["years"])
    return {"status": status}


def apply_all_official(ledger, doc, today_):
    """Apply every curated record whose stock is already in the ledger. Stocks Upstox did not produce are skipped, never created."""
    res = {"applied": 0, "skipped": [], "results": []}
    for r in doc["records"]:
        st = ledger["stocks"].get(r["symbol"])
        if st is None:
            res["skipped"].append({"symbol": r["symbol"], "reason": "not in ledger"})
            continue
        out = apply_official(st, r, today_)
        res["results"].append((r["symbol"], r["fy"], out["status"]))
        if out["status"] in ("created", "confirmed", "revised"):
            res["applied"] += 1
    return res


# ---------------------------------------------------------------- main
def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def normalise_ledger(ledger):
    """Re-derive verification from stored values/checks (older ledgers used 'check_failed'). Values, revisions and other_lines are untouched."""
    for st in (ledger.get("stocks") or {}).values():
        for r in (st or {}).get("years") or []:
            if isinstance(r, dict) and isinstance(r.get("verification"), dict) and isinstance(r["verification"].get("checks"), dict) and isinstance(r.get("values"), dict):
                _set_verification(r)


def main():
    token = get_token()
    day = today()
    ledger = read_json(LEDGER_FILE) or {"schema": SCHEMA_VERSION, "stocks": {}}
    if ledger.get("schema") != SCHEMA_VERSION or not isinstance(ledger.get("stocks"), dict):
        fail("The saved ledger has an unexpected schema. It was not changed.")
    normalise_ledger(ledger)
    raw = os.environ.get("HISTORY_SYMBOLS", "").strip()
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()] if raw else list(SYMBOLS)
    fund = read_json(FUNDAMENTALS_FILE) or {}
    sectors = {x.get("symbol"): x.get("sector") for x in (fund.get("stocks") or []) if isinstance(x, dict)}
    instruments = load_instruments()
    api = Upstox(token, int(os.environ.get("HISTORY_MAX_CALLS", DEFAULT_MAX_CALLS)))
    errors = []
    order = sorted(symbols, key=lambda s: min([(v or {}).get("fetched") or "" for v in ((ledger["stocks"].get(s) or {}).get("fetch_state") or {"x": {}}).values()] or [""]))
    for sym in order:
        meta = instruments.get(sym)
        if not meta:
            errors.append({"symbol": sym, "error": "Symbol not found in Upstox NSE instruments"})
            continue
        s = ledger["stocks"].setdefault(sym, new_stock(sym, meta["isin"], meta["name"], sectors.get(sym)))
        s["isin"], s["company_name"] = meta["isin"], meta["name"]
        if sectors.get(sym):
            s["sector"], s["statement_layout"] = sectors[sym], classify_layout(sectors[sym])
        problems = process_stock(api, s, day)
        if problems:
            errors.append({"symbol": sym, "error": "; ".join(problems)})
        log(sym, "-", "ok" if not problems else "problem: " + "; ".join(problems), "| years:", len({r["fy"] for r in s["years"]}))
    official = read_json(OFFICIAL_FILE)
    if official is not None:
        bad = validate_official(official)
        if bad:
            for x in bad[:20]:
                print("::error::official_financial_records: " + x)
            fail("official_financial_records.json failed its checks. Neither the ledger nor the output was written.")
        applied = apply_all_official(ledger, official, day)
        log("Official records applied:", applied["applied"], "| skipped:", len(applied["skipped"]), "|", applied["results"])
    warnings = compare_with_financials(ledger["stocks"], read_json(FINANCIALS_FILE))
    doc = build_output(ledger, errors, warnings, day)
    problems = validate_doc(doc)
    if problems:
        for x in problems[:20]:
            print("::error::financial_history: " + x)
        fail("financial_history failed its checks. Neither the ledger nor the output was written.")
    ledger["updated"] = day
    write_json(LEDGER_FILE, ledger)
    write_json(OUT_FILE, doc)
    log("Wrote", OUT_FILE, "-", len(doc["stocks"]), "stocks,", len(errors), "with problems,", len(warnings), "consistency warnings, API calls:", api.calls)
    for w in warnings[:30]:
        log("WARNING", w.get("symbol", ""), w["type"], w.get("statement", ""), w.get("period", ""), w.get("basis", ""), w.get("field", ""))


if __name__ == "__main__":
    sys.exit(main())
