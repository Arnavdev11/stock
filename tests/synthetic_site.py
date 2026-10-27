"""
SYNTHETIC preview site for local browser checks of index.html. NOT market data.

    python tests/synthetic_site.py <empty-output-dir>

Writes <dir>/index.html (a copy of the repository page) and <dir>/out/*.json filled with INVENTED companies and numbers
(plus TCS, so the TCS stock page can be opened, with made-up figures). Every file carries "synthetic": true and a
"source" that says it is synthetic. It never writes inside this repository's out/ or data/ folders, never calls a
network service and never touches a provider. It exists only so the responsive layout, the screener filters, sorting
and the mobile cards can be looked at with populated tables; it proves nothing about the real data files.
"""
import datetime as dt
import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "SYNTHETIC FIXTURE (invented values, not market data)"
AS_OF = "2026-10-23"

# symbol, company name, sector, P/E, P/B, ROE, ROCE, ROA, EV/EBITDA, last close
# Edge cases on purpose: negative P/E (loss-making), missing values, a value stored as text, a zero P/E, no sector.
STOCKS = [
    ("TCS", "Tata Consultancy Services Limited", "Information Technology", 29.4, 14.2, 46.1, 58.3, 30.2, 20.1, 3410.0),
    ("ALPHA", "Alpha Synthetic Industries Limited", "Information Technology", 18.2, 3.1, 22.5, 25.0, 9.1, 12.0, 812.5),
    ("BETA", "Beta Fixture Power Limited", "Utilities", 11.4, 1.6, 14.2, 11.8, 4.3, 7.9, 245.1),
    ("GAMMA", "Gamma Test Banking Corporation", "Financials", 14.9, 2.2, 16.8, None, 1.4, None, 1520.0),
    ("DELTA", "Delta Example Motors Limited", "Automobiles", -8.5, 1.1, -12.3, -4.0, -3.2, None, 98.4),
    ("EPS1", "Epsilon Invented Chemicals Limited", "Materials", 42.7, 6.8, 15.1, 19.9, 8.8, 25.4, 1999.9),
    ("ZETA", "Zeta Placeholder Telecom Limited", "Communication Services", None, 0.9, None, 3.2, 0.7, 6.1, 15.2),
    ("ETA", "Eta Demo Consumer Products Limited", "Consumer Staples", 55.3, 11.9, 24.0, 31.1, 15.5, 33.0, 2650.0),
    ("THETA", "Theta Sample Pharma Limited", "Health Care", 31.0, 4.4, 13.7, 17.2, 9.9, 18.8, 1120.0),
    ("IOTA", "Iota Mock Steel Limited", "Materials", 6.2, 0.8, 9.5, 10.1, 4.0, 4.9, 132.0),
    ("KAPPA", "Kappa Trial Realty Limited", "Real Estate", 0.0, 2.0, 0.0, 1.0, 0.1, 40.0, 77.7),
    ("LAMBDA", "Lambda Specimen Energy Limited", None, 9.8, 1.3, 13.0, 12.5, 6.1, 5.5, 410.0),
    ("MU", "Mu Pretend Logistics Limited", "Industrials", "12.5", 2.5, 18.0, 20.0, 7.0, 9.0, 560.0),   # P/E stored as text: must not pass a numeric filter
    ("NU", "Nu Imaginary Retail Limited", "Consumer Discretionary", -120.4, 9.0, -3.1, 2.0, -1.0, None, 45.0),
    ("XI", "Xi Model Infrastructure Limited", "Industrials", 22.2, 3.3, 15.0, 16.0, 6.6, 13.3, 333.0),
    ("OMICRON", "Omicron Stand-in Insurance Limited", "Financials", 64.0, 7.7, 12.0, None, 2.0, None, 1801.0),
    ("PI", "Pi Hypothetical Cement Limited", "Materials", 25.0, 3.9, 15.0, 18.0, 8.0, 14.0, 9050.0),
    ("RHO", "Rho Fictional Media Limited", "Communication Services", 17.5, 2.1, 12.4, 14.0, 6.5, 10.5, 64.3),
    ("SIGMA", "Sigma Negative Equity Airlines Limited", "Industrials", 15.0, -2.4, 18.0, 5.0, 1.0, 9.0, 120.0),   # negative book value: negative P/B
]
for i in range(1, 101):  # a longer list (118 stocks in all), so the table, the mobile cards and "Show 50 more" are exercised with many rows
    STOCKS.append(("SYN%02d" % i, "Synthetic Filler Company %02d Limited" % i, ["Information Technology", "Financials", "Industrials", "Materials", None][i % 5],
                   round(5 + (i * 7.3) % 60, 2), round(0.5 + (i * 1.7) % 9, 2), round(4 + (i * 3.1) % 35, 2), round(5 + (i * 2.9) % 30, 2),
                   round(1 + (i * 1.3) % 12, 2), round(3 + (i * 2.2) % 25, 2), round(50 + i * 37.5, 2)))


def day(n):
    return (dt.date(2026, 10, 23) - dt.timedelta(days=n)).isoformat()


def candles(base, n=260, seed=1):
    out, d, k = [], 0, 0
    while len(out) < n:
        date = dt.date(2026, 10, 23) - dt.timedelta(days=d)
        d += 1
        if date.weekday() >= 5:
            continue
        k += 1
        c = round(base * (1 + 0.08 * math.sin((k + seed) / 9.0) - 0.0004 * k), 2)
        out.append({"date": date.isoformat(), "open": round(c * 0.995, 2), "high": round(c * 1.01, 2), "low": round(c * 0.99, 2), "close": c, "volume": 100000 + 1000 * ((k * seed) % 50)})
    return sorted(out, key=lambda x: x["date"])


