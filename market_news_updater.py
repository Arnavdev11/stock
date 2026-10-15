"""
market_news_updater.py - PRIVATE, DISABLED-BY-DEFAULT market news fetcher (Upstox News API).

Writes only out/market_news.json. It does not read or change fundamentals, financials, quarterly results,
historical prices, the stock universe or the live scanner. No workflow runs it and no website page reads it.

LICENSING IS UNRESOLVED. Nothing here is approved for public display, redistribution or long-term caching:
  * the output always carries public_display=false; there is no switch that turns it on;
  * the cache is short (48 h) unless MARKET_NEWS_CACHE_PERMITTED=yes, and never longer than 7 days;
  * thumbnails are never stored.

Switches (environment). Anything not listed is not configurable.
  MARKET_NEWS_ENABLED=yes          required, otherwise the run does nothing (no token read, no request, no write)
  MARKET_NEWS_INSTRUMENTS          comma separated instrument keys, e.g. NSE_EQ|INE040H01021 (default: a small list)
  MARKET_NEWS_MAX_REQUESTS         request cap per run, counts retries (default 4, ceiling 20)
  MARKET_NEWS_BATCH_SIZE           keys per request (default and ceiling 30, the documented maximum)
  MARKET_NEWS_PAGE_SIZE            1-100 (default 100)
  MARKET_NEWS_MAX_PAGES            pages per batch (default 1)
  MARKET_NEWS_DELAY_SECONDS        pause between requests (default 1.0)
  MARKET_NEWS_RETENTION_HOURS      default 48
  MARKET_NEWS_CACHE_PERMITTED=yes  only after Upstox has confirmed caching in writing; lifts the 48 h limit to 7 days max
Token: UPSTOX_ANALYTICS_TOKEN, read only when the run is enabled, never printed, logged or written.

Assumptions taken from the official documentation (https://upstox.com/developer/api-documentation/get-news/) and NOT yet
verified against a live response: see ASSUMPTIONS at the bottom of this file.

Exit codes: 0 = disabled, ok or partial; 1 = total failure or rejected token (previous output untouched); 2 = bad configuration.
"""
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

NEWS_URL = "https://api.upstox.com/v2/news"
CATEGORY = "instrument_keys"                 # the only category this module ever sends; never positions / holdings
OUT_NAME = "market_news.json"
SCHEMA = 1
TOKEN_ENV = "UPSTOX_ANALYTICS_TOKEN"
ENABLE_ENV = "MARKET_NEWS_ENABLED"

# Starting list only. Keys use the documented NSE_EQ|<ISIN> form; verify them live before relying on them.
DEFAULT_INSTRUMENTS = ["NSE_EQ|INE002A01018", "NSE_EQ|INE467B01029", "NSE_EQ|INE009A01021",
                       "NSE_EQ|INE040H01021", "NSE_EQ|INE090A01021", "NSE_EQ|INE062A01020"]
KEY_RE = re.compile(r"^[A-Z_]{2,20}\|[A-Z0-9]{4,20}$")

MAX_KEYS_PER_REQUEST = 30                    # documented
REQUEST_CEILING = 20
DEFAULT_REQUESTS = 4
MAX_PAGE_SIZE = 100
DEFAULT_RETENTION_HOURS = 48                 # short private test cache while licensing is unresolved
MAX_RETENTION_HOURS = 168                    # 7 days, only with MARKET_NEWS_CACHE_PERMITTED=yes
MAX_RECORDS = 2000
FUTURE_TOLERANCE_MS = 5 * 60 * 1000
MIN_VALID_MS = 946684800000                  # 2000-01-01; also rejects second-precision values instead of guessing
HEADLINE_LIMIT, SUMMARY_LIMIT, ERRORS_KEPT = 300, 1000, 10
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

TAG_RE = re.compile(r"<[^>]*>")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src")
CODE_RE = re.compile(r"\bUDAPI\d{3,5}\b")


class AuthError(Exception):
    pass


