"""
sensex_data_updater.py - StockLens: end-of-day (EOD) daily history of the BSE SENSEX index, as its OWN file (out/sensex_history.json).

This module writes nothing else. It never touches out/historical.json (NIFTY 50 and the stock history) or out/index_history.json. It only IMPORTS the
shared Upstox client and helpers (historical_updater, market_data_updater, upstox_common); those modules are not changed. End-of-day data only: there is
no real-time SENSEX anywhere in this module.

HOW THE INSTRUMENT IS FOUND
  The Upstox BSE instrument metadata is downloaded (no token needed). The SENSEX instrument is accepted ONLY when exactly one distinct instrument key in the
  BSE_INDEX segment has the exact name (or trading symbol) "SENSEX", and every such row has exchange "BSE", instrument type "INDEX" and a key of the form
  "BSE_INDEX|...". Zero, two or more keys, a similar name ("SENSEX 50", "BSE SENSEX"), another segment or a malformed row = the run stops and writes nothing.
  The key is READ from the metadata. VERIFIED_KEY below is only a cross-check against the key seen in the one bounded test of the Upstox candle API: if the
  metadata now gives another key, the run stops for a person to look at it; neither key is used.

WHAT IS NOT YET CONFIRMED (kept here, in the written file and in the tests on purpose)
  1. BSE_INSTRUMENTS_URL is an UNVERIFIED CANDIDATE. It follows the pattern of the NSE file URL the project already uses and it answered HTTP 200 in one test,
     but no Upstox documentation that could be read lists it.
  2. PUBLIC REDISTRIBUTION RIGHTS for BSE SENSEX data are UNCONFIRMED (Upstox terms and the BSE data policy have not been checked for public display).
     The written file says so in "data_rights_note".
  3. Candle history depth beyond the bounded tests, and whether the analytics token keeps access to BSE index candles, are unproven.
  4. The shared client (historical_updater.Client) already drops a candle row that is unusable (wrong shape, non-positive price, high below low) before this
     module sees it, so such a row never reaches the file. Everything that DOES reach this module is checked again here and refused if wrong.

PUBLICATION GATE (fail closed)
  Nothing in this module publishes anything, and no workflow runs it. The written file carries "public_display_approved". It is false unless the environment
  variable SENSEX_PUBLIC_DISPLAY_APPROVED is exactly the text "true" (any case) when the updater runs. A page that shows the file must require it to be true.
  Default: false.

SAFETY (the same rule as the other updaters)
  Any failed request, bad candle or failed check = exit 1 BEFORE anything is written, so the last good file stays exactly as it is. The previous file is
  never trusted blindly: it must be this kind of file, ascending, valid, and for the same instrument key. Nothing that was published may disappear, and
  the data may never move backwards in time. The token is read from the environment, sent only to Upstox and never printed or written.
"""
import contextlib
import datetime as dt
import gzip
import io
import json
import math
import os
import re
import sys

import requests

import historical_updater as hu
import market_data_updater as mu
from upstox_common import ROOT, fail, get_token, log, write_json

OUT_FILE = ROOT / "out" / "sensex_history.json"
SCHEMA_VERSION = 1
KIND = "sensex_history"

BSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz"      # UNVERIFIED CANDIDATE (see the module notes)
URL_STATUS = "unverified candidate: follows the NSE file URL pattern and answered HTTP 200 in one test; not confirmed by Upstox documentation"
VERIFIED_KEY = "BSE_INDEX|SENSEX"          # cross-check only, never a substitute for the metadata
LABEL = "SENSEX"

INDEX_YEARS = 2            # first run: two years of daily candles (enough for 1D/1W/30D/90D and a 52-week view)
KEEP_DAYS = 800            # candles older than this are dropped from the file
MAX_STALE_DAYS = 7         # a latest candle older than this is refused
MAX_CANDLE_REQUESTS = 4    # most candle requests one run may make (two years = 3 windows; an incremental run = 1)
MAX_FILE_BYTES = 150 * 1024 * 1024

KEY_OK = re.compile(r"^BSE_INDEX\|[A-Za-z0-9 &._-]{1,40}$")
ENV_FLAG = "SENSEX_PUBLIC_DISPLAY_APPROVED"
PUBLIC_DISPLAY_DEFAULT = False

DATA_RIGHTS_NOTE = ("Public redistribution rights for BSE SENSEX data are unconfirmed: the Upstox terms and the BSE data policy have not been checked for "
                    "showing this index on a public page. Do not treat this file as cleared for public display.")


def norm(s):
    return " ".join(str(s or "").lower().split())


