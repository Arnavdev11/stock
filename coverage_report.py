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
  --as-of YYYY-MM-DD  the reference date for every age (required: the report never reads the clock, so equal inputs give an identical report)

Identifiers: a stored record whose ISIN differs from the universe's is still counted (the stock is keyed by symbol) but is named under the module's
"flags"; the report does not guess why (an older ISIN after a corporate action is one possibility, not something it assumes).

Freshness: a threshold is used only where the code documents its meaning. Fundamentals ratios: 7 days (fundamentals_updater.RATIOS_MAX_AGE_DAYS,
"refresh ratios weekly"). Candles, financial history, shareholding quarters: no threshold has been agreed, so they report measurable facts (ages, dates,
row counts) and the status "threshold_not_configured"; they are never labelled fresh or stale.

Usage: python coverage_report.py --data-branch DIR --as-of 2026-10-23 --out REPORT_DIR [--scope first500|all|both] [--fundamentals FILE]
Exit codes: 0 report written, 2 refused (bad arguments, unsafe --out, unreadable or invalid universe).
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
# The stocks the shareholding collector may read: its default scope (shareholding_updater.collection_symbols with SHAREHOLDING_FOLLOW_UNIVERSE unset, which
# no workflow in the repository sets): the 10 development stocks plus a fixed five-stock test set. A drift test pins this tuple to the collector's own list.
SH_COLLECTOR_SCOPE = tuple(universe.TEST_SYMBOLS) + ("DOMS", "20MICRONS", "21STCENMGM", "360ONE", "3BBLACKBIO")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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

    def read(self, path, register=True, label=None):
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
            return json.loads(raw.decode("utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c))), None
        except (UnicodeDecodeError, ValueError):
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
    def __init__(self, data_dir, fundamentals_path, as_of):
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
def mod_directory(d, syms, scope):
    n = len(syms)
    seen_sym, seen_isin, dup_sym, dup_isin = set(), set(), 0, 0
    for s in syms:
        dup_sym += s in seen_sym
        seen_sym.add(s)
        i = d.isin.get(s)
        if i is not None:
            dup_isin += i in seen_isin
            seen_isin.add(i)
    dir_map = None
    dir_symbols = d.directory.get("symbols") if isinstance(d.directory, dict) else None
    problems = []
    if isinstance(dir_symbols, list):
        dir_map = {x.get("symbol"): x.get("isin") for x in dir_symbols if isinstance(x, dict)}
    elif d.directory_problem:
        problems.append("stock_directory.json is " + d.directory_problem)
    else:
        problems.append("stock_directory.json has no symbols list")
    valid = invalid = missing = 0
    inv, mis = {}, {}
    for s in syms:
        i = d.isin.get(s)
        if i is None:
            missing += 1
            _tally(mis, "no_isin")
        elif not universe.ISIN_RE.match(i) or not universe.SYMBOL_RE.match(s):
            invalid += 1
            _tally(inv, "bad_symbol_or_isin")
        elif dir_map is not None and s not in dir_map:
            invalid += 1
            _tally(inv, "not_in_directory")
        elif dir_map is not None and dir_map[s] != i:
            invalid += 1
            _tally(inv, "isin_differs_from_directory")
        else:
            valid += 1
    facts = {"duplicate_symbols": dup_sym, "duplicate_isins": dup_isin}
    unverified = []
    if isinstance(dir_symbols, list):
        facts["directory_built_on"] = d.directory.get("built_on")
        facts["directory_counts"] = _sorted_dict(d.directory.get("counts") or {}) if isinstance(d.directory.get("counts"), dict) else None
        cl = d.directory.get("classification")
        facts["directory_classification_counts"] = _sorted_dict(cl.get("counts") or {}) if isinstance(cl, dict) and isinstance(cl.get("counts"), dict) else None
        keys = set()
        for x in dir_symbols:
            if isinstance(x, dict):
                keys |= set(x)
        facts["directory_entry_keys"] = sorted(keys)
        if "series" not in keys:
            unverified.append({"what": "EQ/BE split of the published stocks", "count": n,
                               "reason": "the directory entries carry no series field, so the published stocks cannot be split between EQ and BE offline"})
    return _module("directory", "offline" if dir_map is not None else "partial", n, _counts(valid=valid, missing=missing, invalid=invalid),
                   ["universe.json", "stock_directory.json"], basis=_basis(scope), source_as_of=d.universe_doc.get("as_of"),
                   freshness={"status": "not_assessed", "universe_as_of": d.universe_doc.get("as_of"), "directory_built_on": facts.get("directory_built_on")},
                   facts=facts, missing_reasons=mis, invalid_reasons=inv, unverified=unverified, problems=problems)


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
        if y["fy"] in seen:
            return None, "duplicate_fiscal_year"
        seen.add(y["fy"])
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
            lv = max(ys, key=lambda y: y["fy"])["values"].get(f)
            latest_present += lv is not None
            latest_zero += _isnum(lv) and lv == 0
        k = len(entries)
        fields[f] = {"of": k, "stocks_with_any_year_present": any_year, "stocks_with_latest_year_present": latest_present,
                     "stocks_with_latest_year_null": k - latest_present, "stocks_with_latest_year_zero": latest_zero, "stocks_with_any_zero_year": any_zero,
                     "pct_any_year": _pct(any_year, k), "pct_latest_year": _pct(latest_present, k), "pct_latest_year_of_denominator": _pct(latest_present, n)}
    yrs, newest, ver, basis, layout, fetched, sect_err = {}, {}, {}, {}, {}, [], {}
    gaps = 0
    for s, e, ys in entries:
        _tally(yrs, len(ys))
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


# ---------------------------------------------------------------- the report
def build_report(data_dir, as_of, scope="both", fundamentals_path=None):
    if scope not in ("first500", "all", "both"):
        raise ReportError("scope must be first500, all or both")
    if not isinstance(as_of, dt.date):
        raise ReportError("as_of must be a date")
    if not Path(data_dir).is_dir():
        raise ReportError("--data-branch is not a folder")
    d = Data(data_dir, fundamentals_path, as_of)
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
                out.append("- **%s**: %s%s" % (w["module"], w["message"], (" Stocks: %s." % ", ".join(w["symbols"])) if w.get("symbols") else ""))
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
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        as_of = _date(a.as_of)
        if as_of is None:
            raise ReportError("--as-of must be a real date written YYYY-MM-DD")
        protected = [a.data_branch] + ([a.fundamentals] if a.fundamentals else [])
        out_dir = check_output_dir(a.out, protected)
        report = build_report(a.data_branch, as_of, a.scope, a.fundamentals)
        write_outputs(out_dir, report)
    except (ReportError, OSError) as e:
        print("coverage_report: refused: %s" % e, file=sys.stderr)
        return 2
    print(render_markdown(report))
    print("Written: %s, %s" % tuple(str(out_dir / n) for n in OUTPUT_NAMES))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
