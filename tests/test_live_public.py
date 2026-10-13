"""
live_public/config.py: the hosted service's configuration. OFFLINE: no network, no real token (every token below is an invented placeholder), nothing is started.
"""
import json
import re
import sys
import unittest
from pathlib import Path

TOKEN = "dummy-placeholder-token-AbC123xyz"
HOST = "stocklens-live.example.com"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live_public.config as C                                      # noqa: E402
from live_service.__main__ import DEFAULT_SYMBOLS                   # noqa: E402
from live_service.safety import ConfigError                         # noqa: E402


def env(**over):
    base = {"UPSTOX_ANALYTICS_TOKEN": TOKEN, "ALLOWED_HOSTS": HOST}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


class Defaults(unittest.TestCase):
    def test_a_minimal_environment_gives_the_safe_defaults(self):
        c = C.load(env())
        self.assertEqual(c.origins, ("https://arnavdev11.github.io",))
        self.assertEqual(c.hosts, (HOST,))
        self.assertEqual((c.bind_host, c.port), ("0.0.0.0", 8080))
        self.assertFalse(c.serve_snapshot)                                     # switched off until someone sets exactly "true"
        self.assertFalse(c.trust_proxy)
        self.assertEqual((c.rate_per_minute, c.burst, c.max_connections, c.stale_after_s), (60, 20, 100, 30))
        self.assertEqual(c.token(), TOKEN)

    def test_the_instruments_are_fixed_and_match_the_relays_tested_set(self):
        c = C.load(env())
        self.assertEqual(c.symbols, tuple(DEFAULT_SYMBOLS.split(",")))
        self.assertEqual(len(c.symbols), 5)
        for var in ("LIVE_PUBLIC_SYMBOLS", "SYMBOLS", "LIVE_PUBLIC_UNIVERSE"):
            self.assertEqual(C.load(env(**{var: "RELIANCE,TCS,INFY,HDFCBANK,ITC,SBIN,WIPRO"})).symbols, c.symbols)       # no variable can widen the subscription

    def test_load_reads_only_the_mapping_it_is_given(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": "from-the-real-environment-1", "ALLOWED_HOSTS": "real.example.org"}, clear=True):
            self.assertEqual(C.load(env()).token(), TOKEN)
            self.assertEqual(C.load().token(), "from-the-real-environment-1")      # and the process environment when none is given


class Token(unittest.TestCase):
    def test_a_missing_or_malformed_token_fails_closed_without_echoing_it(self):
        for bad in (None, "", " ", "short", "has space inside-1234", "line\nbreak-12345", "tab\there-12345", "wss://example.com/feed?code=abc12345", "non-ascii-é-12345", "x" * 4097):
            with self.assertRaises(ConfigError) as cm:
                C.load(env(UPSTOX_ANALYTICS_TOKEN=bad))
            msg = str(cm.exception)
            self.assertIn("UPSTOX_ANALYTICS_TOKEN", msg)
            if bad and len(bad) > 4:
                self.assertNotIn(bad.strip(), msg)

    def test_the_token_never_appears_in_any_text_the_config_produces(self):
        c = C.load(env())
        for text in (repr(c), str(c), format(c), json.dumps(c.describe()), repr(c.describe()), "%s" % (c,), "%r" % (c,)):
            self.assertNotIn(TOKEN, text)
            self.assertNotIn("AbC123xyz", text)
        holders = [k for k, v in vars(c).items() if TOKEN in repr(v)]
        self.assertEqual(holders, ["_Config__token"])                            # the one name-mangled slot is the only holder; every other attribute is clean
        self.assertNotIn("token", json.dumps(c.describe()).lower())

    def test_a_secret_pasted_into_another_variable_is_not_echoed(self):
        for var, val in (("ALLOWED_ORIGIN", TOKEN), ("ALLOWED_HOSTS", TOKEN), ("PORT", TOKEN), ("HOST", TOKEN), ("LIVE_PUBLIC_SERVE_SNAPSHOT", TOKEN),
                         ("LIVE_PUBLIC_TRUST_PROXY", TOKEN), ("LIVE_PUBLIC_RATE_PER_MINUTE", TOKEN), ("LIVE_PUBLIC_BURST", TOKEN)):
            with self.assertRaises(ConfigError) as cm:
                C.load(env(**{var: val}))
            self.assertNotIn(TOKEN, str(cm.exception), var)
            self.assertNotIn("AbC123", str(cm.exception), var)
            self.assertIn(var, str(cm.exception))


class Origins(unittest.TestCase):
    def test_exact_https_origins_are_accepted(self):
        self.assertEqual(C.load(env(ALLOWED_ORIGIN="https://arnavdev11.github.io")).origins, ("https://arnavdev11.github.io",))
        self.assertEqual(C.parse_origins("https://a.example.com,https://b.example.org"), ("https://a.example.com", "https://b.example.org"))
        self.assertEqual(C.parse_origins("https://a.example.com,https://a.example.com"), ("https://a.example.com",))        # duplicates collapse

    def test_everything_else_is_refused(self):
        bad = ["*", "null", "https://*.github.io", "http://arnavdev11.github.io", "https://arnavdev11.github.io/", "https://arnavdev11.github.io/stock",
               "https://arnavdev11.github.io:443", "https://arnavdev11.github.io:8443", "https://user@arnavdev11.github.io", "https://ARNAVDEV11.github.io",
               "https://localhost", "https://127.0.0.1", "https://[::1]", "https://1.2.3.4", "https://-bad.example.com", "https://bad-.example.com", "https://a..b.com",
               "arnavdev11.github.io", "//arnavdev11.github.io", "https://arnavdev11.github.io?x=1", "https://arnavdev11.github.io#x", "https://arnavdev11.github.io, https://b.com",
               "https://a.example.com,", ",https://a.example.com", " https://a.example.com", "https://a.example.com ", "https://пример.рф", "javascript:alert(1)", "https://a b.com",
               "https://a.example.com,https://b.example.com,https://c.example.com,https://d.example.com"]
        for b in bad:
            with self.assertRaises(ConfigError, msg=repr(b)):
                C.parse_origins(b)
            with self.assertRaises(ConfigError, msg=repr(b)):
                C.load(env(ALLOWED_ORIGIN=b))

    def test_lookalikes_of_the_stocklens_origin_are_refused(self):
        for b in ("https://arnavdev11.github.io.evil.example.com", "https://evil-arnavdev11.github.io", "https://xn--arnavdev11-x.github.io", "https://xn--e1afmkfd.example.com",
                  "https://a.b.github.io", "https://arnavdev11.github.io.github.io"):
            with self.assertRaises(ConfigError, msg=repr(b)):
                C.parse_origins(b)
        self.assertEqual(C.parse_origins("https://another-user.github.io"), ("https://another-user.github.io",))     # an ordinary single-label github.io site is not a lookalike

    def test_an_empty_variable_means_the_default_not_everything(self):
        self.assertEqual(C.load(env(ALLOWED_ORIGIN="")).origins, (C.DEFAULT_ORIGIN,))


class Hosts(unittest.TestCase):
    def test_hosts_are_required_and_exact(self):
        with self.assertRaises(ConfigError):
            C.load(env(ALLOWED_HOSTS=None))
        with self.assertRaises(ConfigError):
            C.load(env(ALLOWED_HOSTS=""))
        self.assertEqual(C.parse_hosts("a.example.com,b.example.org"), ("a.example.com", "b.example.org"))

    def test_wildcards_ips_ports_and_odd_names_are_refused(self):
        for b in ("*", "*.example.com", "example.com:8080", "127.0.0.1", "[::1]", "localhost", "Example.com", "exa_mple.com", "a..com", "-a.com", "a-.com", "https://a.com", "a.com/x", "a.com,", ",a.com", "a.com, b.com",
                  "a.example.com,b.example.com,c.example.com,d.example.com,e.example.com,f.example.com", "x" * 300 + ".com"):
            with self.assertRaises(ConfigError, msg=repr(b)):
                C.parse_hosts(b)


class NumbersAndFlags(unittest.TestCase):
    def test_integers_are_strict_and_bounded(self):
        for var, ok_lo, ok_hi, bad in (("PORT", "1", "65535", ("0", "65536", "-1", "abc", "1e3", " 80", "80 ", "8.0", "0x50", "1" * 7)),
                                       ("LIVE_PUBLIC_RATE_PER_MINUTE", "1", "600", ("0", "601", "-5", "x")),
                                       ("LIVE_PUBLIC_BURST", "1", "200", ("0", "201")),
                                       ("LIVE_PUBLIC_MAX_CONNECTIONS", "1", "1000", ("0", "1001")),
                                       ("LIVE_PUBLIC_STALE_AFTER_S", "5", "300", ("4", "301", "0"))):
            C.load(env(**{var: ok_lo}))
            C.load(env(**{var: ok_hi}))
            for b in bad:
                with self.assertRaises(ConfigError, msg="%s=%r" % (var, b)):
                    C.load(env(**{var: b}))

    def test_flags_accept_exactly_true_or_false(self):
        for var, attr in (("LIVE_PUBLIC_SERVE_SNAPSHOT", "serve_snapshot"), ("LIVE_PUBLIC_TRUST_PROXY", "trust_proxy")):
            self.assertIs(getattr(C.load(env(**{var: "true"})), attr), True)
            self.assertIs(getattr(C.load(env(**{var: "false"})), attr), False)
            self.assertIs(getattr(C.load(env(**{var: ""})), attr), False)
            self.assertIs(getattr(C.load(env()), attr), False)
            for b in ("TRUE", "True", "1", "yes", "on", " true", "true ", "enabled", "t"):
                with self.assertRaises(ConfigError, msg="%s=%r" % (var, b)):
                    C.load(env(**{var: b}))

    def test_bind_host_must_be_an_ip_address(self):
        self.assertEqual(C.load(env(HOST="::")).bind_host, "::")
        self.assertEqual(C.load(env(HOST="127.0.0.1")).bind_host, "127.0.0.1")
        for b in ("example.com", "localhost", "0.0.0.0/0", "999.1.1.1", "*"):
            with self.assertRaises(ConfigError, msg=b):
                C.load(env(HOST=b))


class Scope(unittest.TestCase):
    def test_the_module_is_environment_only_and_quiet(self):
        src = (ROOT / "live_public" / "config.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", src, flags=re.S)
        for bad in ("print(", "open(", "requests", "websockets", "socket", "subprocess", "wss://", "logging", "sys.stderr"):
            self.assertNotIn(bad, code, bad)
        self.assertEqual(code.count("os.environ"), 1)                           # the one place the real environment is read (the default when no mapping is given)

    def test_the_local_only_relay_is_untouched_by_this_package(self):
        from live_service import server
        self.assertFalse(server.is_loopback_host("0.0.0.0"))                     # the local relay still refuses a public bind
        with self.assertRaises(ConfigError):
            server.check_origins(["https://arnavdev11.github.io"])               # and still refuses a public origin


# ====================================================================================================================== stage 2: the public HTTP server
import asyncio                                                              # noqa: E402
import copy                                                                 # noqa: E402
import email.utils                                                          # noqa: E402
import gzip                                                                 # noqa: E402

import live_public.server as S                                              # noqa: E402
import test_live_service as TLS                                             # noqa: E402  (the relay's own scripted world: real RelayService, fake transport, no network)
from live import snapshot as SNAP                                           # noqa: E402
from live_service.relay import Published                                    # noqa: E402

SNAP_PATH, HEALTH = "/v1/live/snapshot.json", "/healthz"
ORIGIN = "https://arnavdev11.github.io"
RELAY_SECRETS = (TLS.TOKEN, "ONETIMECODE", "wss://", "authorized_redirect", "Bearer")


def pc(**over):
    over.setdefault("LIVE_PUBLIC_SERVE_SNAPSHOT", "true")
    over.setdefault("LIVE_PUBLIC_BURST", "200")
    over.setdefault("LIVE_PUBLIC_RATE_PER_MINUTE", "600")
    return C.load(env(**over))


async def _good_published():
    w = TLS.World()
    t = await w.go_streaming()
    t.feed(w.frame(type_=0))
    await w.until(lambda: w.svc.c["frames"] == 1)
    await w.fresh()
    pub = w.svc.latest
    await w.finish()
    return pub


GOOD = asyncio.run(_good_published())                                       # a REAL snapshot made by the real RelayService from scripted frames
NOW = GOOD.doc["server_ts_ms"]


def variant(mutate):
    doc = copy.deepcopy(GOOD.doc)
    mutate(doc)
    raw, gz, etag = SNAP.encode_snapshot(doc)
    return Published(doc, raw, gz, etag, GOOD.at_ms)


class FakeSvc:
    """Anything with latest / health() / clock is a service: the server never needs a socket, a token or the feed."""

    def __init__(self, latest=GOOD, now=NOW + 1000, status="ok"):
        self.latest, self.now, self.status = latest, now, status

    def clock(self):
        return self.now

    def health(self):
        if isinstance(self.status, Exception):
            raise self.status
        return {"status": self.status, "events": ["SECRET-TOKEN-abc123XYZ"], "last_error": "wss://x/y?code=ONETIMECODE"}


class Resp:
    def __init__(self, data):
        head, _, self.body = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split(" ")[1])
        self.h = {k.lower(): v for k, v in (ln.split(": ", 1) for ln in lines[1:])}

    def json(self):
        return json.loads(self.body)


