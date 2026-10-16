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
import run_status
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


NOT_AVAILABLE = ("", "-", "--", "na", "n/a", "nan", "null", "none")      # the provider's own way of saying "no value" for a metric it lists


def read_ratios(body):
    """(ratios, info) for a key-ratios answer. info: usable (at least one metric with a finite number), explicit (metrics the answer LISTS with an explicit
    'no value' such as '-' or null), unreadable (metrics listed with a value that is neither a number nor such a marker) and, when not usable, reason.
    A metric the answer does not list at all is in neither list: its absence is not a statement that it has no value."""
    data = body.get("data") if isinstance(body, dict) else None
    info = {"usable": False, "explicit": [], "unreadable": [], "reason": None}
    ratios = {k: None for k in METRICS}
    if not isinstance(data, list):
        info["reason"] = "unusable response (no ratio list)"
        return ratios, info
    if not data:
        info["reason"] = "empty response (no ratios)"
        return ratios, info
    for item in data:
        if not isinstance(item, dict):
            continue
        key = RATIO_KEYS.get(str(item.get("name", "")).strip())
        if not key:
            continue
        raw = item.get("company_value")
        v = num(raw)
        if v is not None:
            ratios[key] = v
            if key in info["explicit"]:
                info["explicit"].remove(key)
        elif raw is None or (isinstance(raw, str) and raw.strip().lower() in NOT_AVAILABLE):
            if ratios[key] is None and key not in info["explicit"]:
                info["explicit"].append(key)
        elif ratios[key] is None and key not in info["unreadable"]:
            info["unreadable"].append(key)
    info["explicit"] = [m for m in METRICS if m in info["explicit"] and ratios[m] is None]
    info["unreadable"] = [m for m in METRICS if m in info["unreadable"] and ratios[m] is None]
    info["usable"] = any(v is not None for v in ratios.values())
    if not info["usable"]:
        info["reason"] = "unusable response (no metric with a value)"
    return ratios, info


def lost_metrics(before, after, explicit=()):
    """Metrics that had a value before and have none after, without the provider saying explicitly that they have no value."""
    b, a = before or {}, after or {}
    return [m for m in METRICS if b.get(m) is not None and a.get(m) is None and m not in explicit]


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


CALL_LIMIT = "call limit for this run reached"


class Upstox:
    def __init__(self, token, on_call=None):
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json",
                               "Authorization": "Bearer " + token})
        self.calls = 0
        self.on_call = on_call               # called with the new count BEFORE each attempt is sent, so a run killed mid-request is never under-counted

    def get(self, path):
        """Returns (json, None) or (None, short error text). Never prints headers. When the cap refuses a retry, the error says so AND keeps the
        real error of the attempts already made ("call limit ... (after HTTP 500)"), so the stock's failure is not mistaken for a clean cut."""
        url = API_BASE + "/" + path
        err = "unknown error"
        for attempt in range(RETRIES):
            if self.calls >= MAX_CALLS_PER_RUN:      # checked before EVERY attempt, so a retry can never take a run past its cap
                return None, CALL_LIMIT + (" (after %s)" % err if attempt else "")
            self.calls += 1
            if self.on_call:
                self.on_call(self.calls)
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


CATEGORIES = ("published_listed", "published_not_listed", "held_isin_change", "invalid", "failed", "not_in_upstox", "not_yet_fetched")


def duplicates(seq):
    seen, dup = set(), set()
    for x in seq:
        (dup if x in seen else seen).add(x)
    return sorted(dup)


def category_of(sym, stocks, published):
    """Exactly one of CATEGORIES for a universe symbol: the categories are mutually exclusive, so they add up to the universe."""
    e = stocks.get(sym) or {}
    unlisted = e.get("last_error") == NOT_FOUND_ERROR
    if sym in published:
        return "published_not_listed" if unlisted else "published_listed"
    if e.get("held_isin_change"):
        return "held_isin_change"
    if unlisted:
        return "not_in_upstox"
    if e.get("ratios_fetched") and record_problems(e):
        return "invalid"
    if e.get("last_error"):
        return "failed"
    return "not_yet_fetched"


def coverage_of(symbols, stocks, rows, tally, calls, cap, day):
    """The numbers that tell 'in the universe' apart from 'has all its metrics'. All derived from the rows and cache, never estimated.
    Every universe symbol is counted once: 'categories' is a partition of the distinct symbols (it sums to 'universe'). A stock still published
    after it left the instrument list is 'published_not_listed' and is NOT also counted under not_in_upstox."""
    distinct = list(dict.fromkeys(symbols))
    published = {r["symbol"] for r in rows}
    full = sum(1 for r in rows if not r["missing"])
    none = sum(1 for r in rows if len(r["missing"]) == len(METRICS))
    in_error = [s for s in distinct if (stocks.get(s) or {}).get("last_error") and (stocks.get(s) or {}).get("last_error") != NOT_FOUND_ERROR]
    cats = {k: 0 for k in CATEGORIES}
    for s in distinct:
        cats[category_of(s, stocks, published)] += 1
    return {
        "universe": len(distinct),
        "published": len(rows),
        "metrics_complete": full,
        "metrics_partial": len(rows) - full - none,
        "metrics_none": none,
        "metric_available": {m: sum(1 for r in rows if r.get(m) is not None) for m in METRICS},
        "not_yet_fetched": len(distinct) - len(published & set(distinct)) - cats["not_in_upstox"],   # not published and not 'not in Upstox' (incl. failed, invalid, held)
        "not_in_upstox": cats["not_in_upstox"],                                                      # not listed AND not published (a published one is published_not_listed)
        "published_not_listed": cats["published_not_listed"],
        "with_error": len(in_error),
        "categories": cats,
        "duplicate_symbols": duplicates(symbols),
        "ratios_older_than_%d_days" % RATIOS_MAX_AGE_DAYS: sum(1 for r in rows if is_stale(r.get("fetched"), RATIOS_MAX_AGE_DAYS)),
        "last_run": dict(tally, calls=calls, call_cap=cap, date=day),
        "note": COVERAGE_NOTE,
    }


