"""
historical_updater.py - StockLens Phase 3 Step 2: daily OHLCV history for the price chart.
Runs server-side only (GitHub Actions). The token is read from the environment and never printed or written.

Source : Upstox historical candle API  GET /v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}
Output : out/historical.json   (clean daily candles only - indicators are calculated in the browser)
Stocks : the same 10 test symbols as the other updaters (override with HISTORICAL_SYMBOLS="TCS,INFY,...").
         Adding a stock later = add the symbol; nothing else changes.
Index  : NIFTY 50 is stored as "benchmark" for Relative Strength. Its instrument key is looked up in the official
         Upstox NSE instrument file (segment NSE_INDEX, name "Nifty 50"); the run stops if it is not found exactly once.
Safety : any failed request (401/403/404/429/5xx/bad data) makes the run exit 1 BEFORE anything is written, so a
         known-good out/historical.json is never replaced and the cache is not saved. Every failure is listed with
         the symbol and the request (without the token). Symbols not requested in this run keep their old candles.
Nothing is estimated or filled in: a day without a valid candle is simply absent.
Incremental: an existing out/historical.json is extended (last ~10 days are re-fetched and replaced),
so a normal run needs only 1-2 calls per instrument.
"""
import datetime as dt
import gzip
import json
import os
import sys
import time
from urllib.parse import quote

import requests

from upstox_common import INSTRUMENTS_URL, ROOT, SYMBOLS, fail, get_token, log, num, write_json

OUT_FILE = ROOT / "out" / "historical.json"
CANDLE_URL = "https://api.upstox.com/v2/historical-candle/{key}/day/{to}/{frm}"
BENCHMARK_SYMBOL = "NIFTY 50"
BENCHMARK_NAME = "nifty 50"   # matched (case-insensitive) against the NSE_INDEX rows of the official instrument file.
                              # The instrument key itself is READ from that file at run time - it is not hard-coded.
YEARS = 5                  # first run: ~5 years of daily candles (200 DMA needs warm-up; 3Y/5Y chart ranges)
WINDOW_DAYS = 360          # one request covers at most this many days (keeps every request small)
OVERLAP_DAYS = 10          # re-fetch the last days of an existing file so late corrections are picked up
DELAY_SECONDS = 1.0        # pause after every call (conservative)
RETRIES = 3
MAX_CALLS_PER_RUN = 150
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def symbols():
    raw = os.environ.get("HISTORICAL_SYMBOLS", "").strip()
    return [s.strip().upper() for s in raw.split(",") if s.strip()] if raw else list(SYMBOLS)


