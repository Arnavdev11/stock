"""
cache_validation.py - the helper behind the one-off workflow "Validate cache coverage" (.github/workflows/cache_validation.yml).

It does three small jobs and nothing else. It makes no network call, imports no provider client and writes only the one file it is told to.

  select CACHE_LIST_JSON       Choose the cache to restore from the list of cache METADATA that `gh api` returned (no cache content is involved).
                               The key must match ^nse-data-[0-9]+$ exactly - the key of the daily "Update data and publish site" run and of a fundamentals batch;
                               the other caches that share the prefix (nse-data-shp-..., nse-data-sector-...) never match. The newest such cache is chosen.
                               The list must be complete (the entries read equal the total the API reports); anything else is refused.
  check-files DATA_DIR         Refuse unless the three saved-state files exist as ordinary, non-empty files.
  summarize REPORT_JSON DATA_DIR OUT_MD
                               Build the public job summary from the coverage report. The summary is built NEW from an explicit allowlist: every number and every
                               name is checked and copied one by one. Nothing from the report is passed through. Anything unexpected (a missing field, a wrong type, a
                               number that does not add up, a name that is not a plain lower-case token) refuses the whole run and nothing is written.

What the summary contains: the scope sizes, for each of the three saved-state modules its measurability, its counts (valid, stale, missing, invalid, unverified),
its reason counts and a few counts of its own, which state files were usable, and the provenance (cache key, dates, sizes, file hashes).
What it never contains: a record, a ratio, a company name, an ISIN, a sector label, an error text, a symbol (so no outside-the-universe list either), a path.

THE RESULT IS CACHE COVERAGE, NOT PUBLICATION STATUS. A record in a saved cache is not proof that anything reached the website.

A refusal prints only a fixed code. Never the text of an exception, never a value from the cache or the report.
"""
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import coverage_report as C

CACHE_KEY_RE = re.compile(r"nse-data-[0-9]{1,20}", re.ASCII)       # matched with fullmatch: no prefix match, no trailing text, no newline
STATE_FILES = ("fundamentals_cache.json", "financials_cache.json", "quarterly_cache.json")
STATE_MODULES = ("fundamentals_state", "quarterly_results", "annual_financials_and_balance_sheet")
SCOPES = ("first_500", "all")
COUNT_KEYS = ("valid", "stale", "missing", "invalid", "not_applicable", "unverified")
MEASURABILITY = ("partial", "not_measurable")
TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,79}", re.ASCII)
SCALAR_FACTS = ("stocks_with_ratios", "stocks_with_quarters", "stocks_with_all_three_sections", "stocks_with_failed_requests",
                "stocks_whose_last_check_returned_no_data", "stocks_whose_last_check_was_rejected", "due_for_recheck_under_the_updaters_rule")
DICT_FACTS = ("unverified_reasons", "stale_reasons", "refresh_states_of_stocks_with_quarters")
MAX_COUNT = 10 ** 7
MAX_TOKENS = 40
MAX_WARNINGS = 20
MAX_REPORT_BYTES = 64 * 1024 * 1024
PROVENANCE_ENV = {                                                      # name -> pattern the value must fully match
    "CV_MATCHED_KEY": CACHE_KEY_RE.pattern, "CV_RESTORE_HIT": r"true", "CV_RUN_ID": r"[0-9]{1,20}", "CV_MAIN_SHA": r"[0-9a-f]{40}", "CV_DATA_BRANCH_SHA": r"[0-9a-f]{40}",
    "CV_AS_OF": r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "CV_CREATED_AT": r"[0-9T:.+\-Z]{10,40}", "CV_LAST_ACCESSED_AT": r"[0-9T:.+\-Z]{10,40}", "CV_SIZE": r"[0-9]{1,15}",
}