# ---------------------------------------------------------------- the state of one stock's record (no network)
MAX_RETRY_DAYS = 30                 # a stock whose requests keep failing is retried after 1, 2, 4, 8, 16, then every 30 days
CHECKPOINT_EVERY = 25               # the cache is saved after every 25 processed stocks, so an interrupted run keeps its progress
UNLIMITED = 10 ** 9
MAX_STOCKS_PER_RUN = universe.budget("FUNDAMENTALS_MAX_STOCKS", UNLIMITED)   # a batch's stock limit (the workflow sets about 500); unset = only the call cap limits a run
SECONDS_PER_CALL = 2.29             # measured on the production runs of 2026-10-16 (120 calls in 275 s, 100 in 229 s): the 2 s pause plus the request itself
BATCH_ID_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
STATES = ("fresh", "stale", "missing", "invalid", "failed_due", "failed_backoff", "not_in_upstox", "isin_held")
ELIGIBLE = ("invalid", "missing", "failed_due", "stale")          # processed in this order; everything else is left alone
FINAL_OUTCOMES = ("ok", "partial", "no_data", "failed", "not_in_upstox", "skipped_fresh", "isin_held")
SUCCESS_OUTCOMES = ("ok", "partial", "no_data")                   # a request that returned data (a metric the provider left out stays null)
ATTEMPTED_OUTCOMES = SUCCESS_OUTCOMES + ("failed",)
DEFAULT_MAX_FAILURE_RATE = 0.10                                   # validation fails when more than this share of the attempted stocks failed
HELD_KEYS = ("isin", "company_name", "sector", "ratios", "ratios_fetched", "profile_fetched")   # moved aside, unchanged, when the instrument list's ISIN changes
GATE_PASSED = ("validated", "accepted")                           # the states that let the next batch start


def max_failure_rate(value=None):
    """The configured maximum failure share (0 to 1): the argument, else FUNDAMENTALS_MAX_FAILURE_RATE, else 0.10. Anything else stops the run."""
    raw = value if value is not None else (os.environ.get("FUNDAMENTALS_MAX_FAILURE_RATE") or "").strip()
    if raw in ("", None):
        return DEFAULT_MAX_FAILURE_RATE
    try:
        x = float(raw)
    except (TypeError, ValueError):
        x = -1.0
    if not (math.isfinite(x) and 0 <= x <= 1):
        fail("the maximum failure rate must be a number from 0 to 1 (got %r)." % (raw,))
    return x


# ---------------------------------------------------------------- every API attempt, persisted before it is sent (data/fundamentals_calls.jsonl)
def call_log_file():
    return CACHE_FILE.with_name("fundamentals_calls.jsonl")


def log_call(run_key, n):
    """Appends {run, calls} and forces it to disk, so a run killed in the middle of a request still shows every attempt it started."""
    f = call_log_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as h:
        h.write(json.dumps({"run": run_key, "calls": n}) + "\n")
        h.flush()
        os.fsync(h.fileno())


def logged_calls():
    """run key -> the highest attempt count persisted for it. A torn last line (killed while writing) is ignored: the line before it was already on disk."""
    out = {}
    try:
        lines = call_log_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            x = json.loads(line)
            out[str(x["run"])] = max(out.get(str(x["run"]), 0), int(x["calls"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def compact_call_log(cache, keep_daily=60):
    """Keeps the highest count of each batch run and of the last keep_daily scheduled runs (rewritten atomically)."""
    counts = logged_calls()
    if not counts:
        return
    batch_ids = set((cache.get("batches") or {}))
    daily = [k for k in counts if k.startswith("daily#")]
    keep = {k: v for k, v in counts.items() if k.split("#")[0] in batch_ids and not k.startswith("daily#")}
    keep.update({k: counts[k] for k in daily[-keep_daily:]})
    f = call_log_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps({"run": k, "calls": v}) + "\n" for k, v in keep.items()), encoding="utf-8")
    tmp.replace(f)


def batch_run_key(bid, n):
    return "%s#%d" % (bid, n)


def reconcile_calls(batch, counts=None):
    """Raises each run's recorded call count to what the call log persisted (never lowers it). Returns the runs whose record was too low."""
    counts = logged_calls() if counts is None else counts
    low = []
    for i, r in enumerate(batch.get("runs") or [], 1):
        logged = counts.get(batch_run_key(batch["id"], i), 0)
        r["calls_logged"] = logged
        if logged > int(r.get("calls") or 0):
            low.append(i)
            r["calls"] = logged
    return low


def valid_day(s, day=None):
    """A YYYY-MM-DD date that is not in the future."""
    if not isinstance(s, str):
        return False
    try:
        d = dt.date.fromisoformat(s)
    except ValueError:
        return False
    return d <= (day or dt.date.today())


def record_problems(entry, inst=None, day=None):
    """Why a cached record cannot be trusted (it is then refetched in full). [] = sound. day: the run's date (default today).
    An ISIN that differs from the instrument list is NOT a problem of this kind: see isin_conflict (the record is held aside, never refetched over)."""
    e, p = entry or {}, []
    for k in ("profile_fetched", "ratios_fetched", "last_attempt"):
        if e.get(k) is not None and not valid_day(e.get(k), day):
            p.append(k + " is not a past date")
    if e.get("ratios_fetched"):
        r = e.get("ratios")
        if not isinstance(r, dict) or set(r) != set(METRICS):
            p.append("ratios do not hold exactly the six metrics")
        elif any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)) for v in r.values()):
            p.append("a ratio is not a finite number or null")
    return p


def isin_conflict(entry, inst):
    """True when a record with data was read under an ISIN the instrument list no longer gives for the symbol."""
    e = entry or {}
    return bool(inst and e.get("isin") and e.get("ratios_fetched") and e["isin"] != inst.get("isin"))


def hold_aside(e, inst, day):
    """Moves the record's identity and data, unchanged, under held_isin_change. It is then neither published nor refetched under the new ISIN:
    which ISIN is right is a decision for a person (the instrument list is one source, not proof). Nothing is deleted."""
    if e.get("held_isin_change"):
        e["held_isin_change"]["listed_isin"] = inst.get("isin")
        return False
    e["held_isin_change"] = {"held_on": day, "isin": e.get("isin"), "listed_isin": inst.get("isin"),
                             "record": {k: e[k] for k in HELD_KEYS if k in e}}
    for k in HELD_KEYS:
        e.pop(k, None)
    return True


def restore_held(e, inst):
    """Puts a held record back when the instrument list gives its own ISIN again. True when restored."""
    h = e.get("held_isin_change")
    if not h or not inst or inst.get("isin") != h.get("isin"):
        return False
    e.update(h.get("record") or {})
    e.pop("held_isin_change", None)
    return True


def retry_days(failures):
    return min(2 ** (max(1, int(failures or 1)) - 1), MAX_RETRY_DAYS)


def in_backoff(entry, day=None):
    e = entry or {}
    day = day or dt.date.today()
    if not e.get("last_error") or e.get("last_error") == NOT_FOUND_ERROR or not valid_day(e.get("last_attempt"), day):
        return False
    since = (day - dt.date.fromisoformat(e["last_attempt"])).days
    return since < retry_days(e.get("failures"))


def state_of(entry, inst, day=None):
    """One of STATES. inst: the stock's instrument-list entry, or None when Upstox does not list it."""
    e = entry or {}
    if inst is None:
        return "not_in_upstox"
    if (e.get("held_isin_change") and inst.get("isin") != e["held_isin_change"].get("isin")) or isin_conflict(e, inst):
        return "isin_held"
    failed = bool(e.get("last_error")) and e.get("last_error") != NOT_FOUND_ERROR
    if failed and in_backoff(e, day):                # checked before 'invalid': an invalid record whose refetch failed waits out its back-off too
        return "failed_backoff"
    if record_problems(e, inst, day):
        return "invalid"
    if not e.get("ratios_fetched"):
        return "failed_due" if failed else "missing"
    if is_stale(e.get("ratios_fetched"), RATIOS_MAX_AGE_DAYS) or is_stale(e.get("profile_fetched"), PROFILE_MAX_AGE_DAYS):
        return "failed_due" if failed else "stale"
    return "fresh"


