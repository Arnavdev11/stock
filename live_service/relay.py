"""
live_service/relay.py - the relay: Phase 1's supervisor decides, this module does the network work, and the Phase 1 core turns ticks into snapshots.

    supervisor action        what happens here
    ("authorize",)           GET the authorize endpoint with the token  -> "authorized" / "auth_failed"      (EVERY connection, including every reconnect)
    ("connect",)             open the returned single-use wss address   -> "connected" / "connect_failed"
    ("subscribe",)           send binary JSON subscribe frames, mode ltpc, <= 500 keys each -> "subscribed" / "subscribe_failed"
    ("disconnect",)          close the socket
    ("final_snapshot",)      publish one last snapshot
    ("alert", code, text)    one log line
  A reader task decodes every binary frame (live.decoder), applies it to the tick store (live.ticks) and tells the supervisor a frame arrived.
  A snapshot is built every snapshot_every_s, validated (live.snapshot.validate_snapshot) and published only if it is valid.
All time comes from the injected clock and sleep, so the tests drive it without waiting.
"""
import asyncio
import json
import time
import uuid

from live import decoder, market_hours, snapshot, state, ticks
from live.instruments import chunks
from . import upstox
from .upstox import TransportError

VERSION = "phase2-local-1"


class Config:
    def __init__(self, symbols, snapshot_every_s=2.0, tick_every_s=0.5, ignore_window=False, holidays=None, liquid=None, universe="test_set",
                 connect_timeout=20.0, auth_timeout=10.0, chunk=500, close_timeout=5.0):
        self.symbols = [s.upper() for s in symbols]
        self.snapshot_every_s = snapshot_every_s
        self.tick_every_s = tick_every_s
        self.ignore_window = ignore_window
        self.holidays = set(holidays or ())
        self.holidays_known = holidays is not None
        self.liquid = set(liquid or ())
        self.universe = universe
        self.connect_timeout = connect_timeout
        self.auth_timeout = auth_timeout
        self.chunk = chunk
        self.close_timeout = close_timeout


class Published:
    __slots__ = ("doc", "raw", "gz", "etag", "at_ms")

    def __init__(self, doc, raw, gz, etag, at_ms):
        self.doc, self.raw, self.gz, self.etag, self.at_ms = doc, raw, gz, etag, at_ms


