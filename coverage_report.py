"""
coverage_report.py - StockLens OFFLINE coverage report (version 1). It measures; it never fills, fetches or publishes.

Question it answers: for the first 500 stocks of the published universe, and for the whole universe, which research data do the files we already hold
actually contain? It reads local files only and writes two report files (coverage_report.json and coverage_report.md) into a directory you name.

What it never does (enforced by test_coverage_report.py):
  * it makes no network call and imports no provider client (no requests, urllib, socket, subprocess; nothing from the *_updater modules),
  * it opens every input read-only and changes none of them (no cache, ledger, checkpoint, shard, out/ file or site file),
  * it writes only coverage_report.json / coverage_report.md, and only into --out; an --out that is, contains or sits inside an input folder, the
    repository folder or a symbolic link is refused,
  * it prints and stores no financial value and no shareholding percentage or holder: only counts, dates and field names,
  * it never treats a missing value as zero: null and zero are counted separately, and a JSON file that exists does not make a stock "covered".

Selection rule (exactly the existing one): the universe file (universe.json on the stocklens-data branch) is validated with universe.check_doc, then
the first scope is symbols[:500] in FILE ORDER (the order universe.load() returns). No ranking, no NIFTY 500 assumption. The second scope is every symbol.
An invalid universe file stops the run (exit 2, nothing written).

Inputs (all optional except universe.json; a missing one makes its module "not_measurable" with the reason, never zero):
  --data-branch DIR   a checkout/export of the stocklens-data branch: universe.json, stock_directory.json, financial_history_ledger.json,
                      shareholding_ledger.json, historical/historical.json, historical/attempts.json, historical/shards/<SYMBOL>.json
  --fundamentals FILE a fundamentals file (out/fundamentals.json shape) you supply. It is measured as what it is: its own as_of is reported and ages are
                      taken against --as-of. Without it fundamentals and growth are not measurable (the saved state lives only in the GitHub Actions cache).
  --state-dir DIR     OPTIONAL. A folder you supply that holds the updaters' saved state, copied out of a workflow run's data/ folder (the Actions cache is not
                      reachable from here). Exactly these file names are read, each only if present:
                        fundamentals_cache.json   {"stocks": {SYMBOL: {isin, ratios{pe,pb,roa,roe,roce,ev_ebitda}, ratios_fetched, profile_fetched, last_attempt, last_error, ...}}}
                        financials_cache.json     {"stocks": {SYMBOL: {income|balance_sheet|cash_flow: {data, basis, fetched, schema, error, attempted, failures}, last_error}}}
                        quarterly_cache.json      {"schema_version": 1, "kind": "quarterly_cache", "stocks": {SYMBOL: {basis, last_checked, last_attempt, last_success, quarters{PERIOD_END: ...}}}}
                      (The published out/financials.json is NOT the state: it holds no fetch or attempt dates, so it is never read.) A record is identified by its
                      SYMBOL key in "stocks". Without --state-dir the report is exactly what it was before this option existed.
                      Classification, per universe stock (counts only; no value, error text or record is written):
                        valid        usable data that is not older than the documented interval
                        stale        usable data older than the interval (fundamentals ratios 7 days = RATIOS_MAX_AGE_DAYS; financial statements 30 days =
                                     STATEMENT_MAX_AGE_DAYS; quarterly results 30 days = STEADY_EVERY_DAYS, the steady re-check interval; the 2-day re-check inside
                                     a results-pending window is not applied)
                        missing      no usable data; the reason says why (no cache entry, never fetched, failed last attempt, not in Upstox, ...). Quarterly: a failed
                                     request (failures > 0) is told apart from a check that stored no quarters ("no_data") and from a rejected response, which are not failures.
                                     Financial statements: the unit is the complete set of three statements (income, balance sheet, cash flow); a stock holding only some
                                     of them is missing here with the reason "incomplete_sections" and the sections it does hold are listed in the facts. That is a statement
                                     about this cache, not about publication or about what the provider has: the updater builds a published row from any usable section.
                        invalid      a record that is present but malformed, from a schema version newer than or unknown to the updater, dated in the future, or whose
                                     symbol key appears more than once in the file (no entry is chosen over another)
                        stale        also: a financial section written under an OLDER schema version (or none) is readable but the updater refetches it before use, so it is
                                     counted stale, with the fixed reason "a_section_has_an_older_schema" in the facts (never invalid)
                        unverified   cannot be decided offline (a fundamentals record held aside after an ISIN change; a file that is missing, unreadable, malformed
                                     or of an unsupported schema makes the WHOLE module unverified, never zero coverage). Identity: a fundamentals or financials record that
                                     holds usable data is compared with the canonical ISIN of its symbol (universe.json, cross-checked with stock_directory.json when usable).
                                     Equal -> it keeps its valid/stale class. Different, absent from the record, or no single canonical ISIN (the universe has none, or the
                                     directory contradicts it or lists the symbol twice) -> unverified with a fixed reason; nothing is remapped and no ISIN is ever written.
                                     Quarterly records carry no ISIN and are not compared.
                      Counters: a "failures" counter must be a whole number from 0 to 1000 (this report's own plausibility bound); a boolean, negative, non-integer or larger value
                      makes that record invalid before the counter is used in any calculation, and a whole number longer than 18 characters is read as a marker, never converted,
                      so a corrupt file cannot make the report slow or large. A bad counter is never read as zero.
                      A cache record is not proof of publication: whether the data reached the site is reported as unverified.
                      Quarterly back-off and results-pending states are derived only from fields the cache holds (failures and last_attempt for back-off; the stored
                      quarter-end dates for the pending window), with the updater's own rules, and are reported in the facts beside the counts, never instead of them.
                      A pending window says a new quarter's result is expected; it does not say whether it has arrived. A file whose top-level "stocks" key is repeated
                      is unusable (which copy is meant cannot be known) and its module is unverified.
  --as-of YYYY-MM-DD  the reference date for every age (required: the report never reads the clock, so equal inputs give an identical report)

Identifiers: a stored record whose ISIN differs from the universe's is still counted (the stock is keyed by symbol) but is named under the module's
"flags"; the report does not guess why (an older ISIN after a corporate action is one possibility, not something it assumes).

Freshness: a threshold is used only where the code documents its meaning. Fundamentals ratios: 7 days (fundamentals_updater.RATIOS_MAX_AGE_DAYS,
"refresh ratios weekly"). Candles, financial history, shareholding quarters: no threshold has been agreed, so they report measurable facts (ages, dates,
row counts) and the status "threshold_not_configured"; they are never labelled fresh or stale.

Usage: python coverage_report.py --data-branch DIR --as-of 2026-10-23 --out REPORT_DIR [--scope first500|all|both] [--fundamentals FILE] [--state-dir DIR]
Exit codes: 0 report written, 2 refused (bad arguments, --state-dir not a folder, unsafe --out, unreadable or invalid universe).
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import shareholding_ledger as _sl
import universe

SCHEMA_VERSION = 1
KIND = "coverage_report"
FIRST_N = 500
THRESHOLD_NOT_CONFIGURED = "threshold_not_configured"

FUNDAMENTALS_METRICS = ("pe", "pb", "roe", "roa", "roce", "ev_ebitda")
GROWTH_FIELDS = ("revenue_growth", "profit_growth")
FUNDAMENTALS_MAX_AGE_DAYS = 7        # fundamentals_updater.RATIOS_MAX_AGE_DAYS: "refresh ratios weekly"
NOT_FOUND_ERROR = "Symbol not found in Upstox NSE equity instruments"      # fundamentals_updater.NOT_FOUND_ERROR
FH_VALUE_FIELDS = ("revenue", "total_revenue", "profit_before_tax", "profit_after_tax", "eps_basic", "eps_diluted",
                   "operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "capex", "free_cash_flow")     # financial_history_updater.VALUE_FIELDS
CANDLE_FIELDS = ("open", "high", "low", "close", "volume")
SH_CATEGORIES = tuple(_sl.CATEGORIES)
SH_STATUS = ("available", "unavailable")
SH_QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}
SH_QUALITY = ("ok", "flagged")
DIRECTORY_KIND = "stock_directory"      # stock_directory.KIND
# The stocks the shareholding collector may read: its default scope (shareholding_updater.collection_symbols with SHAREHOLDING_FOLLOW_UNIVERSE unset, which
# no workflow in the repository sets): the 10 development stocks plus a fixed five-stock test set. A drift test pins this tuple to the collector's own list.
SH_COLLECTOR_SCOPE = tuple(universe.TEST_SYMBOLS) + ("DOMS", "20MICRONS", "21STCENMGM", "360ONE", "3BBLACKBIO")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATE_FILES = {"fundamentals": "fundamentals_cache.json", "financials": "financials_cache.json", "quarterly": "quarterly_cache.json"}    # processing_state.KINDS
FIN_SECTIONS = ("income", "balance_sheet", "cash_flow")      # financials_updater.SECTIONS
FIN_SCHEMA = 2                    # financials_updater.SCHEMA_VERSION
STATEMENT_MAX_AGE_DAYS = 30       # financials_updater.STATEMENT_MAX_AGE_DAYS
QUARTERLY_SCHEMA = 1              # quarterly_results_updater.SCHEMA_VERSION
QUARTERLY_STEADY_DAYS = 30        # quarterly_results_updater.STEADY_EVERY_DAYS
BASES = ("consolidated", "standalone")      # quarterly_results_updater.BASES
QUARTERLY_PENDING_FROM_DAYS = 10  # quarterly_results_updater.PENDING_FROM_DAYS
QUARTERLY_PENDING_FOR_DAYS = 90   # quarterly_results_updater.PENDING_FOR_DAYS
QUARTERLY_PENDING_EVERY_DAYS = 2  # quarterly_results_updater.PENDING_EVERY_DAYS
MAX_RETRY_DAYS = 30               # quarterly_results_updater.MAX_RETRY_DAYS (and financials_updater.MAX_RETRY_DAYS)
MAX_PLAUSIBLE_FAILURES = 1000     # this report's own bound, not an updater rule: with the back-off capped at 30 days, 1000 consecutive failures is about 80 years of retries
MAX_INT_TEXT = 18                 # a whole number longer than this many characters in a state file is not parsed to an int (see _bounded_int)
OUTPUT_NAMES = ("coverage_report.json", "coverage_report.md")
OUTSIDE_LIST_LIMIT = 50


class ReportError(Exception):
    pass


# ---------------------------------------------------------------- small helpers
def _date(v):
    if not isinstance(v, str) or not DATE_RE.match(v):
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pct(n, d):
    return None if not d else round(100.0 * n / d, 4)


def _median(values):
    v = sorted(values)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else round((v[mid - 1] + v[mid]) / 2.0, 4)


def _stats(values):
    return {"min": min(values), "median": _median(values), "max": max(values), "count": len(values)} if values else {"min": None, "median": None, "max": None, "count": 0}


def _tally(counter, key):
    counter[key] = counter.get(key, 0) + 1


def _sorted_dict(d):
    return {str(k): d[k] for k in sorted(d, key=str)}


class _HugeInt:
    """Stands in for a whole number too long to be a real counter or value in a state file. It is not an int, so every numeric and date check refuses it, and it is never written to a report."""
    __slots__ = ()

    def __repr__(self):
        return "<huge integer>"


_HUGE = _HugeInt()


def _bounded_int(text):
    """json parse_int for state files: converting a very long digit string is the work a corrupt file could use to waste time and memory, so it is not done."""
    return int(text) if len(text) <= MAX_INT_TEXT else _HUGE


class _DupDict(dict):
    """A JSON object that remembers which keys appeared more than once (json.loads would silently keep only the last)."""
    dups = ()


def _dup_hook(pairs):
    out, seen, dups = _DupDict(), set(), []
    for k, v in pairs:
        if k in seen and k not in dups:
            dups.append(k)
        seen.add(k)
        out[k] = v
    out.dups = tuple(dups)
    return out


class Inputs:
    """Every file the run reads: size and sha256, so a report says exactly which bytes it describes. Files are opened read-only."""

    def __init__(self, root):
        self.root = Path(root)
        self.items = {}

    def label(self, path):
        try:
            return Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return "supplied:" + Path(path).name

    def read(self, path, register=True, label=None, pairs_hook=None, bound_ints=False):
        """(document, problem). problem is None, 'missing', 'unreadable' or 'invalid_json'. NaN/Infinity are refused. register=False: not listed one by one (the shards are listed as one hash)."""
        p = Path(path)
        if not p.is_file():
            return None, "missing"
        try:
            raw = p.read_bytes()
        except OSError:
            return None, "unreadable"
        if register:
            name = label or self.label(p)
            self.items[name] = {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        try:
            return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)),
                              **({"parse_int": _bounded_int} if bound_ints else {})), None
        except (UnicodeDecodeError, ValueError, RecursionError):      # RecursionError: valid JSON nested too deeply for the parser; unusable, like any other file that cannot be read as JSON
            return None, "invalid_json"

    def listing(self):
        return [self.items[k] for k in sorted(self.items)]


def _module(name, measurability, denom, counts, sources, **kw):
    """One module block. counts: valid / stale / missing / invalid / not_applicable / unverified (None = not assessed). The assessed counts add up to the denominator."""
    assessed = [v for v in counts.values() if v is not None]
    if sum(assessed) != denom:
        raise ReportError("internal error: %s counts add up to %d, not %d" % (name, sum(assessed), denom))
    valid, stale = counts["valid"], counts["stale"]
    covered = None if valid is None else valid + (stale or 0)
    return {
        "module": name, "measurability": measurability,
        "denominator": {"value": denom, "basis": kw.get("basis")},
        "counts": counts,
        "coverage_pct": _pct(covered, denom) if covered is not None else None,
        "coverage_pct_of_applicable": _pct(covered, denom - counts["not_applicable"]) if (covered is not None and counts["not_applicable"]) else None,
        "fresh_pct": _pct(valid, denom) if (valid is not None and stale is not None) else None,
        "source_paths": sources,
        "source_as_of": kw.get("source_as_of"),
        "freshness": kw.get("freshness") or {"status": "not_applicable"},
        "fields": kw.get("fields") or {},
        "facts": kw.get("facts") or {},
        "flags": kw.get("flags") or {},
        "warnings": _warnings(kw.get("flags") or {}, kw.get("warnings") or []),
        "missing_reasons": _sorted_dict(kw.get("missing_reasons") or {}),
        "invalid_reasons": _sorted_dict(kw.get("invalid_reasons") or {}),
        "unverified": kw.get("unverified") or [],
        "problems": kw.get("problems") or [],
    }


def _counts(valid=None, stale=None, missing=None, invalid=None, not_applicable=None, unverified=None):
    return {"valid": valid, "stale": stale, "missing": missing, "invalid": invalid, "not_applicable": not_applicable, "unverified": unverified}


def _not_measurable(name, denom, basis, sources, reasons, problems=None, facts=None):
    return _module(name, "not_measurable", denom, _counts(unverified=denom), sources, basis=basis, facts=facts,
                   unverified=[{"what": name, "count": denom, "reason": r} for r in reasons], problems=problems or [])


def _flag_block(items):
    """items: (symbol, stored ISIN, universe ISIN). A stored record is still counted, but its identifier differs from the universe's: reported by name, with
    both identifiers, so it can be looked at. Nothing is remapped and no cause is assumed."""
    rows = sorted(items)
    return {"count": len(rows), "symbols": [r[0] for r in rows][:OUTSIDE_LIST_LIMIT],
            "details": [{"symbol": r[0], "stored_isin": r[1], "universe_isin": r[2]} for r in rows[:OUTSIDE_LIST_LIMIT]]}


def _warnings(flags, extra):
    out = list(extra)
    for code, f in sorted(flags.items()):
        if f["count"]:
            out.append({"code": code, "count": f["count"], "symbols": f["symbols"],
                        "message": "%d stored record(s) carry an ISIN that differs from the universe's. They are counted as present, not remapped and not treated as invalid; "
                                   "the cause has not been established." % f["count"]})
    return out


def _fund_warnings(d):
    """The supplied fundamentals file is a snapshot: say so, and say how old it is against the reference date."""
    fa = _date(d.fund.get("as_of")) if isinstance(d.fund, dict) else None
    w = [{"code": "supplied_file_is_a_snapshot",
          "message": "This module measures the fundamentals file you supplied%s. It is not the saved state of any run and does not represent current pipeline health."
                     % ((", dated %s (%d day(s) before the reference date %s)" % (fa.isoformat(), (d.as_of - fa).days, d.as_of.isoformat())) if fa else
                        " (its own as_of date is missing or invalid)")}]
    if fa is None or (d.as_of - fa).days >= FUNDAMENTALS_MAX_AGE_DAYS:
        w.append({"code": "supplied_file_older_than_the_weekly_threshold",
                  "message": "Rows fetched %d or more days before the reference date are counted stale because of the file's age; stale here does not mean a refresh failed."
                             % FUNDAMENTALS_MAX_AGE_DAYS})
    return w


def _isin_differs(entry_isin, universe_isin):
    return entry_isin is not None and universe_isin is not None and entry_isin != universe_isin


def _cell_counter(values):
    """present / null / zero over a list of raw cell values. Zero is present AND counted as zero; null is never zero."""
    present = sum(1 for v in values if v is not None)
    return {"present": present, "null": len(values) - present, "zero": sum(1 for v in values if _isnum(v) and v == 0), "of": len(values)}


# ---------------------------------------------------------------- loading
class Data:
    def __init__(self, data_dir, fundamentals_path, as_of, state_dir=None):
        self.dir = Path(data_dir)
        self.as_of = as_of
        self.inputs = Inputs(self.dir)
        self.universe_doc = None
        self.symbols = []
        self.isin = {}
        self.directory, self.directory_problem = self.inputs.read(self.dir / "stock_directory.json")
        self.fh, self.fh_problem = self.inputs.read(self.dir / "financial_history_ledger.json")
        self.sh, self.sh_problem = self.inputs.read(self.dir / "shareholding_ledger.json")
        self.hist, self.hist_problem = self.inputs.read(self.dir / "historical" / "historical.json")
        self.attempts, self.attempts_problem = self.inputs.read(self.dir / "historical" / "attempts.json")
        self.fund, self.fund_problem = (None, "not_supplied")
        self.fund_label = None
        if fundamentals_path is not None:
            self.fund_label = "supplied:fundamentals_file"      # a fixed label: the local file name is never written into a report
            self.fund, self.fund_problem = self.inputs.read(fundamentals_path, label=self.fund_label)
        self.shard_names = []
        self.shard_docs = {}
        self.shard_hash = None
        self.state_dir = None if state_dir is None else Path(state_dir)
        self.state = {}                      # kind -> (document or None, problem or None)
        if self.state_dir is not None:
            for kind, fname in STATE_FILES.items():
                doc, prob = self.inputs.read(self.state_dir / fname, label="state:" + fname, pairs_hook=_dup_hook, bound_ints=True)
                self.state[kind] = (None, prob) if prob else _state_schema(kind, doc)

    def load_universe(self):
        doc, prob = self.inputs.read(self.dir / "universe.json")
        if prob:
            raise ReportError("universe.json is %s" % prob)
        bad = universe.check_doc(doc)
        if bad:
            raise ReportError("universe.json is invalid (%d problem(s)), for example: %s" % (len(bad), "; ".join(bad[:3])))
        self.universe_doc = doc
        self.symbols = [x["symbol"] for x in doc["symbols"]]
        self.isin = {x["symbol"]: x.get("isin") for x in doc["symbols"]}

    def list_shards(self):
        d = self.dir / "historical" / "shards"
        h = hashlib.sha256()
        names = []
        if d.is_dir():
            for p in sorted(d.iterdir(), key=lambda x: x.name):
                if p.is_file() and p.name.endswith(".json"):
                    names.append(p.name[:-5])
                    h.update(p.name.encode("utf-8") + b"\0" + hashlib.sha256(p.read_bytes()).hexdigest().encode("ascii") + b"\n")
        self.shard_names = names
        self.shard_hash = h.hexdigest()

    def shard(self, sym):
        """(document, problem) of historical/shards/<SYMBOL>.json, read once."""
        if sym not in self.shard_docs:
            self.shard_docs[sym] = self.inputs.read(self.dir / "historical" / "shards" / (sym + ".json"), register=False)
        return self.shard_docs[sym]


def _basis(scope):
    return "first_500_of_universe" if scope == "first_500" else "all_universe"


# ---------------------------------------------------------------- modules
def _directory_state(d):
    """What stock_directory.json lets us do, decided BEFORE any lookup table is built.
    -> {"usable": False, "reason": ...} or {"usable": True, "entries": {symbol: isin} (first entry per symbol), "dup_symbols": [...], "dup_isins": [...],
    "extra_symbol_entries": n, "extra_isin_entries": n, "malformed": n}. A symbol or ISIN that appears more than once is listed, never resolved by letting
    a later entry overwrite an earlier one."""
    if hasattr(d, "_directory_state"):
        return d._directory_state
    doc = d.directory
    if d.directory_problem:
        reason = d.directory_problem                      # missing / unreadable / invalid_json
    elif not isinstance(doc, dict):
        reason = "not_an_object"
    elif doc.get("kind") != DIRECTORY_KIND:
        reason = "wrong_kind"
    elif not isinstance(doc.get("symbols"), list):
        reason = "no_symbols_list"
    elif not doc["symbols"]:
        reason = "empty_symbols_list"
    else:
        reason = None
    if reason:
        st = {"usable": False, "reason": reason}
    else:
        entries, sym_n, isin_n, malformed = {}, {}, {}, 0
        for x in doc["symbols"]:
            if not isinstance(x, dict) or not isinstance(x.get("symbol"), str) or (x.get("isin") is not None and not isinstance(x.get("isin"), str)):
                malformed += 1
                continue
            sym_n[x["symbol"]] = sym_n.get(x["symbol"], 0) + 1
            if x.get("isin") is not None:
                isin_n[x["isin"]] = isin_n.get(x["isin"], 0) + 1
            entries.setdefault(x["symbol"], x.get("isin"))
        st = {"usable": True, "entries": entries, "malformed": malformed,
              "dup_symbols": sorted(k for k, v in sym_n.items() if v > 1), "dup_isins": sorted(k for k, v in isin_n.items() if v > 1),
              "extra_symbol_entries": sum(v - 1 for v in sym_n.values() if v > 1), "extra_isin_entries": sum(v - 1 for v in isin_n.values() if v > 1)}
    d._directory_state = st
    return st


def mod_directory(d, syms, scope):
    """Two different things are counted and never mixed up:
      * identifiers that are well formed in universe.json alone (reported as a fact, labelled universe-only), and
      * identifiers CROSS-CHECKED against stock_directory.json (these are the 'valid' count).
    When the directory cannot be used, nothing is cross-checked: 'valid' is not assessed (null), the well-formed identifiers are counted as unverified,
    and the module reports no coverage percentage, so a missing directory can never show as 100%."""
    n = len(syms)
    seen_sym, seen_isin, dup_sym, dup_isin = set(), set(), 0, 0
    for s in syms:
        dup_sym += s in seen_sym
        seen_sym.add(s)
        i = d.isin.get(s)
        if i is not None:
            dup_isin += i in seen_isin
            seen_isin.add(i)
    st = _directory_state(d)
    problems, warnings = [], []
    if st["usable"]:
        pass
    elif d.directory_problem:
        problems.append("stock_directory.json is " + d.directory_problem)
    else:
        problems.append("stock_directory.json is not usable (%s)" % st["reason"])
    valid = invalid = missing = well_formed = checked = 0
    inv, mis = {}, {}
    dup_s, dup_i = set(st.get("dup_symbols") or []), set(st.get("dup_isins") or [])
    for s in syms:
        i = d.isin.get(s)
        if i is None:
            missing += 1
            _tally(mis, "no_isin")
        elif not universe.ISIN_RE.match(i) or not universe.SYMBOL_RE.match(s):
            invalid += 1
            _tally(inv, "bad_symbol_or_isin")
        else:
            well_formed += 1
            if not st["usable"]:
                continue
            checked += 1
            if s in dup_s:
                invalid += 1
                _tally(inv, "duplicate_symbol_in_directory")
            elif s not in st["entries"]:
                invalid += 1
                _tally(inv, "not_in_directory")
            elif st["entries"][s] != i:
                invalid += 1
                _tally(inv, "isin_differs_from_directory")
            elif i in dup_i:
                invalid += 1
                _tally(inv, "duplicate_isin_in_directory")
            else:
                valid += 1
    facts = {"duplicate_symbols": dup_sym, "duplicate_isins": dup_isin,
             "universe_identifier_check": {"well_formed": well_formed, "denominator": n, "pct": _pct(well_formed, n),
                                           "note": "checked against universe.json alone; this is not a directory cross-check"},
             "directory_cross_check": {"performed": st["usable"], "reason": st.get("reason"),
                                       "stocks_cross_checked": checked}}
    unverified = []
    if st["usable"]:
        dd = d.directory
        facts["directory_built_on"] = dd.get("built_on")
        facts["directory_counts"] = _sorted_dict(dd.get("counts") or {}) if isinstance(dd.get("counts"), dict) else None
        cl = dd.get("classification")
        facts["directory_classification_counts"] = _sorted_dict(cl.get("counts") or {}) if isinstance(cl, dict) and isinstance(cl.get("counts"), dict) else None
        keys = set()
        for x in dd["symbols"]:
            if isinstance(x, dict):
                keys |= set(x)
        facts["directory_entry_keys"] = sorted(keys)
        facts["directory_duplicates"] = {
            "duplicate_symbols": {"distinct": len(st["dup_symbols"]), "extra_entries": st["extra_symbol_entries"], "symbols": st["dup_symbols"][:OUTSIDE_LIST_LIMIT]},
            "duplicate_isins": {"distinct": len(st["dup_isins"]), "extra_entries": st["extra_isin_entries"], "isins": st["dup_isins"][:OUTSIDE_LIST_LIMIT]},
            "malformed_entries": st["malformed"]}
        if st["dup_symbols"]:
            warnings.append({"code": "duplicate_symbols_in_directory", "count": len(st["dup_symbols"]), "identifiers": st["dup_symbols"][:OUTSIDE_LIST_LIMIT],
                             "message": "stock_directory.json lists %d symbol(s) more than once (%d extra entries). A universe stock with such a symbol cannot be cross-checked "
                                        "unambiguously and is counted invalid; no entry was chosen over another." % (len(st["dup_symbols"]), st["extra_symbol_entries"])})
        if st["dup_isins"]:
            warnings.append({"code": "duplicate_isins_in_directory", "count": len(st["dup_isins"]), "identifiers": st["dup_isins"][:OUTSIDE_LIST_LIMIT],
                             "message": "stock_directory.json lists %d ISIN(s) more than once (%d extra entries). A universe stock with such an ISIN cannot be cross-checked "
                                        "unambiguously and is counted invalid." % (len(st["dup_isins"]), st["extra_isin_entries"])})
        if st["malformed"]:
            warnings.append({"code": "malformed_entries_in_directory", "count": st["malformed"],
                             "message": "%d directory entr(y/ies) have no text symbol or a non-text ISIN and were not used." % st["malformed"]})
        if "series" not in keys:
            unverified.append({"what": "EQ/BE split of the published stocks", "count": n,
                               "reason": "the directory entries carry no series field, so the published stocks cannot be split between EQ and BE offline"})
    else:
        unverified.append({"what": "identifier cross-check against stock_directory.json", "count": well_formed,
                           "reason": "stock_directory.json cannot be used (%s), so no identifier was checked against it; the well-formed identifiers in universe.json are NOT "
                                     "reported as verified" % st["reason"]})
    return _module("directory", "offline" if st["usable"] else "partial", n,
                   _counts(valid=valid if st["usable"] else None, missing=missing, invalid=invalid, unverified=None if st["usable"] else well_formed),
                   ["universe.json", "stock_directory.json"], basis=_basis(scope), source_as_of=d.universe_doc.get("as_of"),
                   freshness={"status": "not_assessed", "universe_as_of": d.universe_doc.get("as_of"), "directory_built_on": facts.get("directory_built_on")},
                   facts=facts, missing_reasons=mis, invalid_reasons=inv, unverified=unverified, problems=problems, warnings=warnings)


def fundamentals_rows(d):
    """(rows by symbol (first row), duplicate symbol set, errors by symbol, malformed row count)"""
    rows, dups, errs, bad = {}, set(), {}, 0
    for r in d.fund.get("stocks") or []:
        if not isinstance(r, dict) or not isinstance(r.get("symbol"), str):
            bad += 1
        elif r["symbol"] in rows:
            dups.add(r["symbol"])
        else:
            rows[r["symbol"]] = r
    for e in d.fund.get("errors") or []:
        if isinstance(e, dict) and isinstance(e.get("symbol"), str) and e["symbol"] not in errs:
            errs[e["symbol"]] = e.get("error")
    return rows, dups, errs, bad


def _fund_usable(d):
    return isinstance(d.fund, dict) and isinstance(d.fund.get("stocks"), list)


def mod_fundamentals(d, syms, scope):
    n = len(syms)
    if not _fund_usable(d):
        why = "no fundamentals file was supplied; the fundamentals state is saved only in the GitHub Actions cache (data/fundamentals_cache.json, out/fundamentals.json), which this tool does not read" \
            if d.fund_problem == "not_supplied" else "the supplied fundamentals file is " + str(d.fund_problem or "not shaped like out/fundamentals.json")
        return _not_measurable("fundamentals", n, _basis(scope), [d.fund_label] if d.fund_label else [], [why])
    rows, dups, errs, bad = fundamentals_rows(d)
    valid = stale = invalid = missing = 0
    inv, mis = {}, {}
    used, dates, isin_flag = [], [], []
    ages = {"0-6": 0, "7-13": 0, "14-29": 0, "30+": 0}
    for s in syms:
        r = rows.get(s)
        if s in dups:
            invalid += 1
            _tally(inv, "duplicate_rows")
        elif r is None:
            missing += 1
            e = errs.get(s)
            _tally(mis, "recorded_not_in_upstox_list" if e == NOT_FOUND_ERROR else "recorded_other_error" if e else "no_record")
        else:
            f = _date(r.get("fetched"))
            if f is None:
                invalid += 1
                _tally(inv, "bad_fetched_date")
            elif f > d.as_of:
                invalid += 1
                _tally(inv, "fetched_after_as_of")
            elif any(r.get(m) is not None and not _isnum(r.get(m)) for m in FUNDAMENTALS_METRICS + GROWTH_FIELDS):
                invalid += 1
                _tally(inv, "non_numeric_metric")
            else:
                age = (d.as_of - f).days
                dates.append(f.isoformat())
                ages["0-6" if age < 7 else "7-13" if age < 14 else "14-29" if age < 30 else "30+"] += 1
                if _isin_differs(r.get("isin"), d.isin.get(s)):
                    isin_flag.append((s, r.get("isin"), d.isin.get(s)))
                if age >= FUNDAMENTALS_MAX_AGE_DAYS:
                    stale += 1
                else:
                    valid += 1
                used.append(r)
    fields = {}
    for m in FUNDAMENTALS_METRICS:
        c = _cell_counter([r.get(m) for r in used])
        c["pct"] = _pct(c["present"], c["of"])
        c["pct_of_denominator"] = _pct(c["present"], n)
        fields[m] = c
    return _module("fundamentals", "partial", n, _counts(valid=valid, stale=stale, missing=missing, invalid=invalid),
                   [d.fund_label], basis=_basis(scope), source_as_of=d.fund.get("as_of"),
                   freshness={"status": "configured", "threshold_days": FUNDAMENTALS_MAX_AGE_DAYS, "threshold_source": "fundamentals_updater.RATIOS_MAX_AGE_DAYS",
                              "oldest_fetched": min(dates) if dates else None, "newest_fetched": max(dates) if dates else None, "age_days_buckets": ages},
                   fields=fields, facts={"rows_in_file": len(rows), "duplicate_symbols_in_file": len(dups), "malformed_rows_in_file": bad,
                                         "supplied_file_as_of": d.fund.get("as_of"), "reference_as_of": d.as_of.isoformat(),
                                         "supplied_file_age_days": (d.as_of - _date(d.fund.get("as_of"))).days if _date(d.fund.get("as_of")) else None},
                   flags={"isin_differs_from_universe": _flag_block(isin_flag)}, warnings=_fund_warnings(d), missing_reasons=mis, invalid_reasons=inv,
                   unverified=[{"what": "current fundamentals pipeline health", "count": n,
                                "reason": "only a supplied snapshot was read; the saved state of the daily and batch runs lives in the GitHub Actions cache, which is not accessible here"}])


def mod_growth(d, syms, scope):
    n = len(syms)
    ledger_growth_keys = None
    if isinstance(d.fh, dict) and isinstance(d.fh.get("stocks"), dict):
        keys = set()
        for s in syms:
            e = d.fh["stocks"].get(s)
            for y in (e.get("years") if isinstance(e, dict) and isinstance(e.get("years"), list) else []):
                if isinstance(y, dict) and isinstance(y.get("values"), dict):
                    keys |= {k for k in y["values"] if "growth" in str(k).lower()}
        ledger_growth_keys = sorted(keys)
    facts = {"growth_fields_stored_in_financial_history_ledger": ledger_growth_keys,
             "derivation": "not performed: this report does not compute growth"}
    if not _fund_usable(d):
        return _not_measurable("growth", n, _basis(scope), [], ["growth fields live in the fundamentals file, which was not supplied (or is unreadable)"], facts=facts)
    rows, dups, _, _ = fundamentals_rows(d)
    valid = missing = 0
    mis = {}
    cells = {g: [] for g in GROWTH_FIELDS}
    for s in syms:
        r = rows.get(s)
        if r is None or s in dups:
            missing += 1
            _tally(mis, "no_usable_fundamentals_row")
            continue
        for g in GROWTH_FIELDS:
            cells[g].append(r.get(g))
        if all(_isnum(r.get(g)) for g in GROWTH_FIELDS):
            valid += 1
        else:
            missing += 1
            _tally(mis, "growth_field_null")
    fields = {}
    for g in GROWTH_FIELDS:
        c = _cell_counter(cells[g])
        c["pct"] = _pct(c["present"], c["of"])
        c["pct_of_denominator"] = _pct(c["present"], n)
        fields[g] = c
    return _module("growth", "partial", n, _counts(valid=valid, missing=missing), [d.fund_label], basis=_basis(scope), source_as_of=d.fund.get("as_of"),
                   freshness={"status": "not_assessed"}, fields=fields, facts=facts, missing_reasons=mis, warnings=_fund_warnings(d)[:1])


def _fh_entry(sym, e, isin):
    """(years (list of valid year dicts) or None, problem). Structural checks only."""
    if not isinstance(e, dict):
        return None, "entry_not_an_object"
    if e.get("symbol") not in (None, sym):
        return None, "symbol_differs_from_key"
    ys = e.get("years")
    if not isinstance(ys, list):
        return None, "years_not_a_list"
    seen = set()
    for y in ys:
        if not isinstance(y, dict) or not isinstance(y.get("fy"), int) or isinstance(y.get("fy"), bool) or not isinstance(y.get("values"), dict):
            return None, "bad_year_record"
        key = (y["fy"], y.get("basis"))              # the ledger keys a year by (fiscal year, basis): one company can hold FY2025 consolidated
        if key in seen:                              # for income and FY2025 standalone for cash flow; the same pair twice is a real duplicate
            return None, "duplicate_fiscal_year"
        seen.add(key)
        if y.get("period_end") is not None and _date(y.get("period_end")) is None:
            return None, "bad_period_end_date"
        if any(v is not None and not _isnum(v) for v in y["values"].values()):
            return None, "non_numeric_value"
    return ys, None


def mod_financial_history(d, syms, scope):
    n = len(syms)
    src = ["financial_history_ledger.json"]
    if not (isinstance(d.fh, dict) and isinstance(d.fh.get("stocks"), dict)):
        return _not_measurable("financial_history", n, _basis(scope), src, ["financial_history_ledger.json is " + str(d.fh_problem or "not shaped like a ledger")])
    valid = missing = invalid = 0
    mis, inv = {}, {}
    entries, isin_flag = [], []
    for s in syms:
        e = d.fh["stocks"].get(s)
        if e is None:
            missing += 1
            _tally(mis, "not_in_ledger")
            continue
        ys, prob = _fh_entry(s, e, d.isin.get(s))
        if isinstance(e, dict) and _isin_differs(e.get("isin"), d.isin.get(s)):
            isin_flag.append((s, e.get("isin"), d.isin.get(s)))
        if prob:
            invalid += 1
            _tally(inv, prob)
        elif not ys:
            missing += 1
            _tally(mis, "entry_without_years")
        else:
            valid += 1
            entries.append((s, e, ys))
    fields = {}
    for f in FH_VALUE_FIELDS:
        any_year = latest_present = latest_zero = any_zero = 0
        for _, _, ys in entries:
            vals = [y["values"].get(f) for y in ys]
            any_year += any(v is not None for v in vals)
            any_zero += any(_isnum(v) and v == 0 for v in vals)
            top = max(y["fy"] for y in ys)
            lv = next((y["values"].get(f) for y in ys if y["fy"] == top and y["values"].get(f) is not None), None)   # either basis of the newest year
            latest_present += lv is not None
            latest_zero += _isnum(lv) and lv == 0
        k = len(entries)
        fields[f] = {"of": k, "stocks_with_any_year_present": any_year, "stocks_with_latest_year_present": latest_present,
                     "stocks_with_latest_year_null": k - latest_present, "stocks_with_latest_year_zero": latest_zero, "stocks_with_any_zero_year": any_zero,
                     "pct_any_year": _pct(any_year, k), "pct_latest_year": _pct(latest_present, k), "pct_latest_year_of_denominator": _pct(latest_present, n)}
    yrs, newest, ver, basis, layout, fetched, sect_err = {}, {}, {}, {}, {}, [], {}
    gaps = 0
    for s, e, ys in entries:
        _tally(yrs, len({y["fy"] for y in ys}))      # distinct fiscal years, not (year, basis) records
        _tally(newest, max(y["fy"] for y in ys))
        _tally(layout, e.get("statement_layout") if isinstance(e.get("statement_layout"), str) else "unknown")
        gaps += bool(e.get("gaps"))
        for y in ys:
            _tally(ver, (y.get("verification") or {}).get("status") if isinstance(y.get("verification"), dict) else "unknown")
            _tally(basis, y.get("basis") if isinstance(y.get("basis"), str) else "unknown")
        fs = e.get("fetch_state")
        if isinstance(fs, dict):
            for name, sec in fs.items():
                if isinstance(sec, dict):
                    f = _date(sec.get("fetched"))
                    if f:
                        fetched.append(f.isoformat())
                    if sec.get("error"):
                        _tally(sect_err, str(name))
    dates = sorted(fetched)
    return _module("financial_history", "offline", n, _counts(valid=valid, stale=None, missing=missing, invalid=invalid), src, basis=_basis(scope),
                   source_as_of=d.fh.get("updated"),
                   freshness={"status": THRESHOLD_NOT_CONFIGURED, "oldest_section_fetched": dates[0] if dates else None, "newest_section_fetched": dates[-1] if dates else None,
                              "section_fetched_date_counts": _sorted_dict({k: dates.count(k) for k in sorted(set(dates))}), "newest_fiscal_year_counts": _sorted_dict(newest)},
                   fields=fields,
                   facts={"years_per_stock": _sorted_dict(yrs), "verification_status_counts": _sorted_dict(ver), "basis_counts": _sorted_dict(basis),
                          "statement_layout_counts": _sorted_dict(layout), "stocks_with_recorded_gaps": gaps, "sections_with_a_recorded_error": _sorted_dict(sect_err)},
                   flags={"isin_differs_from_universe": _flag_block(isin_flag)}, missing_reasons=mis, invalid_reasons=inv)


def _candles(doc, sym):
    """(candle list, problem) from a shard or the single historical file's stock entry."""
    st = (doc.get("stocks") or {}).get(sym) if isinstance(doc, dict) and isinstance(doc.get("stocks"), dict) else None
    if not isinstance(st, dict) or not isinstance(st.get("candles"), list):
        return None, "no_candle_list"
    return st["candles"], None


