"""Read-only 5H.6 inventory (throwaway branch only). For one stock: every quarter's XBRL -> raw typed-member axis inventory + named-holder result +
agreement of the aggregate values with the live ledger. Writes nothing. Prints one annotation per stock."""
import collections, datetime as dt, os, re, sys
import xml.etree.ElementTree as ET
import shareholding_updater as su
import shareholding_ledger as sl

sym = os.environ["SYM"]
ledger = sl.load_ledger("ledger-branch")
nse = su.Nse()
recs, err = nse.index(sym)
lines, allax = [], set()
if err or not recs:
    print("::error::%s index: %s" % (sym, err)); sys.exit(1)
keep, _ = su.select_filings(recs)
stored = {r["quarter_end"]: r for r in ledger["stocks"].get(sym, {}).get("quarters", [])}
for d in sorted(keep, reverse=True):
    rec, q = keep[d], d.isoformat()
    url = rec.get("xbrl") if isinstance(rec.get("xbrl"), str) and rec["xbrl"].startswith("https://") else None
    if not url:
        lines.append("%s no-xbrl-listed" % q); continue
    data, used, e = su.fetch_xbrl(nse, url)
    if data is None:
        lines.append("%s fetch-failed %s" % (q, e)); continue
    new = su.build_record(rec, d, data, used, e, "probe")
    # raw axis inventory: typed-member contexts at the report date
    root = ET.fromstring(data)
    per = new["quality"] and None
    axes = collections.Counter()
    for el in root.iter():
        if su.local(el.tag) == "context":
            for c in el.iter():
                if su.local(c.tag) == "typedMember":
                    axes[re.sub(r"^.*:", "", c.get("dimension") or "")] += 1
    nh = new["named_holders"]
    old = stored.get(q)
    agree = "n/a" if not old or old.get("status") != "available" else ("same" if old["values"] == new["values"] else "DIFF")
    labs = collections.Counter(h["label"] for h in nh["holders"])
    ex = {k: v for k, v in nh["excluded"].items() if v}
    top = ["%s %.2f%%" % (h["holder_name"][:28], h["percentage"]) for h in nh["holders"][:2] if h["percentage"] is not None]
    lines.append("%s %s fmt=%s unit=%s nh=%s n=%d aggregates=%s labels=%s excl=%s unmapped=%s axes=%d top=%s flags=%s" % (
        q, new["status"], (new["format_version"] or "-")[:3], new["percent_unit"], nh["status"], len(nh["holders"]), agree,
        dict(labs), ex, nh["unmapped_axes"], len(axes), top, [f["code"] for f in nh["flags"]]))
    allax.update(axes.keys())
lines.append("ALL AXES: " + "; ".join("%s%s" % (a, "" if su._axis_key(a) in su.HOLDER_AXES or su._axis_key(a) in su.HOLDER_SKIP_AXES else " <-NOT MAPPED") for a in sorted(allax)))
print("::notice title=%s inventory::%s" % (sym, "%0A".join(lines)[:60000]))
