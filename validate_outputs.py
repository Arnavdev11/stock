"""
validate_outputs.py - checks the JSON files in out/ before they are published.
  * Every file must be strict JSON (no NaN / Infinity).
  * financials.json and fundamentals.json also get structure and number checks.
Only a failing financials.json is set aside (renamed to financials.json.invalid, so it is not
published and the site shows "not loaded yet"). All other files are only reported, never touched.
Exit code 1 if fundamentals.json or financials.json has a problem, else 0.
"""
import json
import os
import sys
from pathlib import Path

OUT = Path(os.environ.get("DATA_DIR", ".")) / "out"
FIN_GROUPS = {   # schema 2: every field must be a number or null
    "income": ["revenue", "other_income", "total_revenue", "profit_before_tax", "tax", "profit_after_tax", "eps_basic",
               "eps_diluted", "total_revenue_growth", "profit_before_tax_growth", "profit_after_tax_growth",
               "net_profit", "net_profit_growth"],
    "balance_sheet": ["total_assets", "total_liabilities", "total_equity", "liabilities_to_equity", "non_current_assets",
                      "current_assets", "current_liabilities", "non_current_liabilities", "equity_capital",
                      "total_equity_and_liabilities", "total_debt", "debt_to_equity"],
    "cash_flow": ["operating", "investing", "financing", "capex", "free_cash_flow"],
}
GROWTH_NEEDS_CHECK = {  # growth may exist only when its summary value was verified against the detail line
    "total_revenue_growth": "summary_revenue_equals_total_revenue",
    "profit_before_tax_growth": "summary_operating_profit_equals_pbt",
    "profit_after_tax_growth": "summary_net_profit_equals_pat",
}
VAL_FIELDS = ["pe", "pb", "roa", "roe", "roce", "ev_ebitda", "revenue_growth", "profit_growth"]


def _bad(c):
    raise ValueError("invalid number " + c)


def strict_load(path):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_bad)


def is_num_or_null(v):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def check_financials(doc):
    p = []
    if not isinstance(doc, dict) or not isinstance(doc.get("stocks"), list) or not doc["stocks"]:
        return ["financials.json has no stocks list"]
    if doc.get("schema") != 2:
        return []          # legacy (Phase 1) file: not checked against the new rules, never set aside for that
    for k in ("as_of", "source"):
        if not doc.get(k):
            p.append("missing " + k)
    seen = set()
    for s in doc["stocks"]:
        sym = s.get("symbol") if isinstance(s, dict) else None
        if not sym or sym in seen:
            p.append("missing or duplicate symbol: %r" % sym)
            continue
        seen.add(sym)
        if s.get("basis") not in ("consolidated", "standalone", "mixed"):
            p.append(sym + ": bad basis")
        for g, fields in FIN_GROUPS.items():
            grp = s.get(g)
            if not isinstance(grp, dict):
                p.append(sym + ": missing " + g)
                continue
            if grp.get("basis") not in (None, "consolidated", "standalone"):
                p.append(sym + "." + g + ": bad basis")
            for f in fields:
                if f not in grp or not is_num_or_null(grp[f]):
                    p.append("%s.%s.%s is not a number or null" % (sym, g, f))
        b, c, inc = s.get("balance_sheet") or {}, s.get("cash_flow") or {}, s.get("income") or {}
        if b.get("total_debt") is not None or b.get("debt_to_equity") is not None:
            p.append(sym + ": total_debt / debt_to_equity must stay empty (never derived)")
        for g, need in GROWTH_NEEDS_CHECK.items():
            if inc.get(g) is not None and (inc.get("checks") or {}).get(need) is not True:
                p.append("%s: %s present without a verified %s" % (sym, g, need))
        for grp, name in ((inc, "income"), (b, "balance_sheet"), (c, "cash_flow")):
            if not isinstance(grp.get("checks", {}), dict) or any(
                    v not in (True, False, None) for v in (grp.get("checks") or {}).values()):
                p.append(sym + "." + name + ".checks must hold true/false/null only")
        for grp, key in ((b, "line_items"), (b, "debt_lines_found"), (c, "line_items")):
            if not isinstance(grp.get(key), list) or not all(isinstance(x, str) for x in grp.get(key)):
                p.append(sym + ": " + key + " must be a list of text")
        ta, tl, eq = b.get("total_assets"), b.get("total_liabilities"), b.get("total_equity")
        if None not in (ta, tl, eq) and abs((ta - tl) - eq) > 0.02:
            p.append(sym + ": total_equity is not assets minus liabilities")
        if eq is None and None not in (ta, tl):
            p.append(sym + ": total_equity missing though assets and liabilities exist")
        r = b.get("liabilities_to_equity")
        if r is not None and (not eq or eq <= 0 or abs(tl / eq - r) > 0.02):
            p.append(sym + ": liabilities_to_equity is inconsistent")
        f = c.get("free_cash_flow")
        if f is not None and (c.get("capex") is None or c.get("operating") is None
                              or abs(c["operating"] - c["capex"] - f) > 0.02):
            p.append(sym + ": free_cash_flow is not supported by a capex line")
    return p


def financial_warnings(doc):
    """Soft checks: reported but never set a file aside."""
    w = []
    for s in (doc.get("stocks") or []) if isinstance(doc, dict) else []:
        i = s.get("income") or {}
        r, o, t = i.get("revenue"), i.get("other_income"), i.get("total_revenue")
        if None not in (r, o, t) and abs(r + o - t) > 1.5:
            w.append("%s: revenue + other_income differs from total_revenue" % s.get("symbol"))
        for k, v in (i.get("checks") or {}).items():
            if v is False:
                w.append("%s: %s is false (Upstox summary and detail line disagree)" % (s.get("symbol"), k))
    return w


def check_fundamentals(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("stocks"), list):
        return ["fundamentals.json has no stocks list"]
    p = []
    for s in doc["stocks"]:
        for f in VAL_FIELDS:
            if f in s and not is_num_or_null(s[f]):
                p.append("%s.%s is not a number or null" % (s.get("symbol"), f))
    return p


def main():
    if not OUT.exists():
        print("::warning::no out/ folder to validate")
        return 0
    failed = False
    for path in sorted(OUT.glob("*.json")):
        try:
            doc = strict_load(path)
        except (ValueError, OSError) as e:
            level = "error" if path.name in ("financials.json", "fundamentals.json") else "warning"
            print("::%s::%s is not valid strict JSON (%s)" % (level, path.name, e))
            failed = failed or level == "error"
            if path.name == "financials.json":
                path.replace(path.with_suffix(".json.invalid"))
            continue
        problems = check_financials(doc) if path.name == "financials.json" else \
            check_fundamentals(doc) if path.name == "fundamentals.json" else []
        if problems:
            failed = True
            for x in problems[:20]:
                print("::error::%s: %s" % (path.name, x))
            if path.name == "financials.json":
                path.replace(path.with_suffix(".json.invalid"))
                print("::error::financials.json was set aside and will not be published")
        else:
            print("ok:", path.name)
        if path.name == "financials.json":
            for x in financial_warnings(doc)[:20]:
                print("::warning::" + x)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
