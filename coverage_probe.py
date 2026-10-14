"""
coverage_probe.py - StockLens READ-ONLY coverage probe.

Question it answers: for a small, representative set of stocks from the published stock directory, does Upstox serve every endpoint the research
updaters use, and does each response contain the fields the updaters need? It makes real GET requests and writes ONE folder, probe_output/ (a report plus
the raw responses, which are public company figures and never contain the token). Nothing else.

It reuses the production code READ-ONLY, so the probe measures what the pipeline would really do:
  fundamentals_updater.parse_ratios, financials_updater.fetch_section / parse_income / parse_balance / parse_cashflow,
  quarterly_results_updater.parse_quarterly, historical_updater.Client, upstox_common.Upstox (its retry and 401/403 handling).

What it never does (enforced by test_coverage_probe.py):
  * it calls none of the production writers (write_json, write_outputs, the shard writers), reads or writes no cache, ledger, out/ file or site file;
  * its only write function refuses any path outside probe_output/;
  * the request budget is a STRICT maximum: 130 fundamentals requests plus 16 candle requests = 146 in all, retries included.

Endpoints per stock (Upstox), with the calls each costs when consolidated data exists (a standalone retry adds one call per statement that has no consolidated data):
  1 GET /v2/fundamentals/{ISIN}/profile                                         sector
  2 GET /v2/fundamentals/{ISIN}/key-ratios                                      P/E, P/B, ROA, ROE, ROCE, EV/EBITDA
  3 GET /v2/fundamentals/{ISIN}/income-statement?type=..&time_period=yearly&fs=true
  4 GET /v2/fundamentals/{ISIN}/balance-sheet?type=..&fs=true
  5 GET /v2/fundamentals/{ISIN}/cash-flow?type=..&fs=true
  6 GET /v2/fundamentals/{ISIN}/income-statement?type=..&time_period=quarterly&fs=true
  7 GET /v2/historical-candle/NSE_EQ|{ISIN}/day/{to}/{from}                     one 180-day window of daily candles
Shareholding is NOT probed: it reads NSE filings, which is excluded from bulk collection until the access terms are reviewed.

Environment: UPSTOX_ANALYTICS_TOKEN (required), PROBE_SYMBOLS (comma list, default below; at most 12, each must be in the directory),
             PROBE_DIRECTORY (default out/stock_directory.json), PROBE_OUT (default probe_output).
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import financials_updater as fin
import fundamentals_updater as fund
import historical_updater as hist
import quarterly_results_updater as qru
import stock_directory
import upstox_common as uc

OUT_DIR = Path(os.environ.get("PROBE_OUT", "probe_output"))
DIRECTORY_FILE = Path(os.environ.get("PROBE_DIRECTORY", "out/stock_directory.json"))

# 4 large caps, 3 mid caps, a bank and a card lender (financials: no plain "Revenue" line), a 2025-listed financial, a micro cap, and a stock with a loss-making history.
DEFAULT_SYMBOLS = ("RELIANCE", "HDFCBANK", "TCS", "INFY", "PERSISTENT", "VOLTAS", "ASTRAL", "YESBANK", "SBICARD", "JIOFIN", "3BBLACKBIO", "SUZLON")
MAX_SYMBOLS = 12
FUNDAMENTALS_CALL_CAP = 130      # STRICT maximum of fundamentals requests, retries included (see make_fundamentals_api). 12 stocks need 72 to 120
CANDLE_CALL_CAP = 16             # STRICT maximum of candle requests, retries included (hist.Client checks before every attempt). 12 stocks need 12
CANDLE_WINDOW_DAYS = 180
SYMBOL_RE = re.compile(r"^[A-Z0-9&_.-]{1,20}$")

ENDPOINTS = ("profile", "key_ratios", "income", "balance_sheet", "cash_flow", "quarterly", "candles")
REQUIRED = {
    "profile": ["sector"],
    "key_ratios": sorted(fund.RATIO_KEYS.values()),
    "income": ["revenue", "profit_after_tax"],
    "balance_sheet": ["total_assets", "total_liabilities"],
    "cash_flow": ["operating", "investing", "financing"],
    "quarterly": ["revenue", "operating_profit", "profit_after_tax"],
}
STATUSES = ("ok", "partial", "no_data", "rejected", "error", "skipped_call_cap", "not_run")


# ------------------------------------------------------------------ the only write path
def safe_path(rel, root=None):
    """OUT_DIR / rel, refusing anything that resolves outside OUT_DIR (the probe writes nowhere else)."""
    base = Path(root or OUT_DIR).resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise ValueError("refusing to write outside the probe output folder")
    return p


def write_artifact(rel, obj, root=None):
    p = safe_path(rel, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return p


# ------------------------------------------------------------------ requests
def make_fundamentals_api(token, cap=None):
    """uc.Upstox with a STRICT request maximum. Upstox.get checks its budget only when a call starts, and one call may then make up to RETRIES attempts, so a plain budget of N can
    end at N + RETRIES - 1 requests. Starting no call unless a full set of retries still fits keeps the total at or below the cap (it may stop up to RETRIES - 1 requests early)."""
    cap = FUNDAMENTALS_CALL_CAP if cap is None else cap
    return uc.Upstox(token, cap - (uc.RETRIES - 1))


class Recorder:
    """Wraps uc.Upstox: same get(path, params) -> (body, error); saves each raw response under probe_output/raw (public figures, no credential in it)."""

    def __init__(self, api, root=None):
        self.api, self.root = api, root

    @property
    def calls(self):
        return self.api.calls

    def get(self, path, params=None):
        body, err = self.api.get(path, params)
        if body is not None:
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", "_".join([path] + [str(v) for v in sorted((params or {}).items())]))[:120]
            write_artifact("raw/%s.json" % name, body, self.root)
        return body, err


def short(err):
    return str(err)[:120]


def call_cap(err):
    return "call limit" in str(err)


def result(status, **kw):
    assert status in STATUSES, status
    return dict({"status": status}, **kw)


def missing_of(values, required):
    return [k for k in required if not isinstance(values, dict) or values.get(k) is None]


# ------------------------------------------------------------------ one endpoint each (all read-only; all return a result dict and never raise for a bad response)
def probe_profile(api, isin):
    body, err = api.get(isin + "/profile")
    if err:
        return result("skipped_call_cap" if call_cap(err) else "error", error=short(err))
    sector = (body.get("data") or {}).get("sector") if isinstance(body.get("data"), dict) else None
    ok = isinstance(sector, str) and bool(sector.strip())
    return result("ok" if ok else "no_data", missing=[] if ok else ["sector"])


def probe_ratios(api, isin):
    body, err = api.get(isin + "/key-ratios")
    if err:
        return result("skipped_call_cap" if call_cap(err) else "error", error=short(err))
    vals = fund.parse_ratios(body)
    missing = missing_of(vals, REQUIRED["key_ratios"])
    return result("ok" if not missing else "no_data" if len(missing) == len(REQUIRED["key_ratios"]) else "partial", missing=missing)


def probe_section(api, isin, name, path, extra, parser):
    parsed, basis, err = fin.fetch_section(api, isin, path, extra, parser, None)
    if err:
        if call_cap(err):
            return result("skipped_call_cap", error=short(err))
        return result("no_data" if err.startswith("no usable data") else "error", error=short(err))
    missing = missing_of(parsed, REQUIRED[name])
    return result("ok" if not missing else "partial", basis=basis, period=parsed.get("period"), missing=missing)


def probe_quarterly(api, isin):
    """Mirrors quarterly_results_updater.run_batch for one stock: consolidated first, standalone only when consolidated has no data."""
    last = None
    for basis in qru.BASES:
        body, err = api.get(isin + "/income-statement", {"type": basis, "time_period": "quarterly", "fs": "true"})
        if err:
            return result("skipped_call_cap" if call_cap(err) else "error", error=short(err), basis=basis)
        parsed = qru.parse_quarterly(body)
        if parsed["quarters"]:
            ends = sorted(parsed["quarters"])
            latest = parsed["quarters"][ends[-1]]["values"]
            year_ago = qru.add_months(dt.date.fromisoformat(ends[-1]), -12).isoformat() in parsed["quarters"]
            present = {f: sum(1 for q in parsed["quarters"].values() if q["values"].get(f) is not None) for f in qru.FIELDS}
            missing = missing_of(latest, REQUIRED["quarterly"])
            return result("ok" if not missing else "partial", basis=basis, quarters=len(ends), first=ends[0], latest=ends[-1], year_ago_quarter_present=year_ago,
                          fields_present=present, missing=missing)
        last = parsed["problem"]
        if last != "no_data":
            return result("rejected", problem=last, basis=basis)
    return result("no_data", problem=last)


def probe_candles(client, isin, end):
    to = end.isoformat()
    frm = (end - dt.timedelta(days=CANDLE_WINDOW_DAYS)).isoformat()
    rows, err = client.candles("NSE_EQ|" + isin, frm, to)
    if err:
        return result("skipped_call_cap" if call_cap(err) else "error", error=short(err))
    if not rows:
        return result("no_data", candles=0)
    return result("ok" if len(rows) >= 100 else "partial", candles=len(rows), first=rows[0]["date"], last=rows[-1]["date"])


# ------------------------------------------------------------------ the run
def load_directory(path=None):
    path = Path(path or DIRECTORY_FILE)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        uc.fail("the stock directory could not be read (%s)" % path.name)
    bad = stock_directory.check_directory_doc(doc)
    if bad:
        uc.fail("the stock directory failed its own checks: " + str(bad[0])[:120])
    return {x["symbol"]: x for x in doc["symbols"]}


def choose_symbols(raw):
    parts = [p.strip() for p in (raw or ",".join(DEFAULT_SYMBOLS)).split(",") if p.strip()]
    if not 1 <= len(parts) <= MAX_SYMBOLS or len(set(parts)) != len(parts) or any(not SYMBOL_RE.match(p) for p in parts):
        uc.fail("PROBE_SYMBOLS must be 1 to %d different plain symbols" % MAX_SYMBOLS)
    return parts


def identity_check(directory, symbols):
    """The directory's ISIN against the Upstox NSE instrument file (a public download, no token, not an API call)."""
    try:
        instruments = uc.load_instruments()
    except SystemExit:
        return {s: "instrument_file_unavailable" for s in symbols}
    out = {}
    for s in symbols:
        m = instruments.get(s)
        out[s] = "missing_in_instruments" if not m else "match" if m.get("isin") == directory[s]["isin"] else "isin_mismatch"
    return out


