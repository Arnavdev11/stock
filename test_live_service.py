"""Phase 2 - the local relay service, tested with a mocked authorize endpoint and a mocked WebSocket. No network, no real token."""
import ast
import asyncio
import datetime as dt
import gzip
import io
import json
import re
import unittest
from pathlib import Path

from live import decoder, replay, snapshot, state
from live.replay import enc_feed, enc_frame, enc_ltpc
from live_service import __main__ as cli
from live_service import relay, server, upstox
from live_service.safety import ConfigError, Log, Scrubber, is_loopback_host
from live_service.upstox import TransportError

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T_OPEN = int(dt.datetime(2026, 10, 8, 10, 0, 0, tzinfo=IST).timestamp() * 1000)         # Thursday 10:00 IST: market open
T_NIGHT = int(dt.datetime(2026, 10, 8, 23, 0, 0, tzinfo=IST).timestamp() * 1000)
TOKEN = "SECRET-TOKEN-abc123XYZ"
URI = "wss://feed.example.test/market-data/v3?code=ONETIMECODE-9f8e7d"
SYMS = ["TST00000", "TST00001", "TST00002", "TST00003", "TST00004"]


def make_ins(symbols=SYMS):
    rows = replay.synthetic_rows(10, 1)
    rows.append({"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "NIFTY 50"})
    rows = [r for r in rows if not (r.get("segment") == "NSE_INDEX" and r.get("name") == "nifty 50")]
    return upstox.build_instruments(rows, symbols)


def run(coro):
    return asyncio.run(coro)


class FakeTime:
    def __init__(self, ms):
        self.ms = ms

    def now(self):
        return self.ms

    def advance(self, ms):
        self.ms += int(ms)

    async def sleep(self, s):                    # time never moves on its own: the tests move it
        await asyncio.sleep(0)


class MockTransport:
    def __init__(self, connect_error=None, send_error=None):
        self.q = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.uri = None
        self.connect_error, self.send_error = connect_error, send_error

    async def connect(self, uri):
        self.uri = uri
        if self.connect_error:
            raise self.connect_error

    async def send(self, data):
        if self.send_error:
            raise self.send_error
        self.sent.append(data)

    async def recv(self):
        item = await self.q.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True
        self.q.put_nowait(TransportError("closed by us"))

    def feed(self, item):
        self.q.put_nowait(item)


class World:
    """Everything a RelayService needs, scripted."""

    def __init__(self, t0=T_OPEN, ins=None, auth=None, transports=None, ignore_window=True, cfg_kw=None, sup_cfg=None, token=TOKEN):
        self.time = FakeTime(t0)
        self.ins = ins or make_ins()
        self.auth_script = list(auth or [])
        self.auth_calls = []
        self.transports = list(transports or [])
        self.made = []
        self.scrub = Scrubber()
        self.log = Log(self.scrub, stream=io.StringIO(), clock_ms=self.time.now)
        self.cfg = relay.Config(SYMS, ignore_window=ignore_window, holidays=set(), liquid={s for s in SYMS}, **(cfg_kw or {}))
        self.svc = relay.RelayService(self.ins, self.cfg, token, http_get=self.http_get, transport_factory=self.factory, log=self.log, scrub=self.scrub,
                                      clock_ms=self.time.now, sleep=self.time.sleep, supervisor=state.Supervisor(sup_cfg, rng=lambda: 0.0))
        self.stop = asyncio.Event()
        self.task = None

    def http_get(self, url, headers, timeout):
        self.auth_calls.append((url, dict(headers)))
        item = self.auth_script.pop(0) if self.auth_script else (200, {"data": {"authorized_redirect_uri": URI}})
        if isinstance(item, Exception):
            raise item
        return item

    def factory(self):
        t = self.transports.pop(0) if self.transports else MockTransport()
        self.made.append(t)
        return t

    async def start(self):
        self.task = asyncio.ensure_future(self.svc.run(self.stop))
        await self.settle()

    async def settle(self, n=60):
        for _ in range(n):
            await asyncio.sleep(0)

    async def until(self, cond, timeout=10.0):
        """Wait (yielding to the event loop, and briefly sleeping so the authorize worker thread can finish) until cond() or the real-time timeout."""
        import time
        end = time.monotonic() + timeout
        i = 0
        while time.monotonic() < end:
            if cond():
                return True
            i += 1
            await asyncio.sleep(0 if i % 50 else 0.001)
        return cond()

    async def finish(self):
        self.stop.set()
        await asyncio.wait_for(self.task, 5)

    def frame(self, vals=None, ms=None, type_=1):
        """vals: {instrument_key: ltp}; previous close 100 (equity) / 20000 (index)."""
        ms = ms or self.time.ms
        feeds = {}
        for k in list(self.ins.indices) + list(self.ins.equities):
            ltp = (vals or {}).get(k, 20100.0 if k in self.ins.indices else 101.0)
            feeds[k] = enc_feed(enc_ltpc(ltp, ms - 50, 3, 20000.0 if k in self.ins.indices else 100.0))
        return enc_frame(feeds, type_, ms, {"NSE_EQ": 2})

    async def fresh(self, advance=2500):
        """Move time forward and wait for the snapshot built at that moment."""
        self.time.advance(advance)
        target = self.time.ms
        ok = await self.until(lambda: self.svc.latest is not None and self.svc.latest.doc["server_ts_ms"] >= target)
        assert ok
        return self.svc.latest.doc

    async def go_streaming(self):
        await self.start()
        self.assertion = None
        ok = await self.until(lambda: self.svc.sup.state == state.STREAMING)
        assert ok, self.svc.sup.state
        return self.made[-1]


def stream_world(**kw):
    return World(**kw)


class AsyncCase(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)


# ---------------------------------------------------------------------------------------------------------------------- authorize / instruments / safety
class AuthorizeTests(AsyncCase):
    def test_success_and_bearer_header(self):
        calls = []

        def http_get(url, headers, timeout):
            calls.append((url, headers))
            return 200, {"data": {"authorized_redirect_uri": URI}}
        res = self.run_async(upstox.authorize(TOKEN, http_get, Scrubber()))
        self.assertTrue(res.ok)
        self.assertEqual(res.uri, URI)
        self.assertEqual(calls[0][0], "https://api.upstox.com/v3/feed/market-data-feed/authorize")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer " + TOKEN)

    def test_camel_case_key_accepted(self):
        res = upstox.parse_authorize(200, {"data": {"authorizedRedirectUri": URI}}, Scrubber())
        self.assertTrue(res.ok)

    def test_failures(self):
        s = Scrubber()
        self.assertFalse(upstox.parse_authorize(200, {"data": {}}, s).ok)
        self.assertFalse(upstox.parse_authorize(200, {"data": {"authorized_redirect_uri": "http://x"}}, s).ok)
        self.assertFalse(upstox.parse_authorize(200, "text", s).ok)
        r = upstox.parse_authorize(401, {"errors": [{"errorCode": "UDAPI100050", "message": "Invalid token used to access API"}]}, s)
        self.assertEqual((r.ok, r.http), (False, 401))
        self.assertIn("UDAPI100050", r.error)
        self.assertEqual(upstox.parse_authorize(500, None, s).http, 500)

    def test_exception_and_timeout_become_a_failed_result_without_leaking(self):
        s = Scrubber()
        s.add(TOKEN)

        def boom(url, headers, timeout):
            raise RuntimeError("failed for Bearer %s at %s" % (TOKEN, URI))
        res = self.run_async(upstox.authorize(TOKEN, boom, s))
        self.assertFalse(res.ok)
        self.assertNotIn(TOKEN, res.error + repr(res))
        self.assertNotIn("ONETIMECODE", res.error + repr(res))

    def test_repr_hides_the_address(self):
        r = upstox.AuthResult(True, 200, URI, None)
        self.assertNotIn("ONETIMECODE", repr(r))
        self.assertNotIn("feed.example.test", repr(r))


class InstrumentTests(unittest.TestCase):
    def test_build_restricted_set(self):
        ins = make_ins()
        self.assertEqual(sorted(ins.equities.values()), SYMS)
        self.assertEqual(list(ins.indices.values()), ["NIFTY 50"])
        self.assertEqual(len(ins.keys_for()), 6)

    def test_unresolved_is_an_error_not_a_smaller_test(self):
        rows = replay.synthetic_rows(3, 0) + [{"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "Nifty 50"}]
        with self.assertRaises(ConfigError) as cm:
            upstox.build_instruments(rows, ["TST00000", "NOPE1", "NOPE2"])
        self.assertIn("NOPE1", str(cm.exception))
        with self.assertRaises(ConfigError):
            upstox.build_instruments(replay.synthetic_rows(3, 0), ["TST00000"])              # no NIFTY 50 in the file
        with self.assertRaises(ConfigError):
            upstox.build_instruments(rows, [])

    def test_instrument_bytes_gz_and_plain(self):
        rows = [{"a": 1}]
        self.assertEqual(upstox.parse_instrument_bytes(gzip.compress(json.dumps(rows).encode())), rows)
        self.assertEqual(upstox.parse_instrument_bytes(json.dumps(rows).encode()), rows)
        with self.assertRaises(ConfigError):
            upstox.parse_instrument_bytes(b"{}")
        self.assertEqual(upstox.load_instrument_rows(None, lambda u, t: json.dumps(rows).encode()), rows)


class SafetyTests(unittest.TestCase):
    def test_scrubber_patterns_and_registered_secrets(self):
        s = Scrubber()
        s.add(TOKEN)
        for text in ("x " + TOKEN + " y", "Authorization: Bearer abc.def.ghi", "go to " + URI, "uri=wss://h.example/p/q?code=SECRETCODE&x=1",
                     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.sig_nature-1", '{"authorized_redirect_uri": "wss://h.example/zzz"}', "access_token=ABCD1234"):
            out = s(text)
            for bad in (TOKEN, "abc.def.ghi", "ONETIMECODE", "SECRETCODE", "eyJhbGci", "/zzz", "/market-data", "ABCD1234"):
                self.assertNotIn(bad, out, (text, out))
        self.assertLessEqual(len(s("a" * 1000)), 400)
        self.assertEqual(s("plain text"), "plain text")

    def test_loopback_rules(self):
        for ok in ("127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.5"):
            self.assertTrue(is_loopback_host(ok), ok)
        for bad in ("0.0.0.0", "192.168.1.5", "10.0.0.1", "example.com", "", None, "::", "8.8.8.8"):
            self.assertFalse(is_loopback_host(bad), bad)

    def test_log_scrubs_and_keeps_a_tail_and_writes_no_file(self):
        s = Scrubber()
        s.add(TOKEN)
        buf = io.StringIO()
        log = Log(s, stream=buf, keep=3)
        for i in range(5):
            log.info("line %d %s" % (i, TOKEN))
        self.assertNotIn(TOKEN, buf.getvalue())
        self.assertEqual(len(log.tail), 3)


# ---------------------------------------------------------------------------------------------------------------------- the service
class ServiceFlowTests(AsyncCase):
    def test_happy_path_authorize_connect_subscribe_snapshot(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            self.assertEqual(len(w.auth_calls), 1)
            self.assertEqual(w.auth_calls[0][1]["Authorization"], "Bearer " + TOKEN)
            self.assertEqual(t.uri, URI)
            self.assertEqual(len(t.sent), 1)
            self.assertIsInstance(t.sent[0], bytes)                                         # a BINARY frame
            msg = json.loads(t.sent[0])
            self.assertEqual(msg["method"], "sub")
            self.assertEqual(msg["data"]["mode"], "ltpc")
            self.assertEqual(msg["data"]["instrumentKeys"], w.ins.keys_for())
            self.assertEqual(len(msg["data"]["instrumentKeys"]), 6)
            self.assertTrue(msg["guid"])
            t.feed(w.frame(type_=0))
            await w.until(lambda: w.svc.c["frames"] == 1)
            doc = await w.fresh()
            self.assertEqual(snapshot.validate_snapshot(doc), [])
            self.assertTrue(doc["quality"]["ok"], doc["quality"])
            self.assertEqual(doc["scope"]["universe"], "test_set")
            self.assertEqual((doc["scope"]["subscribed"], doc["scope"]["ticked"]), (5, 5))
            self.assertEqual([i["name"] for i in doc["indices"]], ["NIFTY 50"])
            self.assertEqual(sorted(doc["prices"]), SYMS)
            self.assertEqual(doc["market"]["status"], "open")
            self.assertEqual(doc["breadth"]["advances"], 5)
            h = w.svc.health()
            self.assertEqual((h["status"], h["state"]), ("ok", "streaming"))
            await w.finish()
        self.run_async(go())

    def test_the_browser_rules_refuse_a_test_set_snapshot(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            t.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            await w.fresh()
            r = snapshot.client_accepts(w.svc.latest.doc, w.time.ms)
            self.assertTrue(all((not v[0]) and "test snapshot" in v[1] for v in r.values()), r)
            await w.finish()
        self.run_async(go())

    def test_subscription_is_chunked_at_500(self):
        async def go():
            rows = replay.synthetic_rows(1201, 0) + [{"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "NIFTY 50"}]
            syms = ["TST%05d" % i for i in range(1201)]
            ins = upstox.build_instruments(rows, syms)
            w = World(ins=ins)
            t = await w.go_streaming()
            sizes = [len(json.loads(m)["data"]["instrumentKeys"]) for m in t.sent]
            self.assertEqual(sizes, [500, 500, 202])
            self.assertEqual(sum(sizes), 1202)
            await w.finish()
        self.run_async(go())

    def test_reconnect_authorizes_again_with_a_new_address_and_resubscribes(self):
        async def go():
            second = "wss://feed.example.test/second?code=SECONDCODE"
            w = World(auth=[(200, {"data": {"authorized_redirect_uri": URI}}), (200, {"data": {"authorized_redirect_uri": second}})])
            t1 = await w.go_streaming()
            t1.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            t1.feed(TransportError("closed", code=1006))
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            await w.until(lambda: t1.closed)
            self.assertTrue(t1.closed)
            self.assertEqual(w.svc.health()["status"], "degraded")
            self.assertEqual(len(w.auth_calls), 1)
            w.time.advance(2100)                                                            # backoff is 2 s at the lowest jitter... plus margin
            await w.until(lambda: w.svc.sup.state == state.STREAMING)
            self.assertEqual(len(w.auth_calls), 2)                                          # a NEW authorization
            t2 = w.made[-1]
            self.assertIsNot(t1, t2)
            self.assertEqual(t2.uri, second)
            self.assertEqual(json.loads(t2.sent[0])["data"]["instrumentKeys"], w.ins.keys_for())
            self.assertEqual(w.svc.sup.total_connects, 2)
            # coverage restarts with the new connection: nothing ticked yet
            await w.fresh()
            self.assertEqual(w.svc.latest.doc["scope"]["ticked"], 0)
            self.assertFalse(w.svc.latest.doc["quality"]["ok"])
            t2.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 2)
            await w.fresh()
            self.assertEqual(w.svc.latest.doc["scope"]["ticked"], 5)
            self.assertTrue(w.svc.latest.doc["quality"]["ok"], w.svc.latest.doc["quality"])
            await w.finish()
        self.run_async(go())

    def test_a_late_message_from_the_old_socket_is_ignored(self):
        async def go():
            w = World()
            t1 = await w.go_streaming()
            await w.svc.force_drop()
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            t1.feed(w.frame())                                                              # arrives after the drop: nobody reads it
            await w.settle()
            self.assertEqual(w.svc.c["frames"], 0)
            await w.finish()
        self.run_async(go())

    def test_two_auth_rejections_stop_the_relay_with_one_alert(self):
        async def go():
            w = World(auth=[(401, {"errors": [{"errorCode": "UDAPI100050", "message": "Invalid token"}]})] * 3)
            await w.start()
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            w.time.advance(5000)
            await w.until(lambda: w.svc.sup.state == state.AUTH_STOPPED)
            self.assertEqual(len(w.auth_calls), 2)
            self.assertEqual(w.svc.alerts, ["auth_rejected"])
            w.time.advance(10 ** 7)
            await w.settle()
            self.assertEqual(len(w.auth_calls), 2)                                          # no more attempts
            self.assertEqual(w.svc.health()["status"], "stopped")
            self.assertEqual(w.made, [])
            await w.finish()
        self.run_async(go())

    def test_rate_limit_waits_at_least_30_seconds(self):
        async def go():
            w = World(auth=[(429, {"errors": []})])
            await w.start()
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            w.time.advance(29_000)
            await w.settle()
            self.assertEqual(len(w.auth_calls), 1)
            w.time.advance(2_000)
            await w.until(lambda: len(w.auth_calls) == 2)
            await w.finish()
        self.run_async(go())

    def test_network_error_on_authorize_backs_off_and_recovers(self):
        async def go():
            w = World(auth=[ConnectionError("down for Bearer " + TOKEN)])
            await w.start()
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            w.time.advance(2100)
            await w.until(lambda: w.svc.sup.state == state.STREAMING)
            self.assertEqual(w.svc.c["auth_failed"], 1)
            await w.finish()
        self.run_async(go())

    def test_handshake_403_twice_stops(self):
        async def go():
            ts = [MockTransport(connect_error=TransportError("handshake failed", http=403)) for _ in range(2)]
            w = World(transports=ts)
            await w.start()
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            w.time.advance(5000)
            await w.until(lambda: w.svc.sup.state == state.AUTH_STOPPED)
            self.assertEqual(w.svc.alerts, ["auth_rejected"])
            await w.finish()
        self.run_async(go())

    def test_subscribe_failure_backs_off_and_retries(self):
        async def go():
            w = World(transports=[MockTransport(send_error=TransportError("send failed"))])
            await w.start()
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            await w.until(lambda: w.made[0].closed)
            self.assertTrue(w.made[0].closed)
            w.time.advance(2100)
            await w.until(lambda: w.svc.sup.state == state.STREAMING)
            await w.finish()
        self.run_async(go())

    def test_watchdog_reconnects_a_silent_feed_only_while_the_market_is_open(self):
        async def go():
            w = World()
            t1 = await w.go_streaming()
            t1.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            w.time.advance(10_000)
            await w.settle()
            self.assertEqual(w.svc.sup.state, state.STREAMING)
            w.time.advance(7_000)                                                           # 17 s without a frame while open
            await w.until(lambda: w.svc.sup.state in (state.BACKOFF, state.AUTHORIZING, state.CONNECTING, state.STREAMING) and len(w.auth_calls) >= 1 and t1.closed)
            self.assertTrue(t1.closed)
            await w.finish()
        self.run_async(go())

    def test_no_watchdog_when_the_market_is_closed(self):
        async def go():
            w = World(t0=T_NIGHT)
            t1 = await w.go_streaming()
            w.time.advance(120_000)
            await w.settle()
            self.assertEqual(w.svc.sup.state, state.STREAMING)
            self.assertFalse(t1.closed)
            await w.fresh(3000)
            self.assertEqual(w.svc.latest.doc["market"]["status"], "closed")
            await w.finish()
        self.run_async(go())

    def test_garbage_and_text_frames_do_not_kill_the_reader_or_feed_the_watchdog(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            t.feed(b"\xff\xff\xff")
            t.feed(b"\x08")
            t.feed('{"error": "something Bearer %s %s"}' % (TOKEN, URI))
            t.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            c = w.svc.c
            self.assertEqual((c["decode_errors"], c["text_frames"], c["frames"]), (2, 1, 1))
            self.assertEqual(w.svc.sup.state, state.STREAMING)
            self.assertNotIn(TOKEN, "\n".join(w.log.tail))
            self.assertNotIn("ONETIMECODE", "\n".join(w.log.tail))
            # only garbage from now on: the watchdog still fires
            w.time.advance(16_000)
            t.feed(b"\xff")
            await w.until(lambda: t.closed)
            await w.finish()
        self.run_async(go())

    def test_bad_ticks_are_counted_and_not_published(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            k = list(w.ins.equities)[0]
            t.feed(w.frame())
            t.feed(w.frame({k: 1e9}))                                                       # absurd price
            t.feed(enc_frame({"NSE_EQ|ghost": enc_feed(enc_ltpc(5.0, w.time.ms, 1, 5.0))}, 1, w.time.ms))
            await w.until(lambda: w.svc.c["frames"] == 3)
            rej = w.svc.store.stats()["rejected"]
            self.assertIn("bad_ltp", rej)
            self.assertIn("unknown_key", rej)
            self.assertEqual(w.svc.store.get(k)["ltp"], 101.0)
            await w.finish()
        self.run_async(go())

    def test_window_close_disconnects_and_publishes_a_final_snapshot(self):
        async def go():
            w = World(t0=int(dt.datetime(2026, 10, 8, 15, 44, 0, tzinfo=IST).timestamp() * 1000), ignore_window=False)
            t = await w.go_streaming()
            t.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            before = w.svc.seq
            w.time.advance(90_000)                                                          # 15:45:30: the window is over
            await w.until(lambda: w.svc.sup.state == state.OFF_HOURS)
            await w.until(lambda: t.closed and w.svc.seq > before)
            self.assertTrue(t.closed)
            self.assertGreater(w.svc.seq, before)
            self.assertIn("not_streaming", w.svc.latest.doc["quality"]["reasons"])
            self.assertEqual(w.svc.health()["status"], "ok")                                # off hours is normal
            await w.finish()
        self.run_async(go())

    def test_outside_the_window_nothing_connects(self):
        async def go():
            w = World(t0=T_NIGHT, ignore_window=False)
            await w.start()
            w.time.advance(5000)
            await w.settle()
            self.assertEqual((w.auth_calls, w.made), ([], []))
            self.assertEqual(w.svc.sup.state, state.OFF_HOURS)
            self.assertIsNone(w.svc.latest)
            await w.finish()
        self.run_async(go())

    def test_a_weekend_never_connects(self):
        async def go():
            sat = int(dt.datetime(2026, 10, 10, 10, 0, 0, tzinfo=IST).timestamp() * 1000)
            w = World(t0=sat, ignore_window=False)
            await w.start()
            await w.settle()
            self.assertEqual(w.auth_calls, [])
            await w.finish()
        self.run_async(go())

    def test_new_day_starts_with_an_empty_store(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            t.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            self.assertGreater(len(w.svc.store.ticks), 0)
            w.time.advance(86_400_000)
            await w.until(lambda: w.svc.store.session_date == "2026-10-09")
            self.assertEqual(len(w.svc.store.ticks), 0)
            await w.finish()
        self.run_async(go())

    def test_invalid_snapshot_is_never_published(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            t.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            await w.fresh()
            good = w.svc.latest
            orig = snapshot.build_snapshot
            snapshot.build_snapshot = lambda *a, **k: dict(orig(*a, **k), kind="wrong")
            try:
                w.time.advance(2500)
                await w.until(lambda: w.svc.c["invalid_snapshots"] == 1)
            finally:
                snapshot.build_snapshot = orig
            self.assertIs(w.svc.latest, good)
            await w.finish()
        self.run_async(go())

    def test_force_drop_triggers_reconnect(self):
        async def go():
            w = World()
            t1 = await w.go_streaming()
            await w.svc.force_drop()
            await w.until(lambda: w.svc.sup.state == state.BACKOFF)
            w.time.advance(2100)
            await w.until(lambda: len(w.auth_calls) == 2 and w.svc.sup.state == state.STREAMING)
            self.assertTrue(t1.closed)
            self.assertEqual(w.svc.c["drops_requested"], 1)
            await w.finish()
        self.run_async(go())

    def test_cancelling_the_service_task_ends_it_even_mid_disconnect(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            t.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            w.task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(w.task, 5)
            self.assertTrue(t.closed)
            # and a cancel that lands while a window-close disconnect is under way
            w2 = World(t0=int(dt.datetime(2026, 10, 8, 15, 44, 0, tzinfo=IST).timestamp() * 1000), ignore_window=False)
            await w2.go_streaming()
            w2.time.advance(90_000)
            await asyncio.sleep(0)
            w2.task.cancel()
            try:
                await asyncio.wait_for(w2.task, 5)
            except asyncio.CancelledError:
                pass
        self.run_async(asyncio.wait_for(go(), 20))

    def test_nothing_secret_appears_anywhere(self):
        async def go():
            second = "wss://feed.example.test/second?code=SECONDCODE"
            w = World(auth=[ConnectionError("fail Bearer %s %s" % (TOKEN, URI)), (200, {"data": {"authorized_redirect_uri": URI}}),
                            (200, {"data": {"authorized_redirect_uri": second}})],
                      transports=[MockTransport(connect_error=TransportError("handshake to %s with %s failed" % (URI, TOKEN)))])
            await w.start()
            for _ in range(6):
                w.time.advance(3000)
                await w.settle(200)
            t = w.made[-1] if w.made else None
            if t is not None and t.uri:
                t.feed(w.frame())
                t.feed("text with %s and %s" % (TOKEN, URI))
                t.feed(TransportError("closed after %s" % URI))
            w.time.advance(3000)
            await w.settle(300)
            bag = [repr(w.svc), repr(w.scrub), "\n".join(w.log.tail), w.log.stream.getvalue(), json.dumps(w.svc.health()), repr(w.auth_script)[:0]]
            if w.svc.latest:
                bag += [w.svc.latest.raw.decode(), gzip.decompress(w.svc.latest.gz).decode(), w.svc.latest.etag]
            for route_path in (server.HEALTH_PATH, server.SNAPSHOT_PATH):
                bag.append(server.route(w.svc, "GET", route_path, {"host": "127.0.0.1:1", "accept-encoding": "identity"}).decode("latin-1"))
            text = "\n".join(bag)
            for secret in (TOKEN, "ONETIMECODE", "SECONDCODE", "feed.example.test/market", "feed.example.test/second"):
                self.assertNotIn(secret, text, secret)
            await w.finish()
        self.run_async(go())


# ---------------------------------------------------------------------------------------------------------------------- HTTP server
async def http(port, request, read=True):
    r, wr = await asyncio.open_connection("127.0.0.1", port)
    wr.write(request if isinstance(request, bytes) else request.encode("latin-1"))
    await wr.drain()
    data = await asyncio.wait_for(r.read(), 5) if read else b""
    wr.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1]) if lines and lines[0] else 0
    hdrs = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
    return status, hdrs, body


class ServerTests(AsyncCase):
    def test_endpoints(self):
        async def go():
            w = World()
            srv = await server.start_server(w.svc, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]
            hostline = "Host: 127.0.0.1:%d\r\n" % port
            try:
                # before any snapshot
                st, h, body = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + hostline + "\r\n")
                self.assertEqual(st, 503)
                self.assertEqual(json.loads(body), {"error": "no_snapshot_yet"})
                st, h, body = await http(port, "GET /healthz HTTP/1.1\r\n" + hostline + "\r\n")
                self.assertEqual(st, 200)
                self.assertEqual(json.loads(body)["state"], "off_hours")
                # running
                t = await w.go_streaming()
                t.feed(w.frame())
                await w.until(lambda: w.svc.c["frames"] == 1)
                await w.fresh()
                st, h, body = await http(port, "GET /v1/live/snapshot.json?x=1 HTTP/1.1\r\n" + hostline + "\r\n")
                self.assertEqual(st, 200)
                doc = json.loads(body)
                self.assertEqual(snapshot.validate_snapshot(doc), [])
                self.assertEqual(h["etag"], w.svc.latest.etag)
                self.assertEqual(h["content-type"], "application/json; charset=utf-8")
                self.assertNotIn("access-control-allow-origin", h)
                self.assertEqual(h["cache-control"], "no-store")
                self.assertEqual(int(h["content-length"]), len(body))
                # gzip
                st, h2, gz = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + hostline + "Accept-Encoding: gzip, br\r\n\r\n")
                self.assertEqual((st, h2["content-encoding"]), (200, "gzip"))
                self.assertEqual(json.loads(gzip.decompress(gz)), doc)
                # 304
                st, h3, b3 = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + hostline + 'If-None-Match: "nope", %s\r\n\r\n' % w.svc.latest.etag)
                self.assertEqual((st, b3), (304, b""))
                # HEAD
                st, h4, b4 = await http(port, "HEAD /v1/live/snapshot.json HTTP/1.1\r\n" + hostline + "\r\n")
                self.assertEqual((st, b4), (200, b""))
                # health
                st, h5, b5 = await http(port, "GET /healthz HTTP/1.1\r\n" + hostline + "\r\n")
                hd = json.loads(b5)
                self.assertEqual((st, hd["status"], hd["state"]), (200, "ok", "streaming"))
                self.assertEqual(hd["feed"]["subscribed"], 5)
                # degraded -> 503
                await w.svc.force_drop()
                await w.until(lambda: w.svc.sup.state == state.BACKOFF)
                st, _, b6 = await http(port, "GET /healthz HTTP/1.1\r\n" + hostline + "\r\n")
                self.assertEqual((st, json.loads(b6)["status"]), (503, "degraded"))
                # other methods / paths / hosts / malformed
                for req, want in (("POST /healthz HTTP/1.1\r\n" + hostline + "\r\n", 405), ("GET /nope HTTP/1.1\r\n" + hostline + "\r\n", 404),
                                  ("GET /healthz HTTP/1.1\r\nHost: evil.example\r\n\r\n", 403), ("GET /healthz HTTP/1.1\r\n\r\n", 403),
                                  ("GARBAGE\r\n\r\n", 400), ("GET /healthz HTTP/1.1\r\nHost: localhost:%d\r\n\r\n" % port, 503)):
                    st, _, _ = await http(port, req)
                    self.assertEqual(st, want, req)
                st, _, _ = await http(port, "GET /healthz HTTP/1.1\r\nHost: [::1]:%d\r\n\r\n" % port)
                self.assertEqual(st, 503)
                st, _, _ = await http(port, "GET /healthz HTTP/1.1\r\n" + hostline + "X: " + "a" * 20000 + "\r\n\r\n")
                self.assertIn(st, (0, 400, 431))
            finally:
                srv.close()
                await srv.wait_closed()
                await w.finish()
        self.run_async(go())

    def test_refuses_non_loopback_bind(self):
        async def go():
            w = World()
            for host in ("0.0.0.0", "192.168.0.10", "", "::", "example.com"):
                with self.assertRaises(ConfigError):
                    await server.start_server(w.svc, host, 0)
        self.run_async(go())

    def test_route_is_pure_and_snapshot_etag_changes_with_new_snapshot(self):
        async def go():
            w = World()
            t = await w.go_streaming()
            t.feed(w.frame())
            await w.until(lambda: w.svc.c["frames"] == 1)
            await w.fresh()
            e1 = w.svc.latest.etag
            w.time.advance(2500)
            await w.until(lambda: w.svc.latest.etag != e1)
            await w.finish()
        self.run_async(go())


# ---------------------------------------------------------------------------------------------------------------------- Phase 3: Date header and opt-in CORS
class CorsAndDateTests(AsyncCase):
    def serve(self, w, origins=()):
        return server.start_server(w.svc, "127.0.0.1", 0, origins)

    async def ready(self, w):
        t = await w.go_streaming()
        t.feed(w.frame())
        await w.until(lambda: w.svc.c["frames"] == 1)
        await w.fresh()

    def test_every_response_has_the_relays_date_header(self):
        async def go():
            w = World()
            srv = await self.serve(w)
            port = srv.sockets[0].getsockname()[1]
            host = "Host: 127.0.0.1:%d\r\n" % port
            try:
                await self.ready(w)
                import email.utils
                for req in ("GET /healthz HTTP/1.1\r\n", "GET /v1/live/snapshot.json HTTP/1.1\r\n", "GET /nope HTTP/1.1\r\n", "POST /healthz HTTP/1.1\r\n"):
                    st, h, _ = await http(port, req + host + "\r\n")
                    self.assertIn("date", h, req)
                    got = email.utils.parsedate_to_datetime(h["date"]).timestamp() * 1000
                    self.assertLessEqual(abs(got - w.time.ms), 1000, req)                   # the relay's clock, to the second
                st, h, _ = await http(port, "GET /healthz HTTP/1.1\r\nHost: evil.example\r\n\r\n")
                self.assertEqual(st, 403)
                self.assertIn("date", h)
            finally:
                srv.close()
                await srv.wait_closed()
                await w.finish()
        self.run_async(go())

    def test_cors_is_off_by_default_and_a_cross_origin_request_is_refused(self):
        async def go():
            w = World()
            srv = await self.serve(w)
            port = srv.sockets[0].getsockname()[1]
            host = "Host: 127.0.0.1:%d\r\n" % port
            try:
                await self.ready(w)
                st, h, b = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + host + "Origin: http://127.0.0.1:8000\r\n\r\n")
                self.assertEqual(st, 403)
                self.assertEqual(json.loads(b), {"error": "origin_not_allowed"})
                self.assertFalse([k for k in h if k.startswith("access-control")])
                st, h, _ = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + host + "\r\n")                    # same-origin / non-browser: fine, no CORS headers
                self.assertEqual(st, 200)
                self.assertFalse([k for k in h if k.startswith("access-control")])
                self.assertEqual(h["vary"], "Accept-Encoding")
            finally:
                srv.close()
                await srv.wait_closed()
                await w.finish()
        self.run_async(go())

    def test_an_allowed_origin_is_matched_exactly(self):
        async def go():
            w = World()
            allowed = "http://127.0.0.1:8000"
            srv = await self.serve(w, [allowed, "http://localhost:8000"])
            port = srv.sockets[0].getsockname()[1]
            host = "Host: 127.0.0.1:%d\r\n" % port
            try:
                await self.ready(w)
                st, h, b = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + host + "Origin: " + allowed + "\r\n\r\n")
                self.assertEqual(st, 200)
                self.assertEqual(h["access-control-allow-origin"], allowed)
                self.assertEqual(h["access-control-expose-headers"], "Date, ETag")
                self.assertIn("Origin", h["vary"])
                self.assertEqual(snapshot.validate_snapshot(json.loads(b)), [])
                for bad in ("http://127.0.0.1:8001", "http://127.0.0.1", "https://127.0.0.1:8000", "http://127.0.0.1:8000/", "HTTP://127.0.0.1:8000", "null", "*",
                            "https://arnavdev11.github.io", "http://evil.example:8000", ""):
                    st, h, _ = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + host + "Origin: " + bad + "\r\n\r\n")
                    self.assertEqual(st, 403, bad)
                    self.assertNotIn("access-control-allow-origin", h, bad)
                st, h, _ = await http(port, "GET /healthz HTTP/1.1\r\n" + host + "Origin: " + allowed + "\r\n\r\n")             # errors and health are readable too
                self.assertEqual(h["access-control-allow-origin"], allowed)
                st, h, _ = await http(port, "GET /v1/live/snapshot.json HTTP/1.1\r\n" + host + "\r\n")                           # no Origin header: no CORS header
                self.assertNotIn("access-control-allow-origin", h)
                self.assertIn("Origin", h["vary"])
            finally:
                srv.close()
                await srv.wait_closed()
                await w.finish()
        self.run_async(go())

    def test_only_exact_loopback_origins_are_accepted_by_the_configuration(self):
        self.assertEqual(server.check_origins(["http://127.0.0.1:8000", "http://localhost:3000", "http://[::1]:8000", "http://127.0.0.1:8000"]),
                         ("http://127.0.0.1:8000", "http://localhost:3000", "http://[::1]:8000"))
        self.assertEqual(server.check_origins([]), ())
        for bad in ("*", "null", "", "http://*", "https://arnavdev11.github.io", "http://example.com:8000", "http://192.168.1.5:8000", "http://0.0.0.0:8000",
                    "http://127.0.0.1:8000/path", "127.0.0.1:8000", "http://127.0.0.1:8000 http://localhost", "ftp://127.0.0.1", None, 5):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                server.check_origins([bad])

        async def go():
            w = World()
            with self.assertRaises(ConfigError):
                await server.start_server(w.svc, "127.0.0.1", 0, ["https://arnavdev11.github.io"])
            with self.assertRaises(ConfigError):
                await server.start_server(w.svc, "0.0.0.0", 0, ["http://127.0.0.1:8000"])
        self.run_async(go())

    def test_command_line_flag(self):
        import contextlib
        a = cli.parse_args([])
        self.assertEqual(a.allow_origin, [])
        a = cli.parse_args(["--allow-origin", "http://127.0.0.1:8000", "--allow-origin", "http://localhost:8000"])
        self.assertEqual(a.allow_origin, ["http://127.0.0.1:8000", "http://localhost:8000"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cli.main(["--allow-origin", "https://arnavdev11.github.io"], env={"UPSTOX_ANALYTICS_TOKEN": TOKEN})
        self.assertEqual(rc, 2)
        self.assertIn("loopback", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())


# ---------------------------------------------------------------------------------------------------------------------- command line
class CliTests(unittest.TestCase):
    def test_missing_token_exits_2_without_echoing_anything(self):
        err = io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(err):
            rc = cli.main([], env={})
        self.assertEqual(rc, 2)
        self.assertIn("UPSTOX_ANALYTICS_TOKEN", err.getvalue())

    def test_non_loopback_host_refused(self):
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cli.main(["--host", "0.0.0.0"], env={"UPSTOX_ANALYTICS_TOKEN": TOKEN})
        self.assertEqual(rc, 2)
        self.assertNotIn(TOKEN, err.getvalue())

    def test_unresolvable_symbol_is_a_config_error_with_no_token_in_output(self):
        import contextlib
        err = io.StringIO()
        rows = json.dumps(replay.synthetic_rows(3, 0)).encode()
        with contextlib.redirect_stderr(err):
            rc = cli.main(["--symbols", "NOPE", "--port", "0"], env={"UPSTOX_ANALYTICS_TOKEN": TOKEN}, fetch_bytes=lambda u, t: rows)
        self.assertEqual(rc, 2)
        self.assertIn("NOPE", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())

    def test_defaults_are_the_agreed_test_set_and_local(self):
        a = cli.parse_args([])
        self.assertEqual(a.host, "127.0.0.1")
        self.assertEqual(a.port, 8765)
        self.assertEqual(a.symbols.split(","), ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC"])
        self.assertFalse(a.ignore_window)

    def test_end_to_end_run_with_mocks_prints_a_summary_and_no_secret(self):
        rows = replay.synthetic_rows(10, 0) + [{"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "NIFTY 50"}]
        rows_b = json.dumps(rows).encode()
        made = []

        def factory():
            t = MockTransport()
            made.append(t)
            return t
        out, err = io.StringIO(), io.StringIO()
        import contextlib

        async def feeder():
            for _ in range(200):
                if made and made[0].sent:
                    break
                await asyncio.sleep(0.01)
            ins = upstox.build_instruments(rows, SYMS)
            now = int(__import__("time").time() * 1000)
            feeds = {k: enc_feed(enc_ltpc(101.0 if k in ins.equities else 20100.0, now - 20, 1, 100.0 if k in ins.equities else 20000.0)) for k in ins.keys_for()}
            made[0].feed(enc_frame(feeds, 0, now, {"NSE_EQ": 2}))

        async def both():
            args = cli.parse_args(["--symbols", ",".join(SYMS), "--port", "0", "--ignore-window", "--duration", "1.5", "--summary",
                                   "--holidays-file", "/nonexistent", "--liquid-file", "/nonexistent", "--snapshot-interval", "0.2"])
            f = asyncio.ensure_future(feeder())
            await cli.amain(args, TOKEN, http_get=lambda u, h, t: (200, {"data": {"authorized_redirect_uri": URI}}), transport_factory=factory,
                            fetch_bytes=lambda u, t: rows_b, out=out)
            await f
        with contextlib.redirect_stderr(err):
            asyncio.run(both())
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["health"]["connection"]["auth_http"], 200)
        self.assertGreaterEqual(summary["health"]["counters"]["frames"], 1)
        self.assertEqual(summary["last_snapshot"]["scope"]["universe"], "test_set")
        everything = out.getvalue() + err.getvalue()
        self.assertNotIn(TOKEN, everything)
        self.assertNotIn("ONETIMECODE", everything)
        self.assertIn("http://127.0.0.1:", err.getvalue())
        self.assertIn("/healthz", err.getvalue())


# ---------------------------------------------------------------------------------------------------------------------- guards on the new package
PKG = Path(__file__).parent / "live_service"
PROTECTED = ("index.html", "update.yml", "nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py",
             "shareholding_updater.py", "historical_updater.py")


def index_differs_only_by_live_module(root):
    """index.html may differ from HEAD only by the opt-in live module (Phase 3): with that one block removed from both, the files are byte-identical."""
    import re
    import subprocess
    pat = r'<script type="module" id="stocklens-live">[\s\S]*?</script>\n'
    head = subprocess.run(["git", "show", "HEAD:index.html"], cwd=str(root), capture_output=True).stdout.decode("utf-8")
    now = (Path(root) / "index.html").read_bytes().decode("utf-8")
    return re.sub(pat, "", now, count=1) == re.sub(pat, "", head, count=1)


class PackageGuardTests(unittest.TestCase):
    def sources(self):
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(PKG.glob("*.py"))}

    def test_token_is_only_ever_read_in_main(self):
        for name, src in self.sources().items():
            if name != "__main__.py":
                self.assertNotRegex(src, r"os\.environ|getenv|UPSTOX_ANALYTICS_TOKEN", name)
        self.assertEqual(len(re.findall(r"env\.get\(TOKEN_ENV", self.sources()["__main__.py"])), 1)

    def test_nothing_is_written_to_disk(self):
        for name, src in self.sources().items():
            self.assertNotRegex(src, r"open\([^)]*[\"'][wa]b?\+?[\"']", name)
            self.assertNotRegex(src, r"write_text|write_bytes|\.dump\(|shutil|os\.remove|unlink", name)

    def test_never_prints_the_token_variable(self):
        for name, src in self.sources().items():
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
                    for a in ast.walk(node):
                        if isinstance(a, ast.Name):
                            self.assertNotIn(a.id.lower(), ("token", "uri", "headers"), "%s prints %s" % (name, a.id))

    def test_local_only_no_public_exposure_no_browser_access_no_production_stack(self):
        for name, src in self.sources().items():
            self.assertNotRegex(src, r"0\.0\.0\.0|caddy|cloudflare|ngrok|letsencrypt|certbot", name)
            self.assertNotRegex(src, r"Access-Control-Allow-Origin[\"']?\s*[:,=]\s*[\"']\*", name)         # never a wildcard
            if name != "server.py":
                self.assertNotIn("Access-Control", src, name)                                               # CORS lives in one place only
        self.assertIn("is_loopback_host", self.sources()["server.py"])

    def test_no_limit_probe_and_no_eod_or_site_code(self):
        for name, src in self.sources().items():
            self.assertNotRegex(src, r"probe|import\s+(nse_updater|fundamentals_updater|financials_updater|financial_history_updater|shareholding_updater|historical_updater|universe|shards)\b", name)
            self.assertNotIn("index.html", src.replace("# ", ""), name) if name != "__init__.py" else None

    def test_subscription_mode_is_ltpc_only(self):
        src = self.sources()["relay.py"]
        self.assertEqual(re.findall(r'"mode": "([a-z_]+)"', src), ["ltpc"])

    def test_only_the_agreed_default_symbols_are_named_and_only_in_main(self):
        rx = re.compile(r"\b(RELIANCE|TCS|INFY|HDFCBANK|ITC)\b")
        for name, src in self.sources().items():
            if name != "__main__.py":
                self.assertIsNone(rx.search(src), name)

    def test_no_advice_words(self):
        for name, src in self.sources().items():
            self.assertNotRegex(src.lower(), r"\b(buy|sell|target price|recommend)\b", name)

    def test_network_libraries_are_imported_lazily_only(self):
        for name, src in self.sources().items():
            for node in ast.iter_child_nodes(ast.parse(src)):                        # module level only
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                for m in mods:
                    self.assertNotIn(m.split(".")[0], {"requests", "websockets", "websocket", "ssl", "socket", "aiohttp", "httpx", "urllib", "http"}, "%s imports %s at module level" % (name, m))

    def test_websockets_transport_without_the_package_is_a_clear_error(self):
        import sys
        saved = sys.modules.get("websockets", False)
        sys.modules["websockets"] = None                                              # makes `import websockets` raise ImportError, installed or not
        try:
            t = upstox.WebsocketsTransport(Scrubber())
            with self.assertRaises(TransportError) as cm:
                asyncio.run(t.connect(URI))
        finally:
            if saved is False:
                sys.modules.pop("websockets", None)
            else:
                sys.modules["websockets"] = saved
        self.assertIn("pip install websockets", str(cm.exception))
        self.assertNotIn("ONETIMECODE", str(cm.exception))

    def test_phase1_files_other_than_the_universe_label_are_untouched(self):
        import shutil
        import subprocess
        if shutil.which("git") is None:
            self.skipTest("git is not installed here: this check reads `git status`")
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(PKG.parent), capture_output=True, text=True).stdout.splitlines()
        for line in out:
            path = line[3:].strip()
            if path == "index.html" and index_differs_only_by_live_module(PKG.parent):
                continue
            self.assertFalse(path.endswith(PROTECTED) or path.startswith(".github/") or path == ".gitignore", "unexpected change: " + line)
            self.assertFalse(path.startswith("live/") and path != "live/snapshot.py" and "__pycache__" not in path, "unexpected Phase 1 change: " + line)


if __name__ == "__main__":
    unittest.main()
