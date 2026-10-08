"""
live/replay.py - the offline replay harness: build Upstox-style binary frames, generate a deterministic synthetic market, record frames to a file and play
them back through the decoder -> tick store -> snapshot builder, with a fake clock. No network, no token, no real symbols (synthetic names only).

A recording is JSON Lines: {"t": <ms since epoch when the frame was received>, "hex": "<frame bytes>"} - the same format a future recorder on the relay can
write, so a real session can be replayed through exactly this code.
"""
import json
import random
import struct

from . import decoder, snapshot, ticks

# ---------------------------------------------------------------- frame encoder (the inverse of decoder.py; used only by tests and the synthetic market)


def _vi(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(fn, wt):
    return _vi((fn << 3) | wt)


def f_varint(fn, n):
    return _tag(fn, 0) + _vi(n)


def f_double(fn, x):
    return _tag(fn, 1) + struct.pack("<d", x)


def f_bytes(fn, b):
    return _tag(fn, 2) + _vi(len(b)) + b


def enc_ltpc(ltp=None, ltt=None, ltq=None, cp=None):
    out = b""
    if ltp is not None:
        out += f_double(1, ltp)
    if ltt is not None:
        out += f_varint(2, ltt)
    if ltq is not None:
        out += f_varint(3, ltq)
    if cp is not None:
        out += f_double(4, cp)
    return out


def enc_feed(ltpc=None, full=None, request_mode=None):
    """full: None | 'market' | 'index' (then the ltpc goes inside the full feed, as Upstox's full mode does)."""
    out = b""
    if full is None and ltpc is not None:
        out += f_bytes(1, ltpc)
    elif full is not None:
        inner = f_bytes(1, ltpc) if ltpc is not None else b""
        out += f_bytes(2, f_bytes(1 if full == "market" else 2, inner))
    if request_mode is not None:
        out += f_varint(4, request_mode)
    return out


def enc_frame(feeds, type_=1, current_ts=None, market_info=None):
    """feeds: {instrument_key: enc_feed(...) bytes}. market_info: {segment: status code int}."""
    out = f_varint(1, type_)
    for k, fb in feeds.items():
        out += f_bytes(2, f_bytes(1, k.encode()) + f_bytes(2, fb))
    if current_ts is not None:
        out += f_varint(3, current_ts)
    if market_info is not None:
        mi = b"".join(f_bytes(1, f_bytes(1, seg.encode()) + f_varint(2, st)) for seg, st in market_info.items())
        out += f_bytes(4, mi)
    return out


# ---------------------------------------------------------------- clock and synthetic market


class FakeClock:
    def __init__(self, start_ms):
        self.ms = int(start_ms)

    def now(self):
        return self.ms

    def advance(self, d):
        self.ms += int(d)
        return self.ms


def synthetic_rows(n_stocks, n_indices=3, prefix="TST"):
    """Instrument rows in the shape of the Upstox NSE instrument file, with synthetic names only."""
    rows = [{"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|SYN%05d" % i, "trading_symbol": "%s%05d" % (prefix, i)} for i in range(n_stocks)]
    names = ["nifty 50", "nifty bank", "nifty next 50", "nifty midcap 100", "nifty smallcap 100", "nifty it", "nifty auto"]
    rows += [{"segment": "NSE_INDEX", "instrument_type": "INDEX", "instrument_key": "NSE_INDEX|%s" % names[i].title(), "name": names[i]} for i in range(n_indices)]
    return rows


class SyntheticMarket:
    """A seeded random walk around a previous close for every instrument. The same seed gives the same bytes."""

    def __init__(self, ins, seed=1, start_ms=0):
        self.rng = random.Random(seed)
        self.keys = list(ins.indices) + sorted(ins.equities)
        self.cp = {}
        self.px = {}
        for k in self.keys:
            base = round(self.rng.uniform(50, 5000) if k in ins.equities else self.rng.uniform(10000, 60000), 2)
            self.cp[k] = base
            self.px[k] = base
        self.t = start_ms
        self.last_ltt = {}

    def initial_frame(self, now_ms):
        feeds = {k: enc_feed(enc_ltpc(self.px[k], now_ms - 1000, 1, self.cp[k])) for k in self.keys}
        return enc_frame(feeds, type_=0, current_ts=now_ms)

    def live_frame(self, now_ms, n_ticks=100, with_cp=True):
        feeds = {}
        for k in self.rng.sample(self.keys, min(n_ticks, len(self.keys))):
            self.px[k] = max(0.05, round(self.px[k] * (1 + self.rng.gauss(0, 0.002)), 2))
            ltt = max(self.last_ltt.get(k, 0), now_ms - self.rng.randint(0, 900))        # an exchange timestamp never goes backwards for one instrument
            self.last_ltt[k] = ltt
            feeds[k] = enc_feed(enc_ltpc(self.px[k], ltt, self.rng.randint(1, 500), self.cp[k] if with_cp else None))
        return enc_frame(feeds, type_=1, current_ts=now_ms)


# ---------------------------------------------------------------- recording and replay


def record(path, items):
    """items: iterable of (t_ms, frame_bytes)."""
    with open(path, "w", encoding="utf-8") as f:
        for t, b in items:
            f.write(json.dumps({"t": int(t), "hex": bytes(b).hex()}) + "\n")


def load_recording(path):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                out.append((int(d["t"]), bytes.fromhex(d["hex"])))
    return out


def run(items, ins, *, liquid, session_date, market_status="open", snapshot_every_ms=5000, subscribed=None, store=None, price_symbols=(), end_ms=None, cfg=None):
    """Play (t_ms, bytes) items through decoder -> store, taking a snapshot every snapshot_every_ms of replayed time (and one at the end).
    -> {"snapshots": [doc], "decode_errors": n, "store": TickStore}. Undecodable frames are counted and skipped, exactly as the relay would."""
    store = store or ticks.TickStore(ins, session_date, cfg)
    sub = subscribed if subscribed is not None else len(ins.equities)
    snaps, bad, seq, next_at = [], 0, 0, None
    last_t = None

    def take(now):
        nonlocal seq
        seq += 1
        snaps.append(snapshot.build_snapshot(store, ins, now, seq=seq, market_status=market_status, liquid=liquid, subscribed_equities=sub, price_symbols=price_symbols))

    for t, b in items:
        if next_at is None:
            next_at = t + snapshot_every_ms
        while t >= next_at:
            take(next_at)
            next_at += snapshot_every_ms
        try:
            frame = decoder.decode_frame(b)
        except decoder.DecodeError:
            bad += 1
            continue
        store.apply_frame(frame, t)
        last_t = t
    final = end_ms if end_ms is not None else last_t
    if final is not None:
        while next_at is not None and next_at <= final:
            take(next_at)
            next_at += snapshot_every_ms
        take(final)
    return {"snapshots": snaps, "decode_errors": bad, "store": store}
