"""
market_derive.py - StockLens Phase 5K: PURE calculations for the end-of-day market data foundation.

No file, network or clock access here: every function takes plain Python data and returns plain Python data, so each rule can be tested on its own.
market_data_updater.py loads and validates the NSE bhav files, calls these functions and writes the JSON.

Row shape (one stock on one trading day, already cleaned by market_data_updater.clean_day):
    {"symbol", "date" (YYYY-MM-DD), "prev_close", "open", "high", "low", "close", "volume", "turnover" (Rs lakhs), "deliv" (percent)}
Any value that is not reliably known is None. Nothing is estimated and nothing is filled in.

Every metric that depends on a run of past closes or volumes carries a status:
    safe                  computed from a complete, consistent window
    insufficient_history  fewer observations than the window needs (or the observations are too far apart)
    requires_adjustment   a suspected split / bonus / rights issue sits inside the window, so unadjusted closes or volumes would mislead
    unavailable           the source has no such field
The same status words are used for whole metrics (see metric_status in market_data_updater.py).
"""
import datetime as dt
import math

SAFE, INSUFFICIENT, ADJUST, UNAVAILABLE = "safe", "insufficient_history", "requires_adjustment", "unavailable"

DEFAULTS = {
    "liquid_min_turnover_lakhs": 100.0,   # Rs 1 crore traded on the day (the same cut-off nse_updater.py uses)
    "rows_movers": 50, "rows_active": 100, "rows_volume": 50, "rows_52w": 50,
    "near_pct": 2.0,                      # "near" a 52-week high or low = within this many percent, without having crossed it
    "vol_period": 20, "vol_multiple_min": 2.0, "hv_gain_pct": 3.0, "hv_loss_pct": -3.0,
    "hl_window": 252,                     # prior trading days (the current day is never part of its own comparison)
    "hl_max_span_days": 400,              # the 252 prior observations must fit inside this many calendar days
    "dma_periods": (20, 50, 200), "dma_span_factor": 2.0,   # N observations must fit inside N * 2 calendar days
    "vol_max_span_days": 45,
    "ca_threshold": 0.05,                 # opening reference price differs from the previous close by more than 5% = suspected corporate action
}


def cfg_with(over=None):
    c = dict(DEFAULTS)
    c.update(over or {})
    return c


def _d(s):
    return dt.date.fromisoformat(s)


def _r(x, n=2):
    return None if x is None or not math.isfinite(x) else round(x, n)


# ---------------------------------------------------------------- one-day change
def change_of(row):
    """(change, change_pct) from close and previous close, or (None, None). Previous close is NSE's own, already adjusted for the ex-date."""
    p, c = row.get("prev_close"), row.get("close")
    if p is None or c is None or p <= 0 or c <= 0:
        return None, None
    ch = round(c - p, 4)
    pct = ch / p * 100
    if pct < -100 or not math.isfinite(pct):
        return None, None
    return _r(ch, 2), _r(pct, 2)


def is_liquid(row, cfg):
    t = row.get("turnover")
    return t is not None and t >= cfg["liquid_min_turnover_lakhs"]


# ---------------------------------------------------------------- series and corporate-action flags
def build_series(days):
    """{date: [rows]} -> {symbol: [rows ascending by date]}."""
    out = {}
    for d in sorted(days):
        for r in days[d]:
            out.setdefault(r["symbol"], []).append(r)
    return out


def adjustment_events(series, cfg):
    """Dates on which a stock's previous-close reference differs from its own last close by more than the threshold.
    NSE adjusts the reference price on the ex-date of a split, bonus or rights issue, so a mismatch means the unadjusted closes before that date are
    not comparable with the ones after it. (A very large dividend or a gap of a missing day can also trigger it: the flag is deliberately cautious.)"""
    ev = []
    for i in range(1, len(series)):
        p, c0 = series[i].get("prev_close"), series[i - 1].get("close")
        if p is None or c0 is None or p <= 0 or c0 <= 0:
            continue
        ratio = p / c0
        if abs(ratio - 1) > cfg["ca_threshold"]:
            ev.append({"date": series[i]["date"], "ratio": round(ratio, 4)})
    return ev


def _event_inside(events, first_date):
    """An event on date t separates closes before t from closes on and after t: it matters only if the window starts BEFORE t."""
    return any(e["date"] > first_date for e in events)