def quality_of(entry):
    """'complete' (all six metrics), 'partial', 'none' (fetched, no metric) or None (never fetched)."""
    e = entry or {}
    if not e.get("ratios_fetched"):
        return None
    m = missing_of(e.get("ratios"))
    return "complete" if not m else ("none" if len(m) == len(METRICS) else "partial")


def priority(stocks, symbols, instruments, day=None):
    """The eligible symbols in processing order: invalid, missing, failed (due), stale; inside each group the stalest first, ties alphabetical."""
    by = {s: state_of(stocks.get(s), instruments.get(s), day) for s in symbols}
    out = []
    for st in ELIGIBLE:
        group = [s for s in symbols if by[s] == st]
        out += universe.order_stalest(group, {s: staleness_key(stocks.get(s)) for s in group})
    return out, by


def calls_needed(entry, inst, day=None):
    """Requests this stock needs now, as the updater would make them (1 attempt each; retries come on top)."""
    st = state_of(entry, inst, day)
    if st not in ELIGIBLE:
        return 0
    e = entry or {}
    if st == "invalid":
        return 2
    return int(is_stale(e.get("profile_fetched"), PROFILE_MAX_AGE_DAYS)) + int(is_stale(e.get("ratios_fetched"), RATIOS_MAX_AGE_DAYS))


# ---------------------------------------------------------------- batches: plan, checkpoint, resume, gate
def check_batch_id(bid):
    if not bid or len(bid) > 32 or set(bid) - BATCH_ID_OK:
        fail("FUNDAMENTALS_BATCH_ID must be 1 to 32 letters, digits, '-' or '_'.")
    return bid


def open_batch(cache, bid, eligible, day, published_now=()):
    """The batch record to work on, or None when a new id would plan nothing (no empty batch is created: it could never show a successful update).
    A new id is planned (the next MAX_STOCKS_PER_RUN eligible stocks) only when every earlier batch was VALIDATED or explicitly ACCEPTED
    (python fundamentals_updater.py accept ...); there is no blanket override. An id that is running or was interrupted resumes with its remaining
    planned stocks; any other id is never run again. published_now: the symbols published when the batch is planned (kept, so validation can prove none is lost)."""
    batches = cache.setdefault("batches", {})
    b = batches.get(bid)
    if b is None:
        open_ = [k for k, v in batches.items() if v.get("status") not in GATE_PASSED]
        if open_:
            fail("batch %s cannot start: batch %s is %s, not validated or accepted. Validate it (python fundamentals_updater.py validate --batch %s) "
                 "or accept it explicitly (accept --batch %s --by NAME --reason TEXT)." % (bid, open_[-1], batches[open_[-1]].get("status"), open_[-1], open_[-1]))
        if not eligible:
            log("Batch %s not created: no stock is eligible (nothing missing, stale, invalid or due for a retry)." % bid)
            return None
        planned = eligible[:MAX_STOCKS_PER_RUN]
        stocks = cache.get("stocks") or {}
        baseline = {}                         # the published values of the planned stocks before the batch: validation compares against them
        for s in planned:
            e = stocks.get(s) or {}
            if s in published_now:
                h = e.get("publish_hold")
                baseline[s] = {"ratios": dict((h or {}).get("ratios") or e.get("ratios") or {}), "fetched": (h or {}).get("fetched") or e.get("ratios_fetched")}
        b = batches[bid] = {"id": bid, "created": day, "seq": len(batches) + 1, "status": "running", "max_stocks": MAX_STOCKS_PER_RUN, "max_calls": MAX_CALLS_PER_RUN,
                            "planned": planned, "outcomes": {}, "runs": [], "published_before": sorted(published_now), "baseline": baseline}
        log("Batch %s planned: %d stocks (of %d eligible)" % (bid, len(b["planned"]), len(eligible)))
    elif b.get("status") in ("running", "interrupted"):
        low = reconcile_calls(b)
        if low:
            log("Batch %s: run(s) %s had recorded fewer calls than they made (interrupted); corrected from the call log." % (bid, low))
        log("Batch %s resumes: %d of %d planned stocks still to do" % (bid, sum(1 for s in b["planned"] if b["outcomes"].get(s) not in FINAL_OUTCOMES), len(b["planned"])))
        b["status"] = "running"
    else:
        fail("batch %s is already %s; use a new batch id." % (bid, b.get("status")))
    b["runs"].append({"day": day, "calls": 0, "cap": MAX_CALLS_PER_RUN})
    return b


def checkpoint(cache):
    write_json(CACHE_FILE, cache)