class ConfigError(Exception):
    pass


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- configuration
def _int(env, name, default, lo, hi):
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = int(str(raw).strip())
    except ValueError:
        raise ConfigError("%s must be a whole number" % name)
    return max(lo, min(hi, v))


def _float(env, name, default, lo, hi):
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = float(str(raw).strip())
    except ValueError:
        raise ConfigError("%s must be a number" % name)
    if math.isnan(v):
        raise ConfigError("%s must be a number" % name)
    return max(lo, min(hi, v))


def is_enabled(env):
    return str(env.get(ENABLE_ENV, "")).strip().lower() == "yes"


def parse_instruments(raw):
    keys, seen = [], set()
    for k in str(raw).split(","):
        k = k.strip()
        if not k:
            continue
        if not KEY_RE.match(k):
            raise ConfigError("invalid instrument key (expected NSE_EQ|ISIN form)")
        if k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def load_config(env):
    raw = env.get("MARKET_NEWS_INSTRUMENTS")
    keys = parse_instruments(raw) if raw and str(raw).strip() else list(DEFAULT_INSTRUMENTS)
    if not keys:
        raise ConfigError("no instrument keys configured")
    permitted = str(env.get("MARKET_NEWS_CACHE_PERMITTED", "")).strip().lower() == "yes"
    hours = _int(env, "MARKET_NEWS_RETENTION_HOURS", DEFAULT_RETENTION_HOURS, 1, MAX_RETENTION_HOURS)
    if not permitted:
        hours = min(hours, DEFAULT_RETENTION_HOURS)
    return {
        "instruments": keys,
        "max_requests": _int(env, "MARKET_NEWS_MAX_REQUESTS", DEFAULT_REQUESTS, 1, REQUEST_CEILING),
        "batch_size": _int(env, "MARKET_NEWS_BATCH_SIZE", MAX_KEYS_PER_REQUEST, 1, MAX_KEYS_PER_REQUEST),
        "page_size": _int(env, "MARKET_NEWS_PAGE_SIZE", MAX_PAGE_SIZE, 1, MAX_PAGE_SIZE),
        "max_pages": _int(env, "MARKET_NEWS_MAX_PAGES", 1, 1, 5),
        "delay": _float(env, "MARKET_NEWS_DELAY_SECONDS", 1.0, 0.0, 60.0),
        "retention_hours": hours,
        "retries": 1,                                  # extra attempts for timeouts / 5xx only; each one counts toward the cap
    }


