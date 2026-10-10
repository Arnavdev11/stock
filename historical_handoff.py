"""
historical_handoff.py - the validated hand-over of the daily-price files from the historical-price workflow to the data branch (`stocklens-data`), and back.

Why: a GitHub Actions cache written by a run on `phase1` cannot be read by the publishing workflow on `main`, so out/historical.json never reached the site.
The data branch is shared by both, so the files travel through it, in this layout (nothing outside `historical/` is ever written):

    historical/historical.json          the single file (the 10 development stocks + the NIFTY 50 benchmark), the same document historical_updater.py writes
    historical/shards/<SYMBOL>.json     one file per stock, the same document shape as out/by_symbol/historical/<SYMBOL>.json

Commands (local files only: no network, no environment, no token, nothing is ever printed from inside a file except counts):
    stage   OUT HANDOFF   copy the updater's files out of OUT (the working `out/` folder) into HANDOFF, after verifying them
    verify  NEW OLD       check HANDOFF (NEW) on its own, and against the files already saved (OLD); exit 1 on any problem, writing nothing
    install NEW OLD       verify, then copy NEW into OLD (the checked-out data branch folder). It only adds or replaces files; it never deletes one
    seed    OLD OUT       before the updater runs: merge the saved files into OUT so the run extends the saved history (union by date, never dropping a day)

What verify refuses (the hand-over can never silently delete or alter history):
    * any file that is not historical.json or shards/<SYMBOL>.json, a symlink, a bad symbol name, a file that is not valid JSON of the expected shape, or too big;
    * any candle that is not valid: real date, strictly ascending, positive prices, high >= low, volume a whole number >= 0 or null;
    * anything that looks like a credential (a bearer header, a JWT, a ws address, an authorization or access_token field, the token's variable name);
    * a symbol, or a single day of a symbol, that the saved copy has and the new copy lacks;
    * (so the last date can never move backwards either: every saved day must still be there);
    * a candle older than 30 days before the saved last date that differs from the saved one (only the recent days may be corrected, as the updater does).
"""
import datetime as dt
import json
import math
import re
import shutil
import sys
from pathlib import Path

import shards

MAIN_FILE = "historical.json"
SHARD_DIR = "shards"
MAX_FILES = 700
MAX_FILE_BYTES = 6_000_000
MAX_TOTAL_BYTES = 60_000_000
CORRECTION_DAYS = 30                 # candles older than this (before the saved last date) must be identical
CANDLE_KEYS = {"date", "open", "high", "low", "close", "volume"}
SECRET_RES = [re.compile(p, re.I) for p in (r"bearer\s", r"eyJ[A-Za-z0-9_\-]{10,}\.", r"wss?://", r"access_token", r"authorization", r"api[_-]?key", r"UPSTOX_ANALYTICS_TOKEN")]


class HandoffError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------- reading
def _read_json(path, problems, label):
    try:
        raw = path.read_bytes()
    except OSError:
        problems.append("%s: cannot be read" % label)
        return None, 0
    text = raw.decode("utf-8", "replace")
    for rx in SECRET_RES:
        if rx.search(text):
            problems.append("%s: contains text that looks like a credential (%s)" % (label, rx.pattern))
            break
    try:
        return json.loads(raw.decode("utf-8")), len(raw)
    except (ValueError, UnicodeDecodeError):
        problems.append("%s: is not valid JSON" % label)
        return None, len(raw)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _candle_problems(candles, label):
    if not isinstance(candles, list) or not candles:
        return ["%s: no candles" % label]
    prev = ""
    for c in candles:
        if not isinstance(c, dict) or set(c) != CANDLE_KEYS:
            return ["%s: a candle does not have exactly date, open, high, low, close, volume" % label]
        try:
            dt.date.fromisoformat(c["date"])
            if len(c["date"]) != 10:
                raise ValueError
        except (TypeError, ValueError):
            return ["%s: a candle has no real date" % label]
        if c["date"] <= prev:
            return ["%s: candles are not in strictly ascending date order (at %s)" % (label, c["date"])]
        prev = c["date"]
        if not all(_num(c[k]) and c[k] > 0 for k in ("open", "high", "low", "close")) or c["high"] < c["low"]:
            return ["%s: a bad price on %s" % (label, c["date"])]
        v = c["volume"]
        if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
            return ["%s: a bad volume on %s" % (label, c["date"])]
    return []


def _entries(doc, sym_expected=None):
    """{symbol: candles} from a document: the stocks, plus the benchmark under its own symbol."""
    out = {}
    stocks = doc.get("stocks") if isinstance(doc, dict) else None
    if isinstance(stocks, dict):
        for s, e in stocks.items():
            out[s] = e
    b = doc.get("benchmark") if isinstance(doc, dict) else None
    if isinstance(b, dict):
        out["benchmark:" + str(b.get("symbol"))] = b
    return out


