"""
processing_state.py - a DURABLE copy of the updater caches that today live only in the GitHub Actions cache.

The problem it solves
  data/fundamentals_cache.json, data/financials_cache.json and data/quarterly_cache.json hold what each updater has already fetched and when.
  They travel only through the Actions cache (key nse-data-<run id>, restored by the newest nse-data-* prefix match). If that cache is evicted
  (7 days unused, or the 10 GB repository limit) or an older snapshot is restored, the updaters start again and spend their budgets refetching
  work that was already done. This module keeps a copy on the data branch, one small file per stock, and merges it back before the updaters run.

Layout on the data branch (stocklens-data):   state/<kind>/<SYMBOL>.json      kind = fundamentals | financials | quarterly
  One file per stock, so a run that changes 60 stocks commits 60 small files instead of rewriting one large file.

Privacy: the data branch is PUBLIC. Only what the site already publishes is stored. The fundamentals sector label of the wider universe is
  private until the sector approval gate opens (fundamentals_updater.py), so it is removed on export; the Actions cache keeps it, and a merge
  keeps the cache's private label when it has one. Nothing else in these caches is private (ratios, statements and quarters are published).

Merging (seed): PART BY PART (merge_record): each part of a stock's record (fundamentals: profile, ratios, last attempt; financials: each section's
  data and its attempt record; quarterly: quarters and check dates) comes from the copy where that part is newer; a tie keeps the cache. A newer FAILED
  attempt therefore never replaces older successful data, quarterly quarters are never dropped, and a merge can only move each part forward.
  With no durable copy (or an empty one) the cache file is not touched; an unreadable cache is kept aside as <name>.unreadable, never silently replaced.

No-loss check (verify OLD NEW, run by the save job against the branch head): NEW holds only <kind>/<SYMBOL>.json files; every stock file in OLD
  is still in NEW; no part date is older (part_dates); every stored quarter of the same basis is still there; no damaged file and no private sector
  label is in NEW. Nothing is written if it fails.

Command line (standard library only, so the save job needs no packages):
  python processing_state.py export DATA_DIR OUT_ROOT     write OUT_ROOT/<kind>/<SYMBOL>.json from DATA_DIR/data/*_cache.json
  python processing_state.py seed STATE_ROOT DATA_DIR      merge STATE_ROOT/<kind>/*.json into DATA_DIR/data/*_cache.json
  python processing_state.py verify OLD_ROOT NEW_ROOT      no-loss check (exit 1 with the problems listed)
"""
import copy
import json
import re
import sys
from pathlib import Path

SCHEMA = 1
KINDS = {"fundamentals": "fundamentals_cache.json", "financials": "financials_cache.json", "quarterly": "quarterly_cache.json"}
SECTIONS = ("income", "balance_sheet", "cash_flow")
SYMBOL_RE = re.compile(r"^(?=.*[A-Z0-9])[A-Z0-9&\-_.]{1,30}$")
PUBLIC_SECTOR_SYMBOLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "BHARTIARTL", "LT", "MARUTI")   # = universe.TEST_SYMBOLS


def progress(kind, rec):
    """The newest date (YYYY-MM-DD) a record shows work for. '' when it shows none."""
    r = rec if isinstance(rec, dict) else {}
    if kind == "fundamentals":
        dates = [r.get("profile_fetched"), r.get("ratios_fetched"), r.get("last_attempt")]
    elif kind == "financials":
        dates = [(r.get(s) or {}).get(k) for s in SECTIONS for k in ("fetched", "attempted")]
    else:
        dates = [r.get("last_checked"), r.get("last_attempt"), r.get("last_success")]
    return max([d for d in dates if isinstance(d, str)] or [""])


def public_record(kind, symbol, rec):
    """What may be stored on the public branch: a copy with the private sector removed (fundamentals, wider universe)."""
    out = copy.deepcopy(rec)
    if kind == "fundamentals" and symbol not in PUBLIC_SECTOR_SYMBOLS:
        out.pop("sector", None)
    return out


def _newer(a, b, key):
    """The one of two dicts whose `key` date is later; a tie (or two missing dates) keeps `a` (the cache)."""
    da, db = (a or {}).get(key) or "", (b or {}).get(key) or ""
    return b if db > da else a


