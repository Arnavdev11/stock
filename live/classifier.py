"""
live/classifier.py - the offline ordinary-equity universe classifier.

Pure: it is given the rows of the Upstox NSE instrument file and three parsed NSE reference lists (live.reference), all already read from local files by the caller.
No file access, no network, no environment, no secret. Company and instrument NAMES are never used to classify anything.

Every Upstox NSE_EQ row gets EXACTLY ONE status, by this precedence (the first rule that applies wins; the other memberships are kept in the audit record):

    0  review_structural    the row has no usable instrument_key (nothing can be matched or subscribed)
    1  excluded_sme         ISIN is in the SME reference
    2  excluded_etf         ISIN is in the ETF reference
    3  review_conflict      ISIN is in the equity reference under more than one series (the series cannot be chosen)
    4  review_pca           security_type == "PCA" in the Upstox snapshot (after the exclusions above)
    5  excluded_bz          equity-reference series BZ
    6  eligible_be          equity-reference series BE
    7  eligible_eq          equity-reference series EQ
    8  review_other_series  in the equity reference under some other series (decision needed; never assumed)
    9  review_unmatched     in no reference and not PCA (never assumed to be debt or anything else)

The ISIN is the matching key. The symbol is only a diagnostic (a mismatch, or a symbol-only match, is flagged and changes no status).
Duplicate symbols, duplicate ISINs and duplicate instrument keys are FLAGGED; no row is ever removed for repeating something.
Instrument type is recorded but plays no part in the decision.

Serialization of a record, for the audit hashes:   ISIN|SYMBOL|instrument_key|status      (ISIN and SYMBOL are trimmed and upper-cased; "" when absent or malformed)
A group's hash is the SHA-256 of its lines sorted by Unicode code point (= UTF-8 byte order), each line followed by one LF (so the last line ends with LF too);
an empty group hashes zero bytes. The global hash is the same over all lines of all statuses.
"""
import hashlib
import json
import re

STATUS_ORDER = ("excluded_sme", "excluded_etf", "review_conflict", "review_pca", "excluded_bz", "eligible_be", "eligible_eq",
                "review_unmatched", "review_other_series", "review_structural")
CANDIDATES = ("eligible_eq", "eligible_be")
COMPAT_MERGE = ("review_unmatched", "review_other_series", "review_structural", "review_conflict")     # the baseline's single "unmatched/other" bucket (comparison view only)
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
SAMPLE_PER_STRATUM = 5
LIST_CAP = 100


def _norm(v):
    return v.strip().upper() if isinstance(v, str) else ""


def line_of(r, status=None):
    return "%s|%s|%s|%s" % (r["isin"], r["symbol"], r["key"], status or r["status"])


def hash_lines(lines, trailing_lf=True, sort_mode="line"):
    if not lines:
        return hashlib.sha256(b"").hexdigest()
    ls = sorted(lines) if sort_mode == "line" else sorted(lines, key=lambda s: tuple(s.split("|")))
    data = "\n".join(ls) + ("\n" if trailing_lf else "")
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class Classification:
    pass


def _one(x, equity, sme, etf):
    key = x.get("instrument_key")
    key = key if isinstance(key, str) and key else ""
    isin_raw = _norm(x.get("isin"))
    isin = isin_raw if ISIN_RE.match(isin_raw) else ""
    symbol = _norm(x.get("trading_symbol"))
    sec = _norm(x.get("security_type"))
    flags = []
    if not isin_raw:
        flags.append("no_isin")
    elif not isin:
        flags.append("malformed_isin")
    if not symbol:
        flags.append("no_symbol")
    refs = []
    eq_series = []
    if isin:
        if isin in sme.by_isin:
            refs.append("SME")
        if isin in etf.by_isin:
            refs.append("ETF")
        eq_series = equity.series_of(isin)
        if isin in equity.by_isin:
            refs.append("EQL:" + "/".join(eq_series) if eq_series else "EQL")
    member = list(refs) + (["PCA"] if sec == "PCA" else [])
    if len([m for m in refs if m.split(":")[0] in ("SME", "ETF", "EQL")]) > 1:
        flags.append("multi_reference")
    if isin:                                                               # secondary, diagnostic only: does the symbol agree with the reference record found by ISIN?
        for label, ref in (("SME", sme), ("ETF", etf), ("EQL", equity)):
            recs = ref.by_isin.get(isin)
            if recs and symbol and symbol not in {r["symbol"] for r in recs if r["symbol"]} and any(r["symbol"] for r in recs):
                flags.append("symbol_differs_from_%s_record" % label)
    if symbol:
        for label, ref in (("SME", sme), ("ETF", etf), ("EQL", equity)):
            if symbol in ref.by_symbol and not (isin and isin in ref.by_isin):
                flags.append("symbol_only_match_%s" % label)
    if not key:
        status, reason = "review_structural", "no_instrument_key"
    elif "SME" in refs:
        status, reason = "excluded_sme", "isin_in_sme_reference"
    elif "ETF" in refs:
        status, reason = "excluded_etf", "isin_in_etf_reference"
    elif len(eq_series) > 1:
        status, reason = "review_conflict", "isin_in_equity_reference_under_several_series:" + "/".join(eq_series)
    elif sec == "PCA":
        status, reason = "review_pca", "security_type_is_pca"
    elif eq_series == ["BZ"]:
        status, reason = "excluded_bz", "equity_reference_series_bz"
    elif eq_series == ["BE"]:
        status, reason = "eligible_be", "equity_reference_series_be"
    elif eq_series == ["EQ"]:
        status, reason = "eligible_eq", "equity_reference_series_eq"
    elif isin in equity.by_isin:
        status, reason = "review_other_series", "equity_reference_series:" + ("/".join(eq_series) or "none")
    else:
        status, reason = "review_unmatched", "no_reference_match"
    name = x.get("name")
    return {"key": key, "isin": isin, "isin_raw": isin_raw, "symbol": symbol, "status": status, "reason": reason, "flags": flags, "memberships": sorted(member),
            "security_type": sec or "(none)", "instrument_type": _norm(x.get("instrument_type")) or "(none)", "name": name if isinstance(name, str) else ""}


