"""
stock_directory.py - builds the SEARCHABLE STOCK DIRECTORY (every eligible NSE stock: symbol, ISIN, company name) from the offline classifier's output.

This is NOT the research-data coverage list. The five Upstox-consuming updaters keep reading universe.json; nothing here changes what they fetch.
This is not a NIFTY 500 list and has no all_eq mode: the only way into the directory is the classifier status eligible_eq or eligible_be (live/classifier.py), and the eligibility
rules are NOT repeated here - live.classifier decides, this module only checks the classifier's answer and refuses to publish anything questionable.

Pure: build_directory() is given the rows of the Upstox NSE instrument file, the TEXT of the three NSE reference lists, a description of each input file (size, SHA-256, and a
source date ONLY when a person genuinely supplied one), an approval record and, optionally, the previous directory. It returns an Outcome. No network, no clock, no environment,
no secret, no file access (main() at the bottom is the only part that reads local files, and it writes one file only when every check passed).

Fail closed. If ANY problem is found the Outcome carries no document at all:
    - an input is missing, empty, malformed or unusable (a reference with no ISIN column, an equity list with no SERIES column, an empty SME or ETF list, malformed ISINs, a bad date);
    - the classifier's partition is incomplete (the statuses do not add up to the NSE_EQ rows);
    - conflicts (an ISIN listed under several series) that nobody has acknowledged in the approval record;
    - the approval record is missing or has no minimum count, or fewer stocks than that minimum would be published;
    - the held-out candidates exceed the approved cap;
    - the new list differs too much from the previous one (drift).
An equity-list EQ/BE record that no Upstox row matches ("unresolved") cannot be published (the directory takes names only from Upstox rows) and is never approved: it is
HELD OUT and listed in the report, and it does not by itself refuse the build.
A candidate that is individually questionable (a symbol, ISIN or instrument key shared with another row, a symbol that disagrees with the reference record, no usable name ...)
is WITHHELD from the file and listed in the report. Nothing is guessed, renamed or repaired, and no count is hard-coded: the minimum comes from the approval record.
"""
import datetime
import hashlib
import json
import re
import sys

from live import classifier
from live import reference

SCHEMA_VERSION = 1
KIND = "stock_directory"
SCOPE = "every NSE_EQ row classified eligible_eq or eligible_be by live/classifier.py; not a NIFTY 500 list; not the research-data coverage list"
INPUT_NAMES = ("instruments", "equity", "sme", "etf")
SYMBOL_RE = re.compile(r"^[A-Z0-9&._-]{1,30}$")
ISIN_RE = classifier.ISIN_RE
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_MAX = 300
DEFAULT_MAX_DROP = 0.10             # same tolerance universe.py applies to a rebuilt universe
DEFAULT_MAX_GROWTH = 0.25
LIST_CAP = 100
ENTRY_KEYS = ("symbol", "isin", "name")
DOC_KEYS = ("schema_version", "kind", "scope", "built_on", "rules", "inputs", "classification", "counts", "directory_sha256", "symbols")
INPUT_KEYS = ("name", "bytes", "sha256", "as_of", "source")
UNRESOLVED_SERIES = ("EQ", "BE")     # an equity-list record in a series the classifier would accept, which no Upstox row matched


class Outcome:
    def __init__(self, doc, problems, report, classification=None):
        self.classification = classification   # the classifier's result object (for the text report); None when the build stopped before classifying
        self.doc = doc                  # None whenever there is any problem
        self.problems = problems
        self.report = report            # plain JSON-safe dict for the run summary; never written into the published file

    @property
    def ok(self):
        return self.doc is not None and not self.problems


def describe_input(raw, as_of=None, source=None):
    """Size and SHA-256 of the exact bytes read. as_of / source are recorded only if the caller passes them; nothing is taken from the file."""
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("describe_input needs the bytes of the file")
    return {"bytes": len(raw), "sha256": hashlib.sha256(bytes(raw)).hexdigest(), "as_of": as_of, "source": source}


