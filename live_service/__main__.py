"""
python -m live_service  - start the LOCAL relay (Phase 2). Local only: it listens on 127.0.0.1 and refuses anything else.

    python -m live_service                           the six-instrument test set, respecting market hours
    python -m live_service --ignore-window           connect now even outside 08:55-15:45 (connectivity check; the snapshot will say the market is closed)
    python -m live_service --duration 120 --summary  run two minutes, print one summary, exit
    python -m live_service --drop-after 60           also drop the socket once after 60 s of streaming, to watch the re-authorize + reconnect

The token is read from the environment variable UPSTOX_ANALYTICS_TOKEN, only here, and only held in memory. It is never printed, written or served.
"""
import argparse
import asyncio
import json
import os
import sys

from live import instruments as ins_mod
from live import market_hours
from . import relay, server, upstox
from .safety import ConfigError, Log, Scrubber, is_loopback_host

TOKEN_ENV = "UPSTOX_ANALYTICS_TOKEN"
DEFAULT_SYMBOLS = "RELIANCE,TCS,INFY,HDFCBANK,ITC"            # the initial private test set: NIFTY 50 (index) plus these five equities
DEFAULT_HOLIDAYS = os.path.join("data", "holidays.txt")
DEFAULT_LIQUID = os.path.join("out", "market_dma.json")


def parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m live_service", description="Local-only StockLens live relay (Phase 2).")
    p.add_argument("--host", default="127.0.0.1", help="must be a loopback address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS, help="comma-separated NSE equity symbols (default: %(default)s)")
    p.add_argument("--instruments-file", help="a local copy of the Upstox NSE instrument file (.json or .json.gz); default: download the public file")
    p.add_argument("--holidays-file", default=DEFAULT_HOLIDAYS, help="whitespace-separated YYYY-MM-DD (same format as data/holidays.txt)")
    p.add_argument("--liquid-file", default=DEFAULT_LIQUID, help="out/market_dma.json for the liquid-stock list (without it the movers stay null)")
    p.add_argument("--snapshot-interval", type=float, default=2.0)
    p.add_argument("--ignore-window", action="store_true", help="connect outside the 08:55-15:45 trading-day window too (testing)")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--summary", action="store_true", help="on exit print one JSON summary (health + last snapshot) to stdout")
    p.add_argument("--drop-after", type=float, help="drop the socket once, this many seconds after streaming starts (reconnect test)")
    return p.parse_args(argv)


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


async def amain(args, token, *, http_get=None, transport_factory=None, fetch_bytes=None, out=sys.stdout):
    scrub = Scrubber()
    scrub.add(token)
    log = Log(scrub)
    symbols = [s for s in args.symbols.split(",") if s.strip()]
    rows = upstox.load_instrument_rows(args.instruments_file, fetch_bytes or upstox.requests_fetch_bytes)
    ins = upstox.build_instruments(rows, symbols)
    del rows
    htext = _read_text(args.holidays_file)
    holidays = market_hours.parse_holidays(htext) if htext is not None else None
    liquid = ins_mod.load_liquid(args.liquid_file) if os.path.exists(args.liquid_file) else set()
    cfg = relay.Config(symbols, snapshot_every_s=args.snapshot_interval, ignore_window=args.ignore_window, holidays=holidays, liquid=liquid)
    svc = relay.RelayService(ins, cfg, token, http_get=http_get or upstox.requests_http_get,
                             transport_factory=transport_factory or (lambda: upstox.WebsocketsTransport(scrub)), log=log, scrub=scrub)
    srv = await server.start_server(svc, args.host, args.port)
    port = srv.sockets[0].getsockname()[1]
    log.info("local relay %s listening on http://%s:%d  (local only)" % (relay.VERSION, args.host, port))
    log.info("health:   http://%s:%d/healthz" % (args.host, port))
    log.info("snapshot: http://%s:%d/v1/live/snapshot.json" % (args.host, port))
    log.info("subscribing to %d indices + %d equities (ltpc)%s" % (len(ins.indices), len(ins.equities), " [window ignored]" if args.ignore_window else ""))
    if holidays is None:
        log.warn("no holiday file at %s: only weekends are treated as closed" % args.holidays_file)
    if not liquid:
        log.warn("no liquid-stock list: gainers and losers stay null")
    stop = asyncio.Event()
    tasks = [asyncio.ensure_future(svc.run(stop))]

    async def timer(delay, fn):
        await asyncio.sleep(delay)
        await fn()

    if args.duration:
        tasks.append(asyncio.ensure_future(timer(args.duration, lambda: _set(stop))))
    if args.drop_after:
        async def drop_when_streaming():
            while svc.sup.state != "streaming":
                await asyncio.sleep(0.5)
            await asyncio.sleep(args.drop_after)
            await svc.force_drop()
        tasks.append(asyncio.ensure_future(drop_when_streaming()))
    try:
        await tasks[0]
    except asyncio.CancelledError:
        stop.set()
        raise
    finally:
        for t in tasks[1:]:
            t.cancel()
        srv.close()
        await srv.wait_closed()
        if args.summary:
            summary = {"health": svc.health(), "last_snapshot": svc.latest.doc if svc.latest else None}
            print(json.dumps(summary, indent=2, allow_nan=False), file=out)
    return svc


async def _set(ev):
    ev.set()


def main(argv=None, env=None, **kw):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    env = os.environ if env is None else env
    token = env.get(TOKEN_ENV, "")
    if not token:
        print("error: the environment variable %s is not set (it is read from the environment only)." % TOKEN_ENV, file=sys.stderr)
        return 2
    if not is_loopback_host(args.host):
        print("error: this service is local-only; --host must be 127.0.0.1, ::1 or localhost.", file=sys.stderr)
        return 2
    scrub = Scrubber()
    scrub.add(token)
    try:
        asyncio.run(amain(args, token, **kw))
    except KeyboardInterrupt:
        print("stopped.", file=sys.stderr)
    except ConfigError as e:
        print("error: " + scrub(e), file=sys.stderr)
        return 2
    except Exception as e:
        print("error: %s: %s" % (type(e).__name__, scrub(e, 200)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
