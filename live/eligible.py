"""
live/eligible.py - which instruments of the Upstox NSE instrument file are eligible for the full-market live feed, and a report of what was left out and why.

Pure: it is given the rows of the instrument file (already read from a local file by the caller) and returns an Eligibility. No network, no token, no file access,
no stock list in code: every symbol comes from the rows.

Eligible = a row that, structurally, is one tradable NSE equity:
    segment NSE_EQ, instrument_type EQ, a non-empty instrument_key, a trading_symbol that the snapshot validator accepts, a valid ISIN,
    security_type NORMAL when the row says anything about it, and a symbol (and instrument key) that no other row claims.
Everything else is excluded and COUNTED BY REASON (reasons name the offending value, e.g. "instrument_type:BE").

Names that merely LOOK like funds or other non-stocks (ETF, BEES, FUND ...) are NOT excluded here: guessing would silently drop real companies. They are reported as
flags, with examples, so that a person decides. An exclusion list can be applied later from that review.
"""
import json
import re

SYMBOL_RE = re.compile(r"^[A-Z0-9&._-]{1,30}$")                     # the same rule as the snapshot validator
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
FUND_NAME_RE = re.compile(r"\b(ETF|BEES|FUND|MUTUAL|INDEX\s+FUND|LIQUID)\b", re.I)
EXAMPLES = 10
FLAG_EXAMPLES = 25
REVIEW_SAMPLES = 3                 # raw rows kept per exclusion reason / instrument type in the review
REVIEW_MAX_REASONS = 40
REVIEW_MAX_DISTINCT = 25           # a field with more distinct values than this is "high-cardinality" and only summarised


class Eligibility:
    def __init__(self, equities, report, collected=None):
        self.equities = equities                 # instrument_key -> SYMBOL (the eligible set)
        self.report = report                     # plain dict, JSON-safe
        self.collected = collected               # only when classify(..., collect=True): raw rows for the review (never used by the rules)


def _example(bucket, reason, item, limit=EXAMPLES):
    lst = bucket.setdefault(reason, [])
    if len(lst) < limit:
        lst.append(item)


def classify(rows, collect=False):
    seg_counts, type_counts, sec_counts, fields = {}, {}, {}, {}
    excluded, ex_examples = {}, {}
    flags, flag_examples = {}, {}
    candidates = []                              # (key, symbol, name, isin) rows that passed every per-row test
    nse_eq = 0
    col = {"excluded_rows": {}, "flagged_rows": [], "type_rows": {}} if collect else None

    def drop(reason, sample, raw=None):
        excluded[reason] = excluded.get(reason, 0) + 1
        _example(ex_examples, reason, sample)
        if col is not None:
            _example(col["excluded_rows"], reason, raw, REVIEW_SAMPLES)

    for x in rows:
        if not isinstance(x, dict):
            seg_counts["(not an object)"] = seg_counts.get("(not an object)", 0) + 1
            continue
        seg = x.get("segment")
        seg_counts[str(seg)] = seg_counts.get(str(seg), 0) + 1
        if seg != "NSE_EQ":
            continue
        nse_eq += 1
        for k in x:
            fields[k] = fields.get(k, 0) + 1
        itype = x.get("instrument_type")
        type_counts[str(itype)] = type_counts.get(str(itype), 0) + 1
        if col is not None:
            _example(col["type_rows"], str(itype), x, REVIEW_SAMPLES)
        stype = x.get("security_type")
        if stype is not None:
            sec_counts[str(stype)] = sec_counts.get(str(stype), 0) + 1
        key = x.get("instrument_key")
        sym_raw = x.get("trading_symbol")
        sym = str(sym_raw).strip().upper() if isinstance(sym_raw, str) else ""
        sample = sym or str(key)
        if not isinstance(key, str) or not key:
            drop("no_instrument_key", sample, x)
        elif itype != "EQ":
            drop("instrument_type:%s" % itype, sample, x)
        elif not sym:
            drop("no_trading_symbol", str(key), x)
        elif not SYMBOL_RE.match(sym):
            drop("symbol_not_accepted", sym, x)
        elif stype is not None and stype != "NORMAL":
            drop("security_type:%s" % stype, sym, x)
        elif not isinstance(x.get("isin"), str) or not ISIN_RE.match(x["isin"].strip().upper()):
            drop("no_valid_isin", sym, x)
        else:
            candidates.append((key, sym, str(x.get("name") or ""), x["isin"].strip().upper(), x))

    # a symbol or a key claimed twice: neither claimant is trusted (the same rule as live.instruments)
    by_sym, by_key = {}, {}
    for key, sym, _n, _i, _x in candidates:
        by_sym.setdefault(sym, []).append(key)
        by_key[key] = by_key.get(key, 0) + 1
    equities = {}
    for key, sym, name, isin, raw in candidates:
        if len(by_sym[sym]) > 1:
            drop("duplicate_symbol", sym, raw)
        elif by_key[key] > 1:
            drop("duplicate_instrument_key", sym, raw)
        else:
            equities[key] = sym
    # flags: reviewed by a person, never applied automatically
    isin_of = {}
    for key, sym, name, isin, raw in candidates:
        if key not in equities:
            continue
        isin_of.setdefault(isin, []).append(sym)
        if FUND_NAME_RE.search(name) or FUND_NAME_RE.search(sym):
            if col is not None:
                col["flagged_rows"].append(raw)
            flags["name_looks_like_fund_or_etf"] = flags.get("name_looks_like_fund_or_etf", 0) + 1
            _example(flag_examples, "name_looks_like_fund_or_etf", "%s (%s)" % (sym, name), FLAG_EXAMPLES)
    for isin, syms in isin_of.items():
        if len(syms) > 1:
            flags["isin_shared_by_several_symbols"] = flags.get("isin_shared_by_several_symbols", 0) + len(syms)
            _example(flag_examples, "isin_shared_by_several_symbols", "%s: %s" % (isin, ", ".join(sorted(syms))), FLAG_EXAMPLES)
    report = {
        "total_rows": len(rows),
        "segments": dict(sorted(seg_counts.items())),
        "nse_eq_rows": nse_eq,
        "instrument_type_counts": dict(sorted(type_counts.items())),
        "security_type_counts": dict(sorted(sec_counts.items())),
        "fields_seen_on_nse_eq_rows": dict(sorted(fields.items())),
        "eligible_count": len(equities),
        "excluded_count": sum(excluded.values()),
        "excluded": dict(sorted(excluded.items())),
        "excluded_examples": {k: sorted(v) for k, v in sorted(ex_examples.items())},
        "flags_not_excluded": dict(sorted(flags.items())),
        "flag_examples": {k: sorted(v) for k, v in sorted(flag_examples.items())},
    }
    return Eligibility(equities, report, col)


