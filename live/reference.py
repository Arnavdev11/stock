"""
live/reference.py - parse one NSE reference list (equity list, SME list or ETF list) from its CSV text.

Pure: it is given the TEXT of a CSV that the caller already read from a local file. No file access, no network, no environment, no names used for any decision.
The only fields that are read are the ISIN (the matching key), the symbol (a secondary, diagnostic key) and, when the file has one, the series.
Column names are recognised after trimming, upper-casing and dropping every character that is not a letter or digit, so " ISIN NUMBER" and "isin_number" both work;
a file with no recognisable ISIN column is refused (a reference that cannot be matched by ISIN must never be used silently).
"""
import csv
import io
import re

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
ISIN_HEADERS = ("ISIN", "ISINNUMBER", "ISINNO", "ISINCODE")
SYMBOL_HEADERS = ("SYMBOL", "TRADINGSYMBOL", "SCRIPSYMBOL", "SECURITYSYMBOL")
SERIES_HEADERS = ("SERIES",)
EXAMPLES = 10


class ReferenceError(ValueError):
    pass


def norm(v):
    return v.strip().upper() if isinstance(v, str) else ""


def _head(h):
    return re.sub(r"[^A-Z0-9]", "", (h or "").upper())


def _pick(headers, wanted):
    for i, h in enumerate(headers):
        if _head(h) in wanted:
            return i
    return None


class Reference:
    def __init__(self, label, records, stats, columns):
        self.label = label
        self.records = records                    # list of {"isin","symbol","series","line"} (isin is "" when absent or malformed)
        self.stats = stats
        self.columns = columns                    # which header was used for each field (None when the file has no such column)
        self.by_isin = {}
        self.by_symbol = {}
        for r in records:
            if r["isin"]:
                self.by_isin.setdefault(r["isin"], []).append(r)
            if r["symbol"]:
                self.by_symbol.setdefault(r["symbol"], []).append(r)

    def series_of(self, isin):
        """Sorted distinct series of every record carrying this ISIN ([] when the ISIN is absent; [""] entries are dropped)."""
        return sorted({r["series"] for r in self.by_isin.get(isin, []) if r["series"]})


def parse(label, text):
    if not isinstance(text, str):
        raise ReferenceError("%s: the reference text is not a string" % label)
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ReferenceError("%s: the file is empty" % label)
    headers = rows[0]
    i_isin, i_sym, i_ser = _pick(headers, ISIN_HEADERS), _pick(headers, SYMBOL_HEADERS), _pick(headers, SERIES_HEADERS)
    if i_isin is None:
        raise ReferenceError("%s: no ISIN column found (columns seen: %s)" % (label, ", ".join(h.strip() for h in headers)))
    records, no_isin, bad_isin = [], 0, 0
    bad_examples = []
    for n, r in enumerate(rows[1:], start=2):
        cell = lambda i: r[i] if i is not None and i < len(r) else ""
        isin_raw = norm(cell(i_isin))
        if not isin_raw:
            no_isin += 1
            isin = ""
        elif not ISIN_RE.match(isin_raw):
            bad_isin += 1
            isin = ""
            if len(bad_examples) < EXAMPLES:
                bad_examples.append(isin_raw)
        else:
            isin = isin_raw
        records.append({"isin": isin, "symbol": norm(cell(i_sym)), "series": norm(cell(i_ser)), "line": n})
    seen = {}
    for r in records:
        if r["isin"]:
            seen.setdefault(r["isin"], []).append(r["symbol"])
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    series_counts = {}
    for r in records:
        series_counts[r["series"] or "(none)"] = series_counts.get(r["series"] or "(none)", 0) + 1
    stats = {"rows": len(records), "rows_without_isin": no_isin, "rows_with_malformed_isin": bad_isin, "malformed_isin_examples": sorted(bad_examples),
             "isins_listed_more_than_once": len(dup), "duplicate_isin_examples": sorted("%s: %s" % (k, ", ".join(sorted(v))) for k, v in dup.items())[:EXAMPLES],
             "series_counts": dict(sorted(series_counts.items()))}
    columns = {"isin": headers[i_isin].strip(), "symbol": headers[i_sym].strip() if i_sym is not None else None, "series": headers[i_ser].strip() if i_ser is not None else None}
    return Reference(label, records, stats, columns)
