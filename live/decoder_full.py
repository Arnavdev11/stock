"""
live/decoder_full.py - Upstox Market Data Feed V3 FULL-mode extras: cumulative volume, average traded price, the day's OHLC. Pure; no network.

live.decoder already reads the ltpc block of every feed (ltp, ltt, ltq, cp). A full-mode feed carries more, inside Feed.fullFeed.marketFF. This module reads exactly these extra fields
and nothing else, using the same bounded wire parser (any malformed input raises live.decoder.DecodeError):

  MarketFullFeed   1 ltpc   4 marketOHLC   5 atp (double)   6 vtt (int64)   9 tbq (double)   10 tsq (double)        (marketLevel 2, optionGreeks 3, oi 7, iv 8 are not read)
  MarketOHLC       1 repeated OHLC
  OHLC             1 interval (string)  2 open  3 high  4 low  5 close (double)   6 vol (int64)   7 ts (int64, ms)

STATUS OF THESE FIELD NUMBERS: they are written from Upstox's MarketDataFeedV3.proto as remembered, and the official field LIST (names and meaning) was confirmed from the Upstox V3 documentation,
but no real full-mode frame has been decoded by StockLens yet. Until one has, nothing here may be trusted as live data: the first private run must log, per field, how many feeds carried it
and whether the values are plausible (see scanner.FieldReport). A field that is absent stays None; it is never filled in, never estimated, never 0.

Meaning (Upstox documentation): vtt = volume traded today (shares for an equity); atp = average traded price; the 1-day OHLC candle carries the session open/high/low so far.
"""
from . import decoder
from .decoder import DecodeError, parse_fields, _first, _double, _int

DAY_INTERVALS = ("1d", "1D", "I1D", "day")          # interval labels accepted for the whole-session candle; any other interval (1m, 30m ...) is ignored


def _ohlc(b):
    f = parse_fields(b)
    out = {"interval": None, "open": None, "high": None, "low": None, "close": None, "vol": None, "ts": None}
    w, v = _first(f, 1)
    if v is not None and w == 2:
        out["interval"] = bytes(v).decode("utf-8", "replace")
    for name, fn in (("open", 2), ("high", 3), ("low", 4), ("close", 5)):
        w, v = _first(f, fn)
        if v is not None:
            out[name] = _double(w, v)
    for name, fn in (("vol", 6), ("ts", 7)):
        w, v = _first(f, fn)
        if v is not None:
            out[name] = _int(w, v)
    return out


def decode_market_ff(b):
    """MarketFullFeed bytes -> {"atp", "vtt", "tbq", "tsq", "day"}; every field None when absent. "day" is the whole-session OHLC candle (see DAY_INTERVALS) or None."""
    f = parse_fields(b)
    out = {"atp": None, "vtt": None, "tbq": None, "tsq": None, "day": None}
    for name, fn in (("atp", 5), ("tbq", 9), ("tsq", 10)):
        w, v = _first(f, fn)
        if v is not None:
            out[name] = _double(w, v)
    w, v = _first(f, 6)
    if v is not None:
        out["vtt"] = _int(w, v)
    w, v = _first(f, 4)
    if v is not None and w == 2:
        for fn, wt, item in parse_fields(v):
            if fn == 1 and wt == 2:
                c = _ohlc(item)
                if c["interval"] in DAY_INTERVALS:
                    out["day"] = c
                    break
    return out


def decode_feed_full(b):
    """decoder.decode_feed plus "extra": the market-feed extras of a full-mode equity feed, or None (ltpc mode, or an index feed)."""
    out = decoder.decode_feed(b)
    out["extra"] = None
    f = parse_fields(b)
    w, v = _first(f, 2)
    if v is not None and w == 2:
        w2, v2 = _first(parse_fields(v), 1)             # fullFeed.marketFF
        if v2 is not None and w2 == 2:
            out["extra"] = decode_market_ff(v2)
    return out


def decode_frame_full(b):
    """decoder.decode_frame, with the full-mode extras added to every feed (feed["extra"])."""
    frame = decoder.decode_frame(b)
    if not isinstance(b, (bytes, bytearray, memoryview)):
        return frame
    b = bytes(b)
    for fn, wt, v in parse_fields(b):
        if fn == 2 and wt == 2:
            ent = parse_fields(v)
            _, key = _first(ent, 1)
            fw, feed = _first(ent, 2)
            if key is not None and feed is not None and fw == 2:
                k = key.decode("utf-8", "replace")
                if k in frame["feeds"]:
                    frame["feeds"][k]["extra"] = decode_feed_full(feed)["extra"]
    return frame