def build(dest):
    files = {}
    files["fundamentals.json"] = {"synthetic": True, "as_of": AS_OF, "source": SOURCE, "stocks": [
        {"symbol": s, "isin": "INE%08dX1" % i, "company_name": n, "sector": sec, "pe": pe, "pb": pb, "roe": roe, "roce": roce, "roa": roa, "ev_ebitda": ev,
         "revenue_growth": None, "profit_growth": None, "missing": [k for k, v in (("pe", pe), ("pb", pb), ("roe", roe), ("roce", roce), ("roa", roa), ("ev_ebitda", ev)) if v is None], "fetched": AS_OF}
        for i, (s, n, sec, pe, pb, roe, roce, roa, ev, _) in enumerate(STOCKS)]}

    def inc(i):
        return {"period": "Mar 2026", "basis": "consolidated", "revenue": 1000.0 * i, "other_income": 10.0 * i, "total_revenue": 1010.0 * i, "profit_before_tax": 200.0 * i,
                "tax": 50.0 * i, "profit_after_tax": 150.0 * i, "eps_basic": 1.5 * i, "eps_diluted": 1.4 * i, "total_revenue_growth": 5.0 + i % 7, "profit_before_tax_growth": -2.5,
                "profit_after_tax_growth": 3.0, "net_profit": 150.0 * i, "net_profit_growth": 3.0, "checks": {}}
    files["financials.json"] = {"synthetic": True, "schema": 2, "as_of": AS_OF, "source": SOURCE, "notes": {}, "errors": [], "stocks": [
        {"symbol": s, "isin": "INE%08dX1" % i, "company_name": n, "basis": "consolidated", "income": inc(i + 1),
         "balance_sheet": {"period": "Mar 2026", "basis": "consolidated", "total_assets": 5000.0 * (i + 1), "total_liabilities": 3000.0 * (i + 1), "total_equity": 2000.0 * (i + 1),
                           "liabilities_to_equity": 1.5, "total_debt": None, "debt_to_equity": None, "debt_lines_found": [], "line_items": [], "checks": {}},
         "cash_flow": {"period": "Mar 2026", "basis": "consolidated", "operating": 400.0 * (i + 1), "investing": -300.0 * (i + 1), "financing": -50.0 * (i + 1), "capex": None,
                       "capex_line": None, "free_cash_flow": None, "capex_candidates": [], "capex_status": "none", "line_items": ["x"]}}
        for i, (s, n, *_rest) in enumerate(STOCKS)]}
    files["historical.json"] = {"synthetic": True, "as_of": AS_OF, "source": SOURCE, "benchmark": {"symbol": "NIFTY 50", "candles": candles(24500, seed=3)},
                                "stocks": {row[0]: {"symbol": row[0], "candles": candles(row[-1], seed=i + 2)} for i, row in enumerate(STOCKS[:18])}}
    files["shareholding.json"] = {"synthetic": True, "schema": 1, "source": SOURCE, "stocks": {
        "TCS": {"symbol": "TCS", "quarters": [{"quarter_end": "2026-06-30", "status": "available", "diagnostics": {"share_basis_total": 3618087518}}]}}}
    files["stock_directory.json"] = {"synthetic": True, "schema_version": 1, "kind": "stock_directory", "stage": "synthetic-fixture", "count": len(STOCKS),
                                     "symbols": [{"symbol": s, "isin": None, "name": n} for (s, n, *_r) in STOCKS]}
    files["scans.json"] = {"synthetic": True, "as_of": AS_OF, "source": SOURCE,
                           "gainers": [{"symbol": "ALPHA", "price": 812.5, "change_pct": 6.5, "volume_x_avg": 2.5}, {"symbol": "ETA", "price": 2650, "change_pct": 2.1, "volume_x_avg": 0.8}, {"symbol": "XI", "price": 333, "change_pct": 4.4, "volume_x_avg": 3.1}],
                           "losers": [{"symbol": "DELTA", "price": 98.4, "change_pct": -7.2, "volume_x_avg": 3.4}, {"symbol": "NU", "price": 45, "change_pct": -1.1, "volume_x_avg": 2.4}],
                           "volume_gainers": [{"symbol": "ALPHA", "price": 812.5, "change_pct": 6.5, "volume": 3000000, "volume_x_avg": 4}],
                           "high_52w": [{"symbol": "TCS", "price": 3410, "old_52w_high": 3390, "change_pct": 1.5}],
                           "dma": [{"symbol": "BETA", "price": 245, "dma50": 240, "dma200": 238, "status": "Golden cross today"}],
                           "breadth": {"advancing": 1200, "declining": 800, "unchanged": 100, "new_52w_highs": 33, "above_50dma_pct": 55.5, "above_200dma_pct": 61.2}}
    files["fiidii_history.json"] = [{"category": "DII", "date": "23-Oct-2026", "buyValue": "1000", "sellValue": "400", "netValue": "600"},
                                    {"category": "FII/FPI", "date": "23-Oct-2026", "buyValue": "500", "sellValue": "900", "netValue": "-400"}]

    dest = Path(dest).resolve()
    for bad in (ROOT / "out", ROOT / "data"):
        if dest == bad or bad in dest.parents:
            raise SystemExit("refusing to write synthetic fixtures inside %s" % bad)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("output directory must be empty or new: %s" % dest)
    (dest / "out").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "index.html", dest / "index.html")
    for name, doc in files.items():
        (dest / "out" / name).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (dest / "SYNTHETIC_FIXTURES_README.txt").write_text("Every file under out/ here is INVENTED for layout checks. It is not market data.\n", encoding="utf-8")
    return sorted(files)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print("\n".join(build(sys.argv[1])))
