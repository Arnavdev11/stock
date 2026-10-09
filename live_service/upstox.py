"""
live_service/upstox.py - everything that talks to Upstox, behind small interfaces so the service can be tested with no network.

  http_get(url, headers, timeout) -> (status:int, body:dict|None)         real: requests (imported only when used)
  transport  connect(uri) / send(bytes) / recv() / close()                real: the `websockets` package (imported only when used)
  fetch_bytes(url, timeout) -> bytes                                      real: requests (the public instrument file; needs no token)

The Upstox token is passed in as an argument and used for exactly one thing: the Authorization header of the authorize request.
The wss address returned by authorize carries a one-time code: it is handed to transport.connect and nowhere else.
"""
import asyncio
import gzip
import json

from live.instruments import Instruments
from .safety import ConfigError

AUTH_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
MAX_WS_MESSAGE = 8 * 1024 * 1024


class TransportError(Exception):
    """A connection problem. Carries only safe facts: an HTTP status (handshake) and a close code - never an address."""

    def __init__(self, message, http=None, code=None):
        super().__init__(message)
        self.http = http
        self.code = code


class AuthResult:
    __slots__ = ("ok", "http", "uri", "error")

    def __init__(self, ok, http, uri, error):
        self.ok, self.http, self.uri, self.error = ok, http, uri, error

    def __repr__(self):                                    # the address is never printed, not even by accident
        return "AuthResult(ok=%r, http=%r, error=%r)" % (self.ok, self.http, self.error)


def parse_authorize(status, body, scrub):
    if status == 200 and isinstance(body, dict):
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        uri = data.get("authorized_redirect_uri") or data.get("authorizedRedirectUri")
        if isinstance(uri, str) and uri.startswith("wss://"):
            return AuthResult(True, status, uri, None)
        return AuthResult(False, status, None, "HTTP 200 but the reply held no wss address")
    errs = []
    if isinstance(body, dict) and isinstance(body.get("errors"), list):
        for e in body["errors"][:3]:
            if isinstance(e, dict):
                errs.append("%s: %s" % (e.get("errorCode") or e.get("error_code"), e.get("message")))
    return AuthResult(False, status, None, scrub("HTTP %s %s" % (status, "; ".join(errs))))


async def authorize(token, http_get, scrub, timeout=10):
    """Runs the (blocking) http_get in a worker thread. Never raises: a failure is an AuthResult with ok False."""
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    try:
        status, body = await asyncio.wait_for(asyncio.to_thread(http_get, AUTH_URL, headers, timeout), timeout + 5)
    except asyncio.TimeoutError:
        return AuthResult(False, None, None, "authorize request timed out")
    except Exception as e:
        return AuthResult(False, None, None, scrub("authorize request failed: " + type(e).__name__))
    return parse_authorize(status, body, scrub)


def requests_http_get(url, headers, timeout):
    import requests
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def requests_fetch_bytes(url, timeout):
    import requests
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def parse_instrument_bytes(raw):
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ConfigError("the instrument file is not a list")
    return rows


def load_instrument_rows(path=None, fetch_bytes=requests_fetch_bytes, timeout=90):
    """From a local file when given, else the public Upstox instrument file (no token involved)."""
    if path:
        with open(path, "rb") as f:
            return parse_instrument_bytes(f.read())
    return parse_instrument_bytes(fetch_bytes(INSTRUMENTS_URL, timeout))


def build_instruments(rows, symbols, index_names=(("NIFTY 50", ("nifty 50",)),)):
    """Only the requested symbols and indices. Anything that cannot be resolved to exactly one instrument is a configuration error (a test run must not
    silently run on fewer instruments than asked for)."""
    full = Instruments.from_rows(rows, [(label, list(names)) for label, names in index_names])
    by_symbol = {}
    for k, s in full.equities.items():
        by_symbol[s] = k
    want = [s.strip().upper() for s in symbols if s and s.strip()]
    missing = [s for s in want if s not in by_symbol]
    if missing or full.unresolved:
        parts = []
        if missing:
            parts.append("symbols not found or ambiguous: " + ", ".join(missing))
        if full.unresolved:
            parts.append("indices not found or ambiguous: " + ", ".join(sorted(full.unresolved)))
        raise ConfigError("; ".join(parts))
    if not want:
        raise ConfigError("no symbols requested")
    eq = {by_symbol[s]: s for s in want}
    return Instruments(eq, dict(full.indices), {})


class WebsocketsTransport:
    """The real WebSocket, via the `websockets` package (pip install websockets). Same arguments as the connectivity test that already succeeded."""

    def __init__(self, scrub):
        self.scrub = scrub
        self._ws = None

    async def connect(self, uri, timeout=20):
        try:
            import ssl
            import websockets
        except ImportError:
            raise TransportError("the websockets package is not installed (pip install websockets)")
        try:
            self._ws = await websockets.connect(uri, ssl=ssl.create_default_context(), max_size=MAX_WS_MESSAGE, open_timeout=timeout,
                                                ping_interval=20, ping_timeout=20)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "status_code", None)
            raise TransportError(self.scrub("handshake failed: %s%s" % (type(e).__name__, " (HTTP %s)" % code if code else "")), http=code)

    async def send(self, data):
        try:
            await self._ws.send(data)
        except Exception as e:
            raise TransportError(self.scrub("send failed: " + type(e).__name__))

    async def recv(self):
        try:
            return await self._ws.recv()
        except Exception as e:
            rcvd = getattr(e, "rcvd", None)
            raise TransportError(self.scrub("connection closed: %s code=%s reason=%s" % (type(e).__name__, getattr(rcvd, "code", None), getattr(rcvd, "reason", None))),
                                 code=getattr(rcvd, "code", None))

    async def close(self):
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
