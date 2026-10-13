"""
live/scanner.py - the live market scanner engine. PURE: no network, no file access, no clock (the caller passes "now" in milliseconds), no token, no stock list in code.

  QuoteBook      keeps the latest valid quote per instrument. The price rules are live.ticks.TickStore's (bad price, future/out-of-order time, one-tick jumps, previous close kept);
                 on top of them this adds the FULL-mode extras (live.decoder_full): cumulative volume (must never go down), average traded price and the session OHLC (must contain the price).
  baseline_*     saved DAILY history -> 52-week high/low and the 20-session average volume. Only completed sessions BEFORE the live session are used.
  build_scan     the document the page shows: top gainers/losers, big movers, most active by volume, approximate turnover, unusual volume, 52-week highs/lows, breadth, indices, coverage.

Rules that apply everywhere (a number that cannot be justified is left out and COUNTED, never guessed):
  * Change % = (last price / previous close - 1) * 100 from the feed's own two numbers. No previous close -> not ranked. A change beyond extreme_pct is withheld (counted as extreme).
  * "fresh" (mode "live"): the instrument traded on the session date and its exchange timestamp is at most fresh_ms old. Mode "closing": it traded on the session date. Nothing else is ranked.
  * Volume ranking uses the feed's cumulative volume traded today (vtt), never the last trade quantity.
  * Turnover is "approximate traded value" = average traded price x cumulative volume, labelled so. It is not exchange-reported turnover; it is absent when either factor is missing.
  * Unusual volume = today's volume against the instrument's own 20-session average, never plain high volume. Live it is volume / (average x fraction of the session elapsed) - a LINEAR
    time profile, which is only an approximation of the real (U-shaped) intraday profile, so it is labelled "pace" and needs rvol_min_fraction of the session to have passed. In mode "closing"
    the comparison is the whole day against the average (no profile needed).
  * 52-week high/low are NOT in the live feed (not in Upstox's documented fields). They come from saved daily history: the max high / min low of the completed sessions of the last 365 days, needing
    hl_min_candles of them, and withheld when an unexplained jump of more than hl_gap_ratio between two closes suggests an unadjusted split. The live price is compared with them.
  * A stock without a usable baseline is simply absent from the lists that need one; the coverage block says how many have one.
"""
import datetime as dt
import math

from . import ticks as ticks_mod

SCHEMA = 1
KIND = "live_scan"
SOURCE = "Upstox market data feed V3 (full)"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SESSION_OPEN_MIN, SESSION_MINUTES = 9 * 60 + 15, 375          # 09:15 - 15:30
DEFAULTS = {
    "top_n": 20, "fresh_ms": 15 * 60_000, "index_fresh_ms": 60_000, "extreme_pct": 40.0,
    "mover_min_pct": 3.0, "mover_min_abs": 0.0, "mover_min_price": 0.0, "min_volume": 0,
    "rvol_min": 2.0, "rvol_min_fraction": 0.2, "rvol_min_sessions": 15, "rvol_min_avg_volume": 10_000, "rvol_days": 20,
    "near_pct": 3.0, "at_pct": 0.25, "hl_min_candles": 200, "hl_window_days": 365, "hl_gap_ratio": 0.4, "baseline_max_age_days": 10,
    "ohlc_tolerance": 0.005,
}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def iso(ms):
    try:
        return dt.datetime.fromtimestamp(ms / 1000, IST).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def session_fraction(now_ms, session_date):
    """Fraction (0..1) of the 09:15-15:30 session that has elapsed at now_ms on session_date. 0 before the open, 1 after the close."""
    try:
        d = dt.date.fromisoformat(session_date)
    except (TypeError, ValueError):
        return 0.0
    start = dt.datetime.combine(d, dt.time(9, 15), IST).timestamp() * 1000
    return max(0.0, min(1.0, (now_ms - start) / (SESSION_MINUTES * 60_000)))


