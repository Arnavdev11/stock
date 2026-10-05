"""TEMPORARY Phase 5H.2B probe (throwaway branch only). Read-only: NSE shareholding index + XBRL for one symbol.
Uses no token. Prints only public filing data (never headers/cookies). Results go to GitHub annotations."""
import datetime as dt, json, os, re, sys, time
import xml.etree.ElementTree as ET
import requests

SYM = os.environ["SYM"]
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9", "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern"}
IDX = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol=" + SYM
CH = []   # chunks of <= 3800 chars, written to a file and printed as annotations by later steps (the API shows only 4096 chars)


def emit(title, text):
    text = str(text)
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > 3700:
            if cur: parts.append(cur); cur = ""
            parts.append(line[:3700]); line = line[3700:]
        if len(cur) + len(line) + 1 > 3700 and cur: parts.append(cur); cur = ""
        cur += line + "\n"
    if cur: parts.append(cur)
    for i, ptxt in enumerate(parts, 1):
        CH.append({"t": "%s %s %d/%d" % (SYM, title, i, len(parts)), "m": ptxt})
    json.dump(CH, open("/tmp/chunks_" + SYM + ".json", "w"))


if len(sys.argv) > 1:   # emit mode: print chunks [9k, 9k+9) as notices
    k = int(sys.argv[1]); CHK = json.load(open("/tmp/chunks_" + SYM + ".json"))
    for c in CHK[9 * k:9 * k + 9]:
        print("::notice title=" + c["t"][:200] + "::" + c["m"].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    sys.exit(0)


s = requests.Session(); s.headers.update(HEAD)
try:
    s.get("https://www.nseindia.com", timeout=30)
except requests.RequestException as e:
    print("home page:", type(e).__name__)
time.sleep(1.5)


def get(url, **kw):
    try:
        r = s.get(url, timeout=60, **kw)
    except requests.RequestException as e:
        return None, "network error " + type(e).__name__
    time.sleep(1.5)
    return r, None


def records_of(j):
    if isinstance(j, list): return j
    if isinstance(j, dict):
        for k in ("data", "Data", "records"):
            if isinstance(j.get(k), list): return j[k]
    return []


def pdate(x):
    try: return dt.datetime.strptime(str(x).strip().upper(), "%d-%b-%Y").date()
    except ValueError: return None


r, err = get(IDX)
if err or r.status_code != 200:
    emit("INDEX ERROR", err or "HTTP %d len=%d" % (r.status_code, len(r.text))); sys.exit(0)
try:
    j = r.json()
except ValueError:
    emit("INDEX NOT JSON", "len=%d start=%r" % (len(r.text), r.text[:200])); sys.exit(0)
recs = [x for x in records_of(j) if isinstance(x, dict)]
dates = sorted(d for d in (pdate(x.get("date")) for x in recs) if d)
head = "records=%d oldest=%s newest=%s keys=%s\n" % (len(recs), dates[0] if dates else None, dates[-1] if dates else None, sorted(recs[0].keys()) if recs else None)
KEEP = ("date", "broadcastDate", "submissionDate", "desc", "format", "recordId", "pr_and_prgrp", "public_val", "employeeTrusts", "underlyingDrs", "revisedData", "revisedDate", "revisedRemark", "revisedStatus", "revisionDate", "revisionRemark", "typeOfSubmission", "xbrl", "xbrlFileSize")
emit("INDEX", head + "\n".join(json.dumps({k: (str(x.get(k))[:260] if x.get(k) is not None else None) for k in KEEP}, ensure_ascii=False, separators=(",", ":")) for x in recs))

# deeper history? try date-range variants (counts only)
var = []
for q in ("&from_date=01-01-2010&to_date=05-10-2026", "&period=all", "&from_date=01-01-2015"):
    r2, e2 = get(IDX + q)
    if e2 or r2.status_code != 200: var.append(q + " -> " + (e2 or "HTTP %d" % r2.status_code)); continue
    try:
        rr = [x for x in records_of(r2.json()) if isinstance(x, dict)]
        ds = sorted(d for d in (pdate(x.get("date")) for x in rr) if d)
        var.append("%s -> %d records %s..%s" % (q, len(rr), ds[0] if ds else None, ds[-1] if ds else None))
    except ValueError:
        var.append(q + " -> not JSON")
emit("INDEX VARIANTS", "\n".join(var))

# choose quarters
by = {pdate(x.get("date")): x for x in recs if pdate(x.get("date"))}
want = [dates[-1]] if dates else []
for d in (dt.date(2026, 3, 31), dt.date(2025, 6, 30), dt.date(2025, 12, 31), dt.date(2024, 3, 31), dt.date(2023, 9, 30)):
    if d in by: want.append(d)
if dates: want += [dates[0], dates[1] if len(dates) > 1 else dates[0]]
rev = [pdate(x.get("date")) for x in recs if pdate(x.get("date")) and any(x.get(k) not in (None, "", "-", False) for k in ("revisedDate", "revisionDate", "revisedStatus", "revisedData"))]
want += rev[:3]
if SYM == "ICICIBANK": want = list(dates)
want = sorted(set(want), reverse=True)


def local(t): return t.split("}")[-1] if "}" in t else t


def parse(xml):
    root = ET.fromstring(xml)
    ctx = {}
    for el in root.iter():
        if local(el.tag) == "context":
            members, per = [], None
            for c in el.iter():
                lt = local(c.tag)
                if lt in ("explicitMember", "typedMember"):
                    members.append(local(c.get("dimension", "?")) + "=" + re.sub(r"^.*:", "", (c.text or "").strip() or "".join(c.itertext()).strip()))
                elif lt in ("instant", "endDate"):
                    per = (c.text or "").strip()
            ctx[el.get("id")] = (tuple(members), per)
    nd, rows, names = [], {}, {}
    for el in root:
        cr = el.get("contextRef")
        if cr is None: continue
        n = local(el.tag); v = (el.text or "").strip()
        mem, per = ctx.get(cr, ((), None))
        if not mem:
            if v and len(v) <= 90: nd.append(n + "=" + v)
            continue
        try: float(v.replace(",", ""))
        except ValueError: continue
        if re.search(r"percent|fullypaid", n, re.I):
            code = names.setdefault(n, "N%d" % (len(names) + 1))
            rows.setdefault((mem, per), []).append(code + "=" + v)
    return nd, rows, names


chunks = []
for d in want:
    x = by[d]
    url = next((v for k, v in x.items() if "xbrl" in k.lower() and isinstance(v, str) and v.startswith("http")), None)
    if not url:
        chunks.append("FILING %s: no XBRL url in record" % d); continue
    r3, e3 = get(url)
    if e3 or r3.status_code != 200:
        chunks.append("FILING %s: download failed %s" % (d, e3 or "HTTP %d" % r3.status_code)); continue
    try:
        nd, rows, names = parse(r3.content)
    except ET.ParseError as e:
        chunks.append("FILING %s: XML parse error %s" % (d, e)); continue
    ndk = [z for z in nd if re.match(r"(ScripCode|Symbol|ISIN|DateOfReport|NameOfTheCompany|WhetherTheListedEntityHasAnySharesAgainstWhichDepositoryReceiptsAreIssued=|WhetherTheListedEntityHasGrantedAnyESOPWhichAreOutstanding=)", z)]
    pc = names.get("ShareholdingAsAPercentageOfTotalNumberOfShares")
    sc = names.get("NumberOfFullyPaidUpEquityShares")
    out = ["FILING %s url=%s bytes=%d" % (d, url, len(r3.content)), "LEGEND shares=" + str(sc) + " pctOfTotal=" + str(pc), "ND " + " | ".join(ndk)]
    zero = 0
    for (mem, per), vals in sorted(rows.items(), key=lambda kv: (kv[0][1] or "", kv[0][0])):
        dv = dict(z.split("=", 1) for z in vals)
        a, b = dv.get(sc), dv.get(pc)
        if (a in (None, "0") or a == "0.0") and (b in (None, "0", "0.0")): zero += 1; continue
        out.append("%s @%s: sh=%s pct=%s" % ("/".join(re.sub(r"^.*Axis=|Member$", "", m) for m in mem), (per or "")[2:], a, b))
    out.append("(%d all-zero rows omitted)" % zero)
    chunks.append("\n".join(out))

for c in chunks:
    emit("XBRL", c)
print("done", SYM, len(chunks), "filings,", len(CH), "chunks")