def public_display_approved(env=None):
    """Fail closed: only the exact text "true" (any case) in SENSEX_PUBLIC_DISPLAY_APPROVED approves. Anything else, or unset, = not approved."""
    v = (os.environ if env is None else env).get(ENV_FLAG)
    return True if isinstance(v, str) and v.strip().lower() == "true" else PUBLIC_DISPLAY_DEFAULT


# ---------------------------------------------------------------- the instrument
def load_bse_index_rows(url=BSE_INSTRUMENTS_URL):
    """The BSE_INDEX rows of the Upstox BSE instrument metadata (no token). Redirects are not followed. Stops (exit 1) on anything unexpected."""
    try:
        r = requests.get(url, timeout=90, allow_redirects=False)
    except requests.RequestException as e:
        fail("Could not download the Upstox BSE instrument metadata (" + type(e).__name__ + ").")
    if r.status_code != 200:
        fail("The Upstox BSE instrument metadata request answered HTTP %s (not 200); the URL is an unverified candidate. Nothing was written." % r.status_code)
    if len(r.content) > MAX_FILE_BYTES:
        fail("The Upstox BSE instrument metadata is larger than the safety limit. Nothing was written.")
    try:
        rows = json.loads(gzip.decompress(r.content)) if r.content[:2] == b"\x1f\x8b" else json.loads(r.content)
    except (OSError, EOFError, ValueError, UnicodeDecodeError):
        fail("The Upstox BSE instrument metadata is not valid gzip/JSON. Nothing was written.")
    if not isinstance(rows, list):
        fail("The Upstox BSE instrument metadata is not a list of instruments. Nothing was written.")
    return [x for x in rows if isinstance(x, dict) and x.get("segment") == "BSE_INDEX"]


def resolve(rows):
    """-> (instrument dict, None) or (None, reason). Exactly one distinct key; every matching row must be a BSE INDEX row; the key must equal VERIFIED_KEY."""
    hits = [x for x in rows if isinstance(x, dict) and x.get("segment") == "BSE_INDEX" and "sensex" in (norm(x.get("name")), norm(x.get("trading_symbol")))]
    keys = {x.get("instrument_key") for x in hits}
    if not hits:
        return None, "no BSE_INDEX instrument is named exactly SENSEX in the metadata"
    if len(keys) != 1 or not all(isinstance(k, str) for k in keys):
        return None, "%d distinct instrument keys match SENSEX in BSE_INDEX (need exactly one): not guessing" % len(keys)
    key = next(iter(keys))
    for x in hits:
        if x.get("exchange") != "BSE" or x.get("instrument_type") != "INDEX":
            return None, "the SENSEX row is not a BSE INDEX row (exchange=%s, instrument_type=%s)" % (str(x.get("exchange"))[:20], str(x.get("instrument_type"))[:20])
    if not KEY_OK.match(key):
        return None, "the SENSEX instrument key does not have the form BSE_INDEX|<name>"
    if key != VERIFIED_KEY:
        return None, "the metadata now gives a different key (%s) than the one verified (%s): stopped for a person to review, neither is used" % (key, VERIFIED_KEY)
    row = hits[0]
    return {"instrument_key": key, "instrument_name": str(row.get("name") or row.get("trading_symbol") or "")[:60]}, None


# ---------------------------------------------------------------- candles
def tidy(c):
    """A candle dict in the file's exact shape, or None when it is not valid. Index volume is kept only when it is a positive whole number."""
    if not isinstance(c, dict):
        return None
    d = c.get("date")
    try:
        if not isinstance(d, str) or len(d) != 10 or dt.date.fromisoformat(d).isoformat() != d:
            return None
    except ValueError:
        return None
    px = []
    for k in ("open", "high", "low", "close"):
        v = c.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            return None
        px.append(v)
    if px[1] < px[2]:
        return None
    v = c.get("volume")
    return {"date": d, "open": px[0], "high": px[1], "low": px[2], "close": px[3], "volume": v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None}


def fetch(client, key, old, end):
    """Candles for one index: INDEX_YEARS of history the first time, afterwards from shortly before the last stored date. -> (list, None) or (None, error)."""
    floor = end - dt.timedelta(days=365 * INDEX_YEARS)
    frm = max(dt.date.fromisoformat(old[-1]["date"]) - dt.timedelta(days=hu.OVERLAP_DAYS), floor) if old else floor
    wins = hu.windows(frm, end)
    if len(wins) > MAX_CANDLE_REQUESTS:
        return None, "the request would need %d candle requests (limit %d)" % (len(wins), MAX_CANDLE_REQUESTS)
    got = []
    for a, b in wins:
        try:
            rows, err = client.candles(key, a.isoformat(), b.isoformat())
        except Exception as e:                       # a stop request (SystemExit) is not an Exception and passes through; anything else becomes an ordinary failure
            return None, "unexpected error from the Upstox client (" + type(e).__name__ + ")"
        if err:
            return None, str(err)[:200] + " (" + a.isoformat() + " to " + b.isoformat() + ")"
        got += rows if isinstance(rows, list) else []
    return got, None