def _candle_problem(rows):
    prev = None
    for c in rows:
        if not isinstance(c, dict) or _date(c.get("date")) is None:
            return "bad_candle_date"
        if prev is not None and c["date"] <= prev:
            return "dates_not_strictly_ascending"
        prev = c["date"]
        if not all(_isnum(c.get(k)) for k in ("open", "high", "low", "close")):
            return "non_numeric_price"
        if c["high"] < c["low"] or min(c["open"], c["high"], c["low"], c["close"]) <= 0:
            return "inconsistent_or_non_positive_price"
        if c.get("volume") is not None and not _isnum(c["volume"]):
            return "non_numeric_volume"
    return None


def candle_facts(d, syms):
    """Measured once per scope: used by the candles module and the indicators module."""
    shard_set = set(d.shard_names)
    single = d.hist.get("stocks") if isinstance(d.hist, dict) and isinstance(d.hist.get("stocks"), dict) else {}
    out = {"valid": 0, "missing": 0, "invalid": 0, "mis": {}, "inv": {}, "rows": [], "age": [], "newest": [], "sources": {"shard": 0, "single_file": 0},
           "cells": {k: [] for k in CANDLE_FIELDS}, "vol_all": 0, "vol_some_null": 0, "vol_none": 0}
    for s in syms:
        doc, src = None, None
        if s in shard_set:
            doc, prob = d.shard(s)
            src = "shard"
            if prob:
                out["invalid"] += 1
                _tally(out["inv"], "shard_" + prob)
                continue
        elif s in single:
            doc, src = {"stocks": {s: single[s]}}, "single_file"
        else:
            out["missing"] += 1
            _tally(out["mis"], "no_shard_or_entry")
            continue
        rows, prob = _candles(doc, s)
        if prob:
            out["invalid"] += 1
            _tally(out["inv"], prob)
            continue
        if not rows:
            out["missing"] += 1
            _tally(out["mis"], "empty_candle_list")
            continue
        prob = _candle_problem(rows)
        if prob:
            out["invalid"] += 1
            _tally(out["inv"], prob)
            continue
        newest = _date(rows[-1]["date"])
        if newest > d.as_of:
            out["invalid"] += 1
            _tally(out["inv"], "newest_candle_after_as_of")
            continue
        out["valid"] += 1
        out["sources"][src] += 1
        out["rows"].append(len(rows))
        out["age"].append((d.as_of - newest).days)
        out["newest"].append(newest.isoformat())
        vols = [c.get("volume") for c in rows]
        nn = sum(1 for v in vols if v is not None)
        out["vol_all"] += nn == len(vols)
        out["vol_none"] += nn == 0
        out["vol_some_null"] += 0 < nn < len(vols)
        for k in CANDLE_FIELDS:
            out["cells"][k].extend(c.get(k) for c in rows)
    return out