# ---------------------------------------------------------------- normalisation
def clean_text(v, limit):
    if not isinstance(v, str):
        return None
    t = TAG_RE.sub(" ", html.unescape(TAG_RE.sub(" ", v)))
    t = " ".join(CTRL_RE.sub(" ", t).split())
    if not t:
        return None
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def clean_link(url):
    """http(s) URL without fragment, or None. javascript:, data:, relative and malformed values are rejected."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u or len(u) > 2048 or any(c.isspace() for c in u):
        return None
    try:
        p = urlsplit(u)
        host = p.hostname
        p.port
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not host or p.username or p.password:
        return None
    return p._replace(fragment="").geturl()


def link_key(url):
    """Identity of an article link: scheme-insensitive, case-insensitive host, no fragment, tracking params or trailing slash."""
    p = urlsplit(url)
    q = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(TRACKING))
    port = "" if p.port in (None, 80, 443) else ":%d" % p.port
    return "%s%s%s?%s" % (p.hostname.lower(), port, p.path.rstrip("/"), urlencode(q))


def parse_ms(v, now_ms):
    """Unix milliseconds -> int, or (None, reason)."""
    if isinstance(v, bool) or v is None:
        return None, "invalid_timestamp"
    if isinstance(v, str):
        t = v.strip()
        if not t.isdigit():
            return None, "invalid_timestamp"
        v = int(t)
    if not isinstance(v, (int, float)) or (isinstance(v, float) and not math.isfinite(v)):
        return None, "invalid_timestamp"
    ms = int(v)
    if ms < MIN_VALID_MS:
        return None, "invalid_timestamp"
    if ms > now_ms + FUTURE_TOLERANCE_MS:
        return None, "future_timestamp"
    return ms, None


def iso_utc(ms):
    return dt.datetime.fromtimestamp(ms / 1000.0, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def format_ist(ms):
    """Display helper for the website layer / logs: Unix ms -> 'YYYY-MM-DD HH:MM IST'."""
    return dt.datetime.fromtimestamp(ms / 1000.0, IST).strftime("%Y-%m-%d %H:%M IST")


def _heading_key(headline):
    return " ".join("".join(c if c.isalnum() else " " for c in headline.casefold()).split())


def article_id(headline, link, ms):
    base = ("l:" + link_key(link)) if link else ("h:%s|%d" % (_heading_key(headline), ms))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def normalize_item(raw, instrument_key, now_ms):
    """One API item -> (record, None) or (None, reason). Never raises."""
    if not isinstance(raw, dict):
        return None, "malformed_item"
    headline = clean_text(raw.get("heading"), HEADLINE_LIMIT)
    if not headline or not any(c.isalnum() for c in headline):
        return None, "missing_headline"
    ms, why = parse_ms(raw.get("published_time"), now_ms)
    if ms is None:
        return None, why
    link = clean_link(raw.get("article_link"))
    return {
        "id": article_id(headline, link, ms),
        "headline": headline,
        "summary": clean_text(raw.get("summary"), SUMMARY_LIMIT),
        "published_at": iso_utc(ms),
        "published_ms": ms,
        "link": link,
        "instruments": [instrument_key] if instrument_key else [],
    }, None


def parse_response(body, requested, now_ms, stats):
    """Documented response body -> list of normalised records. Tolerates `data` as an object keyed by instrument key (shown in the
    docs example) or as a plain list (the docs text says 'array'). Returns (records, total_pages)."""
    out = []
    data = body.get("data") if isinstance(body, dict) else None
    groups = []
    if isinstance(data, dict):
        groups = list(data.items())
    elif isinstance(data, list):
        groups = [(requested[0] if len(requested) == 1 else None, data)]
    elif data is not None:
        stats["rejected"]["malformed_group"] = stats["rejected"].get("malformed_group", 0) + 1
    for key, items in groups:
        if key is not None and not (isinstance(key, str) and KEY_RE.match(key)):
            stats["rejected"]["invalid_instrument_key"] = stats["rejected"].get("invalid_instrument_key", 0) + 1
            continue
        if not isinstance(items, list):
            stats["rejected"]["malformed_group"] = stats["rejected"].get("malformed_group", 0) + 1
            continue
        for raw in items:
            stats["seen"] += 1
            rec, why = normalize_item(raw, key, now_ms)
            if rec:
                out.append(rec)
            else:
                stats["rejected"][why] = stats["rejected"].get(why, 0) + 1
    pages = None
    try:
        tp = body["metadata"]["page"]["total_pages"]
        if isinstance(tp, int) and not isinstance(tp, bool) and tp >= 1:
            pages = tp
    except (KeyError, TypeError):
        pass
    return out, pages


# ---------------------------------------------------------------- merge / retention / output
def valid_stored(rec):
    """Re-check a record read back from the previous output (the file is trusted no more than the API)."""
    try:
        return (isinstance(rec, dict) and isinstance(rec["id"], str) and clean_text(rec["headline"], HEADLINE_LIMIT) == rec["headline"]
                and isinstance(rec["published_ms"], int) and not isinstance(rec["published_ms"], bool) and rec["published_ms"] >= MIN_VALID_MS
                and rec["published_at"] == iso_utc(rec["published_ms"])
                and (rec["summary"] is None or clean_text(rec["summary"], SUMMARY_LIMIT) == rec["summary"])
                and (rec["link"] is None or clean_link(rec["link"]) == rec["link"])
                and isinstance(rec["instruments"], list) and all(isinstance(k, str) and KEY_RE.match(k) for k in rec["instruments"]))
    except (KeyError, TypeError):
        return False


def merge(existing, fresh):
    """Union by id. Instruments are unioned; a missing summary/link is filled from the other copy. Returns (records, duplicates_merged)."""
    by_id, dups = {}, 0
    for rec in list(existing) + list(fresh):
        cur = by_id.get(rec["id"])
        if cur is None:
            by_id[rec["id"]] = dict(rec, instruments=sorted(set(rec["instruments"])))
            continue
        dups += 1
        cur["instruments"] = sorted(set(cur["instruments"]) | set(rec["instruments"]))
        for f in ("summary", "link"):
            if not cur.get(f) and rec.get(f):
                cur[f] = rec[f]
    return list(by_id.values()), dups


def prune(records, now_ms, retention_hours):
    cutoff = now_ms - retention_hours * 3600 * 1000
    kept = [r for r in records if cutoff <= r["published_ms"] <= now_ms + FUTURE_TOLERANCE_MS]
    kept.sort(key=lambda r: (-r["published_ms"], r["id"]))
    return kept[:MAX_RECORDS], len(records) - len(kept)


def load_existing(path):
    """(records, ok). A missing, unreadable or wrong-schema file yields ([], False); bad records inside a good file are dropped."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("articles"), list):
        return [], False
    return [r for r in doc["articles"] if valid_stored(r)], True


