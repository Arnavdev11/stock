"""TEMPORARY (probe-5h3-live only): prints a compact validation report of the shareholding run as GitHub notices. Never reads or prints any token."""
import json, sys
from collections import Counter
d = json.load(open("out/shareholding.json"))
S = d["stocks"]
def note(title, lines):
    print("::notice title=%s::%s" % (title, "%0A".join(lines)[:3800]))
tot = sum(len(v["quarters"]) for v in S.values())
fm = Counter(); rev = []; flags = Counter(); un = []
for s, v in S.items():
    for r in v["quarters"]:
        fm[r.get("format_version")] += 1
        if (r.get("source") or {}).get("revised"): rev.append("%s %s id=%s rd=%s" % (s, r["quarter_end"], r["source"]["record_id"], r["source"]["revision_date"]))
        for f in r["quality"]["flags"]: flags[f["code"]] += 1
        if r["status"] != "available": un.append("%s %s: %s" % (s, r["quarter_end"], r["reason"]))
note("1 summary", ["stocks=%d (%s)" % (len(S), ",".join(S)), "summary=%s" % json.dumps(d["summary"]), "format_counts=%s" % dict(fm), "flag_counts=%s" % dict(flags), "as_of=%s" % d["as_of"]])
lines = []
for s, v in S.items():
    c = v["coverage"]
    lines.append("%s n=%d avail=%d oldest=%s newest=%s missing=%s unavail=%s err=%s offcycle=%d" % (s, c["count"], c["available"], c["oldest"], c["newest"], c["missing_quarters"], c["unavailable_quarters"], v.get("error"), len(v["off_cycle_filings"])))
note("2 coverage", lines)
note("3 unavailable", un or ["none"])
note("4 revised filings (%d)" % len(rev), rev or ["none"])
ic = {r["quarter_end"]: r for r in S.get("ICICIBANK", {}).get("quarters", [])}
l = []
for q in ("2026-06-30", "2026-03-31", "2025-12-31"):
    r = ic.get(q)
    if r: l.append("%s status=%s values=%s quality=%s derived=%s conflict=%s" % (q, r["status"], r["values"], json.dumps(r["quality"]), (r["diagnostics"] or {}).get("share_derived_pct"), (r["diagnostics"] or {}).get("conflict")))
note("5 ICICIBANK", l or ["no ICICI records"])
note("6 cross-check", ["%s %s max=%s %s" % (s, (v.get("cross_check") or {}).get("status"), (v.get("cross_check") or {}).get("max_abs_diff"), (v.get("cross_check") or {}).get("reason") or "") for s, v in S.items()])
fl = []
for s, v in S.items():
    for r in v["quarters"]:
        codes = [f["code"] for f in r["quality"]["flags"] if f["code"] != "old-format-other-institutions-in-other-dii"]
        if codes: fl.append("%s %s %s" % (s, r["quarter_end"], ",".join(codes)))
note("7 flags other than old-format info (%d)" % len(fl), fl[:60] or ["none"])
latest = []
for s, v in S.items():
    r = next((x for x in v["quarters"] if x["status"] == "available"), None)
    if r: latest.append("%s %s %s" % (s, r["quarter_end"], r["values"]))
note("8 latest values", latest)