def _valid_date(s):
    if not isinstance(s, str) or not DATE_RE.match(s):
        return False
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def entry_problems(e):
    """Problems of ONE directory entry (also used to withhold a single bad candidate instead of publishing it)."""
    if not isinstance(e, dict) or set(e) != set(ENTRY_KEYS):
        return ["entry must hold exactly symbol, isin and name"]
    p = []
    if not (isinstance(e["symbol"], str) and SYMBOL_RE.match(e["symbol"])):
        p.append("symbol_not_accepted")
    if not (isinstance(e["isin"], str) and ISIN_RE.match(e["isin"])):
        p.append("isin_not_valid")
    n = e["name"]
    if not (isinstance(n, str) and n == n.strip() and 0 < len(n) <= NAME_MAX and not any(ord(c) < 32 for c in n)):
        p.append("company_name_missing_or_unusable")
    return p


def _input_problems(inputs):
    p = []
    if not isinstance(inputs, dict):
        return ["inputs must describe the four input files"]
    for n in INPUT_NAMES:
        d = inputs.get(n)
        if not isinstance(d, dict):
            p.append("input %s is not described" % n)
            continue
        if not (isinstance(d.get("bytes"), int) and not isinstance(d.get("bytes"), bool) and d["bytes"] > 0):
            p.append("input %s: size missing or zero" % n)
        if not (isinstance(d.get("sha256"), str) and HEX64_RE.match(d["sha256"])):
            p.append("input %s: sha256 missing or malformed" % n)
        if d.get("as_of") is not None and not _valid_date(d["as_of"]):
            p.append("input %s: as_of is not a real YYYY-MM-DD date" % n)
        if d.get("source") is not None and not (isinstance(d["source"], str) and d["source"].strip()):
            p.append("input %s: source must be a non-empty text or absent" % n)
    for n in sorted(set(inputs) - set(INPUT_NAMES)):
        p.append("unknown input %r" % n)
    return p


def _approval_problems(a):
    if not isinstance(a, dict):
        return ["no approval record: the minimum count is never assumed"]
    p = []
    m = a.get("min_published")
    if not (isinstance(m, int) and not isinstance(m, bool) and m > 0):
        p.append("approval: min_published must be a positive whole number")
    for k, default in (("max_drop_fraction", DEFAULT_MAX_DROP), ("max_growth_fraction", DEFAULT_MAX_GROWTH)):
        v = a.get(k, default)
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1):
            p.append("approval: %s must be a number from 0 to 1" % k)
    c = a.get("max_held_out_candidates")
    if c is not None and not (isinstance(c, int) and not isinstance(c, bool) and c >= 0):
        p.append("approval: max_held_out_candidates must be a whole number or absent")
    v = a.get("acknowledged_conflict_isins", [])
    if not (isinstance(v, list) and all(isinstance(x, str) and ISIN_RE.match(x) for x in v)):
        p.append("approval: acknowledged_conflict_isins must be a list of valid ISINs")
    return p


def _reference_problems(label, ref, need_series):
    p = []
    st = ref.stats
    if st["rows"] == 0:
        p.append("%s reference has no data rows (an empty list would silently disable its exclusions)" % label)
    if st["rows_without_isin"] or st["rows_with_malformed_isin"]:
        p.append("%s reference: %d rows without an ISIN and %d with a malformed ISIN (examples: %s)" % (
            label, st["rows_without_isin"], st["rows_with_malformed_isin"], ", ".join(st["malformed_isin_examples"][:5]) or "none"))
    if need_series and ref.columns["series"] is None:
        p.append("%s reference has no SERIES column; EQ and BE cannot be told apart" % label)
    return p


def _capped(items):
    return items[:LIST_CAP], max(0, len(items) - LIST_CAP)


def _candidate_reasons(r):
    """Why one classifier candidate is withheld ([] = publishable). Every classifier flag counts: a flagged candidate is never published."""
    reasons = list(r["flags"])
    ep = entry_problems({"symbol": r["symbol"], "isin": r["isin"], "name": r["name"].strip() if isinstance(r["name"], str) else ""})
    return sorted(set(reasons + ep))