# ---------------------------------------------------------------- the run
def main(argv=None):
    argv = list(argv or [])
    if argv[:1] == ["report"]:
        return report_main(argv[1:])
    if argv[:1] == ["validate"]:
        return validate_main(argv[1:])
    if argv[:1] == ["accept"]:
        return accept_main(argv[1:])
    token = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
    if not token:
        fail("UPSTOX_ANALYTICS_TOKEN is not set. Add it in GitHub: "
             "Settings > Secrets and variables > Actions.")
    bid = (os.environ.get("FUNDAMENTALS_BATCH_ID") or "").strip()
    if bid:
        check_batch_id(bid)

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    stocks = cache.setdefault("stocks", {})

    instruments = load_instruments()
    log("NSE equity instruments loaded:", len(instruments))
    api = Upstox(token)
    day_d = dt.date.today()
    day = day_d.isoformat()

    try:
        symbols = research_test_batch.extend(universe.load())      # + the five-stock test batch (see research_test_batch.py); nothing else changes
    except universe.UniverseError as err:
        fail(str(err))
    dup = duplicates(symbols)
    if dup:
        fail("the universe lists %d symbol(s) more than once (%s): each stock must be counted once. Nothing was changed." % (len(dup), ", ".join(dup[:10])))
    compact_call_log(cache)
    published_at_start = {r["symbol"] for r in build_rows(symbols, stocks)[0]}
    tally = {"processed": 0, "succeeded": 0, "partial": 0, "failed": 0, "up_to_date": 0, "not_in_upstox": 0, "not_reached": 0, "in_backoff": 0, "invalid_refetched": 0,
             "isin_held": 0}
    for sym in symbols:                      # the instrument list is local data: classifying costs no API call, so it is done for every stock even after the budget is spent
        m = instruments.get(sym)
        if not m:
            e = stocks.setdefault(sym, {})
            e["last_error"] = NOT_FOUND_ERROR
            e.setdefault("not_listed_since", day)     # a published stock keeps its data (published_not_listed); this says since when it is not listed
            tally["not_in_upstox"] += 1
            continue
        e = stocks.get(sym)
        if e is None:
            continue
        if e.get("last_error") == NOT_FOUND_ERROR:
            e["last_error"] = None               # Upstox lists it now: a stale "not found" must not outlive the listing, even if the budget is spent before it is fetched
        e.pop("not_listed_since", None)
        e["listed_isin"] = m["isin"]             # the ISIN the instrument list gave on this run: validation compares the published row with it
        if restore_held(e, m):
            log(sym, "- held record restored: the instrument list gives its ISIN %s again" % e.get("isin"))
        elif isin_conflict(e, m):
            hold_aside(e, m, day)
            log(sym, "- held aside: read under ISIN %s, the instrument list now gives %s. Kept unchanged and not published; not refetched until a person decides."
                % (e["held_isin_change"]["isin"], m["isin"]))
        elif e.get("isin") and e["isin"] != m["isin"] and not e.get("held_isin_change"):
            e.setdefault("isin_history", []).append({"day": day, "from": e["isin"], "to": m["isin"]})   # no data under the old ISIN: nothing to hold, the change is recorded
            e.pop("isin", None)
        if e.get("held_isin_change"):
            e["held_isin_change"]["listed_isin"] = m["isin"]
            tally["isin_held"] += 1
    eligible, by = priority(stocks, symbols, instruments, day_d)
    tally["in_backoff"] = sum(1 for s in symbols if by[s] == "failed_backoff")
    tally["up_to_date"] = sum(1 for s in symbols if by[s] == "fresh")
    batch = open_batch(cache, bid, eligible, day, published_at_start) if bid else None
    run_key = batch_run_key(bid, len(batch["runs"])) if batch else "daily#%s#%d" % (day, int(time.time() * 1000))

    def on_call(n):                          # persisted BEFORE the request is sent
        log_call(run_key, n)
        if batch:
            batch["runs"][-1]["calls"] = n
    api.on_call = on_call
    work = [s for s in batch["planned"] if batch["outcomes"].get(s) not in FINAL_OUTCOMES] if batch else ([] if bid else eligible[:MAX_STOCKS_PER_RUN])
    log("Universe:", len(symbols), "stocks | eligible (missing, stale, invalid or failed and due):", len(eligible),
        "| this run:", len(work), "| call budget:", MAX_CALLS_PER_RUN, "| batch:", bid or "none")

    pending, done_since_checkpoint = 0, 0
    for sym in work:
        m = instruments.get(sym)
        e = stocks.setdefault(sym, {})
        if not m:
            if batch:
                batch["outcomes"][sym] = "not_in_upstox"
            continue
        st = state_of(e, m, day_d)
        if st not in ELIGIBLE:                 # became fresh or backed off since the batch was planned (another run did it), or held aside: nothing to do
            if batch:
                batch["outcomes"][sym] = {"fresh": "skipped_fresh", "isin_held": "isin_held"}.get(st) or batch["outcomes"].get(sym, "not_reached")
            continue
        if api.calls >= MAX_CALLS_PER_RUN:
            pending += 1                       # not reached this run: not an error, it keeps its previous data and goes first next time
            if batch:
                batch["outcomes"][sym] = "not_reached"
            continue
        force = st == "invalid"
        if force:
            tally["invalid_refetched"] += 1
        e["isin"], e["company_name"] = m["isin"], m["name"]   # same ISIN as the record's (a different one was held aside above, never overwritten here)
        e["listed_isin"] = m["isin"]
        errs, calls_before, ratios_now, limit_hit = [], api.calls, False, False
        history = {k: e.get(k) for k in ("last_error", "failures", "last_attempt")}

        if force or is_stale(e.get("profile_fetched"), PROFILE_MAX_AGE_DAYS):
            body, err = api.get(m["isin"] + "/profile")
            if err:
                if err.startswith(CALL_LIMIT):   # running out of budget mid-stock is not a data problem
                    limit_hit = True
                else:
                    errs.append("profile: " + err)
            else:
                sector = (body.get("data") or {}).get("sector")
                e["sector"] = sector.strip() if isinstance(sector, str) and sector.strip() else None
                e["profile_fetched"] = day

        if not limit_hit and (force or is_stale(e.get("ratios_fetched"), RATIOS_MAX_AGE_DAYS)):
            body, err = api.get(m["isin"] + "/key-ratios")
            if err:
                if err.startswith(CALL_LIMIT):
                    limit_hit = True
                else:
                    errs.append("key-ratios: " + err)
            else:
                # A 200 answer replaces the stored ratios only when it is usable and does not silently lose a metric the record had (or that a
                # publication hold still shows). Otherwise it is a FAILED refresh: the previous ratios and their date stay, and the back-off applies.
                ratios, info = read_ratios(body)
                hold = e.get("publish_hold") or {}
                known = hold.get("ratios") if hold else (e.get("ratios") if e.get("ratios_fetched") and not record_problems(e) else None)
                lost = lost_metrics(known, ratios, info["explicit"])
                if not info["usable"]:
                    errs.append("key-ratios: " + info["reason"])
                elif lost:
                    errs.append("key-ratios: answer drops previously available metric(s) %s without saying they have no value" % ", ".join(lost))
                else:
                    e["ratios"] = ratios
                    e["ratios_fetched"] = day
                    e["explicit_unavailable"] = info["explicit"]    # metrics the provider said have no value (validation accepts these as understood changes)
                    e.pop("publish_hold", None)                     # a sound refresh supersedes a publication hold
                    ratios_now = True

        # An invalid record that could not be refetched stays as it was: build_rows never publishes it (record_problems), and nothing is deleted.
        if errs:
            e["last_error"] = "; ".join(errs)
            e["last_attempt"] = day              # drives the back-off: only a FAILED attempt moves a stock back
            e["failures"] = int(e.get("failures") or 0) + 1
        elif limit_hit:                          # the cap stopped this stock before a result: its failure history and back-off stay exactly as they were
            e.update(history)
            after = err[len(CALL_LIMIT):].strip(" ()")
            e["last_cut"] = {"day": day, "by": "call cap", "after": after[len("after "):] if after.startswith("after ") else (after or None)}
        else:
            e["last_error"] = None
            if api.calls > calls_before:
                e["last_attempt"] = day
            e["failures"] = 0
        if errs:
            tally["failed"] += 1
            outcome = "failed"
        elif limit_hit or state_of(e, m, day_d) in ELIGIBLE:
            pending += 1                         # the budget ran out in the middle of this stock
            outcome = "not_reached"
        elif ratios_now and quality_of(e) == "none":
            tally["partial"] += 1
            outcome = "no_data"
        elif ratios_now and missing_of(e.get("ratios")):
            tally["partial"] += 1
            outcome = "partial"
        else:
            tally["succeeded"] += 1
            outcome = "ok"
        if batch:
            batch["outcomes"][sym] = outcome
        log(sym, "- ok" if not errs else "- problem: " + e["last_error"])
        done_since_checkpoint += 1
        if done_since_checkpoint >= CHECKPOINT_EVERY:
            checkpoint(cache)                  # an interrupted run (timeout, cancel) keeps everything up to here; its calls are in the call log already
            done_since_checkpoint = 0

    tally["not_reached"] = pending
    tally["processed"] = tally["succeeded"] + tally["partial"] + tally["failed"]
    if pending:
        log("Not reached in this run (call budget):", pending, "stock(s)")
    if batch:
        batch["runs"][-1]["calls"] = api.calls
        batch["status"] = "completed" if all(batch["outcomes"].get(s) in FINAL_OUTCOMES for s in batch["planned"]) else "interrupted"
        log("Batch %s %s: %s" % (bid, batch["status"], json.dumps(dict(sorted(_count_outcomes(batch).items())))))
    rows, errors, dates = build_rows(symbols, stocks)
    write_json(CACHE_FILE, cache)
    if not rows:
        fail("No fundamentals could be fetched. The previous out/fundamentals.json (if any) was kept.")
    cov = coverage_of(symbols, stocks, rows, tally, api.calls, MAX_CALLS_PER_RUN, today())
    cov["states"] = {k: sum(1 for s in symbols if state_of(stocks.get(s), instruments.get(s), day_d) == k) for k in STATES}
    write_json(OUT_FILE, {"as_of": max(dates), "source": "Upstox", "stocks": rows, "errors": errors, "coverage": cov})
    log("Wrote", OUT_FILE, "-", len(rows), "stocks,", len(errors), "with problems, API calls:", api.calls)
    run_status.write("fundamentals", tally, calls=api.calls, call_cap=MAX_CALLS_PER_RUN, published=len(rows))
    log("Coverage: universe %d | published %d (all metrics %d, some missing %d, none %d) | not yet fetched %d | not in Upstox %d | with error %d" % (
        cov["universe"], cov["published"], cov["metrics_complete"], cov["metrics_partial"], cov["metrics_none"], cov["not_yet_fetched"], cov["not_in_upstox"], cov["with_error"]))
    log("This run: processed %d (succeeded %d, partial %d, failed %d) | already fresh %d | backing off %d | not reached (budget) %d | calls %d of %d" % (
        tally["processed"], tally["succeeded"], tally["partial"], tally["failed"], tally["up_to_date"], tally["in_backoff"], tally["not_reached"], api.calls, MAX_CALLS_PER_RUN))
    write_report(symbols, stocks, instruments, day_d, batch)
    return 0