def classify_universe(rows, equity, sme, etf):
    c = Classification()
    segs, non_obj, items = {}, 0, []
    for x in rows:
        if not isinstance(x, dict):
            non_obj += 1
            continue
        segs[str(x.get("segment"))] = segs.get(str(x.get("segment")), 0) + 1
        if x.get("segment") == "NSE_EQ":
            items.append(x)
    recs = [_one(x, equity, sme, etf) for x in items]
    groups = {"symbol": {}, "isin": {}, "instrument_key": {}}
    for r in recs:
        if r["symbol"]:
            groups["symbol"].setdefault(r["symbol"], []).append(r)
        if r["isin"]:
            groups["isin"].setdefault(r["isin"], []).append(r)
        if r["key"]:
            groups["instrument_key"].setdefault(r["key"], []).append(r)
    c.duplicates = {}
    for kind, g in groups.items():
        dup = {k: v for k, v in g.items() if len(v) > 1}
        c.duplicates[kind] = dup
        for members in dup.values():
            for r in members:
                r["flags"].append("duplicate_" + kind)
    for r in recs:
        r["flags"] = sorted(set(r["flags"]))
    recs.sort(key=lambda r: (line_of(r), json.dumps(r["flags"]), r["name"], r["instrument_type"], r["security_type"]))
    c.records = recs
    c.total_rows = len(rows)
    c.segments = dict(sorted(segs.items()))
    c.non_object_rows = non_obj
    c.nse_eq_rows = len(recs)
    c.counts = {s: 0 for s in STATUS_ORDER}
    c.lines = {s: [] for s in STATUS_ORDER}
    for r in recs:
        c.counts[r["status"]] += 1
        c.lines[r["status"]].append(line_of(r))
    c.partition_ok = sum(c.counts.values()) == c.nse_eq_rows
    c.candidates = sum(c.counts[s] for s in CANDIDATES)
    c.hashes = {s: hash_lines(c.lines[s]) for s in STATUS_ORDER}
    c.global_hash = hash_lines([l for s in STATUS_ORDER for l in c.lines[s]])
    combo = {}
    for r in recs:
        k = "+".join(r["memberships"]) or "(no reference, not PCA)"
        combo.setdefault(k, {}).setdefault(r["status"], 0)
        combo[k][r["status"]] += 1
    c.overlap = {k: dict(sorted(v.items())) for k, v in sorted(combo.items())}
    # reference side: reference records the Upstox snapshot does not contain (by ISIN), with the symbol as a diagnostic
    up_isins = {r["isin"] for r in recs if r["isin"]}
    by_sym = {}
    for r in recs:
        if r["symbol"]:
            by_sym.setdefault(r["symbol"], []).append(r["key"] or "(no key)")
    c.reference_side = {}
    for ref in (equity, sme, etf):
        unmatched, no_isin = [], []
        matched = 0
        for rec in ref.records:
            if not rec["isin"]:
                no_isin.append(rec)
            elif rec["isin"] in up_isins:
                matched += 1
            else:
                unmatched.append(rec)
        key = lambda rec: (rec["isin"], rec["symbol"], rec["series"])        # the position of a record in its file is layout, not data: it never enters the output
        c.reference_side[ref.label] = {
            "records": len(ref.records), "matched_by_isin": matched,
            "unmatched": [{"symbol": r["symbol"], "series": r["series"], "isin": r["isin"], "symbol_in_upstox": sorted(by_sym.get(r["symbol"], []))} for r in sorted(unmatched, key=key)],
            "without_valid_isin": [{"symbol": r["symbol"], "series": r["series"]} for r in sorted(no_isin, key=key)]}
    # Upstox side: rows that matched no reference (and are not PCA), by stratum, with a small deterministic sample for a person to review
    strata = {}
    for r in recs:
        if r["status"] in ("review_unmatched", "review_other_series", "review_structural"):
            k = (r["instrument_type"], r["security_type"], "has_isin" if r["isin"] else "no_isin")
            strata.setdefault(k, []).append(r)
    c.upstox_unmatched = [{"instrument_type": k[0], "security_type": k[1], "isin": k[2], "rows": len(v),
                           "sample": [{"key": r["key"], "isin": r["isin"], "symbol": r["symbol"], "status": r["status"], "name_for_human_review_only": r["name"]} for r in v[:SAMPLE_PER_STRATUM]]}
                          for k, v in sorted(strata.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    return c


# ---------------------------------------------------------------------------------------------------------------------- baseline comparison
BASELINE_NAMES = STATUS_ORDER + ("global",)


def parse_baseline(text):
    """Lines such as  - `eligible_eq` (2,333): `<64 hex>`  ->  {name: (count or None, hash)}. Anything else is ignored."""
    out = {}
    for line in text.splitlines():
        h = re.search(r"\b([0-9a-f]{64})\b", line)
        n = re.search(r"\b(" + "|".join(BASELINE_NAMES) + r")\b", line.lower())
        if h and n:
            cnt = re.search(r"\(\s*([\d,]+)\s*\)", line)
            out[n.group(1)] = (int(cnt.group(1).replace(",", "")) if cnt else None, h.group(1))
    return out


def _variants(lines):
    return [("as specified", hash_lines(lines)), ("without the final LF", hash_lines(lines, trailing_lf=False)),
            ("sorted by ISIN, symbol, key, status in turn", hash_lines(lines, sort_mode="tuple")),
            ("sorted by field and without the final LF", hash_lines(lines, trailing_lf=False, sort_mode="tuple"))]


def compare_baseline(c, baseline):
    """-> (list of text lines, all_ok). A baseline is a comparison only: nothing is changed to make it match."""
    out, ok = [], True
    merged_lines = []
    for s in COMPAT_MERGE:
        merged_lines += [l.rsplit("|", 1)[0] + "|review_unmatched" for l in c.lines[s]]
    merged_extra = sum(c.counts[s] for s in COMPAT_MERGE if s != "review_unmatched")
    all_lines = [l for s in STATUS_ORDER for l in c.lines[s]]
    compat_global_lines = [l for s in STATUS_ORDER if s not in COMPAT_MERGE for l in c.lines[s]] + merged_lines
    for name in BASELINE_NAMES:
        if name not in baseline:
            continue
        want_n, want_h = baseline[name]
        if name == "global":
            lines, compat = all_lines, compat_global_lines
        else:
            lines, compat = c.lines[name], (merged_lines if name == "review_unmatched" else None)
        n = len(lines)
        strict = hash_lines(lines) == want_h and (want_n is None or want_n == n)
        if strict:
            out.append("  PASS  %-20s %6d rows  %s" % (name, n, want_h))
            continue
        if compat is not None and merged_extra and hash_lines(compat) == want_h and (want_n is None or want_n == len(compat)):
            out.append("  PASS* %-20s %6d rows  matches only as the baseline's merged 'unmatched/other' bucket (this run keeps %d rows apart as review_other_series / review_structural / review_conflict)" % (name, len(compat), merged_extra))
            continue
        ok = False
        out.append("  FAIL  %-20s this run: %d rows %s" % (name, n, hash_lines(lines)))
        out.append("        baseline : %s rows %s" % (want_n if want_n is not None else "?", want_h))
        for label, h in _variants(lines)[1:]:
            if h == want_h:
                out.append("        DIAGNOSTIC ONLY: the baseline hash is reproduced if the lines are %s - the serialization differs, the data may agree; not adopted" % label)
    if not [n for n in BASELINE_NAMES if n in baseline]:
        out.append("  (the baseline file held no recognisable 'name ... 64-hex-digit hash' lines)")
        ok = False
    return out, ok


# ---------------------------------------------------------------------------------------------------------------------- text report
def _cap(items, n=LIST_CAP):
    return items[:n], max(0, len(items) - n)


def format_classification(c, provenance, baseline_lines=None, list_statuses=()):
    o = []
    add = o.append
    add("StockLens universe classification - offline (local files only: no network, no token, nothing written)")
    add("=" * 100)
    add("1. INPUTS")
    for p in provenance:
        add("   %-12s %s" % (p["name"], p["path"]))
        add("                size %s bytes   sha256 %s   as-of: %s" % (p["bytes"], p["sha256"], p["as_of"] or "not recorded"))
        for k, v in p.get("notes", []):
            add("                %s: %s" % (k, v))
    add("   (as-of dates are shown only when passed with --asof; no date is ever taken from the files themselves)")
    add("")
    add("2. UPSTOX SNAPSHOT")
    add("   rows: %d   by segment: %s%s" % (c.total_rows, ", ".join("%s=%d" % kv for kv in c.segments.items()) or "-", ("   not-an-object entries: %d" % c.non_object_rows) if c.non_object_rows else ""))
    add("   NSE_EQ rows classified: %d" % c.nse_eq_rows)
    add("")
    add("3. STATUS COUNTS (precedence order; every NSE_EQ row has exactly one status)")
    for s in STATUS_ORDER:
        add("   %-22s %6d" % (s, c.counts[s]))
    add("   %-22s %6d   %s" % ("TOTAL", sum(c.counts.values()), "PARTITION OK: equals the NSE_EQ rows" if c.partition_ok else "PARTITION BROKEN"))
    add("   proposed candidates (eligible_eq + eligible_be): %d   -- a proposal for review, NOT production approval" % c.candidates)
    add("")
    add("4. REFERENCE OVERLAPS (memberships of each row; the status above follows the precedence, nothing is dropped)")
    for k, v in c.overlap.items():
        add("   %-34s %s" % (k, ", ".join("%s=%d" % kv for kv in v.items())))
    add("")
    add("5. DUPLICATES (flagged, every row kept)")
    for kind in ("symbol", "isin", "instrument_key"):
        g = c.duplicates[kind]
        add("   duplicate %s groups: %d (%d rows)" % (kind, len(g), sum(len(v) for v in g.values())))
        items, more = _cap(sorted(g.items()))
        for k, members in items:
            add("      %s" % k)
            for r in members:
                add("         %-8s key=%s isin=%s instrument_type=%s security_type=%s status=%s" % ("row", r["key"], r["isin"] or "-", r["instrument_type"], r["security_type"], r["status"]))
        if more:
            add("      ... %d more groups not shown" % more)
    add("")
    add("6. REFERENCE RECORDS WITH NO UPSTOX MATCH (by ISIN; the symbol is only a diagnostic)")
    for label, d in c.reference_side.items():
        add("   %s: %d records, %d matched by ISIN, %d unmatched, %d without a valid ISIN" % (label, d["records"], d["matched_by_isin"], len(d["unmatched"]), len(d["without_valid_isin"])))
        items, more = _cap(d["unmatched"])
        for r in items:
            add("      %s series=%s isin=%s   symbol in the Upstox snapshot: %s" % (r["symbol"] or "-", r["series"] or "-", r["isin"], ", ".join(r["symbol_in_upstox"]) or "no"))
        if more:
            add("      ... %d more not shown" % more)
        for r in d["without_valid_isin"][:LIST_CAP]:
            add("      (no valid ISIN) %s series=%s" % (r["symbol"] or "-", r["series"] or "-"))
    add("")
    add("7. UPSTOX ROWS WITH NO EQUITY-REFERENCE MATCH AND NOT PCA - by instrument type and security type, with a sample (never classified as debt or anything else here)")
    for s in c.upstox_unmatched:
        add("   instrument_type=%s security_type=%s %s: %d rows" % (s["instrument_type"], s["security_type"], s["isin"], s["rows"]))
        for r in s["sample"]:
            add("        key=%s isin=%s symbol=%s status=%s   [name, for human review only: %s]" % (r["key"], r["isin"] or "-", r["symbol"] or "-", r["status"], r["name_for_human_review_only"]))
    add("")
    add("8. AUDIT HASHES   line = ISIN|SYMBOL|instrument_key|status; lines sorted by code point; SHA-256 of the lines each followed by one LF (empty group: zero bytes)")
    for s in STATUS_ORDER:
        add("   %-22s %6d  %s" % (s, c.counts[s], c.hashes[s]))
    add("   %-22s %6d  %s" % ("global", c.nse_eq_rows, c.global_hash))
    if baseline_lines is not None:
        add("")
        add("9. COMPARISON WITH THE SUPPLIED BASELINE (a comparison only; nothing is adjusted to match)")
        o.extend(baseline_lines)
    for s in list_statuses:
        add("")
        add("LIST %s (%d lines)" % (s, c.counts[s]))
        o.extend(sorted(c.lines[s]))
    return "\n".join(o)