def check_directory_doc(doc):
    """Structural check of a published directory (also validates a previous one before it is trusted for the drift check)."""
    if not isinstance(doc, dict):
        return ["the directory is not an object"]
    p = []
    if set(doc) != set(DOC_KEYS):
        p.append("keys differ from the schema (extra: %s, missing: %s)" % (sorted(set(doc) - set(DOC_KEYS)), sorted(set(DOC_KEYS) - set(doc))))
        return p
    if doc["schema_version"] != SCHEMA_VERSION or doc["kind"] != KIND:
        p.append("schema_version/kind are not %s/%s" % (SCHEMA_VERSION, KIND))
    if not _valid_date(doc["built_on"]):
        p.append("built_on is not a real date")
    syms = doc["symbols"]
    if not isinstance(syms, list):
        return p + ["symbols is not a list"]
    seen_s, seen_i = set(), set()
    for i, e in enumerate(syms):
        ep = entry_problems(e)
        if ep:
            p.append("entry %d: %s" % (i, ", ".join(ep)))
            continue
        if e["symbol"] in seen_s:
            p.append("duplicate symbol %s" % e["symbol"])
        if e["isin"] in seen_i:
            p.append("duplicate ISIN %s" % e["isin"])
        seen_s.add(e["symbol"])
        seen_i.add(e["isin"])
    if [e.get("symbol") for e in syms if isinstance(e, dict)] != sorted(e.get("symbol") for e in syms if isinstance(e, dict)):
        p.append("entries are not sorted by symbol")
    c = doc["counts"]
    if not isinstance(c, dict) or c.get("published") != len(syms):
        p.append("counts.published does not equal the number of entries")
    cl = doc["classification"]
    if not isinstance(cl, dict) or not isinstance(cl.get("counts"), dict) or sum(cl["counts"].values()) != cl.get("nse_eq_rows"):
        p.append("classification counts do not add up to nse_eq_rows")
    ins = doc["inputs"]
    if not isinstance(ins, list) or [x.get("name") for x in ins if isinstance(x, dict)] != list(INPUT_NAMES):
        p.append("inputs must list instruments, equity, sme, etf in that order")
    else:
        for x in ins:
            if set(x) != set(INPUT_KEYS):
                p.append("input %s has the wrong keys" % x.get("name"))
        p += _input_problems({x["name"]: x for x in ins if set(x) == set(INPUT_KEYS)})
    if not (isinstance(doc["directory_sha256"], str) and HEX64_RE.match(doc["directory_sha256"])):
        p.append("directory_sha256 is malformed")
    elif not p and doc["directory_sha256"] != directory_hash(syms):
        p.append("directory_sha256 does not match the entries")
    return p


def directory_hash(entries):
    return classifier.hash_lines(["%s|%s|%s" % (e["isin"], e["symbol"], e["name"]) for e in entries])


