"""
live_public/config.py - the hosted service's configuration. Environment variables ONLY; nothing is read from a file and nothing is written.

    UPSTOX_ANALYTICS_TOKEN        required. Held in memory only; never in repr(), describe(), a log line or an error message.
    ALLOWED_HOSTS     required unless the platform provides RENDER_EXTERNAL_HOSTNAME (Render sets it to the service's onrender.com name, which is also the Host its health
                      check sends). Comma list of the service's own public host names (no port, no wildcard, no IP). A request for any other Host is refused.
                      When ALLOWED_HOSTS is set it is used alone: list the onrender.com name AND any custom domain.
    ALLOWED_ORIGIN    optional. Exact browser origin(s), default https://arnavdev11.github.io. https only, no port, no path, no wildcard, no "null".
    LIVE_PUBLIC_SERVE_SNAPSHOT    optional. Exactly "true" to serve /v1/live/snapshot.json; anything else (default "false") keeps it switched off.
    LIVE_PUBLIC_TRUST_PROXY       optional. Exactly "true" only when a trusted proxy sets X-Forwarded-For (default "false").
    HOST (default 0.0.0.0), PORT (default 8080; hosting platforms set it)
    LIVE_PUBLIC_RATE_PER_MINUTE, LIVE_PUBLIC_BURST, LIVE_PUBLIC_MAX_CONNECTIONS, LIVE_PUBLIC_STALE_AFTER_S   optional integers within fixed bounds.

The instruments are fixed in this phase (NIFTY 50 plus five equities, the relay's tested default set); no variable can widen them.
A bad value raises ConfigError naming the VARIABLE and the rule, never the value: a secret pasted into the wrong variable is not echoed.
"""
import ipaddress
import os
import re

from live_service.safety import ConfigError

TOKEN_ENV = "UPSTOX_ANALYTICS_TOKEN"
DEFAULT_ORIGIN = "https://arnavdev11.github.io"
DEFAULT_SYMBOLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC")        # the relay's tested set; fixed here
MAX_ORIGINS = 3
MAX_HOSTS = 5

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_NAME_RE = re.compile(r"^(?=.{1,253}$)%s(\.%s)+$" % (_LABEL, _LABEL))
_ORIGIN_RE = re.compile(r"^https://(?P<host>[a-z0-9.-]+)$")
_INT_RE = re.compile(r"^[0-9]{1,6}$")
_TOKEN_RE = re.compile(r"^[\x21-\x7e]{8,4096}$")                        # printable ASCII, no space, no control character

_INT_RULES = {                                                           # variable: (default, minimum, maximum)
    "PORT": (8080, 1, 65535),
    "LIVE_PUBLIC_RATE_PER_MINUTE": (60, 1, 600),
    "LIVE_PUBLIC_BURST": (20, 1, 200),
    "LIVE_PUBLIC_MAX_CONNECTIONS": (100, 1, 1000),
    "LIVE_PUBLIC_STALE_AFTER_S": (30, 5, 300),
}


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _int(env, name):
    default, lo, hi = _INT_RULES[name]
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or not _INT_RE.match(raw) or not lo <= int(raw) <= hi:
        raise ConfigError("%s must be a whole number from %d to %d" % (name, lo, hi))
    return int(raw)


def _flag(env, name):
    raw = env.get(name)
    if raw is None or raw == "":
        return False
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ConfigError('%s must be exactly "true" or "false"' % name)


def _lookalike(host):
    """True for hosts that imitate the StockLens origin: its name embedded in a longer host, punycode (homoglyph) labels, or a host nested under github.io."""
    default = DEFAULT_ORIGIN[len("https://"):]
    if host == default:
        return False
    return default in host or any(l.startswith("xn--") for l in host.split(".")) or host.endswith(".github.io") and host.count(".") != 2


def parse_origins(text):
    """'https://a.example,https://b.example' -> ('https://a.example', 'https://b.example'). Exact origins only: https, a real DNS name, no port, path, query, user, wildcard, 'null' or upper case."""
    parts = [p for p in (text or "").split(",")]
    if not parts or any(p == "" or p != p.strip() for p in parts):
        raise ConfigError("ALLOWED_ORIGIN must be a comma list of exact https origins with no empty or padded items")
    out = []
    for p in parts:
        m = _ORIGIN_RE.match(p)
        if not m or not _NAME_RE.match(m.group("host")) or _is_ip(m.group("host")):
            raise ConfigError("ALLOWED_ORIGIN items must be exact https origins such as https://example.github.io (no port, path, wildcard, IP address or upper case)")
        if _lookalike(m.group("host")):
            raise ConfigError("ALLOWED_ORIGIN item looks like a lookalike of the StockLens origin (embedded name, punycode or nested github.io host)")
        out.append(p)
    out = tuple(dict.fromkeys(out))
    if len(out) > MAX_ORIGINS:
        raise ConfigError("ALLOWED_ORIGIN allows at most %d origins" % MAX_ORIGINS)
    return out