def mod_historical(d, syms, scope, cf):
    n = len(syms)
    src = ["historical/shards/", "historical/historical.json"]
    if not d.shard_names and not (isinstance(d.hist, dict) and isinstance(d.hist.get("stocks"), dict)):
        return _not_measurable("historical_candles", n, _basis(scope), src, ["neither historical/shards/ nor historical/historical.json holds any stock"])
    fields = {}
    for k in CANDLE_FIELDS:
        c = _cell_counter(cf["cells"][k])
        c["pct"] = _pct(c["present"], c["of"])
        fields[k] = c
    failed = d.attempts.get("failed") if isinstance(d.attempts, dict) and isinstance(d.attempts.get("failed"), dict) else None
    return _module("historical_candles", "offline", n, _counts(valid=cf["valid"], stale=None, missing=cf["missing"], invalid=cf["invalid"]), src, basis=_basis(scope),
                   source_as_of=d.hist.get("updated") if isinstance(d.hist, dict) else None,
                   freshness={"status": THRESHOLD_NOT_CONFIGURED, "newest_candle_age_days": _stats(cf["age"]),
                              "oldest_newest_candle_date": min(cf["newest"]) if cf["newest"] else None, "latest_newest_candle_date": max(cf["newest"]) if cf["newest"] else None},
                   fields=fields,
                   facts={"candle_rows_per_stock": _stats(cf["rows"]), "stocks_by_source": cf["sources"],
                          "stocks_with_volume_on_every_row": cf["vol_all"], "stocks_with_some_null_volume_rows": cf["vol_some_null"], "stocks_with_no_volume": cf["vol_none"],
                          "recorded_failed_attempts_in_attempts_file": len(failed) if failed is not None else None},
                   missing_reasons=cf["mis"], invalid_reasons=cf["inv"])


