"""
financials_updater.py - StockLens Phase 2A (data-layer cleanup). 10 test stocks only.
Same architecture as Phase 1: separate cache, per-section freshness, consolidated first, conservative pacing.

What changed in 2A: every statement is requested with fs=true and the DETAILED line items are used, so each
number is stored under its true name (revenue, total_revenue, profit_before_tax, ...). Upstox's summary
categories are used only for growth %, and only when their value equals the matching detail line.
All money values are INR crore. Nothing is estimated.

Calls per stock (3, same as Phase 1):
  /{ISIN}/income-statement?type=..&time_period=yearly&fs=true
  /{ISIN}/balance-sheet?type=..&fs=true
  /{ISIN}/cash-flow?type=..&fs=true            (no time_period sent)
"""
import copy
import json
import re

from upstox_common import (ROOT, SYMBOLS, Upstox, fail, get_token, is_stale, latest, load_instruments,
                           log, num, period_key, today, write_json)

CACHE_FILE = ROOT / "data" / "financials_cache.json"
OUT_FILE = ROOT / "out" / "financials.json"
MAX_CALLS_PER_RUN = 120
STATEMENT_MAX_AGE_DAYS = 30
SCHEMA_VERSION = 2            # cached sections with another version are refetched once
CASHFLOW_PATH = "cash-flow"   # confirmed: /v2/fundamentals/{ISIN}/cash-flow

NOTES = {
    "units": "INR crore",
    "revenue": "revenue is Upstox's 'Revenue' line. total_revenue is its 'Total Revenue' line (revenue plus other income).",
    "profit_before_tax": "From Upstox's 'Profit Before Tax' line. Upstox's summary category 'operating_profit' is NOT used as operating profit; checks.summary_operating_profit_equals_pbt records whether it equals Profit Before Tax.",
    "growth": "Upstox's own period-over-period % change. It is stored only when the summary value equals the matching detail line for the same year (see checks); otherwise it is empty.",
    "net_profit": "Upstox's summary 'net_profit' category value and change for the same year (kept for the existing page). profit_after_tax is the detail line; checks.summary_net_profit_equals_pat shows whether they agree.",
    "total_equity": "Calculated: total assets minus total liabilities (both from Upstox). checks.equity_capital_equals_derived_equity compares it with Upstox's 'Equity Capital' line, which may include non-controlling interests.",
    "liabilities_to_equity": "Calculated: total liabilities divided by total equity. Not Debt/Equity. For banks and financial companies, liabilities are mostly customer deposits, so this ratio is not comparable with other companies. Empty if equity is not positive.",
    "debt": "Total debt and Debt/Equity are not provided by the Upstox balance sheet API and are never derived, so they are empty. debt_lines_found lists any balance-sheet line whose name mentions borrowings or debt (for review only).",
    "free_cash_flow": "Operating cash flow minus capex, only when the cash-flow statement has exactly one clearly named TOTAL capex line for the same year. Part-lines (property/plant/equipment only, intangibles, capital work in progress) are never used. Otherwise empty.",
}

INCOME_LINES = {"revenue": "revenue", "other income": "other_income", "total revenue": "total_revenue",
                "profit before tax": "profit_before_tax", "tax": "tax", "profit after tax": "profit_after_tax",
                "eps basic": "eps_basic", "eps diluted": "eps_diluted"}
BALANCE_LINES = {"non current assets": "non_current_assets", "current assets": "current_assets",
                 "current liabilities": "current_liabilities", "non current liabilities": "non_current_liabilities",
                 "equity capital": "equity_capital", "total equity & liabilities": "total_equity_and_liabilities"}
TOTAL_CAPEX_LABELS = {"capital expenditure", "capital expenditures", "capex", "purchase of fixed assets",
                      "purchase of property plant and equipment and intangible assets",
                      "purchase of property plant and equipment and intangibles"}


# ---------------------------------------------------------------- helpers
def norm(label):
    return " ".join(re.sub(r"[^a-z0-9& ]", " ", str(label).lower()).split())


def lines(full_statement):
    """full_statement -> ({normalised label: history}, [original labels]). A label that repeats is dropped."""
    found, seen, labels = {}, set(), []
    for x in full_statement or []:
        if not isinstance(x, dict) or not x.get("particular"):
            continue
        labels.append(str(x["particular"]))
        k = norm(x["particular"])
        if k in seen:
            found.pop(k, None)
            continue
        seen.add(k)
        found[k] = [h for h in (x.get("history") or []) if isinstance(h, dict)]
    return found, labels