def merge_record(kind, cached, durable):
    """The record to keep for one stock, merged PART BY PART so that no part moves backwards:
      fundamentals  profile (profile_fetched, sector), ratios (ratios_fetched, ratios), last attempt (last_attempt, last_error) - each from the copy where it is newer;
      financials    each statement section: its data (data, basis, fetched, schema) from the newer fetch, its attempt record (attempted, error, failures) from the newer attempt;
      quarterly     every stored quarter of the same basis is kept (a quarter both hold comes from the copy with the later last_success); the dates are the later ones;
                    when the bases differ the copy with the later last_success is kept whole (the updater itself never mixes bases).
    A tie keeps the cache's part. The private sector label is only ever taken from the cache (the durable copy never holds one)."""
    if not isinstance(durable, dict):
        return cached
    if not isinstance(cached, dict):
        return copy.deepcopy(durable)
    c, d = cached, durable
    if kind == "fundamentals":
        out = copy.deepcopy(c)
        prof = _newer(c, d, "profile_fetched")
        if prof is d:
            out["profile_fetched"] = d.get("profile_fetched")
            out["sector"] = c.get("sector") if c.get("sector") else d.get("sector")      # keep the cache's private label; the durable copy has none
        rat = _newer(c, d, "ratios_fetched")
        if rat is d:
            out["ratios_fetched"], out["ratios"] = d.get("ratios_fetched"), copy.deepcopy(d.get("ratios"))
            for k in ("isin", "company_name"):
                if d.get(k):
                    out[k] = d[k]
        att = _newer(c, d, "last_attempt")
        if att is d:
            out["last_attempt"], out["last_error"] = d.get("last_attempt"), d.get("last_error")
        return out
    if kind == "financials":
        out = copy.deepcopy(c)
        for s in SECTIONS:
            cs, ds = c.get(s) or {}, d.get(s) or {}
            if not ds:
                continue
            sec = copy.deepcopy(cs)
            if _newer(cs, ds, "fetched") is ds:
                for k in ("data", "basis", "fetched", "schema"):
                    if k in ds:
                        sec[k] = copy.deepcopy(ds[k])
            if _newer(cs, ds, "attempted") is ds:
                for k in ("attempted", "error", "failures"):
                    if k in ds:
                        sec[k] = ds[k]
            out[s] = sec
        if progress(kind, d) > progress(kind, c):
            for k in ("isin", "company_name", "last_error"):
                if k in d:
                    out[k] = d[k]
        return out
    # quarterly
    if c.get("basis") and d.get("basis") and c["basis"] != d["basis"]:
        return copy.deepcopy(_newer(c, d, "last_success"))
    win = _newer(c, d, "last_success")
    lose = d if win is c else c
    out = copy.deepcopy(win)
    qs = out.setdefault("quarters", {})
    for pe, q in (lose.get("quarters") or {}).items():
        qs.setdefault(pe, copy.deepcopy(q))                  # quarters are never deleted
    out["basis"] = win.get("basis") or lose.get("basis")
    for k in ("last_checked", "last_success"):
        out[k] = max([x for x in (c.get(k), d.get(k)) if isinstance(x, str)] or [None]) if (c.get(k) or d.get(k)) else None
    att = _newer(c, d, "last_attempt")
    out["last_attempt"] = att.get("last_attempt")
    for k in ("failures", "problem"):
        if k in att:
            out[k] = att[k]
    return out


def read_cache(data_dir, kind):
    path = Path(data_dir) / "data" / KINDS[kind]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path, None
    return path, doc if isinstance(doc, dict) and isinstance(doc.get("stocks"), dict) else None


def read_state(root, kind):
    """{symbol: record} from ROOT/<kind>/*.json. A damaged file is skipped and reported."""
    d, out, bad = Path(root) / kind, {}, []
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            if not SYMBOL_RE.match(f.stem):
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bad.append(f.name)
                continue
            if isinstance(doc, dict) and doc.get("schema") == SCHEMA and doc.get("kind") == kind and doc.get("symbol") == f.stem and isinstance(doc.get("record"), dict):
                out[f.stem] = doc["record"]
            else:
                bad.append(f.name)
    return out, bad