# ---------------------------------------------------------------- the quote book
class QuoteBook:
    def __init__(self, instruments, session_date=None, cfg=None):
        self.ins = instruments
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.session_date = session_date
        self.ticks = ticks_mod.TickStore(instruments, session_date, cfg)
        self.extra = {}                                 # instrument_key -> {"vtt", "atp", "day": {"open","high","low"}|None}
        self.field_counts = {"feeds_with_extra": 0, "vtt": 0, "atp": 0, "day": 0}
        self.rejected = {}                              # reason -> count, for the extras only

    def begin_connection(self, now_ms):
        self.ticks.begin_connection(now_ms)

    def set_session_date(self, iso_date):
        self.session_date = iso_date
        self.ticks.set_session_date(iso_date)

    def _reject(self, reason):
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def apply_frame(self, frame, now_ms):
        """frame: live.decoder_full.decode_frame_full output. Returns the TickStore result."""
        res = self.ticks.apply_frame(frame, now_ms)
        for key, feed in (frame.get("feeds") or {}).items():
            ex = (feed or {}).get("extra")
            rec = self.ticks.ticks.get(key)
            if ex is None or rec is None or rec["kind"] != "equity" or rec["recv_ms"] != now_ms:
                continue                                # only extras that arrive with an ACCEPTED price tick are used
            self.field_counts["feeds_with_extra"] += 1
            self._apply_extra(key, rec, ex)
        return res

    def _apply_extra(self, key, rec, ex):
        cur = self.extra.setdefault(key, {"vtt": None, "atp": None, "day": None})
        vtt = ex.get("vtt")
        if vtt is not None:
            self.field_counts["vtt"] += 1
            if not isinstance(vtt, int) or isinstance(vtt, bool) or vtt < 0:
                self._reject("bad_volume")
            elif cur["vtt"] is not None and vtt < cur["vtt"]:
                self._reject("volume_decreased")        # a cumulative volume never goes down: the older value stays
            else:
                cur["vtt"] = vtt
        atp = ex.get("atp")
        if atp is not None:
            self.field_counts["atp"] += 1
            if not _num(atp) or atp <= 0:
                self._reject("bad_atp")
            else:
                cur["atp"] = float(atp)
        day = ex.get("day")
        if day is not None:
            self.field_counts["day"] += 1
            o, h, l = day.get("open"), day.get("high"), day.get("low")
            tol = self.cfg["ohlc_tolerance"]
            if not (_num(o) and _num(h) and _num(l)) or l <= 0 or h < l or not (l * (1 - tol) <= rec["ltp"] <= h * (1 + tol)):
                self._reject("day_ohlc_inconsistent")   # a session range that does not contain the price is not trusted
            else:
                cur["day"] = {"open": float(o), "high": float(h), "low": float(l)}

    def quote(self, key):
        rec = self.ticks.ticks.get(key)
        if rec is None:
            return None
        return dict(rec, **{"extra": self.extra.get(key) or {"vtt": None, "atp": None, "day": None}})


# ---------------------------------------------------------------- baselines from saved daily history
def baseline_from_candles(candles, session_date, cfg=None):
    """candles: ascending [{"date","open","high","low","close","volume"}]. -> {"as_of", "sessions", "hi52", "lo52", "avg_vol", "vol_sessions", "withheld": [reasons]}.
    Only candles dated BEFORE session_date are used (the live session is never part of its own baseline)."""
    c = dict(DEFAULTS, **(cfg or {}))
    out = {"as_of": None, "sessions": 0, "hi52": None, "lo52": None, "avg_vol": None, "vol_sessions": 0, "withheld": []}
    try:
        sd = dt.date.fromisoformat(session_date)
    except (TypeError, ValueError):
        out["withheld"].append("bad_session_date")
        return out
    prior = [x for x in (candles or []) if isinstance(x, dict) and isinstance(x.get("date"), str) and x["date"] < session_date]
    if not prior:
        out["withheld"].append("no_history")
        return out
    out["as_of"] = prior[-1]["date"]
    if (sd - dt.date.fromisoformat(out["as_of"])).days > c["baseline_max_age_days"]:
        out["withheld"].append("history_too_old")
        return out
    floor = (sd - dt.timedelta(days=c["hl_window_days"])).isoformat()
    win = [x for x in prior if x["date"] >= floor and _num(x.get("high")) and _num(x.get("low")) and _num(x.get("close")) and x["low"] > 0 and x["high"] >= x["low"]]
    out["sessions"] = len(win)
    if len(win) < c["hl_min_candles"]:
        out["withheld"].append("52w_too_few_sessions")
    elif any(abs(b["close"] / a["close"] - 1) > c["hl_gap_ratio"] for a, b in zip(win, win[1:])):
        out["withheld"].append("52w_unadjusted_jump_suspected")
    else:
        out["hi52"] = max(x["high"] for x in win)
        out["lo52"] = min(x["low"] for x in win)
    vols = [x["volume"] for x in prior[-c["rvol_days"]:] if isinstance(x.get("volume"), int) and not isinstance(x.get("volume"), bool) and x["volume"] > 0]
    out["vol_sessions"] = len(vols)
    if len(vols) >= c["rvol_min_sessions"]:
        out["avg_vol"] = sum(vols) / len(vols)
    else:
        out["withheld"].append("volume_too_few_sessions")
    return out


