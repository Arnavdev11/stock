"""
live_public/server.py - the PUBLIC HTTP face of the hosted live backend (stage 2). It serves what a RelayService has already validated and published; it opens no
WebSocket, calls no Upstox endpoint and holds no token.

    GET|HEAD /healthz/live            {"status":"alive"}, HTTP 200 whenever this HTTP service is running - and nothing else. It never looks at the relay, the feed or the snapshot, and it
                                      is exempt from the Host, Origin and rate-limit checks (its body holds nothing, and a 4xx on it would make a platform restart a working process). It
                                      carries no CORS grant. This is the path a hosting platform's health check should use.
    GET|HEAD /healthz                 {"status": "ok"|"unavailable", "snapshot": "disabled"|"none"|"fresh"|"stale"|"invalid"}, HTTP 200 / 503. Nothing else.
    GET|HEAD /v1/live/snapshot.json   the latest snapshot, only when ALL of these hold; otherwise a short JSON error with HTTP 503 and never a snapshot:
                                        - LIVE_PUBLIC_SERVE_SNAPSHOT is exactly "true"        (error: snapshot_disabled)
                                        - the relay has published one                          (no_snapshot_yet)
                                        - it passes the relay's own validator, is from the fixed test-set universe, quality.ok is true  (invalid_snapshot / feed_unavailable)
                                        - its timestamp is consistent and not from the future  (invalid_snapshot)
                                        - it is not older than the stale-after threshold, measured on the relay clock  (stale_feed)

What is served is a PUBLIC VIEW of the relay's document, rebuilt here: breadth is zeroed, gainers and losers are null, and prices are cut to the configured symbols.
A six-instrument test set is therefore never published as breadth, movers or a market dashboard, whatever the relay computed.

Access control: exact Host allowlist, exact Origin allowlist (a request with no Origin is answered but is rate limited: CORS is a browser rule, not authentication),
GET/HEAD only, exact paths, no query string, no request body. Bounded header size and read time, a global connection cap, a per-address connection cap (when no proxy
is trusted) and a per-client token-bucket rate limit. X-Forwarded-For is read ONLY when the configuration trusts the proxy, and then only its LAST entry (the one the
trusted proxy appended). Every response is `Cache-Control: no-store` and carries the relay's `Date`, which the page compares with its own clock.

Hand-written minimal HTTP/1.1 on asyncio streams, like live_service/server.py, which is not imported or changed.
"""
import asyncio
import collections
import email.utils
import ipaddress
import json
import re

from live import snapshot

SNAPSHOT_PATH, HEALTH_PATH, LIVE_PATH = "/v1/live/snapshot.json", "/healthz", "/healthz/live"
MAX_HEAD = 8192
MAX_HEADERS = 40
READ_TIMEOUT_S = 5.0
WRITE_TIMEOUT_S = 5.0
MAX_CONN_PER_ADDRESS = 10
MAX_TRACKED_CLIENTS = 4096
SKEW_MS = 5000                                  # allowance for clock differences between the relay's snapshot stamp and its publish time
SOURCE_UNIVERSE = "test_set"

_TARGET_RE = re.compile(r"^/[A-Za-z0-9/_.-]{0,64}$")
_HOST_RE = re.compile(r"^(?P<name>[a-z0-9.-]{1,253})(:(?P<port>[0-9]{1,5}))?$")
_GZIP_RE = re.compile(r"(?:^|,)\s*(?:gzip|\*)\s*(?:;\s*q=(?P<q>[0-9.]+))?\s*(?:,|$)", re.I)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000",
    "Cache-Control": "no-store",
}
REASONS = {200: "OK", 304: "Not Modified", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 429: "Too Many Requests",
           431: "Request Header Fields Too Large", 503: "Service Unavailable"}


def accepts_gzip(value):
    for m in _GZIP_RE.finditer(value or ""):
        try:
            if m.group("q") is None or float(m.group("q")) > 0:
                return True
        except ValueError:
            continue
    return False


