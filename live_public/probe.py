"""
live_public/probe.py - a PRIVATE, time-limited connection test of the Upstox feed, for the hosted service. It answers one question with evidence: does Upstox authorize this token,
accept the WebSocket and send fresh data for the fixed six instruments?

    LIVE_PUBLIC_PROBE_SECONDS=30..900  (default 0 = off; refused together with LIVE_PUBLIC_SERVE_SNAPSHOT=true)

While it runs, the unchanged RelayService does the real work (authorize, ONE WebSocket, subscribe, ticks). Nothing is served: the public snapshot stays "disabled" because
LIVE_PUBLIC_SERVE_SNAPSHOT stays "false", so /v1/live/snapshot.json answers 503 snapshot_disabled throughout. The evidence goes to the service LOG only, through the Scrubber,
as counters, states and ages - never a token, a connection address, a raw frame or a price. When the time is up the relay is stopped, one "PROBE RESULT" line is written and the
service keeps answering (disabled) until it is stopped.

Stages in the evidence, each proven by the relay's own counters rather than by the existence of the token:
    authorized   Upstox answered the authorize call with success (auth_ok >= 1, last HTTP status)          rejected token -> auth_failed, HTTP 401/403
    connected    the single-use WebSocket address was accepted (connects >= 1)
    streaming    the subscribe was sent and frames arrive (frames >= 1)
    fresh        the NIFTY 50 index tick AND at least one equity tick were accepted by the tick store's checks, were received within `fresh_ms`, and the index tick's own
                 exchange timestamp (ltt) is within LTT_FRESH_MS of now
"""
import asyncio
import json

from live import state as live_state

SAMPLE_S = 10                       # one evidence line this often
LTT_FRESH_MS = 60_000               # the exchange timestamp of the index tick must be this recent
STOP_GRACE_S = 10.0


def _ages(svc, now):
    """-> {kind: {"n": accepted instruments, "recv_age_ms": freshest receive age, "ltt_age_ms": exchange-timestamp age of the freshest tick, "today": traded today}}"""
    out = {"index": {"n": 0, "recv_age_ms": None, "ltt_age_ms": None, "today": False}, "equity": {"n": 0, "recv_age_ms": None, "ltt_age_ms": None, "today": False}}
    for rec in list(svc.store.ticks.values()):
        o = out.get(rec.get("kind"))
        if o is None:
            continue
        o["n"] += 1
        age = max(0, now - rec["recv_ms"])
        if o["recv_age_ms"] is None or age < o["recv_age_ms"]:
            o["recv_age_ms"] = age
            o["ltt_age_ms"] = None if rec.get("ltt") is None else max(0, now - rec["ltt"])
        o["today"] = o["today"] or rec.get("traded_today") is True
    return out


def evidence(svc):
    """A whitelisted description of what the relay has proven so far. Numbers, booleans and fixed words only."""
    now = svc.clock()
    c = svc.c
    ages = _ages(svc, now)
    return {
        "state": svc.sup.state,
        "market": svc.market_status(now),
        "auth_ok": c["auth_ok"], "auth_failed": c["auth_failed"], "auth_http": svc.last_auth_http,
        "connects": c["connects"], "connect_failed": c["connect_failed"],
        "frames": c["frames"], "decode_errors": c["decode_errors"], "text_frames": c["text_frames"],
        "accepted_ticks": svc.store.accepted, "rejected": dict(svc.store.rejected),
        "index": ages["index"], "equity": ages["equity"],
        "expected": {"index": len(svc.ins.indices), "equity": len(svc.ins.equities)},
        "last_error": None if svc.last_error is None else svc.scrub(svc.last_error, 80),
    }


def verdict(ev, fresh_ms):
    """-> (code, reason). code is PASS, PARTIAL or FAIL. PASS needs fresh data; PARTIAL is only used when the market is not open, where no fresh data can be expected."""
    if ev["auth_ok"] < 1:
        if ev["auth_failed"] >= 1:
            return "FAIL", "Upstox did not authorize the token (HTTP %s, state %s)" % (ev["auth_http"], ev["state"])
        return "FAIL", "no authorization was attempted (state %s)" % ev["state"]
    if ev["connects"] < 1:
        return "FAIL", "authorized, but the WebSocket was never connected (%d failed attempts)" % ev["connect_failed"]
    idx, eq = ev["index"], ev["equity"]
    fresh_idx = idx["n"] >= 1 and idx["recv_age_ms"] is not None and idx["recv_age_ms"] <= fresh_ms and idx["ltt_age_ms"] is not None and idx["ltt_age_ms"] <= LTT_FRESH_MS and idx["today"]
    fresh_eq = eq["n"] >= 1 and eq["recv_age_ms"] is not None and eq["recv_age_ms"] <= fresh_ms and eq["today"]
    if fresh_idx and fresh_eq and ev["state"] == live_state.STREAMING:
        return "PASS", "authorized, connected, streaming; fresh NIFTY 50 tick (exchange time %d s old) and %d equity instrument(s) ticking" % (idx["ltt_age_ms"] // 1000, eq["n"])
    if ev["market"] != "open":
        return "PARTIAL", "authorized and connected (frames %d); fresh data cannot be expected while the market is %s" % (ev["frames"], ev["market"])
    if ev["frames"] < 1:
        return "FAIL", "authorized and connected, but no frame arrived"
    return "FAIL", "market open but no fresh data (state %s, index ticks %d, equity ticks %d)" % (ev["state"], idx["n"], eq["n"])


async def run(svc, log, stop, seconds, fresh_ms, *, sample_s=SAMPLE_S, grace_s=STOP_GRACE_S):
    """Start the relay, sample its evidence for `seconds` of the relay's own clock (or until `stop`), stop the relay, write the verdict. Returns the verdict code."""
    rstop = asyncio.Event()
    rtask = asyncio.ensure_future(svc.run(rstop))
    log.info("PROBE start: %d s, private (nothing is served); evidence below is counters, states and ages only" % seconds)
    t0 = svc.clock()
    last = t0 - sample_s * 1000
    try:
        while not stop.is_set() and svc.clock() - t0 < seconds * 1000 and not rtask.done():
            if svc.clock() - last >= sample_s * 1000:
                last = svc.clock()
                log.info("PROBE evidence " + json.dumps(evidence(svc), sort_keys=True, separators=(",", ":")))
            await svc.sleep(1.0)
    finally:
        rstop.set()
        try:
            await asyncio.wait_for(asyncio.shield(rtask), grace_s)
        except asyncio.TimeoutError:
            rtask.cancel()
            await asyncio.gather(rtask, return_exceptions=True)
        except (asyncio.CancelledError, Exception):
            await asyncio.gather(rtask, return_exceptions=True)
    ev = evidence(svc)                                           # the relay is stopped now: counters are final, ages are measured at the stop
    code, reason = verdict(ev, fresh_ms)
    log.info("PROBE evidence final " + json.dumps(ev, sort_keys=True, separators=(",", ":")))
    (log.error if code == "FAIL" else log.info)("PROBE RESULT: %s - %s" % (code, reason))
    return code
