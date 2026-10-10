"""
historical_handoff.py - the validated hand-over of the daily-price files from the historical-price workflow to the data branch (`stocklens-data`), and back.

Why: a GitHub Actions cache written by a run on `phase1` cannot be read by the publishing workflow on `main`, so out/historical.json never reached the site.
The data branch is shared by both, so the files travel through it, in this layout (nothing outside `historical/` is ever written):

    historical/historical.json          the single file (the 10 development stocks + the NIFTY 50 benchmark), the same document historical_updater.py writes
    historical/shards/<SYMBOL>.json     one file per stock, the same document shape as out/by_symbol/historical/<SYMBOL>.json
    historical/attempts.json            optional: stocks a batch run could not save, so the next batch does not retry them straight away (historical_batch.py)

A hand-over is a DELTA: it holds historical.json and only the stock files that this run added or changed. A saved stock that is not in the hand-over is simply
left as it is (install never deletes), so a 2,500-stock history never has to travel through an artifact in one piece. Every stock that IS in the hand-over is
still compared with its saved copy, day by day, so nothing saved can be lost or altered.

Commands (local files only: no network, no environment, no token, nothing is ever printed from inside a file except counts):
    stage   OUT HANDOFF [--baseline OLD] [--attempts FILE]
                          copy the updater's files out of OUT (the working `out/` folder) into HANDOFF, after verifying them. With --baseline, a stock file
                          that is identical to the saved one is left out (so only what changed travels). --attempts adds the attempts file.
    verify  NEW OLD       check HANDOFF (NEW) on its own, and against the files already saved (OLD); exit 1 on any problem, writing nothing
    install NEW OLD       verify, then copy NEW into OLD (the checked-out data branch folder). It only adds or replaces files; it never deletes one
    seed    OLD OUT       before the updater runs: merge the saved files into OUT so the run extends the saved history (union by date, never dropping a day)

What verify refuses (the hand-over can never silently delete or alter history):
    * any file that is not historical.json or shards/<SYMBOL>.json, a symlink, a bad symbol name, a file that is not valid JSON of the expected shape, or too big;
    * any candle that is not valid: real date, strictly ascending, positive prices, high >= low, volume a whole number >= 0 or null;
    * anything that looks like a credential (a bearer header, a JWT, a ws address, an authorization or access_token field, the token's variable name);
    * a symbol, or a single day of a symbol, that the saved copy has and the new copy lacks;
    * (so the last date can never move backwards either: every saved day must still be there);
    * a candle older than 30 days before the saved last date that differs from the saved one (only the recent days may be corrected, as the updater does);
    * a hand-over of more than MAX_FILES files / MAX_TOTAL_BYTES bytes, or one that would take the saved folder past MAX_SAVED_FILES / MAX_SAVED_BYTES.

Sizes: a 5-year stock file is about 120 KB (96 bytes a candle x ~1,250 candles), so the 2,548 directory stocks are at most ~310 MB. The saved-folder caps leave
room for that and some growth; the hand-over caps hold one batch of up to 500 stocks (~61 MB) with room to spare.
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
ATTEMPTS_FILE = "attempts.json"
MAX_FILES = 800                      # one hand-over (a batch of at most 500 stocks, plus historical.json and attempts.json, with room to spare)
MAX_FILE_BYTES = 6_000_000
MAX_TOTAL_BYTES = 100_000_000        # one hand-over
MAX_SAVED_FILES = 3000               # everything under historical/ on the data branch after an install (2,548 directory stocks + historical.json + attempts.json)
MAX_SAVED_BYTES = 400_000_000
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


def load_set(root, problems, tag, only=None, max_files=None, max_bytes=None):
    """Read a folder into {'main', 'shards': {SYM: doc}, 'attempts', 'bytes', 'files', 'sizes': {relative path: bytes}} and check its names and sizes. Never raises.
    only: None = parse every stock file; a set of symbols = parse just those (the rest are counted by size but not read: a saved copy of 2,500 stocks is not loaded
    into memory to check a batch of 25). max_files / max_bytes default to the hand-over caps."""
    max_files = MAX_FILES if max_files is None else max_files
    max_bytes = MAX_TOTAL_BYTES if max_bytes is None else max_bytes
    root = Path(root)
    res = {"main": None, "shards": {}, "attempts": None, "bytes": 0, "files": 0, "sizes": {}}
    if not root.is_dir():
        return res
    allowed_main, allowed_attempts = root / MAIN_FILE, root / ATTEMPTS_FILE
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
        if p not in (allowed_main, allowed_attempts) and not in_shards:
            problems.append("%s: unexpected file %s" % (tag, rel))
            continue
        if in_shards and not shards.SYMBOL_RE.match(p.stem):
            problems.append("%s: %s is not a valid symbol file name" % (tag, rel))
            continue
        if in_shards and only is not None and p.stem not in only:
            size = p.stat().st_size                                   # counted, not read
            res["sizes"][rel.as_posix()] = size
            res["bytes"] += size
            if size > MAX_FILE_BYTES:
                problems.append("%s/%s: larger than %d bytes" % (tag, rel, MAX_FILE_BYTES))
            continue
        doc, size = _read_json(p, problems, "%s/%s" % (tag, rel))
        res["sizes"][rel.as_posix()] = size
        res["bytes"] += size
        if size > MAX_FILE_BYTES:
            problems.append("%s/%s: larger than %d bytes" % (tag, rel, MAX_FILE_BYTES))
        if doc is None:
            continue
        if p == allowed_main:
            res["main"] = doc
        elif p == allowed_attempts:
            res["attempts"] = doc
        else:
            res["shards"][p.stem] = doc
    if res["files"] > max_files:
        problems.append("%s: more than %d files" % (tag, max_files))
    if res["bytes"] > max_bytes:
        problems.append("%s: more than %d bytes in all" % (tag, max_bytes))
    return res


def attempts_problems(doc, label):
    """The attempts file: {"kind": "historical_attempts", "updated": date, "failed": {SYMBOL: {"last": date, "count": n}}}. Nothing else."""
    if not isinstance(doc, dict) or set(doc) != {"kind", "updated", "failed"} or doc["kind"] != "historical_attempts":
        return ["%s: is not an attempts file" % label]
    try:
        dt.date.fromisoformat(doc["updated"])
    except (TypeError, ValueError):
        return ["%s: updated is not a real date" % label]
    failed = doc["failed"]
    if not isinstance(failed, dict) or len(failed) > MAX_SAVED_FILES:
        return ["%s: failed must be an object of at most %d symbols" % (label, MAX_SAVED_FILES)]
    for sym, v in failed.items():
        if not isinstance(sym, str) or not shards.SYMBOL_RE.match(sym) or not isinstance(v, dict) or set(v) != {"last", "count"}:
            return ["%s: a bad entry for %r" % (label, str(sym)[:30])]
        try:
            dt.date.fromisoformat(v["last"])
        except (TypeError, ValueError):
            return ["%s: %s has no real date" % (label, sym)]
        if not isinstance(v["count"], int) or isinstance(v["count"], bool) or not 1 <= v["count"] <= 1000:
            return ["%s: %s has a bad count" % (label, sym)]
    return []


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
    if res.get("attempts") is not None:
        problems += attempts_problems(res["attempts"], "%s/%s" % (tag, ATTEMPTS_FILE))


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
    """-> list of problems (empty = safe to install). NEW is a delta: a saved stock file that NEW does not contain is left untouched (install never deletes),
    but historical.json must still hold every saved stock and the benchmark, and every stock file NEW does contain is compared with its saved copy."""
    problems = []
    new = load_set(new_dir, problems, "new")
    check_shape(new, "new", problems)
    if new["main"] is None and not new["shards"]:
        problems.append("new: nothing to hand over")
    if new["main"] is None:
        problems.append("new: %s is missing" % MAIN_FILE)
    old_problems = []
    if old_dir:
        old = load_set(old_dir, old_problems, "saved", only=set(new["shards"]), max_files=MAX_SAVED_FILES, max_bytes=MAX_SAVED_BYTES)
        check_shape(old, "saved", old_problems)
    else:
        old = {"main": None, "shards": {}, "sizes": {}}
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
            if sym in new["shards"]:
                _no_loss(doc["stocks"][sym]["candles"], new["shards"][sym]["stocks"][sym]["candles"], "shards/%s.json" % sym, problems)
        osz, nsz = old["sizes"], new["sizes"]
        files = len(osz) + sum(1 for k in nsz if k not in osz)
        total = sum(osz.values()) + sum(v - osz.get(k, 0) for k, v in nsz.items())
        if files > MAX_SAVED_FILES:
            problems.append("the saved folder would hold more than %d files (%d)" % (MAX_SAVED_FILES, files))
        if total > MAX_SAVED_BYTES:
            problems.append("the saved folder would hold more than %d bytes (%d)" % (MAX_SAVED_BYTES, total))
    return problems


# ---------------------------------------------------------------------------------------------------- writing
def _write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _dump(doc):
    return (json.dumps(doc, separators=(",", ":"), allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")


def _same_doc(a, b):
    """True when two JSON files hold the same document (a byte difference such as a trailing newline does not count)."""
    try:
        return json.loads(Path(a).read_text(encoding="utf-8")) == json.loads(Path(b).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False


def stage(out_dir, handoff_dir, baseline_dir=None, attempts_file=None):
    """Copy the updater's files into the hand-over folder, verifying them first (so a bad run fails before anything is uploaded).
    With a baseline (the saved folder), a stock file identical to its saved copy is left out: only what was added or changed travels."""
    out, hand = Path(out_dir), Path(handoff_dir)
    main = out / MAIN_FILE
    sdir = out / "by_symbol" / "historical"
    if hand.exists() and any(hand.iterdir()):
        raise HandoffError("the hand-over folder is not empty")
    if not main.is_file():
        raise HandoffError("out/historical.json does not exist, so there is nothing to hand over")
    hand.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(main, hand / MAIN_FILE)
    base = Path(baseline_dir) / SHARD_DIR if baseline_dir else None
    if sdir.is_dir():
        for f in sorted(sdir.glob("*.json")):
            if shards.SYMBOL_RE.match(f.stem) and f.is_file() and not f.is_symlink():
                if base is not None and (base / f.name).is_file() and _same_doc(f, base / f.name):
                    continue
                (hand / SHARD_DIR).mkdir(exist_ok=True)
                shutil.copyfile(f, hand / SHARD_DIR / f.name)
    if attempts_file and Path(attempts_file).is_file() and not Path(attempts_file).is_symlink():
        shutil.copyfile(attempts_file, hand / ATTEMPTS_FILE)
    problems = verify(hand, baseline_dir)
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
    if (new / ATTEMPTS_FILE).is_file():
        files.append(new / ATTEMPTS_FILE)
    for f in files:
        rel = f.relative_to(new)
        target = (old / rel).resolve()
        if old.resolve() not in target.parents:
            raise HandoffError("refusing to write outside the target folder")
        data = f.read_bytes()
        if target.exists() and (target.read_bytes() == data or _same_doc(f, target)):
            continue                                   # the same document (a trailing newline is not a change): nothing to write, nothing to commit
        _write_bytes(target, data)
        n += 1
    return n


def _merge_candles(durable, current):
    by = _by_date(current)
    by.update(_by_date(durable))           # the validated saved copy wins on a day both have; every day of either survives
    return [by[d] for d in sorted(by)]


def _read_saved_shard(f, sym):
    problems = []
    doc, _ = _read_json(f, problems, "saved/%s/%s" % (SHARD_DIR, f.name))
    if not problems:
        check_shape({"main": None, "shards": {sym: doc}}, "saved", problems)
    return doc, problems


def seed(old_dir, out_dir):
    """Merge the saved files into out/ before the updater runs. Union by date, so no day present in either place is lost.
    The saved stock files are read one at a time (a saved copy of 2,500 stocks is never held in memory), in two passes: the first only checks, so a damaged
    saved copy seeds nothing at all."""
    old = Path(old_dir)
    problems = []
    probe = load_set(old, problems, "saved", only=set(), max_files=MAX_SAVED_FILES, max_bytes=MAX_SAVED_BYTES)
    check_shape({"main": probe["main"], "shards": {}, "attempts": probe["attempts"]}, "saved", problems)
    if problems:
        raise HandoffError("the saved copy is not usable, nothing was seeded: " + problems[0])
    files = sorted((old / SHARD_DIR).glob("*.json")) if (old / SHARD_DIR).is_dir() else []
    for f in files:
        _, bad = _read_saved_shard(f, f.stem)
        if bad:
            raise HandoffError("the saved copy is not usable, nothing was seeded: " + bad[0])
    out = Path(out_dir)
    n = 0
    for f in files:
        sym = f.stem
        doc, _ = _read_saved_shard(f, sym)
        path = out / "by_symbol" / "historical" / (sym + ".json")
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cur = None
        entry = doc["stocks"][sym]
        cur_c = (((cur or {}).get("stocks") or {}).get(sym) or {}).get("candles") if isinstance(cur, dict) else None
        if isinstance(cur_c, list) and not _candle_problems(cur_c, "x"):
            entry["candles"] = _merge_candles(entry["candles"], cur_c)
        _write_bytes(path, _dump(doc))
        n += 1
    m = probe["main"]
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
    usage = "usage: historical_handoff.py stage OUT HANDOFF [--baseline OLD] [--attempts FILE] | verify NEW OLD | install NEW OLD | seed OLD OUT"
    opts, rest, i = {}, [], 0
    while i < len(argv):
        if argv[i] in ("--baseline", "--attempts") and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if len(rest) != 3 or rest[0] not in ("stage", "verify", "install", "seed") or (opts and rest[0] != "stage"):
        print(usage, file=err)
        return 2
    cmd, a, b = rest
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
            print("staged historical.json and %d stock file(s)" % stage(a, b, opts.get("--baseline"), opts.get("--attempts")), file=out)
        else:
            print("seeded %d file(s)" % seed(a, b), file=out)
    except HandoffError as e:
        print("::error::" + str(e), file=err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