def host_name(value):
    """Host header -> lower-case name without a port, or None when it is not a plain DNS name (bracketed IPv6, junk, empty)."""
    m = _HOST_RE.match((value or "").strip().lower())
    if not m or (m.group("port") is not None and not 0 < int(m.group("port")) < 65536):
        return None
    return m.group("name")


class RateLimiter:
    """Token bucket per client key. Memory is bounded: at most MAX_TRACKED_CLIENTS keys; the oldest idle ones go first."""

    def __init__(self, per_minute, burst, clock, cap=MAX_TRACKED_CLIENTS):
        self.rate, self.burst, self.clock, self.cap = per_minute / 60.0, float(burst), clock, cap
        self.b = collections.OrderedDict()                 # key -> (tokens, at)

    def allow(self, key):
        now = self.clock()
        tokens, at = self.b.pop(key, (self.burst, now))
        tokens = min(self.burst, tokens + max(0.0, now - at) * self.rate)
        ok = tokens >= 1.0
        if ok:
            tokens -= 1.0
        self.b[key] = (tokens, now)                          # most recently used goes last
        while len(self.b) > self.cap:
            self.b.popitem(last=False)
        return ok

    def retry_after_s(self):
        return max(1, int(1.0 / self.rate) + 1) if self.rate > 0 else 60


def _client_key(peer_ip, headers, trust_proxy):
    """The address a request is counted against. Forwarded headers count only when the proxy is trusted by configuration, and only the last entry."""
    if trust_proxy:
        xff = headers.get("x-forwarded-for", "")
        last = xff.rsplit(",", 1)[-1].strip() if xff else ""
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            pass                                             # missing or malformed: fall back to the connection's own address
    return peer_ip or "unknown"