def latest_summary(candles):
    last = candles[-1]
    out = {"date": last["date"], "close": last["close"], "prev_close": None, "change_pct": None}
    if len(candles) > 1:
        prev = candles[-2]
        if (dt.date.fromisoformat(last["date"]) - dt.date.fromisoformat(prev["date"])).days <= 7 and prev["close"] > 0:
            out["prev_close"], out["change_pct"] = prev["close"], round((last["close"] / prev["close"] - 1) * 100, 2)
    return out


def build_doc(inst, candles, now, expected, approved):
    last = candles[-1]["date"]
    return {"schema_version": SCHEMA_VERSION, "kind": KIND, "as_of": last,
            "source": "Upstox historical candle API (BSE index); the instrument key was read from the Upstox BSE instrument metadata",
            "metadata_source": {"url": BSE_INSTRUMENTS_URL, "url_status": URL_STATUS},
            "freshness": "EOD",
            "freshness_detail": {"label": "EOD as of " + last, "source_date": last, "generated_at": now.isoformat(timespec="seconds"),
                                 "expected_latest_trading_day": expected, "source_is_latest_trading_day": last >= expected,
                                 "note": "End-of-day candles: the last close, not real-time prices."},
            "generated_at": now.isoformat(timespec="seconds"), "interval": "1day",
            "instrument": {"label": LABEL, "instrument_key": inst["instrument_key"], "instrument_name": inst["instrument_name"],
                           "exchange": "BSE", "segment": "BSE_INDEX", "instrument_type": "INDEX"},
            "first_date": candles[0]["date"], "last_date": last, "candle_count": len(candles), "latest": latest_summary(candles),
            "data_rights_note": DATA_RIGHTS_NOTE, "public_display_approved": bool(approved), "candles": candles}


def check_candles(candles, name, p):
    """Ascending, one per date, valid prices. Appends problems to p."""
    prev = None
    for c in candles:
        if tidy(c) != c:
            p.append(name + ": a candle is malformed or impossible (" + str(c.get("date") if isinstance(c, dict) else "?")[:10] + ")")
            return
        if prev is not None and c["date"] == prev:
            p.append(name + ": duplicate date " + prev)
            return
        if prev is not None and c["date"] < prev:
            p.append(name + ": dates are not ascending at " + c["date"])
            return
        prev = c["date"]


def check_old(old_doc):
    """The previous file must be a well-formed file of this kind. Returns (candles, key, problems); an absent file = no history and no problems."""
    if old_doc is None:
        return [], None, []
    p = []
    if not isinstance(old_doc, dict) or old_doc.get("kind") != KIND or old_doc.get("schema_version") != SCHEMA_VERSION or not isinstance(old_doc.get("candles"), list):
        return [], None, ["the existing " + OUT_FILE.name + " is not a valid " + KIND + " file: it is left exactly as it is (move it aside by hand to rebuild)"]
    key = (old_doc.get("instrument") or {}).get("instrument_key") if isinstance(old_doc.get("instrument"), dict) else None
    check_candles(old_doc["candles"], "existing file", p)
    return old_doc["candles"], key, p


def check_doc(doc, old_doc, cutoff, today):
    """Checks on the document that would be written. Returns a list of problems."""
    p = []
    c = doc.get("candles") or []
    if not c:
        return ["no candles"]
    check_candles(c, "new file", p)
    if p:
        return p
    if c[-1]["date"] > today.isoformat():
        p.append("a candle is dated in the future (" + c[-1]["date"] + ")")
    if (today - dt.date.fromisoformat(c[-1]["date"])).days > MAX_STALE_DAYS:
        p.append("the latest candle (%s) is more than %d days old" % (c[-1]["date"], MAX_STALE_DAYS))
    if doc["instrument"]["instrument_key"] != VERIFIED_KEY:
        p.append("the instrument key is not the verified key")
    try:
        json.dumps(doc, allow_nan=False)
    except ValueError:
        p.append("not strict JSON")
    if isinstance(old_doc, dict) and old_doc.get("kind") == KIND:
        old_key = (old_doc.get("instrument") or {}).get("instrument_key") if isinstance(old_doc.get("instrument"), dict) else None
        if old_key != doc["instrument"]["instrument_key"]:
            p.append("the instrument key changed (%s -> %s): refusing to mix two instruments" % (str(old_key)[:40], doc["instrument"]["instrument_key"]))
        old_asof = old_doc.get("as_of")
        if isinstance(old_asof, str) and doc["as_of"] < old_asof:
            p.append("the new data ends %s but %s is already published: refusing to go back in time" % (doc["as_of"], old_asof))
        have = {x["date"] for x in c}
        lost = sorted(x["date"] for x in old_doc.get("candles", []) if isinstance(x, dict) and isinstance(x.get("date"), str) and x["date"] >= cutoff and x["date"] not in have)
        if lost:
            p.append("%d published date(s) would be dropped (first: %s)" % (len(lost), lost[0]))
    return p


