"""TEMP read-only probe v2 (Phase 5H.6, throwaway branch only): the per-holder structure inside one NSE shareholding XBRL filing.
Uses the existing updater's NSE access. Reads only; writes nothing; no token is used or printed."""
import sys, re, collections
import xml.etree.ElementTree as ET
import shareholding_updater as su

sym = sys.argv[1]
def note(title, lines):
    msg = "\n".join(lines)
    print("\n===== %s %s =====\n%s" % (sym, title, msg))
    print("::notice title=%s %s::%s" % (sym, title, msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A")))

nse = su.Nse()
recs, err = nse.index(sym)
if err:
    note("FAILED", ["index: " + err]); sys.exit(1)
keep, skipped = su.select_filings(recs)
d = max(keep); rec = keep[d]; url = rec.get("xbrl")
data, used, e = su.fetch_xbrl(nse, url) if isinstance(url, str) and url.startswith("https://") else (None, None, "no xbrl url")
if data is None:
    note("FAILED", ["xbrl download: %s" % e]); sys.exit(1)
lt = lambda t: t.rsplit("}", 1)[-1] if isinstance(t, str) else str(t)
dn = lambda s: re.sub(r"^.*:", "", s or "")
short = lambda s, n=60: (s if len(s) <= n else s[:n] + "...")
root = ET.fromstring(data)
ctx = {}
for el in root.iter():
    if lt(el.tag) == "context":
        dims, per = [], None
        for c in el.iter():
            n = lt(c.tag)
            if n == "explicitMember": dims.append(("E", dn(c.get("dimension")), dn((c.text or "").strip())))
            elif n == "typedMember": dims.append(("T", dn(c.get("dimension")), "|".join((x.text or "").strip() for x in c.iter() if x is not c)))
            elif n in ("instant", "endDate"): per = (c.text or "").strip()
        ctx[el.get("id")] = (dims, per)
facts = collections.defaultdict(list)
for el in root:
    cr = el.get("contextRef")
    if cr in ctx: facts[cr].append((lt(el.tag), (el.text or "").strip()))
merged = collections.defaultdict(list)          # (axis, member, period) -> facts of BOTH contexts (the "D_" descriptive one and the numeric one)
for cid, (dims, per) in ctx.items():
    if len(dims) == 1 and dims[0][0] == "T": merged[(dims[0][1], dims[0][2], per)] += facts[cid]
fvl = lambda fl, name: next((t for n, t in fl if n == name), None)
fv = lambda cid, name: next((t for n, t in facts[cid] if n == name), None)
num = lambda s: float(s) if s not in (None, "") and re.match(r"^-?[\d.]+(E-?\d+)?$", s) else None
rep = next((t for cid in ctx for n, t in facts[cid] if n == "DateOfReport"), None)
note("A overview", ["record %s | quarter %s | DateOfReport %s | broadcast %s | bytes %d | host %s" % (rec.get("recordId"), d, rep, rec.get("broadcastDate"), len(data), re.sub(r"^https://([^/]+)/.*", r"\1", used)),
                    "filing: %s" % used, "contexts %d | context periods: %s" % (len(ctx), dict(collections.Counter(p for _, p in ctx.values())))])
# B. every axis, kind, contexts, distinct members, periods, fact elements
ax = collections.defaultdict(lambda: {"kind": set(), "ctx": [], "mem": set(), "per": collections.Counter()})
for cid, (dims, per) in ctx.items():
    for k, a, m in dims:
        ax[a]["kind"].add(k); ax[a]["ctx"].append(cid); ax[a]["mem"].add(m); ax[a]["per"][per] += 1
L = ["AXES (name | kind E=explicit T=typed | contexts | distinct members | periods):"]
for a, v in sorted(ax.items(), key=lambda kv: -len(kv[1]["ctx"])):
    L.append("  %s | %s | %d | %d | %s" % (a, "".join(sorted(v["kind"])), len(v["ctx"]), len(v["mem"]), dict(v["per"])))
note("B axes", L)
# C. one member with all its contexts: shows the duplicate contexts and the facts
def example(axis, which=0):
    mems = sorted({m for cid in ax[axis]["ctx"] for k, a, m in ctx[cid][0] if a == axis})
    if len(mems) <= which: return ["(none)"]
    m = mems[which]; out = ["axis %s member %s" % (axis, m)]
    for cid in ax[axis]["ctx"]:
        if any(a == axis and mm == m for k, a, mm in ctx[cid][0]):
            out.append("  ctx id %s period %s dims %s" % (cid, ctx[cid][1], [(a, mm) for k, a, mm in ctx[cid][0]]))
            out.append("    " + "; ".join("%s=%s" % (n, short(t, 40)) for n, t in facts[cid]))
    return out
L = []
for a in [x for x, v in ax.items() if "T" in v["kind"]][:3]: L += example(a, 0)
note("C one member, all its contexts", L)
# D. per typed axis: named rows for the report period (rows, nonzero, top 12 by percentage)
rows = collections.defaultdict(list)
for (a, m, per), fl in merged.items():
    if rep and per != rep: continue
    name = fvl(fl, "NameOfTheShareholder")
    cat = next((t for n, t in fl if n.startswith("CategoryOf")), None)
    rows[a].append((name, cat, num(fvl(fl, "NumberOfShares")), num(fvl(fl, "ShareholdingAsAPercentageOfTotalNumberOfShares")), fvl(fl, "WhetherACategoryOrMoreThan1PercentageOfShareholding"), m, len([1 for cid in ax[a]["ctx"] if any(mm == m for k, aa, mm in ctx[cid][0])])))
L = []
for a, r in rows.items():
    nz = [x for x in r if (x[2] or 0) > 0]
    L.append("AXIS %s: %d rows in the report period, %d with shares>0, %d with a name, %d names unique" % (a, len(r), len(nz), sum(1 for x in r if x[0]), len({x[0] for x in r if x[0]})))
    for x in sorted(nz, key=lambda x: -(x[3] or 0))[:10]:
        L.append("   %s | cat=%s | shares=%s | pct=%s | %s | %s | contexts=%d" % (short(x[0] or "(no name)", 55), x[1], None if x[2] is None else int(x[2]), x[3], x[4], x[5], x[6]))
note("D named rows per axis (report period)", L)
# E. search for well-known holder words: where do they sit?
pat = re.compile(r"(life insurance|\bLIC\b|SBI |ICICI Prudential|Nifty|Vanguard|Government of Singapore|Tata Sons|Mutual Fund|ETF)", re.I)
L = []
for (a, m, per), fl in merged.items():
    t = fvl(fl, "NameOfTheShareholder")
    if t and pat.search(t):
        L.append("  %s | axis %s member %s | shares=%s pct=%s | cat=%s | %s" % (short(t, 70), a, m, fvl(fl, "NumberOfShares"), fvl(fl, "ShareholdingAsAPercentageOfTotalNumberOfShares"), next((t2 for n2, t2 in fl if n2.startswith("CategoryOf")), None), fvl(fl, "WhetherACategoryOrMoreThan1PercentageOfShareholding")))
raw = data.decode("utf-8", "ignore")
L.insert(0, "raw-file word hits: " + ", ".join("%s x%d" % kv for kv in collections.Counter(m.group(1).lower() for m in pat.finditer(raw)).most_common(10)))
note("E where well-known holder words occur", L[:30])
# F. reconciliation: named rows (report period) vs the aggregate category row with the same core name
agg = {}
for cid, (dims, per) in ctx.items():
    if len(dims) == 1 and dims[0][1] == "CategoryOfShareholdersAxis" and per == rep:
        agg[dims[0][2]] = (num(fv(cid, "NumberOfShares")), num(fv(cid, "ShareholdingAsAPercentageOfTotalNumberOfShares")))
core = lambda s: re.sub(r"(Member|Axis|DetailsOfSharesHeldBy|Details|s$)", "", s).lower()
L = ["aggregate category rows in the report period: %d" % len(agg)]
for a, r in rows.items():
    tot = sum(x[2] or 0 for x in r); totp = sum(x[3] or 0 for x in r)
    cands = [(k, v) for k, v in agg.items() if core(k) == core(a) or core(a) in core(k) or core(k) in core(a)]
    L.append("  %s: named-rows sum shares=%d pct=%.4f | same-core aggregate rows: %s" % (a, tot, totp, "; ".join("%s shares=%s pct=%s" % (k, None if v[0] is None else int(v[0]), v[1]) for k, v in cands[:3]) or "none"))
note("F named rows vs aggregate rows", L)