def _count_outcomes(batch):
    c = {}
    for s in batch["planned"]:
        o = batch["outcomes"].get(s, "pending")
        c[o] = c.get(o, 0) + 1
    return c


def build_rows(symbols, stocks):
    rows, errors, dates = [], [], []
    for sym in symbols:
        e = stocks.get(sym, {})
        if e.get("last_error"):
            errors.append({"symbol": sym, "error": e["last_error"]})
        if not e.get("isin") or not e.get("ratios_fetched") or record_problems(e):
            continue                       # never publish a stock we have no real (or no sound) ratios for
        hold = e.get("publish_hold")
        if hold:                           # a batch that failed validation for lost metrics: the site keeps the values from before it, with their own date
            r, fetched = hold.get("ratios") or {}, hold.get("fetched") or e["ratios_fetched"]
        else:
            r, fetched = e.get("ratios") or {}, e["ratios_fetched"]
        dates.append(fetched)
        rows.append({
            "symbol": sym, "isin": e["isin"], "company_name": e.get("company_name"),
            "sector": e.get("sector") if sym in universe.TEST_SYMBOLS else None,   # sector stays private for the wider universe until the sector approval gate is opened
            "pe": r.get("pe"), "pb": r.get("pb"), "roa": r.get("roa"), "roe": r.get("roe"),
            "roce": r.get("roce"), "ev_ebitda": r.get("ev_ebitda"),
            "revenue_growth": None, "profit_growth": None,   # not fetched in this version
            "missing": missing_of(r),                         # the ratio metrics Upstox did not return for this stock (null above); [] = all six present
            "fetched": fetched,                               # the day these ratios were read
        })
    return rows, errors, dates