def ask(app, path=SNAP_PATH, method="GET", ip="203.0.113.5", host=HOST, **hdrs):
    headers = {"host": host} if host is not None else {}
    headers.update({k.replace("_", "-"): v for k, v in hdrs.items()})
    return Resp(app.handle(method, path, headers, ip))


def app_for(svc=None, **over):
    clock = {"t": 0.0}
    app = S.PublicApp(pc(**over), svc or FakeSvc(), mono=lambda: clock["t"])
    app.tick = lambda s: clock.__setitem__("t", clock["t"] + s)
    return app


class PathsAndMethods(unittest.TestCase):
    def test_only_the_four_routes_answer(self):
        app = app_for()
        for method in ("GET", "HEAD"):
            self.assertEqual(ask(app, SNAP_PATH, method).status, 200)
            self.assertEqual(ask(app, HEALTH, method).status, 200)

    def test_other_methods_are_refused_with_allow(self):
        app = app_for()
        for m in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CONNECT", "get"):
            r = ask(app, SNAP_PATH, m)
            self.assertEqual((r.status, r.h["allow"]), (405, "GET, HEAD"), m)

    def test_other_paths_and_query_strings_are_refused(self):
        app = app_for()
        for p in ("/", "/index.html", "/v1/live", "/v1/live/snapshot.json/", "/v1/live/Snapshot.json", "/healthz/", "/../etc/passwd", "/v1/../healthz", "//healthz", "/admin", "/metrics",
                  "/v1/live/universe.json", "/v1/live/breadth.json"):
            self.assertIn(ask(app, p).status, (400, 404), p)
        for p in ("/v1/live/snapshot.json?symbols=ALL", "/v1/live/snapshot.json?instruments=NSE_EQ|X", "/healthz?x=1", "/healthz#x", "/v1/live/snapshot.json%2f", "/%2e%2e/healthz",
                  "/v1/live/snapshot.json\x00", "/v1/live/snapshot.json ", "", "healthz", "http://x/healthz", "/" + "a" * 500):
            self.assertEqual(ask(app, p).status, 400, repr(p))

    def test_a_request_cannot_widen_the_universe(self):
        app = app_for()
        a = ask(app, SNAP_PATH)
        b = ask(app, SNAP_PATH + "?symbols=RELIANCE,TCS,ALL&universe=nse_eq_all")
        self.assertEqual((a.status, b.status), (200, 400))
        self.assertLessEqual(set(a.json()["prices"]), set(pc().symbols))


class HostAndOrigin(unittest.TestCase):
    def test_host_must_be_an_allowed_name(self):
        app = app_for()
        for ok in (HOST, HOST.upper(), HOST + ":443", HOST + ":8080"):
            self.assertEqual(ask(app, HEALTH, host=ok).status, 200, ok)
        for bad in (None, "", "evil.example.com", "localhost", "127.0.0.1", "[::1]", "127.0.0.1:8080", HOST + ".evil.com", "evil." + HOST, HOST + ":99999", HOST + ":x", HOST + ",evil.com",
                    "user@" + HOST, "*", " "):
            r = ask(app, HEALTH, host=bad)
            self.assertEqual((r.status, r.json()["error"]), (403, "host_not_allowed"), repr(bad))
            self.assertNotIn("access-control-allow-origin", r.h)

    def test_only_the_exact_origin_is_echoed(self):
        app = app_for()
        r = ask(app, SNAP_PATH, origin=ORIGIN)
        self.assertEqual((r.status, r.h["access-control-allow-origin"], r.h["access-control-expose-headers"]), (200, ORIGIN, "Date, ETag"))
        self.assertIn("Origin", r.h["vary"])
        self.assertNotIn("access-control-allow-origin", ask(app, SNAP_PATH).h)             # no Origin header: answered, but no CORS grant
        for bad in ("null", "*", "http://arnavdev11.github.io", ORIGIN + "/", ORIGIN + ":443", ORIGIN + ".evil.com", "https://evil-arnavdev11.github.io", "https://ARNAVDEV11.github.io",
                    "https://arnavdev11.github.io, https://evil.example.com", "", " " + ORIGIN, "https://evil.example.com", "http://localhost:8000", "http://127.0.0.1:8000"):
            for path in (SNAP_PATH, HEALTH):
                r = ask(app, path, origin=bad)
                self.assertEqual((r.status, r.json()["error"]), (403, "origin_not_allowed"), repr(bad))
                self.assertNotIn("access-control-allow-origin", r.h, repr(bad))

    def test_the_origin_list_is_the_configured_list_not_a_wildcard(self):
        app = app_for(ALLOWED_ORIGIN="https://one.example.org,https://two.example.org")
        self.assertEqual(ask(app, HEALTH, origin="https://two.example.org").h["access-control-allow-origin"], "https://two.example.org")
        self.assertEqual(ask(app, HEALTH, origin=ORIGIN).status, 403)                    # the default is replaced, not added to


class SnapshotStates(unittest.TestCase):
    def serve(self, latest=GOOD, now=NOW + 1000, **over):
        return ask(app_for(FakeSvc(latest, now), **over), SNAP_PATH)

    def assertWithheld(self, r, error):
        self.assertEqual((r.status, r.json()), (503, {"error": error}))
        self.assertIn("retry-after", r.h)
        self.assertNotIn(b"indices", r.body)
        self.assertNotIn(b"schema", r.body)                                                 # never a snapshot, so never something a page could call live

    def test_the_snapshot_is_off_unless_the_flag_is_exactly_true(self):
        for v in ("false", "", "1", "TRUE", "yes"):
            if v in ("", "false"):
                self.assertWithheld(self.serve(LIVE_PUBLIC_SERVE_SNAPSHOT=v), "snapshot_disabled")
            else:
                with self.assertRaises(ConfigError):
                    pc(LIVE_PUBLIC_SERVE_SNAPSHOT=v)
        self.assertEqual(ask(app_for(**{"LIVE_PUBLIC_SERVE_SNAPSHOT": "false"}), HEALTH).json()["snapshot"], "disabled")

    def test_before_the_first_snapshot_it_is_unavailable(self):
        self.assertWithheld(self.serve(latest=None), "no_snapshot_yet")
        self.assertEqual(ask(app_for(FakeSvc(None)), HEALTH).json(), {"status": "ok", "snapshot": "none"})

    def test_a_fresh_snapshot_is_served(self):
        r = self.serve()
        self.assertEqual(r.status, 200)
        doc = r.json()
        self.assertEqual(SNAP.validate_snapshot(doc), [])
        self.assertEqual((doc["scope"]["universe"], doc["quality"]["ok"], doc["market"]["status"]), ("test_set", True, "open"))

    def test_staleness_is_judged_on_the_relay_clock_at_the_threshold(self):
        ts = GOOD.doc["server_ts_ms"]
        self.assertEqual(self.serve(now=ts + 30000).status, 200)                            # exactly at the limit: still fresh
        self.assertWithheld(self.serve(now=ts + 30001), "stale_feed")
        self.assertWithheld(self.serve(now=ts + 3_600_000), "stale_feed")
        self.assertEqual(self.serve(now=ts + 60000, LIVE_PUBLIC_STALE_AFTER_S="120").status, 200)         # the threshold is configuration
        self.assertEqual(ask(app_for(FakeSvc(GOOD, ts + 31000)), HEALTH).json()["snapshot"], "stale")

    def test_a_snapshot_from_the_future_is_invalid(self):
        ts = GOOD.doc["server_ts_ms"]
        self.assertEqual(self.serve(now=ts - 5000).status, 200)                             # within the allowed skew
        self.assertWithheld(self.serve(now=ts - 5001), "invalid_snapshot")

    def test_inconsistent_timestamps_are_invalid(self):
        ts = GOOD.doc["server_ts_ms"]
        for at in (ts + 60000, ts - 60000, None, "x", True, 0):
            pub = Published(GOOD.doc, GOOD.raw, GOOD.gz, GOOD.etag, at)
            self.assertWithheld(self.serve(latest=pub), "invalid_snapshot")
        def bad_stamp(d):
            d["generated_at"] = "2000-01-01T00:00:00+05:30"
        self.assertWithheld(self.serve(latest=variant(bad_stamp)), "invalid_snapshot")
        def zero_stamp(d):
            d["server_ts_ms"] = 0
        self.assertWithheld(self.serve(latest=variant(zero_stamp), now=1000), "invalid_snapshot")

    def test_malformed_documents_are_never_served(self):
        def drop_key(d):
            del d["indices"]
        def nan_price(d):
            d["indices"][0]["ltp"] = float("nan")
        def negative_price(d):
            d["indices"][0]["ltp"] = -1
        def wrong_source(d):
            d["source"] = "somewhere else"
        for f in (drop_key, negative_price, wrong_source):
            doc = copy.deepcopy(GOOD.doc)
            f(doc)
            pub = Published(doc, b"{}", b"", '"x"', GOOD.at_ms)
            self.assertWithheld(self.serve(latest=pub), "invalid_snapshot")
        doc = copy.deepcopy(GOOD.doc)
        nan_price(doc)
        self.assertWithheld(self.serve(latest=Published(doc, b"{}", b"", '"x"', GOOD.at_ms)), "invalid_snapshot")
        for junk in ("text", 7, [], {}, object()):
            self.assertWithheld(self.serve(latest=Published(junk, b"", b"", '"x"', GOOD.at_ms)), "invalid_snapshot")
        self.assertWithheld(self.serve(latest=object()), "invalid_snapshot")

    def test_an_unhealthy_feed_is_not_served_as_live(self):
        def unhealthy(d):
            d["quality"] = {"ok": False, "reasons": ["feed_silent"], "warnings": []}
        self.assertWithheld(self.serve(latest=variant(unhealthy)), "feed_unavailable")

    def test_only_the_fixed_test_universe_can_be_published(self):
        def full(d):
            d["scope"]["universe"] = "nse_eq_all"
        self.assertWithheld(self.serve(latest=variant(full)), "invalid_snapshot")

    def test_the_public_view_is_not_a_market_dashboard(self):
        self.assertGreater(GOOD.doc["breadth"]["counted"], 0)                               # the relay computed breadth over its few instruments...
        doc = self.serve().json()
        self.assertEqual(doc["breadth"], {"advances": 0, "declines": 0, "unchanged": 0, "counted": 0})        # ...the public view does not carry it
        self.assertIsNone(doc["gainers"])
        self.assertIsNone(doc["losers"])
        self.assertEqual(SNAP.validate_snapshot(doc), [])

    def test_prices_are_cut_to_the_configured_symbols(self):
        sym = sorted(GOOD.doc["prices"])[0]
        cfg_syms = pc().symbols
        self.assertNotIn(sym, cfg_syms)                                                     # the scripted world uses invented symbols, so none is configured
        self.assertEqual(self.serve().json()["prices"], {})
        def add_configured(d):
            d["prices"]["RELIANCE"] = {"ltp": 2500.0, "cp": 2490.0, "change_pct": 0.402, "ltt": d["prices"][sym]["ltt"]}
        self.assertEqual(sorted(self.serve(latest=variant(add_configured)).json()["prices"]), ["RELIANCE"])


