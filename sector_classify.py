"""
sector_classify.py - StockLens sector architecture, Phase 1: INSTRUMENT CLASSIFICATION from authoritative evidence only.

Classes:  operating_equity | etf | unclassified

Rules (all enforced by tests):
  * "etf" only when the instrument's symbol is in an authoritative ETF list that was loaded from a file (never from a symbol or name pattern).
  * "operating_equity" only with positive evidence:
        - the provider returned a sector label for the ISIN, or
        - an EXHAUSTIVE ETF list is loaded and the instrument is not on it (the rest of the equity universe is then operating equity).
    Without an ETF list, an instrument that has no sector label stays "unclassified". Nothing is guessed.
  * Evidence that disagrees (on the ETF list AND carries a sector label) stays "unclassified" with class_source "conflicting_evidence".
  * No sector is ever assigned here: sectors come only from a provider (sector_provider.py).

The ETF list is data, not code. File shape (JSON):
    {"source": "<who published the list>", "as_of": "YYYY-MM-DD", "exhaustive": true, "symbols": ["...", ...]}
parse_etf_csv() turns a downloaded list (a CSV with a Symbol column) into that shape.
This module contains no stock-by-stock data.
"""
import csv
import io
import json
import math
from pathlib import Path

OPERATING, ETF, UNCLASSIFIED = "operating_equity", "etf", "unclassified"


def parse_etf_csv(text, source, as_of, exhaustive=True):
    """CSV text with a 'Symbol' column -> evidence dict. Raises ValueError when the column is missing or no symbol is found."""
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    cols = {(c or "").strip().lower(): c for c in (rdr.fieldnames or [])}
    if "symbol" not in cols:
        raise ValueError("the ETF list has no Symbol column")
    syms = sorted({(r.get(cols["symbol"]) or "").strip().upper() for r in rdr} - {""})
    if not syms:
        raise ValueError("the ETF list is empty")
    return {"source": source, "as_of": as_of, "exhaustive": bool(exhaustive), "symbols": syms}


def load_etf_evidence(path):
    """Returns (evidence or None, problems). A missing file is (None, []): classification then stays conservative."""
    path = Path(path)
    if not path.exists():
        return None, []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ["ETF evidence file unreadable"]
    return check_evidence(doc)


def check_evidence(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("symbols"), list) or not doc["symbols"] \
            or not isinstance(doc.get("source"), str) or not doc["source"].strip():
        return None, ["ETF evidence has the wrong shape (needs source and a non-empty symbols list)"]
    syms = {str(s).strip().upper() for s in doc["symbols"] if str(s).strip()}
    return {"source": doc["source"].strip(), "as_of": doc.get("as_of"), "exhaustive": doc.get("exhaustive") is True,
            "symbols": syms}, []


def classify(symbol, label, evidence):
    """-> (instrument_class, class_source). 'label' is the provider's sector text or None."""
    sym = (symbol or "").strip().upper()
    on_list = bool(evidence and sym and sym in evidence["symbols"])
    if on_list and label:
        return UNCLASSIFIED, "conflicting_evidence"
    if on_list:
        return ETF, "etf_list:" + evidence["source"]
    if label:
        return OPERATING, "provider_sector_present"
    if evidence and evidence["exhaustive"]:
        return OPERATING, "not_on_exhaustive_etf_list:" + evidence["source"]
    return UNCLASSIFIED, "no_evidence"


def main(argv):
    """python sector_classify.py import-etf --csv FILE --source TEXT --as-of YYYY-MM-DD --out private/etf_list.json
    Turns the downloaded ETF list (CSV with a Symbol column) into the private evidence file. Prints counts only, never symbols."""
    import sys
    if len(argv) < 1 or argv[0] != "import-etf":
        print("usage: sector_classify.py import-etf --csv FILE --source TEXT --as-of DATE --out FILE")
        return 2
    opt = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1, 2)}
    try:
        text = Path(opt["--csv"]).read_text(encoding="utf-8-sig")
        ev = parse_etf_csv(text, opt["--source"], opt["--as-of"])
    except (KeyError, OSError, ValueError) as e:
        print("::error::ETF list not imported: " + type(e).__name__ + (" - " + str(e) if isinstance(e, ValueError) else ""))
        return 1
    out = Path(opt["--out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(ev, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)
    print("ETF list imported: %d symbol(s), as of %s" % (len(ev["symbols"]), ev["as_of"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
