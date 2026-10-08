"""
universe.py - StockLens coverage universe (the list of stocks every stock-specific updater works on).

ONE mechanism for every stage. The universe is DATA (a JSON file), never code:
    Stage 0 (default, nothing configured)  -> the 10 development stocks below (exactly the old behaviour)
    Stage 1                                -> NIFTY 500 constituents      (file built from NSE's official index list)
    Stage 2                                -> all NSE EQ operating stocks (the same file shape, built with --mode all_eq)
Moving between stages = pointing STOCKLENS_UNIVERSE_FILE at another file. No updater, page or workflow code changes.

File shape:
    {"schema_version":1, "kind":"universe", "stage":"nifty500", "source":"...", "as_of":"YYYY-MM-DD", "count":N,
     "symbols":[{"symbol":"TCS","isin":"INE467B01029","name":"Tata Consultancy Services Limited"}, ...]}
No sector, no price, no rating is ever stored in the universe file.

Environment:  STOCKLENS_UNIVERSE_FILE   path to the universe file; unset = the 10 development stocks.
              A path that is set but unreadable or invalid STOPS the run (a bad universe must never silently shrink coverage).

CLI:  python universe.py build --mode nifty500 --csv FILE --instruments FILE --as-of YYYY-MM-DD --out FILE
      python universe.py build --mode nifty500 --fetch --out FILE        (downloads NSE's list and the Upstox instrument file)
      python universe.py build --mode all_eq --instruments FILE --exclude FILE --out FILE   (Stage 2; not used in Stage 1)
      python universe.py check FILE
"""
import csv
import gzip
import io
import json
import math
import os
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
KIND = "universe"
TEST_SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "BHARTIARTL", "LT", "MARUTI"]
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
SYMBOL_RE = re.compile(r"^(?=.*[A-Z0-9])[A-Z0-9&\-_.]{1,30}$")
NIFTY500_URLS = ["https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
                 "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
                 "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"]
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
MIN_NIFTY500 = 450          # a NIFTY 500 build with fewer symbols than this is a broken download, not an index
MAX_DROP_VS_PREVIOUS = 0.10  # a rebuilt universe may not lose more than this share of the previous symbols


class UniverseError(Exception):
    pass


def _norm_symbol(v):
    s = str(v or "").strip().upper()
    return s if SYMBOL_RE.match(s) else None


def _norm_isin(v):
    s = str(v or "").strip().upper()
    return s if ISIN_RE.match(s) else None


# ------------------------------------------------------------------ reading the universe
def fallback_symbols():
    return list(TEST_SYMBOLS)


def check_doc(doc):
    """-> list of problems (empty = valid)."""
    p = []
    if not isinstance(doc, dict) or doc.get("kind") != KIND or doc.get("schema_version") != SCHEMA_VERSION:
        return ["universe file has the wrong shape (kind/schema_version)"]
    syms = doc.get("symbols")
    if not isinstance(syms, list) or not syms:
        return ["universe file has no symbols"]
    seen, seen_isin = set(), set()
    for i, x in enumerate(syms):
        if not isinstance(x, dict) or _norm_symbol(x.get("symbol")) != x.get("symbol"):
            p.append("entry %d has an invalid symbol" % i)
            continue
        if x["symbol"] in seen:
            p.append("duplicate symbol " + x["symbol"])
        seen.add(x["symbol"])
        if x.get("isin") is not None:
            if _norm_isin(x["isin"]) != x["isin"]:
                p.append("%s: invalid ISIN" % x["symbol"])
            elif x["isin"] in seen_isin:
                p.append("duplicate ISIN " + x["isin"])
            seen_isin.add(x.get("isin"))
        for k in x:
            if k not in ("symbol", "isin", "name"):
                p.append("%s: unexpected field %s (the universe holds no sector, price or rating)" % (x["symbol"], k))
    if doc.get("count") != len(syms):
        p.append("count does not match the number of symbols")
    return p