class CachingAndEncoding(unittest.TestCase):
    def test_etag_and_304(self):
        app = app_for()
        r = ask(app, SNAP_PATH)
        etag = r.h["etag"]
        self.assertTrue(etag.startswith('"'))
        n = ask(app, SNAP_PATH, if_none_match=etag, origin=ORIGIN)
        self.assertEqual((n.status, n.body, n.h["etag"], n.h["access-control-allow-origin"]), (304, b"", etag, ORIGIN))
        self.assertEqual(ask(app, SNAP_PATH, if_none_match='"nope", ' + etag).status, 304)
        for other in ('"nope"', "*", "", etag.upper() + "x"):
            self.assertEqual(ask(app, SNAP_PATH, if_none_match=other).status, 200, other)
        self.assertEqual(ask(app, SNAP_PATH).h["etag"], etag)                               # stable while the relay publishes nothing new

    def test_a_new_snapshot_changes_the_etag(self):
        svc = FakeSvc()
        app = app_for(svc)
        first = ask(app, SNAP_PATH).h["etag"]
        def newer(d):
            d["seq"] += 1
        svc.latest = variant(newer)
        self.assertNotEqual(ask(app, SNAP_PATH, if_none_match=first).h["etag"], first)
        self.assertEqual(ask(app, SNAP_PATH, if_none_match=first).status, 200)

    def test_a_stale_snapshot_is_not_304(self):
        svc = FakeSvc()
        app = app_for(svc)
        etag = ask(app, SNAP_PATH).h["etag"]
        svc.now = NOW + 120000
        r = ask(app, SNAP_PATH, if_none_match=etag)
        self.assertEqual((r.status, r.json()), (503, {"error": "stale_feed"}))

    def test_gzip(self):
        app = app_for()
        plain = ask(app, SNAP_PATH)
        z = ask(app, SNAP_PATH, accept_encoding="gzip, deflate, br")
        self.assertEqual((z.status, z.h["content-encoding"], z.h["etag"]), (200, "gzip", plain.h["etag"]))
        self.assertEqual(gzip.decompress(z.body), plain.body)
        self.assertNotIn("content-encoding", plain.h)
        for no in ("gzip;q=0", "identity", "br", "deflate", ""):
            self.assertNotIn("content-encoding", ask(app, SNAP_PATH, accept_encoding=no).h, no)
        self.assertEqual(ask(app, SNAP_PATH, accept_encoding="GZIP;q=0.5").h["content-encoding"], "gzip")
        self.assertIn("Accept-Encoding", plain.h["vary"])

    def test_head_matches_get_without_a_body(self):
        app = app_for()
        for hdrs in ({}, {"accept_encoding": "gzip"}, {"origin": ORIGIN}):
            g, h = ask(app, SNAP_PATH, "GET", **hdrs), ask(app, SNAP_PATH, "HEAD", **hdrs)
            self.assertEqual((h.status, h.body), (200, b""))
            self.assertEqual(h.h, g.h)                                                      # same headers, including Content-Length
            self.assertEqual(int(h.h["content-length"]), len(g.body))
        g, h = ask(app, HEALTH, "GET"), ask(app, HEALTH, "HEAD")
        self.assertEqual((h.body, h.h), (b"", g.h))
        e = ask(app_for(FakeSvc(None)), SNAP_PATH, "HEAD")
        self.assertEqual((e.status, e.body), (503, b""))

    def test_every_response_is_no_store_and_carries_security_headers_and_the_relay_date(self):
        app = app_for()
        rs = [ask(app, SNAP_PATH), ask(app, SNAP_PATH, if_none_match=ask(app, SNAP_PATH).h["etag"]), ask(app, HEALTH), ask(app, "/nope"), ask(app, SNAP_PATH, "POST"),
              ask(app, SNAP_PATH, host="evil.example.com"), ask(app, SNAP_PATH, origin="null"), ask(app_for(FakeSvc(None)), SNAP_PATH)]
        for r in rs:
            self.assertEqual(r.h["cache-control"], "no-store")
            self.assertEqual(r.h["x-content-type-options"], "nosniff")
            self.assertEqual(r.h["referrer-policy"], "no-referrer")
            self.assertIn("default-src 'none'", r.h["content-security-policy"])
            self.assertIn("strict-transport-security", r.h)
            self.assertEqual(r.h["connection"], "close")
            self.assertEqual(int(email.utils.parsedate_to_datetime(r.h["date"]).timestamp() * 1000) // 1000, (NOW + 1000) // 1000)     # the relay's clock
        self.assertNotIn("server", rs[0].h)


class RateLimiting(unittest.TestCase):
    def limited(self, **over):
        over.setdefault("LIVE_PUBLIC_RATE_PER_MINUTE", "60")
        over.setdefault("LIVE_PUBLIC_BURST", "3")
        return app_for(**over)

    def test_burst_then_429_then_refill(self):
        app = self.limited()
        self.assertEqual([ask(app, HEALTH).status for _ in range(3)], [200, 200, 200])
        r = ask(app, HEALTH)
        self.assertEqual((r.status, r.json()), (429, {"error": "rate_limited"}))
        self.assertGreaterEqual(int(r.h["retry-after"]), 1)
        app.tick(1.0)                                                                       # 60 per minute = one token per second
        self.assertEqual(ask(app, HEALTH).status, 200)
        self.assertEqual(ask(app, HEALTH).status, 429)
        app.tick(10.0)
        self.assertEqual([ask(app, HEALTH).status for _ in range(4)], [200, 200, 200, 429])    # refill never exceeds the burst

    def test_clients_are_counted_separately_and_a_429_is_still_readable_by_the_page(self):
        app = self.limited()
        for _ in range(3):
            ask(app, HEALTH, ip="198.51.100.1")
        self.assertEqual(ask(app, HEALTH, ip="198.51.100.1").status, 429)
        self.assertEqual(ask(app, HEALTH, ip="198.51.100.2").status, 200)
        r = ask(app, HEALTH, ip="198.51.100.1", origin=ORIGIN)
        self.assertEqual((r.status, r.h["access-control-allow-origin"]), (429, ORIGIN))

    def test_forwarded_headers_are_ignored_unless_the_proxy_is_trusted(self):
        app = self.limited()
        for i in range(3):
            ask(app, HEALTH, ip="198.51.100.1", x_forwarded_for="192.0.2.%d" % i)
        self.assertEqual(ask(app, HEALTH, ip="198.51.100.1", x_forwarded_for="192.0.2.99").status, 429)     # spoofing buys nothing

    def test_a_trusted_proxy_uses_the_last_forwarded_entry_only(self):
        app = self.limited(LIVE_PUBLIC_TRUST_PROXY="true")
        for i in range(3):                                                                  # the client invents leading entries; the proxy appends the real one last
            ask(app, HEALTH, ip="10.0.0.1", x_forwarded_for="1.1.1.%d, 192.0.2.7" % i)
        self.assertEqual(ask(app, HEALTH, ip="10.0.0.1", x_forwarded_for="9.9.9.9, 192.0.2.7").status, 429)
        self.assertEqual(ask(app, HEALTH, ip="10.0.0.1", x_forwarded_for="192.0.2.8").status, 200)         # a different real client behind the same proxy
        for junk in ("not-an-ip", "", "1.2.3.4, ", "999.1.1.1", "1.2.3.4:80"):
            for _ in range(3):
                ask(app, HEALTH, ip="10.0.0.2", x_forwarded_for=junk)
            self.assertEqual(ask(app, HEALTH, ip="10.0.0.2", x_forwarded_for=junk).status, 429, repr(junk))   # malformed: counted against the connection's own address
            app.tick(100)

    def test_the_table_of_clients_is_bounded(self):
        lim = S.RateLimiter(60, 3, lambda: 0.0, cap=50)
        for i in range(1000):
            lim.allow("10.0.%d.%d" % (i // 250, i % 250))
        self.assertLessEqual(len(lim.b), 50)

    def test_rate_limits_apply_to_requests_without_an_origin_too(self):
        app = self.limited()
        self.assertEqual([ask(app, SNAP_PATH).status for _ in range(4)], [200, 200, 200, 429])


class SafeErrors(unittest.TestCase):
    def test_health_is_minimal(self):
        r = ask(app_for(), HEALTH)
        self.assertEqual(r.json(), {"status": "ok", "snapshot": "fresh"})
        raw = r.body.decode()
        for s in RELAY_SECRETS + ("events", "last_error", "counters", "uptime", "connection", "alerts", "version"):
            self.assertNotIn(s, raw)

    def test_a_degraded_relay_gives_503_on_health_without_details(self):
        r = ask(app_for(FakeSvc(status="degraded")), HEALTH)
        self.assertEqual((r.status, r.json()["status"]), (503, "unavailable"))
        self.assertNotIn(b"SECRET", r.body)

    def test_exceptions_never_reach_the_peer(self):
        class Boom(FakeSvc):
            @property
            def latest(self):
                raise RuntimeError("boom: token SECRET-TOKEN-abc123XYZ wss://feed.example.test/x?code=ONETIMECODE")

            @latest.setter
            def latest(self, v):
                pass
        cases = ((Boom(), (SNAP_PATH, HEALTH)), (FakeSvc(status=RuntimeError("SECRET-TOKEN-abc123XYZ")), (HEALTH,)))
        for svc, paths in cases:
            app = app_for(svc)
            for path in paths:
                r = ask(app, path)
                self.assertEqual(r.status, 503)
                self.assertEqual(r.json().get("error", r.json().get("status")), "unavailable")
                for s in RELAY_SECRETS + ("boom", "RuntimeError", "Traceback"):
                    self.assertNotIn(s, r.body.decode())

    def test_an_unusable_relay_clock_falls_back_and_stays_safe(self):
        class BadClock(FakeSvc):
            def clock(self):
                raise RuntimeError("x")
        r = ask(app_for(BadClock()), SNAP_PATH)
        self.assertEqual(r.status, 503)                                                     # judged against the wall clock: the 2026 fixture is stale or in the future
        self.assertIn("date", r.h)

    def test_no_response_from_a_real_relay_contains_a_secret(self):
        async def go():
            w = TLS.World()
            t = await w.go_streaming()
            t.feed(w.frame(type_=0))
            await w.until(lambda: w.svc.c["frames"] == 1)
            await w.fresh()
            app = S.PublicApp(pc(), w.svc)
            outs = [app.handle("GET", p, {"host": HOST, "origin": ORIGIN, "accept-encoding": "gzip"}, "203.0.113.9") for p in (SNAP_PATH, HEALTH, "/x")]
            outs.append(app.handle("GET", SNAP_PATH, {"host": HOST}, "203.0.113.9"))
            await w.finish()
            return outs
        for out in asyncio.run(go()):
            blob = gzip.decompress(out.partition(b"\r\n\r\n")[2]) if b"Content-Encoding: gzip" in out else out
            for s in RELAY_SECRETS:
                self.assertNotIn(s.encode(), blob)

    def test_the_module_never_touches_the_token_or_the_environment(self):
        src = (ROOT / "live_public" / "server.py").read_text(encoding="utf-8")
        for needle in ("os.environ", "getenv", ".token(", "Authorization", "websockets", "authorize", "open(", "live_service.server", "print("):
            self.assertNotIn(needle, src, needle)


async def raw_exchange(port, data, read=True, timeout=3.0):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(data)
    await w.drain()
    out = b""
    if read:
        try:
            out = await asyncio.wait_for(r.read(), timeout)
        except (asyncio.TimeoutError, ConnectionError):
            pass
    w.close()
    return out


def request_bytes(path=SNAP_PATH, host=HOST, extra="", method="GET"):
    return ("%s %s HTTP/1.1\r\nHost: %s\r\n%s\r\n" % (method, path, host, extra)).encode("latin-1")


class OverTheWire(unittest.TestCase):
    """The real asyncio server on an ephemeral loopback port, with a fake service."""

    def run_server(self, body, svc=None, **over):
        async def go():
            srv = S.PublicServer(pc(**over), svc or FakeSvc())
            await srv.start(host="127.0.0.1", port=0)
            try:
                return await body(srv)
            finally:
                await srv.close()
        return asyncio.run(go())

    def test_serves_get_and_head_and_a_conditional_get(self):
        async def body(srv):
            g = Resp(await raw_exchange(srv.port, request_bytes(extra="Origin: %s\r\nAccept-Encoding: gzip\r\n" % ORIGIN)))
            h = Resp(await raw_exchange(srv.port, request_bytes(method="HEAD")))
            n = Resp(await raw_exchange(srv.port, request_bytes(extra="If-None-Match: %s\r\n" % g.h["etag"])))
            return g, h, n
        g, h, n = self.run_server(body)
        self.assertEqual((g.status, g.h["content-encoding"], g.h["access-control-allow-origin"]), (200, "gzip", ORIGIN))
        self.assertEqual(SNAP.validate_snapshot(json.loads(gzip.decompress(g.body))), [])
        self.assertEqual((h.status, h.body), (200, b""))
        self.assertEqual((n.status, n.body), (304, b""))

    def test_malformed_requests_get_a_plain_400_or_431(self):
        async def body(srv):
            sends = {
                "bad line": b"NONSENSE\r\n\r\n",
                "bad version": b"GET /healthz HTTP/2.0\r\nHost: %s\r\n\r\n" % HOST.encode(),
                "http 0.9": b"GET /healthz\r\n\r\n",
                "non ascii": b"GET /healthz HTTP/1.1\r\nHost: %s\r\nX: \xff\xfe\r\n\r\n" % HOST.encode(),
                "folded header": b"GET /healthz HTTP/1.1\r\nHost: %s\r\n X: y\r\n\r\n" % HOST.encode(),
                "padded name": b"GET /healthz HTTP/1.1\r\nHost : %s\r\n\r\n" % HOST.encode(),
                "no colon": b"GET /healthz HTTP/1.1\r\nHost: %s\r\nbroken\r\n\r\n" % HOST.encode(),
                "two hosts": b"GET /healthz HTTP/1.1\r\nHost: %s\r\nHost: evil.example.com\r\n\r\n" % HOST.encode(),
                "two origins": request_bytes(extra="Origin: %s\r\nOrigin: https://evil.example.com\r\n" % ORIGIN),
                "body": request_bytes(extra="Content-Length: 5\r\n") + b"hello",
                "chunked": request_bytes(extra="Transfer-Encoding: chunked\r\n"),
                "many headers": request_bytes(extra="".join("X-%d: 1\r\n" % i for i in range(60))),
            }
            return {k: Resp(await raw_exchange(srv.port, v)) for k, v in sends.items()}
        for k, r in self.run_server(body).items():
            self.assertEqual(r.status, 400, k)
            self.assertEqual(r.json(), {"error": "bad_request"}, k)

    def test_oversized_headers_get_431(self):
        async def body(srv):
            big = request_bytes(extra="X-Pad: %s\r\n" % ("a" * 20000))
            return Resp(await raw_exchange(srv.port, big)), Resp(await raw_exchange(srv.port, request_bytes(extra="X-Pad: %s\r\n" % ("a" * 9000))))
        a, b = self.run_server(body)
        self.assertEqual((a.status, a.json()), (431, {"error": "headers_too_large"}))
        self.assertEqual(b.status, 431)

    def test_a_silent_client_is_cut_off(self):
        async def body(srv):
            return Resp(await raw_exchange(srv.port, b"GET /healthz HTTP/1.1\r\nHost: x", timeout=2.0)), srv.active
        old = S.READ_TIMEOUT_S
        S.READ_TIMEOUT_S = 0.2
        try:
            r, active = self.run_server(body)
        finally:
            S.READ_TIMEOUT_S = old
        self.assertEqual(r.status, 400)
        self.assertEqual(active, 0)

    def test_the_connection_cap(self):
        async def body(srv):
            held = [await asyncio.open_connection("127.0.0.1", srv.port) for _ in range(2)]
            await asyncio.sleep(0.1)
            self.assertEqual(srv.active, 2)
            over = Resp(await raw_exchange(srv.port, request_bytes(path=HEALTH)))
            for _, w in held:
                w.close()
            await asyncio.sleep(0.2)
            again = Resp(await raw_exchange(srv.port, request_bytes(path=HEALTH)))
            return over, again, srv.active, dict(srv.per_address)
        over, again, active, per = self.run_server(body, LIVE_PUBLIC_MAX_CONNECTIONS="2", LIVE_PUBLIC_TRUST_PROXY="true")
        self.assertEqual((over.status, over.json()), (503, {"error": "busy"}))
        self.assertEqual(again.status, 200)
        self.assertEqual((active, per), (0, {}))                                            # counters return to zero

    def test_the_per_address_cap_applies_only_without_a_trusted_proxy(self):
        async def body(srv):
            held = [await asyncio.open_connection("127.0.0.1", srv.port) for _ in range(3)]
            await asyncio.sleep(0.1)
            over = Resp(await raw_exchange(srv.port, request_bytes(path=HEALTH)))
            for _, w in held:
                w.close()
            return over
        old = S.MAX_CONN_PER_ADDRESS
        S.MAX_CONN_PER_ADDRESS = 3
        try:
            self.assertEqual(self.run_server(body).status, 503)
            self.assertEqual(self.run_server(body, LIVE_PUBLIC_TRUST_PROXY="true").status, 200)       # behind a proxy every client shares the proxy's address
        finally:
            S.MAX_CONN_PER_ADDRESS = old

    def test_shutdown_closes_everything(self):
        async def go():
            srv = S.PublicServer(pc(), FakeSvc())
            await srv.start(host="127.0.0.1", port=0)
            port = srv.port
            idle = [await asyncio.open_connection("127.0.0.1", port) for _ in range(3)]
            await asyncio.sleep(0.1)
            self.assertEqual(srv.active, 3)
            await srv.close()
            ends = [await asyncio.wait_for(r.read(), 2) for r, _ in idle]                    # every idle client sees the connection end
            for _, w in idle:
                w.close()
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
            await srv.close()                                                                # closing twice is harmless
            left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return srv, ends, left
        srv, ends, left = asyncio.run(go())
        self.assertEqual((srv.active, len(srv.tasks), len(srv.writers), dict(srv.per_address)), (0, 0, 0, {}))
        self.assertEqual(left, [])

    def test_only_the_configured_interface_is_bound(self):
        async def body(srv):
            return {s.getsockname()[0] for s in srv.server.sockets}
        self.assertEqual(self.run_server(body), {"127.0.0.1"})
        self.assertEqual(pc().bind_host, "0.0.0.0")                                          # the production default comes from configuration (HOST), never from a request

    def test_end_to_end_with_the_real_relay(self):
        async def go():
            w = TLS.World()
            t = await w.go_streaming()
            t.feed(w.frame(type_=0))
            await w.until(lambda: w.svc.c["frames"] == 1)
            await w.fresh()
            srv = S.PublicServer(pc(), w.svc)
            await srv.start(host="127.0.0.1", port=0)
            ok = Resp(await raw_exchange(srv.port, request_bytes(extra="Origin: %s\r\n" % ORIGIN)))
            health = Resp(await raw_exchange(srv.port, request_bytes(path=HEALTH)))
            w.time.advance(31000)                                                            # the feed goes quiet: no new snapshot, the relay clock moves on
            stale = Resp(await raw_exchange(srv.port, request_bytes()))
            stale_health = Resp(await raw_exchange(srv.port, request_bytes(path=HEALTH)))
            await srv.close()
            await w.finish()
            return ok, health, stale, stale_health
        ok, health, stale, stale_health = asyncio.run(go())
        doc = ok.json()
        self.assertEqual((ok.status, doc["scope"]["universe"], doc["breadth"]["counted"], doc["gainers"]), (200, "test_set", 0, None))
        self.assertEqual(health.json()["snapshot"], "fresh")
        self.assertEqual(stale.status, 503)                                                  # withheld whether the relay noticed first (feed_unavailable) or the age did (stale_feed)
        self.assertIn(stale.json()["error"], ("stale_feed", "feed_unavailable"))
        self.assertNotEqual(stale_health.json()["snapshot"], "fresh")
        for blob in (ok.body, health.body, stale.body, stale_health.body):
            for s in RELAY_SECRETS:
                self.assertNotIn(s.encode(), blob)



# ====================================================================================================================== stage 3: the runtime (python -m live_public)
import contextlib                                                           # noqa: E402
import io                                                                   # noqa: E402
import os                                                                   # noqa: E402
import signal                                                               # noqa: E402
import socket                                                               # noqa: E402
from unittest import mock                                                   # noqa: E402

import live_public.__main__ as M                                            # noqa: E402
from live.replay import enc_feed, enc_frame, enc_ltpc                       # noqa: E402
from live_service.upstox import TransportError                              # noqa: E402

RT_ROWS = json.dumps([{"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|FAKE%04d" % i, "trading_symbol": s} for i, s in enumerate(C.DEFAULT_SYMBOLS)]
                     + [{"segment": "NSE_INDEX", "instrument_type": "INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "Nifty 50"}]).encode()
SECRET_TEXTS = (TLS.TOKEN, "ONETIMECODE", TLS.URI, "feed.example.test")


def rt_env(**over):
    base = {"UPSTOX_ANALYTICS_TOKEN": TLS.TOKEN, "ALLOWED_HOSTS": HOST, "HOST": "127.0.0.1", "PORT": "8080", "LIVE_PUBLIC_SERVE_SNAPSHOT": "true", "LIVE_PUBLIC_BURST": "200",
            "LIVE_PUBLIC_RATE_PER_MINUTE": "600"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


class Harness:
    """python -m live_public's amain() with scripted dependencies: a fake instrument file, a fake authorize endpoint, a fake WebSocket and a fake clock. No network, no real token."""

    def __init__(self, auth=None, **env_over):
        self.ft = TLS.FakeTime(TLS.T_OPEN)
        self.env = rt_env(**env_over)
        self.auth_script, self.auth_calls, self.made, self.responses = list(auth or []), [], [], []
        self.stream = io.StringIO()
        self.stop = asyncio.Event()
        self.srv = self.svc = None
        self.code = None

    def http_get(self, url, headers, timeout):
        self.auth_calls.append((url, dict(headers)))
        item = self.auth_script.pop(0) if self.auth_script else (200, {"data": {"authorized_redirect_uri": TLS.URI}})
        if isinstance(item, Exception):
            raise item
        return item

    def factory(self):
        t = TLS.MockTransport()
        self.made.append(t)
        return t

    def deps(self):
        return dict(http_get=self.http_get, transport_factory=self.factory, fetch_bytes=lambda url, timeout: RT_ROWS, clock_ms=self.ft.now, sleep=self.ft.sleep, stream=self.stream)

    async def run(self, scenario):
        ready = asyncio.Event()
        def on_ready(srv, svc, log):
            self.srv, self.svc = srv, svc
            ready.set()
        task = asyncio.ensure_future(M.amain(self.env, stop=self.stop, on_ready=on_ready, port=0, **self.deps()))
        await asyncio.wait_for(ready.wait(), 10)
        try:
            await scenario(self)
        finally:
            self.stop.set()
            self.code = await asyncio.wait_for(task, 20)
        return self.code

    async def until(self, cond, timeout=10.0):
        import time
        end, i = time.monotonic() + timeout, 0
        while time.monotonic() < end:
            if cond():
                return True
            i += 1
            await asyncio.sleep(0 if i % 50 else 0.001)
        return cond()

    def frame(self, ms=None):
        ms = ms or self.ft.ms
        feeds = {k: enc_feed(enc_ltpc(20100.0 if k in self.svc.ins.indices else 101.0, ms - 50, 3, 20000.0 if k in self.svc.ins.indices else 100.0)) for k in list(self.svc.ins.indices) + list(self.svc.ins.equities)}
        return enc_frame(feeds, 1, ms, {"NSE_EQ": 2})

    async def streaming(self):
        assert await self.until(lambda: self.svc.sup.state == "streaming"), self.svc.sup.state
        return self.made[-1]

    async def fresh(self, advance=2500):
        self.ft.advance(advance)
        target = self.ft.ms
        assert await self.until(lambda: self.svc.latest is not None and self.svc.latest.doc["server_ts_ms"] >= target)
        return self.svc.latest

    async def go_live(self):
        t = await self.streaming()
        t.feed(self.frame())
        assert await self.until(lambda: self.svc.c["frames"] >= 1)
        return t, await self.fresh()

    async def get(self, path=SNAP_PATH, extra="", method="GET"):
        r = Resp(await raw_exchange(self.srv.port, request_bytes(path=path, extra=extra, method=method)))
        self.responses.append(r)
        return r

    def secrets_seen(self):
        """Secrets found anywhere they must not be. The log may name the feed's HOST (the relay's own scrubbed form is wss://host/***); responses may not contain even that."""
        logs, resp = self.stream.getvalue(), [r.body.decode("latin-1") + str(r.h) for r in self.responses]
        return [s for s in SECRET_TEXTS if (s != "feed.example.test" and s in logs) or any(s in b for b in resp)]


def run_rt(h, scenario):
    return asyncio.run(h.run(scenario))


class RuntimeEndToEnd(unittest.TestCase):
    def test_a_snapshot_published_by_the_running_relay_reaches_the_endpoint(self):
        h = Harness()
        seen = {}
        async def scenario(h):
            t, pub = await h.go_live()
            r = await h.get(extra="Origin: %s\r\n" % ORIGIN)
            doc = r.json()
            seen["bound"] = {sk.getsockname()[0] for sk in h.srv.server.sockets}
            seen.update(status=r.status, cors=r.h.get("access-control-allow-origin"), doc=doc, seq=pub.doc["seq"], etag=r.h["etag"], sent=t.sent, health=(await h.get(HEALTH)).json())
            seen["304"] = (await h.get(extra="If-None-Match: %s\r\n" % r.h["etag"])).status
            seen["head"] = (await h.get(method="HEAD")).body
        self.assertEqual(run_rt(h, scenario), 0)
        doc = seen["doc"]
        self.assertEqual((seen["status"], seen["cors"]), (200, ORIGIN))
        self.assertEqual(SNAP.validate_snapshot(doc), [])
        self.assertEqual((doc["seq"], doc["scope"]["universe"], doc["quality"]["ok"], doc["market"]["status"]), (seen["seq"], "test_set", True, "open"))
        self.assertEqual(sorted(doc["prices"]), sorted(C.DEFAULT_SYMBOLS))
        self.assertEqual([i["name"] for i in doc["indices"]], ["NIFTY 50"])
        self.assertEqual(doc["breadth"]["counted"], 0)                                      # computed over six instruments by the relay, not published
        self.assertIsNone(doc["gainers"])
        self.assertEqual(seen["health"], {"status": "ok", "snapshot": "fresh"})
        self.assertEqual((seen["304"], seen["head"]), (304, b""))
        self.assertEqual(seen["bound"], {"127.0.0.1"})                                      # bound to the configured HOST only
        self.assertEqual(len(h.made), 1)                                                    # ONE WebSocket
        self.assertEqual(len(h.auth_calls), 1)
        self.assertEqual(h.auth_calls[0][1].get("Authorization"), "Bearer " + TLS.TOKEN)    # the token goes to the authorize call and nowhere else
        msg = json.loads(seen["sent"][0])
        self.assertEqual((msg["method"], msg["data"]["mode"], len(msg["data"]["instrumentKeys"])), ("sub", "ltpc", 6))
        self.assertEqual(h.secrets_seen(), [])

    def test_the_gate_off_means_no_relay_and_no_connection(self):
        h = Harness(LIVE_PUBLIC_SERVE_SNAPSHOT="false")
        seen = {}
        async def scenario(h):
            await asyncio.sleep(0.3)
            seen["snap"] = await h.get()
            seen["health"] = await h.get(HEALTH)
        self.assertEqual(run_rt(h, scenario), 0)
        self.assertEqual((seen["snap"].status, seen["snap"].json()), (503, {"error": "snapshot_disabled"}))
        self.assertEqual(seen["health"].json()["snapshot"], "disabled")
        self.assertEqual((h.auth_calls, h.made), ([], []))                                  # no authorize call, no WebSocket
        self.assertIn("relay is not started", h.stream.getvalue())
        self.assertEqual(h.secrets_seen(), [])

    def test_unset_gate_is_off_too(self):
        h = Harness()
        h.env.pop("LIVE_PUBLIC_SERVE_SNAPSHOT")
        async def scenario(h):
            await asyncio.sleep(0.2)
            self.assertEqual((await h.get()).json(), {"error": "snapshot_disabled"})
        self.assertEqual(run_rt(h, scenario), 0)
        self.assertEqual((h.auth_calls, h.made), ([], []))

    def test_before_the_first_snapshot_and_during_an_authorize_failure(self):
        bad = [(500, {}), (500, {}), RuntimeError("boom " + TLS.TOKEN + " " + TLS.URI)]
        h = Harness(auth=bad)
        seen = {}
        async def scenario(h):
            assert await h.until(lambda: len(h.auth_calls) >= 1)
            seen["snap"] = await h.get()
            seen["health"] = await h.get(HEALTH)
            h.ft.advance(120000)
            assert await h.until(lambda: len(h.auth_calls) >= 2)
        self.assertEqual(run_rt(h, scenario), 0)
        self.assertEqual(seen["snap"].status, 503)                                          # the relay publishes an UNHEALTHY snapshot while it cannot connect; it is withheld either way
        self.assertIn(seen["snap"].json()["error"], ("no_snapshot_yet", "feed_unavailable"))
        self.assertEqual((seen["health"].status, seen["health"].json()["status"]), (503, "unavailable"))
        self.assertEqual(h.made, [])                                                        # never connected: nothing was authorised
        self.assertEqual(h.secrets_seen(), [])

    def test_relay_failure_and_recovery(self):
        h = Harness()
        seen = {}
        async def scenario(h):
            t1, pub1 = await h.go_live()
            seen["before"] = (await h.get()).status
            t1.feed(TransportError("connection reset " + TLS.URI))                          # the feed drops
            assert await h.until(lambda: h.svc.sup.state != "streaming")
            h.ft.advance(3000)
            seen["during"] = []
            for _ in range(3):
                h.ft.advance(40000)                                                         # longer than the stale threshold, in steps
                await asyncio.sleep(0.05)
                r = await h.get()
                seen["during"].append((r.status, r.json().get("error")))
                if len(h.made) > 1:
                    break
            assert await h.until(lambda: len(h.made) >= 2 or h.svc.sup.state == "streaming", 15) or True
            for _ in range(60):                                                             # let the supervisor's backoff elapse
                if len(h.made) >= 2:
                    break
                h.ft.advance(20000)
                await asyncio.sleep(0.02)
            assert len(h.made) >= 2, (h.svc.sup.state, len(h.made))
            t2 = await h.streaming()
            t2.feed(h.frame())
            assert await h.until(lambda: h.svc.c["frames"] >= 2)
            pub2 = await h.fresh()
            r = await h.get()
            seen["after"] = (r.status, r.json()["seq"], pub1.doc["seq"], r.json()["quality"]["ok"])
            seen["closed_first"] = h.made[0].closed
            seen["health"] = (await h.get(HEALTH)).json()
        self.assertEqual(run_rt(h, scenario), 0)
        self.assertEqual(seen["before"], 200)
        for status, error in seen["during"]:
            self.assertEqual(status, 503)                                                   # never a snapshot while the feed is down
            self.assertIn(error, ("stale_feed", "feed_unavailable", "invalid_snapshot"))
        status, seq, seq_before, ok = seen["after"]
        self.assertEqual((status, ok), (200, True))
        self.assertGreater(seq, seq_before)
        self.assertTrue(seen["closed_first"])                                               # the old socket was closed before the new one: never two at once
        self.assertEqual(seen["health"], {"status": "ok", "snapshot": "fresh"})
        self.assertEqual(h.secrets_seen(), [])

    def test_stale_and_invalid_snapshots_are_rejected_in_the_running_service(self):
        h = Harness()
        seen = {}
        async def scenario(h):
            t, pub = await h.go_live()
            good = h.svc.latest
            def swap(mutate):
                doc = copy.deepcopy(good.doc)
                mutate(doc)
                raw, gz, etag = SNAP.encode_snapshot(doc)
                h.svc.latest = Published(doc, raw, gz, etag, good.at_ms)
            def drop(d):
                del d["scope"]
            def future(d):
                d["server_ts_ms"] += 600000
                d["generated_at"] = SNAP._iso(d["server_ts_ms"])
            def unhealthy(d):
                d["quality"] = {"ok": False, "reasons": ["feed_silent"], "warnings": []}
            for name, mut in (("malformed", drop), ("future", future), ("unhealthy", unhealthy)):
                swap(mut)
                r = await h.get()
                seen[name] = (r.status, r.json())
            h.svc.latest = good
            seen["restored"] = (await h.get()).status
            h.svc.latest = None
            seen["missing"] = (await h.get()).json()
            h.svc.latest = good
            class Frozen:                                                                   # the server's view of the relay: the last snapshot, 31 s later
                latest, clock, health = good, staticmethod(lambda: good.doc["server_ts_ms"] + 31000), staticmethod(h.svc.health)
            h.srv.app.service = Frozen()
            seen["stale"] = (await h.get()).json()
        self.assertEqual(run_rt(h, scenario), 0)
        self.assertEqual(seen["malformed"], (503, {"error": "invalid_snapshot"}))
        self.assertEqual(seen["future"], (503, {"error": "invalid_snapshot"}))
        self.assertEqual(seen["unhealthy"], (503, {"error": "feed_unavailable"}))
        self.assertEqual(seen["restored"], 200)
        self.assertEqual(seen["missing"], {"error": "no_snapshot_yet"})
        self.assertEqual(seen["stale"], {"error": "stale_feed"})
        self.assertEqual(h.secrets_seen(), [])

    def test_shutdown_closes_the_listener_the_connections_and_the_relay(self):
        h = Harness()
        seen = {}
        async def scenario(h):
            t, _ = await h.go_live()
            seen["idle"] = [await asyncio.open_connection("127.0.0.1", h.srv.port) for _ in range(2)]
            seen["port"], seen["t"] = h.srv.port, t
            await asyncio.sleep(0.1)
        async def go():
            code = await h.run(scenario)
            ends = [await asyncio.wait_for(r.read(), 2) for r, _ in seen["idle"]]            # every idle client sees its connection end
            for _, w in seen["idle"]:
                w.close()
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", seen["port"])                    # and nobody can connect any more
            return code, ends
        code, ends = asyncio.run(go())
        self.assertEqual((code, ends), (0, [b"", b""]))
        self.assertTrue(seen["t"].closed)                                                   # the WebSocket was closed
        self.assertEqual((h.srv.active, len(h.srv.tasks), len(h.srv.writers), dict(h.srv.per_address)), (0, 0, 0, {}))
        self.assertIn("stopped", h.stream.getvalue())

    def test_cancellation_cleans_up(self):
        h = Harness()
        async def go():
            ready = asyncio.Event()
            task = asyncio.ensure_future(M.amain(h.env, stop=h.stop, on_ready=lambda s, v, l: (setattr(h, "srv", s), setattr(h, "svc", v), ready.set()), port=0, **h.deps()))
            await asyncio.wait_for(ready.wait(), 10)
            await h.go_live()
            port = h.srv.port
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
            left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return left, h.made[0].closed
        left, closed = asyncio.run(go())
        self.assertTrue(closed)
        self.assertEqual(left, [])

    def test_a_signal_stops_it_cleanly(self):
        h = Harness()
        async def go():
            ready = asyncio.Event()
            def on_ready(srv, svc, log):
                h.srv, h.svc = srv, svc
                asyncio.get_running_loop().call_later(0.2, os.kill, os.getpid(), signal.SIGTERM)
                ready.set()
            return await asyncio.wait_for(M.amain(h.env, stop=h.stop, on_ready=on_ready, install_signals=True, port=0, **h.deps()), 15)
        self.assertEqual(asyncio.run(go()), 0)
        self.assertTrue(h.stop.is_set())

    def test_a_relay_that_dies_on_its_own_ends_the_process_with_an_error(self):
        h = Harness()
        async def boom(self, stop):
            raise RuntimeError("relay exploded " + TLS.TOKEN)
        async def scenario(h):
            await asyncio.wait_for(h.stop.wait(), 10)
        with mock.patch.object(M.relay.RelayService, "run", boom):
            code = run_rt(h, scenario)
        self.assertEqual(code, 1)
        self.assertIn("relay stopped on its own", h.stream.getvalue())
        self.assertEqual(h.secrets_seen(), [])

    def test_a_relay_that_will_not_stop_is_cancelled_after_the_grace_period(self):
        h = Harness()
        cancelled = []
        async def stubborn(self, stop):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        async def go():
            ready = asyncio.Event()
            task = asyncio.ensure_future(M.amain(h.env, stop=h.stop, on_ready=lambda s, v, l: ready.set(), port=0, grace_s=0.2, **h.deps()))
            await asyncio.wait_for(ready.wait(), 10)
            h.stop.set()
            code = await asyncio.wait_for(task, 10)
            return code, list(cancelled)                                                     # looked at BEFORE the loop is torn down
        with mock.patch.object(M.relay.RelayService, "run", stubborn):
            code, was_cancelled = asyncio.run(go())
        self.assertEqual(code, 0)
        self.assertEqual(was_cancelled, [True])                                             # it was cancelled by the shutdown, not left running


class StartupErrors(unittest.TestCase):
    def run_main(self, env_, **deps):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = M.main([], env_, **deps)
        return code, err.getvalue()

    def test_bad_configuration_stops_before_anything_is_opened(self):
        calls = []
        deps = dict(http_get=lambda *a: calls.append("auth"), transport_factory=lambda: calls.append("ws"), fetch_bytes=lambda *a: calls.append("fetch") or b"[]")
        for name, e in (("no token", rt_env(UPSTOX_ANALYTICS_TOKEN=None)), ("short token", rt_env(UPSTOX_ANALYTICS_TOKEN="x")), ("no hosts", rt_env(ALLOWED_HOSTS=None)),
                        ("wildcard origin", rt_env(ALLOWED_ORIGIN="*")), ("http origin", rt_env(ALLOWED_ORIGIN="http://arnavdev11.github.io")), ("bad port", rt_env(PORT="99999")),
                        ("bad flag", rt_env(LIVE_PUBLIC_SERVE_SNAPSHOT="yes")), ("bad host", rt_env(HOST="not-an-ip")), ("empty", {})):
            code, err = self.run_main(e, **deps)
            self.assertEqual(code, 2, name)
            self.assertTrue(err.startswith("error: "), "%s -> %r" % (name, err))
            self.assertNotIn(TLS.TOKEN, err, name)
        self.assertEqual(calls, [])
        code, err = self.run_main(rt_env(ALLOWED_ORIGIN=TLS.TOKEN + "x"), **deps)               # a secret pasted into the wrong variable is not echoed
        self.assertEqual(code, 2)
        self.assertNotIn(TLS.TOKEN, err)

    def test_arguments_are_refused(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(M.main(["--port", "1"], rt_env()), 2)
        self.assertIn("environment variables only", err.getvalue())

    def test_an_instrument_download_failure_is_reported_without_the_token(self):
        def fetch(url, timeout):
            raise RuntimeError("download failed for token " + TLS.TOKEN)
        code, err = self.run_main(rt_env(), fetch_bytes=fetch)
        self.assertEqual(code, 1)
        self.assertNotIn(TLS.TOKEN, err)
        self.assertIn("RuntimeError", err)

    def test_a_configuration_error_raised_while_building_is_scrubbed_too(self):
        from live_service.safety import ConfigError as CE
        def fetch(url, timeout):
            raise CE("rejected, token was " + TLS.TOKEN + " at " + TLS.URI)
        code, err = self.run_main(rt_env(), fetch_bytes=fetch)
        self.assertEqual(code, 2)
        self.assertNotIn(TLS.TOKEN, err)
        self.assertNotIn("ONETIMECODE", err)

    def test_a_port_that_is_taken_fails_before_the_relay_starts(self):
        calls = []
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            code, err = self.run_main(rt_env(PORT=str(s.getsockname()[1])), http_get=lambda *a: calls.append("auth"), transport_factory=lambda: calls.append("ws"),
                                      fetch_bytes=lambda *a: RT_ROWS)
        self.assertEqual(code, 1)
        self.assertIn("cannot start the server", err)
        self.assertEqual(calls, [])                                                         # nothing was authorised, nothing connected
        self.assertNotIn(TLS.TOKEN, err)


class RuntimeScope(unittest.TestCase):
    def test_the_runtime_reuses_the_relay_and_adds_no_websocket_or_token_handling(self):
        src = (ROOT / "live_public" / "__main__.py").read_text(encoding="utf-8")
        for needle in ("import websockets", "websockets.connect", "live_service.server", "print(cfg", "print(token", "Authorization", "Bearer"):
            self.assertNotIn(needle, src, needle)
        self.assertEqual(src.count("RelayService("), 1)                                      # exactly one relay, so exactly one connection
        self.assertEqual(src.count("os.environ"), 1)
        self.assertNotIn("serve_snapshot=True", src)

    def test_the_local_relay_is_untouched(self):
        import subprocess
        r = subprocess.run(["git", "diff", "--stat", "origin/main", "--", "live_service", "live", "quarterly_results_updater.py", "test_quarterly_results.py", ".github", "market_data.yml", ".gitignore"],
                           cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertEqual(r.stdout.strip(), "")



# ====================================================================================================================== stage 5: hosting readiness (Render)
import sys as _sys                                                          # noqa: E402

import yaml                                                                 # noqa: E402

FAKE_RENDER_HOST = "stocklens-live-x1y2.onrender.com"


def render_service():
    return yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))["services"][0]


def render_env(**extra):
    """The environment Render would give the process: the Blueprint's own values, the dashboard-entered token (a fake one here) and the variable Render provides itself."""
    out = {e["key"]: e["value"] for e in render_service()["envVars"] if "value" in e}
    out.update({"UPSTOX_ANALYTICS_TOKEN": TLS.TOKEN, "RENDER_EXTERNAL_HOSTNAME": FAKE_RENDER_HOST})
    out.update(extra)
    return {k: v for k, v in out.items() if v is not None}


class RenderBlueprint(unittest.TestCase):
    def test_the_service_is_declared_as_requested(self):
        svc = render_service()
        self.assertEqual((svc["type"], svc["runtime"], svc["startCommand"], svc["healthCheckPath"]), ("web", "python", "python -m live_public", "/healthz/live"))
        self.assertEqual(svc["buildCommand"], "pip install -r requirements.txt")
        self.assertIsInstance(svc["autoDeployTrigger"], str)                                 # the string "off" - an unquoted off would be read by YAML as false
        self.assertEqual(svc["autoDeployTrigger"], "off")                                    # nothing deploys by itself
        self.assertNotEqual(svc.get("plan"), "free")                                         # a always-on feed is not a free-instance job
        self.assertTrue(svc["name"] and svc["region"])

    def test_no_secret_and_no_guessed_address_is_in_any_deployment_file(self):
        for name in ("render.yaml", "requirements.txt", "live_public/DEPLOY_RENDER.md", "live_public/__main__.py", "live_public/config.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for bad in (TLS.TOKEN, TOKEN, "eyJ", "wss://", "ONETIMECODE", "authorized_redirect"):
                self.assertNotIn(bad, text, "%s contains %r" % (name, bad))
        env_entries = {e["key"]: e for e in render_service()["envVars"]}
        self.assertIs(env_entries["UPSTOX_ANALYTICS_TOKEN"].get("sync"), False)               # asked for in the dashboard...
        self.assertNotIn("value", env_entries["UPSTOX_ANALYTICS_TOKEN"])                      # ...and never given a value in the file
        self.assertNotIn("generateValue", env_entries["UPSTOX_ANALYTICS_TOKEN"])
        for name in ("render.yaml", "index.html"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(text.replace("onrender.com name", "").replace("onrender.com", "") if name == "render.yaml" else text, r"https://[a-z0-9-]+\.onrender\.com")
        self.assertNotRegex((ROOT / "index.html").read_text(encoding="utf-8"), r"onrender\.com")        # no Render address was invented in the website

    def test_every_variable_in_the_blueprint_is_one_the_service_reads(self):
        known = {"UPSTOX_ANALYTICS_TOKEN", "ALLOWED_HOSTS", "ALLOWED_ORIGIN", "HOST", "PORT", "LIVE_PUBLIC_SERVE_SNAPSHOT", "LIVE_PUBLIC_TRUST_PROXY", "LIVE_PUBLIC_RATE_PER_MINUTE",
                 "LIVE_PUBLIC_BURST", "LIVE_PUBLIC_MAX_CONNECTIONS", "LIVE_PUBLIC_STALE_AFTER_S"}
        src = (ROOT / "live_public" / "config.py").read_text(encoding="utf-8")
        for e in render_service()["envVars"]:
            if e["key"] != "PYTHON_VERSION":                                                # read by Render's build, not by the service
                self.assertIn(e["key"], known, e["key"])
                self.assertIn(e["key"], src, e["key"])

    def test_the_production_configuration_loads_from_the_blueprint_values(self):
        c = C.load(render_env())
        self.assertEqual((c.bind_host, c.port), ("0.0.0.0", 10000))
        self.assertEqual(c.hosts, (FAKE_RENDER_HOST,))                                        # exactly the service's own name, from the platform
        self.assertEqual(c.origins, ("https://arnavdev11.github.io",))
        self.assertFalse(c.serve_snapshot)                                                    # the gate ships closed
        self.assertFalse(c.trust_proxy)                                                       # no undocumented forwarded header is trusted
        self.assertEqual((c.rate_per_minute, c.burst, c.max_connections, c.stale_after_s), (600, 200, 200, 30))
        self.assertNotIn(TLS.TOKEN, repr(c))
        self.assertTrue(C.load(render_env(LIVE_PUBLIC_SERVE_SNAPSHOT="true")).serve_snapshot)   # what the dashboard switch does

    def test_the_hosts_rule_follows_the_platform_name_and_stays_strict(self):
        self.assertEqual(C.load(env(ALLOWED_HOSTS=None, RENDER_EXTERNAL_HOSTNAME=FAKE_RENDER_HOST)).hosts, (FAKE_RENDER_HOST,))
        both = C.load(env(ALLOWED_HOSTS="stocks.example.org," + FAKE_RENDER_HOST, RENDER_EXTERNAL_HOSTNAME=FAKE_RENDER_HOST)).hosts
        self.assertEqual(both, ("stocks.example.org", FAKE_RENDER_HOST))
        self.assertEqual(C.load(env(ALLOWED_HOSTS="only.example.org", RENDER_EXTERNAL_HOSTNAME=FAKE_RENDER_HOST)).hosts, ("only.example.org",))     # explicit setting wins
        for bad in ("", "UPPER.onrender.com", "a.onrender.com:443", "*.onrender.com", "127.0.0.1", "localhost", "x" * 300 + ".com"):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                C.load(env(ALLOWED_HOSTS=None, RENDER_EXTERNAL_HOSTNAME=bad))
        with self.assertRaises(ConfigError):
            C.load(env(ALLOWED_HOSTS=None))                                                   # neither set: fails, as before


class RenderProductionBehaviour(unittest.TestCase):
    """The real runtime with the Blueprint's environment (fake token, fake dependencies, loopback bind): what Render's health check and the page would see."""

    def go(self, env_, scenario):
        env_ = dict(env_, HOST="127.0.0.1")                                                  # the only change from Render's environment: a loopback bind for the test
        h = Harness()
        h.env = env_
        return h, run_rt(h, scenario)

    def test_health_check_with_the_platform_host_is_minimal_and_other_hosts_are_refused(self):
        seen = {}
        async def scenario(h):
            r = Resp(await raw_exchange(h.srv.port, request_bytes(path=HEALTH, host=FAKE_RENDER_HOST)))
            seen.update(health=r, raw=r.body, headers=r.h)
            seen["other"] = Resp(await raw_exchange(h.srv.port, request_bytes(path=HEALTH, host="evil.example.com")))
            seen["snap"] = Resp(await raw_exchange(h.srv.port, request_bytes(host=FAKE_RENDER_HOST)))
            seen["cors"] = Resp(await raw_exchange(h.srv.port, request_bytes(host=FAKE_RENDER_HOST, extra="Origin: https://arnavdev11.github.io\r\n")))
            seen["badcors"] = Resp(await raw_exchange(h.srv.port, request_bytes(host=FAKE_RENDER_HOST, extra="Origin: https://evil.example.com\r\n")))
        h, code = self.go(render_env(), scenario)
        self.assertEqual(code, 0)
        self.assertEqual((seen["health"].status, seen["health"].json()), (200, {"status": "ok", "snapshot": "disabled"}))      # the check Render runs passes with the gate shut
        self.assertEqual(seen["other"].status, 403)
        self.assertEqual((seen["snap"].status, seen["snap"].json()), (503, {"error": "snapshot_disabled"}))
        self.assertEqual((seen["cors"].status, seen["badcors"].status), (503, 403))
        self.assertNotIn("access-control-allow-origin", seen["badcors"].h)
        self.assertEqual((h.auth_calls, h.made), ([], []))                                    # the shipped configuration never contacts Upstox
        self.assertEqual(h.secrets_seen(), [])

    def test_with_the_gate_open_the_health_check_passes_and_serves_only_the_expected_fields(self):
        seen = {}
        async def scenario(h):
            await h.go_live()
            r = Resp(await raw_exchange(h.srv.port, request_bytes(path=HEALTH, host=FAKE_RENDER_HOST)))
            s = Resp(await raw_exchange(h.srv.port, request_bytes(host=FAKE_RENDER_HOST, extra="Origin: https://arnavdev11.github.io\r\n")))
            seen.update(r=r, s=s)
        h, code = self.go(render_env(LIVE_PUBLIC_SERVE_SNAPSHOT="true"), scenario)
        self.assertEqual(code, 0)
        self.assertEqual(seen["r"].json(), {"status": "ok", "snapshot": "fresh"})
        self.assertEqual(seen["s"].status, 200)
        self.assertEqual(seen["s"].h["access-control-allow-origin"], "https://arnavdev11.github.io")
        self.assertEqual(h.secrets_seen(), [])

    def test_missing_or_invalid_production_configuration_fails_safely(self):
        calls = []
        deps = dict(http_get=lambda *a: calls.append("auth"), transport_factory=lambda: calls.append("ws"), fetch_bytes=lambda *a: calls.append("fetch") or b"[]")
        for name, e in (("no token", render_env(UPSTOX_ANALYTICS_TOKEN=None)), ("no host at all", render_env(RENDER_EXTERNAL_HOSTNAME=None)),
                        ("wildcard origin", render_env(ALLOWED_ORIGIN="*")), ("trust flag garbage", render_env(LIVE_PUBLIC_TRUST_PROXY="maybe")),
                        ("port out of range", render_env(PORT="70000")), ("gate garbage", render_env(LIVE_PUBLIC_SERVE_SNAPSHOT="on"))):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = M.main([], e, **deps)
            self.assertEqual(code, 2, name)
            self.assertTrue(err.getvalue().startswith("error: "), name)
            self.assertNotIn(TLS.TOKEN, err.getvalue(), name)
        self.assertEqual(calls, [])                                                           # nothing was downloaded, authorised or connected


class DependenciesAndPackaging(unittest.TestCase):
    def test_requirements_pin_exactly_the_third_party_imports(self):
        import ast
        pins = {}
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$", line)         # exact pins only
                pins[line.split("==")[0].lower()] = line.split("==")[1]
        imported = set()
        for pkg in ("live_public", "live_service", "live"):
            for f in (ROOT / pkg).rglob("*.py"):
                for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
                    imported.update(n.split(".")[0] for n in names)
        optional = {"upstox_client", "google"}                                               # live/decoder.py crosscheck_with_sdk(): inside try/except, "never required", not used by the relay
        decoder_src = (ROOT / "live" / "decoder.py").read_text(encoding="utf-8")
        self.assertIn("def crosscheck_with_sdk", decoder_src)
        self.assertEqual({n for n in optional if n in imported}, optional)
        self.assertEqual([f.name for f in (ROOT / "live").rglob("*.py") if "upstox_client" in f.read_text(encoding="utf-8")], ["decoder.py"])
        self.assertNotIn("crosscheck_with_sdk", (ROOT / "live_service" / "relay.py").read_text(encoding="utf-8"))
        third = {n for n in imported if n not in _sys.stdlib_module_names and n not in ("live", "live_service", "live_public") and n not in optional}
        self.assertEqual(third, set(pins), "requirements.txt must list exactly the imports the service needs")
        self.assertEqual(set(pins), {"websockets", "requests"})

    def test_the_website_switch_is_off_and_the_local_mode_is_still_there(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('var PUBLIC_LIVE={enabled:false,url:""};'), 1)
        self.assertEqual(html.count("var PUBLIC_LIVE="), 1)
        self.assertIn('q.get("live")!=="local"', html)                                        # the local private-test gate
        self.assertIn('q.get("live_set")==="private_test"', html)

    def test_this_stage_touches_only_deployment_files(self):
        import subprocess
        r = subprocess.run(["git", "diff", "--stat", "origin/main", "--", "live_service", "live", ".github", "market_data.yml", ".gitignore", "quarterly_results_updater.py",
                            "test_quarterly_results.py", "sensex_publish.py", "sector_publish.py", "update.yml"], cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertEqual(r.stdout.strip(), "")



# ====================================================================================================================== liveness endpoint (/healthz/live) and the platform health check
LIVE = "/healthz/live"
ALIVE_BODY = b'{"status":"alive"}'


class Boom:
    """A service that raises on EVERY access: the liveness path must never touch it."""
    def __getattr__(self, name):
        raise RuntimeError("liveness touched the service (%s): token SECRET-TOKEN-abc123XYZ" % name)


class Liveness(unittest.TestCase):
    def test_it_answers_200_with_a_fixed_body_for_get_and_head(self):
        app = app_for()
        g, h = ask(app, LIVE), ask(app, LIVE, "HEAD")
        self.assertEqual((g.status, g.body, g.h["content-type"]), (200, ALIVE_BODY, "application/json; charset=utf-8"))
        self.assertEqual((h.status, h.body, h.h), (200, b"", g.h))
        self.assertEqual(int(h.h["content-length"]), len(ALIVE_BODY))
        for r in (g, h):
            self.assertEqual(r.h["cache-control"], "no-store")
            self.assertEqual(r.h["x-content-type-options"], "nosniff")
            self.assertIn("date", r.h)
            self.assertNotIn("access-control-allow-origin", r.h)

    def test_it_is_200_whatever_the_feed_is_doing(self):
        ts = GOOD.doc["server_ts_ms"]
        cases = {"fresh": FakeSvc(), "stale": FakeSvc(now=ts + 3_600_000), "no snapshot": FakeSvc(None), "future": FakeSvc(now=ts - 3_600_000), "relay degraded": FakeSvc(status="degraded"),
                 "relay stopped": FakeSvc(status="stopped"), "health raises": FakeSvc(status=RuntimeError("SECRET-TOKEN-abc123XYZ")), "service raises on everything": Boom(),
                 "malformed snapshot": FakeSvc(latest=Published({"x": 1}, b"", b"", '"x"', 0)), "gate off": None}
        for name, svc in cases.items():
            app = app_for(svc, **({"LIVE_PUBLIC_SERVE_SNAPSHOT": "false"} if name == "gate off" else {}))
            r = ask(app, LIVE)
            self.assertEqual((r.status, r.body), (200, ALIVE_BODY), name)
        deg = app_for(FakeSvc(status="degraded"))
        self.assertEqual(ask(deg, HEALTH).status, 503)                                       # the reporting endpoint still says so...
        self.assertEqual(ask(deg, LIVE).status, 200)                                          # ...and the liveness endpoint does not

    def test_the_reporting_endpoints_are_unchanged(self):
        app = app_for()
        self.assertEqual(ask(app, HEALTH).json(), {"status": "ok", "snapshot": "fresh"})
        self.assertEqual(ask(app, SNAP_PATH).status, 200)
        self.assertEqual(ask(app_for(FakeSvc(status="degraded")), HEALTH).json()["status"], "unavailable")
        self.assertEqual(ask(app_for(FakeSvc(None)), SNAP_PATH).json(), {"error": "no_snapshot_yet"})

    def test_it_does_not_depend_on_host_origin_or_the_rate_limit(self):
        app = app_for(LIVE_PUBLIC_RATE_PER_MINUTE="60", LIVE_PUBLIC_BURST="1")
        for host in (HOST, "evil.example.com", "127.0.0.1", None, "stocklens-live-x.onrender.com"):
            self.assertEqual(ask(app, LIVE, host=host).status, 200, repr(host))
        r = ask(app, LIVE, origin="https://evil.example.com")
        self.assertEqual(r.status, 200)
        self.assertNotIn("access-control-allow-origin", r.h)                                  # answered, never granted to a page
        self.assertEqual(ask(app, LIVE, origin=ORIGIN).h.get("access-control-allow-origin"), None)
        self.assertEqual(ask(app, HEALTH).status, 200)                                        # the one token of the bucket is still there: liveness used none
        self.assertEqual(ask(app, HEALTH).status, 429)
        for _ in range(50):
            self.assertEqual(ask(app, LIVE).status, 200)                                      # and a throttled client cannot make the check fail
        self.assertEqual(ask(app, SNAP_PATH).status, 429)

    def test_only_the_exact_path_and_safe_methods_are_liveness(self):
        app = app_for()
        for m in ("POST", "PUT", "DELETE", "OPTIONS", "PATCH"):
            self.assertEqual(ask(app, LIVE, m).status, 405, m)
        for p in (LIVE + "/", LIVE + "?x=1", LIVE + "#x", "/healthz/Live", "/healthz/liveness", "/healthz/live/x", "/live", "/healthz//live", "/HEALTHZ/live", "/healthz/live%2f"):
            self.assertIn(ask(app, p).status, (400, 403, 404), p)
        self.assertNotEqual(ask(app, "/healthz/other").status, 200)

    def test_it_exposes_nothing(self):
        app = app_for(Boom())
        r = ask(app, LIVE)
        blob = (r.body.decode() + str(r.h))
        for s in RELAY_SECRETS + ("SECRET", "Traceback", "RuntimeError", "uptime", "counters", "events", "snapshot", "version", "state"):
            self.assertNotIn(s, blob)
        self.assertEqual(r.json(), {"status": "alive"})

    def test_over_the_wire_including_with_a_wrong_host_and_a_broken_relay(self):
        async def go():
            srv = S.PublicServer(pc(), Boom())
            await srv.start(host="127.0.0.1", port=0)
            try:
                a = Resp(await raw_exchange(srv.port, request_bytes(path=LIVE)))
                b = Resp(await raw_exchange(srv.port, request_bytes(path=LIVE, host="anything.invalid", method="HEAD")))
                c = Resp(await raw_exchange(srv.port, request_bytes(path=HEALTH)))
                d = Resp(await raw_exchange(srv.port, request_bytes(path=LIVE + "?x=1")))
                return a, b, c, d
            finally:
                await srv.close()
        a, b, c, d = asyncio.run(go())
        self.assertEqual((a.status, a.body), (200, ALIVE_BODY))
        self.assertEqual((b.status, b.body), (200, b""))
        self.assertEqual(c.status, 503)                                                       # /healthz on a broken relay is still a safe 503
        self.assertNotIn(b"SECRET", c.body)
        self.assertNotEqual(d.status, 200)

    def test_the_runtime_stays_live_while_the_feed_fails_and_the_gate_is_open(self):
        h = Harness(auth=[(500, {})] * 3)
        seen = {}
        async def scenario(h):
            assert await h.until(lambda: len(h.auth_calls) >= 1)
            seen["live"] = await h.get(LIVE)
            seen["health"] = await h.get(HEALTH)
            seen["snap"] = await h.get()
        self.assertEqual(run_rt(h, scenario), 0)
        self.assertEqual((seen["live"].status, seen["live"].body), (200, ALIVE_BODY))
        self.assertEqual(seen["health"].status, 503)                                          # what would have restarted the service under the old health check
        self.assertEqual(seen["snap"].status, 503)
        self.assertEqual(h.secrets_seen(), [])

    def test_liveness_is_available_immediately_and_stops_with_the_server(self):
        async def go():
            srv = S.PublicServer(pc(), FakeSvc(None))
            await srv.start(host="127.0.0.1", port=0)
            port = srv.port
            first = Resp(await raw_exchange(port, request_bytes(path=LIVE)))
            await srv.close()
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
            return first
        self.assertEqual(asyncio.run(go()).status, 200)

    def test_the_blueprint_uses_it_and_the_reporting_path_is_not_the_platform_check(self):
        svc = render_service()
        self.assertEqual(svc["healthCheckPath"], LIVE)
        self.assertNotEqual(svc["healthCheckPath"], HEALTH)
        doc = (ROOT / "live_public" / "DEPLOY_RENDER.md").read_text(encoding="utf-8")
        self.assertIn("/healthz/live", doc)

    def test_the_platform_check_passes_with_the_blueprint_environment_in_every_gate_state(self):
        for gate in ("false", "true"):
            seen = {}
            async def scenario(h):
                seen["r"] = Resp(await raw_exchange(h.srv.port, request_bytes(path=LIVE, host=FAKE_RENDER_HOST)))
            h = Harness(auth=[(500, {})] * 5)
            h.env = dict(render_env(LIVE_PUBLIC_SERVE_SNAPSHOT=gate), HOST="127.0.0.1")
            self.assertEqual(run_rt(h, scenario), 0)
            self.assertEqual((seen["r"].status, seen["r"].body), (200, ALIVE_BODY), gate)


if __name__ == "__main__":
    unittest.main()