class Refused(Exception):
    """A fixed code, never data."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- choosing the cache (metadata only)
def _when(text):
    if not isinstance(text, str):
        raise Refused("cache_list_bad_date")
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise Refused("cache_list_bad_date") from None
    if t.tzinfo is None:
        raise Refused("cache_list_bad_date")
    return t


def select_cache(text):
    """The newest cache whose key is exactly nse-data-<digits>, from the (possibly paginated, concatenated) JSON the cache list API returned."""
    dec, i, pages = json.JSONDecoder(), 0, []
    text = text if isinstance(text, str) else ""
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            doc, i = dec.raw_decode(text, i)
        except ValueError:
            raise Refused("cache_list_not_json") from None
        pages.append(doc)
    if not pages:
        raise Refused("cache_list_empty_response")
    totals, entries = set(), []
    for p in pages:
        if not isinstance(p, dict) or not isinstance(p.get("actions_caches"), list):
            raise Refused("cache_list_unexpected_shape")
        t = p.get("total_count")
        if isinstance(t, bool) or not isinstance(t, int) or t < 0:
            raise Refused("cache_list_unexpected_shape")
        totals.add(t)
        entries.extend(p["actions_caches"])
    if len(totals) != 1 or len(entries) != next(iter(totals)):
        raise Refused("cache_list_incomplete")
    found = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("key"), str):
            raise Refused("cache_list_unexpected_shape")
        if CACHE_KEY_RE.fullmatch(e["key"]):
            size = e.get("size_in_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise Refused("cache_list_unexpected_shape")
            created = _when(e.get("created_at"))
            la = e.get("last_accessed_at") or e["created_at"]          # a cache never restored may carry no access time
            _when(la)
            found.append((created, int(e["key"][len("nse-data-"):]), e["key"], e["created_at"], la, size))
    if not found:
        raise Refused("no_cache_with_an_exact_nse_data_key")
    _, _, key, created, accessed, size = max(found)
    return {"key": key, "created_at": created, "last_accessed_at": accessed, "size_in_bytes": size}


# ---------------------------------------------------------------- the restored files
def check_files(data_dir):
    root = Path(data_dir)
    for name in STATE_FILES:
        p = root / name
        if p.is_symlink() or not p.is_file():
            raise Refused("state_file_missing_" + name.split("_")[0])
        if p.stat().st_size == 0:
            raise Refused("state_file_empty_" + name.split("_")[0])


def file_facts(data_dir):
    out = {}
    for name in STATE_FILES:
        b = (Path(data_dir) / name).read_bytes()
        out[name] = {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    return out


# ---------------------------------------------------------------- the allowlist
def _count(v, hi=MAX_COUNT):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > hi:
        raise Refused("report_bad_number")
    return v


def _opt_count(v):
    return None if v is None else _count(v)


def _tokens(d):
    if not isinstance(d, dict) or len(d) > MAX_TOKENS:
        raise Refused("report_bad_reason_counts")
    out = {}
    for k in sorted(d):
        if not isinstance(k, str) or not TOKEN_RE.fullmatch(k):
            raise Refused("report_bad_reason_name")
        out[k] = _count(d[k])
    return out


def _token(v):
    """A plain lower-case token, or the fixed word 'withheld'. Used for the one-word problem of an unusable file."""
    head = v.split(":", 1)[0].strip() if isinstance(v, str) else ""
    return head if TOKEN_RE.fullmatch(head) else "withheld"


def _percent(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or v > 100:
        raise Refused("report_bad_number")
    return v


def _facts(f):
    if not isinstance(f, dict):
        raise Refused("report_bad_module")
    out = {}
    for k in SCALAR_FACTS:
        if k in f:
            out[k] = _count(f[k])
    for k in DICT_FACTS:
        if k in f:
            out[k] = _tokens(f[k])
    sf = f.get("state_file")
    if not isinstance(sf, dict) or not isinstance(sf.get("usable"), bool):
        raise Refused("report_bad_module")
    st = {"usable": sf["usable"]}
    if sf["usable"]:
        dup = sf.get("duplicate_symbol_keys")
        if not isinstance(dup, dict):
            raise Refused("report_bad_module")
        st.update(entries=_count(sf.get("entries")), keys_that_are_not_valid_symbols=_count(sf.get("keys_that_are_not_valid_symbols")),
                  duplicate_symbol_keys_distinct=_count(dup.get("distinct")), duplicate_symbol_keys_in_this_scope=_count(dup.get("in_this_scope")))
    else:
        st["problem"] = _token(sf.get("problem"))
    out["state_file"] = st
    return out


def _module(m, denominator):
    if not isinstance(m, dict) or not isinstance(m.get("counts"), dict) or not isinstance(m.get("denominator"), dict):
        raise Refused("report_bad_module")
    if m.get("measurability") not in MEASURABILITY or _count(m["denominator"].get("value")) != denominator:
        raise Refused("report_bad_module")
    c = m["counts"]
    if set(c) != set(COUNT_KEYS):
        raise Refused("report_bad_counts")
    counts = {k: _opt_count(c[k]) for k in COUNT_KEYS}
    if sum(v for v in counts.values() if v is not None) != denominator:
        raise Refused("report_counts_do_not_add_up")
    warns = m.get("warnings")
    if not isinstance(warns, list) or len(warns) > MAX_WARNINGS:
        raise Refused("report_bad_module")
    clean_warns = []
    for w in warns:
        if not isinstance(w, dict) or not isinstance(w.get("code"), str) or not TOKEN_RE.fullmatch(w["code"]):
            raise Refused("report_bad_warning")
        clean_warns.append({"code": w["code"], "count": _count(w.get("count", 0))})
    problems = m.get("problems")
    if not isinstance(problems, list):
        raise Refused("report_bad_module")
    return {"measurability": m["measurability"], "denominator": denominator, "counts": counts, "coverage_pct": _percent(m.get("coverage_pct")),
            "missing_reasons": _tokens(m.get("missing_reasons", {})), "invalid_reasons": _tokens(m.get("invalid_reasons", {})),
            "facts": _facts(m.get("facts", {})), "warnings": clean_warns, "problem_count": len(problems)}


def sanitize(report, as_of):
    """The allowlisted summary of a coverage report made with --state-dir. Raises Refused for anything that differs from the reviewed shape."""
    if not isinstance(report, dict) or report.get("kind") != C.KIND or report.get("schema_version") != C.SCHEMA_VERSION:
        raise Refused("report_unexpected_kind_or_version")
    if report.get("generated_for") != as_of:
        raise Refused("report_date_mismatch")
    sf = report.get("state_files")
    if not isinstance(sf, dict) or set(sf) != set(STATE_FILES):
        raise Refused("report_has_no_state_files_block")
    files = {}
    for name in STATE_FILES:
        e = sf[name]
        if not isinstance(e, dict) or not isinstance(e.get("usable"), bool):
            raise Refused("report_bad_state_files_block")
        files[name] = {"usable": e["usable"], "entries": _opt_count(e.get("entries")), "problem": None if e.get("problem") is None else _token(e["problem"])}
    scopes = report.get("scopes")
    if not isinstance(scopes, dict) or set(scopes) != set(SCOPES):
        raise Refused("report_unexpected_scopes")
    out_scopes = {}
    for label in SCOPES:
        sc = scopes[label]
        if not isinstance(sc, dict) or not isinstance(sc.get("modules"), list):
            raise Refused("report_bad_scope")
        den = _count(sc.get("denominator"))
        by_name = {}
        for m in sc["modules"]:
            if isinstance(m, dict) and m.get("module") in STATE_MODULES:
                if m["module"] in by_name:
                    raise Refused("report_repeated_module")
                by_name[m["module"]] = m
        if set(by_name) != set(STATE_MODULES):
            raise Refused("report_state_modules_missing")
        out_scopes[label] = {"denominator": den, "modules": {n: _module(by_name[n], den) for n in STATE_MODULES}}
    return {"generated_for": as_of, "state_files": files, "scopes": out_scopes}


def provenance_from_env(env):
    out = {}
    for name, pattern in PROVENANCE_ENV.items():
        v = env.get(name)
        if not isinstance(v, str) or not re.fullmatch(pattern, v, re.ASCII):
            raise Refused("provenance_missing_or_malformed")
        out[name] = v
    return out


def render(summary, prov, hashes):
    """Markdown built from the sanitised object only: numbers and plain tokens."""
    L = ["# Cache coverage validation", "",
         "**This is cache coverage, not publication status.** It counts what the saved updater caches hold. A cached record is not proof that anything reached the website, "
         "and a stock the cache lacks may still be published from another source. Counts only: no record, value, name, identifier, sector or symbol appears here.", "",
         "Reference date %s. Raw cache files stayed on the runner and were not uploaded." % summary["generated_for"], "", "## Provenance", "",
         "- cache key: `%s` (restored by exact key, no prefix match, nothing saved)" % prov["CV_MATCHED_KEY"],
         "- cache created %s, last accessed %s before this restore, %s bytes" % (prov["CV_CREATED_AT"], prov["CV_LAST_ACCESSED_AT"], prov["CV_SIZE"]),
         "- run %s, repository commit `%s`, data branch commit `%s`" % (prov["CV_RUN_ID"], prov["CV_MAIN_SHA"], prov["CV_DATA_BRANCH_SHA"]), "",
         "| State file | Usable | Entries | Problem | Bytes | SHA-256 |", "|---|---|---|---|---|---|"]
    for name in STATE_FILES:
        f, h = summary["state_files"][name], hashes[name]
        L.append("| %s | %s | %s | %s | %d | `%s` |" % (name, f["usable"], "n/a" if f["entries"] is None else f["entries"], f["problem"] or "none", h["bytes"], h["sha256"]))
    for label in SCOPES:
        sc = summary["scopes"][label]
        L += ["", "## %s (%d stocks)" % (label, sc["denominator"]), "", "| Module | Measurability | Valid | Stale | Missing | Invalid | Unverified | Coverage % |", "|---|---|---|---|---|---|---|---|"]
        for n in STATE_MODULES:
            m = sc["modules"][n]
            cell = lambda v: "n/a" if v is None else str(v)
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (n, m["measurability"], *(cell(m["counts"][k]) for k in ("valid", "stale", "missing", "invalid", "unverified")), cell(m["coverage_pct"])))
        for n in STATE_MODULES:
            m = sc["modules"][n]
            parts = []
            for title, d in (("missing", m["missing_reasons"]), ("invalid", m["invalid_reasons"]), ("unverified", m["facts"].get("unverified_reasons", {})), ("stale", m["facts"].get("stale_reasons", {}))):
                if d:
                    parts.append("%s: %s" % (title, ", ".join("%s=%d" % kv for kv in sorted(d.items()))))
            extra = ", ".join("%s=%d" % (k, m["facts"][k]) for k in SCALAR_FACTS if k in m["facts"])
            if extra:
                parts.append(extra)
            if m["warnings"]:
                parts.append("warnings: " + ", ".join("%s=%d" % (w["code"], w["count"]) for w in m["warnings"]))
            if parts:
                L.append("- %s - %s" % (n, "; ".join(parts)))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- command line
def _read_report(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_REPORT_BYTES:
        raise Refused("report_unreadable")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        raise Refused("report_not_json") from None


def main(argv, env=None):
    env = os.environ if env is None else env
    try:
        cmd = argv[1] if len(argv) > 1 else ""
        if cmd == "select" and len(argv) == 3:
            c = select_cache(Path(argv[2]).read_text(encoding="utf-8"))
            lines = ["key=" + c["key"], "created_at=" + c["created_at"], "last_accessed_at=" + c["last_accessed_at"], "size=%d" % c["size_in_bytes"]]
            target = env.get("GITHUB_OUTPUT")
            if target:
                with open(target, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            else:
                print("\n".join(lines))
            return 0
        if cmd == "check-files" and len(argv) == 3:
            check_files(argv[2])
            print("cache_validation: the three state files are present")
            return 0
        if cmd == "summarize" and len(argv) == 5:
            prov = provenance_from_env(env)
            summary = sanitize(_read_report(argv[2]), prov["CV_AS_OF"])
            text = render(summary, prov, file_facts(argv[3]))
            with open(argv[4], "a", encoding="utf-8") as fh:
                fh.write(text)
            print("cache_validation: summary written")
            return 0
        print("cache_validation: usage: select LIST | check-files DIR | summarize REPORT DIR OUT")
        return 2
    except Refused as e:
        print("cache_validation: refused (%s)" % e.code)
        return 1
    except Exception:                                                   # never print the exception: it could carry data
        print("cache_validation: refused (internal_error)")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
