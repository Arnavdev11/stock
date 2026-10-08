"""
live/snapshot.py - the snapshot the browser receives: builder, strict encoder, validator, and the browser's accept rules (mirrored here so the tests pin them).

The snapshot is small (indices, counts, the top 10 each way, a handful of prices) and contains NO raw ticks, NO token, NO Upstox address.
Only ltpc data is used: last price and previous close. Change % = (ltp / previous close - 1) * 100, calculated here from those two numbers only.
Nothing is estimated: an instrument without a valid price or previous close, or that has not ticked on the current connection, is left out and counted.
"""
import datetime as dt
import gzip
import hashlib
import json
import math
import re

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SCHEMA = 1
KIND = "live_snapshot"
SOURCE = "Upstox market data feed V3 (ltpc)"
MARKET_STATUSES = ("open", "pre_open", "closed", "unknown")
UNIVERSES = ("nse_eq_all", "nifty500")
SYMBOL_RE = re.compile(r"^[A-Z0-9&._-]{1,30}$")
TOP_KEYS = {"schema", "kind", "seq", "generated_at", "server_ts_ms", "market", "scope", "indices", "breadth", "gainers", "losers", "prices", "quality", "source"}
DEFAULTS = {"movers": 10, "frame_gap_ms": 10_000, "index_stale_ms": 20_000, "min_coverage": 0.90, "extreme_pct": 40.0, "max_age_ms": 20_000, "skew_ms": 5_000}


def _iso(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, IST).isoformat(timespec="milliseconds")


def _pct(ltp, cp):
    return (ltp / cp - 1) * 100


def build_snapshot(store, ins, now_ms, *, seq, market_status, liquid, subscribed_equities, relay_state="streaming",
                   price_symbols=(), universe="nse_eq_all", cfg=None):
    c = dict(DEFAULTS, **(cfg or {}))
    since = store.coverage_since
    indices = []
    for key, label in ins.indices.items():
        r = store.get(key)
        if r is None or r["recv_ms"] < since:
            continue
        row = {"name": label, "ltp": round(r["ltp"], 4), "cp": round(r["cp"], 4) if r["cp"] else None, "change": None, "change_pct": None, "ltt": _iso(r["ltt"])}
        if r["cp"]:
            row["change"] = round(r["ltp"] - r["cp"], 4)
            row["change_pct"] = round(_pct(r["ltp"], r["cp"]), 3)
        indices.append(row)
    adv = dec = unch = 0
    movers, prices, suspect = [], {}, 0
    want_prices = {s.upper() for s in price_symbols}
    for key, sym in ins.equities.items():
        r = store.get(key)
        if r is None or r["recv_ms"] < since:
            continue
        if sym in want_prices:
            prices[sym] = {"ltp": round(r["ltp"], 4), "cp": round(r["cp"], 4) if r["cp"] else None,
                           "change_pct": round(_pct(r["ltp"], r["cp"]), 3) if r["cp"] else None, "ltt": _iso(r["ltt"])}
        if not r["cp"]:
            continue                                                     # no previous close: not part of breadth or movers
        a, b = round(r["ltp"], 2), round(r["cp"], 2)
        if a > b:
            adv += 1
        elif a < b:
            dec += 1
        else:
            unch += 1
        if sym in liquid:
            pct = _pct(r["ltp"], r["cp"])
            if abs(pct) > c["extreme_pct"]:
                suspect += 1                                             # shown nowhere as a mover; counted
                continue
            movers.append((sym, r["ltp"], r["cp"], pct, r["ltt"]))
    row_of = lambda m: {"symbol": m[0], "ltp": round(m[1], 4), "cp": round(m[2], 4), "change_pct": round(m[3], 3), "ltt": _iso(m[4])}
    if liquid:
        gain = sorted((m for m in movers if round(m[3], 3) > 0), key=lambda m: (-round(m[3], 3), m[0]))[:c["movers"]]
        lose = sorted((m for m in movers if round(m[3], 3) < 0), key=lambda m: (round(m[3], 3), m[0]))[:c["movers"]]
        gainers, losers = [row_of(m) for m in gain], [row_of(m) for m in lose]
    else:
        gainers = losers = None                                          # no end-of-day liquid list: no live movers (never an unfiltered list)
    covered = store.covered_equities()
    sub = max(0, int(subscribed_equities))
    coverage = (covered / sub) if sub else 0.0
    reasons, warnings = [], []
    if relay_state != "streaming":
        reasons.append("not_streaming")
    if store.last_frame_ms is None or now_ms - store.last_frame_ms > c["frame_gap_ms"]:
        reasons.append("feed_silent")
    if market_status == "open":
        if store.last_index_tick_ms is None or now_ms - store.last_index_tick_ms > c["index_stale_ms"]:
            reasons.append("index_ticks_stale")
        if coverage < c["min_coverage"]:
            reasons.append("low_coverage")
    if ins.indices and not indices:
        reasons.append("no_indices")
    if not liquid:
        warnings.append("liquid_list_unavailable")
    if suspect:
        warnings.append("suspect_extreme_moves:%d" % suspect)
    if ins.unresolved:
        warnings.append("indices_unresolved:%d" % len(ins.unresolved))
    last_tick = max([t for t in (store.last_index_tick_ms, store.last_equity_tick_ms) if t is not None], default=None)
    return {
        "schema": SCHEMA, "kind": KIND, "seq": int(seq), "generated_at": _iso(now_ms), "server_ts_ms": int(now_ms),
        "market": {"status": market_status if market_status in MARKET_STATUSES else "unknown", "session_date": store.session_date,
                   "segments": dict(store.segment_status), "last_tick_at": _iso(last_tick)},
        "scope": {"universe": universe, "subscribed": sub, "ticked": covered, "coverage": round(coverage, 4), "indices_expected": len(ins.indices)},
        "indices": indices,
        "breadth": {"advances": adv, "declines": dec, "unchanged": unch, "counted": adv + dec + unch},
        "gainers": gainers, "losers": losers, "prices": prices,
        "quality": {"ok": not reasons, "reasons": reasons, "warnings": warnings},
        "source": SOURCE,
    }


