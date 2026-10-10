"""
index_data_updater.py - StockLens Phase 5K: daily history of the important NSE indices, as a SEPARATE file (out/index_history.json).
The 10-stock historical.json and historical_updater.py are not changed; this module only re-uses their Upstox client and helpers.

How an index is found: the official Upstox NSE instrument file lists every index (segment NSE_INDEX) with its instrument key. Each wanted index has a
short list of exact NAMES (see WANTED). It is included ONLY when exactly one instrument in that file has one of those names. No key is typed in here, none is
guessed, nothing "similar" is accepted: an index that is missing or ambiguous is listed under "unresolved" with the names that were tried and the nearest
names found, so a person can decide. (Which names Upstox actually uses is therefore confirmed on the first real run, not assumed.)

Safety (the same rule as historical_updater.py): any failed request, bad candle or failed check = exit 1 BEFORE anything is written, so the last good file
stays. Candles are daily OHLC exactly as Upstox provides; days without a valid candle are absent. Index volume is stored only when Upstox reports a positive number.
Everything is end-of-day. The token is read from the environment and never printed or written.
"""
import datetime as dt
import gzip
import json
import os
import sys

import requests

import historical_updater as hu
import market_data_updater as mu
from upstox_common import INSTRUMENTS_URL, ROOT, fail, get_token, log, write_json

OUT_FILE = ROOT / "out" / "index_history.json"
SCHEMA_VERSION = 1
INDEX_YEARS = 2            # enough for 1D/1W/30D/90D and a 52-week view
KEEP_DAYS = 800            # candles older than this are dropped from the file
MAX_STALE_DAYS = 7

# label shown on the site -> exact instrument names to look for (lower-case, compared with the whole name or trading symbol)
WANTED = [
    ("NIFTY 50", ["nifty 50"]),
    ("BANK NIFTY", ["nifty bank", "bank nifty"]),
    ("NIFTY NEXT 50", ["nifty next 50"]),
    ("NIFTY MIDCAP 100", ["nifty midcap 100"]),
    ("NIFTY SMALLCAP 100", ["nifty smallcap 100", "nifty smlcap 100"]),
    ("NIFTY IT", ["nifty it"]),
    ("NIFTY AUTO", ["nifty auto"]),
    ("NIFTY PHARMA", ["nifty pharma"]),
    ("NIFTY FMCG", ["nifty fmcg"]),
    ("NIFTY METAL", ["nifty metal"]),
    ("NIFTY REALTY", ["nifty realty"]),
    ("NIFTY ENERGY", ["nifty energy"]),
    ("NIFTY INFRASTRUCTURE", ["nifty infrastructure", "nifty infra"]),
]


def norm(s):
    return " ".join(str(s or "").lower().split())


