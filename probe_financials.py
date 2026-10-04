"""
probe_financials.py - ONE-OFF, READ-ONLY diagnostic for StockLens Phase 4 Step 4 (5-Year Financial Trend).
Not part of the production pipeline: it writes nothing to out/ or data/, changes no existing file and publishes nothing.

Question it answers: for each of the 10 development stocks, how many fiscal years does Upstox really return for the
income statement, balance sheet and cash flow, and which of the lines a 5-year trend needs exist for each year?

It makes the same three calls the production updater makes (income-statement yearly, balance-sheet, cash-flow, all with fs=true)
for consolidated AND standalone: 10 stocks x 2 bases x 3 statements = 60 calls, paced at 2 s.

It REPORTS ONLY: statement, basis, period labels, number of periods, which lines exist per period, gaps between years,
summary-versus-detail period differences. It never prints, saves or logs a financial value, a raw response or the token.
Period labels must parse as "Mon YYYY"; anything else is reported as "<unparsable>". Line labels are shortened and cleaned.
"""
import json
import re
import sys
from pathlib import Path

from financials_updater import norm
from upstox_common import SYMBOLS, Upstox, fail, get_token, load_instruments, log, num, period_key

OUT_DIR = Path("probe_output")
OUT_FILE = OUT_DIR / "financials_probe_report.json"
BASES = ("consolidated", "standalone")
MAX_CALLS = 80

# normalised line name -> name used in the report (lines a 5-year trend needs, plus a few that show how the statement is laid out)
INCOME_LINES = {"revenue": "Revenue", "total revenue": "Total Revenue", "profit before tax": "Profit Before Tax",
                "profit after tax": "Profit After Tax", "eps basic": "EPS - Basic", "eps diluted": "EPS - Diluted"}
INCOME_CORE = ("Revenue", "Total Revenue", "Profit Before Tax", "Profit After Tax")
BALANCE_LINES = {"total assets": "Total Assets", "equity capital": "Equity Capital", "total equity & liabilities": "Total Equity & Liabilities"}
CASH_LINES = {"cash flow from operations": "Cash flow from Operations", "cash flow from investing": "Cash flow from Investing",
              "cash flow from financing": "Cash flow from Financing"}
SUMMARY_INCOME = ("revenue", "operating_profit", "net_profit")
SUMMARY_CASH = ("operating", "investing", "financing")
TIME_PERIODS = {"yearly", "quarterly"}


# ---------------------------------------------------------------- safe helpers (labels only, never values)
def plabel(period):
    """A period label such as 'Mar 2026' if it parses, else None."""
    k = period_key(period)
    return "%s %d" % (str(period).strip().split()[0][:3].title(), k[0]) if k else None


def clean(text, n=60):
    return re.sub(r"[^A-Za-z0-9 &/()'.,%+-]", "?", str(text))[:n]


def entries(history):
    return [h for h in (history or []) if isinstance(h, dict)]


def periods_with_value(history):
    """Parsable period labels whose value is a usable number (presence only; the number is never kept)."""
    return {plabel(h.get("period")) for h in entries(history) if plabel(h.get("period")) and num(h.get("value")) is not None}


def periods_with_change(history):
    return {plabel(h.get("period")) for h in entries(history) if plabel(h.get("period")) and num(h.get("change")) is not None}


def all_periods(history):
    return {plabel(h.get("period")) for h in entries(history) if plabel(h.get("period"))}


def unparsable(history):
    return sum(1 for h in entries(history) if not plabel(h.get("period")))


def newest_first(labels):
    return sorted((x for x in labels if x), key=lambda p: period_key(p), reverse=True)


def gaps(labels):
    """Missing fiscal years between the oldest and newest period, or a note when year-end months differ."""
    keys = [period_key(x) for x in labels if x]
    if not keys:
        return {"months": [], "missing_years": [], "note": "no periods"}
    months = sorted({m for _, m in keys})
    if len(months) > 1:
        return {"months": months, "missing_years": None, "note": "periods have different month-ends (mixed or quarterly history)"}
    years = {y for y, _ in keys}
    return {"months": months, "missing_years": [y for y in range(min(years), max(years) + 1) if y not in years], "note": ""}


def duplicate_count(history):
    seen, dup = set(), 0
    for h in entries(history):
        p = plabel(h.get("period"))
        if p in seen:
            dup += 1
        seen.add(p)
    return dup


def detail_lines(full, wanted):
    """{report name: history} for the wanted lines. Reports repeated labels (the production parser drops them) and all labels seen."""
    found, repeated, counts, labels = {}, [], {}, []
    for x in full or []:
        if not isinstance(x, dict) or not x.get("particular"):
            continue
        labels.append(clean(x["particular"]))
        k = norm(x["particular"])
        counts[k] = counts.get(k, 0) + 1
        if k in wanted:
            found[wanted[k]] = entries(x.get("history"))
    repeated = sorted(wanted[k] for k, c in counts.items() if c > 1 and k in wanted)
    for name in repeated:
        found.pop(name, None)
    return found, repeated, labels