def encode_snapshot(doc):
    """-> (json bytes, gzip bytes, etag). Strict JSON (NaN / Infinity are refused), deterministic gzip."""
    raw = json.dumps(doc, separators=(",", ":"), allow_nan=False, ensure_ascii=False).encode("utf-8")
    gz = gzip.compress(raw, 6, mtime=0)
    return raw, gz, '"%d-%s"' % (doc["seq"], hashlib.sha1(raw).hexdigest()[:12])


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_snapshot(doc):
    """-> list of problems (empty = valid). Used by the tests now and by the server before it publishes anything."""
    p = []
    if not isinstance(doc, dict):
        return ["not an object"]
    if set(doc) != TOP_KEYS:
        p.append("keys differ from the schema: %s" % sorted(set(doc) ^ TOP_KEYS))
        return p
    if doc["schema"] != SCHEMA or doc["kind"] != KIND:
        p.append("wrong schema or kind")
    if not isinstance(doc["seq"], int) or isinstance(doc["seq"], bool) or doc["seq"] < 0:
        p.append("bad seq")
    if not isinstance(doc["server_ts_ms"], int) or isinstance(doc["server_ts_ms"], bool) or doc["server_ts_ms"] <= 0:
        p.append("bad server_ts_ms")
    elif doc["generated_at"] != _iso(doc["server_ts_ms"]):
        p.append("generated_at does not match server_ts_ms")
    m = doc["market"]
    if not isinstance(m, dict) or m.get("status") not in MARKET_STATUSES or not isinstance(m.get("segments"), dict):
        p.append("bad market block")
    s = doc["scope"]
    if not isinstance(s, dict) or s.get("universe") not in UNIVERSES or not all(isinstance(s.get(k), int) and s[k] >= 0 for k in ("subscribed", "ticked", "indices_expected")) \
            or not _num(s.get("coverage")) or not 0 <= s["coverage"] <= 1:
        p.append("bad scope block")
    elif s["ticked"] > s["subscribed"] and s["subscribed"] > 0:
        p.append("ticked exceeds subscribed")
    if not isinstance(doc["indices"], list):
        p.append("indices is not a list")
    else:
        for r in doc["indices"]:
            if not isinstance(r, dict) or not r.get("name") or not _num(r.get("ltp")) or r["ltp"] <= 0:
                p.append("bad index row")
                continue
            if r.get("cp") is not None and (not _num(r["cp"]) or r["cp"] <= 0):
                p.append("bad index cp")
            elif r.get("cp") is not None and (not _num(r.get("change_pct")) or abs(r["change_pct"] - _pct(r["ltp"], r["cp"])) > 0.01):
                p.append("index change_pct does not match ltp and cp")
        if isinstance(s, dict) and len(doc["indices"]) > s.get("indices_expected", 0):
            p.append("more indices than expected")
    b = doc["breadth"]
    if not isinstance(b, dict) or not all(isinstance(b.get(k), int) and b[k] >= 0 for k in ("advances", "declines", "unchanged", "counted")) \
            or b["advances"] + b["declines"] + b["unchanged"] != b["counted"]:
        p.append("bad breadth block")
    for name, sign in (("gainers", 1), ("losers", -1)):
        rows = doc[name]
        if rows is None:
            continue
        if not isinstance(rows, list) or len(rows) > 50:
            p.append(name + " is not a short list")
            continue
        prev = None
        for r in rows:
            if not isinstance(r, dict) or not SYMBOL_RE.match(str(r.get("symbol"))) or not _num(r.get("ltp")) or not _num(r.get("cp")) or r["cp"] <= 0 or not _num(r.get("change_pct")):
                p.append("bad %s row" % name)
                break
            if r["change_pct"] * sign <= 0:
                p.append("%s row with the wrong sign: %s" % (name, r["symbol"]))
            if abs(r["change_pct"] - _pct(r["ltp"], r["cp"])) > 0.01:
                p.append("%s change_pct does not match ltp and cp: %s" % (name, r["symbol"]))
            key = (-r["change_pct"] * sign, r["symbol"])
            if prev is not None and key < prev:
                p.append(name + " not in rank order")
                break
            prev = key
    if (doc["gainers"] is None) != (doc["losers"] is None):
        p.append("gainers and losers must be both present or both null")
    if not isinstance(doc["prices"], dict) or len(doc["prices"]) > 200 or not all(SYMBOL_RE.match(str(k)) and isinstance(v, dict) and _num(v.get("ltp")) for k, v in doc["prices"].items()):
        p.append("bad prices block")
    q = doc["quality"]
    if not isinstance(q, dict) or not isinstance(q.get("ok"), bool) or not isinstance(q.get("reasons"), list) or not isinstance(q.get("warnings"), list) \
            or q["ok"] != (not q["reasons"]):
        p.append("bad quality block")
    if doc["source"] != SOURCE:
        p.append("bad source")
    try:
        json.dumps(doc, allow_nan=False)
    except ValueError:
        p.append("contains NaN or Infinity")
    return p