def dumps(doc):
    """Deterministic text: the same input always gives the same bytes."""
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def build_directory(rows, equity_text, sme_text, etf_text, inputs, approval, built_on, previous=None):
    problems = []
    report = {"problems": problems, "inputs": inputs}
    problems += _input_problems(inputs)
    problems += _approval_problems(approval)
    if not _valid_date(built_on):
        problems.append("built_on must be a real YYYY-MM-DD date supplied by the caller")
    if not isinstance(rows, list):
        problems.append("the Upstox instrument rows are not a list")
        return Outcome(None, problems, report)
    refs = {}
    for label, text in (("equity", equity_text), ("sme", sme_text), ("etf", etf_text)):
        try:
            refs[label] = reference.parse(label, text)
        except reference.ReferenceError as e:
            problems.append("invalid reference file: %s" % e)
    if len(refs) < 3:
        return Outcome(None, problems, report)
    for label in ("equity", "sme", "etf"):
        problems += _reference_problems(label, refs[label], need_series=(label == "equity"))
    c = classifier.classify_universe(rows, refs["equity"], refs["sme"], refs["etf"])
    if c.non_object_rows:
        problems.append("the Upstox file holds %d entries that are not objects" % c.non_object_rows)
    if c.nse_eq_rows == 0:
        problems.append("the Upstox file has no NSE_EQ rows")
    if not c.partition_ok or sum(c.counts.values()) != c.nse_eq_rows or len(c.records) != c.nse_eq_rows:
        problems.append("the classification is not a complete partition (%d statuses for %d NSE_EQ rows)" % (sum(c.counts.values()), c.nse_eq_rows))
    if any(r["status"] not in classifier.STATUS_ORDER for r in c.records):
        problems.append("a row has a status the classifier does not define")
    report["classification_counts"] = dict(c.counts)
    report["classification_hashes"] = dict(c.hashes)
    report["classification_global_hash"] = c.global_hash
    report["nse_eq_rows"] = c.nse_eq_rows
    report["candidates"] = c.candidates
    report["partition_ok"] = bool(c.partition_ok)

    ap = approval if isinstance(approval, dict) else {}
    ack_conflicts = set(ap.get("acknowledged_conflict_isins", [])) if isinstance(ap.get("acknowledged_conflict_isins", []), list) else set()

    conflicts = sorted({r["isin"] for r in c.records if r["status"] == "review_conflict"})
    unack_conf = [i for i in conflicts if i not in ack_conflicts]
    report["conflicts"] = {"isins": _capped(conflicts)[0], "acknowledged": len(conflicts) - len(unack_conf)}
    if unack_conf:
        problems.append("%d conflicting ISIN(s) (several series) are not acknowledged in the approval record, first: %s" % (len(unack_conf), ", ".join(unack_conf[:5])))
    unres = [{"isin": u["isin"], "symbol": u["symbol"], "series": u["series"]} for u in c.reference_side["equity"]["unmatched"] if u["series"] in UNRESOLVED_SERIES]
    report["unresolved_reference_records"] = {"count": len(unres), "held_out": True, "records": _capped(unres)[0], "not_shown": _capped(unres)[1]}

    published, held = [], []
    for r in c.records:
        if r["status"] not in classifier.CANDIDATES:
            continue
        why = _candidate_reasons(r)
        if why:
            held.append({"isin": r["isin"], "symbol": r["symbol"], "status": r["status"], "reasons": why})
        else:
            published.append({"symbol": r["symbol"], "isin": r["isin"], "name": r["name"].strip()})
    published.sort(key=lambda e: e["symbol"])
    held.sort(key=lambda h: (h["symbol"], h["isin"]))
    report["held_out_candidates"] = {"count": len(held), "records": _capped(held)[0], "not_shown": _capped(held)[1]}
    cap = ap.get("max_held_out_candidates")
    if isinstance(cap, int) and not isinstance(cap, bool) and len(held) > cap:
        problems.append("%d candidates are withheld, more than the approved cap of %d" % (len(held), cap))
    m = ap.get("min_published")
    if isinstance(m, int) and not isinstance(m, bool) and len(published) < m:
        problems.append("only %d stocks would be published, fewer than the approved minimum of %d" % (len(published), m))
    if not published:
        problems.append("nothing would be published")

    report["drift"] = None
    if previous is not None:
        pp = check_directory_doc(previous)
        if pp:
            problems.append("the previous directory is not valid, so no drift check is possible: %s" % pp[0])
        else:
            old = {e["isin"]: e for e in previous["symbols"]}
            new = {e["isin"]: e for e in published}
            gone, added = sorted(set(old) - set(new)), sorted(set(new) - set(old))
            renamed = sorted(i for i in set(old) & set(new) if old[i]["symbol"] != new[i]["symbol"])
            drop = ap.get("max_drop_fraction", DEFAULT_MAX_DROP)
            grow = ap.get("max_growth_fraction", DEFAULT_MAX_GROWTH)
            report["drift"] = {"previous": len(old), "removed": len(gone), "added": len(added), "symbol_changed": len(renamed),
                               "removed_isins": _capped(gone)[0], "added_isins": _capped(added)[0], "max_drop_fraction": drop, "max_growth_fraction": grow}
            if isinstance(drop, (int, float)) and len(gone) > len(old) * drop:
                problems.append("%d of %d previous stocks vanished, more than the approved %.0f%%" % (len(gone), len(old), drop * 100))
            if isinstance(grow, (int, float)) and len(added) > len(old) * grow:
                problems.append("%d stocks were added to a previous %d, more than the approved %.0f%%" % (len(added), len(old), grow * 100))

    if problems:
        return Outcome(None, problems, report, c)
    doc = {"schema_version": SCHEMA_VERSION, "kind": KIND, "scope": SCOPE, "built_on": built_on,
           "rules": {"classifier": "live/classifier.py", "eligible_statuses": list(classifier.CANDIDATES), "precedence": list(classifier.STATUS_ORDER)},
           "inputs": [{"name": n, "bytes": inputs[n]["bytes"], "sha256": inputs[n]["sha256"], "as_of": inputs[n].get("as_of"), "source": inputs[n].get("source")} for n in INPUT_NAMES],
           "classification": {"nse_eq_rows": c.nse_eq_rows, "counts": dict(c.counts), "hashes": dict(c.hashes), "global_hash": c.global_hash},
           "counts": {"published": len(published), "candidates": c.candidates, "withheld_candidates": len(held), "conflicts_acknowledged": len(conflicts),
                      "unresolved_reference_records_held_out": len(unres)},
           "directory_sha256": directory_hash(published), "symbols": published}
    final = check_directory_doc(doc)
    if final:
        return Outcome(None, ["internal check of the result failed: %s" % x for x in final], report, c)
    return Outcome(doc, [], report, c)