def mod_indicators(d, syms, scope, cf):
    n = len(syms)
    return _not_measurable("technical_indicators", n, _basis(scope), ["historical/shards/"],
                           ["indicators are not stored: the browser computes them from the daily candles, so they can only be as available as the candles",
                            "no readiness threshold (candle rows needed) has been agreed, so no stock is labelled ready or not ready"],
                           facts={"readiness": THRESHOLD_NOT_CONFIGURED, "candle_rows_per_stock": _stats(cf["rows"])})


def mod_sector(d, syms, scope):
    n = len(syms)
    fh_ok = isinstance(d.fh, dict) and isinstance(d.fh.get("stocks"), dict)
    fund_ok = _fund_usable(d)
    if not (fh_ok or fund_ok):
        return _not_measurable("sector", n, _basis(scope), [], ["neither the financial-history ledger nor a fundamentals file is available"])
    rows = fundamentals_rows(d)[0] if fund_ok else {}
    valid = 0
    layout = {}
    for s in syms:
        e = d.fh["stocks"].get(s) if fh_ok else None
        has = (isinstance(e, dict) and isinstance(e.get("sector"), str) and bool(e["sector"].strip())) or \
              (isinstance(rows.get(s), dict) and isinstance(rows[s].get("sector"), str) and bool(rows[s]["sector"].strip()))
        valid += has
        if fh_ok:
            _tally(layout, (e.get("statement_layout") if isinstance(e, dict) and isinstance(e.get("statement_layout"), str) else "not_in_ledger"))
    sources = (["financial_history_ledger.json"] if fh_ok else []) + ([d.fund_label] if fund_ok else [])
    return _module("sector", "partial", n, _counts(valid=valid, missing=n - valid), sources, basis=_basis(scope), freshness={"status": "not_assessed"},
                   facts={"statement_layout_counts": _sorted_dict(layout) if fh_ok else None, "sector_names": "not listed in this report"},
                   missing_reasons={"no_sector_value_in_the_files_read": n - valid},
                   unverified=[{"what": "why a stock has no sector value", "count": n - valid,
                                "reason": "the files do not say whether the value was withheld, not fetched or not provided by the source"}])


