"""
market_data_updater.py - StockLens Phase 5K: the end-of-day MARKET DATA FOUNDATION.

It does NOT download anything. nse_updater.py already downloads NSE's daily full-universe file (sec_bhavdata_full) into data/raw/bhav_YYYYMMDD.csv;
this script only READS those files (the same saved-data cache the production workflow restores), checks them, and writes separate derived files:

    out/market_breadth.json    advances / declines / unchanged, A/D ratio, % above the 20/50/200-day average, 52-week high/low counts
    out/market_movers.json     top gainers and losers (liquid stocks), with change, volume, turnover and delivery %
    out/market_activity.json   most active by traded value and by traded quantity, volume shockers, 52-week highs/lows, corporate-action flags
    out/market_dma.json        the 20/50/200-day averages per stock (the numbers behind the breadth percentages)

Principles: data quality over quantity. Nothing is estimated, filled in or reused from an older day without saying so. A value that cannot be computed
reliably is null with a status (safe / insufficient_history / requires_adjustment / unavailable). Every file says it is END-OF-DAY data and for which date.
If any gate fails nothing is written and the previous files stay exactly as they were (exit code 1).

The pure calculations are in market_derive.py; the schema checks are in validate_market_outputs.py. No token and no network are used here.
"""
import csv
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import market_derive as md
import validate_market_outputs as vmo

ROOT = Path(os.environ.get("DATA_DIR", "."))
RAW_DIR, OUT_DIR, HOLIDAYS_FILE = ROOT / "data" / "raw", ROOT / "out", ROOT / "data" / "holidays.txt"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SCHEMA_VERSION = 1
SOURCE = "NSE EOD bhav data (sec_bhavdata_full), read from the saved copy made by nse_updater.py"
FILES = {"breadth": "market_breadth.json", "movers": "market_movers.json", "activity": "market_activity.json", "dma": "market_dma.json"}