def run(inst, client, old_doc, now, end, expected=None, approved=False):
    """The whole job without any I/O of its own. inst: the resolved instrument. Returns (doc or None, problems)."""
    prior, old_key, problems = check_old(old_doc)
    if problems:
        return None, problems
    if old_doc is not None and old_key != inst["instrument_key"]:
        return None, ["the instrument key changed (%s -> %s): refusing to mix two instruments" % (str(old_key)[:40], inst["instrument_key"])]
    fresh, err = fetch(client, inst["instrument_key"], prior, end)
    if err:
        return None, ["SENSEX (%s): %s" % (inst["instrument_key"], err)]
    seen, new = set(), []
    for raw in fresh:
        c = tidy(raw)
        if c is None:
            return None, ["a malformed or impossible candle was returned"]
        if c["date"] in seen:
            return None, ["the response repeats the date " + c["date"]]
        seen.add(c["date"])
        new.append(c)
    if not new:
        return None, ["Upstox returned no valid candles for " + inst["instrument_key"]]
    cutoff = (end - dt.timedelta(days=KEEP_DAYS)).isoformat()
    by_date = {c["date"]: c for c in prior}
    by_date.update({c["date"]: c for c in new})                    # a freshly fetched candle replaces an older one
    candles = [by_date[d] for d in sorted(by_date) if d >= cutoff]
    if not candles:
        return None, ["no candle is newer than the keep limit"]
    expected = expected or mu.expected_latest_trading_day(now, mu.read_holidays())
    doc = build_doc(inst, candles, now, expected, approved)
    problems = check_doc(doc, old_doc, cutoff, now.date())
    return (None, problems) if problems else (doc, [])


# ---------------------------------------------------------------- the job
def scrub(text, token):
    return str(text).replace(token, "[redacted]") if token else str(text)


def summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main():
    token = get_token()
    old = None
    if OUT_FILE.exists():
        try:
            old = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            old = {}                                           # unreadable: reported by check_old, never overwritten
    now = dt.datetime.now(mu.IST)
    inst, why = resolve(load_bse_index_rows())
    if inst is None:
        print("::error::" + scrub(why, token))
        summary(["### SENSEX data: FAILED - nothing was written", "", "- " + scrub(why, token)])
        fail("SENSEX instrument not verified; " + OUT_FILE.name + " was NOT changed.")
    log("SENSEX instrument resolved from the metadata:", inst["instrument_key"])
    # Everything the shared client prints while it works (including its own authentication-failure message, which can quote Upstox's reply) is captured
    # and passed through scrub() before it reaches the log.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            doc, problems = run(inst, hu.Client(token), old, now, hu.last_complete_day(), approved=public_display_approved())
    except SystemExit:
        print(scrub(buf.getvalue(), token), end="", flush=True)
        summary(["### SENSEX data: FAILED - nothing was written", "", "- the Upstox client stopped the run (see the log)"])
        raise
    print(scrub(buf.getvalue(), token), end="", flush=True)
    if problems:
        for x in problems:
            print("::error::" + scrub(x, token))
        summary(["### SENSEX data: FAILED - nothing was written", ""] + ["- " + scrub(x, token) for x in problems])
        fail("%d problem(s); %s was NOT changed." % (len(problems), OUT_FILE.name))
    write_json(OUT_FILE, doc)
    summary(["### SENSEX data: OK", "", "| Instrument key | Candles | Range | Public display approved |", "|---|---|---|---|",
             "| %s | %d | %s to %s | %s |" % (doc["instrument"]["instrument_key"], doc["candle_count"], doc["first_date"], doc["last_date"], doc["public_display_approved"])])
    log("Wrote", OUT_FILE, "-", doc["candle_count"], "candles,", doc["first_date"], "to", doc["last_date"], "- public_display_approved:", doc["public_display_approved"])


if __name__ == "__main__":
    sys.exit(main())
