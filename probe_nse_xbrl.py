"""
probe_nse_xbrl.py - ONE-OFF, READ-ONLY technical feasibility probe (StockLens Phase 4 Step 4, "Tier 1").

Question: can a GitHub Actions runner reach NSE's official financial-results RSS feed and the XBRL files that feed links to,
and what does the XBRL structure look like?

Strict scope (decided by the project owner):
  * Only the official RSS feed https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml
    and at most 3 XBRL files that this feed links to directly.
  * No search of NSE's filing listing, no look-up by company, no attempt to find older filings (e.g. FY2022).
  * Honest User-Agent, no cookies (no session), a single attempt per URL (no retries), 2 s pause between requests,
    redirects are not followed. A block, rate limit or error (403, 429, 5xx, ...) ends the probe immediately - nothing is worked around.
  * Nothing from NSE is stored: the files are parsed in memory only. The report holds metadata and tag NAMES with fact COUNTS,
    never a financial value. It writes nothing to out/ or data/.
  * No token or secret is used.
"""
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests

RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml"
ALLOWED_HOSTS = {"archives.nseindia.com", "nsearchives.nseindia.com"}
ALLOWED_PATH = re.compile(r"^/+corporate/xbrl/[A-Za-z0-9_.-]+\.xml$")
USER_AGENT = "StockLens-feasibility-probe/1.0 (one-off read-only check; +https://github.com/Arnavdev11/stock)"
MAX_FILES = 3
PAUSE_SECONDS = 2.0
TIMEOUT = 30
MAX_RSS_BYTES = 2_000_000
MAX_XBRL_BYTES = 8_000_000
OUT_DIR = Path("probe_output")
OUT_FILE = OUT_DIR / "nse_xbrl_probe_report.json"

# concept -> regex on the element's LOCAL NAME (candidates only; the report also lists every matching name so the real tags are visible)
CONCEPTS = {
    "revenue": re.compile(r"^(revenuefromoperations|revenue|revenuefromoperationsnet|interestearned|income?fromoperations)$", re.I),
    "total_revenue": re.compile(r"^(income|totalincome|totalrevenue|totalincomefromoperations)$", re.I),
    "profit_before_tax": re.compile(r"^(profitbeforetax|profitlossbeforetax|profitlossfromordinaryactivitiesbeforetax|profitlossbeforetaxfromcontinuingoperations)$", re.I),
    "profit_after_tax": re.compile(r"^(profitloss|profitlossforperiod|profitlossfromordinaryactivitiesaftertax|profitlossfortheperiod|netprofitlossfortheperiod|profitaftertax)$", re.I),
    "eps": re.compile(r"(earnings?lossper(share|equityshare)|earningspershare|basicearnings|dilutedearnings)", re.I),
    "operating_cash_flow": re.compile(r"(cashflows?(fromusedin|from|used)?.*operatingactivities|netcash.*operating)", re.I),
}
BROAD = re.compile(r"(revenue|income|profit|loss|earning|cashflow|operatingactivities|interestearned)", re.I)
MAX_NAMES = 60


class Stop(Exception):
    """Raised to end the probe at once (blocked / error). Nothing is retried or worked around."""


# ---------------------------------------------------------------- safe helpers
def clean(text, n=120):
    return re.sub(r"[^A-Za-z0-9 &/()'.,:|+_-]", "?", str(text))[:n]


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def safe_xml(data):
    """Parse XML from bytes; refuse DTDs / entity declarations (no entity-expansion tricks)."""
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data[:200000].upper():
        raise ValueError("DTD or entity declaration present - not parsed")
    return ET.fromstring(data)