def load_set(root, problems, tag):
    """Read a hand-over folder into {'main': doc or None, 'shards': {SYM: doc}} and check its shape. Never raises."""
    root = Path(root)
    res = {"main": None, "shards": {}, "bytes": 0, "files": 0}
    if not root.is_dir():
        return res
    allowed_main = root / MAIN_FILE
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_symlink():
            problems.append("%s: %s is a symbolic link" % (tag, rel))
            continue
        if p.is_dir():
            if str(rel) not in (SHARD_DIR,):
                problems.append("%s: unexpected folder %s" % (tag, rel))
            continue
        res["files"] += 1
        in_shards = rel.parent == Path(SHARD_DIR) and p.suffix == ".json"
        if p != allowed_main and not in_shards:
            problems.append("%s: unexpected file %s" % (tag, rel))
            continue
        if in_shards and not shards.SYMBOL_RE.match(p.stem):
            problems.append("%s: %s is not a valid symbol file name" % (tag, rel))
            continue
        doc, size = _read_json(p, problems, "%s/%s" % (tag, rel))
        res["bytes"] += size
        if size > MAX_FILE_BYTES:
            problems.append("%s/%s: larger than %d bytes" % (tag, rel, MAX_FILE_BYTES))
        if doc is None:
            continue
        if p == allowed_main:
            res["main"] = doc
        else:
            res["shards"][p.stem] = doc
    if res["files"] > MAX_FILES:
        problems.append("%s: more than %d files" % (tag, MAX_FILES))
    if res["bytes"] > MAX_TOTAL_BYTES:
        problems.append("%s: more than %d bytes in all" % (tag, MAX_TOTAL_BYTES))
    return res


def check_shape(res, tag, problems):
    m = res["main"]
    if m is not None:
        if not isinstance(m, dict) or not isinstance(m.get("stocks"), dict):
            problems.append("%s/%s: no stocks object" % (tag, MAIN_FILE))
        else:
            for s, e in _entries(m).items():
                if not isinstance(e, dict):
                    problems.append("%s/%s: entry %s is not an object" % (tag, MAIN_FILE, s))
                    continue
                if not s.startswith("benchmark:") and e.get("symbol") != s:
                    problems.append("%s/%s: entry %s names another symbol" % (tag, MAIN_FILE, s))
                problems += _candle_problems(e.get("candles"), "%s/%s %s" % (tag, MAIN_FILE, s))
    for sym, doc in res["shards"].items():
        st = doc.get("stocks") if isinstance(doc, dict) else None
        if not isinstance(st, dict) or list(st) != [sym] or not isinstance(st[sym], dict) or st[sym].get("symbol") != sym:
            problems.append("%s/%s/%s.json: must hold exactly its own symbol" % (tag, SHARD_DIR, sym))
            continue
        problems += _candle_problems(st[sym].get("candles"), "%s/%s/%s.json" % (tag, SHARD_DIR, sym))


def _by_date(candles):
    return {c["date"]: c for c in candles}


def _no_loss(old_candles, new_candles, label, problems):
    old, new = _by_date(old_candles), _by_date(new_candles)
    missing = sorted(set(old) - set(new))
    if missing:
        problems.append("%s: %d saved day(s) would be lost, first %s" % (label, len(missing), missing[0]))
        return
    cutoff = (dt.date.fromisoformat(max(old)) - dt.timedelta(days=CORRECTION_DAYS)).isoformat()
    changed = sorted(d for d in old if d < cutoff and old[d] != new[d])
    if changed:
        problems.append("%s: %d old candle(s) were altered (first %s); only the last %d days may be corrected" % (label, len(changed), changed[0], CORRECTION_DAYS))


def verify(new_dir, old_dir=None):
    """-> list of problems (empty = safe to install)."""
    problems = []
    new = load_set(new_dir, problems, "new")
    check_shape(new, "new", problems)
    if new["main"] is None and not new["shards"]:
        problems.append("new: nothing to hand over")
    if new["main"] is None:
        problems.append("new: %s is missing" % MAIN_FILE)
    old_problems = []
    old = load_set(old_dir, old_problems, "saved") if old_dir else {"main": None, "shards": {}}
    if old_problems:
        problems.append("the saved copy on the data branch is not in the expected shape, so no comparison is possible: " + old_problems[0])
        return problems
    if not problems:
        if old["main"] is not None:
            if new["main"] is None:
                problems.append("historical.json is missing from the new files but exists in the saved copy")
            else:
                ne = _entries(new["main"])
                for s, e in _entries(old["main"]).items():
                    if s not in ne:
                        problems.append("historical.json: %s would disappear" % s)
                    elif _candle_problems(e.get("candles"), "saved") == []:
                        _no_loss(e["candles"], ne[s]["candles"], "historical.json %s" % s, problems)
        for sym, doc in old["shards"].items():
            if sym not in new["shards"]:
                problems.append("shards/%s.json would disappear" % sym)
                continue
            o, n = doc["stocks"][sym]["candles"], new["shards"][sym]["stocks"][sym]["candles"]
            _no_loss(o, n, "shards/%s.json" % sym, problems)
    return problems


