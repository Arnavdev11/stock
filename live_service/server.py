"""
live_service/server.py - the two local endpoints, on 127.0.0.1 only:

    GET /v1/live/snapshot.json   the latest validated snapshot (ETag, gzip when accepted, 304 on If-None-Match); 503 until the first one exists
    GET /healthz                 relay health as JSON; HTTP 200 when ok, 503 when degraded or stopped

Hand-written minimal HTTP/1.1 on asyncio streams (no extra dependency). GET and HEAD only. Refuses any peer that is not loopback and any Host header that
is not a loopback name (a web page on the internet cannot reach it through DNS rebinding).

Every response carries a Date header (the relay's own clock), which the page uses to judge a snapshot's age.
CORS is OFF by default: no Access-Control header is ever sent and a request that carries an Origin header is refused (403). It is switched on only by naming exact
origins (--allow-origin), and only LOOPBACK origins are accepted (http://127.0.0.1:8000, http://localhost:8000, http://[::1]:8000): a public web page can never be
allowed to read the local relay. No wildcard, no "null", no pattern - an origin matches character for character or not at all.
"""
import asyncio
import email.utils
import json
import re

from .safety import ConfigError, is_loopback_host

MAX_HEAD = 8192
SNAPSHOT_PATH, HEALTH_PATH = "/v1/live/snapshot.json", "/healthz"


def _host_ok(value):
    h = (value or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):                                       # [::1]:8765
        name = h[1:].split("]")[0]
    else:
        name = h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    return is_loopback_host(name)


ORIGIN_RE = re.compile(r"^https?://(\[::1\]|[A-Za-z0-9.-]{1,253})(:[0-9]{1,5})?$")


def check_origins(origins):
    """-> tuple of exact origins. Anything that is not a plain loopback origin is a configuration error (no wildcard, no 'null', no path, no public host)."""
    out = []
    for o in origins or ():
        if not isinstance(o, str) or not ORIGIN_RE.match(o):
            raise ConfigError("--allow-origin must be an exact origin such as http://127.0.0.1:8000 (got %r)" % (o,))
        host = o.split("://", 1)[1]
        host = host[:host.index("]") + 1] if host.startswith("[") else host.split(":", 1)[0]
        if not is_loopback_host(host):
            raise ConfigError("--allow-origin must be a loopback origin: the local relay is never opened to a public page (got %r)" % (o,))
        out.append(o)
    return tuple(dict.fromkeys(out))


def _response(code, reason, body=b"", headers=None, head_only=False, extra=None):
    h = {"Content-Length": str(len(body)), "Connection": "close", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    h.update(extra or {})
    h.update(headers or {})
    lines = ["HTTP/1.1 %d %s" % (code, reason)] + ["%s: %s" % kv for kv in h.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + (b"" if head_only else body)


def _json(code, reason, obj, head_only=False, extra=None):
    return _response(code, reason, json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8"), {"Content-Type": "application/json; charset=utf-8"}, head_only, extra)


def route(service, method, target, headers, allow_origins=()):
    """-> response bytes. Pure apart from reading the service."""
    head_only = method == "HEAD"
    extra = {"Date": email.utils.formatdate(service.clock() / 1000.0, usegmt=True)}      # the relay's clock: the page measures a snapshot's age against it
    if method not in ("GET", "HEAD"):
        return _response(405, "Method Not Allowed", b"", {"Allow": "GET, HEAD"}, extra=extra)
    if not _host_ok(headers.get("host")):
        return _json(403, "Forbidden", {"error": "host_not_allowed"}, head_only, extra)
    origin = headers.get("origin")
    if origin is not None:                                      # a cross-origin request: only an exactly allowed origin gets an answer
        if origin not in allow_origins:
            return _json(403, "Forbidden", {"error": "origin_not_allowed"}, head_only, extra)
        extra.update({"Access-Control-Allow-Origin": origin, "Access-Control-Expose-Headers": "Date, ETag"})
    if allow_origins:
        extra["Vary"] = "Origin"
    path = target.split("?", 1)[0]
    if path == HEALTH_PATH:
        h = service.health()
        return _json(200 if h["status"] == "ok" else 503, "OK" if h["status"] == "ok" else "Service Unavailable", h, head_only, extra)
    if path == SNAPSHOT_PATH:
        pub = service.latest
        if pub is None:
            return _json(503, "Service Unavailable", {"error": "no_snapshot_yet"}, head_only, extra)
        vary = "Accept-Encoding, Origin" if allow_origins else "Accept-Encoding"
        base = {"ETag": pub.etag, "Content-Type": "application/json; charset=utf-8", "Vary": vary}
        inm = [x.strip() for x in headers.get("if-none-match", "").split(",")]
        if pub.etag in inm:
            return _response(304, "Not Modified", b"", {"ETag": pub.etag, "Vary": vary}, extra=extra)
        if "gzip" in headers.get("accept-encoding", "").lower():
            return _response(200, "OK", pub.gz, dict(base, **{"Content-Encoding": "gzip"}), head_only, extra)
        return _response(200, "OK", pub.raw, base, head_only, extra)
    return _json(404, "Not Found", {"error": "not_found"}, head_only, extra)


async def _handle(service, reader, writer, allow_origins=()):
    try:
        peer = writer.get_extra_info("peername")
        if not peer or not is_loopback_host(peer[0]):
            return
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            writer.write(_response(400, "Bad Request", b""))
            return
        if len(raw) > MAX_HEAD:
            writer.write(_response(431, "Request Header Fields Too Large", b""))
            return
        lines = raw.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/"):
            writer.write(_response(400, "Bad Request", b""))
            return
        headers = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        writer.write(route(service, parts[0], parts[1], headers, allow_origins))
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_server(service, host="127.0.0.1", port=8765, allow_origins=()):
    if not is_loopback_host(host):
        raise ConfigError("the service is local-only: host must be 127.0.0.1, ::1 or localhost (got %r)" % (host,))
    origins = check_origins(allow_origins)
    return await asyncio.start_server(lambda r, w: _handle(service, r, w, origins), host, port, limit=MAX_HEAD * 2)
