"""
upstox_common.py - shared helpers for the NEW StockLens updaters (Phase 1 and later).
The working fundamentals_updater.py does not import this file and is not affected by it.
The token is read only from the environment and is never printed, logged or written.
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
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
API_BASE = "https://api.upstox.com/v2/fundamentals"
SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
           "SBIN", "ITC", "BHARTIARTL", "LT", "MARUTI"]
DELAY_SECONDS = 2.0   # pause after every call (conservative)
RETRIES = 3
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def log(*a):
    print(*a, flush=True)


def fail(msg):
    print("::error::" + msg, flush=True)
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
    """'8.94%', '+10.53%', '1,234.5' or a number -> float. Anything unusable -> None (never NaN)."""
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


def period_key(label):
    """'Mar 2026' -> (2026, 3); anything else -> None."""
    try:
        mon, year = str(label).strip().split()
        return (int(year), MONTHS[mon[:3].lower()])
    except (ValueError, KeyError):
        return None


def latest(history):
    """Entry with the newest parsable period (falls back to the first entry)."""
    items = [h for h in (history or []) if isinstance(h, dict)]
    best = None
    for h in items:
        k = period_key(h.get("period"))
        if k and (best is None or k > best[0]):
            best = (k, h)
    return best[1] if best else (items[0] if items else None)


def load_instruments():
    """symbol -> {isin, name} from the official Upstox NSE instrument JSON (no token needed)."""
    try:
        r = requests.get(INSTRUMENTS_URL, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        fail("Could not download the Upstox instruments file (" + type(e).__name__ + ").")
    try:
        rows = json.loads(gzip.decompress(r.content))
    except OSError:
        rows = json.loads(r.content)
    found = {}
    for x in rows:
        if (x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"
                and x.get("trading_symbol") and x.get("isin")):
            found[x["trading_symbol"].strip().upper()] = {"isin": x["isin"], "name": x.get("name")}
    return found


def get_token():
    token = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
    if not token:
        fail("UPSTOX_ANALYTICS_TOKEN is not set. Add it in GitHub: "
             "Settings > Secrets and variables > Actions.")
    return token


class Upstox:
    def __init__(self, token, max_calls):
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json", "Authorization": "Bearer " + token})
        self.calls, self.max_calls = 0, max_calls

    def get(self, path, params=None):
        """Returns (json, None) or (None, short error text). Never prints headers."""
        if self.calls >= self.max_calls:
            return None, "call limit for this run reached"
        url = API_BASE + "/" + path
        err = "unknown error"
        for attempt in range(RETRIES):
            self.calls += 1
            try:
                r = self.s.get(url, params=params, timeout=30)
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
                time.sleep(10 * (attempt + 1))
                continue
            break
        return None, err


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, allow_nan=False), encoding="utf-8")  # NaN impossible
    tmp.replace(path)