def per_line(found, wanted_names):
    """{line: {'present': bool, 'periods': [...], 'unparsable_entries': n}} - labels only."""
    out = {}
    for name in wanted_names:
        h = found.get(name)
        out[name] = {"line_exists": h is not None, "periods_with_a_value": newest_first(periods_with_value(h)) if h is not None else [],
                     "unparsable_entries": unparsable(h) if h is not None else 0}
    return out


def tp(d):
    v = str(d.get("time_period", "")).lower()
    return v if v in TIME_PERIODS else ("not stated" if not v else "other")


# ---------------------------------------------------------------- one analysis per statement
def analyse_income(body):
    d = body.get("data") or {}
    summ = {str(c.get("category", "")).lower(): entries(c.get("history")) for c in (d.get("income_statement") or []) if isinstance(c, dict)}
    found, repeated, labels = detail_lines(d.get("full_statement"), INCOME_LINES)
    lines = per_line(found, INCOME_LINES.values())
    s_union = set().union(*(all_periods(summ.get(c)) for c in SUMMARY_INCOME)) if summ else set()
    d_union = set().union(*(set(v["periods_with_a_value"]) for v in lines.values())) if lines else set()
    core = None
    for n in INCOME_CORE:
        p = set(lines[n]["periods_with_a_value"])
        core = p if core is None else core & p
    eps = set(lines["EPS - Basic"]["periods_with_a_value"]) | set(lines["EPS - Diluted"]["periods_with_a_value"])
    return {"time_period_field": tp(d), "units_field": clean(d.get("units_in", "")),
            "summary": {c: {"periods": newest_first(all_periods(summ.get(c))), "periods_with_growth_change": newest_first(periods_with_change(summ.get(c))),
                            "duplicate_periods": duplicate_count(summ.get(c)), "unparsable_entries": unparsable(summ.get(c))} for c in SUMMARY_INCOME},
            "detail_lines": lines, "repeated_labels_in_detail": repeated, "detail_labels_seen": labels,
            "summary_periods": newest_first(s_union), "detail_periods": newest_first(d_union),
            "only_in_summary": newest_first(s_union - d_union), "only_in_detail": newest_first(d_union - s_union),
            "years_with_all_four_core_lines": newest_first(core or set()), "years_with_eps": newest_first(eps),
            "gaps_in_core_years": gaps(core or set())}


def analyse_balance(body):
    d = body.get("data") or {}
    hist = entries(d.get("history"))
    found, repeated, labels = detail_lines(d.get("full_statement"), BALANCE_LINES)
    lines = per_line(found, BALANCE_LINES.values())
    both = {plabel(h.get("period")) for h in hist if plabel(h.get("period")) and num(h.get("total_asset")) is not None and num(h.get("total_liability")) is not None}
    d_union = set().union(*(set(v["periods_with_a_value"]) for v in lines.values())) if lines else set()
    return {"time_period_field": tp(d), "summary_periods": newest_first(all_periods(hist)), "summary_periods_with_assets_and_liabilities": newest_first(both),
            "duplicate_periods": duplicate_count(hist), "unparsable_entries": unparsable(hist), "detail_lines": lines, "repeated_labels_in_detail": repeated,
            "detail_labels_seen": labels, "detail_periods": newest_first(d_union),
            "only_in_summary": newest_first(all_periods(hist) - d_union), "only_in_detail": newest_first(d_union - all_periods(hist)),
            "gaps": gaps(both)}


def analyse_cash(body):
    d = body.get("data") or {}
    cats = {str(c.get("category", "")).lower(): entries(c.get("history")) for c in (d.get("cash_flow") or []) if isinstance(c, dict)}
    found, repeated, labels = detail_lines(d.get("full_statement"), CASH_LINES)
    lines = per_line(found, CASH_LINES.values())
    s_union = set().union(*(all_periods(cats.get(c)) for c in SUMMARY_CASH)) if cats else set()
    d_union = set().union(*(set(v["periods_with_a_value"]) for v in lines.values())) if lines else set()
    op = periods_with_value(cats.get("operating"))
    return {"time_period_field": tp(d), "time_period_sent": "none (as in production)",
            "summary": {c: {"periods": newest_first(all_periods(cats.get(c))), "duplicate_periods": duplicate_count(cats.get(c)),
                            "unparsable_entries": unparsable(cats.get(c))} for c in SUMMARY_CASH},
            "detail_lines": lines, "repeated_labels_in_detail": repeated, "detail_labels_seen": labels,
            "summary_periods": newest_first(s_union), "detail_periods": newest_first(d_union),
            "only_in_summary": newest_first(s_union - d_union), "only_in_detail": newest_first(d_union - s_union),
            "operating_cash_flow_years": newest_first(op), "gaps_in_operating_years": gaps(op),
            "period_month_ends": gaps(s_union)["months"]}


