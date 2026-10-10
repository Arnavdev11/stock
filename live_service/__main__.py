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
    p.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                   help="let ONE exact loopback web origin (e.g. http://127.0.0.1:8000) read the snapshot from the browser (CORS). Off by default; repeatable; no wildcard, no public origin")
    p.add_argument("--count-only", action="store_true",
                   help="Stage A: read a LOCAL instrument file (--instruments-file, required), print how many NSE equities are eligible and why the rest are not, then exit. "
                        "Reads no token, opens no network connection, never downloads anything")
    p.add_argument("--review-flagged", action="store_true",
                   help="with --count-only: after the report, print the evidence a person needs to decide the eligibility rules - complete metadata of every flagged instrument, "
                        "which fields separate the instrument types, observed ISIN structure, and sample rows. Descriptive only; same offline, token-free guarantees")
    p.add_argument("--classify-universe", action="store_true",
                   help="offline universe classifier: reads LOCAL files only (--instruments-file, --equity-ref, --sme-ref, --etf-ref), gives every NSE_EQ row exactly one status, prints counts, "
                        "reference overlaps, duplicates, unmatched records in both directions and audit hashes, then exits. No token is read and nothing is downloaded or written")
    p.add_argument("--equity-ref", metavar="PATH", help="with --classify-universe: NSE equity list (EQUITY_L.csv), the source of the trading series")
    p.add_argument("--sme-ref", metavar="PATH", help="with --classify-universe: NSE SME list (SME_EQUITY_L.csv)")
    p.add_argument("--etf-ref", metavar="PATH", help="with --classify-universe: NSE ETF reference (nse_etfs.csv)")
    p.add_argument("--baseline", metavar="PATH", help="with --classify-universe: a text file of reported counts and SHA-256 hashes to COMPARE against (never adjusted to match)")
    p.add_argument("--asof", action="append", default=[], metavar="NAME=YYYY-MM-DD",
                   help="with --classify-universe: the real source/as-of date of an input (NAME: instruments, equity, sme or etf). Without it the report says 'not recorded'; dates are never inferred")
    p.add_argument("--list", dest="list_status", action="append", default=[], metavar="STATUS", help="with --classify-universe: also print every sorted audit line of this status (repeatable)")
    p.add_argument("--drop-after", type=float, help="drop the socket once, this many seconds after streaming starts (reconnect test)")
    return p.parse_args(argv)


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


async def amain(args, token, *, http_get=None, transport_factory=None, fetch_bytes=None, out=sys.stdout):
    allow_origins = server.check_origins(args.allow_origin)          # refuse a bad origin before anything is downloaded or opened
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
    srv = await server.start_server(svc, args.host, args.port, allow_origins)
    port = srv.sockets[0].getsockname()[1]
    log.info("local relay %s listening on http://%s:%d  (local only)" % (relay.VERSION, args.host, port))
    log.info("health:   http://%s:%d/healthz" % (args.host, port))
    log.info("snapshot: http://%s:%d/v1/live/snapshot.json" % (args.host, port))
    if args.allow_origin:
        log.info("browser access (CORS) allowed for: " + ", ".join(allow_origins))
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