class PublicApp:
    """The request logic with no sockets: handle(method, target, headers, peer_ip) -> response bytes. `service` is a RelayService (anything with latest, health(), clock)."""

    def __init__(self, cfg, service, mono=None):
        import time
        self.cfg, self.service = cfg, service
        self.limiter = RateLimiter(cfg.rate_per_minute, cfg.burst, mono or time.monotonic)
        self._view = (None, None)                            # (published object, public view) - rebuilt only when the relay publishes a new one

    # ---------------------------------------------------------------- public view and freshness
    def _public_view(self, pub):
        if self._view[0] is pub:
            return self._view[1]
        view = None
        try:
            d = pub.doc
            if isinstance(d, dict) and snapshot.validate_snapshot(d) == [] and d["scope"]["universe"] == SOURCE_UNIVERSE:
                v = dict(d)
                v["breadth"] = {"advances": 0, "declines": 0, "unchanged": 0, "counted": 0}
                v["gainers"] = v["losers"] = None
                v["prices"] = {k: p for k, p in d["prices"].items() if k in self.cfg.symbols}
                if snapshot.validate_snapshot(v) == []:
                    raw, gz, etag = snapshot.encode_snapshot(v)
                    view = {"doc": v, "raw": raw, "gz": gz, "etag": etag, "ts": v["server_ts_ms"], "ok": bool(v["quality"]["ok"]), "at": pub.at_ms}
        except Exception:
            view = None
        self._view = (pub, view)
        return view

    def snapshot_state(self, now):
        """-> (state, view). state: disabled | none | invalid | unavailable | stale | fresh. Only "fresh" is ever served."""
        if not self.cfg.serve_snapshot:
            return "disabled", None
        pub = getattr(self.service, "latest", None)
        if pub is None:
            return "none", None
        view = self._public_view(pub)
        ts, at = (view or {}).get("ts"), (view or {}).get("at")
        if view is None or not isinstance(at, int) or isinstance(at, bool) or abs(at - ts) > SKEW_MS:
            return "invalid", None
        age = now - ts
        if age < -SKEW_MS:
            return "invalid", None                           # stamped in the future
        if age > self.cfg.stale_after_s * 1000:
            return "stale", None
        if not view["ok"]:
            return "unavailable", None                       # the relay itself says the feed is not healthy
        return "fresh", view

    # ---------------------------------------------------------------- responses
    def _date(self, now):
        return email.utils.formatdate(now / 1000.0, usegmt=True)

    def _build(self, code, body, headers, head_only, extra):
        h = dict(SECURITY_HEADERS)
        h.update({"Content-Length": str(len(body)), "Connection": "close"})
        h.update(extra)
        h.update(headers)
        lines = ["HTTP/1.1 %d %s" % (code, REASONS[code])] + ["%s: %s" % kv for kv in h.items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + (b"" if head_only or code == 304 else body)

    def _json(self, code, obj, head_only, extra, more=None):
        body = json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return self._build(code, body, dict({"Content-Type": "application/json; charset=utf-8"}, **(more or {})), head_only, extra)

    def _now(self):
        try:
            n = self.service.clock()
            return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None
        except Exception:
            return None

    # ---------------------------------------------------------------- the request
    def base_extra(self):
        return {"Date": self._date(self._now_or_wall())}

    def _now_or_wall(self):
        import time
        n = self._now()
        return n if n is not None else int(time.time() * 1000)

    def handle(self, method, target, headers, peer_ip):
        now = self._now_or_wall()                            # an unusable relay clock falls back to the wall clock (and snapshots are then judged against it)
        extra = {"Date": self._date(now)}
        head_only = method == "HEAD"
        try:
            return self._route(method, target, headers, peer_ip, now, extra, head_only)
        except Exception:
            return self._json(503, {"error": "unavailable"}, head_only, extra, {"Retry-After": "10"})   # never the exception text

    def _route(self, method, target, headers, peer_ip, now, extra, head_only):
        if method not in ("GET", "HEAD"):
            return self._json(405, {"error": "method_not_allowed"}, False, extra, {"Allow": "GET, HEAD"})
        if target == LIVE_PATH:                              # liveness only: no service access, no Host / Origin / rate-limit decision, no CORS grant
            return self._json(200, {"status": "alive"}, head_only, extra)
        if host_name(headers.get("host")) not in self.cfg.hosts:
            return self._json(403, {"error": "host_not_allowed"}, head_only, extra)
        origin = headers.get("origin")
        cors = {"Vary": "Origin"}
        if origin is not None:
            if origin not in self.cfg.origins:
                return self._json(403, {"error": "origin_not_allowed"}, head_only, extra, cors)
            cors.update({"Access-Control-Allow-Origin": origin, "Access-Control-Expose-Headers": "Date, ETag"})
        if not self.limiter.allow(_client_key(peer_ip, headers, self.cfg.trust_proxy)):
            return self._json(429, {"error": "rate_limited"}, head_only, extra, dict(cors, **{"Retry-After": str(self.limiter.retry_after_s())}))
        if "?" in target or "#" in target or not _TARGET_RE.match(target):
            return self._json(400, {"error": "bad_request"}, head_only, extra, cors)
        if target == HEALTH_PATH:
            st, _ = self.snapshot_state(now)
            ok = self._relay_ok() and st in ("disabled", "none", "fresh", "stale", "unavailable")
            return self._json(200 if ok else 503, {"status": "ok" if ok else "unavailable", "snapshot": st}, head_only, extra, cors)
        if target == SNAPSHOT_PATH:
            st, view = self.snapshot_state(now)
            if view is None:
                err = {"disabled": "snapshot_disabled", "none": "no_snapshot_yet", "invalid": "invalid_snapshot", "unavailable": "feed_unavailable", "stale": "stale_feed"}[st]
                return self._json(503, {"error": err}, head_only, extra, dict(cors, **{"Retry-After": "10"}))
            vary = "Accept-Encoding, Origin"
            if view["etag"] in [x.strip() for x in headers.get("if-none-match", "").split(",")]:
                return self._build(304, b"", dict(cors, **{"ETag": view["etag"], "Vary": vary}), False, extra)
            base = dict(cors, **{"ETag": view["etag"], "Content-Type": "application/json; charset=utf-8", "Vary": vary})
            if accepts_gzip(headers.get("accept-encoding")):
                return self._build(200, view["gz"], dict(base, **{"Content-Encoding": "gzip"}), head_only, extra)
            return self._build(200, view["raw"], base, head_only, extra)
        return self._json(404, {"error": "not_found"}, head_only, extra, cors)

    def _relay_ok(self):
        try:
            return self.service.health().get("status") == "ok"
        except Exception:
            return False


class PublicServer:
    """The sockets around PublicApp: header read timeout, size limits, connection caps, clean shutdown."""

    def __init__(self, cfg, service, mono=None):
        self.cfg, self.app = cfg, PublicApp(cfg, service, mono)
        self.server = None
        self.active = 0
        self.per_address = collections.Counter()
        self.writers = set()
        self.tasks = set()

    async def start(self, host=None, port=None):
        self.server = await asyncio.start_server(self._on_connect, host if host is not None else self.cfg.bind_host, self.cfg.port if port is None else port,
                                                 limit=MAX_HEAD * 2)
        return self.server

    @property
    def port(self):
        return self.server.sockets[0].getsockname()[1]

    async def _on_connect(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            await self._serve(reader, writer)
        finally:
            self.tasks.discard(task)

    async def _write(self, writer, data):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT_S)

    async def _serve(self, reader, writer):
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else None
        counted = False
        self.writers.add(writer)
        try:
            if self.active >= self.cfg.max_connections or (not self.cfg.trust_proxy and self.per_address[ip] >= MAX_CONN_PER_ADDRESS):
                await self._write(writer, self.app._json(503, {"error": "busy"}, False, self.app.base_extra(), {"Retry-After": "5"}))
                try:
                    await asyncio.wait_for(reader.read(MAX_HEAD), 0.25)      # take the request off the socket so closing does not reset the connection and lose the answer
                except Exception:
                    pass
                return
            self.active += 1
            self.per_address[ip] += 1
            counted = True
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT_S)
            except asyncio.LimitOverrunError:
                await self._write(writer, self.app._json(431, {"error": "headers_too_large"}, False, self.app.base_extra()))
                return
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
                try:
                    await self._write(writer, self.app._json(400, {"error": "bad_request"}, False, self.app.base_extra()))
                except Exception:
                    pass
                return
            await self._write(writer, self._respond(raw, ip))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass                                             # nothing about a failed connection is ever echoed to the peer
        finally:
            if counted:
                self.active -= 1
                self.per_address[ip] -= 1
                if self.per_address[ip] <= 0:
                    del self.per_address[ip]
            self.writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    def _respond(self, raw, ip):
        bad = lambda code, err: self.app._json(code, {"error": err}, False, self.app.base_extra())
        if len(raw) > MAX_HEAD:
            return bad(431, "headers_too_large")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return bad(400, "bad_request")
        lines = text.split("\r\n")[:-2]
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[2] not in ("HTTP/1.1", "HTTP/1.0") or len(lines) - 1 > MAX_HEADERS:
            return bad(400, "bad_request")
        headers = {}
        for ln in lines[1:]:
            name, sep, v = ln.partition(":")
            k = name.lower()
            if not sep or not name or name != name.strip() or k in headers and k in ("host", "origin", "content-length"):
                return bad(400, "bad_request")                   # folded lines, padded names and repeated security-relevant headers
            headers[k] = v.strip()
        if headers.get("transfer-encoding") or headers.get("content-length", "0") not in ("0", ""):
            return bad(400, "bad_request")                   # these endpoints take no body
        return self.app.handle(parts[0], parts[1], headers, ip)

    async def close(self):
        """Stop listening, drop every open connection and wait for the handlers: nothing is left running."""
        if self.server is not None:
            self.server.close()
        for w in list(self.writers):
            try:
                w.close()
            except Exception:
                pass
        for t in list(self.tasks):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()
            self.server = None
