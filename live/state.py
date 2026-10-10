"""
live/state.py - the relay's connection / recovery state machine. PURE: events go in, actions come out. It does no I/O, sleeps never, reads no clock
(the caller passes "now" in ms) and has its own random source injected, so every rule below is tested without a network.

States:   OFF_HOURS -> AUTHORIZING -> CONNECTING -> SUBSCRIBING -> STREAMING -> (BACKOFF -> AUTHORIZING ...)      AUTH_STOPPED (needs a person)
Events:   window_open  window_close  authorized  auth_failed(http)  connected  connect_failed(http)  subscribed  subscribe_failed  frame  closed(code)  tick(expect_frames)  reset
Actions:  ("authorize",)  ("connect",)  ("subscribe",)  ("disconnect",)  ("final_snapshot",)  ("alert", code, text)

Rules
  * every reconnect starts with a NEW authorization (the wss address is single-use)
  * backoff: min(60, 2**failures) seconds, with jitter between 50% and 100% of that; HTTP 429 waits at least 30 s
  * HTTP 401/403 from the authorization (or the handshake) twice in a row = AUTH_STOPPED: no more attempts, one alert; only "reset" (a new token) restarts it
  * watchdog: STREAMING with no frame for WATCHDOG_MS while frames are expected = drop the socket and reconnect
  * a connection that has streamed for STABLE_MS clears the failure counters
  * more than MAX_RECONNECTS_PER_HOUR reconnects in an hour = one "reconnect_storm" alert, and the wait is never shorter than 60 s
  * window_close at any time = disconnect and publish one final snapshot; the process then sits in OFF_HOURS until window_open
"""
import random

OFF_HOURS, AUTHORIZING, CONNECTING, SUBSCRIBING, STREAMING, BACKOFF, AUTH_STOPPED = (
    "off_hours", "authorizing", "connecting", "subscribing", "streaming", "backoff", "auth_stopped")
DEFAULTS = {"watchdog_ms": 15_000, "stable_ms": 60_000, "backoff_cap_s": 60, "rate_limit_wait_s": 30, "max_reconnects_per_hour": 30, "auth_failures_to_stop": 2}


class Supervisor:
    def __init__(self, cfg=None, rng=None):
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.rng = rng or random.random
        self.state = OFF_HOURS
        self.failures = 0                # consecutive failed attempts (reset by a stable stream)
        self.auth_failures = 0
        self.retry_at_ms = None
        self.last_frame_ms = None
        self.stream_since_ms = None
        self.reconnect_times = []        # ms of recent (re)authorizations after the first
        self.alerts_raised = set()
        self.total_connects = 0

    # ---- helpers
    def relay_state(self):
        """The word the snapshot builder expects: 'streaming' only while really streaming."""
        return STREAMING if self.state == STREAMING else self.state

    def backoff_seconds(self, http=None, storm=False):
        base = min(self.cfg["backoff_cap_s"], 2 ** min(self.failures, 10))
        wait = base * (0.5 + 0.5 * self.rng())
        if http == 429:
            wait = max(wait, self.cfg["rate_limit_wait_s"])
        if storm:
            wait = max(wait, 60)
        return wait

    def _alert(self, code, text):
        if code in self.alerts_raised:
            return []
        self.alerts_raised.add(code)
        return [("alert", code, text)]

    def _recent_reconnects(self, now_ms):
        self.reconnect_times = [t for t in self.reconnect_times if now_ms - t < 3_600_000]
        return len(self.reconnect_times)

    def _fail(self, now_ms, http=None, why=""):
        """A connection attempt or a live connection failed: schedule the next attempt."""
        self.failures += 1
        storm = self._recent_reconnects(now_ms) > self.cfg["max_reconnects_per_hour"]
        self.retry_at_ms = now_ms + int(self.backoff_seconds(http, storm) * 1000)
        self.state = BACKOFF
        acts = [("disconnect",)]
        if storm:
            acts += self._alert("reconnect_storm", "more than %d reconnects in the last hour" % self.cfg["max_reconnects_per_hour"])
        return acts

    def _auth_rejected(self, now_ms, http):
        self.auth_failures += 1
        if self.auth_failures >= self.cfg["auth_failures_to_stop"]:
            self.state = AUTH_STOPPED
            return [("disconnect",)] + self._alert("auth_rejected", "Upstox rejected the token (HTTP %s): the relay stopped retrying" % http)
        return self._fail(now_ms, http, "auth")

    # ---- the one entry point
    def handle(self, event, now_ms, **kw):
        s = self.state
        if event == "reset":
            self.__init__(self.cfg, self.rng)
            return []
        if event == "window_close":
            if s in (OFF_HOURS,):
                return []
            was_auth_stopped = s == AUTH_STOPPED
            self.state = AUTH_STOPPED if was_auth_stopped else OFF_HOURS
            self.retry_at_ms = None
            return [("disconnect",), ("final_snapshot",)]
        if event == "window_open":
            if s == OFF_HOURS:
                self.state = AUTHORIZING
                return [("authorize",)]
            return []
        if s == AUTH_STOPPED or s == OFF_HOURS:
            return []                                    # nothing else matters until a person resets, or the window opens
        if event == "tick":
            if s == BACKOFF and self.retry_at_ms is not None and now_ms >= self.retry_at_ms:
                self.state = AUTHORIZING
                self.reconnect_times.append(now_ms)
                return [("authorize",)]
            if s == STREAMING and kw.get("expect_frames") and self.last_frame_ms is not None and now_ms - self.last_frame_ms > self.cfg["watchdog_ms"]:
                return self._fail(now_ms, None, "watchdog")
            return []
        if event == "authorized" and s == AUTHORIZING:
            self.state = CONNECTING
            return [("connect",)]
        if event == "auth_failed" and s == AUTHORIZING:
            http = kw.get("http")
            return self._auth_rejected(now_ms, http) if http in (401, 403) else self._fail(now_ms, http)
        if event == "connected" and s == CONNECTING:
            self.state = SUBSCRIBING
            self.total_connects += 1
            return [("subscribe",)]
        if event == "connect_failed" and s == CONNECTING:
            http = kw.get("http")
            return self._auth_rejected(now_ms, http) if http in (401, 403) else self._fail(now_ms, http)
        if event == "subscribed" and s == SUBSCRIBING:
            self.state = STREAMING
            self.stream_since_ms = now_ms
            self.last_frame_ms = now_ms                  # the watchdog counts from the moment we started listening
            return []
        if event == "subscribe_failed" and s == SUBSCRIBING:
            return self._fail(now_ms, kw.get("http"))
        if event == "frame" and s in (STREAMING, SUBSCRIBING):
            self.last_frame_ms = now_ms
            if s == STREAMING and self.stream_since_ms is not None and now_ms - self.stream_since_ms >= self.cfg["stable_ms"] and (self.failures or self.auth_failures):
                self.failures = self.auth_failures = 0
                self.alerts_raised.discard("reconnect_storm")
            return []
        if event == "closed" and s in (CONNECTING, SUBSCRIBING, STREAMING):
            return self._fail(now_ms, kw.get("http"))
        return []