def _sh_entry(sym, e, isin):
    """(quarters list or None, problem). Structure only; values are never read out."""
    if not isinstance(e, dict) or not isinstance(e.get("quarters"), list):
        return None, "quarters_not_a_list"
    seen = set()
    for q in e["quarters"]:
        if not isinstance(q, dict):
            return None, "quarter_not_an_object"
        qe = _date(q.get("quarter_end"))
        if qe is None or (qe.month, qe.day) not in SH_QUARTER_ENDS:
            return None, "quarter_end_not_a_quarter_end"
        if q["quarter_end"] in seen:
            return None, "duplicate_quarter"
        seen.add(q["quarter_end"])
        if q.get("status") not in SH_STATUS:
            return None, "bad_status"
        v = q.get("values")
        if not isinstance(v, dict) or set(v) != set(SH_CATEGORIES):
            return None, "values_do_not_hold_exactly_the_categories"
        if any(x is not None and (not _isnum(x) or not 0 <= x <= 100.5) for x in v.values()):
            return None, "category_value_out_of_range"
        if q["status"] == "unavailable" and any(x is not None for x in v.values()):
            return None, "unavailable_quarter_holds_values"
        if q["status"] == "available" and not (isinstance(q.get("quality"), dict) and q["quality"].get("status") in SH_QUALITY):
            return None, "bad_quality_status"
    return e["quarters"], None


def mod_shareholding(d, syms, scope):
    n = len(syms)
    src = ["shareholding_ledger.json"]
    if not (isinstance(d.sh, dict) and isinstance(d.sh.get("stocks"), dict)):
        return _not_measurable("shareholding", n, _basis(scope), src, ["shareholding_ledger.json is " + str(d.sh_problem or "not shaped like a ledger")])
    valid = missing = invalid = not_applicable = 0
    mis, inv = {}, {}
    ages, newest, qcount, present_cats, quality, isin_flag = [], {}, {}, {}, {}, []
    coll = set(SH_COLLECTOR_SCOPE)
    in_scope = sorted(s for s in syms if s in coll)
    in_scope_missing = []
    for s in syms:
        e = d.sh["stocks"].get(s)
        if e is None:
            if s in coll:
                missing += 1
                in_scope_missing.append(s)
                _tally(mis, "in_collector_scope_but_not_in_ledger")
            else:
                not_applicable += 1
            continue
        qs, prob = _sh_entry(s, e, d.isin.get(s))
        if isinstance(e, dict) and _isin_differs(e.get("isin"), d.isin.get(s)):
            isin_flag.append((s, e.get("isin"), d.isin.get(s)))
        if prob:
            invalid += 1
            _tally(inv, prob)
            continue
        avail = [q for q in qs if q["status"] == "available"]
        if not avail:
            missing += 1
            _tally(mis, "no_available_quarter")
            continue
        valid += 1
        latest = max(avail, key=lambda q: q["quarter_end"])
        _tally(newest, latest["quarter_end"])
        _tally(qcount, len(avail))
        _tally(present_cats, sum(1 for x in latest["values"].values() if x is not None))
        _tally(quality, latest["quality"]["status"])
        ages.append((d.as_of - _date(latest["quarter_end"])).days)
    outside_ledger = sorted(s for s in syms if s not in coll and s in d.sh["stocks"])
    return _module("shareholding", "offline", n, _counts(valid=valid, stale=None, missing=missing, invalid=invalid, not_applicable=not_applicable), src, basis=_basis(scope),
                   source_as_of=None,
                   freshness={"status": THRESHOLD_NOT_CONFIGURED, "newest_available_quarter_counts": _sorted_dict(newest), "newest_quarter_age_days": _stats(ages)},
                   facts={"available_quarters_per_stock": _sorted_dict(qcount), "newest_quarter_categories_present_counts": _sorted_dict(present_cats),
                          "newest_quarter_quality_status_counts": _sorted_dict(quality), "categories_checked": list(SH_CATEGORIES),
                          "values": "no shareholding percentage or holder appears in this report",
                          "collector_scope": {"symbols": list(SH_COLLECTOR_SCOPE), "size": len(SH_COLLECTOR_SCOPE), "in_this_scope": len(in_scope),
                                              "evidence": "shareholding_updater.collection_symbols default; no workflow in the repository sets SHAREHOLDING_FOLLOW_UNIVERSE",
                                              "not_applicable_means": "the stock is outside the collector's fixed scope by design (expansion needs a separate approval), not a failed or missing record"},
                          "in_scope_symbols_missing_from_ledger": in_scope_missing, "ledger_stocks_outside_collector_scope": outside_ledger[:OUTSIDE_LIST_LIMIT]},
                   flags={"isin_differs_from_universe": _flag_block(isin_flag)}, missing_reasons=mis, invalid_reasons=inv,
                   unverified=([{"what": "why an in-scope stock is absent from the ledger", "count": missing,
                                 "reason": "the ledger records no failure reason, so a failed fetch, a missing filing and a not-yet-processed stock cannot be told apart offline"}] if missing else []))


def mod_cache_only(name, d, syms, scope, paths):
    return _not_measurable(name, len(syms), _basis(scope), [],
                           ["saved only in the GitHub Actions cache (%s), which is not accessible to this tool and was not supplied" % ", ".join(paths)])


def mod_detail(d, syms, scope, cf, fh_mod, sh_mod):
    n = len(syms)
    return _module("stock_detail_data", "partial", n, _counts(unverified=n), ["historical/shards/", "financial_history_ledger.json", "shareholding_ledger.json"], basis=_basis(scope),
                   facts={"expected_per_stock_files_from_stored_data": {"historical_shards_or_single_file_entries": cf["valid"], "financial_history_entries": fh_mod["counts"]["valid"],
                                                                         "shareholding_entries": sh_mod["counts"]["valid"]}},
                   unverified=[{"what": "what the public site serves per stock", "count": n,
                                "reason": "the published pages, the out/by_symbol shards generated at build time and the Actions cache are not accessible; only the stored inputs above are measured"}])


