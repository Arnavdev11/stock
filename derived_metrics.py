"""
derived_metrics.py - a sector-aware, separately testable layer that fills metrics the provider (Upstox) did not return.

It never edits provider data. Its output is its own document (out/derived_metrics.json); every metric carries:
    source     "provider" | "calculated" | "unavailable"
    value      number or null
    formula    formula id + version (calculated only)
    input_period   what the value is based on (calculated / provider-annual)
    calculated_at  timestamp (calculated only)
    reason     why it is unavailable (unavailable only)

Rules: a valid provider value always wins. A metric is calculated only when every input is present, a real number, from the
same basis (consolidated / standalone) and from compatible periods. Missing is never zero. Negative values are kept; ratios whose
denominator makes them meaningless (zero or negative equity, zero/negative growth base) are reported unavailable with a reason.

Which metrics can be calculated from the data StockLens stores today (see FEASIBILITY) is decided by the adapter inputs, not by hope:
  pe                  price (market data close) / annual EPS                       -> calculable
  revenue_growth      provider annual growth, else same-quarter year-on-year       -> calculable
  profit_growth       provider annual growth, else same-quarter year-on-year       -> calculable
  roe, roa            need a prior-year balance sheet (only the latest is stored)  -> formula tested, inputs absent
  pb                  needs book value per share (not stored)                      -> formula tested, inputs absent
  roce, ev_ebitda     need EBIT / EBITDA, finance cost, debt, cash (not stored)    -> formula tested, inputs absent
  debt_to_equity      needs total borrowings (parser does not find it)             -> formula tested, inputs absent
Financial companies (banks, NBFCs, insurers...) never get ROCE, EV/EBITDA or debt/equity from here; the flag that says so is an
internal input and the sector name is never written to the output.
"""
import datetime as dt
import calendar
import json
import math
import sys
from pathlib import Path

VERSION = 1
METRICS = ("pe", "pb", "roe", "roa", "roce", "ev_ebitda", "revenue_growth", "profit_growth", "debt_to_equity")
PROVIDER_METRICS = ("pe", "pb", "roe", "roa", "roce", "ev_ebitda", "revenue_growth", "profit_growth", "debt_to_equity")
FINANCIAL_EXCLUDED = ("roce", "ev_ebitda", "debt_to_equity")
MAX_PRICE_AGE_DAYS = 10
FORMULAS = {
    "pe": "pe_price_over_annual_eps@1",
    "pb": "pb_price_over_book_value_per_share@1",
    "roe": "roe_net_profit_over_average_equity@1",
    "roa": "roa_net_profit_over_average_assets@1",
    "roce": "roce_ebit_over_average_capital_employed@1 (capital employed = total assets - current liabilities)",
    "ev_ebitda": "ev_ebitda_enterprise_value_over_positive_ebitda@1",
    "revenue_growth": "growth_same_quarter_yoy_pct@1",
    "profit_growth": "growth_same_quarter_yoy_pct@1",
    "debt_to_equity": "debt_to_equity_borrowings_over_positive_equity@1",
}
FEASIBILITY = {
    "pe": "calculable: price + annual EPS",
    "revenue_growth": "calculable: quarterly shard (same quarter a year earlier, same basis)",
    "profit_growth": "calculable: quarterly shard (same quarter a year earlier, same basis)",
    "roe": "inputs absent: needs prior-year shareholders' equity (balance sheet is stored for the latest period only)",
    "roa": "inputs absent: needs prior-year total assets (balance sheet is stored for the latest period only)",
    "pb": "inputs absent: needs book value per share (no share count or BVPS stored)",
    "roce": "inputs absent: needs EBIT (finance cost) and prior-year capital employed",
    "ev_ebitda": "inputs absent: needs EBITDA, market capitalisation, debt and cash",
    "debt_to_equity": "inputs absent: financials parser finds no borrowings line",
}


