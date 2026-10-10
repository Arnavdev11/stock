"""
shards.py - per-stock output files, so a stock page loads ONE small file instead of a file holding every stock.

Layout (published next to the other out/*.json files):
    out/by_symbol/<kind>/<SYMBOL>.json      the SAME document shape as out/<kind>.json, holding only that stock
    out/by_symbol/index.json                what exists: {kind: {symbol: bytes}}   (rebuilt from the files on disk)
kinds: historical | financial_history | shareholding

The monolithic out/<kind>.json is still written for the 10 development stocks only (Page 1 and the old loaders keep working unchanged).
A shard is written atomically; a failure for one stock never touches another stock's shard. Nothing is invented: a shard exists only
for a stock that has real data of that kind.
"""
import json
import re
from pathlib import Path

KINDS = ("historical", "financial_history", "shareholding")
SYMBOL_RE = re.compile(r"^(?=.*[A-Z0-9])[A-Z0-9&\-_.]{1,30}$")


def shard_dir(root, kind):
    if kind not in KINDS:
        raise ValueError("unknown shard kind " + str(kind))
    return Path(root) / "out" / "by_symbol" / kind


def shard_path(root, kind, symbol):
    if not SYMBOL_RE.match(symbol or ""):
        raise ValueError("unsafe symbol for a file name")
    return shard_dir(root, kind) / (symbol + ".json")


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    text = json.dumps(doc, separators=(",", ":"), allow_nan=False, ensure_ascii=False)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return len(text.encode("utf-8"))


def write_shard(root, kind, symbol, template, stock_obj, stocks_key="stocks"):
    """template: the document with its 'stocks' removed (as_of, source, notes ...). Returns the size in bytes."""
    doc = {k: v for k, v in template.items() if k != stocks_key}
    doc[stocks_key] = {symbol: stock_obj}
    return _write(shard_path(root, kind, symbol), doc)


def read_shard(root, kind, symbol):
    """The stored document for one stock, or None (missing or damaged: never raises)."""
    try:
        return json.loads(shard_path(root, kind, symbol).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_index(root):
    """Scan the shard folders and return {kind: {symbol: bytes}} (symbols sorted)."""
    idx = {}
    for kind in KINDS:
        d = shard_dir(root, kind)
        items = {}
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                if SYMBOL_RE.match(f.stem):
                    items[f.stem] = f.stat().st_size
        idx[kind] = items
    return idx


def write_index(root, as_of):
    idx = build_index(root)
    doc = {"schema": 1, "as_of": as_of, "counts": {k: len(v) for k, v in idx.items()}, "kinds": idx}
    return _write(Path(root) / "out" / "by_symbol" / "index.json", doc)
