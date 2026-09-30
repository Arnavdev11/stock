"""
fundamentals_updater.py - real fundamentals from the Upstox Fundamentals API.

TEST VERSION: 10 stocks only. Per stock it makes at most 2 calls:
  GET /v2/fundamentals/{ISIN}/profile      -> sector
  GET /v2/fundamentals/{ISIN}/key-ratios   -> P/E, P/B, ROA, ROE, ROCE, EV/EBITDA
Symbol -> ISIN -> company name comes from the official Upstox NSE instrument JSON
(segment NSE_EQ, instrument_type EQ). That file needs no token.

The token is read ONLY from the environment variable UPSTOX_ANALYTICS_TOKEN.
It is never printed, logged, or written to any file.

Output:  out/fundamentals.json      (read by the website)
Cache:   data/fundamentals_cache.json (so unchanged data is not fetched again)

Rate limits: Upstox lists 50 requests/second, 500/minute and 2,000 per 30 minutes for
standard APIs. This script deliberately waits 2 seconds after every call (about 30 per
minute), far below those limits.
"""
import gzip
import json
import math
import os
import sys
import time
import datetime as dt
from pathlib import Path

import requests

ROOT = Path(os.environ.get("DATA_DIR", "."))
CACHE_FILE = ROOT / "data" / "fundamentals_cache.json"
OUT_FILE = ROOT / "out" / "fundamentals.json"

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
API_BASE = "https://api.upstox.com/v2/fundamentals"

SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
           "SBIN", "ITC", "BHARTIARTL", "LT", "MARUTI"]

DELAY_SECONDS = 2.0        # pause after every call
MAX_CALLS_PER_RUN = 60     # hard stop for one run (test needs about 20)
RATIOS_MAX_AGE_DAYS = 7    # refresh ratios weekly
PROFILE_MAX_AGE_DAYS = 90  # sector rarely changes
RETRIES = 3
RATIO_KEYS = {"P/E": "pe", "P/B": "pb", "ROA": "roa", "ROE": "roe",
              "ROCE": "roce", "EV/EBITDA": "ev_ebitda"}


def log(*a):
    print(*a, flush=True)


def fail(msg):
    print("::error::" + msg, flush=True)  # shows as a red error in GitHub Actions
    sys.exit(1)


def today():
    return dt.date.today().isoformat()


def is_stale(day, max_age):
    if not day:
        return True
    try:
        return (dt.date.today() - dt.date.fromisoformat(day)).days >= max_age
    except ValueError:
        return True


def num(v):
    """Text like '8.94%' or '1,234.5' -> float. Anything unusable -> None (never NaN)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        t = str(v).strip().replace(",", "").replace("%", "")
        if t.lower() in ("", "-", "--", "na", "n/a", "nan", "null", "none"):
            return None
        try:
            x = float(t)
        except ValueError:
            return None
    return round(x, 2) if math.isfinite(x) else None


def parse_ratios(body):
    out = {k: None for k in RATIO_KEYS.values()}
    for item in (body.get("data") or []):
        key = RATIO_KEYS.get(str(item.get("name", "")).strip())
        if key:
            out[key] = num(item.get("company_value"))
    return out


def load_instruments():
    """symbol -> {isin, name} from the official Upstox NSE instrument JSON."""
    try:
        r = requests.get(INSTRUMENTS_URL, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        fail("Could not download the Upstox instruments file (" + type(e).__name__ + ").")
    try:
        rows = json.loads(gzip.decompress(r.content))
    except OSError:                      # already decompressed by the HTTP layer
        rows = json.loads(r.content)
    found = {}
    for x in rows:
        if (x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"
                and x.get("trading_symbol") and x.get("isin")):
            found[x["trading_symbol"].strip().upper()] = {"isin": x["isin"], "name": x.get("name")}
    return found


class Upstox:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json",
                               "Authorization": "Bearer " + token})
        self.calls = 0

    def get(self, path):
        """Returns (json, None) or (None, short error text). Never prints headers."""
        if self.calls >= MAX_CALLS_PER_RUN:
            return None, "call limit for this run reached"
        url = API_BASE + "/" + path
        err = "unknown error"
        for attempt in range(RETRIES):
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
                if not isinstance(body, dict) or body.get("status") == "error":
                    return None, "Upstox returned an error response"
                return body, None
            if r.status_code in (401, 403):
                fail("Upstox rejected the token (HTTP %d). Renew UPSTOX_ANALYTICS_TOKEN." % r.status_code)
            err = "HTTP %d" % r.status_code
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(10 * (attempt + 1))   # back off, then retry
                continue
            break
        return None, err


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, allow_nan=False), encoding="utf-8")  # NaN is impossible
    tmp.replace(path)


def main():
    token = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
    if not token:
        fail("UPSTOX_ANALYTICS_TOKEN is not set. Add it in GitHub: "
             "Settings > Secrets and variables > Actions.")

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    stocks = cache.setdefault("stocks", {})

    instruments = load_instruments()
    log("NSE equity instruments loaded:", len(instruments))
    api = Upstox(token)

    for sym in SYMBOLS:
        e = stocks.setdefault(sym, {})
        m = instruments.get(sym)
        if not m:
            e["last_error"] = "Symbol not found in Upstox NSE equity instruments"
            log(sym, "- not found")
            continue
        e["isin"], e["company_name"] = m["isin"], m["name"]
        errs = []

        if is_stale(e.get("profile_fetched"), PROFILE_MAX_AGE_DAYS):
            body, err = api.get(m["isin"] + "/profile")
            if err:
                errs.append("profile: " + err)
            else:
                sector = (body.get("data") or {}).get("sector")
                e["sector"] = sector.strip() if isinstance(sector, str) and sector.strip() else None
                e["profile_fetched"] = today()

        if is_stale(e.get("ratios_fetched"), RATIOS_MAX_AGE_DAYS):
            body, err = api.get(m["isin"] + "/key-ratios")
            if err:
                errs.append("key-ratios: " + err)
            else:
                e["ratios"] = parse_ratios(body)
                e["ratios_fetched"] = today()

        e["last_error"] = "; ".join(errs) or None
        log(sym, "- ok" if not errs else "- problem: " + e["last_error"])

    rows, errors, dates = [], [], []
    for sym in SYMBOLS:
        e = stocks.get(sym, {})
        if e.get("last_error"):
            errors.append({"symbol": sym, "error": e["last_error"]})
        if not e.get("isin") or not e.get("ratios_fetched"):
            continue                       # never publish a stock we have no real ratios for
        r = e.get("ratios") or {}
        dates.append(e["ratios_fetched"])
        rows.append({
            "symbol": sym, "isin": e["isin"], "company_name": e.get("company_name"),
            "sector": e.get("sector"),
            "pe": r.get("pe"), "pb": r.get("pb"), "roa": r.get("roa"), "roe": r.get("roe"),
            "roce": r.get("roce"), "ev_ebitda": r.get("ev_ebitda"),
            "revenue_growth": None, "profit_growth": None,   # not fetched in this version
        })

    write_json(CACHE_FILE, cache)
    if not rows:
        fail("No fundamentals could be fetched. The previous out/fundamentals.json (if any) was kept.")
    write_json(OUT_FILE, {"as_of": max(dates), "source": "Upstox", "stocks": rows, "errors": errors})
    log("Wrote", OUT_FILE, "-", len(rows), "stocks,", len(errors), "with problems, API calls:", api.calls)


if __name__ == "__main__":
    main()