# ---------------------------------------------------------------- saved state (--state-dir)
def _state_schema(kind, doc):
    """(document, None) when the file has the shape its updater writes, else (None, 'unsupported_schema: ...'). Structure only; no value is looked at."""
    if not isinstance(doc, dict):
        return None, "unsupported_schema: not a JSON object"
    if "stocks" in getattr(doc, "dups", ()):
        return None, "unsupported_schema: the stocks key appears more than once"
    if not isinstance(doc.get("stocks"), dict):
        return None, "unsupported_schema: no stocks object"
    if kind == "quarterly":
        if doc.get("kind") is not None and doc.get("kind") != "quarterly_cache":
            return None, "unsupported_schema: wrong kind"
        v = doc.get("schema_version")
        if v is not None and (isinstance(v, bool) or v != QUARTERLY_SCHEMA):
            return None, "unsupported_schema: schema_version is not %d" % QUARTERLY_SCHEMA
    return doc, None


def _state_open(name, kind, d, syms, scope):
    """-> (module, None) when the file cannot be used (every stock unverified, never zero coverage), else (None, (stocks, duplicate keys))."""
    fname = STATE_FILES[kind]
    doc, prob = d.state[kind]
    if doc is None:
        problems = ["%s is %s" % (fname, prob)]
        if kind == "financials" and (d.state_dir / "financials.json").is_file():
            problems.append("a file named financials.json is in the state folder; it is the published output, not the saved state, and was not read (the state is financials_cache.json)")
        return _not_measurable(name, len(syms), _basis(scope), [fname], ["%s cannot be used (%s), so %s is NOT measured; no coverage is reported" % (fname, prob, name)],
                               problems=problems, facts={"state_file": {"name": fname, "usable": False, "problem": prob}}), None
    stocks = doc["stocks"]
    return None, (stocks, set(getattr(stocks, "dups", ())))


def _state_finish(name, kind, d, syms, scope, stocks, dups, tally, mis, inv, facts, freshness, unverified, extra_unverified=0, extra_warnings=None):
    n = len(syms)
    counts = _counts(valid=tally["valid"], stale=tally["stale"], missing=tally["missing"], invalid=tally["invalid"], unverified=extra_unverified)
    fname = STATE_FILES[kind]
    in_scope = [s for s in syms if s in dups]
    bad_keys = sum(1 for k in stocks if not (isinstance(k, str) and universe.SYMBOL_RE.match(k)))
    facts = dict(facts)
    facts["state_file"] = {"name": fname, "usable": True, "entries": len(stocks), "identified_by": 'the symbol key in "stocks"',
                           "duplicate_symbol_keys": {"distinct": len(dups), "in_this_scope": len(in_scope), "symbols": sorted(x for x in dups if isinstance(x, str) and universe.SYMBOL_RE.match(x))[:OUTSIDE_LIST_LIMIT]},
                           "keys_that_are_not_valid_symbols": bad_keys}
    warnings = list(extra_warnings or [])
    if dups:
        warnings.append({"code": "duplicate_symbol_keys_in_state_file", "count": len(dups), "identifiers": facts["state_file"]["duplicate_symbol_keys"]["symbols"],
                         "message": "%s repeats %d symbol key(s). A universe stock with a repeated key cannot be read unambiguously and is counted invalid; no entry was chosen over another." % (fname, len(dups))})
    return _module(name, "partial", n, counts, [fname], basis=_basis(scope), freshness=freshness, facts=facts, missing_reasons=mis, invalid_reasons=inv,
                   unverified=unverified, warnings=warnings)


def _classify_all(syms, stocks, dups, classify, as_of):
    tally = {"valid": 0, "stale": 0, "missing": 0, "invalid": 0, "unverified": 0}
    mis, inv, unv, stl = {}, {}, {}, {}
    for s in syms:
        if s in dups:
            st, why = "invalid", "duplicate_symbol_key"
        else:
            st, why = classify(s, stocks.get(s), as_of)
        tally[st] += 1
        if st == "missing":
            _tally(mis, why)
        elif st == "invalid":
            _tally(inv, why)
        elif st == "unverified":
            _tally(unv, why)
        elif st == "stale":
            _tally(stl, why)
    return tally, mis, inv, unv, stl


def _canonical_isin(d, sym):
    """(isin or None, reason or None). The universe's ISIN, unless it is absent or the stock directory (when usable) contradicts it or lists the symbol twice: then there is no single
    canonical ISIN and nothing is compared. Never guessed, never remapped."""
    u = d.isin.get(sym)
    if u is None:
        return None, "canonical_isin_unavailable_universe_has_none"
    st = _directory_state(d)
    if st["usable"]:
        if sym in st["dup_symbols"]:
            return None, "canonical_isin_ambiguous_directory_lists_symbol_twice"
        di = st["entries"].get(sym)
        if di is not None and di != u:
            return None, "canonical_isin_ambiguous_directory_disagrees"
    return u, None


def _isin_verdict(d, sym, record_isin, acc):
    """None when the record's ISIN equals the canonical one (counted as matched); otherwise the fixed reason the record's identity is NOT confirmed. Values are compared, never written."""
    canonical, why = _canonical_isin(d, sym)
    if canonical is None:
        _tally(acc["canonical_unavailable_or_ambiguous"], why)
        return why
    if record_isin is None or record_isin is _HUGE or record_isin == "":
        acc["record_has_no_isin"] += 1
        return "cache_record_has_no_isin"
    if record_isin != canonical:
        acc["differs_from_universe"] += 1
        acc["differing_symbols"].append(sym)
        return "isin_differs_from_universe"
    acc["matched"] += 1
    return None


def _new_isin_acc():
    return {"matched": 0, "differs_from_universe": 0, "record_has_no_isin": 0, "canonical_unavailable_or_ambiguous": {}, "differing_symbols": []}


def _isin_facts_and_warning(acc):
    facts = {"canonical_source": "universe.json, cross-checked with stock_directory.json when that file is usable", "matched": acc["matched"],
             "differs_from_universe": acc["differs_from_universe"], "record_has_no_isin": acc["record_has_no_isin"],
             "canonical_unavailable_or_ambiguous": _sorted_dict(acc["canonical_unavailable_or_ambiguous"]),
             "note": "compared for records that hold usable data; the identifiers themselves are never written; nothing is remapped"}
    warns = []
    if acc["differs_from_universe"]:
        syms = sorted(acc["differing_symbols"])
        warns.append({"code": "state_isin_differs_from_universe", "count": len(syms), "identifiers": syms[:OUTSIDE_LIST_LIMIT],
                      "message": "%d cached record(s) carry an ISIN that differs from the universe's. They are counted unverified (the data may belong to another instrument); "
                                 "they are not remapped and not treated as invalid, and the cause has not been established. The ISINs are not shown." % len(syms)})
    return facts, warns


def mod_fundamentals_state(d, syms, scope):
    name = "fundamentals_state"
    mod, ctx = _state_open(name, "fundamentals", d, syms, scope)
    if mod:
        return mod
    stocks, dups = ctx
    acc = {"all_six_metrics": 0, "some_metrics": 0, "no_metrics": 0, "with_recorded_error_but_data": 0, "data_but_not_listed_now": 0}
    iacc = _new_isin_acc()

    def classify(sym, e, as_of):
        if e is None:
            return "missing", "no_cache_entry"
        if not isinstance(e, dict):
            return "invalid", "entry_not_an_object"
        if e.get("held_isin_change"):
            return "unverified", "held_aside_after_isin_change"
        for k in ("profile_fetched", "ratios_fetched", "last_attempt"):
            if e.get(k) is not None:
                day = _date(e.get(k))
                if day is None or day > as_of:
                    return "invalid", k + "_not_a_past_date"
        if e.get("ratios_fetched"):
            r = e.get("ratios")
            if not isinstance(r, dict) or set(r) != set(FUNDAMENTALS_METRICS):
                return "invalid", "ratios_not_exactly_the_six_metrics"
            if any(v is not None and not _isnum(v) for v in r.values()):
                return "invalid", "ratio_not_a_finite_number_or_null"
            verdict = _isin_verdict(d, sym, e.get("isin"), iacc)
            if verdict:
                return "unverified", verdict
            present = sum(1 for v in r.values() if v is not None)
            acc["all_six_metrics" if present == len(FUNDAMENTALS_METRICS) else "no_metrics" if present == 0 else "some_metrics"] += 1
            if e.get("last_error") == NOT_FOUND_ERROR:
                acc["data_but_not_listed_now"] += 1
            elif e.get("last_error"):
                acc["with_recorded_error_but_data"] += 1
            return ("stale", "ratios_older_than_threshold") if (as_of - _date(e["ratios_fetched"])).days >= FUNDAMENTALS_MAX_AGE_DAYS else ("valid", None)
        if e.get("last_error") == NOT_FOUND_ERROR:
            return "missing", "not_in_upstox"
        if e.get("last_error"):
            return "missing", "failed_last_attempt"
        return "missing", "never_fetched"

    tally, mis, inv, unv, stl = _classify_all(syms, stocks, dups, classify, d.as_of)
    facts = {"stocks_with_ratios": tally["valid"] + tally["stale"], "stale_reasons": _sorted_dict(stl), "ratio_completeness_of_stocks_with_ratios": acc,
             "held_aside_after_isin_change": unv.get("held_aside_after_isin_change", 0), "isin_check": _isin_facts_and_warning(iacc)[0],
             "sector_label": "never read: it is private until the sector approval gate opens",
             "batches_recorded": len(d.state["fundamentals"][0]["batches"]) if isinstance(d.state["fundamentals"][0].get("batches"), dict) else None}
    unverified = [{"what": "whether the cached ratios were published", "count": tally["valid"] + tally["stale"],
                   "reason": "a cache record is not proof of publication; out/fundamentals.json and the site are not part of the state folder"}]
    if unv.get("held_aside_after_isin_change"):
        unverified.append({"what": "records held aside after an ISIN change", "count": unv["held_aside_after_isin_change"],
                           "reason": "which ISIN is right is a decision for a person; the held data is neither counted as usable nor as missing"})
    others = sum(v for k, v in unv.items() if k != "held_aside_after_isin_change")
    if others:
        unverified.append({"what": "cached records whose identity is not confirmed", "count": others,
                           "reason": "the record's ISIN differs from, or is missing next to, the canonical ISIN, or no single canonical ISIN exists; the reasons are in unverified_reasons"})
    facts["unverified_reasons"] = _sorted_dict(unv)
    return _state_finish(name, "fundamentals", d, syms, scope, stocks, dups, tally, mis, inv, facts,
                         {"status": "configured", "threshold_days": FUNDAMENTALS_MAX_AGE_DAYS, "threshold_source": "fundamentals_updater.RATIOS_MAX_AGE_DAYS", "age_taken_from": "ratios_fetched"},
                         unverified, extra_unverified=sum(unv.values()), extra_warnings=_isin_facts_and_warning(iacc)[1])