REQUIRED_COLS = ["SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "HIGH_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS"]
OPTIONAL_COLS = ["OPEN_PRICE", "LOW_PRICE", "DELIV_PER"]    # a file without them still loads; the metrics that need them say "unavailable"

GATE = {
    "min_eq_rows_per_file": 500,        # a daily file with fewer EQ rows is treated as a partial download
    "min_universe": 1000,               # latest day must have at least this many EQ stocks
    "max_universe_change": 0.10,        # +/-10% against the previous trading day is not believable
    "max_invalid_share": 0.05,          # more than 5% unusable rows on the latest day = something is wrong with the file
    "max_conflict_share": 0.01,         # more than 1% of symbols repeated with different numbers
    "max_stale_days": 5,                # the latest file may be at most this many calendar days older than the expected trading day
    "max_no_loss_drop": 0.10,           # a new universe more than 10% smaller than the published one is refused
    "extreme_change_pct": 50.0,         # not rejected, only counted: |1-day change| above this
}


# ---------------------------------------------------------------- numbers and dates
def fnum(v):
    """Text -> finite float, or None. Never NaN or Infinity."""
    if v is None:
        return None
    t = str(v).strip().replace(",", "")
    if t in ("", "-", "--"):
        return None
    try:
        x = float(t)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def parse_nse_date(s):
    """'03-Oct-2025' -> '2025-10-03', or None."""
    try:
        return dt.datetime.strptime(str(s).strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def read_holidays(path=HOLIDAYS_FILE):
    try:
        return {t for t in Path(path).read_text().split() if len(t) == 10}
    except OSError:
        return set()


def expected_latest_trading_day(now, holidays):
    """The most recent weekday (not a known holiday) whose end-of-day file should exist by `now` (IST). NSE files appear after about 20:30 IST."""
    d = now.date() if (now.hour, now.minute) >= (20, 30) else now.date() - dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def market_session_open(now, holidays):
    """True during the regular NSE session (09:15-15:30 IST on a trading weekday)."""
    if now.weekday() >= 5 or now.date().isoformat() in holidays:
        return False
    return (9, 15) <= (now.hour, now.minute) <= (15, 30)


def calendar_gaps(dates, holidays):
    """Weekdays between the first and last date that have no file and are not known holidays."""
    if not dates:
        return []
    ds = sorted(dates)
    d, end, have, gaps = dt.date.fromisoformat(ds[0]), dt.date.fromisoformat(ds[-1]), set(ds), []
    while d <= end:
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in have and iso not in holidays:
            gaps.append(iso)
        d += dt.timedelta(days=1)
    return gaps


# ---------------------------------------------------------------- 1. load raw files
def parse_bhav_file(path, name_date):
    """One raw file -> (raw row dicts, report). A file that is empty, malformed, missing core columns or for another date is rejected whole."""
    rep = {"file": Path(path).name, "date": name_date, "status": "ok", "rows": 0, "problem": None, "missing_optional": []}
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return None, dict(rep, status="unreadable", problem=type(e).__name__)
    if not data.strip():
        return None, dict(rep, status="empty", problem="the file has no content")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, dict(rep, status="malformed", problem="not valid text")
    try:
        reader = csv.reader(text.splitlines())
        header = [h.strip() for h in next(reader)]
        rows = [r for r in reader if any(c.strip() for c in r)]
    except (csv.Error, StopIteration):
        return None, dict(rep, status="malformed", problem="not readable as CSV")
    missing = [c for c in REQUIRED_COLS if c not in header]
    if missing:
        return None, dict(rep, status="missing_columns", problem="missing columns: " + ", ".join(missing))
    if not rows:
        return None, dict(rep, status="empty", problem="the file has a header but no rows")
    if any(len(r) != len(header) for r in rows):
        bad = sum(1 for r in rows if len(r) != len(header))
        if bad / len(rows) > 0.01:
            return None, dict(rep, status="malformed", problem="%d of %d rows have the wrong number of fields (partial download?)" % (bad, len(rows)))
        rows = [r for r in rows if len(r) == len(header)]
    out = [{h: c.strip() for h, c in zip(header, r)} for r in rows]
    rep["rows"], rep["missing_optional"] = len(out), [c for c in OPTIONAL_COLS if c not in header]
    wrong = sum(1 for r in out if parse_nse_date(r.get("DATE1")) != name_date)
    if wrong:
        return None, dict(rep, status="date_mismatch", problem="%d row(s) are not dated %s" % (wrong, name_date))
    return out, rep


def clean_day(raw, cfg=GATE):
    """Raw rows of one file -> (clean rows, counts). Keeps series EQ only; resolves duplicates; drops or nulls values that are impossible."""
    c = {"raw_rows": len(raw), "other_series": 0, "eq_rows": 0, "duplicate_identical": 0, "conflicting_duplicate_symbols": 0, "invalid_close": 0,
         "missing_prev_close": 0, "invalid_volume": 0, "invalid_turnover": 0, "invalid_price_field": 0, "invalid_delivery_pct": 0,
         "extreme_change": 0, "usable_rows": 0}
    eq = {}
    for r in raw:
        if r.get("SERIES", "").strip() != "EQ":
            c["other_series"] += 1
            continue
        eq.setdefault(r.get("SYMBOL", "").strip(), []).append(r)
    rows = []
    for sym, group in sorted(eq.items()):
        c["eq_rows"] += len(group)
        if not sym:
            c["invalid_close"] += len(group)
            continue
        key = lambda r: tuple(r.get(k) for k in REQUIRED_COLS + OPTIONAL_COLS)
        if len(group) > 1:
            if len({key(g) for g in group}) == 1:
                c["duplicate_identical"] += len(group) - 1
            else:
                c["conflicting_duplicate_symbols"] += 1
                continue
        r = group[0]
        close = fnum(r.get("CLOSE_PRICE"))
        if close is None or close <= 0:
            c["invalid_close"] += 1
            continue
        prev = fnum(r.get("PREV_CLOSE"))
        if prev is None or prev <= 0:
            c["missing_prev_close"] += 1
            prev = None
        vol, tov = fnum(r.get("TTL_TRD_QNTY")), fnum(r.get("TURNOVER_LACS"))
        if vol is not None and vol < 0:
            c["invalid_volume"] += 1
            vol = None
        if tov is not None and tov < 0:
            c["invalid_turnover"] += 1
            tov = None
        hi, lo, op = fnum(r.get("HIGH_PRICE")), fnum(r.get("LOW_PRICE")), fnum(r.get("OPEN_PRICE"))
        for nm in ("hi", "lo", "op"):
            v = {"hi": hi, "lo": lo, "op": op}[nm]
            if v is not None and v <= 0:
                c["invalid_price_field"] += 1
                if nm == "hi":
                    hi = None
                elif nm == "lo":
                    lo = None
                else:
                    op = None
        if hi is not None and lo is not None and hi < lo:
            c["invalid_price_field"] += 1
            hi = lo = None
        dp = fnum(r.get("DELIV_PER"))
        if dp is not None and not 0 <= dp <= 100:
            c["invalid_delivery_pct"] += 1
            dp = None
        row = {"symbol": sym, "date": None, "prev_close": prev, "open": op, "high": hi, "low": lo, "close": close,
               "volume": int(vol) if vol is not None else None, "turnover": tov, "deliv": dp}
        _, pct = md.change_of(row)
        if pct is not None and abs(pct) > cfg["extreme_change_pct"]:
            c["extreme_change"] += 1
        rows.append(row)
    c["usable_rows"] = len(rows)
    return rows, c


def load_days(raw_dir, today_ist, cfg=GATE):
    """Every readable file -> ({date: clean rows}, [file reports], {date: counts}). Files that fail a file-level check are rejected and reported."""
    days, reports, counts = {}, [], {}
    files = sorted(Path(raw_dir).glob("bhav_*.csv")) if Path(raw_dir).is_dir() else []
    for f in files:
        stem = f.stem.replace("bhav_", "")
        try:
            name_date = dt.datetime.strptime(stem, "%Y%m%d").date()
        except ValueError:
            reports.append({"file": f.name, "date": None, "status": "bad_file_name", "rows": 0, "problem": "name is not bhav_YYYYMMDD.csv", "missing_optional": []})
            continue
        iso = name_date.isoformat()
        raw, rep = parse_bhav_file(f, iso)
        if raw is not None and name_date.weekday() >= 5:
            raw, rep = None, dict(rep, status="weekend_file", problem="dated on a weekend")
        if raw is not None and name_date > today_ist:
            raw, rep = None, dict(rep, status="future_date", problem="dated after today")
        if raw is not None:
            rows, c = clean_day(raw, cfg)
            for r in rows:
                r["date"] = iso
            if c["eq_rows"] < cfg["min_eq_rows_per_file"]:
                raw, rep = None, dict(rep, status="partial", problem="only %d EQ rows (a full file has well over %d)" % (c["eq_rows"], cfg["min_eq_rows_per_file"]))
            else:
                days[iso], counts[iso] = rows, c
        reports.append(rep)
    return days, reports, counts


# ---------------------------------------------------------------- 2. the data-quality gate
def assess(days, reports, counts, now, holidays, cfg=GATE):
    """Checks everything that must be true before anything is published. Returns the data_quality block; status is 'pass' or 'fail' (with errors)."""
    errors, warnings = [], []
    rejected = [{"file": r["file"], "status": r["status"], "problem": r["problem"]} for r in reports if r["status"] != "ok"]
    dq = {"status": "pass", "errors": errors, "warnings": warnings, "files_seen": len(reports), "files_used": len(days), "files_rejected": rejected}
    if not reports:
        errors.append("no raw bhav files were found: nothing to compute from (the saved data is missing or empty)")
    if not days:
        if reports:
            errors.append("every raw bhav file was rejected")
        dq["status"] = "fail"
        return dq, None
    latest = max(days)
    newer_rejected = [r["file"] for r in reports if r["status"] != "ok" and r["date"] and r["date"] > latest]
    if newer_rejected:
        errors.append("a newer file was rejected, so the latest usable day is older than the latest file: " + ", ".join(newer_rejected))
    expected = expected_latest_trading_day(now, holidays)
    stale = (dt.date.fromisoformat(expected) - dt.date.fromisoformat(latest)).days
    is_latest = latest >= expected
    if latest > now.date().isoformat():
        errors.append("the latest date %s is in the future" % latest)
    if stale > cfg["max_stale_days"]:
        errors.append("the latest day %s is %d days older than the expected trading day %s: the saved data is stale" % (latest, stale, expected))
    elif not is_latest:
        warnings.append("the latest day %s is not yet the expected trading day %s (the file may not be published yet); the numbers are the earlier close" % (latest, expected))
    c = counts[latest]
    n = len(days[latest])
    if n < cfg["min_universe"]:
        errors.append("only %d EQ stocks on %s (expected at least %d): partial or wrong file" % (n, latest, cfg["min_universe"]))
    earlier = sorted(d for d in days if d < latest)
    if earlier:
        prev_n = len(days[earlier[-1]])
        if prev_n:
            ch = (n - prev_n) / prev_n
            if abs(ch) > cfg["max_universe_change"]:
                errors.append("the universe changed from %d (%s) to %d (%s): %+.1f%%, more than the %d%% allowed" % (prev_n, earlier[-1], n, latest, ch * 100, cfg["max_universe_change"] * 100))
    if c["eq_rows"]:
        if (c["invalid_close"]) / c["eq_rows"] > cfg["max_invalid_share"]:
            errors.append("%d of %d EQ rows on %s have no usable close" % (c["invalid_close"], c["eq_rows"], latest))
        if c["conflicting_duplicate_symbols"] / max(1, c["eq_rows"]) > cfg["max_conflict_share"]:
            errors.append("%d symbols are repeated with different numbers on %s" % (c["conflicting_duplicate_symbols"], latest))
    if c["eq_rows"] == 0:
        errors.append("the latest file has no EQ-series rows")
    for k, msg in (("invalid_volume", "negative volume"), ("invalid_turnover", "negative turnover"), ("invalid_delivery_pct", "delivery % outside 0-100"),
                   ("invalid_price_field", "impossible open/high/low"), ("missing_prev_close", "missing previous close"), ("conflicting_duplicate_symbols", "conflicting duplicate symbols"),
                   ("duplicate_identical", "identical duplicate rows (removed)")):
        if c[k]:
            warnings.append("%d %s on %s" % (c[k], msg, latest))
    gaps = calendar_gaps(list(days), holidays)
    recent = [g for g in gaps if g >= (dt.date.fromisoformat(latest) - dt.timedelta(days=400)).isoformat()]
    if recent:
        warnings.append("%d weekday(s) have no file and are not known holidays; averages across them use the days that exist" % len(recent))
    for r in reports:
        if r["status"] != "ok":
            warnings.append("file %s rejected: %s" % (r["file"], r["problem"]))
    missing_opt = sorted({m for r in reports if r["status"] == "ok" for m in r["missing_optional"]})
    if missing_opt:
        warnings.append("optional columns missing from some files: " + ", ".join(missing_opt))
    dq.update({"latest_date": latest, "expected_latest_trading_day": expected, "source_is_latest_trading_day": is_latest, "stale_days": max(0, stale),
               "latest_day_rows": dict(c), "trading_days_loaded": len(days), "first_date": min(days), "unexplained_weekday_gaps": len(gaps),
               "checks": {"dates_valid_and_not_future": latest <= now.date().isoformat(), "series_filter": "EQ only", "duplicates_resolved": True,
                          "universe_vs_previous_day": "checked", "schema_columns": "checked", "percentages": "checked"}})
    if errors:
        dq["status"] = "fail"
    return dq, latest


# ---------------------------------------------------------------- 3. calculations -> documents
def envelope(kind, as_of, now, universe_count, filters, dq, is_latest, session_open=False):
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "as_of": as_of, "source": SOURCE, "freshness": "EOD",
            "freshness_detail": {"label": "EOD as of " + as_of, "source_date": as_of, "generated_at": now.isoformat(timespec="seconds"),
                                 "source_is_latest_trading_day": is_latest, "market_session_open_at_generation": session_open,
                                 "note": "End-of-day data: the last close, not real-time prices."},
            "generated_at": now.isoformat(timespec="seconds"), "universe": "NSE EQ series", "universe_count": universe_count, "filters": filters, "data_quality": dq}


def compute(days, latest, dq, now, cfg=md.DEFAULTS, holidays=frozenset()):
    """Everything derived from the clean days. Returns the four documents."""
    series = md.build_series(days)
    today_rows = sorted((s[-1] for s in series.values() if s[-1]["date"] == latest), key=lambda r: r["symbol"])
    symbols = [r["symbol"] for r in today_rows]
    liquid = [r for r in today_rows if md.is_liquid(r, cfg)]
    liquid_syms = {r["symbol"] for r in liquid}
    events = {s: md.adjustment_events(series[s], cfg) for s in symbols}
    dma = {s: {p: md.dma_for(series[s], p, events[s], cfg) for p in cfg["dma_periods"]} for s in symbols}
    hl = [md.high_low_52w(series[s], events[s], cfg) for s in symbols]
    vol = {s: md.volume_stats(series[s], events[s], cfg) for s in symbols}
    is_latest = dq.get("source_is_latest_trading_day", False)
    open_now = market_session_open(now, holidays)
    has_low = any(r["low"] is not None for r in today_rows)
    filters = {"universe_filter": "none: every EQ-series stock with a valid close", "liquidity_filter": {"min_turnover_lakhs": cfg["liquid_min_turnover_lakhs"], "meaning": "turnover of at least Rs 1 crore on the day",
               "applies_to": ["movers", "volume", "breadth.liquid", "dma_breadth.liquid"], "does_not_apply_to": ["most_active", "breadth.all_eq", "dma_breadth.all_eq", "high_low_52w"]}}
    # --- breadth
    dmab = {}
    for p in cfg["dma_periods"]:
        dmab[str(p)] = {"all_eq": md.dma_breadth(dma, p, symbols), "liquid": md.dma_breadth(dma, p, [s for s in symbols if s in liquid_syms])}
    counts52 = {}
    for name, kind, state in (("new_52w_high", "high", "new_high"), ("new_52w_low", "low", "new_low")):
        num = den = ins = adj = una = 0
        for h in hl:
            if h["status"] == md.INSUFFICIENT:
                ins += 1
            elif h["status"] == md.ADJUST:
                adj += 1
            elif h[kind]["status"] != md.SAFE:
                una += 1
            else:
                den += 1
                num += h[kind]["state"] == state
        st = md.SAFE if den else (md.UNAVAILABLE if una and not ins and not adj else md.ADJUST if adj and not ins else md.INSUFFICIENT)
        counts52[name] = {"count": num if den else None, "denominator": den, "status": st, "excluded": {md.INSUFFICIENT: ins, md.ADJUST: adj, md.UNAVAILABLE: una}}
    n_trading = len(days)
    ca_total = sum(1 for s in symbols if events[s])
    metric = {"advance_decline_1d": md.SAFE if today_rows else md.UNAVAILABLE, "most_active": md.SAFE if today_rows else md.UNAVAILABLE,
              "movers_1d": md.SAFE if today_rows else md.UNAVAILABLE}
    for p in cfg["dma_periods"]:
        metric["above_%d_dma" % p] = dmab[str(p)]["all_eq"]["status"]
    metric["new_52w_high"], metric["new_52w_low"] = counts52["new_52w_high"]["status"], counts52["new_52w_low"]["status"]
    nsafe = sum(1 for s in symbols if vol[s]["status"] == md.SAFE)
    metric["volume_vs_20d_average"] = md.SAFE if nsafe else (md.ADJUST if any(v["status"] == md.ADJUST for v in vol.values()) and not any(v["status"] == md.INSUFFICIENT for v in vol.values()) else md.INSUFFICIENT)
    metric["performance_30d_90d_all_market"] = md.ADJUST      # deliberately NOT published: raw closes are not corporate-action adjusted
    ca = {"threshold_pct": cfg["ca_threshold"] * 100, "stocks_flagged": ca_total,
          "rule": "a stock is flagged when its previous-close reference on some day differs from its own last close by more than the threshold (NSE adjusts the reference on the ex-date of a split, bonus or rights issue)",
          "effect": "a flagged stock is left out of every metric whose window contains the event (moving averages, 52-week high/low, 20-day volume baseline), never silently included",
          "events": sorted(({"symbol": s, "date": e["date"], "ratio": e["ratio"]} for s in symbols for e in events[s]), key=lambda x: (x["date"], x["symbol"]), reverse=True)[:300]}
    dq2 = dict(dq)
    docs = {}
    docs["breadth"] = dict(envelope("market_breadth", latest, now, len(today_rows), filters, dq2, is_latest, open_now), metric_status=metric,
                           breadth={"all_eq": md.breadth(today_rows), "liquid": md.breadth(liquid)}, dma_breadth=dmab, high_low_52w_counts=counts52,
                           definitions={"advancing": "close above NSE's adjusted previous close", "ad_ratio": "advancing divided by declining; null when there are no decliners",
                                        "above_dma": "close strictly above the simple average of the last N closes including today; only stocks with N real observations and no suspected corporate action in the window are counted in the denominator",
                                        "new_52w_high": "close above the highest HIGH of the previous 252 trading days", "new_52w_low": "close below the lowest LOW of the previous 252 trading days"})
    mv = md.movers(today_rows, cfg)
    docs["movers"] = dict(envelope("market_movers", latest, now, len(today_rows), filters, dq2, is_latest, open_now), metric_status={"movers_1d": metric["movers_1d"]},
                          liquid_universe_count=len(liquid), gainers=mv["gainers"], losers=mv["losers"],
                          definitions={"change_pct": "(close / NSE adjusted previous close - 1) x 100", "turnover_lakhs": "traded value in Rs lakhs", "volume": "traded quantity in shares", "rank": "1 = largest move; ties broken by symbol"})
    ma = md.most_active(today_rows, cfg)
    vl = md.volume_lists(today_rows, vol, cfg)
    hll = md.high_low_lists(hl, cfg)
    docs["activity"] = dict(envelope("market_activity", latest, now, len(today_rows), filters, dq2, is_latest, open_now),
                            metric_status={k: metric[k] for k in ("most_active", "volume_vs_20d_average", "new_52w_high", "new_52w_low", "performance_30d_90d_all_market")},
                            most_active={"by_value": {"ranked_by": "turnover_lakhs (traded value, Rs lakhs)", "rows": ma["by_value"]},
                                         "by_volume": {"ranked_by": "volume (traded quantity, shares)", "rows": ma["by_volume"]}},
                            volume={"baseline": "average volume of the previous %d trading days (the latest day is not in its own baseline)" % cfg["vol_period"],
                                    "shocker_rule": "volume at least %.1fx the baseline, liquid stocks only, either direction" % cfg["vol_multiple_min"],
                                    "high_volume_high_gain_rule": "shocker with a gain of at least %.1f%%" % cfg["hv_gain_pct"],
                                    "high_volume_top_losers_rule": "shocker with a loss of at least %.1f%%" % -cfg["hv_loss_pct"],
                                    "candidates": vl["candidates"], "shockers": vl["shockers"], "high_volume_high_gain": vl["high_volume_high_gain"], "high_volume_top_losers": vl["high_volume_top_losers"]},
                            high_low_52w=dict(hll, window="previous 252 trading days; the latest day is not in its own window", near_rule="within %.1f%% without having crossed" % cfg["near_pct"],
                                              low_column_available=has_low),
                            corporate_action_flags=ca)
    docs["dma"] = dict(envelope("market_dma", latest, now, len(today_rows), filters, dq2, is_latest, open_now), metric_status={k: v for k, v in metric.items() if k.startswith("above_")},
                       methodology="simple average of the last N closes including the latest; N real observations required; unadjusted closes, so a window containing a suspected corporate action is reported as requires_adjustment",
                       stocks=[{"symbol": s, "close": series[s][-1]["close"], "liquid": s in liquid_syms,
                                "dma": {str(p): {"value": d["value"], "observations": d["observations"], "status": d["status"], "position": d["position"]} for p, d in dma[s].items()}} for s in symbols])
    docs["_trading_days"] = n_trading
    return docs


# ---------------------------------------------------------------- 4. write
def sanitize(x):
    """Replace any non-finite number by None, recursively, so the JSON is always strict."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: sanitize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [sanitize(v) for v in x]
    return x


def dumps(doc):
    return json.dumps(sanitize(doc), indent=1, allow_nan=False, ensure_ascii=False)


def check_no_loss(new_breadth, old_path, cfg=GATE):
    """Never replace a published dataset with an older or dramatically smaller one."""
    try:
        old = json.loads(Path(old_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(old, dict) or not isinstance(old.get("as_of"), str):
        return []
    p = []
    if new_breadth["as_of"] < old["as_of"]:
        p.append("the new data is for %s but %s is already published: refusing to go back in time" % (new_breadth["as_of"], old["as_of"]))
    oc = old.get("universe_count")
    if isinstance(oc, int) and oc > 0 and new_breadth["universe_count"] < oc * (1 - cfg["max_no_loss_drop"]):
        p.append("the new universe (%d stocks) is more than %d%% smaller than the published one (%d): refusing to replace it" % (new_breadth["universe_count"], cfg["max_no_loss_drop"] * 100, oc))
    return p


def write_all(out_dir, docs):
    """All four files are written to temporary names first and renamed only when every one succeeded."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmp = {}
    try:
        for k, name in FILES.items():
            t = out / (name + ".tmp")
            t.write_text(dumps(docs[k]) + "\n", encoding="utf-8")
            tmp[k] = t
        for k, name in FILES.items():
            os.replace(tmp[k], out / name)
    finally:
        for t in tmp.values():
            if t.exists():
                t.unlink()


def summary(lines):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def run(root=ROOT, now=None, cfg=GATE):
    """The whole pipeline. Returns (exit_code, messages)."""
    raw_dir, out_dir = Path(root) / "data" / "raw", Path(root) / "out"
    now = now or dt.datetime.now(IST)
    holidays = read_holidays(Path(root) / "data" / "holidays.txt")
    days, reports, counts = load_days(raw_dir, now.date(), cfg)
    dq, latest = assess(days, reports, counts, now, holidays, cfg)
    if dq["status"] != "pass":
        return 1, ["data-quality gate FAILED: " + e for e in dq["errors"]]
    docs = compute(days, latest, dq, now, md.DEFAULTS, holidays)
    problems = vmo.validate(docs)
    problems += check_no_loss(docs["breadth"], out_dir / FILES["breadth"], cfg)
    if problems:
        return 1, ["output check FAILED: " + p for p in problems]
    write_all(out_dir, docs)
    return 0, ["wrote %d files for %s: %d EQ stocks, %d trading days of history, %d warning(s)" % (len(FILES), latest, docs["breadth"]["universe_count"], docs["_trading_days"], len(dq["warnings"]))] + \
           ["warning: " + w for w in dq["warnings"]]


def main():
    code, msgs = run()
    for m in msgs:
        print(("::error::" if code else "") + m if (code or not m.startswith("warning")) else "::warning::" + m[9:], flush=True)
    summary(["### Market data (Phase 5K): " + ("FAILED - nothing was written, the previous files are unchanged" if code else "OK"), ""] + ["- " + m for m in msgs])
    return code


if __name__ == "__main__":
    sys.exit(main())
