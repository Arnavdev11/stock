"""
python -m live_public  - the HOSTED live backend (stage 3). One process, one event loop:

    1. configuration from the environment ONLY (live_public/config.py) - a bad or missing value stops the start with a message that names the variable, never its value
    2. the relay: live_service.relay.RelayService, unchanged, with the established Upstox integration (authorize, then ONE WebSocket V3 connection in ltpc mode)
    3. the public HTTP server: live_public/server.py, bound to HOST:PORT from the configuration

Nothing is re-implemented here: this file only builds the objects, runs them side by side and stops them in order. There is exactly one RelayService, so exactly one WebSocket.

The snapshot gate is preserved: unless LIVE_PUBLIC_SERVE_SNAPSHOT is exactly "true" the relay is NOT started at all (no authorize call, no connection, no token use) and the server
answers /healthz and a 503 snapshot_disabled. The token is held in memory and registered with the Scrubber, which every log line passes through; it is never printed.

Stops on SIGINT / SIGTERM (or when `stop` is set): the listener closes first, open connections are dropped, then the relay is stopped and disconnects.
Exit codes: 0 clean stop, 2 configuration error, 1 anything else (start-up failure or the relay task ending on its own).
"""
import asyncio
import os
import signal
import sys

from live import instruments as ins_mod
from live import market_hours
from live_service import relay, upstox
from live_service.safety import ConfigError, Log, Scrubber

from . import config as cfgmod
from .server import PublicServer

HOLIDAYS_FILE = os.path.join("data", "holidays.txt")          # optional, same format as the local relay's; without it only weekends count as closed
LIQUID_FILE = os.path.join("out", "market_dma.json")          # optional; the public view never carries movers, so its absence changes nothing that is served
SHUTDOWN_GRACE_S = 10.0


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def build(cfg, *, http_get=None, transport_factory=None, fetch_bytes=None, clock_ms=None, sleep=None, stream=None, holidays_file=HOLIDAYS_FILE, liquid_file=LIQUID_FILE):
    """-> (RelayService, Log, Scrubber). With the gate on, the only network access here is the public instrument file (no token); with the gate off there is none. The token goes to the relay and nowhere else."""
    scrub = Scrubber()
    scrub.add(cfg.token())
    log = Log(scrub, stream=stream, clock_ms=clock_ms)
    if cfg.serve_snapshot:
        rows = upstox.load_instrument_rows(None, fetch_bytes or upstox.requests_fetch_bytes)
        ins = upstox.build_instruments(rows, list(cfg.symbols))
        del rows
    else:                                                         # gate off: the relay never runs and nothing is served, so the (large) instrument file is neither downloaded nor parsed.
        ins = ins_mod.Instruments({}, {}, {})                     # An empty universe only lets the idle RelayService and /healthz be constructed; it is never subscribed.
    htext = _read_text(holidays_file)
    holidays = market_hours.parse_holidays(htext) if htext is not None else None
    liquid = ins_mod.load_liquid(liquid_file) if os.path.exists(liquid_file) else set()
    rcfg = relay.Config(list(cfg.symbols), snapshot_every_s=2.0, holidays=holidays, liquid=liquid, universe="test_set")
    svc = relay.RelayService(ins, rcfg, cfg.token(), http_get=http_get or upstox.requests_http_get,
                             transport_factory=transport_factory or (lambda: upstox.WebsocketsTransport(scrub)), log=log, scrub=scrub, clock_ms=clock_ms, sleep=sleep)
    if holidays is None:
        log.warn("no holiday file: only weekends are treated as closed")
    return svc, log, scrub


async def amain(env=None, *, stop=None, on_ready=None, install_signals=False, port=None, grace_s=SHUTDOWN_GRACE_S, **deps):
    """Run until `stop` is set (or a signal). `deps` are the relay's injectable dependencies (http_get, transport_factory, fetch_bytes, clock_ms, sleep, stream); `on_ready(server, service, log)`
    is called once everything is listening. Returns the exit code."""
    cfg = cfgmod.load(env)                                        # ConfigError here: nothing has been opened yet
    svc, log, scrub = build(cfg, **deps)
    stop = stop if stop is not None else asyncio.Event()
    srv = PublicServer(cfg, svc)
    await srv.start(port=port)                                    # binds the configured HOST and PORT; an OSError here propagates before the relay has started
    relay_task = None
    waiter = None
    code = 0
    try:
        log.info("live_public %s listening on %s:%d" % (relay.VERSION, cfg.bind_host, srv.port))
        log.info("configuration: " + repr(cfg.describe()))
        if install_signals:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop.set)
                except (NotImplementedError, RuntimeError):
                    pass
        if cfg.serve_snapshot:
            log.info("snapshot serving is ON: starting the relay (%d instruments, one WebSocket)" % (len(svc.ins.indices) + len(svc.ins.equities)))
            relay_task = asyncio.ensure_future(svc.run(stop))
        else:
            log.warn("snapshot serving is OFF (LIVE_PUBLIC_SERVE_SNAPSHOT is not \"true\"): the relay is not started and no Upstox connection is made")
        if on_ready is not None:
            on_ready(srv, svc, log)
        waiter = asyncio.ensure_future(stop.wait())
        watch = {waiter} if relay_task is None else {waiter, relay_task}
        done, _ = await asyncio.wait(watch, return_when=asyncio.FIRST_COMPLETED)
        if relay_task is not None and relay_task in done and not stop.is_set():
            code = 1
            err = relay_task.exception() if not relay_task.cancelled() else None
            log.error("the relay stopped on its own%s: shutting down" % (" (" + type(err).__name__ + ")" if err else ""))
    finally:
        stop.set()
        if waiter is not None:
            waiter.cancel()
        await srv.close()                                         # stop answering first: nobody is told the feed is fine while it is being torn down
        if relay_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(relay_task), grace_s)
            except asyncio.TimeoutError:
                relay_task.cancel()
                await asyncio.gather(relay_task, return_exceptions=True)
            except (asyncio.CancelledError, Exception):
                await asyncio.gather(relay_task, return_exceptions=True)
        log.info("stopped")
    return code


def main(argv=None, env=None, **deps):
    if argv:
        print("error: python -m live_public takes no arguments; it is configured by environment variables only (see live_public/config.py).", file=sys.stderr)
        return 2
    env = os.environ if env is None else env
    scrub = Scrubber()
    scrub.add(env.get(cfgmod.TOKEN_ENV, ""))
    try:
        return asyncio.run(amain(env, install_signals=True, **deps))
    except KeyboardInterrupt:
        return 0
    except ConfigError as e:
        print("error: " + scrub(e), file=sys.stderr)
        return 2
    except OSError as e:
        print("error: cannot start the server: %s" % type(e).__name__, file=sys.stderr)
        return 1
    except Exception as e:
        print("error: %s: %s" % (type(e).__name__, scrub(e, 200)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