def run(symbols, directory, api, client, end, identity=None, root=None):
    report = {"kind": "coverage_probe", "generated_on": dt.date.today().isoformat(), "symbols": list(symbols), "endpoints": list(ENDPOINTS),
              "caps": {"fundamentals": FUNDAMENTALS_CALL_CAP, "candles": CANDLE_CALL_CAP}, "shareholding": "not probed (NSE filing collection is excluded until its terms are reviewed)",
              "identity": identity or {}, "results": {}, "summary": {}}
    rec = Recorder(api, root)
    try:
        for sym in symbols:
            entry = directory.get(sym)
            if not entry:
                report["results"][sym] = {"directory": "not_in_directory"}
                continue
            isin = entry["isin"]
            r = report["results"][sym] = {"isin": isin, "name": entry["name"]}
            r["profile"] = probe_profile(rec, isin)
            r["key_ratios"] = probe_ratios(rec, isin)
            for name, path, extra, parser in fin.SECTIONS:
                r[name] = probe_section(rec, isin, name, path, extra, parser)
            r["quarterly"] = probe_quarterly(rec, isin)
            r["candles"] = probe_candles(client, isin, end)
            uc.log(sym, " ".join("%s=%s" % (e, r[e]["status"]) for e in ENDPOINTS))
    finally:
        report["calls"] = {"fundamentals": api.calls, "candles": client.calls}
        report["within_caps"] = api.calls <= FUNDAMENTALS_CALL_CAP and client.calls <= CANDLE_CALL_CAP
        report["summary"] = summarize(report["results"])
        write_artifact("report.json", report, root)
    return report


def summarize(results):
    out = {e: {s: 0 for s in STATUSES if s != "not_run"} for e in ENDPOINTS}
    for r in results.values():
        for e in ENDPOINTS:
            s = (r.get(e) or {}).get("status")
            if s in out[e]:
                out[e][s] += 1
    return out


def main():
    token = uc.get_token()
    directory = load_directory()
    symbols = choose_symbols(os.environ.get("PROBE_SYMBOLS"))
    present = [s for s in symbols if s in directory]
    hist.MAX_CALLS_PER_RUN = CANDLE_CALL_CAP
    api = make_fundamentals_api(token)
    client = hist.Client(token)
    report = run(symbols, directory, api, client, hist.last_complete_day(), identity_check(directory, present))
    uc.log("calls used: fundamentals %d, candles %d" % (report["calls"]["fundamentals"], report["calls"]["candles"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