# ---------------------------------------------------------------- the coverage report (after every run; also offline, with no API call)
def coverage_report(symbols, stocks, instruments, day, batch=None):
    """Exact counts and complete symbol lists by state, metric and quality. instruments=None (offline report): a stock is 'not_in_upstox' only when a run
    recorded it; otherwise its listing is unknown and it is judged by its record alone."""
    def inst(s):
        if instruments is not None:
            return instruments.get(s)
        e = stocks.get(s) or {}
        return None if e.get("last_error") == NOT_FOUND_ERROR else {"isin": e.get("listed_isin") or e.get("isin")}   # the list's ISIN as the last run saw it
    states = {k: [] for k in STATES}
    for s in symbols:
        states[state_of(stocks.get(s), inst(s), day)].append(s)
    quality = {"complete": [], "partial": [], "none": []}
    for s in symbols:
        q = quality_of(stocks.get(s))
        if q and not record_problems(stocks.get(s)):
            quality[q].append(s)
    metric_missing = {m: sorted(s for s in quality["partial"] + quality["none"] if (stocks[s].get("ratios") or {}).get(m) is None) for m in METRICS}
    ages = {"0-6": 0, "7-13": 0, "14-29": 0, "30+": 0}
    for s in symbols:
        f = (stocks.get(s) or {}).get("ratios_fetched")
        if valid_day(f, day):
            a = (day - dt.date.fromisoformat(f)).days
            ages["0-6" if a < 7 else "7-13" if a < 14 else "14-29" if a < 30 else "30+"] += 1
    nxt, _ = priority(stocks, symbols, {s: inst(s) for s in symbols if inst(s)}, day)
    calls = [calls_needed(stocks.get(s), inst(s), day) for s in nxt]
    rep = {"kind": "fundamentals_coverage", "as_of": day.isoformat(), "universe": len(symbols), "offline": instruments is None,
           "states": {k: len(v) for k, v in states.items()}, "quality": {k: len(v) for k, v in quality.items()},
           "metric_available": {m: len(quality["complete"]) + len(quality["partial"]) + len(quality["none"]) - len(metric_missing[m]) for m in METRICS},
           "ratio_age_days": ages,
           "next": {"eligible": len(nxt), "calls_nominal": sum(calls), "calls_worst_case": sum(calls) * RETRIES,
                    "minutes_nominal": round(sum(calls) * SECONDS_PER_CALL / 60, 1),
                    "batches_of_500": -(-len(nxt) // 500) if nxt else 0},
           "lists": {"states": states, "quality": quality, "metric_missing": metric_missing},
           "note": "Exact counts from the cache. 'fresh' = ratios younger than %d days and profile younger than %d days. A metric the provider did not return is null and "
                   "listed under metric_missing; nothing is estimated. Calls are 1 attempt per request; a retry (429, 5xx, network) costs up to %d attempts. "
                   "'categories' counts every distinct universe symbol exactly once and sums to 'universe'."
                   % (RATIOS_MAX_AGE_DAYS, PROFILE_MAX_AGE_DAYS, RETRIES)}
    published = {r["symbol"] for r in build_rows(symbols, stocks)[0]}
    cats = {k: [] for k in CATEGORIES}
    for s in dict.fromkeys(symbols):
        cats[category_of(s, stocks, published)].append(s)
    rep["categories"] = {k: len(v) for k, v in cats.items()}
    rep["duplicate_symbols"] = duplicates(symbols)
    rep["lists"]["categories"] = cats
    rep["lists"]["published_not_listed"] = {s: {"fetched": stocks[s].get("ratios_fetched"), "not_listed_since": stocks[s].get("not_listed_since")} for s in cats["published_not_listed"]}
    rep["isin_notices"] = isin_notices(stocks, set(symbols), _universe_isins())
    if batch:
        reconcile_calls(batch)
        rep["batch"] = {"id": batch["id"], "status": batch["status"], "planned": len(batch["planned"]), "outcomes": _count_outcomes(batch),
                        "calls": sum(r.get("calls", 0) for r in batch.get("runs", [])), "runs": len(batch.get("runs", [])),
                        "failed": sorted(s for s in batch["planned"] if batch["outcomes"].get(s) == "failed"),
                        "not_reached": sorted(s for s in batch["planned"] if batch["outcomes"].get(s) in (None, "not_reached"))}
        if batch.get("validation"):
            rep["batch"]["validation"] = batch["validation"]
        if batch.get("acceptance"):
            rep["batch"]["acceptance"] = batch["acceptance"]
    return rep


def report_markdown(rep):
    st, q, n = rep["states"], rep["quality"], rep["next"]
    lines = ["### Fundamentals coverage (%s%s)" % (rep["as_of"], ", offline" if rep["offline"] else ""), "",
             "| Universe | Fresh | Stale | Missing | Invalid | Failed (due) | Failed (backing off) | Not in Upstox |", "|---|---|---|---|---|---|---|---|",
             "| %d | %d | %d | %d | %d | %d | %d | %d |" % (rep["universe"], st["fresh"], st["stale"], st["missing"], st["invalid"], st["failed_due"], st["failed_backoff"], st["not_in_upstox"]),
             "", "With ratios: %d complete, %d partial, %d with no metric. Metrics available: %s." % (
                 q["complete"], q["partial"], q["none"], ", ".join("%s %d" % (k, v) for k, v in rep["metric_available"].items())),
             "", "Next: %d eligible stocks, about %d calls (up to %d with retries), about %.0f minutes; %d batch(es) of 500." % (
                 n["eligible"], n["calls_nominal"], n["calls_worst_case"], n["minutes_nominal"], n["batches_of_500"])]
    c = rep.get("categories")
    if c:
        lines += ["", "Each stock once (sums to %d): %s." % (sum(c.values()), ", ".join("%s %d" % (k.replace("_", " "), v) for k, v in c.items()))]
    held = (rep.get("isin_notices") or {}).get("held_isin_change") or {}
    if held:
        lines += ["", "Held aside after an ISIN change (not published, nothing deleted; a person must decide): " + ", ".join(
            "%s (%s, list now %s)" % (s, v["isin"], v["listed_isin"]) for s, v in sorted(held.items()))]
    b = rep.get("batch")
    if b:
        lines += ["", "Batch %s: %s - %d planned, outcomes %s, %d calls in %d run(s)." % (b["id"], b["status"], b["planned"], json.dumps(b["outcomes"], sort_keys=True), b["calls"], b["runs"])]
        if b.get("validation"):
            lines += ["Validation: %s%s" % (b["validation"]["result"], "" if not b["validation"]["problems"] else " - " + "; ".join(b["validation"]["problems"][:10]))]
        if b.get("acceptance"):
            a = b["acceptance"]
            lines += ["Accepted on %s by %s (was %s): %s" % (a["day"], a["by"], a["status_before"], a["reason"])]
    return "\n".join(lines) + "\n"


def write_report(symbols, stocks, instruments, day, batch=None):
    rep = coverage_report(symbols, stocks, instruments, day, batch)
    d = (os.environ.get("FUNDAMENTALS_REPORT_DIR") or "").strip()
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        write_json(Path(d) / "fundamentals_coverage.json", rep)
        (Path(d) / "fundamentals_coverage.md").write_text(report_markdown(rep), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report_markdown(rep))
    return rep


def _load_for_offline():
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    try:
        symbols = research_test_batch.extend(universe.load())
    except universe.UniverseError as err:
        fail(str(err))
    return cache, cache.setdefault("stocks", {}), symbols


def report_main(argv):
    """python fundamentals_updater.py report  -  the coverage report from the cache alone: no token, no instrument file, no API call."""
    cache, stocks, symbols = _load_for_offline()
    batches = cache.get("batches") or {}
    last = batches[max(batches, key=lambda k: batches[k].get("seq", 0))] if batches else None
    rep = write_report(symbols, stocks, None, dt.date.today(), last)
    print(report_markdown(rep))
    return 0


# ---------------------------------------------------------------- validation: a batch must pass before the next one may start
def isin_notices(stocks, symbols, universe_isins=None):
    """Informational (never a validation failure): records held aside after an ISIN change, and published ISINs that differ from the universe file
    (the stock directory is a dated snapshot, so a difference there is reported for a person to check, not acted on)."""
    held = {s: {"held_on": e["held_isin_change"].get("held_on"), "isin": e["held_isin_change"].get("isin"), "listed_isin": e["held_isin_change"].get("listed_isin")}
            for s, e in stocks.items() if (symbols is None or s in symbols) and e.get("held_isin_change")}
    differs = {}
    for s, isin in (universe_isins or {}).items():
        e = stocks.get(s) or {}
        if e.get("isin") and isin and e["isin"] != isin:
            differs[s] = {"record_isin": e["isin"], "universe_isin": isin}
    return {"held_isin_change": held, "isin_differs_from_universe": differs}


def validate_batch(cache, batch, out_doc, previous_out=None, symbols=None, failure_rate=None, call_counts=None):
    """Problems with a finished batch (an empty list = valid). Checks every planned stock's outcome against its record, that no failure is recorded as a
    success, the failure rate (a batch with no successful update never passes), ISIN identity (published row = cache = instrument list of the run),
    that the published file is sound, counts each symbol once and matches the cache, that no stock published before has disappeared, and that every
    run stayed within its cap counting EVERY persisted attempt."""
    p, stocks = [], cache.get("stocks") or {}
    rate = max_failure_rate(failure_rate)
    if batch.get("status") not in ("completed", "failed_validation"):
        p.append("batch status is %s, not completed (resume it first)" % batch.get("status"))
    first_day = min(r["day"] for r in batch.get("runs") or [{"day": batch.get("created")}])
    planned = batch.get("planned", [])
    if duplicates(planned):
        p.append("the batch plans a stock more than once: %s" % ", ".join(duplicates(planned)[:10]))
    for s in planned:
        o, e = batch.get("outcomes", {}).get(s), stocks.get(s) or {}
        if o not in FINAL_OUTCOMES:
            p.append("%s: no final outcome (%s)" % (s, o))
        elif o in SUCCESS_OUTCOMES:
            if e.get("last_error"):
                p.append("%s: recorded as %s but its last request failed" % (s, o))
            if not e.get("ratios_fetched") or e["ratios_fetched"] < first_day:
                p.append("%s: recorded as %s but its ratios were not fetched in this batch" % (s, o))
            if record_problems(e):
                p.append("%s: %s" % (s, "; ".join(record_problems(e))))
            want = {"ok": "complete", "partial": "partial", "no_data": "none"}[o]
            if quality_of(e) != want:
                p.append("%s: recorded as %s but the ratios are %s" % (s, o, quality_of(e)))
        elif o == "failed":
            if not e.get("last_error") or not int(e.get("failures") or 0):
                p.append("%s: recorded as failed without a recorded error" % s)
        elif o == "isin_held" and not e.get("held_isin_change"):
            p.append("%s: recorded as held aside but no held record exists" % s)
    oc = _count_outcomes(batch)
    attempted = sum(oc.get(k, 0) for k in ATTEMPTED_OUTCOMES)
    succeeded = sum(oc.get(k, 0) for k in SUCCESS_OUTCOMES)
    if succeeded == 0:
        p.append("no successful update: %d planned, %d attempted, %d failed" % (len(planned), attempted, oc.get("failed", 0)))
    elif attempted and oc.get("failed", 0) / attempted > rate:
        p.append("failure rate %.1f%% (%d of %d attempted) is above the maximum of %.1f%%" % (100.0 * oc["failed"] / attempted, oc["failed"], attempted, 100 * rate))
    if not isinstance(out_doc, dict):
        p.append("the published fundamentals.json is missing or unreadable")
        return p
    try:
        import validate_outputs
        p += ["fundamentals.json: " + x for x in validate_outputs.check_fundamentals(out_doc, symbols)]
    except ImportError:
        pass
    row_list = out_doc.get("stocks") or []
    rows = {r.get("symbol"): r for r in row_list}
    for s, r in rows.items():
        e = stocks.get(s) or {}
        h = e.get("publish_hold")
        want_r, want_f = ((h.get("ratios") or {}), h.get("fetched")) if h else ((e.get("ratios") or {}), e.get("ratios_fetched"))
        if r.get("fetched") != want_f or any(r.get(m) != want_r.get(m) for m in METRICS):
            p.append("%s: the published row does not match the cache" % s)
        if r.get("isin") != e.get("isin"):
            p.append("%s: published under ISIN %s but the cache holds %s" % (s, r.get("isin"), e.get("isin")))
        elif e.get("listed_isin") and e.get("last_error") != NOT_FOUND_ERROR and r.get("isin") != e["listed_isin"]:
            p.append("%s: published under ISIN %s but the instrument list of the last run gives %s (it should have been held aside)" % (s, r.get("isin"), e["listed_isin"]))
        if e.get("held_isin_change"):
            p.append("%s: published although its record is held aside" % s)
    for s, e in stocks.items():
        if (symbols is None or s in symbols) and e.get("isin") and e.get("ratios_fetched") and not record_problems(e) and s not in rows:
            p.append("%s: has sound ratios but is not published" % s)
    held = {s for s, e in stocks.items() if e.get("held_isin_change")}
    before = set(batch.get("published_before") or [])
    if isinstance(previous_out, dict):
        before |= {r.get("symbol") for r in previous_out.get("stocks") or []}
    elif "published_before" not in batch and rows:
        p.append("no record of what was published before the batch: preservation cannot be checked")
    gone = sorted(before - set(rows) - held)
    if gone:
        p.append("%d stock(s) published before are gone: %s" % (len(gone), ", ".join(gone[:10])))
    for s, v in sorted(metric_losses(batch, stocks, out_doc, previous_out).items()):
        p.append("%s: metric(s) %s disappeared from the published row without the provider saying they have no value" % (s, ", ".join(v["lost"])))
    counts = logged_calls() if call_counts is None else call_counts
    reconcile_calls(batch, counts)
    for i, r in enumerate(batch.get("runs") or [], 1):
        if r.get("calls", 0) > r.get("cap", batch.get("max_calls", MAX_CALLS_PER_RUN)):
            p.append("run %d of the batch used %d calls, more than its cap of %d" % (i, r.get("calls", 0), r.get("cap", 0)))
    return p


def metric_losses(batch, stocks, out_doc, previous_out=None):
    """{symbol: {lost, ratios, fetched}} for published rows that lost a metric compared with the values before the batch (the batch's own baseline,
    else the previously published file). A metric the provider explicitly listed as having no value in a refresh made after the baseline is an
    understood change and is not counted; neither is a metric that was already null."""
    base = {s: dict(v) for s, v in (batch.get("baseline") or {}).items()}
    if isinstance(previous_out, dict):
        for r in previous_out.get("stocks") or []:
            sym = r.get("symbol")
            if sym and sym not in base:
                base[sym] = {"ratios": {m: r.get(m) for m in METRICS}, "fetched": r.get("fetched")}
    rows = {r.get("symbol"): r for r in (out_doc or {}).get("stocks") or [] if isinstance(r, dict)}
    out = {}
    for sym, b in base.items():
        r = rows.get(sym)
        if r is None:
            continue                         # a disappearance is checked separately
        e = stocks.get(sym) or {}
        explicit = (e.get("explicit_unavailable") or []) if (e.get("ratios_fetched") or "") > (b.get("fetched") or "") else []
        lost = lost_metrics(b.get("ratios"), {m: r.get(m) for m in METRICS}, explicit)
        if lost:
            out[sym] = {"lost": lost, "ratios": dict(b.get("ratios") or {}), "fetched": b.get("fetched")}
    return out


def rebuild_output(symbols, stocks, out_doc):
    """Rewrites out/fundamentals.json from the cache (publication holds applied), keeping the last run's counts. No API call."""
    rows, errors, dates = build_rows(symbols, stocks)
    prev = (out_doc or {}).get("coverage") or {}
    lr = prev.get("last_run") or {}
    tally = {k: v for k, v in lr.items() if k not in ("calls", "call_cap", "date")}
    cov = coverage_of(symbols, stocks, rows, tally, lr.get("calls", 0), lr.get("call_cap", MAX_CALLS_PER_RUN), lr.get("date", today()))
    if "states" in prev:
        cov["states"] = prev["states"]
    write_json(OUT_FILE, {"as_of": max(dates) if dates else (out_doc or {}).get("as_of"), "source": (out_doc or {}).get("source", "Upstox"),
                          "stocks": rows, "errors": errors, "coverage": cov})


def set_holds(stocks, bid, losses, day):
    """Failed validation: the stocks that lost metrics are published with their values from before the batch. Returns True when anything changed."""
    changed = False
    for sym, v in losses.items():
        e = stocks.get(sym)
        if e is not None and v.get("fetched"):
            e["publish_hold"] = {"batch": bid, "since": day, "lost": v["lost"], "ratios": v["ratios"], "fetched": v["fetched"]}
            changed = True
    return changed


def clear_holds(stocks, bid):
    changed = False
    for e in stocks.values():
        if (e.get("publish_hold") or {}).get("batch") == bid:
            e.pop("publish_hold", None)
            changed = True
    return changed


def _universe_isins():
    try:
        return {s: x.get("isin") for s, x in universe.load_entries().items()}
    except Exception:                    # noqa: BLE001 - informational only: the 10 development stocks have no universe file
        return {}


def _opts(argv, names):
    out, it = {}, iter(argv)
    for a in it:
        if a in names:
            out[a.lstrip("-").replace("-", "_")] = next(it, None)
        else:
            fail("unknown option %s" % a)
    return out


def validate_main(argv):
    """python fundamentals_updater.py validate --batch ID [--previous PATH] [--max-failure-rate X]  -  marks the batch validated or failed_validation
    (no API call); exit 1 on failure. A batch that failed validation may be validated again (for example after a mistake in the inputs was corrected)."""
    o = _opts(argv, ("--batch", "--previous", "--max-failure-rate"))
    bid, prev = o.get("batch"), o.get("previous")
    check_batch_id(bid)
    rate = max_failure_rate(o.get("max_failure_rate"))
    cache, stocks, symbols = _load_for_offline()
    batch = (cache.get("batches") or {}).get(bid)
    if not batch:
        fail("batch %s does not exist" % bid)
    if batch.get("status") in GATE_PASSED:     # already decided (e.g. the same id was dispatched again and refused): its record is left exactly as it is
        log("Batch %s is already %s; nothing to validate and nothing changed." % (bid, batch["status"]))
        return 0
    try:
        out_doc = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out_doc = None
    previous = None
    if prev:
        try:
            previous = json.loads(Path(prev).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    problems = validate_batch(cache, batch, out_doc, previous, set(symbols), rate)
    notices = isin_notices(stocks, set(symbols), _universe_isins())
    batch["validation"] = {"day": today(), "result": "passed" if not problems else "failed", "problems": problems[:50], "problem_count": len(problems),
                           "max_failure_rate": rate, "outcomes": _count_outcomes(batch),
                           "notices": {"held_isin_change": sorted(notices["held_isin_change"]), "isin_differs_from_universe": sorted(notices["isin_differs_from_universe"])}}
    if batch.get("status") in ("completed", "failed_validation"):
        batch["status"] = "validated" if not problems else "failed_validation"
    losses = metric_losses(batch, stocks, out_doc, previous)
    changed = set_holds(stocks, bid, losses, today()) if problems else clear_holds(stocks, bid)
    if losses and problems:
        batch["validation"]["publish_holds"] = sorted(losses)
    write_json(CACHE_FILE, cache)
    if changed and isinstance(out_doc, dict):
        rebuild_output(symbols, stocks, out_doc)    # the next publish shows the values from before the batch for those stocks
        log("Published values from before the batch kept for %d stock(s) that lost metrics: %s" % (len(losses), ", ".join(sorted(losses)[:20])))
    rep = write_report(symbols, stocks, None, dt.date.today(), batch)
    print(report_markdown(rep))
    for x in problems[:30]:
        print("::error::fundamentals batch %s: %s" % (bid, x))
    for s, v in sorted(notices["held_isin_change"].items()):
        print("::warning::%s is held aside: read under ISIN %s, the instrument list gives %s (since %s). A person must decide which is right." % (s, v["isin"], v["listed_isin"], v["held_on"]))
    for s, v in sorted(notices["isin_differs_from_universe"].items()):
        print("::notice::%s: record ISIN %s differs from the universe file's %s (informational)." % (s, v["record_isin"], v["universe_isin"]))
    return 0 if not problems else 1


ACCEPTABLE = ("failed_validation", "interrupted")


def accept_batch(batch, by, reason, day):
    """Records a person's explicit acceptance of ONE batch that failed validation or cannot be completed. The validation result it accepts is kept
    with it, so the record shows exactly what was accepted, by whom and why. It never touches any stock record or any other batch."""
    if batch.get("status") not in ACCEPTABLE:
        fail("batch %s is %s: only a batch that failed validation or was interrupted can be accepted (validate a completed batch first)." % (batch["id"], batch.get("status")))
    by, reason = (by or "").strip(), (reason or "").strip()
    if not by or len(by) > 64 or any(ord(c) < 32 for c in by):
        fail("--by must name who accepts the batch (1 to 64 characters).")
    if len(reason) < 10 or len(reason) > 500 or any(ord(c) < 32 for c in reason):
        fail("--reason must say why the batch is accepted (10 to 500 characters, one line).")
    v = batch.get("validation") or {}
    batch["acceptance"] = {"day": day, "by": by, "reason": reason, "status_before": batch.get("status"), "outcomes": _count_outcomes(batch),
                           "validation_result": v.get("result"), "validation_problem_count": v.get("problem_count"), "validation_problems": (v.get("problems") or [])[:20]}
    batch["status"] = "accepted"
    return batch["acceptance"]


def accept_main(argv):
    """python fundamentals_updater.py accept --batch ID --by NAME --reason TEXT  -  explicit, recorded acceptance of one batch (no API call)."""
    o = _opts(argv, ("--batch", "--by", "--reason"))
    bid = o.get("batch")
    check_batch_id(bid)
    cache, stocks, symbols = _load_for_offline()
    batch = (cache.get("batches") or {}).get(bid)
    if not batch:
        fail("batch %s does not exist" % bid)
    reconcile_calls(batch)
    a = accept_batch(batch, o.get("by"), o.get("reason"), today())
    released = clear_holds(stocks, bid)       # the accepted values replace the held ones on the next publish
    write_json(CACHE_FILE, cache)
    if released:
        try:
            rebuild_output(symbols, stocks, json.loads(OUT_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    log("Batch %s accepted by %s (was %s; validation %s with %s problem(s)): %s" % (bid, a["by"], a["status_before"], a["validation_result"], a["validation_problem_count"], a["reason"]))
    print("::warning::fundamentals batch %s was accepted without passing validation, by %s: %s" % (bid, a["by"], a["reason"]))
    write_report(symbols, stocks, None, dt.date.today(), batch)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
