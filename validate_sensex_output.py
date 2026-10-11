"""
validate_sensex_output.py - StockLens: offline check of out/sensex_history.json, written by sensex_data_updater.py.

    python validate_sensex_output.py [path]        default path: out/sensex_history.json      exit 0 = the file is good, exit 1 = it is not (or is missing)

It is the second line of defence after the checks the updater already makes before it writes (the "Validate" step of sensex_data.yml). It only READS the file.
It prints WHAT is wrong (field names, counts, dates) and never a price, a credential or any part of the file's contents.

The file is accepted only when ALL of these hold:
  * it exists, is not larger than MAX_BYTES, and is a strict JSON object with exactly the keys the updater writes;
  * kind "sensex_history", schema_version 1, interval "1day", freshness "EOD", and the instrument is SENSEX / BSE_INDEX|SENSEX (BSE, BSE_INDEX, INDEX);
  * the candles are valid (a real calendar date, finite open/high/low/close above zero, high not below low, volume empty or a positive whole number),
    strictly ascending by date with no duplicate, none in the future, and the newest is not older than MAX_STALE_DAYS;
  * candle_count, first_date, last_date and as_of agree with the candles, and "latest" (date, close, previous close, change) agrees with the last two candles;
  * public_display_approved is exactly the boolean false. A file that approves itself is REFUSED here: nothing in sensex_data.yml may approve publication.

Standard library only; no network, no credentials.
"""
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / "out" / "sensex_history.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAX_BYTES = 5 * 1024 * 1024           # 800 days of candles is about 0.15 MB; anything near this size is not our file
MAX_STALE_DAYS = 7
MAX_PREV_GAP_DAYS = 7
KIND, SCHEMA_VERSION, INTERVAL, FRESHNESS = "sensex_history", 1, "1day", "EOD"
KEY, LABEL = "BSE_INDEX|SENSEX", "SENSEX"
TOP_KEYS = {"schema_version", "kind", "as_of", "source", "metadata_source", "freshness", "freshness_detail", "generated_at", "interval", "instrument",
            "first_date", "last_date", "candle_count", "latest", "data_rights_note", "public_display_approved", "candles"}