def entry_at(history, period):
    return next((h for h in (history or []) if isinstance(h, dict) and h.get("period") == period), None)


def value_at(history, period):
    e = entry_at(history, period)
    return num(e.get("value")) if e else None


def same(a, b):
    """True/False when both numbers exist, else None (could not check)."""
    return None if a is None or b is None else abs(a - b) <= 0.01


def pick_period(entries):
    """entries: {key: history-entry or None}. Returns the newest period present (no year mixing)."""
    present = [e for e in entries.values() if e and e.get("period")]
    if not present:
        return None, {k: None for k in entries}
    newest = max((e["period"] for e in present), key=lambda p: period_key(p) or (0, 0))
    return newest, {k: (e if e and e.get("period") == newest else None) for k, e in entries.items()}


def is_yearly(d):
    return str(d.get("time_period", "yearly")).lower() == "yearly"


# ---------------------------------------------------------------- income statement
def parse_income(body, fy_month=None):
    d = body.get("data") or {}
    if not is_yearly(d):
        return None
    fs, _ = lines(d.get("full_statement"))
    summ = {str(c.get("category", "")).lower(): [h for h in (c.get("history") or []) if isinstance(h, dict)]
            for c in (d.get("income_statement") or []) if isinstance(c, dict)}
    detail = {key: fs[label] for label, key in INCOME_LINES.items() if label in fs}
    period, _ = pick_period({k: latest(h) for k, h in detail.items()})
    if period is None:                                  # no detail lines: newest summary period
        period, _ = pick_period({k: latest(h) for k, h in summ.items()})
    out = {"period": period}
    out.update({k: None for k in INCOME_LINES.values()})
    out.update({"total_revenue_growth": None, "profit_before_tax_growth": None, "profit_after_tax_growth": None,
                "net_profit": None, "net_profit_growth": None,
                "checks": {"summary_revenue_equals_total_revenue": None,
                           "summary_operating_profit_equals_pbt": None,
                           "summary_net_profit_equals_pat": None}})
    for key, hist in detail.items():
        out[key] = value_at(hist, period)
    s_rev, s_op, s_np = (entry_at(summ.get(c), period) for c in ("revenue", "operating_profit", "net_profit"))
    if s_np:                                            # Upstox's own labelled field, same year
        out["net_profit"], out["net_profit_growth"] = num(s_np.get("value")), num(s_np.get("change"))
    for check, s, key, growth in (
            ("summary_revenue_equals_total_revenue", s_rev, "total_revenue", "total_revenue_growth"),
            ("summary_operating_profit_equals_pbt", s_op, "profit_before_tax", "profit_before_tax_growth"),
            ("summary_net_profit_equals_pat", s_np, "profit_after_tax", "profit_after_tax_growth")):
        if s:
            ok = same(num(s.get("value")), out[key])
            out["checks"][check] = ok
            if ok:                                       # growth only where the metric is verified
                out[growth] = num(s.get("change"))
    data_keys = list(INCOME_LINES.values()) + ["net_profit"]
    return out if any(out[k] is not None for k in data_keys) else None


# ---------------------------------------------------------------- balance sheet
def parse_balance(body, fy_month=None):
    d = body.get("data") or {}
    if not is_yearly(d):
        return None
    h = latest(d.get("history"))
    if not h:
        return None
    period = h.get("period")
    ta, tl = num(h.get("total_asset")), num(h.get("total_liability"))
    eq = round(ta - tl, 2) if ta is not None and tl is not None else None
    ratio = round(tl / eq, 2) if eq is not None and eq > 0 else None
    fs, labels = lines(d.get("full_statement"))
    out = {"period": period, "total_assets": ta, "total_liabilities": tl, "total_equity": eq,
           "liabilities_to_equity": ratio}
    out.update({key: value_at(fs.get(label), period) for label, key in BALANCE_LINES.items()})
    out.update({"total_debt": None, "debt_to_equity": None,
                "debt_lines_found": [x for x in labels if re.search(r"borrow|debt", x, re.I)],
                "line_items": labels,
                "checks": {"total_assets_equals_detail": same(ta, value_at(fs.get("total assets"), period)),
                           "equity_capital_equals_derived_equity": same(eq, out["equity_capital"])}})
    return out if ta is not None or tl is not None else None