def load_index_rows():
    """Every NSE_INDEX row of the official Upstox instrument file (no token needed)."""
    try:
        r = requests.get(INSTRUMENTS_URL, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        fail("Could not download the Upstox instruments file (" + type(e).__name__ + ").")
    try:
        rows = json.loads(gzip.decompress(r.content))
    except OSError:
        rows = json.loads(r.content)
    return [{"instrument_key": x["instrument_key"], "name": str(x.get("name") or ""), "trading_symbol": str(x.get("trading_symbol") or "")}
            for x in rows if x.get("segment") == "NSE_INDEX" and x.get("instrument_key")]


def resolve(rows, wanted=WANTED):
    """-> (resolved, unresolved). An index is resolved only when its names match exactly ONE distinct instrument key."""
    resolved, unresolved, used = [], [], {}
    for label, names in wanted:
        want = {norm(n) for n in names}
        hits = {}
        for r in rows:
            if norm(r.get("name")) in want or norm(r.get("trading_symbol")) in want:
                hits[r["instrument_key"]] = r
        core = [w for w in norm(label).split() if w != "nifty"]
        near = sorted({r["name"] for r in rows if r.get("name") and core and all(c in norm(r["name"]) for c in core[:1])})[:8]
        if not hits:
            unresolved.append({"label": label, "reason": "no instrument with one of the expected names", "tried": names, "nearest_names": near})
        elif len(hits) > 1:
            unresolved.append({"label": label, "reason": "more than one instrument matches: not guessing", "tried": names, "matches": sorted(hits)})
        else:
            key, row = next(iter(hits.items()))
            if key in used:
                unresolved.append({"label": label, "reason": "same instrument as " + used[key], "tried": names})
            else:
                used[key] = label
                resolved.append({"label": label, "instrument_key": key, "instrument_name": row["name"] or row["trading_symbol"]})
    return resolved, unresolved


def fetch(client, key, old, end):
    """New candles for one index: INDEX_YEARS of history the first time, afterwards from shortly before the last stored date."""
    floor = end - dt.timedelta(days=365 * INDEX_YEARS)
    frm = max(dt.date.fromisoformat(old[-1]["date"]) - dt.timedelta(days=hu.OVERLAP_DAYS), floor) if old else floor
    got = []
    for a, b in hu.windows(frm, end):
        rows, err = client.candles(key, a.isoformat(), b.isoformat())
        if err:
            return None, err + " (" + a.isoformat() + " to " + b.isoformat() + ")"
        got += rows
    return got, None


def tidy(c):
    """Index volume only when Upstox reports a positive number (index candles usually carry none)."""
    v = c.get("volume")
    return {"date": c["date"], "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": v if isinstance(v, int) and v > 0 else None}


def latest_summary(candles):
    if not candles:
        return None
    last = candles[-1]
    out = {"date": last["date"], "close": last["close"], "prev_close": None, "change_pct": None}
    if len(candles) > 1:
        prev = candles[-2]
        if (dt.date.fromisoformat(last["date"]) - dt.date.fromisoformat(prev["date"])).days <= 7 and prev["close"] > 0:
            out["prev_close"], out["change_pct"] = prev["close"], round((last["close"] / prev["close"] - 1) * 100, 2)
    return out


def build_doc(entries, unresolved, now, expected):
    last_dates = [e["last_date"] for e in entries]
    as_of = max(last_dates) if last_dates else None
    for e in entries:
        e["source_is_latest_trading_day"] = e["last_date"] >= expected
        e["freshness"] = "EOD as of " + e["last_date"]
    return {"schema_version": SCHEMA_VERSION, "kind": "index_history", "as_of": as_of, "source": "Upstox historical candle API; instrument keys read from the official Upstox NSE instrument file",
            "freshness": "EOD", "freshness_detail": {"label": "EOD as of " + str(as_of), "source_date": as_of, "generated_at": now.isoformat(timespec="seconds"),
                                                      "expected_latest_trading_day": expected, "note": "End-of-day candles: the last close, not real-time prices."},
            "generated_at": now.isoformat(timespec="seconds"), "interval": "1day", "requested": [w[0] for w in WANTED], "indices": entries, "unresolved": unresolved, "errors": []}


def check_doc(doc, today):
    """Structure checks before the file is written. Returns a list of problems."""
    p = []
    if not doc.get("indices"):
        return ["no index could be resolved and fetched"]
    names = set()
    for e in doc["indices"]:
        n = e.get("label")
        if n in names:
            p.append(n + ": listed twice")
        names.add(n)
        c = e.get("candles") or []
        if not c:
            p.append(n + ": no candles")
            continue
        prev = None
        for x in c:
            if prev is not None and x["date"] <= prev:
                p.append(n + ": candles not in strictly ascending date order")
                break
            prev = x["date"]
            if x["date"] > today.isoformat():
                p.append(n + ": a candle is dated in the future")
                break
            if min(x["open"], x["high"], x["low"], x["close"]) <= 0 or x["high"] < x["low"]:
                p.append(n + ": impossible prices on " + x["date"])
                break
            if x["volume"] is not None and x["volume"] <= 0:
                p.append(n + ": non-positive volume on " + x["date"])
                break
        if (today - dt.date.fromisoformat(c[-1]["date"])).days > MAX_STALE_DAYS:
            p.append(n + ": the latest candle (%s) is more than %d days old" % (c[-1]["date"], MAX_STALE_DAYS))
    try:
        json.dumps(doc, allow_nan=False)
    except ValueError:
        p.append("not strict JSON")
    return p


def run(rows, client, old_doc, now, end):
    """The whole job without any I/O of its own. Returns (doc or None, problems)."""
    resolved, unresolved = resolve(rows)
    old = {e["label"]: e for e in (old_doc or {}).get("indices", []) if isinstance(e, dict) and e.get("label")}
    entries, errors = [], []
    for r in resolved:
        prior = (old.get(r["label"]) or {}).get("candles") or []
        if (old.get(r["label"]) or {}).get("instrument_key") not in (None, r["instrument_key"]):
            prior = []                                      # the key changed: never mix two instruments
        fresh, err = fetch(client, r["instrument_key"], prior, end)
        if err:
            errors.append("%s (%s): %s" % (r["label"], r["instrument_key"], err))
            continue
        cutoff = (end - dt.timedelta(days=KEEP_DAYS)).isoformat()
        candles = [tidy(c) for c in hu.merge(prior, fresh) if c["date"] >= cutoff]
        if not candles:
            errors.append("%s (%s): Upstox returned no valid candles" % (r["label"], r["instrument_key"]))
            continue
        entries.append({"label": r["label"], "instrument_name": r["instrument_name"], "instrument_key": r["instrument_key"], "first_date": candles[0]["date"],
                        "last_date": candles[-1]["date"], "candle_count": len(candles), "latest": latest_summary(candles), "candles": candles})
    if errors:
        return None, errors
    doc = build_doc(entries, unresolved, now, mu.expected_latest_trading_day(now, mu.read_holidays()))
    problems = check_doc(doc, now.date())
    old_asof = (old_doc or {}).get("as_of")
    if old_asof and doc["as_of"] and doc["as_of"] < old_asof:
        problems.append("the new data ends %s but %s is already published: refusing to go back in time" % (doc["as_of"], old_asof))
    for lbl, e in old.items():                                  # no-loss: an index that was published must still be there
        if lbl not in {x["label"] for x in entries}:
            problems.append(lbl + ": was published before and could not be resolved this time: refusing to drop it")
    return (None, problems) if problems else (doc, [])


def summary(lines):
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
    now = dt.datetime.now(mu.IST)
    rows = load_index_rows()
    log("NSE_INDEX instruments in the Upstox file:", len(rows))
    doc, problems = run(rows, hu.Client(token), old, now, hu.last_complete_day())
    if problems:
        for x in problems:
            print("::error::" + x)
        summary(["### Index data: FAILED - nothing was written", ""] + ["- " + x for x in problems])
        fail("%d problem(s); out/index_history.json was NOT changed." % len(problems))
    write_json(OUT_FILE, doc)
    lines = ["### Index data: OK", "", "| Index | Instrument key | Candles | Range |", "|---|---|---|---|"] + \
            ["| %s | %s | %d | %s to %s |" % (e["label"], e["instrument_key"], e["candle_count"], e["first_date"], e["last_date"]) for e in doc["indices"]]
    if doc["unresolved"]:
        lines += ["", "Not resolved (never guessed): " + ", ".join(u["label"] + " (" + u["reason"] + ")" for u in doc["unresolved"])]
    summary(lines)
    log("Wrote", OUT_FILE, "-", len(doc["indices"]), "indices,", len(doc["unresolved"]), "unresolved")


if __name__ == "__main__":
    sys.exit(main())