def export(data_dir, out_root):
    """Write one public file per stock and kind. Returns {kind: files written}."""
    counts = {}
    for kind in KINDS:
        _, doc = read_cache(data_dir, kind)
        n = 0
        for sym, rec in sorted(((doc or {}).get("stocks") or {}).items()):
            if not SYMBOL_RE.match(sym) or not isinstance(rec, dict) or not progress(kind, rec):
                continue                                     # nothing done for this stock yet: nothing to keep
            path = Path(out_root) / kind / (sym + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {"schema": SCHEMA, "kind": kind, "symbol": sym, "record": public_record(kind, sym, rec)}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            tmp.replace(path)                                # atomic: a crash never leaves a half-written state file
            n += 1
        counts[kind] = n
    return counts


def seed(state_root, data_dir):
    """Merge the durable copy into the working caches. Returns {kind: {"from_state": n, "kept_cache": n, "added": n}}."""
    report = {}
    for kind in KINDS:
        durable, bad = read_state(state_root, kind)
        path, doc = read_cache(data_dir, kind)
        if not durable:
            report[kind] = {"from_state": 0, "kept_cache": 0, "added": 0, "damaged_files": len(bad)}
            continue                                         # nothing to merge: the cache file is left exactly as it is
        if doc is None:
            if path.exists():                                # unreadable cache: kept aside for inspection, never silently replaced
                path.replace(path.with_name(path.name + ".unreadable"))
            doc = {"stocks": {}} if kind != "quarterly" else {"schema_version": 1, "kind": "quarterly_cache", "stocks": {}}
        stocks = doc["stocks"]
        r = {"from_state": 0, "kept_cache": 0, "added": 0, "damaged_files": len(bad)}
        for sym, rec in durable.items():
            old = stocks.get(sym)
            new = merge_record(kind, old, rec)
            if old is None:
                r["added"] += 1
            elif progress(kind, new) > progress(kind, old) or new != old:
                r["from_state"] += 1
            else:
                r["kept_cache"] += 1
            stocks[sym] = new
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, allow_nan=False), encoding="utf-8")
        tmp.replace(path)
        report[kind] = r
    return report


def part_dates(kind, rec):
    """{part: date} that may never move backwards between two saved copies of one stock."""
    r = rec if isinstance(rec, dict) else {}
    if kind == "fundamentals":
        return {"profile": r.get("profile_fetched") or "", "ratios": r.get("ratios_fetched") or ""}
    if kind == "financials":
        return {s: (r.get(s) or {}).get("fetched") or "" for s in SECTIONS}
    return {"last_success": r.get("last_success") or "", "last_checked": r.get("last_checked") or ""}


def unexpected_files(root):
    """Anything under ROOT that is not <kind>/<SYMBOL>.json for a known kind: the save job may write nothing else."""
    bad = []
    for f in sorted(Path(root).rglob("*")):
        if f.is_dir():
            continue
        rel = f.relative_to(root).parts
        if len(rel) != 2 or rel[0] not in KINDS or not rel[1].endswith(".json") or not SYMBOL_RE.match(rel[1][:-5]):
            bad.append("/".join(rel))
    return bad


def no_loss_problems(old_root, new_root):
    problems = ["unexpected file in the new state: " + x for x in unexpected_files(new_root)]
    for kind in KINDS:
        old, _ = read_state(old_root, kind)
        new, bad = read_state(new_root, kind)
        problems += ["%s: damaged file %s" % (kind, b) for b in bad]
        for sym, n in new.items():
            if kind == "fundamentals" and sym not in PUBLIC_SECTOR_SYMBOLS and "sector" in n:
                problems.append("fundamentals/%s: holds a private sector label" % sym)
        for sym, o in old.items():
            n = new.get(sym)
            if n is None:
                problems.append("%s/%s: missing from the new state" % (kind, sym))
                continue
            po, pn = part_dates(kind, o), part_dates(kind, n)
            for part, d in po.items():
                if pn.get(part, "") < d:
                    problems.append("%s/%s: %s is older than the saved copy (%s < %s)" % (kind, sym, part, pn.get(part) or "none", d))
            if kind == "quarterly":
                if n.get("basis") == o.get("basis"):
                    lost = sorted(set((o.get("quarters") or {})) - set((n.get("quarters") or {})))
                    if lost:
                        problems.append("quarterly/%s: quarters lost %s" % (sym, ", ".join(lost[:5])))
                elif (n.get("last_success") or "") <= (o.get("last_success") or ""):
                    problems.append("quarterly/%s: basis changed without a newer successful check" % sym)
    return problems


def main(argv):
    if len(argv) == 3 and argv[0] == "export":
        print(json.dumps(export(argv[1], argv[2]), sort_keys=True))
        return 0
    if len(argv) == 3 and argv[0] == "seed":
        print(json.dumps(seed(argv[1], argv[2]), sort_keys=True))
        return 0
    if len(argv) == 3 and argv[0] == "verify":
        p = no_loss_problems(argv[1], argv[2])
        for x in p[:50]:
            print("::error::processing state: " + x)
        if p:
            print("::error::%d problem(s); nothing may be saved." % len(p))
            return 1
        print("Processing state verified: nothing lost.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