# ---------------------------------------------------------------- moving averages
def dma_for(series, period, events, cfg):
    """Simple average of the last `period` closes (including the latest). Needs exactly that many real observations, close enough together."""
    last = series[-1] if series else None
    out = {"period": period, "calc_date": last["date"] if last else None, "observations": min(len(series), period), "close": last["close"] if last else None,
           "value": None, "status": INSUFFICIENT, "position": None}
    if len(series) < period:
        return out
    win = series[-period:]
    if (_d(win[-1]["date"]) - _d(win[0]["date"])).days > period * cfg["dma_span_factor"]:
        return out
    if _event_inside(events, win[0]["date"]):
        out["status"] = ADJUST
        return out
    v = _r(sum(r["close"] for r in win) / period, 2)
    out["value"], out["status"] = v, SAFE
    out["position"] = "above" if last["close"] > v else "below" if last["close"] < v else "equal"
    return out


def dma_breadth(per_symbol, period, symbols):
    """per_symbol: {symbol: {period: dma_for(...)}}. Only stocks with a SAFE average are counted; the rest are listed as exclusions, never as 'below'."""
    num = den = ins = adj = 0
    for s in symbols:
        d = per_symbol.get(s, {}).get(period)
        if d is None or d["status"] == INSUFFICIENT:
            ins += 1
        elif d["status"] == ADJUST:
            adj += 1
        elif d["status"] == SAFE:
            den += 1
            if d["position"] == "above":
                num += 1
    pct = _r(num / den * 100, 2) if den else None
    st = SAFE if den else (ADJUST if adj and not ins else INSUFFICIENT)
    return {"period": period, "numerator": num, "denominator": den, "pct": pct, "status": st,
            "excluded": {INSUFFICIENT: ins, ADJUST: adj}}


# ---------------------------------------------------------------- 52-week high / low
def _extreme(window, field, cur, kind, cfg):
    vals = [r.get(field) for r in window]
    if any(v is None or v <= 0 for v in vals):
        return {"status": UNAVAILABLE, "reason": "the source has no valid " + field + " for every day of the window"}
    ext = max(vals) if kind == "high" else min(vals)
    when = [r["date"] for r in window if r[field] == ext][-1]
    dist = _r((cur["close"] / ext - 1) * 100, 2)
    if kind == "high":
        state = "new_high" if cur["close"] > ext else "near_high" if cur["close"] >= ext * (1 - cfg["near_pct"] / 100) else "away"
    else:
        state = "new_low" if cur["close"] < ext else "near_low" if cur["close"] <= ext * (1 + cfg["near_pct"] / 100) else "away"
    return {"status": SAFE, "state": state, "prior_extreme": ext, "prior_extreme_date": when, "distance_pct": dist}


def high_low_52w(series, events, cfg):
    """Close of the latest day against the highest HIGH / lowest LOW of the previous 252 trading days (the latest day is not in its own window)."""
    W = cfg["hl_window"]
    cur = series[-1]
    base = {"symbol": cur["symbol"], "date": cur["date"], "close": cur["close"], "window_observations": min(len(series) - 1, W)}
    if len(series) < W + 1:
        return dict(base, status=INSUFFICIENT, high=None, low=None)
    win = series[-(W + 1):-1]
    if (_d(cur["date"]) - _d(win[0]["date"])).days > cfg["hl_max_span_days"]:
        return dict(base, status=INSUFFICIENT, high=None, low=None)
    if _event_inside(events, win[0]["date"]):
        return dict(base, status=ADJUST, high=None, low=None)
    hi, lo = _extreme(win, "high", cur, "high", cfg), _extreme(win, "low", cur, "low", cfg)
    return dict(base, status=SAFE if SAFE in (hi["status"], lo["status"]) else UNAVAILABLE, high=hi, low=lo)


# ---------------------------------------------------------------- volume baseline
def volume_stats(series, events, cfg):
    """Volume of the latest day against the average of the PREVIOUS `vol_period` trading days (the latest day is not in its own baseline)."""
    P = cfg["vol_period"]
    cur = series[-1]
    out = {"status": INSUFFICIENT, "volume": cur.get("volume"), "avg_volume": None, "multiple": None, "baseline_days": min(len(series) - 1, P)}
    if cur.get("volume") is None or len(series) < P + 1:
        return out
    win = series[-(P + 1):-1]
    if any(r.get("volume") is None for r in win):
        out["status"] = UNAVAILABLE
        return out
    if (_d(cur["date"]) - _d(win[0]["date"])).days > cfg["vol_max_span_days"]:
        return out
    if _event_inside(events, win[0]["date"]):
        out["status"] = ADJUST
        return out
    avg = sum(r["volume"] for r in win) / P
    out["avg_volume"] = _r(avg, 0)
    if avg <= 0:
        out["status"] = UNAVAILABLE
        out["reason"] = "the 20-day average volume is zero"
        return out
    out["multiple"], out["status"] = _r(cur["volume"] / avg, 2), SAFE
    return out