def client_accepts(doc, now_server_ms, cfg=None):
    """What the browser may show from this snapshot, block by block. -> {"indices": (bool, reason), "breadth": ..., "movers": ..., "prices": ...}
    Every block is refused unless: the snapshot validates, the relay says it is healthy, the market is open, and the snapshot is fresh (age measured against the
    SERVER's clock, with a small allowance for skew). A refused block stays on its end-of-day content."""
    c = dict(DEFAULTS, **(cfg or {}))
    refuse = lambda why: {k: (False, why) for k in ("indices", "breadth", "movers", "prices")}
    probs = validate_snapshot(doc)
    if probs:
        return refuse("invalid snapshot: " + probs[0])
    if not doc["quality"]["ok"]:
        return refuse("relay reports a problem: " + ",".join(doc["quality"]["reasons"]))
    if doc["market"]["status"] != "open":
        return refuse("market is not open")
    age = now_server_ms - doc["server_ts_ms"]
    if age > c["max_age_ms"]:
        return refuse("snapshot is %d ms old" % age)
    if age < -c["skew_ms"]:
        return refuse("snapshot is from the future")
    out = {}
    sc = doc["scope"]
    out["indices"] = (True, "") if sc["indices_expected"] > 0 and len(doc["indices"]) == sc["indices_expected"] else (False, "not every index has a price")
    cov_ok = sc["coverage"] >= c["min_coverage"] and doc["breadth"]["counted"] > 0
    out["breadth"] = (True, "") if cov_ok else (False, "coverage below %d%%" % round(c["min_coverage"] * 100))
    out["movers"] = (True, "") if cov_ok and doc["gainers"] is not None and doc["losers"] is not None else (False, "movers unavailable")
    out["prices"] = (True, "") if doc["prices"] else (False, "no prices")
    return out