# ---------------------------------------------------------------- cash flow
def capex_class(label):
    """'total' = clearly the whole capex line; 'partial' = a part-line or unrecognised variant; None = not capex."""
    t = norm(label).replace(" & ", " and ")
    if any(w in t for w in ("sale", "disposal", "proceeds", "investment")):
        return None
    if t in TOTAL_CAPEX_LABELS:
        return "total"
    assets = ("property plant", "ppe", "intangible", "capital work", "cwip", "fixed assets", "capital expenditure", "capex")
    if any(w in t for w in assets) and (t.startswith(("purchase", "acquisition", "payment", "addition"))
                                        or "capital work" in t or "cwip" in t):
        return "partial"
    return None


def parse_cashflow(body, fy_month=None):
    """Latest annual period from the returned history (no time_period is sent for cash flow). Annual entries are
    recognised by the financial-year-end month taken from the same company's other statements; mixed or
    quarterly history without that reference is rejected, not guessed."""
    d = body.get("data") or {}
    cats = {str(c.get("category", "")).lower(): c for c in (d.get("cash_flow") or []) if isinstance(c, dict)}
    hist = {k: [h for h in ((cats.get(k) or {}).get("history") or []) if isinstance(h, dict)]
            for k in ("operating", "investing", "financing")}
    months = {period_key(h.get("period"))[1] for hs in hist.values() for h in hs if period_key(h.get("period"))}
    if fy_month:
        hist = {k: [h for h in hs if (period_key(h.get("period")) or (0, 0))[1] == fy_month] for k, hs in hist.items()}
    elif str(d.get("time_period", "")).lower() == "quarterly" or len(months) > 1:
        return None
    period, ent = pick_period({k: latest(hs) for k, hs in hist.items()})
    out = {"period": period}
    for k, e in ent.items():
        out[k] = num(e.get("value")) if e else None
    items = [x for x in (d.get("full_statement") or []) if isinstance(x, dict) and x.get("particular")]
    cands = [{"label": str(x["particular"]), "class": capex_class(x["particular"])}
             for x in items if capex_class(x["particular"])]
    totals = [x for x in items if capex_class(x["particular"]) == "total"]
    out.update({"capex": None, "capex_line": None, "free_cash_flow": None, "capex_candidates": cands,
                "capex_status": "none" if not cands else "partial_only" if not totals else None,
                "line_items": [str(x["particular"]) for x in items]})
    if len(totals) > 1:
        out["capex_status"] = "ambiguous"
    elif len(totals) == 1:
        v = value_at(totals[0].get("history"), period) if period else None
        out["capex_status"] = "found" if v is not None else "no_value_for_period"
        if v is not None:
            out["capex"], out["capex_line"] = abs(v), str(totals[0]["particular"])
            if out["operating"] is not None:
                out["free_cash_flow"] = round(out["operating"] - out["capex"], 2)
    return out if any(out[k] is not None for k in ("operating", "investing", "financing")) else None


SECTIONS = (("income", "income-statement", {"time_period": "yearly", "fs": "true"}, parse_income),
            ("balance_sheet", "balance-sheet", {"fs": "true"}, parse_balance),
            ("cash_flow", CASHFLOW_PATH, {"fs": "true"}, parse_cashflow))   # no time_period for cash flow

EMPTY = {
    "income": dict({"period": None}, **{k: None for k in list(INCOME_LINES.values()) + [
        "total_revenue_growth", "profit_before_tax_growth", "profit_after_tax_growth", "net_profit", "net_profit_growth"]},
        checks={"summary_revenue_equals_total_revenue": None, "summary_operating_profit_equals_pbt": None,
                "summary_net_profit_equals_pat": None}),
    "balance_sheet": dict({"period": None}, **{k: None for k in [
        "total_assets", "total_liabilities", "total_equity", "liabilities_to_equity"] + list(BALANCE_LINES.values()) + [
        "total_debt", "debt_to_equity"]}, debt_lines_found=[], line_items=[],
        checks={"total_assets_equals_detail": None, "equity_capital_equals_derived_equity": None}),
    "cash_flow": {"period": None, "operating": None, "investing": None, "financing": None, "capex": None,
                  "capex_line": None, "free_cash_flow": None, "capex_candidates": [], "capex_status": None,
                  "line_items": []},
}


