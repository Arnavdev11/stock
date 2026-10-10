"""
ledger_storage.py - StorageStep 4D: where the financial-history ledger is read from, and the no-loss guard that protects it.

Standard library only, so the workflow job that saves the ledger needs no packages and no secrets.

Where the ledger comes from (financial_history_updater.py asks choose_source):
  * The data branch `stocklens-data` exists  -> its financial_history_ledger.json is the source of truth. If the branch exists but has no ledger,
    that is an ERROR: nothing falls back to the cache and nothing is created.
  * No data branch (first migration only)    -> the cached data/financial_history_ledger.json.
  * Neither exists                           -> an ERROR. An empty ledger is never started, and no branch is created.
  * A ledger that cannot be read or has the wrong shape is an ERROR; it is never replaced by an empty one.

no_loss_problems(old, new) is run before anything is written (and again by the save job against the branch head):
  * every (symbol, fiscal year, basis) record that existed still exists, with the same provider;
  * every non-null value is unchanged, or the change is recorded in that record's revisions with the old value;
  * earlier revisions are kept as they were; a missing value never becomes zero;
  * an official report record keeps its provenance fields.

Command line (used by the save job):  python ledger_storage.py verify OLD NEW   |   python ledger_storage.py verify-first NEW
"""
import json
import sys
from pathlib import Path

LEDGER_NAME = "financial_history_ledger.json"
SCHEMA = 1
TOLERANCE = 0.005


class StorageError(Exception):
    pass


def choose_source(branch_dir, legacy_file):
    """(path, kind) of the ledger to read. kind is 'branch' or 'legacy-cache'. Raises StorageError instead of ever creating anything."""
    if branch_dir:
        path = Path(branch_dir) / LEDGER_NAME
        if not path.is_file():
            raise StorageError("The data branch exists but holds no %s. Stopping: nothing falls back to the cache and nothing is created." % LEDGER_NAME)
        return path, "branch"
    legacy = Path(legacy_file)
    if legacy.is_file():
        return legacy, "legacy-cache"
    raise StorageError("No ledger found: there is no data branch and no cached %s. Stopping: an empty ledger is never started." % legacy.name)


def load_ledger(path):
    """Read a ledger strictly. A missing, unreadable or wrongly shaped file is an error, never an empty ledger."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StorageError("The ledger %s could not be read (%s)." % (path, type(e).__name__))
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("stocks"), dict):
        raise StorageError("The ledger %s has an unexpected shape or schema." % path)
    return doc


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _provider(rec):
    return ((rec.get("source") or {}).get("provider"))


def no_loss_problems(old, new):
    """List of reasons the new ledger loses something the old one held (empty = nothing lost)."""
    if not (isinstance(old, dict) and isinstance(new, dict) and isinstance(old.get("stocks"), dict) and isinstance(new.get("stocks"), dict)):
        return ["a ledger is not an object with stocks"]
    p = []
    for sym, ost in old["stocks"].items():
        oyears = [r for r in ((ost or {}).get("years") or []) if isinstance(r, dict)]
        nst = new["stocks"].get(sym)
        if nst is None:
            if oyears:
                p.append("%s: stock removed" % sym)
            continue
        nmap = {(r.get("fy"), r.get("basis")): r for r in (nst.get("years") or []) if isinstance(r, dict)}
        for orec in oyears:
            w = "%s FY%s %s: " % (sym, orec.get("fy"), orec.get("basis"))
            nrec = nmap.get((orec.get("fy"), orec.get("basis")))
            if nrec is None:
                p.append(w + "record removed")
                continue
            if _provider(orec) != _provider(nrec):
                p.append(w + "provider changed")
            ofields = ((orec.get("source") or {}).get("fields")) or {}
            nfields = ((nrec.get("source") or {}).get("fields")) or {}
            for f in ofields:
                if f not in nfields:
                    p.append(w + "provenance for %s was removed" % f)
            orevs, nrevs = orec.get("revisions") or [], nrec.get("revisions") or []
            if nrevs[:len(orevs)] != orevs:
                p.append(w + "earlier revisions were altered or removed")
            added = nrevs[len(orevs):]
            nvals = nrec.get("values") or {}
            for f, o in (orec.get("values") or {}).items():
                n = nvals.get(f)
                if o is None:
                    continue                                  # a missing value may become any value, including a genuine 0
                if n is None:
                    p.append(w + "%s: a value was lost" % f)
                elif _num(o) and _num(n) and abs(o - n) >= TOLERANCE:
                    if not any(isinstance(rv, dict) and _num((rv.get("values") or {}).get(f)) and abs(rv["values"][f] - o) < TOLERANCE for rv in added):
                        p.append(w + "%s changed without a revision recording the old value" % f)
    return p


def verify_first(doc):
    """A ledger that starts the data branch must hold real data."""
    if not any(isinstance(s, dict) and s.get("years") for s in doc["stocks"].values()):
        return ["the first ledger holds no year records at all; refusing to start the data branch with it"]
    return []


def main(argv):
    try:
        if len(argv) == 4 and argv[1] == "verify":
            problems = no_loss_problems(load_ledger(argv[2]), load_ledger(argv[3]))
        elif len(argv) == 3 and argv[1] == "verify-first":
            problems = verify_first(load_ledger(argv[2]))
        else:
            print("usage: ledger_storage.py verify OLD NEW | verify-first NEW")
            return 2
    except StorageError as e:
        print("::error::" + str(e))
        return 1
    for x in problems[:30]:
        print("::error::ledger check: " + x)
    if problems:
        return 1
    print("ledger check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
