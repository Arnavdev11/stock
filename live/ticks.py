"""
live/ticks.py - the in-memory tick store with the bad-tick rules. Pure: the caller passes the current time in milliseconds.

A tick is the ltpc block of one instrument: ltp (last price), ltt (last trade time, ms), ltq (last quantity), cp (previous close).
Rules, in order (the reason is counted, never hidden):
  unknown_key    the instrument key is not one we subscribed to / know
  bad_ltp        last price missing, not finite, zero, negative or absurdly large
  future_ltt     the exchange timestamp is more than FUTURE_LTT_MS ahead of our clock
  out_of_order   the exchange timestamp is older than the one already stored for this instrument
  jump_pending   the price moved more than JUMP_RATIO in one tick: it is held, and accepted only if the NEXT tick agrees with it (within JUMP_CONFIRM)
A missing or non-positive previous close is NOT a rejection: the price is stored, the previous close stays unknown (None) and the instrument is left out
of every change / breadth calculation (counted as no_prev_close). A previous close already stored is kept when a later frame omits it.
Nothing is estimated, filled in or carried over silently: a stock that has not ticked since the connection began is not "covered".
"""
import datetime as dt
import math

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DEFAULTS = {"future_ltt_ms": 60_000, "jump_ratio": 0.25, "jump_confirm": 0.05, "max_price": 1e7}


def _ist_date(ms):
    try:
        return dt.datetime.fromtimestamp(ms / 1000, IST).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class TickStore:
    def __init__(self, instruments, session_date=None, cfg=None):
        self.ins = instruments
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.session_date = session_date
        self.ticks = {}                 # instrument_key -> record
        self.pending = {}               # instrument_key -> {"ltp", "recv_ms"}  (a held jump)
        self.rejected = {}              # reason -> count (cumulative)
        self.accepted = 0
        self.frames = 0
        self.last_frame_ms = None
        self.last_index_tick_ms = None
        self.last_equity_tick_ms = None
        self.segment_status = {}        # segment -> status text, as the feed reported it
        self.segment_status_ms = None
        self.coverage_since = 0         # only ticks received at or after this moment count as covered (reset on every new connection)
        self.last_feed_ts = None

    # ---- lifecycle
    def begin_connection(self, now_ms):
        """A new socket: previous prices stay (they are real) but nothing counts as covered until it ticks again."""
        self.coverage_since = now_ms
        self.pending.clear()

    def set_session_date(self, iso):
        self.session_date = iso

    # ---- input
    def _reject(self, reason, out):
        self.rejected[reason] = self.rejected.get(reason, 0) + 1
        out[reason] = out.get(reason, 0) + 1

    def apply_frame(self, frame, now_ms):
        """frame: the dict from decoder.decode_frame. Returns {"accepted": n, "rejected": {reason: n}, "type": ...}."""
        self.frames += 1
        self.last_frame_ms = now_ms
        rej, acc = {}, 0
        if frame.get("current_ts"):
            self.last_feed_ts = frame["current_ts"]
        if frame.get("market_info"):
            self.segment_status.update(frame["market_info"])
            self.segment_status_ms = now_ms
        for key, feed in (frame.get("feeds") or {}).items():
            lt = (feed or {}).get("ltpc")
            if lt is None:
                continue
            if self._accept(key, lt, now_ms, rej):
                acc += 1
        self.accepted += acc
        return {"accepted": acc, "rejected": rej, "type": frame.get("type")}

    def _accept(self, key, lt, now_ms, rej):
        kind = self.ins.kind_of(key)
        if kind is None:
            self._reject("unknown_key", rej)
            return False
        ltp = lt.get("ltp")
        if not isinstance(ltp, (int, float)) or isinstance(ltp, bool) or not math.isfinite(ltp) or ltp <= 0 or ltp > self.cfg["max_price"]:
            self._reject("bad_ltp", rej)
            return False
        ltt = lt.get("ltt")
        if ltt is not None and (not isinstance(ltt, int) or isinstance(ltt, bool) or ltt < 0):
            ltt = None
        if ltt is not None and ltt > now_ms + self.cfg["future_ltt_ms"]:
            self._reject("future_ltt", rej)
            return False
        old = self.ticks.get(key)
        if old is not None and ltt is not None and old.get("ltt") is not None and ltt < old["ltt"]:
            self._reject("out_of_order", rej)
            return False
        if old is not None:                                   # a single-tick jump is held until the next tick confirms it
            ratio = ltp / old["ltp"] - 1
            if abs(ratio) > self.cfg["jump_ratio"]:
                p = self.pending.get(key)
                if p is not None and abs(ltp / p["ltp"] - 1) <= self.cfg["jump_confirm"]:
                    self.pending.pop(key, None)               # confirmed by a second, agreeing tick
                else:
                    self.pending[key] = {"ltp": ltp, "recv_ms": now_ms}
                    self._reject("jump_pending", rej)
                    return False
            else:
                self.pending.pop(key, None)
        cp = lt.get("cp")
        cp_ok = isinstance(cp, (int, float)) and not isinstance(cp, bool) and math.isfinite(cp) and cp > 0
        if not cp_ok:
            cp = old.get("cp") if old else None
            if cp is None:
                self.rejected["no_prev_close"] = self.rejected.get("no_prev_close", 0) + 1
                rej["no_prev_close"] = rej.get("no_prev_close", 0) + 1
        ltq = lt.get("ltq") if isinstance(lt.get("ltq"), int) and not isinstance(lt.get("ltq"), bool) and lt.get("ltq") >= 0 else None
        self.ticks[key] = {"kind": kind, "ltp": float(ltp), "cp": float(cp) if cp is not None else None, "ltq": ltq, "ltt": ltt, "recv_ms": now_ms,
                           "n": (old["n"] + 1) if old else 1, "traded_today": (_ist_date(ltt) == self.session_date) if (ltt is not None and self.session_date) else None}
        if kind == "index":
            self.last_index_tick_ms = now_ms
        else:
            self.last_equity_tick_ms = now_ms
        return True

    # ---- views (read only)
    def covered_equities(self):
        return sum(1 for r in self.ticks.values() if r["kind"] == "equity" and r["recv_ms"] >= self.coverage_since)

    def covered_indices(self):
        return sum(1 for r in self.ticks.values() if r["kind"] == "index" and r["recv_ms"] >= self.coverage_since)

    def get(self, key):
        return self.ticks.get(key)

    def stats(self):
        return {"accepted": self.accepted, "frames": self.frames, "rejected": dict(self.rejected), "instruments": len(self.ticks), "held_jumps": len(self.pending)}