def load_instrument_file():
    """Official Upstox NSE instrument file (no token needed) -> (equities, indices).
    equities: symbol -> {isin, instrument_key, name}; indices: list of {instrument_key, name} (segment NSE_INDEX)."""
    try:
        r = requests.get(INSTRUMENTS_URL, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        fail("Could not download the Upstox instruments file (" + type(e).__name__ + ").")
    try:
        rows = json.loads(gzip.decompress(r.content))
    except OSError:
        rows = json.loads(r.content)
    eq, idx = {}, []
    for x in rows:
        if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ" and x.get("trading_symbol") and x.get("isin"):
            eq[x["trading_symbol"].strip().upper()] = {"isin": x["isin"], "name": x.get("name"),
                                                       "instrument_key": x.get("instrument_key") or "NSE_EQ|" + x["isin"]}
        elif x.get("segment") == "NSE_INDEX" and x.get("instrument_key"):
            idx.append({"instrument_key": x["instrument_key"], "name": str(x.get("name") or x.get("trading_symbol") or "")})
    return eq, idx


def find_benchmark(indices):
    """Exactly one NSE_INDEX row called "Nifty 50" or the run stops (no guessed key)."""
    hits = [i for i in indices if i["name"].strip().lower() == BENCHMARK_NAME]
    if len(hits) != 1:
        near = sorted({i["name"] for i in indices if "nifty" in i["name"].lower() and "50" in i["name"]})[:8]
        fail("Could not identify NIFTY 50 in the Upstox instrument file (found %d exact matches). "
             "Similar index names: %s" % (len(hits), near))
    return hits[0]["instrument_key"]


def clean_candle(row):
    """[timestamp, open, high, low, close, volume, oi] -> candle dict, or None when the row is not usable."""
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return None
    day = str(row[0])[:10]
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        return None
    o, h, l, c, v = (num(row[i]) for i in range(1, 6))
    if None in (o, h, l, c) or min(o, h, l, c) <= 0 or h < l:
        return None
    return {"date": day, "open": o, "high": h, "low": l, "close": c,
            "volume": int(v) if v is not None and v >= 0 else None}


def short_message(r):
    """Upstox's own error text (first 120 chars), for the log. Never includes request headers."""
    try:
        e = (r.json().get("errors") or [{}])[0]
        m = e.get("message") or e.get("errorCode") or ""
    except (ValueError, AttributeError, IndexError, TypeError):
        m = ""
    return (": " + str(m)[:120]) if m else ""


class Client:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json", "Authorization": "Bearer " + token})
        self.calls = 0

    def candles(self, key, frm, to):
        """(list of candles, None) or (None, short error text). Never prints headers."""
        url = CANDLE_URL.format(key=quote(key, safe=""), to=to, frm=frm)
        err = "unknown error"
        for attempt in range(RETRIES):
            if self.calls >= MAX_CALLS_PER_RUN:
                return None, "call limit for this run reached"
            self.calls += 1
            try:
                r = self.s.get(url, timeout=30)
            except requests.RequestException as e:
                err = "network error: " + type(e).__name__
                time.sleep(DELAY_SECONDS * (attempt + 2))
                continue
            time.sleep(DELAY_SECONDS)
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    return None, "invalid JSON from Upstox"
                rows = ((body or {}).get("data") or {}).get("candles") if isinstance(body, dict) else None
                if body.get("status") == "error" or not isinstance(rows, list):
                    return None, "Upstox returned an error response"
                return [c for c in (clean_candle(x) for x in rows) if c], None
            if r.status_code in (401, 403):
                fail("Upstox rejected the token for historical candles (HTTP %d)%s while requesting %s. "
                     "Nothing was written. Renew UPSTOX_ANALYTICS_TOKEN or check that it may read market data."
                     % (r.status_code, short_message(r), key))
            err = "HTTP %d%s" % (r.status_code, short_message(r))
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            break
        return None, err


def windows(frm, to):
    """Split [frm, to] into consecutive date windows of at most WINDOW_DAYS."""
    out, start = [], frm
    while start <= to:
        end = min(start + dt.timedelta(days=WINDOW_DAYS - 1), to)
        out.append((start, end))
        start = end + dt.timedelta(days=1)
    return out


def last_complete_day():
    """Today's candle is partial until the market has closed, so before 16:00 IST stop at yesterday."""
    now = dt.datetime.now(IST)
    return now.date() if now.hour >= 16 else now.date() - dt.timedelta(days=1)


def merge(old, new):
    """Union by date; a freshly fetched candle replaces an older one. Sorted ascending."""
    by_date = {c["date"]: c for c in old}
    by_date.update({c["date"]: c for c in new})
    return [by_date[d] for d in sorted(by_date)]


def fetch_history(client, key, old, end):
    """New candles for one instrument: whole history if none yet, else from shortly before the last date."""
    if old:
        frm = max(dt.date.fromisoformat(old[-1]["date"]) - dt.timedelta(days=OVERLAP_DAYS),
                  end - dt.timedelta(days=365 * YEARS))
    else:
        frm = end - dt.timedelta(days=365 * YEARS)
    got = []
    for a, b in windows(frm, end):
        rows, err = client.candles(key, a.isoformat(), b.isoformat())
        if err:
            return None, err + " (" + a.isoformat() + " to " + b.isoformat() + ")"
        got += rows
    return got, None


def check_doc(doc):
    """Structure checks before the file is written. Returns a list of problems."""
    p = []
    entries = list(doc["stocks"].items()) + ([(BENCHMARK_SYMBOL, doc["benchmark"])] if doc.get("benchmark") else [])
    for sym, e in entries:
        prev = ""
        for c in e["candles"]:
            if c["date"] <= prev:
                p.append(sym + ": candles not in strictly ascending date order")
                break
            prev = c["date"]
            if c["high"] < c["low"] or min(c["open"], c["high"], c["low"], c["close"]) <= 0:
                p.append(sym + ": bad candle on " + c["date"])
                break
    return p


def summary(lines):
    """Also show the result on the workflow run page (GITHUB_STEP_SUMMARY), when running in GitHub Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main():
    token = get_token()
    old = {}
    if OUT_FILE.exists():
        try:
            old = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            old = {}
    old_stocks = old.get("stocks") if isinstance(old.get("stocks"), dict) else {}
    old_bench = (old.get("benchmark") or {}).get("candles") or []
    equities, indices = load_instrument_file()
    bench_key = find_benchmark(indices)
    log("NIFTY 50 resolved from the instrument file to key:", bench_key)
    client = Client(token)
    end = last_complete_day()

    stocks, errors, rows = dict(old_stocks), [], []      # symbols not requested this run keep their old candles
    for sym in symbols():
        meta = equities.get(sym)
        if not meta:
            errors.append({"symbol": sym, "request": "instrument lookup", "error": "Symbol not found in Upstox NSE equity instruments"})
            continue
        key = meta["instrument_key"]
        log(sym, "->", key)
        prior = (old_stocks.get(sym) or {}).get("candles") or []
        fresh, err = fetch_history(client, key, prior, end)
        if err:
            errors.append({"symbol": sym, "request": key, "error": err})
            continue
        candles = merge(prior, fresh)
        if not candles:
            errors.append({"symbol": sym, "request": key, "error": "Upstox returned no valid candles"})
            continue
        stocks[sym] = {"symbol": sym, "isin": meta["isin"], "instrument_key": key, "candles": candles}
        rows.append("| %s | %s | %d candles | %s to %s |" % (sym, key, len(candles), candles[0]["date"], candles[-1]["date"]))
        log(sym, "- ok,", len(candles), "candles")

    fresh, err = fetch_history(client, bench_key, old_bench, end)
    bench = None
    if err:
        errors.append({"symbol": BENCHMARK_SYMBOL, "request": bench_key, "error": err})
    else:
        merged = merge(old_bench, fresh)
        if merged:
            bench = {"symbol": BENCHMARK_SYMBOL, "instrument_key": bench_key, "candles": merged}
            rows.append("| %s | %s | %d candles | %s to %s |" % (BENCHMARK_SYMBOL, bench_key, len(merged), merged[0]["date"], merged[-1]["date"]))
            log(BENCHMARK_SYMBOL, "- ok,", len(merged), "candles")
        else:
            errors.append({"symbol": BENCHMARK_SYMBOL, "request": bench_key, "error": "Upstox returned no valid candles"})

    if errors:      # strict: one failed request = nothing is written, the old file (if any) stays exactly as it was
        lines = ["### Historical data: FAILED - nothing was written, no data was invented", ""]
        for e in errors:
            msg = "%s: %s (request: %s)" % (e["symbol"], e["error"], e["request"])
            print("::error::" + msg)
            lines.append("- " + msg)
        summary(lines)
        fail("%d request(s) failed; out/historical.json was NOT changed. See the errors above." % len(errors))

    doc = {"updated": dt.date.today().isoformat(), "source": "Upstox", "interval": "1day",
           "notes": {"candles": "Daily OHLCV, prices in INR, ascending dates, exactly as provided by Upstox. "
                                "Days without a valid candle are absent, never filled in.",
                     "benchmark": "NIFTY 50 index daily candles, used for Relative Strength. Index volume is not used."},
           "benchmark": bench, "stocks": stocks, "errors": errors}
    problems = check_doc(doc)
    if problems:
        for x in problems[:20]:
            print("::error::historical.json: " + x)
        fail("historical.json failed its checks and was NOT written.")
    write_json(OUT_FILE, doc)
    summary(["### Historical data: OK", "", "| Series | Instrument key | Candles | Range |", "|---|---|---|---|"] + rows)
    log("Wrote", OUT_FILE, "-", len(stocks), "stocks, API calls:", client.calls)


if __name__ == "__main__":
    sys.exit(main())