def parse_hosts(text):
    """The service's own public host names (as they appear in the Host header, without a port)."""
    parts = [p for p in (text or "").split(",")]
    if not text or any(p == "" or p != p.strip() for p in parts):
        raise ConfigError("ALLOWED_HOSTS is required (or RENDER_EXTERNAL_HOSTNAME from the host): a comma list of host names with no empty or padded items")
    out = []
    for p in parts:
        if not _NAME_RE.match(p) or _is_ip(p):
            raise ConfigError("ALLOWED_HOSTS items must be plain DNS host names (no port, wildcard, IP address or upper case)")
        out.append(p)
    out = tuple(dict.fromkeys(out))
    if len(out) > MAX_HOSTS:
        raise ConfigError("ALLOWED_HOSTS allows at most %d host names" % MAX_HOSTS)
    return out


def _bind_host(env):
    raw = env.get("HOST")
    if raw is None or raw == "":
        return "0.0.0.0"
    if not isinstance(raw, str) or not _is_ip(raw):
        raise ConfigError("HOST must be an IP address such as 0.0.0.0")
    return raw


def _token(env):
    raw = env.get(TOKEN_ENV)
    if not isinstance(raw, str) or not _TOKEN_RE.match(raw) or "://" in raw:
        raise ConfigError("%s is required (printable text, 8 to 4096 characters, no spaces) and must come from the hosting environment" % TOKEN_ENV)
    return raw


class Config:
    """Validated settings. The token is held name-mangled and is reachable only through token(); repr(), str() and describe() never contain it."""

    def __init__(self, *, token, bind_host, port, origins, hosts, serve_snapshot, trust_proxy, rate_per_minute, burst, max_connections, stale_after_s):
        self.__token = token
        self.bind_host, self.port = bind_host, port
        self.origins, self.hosts = origins, hosts
        self.symbols = DEFAULT_SYMBOLS
        self.serve_snapshot, self.trust_proxy = serve_snapshot, trust_proxy
        self.rate_per_minute, self.burst = rate_per_minute, burst
        self.max_connections, self.stale_after_s = max_connections, stale_after_s

    def token(self):
        return self.__token

    def describe(self):
        """Everything that may be logged at start-up. No token, no secret-bearing value."""
        return {"bind": "%s:%d" % (self.bind_host, self.port), "origins": list(self.origins), "hosts": list(self.hosts), "symbols": list(self.symbols),
                "serve_snapshot": self.serve_snapshot, "trust_proxy": self.trust_proxy, "rate_per_minute": self.rate_per_minute, "burst": self.burst,
                "max_connections": self.max_connections, "stale_after_s": self.stale_after_s}

    def __repr__(self):
        return "<live_public.Config %s>" % self.describe()

    __str__ = __repr__


def load(env=None):
    """Build a Config from `env` (default: the process environment). Fails closed: a missing or malformed required value raises ConfigError."""
    env = os.environ if env is None else env
    origin_raw = env.get("ALLOWED_ORIGIN")
    return Config(
        token=_token(env),
        bind_host=_bind_host(env),
        port=_int(env, "PORT"),
        origins=parse_origins(origin_raw) if origin_raw not in (None, "") else (DEFAULT_ORIGIN,),
        hosts=parse_hosts(env.get("ALLOWED_HOSTS") if env.get("ALLOWED_HOSTS") not in (None, "") else env.get("RENDER_EXTERNAL_HOSTNAME")),
        serve_snapshot=_flag(env, "LIVE_PUBLIC_SERVE_SNAPSHOT"),
        trust_proxy=_flag(env, "LIVE_PUBLIC_TRUST_PROXY"),
        rate_per_minute=_int(env, "LIVE_PUBLIC_RATE_PER_MINUTE"),
        burst=_int(env, "LIVE_PUBLIC_BURST"),
        max_connections=_int(env, "LIVE_PUBLIC_MAX_CONNECTIONS"),
        stale_after_s=_int(env, "LIVE_PUBLIC_STALE_AFTER_S"),
    )