# ---------------------------------------------------------------- the scan
def _row(sym, name, q, ltp, cp, pct):
    r = {"symbol": sym, "name": name, "ltp": ltp, "prev_close": cp, "change": round(ltp - cp, 2), "change_pct": round(pct, 2), "quote_time": iso(q["ltt"])}
    ex = q["extra"]
    if ex.get("day"):
        r["day_open"], r["day_high"], r["day_low"] = ex["day"]["open"], ex["day"]["high"], ex["day"]["low"]
    return r


def build_scan(book, directory, subscribed_keys, baselines, now_ms, market, mode="live", cfg=None):
    """directory: {symbol: name} (the published stock directory). subscribed_keys: the instrument keys actually subscribed. baselines: {symbol: baseline_from_candles(...)}.
    mode: "live" (market open) or "closing" (the last captured session, shown after the close). -> the scan document (plain dict, JSON-safe)."""
    c = dict(DEFAULTS, **(cfg or {}))
    if mode not in ("live", "closing"):
        raise ValueError("mode must be live or closing")
    sd = book.session_date
    frac = 1.0 if mode == "closing" else session_fraction(now_ms, sd)
    key_of = {s: k for k, s in book.ins.equities.items()}
    subscribed = set(subscribed_keys)
    cov = {"universe": len(directory), "no_instrument_key": 0, "subscribed": 0, "unavailable": 0, "quoted": 0, "fresh": 0, "stale": 0,
           "no_prev_close": 0, "extreme_withheld": 0, "with_volume": 0, "with_baseline_52w": 0, "with_baseline_volume": 0}
    rows = []                                                      # fresh, valid equities with a previous close
    for sym in sorted(directory):
        k = key_of.get(sym)
        if k is None:
            cov["no_instrument_key"] += 1
            continue
        if k not in subscribed:
            continue
        cov["subscribed"] += 1
        q = book.quote(k)
        if q is None:
            cov["unavailable"] += 1
            continue
        cov["quoted"] += 1
        traded = q.get("traded_today") is True
        age_ok = mode == "closing" or (q["ltt"] is not None and now_ms - q["ltt"] <= c["fresh_ms"])
        if not (traded and age_ok):
            cov["stale"] += 1
            continue
        cov["fresh"] += 1
        if q["cp"] is None:
            cov["no_prev_close"] += 1
            continue
        pct = (q["ltp"] / q["cp"] - 1) * 100
        if abs(pct) > c["extreme_pct"]:
            cov["extreme_withheld"] += 1
            continue
        rows.append((sym, directory[sym], q, q["ltp"], q["cp"], pct))
        if q["extra"].get("vtt") is not None:
            cov["with_volume"] += 1
        b = baselines.get(sym) or {}
        cov["with_baseline_52w"] += b.get("hi52") is not None
        cov["with_baseline_volume"] += b.get("avg_vol") is not None
    n = c["top_n"]
    base = [(r, _row(*r)) for r in rows]
    by_pct = sorted(base, key=lambda x: (-x[0][5], x[0][0]))
    gainers = [x[1] for x in by_pct if x[0][5] > 0][:n]
    losers = [x[1] for x in reversed(by_pct) if x[0][5] < 0][:n]
    losers.sort(key=lambda r: (r["change_pct"], r["symbol"]))
    movers = sorted((x for x in base if abs(x[0][5]) >= c["mover_min_pct"] and abs(x[0][3] - x[0][4]) >= c["mover_min_abs"] and x[0][3] >= c["mover_min_price"]),
                    key=lambda x: (-abs(x[0][5]), x[0][0]))
    movers = [x[1] for x in movers][:n]
    vol_rows, tov_rows, unusual = [], [], []
    for (sym, name, q, ltp, cp, pct), row in base:
        ex = q["extra"]
        vtt = ex.get("vtt")
        if vtt is not None and vtt >= c["min_volume"]:
            vol_rows.append(dict(row, volume=vtt))
            if ex.get("atp") is not None:
                tov_rows.append(dict(row, volume=vtt, avg_traded_price=ex["atp"], approx_traded_value=round(ex["atp"] * vtt, 2)))
            b = baselines.get(sym) or {}
            avg = b.get("avg_vol")
            if avg and avg >= c["rvol_min_avg_volume"] and frac >= (c["rvol_min_fraction"] if mode == "live" else 0.0) and frac > 0:
                rv = vtt / (avg * frac)
                if rv >= c["rvol_min"]:
                    unusual.append(dict(row, volume=vtt, avg_volume_20d=round(avg), volume_ratio=round(rv, 2)))
    vol_rows.sort(key=lambda r: (-r["volume"], r["symbol"]))
    tov_rows.sort(key=lambda r: (-r["approx_traded_value"], r["symbol"]))
    unusual.sort(key=lambda r: (-r["volume_ratio"], r["symbol"]))
    highs, lows = [], []
    for (sym, name, q, ltp, cp, pct), row in base:
        b = baselines.get(sym) or {}
        hi, lo = b.get("hi52"), b.get("lo52")
        if hi:
            d = (ltp / hi - 1) * 100
            st = "new_high" if ltp > hi else "at_high" if d >= -c["at_pct"] else "near_high" if d >= -c["near_pct"] else None
            if st:
                highs.append(dict(row, status=st, high_52w=hi, from_high_pct=round(d, 2)))
        if lo:
            d = (ltp / lo - 1) * 100
            st = "new_low" if ltp < lo else "at_low" if d <= c["at_pct"] else "near_low" if d <= c["near_pct"] else None
            if st:
                lows.append(dict(row, status=st, low_52w=lo, from_low_pct=round(d, 2)))
    highs.sort(key=lambda r: (-r["from_high_pct"], r["symbol"]))
    lows.sort(key=lambda r: (r["from_low_pct"], r["symbol"]))
    adv = sum(1 for x in rows if x[5] > 0)
    dec = sum(1 for x in rows if x[5] < 0)
    indices = []
    for k, label in book.ins.indices.items():
        q = book.quote(k)
        ok = q is not None and q.get("traded_today") is True and (mode == "closing" or (q["ltt"] is not None and now_ms - q["ltt"] <= c["index_fresh_ms"]))
        e = {"label": label, "fresh": bool(ok), "value": None, "prev_close": None, "change": None, "change_pct": None, "quote_time": None}
        if ok and q["cp"]:
            e.update(value=q["ltp"], prev_close=q["cp"], change=round(q["ltp"] - q["cp"], 2), change_pct=round((q["ltp"] / q["cp"] - 1) * 100, 2), quote_time=iso(q["ltt"]))
        indices.append(e)
    return {
        "schema_version": SCHEMA, "kind": KIND, "source": SOURCE, "mode": mode, "market": market, "session_date": sd, "generated_at": iso(now_ms),
        "label": ("Live" if mode == "live" else "Closing snapshot of " + str(sd)),
        "indices": indices,
        "breadth": {"advancing": adv, "declining": dec, "unchanged": len(rows) - adv - dec, "counted": len(rows)},
        "coverage": cov,
        "gainers": gainers, "losers": losers, "movers": movers,
        "most_active_volume": vol_rows[:n], "most_active_value": tov_rows[:n], "unusual_volume": unusual[:n],
        "highs_52w": highs[:n], "lows_52w": lows[:n],
        "counts_52w": {"new_high": sum(r["status"] == "new_high" for r in highs), "at_high": sum(r["status"] == "at_high" for r in highs), "near_high": sum(r["status"] == "near_high" for r in highs),
                       "new_low": sum(r["status"] == "new_low" for r in lows), "at_low": sum(r["status"] == "at_low" for r in lows), "near_low": sum(r["status"] == "near_low" for r in lows)},
        "rules": {"top_n": n, "fresh_ms": c["fresh_ms"], "mover_min_pct": c["mover_min_pct"], "mover_min_abs": c["mover_min_abs"], "rvol_min": c["rvol_min"],
                  "rvol_min_fraction": c["rvol_min_fraction"], "near_pct": c["near_pct"], "at_pct": c["at_pct"], "session_fraction": round(frac, 4),
                  "turnover": "approximate traded value = average traded price x cumulative volume (not exchange-reported turnover)",
                  "unusual_volume": "volume against the stock's own 20-session average; live it is a pace on a linear time profile (approximate)",
                  "52w": "from saved daily history (completed sessions, last 365 days); not part of the live feed"},
        "field_report": dict(book.field_counts, rejected=dict(book.rejected)),
    }
