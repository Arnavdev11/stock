"""
live/decoder.py - Upstox Market Data Feed V3 frames (binary protobuf) -> plain dicts.

Schema-less wire parsing with the field numbers of Upstox's MarketDataFeedV3.proto. In the first real test (ltpc mode) these field numbers agreed with the
official SDK definition. Any malformed or oversized input raises DecodeError (a ValueError) and nothing else, so a bad frame can never crash the relay.

FeedResponse   1 type (0 initial_feed, 1 live_feed, 2 market_info)   2 map<string, Feed> feeds   3 currentTs (ms)   4 marketInfo
Feed           1 ltpc   2 fullFeed (1 marketFF | 2 indexFF; each holds the ltpc at field 1)   3 firstLevelWithGreeks   4 requestMode
LTPC           1 ltp (double)   2 ltt (ms)   3 ltq   4 cp (previous close, double)
MarketInfo     1 map<string, status> segmentStatus
"""
import math
import struct

MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_FEEDS_PER_FRAME = 20000
MAX_FIELDS = 200000

TYPE_NAMES = {0: "initial_feed", 1: "live_feed", 2: "market_info"}
STATUS_NAMES = {0: "PRE_OPEN_START", 1: "PRE_OPEN_END", 2: "NORMAL_OPEN", 3: "NORMAL_CLOSE", 4: "CLOSING_START", 5: "CLOSING_END"}


class DecodeError(ValueError):
    pass


def _varint(b, i):
    x = shift = 0
    while True:
        if i >= len(b):
            raise DecodeError("truncated varint")
        c = b[i]
        i += 1
        x |= (c & 0x7F) << shift
        if not c & 0x80:
            return x, i
        shift += 7
        if shift > 63:
            raise DecodeError("varint too long")


def parse_fields(b):
    """bytes -> [(field_number, wire_type, value)]. wire 0 -> int; 1, 2, 5 -> bytes."""
    out, i, n = [], 0, len(b)
    while i < n:
        tag, i = _varint(b, i)
        fn, wt = tag >> 3, tag & 7
        if fn == 0:
            raise DecodeError("field number 0")
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
            if len(v) < 8:
                raise DecodeError("truncated fixed64")
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
            if len(v) < 4:
                raise DecodeError("truncated fixed32")
        elif wt == 2:
            ln, i = _varint(b, i)
            if ln > n - i:
                raise DecodeError("truncated length-delimited field")
            v, i = b[i:i + ln], i + ln
        else:
            raise DecodeError("unsupported wire type %d" % wt)
        out.append((fn, wt, v))
        if len(out) > MAX_FIELDS:
            raise DecodeError("too many fields")
    return out


def _first(fields, fn):
    for f, w, v in fields:
        if f == fn:
            return w, v
    return None, None


def _double(w, v):
    """A protobuf double (wire 1) or float (wire 5). Not finite -> None."""
    try:
        x = struct.unpack("<d", v)[0] if w == 1 else (struct.unpack("<f", v)[0] if w == 5 else None)
    except struct.error:
        return None
    return x if x is not None and math.isfinite(x) else None


def _int(w, v):
    return v if w == 0 else None


def decode_ltpc(b):
    f = parse_fields(b)
    out = {"ltp": None, "ltt": None, "ltq": None, "cp": None}
    for name, fn in (("ltp", 1), ("ltt", 2), ("ltq", 3), ("cp", 4)):
        w, v = _first(f, fn)
        if v is None:
            continue
        out[name] = _double(w, v) if name in ("ltp", "cp") else _int(w, v)
    return out


def decode_feed(b):
    f = parse_fields(b)
    out = {"ltpc": None, "full": None, "request_mode": None}
    w, v = _first(f, 1)
    if v is not None and w == 2:
        out["ltpc"] = decode_ltpc(v)
    w, v = _first(f, 2)
    if v is not None and w == 2:
        inner = parse_fields(v)
        for fn, kind in ((1, "market"), (2, "index")):
            w2, v2 = _first(inner, fn)
            if v2 is not None and w2 == 2:
                sub = parse_fields(v2)
                w3, v3 = _first(sub, 1)
                out["full"] = kind
                if v3 is not None and w3 == 2:
                    out["ltpc"] = decode_ltpc(v3)          # a full feed carries the same ltpc block: one code path downstream
                break
    w, v = _first(f, 4)
    if v is not None and w == 0:
        out["request_mode"] = v
    return out


def decode_frame(b):
    """-> {"type", "current_ts", "feeds": {instrument_key: {"ltpc": {...}|None, ...}}, "market_info": {segment: status}|None}"""
    if not isinstance(b, (bytes, bytearray, memoryview)):
        raise DecodeError("not a binary frame")
    b = bytes(b)
    if len(b) > MAX_FRAME_BYTES:
        raise DecodeError("frame too large")
    out = {"type": None, "current_ts": None, "feeds": {}, "market_info": None}
    for fn, wt, v in parse_fields(b):
        if fn == 1 and wt == 0:
            out["type"] = TYPE_NAMES.get(v, "type_%d" % v)
        elif fn == 3 and wt == 0:
            out["current_ts"] = v
        elif fn == 2 and wt == 2:
            ent = parse_fields(v)
            _, key = _first(ent, 1)
            fw, feed = _first(ent, 2)
            if key is not None and feed is not None and fw == 2:
                out["feeds"][key.decode("utf-8", "replace")] = decode_feed(feed)
                if len(out["feeds"]) > MAX_FEEDS_PER_FRAME:
                    raise DecodeError("too many feeds in one frame")
        elif fn == 4 and wt == 2:
            seg = {}
            for f2, w2, v2 in parse_fields(v):
                if f2 == 1 and w2 == 2:
                    ent = parse_fields(v2)
                    _, k = _first(ent, 1)
                    sw, st = _first(ent, 2)
                    if k is not None and sw == 0:
                        seg[k.decode("utf-8", "replace")] = STATUS_NAMES.get(st, "STATUS_%d" % st)
            out["market_info"] = seg
    return out


def crosscheck_with_sdk(b):
    """Optional (needs the official upstox-python-sdk): the SDK's own decode of the same bytes, as a dict, or None when it is not installed.
    Used by the offline tests when the SDK happens to be present, and by the private connectivity test; never required."""
    try:
        from google.protobuf.json_format import MessageToDict
        from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb
    except Exception:
        return None
    m = pb.FeedResponse()
    m.ParseFromString(bytes(b))
    return MessageToDict(m, preserving_proto_field_name=True)
