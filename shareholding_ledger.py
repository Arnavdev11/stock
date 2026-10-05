"""
shareholding_ledger.py - Phase 5H.3: where the shareholding-pattern ledger is read from, and the no-loss guard that protects it.

Standard library only, so the workflow job that saves the ledger needs no packages and no secrets.
The ledger is a second file (shareholding_ledger.json) on the same `stocklens-data` branch as financial_history_ledger.json. The two are independent.

Where the ledger comes from (shareholding_updater.py asks choose_source):
  * The data branch exists and holds shareholding_ledger.json -> that file is the source of truth.
  * The data branch exists but the file is missing           -> an ERROR, unless the run was started with init=true (the very first run
                                                               only), in which case an empty ledger is started explicitly.
  * No data branch                                           -> an ERROR. The branch is never created here.
  * A ledger that cannot be read or has the wrong shape is an ERROR; it is never replaced by an empty one.

no_loss_problems(old, new) is run before anything is written (and again by the save job against the branch head):
  * every (symbol, quarter_end) record that existed still exists;
  * an available quarter never becomes unavailable;
  * every non-null category value is unchanged, or the change is recorded in that record's revisions with the old value;
  * earlier revisions are kept exactly as they were (append-only);
  * the NSE record id and the XBRL URL of a record are never removed, and a changed record id is recorded in the revisions.

Command line (used by the save job):  python shareholding_ledger.py verify OLD NEW   |   python shareholding_ledger.py verify-first NEW
"""
import json
import sys
from pathlib import Path

LEDGER_NAME = "shareholding_ledger.json"
SCHEMA = 1
TOLERANCE = 0.005
CATEGORIES = ("promoters", "fii", "other_dii", "mutual_funds", "retail_other")


class StorageError(Exception):
    pass


def new_ledger():
    return {"schema": SCHEMA, "stocks": {}}


def choose_source(branch_dir, init_allowed=False):
    """(path or None, kind). kind is 'branch' or 'init'. Raises StorageError instead of ever creating anything on its own."""
    if not branch_dir:
        raise StorageError("There is no data branch checked out. Stopping: the data branch is never created by the shareholding updater.")
    path = Path(branch_dir) / LEDGER_NAME
    if path.is_file():
        if init_allowed:
            raise StorageError("The data branch already holds %s; init_ledger is for the very first run only and never replaces it." % LEDGER_NAME)
        return path, "branch"
    if init_allowed:
        return None, "init"
    raise StorageError("The data branch holds no %s. Stopping: run with init_ledger = true for the very first run only." % LEDGER_NAME)


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


def _quarters(stock):
    return [r for r in ((stock or {}).get("quarters") or []) if isinstance(r, dict)]


def no_loss_problems(old, new):
    """List of reasons the new ledger loses something the old one held (empty = nothing lost)."""
    if not (isinstance(old, dict) and isinstance(new, dict) and isinstance(old.get("stocks"), dict) and isinstance(new.get("stocks"), dict)):
        return ["a ledger is not an object with stocks"]
    p = []
    for sym, ost in old["stocks"].items():
        oq = _quarters(ost)
        nst = new["stocks"].get(sym)
        if nst is None:
            if oq:
                p.append("%s: stock removed" % sym)
            continue
        nmap = {r.get("quarter_end"): r for r in _quarters(nst)}
        for orec in oq:
            w = "%s %s: " % (sym, orec.get("quarter_end"))
            nrec = nmap.get(orec.get("quarter_end"))
            if nrec is None:
                p.append(w + "record removed")
                continue
            if orec.get("status") == "available" and nrec.get("status") != "available":
                p.append(w + "an available quarter became unavailable")
            osrc, nsrc = orec.get("source") or {}, nrec.get("source") or {}
            for f in ("record_id", "xbrl_url"):
                if osrc.get(f) and not nsrc.get(f):
                    p.append(w + "source %s was removed" % f)
            orevs, nrevs = orec.get("revisions") or [], nrec.get("revisions") or []
            if nrevs[:len(orevs)] != orevs:
                p.append(w + "earlier revisions were altered or removed")
            added = nrevs[len(orevs):]
            if osrc.get("record_id") and nsrc.get("record_id") and osrc["record_id"] != nsrc["record_id"]:
                if not any(isinstance(rv, dict) and (rv.get("source") or {}).get("record_id") == osrc["record_id"] for rv in added):
                    p.append(w + "the NSE record id changed without a revision recording the old one")
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
    """A ledger that starts the file on the data branch must hold real data."""
    if not any(isinstance(s, dict) and s.get("quarters") for s in doc["stocks"].values()):
        return ["the first ledger holds no quarter records at all; refusing to start with it"]
    return []


def main(argv):
    try:
        if len(argv) == 4 and argv[1] == "verify":
            problems = no_loss_problems(load_ledger(argv[2]), load_ledger(argv[3]))
        elif len(argv) == 3 and argv[1] == "verify-first":
            problems = verify_first(load_ledger(argv[2]))
        else:
            print("usage: shareholding_ledger.py verify OLD NEW | verify-first NEW")
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
