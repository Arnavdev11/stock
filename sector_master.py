"""
sector_master.py - StockLens sector architecture, Phase 1: the SECTOR MASTER (slow-changing reference data).

One record per ISIN. Pure functions only (no network, no clock): the caller passes "today".
The master never deletes a record and never overwrites a provider's label with a guess.

Record fields:
    isin, symbol, symbol_history, company_name, exchange, instrument_class, class_source,
    sector_source_label (the provider's text, kept exactly as received), sector_key (a neutral slug of that text),
    sector_group (reserved for a later grouping; defaults to sector_key), provider, fetched_on,
    first_seen, last_confirmed, status, revisions

status:  classified | missing_sector | unclassified | excluded | unconfirmed | absent
    classified      operating equity with a provider sector label
    missing_sector  operating equity, the provider has no sector label
    unclassified    no authoritative evidence for the instrument class
    excluded        authoritative evidence says it is not an operating company (for example an ETF)
    unconfirmed     the provider returned nothing this time; the earlier label is kept but not re-confirmed
    absent          no longer in the provider's universe; the record is kept

This file holds no stock-by-stock sector mapping of any kind.
"""
import datetime as dt
import json
import math
import re
from pathlib import Path

SCHEMA_VERSION = 1
KIND = "sector_master"
FIELDS = ["isin", "symbol", "symbol_history", "company_name", "exchange", "instrument_class", "class_source",
          "sector_source_label", "sector_key", "sector_group", "provider", "fetched_on", "first_seen",
          "last_confirmed", "status", "revisions"]
STATUSES = {"classified", "missing_sector", "unclassified", "excluded", "unconfirmed", "absent"}
CLASSES = {"operating_equity", "etf", "unclassified"}
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
MAX_REVISIONS = 50
MAX_NO_LOSS_DROP = 0.10     # the share of classified records that may disappear in one update


def norm_isin(v):
    s = str(v or "").strip().upper()
    return s if ISIN_RE.match(s) else None


def norm_symbol(v):
    s = str(v or "").strip().upper()
    return s or None


def sector_key(label):
    """Neutral slug of the provider's label. It never maps one sector onto another."""
    if label is None:
        return None
    s = re.sub(r"[^a-z0-9]+", "_", str(label).strip().lower()).strip("_")
    return s or None


def clean_label(label):
    if label is None:
        return None
    s = re.sub(r"\s+", " ", str(label)).strip()
    return s or None


def status_for(instrument_class, label):
    if instrument_class == "etf":
        return "excluded"
    if instrument_class == "operating_equity":
        return "classified" if label else "missing_sector"
    return "unclassified"


def _new(obs, today):
    label = clean_label(obs.get("sector_source_label"))
    cls = obs.get("instrument_class") or "unclassified"
    return {"isin": obs["isin"], "symbol": obs["symbol"], "symbol_history": [], "company_name": obs.get("company_name"),
            "exchange": obs.get("exchange") or "NSE", "instrument_class": cls, "class_source": obs.get("class_source"),
            "sector_source_label": label, "sector_key": sector_key(label), "sector_group": sector_key(label),
            "provider": obs.get("provider"), "fetched_on": obs.get("fetched_on"), "first_seen": today,
            "last_confirmed": today if (label or obs.get("provider_answered")) else None,
            "status": status_for(cls, label), "revisions": []}


def _rev(rec, today, field, old, new):
    rec["revisions"].append({"on": today, "field": field, "old": old, "new": new})
    del rec["revisions"][:-MAX_REVISIONS]


def merge(old, observations, today):
    """old: {isin: record}; observations: list of dicts with isin, symbol, instrument_class ...
    Returns (NEW {isin: record}, skipped_count). Records not observed are kept (status absent). Never raises on bad rows: they are skipped and counted."""
    new = {k: json.loads(json.dumps(v)) for k, v in (old or {}).items()}
    seen = set()
    skipped = 0
    for obs in observations:
        isin, sym = norm_isin(obs.get("isin")), norm_symbol(obs.get("symbol"))
        if not isin or not sym or isin in seen:
            skipped += 1
            continue
        seen.add(isin)
        obs = dict(obs, isin=isin, symbol=sym)
        rec = new.get(isin)
        if rec is None:
            new[isin] = _new(obs, today)
            continue
        if rec["symbol"] != sym:
            if rec["symbol"] not in rec["symbol_history"]:
                rec["symbol_history"].append(rec["symbol"])
            _rev(rec, today, "symbol", rec["symbol"], sym)
            rec["symbol"] = sym
        if obs.get("company_name"):
            rec["company_name"] = obs["company_name"]
        cls = obs.get("instrument_class") or "unclassified"
        if cls != rec["instrument_class"]:
            _rev(rec, today, "instrument_class", rec["instrument_class"], cls)
        rec["instrument_class"], rec["class_source"] = cls, obs.get("class_source")
        label = clean_label(obs.get("sector_source_label"))
        answered = bool(obs.get("provider_answered"))
        if label:
            if label != rec["sector_source_label"]:
                _rev(rec, today, "sector_source_label", rec["sector_source_label"], label)
            rec["sector_source_label"], rec["sector_key"], rec["sector_group"] = label, sector_key(label), sector_key(label)
            rec["last_confirmed"], rec["status"] = today, status_for(cls, label)
        elif answered:
            # the provider answered and has no sector for this instrument: that is a fact, replace the old label (and record it)
            if rec["sector_source_label"]:
                _rev(rec, today, "sector_source_label", rec["sector_source_label"], None)
            rec["sector_source_label"] = rec["sector_key"] = rec["sector_group"] = None
            rec["last_confirmed"], rec["status"] = today, status_for(cls, None)
        else:
            # no answer this time (not fetched, or an error): keep what we had, do not re-confirm
            if obs.get("attempted") and rec["sector_source_label"] and cls == "operating_equity":
                rec["status"] = "unconfirmed"      # we asked and got no answer
            else:
                rec["status"] = status_for(cls, rec["sector_source_label"])
        if obs.get("provider"):
            rec["provider"] = obs["provider"]
        if obs.get("fetched_on"):
            rec["fetched_on"] = obs["fetched_on"]
    for isin, rec in new.items():
        if isin not in seen and (old or {}).get(isin) is not None and rec["status"] != "absent":
            rec["status"] = "absent"
    return new, skipped


