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

import research_test_batch
import universe

ROOT = Path(os.environ.get("DATA_DIR", "."))
CACHE_FILE = ROOT / "data" / "fundamentals_cache.json"
OUT_FILE = ROOT / "out" / "fundamentals.json"

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
API_BASE = "https://api.upstox.com/v2/fundamentals"

SYMBOLS = list(universe.TEST_SYMBOLS)   # the 10 development stocks; the stocks actually processed come from universe.load()

DELAY_SECONDS = 2.0        # pause after every call
MAX_CALLS_PER_RUN = universe.budget("FUNDAMENTALS_MAX_CALLS", 60)   # hard stop for one run (10 stocks need about 20; a NIFTY 500 fill is set by the workflow)
RATIOS_MAX_AGE_DAYS = 7    # refresh ratios weekly
PROFILE_MAX_AGE_DAYS = 90  # sector rarely changes
RETRIES = 3
RATIO_KEYS = {"P/E": "pe", "P/B": "pb", "ROA": "roa", "ROE": "roe",
              "ROCE": "roce", "EV/EBITDA": "ev_ebitda"}
METRICS = tuple(RATIO_KEYS.values())
NOT_FOUND_ERROR = "Symbol not found in Upstox NSE equity instruments"
COVERAGE_NOTE = ("Counts describe what this file holds, not what Upstox could provide: a metric the provider did not return stays null and is listed in the row's 'missing'. "
                 "A stock in the universe that has no row yet has not been fetched (or Upstox does not serve it); it is counted under not_yet_fetched / not_in_upstox, never filled in.")


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
        url = API_BASE + "/" + path
        err = "unknown error"
        for attempt in range(RETRIES):
            if self.calls >= MAX_CALLS_PER_RUN:      # checked before EVERY attempt, so a retry can never take a run past its cap
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


def staleness_key(entry):
    """What order_stalest sorts on: the older of the two fetch dates (never fetched = ""). A stock whose last attempt FAILED counts as if it had been refreshed on the day of that attempt, so a
    stock Upstox keeps refusing goes to the back of the queue instead of taking the front of every run's budget while thousands of others wait."""
    e = entry or {}
    key = min(e.get("profile_fetched") or "", e.get("ratios_fetched") or "")
    if e.get("last_error") and e.get("last_attempt"):
        key = max(key, e["last_attempt"])
    return key


def missing_of(ratios):
    """The metrics a stock's ratios do not hold, in a fixed order. Always explicit: nothing is estimated or carried over from another metric."""
    r = ratios or {}
    return [m for m in METRICS if r.get(m) is None]