def _quarterly_problem(e, as_of):
    for k in ("last_attempt", "last_checked", "last_success"):
        if e.get(k) is not None:
            day = _date(e.get(k))
            if day is None or day > as_of:
                return k + "_not_a_past_date"
    if e.get("basis") is not None and e.get("basis") not in BASES:
        return "basis_not_recognised"
    bad = _failures_problem(e.get("failures"))
    if bad:
        return bad
    qs = e.get("quarters")
    if not isinstance(qs, dict):
        return "quarters_not_an_object"
    for pe, q in qs.items():
        if _date(pe) is None:
            return "quarter_key_not_a_date"
        if not isinstance(q, dict) or not isinstance(q.get("values"), dict):
            return "quarter_record_malformed"
        if any(v is not None and not _isnum(v) for v in q["values"].values()):
            return "quarter_value_not_a_number_or_null"
    return None


def _add_months(day, n):
    """quarterly_results_updater.add_months: the month-end n months after the month-end `day`."""
    m = day.month - 1 + n
    y, m = day.year + m // 12, m % 12 + 1
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return dt.date(ny, nm, 1) - dt.timedelta(days=1)


def _retry_days(failures):
    """The updater's retry_days. The exponent is capped: 2**16 already exceeds the 30-day ceiling, so the result is identical for every counter while the work stays constant."""
    return min(2 ** (min(max(1, int(failures or 1)), 17) - 1), MAX_RETRY_DAYS)


def _failures_problem(v):
    """None, or a fixed reason why a failures counter cannot be trusted. Checked before the counter is used in any calculation; a bad counter is never read as zero."""
    if v is None:
        return None
    if v is _HUGE:
        return "failures_implausibly_large"
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return "failures_not_a_count"
    return "failures_implausibly_large" if v > MAX_PLAUSIBLE_FAILURES else None


def _quarterly_refresh_state(e, as_of):
    """(in_backoff, results_pending, due), the updater's own rules (in_backoff / results_pending / needs_refresh) applied to the cache fields, for a record that holds quarters.
    Evidence used: failures and last_attempt (back-off), the stored quarter-end dates (pending window), last_checked (due)."""
    last = _date(e.get("last_attempt"))
    backoff = bool(e.get("failures")) and last is not None and (as_of - last).days < _retry_days(e.get("failures"))
    ends = sorted(k for k in e["quarters"] if _date(k))
    start = _add_months(_date(ends[-1]), 3) + dt.timedelta(days=QUARTERLY_PENDING_FROM_DAYS)
    pending = start <= as_of <= start + dt.timedelta(days=QUARTERLY_PENDING_FOR_DAYS)
    lc = _date(e.get("last_checked"))
    if backoff:
        due = False
    elif lc is None:
        due = True
    else:
        due = (as_of - lc).days >= (QUARTERLY_PENDING_EVERY_DAYS if pending else QUARTERLY_STEADY_DAYS)
    return backoff, pending, due


def mod_quarterly_state(d, syms, scope):
    name = "quarterly_results"
    mod, ctx = _state_open(name, "quarterly", d, syms, scope)
    if mod:
        return mod
    stocks, dups = ctx
    per_stock, newest, basis = {}, {}, {}
    acc = {"stocks_with_failed_requests": 0, "stocks_whose_last_check_returned_no_data": 0, "stocks_whose_last_check_was_rejected": 0}
    states = {"in_retry_backoff": 0, "results_pending_window": 0, "neither": 0}
    due_n = [0]

    def classify(sym, e, as_of):
        if e is None:
            return "missing", "no_cache_entry"
        if not isinstance(e, dict):
            return "invalid", "entry_not_an_object"
        bad = _quarterly_problem(e, as_of)
        if bad:
            return "invalid", bad
        failed = bool(e.get("failures"))                    # only a failed request raises failures; no_data and rejected responses leave it at 0
        acc["stocks_with_failed_requests"] += failed
        if e.get("problem") == "no_data":
            acc["stocks_whose_last_check_returned_no_data"] += 1
        elif e.get("problem"):
            acc["stocks_whose_last_check_was_rejected"] += 1
        if not e["quarters"]:
            if failed:
                return "missing", "failed_last_attempt"
            if e.get("problem") == "no_data":
                return "missing", "checked_no_quarters_stored"
            if e.get("problem"):
                return "missing", "checked_result_rejected"
            return "missing", ("checked_no_quarters_stored" if e.get("last_checked") else "never_checked")
        _tally(per_stock, len(e["quarters"]))
        _tally(newest, max(e["quarters"]))
        _tally(basis, e.get("basis") or "unknown")
        backoff, pending, due = _quarterly_refresh_state(e, as_of)
        states["in_retry_backoff" if backoff else "results_pending_window" if pending else "neither"] += 1
        due_n[0] += due
        lc = _date(e.get("last_checked"))
        if lc is not None and (as_of - lc).days < QUARTERLY_STEADY_DAYS:
            return "valid", None
        base = "no_check_date" if lc is None else "last_check_older_than_interval"
        return "stale", ("stale_in_retry_backoff" if backoff else "stale_in_results_pending_window" if pending else base)

    tally, mis, inv, _, stl = _classify_all(syms, stocks, dups, classify, d.as_of)
    facts = {"stocks_with_quarters": tally["valid"] + tally["stale"], "quarters_per_stock": _sorted_dict(per_stock), "newest_quarter_end_counts": _sorted_dict(newest),
             "basis_counts": _sorted_dict(basis), "cache_schema_version": d.state["quarterly"][0].get("schema_version"), "stale_reasons": _sorted_dict(stl),
             "refresh_states_of_stocks_with_quarters": states, "due_for_recheck_under_the_updaters_rule": due_n[0], **acc}
    unverified = [{"what": "whether the cached quarterly results were published", "count": tally["valid"] + tally["stale"],
                   "reason": "a cache record is not proof of publication; out/quarterly_results.json, the shards and the site are not part of the state folder"},
                  {"what": "whether a new quarter has arrived inside a results-pending window", "count": states["results_pending_window"],
                   "reason": "the window says a result is expected from the stored quarter-end dates; the cache does not say whether it has been published yet"}]
    return _state_finish(name, "quarterly", d, syms, scope, stocks, dups, tally, mis, inv, facts,
                         {"status": "configured", "threshold_days": QUARTERLY_STEADY_DAYS, "threshold_source": "quarterly_results_updater.STEADY_EVERY_DAYS (the steady re-check interval); "
                          "valid/stale use it alone, the back-off and results-pending states are reported beside the counts", "age_taken_from": "last_checked"}, unverified)


def _fin_section(sec, as_of):
    """-> (state, reason): state is fresh / stale / outdated_schema / none / failed / invalid. A section the updater could use needs data, a known basis, the current schema and a
    past fetch date. An OLDER (or absent) schema version is readable but the updater refetches it before use (needs_refresh), so it is outdated_schema, not invalid; a newer or
    unrecognised version cannot be read with the current rules and is invalid."""
    if sec is None:
        return "none", None
    if not isinstance(sec, dict):
        return "invalid", "section_not_an_object"
    for k in ("fetched", "attempted"):
        if sec.get(k) is not None:
            day = _date(sec.get(k))
            if day is None or day > as_of:
                return "invalid", k + "_not_a_past_date"
    bad = _failures_problem(sec.get("failures"))
    if bad:
        return "invalid", bad
    if not sec.get("data"):
        return ("failed", None) if sec.get("error") else ("none", None)
    if not isinstance(sec["data"], dict):
        return "invalid", "data_not_an_object"
    if sec.get("basis") not in BASES:
        return "invalid", "basis_not_recognised"
    v = sec.get("schema")
    if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v > FIN_SCHEMA):
        return "invalid", "schema_not_recognised"
    f = _date(sec.get("fetched"))
    if f is None:
        return "invalid", "fetched_missing"
    if v is None or v < FIN_SCHEMA:
        return "outdated_schema", "schema_older_or_absent"
    return ("stale", None) if (as_of - f).days >= STATEMENT_MAX_AGE_DAYS else ("fresh", None)


def mod_financials_state(d, syms, scope):
    name = "annual_financials_and_balance_sheet"
    mod, ctx = _state_open(name, "financials", d, syms, scope)
    if mod:
        return mod
    stocks, dups = ctx
    sections = {s: {"fresh": 0, "stale": 0, "outdated_schema": 0, "none": 0, "failed": 0, "invalid": 0} for s in FIN_SECTIONS}
    basis, partial = {}, {}
    iacc = _new_isin_acc()

    def classify(sym, e, as_of):
        if e is None:
            return "missing", "no_cache_entry"
        if not isinstance(e, dict):
            return "invalid", "entry_not_an_object"
        st = {n: _fin_section(e.get(n), as_of) for n in FIN_SECTIONS}
        for n, (state, _) in st.items():
            sections[n][state] += 1
        for n in FIN_SECTIONS:
            if st[n][0] == "invalid":
                return "invalid", "%s:%s" % (n, st[n][1])
        usable = [n for n in FIN_SECTIONS if st[n][0] in ("fresh", "stale", "outdated_schema")]
        if len(usable) == len(FIN_SECTIONS):
            verdict = _isin_verdict(d, sym, e.get("isin"), iacc)
            if verdict:
                return "unverified", verdict
            for n in FIN_SECTIONS:
                _tally(basis, e[n]["basis"])
            if any(st[n][0] == "outdated_schema" for n in FIN_SECTIONS):
                return "stale", "a_section_has_an_older_schema"
            return ("stale", "a_section_older_than_threshold") if any(st[n][0] == "stale" for n in FIN_SECTIONS) else ("valid", None)
        if usable:
            _tally(partial, "+".join(usable))
            return "missing", "incomplete_sections"
        if e.get("last_error") == NOT_FOUND_ERROR:
            return "missing", "not_in_upstox"
        if e.get("last_error") or any(st[n][0] == "failed" for n in FIN_SECTIONS):
            return "missing", "failed_last_attempt"
        return "missing", "never_fetched"

    tally, mis, inv, unv, stl = _classify_all(syms, stocks, dups, classify, d.as_of)
    facts = {"isin_check": _isin_facts_and_warning(iacc)[0], "unverified_reasons": _sorted_dict(unv), "stocks_with_all_three_sections": tally["valid"] + tally["stale"], "required_sections": list(FIN_SECTIONS), "stale_reasons": _sorted_dict(stl),
             "incomplete_statements": {"stocks": mis.get("incomplete_sections", 0), "sections_held": _sorted_dict(partial),
                                       "meaning": "some but not all of the three statements are usable in this cache; not a statement about publication or about what the provider has"},
             "section_states_counted_over_stocks_with_an_entry": sections, "basis_counts_of_complete_stocks": _sorted_dict(basis), "required_schema": FIN_SCHEMA}
    unverified = [{"what": "whether the cached statements were published", "count": tally["valid"] + tally["stale"],
                   "reason": "a cache record is not proof of publication; out/financials.json and the site are not part of the state folder"},
                  {"what": "whether stocks with incomplete statements are published", "count": mis.get("incomplete_sections", 0),
                   "reason": "the updater builds a published row from any usable section, so a stock counted missing here may still be published with fewer statements; the published file is not supplied"}]
    if unv:
        unverified.append({"what": "cached records whose identity is not confirmed", "count": sum(unv.values()),
                           "reason": "the record's ISIN differs from, or is missing next to, the canonical ISIN, or no single canonical ISIN exists; the reasons are in unverified_reasons"})
    return _state_finish(name, "financials", d, syms, scope, stocks, dups, tally, mis, inv, facts,
                         {"status": "configured", "threshold_days": STATEMENT_MAX_AGE_DAYS, "threshold_source": "financials_updater.STATEMENT_MAX_AGE_DAYS",
                          "age_taken_from": "each section's fetched date; the stock is stale when any section is, or when a section has an older schema version"}, unverified,
                         extra_unverified=sum(unv.values()), extra_warnings=_isin_facts_and_warning(iacc)[1])