def write_atomic(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


# ---------------------------------------------------------------- fetching
def requests_transport(url, params, headers, timeout):
    """The only code that touches the network. Returns (http_status, parsed_json_or_None). Imported lazily so tests never load it."""
    import requests
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def _safe_error(status, body):
    code = ""
    if isinstance(body, dict):
        m = CODE_RE.search(json.dumps(body, default=str)[:4000])
        code = " " + m.group(0) if m else ""
    return "HTTP %d%s" % (status, code)


def fetch_all(cfg, token, transport, sleep, now_ms):
    """Returns (records, stats). Raises AuthError on 401/403. Every attempt, including retries, counts toward max_requests."""
    stats = {"requests_made": 0, "request_cap": cfg["max_requests"], "batches_planned": 0, "batches_ok": 0, "batches_failed": 0,
             "batches_skipped": 0, "partial": False, "seen": 0, "rejected": {}, "errors": [], "rate_limited": False}
    keys = cfg["instruments"]
    batches = [keys[i:i + cfg["batch_size"]] for i in range(0, len(keys), cfg["batch_size"])]
    stats["batches_planned"] = len(batches)
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    records = []

    def err(msg):
        if len(stats["errors"]) < ERRORS_KEPT:
            stats["errors"].append(msg.replace(token, "[redacted]") if token else msg)

    def call(batch, page):
        """-> (body, None) or (None, kind) with kind in capped / rate / failed."""
        attempts = 0
        while True:
            if stats["requests_made"] >= cfg["max_requests"]:
                return None, "capped"
            if stats["requests_made"]:
                sleep(cfg["delay"])
            stats["requests_made"] += 1
            attempts += 1
            params = {"category": CATEGORY, "instrument_keys": ",".join(batch), "page_number": page, "page_size": cfg["page_size"]}
            try:
                status, body = transport(NEWS_URL, params, headers, 30)
            except Exception as e:                      # message is never used: it could echo request details
                status, body = None, type(e).__name__
            retryable = status is None or (isinstance(status, int) and status >= 500)
            if status == 200:
                if isinstance(body, dict) and body.get("status") != "error" and "data" in body:
                    return body, None
                err("invalid or error response body")
                return None, "failed"
            if status in (401, 403):
                raise AuthError("Upstox rejected the token (HTTP %d)" % status)
            if status == 429:
                stats["rate_limited"] = True
                err("HTTP 429 rate limited; run stopped")
                return None, "rate"
            err("network error: " + body if status is None else _safe_error(status, body))
            if retryable and attempts <= cfg["retries"]:
                continue
            return None, "failed"

    stop = False
    for batch in batches:
        if stop:
            stats["batches_skipped"] += 1
            continue
        ok, page, total = False, 1, 1
        while page <= min(total, cfg["max_pages"]):
            body, kind = call(batch, page)
            if body is None:
                stats["partial"] = True
                stop = stop or kind in ("capped", "rate")
                if not ok and kind == "capped":
                    stats["batches_skipped"] += 1
                    ok = None                          # counted as skipped, not failed
                break
            recs, pages = parse_response(body, batch, now_ms, stats)
            records.extend(recs)
            ok = True
            total = pages or 1
            page += 1
        if ok:
            stats["batches_ok"] += 1
        elif ok is not None:
            stats["batches_failed"] += 1
    return records, stats


# ---------------------------------------------------------------- run
def run(cfg, token, out_dir, transport=requests_transport, sleep=time.sleep, now=None):
    """-> (exit_code, status). Writes out_dir/market_news.json only when at least one batch succeeded."""
    now = now or dt.datetime.now(dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    path = out_dir / OUT_NAME
    try:
        fresh, stats = fetch_all(cfg, token, transport, sleep, now_ms)
    except AuthError as e:
        log("::error::" + str(e).replace(token, "[redacted]") + ". Previous output left untouched.")
        return 1, "auth_failed"
    if stats["batches_ok"] == 0:
        log("::error::No news request succeeded (%d made). Previous output left untouched. %s" % (stats["requests_made"], "; ".join(stats["errors"][:3])))
        return 1, "failed"
    existing, _ = load_existing(path)
    merged, dups = merge(existing, fresh)
    kept, expired = prune(merged, now_ms, cfg["retention_hours"])
    status = "partial" if stats["partial"] or stats["batches_failed"] or stats["batches_skipped"] or stats["rate_limited"] else "ok"
    doc = {
        "schema": SCHEMA,
        "generated_at": iso_utc(now_ms),
        "status": status,
        "public_display": False,
        "licensing": "unresolved: not approved for public display, redistribution or long-term caching",
        "retention_hours": cfg["retention_hours"],
        "instruments": cfg["instruments"],
        "run": {"requests_made": stats["requests_made"], "request_cap": stats["request_cap"],
                "batches": {"planned": stats["batches_planned"], "ok": stats["batches_ok"], "failed": stats["batches_failed"], "skipped": stats["batches_skipped"]},
                "rate_limited": stats["rate_limited"], "articles_seen": stats["seen"], "articles_accepted": len(fresh),
                "rejected": stats["rejected"], "duplicates_merged": dups, "expired_or_out_of_window": expired, "errors": stats["errors"]},
        "articles": kept,
    }
    write_atomic(path, doc)
    log("market news: %s, %d article(s) kept, %d request(s)" % (status, len(kept), stats["requests_made"]))
    return 0, status


def main(env=None, out_dir=None, transport=requests_transport, sleep=time.sleep, now=None):
    env = os.environ if env is None else env
    if not is_enabled(env):
        log("market news: disabled (set %s=yes to enable). Nothing fetched or written." % ENABLE_ENV)
        return 0
    try:
        cfg = load_config(env)
    except ConfigError as e:
        log("::error::market news configuration: " + str(e))
        return 2
    token = str(env.get(TOKEN_ENV, "")).strip()
    if not token:
        log("::error::%s is not set." % TOKEN_ENV)
        return 1
    out = Path(out_dir) if out_dir else Path(env.get("DATA_DIR", ".")) / "out"
    code, _ = run(cfg, token, out, transport, sleep, now)
    return code


# ASSUMPTIONS (documentation-only; each needs a live check before anything depends on it)
#  1. The analytics token is accepted by GET /v2/news. The docs do not say. A 401/403 is treated as fatal.
#  2. `data` is an object keyed by instrument key (docs example); a plain list is also tolerated (docs text says "array").
#  3. Item fields: heading, summary, thumbnail, article_link, published_time (Unix milliseconds). Items may omit summary/link.
#  4. Pagination via page_number/page_size and metadata.page.total_pages; no date filter exists, so "latest N" is all we get.
#  5. The default instrument keys (NSE_EQ|ISIN) are valid and have news; there is no market-wide feed.
#  6. Error bodies for 4XX are undocumented; only a UDAPI#### code and the status are kept.
#  7. Previous-day availability by 06:00 IST and the depth of history per call are unknown.

if __name__ == "__main__":
    sys.exit(main())
