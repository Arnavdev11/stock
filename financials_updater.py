"""
financials_updater.py - StockLens Phase 1: income statement, balance sheet, cash flow (Upstox).
10 test stocks only. Yearly data, consolidated first; standalone only if consolidated has no data.
Output: out/financials.json   Cache: data/financials_cache.json (separate from the fundamentals cache).
All money values are INR crore, exactly as Upstox provides them. Nothing is estimated.

Endpoints (per stock, 3 calls):
  /{ISIN}/income-statement?type=..&time_period=yearly
  /{ISIN}/balance-sheet?type=..
  /{ISIN}/cash-flow?type=..&fs=true      (no time_period sent; fs=true only to look for a genuine capex line)
Cash flow: the latest ANNUAL period is chosen from the returned history (see parse_cashflow).
Growth values are Upstox's own period-over-period "change"; none are calculated here.
"""
import json
import re

from upstox_common import (ROOT, SYMBOLS, Upstox, fail, get_token, is_stale, latest, load_instruments,
                           log, num, period_key, today, write_json)

CACHE_FILE = ROOT / "data" / "financials_cache.json"
OUT_FILE = ROOT / "out" / "financials.json"
MAX_CALLS_PER_RUN = 120
STATEMENT_MAX_AGE_DAYS = 30
CASHFLOW_PATH = "cash-flow"      # confirmed: /v2/fundamentals/{ISIN}/cash-flow

NOTES = {
    "units": "INR crore",
    "total_equity": "Calculated: total assets minus total liabilities (both from Upstox).",
    "liabilities_to_equity": "Calculated: total liabilities divided by total equity. Not Debt/Equity. Empty if equity is not positive.",
    "debt": "Total debt and Debt/Equity are not provided by the Upstox balance sheet API, so they are empty.",
    "operating_profit": "Operating profit as labelled by Upstox; in Upstox's sample it equals Profit Before Tax.",
    "growth": "Upstox's own period-over-period change for the latest year. Not calculated by StockLens.",
    "free_cash_flow": "Operating cash flow minus capex, only when the statement has exactly one clearly named capex line for the same year. Otherwise empty.",
}


def pick_period(entries):
    """entries: {key: history-entry or None}. Keep only entries of the newest period (no year mixing)."""
    present = [e for e in entries.values() if e and e.get("period")]
    if not present:
        return None, {k: None for k in entries}
    newest = max((e["period"] for e in present), key=lambda p: period_key(p) or (0, 0))
    return newest, {k: (e if e and e.get("period") == newest else None) for k, e in entries.items()}


def is_yearly(d):
    return str(d.get("time_period", "yearly")).lower() == "yearly"


def parse_income(body, fy_month=None):
    d = body.get("data") or {}
    if not is_yearly(d):
        return None
    cats = {str(c.get("category", "")).lower(): c for c in (d.get("income_statement") or []) if isinstance(c, dict)}
    period, ent = pick_period({k: latest((cats.get(k) or {}).get("history"))
                               for k in ("revenue", "operating_profit", "net_profit")})
    out = {"period": period}
    for k, e in ent.items():
        out[k] = num(e.get("value")) if e else None
        out[k + "_growth"] = num(e.get("change")) if e else None
    return out if any(v is not None for k, v in out.items() if k != "period") else None


def parse_balance(body, fy_month=None):
    d = body.get("data") or {}
    if not is_yearly(d):
        return None
    h = latest(d.get("history"))
    if not h:
        return None
    ta, tl = num(h.get("total_asset")), num(h.get("total_liability"))
    eq = round(ta - tl, 2) if ta is not None and tl is not None else None
    ratio = round(tl / eq, 2) if eq is not None and eq > 0 else None
    out = {"period": h.get("period"), "total_assets": ta, "total_liabilities": tl, "total_equity": eq,
           "liabilities_to_equity": ratio, "total_debt": None, "debt_to_equity": None}
    return out if ta is not None or tl is not None else None


def is_capex_line(label):
    t = " ".join(re.sub(r"[^a-z0-9 ]", " ", str(label).lower()).split())
    if any(w in t for w in ("intangible", "investment", "sale", "disposal", "proceeds")):
        return False
    if t in ("capital expenditure", "capital expenditures", "capex"):
        return True
    return (t.startswith(("purchase of", "acquisition of", "payment for", "payments for"))
            and any(k in t for k in ("fixed assets", "property plant", "ppe", "capital work")))


def parse_cashflow(body, fy_month=None):
    """Latest annual period from the returned history. Upstox's cash-flow call gets no time_period parameter,
    so annual entries are recognised by the financial-year-end month (taken from the same company's balance
    sheet / income statement). Mixed or quarterly history without that reference is rejected, not guessed."""
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
    out.update({"capex": None, "capex_line": None, "free_cash_flow": None})
    hits = [x for x in (d.get("full_statement") or []) if isinstance(x, dict) and is_capex_line(x.get("particular"))]
    if len(hits) == 1 and period:                     # zero or several candidates -> no FCF
        same = [e for e in (hits[0].get("history") or []) if isinstance(e, dict) and e.get("period") == period]
        v = num(same[0].get("value")) if same else None
        if v is not None:
            out["capex"], out["capex_line"] = abs(v), str(hits[0].get("particular"))
            if out["operating"] is not None:
                out["free_cash_flow"] = round(out["operating"] - out["capex"], 2)
    return out if any(out[k] is not None for k in ("operating", "investing", "financing")) else None


SECTIONS = (("income", "income-statement", {"time_period": "yearly"}, parse_income),
            ("balance_sheet", "balance-sheet", {}, parse_balance),
            ("cash_flow", CASHFLOW_PATH, {"fs": "true"}, parse_cashflow))   # no time_period for cash flow
EMPTY = {"income": {"period": None, "revenue": None, "revenue_growth": None, "operating_profit": None,
                    "operating_profit_growth": None, "net_profit": None, "net_profit_growth": None},
         "balance_sheet": {"period": None, "total_assets": None, "total_liabilities": None, "total_equity": None,
                           "liabilities_to_equity": None, "total_debt": None, "debt_to_equity": None},
         "cash_flow": {"period": None, "operating": None, "investing": None, "financing": None,
                       "capex": None, "capex_line": None, "free_cash_flow": None}}


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
            if not is_stale(sec.get("fetched"), STATEMENT_MAX_AGE_DAYS):
                continue
            data, basis, err = fetch_section(api, m["isin"], path, extra, parser, fy_month(e))
            if err:
                sec["error"] = err
                problems.append(name + ": " + err)
            else:
                sec.update({"data": data, "basis": basis, "fetched": today(), "error": None})
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
            row[name] = dict(sec.get("data") or EMPTY[name], basis=sec.get("basis") if sec.get("data") else None)
            if sec.get("data"):
                bases.add(sec["basis"])
                dates.append(sec["fetched"])
        if not bases:
            continue                          # never publish a stock with no real statement data
        row["basis"] = bases.pop() if len(bases) == 1 else "mixed"
        rows.append(row)

    write_json(CACHE_FILE, cache)
    if not rows:
        fail("No financial statements could be fetched. The previous out/financials.json (if any) was kept.")
    write_json(OUT_FILE, {"as_of": max(dates), "source": "Upstox", "notes": NOTES, "stocks": rows, "errors": errors})
    log("Wrote", OUT_FILE, "-", len(rows), "stocks,", len(errors), "with problems, API calls:", api.calls)


if __name__ == "__main__":
    main()
