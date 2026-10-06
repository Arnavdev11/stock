"""TEMP read-only probe (Phase 5H.6, throwaway branch only): the STRUCTURE of the per-holder information inside one NSE shareholding XBRL filing.
Uses the existing updater's NSE access (Nse, select_filings, fetch_xbrl). Reads only; writes nothing; no token is used or printed."""
import sys, re, collections
import xml.etree.ElementTree as ET
import shareholding_updater as su

sym = sys.argv[1]
OUT = []
def note(title, lines):
    msg = "\n".join(lines)
    OUT.append((title, msg))
    print("\n===== %s %s =====\n%s" % (sym, title, msg))
    print("::notice title=%s %s::%s" % (sym, title, msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A")))

nse = su.Nse()
recs, err = nse.index(sym)
if err:
    note("FAILED", ["index: " + err]); sys.exit(1)
keep, skipped = su.select_filings(recs)
d = max(keep); rec = keep[d]
url = rec.get("xbrl")
data, used, e = su.fetch_xbrl(nse, url) if isinstance(url, str) and url.startswith("https://") else (None, None, "no xbrl url")
if data is None:
    note("FAILED", ["xbrl download: %s" % e, "record %s" % rec.get("recordId")]); sys.exit(1)
lt = lambda t: t.rsplit("}", 1)[-1] if isinstance(t, str) else str(t)
root = ET.fromstring(data)
ctx = {}
for el in root.iter():
    if lt(el.tag) == "context":
        mem, per = [], None
        for c in el.iter():
            n = lt(c.tag)
            if n in ("explicitMember", "typedMember"):
                if n == "typedMember":
                    inner = [(lt(x.tag), (x.text or "").strip()) for x in c.iter() if x is not c]
                    mem.append((n, c.get("dimension", ""), inner))
                else:
                    mem.append((n, c.get("dimension", ""), (c.text or "").strip()))
            elif n in ("instant", "endDate"):
                per = (c.text or "").strip()
        ctx[el.get("id")] = (mem, per)
facts = [(lt(el.tag), el.get("contextRef"), (el.text or "").strip()) for el in root if el.get("contextRef") in ctx]
short = lambda s, n=70: (s if len(s) <= n else s[:n] + "...")
dimname = lambda s: re.sub(r"^.*:", "", s)
sig = lambda cid: tuple(sorted((m[0][:1], dimname(m[1])) for m in ctx[cid][0]))
L = ["record %s | quarter %s | broadcast %s | bytes %d | host used %s" % (rec.get("recordId"), d, rec.get("broadcastDate"), len(data), re.sub(r"^https://([^/]+)/.*", r"\1", used)),
     "filing URL: %s" % used, "root element: %s | contexts %d | facts %d" % (lt(root.tag), len(ctx), len(facts)),
     "namespaces in file: " + ", ".join(sorted(set(re.findall(r'xmlns:(\w+)=', data.decode("utf-8", "ignore"))))[:25])]
note("A overview", L)
# B. context signatures (dimensions) and how many contexts / facts each has
bysig = collections.defaultdict(list)
for cid in ctx: bysig[sig(cid)].append(cid)
factsby = collections.defaultdict(list)
for n, cr, t in facts: factsby[cr].append((n, t))
L = []
for s, ids in sorted(bysig.items(), key=lambda kv: -len(kv[1])):
    members = collections.Counter()
    for cid in ids:
        for m in ctx[cid][0]:
            members[re.sub(r"^.*:", "", m[2] if isinstance(m[2], str) else "|".join(x[1] for x in m[2]))] += 1
    els = collections.Counter(n for cid in ids for n, _ in factsby.get(cid, []))
    L.append("DIMENSION SET %s: %d contexts, %d distinct members. members sample: %s" % (list(s) if s else "(none)", len(ids), len(members), ", ".join(list(members)[:8])))
    L.append("   fact elements on these contexts: " + ", ".join("%s x%d" % (k, v) for k, v in els.most_common(12)))
note("B context dimension sets", L)
# C. the per-holder detail members the current parser skips: _ContextNN / Details...
L = []
det = [cid for cid in ctx if any(isinstance(m[2], str) and ("_Context" in m[2] or re.sub(r"^.*:", "", m[2]).startswith("Details")) for m in ctx[cid][0])]
L.append("contexts whose member has _Context or starts Details: %d" % len(det))
kinds = collections.Counter()
for cid in det:
    for m in ctx[cid][0]:
        mm = re.sub(r"^.*:", "", m[2]) if isinstance(m[2], str) else "typed"
        kinds[re.sub(r"\d+", "NN", mm)] += 1
L.append("member name patterns (digits -> NN): " + ", ".join("%s x%d" % kv for kv in kinds.most_common(10)))
for cid in det[:6]:
    L.append("  ctx %s dims %s period %s" % (cid, [(dimname(m[1]), re.sub(r'^.*:', '', m[2]) if isinstance(m[2], str) else m[2]) for m in ctx[cid][0]], ctx[cid][1]))
    L.append("     facts: " + "; ".join("%s=%s" % (n, short(t, 50)) for n, t in factsby.get(cid, [])[:10]))
note("C detail-context rows", L)
# D. every text-valued (non-numeric) fact: where do holder NAMES live?
txt = collections.defaultdict(list)
for n, cr, t in facts:
    if t and not re.match(r"^-?[\d.,]+(E-?\d+)?$", t) and not re.match(r"^\d{4}-\d{2}-\d{2}$", t) and t.lower() not in ("true", "false"):
        txt[n].append((cr, t))
L = ["text-valued fact elements (name, count, sample values):"]
for n, v in sorted(txt.items(), key=lambda kv: -len(kv[1]))[:25]:
    L.append("  %s x%d: %s" % (n, len(v), " | ".join(short(t, 45) for _, t in v[:4])))
note("D text-valued facts", L)
# E. typed members (a holder name can be carried as a typed dimension member)
tm = [(cid, m) for cid in ctx for m in ctx[cid][0] if m[0] == "typedMember"]
L = ["typed-member contexts: %d" % len(tm)]
for cid, m in tm[:10]:
    L.append("  ctx %s dim %s typed %s" % (cid, dimname(m[1]), m[2]))
note("E typed members", L)
# F. which element names carry shares / percentages, with the dimension sets they sit on
num = collections.defaultdict(collections.Counter)
for n, cr, t in facts:
    if re.match(r"^-?[\d.]+(E-?\d+)?$", t): num[n][str(list(dict.fromkeys(dimname(m[1]) for m in ctx[cr][0])))] += 1
L = ["numeric fact elements -> dimension axes they appear with:"]
for n, c in sorted(num.items(), key=lambda kv: -sum(kv[1].values()))[:14]:
    L.append("  %s: %s" % (n, "; ".join("%s x%d" % kv for kv in c.most_common(3))))
note("F numeric elements", L)
# G. search the whole file (element text, attribute values, member names) for the typical named-holder words, without assuming any name
hay = data.decode("utf-8", "ignore")
pat = re.compile(r"(Tata Sons|Life Insurance|LIC |SBI |ICICI Prudential|HDFC|Mutual Fund|Nifty|Vanguard|BlackRock|Government of Singapore|Trust|Limited|Ltd\.?|Private|Fund|Insurance)", re.I)
hits = collections.Counter(m.group(1).lower() for m in pat.finditer(hay))
L = ["word hits in the raw file: " + ", ".join("%s x%d" % kv for kv in hits.most_common(12))]
names = []
for n, v in txt.items():
    for cr, t in v:
        if re.search(r"(limited|ltd|private|fund|insurance|trust|bank|company|corporation|llc|inc|plc|life)", t, re.I): names.append((n, t))
L.append("name-like text values found: %d (element: value, first 25)" % len(names))
for n, t in names[:25]: L.append("  %s: %s" % (n, short(t, 90)))
note("G named-holder search", L)