def count_only(args, out=None, err=None):
    """Stage A. Deliberately before (and apart from) everything that reads the token or touches the network."""
    import hashlib
    from live import eligible
    out = out or sys.stdout
    err = err or sys.stderr
    if not args.instruments_file:
        print("error: --count-only reads a local file and never downloads one: pass --instruments-file PATH (NSE.json or NSE.json.gz).", file=err)
        return 2
    try:
        with open(args.instruments_file, "rb") as f:
            raw = f.read()
        rows = upstox.parse_instrument_bytes(raw)
    except (OSError, ValueError, ConfigError) as e:
        print("error: cannot read the instrument file: %s" % type(e).__name__, file=err)
        return 2
    result = eligible.classify(rows, collect=bool(args.review_flagged))
    source = {"path": args.instruments_file, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    print(eligible.format_report(result.report, source), file=out)
    if args.review_flagged:
        print("\n" + eligible.review(rows, result), file=out)
    return 0


def classify_universe_cmd(args, out=None, err=None):
    """Offline classifier. Like count_only, it runs before (and apart from) everything that reads the token or touches the network."""
    import datetime
    import hashlib
    from live import classifier, reference
    out = out or sys.stdout
    err = err or sys.stderr
    need = [("--instruments-file", args.instruments_file), ("--equity-ref", args.equity_ref), ("--sme-ref", args.sme_ref), ("--etf-ref", args.etf_ref)]
    missing = [n for n, v in need if not v]
    if missing:
        print("error: --classify-universe reads local files only and never downloads anything; missing: %s" % ", ".join(missing), file=err)
        return 2
    asof = {}
    for item in args.asof:
        name, _, value = item.partition("=")
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            value = ""
        if name not in ("instruments", "equity", "sme", "etf") or not value or len(value) != 10:
            print("error: --asof must look like NAME=YYYY-MM-DD with NAME one of instruments, equity, sme, etf (got %r)" % item, file=err)
            return 2
        asof[name] = value
    for st in args.list_status:
        if st not in classifier.STATUS_ORDER:
            print("error: --list needs one of: %s" % ", ".join(classifier.STATUS_ORDER), file=err)
            return 2

    def read(path, what):
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            print("error: cannot read the %s file: %s" % (what, type(e).__name__), file=err)
            return None
        return raw

    raws = {}
    for name, path in (("instruments", args.instruments_file), ("equity", args.equity_ref), ("sme", args.sme_ref), ("etf", args.etf_ref)):
        raws[name] = read(path, name)
        if raws[name] is None:
            return 2
    try:
        rows = upstox.parse_instrument_bytes(raws["instruments"])
        refs = {n: reference.parse(n, raws[n].decode("utf-8-sig")) for n in ("equity", "sme", "etf")}
    except (ValueError, ConfigError, UnicodeDecodeError) as e:
        print("error: cannot read an input: %s: %s" % (type(e).__name__, e if isinstance(e, reference.ReferenceError) else ""), file=err)
        return 2
    prov = []
    paths = {"instruments": args.instruments_file, "equity": args.equity_ref, "sme": args.sme_ref, "etf": args.etf_ref}
    for n in ("instruments", "equity", "sme", "etf"):
        entry = {"name": n, "path": paths[n], "bytes": len(raws[n]), "sha256": hashlib.sha256(raws[n]).hexdigest(), "as_of": asof.get(n)}
        if n != "instruments":
            st, cols = refs[n].stats, refs[n].columns
            entry["notes"] = [("columns used", "isin=%r symbol=%r series=%r" % (cols["isin"], cols["symbol"], cols["series"])),
                              ("rows", "%d (without ISIN %d, malformed ISIN %d, ISINs listed more than once %d)" % (st["rows"], st["rows_without_isin"], st["rows_with_malformed_isin"], st["isins_listed_more_than_once"])),
                              ("series counts", ", ".join("%s=%d" % kv for kv in st["series_counts"].items()))]
        prov.append(entry)
    result = classifier.classify_universe(rows, refs["equity"], refs["sme"], refs["etf"])
    baseline_lines, ok = None, True
    if args.baseline:
        braw = read(args.baseline, "baseline")
        if braw is None:
            return 2
        baseline_lines, ok = classifier.compare_baseline(result, classifier.parse_baseline(braw.decode("utf-8-sig", "replace")))
    print(classifier.format_classification(result, prov, baseline_lines, args.list_status), file=out)
    return 0 if ok else 3


def main(argv=None, env=None, **kw):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.classify_universe:
        if args.count_only:
            print("error: use either --count-only or --classify-universe, not both.", file=sys.stderr)
            return 2
        return classify_universe_cmd(args)
    if args.review_flagged and not args.count_only:
        print("error: --review-flagged only works together with --count-only.", file=sys.stderr)
        return 2
    if args.count_only:
        return count_only(args)
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