class RelayService:
    def __init__(self, ins, cfg, token, *, http_get, transport_factory, log, scrub, clock_ms=None, sleep=None, supervisor=None):
        self.ins, self.cfg, self.log, self.scrub = ins, cfg, log, scrub
        self.__token = token                                   # name-mangled: not an obvious attribute, never in repr or health
        scrub.add(token)
        self.http_get, self.transport_factory = http_get, transport_factory
        self.clock = clock_ms or (lambda: int(time.time() * 1000))
        self.sleep = sleep or asyncio.sleep
        self.sup = supervisor or state.Supervisor()
        self.store = ticks.TickStore(ins, market_hours.session_date(self._now()))
        self.started_ms = self.clock()
        self.latest = None
        self.seq = 0
        self.transport = None
        self.reader = None
        self.conn_id = 0
        self._uri = None
        self._last_snapshot_ms = None
        self.alerts = []
        self.c = {"auth_ok": 0, "auth_failed": 0, "connects": 0, "connect_failed": 0, "frames": 0, "decode_errors": 0, "text_frames": 0,
                  "snapshots": 0, "invalid_snapshots": 0, "snapshot_errors": 0, "drops_requested": 0, "reader_errors": 0}
        self.last_auth_http = None
        self.last_error = None

    def __repr__(self):
        return "<RelayService %s>" % self.sup.state

    # ------------------------------------------------------------------ time and market
    def _now(self):
        return market_hours.dt.datetime.fromtimestamp(self.clock() / 1000, market_hours.IST)

    def market_status(self, now_ms=None):
        now = market_hours.dt.datetime.fromtimestamp((now_ms if now_ms is not None else self.clock()) / 1000, market_hours.IST)
        return market_hours.status(now, self.cfg.holidays, self.store.segment_status.get("NSE_EQ"))

    # ------------------------------------------------------------------ main loop
    async def run(self, stop):
        """Until `stop` (an asyncio.Event) is set. Never raises for a network problem: those are state transitions."""
        try:
            while not stop.is_set():
                try:
                    await self.step(self.clock())
                except asyncio.CancelledError:
                    raise
                except Exception as e:                           # a bug must not kill the relay silently
                    self.c["reader_errors"] += 1
                    self.last_error = type(e).__name__
                    self.log.error("internal error in the relay loop: " + type(e).__name__)
                await self.sleep(self.cfg.tick_every_s)
        finally:
            await self._disconnect()

    async def step(self, now):
        iso = market_hours.session_date(self._now())
        if iso != self.store.session_date:                       # a new trading day: start from an empty store
            self.store = ticks.TickStore(self.ins, iso)
        dtn = self._now()
        want = self.cfg.ignore_window or market_hours.in_connection_window(dtn, self.cfg.holidays)
        if want and self.sup.state == state.OFF_HOURS:
            await self._dispatch(self.sup.handle("window_open", now))
        elif not want and self.sup.state not in (state.OFF_HOURS, state.AUTH_STOPPED):
            await self._dispatch(self.sup.handle("window_close", now))
        await self._dispatch(self.sup.handle("tick", now, expect_frames=self.market_status(now) == "open"))
        if self.sup.state != state.OFF_HOURS and (self._last_snapshot_ms is None or now - self._last_snapshot_ms >= self.cfg.snapshot_every_s * 1000):
            self.publish()

    async def _dispatch(self, actions):
        queue = list(actions)
        while queue:
            a = queue.pop(0)
            kind = a[0]
            if kind == "authorize":
                queue += await self._authorize()
            elif kind == "connect":
                queue += await self._connect()
            elif kind == "subscribe":
                queue += await self._subscribe()
            elif kind == "disconnect":
                await self._disconnect()
            elif kind == "final_snapshot":
                self.publish()
            elif kind == "alert":
                self.alerts.append(a[1])
                self.log.error("ALERT %s: %s" % (a[1], a[2]))

    # ------------------------------------------------------------------ the supervisor's actions
    async def _authorize(self):
        res = await upstox.authorize(self.__token, self.http_get, self.scrub, self.cfg.auth_timeout)
        now = self.clock()
        self.last_auth_http = res.http
        if res.ok:
            self.c["auth_ok"] += 1
            self._uri = res.uri
            self.log.info("authorized (HTTP %s)" % res.http)
            return self.sup.handle("authorized", now)
        self.c["auth_failed"] += 1
        self.last_error = res.error
        self.log.warn("authorization failed: %s" % res.error)
        return self.sup.handle("auth_failed", now, http=res.http)

    async def _connect(self):
        uri, self._uri = self._uri, None                         # single use: forgotten as soon as it is handed over
        t = self.transport_factory()
        try:
            await asyncio.wait_for(t.connect(uri), self.cfg.connect_timeout + 5)
        except TransportError as e:
            self.c["connect_failed"] += 1
            self.last_error = str(e)
            self.log.warn("connection failed: %s" % e)
            return self.sup.handle("connect_failed", self.clock(), http=e.http)
        except asyncio.TimeoutError:
            self.c["connect_failed"] += 1
            self.last_error = "connect timed out"
            self.log.warn("connection timed out")
            return self.sup.handle("connect_failed", self.clock())
        finally:
            uri = None
        self.conn_id += 1
        self.transport = t
        self.c["connects"] += 1
        self.store.begin_connection(self.clock())
        self.reader = asyncio.ensure_future(self._read(t, self.conn_id))
        self.log.info("connected")
        return self.sup.handle("connected", self.clock())

    async def _subscribe(self):
        keys = self.ins.keys_for()
        try:
            for part in chunks(keys, self.cfg.chunk):
                msg = {"guid": uuid.uuid4().hex[:20], "method": "sub", "data": {"mode": "ltpc", "instrumentKeys": part}}
                await self.transport.send(json.dumps(msg).encode("utf-8"))        # V3 wants a BINARY frame
        except TransportError as e:
            self.last_error = str(e)
            self.log.warn("subscribe failed: %s" % e)
            return self.sup.handle("subscribe_failed", self.clock(), http=e.http)
        self.log.info("subscribed: %d instruments (ltpc)" % len(keys))
        return self.sup.handle("subscribed", self.clock())

    async def _disconnect(self):
        self.conn_id += 1                                        # whatever the old reader still reports is ignored
        r, self.reader = self.reader, None
        if r is not None and r is not asyncio.current_task():
            r.cancel()
            await asyncio.wait({r}, timeout=self.cfg.close_timeout)      # asyncio.wait never swallows a cancellation aimed at THIS task (a bare `await r` could)
        await self._close_transport()

    async def _close_transport(self):
        t, self.transport = self.transport, None
        if t is not None:
            try:
                await asyncio.wait_for(t.close(), self.cfg.close_timeout)
            except Exception:
                pass

    async def force_drop(self):
        """Test aid (command line --drop-after): close the socket as if the network had dropped, to exercise reconnect with the real feed."""
        self.c["drops_requested"] += 1
        self.log.warn("deliberate drop requested (reconnect test)")
        await self._close_transport()

    # ------------------------------------------------------------------ the reader
    async def _read(self, t, cid):
        try:
            while True:
                msg = await t.recv()
                if cid != self.conn_id:
                    return
                now = self.clock()
                if isinstance(msg, (bytes, bytearray)):
                    try:
                        frame = decoder.decode_frame(msg)
                    except decoder.DecodeError:
                        self.c["decode_errors"] += 1             # garbage is not a heartbeat: the watchdog is not fed
                        continue
                    self.c["frames"] += 1
                    self.store.apply_frame(frame, now)
                    self.sup.handle("frame", now)
                else:
                    self.c["text_frames"] += 1
                    if self.c["text_frames"] <= 5:
                        self.log.warn("text frame from the feed: " + self.scrub(str(msg), 160))
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # TransportError (closed) or anything unexpected: the socket is finished either way
            if cid != self.conn_id:
                return
            if not isinstance(e, TransportError):
                self.c["reader_errors"] += 1
            self.last_error = str(e) if isinstance(e, TransportError) else type(e).__name__
            self.log.warn("feed connection ended: %s" % self.last_error)
            self.conn_id += 1
            self.reader = None
            self.sup.handle("closed", self.clock(), http=getattr(e, "http", None))
            await self._close_transport()

    # ------------------------------------------------------------------ output
    def publish(self):
        now = self.clock()
        self._last_snapshot_ms = now
        try:
            doc = snapshot.build_snapshot(self.store, self.ins, now, seq=self.seq + 1, market_status=self.market_status(now), liquid=self.cfg.liquid,
                                          subscribed_equities=len(self.ins.equities), relay_state=self.sup.relay_state(), price_symbols=self.cfg.symbols,
                                          universe=self.cfg.universe)
            problems = snapshot.validate_snapshot(doc)
            if problems:
                self.c["invalid_snapshots"] += 1
                self.log.error("snapshot not published, it failed validation: " + problems[0])
                return None
            raw, gz, etag = snapshot.encode_snapshot(doc)
        except Exception as e:
            self.c["snapshot_errors"] += 1
            self.log.error("snapshot could not be built: " + type(e).__name__)
            return None
        self.seq += 1
        self.c["snapshots"] += 1
        self.latest = Published(doc, raw, gz, etag, now)
        return self.latest

    def health(self):
        """Everything shown here is whitelisted by construction: counters, states, ages. No token, no address, no raw frames."""
        now = self.clock()
        st = self.sup.state
        pub = self.latest
        age = lambda t: None if t is None else max(0, now - t)
        status = "stopped" if st == state.AUTH_STOPPED else ("ok" if st in (state.STREAMING, state.OFF_HOURS) else "degraded")
        s = self.store
        return {
            "status": status, "version": VERSION, "state": st, "uptime_s": round((now - self.started_ms) / 1000, 1),
            "market": {"status": self.market_status(now), "session_date": s.session_date, "segments": dict(s.segment_status)},
            "window": {"ignore_window": self.cfg.ignore_window, "holidays_known": self.cfg.holidays_known},
            "snapshot": None if pub is None else {"seq": pub.doc["seq"], "age_ms": age(pub.at_ms), "scope": pub.doc["scope"], "quality": pub.doc["quality"]},
            "feed": {"last_frame_age_ms": age(s.last_frame_ms), "last_index_tick_age_ms": age(s.last_index_tick_ms), "last_equity_tick_age_ms": age(s.last_equity_tick_ms),
                     "ticks": s.stats(), "subscribed": len(self.ins.equities), "indices_subscribed": len(self.ins.indices)},
            "connection": {"auth_http": self.last_auth_http, "failures": self.sup.failures, "total_connects": self.sup.total_connects,
                           "retry_in_s": (round(max(0, self.sup.retry_at_ms - now) / 1000, 1) if st == state.BACKOFF and self.sup.retry_at_ms else None)},
            "counters": dict(self.c), "alerts": list(self.alerts), "last_error": None if self.last_error is None else self.scrub(self.last_error, 160),
            "events": list(self.log.tail)[-20:],
        }
