"""
coverage_probe.py - StockLens READ-ONLY coverage probe.

Question it answers: for a small, representative set of stocks from the published stock directory, does Upstox serve every endpoint the research
updaters use, and does each response contain the fields the updaters need? It makes real GET requests and writes ONE folder, probe_output/ (a report plus
the raw responses, which are public company figures and never contain the token). Nothing else.

It reuses the production code READ-ONLY, so the probe measures what the pipeline would really do:
  fundamentals_updater.parse_ratios, financials_updater.fetch_section / parse_income / parse_balance / parse_cashflow,
  quarterly_results_updater.parse_quarterly, fundamentals_extra_updater.fetch_one (corporate actions, share holdings), historical_updater.Client,
  upstox_common.Upstox (its retry and 401/403 handling).

What it never does (enforced by test_coverage_probe.py):
  * it calls none of the production writers (write_json, write_outputs, the shard writers), reads or writes no cache, ledger, out/ file or site file;
  * its only write function refuses any path outside probe_output/;
  * the request budget is a STRICT maximum: 150 fundamentals requests plus 16 candle requests = 166 in all, retries included.

Endpoints per stock (Upstox), with the calls each costs when consolidated data exists (a standalone retry adds one call per statement that has no consolidated data):
  1 GET /v2/fundamentals/{ISIN}/profile                                         sector
  2 GET /v2/fundamentals/{ISIN}/key-ratios                                      P/E, P/B, ROA, ROE, ROCE, EV/EBITDA
  3 GET /v2/fundamentals/{ISIN}/income-statement?type=..&time_period=yearly&fs=true
  4 GET /v2/fundamentals/{ISIN}/balance-sheet?type=..&fs=true
  5 GET /v2/fundamentals/{ISIN}/cash-flow?type=..&fs=true
  6 GET /v2/fundamentals/{ISIN}/income-statement?type=..&time_period=quarterly&fs=true
  7 GET /v2/historical-candle/NSE_EQ|{ISIN}/day/{to}/{from}                     one 180-day window of daily candles
  8 GET /v2/fundamentals/{ISIN}/corporate-actions                               dividends, bonuses, splits, rights (fundamentals_extra_updater)
  9 GET /v2/fundamentals/{ISIN}/share-holdings                                  quarterly % by holder category (fundamentals_extra_updater)
NSE shareholding filings are NOT probed: bulk collection from NSE is excluded until the access terms are reviewed. Endpoint 9 is Upstox's own data.

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
import fundamentals_extra_updater as fx
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
FUNDAMENTALS_CALL_CAP = 150      # STRICT maximum of fundamentals requests, retries included (see make_fundamentals_api). 12 stocks need 96 to 144
CANDLE_CALL_CAP = 16             # STRICT maximum of candle requests, retries included (hist.Client checks before every attempt). 12 stocks need 12
CANDLE_WINDOW_DAYS = 180
SYMBOL_RE = re.compile(r"^[A-Z0-9&_.-]{1,20}$")

ENDPOINTS = ("profile", "key_ratios", "income", "balance_sheet", "cash_flow", "quarterly", "candles", "corporate_actions", "share_holdings")
REQUIRED = {
    "profile": ["sector"],
    "key_ratios": sorted(fund.RATIO_KEYS.values()),
    "income": ["revenue", "profit_after_tax"],
    "balance_sheet": ["total_assets", "total_liabilities"],
    "cash_flow": ["operating", "investing", "financing"],
    "quarterly": ["revenue", "operating_profit", "profit_after_tax"],
    "share_holdings": list(fx.CATEGORIES),
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


class Tally:
    """Requests made per logical call. One logical call makes 1 request, plus 1 for every retry (Upstox.get and Client.candles retry on network errors, 429 and 5xx).
    retries = requests - logical calls that made at least one request. Only counts and endpoint labels are kept: no URL, header or token."""

    def __init__(self):
        self.logical, self.retries, self.events = 0, 0, []

    def note(self, label, before, after):
        used = after - before
        if used > 0:
            self.logical += 1
            self.retries += used - 1
            if used > 1:
                self.events.append({"call": label, "requests": used})

    def as_dict(self):
        return {"logical_calls": self.logical, "retries": self.retries, "retried_calls": self.events[:50]}


class Recorder:
    """Wraps uc.Upstox: same get(path, params) -> (body, error); saves each raw response under probe_output/raw (public figures, no credential in it)."""

    def __init__(self, api, root=None):
        self.api, self.root, self.tally = api, root, Tally()

    @property
    def calls(self):
        return self.api.calls

    def get(self, path, params=None):
        before = self.api.calls
        body, err = self.api.get(path, params)
        self.tally.note(path.split("/", 1)[-1], before, self.api.calls)
        if body is not None:
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", "_".join([path] + [str(v) for v in sorted((params or {}).items())]))[:120]
            write_artifact("raw/%s.json" % name, body, self.root)
        return body, err


class CandleRecorder:
    """Wraps hist.Client the same way: same candles(key, frm, to), plus the retry tally."""

    def __init__(self, client):
        self.client, self.tally = client, Tally()

    @property
    def calls(self):
        return self.client.calls

    def candles(self, key, frm, to):
        before = self.client.calls
        out = self.client.candles(key, frm, to)
        self.tally.note("candles", before, self.client.calls)
        return out


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


def probe_extra(api, isin, kind):
    """corporate_actions or share_holdings through fundamentals_extra_updater.fetch_one (its documented-shape parser). ok / partial (a quarter flagged, or an
    action without its amount, ratio or ex-date) / no_data (a valid empty answer) / rejected (not the documented shape) / error."""
    status, data, err = fx.fetch_one(api, isin, kind)
    if status == "failed":
        return result("skipped_call_cap" if call_cap(err) else "error", error=short(err))
    if status == "unusable":
        return result("rejected", problem=short(err))
    if status == "none_reported":
        return result("no_data")
    if kind == "corporate_actions":
        ev = data["events"]
        types = {}
        for e in ev:
            types[e["type"]] = types.get(e["type"], 0) + 1
        gaps = {"dividend_without_amount": sum(1 for e in ev if e["type"] == "dividend" and e["amount"] is None),
                "bonus_or_split_without_ratio": sum(1 for e in ev if e["type"] in ("bonus", "split") and e["ratio"] is None),
                "without_ex_date": sum(1 for e in ev if e["ex_date"] is None), "type_other": types.get("other", 0)}
        dates = sorted(e["ex_date"] for e in ev if e["ex_date"])
        return result("partial" if any(gaps.values()) else "ok", events=len(ev), types=types, gaps=gaps, first=dates[0] if dates else None, last=dates[-1] if dates else None,
                      ttm_dividend_computable=fx.ttm_dividend(ev, dt.date.today().isoformat())["value"] is not None)
    q = data["quarters"]
    newest = q[0]
    missing = [c for c in REQUIRED["share_holdings"] if newest["values"].get(c) is None]
    return result("ok" if not newest["flags"] else "partial", quarters=len(q), first=q[-1]["quarter_end"], latest=newest["quarter_end"], flags=newest["flags"], missing=missing)


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
              "caps": {"fundamentals": FUNDAMENTALS_CALL_CAP, "candles": CANDLE_CALL_CAP},
              "shareholding": "NSE filings not probed (bulk NSE collection is excluded until its terms are reviewed); Upstox share-holdings is endpoint share_holdings",
              "identity": identity or {}, "results": {}, "summary": {}}
    rec, crec = Recorder(api, root), CandleRecorder(client)
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
            r["candles"] = probe_candles(crec, isin, end)
            r["corporate_actions"] = probe_extra(rec, isin, "corporate_actions")
            r["share_holdings"] = probe_extra(rec, isin, "share_holdings")
            uc.log(sym, " ".join("%s=%s" % (e, r[e]["status"]) for e in ENDPOINTS))
    finally:
        report["calls"] = {"fundamentals": api.calls, "candles": client.calls}
        report["retries"] = {"fundamentals": rec.tally.as_dict(), "candles": crec.tally.as_dict()}
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


def _cell(v):
    return re.sub(r"[|\r\n`<>]+", " ", str(v)).strip()[:160]


def summary_markdown(report):
    """The whole report as GitHub-flavoured markdown for the Actions step summary. Built only from report.json (public figures and counts; the report never holds the token)."""
    rep = report if isinstance(report, dict) else {}
    calls, caps, ret = rep.get("calls") or {}, rep.get("caps") or {}, rep.get("retries") or {}
    ok = rep.get("within_caps")
    out = ["## Coverage probe (read-only)", "",
           "Generated %s - %d symbol(s). Shareholding: %s" % (_cell(rep.get("generated_on", "?")), len(rep.get("symbols") or []), _cell(rep.get("shareholding", "?"))), "",
           "### Requests and cap", "",
           "| | Requests | Cap | Retries | Logical calls |", "|---|---|---|---|---|"]
    for k in ("fundamentals", "candles"):
        r = ret.get(k) or {}
        out.append("| %s | %s | %s | %s | %s |" % (k, calls.get(k, "?"), caps.get(k, "?"), r.get("retries", "?"), r.get("logical_calls", "?")))
    out += ["", "**Cap compliance: %s** (total %s of %s requests)" % ("WITHIN CAPS" if ok is True else "CAP EXCEEDED OR UNKNOWN" if ok is False else "UNKNOWN",
            sum(v for v in calls.values() if isinstance(v, int)), sum(v for v in caps.values() if isinstance(v, int)))]
    for k in ("fundamentals", "candles"):
        for e in (ret.get(k) or {}).get("retried_calls") or []:
            out.append("- retried %s call: %s: %s requests" % (k, _cell(e.get("call")), _cell(e.get("requests"))))
    out += ["", "### Identity (directory ISIN vs Upstox instrument master)", "", "| Symbol | Result |", "|---|---|"]
    for s, v in sorted((rep.get("identity") or {}).items()):
        out.append("| %s | %s |" % (_cell(s), _cell(v)))
    out += ["", "### Endpoint statuses by stock", "", "| Symbol | " + " | ".join(ENDPOINTS) + " |", "|---|" + "---|" * len(ENDPOINTS)]
    results = rep.get("results") or {}
    for s, r in results.items():
        r = r if isinstance(r, dict) else {}
        out.append("| %s | %s |" % (_cell(s), " | ".join(_cell((r.get(e) or {}).get("status", r.get("directory", "-"))) for e in ENDPOINTS)))
    out += ["", "### Totals per endpoint", "", "| Endpoint | " + " | ".join(s for s in STATUSES if s != "not_run") + " |", "|---|" + "---|" * (len(STATUSES) - 1)]
    for e, c in (rep.get("summary") or {}).items():
        out.append("| %s | %s |" % (_cell(e), " | ".join(str((c or {}).get(s, 0)) for s in STATUSES if s != "not_run")))
    out += ["", "### Missing fields, errors and quarterly/candle detail", "", "| Symbol | Endpoint | Status | Basis | Missing fields | Error / problem | Detail |", "|---|---|---|---|---|---|---|"]
    n = 0
    for s, r in results.items():
        for e in ENDPOINTS:
            x = (r or {}).get(e) if isinstance(r, dict) else None
            if not isinstance(x, dict):
                continue
            detail = ""
            if e == "quarterly":
                detail = "quarters=%s %s..%s same-quarter-last-year=%s" % (x.get("quarters"), x.get("first"), x.get("latest"), x.get("year_ago_quarter_present"))
            elif e == "candles":
                detail = "candles=%s %s..%s" % (x.get("candles"), x.get("first"), x.get("last"))
            elif e == "corporate_actions":
                detail = "events=%s %s..%s types=%s gaps=%s ttm=%s" % (x.get("events"), x.get("first"), x.get("last"), x.get("types"), x.get("gaps"), x.get("ttm_dividend_computable"))
            elif e == "share_holdings":
                detail = "quarters=%s %s..%s flags=%s" % (x.get("quarters"), x.get("first"), x.get("latest"), x.get("flags"))
            elif x.get("period"):
                detail = "period=%s" % x.get("period")
            if x.get("status") != "ok" or e in ("quarterly", "candles", "corporate_actions", "share_holdings"):
                n += 1
                out.append("| %s | %s | %s | %s | %s | %s | %s |" % (_cell(s), e, _cell(x.get("status")), _cell(x.get("basis", "")), _cell(", ".join(x.get("missing") or [])),
                                                                _cell(x.get("error") or x.get("problem") or ""), _cell(detail)))
    if not n:
        out.append("| - | - | - | - | - | - | - |")
    return "\n".join(out) + "\n"


def summary_main(path=None):
    """python coverage_probe.py summary  ->  markdown on stdout (the workflow appends it to the step summary). Reads only probe_output/report.json."""
    p = Path(path) if path else OUT_DIR / "report.json"
    try:
        report = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("## Coverage probe\n\nNo readable report.json was produced; see the job log.")
        return 0
    sys.stdout.write(summary_markdown(report))
    return 0


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
    sys.exit(summary_main() if sys.argv[1:] == ["summary"] else main())