CANDLE_KEYS = {"date", "open", "high", "low", "close", "volume"}
INSTRUMENT = {"label": LABEL, "instrument_key": KEY, "exchange": "BSE", "segment": "BSE_INDEX", "instrument_type": "INDEX"}
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day(s):
    """A real calendar date as a date object, or None."""
    if not isinstance(s, str) or not DAY.match(s):
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def problems(doc, today=None):
    """-> list of problems (empty = valid). Messages name fields, counts and dates only, never a price."""
    today = today or dt.datetime.now(IST).date()
    if not isinstance(doc, dict):
        return ["the file is not a JSON object"]
    p = []
    keys = set(doc)
    if keys != TOP_KEYS:
        if TOP_KEYS - keys:
            p.append("missing fields: " + ", ".join(sorted(TOP_KEYS - keys)))
        if keys - TOP_KEYS:
            p.append("unexpected fields: " + ", ".join(sorted(str(k)[:30] for k in keys - TOP_KEYS)))
    if doc.get("kind") != KIND:
        p.append("kind is not " + KIND)
    if doc.get("schema_version") != SCHEMA_VERSION or isinstance(doc.get("schema_version"), bool):
        p.append("schema_version is not %d" % SCHEMA_VERSION)
    if doc.get("interval") != INTERVAL:
        p.append("interval is not " + INTERVAL)
    if doc.get("freshness") != FRESHNESS:
        p.append("freshness is not " + FRESHNESS)
    if doc.get("public_display_approved") is not False:
        p.append("public_display_approved must be exactly the boolean false (this workflow never approves publication)")
    ins = doc.get("instrument")
    if not isinstance(ins, dict):
        p.append("instrument is missing")
    else:
        for k, want in INSTRUMENT.items():
            if ins.get(k) != want:
                p.append("instrument." + k + " is not " + want)
    if not isinstance(doc.get("generated_at"), str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", doc.get("generated_at") or ""):
        p.append("generated_at is not a timestamp")
    cs = doc.get("candles")
    if not isinstance(cs, list) or not cs:
        p.append("candles is missing or empty")
        return p
    prev, bad = None, 0
    for c in cs:
        d = _day(c.get("date")) if isinstance(c, dict) else None
        ok = (d is not None and set(c) == CANDLE_KEYS and all(_num(c[k]) and c[k] > 0 for k in ("open", "high", "low", "close")) and c["high"] >= c["low"]
              and (c["volume"] is None or (isinstance(c["volume"], int) and not isinstance(c["volume"], bool) and c["volume"] > 0)))
        if not ok:
            bad += 1
            continue
        if prev is not None and d <= prev:
            p.append("candle dates are not strictly ascending at " + c["date"])
            break
        prev = d
    if bad:
        p.append("%d malformed candle(s)" % bad)
    if bad or any(m.startswith("candle dates") for m in p):
        return p
    last, first = _day(cs[-1]["date"]), _day(cs[0]["date"])
    if last > today:
        p.append("the newest candle is dated in the future (" + cs[-1]["date"] + ")")
    elif (today - last).days > MAX_STALE_DAYS:
        p.append("the newest candle (%s) is more than %d days old" % (cs[-1]["date"], MAX_STALE_DAYS))
    if doc.get("candle_count") != len(cs) or isinstance(doc.get("candle_count"), bool):
        p.append("candle_count does not match the candles")
    if doc.get("first_date") != cs[0]["date"]:
        p.append("first_date does not match the first candle")
    if doc.get("last_date") != cs[-1]["date"]:
        p.append("last_date does not match the last candle")
    if doc.get("as_of") != cs[-1]["date"]:
        p.append("as_of does not match the last candle")
    lt = doc.get("latest")
    if not isinstance(lt, dict) or set(lt) != {"date", "close", "prev_close", "change_pct"}:
        p.append("latest is missing or has the wrong fields")
    else:
        if lt["date"] != cs[-1]["date"] or lt["close"] != cs[-1]["close"]:
            p.append("latest does not match the last candle")
        want_prev = None
        if len(cs) > 1 and (last - _day(cs[-2]["date"])).days <= MAX_PREV_GAP_DAYS:
            want_prev = cs[-2]["close"]
        if lt["prev_close"] != want_prev:
            p.append("latest.prev_close does not match the previous candle")
        elif want_prev is not None and lt["change_pct"] != round((cs[-1]["close"] / want_prev - 1) * 100, 2):
            p.append("latest.change_pct does not match the last two candles")
        elif want_prev is None and lt["change_pct"] is not None:
            p.append("latest.change_pct must be empty without a previous close")
    fd = doc.get("freshness_detail")
    if not isinstance(fd, dict) or fd.get("source_date") != cs[-1]["date"]:
        p.append("freshness_detail.source_date does not match the last candle")
    return p


def _reject_constant(name):
    raise ValueError("not strict JSON: " + name)


def check_file(path, today=None):
    """-> list of problems for the file at `path` (a missing or unreadable file is a problem)."""
    path = Path(path)
    try:
        if not path.is_file():
            return ["the file does not exist: " + path.name]
        if path.stat().st_size > MAX_BYTES:
            return ["the file is larger than the safety limit"]
        doc = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)        # NaN / Infinity are refused
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return ["the file cannot be read as strict JSON: " + path.name]
    return problems(doc, today)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) > 1:
        print("usage: python validate_sensex_output.py [path]", file=sys.stderr)
        return 2
    path = Path(a[0]) if a else DEFAULT_PATH
    found = check_file(path)
    for m in found:
        print("::error::validate_sensex_output: " + m)
    if found:
        print("SENSEX output is NOT valid (%d problem(s)); it will not be saved." % len(found))
        return 1
    print("SENSEX output is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