# ------------------------------------------------------------------------------------------------------------ command line (local files only)
def main(argv=None, out=None, err=None):
    import argparse
    import gzip
    out, err = out or sys.stdout, err or sys.stderr
    ap = argparse.ArgumentParser(description="Build stock_directory.json from LOCAL files. Local files only: no download, no API call, no secret.")
    ap.add_argument("--instruments-file", required=True)
    ap.add_argument("--equity-ref", required=True)
    ap.add_argument("--sme-ref", required=True)
    ap.add_argument("--etf-ref", required=True)
    ap.add_argument("--approval", required=True, help="JSON approval record (min_published, ...)")
    ap.add_argument("--built-on", required=True, help="build date YYYY-MM-DD")
    ap.add_argument("--previous", help="previous stock_directory.json for the drift check")
    ap.add_argument("--out", required=True, help="written only if every check passed")
    ap.add_argument("--report-json", help="where to write the run report (written even when the build is refused)")
    ap.add_argument("--classifier-report", help="where to write the classifier's text report (written even when the build is refused)")
    ap.add_argument("--asof", action="append", default=[], metavar="NAME=YYYY-MM-DD", help="source date, only if genuinely known")
    ap.add_argument("--source", action="append", default=[], metavar="NAME=TEXT", help="where the file came from, only if genuinely known")
    a = ap.parse_args(argv)

    def pairs(items, what):
        d = {}
        for it in items:
            k, _, v = it.partition("=")
            if k not in INPUT_NAMES or not v:
                print("error: --%s needs NAME=VALUE with NAME one of %s" % (what, ", ".join(INPUT_NAMES)), file=err)
                return None
            d[k] = v
        return d
    asof, source = pairs(a.asof, "asof"), pairs(a.source, "source")
    if asof is None or source is None:
        return 2
    raws = {}
    for n, path in (("instruments", a.instruments_file), ("equity", a.equity_ref), ("sme", a.sme_ref), ("etf", a.etf_ref)):
        try:
            with open(path, "rb") as f:
                raws[n] = f.read()
        except OSError as e:
            print("error: cannot read the %s file: %s" % (n, type(e).__name__), file=err)
            return 2
    try:
        with open(a.approval, "r", encoding="utf-8") as f:
            approval = json.load(f)
        previous = None
        if a.previous:
            with open(a.previous, "r", encoding="utf-8") as f:
                previous = json.load(f)
        data = raws["instruments"]
        data = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        rows = json.loads(data.decode("utf-8"))
        texts = {n: raws[n].decode("utf-8-sig") for n in ("equity", "sme", "etf")}
    except (OSError, ValueError, EOFError) as e:
        print("error: cannot read an input: %s" % type(e).__name__, file=err)
        return 2
    inputs = {n: describe_input(raws[n], asof.get(n), source.get(n)) for n in INPUT_NAMES}
    res = build_directory(rows, texts["equity"], texts["sme"], texts["etf"], inputs, approval, a.built_on, previous)
    cls = res.classification
    printable = json.dumps(res.report, indent=1, ensure_ascii=False)
    print(printable, file=out)
    try:
        if a.report_json:
            with open(a.report_json, "w", encoding="utf-8", newline="\n") as f:
                f.write(printable + "\n")
        if a.classifier_report and cls is not None:
            paths = {"instruments": a.instruments_file, "equity": a.equity_ref, "sme": a.sme_ref, "etf": a.etf_ref}
            prov = [{"name": n, "path": paths[n], "bytes": inputs[n]["bytes"], "sha256": inputs[n]["sha256"], "as_of": inputs[n]["as_of"]} for n in INPUT_NAMES]
            with open(a.classifier_report, "w", encoding="utf-8", newline="\n") as f:
                f.write(classifier.format_classification(cls, prov) + "\n")
    except OSError as e:
        print("error: cannot write a report: %s" % type(e).__name__, file=err)
        return 2
    if res.problems:
        print("REFUSED - nothing written:", file=err)
        for p in res.problems:
            print("  - " + p, file=err)
        return 3
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(res.doc))
    print("wrote %s (%d stocks)" % (a.out, res.doc["counts"]["published"]), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
