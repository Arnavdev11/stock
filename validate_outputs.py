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
FIN_GROUPS = {
    "income": ["revenue", "revenue_growth", "operating_profit", "operating_profit_growth", "net_profit", "net_profit_growth"],
    "balance_sheet": ["total_assets", "total_liabilities", "total_equity", "liabilities_to_equity", "total_debt", "debt_to_equity"],
    "cash_flow": ["operating", "investing", "financing", "capex", "free_cash_flow"],
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
        b, c = s.get("balance_sheet") or {}, s.get("cash_flow") or {}
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