# ---------------------------------------------------------------- fetching and main
def fy_month(e):
    for name in ("balance_sheet", "income"):
        k = period_key(((e.get(name) or {}).get("data") or {}).get("period"))
        if k:
            return k[1]
    return None


def fetch_section(api, isin, path, extra, parser, fy=None):
    """(parsed, basis, error). Consolidated first; standalone only when consolidated has no data."""
    last = "no usable data"
    for basis in ("consolidated", "standalone"):
        body, err = api.get(isin + "/" + path, dict(extra, type=basis))
        if err:
            if basis == "consolidated" and err.startswith("HTTP 4") and err != "HTTP 429":
                last = err
                continue                      # consolidated not available -> try standalone
            return None, None, err            # network / server problem: do not switch basis
        parsed = parser(body, fy)
        if parsed:
            return parsed, basis, None
        last = "no usable data (" + basis + ")"
    return None, None, last


def needs_refresh(sec):
    return is_stale(sec.get("fetched"), STATEMENT_MAX_AGE_DAYS) or sec.get("schema") != SCHEMA_VERSION


def main():
    token = get_token()
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    stocks = cache.setdefault("stocks", {})
    instruments = load_instruments()
    api = Upstox(token, MAX_CALLS_PER_RUN)

    for sym in SYMBOLS:
        e = stocks.setdefault(sym, {})
        m = instruments.get(sym)
        if not m:
            e["last_error"] = "Symbol not found in Upstox NSE equity instruments"
            log(sym, "- not found")
            continue
        e["isin"], e["company_name"] = m["isin"], m["name"]
        problems = []
        for name, path, extra, parser in SECTIONS:
            sec = e.setdefault(name, {})
            if not needs_refresh(sec):
                continue
            data, basis, err = fetch_section(api, m["isin"], path, extra, parser, fy_month(e))
            if err:
                sec["error"] = err
                problems.append(name + ": " + err)
            else:
                sec.update({"data": data, "basis": basis, "fetched": today(), "schema": SCHEMA_VERSION, "error": None})
        e["last_error"] = "; ".join(problems) or None
        log(sym, "- ok" if not problems else "- problem: " + e["last_error"])

    rows, errors, dates = [], [], []
    for sym in SYMBOLS:
        e = stocks.get(sym, {})
        if e.get("last_error"):
            errors.append({"symbol": sym, "error": e["last_error"]})
        if not e.get("isin"):
            continue
        row, bases = {"symbol": sym, "isin": e["isin"], "company_name": e.get("company_name")}, set()
        for name, *_ in SECTIONS:
            sec = e.get(name) or {}
            ok = bool(sec.get("data")) and sec.get("schema") == SCHEMA_VERSION
            row[name] = dict(sec["data"] if ok else copy.deepcopy(EMPTY[name]), basis=sec.get("basis") if ok else None)
            if ok:
                bases.add(sec["basis"])
                dates.append(sec["fetched"])
        if not bases:
            continue                          # never publish a stock with no real statement data
        row["basis"] = bases.pop() if len(bases) == 1 else "mixed"
        rows.append(row)

    write_json(CACHE_FILE, cache)
    if not rows:
        fail("No financial statements could be fetched. The previous out/financials.json (if any) was kept.")
    write_json(OUT_FILE, {"schema": SCHEMA_VERSION, "as_of": max(dates), "source": "Upstox", "notes": NOTES,
                          "stocks": rows, "errors": errors})
    log("Wrote", OUT_FILE, "-", len(rows), "stocks,", len(errors), "with problems, API calls:", api.calls)

    log("=== CAPEX CHECK (line labels only; no values, no secrets) ===")
    for r in rows:
        cf = r["cash_flow"]
        log("%s: capex_status=%s | FCF=%s | candidates=%s" % (
            r["symbol"], cf.get("capex_status"), "yes" if cf.get("free_cash_flow") is not None else "null",
            json.dumps(cf.get("capex_candidates"))))
        log("   cash-flow lines (%d): %s" % (len(cf.get("line_items") or []), json.dumps(cf.get("line_items"))))
        log("   debt-like balance-sheet lines: %s" % json.dumps(r["balance_sheet"].get("debt_lines_found")))


if __name__ == "__main__":
    main()