# ---------------------------------------------------------------- helpers
def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _day(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _month_end(y, m):
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def parse_period(label):
    """'Mar 2026' -> month-end date; an ISO date -> itself; else None."""
    d = _day(label) if isinstance(label, str) and label[:1].isdigit() else None
    if d:
        return d
    try:
        mon, year = str(label).strip().split()
        m = list(calendar.month_abbr).index(mon[:3].title())
        return _month_end(int(year), m) if m else None
    except (ValueError, TypeError):
        return None


def add_months(d, n):
    m = d.month - 1 + n
    return _month_end(d.year + m // 12, m % 12 + 1)


def unavailable(reason):
    return {"source": "unavailable", "value": None, "reason": reason}


def calculated(metric, value, period, now):
    return {"source": "calculated", "value": round(value, 2), "formula": FORMULAS[metric], "input_period": period,
            "calculated_at": now}


def provider(value, period=None):
    rec = {"source": "provider", "value": value}
    if period:
        rec["input_period"] = period
    return rec


def _same_basis(*bases):
    bs = {b for b in bases}
    if None in bs:
        return False
    return len(bs) == 1


# ---------------------------------------------------------------- pure formulas: (value | None, reason | None)
def pe_from(price, eps, price_date=None, eps_period_end=None, today=None):
    if not isnum(price) or price <= 0:
        return None, "price_missing_or_invalid"
    if not isnum(eps):
        return None, "eps_missing"
    if eps == 0:
        return None, "eps_zero"
    if eps < 0:
        return None, "eps_negative_pe_not_meaningful"
    pd_, ed = _day(price_date), eps_period_end if isinstance(eps_period_end, dt.date) else _day(eps_period_end)
    if pd_ is None or ed is None:
        return None, "price_or_eps_date_missing"
    if pd_ < ed:
        return None, "incompatible_periods_price_before_eps_period_end"
    if today is not None and (today - pd_).days > MAX_PRICE_AGE_DAYS:
        return None, "price_stale"
    return price / eps, None


def pb_from(price, bvps):
    if not isnum(price) or price <= 0:
        return None, "price_missing_or_invalid"
    if not isnum(bvps):
        return None, "book_value_per_share_missing"
    if bvps <= 0:
        return None, "book_value_non_positive_pb_not_meaningful"
    return price / bvps, None


def _average(cur, prev):
    return (cur + prev) / 2.0


def _return_on(net_profit, opening, closing, label, require_positive=True):
    if not isnum(net_profit):
        return None, "net_profit_missing"
    if not isnum(opening) or not isnum(closing):
        return None, "prior_year_%s_missing" % label
    avg = _average(opening, closing)
    if avg == 0:
        return None, "average_%s_zero" % label
    if require_positive and avg < 0:
        return None, "average_%s_negative_not_meaningful" % label
    return net_profit / avg * 100.0, None


def roe_from(net_profit, equity_open, equity_close):
    return _return_on(net_profit, equity_open, equity_close, "equity")


def roa_from(net_profit, assets_open, assets_close):
    return _return_on(net_profit, assets_open, assets_close, "assets")


def roce_from(ebit, cap_open, cap_close):
    if not isnum(ebit):
        return None, "ebit_missing"
    return _return_on(ebit, cap_open, cap_close, "capital_employed")


def ev_ebitda_from(enterprise_value, ebitda):
    if not isnum(enterprise_value) or enterprise_value <= 0:
        return None, "enterprise_value_missing_or_invalid"
    if not isnum(ebitda):
        return None, "ebitda_missing"
    if ebitda <= 0:
        return None, "ebitda_non_positive_not_meaningful"
    return enterprise_value / ebitda, None


def debt_to_equity_from(borrowings, equity):
    if not isnum(borrowings) or borrowings < 0:
        return None, "total_borrowings_missing"
    if not isnum(equity):
        return None, "equity_missing"
    if equity <= 0:
        return None, "equity_non_positive_not_meaningful"
    return borrowings / equity, None


def growth_from(current, previous):
    """(current / previous - 1) * 100. A zero or negative base is not a meaningful growth rate."""
    if not isnum(current):
        return None, "current_value_missing"
    if not isnum(previous):
        return None, "previous_value_missing"
    if previous == 0:
        return None, "previous_period_zero"
    if previous < 0:
        return None, "previous_period_negative"
    return (current / previous - 1.0) * 100.0, None


# ---------------------------------------------------------------- adapter: stock inputs -> metric records
def _valid_provider(v):
    return isnum(v)


def _quarter_growth(qdoc, fields):
    """Latest quarter against the same quarter one year earlier (same basis). -> (value, period_label, reason)."""
    if not isinstance(qdoc, dict) or not isinstance(qdoc.get("quarters"), list) or not qdoc["quarters"]:
        return None, None, "no_quarterly_data"
    by_end = {}
    for q in qdoc["quarters"]:
        d = _day((q or {}).get("quarter_end")) if isinstance(q, dict) else None
        if d and isinstance(q.get("values"), dict):
            by_end[d] = q["values"]
    if not by_end:
        return None, None, "no_quarterly_data"
    if not qdoc.get("basis"):
        return None, None, "reporting_basis_unknown"
    latest = max(by_end)
    prior = add_months(latest, -12)
    if prior not in by_end:
        return None, None, "same_quarter_prior_year_missing"
    last_reason = "revenue_missing"
    for f in fields:                       # one field for both periods; never revenue now and total revenue before
        cur, prev = by_end[latest].get(f), by_end[prior].get(f)
        if cur is None and prev is None:
            continue
        v, reason = growth_from(cur, prev)
        if reason:
            return None, None, reason
        return v, "%s vs %s (%s)" % (latest.isoformat(), prior.isoformat(), qdoc["basis"]), None
    return None, None, last_reason if fields[0] == "revenue" else "profit_after_tax_missing"


def derive_stock(inp, now):
    """inp (all optional):
         provider   {metric: value}            what Upstox returned (fundamentals row)
         basis_ok   ignored
         financial  bool                       bank / NBFC / insurer etc. (internal; never published)
         price      {"close": x, "date": "YYYY-MM-DD"}
         annual     {"period": "Mar 2026", "basis": ..., "eps_basic", "eps_diluted", "net_profit",
                     "total_revenue_growth", "net_profit_growth", "growth_verified": bool}
         balance    {"period", "basis", "total_equity", "total_assets", "total_debt", "current_liabilities",
                     "prior": {"period", "basis", "total_equity", "total_assets", "current_liabilities"}}
         bvps, ebit, ebitda, enterprise_value   optional extra inputs (none exist in the stored data today)
         quarterly  a published quarterly_results stock document
       now: ISO timestamp. Returns {metric: record}. Never raises."""
    out = {}
    try:
        _derive(inp if isinstance(inp, dict) else {}, now, out)
    except Exception as e:                  # one bad stock must never stop the run
        for m in METRICS:
            if m not in out:
                out[m] = unavailable("invalid_source_data (%s)" % type(e).__name__)
    return out


def _derive(inp, now, out):
    prov = inp.get("provider") if isinstance(inp.get("provider"), dict) else {}
    financial = bool(inp.get("financial"))
    ann = inp.get("annual") if isinstance(inp.get("annual"), dict) else {}
    bal = inp.get("balance") if isinstance(inp.get("balance"), dict) else {}
    prior = bal.get("prior") if isinstance(bal.get("prior"), dict) else {}
    today = _day(now)

    def done(metric, value, reason, period):
        if value is None:
            out[metric] = unavailable(reason)
        else:
            out[metric] = calculated(metric, value, period, now)

    for m in METRICS:
        if _valid_provider(prov.get(m)):
            out[m] = provider(prov[m])                      # a valid provider value is final
    todo = lambda m: m not in out

    if todo("pe"):
        pr = inp.get("price") if isinstance(inp.get("price"), dict) else {}
        eps = ann.get("eps_diluted") if isnum(ann.get("eps_diluted")) else ann.get("eps_basic")
        end = parse_period(ann.get("period"))
        if end is None:
            done("pe", None, "annual_period_missing", None)
        else:
            v, r = pe_from(pr.get("close"), eps, pr.get("date"), end, today)
            done("pe", v, r, "price %s / EPS FY %s (%s)" % (pr.get("date"), ann.get("period"), ann.get("basis")))
    if todo("pb"):
        v, r = pb_from((inp.get("price") or {}).get("close") if isinstance(inp.get("price"), dict) else None, inp.get("bvps"))
        done("pb", v, r, "book value per share")

    units = {d.get("unit") for d in (ann, bal, prior) if d and d.get("unit") is not None}
    pair_ok = (prior and bal and len(units) <= 1 and _same_basis(bal.get("basis"), prior.get("basis"), ann.get("basis"))
               and parse_period(prior.get("period")) is not None and parse_period(bal.get("period")) is not None
               and add_months(parse_period(bal["period"]), -12) == parse_period(prior["period"])
               and parse_period(ann.get("period")) == parse_period(bal.get("period")))

    def pair_problem():
        if not prior:
            return "prior_year_balance_sheet_not_stored"
        if len(units) > 1:
            return "unit_mismatch"
        if not _same_basis(bal.get("basis"), prior.get("basis"), ann.get("basis")):
            return "incompatible_basis_standalone_vs_consolidated"
        return "incompatible_periods"

    for metric, label, fn in (("roe", "total_equity", roe_from), ("roa", "total_assets", roa_from)):
        if todo(metric):
            if not pair_ok:
                done(metric, None, pair_problem(), None)
            else:
                v, r = fn(ann.get("net_profit"), prior.get(label), bal.get(label))
                done(metric, v, r, "FY %s average of %s and %s (%s)" % (bal.get("period"), prior.get("period"), bal.get("period"), bal.get("basis")))

    for metric in FINANCIAL_EXCLUDED:
        if todo(metric) and financial:
            out[metric] = unavailable("not_applicable_financial_company")

    if todo("roce"):
        cap = lambda d: (d.get("total_assets") - d.get("current_liabilities")) if isnum(d.get("total_assets")) and isnum(d.get("current_liabilities")) else None
        if not pair_ok:
            done("roce", None, pair_problem(), None)
        else:
            v, r = roce_from(inp.get("ebit"), cap(prior), cap(bal))
            done("roce", v, r, "FY %s" % bal.get("period"))
    if todo("ev_ebitda"):
        v, r = ev_ebitda_from(inp.get("enterprise_value"), inp.get("ebitda"))
        done("ev_ebitda", v, r, "enterprise value / EBITDA")
    if todo("debt_to_equity"):
        v, r = debt_to_equity_from(bal.get("total_debt"), bal.get("total_equity"))
        done("debt_to_equity", v, r, "FY %s (%s)" % (bal.get("period"), bal.get("basis")))

    for metric, provider_field, fields in (("revenue_growth", "total_revenue_growth", ("revenue", "total_revenue")),
                                           ("profit_growth", "net_profit_growth", ("profit_after_tax",))):
        if todo(metric):
            pv = ann.get(provider_field)
            if inp.get("growth_verified", ann.get("growth_verified")) and _valid_provider(pv):
                out[metric] = provider(pv, "FY %s (%s)" % (ann.get("period"), ann.get("basis")))
                continue
            v, period, r = _quarter_growth(inp.get("quarterly"), fields)
            done(metric, v, r, period)


# ---------------------------------------------------------------- the universe: never lets one stock stop the rest
def build(stocks, now):
    """stocks: iterable of {"symbol": s, **inputs}. -> list of {"symbol", "metrics"}; an unreadable stock still gets an entry."""
    rows = []
    for s in stocks:
        sym = s.get("symbol") if isinstance(s, dict) else None
        if not isinstance(sym, str) or not sym:
            continue
        rows.append({"symbol": sym, "metrics": derive_stock(s, now)})
    return rows


def coverage(rows, before_keys=None):
    """Per metric: provider / calculated / unavailable counts, and coverage before (provider only) and after (provider + calculated),
    plus the unavailable reasons. Percentages are of the stocks processed."""
    n = len(rows)
    cov = {}
    for m in METRICS:
        c = {"provider": 0, "calculated": 0, "unavailable": 0, "reasons": {}}
        for r in rows:
            rec = (r.get("metrics") or {}).get(m) or unavailable("not_evaluated")
            c[rec["source"]] += 1
            if rec["source"] == "unavailable":
                c["reasons"][rec["reason"]] = c["reasons"].get(rec["reason"], 0) + 1
        c["before_pct"] = round(100.0 * c["provider"] / n, 1) if n else None
        c["after_pct"] = round(100.0 * (c["provider"] + c["calculated"]) / n, 1) if n else None
        cov[m] = c
    return {"stocks": n, "metrics": cov}


def document(rows, now):
    return {"schema": 1, "kind": "derived_metrics", "version": VERSION, "as_of": now, "source": "calculated from stored Upstox statements and NSE closing prices",
            "notes": {"never": "provider values are never replaced; unavailable means the inputs do not exist or the ratio is not meaningful; missing is never zero",
                      "feasibility": FEASIBILITY},
            "stocks": rows, "coverage": coverage(rows)}


def inputs_from(fund_row, fin_row, qdoc, price, financial=False):
    """Map the real published shapes (fundamentals row, financials row, quarterly_results stock document, market close) to derive_stock inputs.
    The latest balance sheet is all that is stored, so no prior-year balance is supplied: roe/roa/roce stay unavailable with that reason.
    Provider annual growth is passed on only when its summary value was verified against the detail line (the financials 'checks')."""
    fund_row = fund_row if isinstance(fund_row, dict) else {}
    fin_row = fin_row if isinstance(fin_row, dict) else {}
    inc = fin_row.get("income") if isinstance(fin_row.get("income"), dict) else {}
    bs = fin_row.get("balance_sheet") if isinstance(fin_row.get("balance_sheet"), dict) else {}
    chk = inc.get("checks") if isinstance(inc.get("checks"), dict) else {}
    annual = {"period": inc.get("period"), "basis": inc.get("basis"), "eps_basic": inc.get("eps_basic"), "eps_diluted": inc.get("eps_diluted"),
              "net_profit": inc.get("net_profit"), "unit": "INR crore",
              "total_revenue_growth": inc.get("total_revenue_growth") if chk.get("summary_revenue_equals_total_revenue") is True else None,
              "net_profit_growth": inc.get("net_profit_growth") if chk.get("summary_net_profit_equals_pat") is True else None}
    balance = {"period": bs.get("period"), "basis": bs.get("basis"), "total_equity": bs.get("total_equity"), "total_assets": bs.get("total_assets"),
               "total_debt": bs.get("total_debt"), "unit": "INR crore"}
    return {"provider": {m: fund_row.get(m) for m in PROVIDER_METRICS if m in fund_row}, "financial": financial, "price": price,
            "annual": annual, "balance": balance, "growth_verified": True, "quarterly": qdoc}


def main(argv=None):
    sys.stderr.write("derived_metrics: library module; the adapter that reads out/ is deliberately not wired into any workflow yet.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
