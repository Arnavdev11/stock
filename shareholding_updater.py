"""
shareholding_updater.py - StockLens Phase 5H.3: the quarterly SHAREHOLDING-PATTERN ledger (data layer only; no UI).

Source: the official NSE shareholding-pattern filings (the filing index and the XBRL file of every quarter), reached with the same
cookie-session pattern as nse_updater.py (which is not touched). Upstox is used ONLY as a latest-quarter cross-check; it never supplies a value.

What it does
  * Reads the NSE filing index of each development stock and keeps the quarter-end filings from Sep 2021 on (off-cycle filings, such as the
    one after a merger or an allotment, are listed as skipped and never stored as quarters).
  * Downloads the XBRL of every quarter it does not already hold, parses the ownership categories, and keeps the quarters in a persistent,
    append-only ledger:  shareholding_ledger.json (on the `stocklens-data` branch)  and publishes lean per-stock records as out/shareholding.json.
  * Five StockLens categories (percent of shares):
        promoters     = promoter and promoter group
        fii           = foreign institutions (new format: InstitutionsForeign = FPI Category I + II + other foreign;
                        old format: the single foreign portfolio investor row)
        mutual_funds  = mutual funds / UTI
        other_dii     = domestic institutions minus mutual funds (new format);  institutions minus foreign portfolio investors minus
                        mutual funds (old format)
        retail_other  = non-institutions + governments (+ employee benefit trusts when the filing reports them separately)
    Employee trusts are inside retail_other for the investor-facing value; their own percentage is kept in diagnostics.

Rules (all enforced by test_shareholding.py and test_shareholding_storage.py)
  * One record per (stock, quarter end). A quarter the index does not list is never invented; a quarter that is listed but whose XBRL cannot be
    obtained is stored as status "unavailable" with the reason. Nothing is interpolated, carried forward or supplemented from another site.
  * The percentage unit is DETECTED per filing (0-100 or 0-1 fractions) from the total row, and only then normalised to percent. Totals are never forced to 100.
  * format_version says which structure the filing used: "old-institutions-fpi" (single Institutions subtotal, one FPI row) or
    "new-domestic-foreign" (InstitutionsDomestic / InstitutionsForeign, FPI Category I and II). The two are never claimed to be identical.
  * The official REPORTED percentage column is what is stored. Where it conflicts with the filing's own share counts (more than 1 percentage
    point on any category) the quarter gets the quality flag "reported-pct-conflicts-with-share-counts" and the share-count-derived percentages
    are kept only as diagnostics. They never replace the reported values.
  * The latest NSE version of a quarter is the production version. When a later fetch brings a different NSE record id or different values, the
    previous version is appended to that record's revisions. An existing value never becomes null; an available quarter never becomes unavailable.
  * The NSE record id, XBRL URL, broadcast / submission date, revised flag, revision date and revision remark are kept for every quarter.

No token is needed for NSE. The optional Upstox cross-check reads the token from the environment only and never prints, logs or writes it.
"""
import copy
import datetime as dt
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import shareholding_ledger as sl
from upstox_common import ROOT, SYMBOLS, Upstox, load_instruments, log, write_json

SCHEMA_VERSION = 1
LEDGER_FILE = ROOT / "data" / sl.LEDGER_NAME
OUT_FILE = ROOT / "out" / "shareholding.json"
INDEX_URL = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol="
START_QUARTER = dt.date(2021, 9, 30)
QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}
WAIT = 1.5
DEFAULT_MAX_DOWNLOADS = 320
CONFLICT_PP = 1.0            # a reported category may differ this much from its share-count value before the quarter is flagged
SUM_TOLERANCE = 0.25         # the five categories may differ this much from 100 before the quarter is flagged (never forced to 100)
CROSS_TOLERANCE = 0.05
CATEGORIES = list(sl.CATEGORIES)
FORMAT_OLD, FORMAT_NEW = "old-institutions-fpi", "new-domestic-foreign"
FLAG_CONFLICT = "reported-pct-conflicts-with-share-counts"
FLAG_SUM = "five-categories-do-not-sum-to-100"
FLAG_OTHER_INST = "old-format-other-institutions-in-other-dii"
FLAG_ROW_MISSING = "category-row-missing"
FLAG_DUPLICATE = "duplicate-member-conflict"
FLAG_REPORT_DATE = "report-date-differs-from-quarter-end"
FLAG_KEPT = "kept-previous-value"
STATUS = ("available", "unavailable")

HEADERS = {   # the same browser-like headers nse_updater.py uses; the home page is visited first for the cookies NSE expects
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern",
}

NOTES = {
    "units": "Percent of shares, two decimals. employee_trusts_pct and every share-count figure are diagnostics.",
    "source": "Official NSE shareholding-pattern filings (index and XBRL). Upstox is only a latest-quarter cross-check and never supplies a value.",
    "categories": "promoters = promoter and promoter group; fii = foreign institutions / FPI; mutual_funds = mutual funds and UTI; other_dii = domestic institutions minus mutual funds; "
                  "retail_other = non-institutions + governments (+ employee benefit trusts when reported separately).",
    "format_version": "old-institutions-fpi = single Institutions subtotal and one FPI row (other_dii = institutions - FPI - mutual funds, so it also holds 'other institutions'); "
                      "new-domestic-foreign = InstitutionsDomestic / InstitutionsForeign with FPI Category I and II. The two definitions are not identical.",
    "null": "null means unavailable. Never zero as a substitute, never carried forward, never interpolated. A missing quarter has no record (see coverage).",
    "unavailable": "A quarter that NSE lists but whose XBRL could not be obtained is status 'unavailable' with the reason. No other site is used to fill it.",
    "totals": "The five categories are never forced to 100. A quarter whose total is off by more than %.2f is flagged." % SUM_TOLERANCE,
    "quality": "flagged = a data-quality warning applies. reported-pct-conflicts-with-share-counts means the filing's own percentage column disagrees with its own share counts; "
               "the reported percentages are shown and the share-count percentages are diagnostics only.",
    "revisions": "The latest NSE version of a quarter is the production version. Earlier versions are kept in revisions.",
    "scope": "Data layer only. No Stock Detail UI, chart or comparison is built from this file in this phase.",
}