def format_report(report, source=None):
    r = report
    out = ["StockLens live universe - count only (no network, no token)"]
    if source:
        out.append("instrument file : %s" % source.get("path"))
        out.append("  size          : %s bytes   sha256 %s" % (source.get("bytes"), source.get("sha256")))
    out.append("rows in file    : %d" % r["total_rows"])
    out.append("by segment      : " + ", ".join("%s=%d" % kv for kv in r["segments"].items()))
    out.append("NSE_EQ rows     : %d   by instrument_type: %s" % (r["nse_eq_rows"], ", ".join("%s=%d" % kv for kv in r["instrument_type_counts"].items()) or "-"))
    if r["security_type_counts"]:
        out.append("security_type   : " + ", ".join("%s=%d" % kv for kv in r["security_type_counts"].items()))
    out.append("")
    out.append("ELIGIBLE EQUITIES: %d" % r["eligible_count"])
    out.append("EXCLUDED (NSE_EQ rows): %d" % r["excluded_count"])
    for reason, n in r["excluded"].items():
        out.append("  %-34s %6d   e.g. %s" % (reason, n, ", ".join(r["excluded_examples"].get(reason, [])[:5])))
    if r["flags_not_excluded"]:
        out.append("")
        out.append("FLAGGED FOR REVIEW (still counted as eligible; nothing was guessed):")
        for reason, n in r["flags_not_excluded"].items():
            out.append("  %-34s %6d" % (reason, n))
            for e in r["flag_examples"].get(reason, [])[:FLAG_EXAMPLES]:
                out.append("      " + e)
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------- review (Step 0): evidence, not rules
def _j(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _isin_parts(x):
    v = x.get("isin")
    if not isinstance(v, str):
        return None, None
    v = v.strip().upper()
    return (v[:3] or None), (v[7:9] or None)


def review(rows, result):
    """-> text. DESCRIPTIVE ONLY: it shows the metadata the instrument file carries so a person can decide the rules. It classifies nothing and changes nothing.
    `result` must come from classify(rows, collect=True) on the same rows."""
    col = result.collected
    if col is None:
        raise ValueError("review needs classify(rows, collect=True)")
    eq_rows = [x for x in rows if isinstance(x, dict) and x.get("segment") == "NSE_EQ"]
    outcome = lambda x: "eligible" if isinstance(x.get("instrument_key"), str) and x["instrument_key"] in result.equities else "excluded"
    out = []
    add = out.append
    add("=" * 100)
    add("REVIEW OF THE INSTRUMENT METADATA (evidence only: nothing here classifies, includes or excludes anything)")
    add("=" * 100)

    flagged = sorted(col["flagged_rows"], key=lambda r: (str(r.get("trading_symbol")), str(r.get("instrument_key"))))
    add("")
    add("1. FLAGGED instruments (name looks like a fund/ETF; still counted as eligible): %d - complete metadata, one JSON object per line" % len(flagged))
    for r in flagged:
        add("   " + _j(r))

    fields = {}
    for x in eq_rows:
        for k, v in x.items():
            fields.setdefault(k, {}).setdefault(_j(v), [0, 0])[0 if outcome(x) == "eligible" else 1] += 1
    low = sorted(k for k, d in fields.items() if len(d) <= REVIEW_MAX_DISTINCT)
    high = sorted(k for k, d in fields.items() if len(d) > REVIEW_MAX_DISTINCT)
    add("")
    add("2. FIELD VALUES on the %d NSE_EQ rows: value -> [eligible, excluded]   (fields with more than %d distinct values are only summarised)" % (len(eq_rows), REVIEW_MAX_DISTINCT))
    for k in low:
        add("   %s  (%d distinct)" % (k, len(fields[k])))
        for v, (e, n) in sorted(fields[k].items(), key=lambda kv: (-(kv[1][0] + kv[1][1]), kv[0])):
            add("       %-40s eligible %5d   excluded %5d" % (v[:40], e, n))
    for k in high:
        add("   %s  (%d distinct values; high-cardinality, not tabulated)" % (k, len(fields[k])))

    add("")
    add("3. FIELD VALUES WITHIN EACH instrument_type (the other low-cardinality fields), to see which fields move together with a type")
    by_type = {}
    for x in eq_rows:
        by_type.setdefault(_j(x.get("instrument_type")), []).append(x)
    for t in sorted(by_type, key=lambda t: (-len(by_type[t]), t)):
        add("   instrument_type=%s  (%d rows)" % (t, len(by_type[t])))
        for k in low:
            if k == "instrument_type":
                continue
            d = {}
            for x in by_type[t]:
                d[_j(x.get(k))] = d.get(_j(x.get(k)), 0) + 1
            add("       %-18s %s" % (k, "  ".join("%s:%d" % (v[:24], n) for v, n in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:8])))

    add("")
    add("4. ISIN STRUCTURE as observed (a description of the data; NOT a validated classification): prefix = first 3 characters, middle = characters 8-9; what these mean must be validated against a reliable reference before any use")
    for title, idx in (("prefix (characters 1-3)", 0), ("characters 8-9", 1)):
        d = {}
        for x in eq_rows:
            v = _isin_parts(x)[idx]
            d.setdefault(v, [0, 0])[0 if outcome(x) == "eligible" else 1] += 1
        add("   %s:" % title)
        for v, (e, n) in sorted(d.items(), key=lambda kv: (-(kv[1][0] + kv[1][1]), str(kv[0])))[:30]:
            add("       %-10s eligible %5d   excluded %5d" % (v, e, n))

    add("")
    add("5. SYMBOL SUFFIX after the last '-' (counts only; shows whether series/segment appear in the symbol)")
    d = {}
    for x in eq_rows:
        s = x.get("trading_symbol")
        if isinstance(s, str) and "-" in s:
            d.setdefault(s.rsplit("-", 1)[1].upper(), [0, 0])[0 if outcome(x) == "eligible" else 1] += 1
    for v, (e, n) in sorted(d.items(), key=lambda kv: (-(kv[1][0] + kv[1][1]), kv[0]))[:30]:
        add("       -%-10s eligible %5d   excluded %5d" % (v, e, n))
    if not d:
        add("       (no trading symbol contains a '-')")

    add("")
    add("6. SAMPLE ROWS (up to %d each) per instrument_type, and per exclusion reason" % REVIEW_SAMPLES)
    for t in sorted(col["type_rows"]):
        add("   instrument_type=%s" % t)
        for r in col["type_rows"][t]:
            add("       " + _j(r))
    for reason in sorted(col["excluded_rows"])[:REVIEW_MAX_REASONS]:
        add("   excluded: %s  (%d rows)" % (reason, result.report["excluded"][reason]))
        for r in col["excluded_rows"][reason]:
            add("       " + (_j(r) if r is not None else "(row not an object)"))
    return "\n".join(out)