def load_doc(path):
    """Returns the validated document. Raises UniverseError for a missing, unreadable or invalid file."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (OSError, ValueError) as e:
        raise UniverseError("universe file unreadable: " + type(e).__name__)
    bad = check_doc(doc)
    if bad:
        raise UniverseError("universe file invalid: " + bad[0])
    return doc


def configured_path(env=None):
    return ((env if env is not None else os.environ).get("STOCKLENS_UNIVERSE_FILE") or "").strip() or None


def load(env=None):
    """The symbols the updaters work on, in file order. Unset -> the 10 development stocks. Set but bad -> UniverseError."""
    path = configured_path(env)
    if not path:
        return fallback_symbols()
    return [x["symbol"] for x in load_doc(path)["symbols"]]


def load_entries(env=None):
    """{symbol: {symbol, isin, name}} for the configured universe (the fallback has no ISIN/name)."""
    path = configured_path(env)
    if not path:
        return {s: {"symbol": s} for s in fallback_symbols()}
    return {x["symbol"]: x for x in load_doc(path)["symbols"]}


def describe(env=None):
    path = configured_path(env)
    if not path:
        return {"stage": "development", "count": len(TEST_SYMBOLS), "source": "built-in 10 development stocks", "as_of": None}
    d = load_doc(path)
    return {"stage": d.get("stage"), "count": d["count"], "source": d.get("source"), "as_of": d.get("as_of")}


def in_universe(symbol, env=None):
    return symbol in set(load(env))


# ------------------------------------------------------------------ batching
def order_stalest(symbols, last_done):
    """Never-done symbols first, then the oldest 'last_done' date first; ties alphabetical. last_done: {symbol: 'YYYY-MM-DD' or None}.
    The 10 development stocks are NOT given priority: a capped run must rotate through the whole universe."""
    return sorted(symbols, key=lambda s: ((last_done.get(s) or ""), s))


def budget(env_name, default, env=None):
    """An integer budget from the environment; anything unusable falls back to the default (never 0 or negative)."""
    raw = ((env if env is not None else os.environ).get(env_name) or "").strip()
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


# ------------------------------------------------------------------ building the universe file
def parse_index_csv(text):
    """NSE index list CSV (Company Name, Industry, Symbol, Series, ISIN Code) -> [{'symbol','isin','name'}], EQ series only.
    The Industry column is deliberately ignored: the universe carries no sector."""
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    cols = {(c or "").strip().lower(): c for c in (rdr.fieldnames or [])}
    if "symbol" not in cols:
        raise UniverseError("the index list has no Symbol column")
    out, seen = [], set()
    for r in rdr:
        sym = _norm_symbol(r.get(cols["symbol"]))
        ser = (r.get(cols.get("series")) or "EQ").strip().upper() if "series" in cols else "EQ"
        if not sym or ser != "EQ" or sym in seen:
            continue
        seen.add(sym)
        out.append({"symbol": sym, "isin": _norm_isin(r.get(cols.get("isin code"))) if "isin code" in cols else None,
                    "name": (r.get(cols.get("company name")) or "").strip() or None if "company name" in cols else None})
    return out


def load_instruments_file(path):
    raw = Path(path).read_bytes()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    found = {}
    for x in json.loads(raw):
        if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ" and x.get("trading_symbol") and x.get("isin"):
            found[x["trading_symbol"].strip().upper()] = {"isin": x["isin"], "name": x.get("name")}
    return found


def build_doc(members, instruments, as_of, source, stage):
    """members: [{'symbol','isin','name'}] (an index list); instruments: {symbol: {'isin','name'}} (what Upstox can serve).
    A member is kept only when Upstox has the same symbol, and (when the list gives an ISIN) the same ISIN.
    Returns (doc, dropped) where dropped = [(symbol, reason)]."""
    syms, dropped = [], []
    for m in members:
        meta = instruments.get(m["symbol"])
        if not meta:
            dropped.append((m["symbol"], "not in the Upstox NSE equity instrument file"))
        elif m.get("isin") and m["isin"] != meta["isin"]:
            dropped.append((m["symbol"], "ISIN differs between the index list and Upstox"))
        else:
            syms.append({"symbol": m["symbol"], "isin": meta["isin"], "name": meta.get("name") or m.get("name")})
    syms.sort(key=lambda x: x["symbol"])
    return {"schema_version": SCHEMA_VERSION, "kind": KIND, "stage": stage, "source": source, "as_of": as_of,
            "count": len(syms), "symbols": syms}, dropped


def build_all_eq(instruments, exclude, as_of, source):
    """Stage 2 form: every Upstox NSE EQ instrument except the excluded symbols (ETFs and other non-equity instruments)."""
    ex = {str(s).strip().upper() for s in exclude}
    syms = [{"symbol": s, "isin": v["isin"], "name": v.get("name")} for s, v in sorted(instruments.items()) if s not in ex and _norm_symbol(s)]
    return {"schema_version": SCHEMA_VERSION, "kind": KIND, "stage": "all_eq", "source": source, "as_of": as_of,
            "count": len(syms), "symbols": syms}


def build_problems(doc, previous=None, minimum=0):
    p = check_doc(doc)
    if not p and doc["count"] < minimum:
        p.append("only %d symbols (expected at least %d): the download is probably incomplete" % (doc["count"], minimum))
    if not p and previous:
        old = {x["symbol"] for x in previous["symbols"]}
        gone = old - {x["symbol"] for x in doc["symbols"]}
        if old and len(gone) > len(old) * MAX_DROP_VS_PREVIOUS:
            p.append("%d of %d previous symbols vanished (more than %d%%)" % (len(gone), len(old), round(MAX_DROP_VS_PREVIOUS * 100)))
    return p


def write_doc(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, allow_nan=False, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ CLI
def _fetch_csv():
    import requests
    hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "text/csv,*/*"}
    last = "no address tried"
    for url in NIFTY500_URLS:
        try:
            r = requests.get(url, headers=hdr, timeout=60)
            if r.status_code == 200 and "symbol" in r.text[:300].lower():
                return r.text
            last = "HTTP %d" % r.status_code
        except requests.RequestException as e:
            last = type(e).__name__
    raise UniverseError("could not download the NIFTY 500 list (" + last + ")")


def _fetch_instruments(dest):
    import requests
    try:
        r = requests.get(INSTRUMENTS_URL, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        raise UniverseError("could not download the Upstox instrument file (" + type(e).__name__ + ")")
    Path(dest).write_bytes(r.content)


def main(argv):
    if not argv or argv[0] not in ("build", "check"):
        print(__doc__)
        return 2
    if argv[0] == "check":
        try:
            d = load_doc(argv[1])
        except (IndexError, UniverseError) as e:
            print("::error::" + str(e))
            return 1
        print("universe ok: %d symbols, stage %s, as of %s" % (d["count"], d.get("stage"), d.get("as_of")))
        return 0
    flags, opt, i = {"--fetch"}, {}, 1
    while i < len(argv):
        if argv[i] in flags:
            opt[argv[i]] = True
            i += 1
        elif i + 1 < len(argv):
            opt[argv[i]] = argv[i + 1]
            i += 2
        else:
            print("::error::missing value for " + argv[i])
            return 2
    try:
        import datetime as dt
        as_of = opt.get("--as-of") or dt.date.today().isoformat()
        mode = opt.get("--mode", "nifty500")
        inst_path = opt.get("--instruments")
        if opt.get("--fetch"):
            inst_path = inst_path or "instruments.json.gz"
            _fetch_instruments(inst_path)
        if not inst_path:
            raise UniverseError("--instruments FILE (or --fetch) is required")
        instruments = load_instruments_file(inst_path)
        prev = None
        out = Path(opt["--out"])
        if out.exists():
            try:
                prev = load_doc(out)
            except UniverseError:
                prev = None
        if mode == "nifty500":
            text = _fetch_csv() if opt.get("--fetch") else Path(opt["--csv"]).read_text(encoding="utf-8-sig")
            doc, dropped = build_doc(parse_index_csv(text), instruments, as_of, "NSE NIFTY 500 constituent list, matched to Upstox NSE EQ instruments", "nifty500")
            minimum = MIN_NIFTY500
        elif mode == "all_eq":
            ex = json.loads(Path(opt["--exclude"]).read_text(encoding="utf-8"))
            ex = ex.get("symbols") if isinstance(ex, dict) else ex
            doc, dropped, minimum = build_all_eq(instruments, ex or [], as_of, "Upstox NSE EQ instruments minus the excluded list"), [], 1000
        else:
            raise UniverseError("unknown mode " + mode)
        bad = build_problems(doc, prev, minimum)
        if bad:
            raise UniverseError("universe not written: " + bad[0])
        write_doc(out, doc)
        print("universe written: %d symbols (%s); dropped %d not served by Upstox; as of %s" % (doc["count"], doc["stage"], len(dropped), as_of))
        for s, why in dropped[:50]:
            print("  dropped %s: %s" % (s, why))
        return 0
    except (UniverseError, KeyError, OSError, ValueError) as e:
        print("::error::" + (str(e) if isinstance(e, UniverseError) else type(e).__name__ + " " + str(e)[:120]))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