def coverage_of(symbols, stocks, rows, tally, calls, cap, day):
    """The numbers that tell 'in the universe' apart from 'has all its metrics'. All derived from the rows and cache, never estimated."""
    published = {r["symbol"] for r in rows}
    full = sum(1 for r in rows if not r["missing"])
    none = sum(1 for r in rows if len(r["missing"]) == len(METRICS))
    in_error = [s for s in symbols if (stocks.get(s) or {}).get("last_error") and (stocks.get(s) or {}).get("last_error") != NOT_FOUND_ERROR]
    return {
        "universe": len(symbols),
        "published": len(rows),
        "metrics_complete": full,
        "metrics_partial": len(rows) - full - none,
        "metrics_none": none,
        "metric_available": {m: sum(1 for r in rows if r.get(m) is not None) for m in METRICS},
        "not_yet_fetched": sum(1 for s in symbols if s not in published and (stocks.get(s) or {}).get("last_error") != NOT_FOUND_ERROR),
        "not_in_upstox": sum(1 for s in symbols if (stocks.get(s) or {}).get("last_error") == NOT_FOUND_ERROR),
        "with_error": len(in_error),
        "ratios_older_than_%d_days" % RATIOS_MAX_AGE_DAYS: sum(1 for r in rows if is_stale(r.get("fetched"), RATIOS_MAX_AGE_DAYS)),
        "last_run": dict(tally, calls=calls, call_cap=cap, date=day),
        "note": COVERAGE_NOTE,
    }


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

    try:
        symbols = research_test_batch.extend(universe.load())      # + the five-stock test batch (see research_test_batch.py); nothing else changes
    except universe.UniverseError as err:
        fail(str(err))
    # stalest first: a run that stops at its call budget carries on with the rest next time, so the whole universe rotates
    symbols = universe.order_stalest(symbols, {x: staleness_key(stocks.get(x)) for x in symbols})
    log("Universe:", len(symbols), "stocks | call budget:", MAX_CALLS_PER_RUN)
    pending = 0
    tally = {"processed": 0, "succeeded": 0, "partial": 0, "failed": 0, "up_to_date": 0, "not_in_upstox": 0, "not_reached": 0}
    for sym in symbols:
        m = instruments.get(sym)
        old = stocks.get(sym) or {}
        if not m:                            # the instrument list is local data: this costs no API call, so it is classified even after the budget is spent
            stocks.setdefault(sym, {})["last_error"] = NOT_FOUND_ERROR
            tally["not_in_upstox"] += 1
            log(sym, "- not found")
            continue
        if old.get("last_error") == NOT_FOUND_ERROR:
            old["last_error"] = None         # Upstox lists it now: a stale "not found" must not outlive the listing, even if the budget is spent before it is fetched
        if api.calls >= MAX_CALLS_PER_RUN:
            if (is_stale(old.get("profile_fetched"), PROFILE_MAX_AGE_DAYS) or is_stale(old.get("ratios_fetched"), RATIOS_MAX_AGE_DAYS)):
                pending += 1                 # needs work but not reached this run: not an error, it keeps its previous data and goes first next time
            continue
        e = stocks.setdefault(sym, {})
        e["isin"], e["company_name"] = m["isin"], m["name"]
        errs = []
        calls_before, ratios_now = api.calls, False

        if is_stale(e.get("profile_fetched"), PROFILE_MAX_AGE_DAYS):
            body, err = api.get(m["isin"] + "/profile")
            if err:
                if "call limit" not in err:       # running out of budget mid-stock is not a data problem
                    errs.append("profile: " + err)
            else:
                sector = (body.get("data") or {}).get("sector")
                e["sector"] = sector.strip() if isinstance(sector, str) and sector.strip() else None
                e["profile_fetched"] = today()

        if is_stale(e.get("ratios_fetched"), RATIOS_MAX_AGE_DAYS):
            body, err = api.get(m["isin"] + "/key-ratios")
            if err:
                if "call limit" not in err:
                    errs.append("key-ratios: " + err)
            else:
                e["ratios"] = parse_ratios(body)
                e["ratios_fetched"] = today()
                ratios_now = True

        e["last_error"] = "; ".join(errs) or None
        if api.calls > calls_before:
            e["last_attempt"] = today()          # drives the back-off in staleness_key(): only a FAILED attempt moves a stock back
        if errs:
            tally["failed"] += 1
        elif is_stale(e.get("profile_fetched"), PROFILE_MAX_AGE_DAYS) or is_stale(e.get("ratios_fetched"), RATIOS_MAX_AGE_DAYS):
            pending += 1                         # the budget ran out in the middle of this stock
        elif api.calls == calls_before:
            tally["up_to_date"] += 1
        elif ratios_now and missing_of(e.get("ratios")):
            tally["partial"] += 1
        else:
            tally["succeeded"] += 1
        log(sym, "- ok" if not errs else "- problem: " + e["last_error"])

    tally["not_reached"] = pending
    tally["processed"] = tally["succeeded"] + tally["partial"] + tally["failed"]
    if pending:
        log("Not reached in this run (call budget):", pending, "stock(s)")
    rows, errors, dates = [], [], []
    for sym in symbols:
        e = stocks.get(sym, {})
        if e.get("last_error"):
            errors.append({"symbol": sym, "error": e["last_error"]})
        if not e.get("isin") or not e.get("ratios_fetched"):
            continue                       # never publish a stock we have no real ratios for
        r = e.get("ratios") or {}
        dates.append(e["ratios_fetched"])
        rows.append({
            "symbol": sym, "isin": e["isin"], "company_name": e.get("company_name"),
            "sector": e.get("sector") if sym in universe.TEST_SYMBOLS else None,   # sector stays private for the wider universe until the sector approval gate is opened
            "pe": r.get("pe"), "pb": r.get("pb"), "roa": r.get("roa"), "roe": r.get("roe"),
            "roce": r.get("roce"), "ev_ebitda": r.get("ev_ebitda"),
            "revenue_growth": None, "profit_growth": None,   # not fetched in this version
            "missing": missing_of(r),                         # the ratio metrics Upstox did not return for this stock (null above); [] = all six present
            "fetched": e["ratios_fetched"],                   # the day these ratios were read
        })

    write_json(CACHE_FILE, cache)
    if not rows:
        fail("No fundamentals could be fetched. The previous out/fundamentals.json (if any) was kept.")
    cov = coverage_of(symbols, stocks, rows, tally, api.calls, MAX_CALLS_PER_RUN, today())
    write_json(OUT_FILE, {"as_of": max(dates), "source": "Upstox", "stocks": rows, "errors": errors, "coverage": cov})
    log("Wrote", OUT_FILE, "-", len(rows), "stocks,", len(errors), "with problems, API calls:", api.calls)
    log("Coverage: universe %d | published %d (all metrics %d, some missing %d, none %d) | not yet fetched %d | not in Upstox %d | with error %d" % (
        cov["universe"], cov["published"], cov["metrics_complete"], cov["metrics_partial"], cov["metrics_none"], cov["not_yet_fetched"], cov["not_in_upstox"], cov["with_error"]))
    log("This run: processed %d (succeeded %d, partial %d, failed %d) | already fresh %d | not reached (budget) %d | calls %d of %d" % (
        tally["processed"], tally["succeeded"], tally["partial"], tally["failed"], tally["up_to_date"], tally["not_reached"], api.calls, MAX_CALLS_PER_RUN))


if __name__ == "__main__":
    main()