# ---------------------------------------------------------------- the report
def build_report(data_dir, as_of, scope="both", fundamentals_path=None, state_dir=None):
    if scope not in ("first500", "all", "both"):
        raise ReportError("scope must be first500, all or both")
    if not isinstance(as_of, dt.date):
        raise ReportError("as_of must be a date")
    if not Path(data_dir).is_dir():
        raise ReportError("--data-branch is not a folder")
    if state_dir is not None and not Path(state_dir).is_dir():
        raise ReportError("--state-dir is not a folder")
    d = Data(data_dir, fundamentals_path, as_of, state_dir)
    d.load_universe()
    d.list_shards()
    symbols = d.symbols
    scopes = {}
    wanted = [("first_500", symbols[:FIRST_N])] if scope == "first500" else [("all", symbols)] if scope == "all" else [("first_500", symbols[:FIRST_N]), ("all", symbols)]
    for label, syms in wanted:
        cf = candle_facts(d, syms)
        fh_mod = mod_financial_history(d, syms, label)
        sh_mod = mod_shareholding(d, syms, label)
        mods = [mod_directory(d, syms, label), mod_fundamentals(d, syms, label), fh_mod, mod_growth(d, syms, label), mod_historical(d, syms, label, cf),
                mod_indicators(d, syms, label, cf), mod_sector(d, syms, label), sh_mod,
                mod_cache_only("quarterly_results", d, syms, label, ["data/quarterly_cache.json", "out/by_symbol/quarterly_results/", "out/quarterly_results.json"]),
                mod_cache_only("annual_financials_and_balance_sheet", d, syms, label, ["out/financials.json"]),
                mod_detail(d, syms, label, cf, fh_mod, sh_mod)]
        if d.state_dir is not None:          # measured from the supplied state files; without --state-dir the list above is the whole report
            mods = [mod_quarterly_state(d, syms, label) if m["module"] == "quarterly_results" else mod_financials_state(d, syms, label)
                    if m["module"] == "annual_financials_and_balance_sheet" else m for m in mods]
            mods.insert([m["module"] for m in mods].index("fundamentals") + 1, mod_fundamentals_state(d, syms, label))
        warnings = [dict(w, module=m["module"]) for m in mods for w in m["warnings"]]
        isin = [w for w in warnings if w["code"] == "isin_differs_from_universe"]
        scopes[label] = {"warnings": warnings,
                         "warning_summary": {"isin_differs_from_universe": {"records": sum(w["count"] for w in isin), "distinct_symbols": len({x for w in isin for x in w["symbols"]})}},
                         "selection": ("the first 500 symbols of universe.json in file order (symbols[:500]); no ranking and no NIFTY 500 assumption" if label == "first_500"
                                       else "every symbol of universe.json"),
                         "denominator": len(syms), "first_symbol": syms[0] if syms else None, "last_symbol": syms[-1] if syms else None,
                         "fewer_than_500_in_universe": bool(label == "first_500" and len(symbols) < FIRST_N), "modules": mods}
    uni_set = set(symbols)
    outside = {}
    if isinstance(d.fh, dict) and isinstance(d.fh.get("stocks"), dict):
        outside["financial_history"] = sorted(set(d.fh["stocks"]) - uni_set)
    if isinstance(d.sh, dict) and isinstance(d.sh.get("stocks"), dict):
        outside["shareholding"] = sorted(set(d.sh["stocks"]) - uni_set)
    outside["historical_shards"] = sorted(set(d.shard_names) - uni_set)
    if isinstance(d.hist, dict) and isinstance(d.hist.get("stocks"), dict):
        outside["historical_single_file"] = sorted(set(d.hist["stocks"]) - uni_set)
    if _fund_usable(d):
        outside["fundamentals_rows"] = sorted({r["symbol"] for r in d.fund["stocks"] if isinstance(r, dict) and isinstance(r.get("symbol"), str)} - uni_set)
    state_report = None
    if d.state_dir is not None:
        state_report = {}
        for kind, fname in STATE_FILES.items():
            doc, prob = d.state[kind]
            state_report[fname] = {"usable": doc is not None, "problem": prob, "entries": len(doc["stocks"]) if doc is not None else None}
            if doc is not None:
                outside["state:" + fname] = sorted({k for k in doc["stocks"] if isinstance(k, str) and universe.SYMBOL_RE.match(k)} - uni_set)
    inputs = d.inputs.listing()
    inputs.append({"path": "historical/shards/", "files": len(d.shard_names), "sha256": d.shard_hash})
    return {
        "schema_version": SCHEMA_VERSION, "kind": KIND, "generated_for": as_of.isoformat(),
        "tool": {"name": "coverage_report.py", "reads_only": True, "provider_calls": 0},
        "universe": {"path": "universe.json", "as_of": d.universe_doc.get("as_of"), "stage": d.universe_doc.get("stage"), "count": len(symbols),
                     "unique_symbols": len(uni_set), "unique_isins": len({i for i in d.isin.values() if i is not None}), "sorted_ascending": symbols == sorted(symbols),
                     "validated_with": "universe.check_doc"},
        "inputs": inputs,
        "reconciliation": {"present_in_stored_data_but_not_in_universe": {k: {"count": len(v), "first": v[:OUTSIDE_LIST_LIMIT]} for k, v in sorted(outside.items())}},
        "scopes": scopes,
        **({"state_files": state_report} if state_report is not None else {}),
    }


def render_json(report):
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def render_markdown(report):
    out = ["# StockLens coverage report", "", "Generated for %s. Offline: no provider was called. Counts only; no value or percentage of a holding appears here." % report["generated_for"], ""]
    u = report["universe"]
    out += ["Universe: %d stocks (%d unique symbols, %d unique ISINs), as of %s, validated with %s. Sorted ascending: %s." %
            (u["count"], u["unique_symbols"], u["unique_isins"], u["as_of"], u["validated_with"], u["sorted_ascending"]), ""]
    for label, sc in report["scopes"].items():
        out += ["## %s (%d stocks: %s to %s)" % (label, sc["denominator"], sc["first_symbol"], sc["last_symbol"]), "", sc["selection"], "",
                "| Module | Measurability | Valid | Stale | Missing | Invalid | Unverified | Coverage % | Denominator |", "|---|---|---|---|---|---|---|---|---|"]
        for m in sc["modules"]:
            c = m["counts"]
            cell = lambda v: "n/a" if v is None else str(v)
            out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %d |" % (m["module"], m["measurability"], cell(c["valid"]), cell(c["stale"]), cell(c["missing"]),
                                                                            cell(c["invalid"]), cell(c["unverified"]), cell(m["coverage_pct"]), m["denominator"]["value"]))
        out.append("")
        if sc["warnings"]:
            out.append("### Warnings")
            out.append("")
            for w in sc["warnings"]:
                out.append("- **%s**: %s%s" % (w["module"], w["message"], (" Stocks: %s." % ", ".join(w["symbols"])) if w.get("symbols") else (" Identifiers: %s." % ", ".join(w["identifiers"])) if w.get("identifiers") else ""))
            out.append("")
        for m in sc["modules"]:
            for u_ in m["unverified"]:
                out.append("- %s: unverified (%d) - %s" % (m["module"], u_["count"], u_["reason"]))
            if m["freshness"].get("status") == THRESHOLD_NOT_CONFIGURED:
                out.append("- %s: freshness status %s (facts are in the JSON report)" % (m["module"], THRESHOLD_NOT_CONFIGURED))
        out.append("")
    out += ["## Inputs", ""] + ["- %s %s" % (i["path"], i["sha256"]) for i in report["inputs"]]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- output safety
def check_output_dir(out, protected):
    """-> the resolved output folder, or raises ReportError. protected: input folders/files that must not be touched."""
    p = Path(out)
    if p.is_symlink():
        raise ReportError("--out is a symbolic link")
    if p.exists() and not p.is_dir():
        raise ReportError("--out exists and is not a folder")
    if not p.parent.is_dir():
        raise ReportError("the parent of --out does not exist (it is not created)")
    r = p.parent.resolve() / p.name
    here = Path(__file__).resolve().parent
    if ".git" in r.parts or r == Path(r.anchor):
        raise ReportError("--out is not allowed here")
    for q in list(protected) + [here]:
        q = Path(q).resolve()
        if r == q or q in r.parents or r in q.parents:
            raise ReportError("--out must be a separate folder: it is, contains or sits inside %s" % q.name)
    return r


def write_outputs(out_dir, report):
    out_dir.mkdir(exist_ok=True)
    for name, text in zip(OUTPUT_NAMES, (render_json(report), render_markdown(report))):
        target = out_dir / name
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise ReportError("%s exists and is not a regular file" % name)
        tmp = out_dir / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(str(tmp), str(target))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="coverage_report.py", description="Offline StockLens coverage report (reads local files, writes only --out).")
    ap.add_argument("--data-branch", required=True)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scope", default="both", choices=("first500", "all", "both"))
    ap.add_argument("--fundamentals", default=None)
    ap.add_argument("--state-dir", default=None)
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        as_of = _date(a.as_of)
        if as_of is None:
            raise ReportError("--as-of must be a real date written YYYY-MM-DD")
        protected = [a.data_branch] + ([a.fundamentals] if a.fundamentals else []) + ([a.state_dir] if a.state_dir else [])
        out_dir = check_output_dir(a.out, protected)
        report = build_report(a.data_branch, as_of, a.scope, a.fundamentals, a.state_dir)
        write_outputs(out_dir, report)
    except (ReportError, OSError) as e:
        print("coverage_report: refused: %s" % e, file=sys.stderr)
        return 2
    print(render_markdown(report))
    print("Written: %s, %s" % tuple(str(out_dir / n) for n in OUTPUT_NAMES))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
