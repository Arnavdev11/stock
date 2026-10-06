"""Read-only 5H.6 inventory (throwaway branch only). Writes nothing."""
import collections, os, re, sys, traceback
import xml.etree.ElementTree as ET
import shareholding_updater as su
import shareholding_ledger as sl


def pairs_of(data, report):
    root = ET.fromstring(data)
    ctx = {}
    for el in root.iter():
        if su.local(el.tag) == "context":
            dims, per = [], None
            for c in el.iter():
                lt = su.local(c.tag)
                if lt == "typedMember":
                    dims.append((c.get("dimension") or "", "".join(x.text or "" for x in c.iter() if x is not c).strip()))
                elif lt in ("instant", "endDate"):
                    per = (c.text or "").strip()
            ctx[el.get("id")] = (dims, per)
    out = {}
    for el in root:
        cr = el.get("contextRef")
        if cr in ctx and ctx[cr][1] == report and len(ctx[cr][0]) == 1:
            out.setdefault(ctx[cr][0][0], {}).setdefault(su.local(el.tag), (el.text or "").strip())
    return out


def short(a):
    return re.sub(r"^DetailsOfSharesHeldBy|^DetailsOf|Axis$", "", re.sub(r"^.*:", "", a))


def main():
    sym = os.environ["SYM"]
    ledger = sl.load_ledger("ledger-branch/shareholding_ledger.json")
    nse = su.Nse()
    recs, err = nse.index(sym)
    if err or not recs:
        print("::error::%s index: %s" % (sym, err)); sys.exit(1)
    keep, _ = su.select_filings(recs)
    stored = {r["quarter_end"]: r for r in ledger["stocks"].get(sym, {}).get("quarters", [])}
    lines, allax, detail = [], set(), []
    ds = sorted(keep, reverse=True)
    for i, d in enumerate(ds):
        rec, q = keep[d], d.isoformat()
        url = rec.get("xbrl") if isinstance(rec.get("xbrl"), str) and rec["xbrl"].startswith("https://") else None
        if not url:
            lines.append("%s no-xbrl" % q[2:]); continue
        data, used, e = su.fetch_xbrl(nse, url)
        if data is None:
            lines.append("%s fetch-failed %s" % (q[2:], e)); continue
        new = su.build_record(rec, d, data, used, e, "probe")
        nh = new["named_holders"]
        old = stored.get(q)
        agree = "-" if not old or old.get("status") != "available" else ("=" if old["values"] == new["values"] else "DIFF")
        labs = collections.Counter(h["label"][:3] + h["label"][-3:] for h in nh["holders"])
        ex = "".join("%s%d " % (k[:4], v) for k, v in nh["excluded"].items() if v)
        lines.append("%s %s %s %s %s n=%d agg%s %s |%s| um=%s fl=%s" % (q[2:], (new["status"] or "?")[:5], (new["format_version"] or "-")[:3], (new["percent_unit"] or "-")[:3], nh["status"][:6], len(nh["holders"]), agree,
                     dict(labs), ex, ",".join("%s:%d" % (short(a), n) for a, n in nh["unmapped_axes"].items()), [f["code"][:12] for f in nh["flags"]]))
        P = pairs_of(data, su.parse_xbrl(data)["report_date"] or q)
        allax.update(short(a) + ("" if su._axis_key(a) in su.HOLDER_AXES or su._axis_key(a) in su.HOLDER_SKIP_AXES else "*") for a, m in P)
        if i in (0, len(ds) - 1) or (new["format_version"] or "").startswith("old") and not any(x.startswith("OLD") for x in detail):
            for (a, m), f in sorted(P.items()):
                if su._axis_key(a) not in su.HOLDER_AXES and su._axis_key(a) not in su.HOLDER_SKIP_AXES:
                    sh = su.fnum(f.get(su.HOLDER_SHARES_EL))
                    if sh:
                        detail.append("%s %s: %s | %s | sh=%s pct=%s flag=%s" % (q[2:], short(a)[:24], (f.get(su.HOLDER_NAME_EL) or "")[:36], m[-12:], sh, f.get(su.HOLDER_PCT_EL), (f.get(su.HOLDER_FLAG_EL) or "")[:14]))
            ag = {k: (r["shares"], r["pct"]) for k, r in su.parse_xbrl(data)["rows"].items() if k[1] == (su.parse_xbrl(data)["report_date"] or q) and re.search("government|noninstit|custod|promoter", k[0])}
            detail.append("   agg " + "; ".join("%s=%s" % (k[0][:34], v) for k, v in sorted(ag.items())))
    chunk = 7
    for j in range(0, len(lines), chunk):
        print("::notice title=%s %d::%s" % (sym, j // chunk, "%0A".join(lines[j:j + chunk])))
    print("::notice title=%s axes::%s" % (sym, "; ".join(sorted(allax))))
    for j in range(0, len(detail), 14):
        print("::notice title=%s detail %d::%s" % (sym, j // 14, "%0A".join(detail[j:j + 14])))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        print("::notice title=ERR::" + traceback.format_exc().replace("\n", "%0A")[-3000:])