# ---------------------------------------------------------------- breadth
def breadth(rows):
    """Advancing / declining / unchanged among rows that have a valid change. No division by zero: with no decliners the ratio is None."""
    adv = dec = unch = 0
    for r in rows:
        ch, pct = change_of(r)
        if ch is None:
            continue
        if ch > 0:
            adv += 1
        elif ch < 0:
            dec += 1
        else:
            unch += 1
    out = {"advancing": adv, "declining": dec, "unchanged": unch, "counted": adv + dec + unch, "ad_ratio": None}
    if dec == 0:
        out["ad_ratio_note"] = "no declining stocks, so the ratio is not defined" if adv else "no stock with a valid change, so the ratio is not defined"
    else:
        out["ad_ratio"] = _r(adv / dec, 2)
    return out


# ---------------------------------------------------------------- rankings
def _rank(rows, n):
    return [dict(r, rank=i + 1) for i, r in enumerate(rows[:n])]


def _line(row):
    ch, pct = change_of(row)
    return {"symbol": row["symbol"], "close": row["close"], "prev_close": row.get("prev_close"), "change": ch, "change_pct": pct,
            "volume": row.get("volume"), "turnover_lakhs": row.get("turnover"), "delivery_pct": row.get("deliv")}


def most_active(rows, cfg):
    """By traded VALUE (turnover, Rs lakhs) and by traded QUANTITY (shares): two different rankings. Full universe, no liquidity filter."""
    def top(key):
        c = [r for r in rows if r.get(key) is not None and r[key] > 0]
        c.sort(key=lambda r: (-r[key], r["symbol"]))
        return _rank([_line(r) for r in c], cfg["rows_active"])
    return {"by_value": top("turnover"), "by_volume": top("volume")}


def movers(rows, cfg):
    """Gainers and losers among LIQUID stocks (turnover at or above the cut-off), ranked by percentage change."""
    c = []
    for r in rows:
        if not is_liquid(r, cfg):
            continue
        ch, pct = change_of(r)
        if pct is not None:
            c.append((pct, r))
    gain = sorted([x for x in c if x[0] > 0], key=lambda x: (-x[0], x[1]["symbol"]))
    lose = sorted([x for x in c if x[0] < 0], key=lambda x: (x[0], x[1]["symbol"]))
    return {"gainers": _rank([_line(r) for p, r in gain], cfg["rows_movers"]), "losers": _rank([_line(r) for p, r in lose], cfg["rows_movers"]),
            "candidates": len(c)}


def volume_lists(rows, vol, cfg):
    """vol: {symbol: volume_stats(...)}. Only liquid stocks with a SAFE baseline can be shockers."""
    c = []
    for r in rows:
        v = vol.get(r["symbol"])
        if not v or v["status"] != SAFE or not is_liquid(r, cfg) or not r.get("volume"):
            continue
        ch, pct = change_of(r)
        c.append((v["multiple"], pct, r, v))

    def line(x):
        m, pct, r, v = x
        return {"symbol": r["symbol"], "close": r["close"], "change_pct": pct, "volume": r["volume"], "avg_volume_20d": v["avg_volume"],
                "volume_multiple": m, "turnover_lakhs": r.get("turnover"), "delivery_pct": r.get("deliv")}

    hv = sorted([x for x in c if x[0] >= cfg["vol_multiple_min"]], key=lambda x: (-x[0], x[2]["symbol"]))
    gain = [x for x in hv if x[1] is not None and x[1] >= cfg["hv_gain_pct"]]
    loss = [x for x in hv if x[1] is not None and x[1] <= cfg["hv_loss_pct"]]
    n = cfg["rows_volume"]
    return {"shockers": _rank([line(x) for x in hv], n), "high_volume_high_gain": _rank([line(x) for x in gain], n),
            "high_volume_top_losers": _rank([line(x) for x in loss], n), "candidates": len(c)}


def high_low_lists(results, cfg):
    """results: [high_low_52w(...)]. Lists of new and near highs / lows, each sorted by how far beyond / close to the prior extreme."""
    def pick(kind, state, key, rev):
        rows = []
        for h in results:
            e = h.get(kind)
            if h["status"] == SAFE and e and e["status"] == SAFE and e["state"] == state:
                rows.append({"symbol": h["symbol"], "close": h["close"], "prior_" + kind + "_52w": e["prior_extreme"], "prior_" + kind + "_date": e["prior_extreme_date"],
                             "distance_pct": e["distance_pct"], "state": state})
        rows.sort(key=lambda x: (-x["distance_pct"] if rev else x["distance_pct"], x["symbol"]))
        return rows
    out = {}
    for name, kind, state, rev in (("new_high", "high", "new_high", True), ("near_high", "high", "near_high", True),
                                   ("new_low", "low", "new_low", False), ("near_low", "low", "near_low", False)):
        rows = pick(kind, state, None, rev)
        out[name] = {"count": len(rows), "rows": _rank(rows, cfg["rows_52w"])}
    return out