STATEMENTS = (("income", "income-statement", lambda b: {"type": b, "time_period": "yearly", "fs": "true"}, analyse_income),
              ("balance_sheet", "balance-sheet", lambda b: {"type": b, "fs": "true"}, analyse_balance),
              ("cash_flow", "cash-flow", lambda b: {"type": b, "fs": "true"}, analyse_cash))


def probe_one(api, isin):
    out = {}
    for basis in BASES:
        out[basis] = {}
        for name, path, params, analyse in STATEMENTS:
            body, err = api.get(isin + "/" + path, params(basis))
            if err:
                out[basis][name] = {"status": "no data", "reason": clean(err, 40)}
                continue
            try:
                out[basis][name] = dict({"status": "ok"}, **analyse(body))
            except (AttributeError, TypeError, KeyError, ValueError):
                out[basis][name] = {"status": "unexpected response shape"}
    return out


# ---------------------------------------------------------------- readable summary (labels and counts only)
def summary_lines(sym, res):
    rows = []
    for basis in BASES:
        r = res.get(basis, {})
        inc, bal, cf = r.get("income", {}), r.get("balance_sheet", {}), r.get("cash_flow", {})
        if inc.get("status") != "ok":
            rows.append("%-10s %-12s income: %s" % (sym, basis, inc.get("status", "-") + (" (" + inc["reason"] + ")" if inc.get("reason") else "")))
        else:
            core, eps = inc["years_with_all_four_core_lines"], inc["years_with_eps"]
            rows.append("%-10s %-12s income  | summary %d (%s..%s) | detail %d (%s..%s) | core years %d | EPS years %d | only-summary %s | only-detail %s | gaps %s"
                        % (sym, basis, len(inc["summary_periods"]), (inc["summary_periods"] or ["-"])[0], (inc["summary_periods"] or ["-"])[-1],
                           len(inc["detail_periods"]), (inc["detail_periods"] or ["-"])[0], (inc["detail_periods"] or ["-"])[-1],
                           len(core), len(eps), inc["only_in_summary"] or "none", inc["only_in_detail"] or "none", inc["gaps_in_core_years"]["missing_years"] if inc["gaps_in_core_years"]["missing_years"] is not None else inc["gaps_in_core_years"]["note"]))
            missing = [n for n, v in inc["detail_lines"].items() if not v["line_exists"]]
            if missing or inc["repeated_labels_in_detail"]:
                rows.append("%-10s %-12s income  | lines missing: %s | repeated labels: %s" % (sym, basis, missing or "none", inc["repeated_labels_in_detail"] or "none"))
        if bal.get("status") != "ok":
            rows.append("%-10s %-12s balance: %s" % (sym, basis, bal.get("status", "-")))
        else:
            rows.append("%-10s %-12s balance | summary %d (%s) | with assets+liabilities %d | gaps %s" % (
                sym, basis, len(bal["summary_periods"]), "..".join([(bal["summary_periods"] or ["-"])[0], (bal["summary_periods"] or ["-"])[-1]]),
                len(bal["summary_periods_with_assets_and_liabilities"]), bal["gaps"]["missing_years"] if bal["gaps"]["missing_years"] is not None else bal["gaps"]["note"]))
        if cf.get("status") != "ok":
            rows.append("%-10s %-12s cash   : %s" % (sym, basis, cf.get("status", "-")))
        else:
            rows.append("%-10s %-12s cash   | time_period field %s | summary %d (%s) | operating-cash-flow years %d | month-ends %s | gaps %s | only-summary %s | only-detail %s" % (
                sym, basis, cf["time_period_field"], len(cf["summary_periods"]), "..".join([(cf["summary_periods"] or ["-"])[0], (cf["summary_periods"] or ["-"])[-1]]),
                len(cf["operating_cash_flow_years"]), cf["period_month_ends"], cf["gaps_in_operating_years"]["missing_years"] if cf["gaps_in_operating_years"]["missing_years"] is not None else cf["gaps_in_operating_years"]["note"],
                cf["only_in_summary"] or "none", cf["only_in_detail"] or "none"))
    return rows


def main():
    token = get_token()
    instruments = load_instruments()
    api = Upstox(token, MAX_CALLS)
    report, lines = {}, []
    for sym in SYMBOLS:
        meta = instruments.get(sym)
        if not meta:
            report[sym] = {"status": "symbol not found in Upstox NSE instruments"}
            lines.append("%-10s symbol not found" % sym)
            continue
        report[sym] = probe_one(api, meta["isin"])
        rows = summary_lines(sym, report[sym])
        lines += rows
        for r in rows:
            log(r)
    text = json.dumps({"purpose": "Phase 4 Step 4 availability probe. Period labels, counts and line presence only; no financial values.",
                       "stocks": report}, indent=1, allow_nan=False)
    if token in text:                         # cannot happen; belt and braces
        fail("probe report contained the token and was NOT written.")
    OUT_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(text, encoding="utf-8")
    log("Wrote", OUT_FILE, "- stocks:", len(report), "- API calls:", api.calls)


if __name__ == "__main__":
    sys.exit(main())