def get(url, limit):
    """One GET, no cookies, no redirects, no retries. Returns (status, bytes, content_type). Raises Stop on any problem."""
    host = urlparse(url).hostname
    if host not in ALLOWED_HOSTS:
        raise Stop("refused to request a host outside the NSE archive hosts")
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,*/*;q=0.5"},
                         timeout=TIMEOUT, allow_redirects=False, stream=True)
    except requests.RequestException as e:
        raise Stop("network error: " + type(e).__name__)
    ctype = clean(r.headers.get("Content-Type", ""), 60)
    if r.status_code != 200:
        r.close()
        raise Stop("HTTP %d%s" % (r.status_code, " (blocked or rate limited - not worked around)" if r.status_code in (401, 403, 429) else ""))
    body = b""
    for chunk in r.iter_content(65536):
        body += chunk
        if len(body) > limit:
            r.close()
            raise Stop("response larger than the probe's size limit")
    return r.status_code, body, ctype


# ---------------------------------------------------------------- RSS
def parse_description(desc):
    out = {}
    for part in str(desc or "").split("|"):
        k, _, v = part.partition(":")
        if v:
            out[k.strip().upper()] = v.strip()
    return out


def rss_items(root):
    items = []
    for it in root.iter("item"):
        d = {c.tag: (c.text or "").strip() for c in it}
        link = d.get("link", "")
        p = urlparse(link)
        if p.scheme == "https" and p.hostname in ALLOWED_HOSTS and ALLOWED_PATH.match(p.path or ""):
            f = parse_description(d.get("description"))
            items.append({"company": clean(d.get("title", "")), "url": link, "pub_date": clean(d.get("pubDate", ""), 40),
                          "relating_to": clean(f.get("RELATING TO", ""), 40), "audited": clean(f.get("AUDITED/UNAUDITED", ""), 20),
                          "basis": clean(f.get("CONSOLIDATED/NON-CONSOLIDATED", ""), 30), "ind_as": clean(f.get("IND AS/ NON IND AS", f.get("IND AS/NON IND AS", "")), 30),
                          "period_type": clean(f.get("PERIOD", ""), 20), "period_ended": clean(f.get("PERIOD ENDED", ""), 20),
                          "file_format": clean(Path(p.path).name.split("_")[0], 20)})
    return items


def choose(items):
    """Up to MAX_FILES distinct files, no company search: one per distinct file format first (annual preferred), then the first remaining."""
    seen_urls, picked, formats = set(), [], set()
    ordered = sorted(items, key=lambda x: (0 if x["period_type"].lower() == "annual" else 1))
    for it in ordered:
        if it["url"] in seen_urls or it["file_format"] in formats:
            continue
        picked.append(it); seen_urls.add(it["url"]); formats.add(it["file_format"])
        if len(picked) == MAX_FILES:
            return picked
    for it in items:
        if it["url"] not in seen_urls:
            picked.append(it); seen_urls.add(it["url"])
            if len(picked) == MAX_FILES:
                break
    return picked


# ---------------------------------------------------------------- XBRL structure (names and counts only)
def analyse_xbrl(root):
    names, ctx_periods, units = {}, set(), set()
    namespaces, schema_refs, elements = set(), [], 0
    for el in root.iter():
        elements += 1
        tag = el.tag if isinstance(el.tag, str) else ""
        ns = tag[1:].split("}")[0] if tag.startswith("{") else ""
        if ns:
            namespaces.add(clean(ns, 90))
        name = local(tag)
        if name == "schemaRef":
            schema_refs.append(clean(el.attrib.get("{http://www.w3.org/1999/xlink}href", ""), 140))
        elif name == "context":
            ctx_periods.add(clean(" ".join(x.text.strip() for x in el.iter() if x.text and local(x.tag) in ("startDate", "endDate", "instant")), 40))
        elif name == "unit":
            units.add(clean(" ".join(x.text.strip() for x in el.iter() if x.text and local(x.tag) == "measure"), 30))
        elif el.text and el.text.strip() and not len(el) and name:        # a fact: count it, never keep its value
            names[name] = names.get(name, 0) + 1
    concept = {}
    for key, rx in CONCEPTS.items():
        hits = sorted(n for n in names if rx.search(n))
        concept[key] = {"present": bool(hits), "matching_tag_names": [{"name": clean(n, 90), "fact_count": names[n]} for n in hits[:MAX_NAMES]]}
    broad = sorted(n for n in names if BROAD.search(n))
    return {"root_element": clean(local(root.tag), 60), "total_elements": elements, "distinct_fact_tag_names": len(names),
            "namespace_count": len(namespaces), "namespaces": sorted(namespaces)[:12], "schema_refs": schema_refs[:5],
            "context_count_distinct_periods": len(ctx_periods), "context_periods": sorted(ctx_periods)[:10], "units": sorted(units)[:6],
            "concepts": concept, "other_income_profit_loss_earnings_cashflow_tag_names": [clean(n, 90) for n in broad[:MAX_NAMES]],
            "_names": set(names)}


def compare_formats(files):
    """Differences between sampled file formats (e.g. banking vs other). Only meaningful when more than one format was sampled."""
    by = {}
    for f in files:
        if f.get("analysis"):
            by.setdefault(f["file_format"], []).append(f["analysis"])
    if len(by) < 2:
        return {"comparable": False, "note": "only one file format was in the sample, so banking vs non-banking cannot be determined"}
    rows = {fmt: {k: any(a["concepts"][k]["present"] for a in v) for k in CONCEPTS} for fmt, v in by.items()}
    names = {fmt: set().union(*(a["_names"] for a in v)) for fmt, v in by.items()}
    fmts = sorted(by)
    return {"comparable": True, "concepts_present_by_format": rows,
            "tag_name_overlap": {a + " vs " + b: {"only_in_first": len(names[a] - names[b]), "only_in_second": len(names[b] - names[a]), "shared": len(names[a] & names[b])}
                                 for i, a in enumerate(fmts) for b in fmts[i + 1:]}}


# ---------------------------------------------------------------- main
def run(fetch=get, sleep=time.sleep):
    report = {"purpose": "Tier 1 feasibility probe: official NSE RSS feed and up to 3 directly linked XBRL files. Metadata and tag names only; no financial values.",
              "rss_url": RSS_URL, "user_agent": USER_AGENT, "stopped_because": None, "requests_made": 0, "files": []}
    requests_made = 0
    try:
        requests_made += 1
        status, body, ctype = fetch(RSS_URL, MAX_RSS_BYTES)
        report["rss"] = {"reachable": True, "http_status": status, "content_type": ctype, "bytes": len(body)}
        root = safe_xml(body)
        items = rss_items(root)
        report["rss"].update({"items_with_valid_xbrl_links": len(items), "formats_in_feed": sorted({i["file_format"] for i in items}),
                              "feed_title": clean((root.findtext("channel/title") or ""), 80)})
        picked = choose(items)
        report["rss"]["files_selected"] = len(picked)
        for it in picked:
            sleep(PAUSE_SECONDS)
            entry = dict((k, v) for k, v in it.items() if k != "url")
            entry["url"] = it["url"]
            report["files"].append(entry)
            try:
                requests_made += 1
                st, data, ct = fetch(it["url"], MAX_XBRL_BYTES)
                entry.update({"reachable": True, "http_status": st, "content_type": ct, "bytes": len(data)})
                try:
                    entry["parses"] = True
                    entry["analysis"] = analyse_xbrl(safe_xml(data))
                except (ET.ParseError, ValueError) as e:
                    entry["parses"] = False
                    entry["parse_error"] = clean(type(e).__name__ + ": " + str(e), 80)
            except Stop as s:
                entry.update({"reachable": False, "problem": str(s)})
                report["stopped_because"] = str(s)
                break
    except Stop as s:
        report.setdefault("rss", {"reachable": False})
        report["rss"]["problem"] = str(s)
        report["stopped_because"] = str(s)
    except (ET.ParseError, ValueError) as e:
        report["rss"]["parses"] = False
        report["rss"]["problem"] = clean(type(e).__name__ + ": " + str(e), 80)
    report["requests_made"] = requests_made
    report["format_comparison"] = compare_formats(report["files"])
    for f in report["files"]:
        if f.get("analysis"):
            f["analysis"].pop("_names", None)
    return report


def print_summary(rep):
    log = lambda *a: print(*a, flush=True)
    rss = rep.get("rss", {})
    log("RSS feed reachable:", rss.get("reachable"), "| HTTP", rss.get("http_status"), "| items with XBRL links:", rss.get("items_with_valid_xbrl_links"),
        "| formats:", rss.get("formats_in_feed"), "| problem:", rss.get("problem", "none"))
    for f in rep["files"]:
        a = f.get("analysis") or {}
        log("FILE format=%s | %s | %s | %s | %s | reachable=%s HTTP %s | parses=%s | facts tags=%s | concepts present: %s" % (
            f["file_format"], f["company"], f["period_type"] + " " + f["period_ended"], f["basis"], f["audited"], f.get("reachable"), f.get("http_status"),
            f.get("parses"), a.get("distinct_fact_tag_names"), {k: v["present"] for k, v in a.get("concepts", {}).items()} if a else "-"))
    log("Format comparison:", json.dumps(rep["format_comparison"])[:600])
    log("Requests made:", rep["requests_made"], "| stopped because:", rep["stopped_because"] or "nothing - completed")


def main():
    rep = run()
    print_summary(rep)
    OUT_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(rep, indent=1, allow_nan=False), encoding="utf-8")
    print("Wrote", OUT_FILE, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