# ---------------------------------------------------------------------------------------------------- writing
def _write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _dump(doc):
    return (json.dumps(doc, separators=(",", ":"), allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")


def stage(out_dir, handoff_dir):
    """Copy the updater's files into the hand-over folder, verifying them first (so a bad run fails before anything is uploaded)."""
    out, hand = Path(out_dir), Path(handoff_dir)
    main = out / MAIN_FILE
    sdir = out / "by_symbol" / "historical"
    if hand.exists() and any(hand.iterdir()):
        raise HandoffError("the hand-over folder is not empty")
    if not main.is_file():
        raise HandoffError("out/historical.json does not exist, so there is nothing to hand over")
    hand.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(main, hand / MAIN_FILE)
    if sdir.is_dir():
        for f in sorted(sdir.glob("*.json")):
            if shards.SYMBOL_RE.match(f.stem) and f.is_file() and not f.is_symlink():
                (hand / SHARD_DIR).mkdir(exist_ok=True)
                shutil.copyfile(f, hand / SHARD_DIR / f.name)
    problems = verify(hand, None)
    if problems:
        shutil.rmtree(hand)
        raise HandoffError("; ".join(problems[:5]))
    return len(list((hand / SHARD_DIR).glob("*.json"))) if (hand / SHARD_DIR).is_dir() else 0


def install(new_dir, old_dir):
    """Verify, then add or replace files under old_dir. Never deletes. Returns the number of files written."""
    problems = verify(new_dir, old_dir)
    if problems:
        raise HandoffError("; ".join(problems[:5]))
    new, old = Path(new_dir), Path(old_dir)
    n = 0
    files = [new / MAIN_FILE] + sorted((new / SHARD_DIR).glob("*.json") if (new / SHARD_DIR).is_dir() else [])
    for f in files:
        rel = f.relative_to(new)
        target = (old / rel).resolve()
        if old.resolve() not in target.parents:
            raise HandoffError("refusing to write outside the target folder")
        data = f.read_bytes()
        if target.exists() and target.read_bytes() == data:
            continue
        _write_bytes(target, data)
        n += 1
    return n


def _merge_candles(durable, current):
    by = _by_date(current)
    by.update(_by_date(durable))           # the validated saved copy wins on a day both have; every day of either survives
    return [by[d] for d in sorted(by)]


def seed(old_dir, out_dir):
    """Merge the saved files into out/ before the updater runs. Union by date, so no day present in either place is lost."""
    problems = []
    old = load_set(old_dir, problems, "saved")
    check_shape(old, "saved", problems)
    if problems:
        raise HandoffError("the saved copy is not usable, nothing was seeded: " + problems[0])
    out = Path(out_dir)
    n = 0
    for sym, doc in old["shards"].items():
        path = out / "by_symbol" / "historical" / (sym + ".json")
        cur = None
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cur = None
        merged = json.loads(json.dumps(doc))
        entry = merged["stocks"][sym]
        cur_c = (((cur or {}).get("stocks") or {}).get(sym) or {}).get("candles") if isinstance(cur, dict) else None
        if isinstance(cur_c, list) and not _candle_problems(cur_c, "x"):
            entry["candles"] = _merge_candles(entry["candles"], cur_c)
        _write_bytes(path, _dump(merged))
        n += 1
    m = old["main"]
    if m is not None:
        path = out / MAIN_FILE
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cur = None
        merged = json.loads(json.dumps(m))
        cur_e = _entries(cur) if isinstance(cur, dict) else {}
        for s, e in _entries(merged).items():
            ce = cur_e.get(s)
            if isinstance(ce, dict) and isinstance(ce.get("candles"), list) and not _candle_problems(ce["candles"], "x"):
                e["candles"] = _merge_candles(e["candles"], ce["candles"])
        _write_bytes(path, _dump(merged))
        n += 1
    return n


# ---------------------------------------------------------------------------------------------------- command line
def main(argv=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = "usage: historical_handoff.py stage OUT HANDOFF | verify NEW OLD | install NEW OLD | seed OLD OUT"
    if len(argv) != 3 or argv[0] not in ("stage", "verify", "install", "seed"):
        print(usage, file=err)
        return 2
    cmd, a, b = argv
    try:
        if cmd == "verify":
            problems = verify(a, b)
            if problems:
                for p in problems[:20]:
                    print("::error::" + p, file=err)
                return 1
            print("verified: nothing would be lost", file=out)
        elif cmd == "install":
            print("installed %d file(s)" % install(a, b), file=out)
        elif cmd == "stage":
            print("staged historical.json and %d stock file(s)" % stage(a, b), file=out)
        else:
            print("seeded %d file(s)" % seed(a, b), file=out)
    except HandoffError as e:
        print("::error::" + str(e), file=err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