# ---------------------------------------------------------------- small helpers
def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fnum(text):
    try:
        x = float(str(text).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def r2(x):
    return None if x is None else round(x + 0.0, 2)


def today():
    return dt.date.today().isoformat()


def pdate(text):
    """NSE date 'dd-MON-yyyy' (optionally followed by a time) -> date, else None."""
    try:
        return dt.datetime.strptime(str(text).strip().upper()[:11], "%d-%b-%Y").date()
    except (ValueError, TypeError):
        return None


def iso(text):
    d = pdate(text)
    return d.isoformat() if d else None


def local(tag):
    return tag.rsplit("}", 1)[-1]


def norm_name(text):
    """'in-bse-shp:MutualFundsOrUtiMember' -> 'mutualfundsoruti'. The spelling and capitalisation of members changed between years."""
    s = re.sub(r"[^A-Za-z0-9]", "", re.sub(r"^.*:", "", text or "")).lower()
    return s[:-6] if s.endswith("member") else s


def display_name(text):
    s = re.sub(r"^.*:", "", (text or "").strip())
    return s[:-6] if s.endswith("Member") else s


# ---------------------------------------------------------------- the NSE connection
class Nse:
    """The NSE filing index and XBRL downloads. Retries a few times; a 404 is final (the file is not there)."""

    def __init__(self, session=None, wait=WAIT):
        self.s, self.wait, self.downloads = session, wait, 0

    def _session(self):
        if self.s is None:
            s = requests.Session()
            s.headers.update(HEADERS)
            try:
                s.get("https://www.nseindia.com", timeout=30)
            except requests.RequestException as e:
                log("warning: could not open nseindia.com:", type(e).__name__)
            self.s = s
        return self.s

    def _get(self, url):
        err = "unknown error"
        for attempt in range(3):
            try:
                r = self._session().get(url, timeout=60)
            except requests.RequestException as e:
                err = "network error: " + type(e).__name__
                time.sleep(self.wait * (attempt + 2))
                self.s = None
                continue
            time.sleep(self.wait)
            if r.status_code == 200:
                return r, None
            err = "HTTP %d" % r.status_code
            if r.status_code in (401, 403, 429) or r.status_code >= 500:
                self.s = None                       # a fresh session gets fresh cookies
                time.sleep(self.wait * (attempt + 2))
                continue
            break
        return None, err

    def index(self, symbol):
        r, err = self._get(INDEX_URL + symbol)
        if err:
            return None, err
        try:
            body = r.json()
        except ValueError:
            return None, "the filing index was not JSON"
        recs = body if isinstance(body, list) else (body.get("data") if isinstance(body, dict) else None)
        if not isinstance(recs, list):
            return None, "the filing index had an unexpected shape"
        return [x for x in recs if isinstance(x, dict)], None

    def xbrl(self, url):
        self.downloads += 1
        r, err = self._get(url)
        return (r.content, None) if not err else (None, err)


def xbrl_candidates(url):
    """The listed URL first; if it is not there, the same file on the other official NSE archive hosts."""
    out = [url]
    for a, b in (("nsearchives.nseindia.com", "archives.nseindia.com"), ("nsearchives.nseindia.com", "www.nseindia.com")):
        if a in url:
            out.append(url.replace(a, b))
    return out


def fetch_xbrl(nse, url):
    """(bytes, url_used, None) or (None, None, reason). Only a 404 is retried on the other official hosts."""
    last = "no URL"
    for u in xbrl_candidates(url):
        data, err = nse.xbrl(u)
        if data is not None:
            return data, u, None
        last = err
        if err != "HTTP 404":
            break
    return None, None, last


# ---------------------------------------------------------------- the XBRL
def parse_xbrl(data):
    """XBRL bytes -> {'report_date': 'YYYY-MM-DD'|None, 'rows': {(norm, period): row}, 'duplicates': [..]}.
    A row is {'member', 'shares', 'pct'}; only contexts with exactly one CategoryOfShareholdersAxis member count, and the per-holder detail
    members (names ending _ContextNN or starting Details) are ignored."""
    root = ET.fromstring(data)
    ctx = {}
    for el in root.iter():
        if local(el.tag) == "context":
            members, per = [], None
            for c in el.iter():
                lt = local(c.tag)
                if lt in ("explicitMember", "typedMember"):
                    members.append((c.get("dimension", ""), (c.text or "").strip() or "".join(c.itertext()).strip()))
                elif lt in ("instant", "endDate"):
                    per = (c.text or "").strip()
            ctx[el.get("id")] = (members, per)
    report_date, rows, dups = None, {}, []
    for el in root:
        cr = el.get("contextRef")
        if cr is None or cr not in ctx:
            continue
        name, text = local(el.tag), (el.text or "").strip()
        members, per = ctx[cr]
        if not members:
            if name == "DateOfReport" and re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                report_date = text
            continue
        if len(members) != 1 or not members[0][0].endswith("CategoryOfShareholdersAxis"):
            continue
        mem = re.sub(r"^.*:", "", members[0][1])
        if "_Context" in mem or mem.startswith("Details"):
            continue
        if name not in ("NumberOfFullyPaidUpEquityShares", "ShareholdingAsAPercentageOfTotalNumberOfShares"):
            continue
        v = fnum(text)
        row = rows.setdefault((norm_name(mem), per), {"member": display_name(mem), "shares": None, "pct": None})
        key = "shares" if name == "NumberOfFullyPaidUpEquityShares" else "pct"
        if row[key] is not None and v is not None and row[key] != v:
            dups.append(row["member"] + ":" + key)
        elif row[key] is None:
            row[key] = v
    return {"report_date": report_date, "rows": rows, "duplicates": sorted(set(dups))}


# ---------------------------------------------------------------- Phase 5H.6: individually disclosed holders (named holders)
# In the NSE XBRL every individually disclosed holder is a PAIR of contexts that share one typed member, in a dimension called
# DetailsOfSharesHeldBy<Category>Axis (typed member "<Category>_ContextNN"):
#     the descriptive context  id "D_<Category>_ContextNN"  -> NameOfTheShareholder (PAN and promoter type are masked "******")
#     the numeric context      id "<Category>_ContextNN"    -> NumberOfFullyPaidUpEquityShares, ShareholdingAsAPercentageOfTotalNumberOfShares
# The two are joined on (axis, member, period). The holder's category comes ONLY from the axis (HOLDER_AXES below, verified against the filings
# by reconciling the named shares with the aggregate category row); a name is never used to classify, and an axis that is not in the table is
# counted and left out, never guessed. The filing lists institutional holders only where they hold 1% or more, so the named rows are
# a PART of a category, never the whole of it; they are kept apart from the aggregate ownership and never added to it.
NAMED_VERSION = 2   # 2: the foreign axes are split into FPI / FDI / other foreign (category 'Foreign Investor'); version-1 records are relabelled from their stored axis
HOLDER_BASIS = ("Individually disclosed holders only. For institutions the filing lists holders of 1% or more; promoter-group entities are listed individually. "
                "The aggregate ownership categories are reported separately and are not the sum of these rows.")
L_PROMOTER, L_MF, L_INS, L_DII, L_PUBLIC, L_OTHER = "Promoter", "DII – Mutual Fund", "DII – Insurance", "DII – Other", "Public – Disclosed", "Other – Custodian / DR holder"
C_FOREIGN, L_FPI, L_FDI, L_FOREIGN = "Foreign Investor", "FPI", "FDI", "Foreign – Other"   # the label follows the XBRL axis; FDI is not FPI
# axis (lower case, without "DetailsOfSharesHeldBy" and "Axis") -> (section, category, investor label, the aggregate row to reconcile with)
HOLDER_AXES = {
    "othersindianshareholders": ("promoter_group_indian", "Promoter", L_PROMOTER, "otherindianshareholders"),
    "otherforeignshareholders": ("promoter_group_foreign", "Promoter", L_PROMOTER, "otherforeignshareholders"),
    "mutualfundsoruti": ("mutual_funds", "DII", L_MF, "mutualfundsoruti"),
    "insurancecompanies": ("insurance_companies", "DII", L_INS, "insurancecompanies"),
    "providentfundsorpensionfunds": ("provident_pension_funds", "DII", L_DII, "providentfundsorpensionfunds"),
    "otherfinancialinstitutions": ("other_financial_institutions", "DII", L_DII, "otherfinancialinstitutions"),
    "otherinstitutionsdomestic": ("other_institutions_domestic", "DII", L_DII, "otherinstitutionsdomestic"),
    "institutionsforeignportfolioinvestorone": ("fpi_category_one", C_FOREIGN, L_FPI, None),
    "institutionsforeignportfolioinvestortwo": ("fpi_category_two", C_FOREIGN, L_FPI, None),
    "foreigndirectinvestment": ("foreign_direct_investment", C_FOREIGN, L_FDI, "foreigndirectinvestment"),
    "overseasdepositories": ("overseas_depositories", C_FOREIGN, L_FOREIGN, "overseasdepositories"),
    "otherinstitutionsforeign": ("other_institutions_foreign", C_FOREIGN, L_FOREIGN, "otherinstitutionsforeign"),
    "custodianordrholder": ("custodian_dr_holder", "Other", L_OTHER, "custodianordrholder"),
    # Verified by the live inventory (named shares reconcile EXACTLY with the aggregate row of the promoter table, or the axis is the FPI row of the old format):
    "detailssharesheldbyindividualsorhuf": ("promoter_individuals", "Promoter", L_PROMOTER, "individualsorhinduundividedfamily"),
    "centralgovernmentorstategovernments": ("promoter_central_state_government", "Promoter", L_PROMOTER, "centralgovernmentorstategovernments"),
    "institutionsforeignportfolioinvestor": ("fpi_old_format", C_FOREIGN, L_FPI, "institutionsforeignportfolioinvestor"),
}
# NOT mapped on purpose (they stay counted in unmapped_axes and are never shown): OtherNonInstitutions (category rows such as Bodies Corporate / HUF / NRI, not holders),
# OtherInstitutions in the old format (rows are named "Other"), SignificantBeneficialOwners (they repeat holdings reported elsewhere).
# axes that are never holders: they repeat holders already listed elsewhere, or are not holders at all
HOLDER_SKIP_AXES = {"shareholdersactingaspersonsinconcertforpublic": "persons_in_concert", "shareswhichremainunclaimedforpublicshareholders": "unclaimed"}
HOLDER_NAME_EL, HOLDER_SHARES_EL, HOLDER_PCT_EL = "NameOfTheShareholder", "NumberOfFullyPaidUpEquityShares", "ShareholdingAsAPercentageOfTotalNumberOfShares"
HOLDER_FLAG_EL = "WhetherACategoryOrMoreThan1PercentageOfShareholding"


def _axis_key(axis):
    k = re.sub(r"[^a-z0-9]", "", re.sub(r"^.*:", "", axis or "").lower())
    k = k[:-4] if k.endswith("axis") else k
    for pre in ("detailsofsharesheldby", "detailsofthe", "detailsof"):
        if k.startswith(pre):
            return k[len(pre):]
    return k


def nh_state(status, reason, day):
    """A named_holders record that holds no holders."""
    return {"version": NAMED_VERSION, "status": status, "reason": reason, "fetched": day, "basis": HOLDER_BASIS, "holders": [],
            "excluded": {}, "unmapped_axes": {}, "section_totals": {}, "flags": []}


def holder_sort_key(h):
    return (h.get("percentage") is None, -(h.get("percentage") or 0), h.get("shares") is None, -(h.get("shares") or 0), h.get("holder_name") or "", h.get("axis") or "", h.get("member") or "")


def build_named_holders(data, report_date, unit, rows, day):
    """XBRL bytes -> a named_holders record. report_date: the period of the holder contexts (the filing's own report date).
    unit: 'percent' | 'fraction' (the unit the aggregate parse detected). rows: the aggregate rows of that quarter ({norm: row}), only for reconciliation."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return nh_state("unavailable", "the XBRL file could not be parsed (%s)" % type(e).__name__, day)
    ctx = {}
    for el in root.iter():
        if local(el.tag) == "context":
            dims, per = [], None
            for c in el.iter():
                lt = local(c.tag)
                if lt == "typedMember":
                    dims.append(("T", (c.get("dimension") or ""), "".join(x.text or "" for x in c.iter() if x is not c).strip()))
                elif lt == "explicitMember":
                    dims.append(("E", (c.get("dimension") or ""), (c.text or "").strip()))
                elif lt in ("instant", "endDate"):
                    per = (c.text or "").strip()
            ctx[el.get("id")] = (dims, per)
    pairs, conflicts = {}, set()
    for el in root:
        cr = el.get("contextRef")
        if cr not in ctx:
            continue
        dims, per = ctx[cr]
        if per != report_date or len(dims) != 1 or dims[0][0] != "T":
            continue
        key = (dims[0][1], dims[0][2])
        name, text = local(el.tag), (el.text or "").strip()
        if name not in (HOLDER_NAME_EL, HOLDER_SHARES_EL, HOLDER_PCT_EL, HOLDER_FLAG_EL):
            continue
        slot = pairs.setdefault(key, {})
        if name in slot and slot[name] != text:
            conflicts.add(key)
        slot.setdefault(name, text)
    k = 100.0 if unit == "fraction" else (1.0 if unit == "percent" else None)
    excluded = {"zero_share": 0, "category_rows": 0, "persons_in_concert": 0, "unclaimed": 0, "unnamed": 0, "unmapped_axis": 0, "conflicting": 0}
    unmapped, holders, flags = {}, [], []
    if not pairs:
        st = nh_state("not-in-filing", "the filing carries no per-holder rows for %s" % report_date, day)
        st["excluded"] = excluded
        return st
    for (axis, member), f in sorted(pairs.items()):
        ak = _axis_key(axis)
        ak = ak[3:] if ak.startswith("the") and ak[3:] in HOLDER_SKIP_AXES else ak
        if ak in HOLDER_SKIP_AXES:
            excluded[HOLDER_SKIP_AXES[ak]] += 1
            continue
        if ak not in HOLDER_AXES:
            unmapped[re.sub(r"^.*:", "", axis)] = unmapped.get(re.sub(r"^.*:", "", axis), 0) + 1
            excluded["unmapped_axis"] += 1
            continue
        if (axis, member) in conflicts:
            excluded["conflicting"] += 1
            continue
        if re.match(r"^\s*category\b", f.get(HOLDER_FLAG_EL) or "", re.I):
            excluded["category_rows"] += 1
            continue
        name = re.sub(r"\s+", " ", f.get(HOLDER_NAME_EL) or "").strip()
        shares, pct = fnum(f.get(HOLDER_SHARES_EL)), fnum(f.get(HOLDER_PCT_EL))
        if not name or set(name) <= {"*", "-", "."}:
            excluded["unnamed"] += 1
            continue
        if shares is None and pct is None or shares == 0:
            excluded["zero_share"] += 1
            continue
        if shares is not None and shares < 0:
            excluded["conflicting"] += 1
            continue
        pv = None if (pct is None or k is None) else r2(pct * k)
        if pv is not None and not 0 <= pv <= 100.5:
            pv = None
            flags.append({"code": "holder-percentage-out-of-range", "detail": "%s: the reported percentage is outside 0-100 and is not shown" % name})
        section, cat, label, _agg = HOLDER_AXES[ak]
        holders.append({"holder_name": name, "category": cat, "label": label, "section": section, "shares": None if shares is None else int(shares) if shares == int(shares) else shares,
                        "percentage": pv, "axis": re.sub(r"^.*:", "", axis), "member": member})
    holders.sort(key=holder_sort_key)
    totals = {}
    for h in holders:
        t = totals.setdefault(h["section"], {"named_shares": 0, "category_total_shares": None, "named_count": 0})
        t["named_shares"] += h["shares"] or 0
        t["named_count"] += 1
    for ak, (section, cat, label, aggkey) in HOLDER_AXES.items():
        if section in totals and aggkey:
            a = rows.get(aggkey)
            totals[section]["category_total_shares"] = None if a is None or a["shares"] is None else int(a["shares"])
            ct = totals[section]["category_total_shares"]
            if ct is not None and totals[section]["named_shares"] > ct + 1:
                flags.append({"code": "named-holders-exceed-category-total", "detail": "%s: the named rows hold more shares than the category row; check before use" % section})
    status = "available" if holders else "none-disclosed"
    st = nh_state(status, None if holders else "the filing lists no individually disclosed holder with shares for %s" % report_date, day)
    st.update(holders=holders, excluded=excluded, unmapped_axes=dict(sorted(unmapped.items())), section_totals=totals, flags=flags)
    return st


def relabel_named(nh):
    """Version-1 records carry the same holders under the older foreign label. The category / label / section come from the holder's stored XBRL axis (never its name);
    names, shares, percentages, axes and members are not touched. Returns True if anything changed."""
    if not isinstance(nh, dict) or nh.get("version", 1) >= NAMED_VERSION:
        return False
    for h in nh.get("holders") or []:
        m = HOLDER_AXES.get(_axis_key(h.get("axis")))
        if m:
            h["section"], h["category"], h["label"] = m[0], m[1], m[2]
    nh["version"] = NAMED_VERSION
    return True


def nh_core(nh):
    """The part of a named_holders record that says what the filing held (the fetch date is not part of it)."""
    return None if not isinstance(nh, dict) else {k: v for k, v in nh.items() if k != "fetched"}


def select_major(nh, limit=10):
    """The deterministic 'major disclosed shareholders' list: only individually disclosed holders, percentage descending, at most `limit`."""
    if not isinstance(nh, dict) or nh.get("status") != "available":
        return []
    return sorted(nh.get("holders") or [], key=holder_sort_key)[:limit]


PROM = ("shareholdingofpromoterandpromotergroup",)
MF = ("mutualfundsoruti",)
DOM, FOR_NEW = ("institutionsdomestic",), ("institutionsforeign",)
OLD_INST, OLD_FPI = ("institutions",), ("institutionsforeignportfolioinvestor",)
FII_OLD = ("foreigninstitutionalinvestors", "foreigninstitutionalinvestor")
OLD_OTHER = ("otherinstitutions",)
NONINST = ("noninstitutions",)
GOV = ("governments", "goverments")
TRUSTS = ("employeebenefitstrusts", "employeetrusts")
TOTAL, PUBLIC, CUSTODIAN = ("shareholdingpattern",), ("publicshareholding",), ("custodianordrholder",)
NPNP = ("sharesheldbynonpromoternonpublicshareholders",)


def first(rows, names):
    for n in names:
        if n in rows:
            return rows[n]
    return None


def detect_format(rows):
    if first(rows, DOM) or first(rows, FOR_NEW):      # one missing row is a flagged gap later, not an unknown structure
        return FORMAT_NEW
    if first(rows, OLD_INST) or first(rows, OLD_FPI):
        return FORMAT_OLD
    return None


def detect_unit(rows):
    """'percent' (0-100) or 'fraction' (0-1) from the total row; None when it cannot be told."""
    t = first(rows, TOTAL)
    p = t["pct"] if t else None
    if p is None:
        return None
    if 0.99 <= p <= 1.01:
        return "fraction"
    if 99 <= p <= 101:
        return "percent"
    return None


def map_categories(rows):
    """rows (one quarter, {norm: row}) -> dict(values, format_version, percent_unit, diagnostics, flags, problem).
    Never forces a total to 100, never fills a missing category row with a made-up number."""
    flags, unit, fmt = [], detect_unit(rows), detect_format(rows)
    if unit is None:
        return {"problem": "the percentage unit (0-100 or 0-1) could not be detected from the total row", "flags": flags}
    if fmt is None:
        return {"problem": "the category structure is not one of the two known formats", "flags": flags}
    k = 100.0 if unit == "fraction" else 1.0

    def pct(names):
        r = first(rows, names)
        return None if (r is None or r["pct"] is None) else r["pct"] * k

    def sh(names):
        r = first(rows, names)
        return None if r is None else r["shares"]

    def missing(label):
        flags.append({"code": FLAG_ROW_MISSING, "detail": label})

    pub = pct(PUBLIC)
    if first(rows, PROM) is None:
        if pub is not None and pub >= 99.5:
            prom, prom_absent = 0.0, True            # the public holds everything: there is no promoter group (not a parse miss)
        else:
            prom, prom_absent = None, False
            missing("promoters")
    else:
        prom, prom_absent = pct(PROM), False
        if prom is None:
            missing("promoters")
    mf, ni = pct(MF), pct(NONINST)
    if mf is None:
        missing("mutual_funds")
    if ni is None:
        missing("retail_other (non-institutions)")
    gov = pct(GOV) or 0.0
    trusts = pct(TRUSTS)
    if fmt == FORMAT_NEW:
        fii, dom = pct(FOR_NEW), pct(DOM)
        other = None if (dom is None or mf is None) else dom - mf
        s0 = None if None in (prom, fii, dom, ni) else prom + fii + dom + ni + gov
    else:
        fpi, inst = pct(OLD_FPI), pct(OLD_INST)
        old_fii = pct(FII_OLD) or 0.0
        fii = None if fpi is None else fpi + old_fii
        other = None if (inst is None or fpi is None or mf is None) else inst - fpi - old_fii - mf
        dom = inst
        s0 = None if None in (prom, inst, ni) else prom + inst + ni + gov
        oi = first(rows, OLD_OTHER)
        if oi and oi["pct"]:
            flags.append({"code": FLAG_OTHER_INST, "detail": "the filing has an 'Other Institutions' row (%.2f%%); it cannot be split between foreign and domestic, so it sits in other_dii" % (oi["pct"] * k)})
    if fii is None:
        missing("fii")
    if dom is None:
        missing("institutions")
    # employee trusts: a separate holder outside Public (new filings) or nested inside non-institutions (some old ones). The sum tells which.
    mode = "none"
    if trusts:
        mode = "separate" if (s0 is not None and abs(s0 + trusts - 100) < abs(s0 - 100)) else "nested"
        if s0 is None:
            mode = "separate"
    retail = None if ni is None else ni + gov + (trusts if mode == "separate" else 0.0)
    values = {"promoters": r2(prom), "fii": r2(fii), "other_dii": r2(other), "mutual_funds": r2(mf), "retail_other": r2(retail)}
    total = None if None in values.values() else round(sum(values.values()), 2)
    if total is not None and abs(total - 100) > SUM_TOLERANCE:
        flags.append({"code": FLAG_SUM, "detail": "the five categories add up to %.2f" % total})
    # share counts, on the basis the percentages use (the total without the shares underlying depository receipts)
    tot_sh = sh(TOTAL)
    dr = sh(CUSTODIAN)
    basis = None if tot_sh is None else tot_sh - (dr or 0.0)
    derived = None
    conflict = None
    if basis:
        def d(x):
            return None if x is None else round(x / basis * 100, 2)
        ps = 0.0 if prom_absent else sh(PROM)
        mfs, nis, govs, trs = sh(MF), sh(NONINST), sh(GOV) or 0.0, sh(TRUSTS)
        if fmt == FORMAT_NEW:
            fs, ds = sh(FOR_NEW), sh(DOM)
            os_ = None if (ds is None or mfs is None) else ds - mfs
        else:
            fs, ins = sh(OLD_FPI), sh(OLD_INST)
            os_ = None if (ins is None or fs is None or mfs is None) else ins - fs - mfs
        rs = None if nis is None else nis + govs + ((trs or 0.0) if mode == "separate" else 0.0)
        derived = {"promoters": d(ps), "fii": d(fs), "other_dii": d(os_), "mutual_funds": d(mfs), "retail_other": d(rs)}
        diffs = {c: round(abs(values[c] - derived[c]), 2) for c in CATEGORIES if values[c] is not None and derived[c] is not None}
        if diffs and max(diffs.values()) > CONFLICT_PP:
            conflict = {"max_abs_diff_pp": max(diffs.values()), "diff_pp": diffs}
            flags.append({"code": FLAG_CONFLICT, "detail": "the reported percentages differ from the filing's own share counts by up to %.2f percentage points" % max(diffs.values())})
    diagnostics = {
        "total_reported_pct": total,
        "employee_trusts_pct": r2(trusts),
        "employee_trusts_mode": mode,
        "retail_other_excl_trusts": None if retail is None else r2(retail - (trusts if mode == "separate" else 0.0)),
        "governments_pct": r2(gov),
        "promoter_row_absent": prom_absent,
        "share_basis_total": basis,
        "dr_custodian_shares": dr,
        "non_promoter_non_public_shares": sh(NPNP),
        "share_derived_pct": derived,
        "conflict": conflict,
    }
    return {"values": values, "format_version": fmt, "percent_unit": unit, "diagnostics": diagnostics, "flags": flags, "problem": None}


# ---------------------------------------------------------------- one quarter
def source_meta(rec, url):
    revised = any(str(rec.get(k) or "").strip().lower() == "revised" for k in ("revisedStatus", "revisedData"))
    remark = str(rec.get("revisionRemark") or rec.get("revisedRemark") or "").strip()
    return {
        "provider": "NSE",
        "record_id": str(rec.get("recordId")) if rec.get("recordId") not in (None, "") else None,
        "xbrl_url": url,
        "broadcast_date": iso(rec.get("broadcastDate")),
        "submission_date": iso(rec.get("submissionDate")),
        "revised": revised,
        "revision_date": iso(rec.get("revisionDate") or rec.get("revisedDate")) if revised else None,
        "revision_remark": (remark[:600] or None) if revised else None,
    }


def empty_values():
    return {c: None for c in CATEGORIES}


def build_record(rec, qdate, data, used_url, err, day):
    """The new ledger record for one quarter. data is the XBRL bytes, or None with err saying why not."""
    listed = rec.get("xbrl") if isinstance(rec.get("xbrl"), str) else None
    src = source_meta(rec, used_url or listed)
    if used_url and listed and used_url != listed:
        src["xbrl_url_listed"] = listed
    base = {"quarter_end": qdate.isoformat(), "status": "unavailable", "reason": None, "values": empty_values(), "format_version": None,
            "percent_unit": None, "diagnostics": None, "quality": {"status": "ok", "flags": []}, "raw": None, "source": src, "fetched": day, "revisions": [],
            "named_holders": None}
    if data is None:
        base["named_holders"] = nh_state("unavailable", "the XBRL file could not be obtained (%s)" % (err or "no URL listed"), day)
        base["reason"] = "the XBRL file could not be obtained (%s)" % (err or "no URL listed")
        base["quality"] = {"status": "flagged", "flags": [{"code": "xbrl-unavailable", "detail": base["reason"]}]}
        return base
    try:
        parsed = parse_xbrl(data)
    except ET.ParseError as e:
        base["reason"] = "the XBRL file could not be parsed (%s)" % type(e).__name__
        base["named_holders"] = nh_state("unavailable", base["reason"], day)
        base["quality"] = {"status": "flagged", "flags": [{"code": "xbrl-unparsable", "detail": base["reason"]}]}
        return base
    flags = []
    want = parsed["report_date"] or qdate.isoformat()
    if parsed["report_date"] and parsed["report_date"] != qdate.isoformat():
        flags.append({"code": FLAG_REPORT_DATE, "detail": "the filing says %s; the index says %s; the filing's own date is used" % (parsed["report_date"], qdate.isoformat())})
    rows = {n: r for (n, per), r in parsed["rows"].items() if per == want}
    if not rows:
        base["reason"] = "the XBRL holds no category rows for %s" % want
        base["named_holders"] = nh_state("unavailable", base["reason"], day)
        base["quality"] = {"status": "flagged", "flags": [{"code": "xbrl-no-rows", "detail": base["reason"]}]}
        return base
    if parsed["duplicates"]:
        flags.append({"code": FLAG_DUPLICATE, "detail": "the same member appears with different values: " + ", ".join(parsed["duplicates"][:6])})
    m = map_categories(rows)
    base["raw"] = {"total_shares": (first(rows, TOTAL) or {}).get("shares"),
                   "rows": {r["member"]: [r["shares"], r["pct"]] for r in sorted(rows.values(), key=lambda x: x["member"])}}
    if m["problem"]:
        base["reason"] = m["problem"]
        base["named_holders"] = nh_state("unavailable", m["problem"], day)
        base["quality"] = {"status": "flagged", "flags": flags + [{"code": "xbrl-unmappable", "detail": m["problem"]}]}
        return base
    base.update(status="available", values=m["values"], format_version=m["format_version"], percent_unit=m["percent_unit"], diagnostics=m["diagnostics"])
    flags += m["flags"]
    base["quality"] = {"status": "flagged" if flags else "ok", "flags": flags}
    base["named_holders"] = build_named_holders(data, want, m["percent_unit"], rows, day)
    return base


# ---------------------------------------------------------------- the index
def select_filings(records):
    """(keep: {date: record}, skipped: [..]). Only quarter-end filings from START_QUARTER on are quarters; the latest broadcast wins a duplicate."""
    keep, skipped = {}, []
    for r in records:
        d = pdate(r.get("date"))
        info = {"date": d.isoformat() if d else None, "record_id": str(r.get("recordId")) if r.get("recordId") not in (None, "") else None,
                "broadcast_date": iso(r.get("broadcastDate"))}
        if d is None:
            skipped.append(dict(info, reason="unparsable date"))
        elif (d.month, d.day) not in QUARTER_ENDS:
            skipped.append(dict(info, reason="off-cycle filing (not a quarter end)"))
        elif d < START_QUARTER:
            skipped.append(dict(info, reason="before Sep 2021 (outside the verified window)"))
        elif d in keep:
            a, b = keep[d], r
            if _newer(b, a):
                skipped.append(dict(info_of(a), reason="superseded duplicate"))
                keep[d] = b
            else:
                skipped.append(dict(info, reason="superseded duplicate"))
        else:
            keep[d] = r
    return keep, skipped


def info_of(r):
    d = pdate(r.get("date"))
    return {"date": d.isoformat() if d else None, "record_id": str(r.get("recordId")), "broadcast_date": iso(r.get("broadcastDate"))}


def _stamp(r):
    try:
        return dt.datetime.strptime(str(r.get("broadcastDate")).strip().upper(), "%d-%b-%Y %H:%M:%S")
    except ValueError:
        d = pdate(r.get("broadcastDate"))
        return dt.datetime.combine(d, dt.time()) if d else dt.datetime.min


def _newer(a, b):
    return (_stamp(a), fnum(a.get("recordId")) or 0) > (_stamp(b), fnum(b.get("recordId")) or 0)


# ---------------------------------------------------------------- the ledger
def new_stock(symbol, isin, name):
    return {"symbol": symbol, "isin": isin, "name": name, "quarters": []}


def sort_quarters(stock):
    stock["quarters"].sort(key=lambda r: r["quarter_end"], reverse=True)


def _snapshot(rec, day):
    return {"recorded": day, "status": rec.get("status"), "reason": rec.get("reason"), "values": copy.deepcopy(rec.get("values")),
            "format_version": rec.get("format_version"), "source": copy.deepcopy(rec.get("source")), "quality": copy.deepcopy(rec.get("quality")),
            "named_holders": copy.deepcopy(rec.get("named_holders"))}


def _core(rec):
    return (rec.get("status"), json.dumps(rec.get("values"), sort_keys=True), (rec.get("source") or {}).get("record_id"))


def _nh_ok(nh):
    return isinstance(nh, dict) and nh.get("status") in ("available", "none-disclosed", "not-in-filing")


def _carry_named(old, new):
    """Named holders follow the no-loss rule: a read result is never replaced by a failed read, and the same NSE version keeps the holders it already has."""
    o, n = (old or {}).get("named_holders"), new.get("named_holders")
    if _nh_ok(o) and _core(old) == _core(new):
        new["named_holders"] = o
    return new


def merge_record(old, new, day):
    rec, ev = _merge_record(old, new, day)
    if rec is old and old is not None and old.get("status") == "available" and new.get("status") == "available" and not _nh_ok(old.get("named_holders")) and _nh_ok(new.get("named_holders")):
        old["named_holders"] = new["named_holders"]      # a same-version quarter that had no named holders yet gains them; nothing else changes
        return old, "named-added"
    if rec is old and old is not None and old.get("status") != "available" and not isinstance(old.get("named_holders"), dict) and isinstance(new.get("named_holders"), dict):
        old["named_holders"] = new["named_holders"]      # an unavailable quarter records why its holders are unavailable too (nothing else changes)
    if rec is not old:
        _carry_named(old, rec)
    return rec, ev


def _merge_record(old, new, day):
    """(record, event). Events: added, unchanged, revised, refreshed, resurrected, kept-existing. Never loses a value or a quarter."""
    if old is None:
        return new, "added"
    if old.get("status") == "available" and new.get("status") != "available":
        return old, "kept-existing"                      # a failed re-fetch never erases a quarter that was read
    if old.get("status") != "available" and new.get("status") == "available":
        new["revisions"] = list(old.get("revisions") or []) + [_snapshot(old, day)]
        return new, "resurrected"
    if old.get("status") != "available":                 # unavailable both times: the latest attempt describes it; history is kept
        new["revisions"] = list(old.get("revisions") or [])
        if _core(old) == _core(new) and old.get("reason") == new.get("reason") and old.get("source") == new.get("source"):
            return old, "unchanged"
        return new, "refreshed"
    kept = []
    for c in CATEGORIES:
        if new["values"].get(c) is None and old["values"].get(c) is not None:
            new["values"][c] = old["values"][c]          # an existing non-null value never silently becomes null
            kept.append(c)
    if kept:
        new["quality"]["flags"].append({"code": FLAG_KEPT, "detail": "the new filing gave no value for " + ", ".join(kept) + "; the previous value is kept"})
        new["quality"]["status"] = "flagged"
    if _core(old) == _core(new):
        same_rest = all(old.get(k) == new.get(k) for k in ("format_version", "percent_unit", "diagnostics", "quality", "raw", "source"))
        if same_rest:
            return old, "unchanged"
        new["revisions"], new["fetched"] = list(old.get("revisions") or []), old.get("fetched")
        return new, "refreshed"                          # same values and same NSE version: only the explanation changed
    new["revisions"] = list(old.get("revisions") or []) + [_snapshot(old, day)]
    return new, "revised"


def expected_quarters(newest):
    """Every quarter end from START_QUARTER to newest (a date)."""
    out, y, m = [], START_QUARTER.year, START_QUARTER.month
    while (y, m) <= (newest.year, newest.month):
        out.append(dt.date(y, m, {3: 31, 6: 30, 9: 30, 12: 31}[m]).isoformat())
        m += 3
        if m > 12:
            m, y = m - 12, y + 1
    return out


def fill_named(nse, old, listed, d, rec, day, budget):
    """Gap-fill: an available quarter of the same NSE version that has no named holders yet. The XBRL is read again ONLY to add named_holders, and only if its
    aggregate rows reproduce the stored ones exactly; the stored aggregate values are never touched. Returns the event."""
    data, used, e = (None, None, "the index lists no XBRL file") if not listed else fetch_xbrl(nse, listed)
    if listed:
        budget[0] -= 1
    new = build_record(rec, d, data, used, e, day)
    if new.get("status") != "available":
        if not _nh_ok(old.get("named_holders")):
            old["named_holders"] = nh_state("unavailable", new.get("reason") or "the XBRL file could not be read", day)
        return "named-unavailable"
    if new["values"] != old.get("values"):
        old["named_holders"] = nh_state("unavailable", "the filing now reads differently from the stored aggregates; named holders are not added to a quarter whose values would change", day)
        return "named-skipped"
    old["named_holders"] = new["named_holders"]
    return "named-added"


def process_stock(nse, ledger, symbol, day, budget, refresh_all=False):
    """Fetch the index and every quarter that is new, revised or not yet read. Returns (events, skipped, error)."""
    recs, err = nse.index(symbol)
    if err:
        return {}, [], "the NSE filing index could not be read (%s)" % err
    if not recs:
        return {}, [], "the NSE filing index was empty"
    keep, skipped = select_filings(recs)
    if not keep:
        return {}, skipped, "the NSE filing index held no quarter-end filings from Sep 2021"
    sample = next(iter(keep.values()))
    isin = next((str(r["isin"]).strip() for r in list(keep.values()) + list(recs) if isinstance(r.get("isin"), str) and r["isin"].strip()), None)
    st = ledger["stocks"].setdefault(symbol, new_stock(symbol, isin, sample.get("name")))
    if isin and not st.get("isin"):
        st["isin"] = isin
    have = {r["quarter_end"]: r for r in st["quarters"]}
    events = {}
    for d in sorted(keep, reverse=True):
        rec, q = keep[d], d.isoformat()
        old = have.get(q)
        rid = str(rec.get("recordId")) if rec.get("recordId") not in (None, "") else None
        same_version = bool(old and old.get("status") == "available" and (old.get("source") or {}).get("record_id") == rid)
        if same_version and not refresh_all and _nh_ok(old.get("named_holders")):
            events[q] = "relabelled" if relabel_named(old["named_holders"]) else "unchanged"
            continue
        listed = rec.get("xbrl") if isinstance(rec.get("xbrl"), str) and rec.get("xbrl", "").startswith("https://") else None
        if budget[0] <= 0:
            events[q] = "not-attempted"
            continue
        if same_version and not refresh_all:
            events[q] = fill_named(nse, old, listed, d, rec, day, budget)
            continue
        data, used, e = (None, None, "the index lists no XBRL file") if not listed else fetch_xbrl(nse, listed)
        if listed:
            budget[0] -= 1
        new = build_record(rec, d, data, used, e, day)
        merged, ev = merge_record(old, new, day)
        events[q] = ev
        if old is None:
            st["quarters"].append(merged)
        else:
            st["quarters"][st["quarters"].index(old)] = merged
        have[q] = merged
    sort_quarters(st)
    return events, skipped, None


# ---------------------------------------------------------------- the Upstox cross-check (latest quarter only; never changes a value)
UPSTOX_CAT = {"promoters": "promoters", "fii": "fii", "other_dii": "other_dii", "mutual_funds": "mutual_funds", "retail_other": "retail_and_other"}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def upstox_isins():
    """symbol -> equity ISIN from the official Upstox NSE instrument file (no token). NSE's filing index can carry a legacy or partly-paid ISIN (or none),
    which Upstox does not know, so the cross-check asks Upstox about the ISIN Upstox itself lists for the symbol. Never used for any stored value."""
    try:
        return {k: v.get("isin") for k, v in load_instruments().items() if v.get("isin")}
    except SystemExit:
        return {}


def cross_check(api, stock, isin=None):
    latest = next((r for r in stock["quarters"] if r.get("status") == "available"), None)
    if latest is None:
        return {"status": "unavailable", "reason": "no available quarter to compare"}
    if api is None:
        return {"status": "skipped", "quarter_end": latest["quarter_end"], "reason": "no Upstox token in this run"}
    use = isin or stock.get("isin")
    body, err = api.get(use + "/share-holdings") if use else (None, "no ISIN (neither Upstox's instrument list nor the NSE filing gives one)")
    if err:
        return {"status": "unavailable", "quarter_end": latest["quarter_end"], "reason": "Upstox: " + err, "upstox_isin": use}
    d = dt.date.fromisoformat(latest["quarter_end"])
    label = "%s %d" % (MONTH_NAMES[d.month - 1], d.year)
    up = {}
    for item in (body.get("data") or []) if isinstance(body, dict) else []:
        for h in (item.get("history") or []) if isinstance(item, dict) else []:
            if isinstance(h, dict) and h.get("period") == label and isnum(h.get("value")):
                up[item.get("category")] = float(h["value"])
    if not up:
        return {"status": "unavailable", "quarter_end": latest["quarter_end"], "reason": "Upstox does not return %s" % label, "upstox_isin": use}
    diffs = {}
    for c in CATEGORIES:
        o, u = latest["values"].get(c), up.get(UPSTOX_CAT[c])
        diffs[c] = {"official": o, "upstox": u, "diff": None if (o is None or u is None) else round(o - u, 2)}
    nums = [abs(v["diff"]) for v in diffs.values() if v["diff"] is not None]
    ok = bool(nums) and max(nums) <= CROSS_TOLERANCE and len(nums) == len(CATEGORIES)
    return {"status": "match" if ok else "mismatch", "quarter_end": latest["quarter_end"], "max_abs_diff": max(nums) if nums else None, "categories": diffs, "upstox_isin": use}


# ---------------------------------------------------------------- the published file
def public_quarter(r):
    out = {k: copy.deepcopy(r.get(k)) for k in ("quarter_end", "status", "reason", "values", "format_version", "percent_unit", "diagnostics", "quality", "source", "fetched", "revisions")}
    out["named_holders"] = copy.deepcopy(r.get("named_holders")) or nh_state("unavailable", "named holders have not been read for this quarter yet", r.get("fetched"))
    return out


def major_for(qs):
    """The stock-level 'major disclosed shareholders': the NEWEST available quarter, top 10 by percentage. Never an older quarter's holders under a newer date."""
    latest = next((r for r in qs if r.get("status") == "available"), None)
    if latest is None:
        return {"quarter_end": None, "status": "unavailable", "reason": "no available quarter", "basis": HOLDER_BASIS, "holders": []}
    nh = latest.get("named_holders") or {}
    return {"quarter_end": latest["quarter_end"], "status": nh.get("status") or "unavailable", "reason": nh.get("reason"), "basis": HOLDER_BASIS,
            "holders": [{k: h.get(k) for k in ("holder_name", "category", "label", "shares", "percentage")} for h in select_major(nh)]}


def build_output(ledger, extra, day):
    """out/shareholding.json from the ledger. extra[symbol] = {'skipped', 'cross_check', 'error'}."""
    stocks, total, avail, flagged = {}, 0, 0, 0
    for sym in SYMBOLS:
        st = ledger["stocks"].get(sym)
        if not st:
            continue
        qs = [public_quarter(r) for r in st["quarters"]]
        have = {r["quarter_end"] for r in qs}
        newest = max(have) if have else None
        exp = expected_quarters(dt.date.fromisoformat(newest)) if newest else []
        e = extra.get(sym) or {}
        stocks[sym] = {
            "symbol": sym, "isin": st.get("isin"), "name": st.get("name"), "quarters": qs,
            "coverage": {"count": len(qs), "available": sum(1 for r in qs if r["status"] == "available"), "oldest": min(have) if have else None, "newest": newest,
                         "missing_quarters": [q for q in exp if q not in have], "unavailable_quarters": [r["quarter_end"] for r in qs if r["status"] != "available"]},
            "major_holders": major_for(qs),
            "off_cycle_filings": e.get("skipped") or [],
            "cross_check": e.get("cross_check"),
            "error": e.get("error"),
        }
        total += len(qs)
        avail += stocks[sym]["coverage"]["available"]
        flagged += sum(1 for r in qs if (r.get("quality") or {}).get("status") == "flagged")
    return {"schema": SCHEMA_VERSION, "as_of": day, "categories": CATEGORIES, "notes": NOTES, "stocks": stocks,
            "summary": {"stocks": len(stocks), "quarter_records": total, "available": avail, "unavailable": total - avail, "flagged": flagged}}


def validate_named(nh, status):
    if not isinstance(nh, dict):
        return ["missing"]
    p = []
    if nh.get("status") not in ("available", "none-disclosed", "not-in-filing", "unavailable"):
        p.append("bad status")
    hs = nh.get("holders")
    if not isinstance(hs, list):
        return p + ["holders is not a list"]
    if nh.get("status") != "available" and hs:
        p.append("holders present without status available")
    if nh.get("status") == "available" and not hs:
        p.append("available without holders")
    if status != "available" and nh.get("status") == "available":
        p.append("holders on an unavailable quarter")
    for h in hs:
        if not isinstance(h, dict) or not h.get("holder_name") or h.get("label") not in (L_PROMOTER, L_MF, L_INS, L_DII, L_FPI, L_FDI, L_FOREIGN, L_PUBLIC, L_OTHER):
            p.append("holder without a name or a known label")
        elif not (h.get("shares") is None or (isnum(h["shares"]) and h["shares"] > 0)):
            p.append("%s: shares must be positive" % h["holder_name"])
        elif h.get("percentage") is not None and not (isnum(h["percentage"]) and 0 <= h["percentage"] <= 100.5):
            p.append("%s: percentage out of range" % h["holder_name"])
    if hs != sorted(hs, key=holder_sort_key):
        p.append("holders are not sorted")
    return p


def validate_doc(doc):
    p = []
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION or not isinstance(doc.get("stocks"), dict):
        return ["wrong shape or schema"]
    if doc.get("categories") != CATEGORIES:
        p.append("categories changed")
    for sym, st in doc["stocks"].items():
        seen = set()
        qs = st.get("quarters")
        if not isinstance(qs, list):
            p.append(sym + ": quarters is not a list")
            continue
        if [r.get("quarter_end") for r in qs] != sorted((r.get("quarter_end") for r in qs), reverse=True):
            p.append(sym + ": quarters are not newest first")
        for r in qs:
            w = "%s %s: " % (sym, r.get("quarter_end"))
            q = r.get("quarter_end")
            if not (isinstance(q, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", q) and (int(q[5:7]), int(q[8:])) in QUARTER_ENDS):
                p.append(w + "not a quarter end")
            if q in seen:
                p.append(w + "duplicate quarter")
            seen.add(q)
            if r.get("status") not in STATUS:
                p.append(w + "bad status")
            vals = r.get("values")
            if not isinstance(vals, dict) or list(vals) != CATEGORIES:
                p.append(w + "values must hold exactly the five categories")
                continue
            for c, v in vals.items():
                if v is not None and not (isnum(v) and 0 <= v <= 100.5):
                    p.append(w + "%s is not a percentage" % c)
            src = r.get("source") or {}
            if r.get("status") == "available":
                if r.get("format_version") not in (FORMAT_OLD, FORMAT_NEW) or r.get("percent_unit") not in ("percent", "fraction"):
                    p.append(w + "available quarter without format_version / percent_unit")
                if not src.get("record_id") or not str(src.get("xbrl_url") or "").startswith("https://"):
                    p.append(w + "available quarter without record id / https XBRL URL")
                if any(v is None for v in vals.values()) and not any(f.get("code") == FLAG_ROW_MISSING for f in (r.get("quality") or {}).get("flags", [])):
                    p.append(w + "a null category without a flag")
            else:
                if any(v is not None for v in vals.values()):
                    p.append(w + "an unavailable quarter must hold no values")
                if not r.get("reason"):
                    p.append(w + "an unavailable quarter must say why")
            problems_nh = validate_named(r.get("named_holders"), r.get("status"))
            p += [w + "named_holders: " + x for x in problems_nh]
            q_ = r.get("quality") or {}
            if q_.get("status") not in ("ok", "flagged") or (q_.get("status") == "ok") != (not q_.get("flags")):
                p.append(w + "quality status does not match its flags")
    return p


def count_values(ledger):
    return sum(1 for s in ledger["stocks"].values() for r in s.get("quarters", []) for v in (r.get("values") or {}).values() if v is not None)


# ---------------------------------------------------------------- main
def main():
    day = today()
    try:
        source, kind = sl.choose_source(os.environ.get("SHAREHOLDING_LEDGER_DIR", "").strip(), os.environ.get("SHAREHOLDING_INIT", "").strip().lower() == "true")
        old = sl.load_ledger(source) if source else sl.new_ledger()
    except sl.StorageError as e:
        print("::error::" + str(e))
        return 1
    ledger = copy.deepcopy(old)
    wanted = [s.strip().upper() for s in os.environ.get("SHAREHOLDING_SYMBOLS", "").split(",") if s.strip()] or list(SYMBOLS)
    bad = [s for s in wanted if s not in SYMBOLS]
    if bad:
        print("::error::Unknown symbols: " + ", ".join(bad))
        return 1
    refresh_all = os.environ.get("SHAREHOLDING_REFRESH", "").strip().lower() == "all"
    budget = [int(os.environ.get("SHAREHOLDING_MAX_DOWNLOADS", "") or DEFAULT_MAX_DOWNLOADS)]
    nse = Nse()
    extra, failed = {}, 0
    log("Ledger source:", kind, "- symbols:", ",".join(wanted))
    for sym in wanted:
        events, skipped, err = process_stock(nse, ledger, sym, day, budget, refresh_all)
        extra[sym] = {"skipped": skipped, "error": err}
        if err:
            failed += 1
            log(sym, "- PROBLEM:", err)
            continue
        counts = {}
        for ev in events.values():
            counts[ev] = counts.get(ev, 0) + 1
        log(sym, "-", ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items())), "| skipped filings:", len(skipped))
    if failed == len(wanted):
        print("::error::No stock could be processed; nothing is written.")
        return 1
    api = None
    token = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
    if token:
        api = Upstox(token, 20)
    rejected = False
    isins = upstox_isins() if api else {}
    for sym in wanted:
        st = ledger["stocks"].get(sym)
        if not st:
            continue
        if rejected:
            extra[sym]["cross_check"] = {"status": "unavailable", "reason": "Upstox rejected the token earlier in this run"}
            continue
        try:
            extra[sym]["cross_check"] = cross_check(api, st, isins.get(sym))
        except SystemExit:
            extra[sym]["cross_check"] = {"status": "unavailable", "reason": "Upstox rejected the token"}
            rejected = True
    problems = sl.no_loss_problems(old, ledger)
    doc = build_output(ledger, extra, day)
    problems += ["output: " + x for x in validate_doc(doc)]
    if problems:
        for x in problems[:30]:
            print("::error::" + x)
        return 1
    out_ledger = Path(os.environ.get("SHAREHOLDING_LEDGER_OUT", "").strip() or LEDGER_FILE)
    out_ledger.parent.mkdir(parents=True, exist_ok=True)
    out_ledger.write_text(json.dumps(ledger, indent=1, allow_nan=False, sort_keys=False) + "\n", encoding="utf-8")
    write_json(OUT_FILE, doc)
    s = doc["summary"]
    log("Wrote", OUT_FILE, "-", s["stocks"], "stocks,", s["quarter_records"], "quarter records,", s["unavailable"], "unavailable,", s["flagged"], "flagged; downloads:", nse.downloads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
