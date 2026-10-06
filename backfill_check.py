"""Throwaway branch only: compares the ledger the updater produced with the live one. Strict: nothing except the added named_holders may differ."""
import collections, json, sys
import shareholding_ledger as sl
old, new = sl.load_ledger(sys.argv[1]), sl.load_ledger(sys.argv[2])
bad, lines, unav = [], [], []
for sym, ost in old["stocks"].items():
    nst = new["stocks"][sym]
    nm = {r["quarter_end"]: r for r in nst["quarters"]}
    row = []
    for o in ost["quarters"]:
        n = nm[o["quarter_end"]]
        a = {k: v for k, v in o.items() if k != "named_holders"}
        b = {k: v for k, v in n.items() if k != "named_holders"}
        if a != b:
            bad.append("%s %s: a field other than named_holders changed: %s" % (sym, o["quarter_end"], [k for k in set(a) | set(b) if a.get(k) != b.get(k)]))
        if o.get("named_holders") is not None and o["named_holders"] != n.get("named_holders"):
            bad.append("%s %s: existing named_holders changed" % (sym, o["quarter_end"]))
        nh = n.get("named_holders") or {}
        row.append("%s=%s%s" % (o["quarter_end"][2:7], len(nh.get("holders") or []), "" if nh.get("status") == "available" else "(%s)" % (nh.get("status") or "none")[:5]))
        if nh.get("status") != "available":
            unav.append("%s %s %s: %s" % (sym, o["quarter_end"], nh.get("status"), (nh.get("reason") or "")[:90]))
    lines.append("%s %s" % (sym, " ".join(row)))
    if len(nst["quarters"]) != len(ost["quarters"]):
        bad.append("%s: quarter count changed" % sym)
problems = sl.no_loss_problems(old, new)
print("::notice title=backfill diff::bad=%d no_loss=%d%%0A%s" % (len(bad), len(problems), "%0A".join(bad[:10] + problems[:10])))
for i in range(0, len(lines), 5):
    print("::notice title=named holders %d::%s" % (i // 5, "%0A".join(lines[i:i + 5])))
print("::notice title=not available::%s" % "%0A".join(unav[:40]))
if bad or problems:
    sys.exit(1)
