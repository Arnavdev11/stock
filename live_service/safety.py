"""
live_service/safety.py - keeping secrets out of everything that leaves the process, and keeping the service on this machine.
"""
import collections
import datetime as dt
import ipaddress
import re
import sys

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class ConfigError(Exception):
    pass


class Scrubber:
    """Callable: text -> text with every registered secret and every pattern that looks like one removed. Applied to EVERY log line and every error text."""
    PATTERNS = [
        (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
        (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}(\.[A-Za-z0-9_-]+){1,2}"), "***jwt***"),
        (re.compile(r"(?i)\b(code|token|access_token|authorization)=[^&\s'\"]+"), r"\1=***"),
        (re.compile(r"(wss?://[^/\s'\"]+)/[^\s'\"]*"), r"\1/***"),
        (re.compile(r"(?i)(\"?(?:authorized_redirect_uri|authorizedRedirectUri|access_token)\"?\s*[:=]\s*)\"?[^\s,}\"']+\"?"), r"\1***"),
    ]

    def __init__(self):
        self._secrets = set()

    def add(self, secret):
        if isinstance(secret, str) and len(secret) >= 4:
            self._secrets.add(secret)

    def __call__(self, text, limit=400):
        s = str(text)
        for sec in sorted(self._secrets, key=len, reverse=True):
            s = s.replace(sec, "***")
        for rx, rep in self.PATTERNS:
            s = rx.sub(rep, s)
        return s[:limit]

    def __repr__(self):
        return "<Scrubber>"


class Log:
    """Timestamped lines to a stream (stderr), always scrubbed, with a short in-memory tail the health endpoint can show. Never writes a file."""

    def __init__(self, scrub, stream=None, keep=40, clock_ms=None):
        self.scrub = scrub
        self.stream = stream if stream is not None else sys.stderr
        self.tail = collections.deque(maxlen=keep)
        self.clock_ms = clock_ms
        self.quiet = False

    def _line(self, level, msg):
        import time
        ms = self.clock_ms() if self.clock_ms else int(time.time() * 1000)
        stamp = dt.datetime.fromtimestamp(ms / 1000, IST).strftime("%H:%M:%S")
        line = "%s %-5s %s" % (stamp, level, self.scrub(msg))
        self.tail.append(line)
        if not self.quiet:
            try:
                print(line, file=self.stream, flush=True)
            except Exception:
                pass
        return line

    def info(self, msg):
        return self._line("INFO", msg)

    def warn(self, msg):
        return self._line("WARN", msg)

    def error(self, msg):
        return self._line("ERROR", msg)


def is_loopback_host(host):
    """True only for 'localhost' or a literal loopback IP. Anything else (the wildcard address, a LAN address, a name) is refused: the service is local-only."""
    if not isinstance(host, str):
        return False
    h = host.strip()
    if h.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(h.strip("[]")).is_loopback
    except ValueError:
        return False