# ------------------------------------------------------------------ checks
def _finite(x, path, out):
    if isinstance(x, float) and not math.isfinite(x):
        out.append("non-finite number at " + path)
    elif isinstance(x, dict):
        for k, v in x.items():
            _finite(v, path + "." + str(k), out)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _finite(v, "%s[%d]" % (path, i), out)


def validate_records(records):
    """records: list of record dicts (the file form). Returns a list of problems (empty = fine)."""
    p = []
    seen_isin, current_symbols = {}, {}
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            p.append("record %d is not an object" % i)
            continue
        miss = [f for f in FIELDS if f not in r]
        if miss:
            p.append("record %d is missing %s" % (i, ",".join(miss)))
            continue
        isin = r["isin"]
        if not norm_isin(isin):
            p.append("record %d has an invalid ISIN" % i)
        if isin in seen_isin:
            p.append("duplicate ISIN " + str(isin))
        seen_isin[isin] = True
        if r["symbol"] != norm_symbol(r["symbol"]):
            p.append("%s: symbol is not normalised" % isin)
        if r["status"] != "absent":
            other = current_symbols.get(r["symbol"])
            if other and other != isin:
                p.append("symbol %s belongs to two ISINs (%s, %s)" % (r["symbol"], other, isin))
            current_symbols[r["symbol"]] = isin
        if r["symbol"] in (r["symbol_history"] or []):
            p.append("%s: current symbol is also in symbol_history" % isin)
        if r["instrument_class"] not in CLASSES:
            p.append("%s: unknown instrument_class" % isin)
        if r["status"] not in STATUSES:
            p.append("%s: unknown status" % isin)
        if r["instrument_class"] == "etf" and not r["class_source"]:
            p.append("%s: ETF without an authoritative class_source" % isin)
        if r["instrument_class"] == "etf" and r["sector_key"]:
            p.append("%s: an ETF must not carry a sector" % isin)
        if r["sector_key"] != sector_key(r["sector_source_label"]):
            p.append("%s: sector_key does not match the stored label" % isin)
        if r["sector_source_label"] and not r["provider"]:
            p.append("%s: a label without a provider" % isin)
        if r["status"] == "classified" and not (r["instrument_class"] == "operating_equity" and r["sector_key"]):
            p.append("%s: classified without class and sector" % isin)
    _finite(records, "records", p)
    return p


def no_loss_problems(old_records, new_records, max_drop=MAX_NO_LOSS_DROP):
    """The master only grows: no ISIN may vanish and the classified count may not collapse."""
    p = []
    old = {r["isin"]: r for r in old_records}
    new = {r["isin"]: r for r in new_records}
    gone = sorted(set(old) - set(new))
    if gone:
        p.append("%d ISIN(s) vanished from the master" % len(gone))
    has_label = lambda r: r["instrument_class"] == "operating_equity" and bool(r["sector_source_label"])   # an absent or unconfirmed record keeps its label
    oc = sum(1 for r in old.values() if has_label(r))
    nc = sum(1 for r in new.values() if has_label(r))
    if oc and nc < oc * (1 - max_drop):
        p.append("classified records fell from %d to %d (more than %d%%)" % (oc, nc, round(max_drop * 100)))
    lost_labels = sum(1 for k, r in old.items() if r["sector_source_label"] and k in new and not new[k]["sector_source_label"])
    if oc and lost_labels > oc * max_drop:
        p.append("%d sector labels were cleared at once" % lost_labels)
    return p


# ------------------------------------------------------------------ file form
def to_doc(records, today):
    recs = sorted(records.values(), key=lambda r: r["isin"])
    return {"schema_version": SCHEMA_VERSION, "kind": KIND, "generated_on": today, "count": len(recs), "records": recs}


def dumps(doc):
    return json.dumps(doc, indent=1, allow_nan=False, ensure_ascii=False, sort_keys=True)


def _bad_const(c):
    raise ValueError("invalid number " + c)


def load(path):
    """Returns ({isin: record}, problems). A missing file is ({}, []). A damaged file is ({}, [problem]) - never silently treated as empty."""
    path = Path(path)
    if not path.exists():
        return {}, []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"), parse_constant=_bad_const)
    except (OSError, ValueError) as e:
        return {}, ["sector master unreadable: " + type(e).__name__]
    if not isinstance(doc, dict) or doc.get("kind") != KIND or doc.get("schema_version") != SCHEMA_VERSION \
            or not isinstance(doc.get("records"), list):
        return {}, ["sector master has the wrong shape"]
    probs = validate_records(doc["records"])
    if probs:
        return {}, ["sector master is invalid: " + probs[0]]
    return {r["isin"]: r for r in doc["records"]}, []


def write(path, records, today):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(to_doc(records, today)) + "\n", encoding="utf-8")
    tmp.replace(path)


def age_days(day, today):
    try:
        return (dt.date.fromisoformat(today) - dt.date.fromisoformat(day)).days
    except (TypeError, ValueError):
        return None
