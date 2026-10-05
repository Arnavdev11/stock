"""TEMPORARY (probe-5h3a-live only): compares the run's ledger with the ledger on stocklens-data and prints the cross-check as notices. No token is printed."""
import json
new = json.load(open("ledger-out/shareholding_ledger.json")); old = json.load(open("ledger-branch/shareholding_ledger.json"))
doc = json.load(open("out/shareholding.json"))
def note(t, l): print("::notice title=%s::%s" % (t, "%0A".join(l)[:3800]))
diff = []
for s, st in old["stocks"].items():
    nm = {r["quarter_end"]: r for r in new["stocks"][s]["quarters"]}
    for r in st["quarters"]:
        n = nm[r["quarter_end"]]
        if n["values"] != r["values"]: diff.append("%s %s values differ" % (s, r["quarter_end"]))
        if n["status"] != r["status"]: diff.append("%s %s status differs" % (s, r["quarter_end"]))
        if n["source"]["record_id"] != r["source"]["record_id"]: diff.append("%s %s record id differs" % (s, r["quarter_end"]))
    if new["stocks"][s].get("isin") != st.get("isin"): diff.append("%s isin %s -> %s" % (s, st.get("isin"), new["stocks"][s].get("isin")))
note("A values vs stocklens-data", ["records old=%d new=%d" % (sum(len(v["quarters"]) for v in old["stocks"].values()), sum(len(v["quarters"]) for v in new["stocks"].values())), "differences=%d" % len(diff)] + diff)
l = []
for s, v in doc["stocks"].items():
    c = v.get("cross_check") or {}
    l.append("%s %s q=%s isin_used=%s max=%s %s" % (s, c.get("status"), c.get("quarter_end"), c.get("upstox_isin"), c.get("max_abs_diff"), c.get("reason") or ""))
    if c.get("status") == "mismatch": l.append("   " + json.dumps(c.get("categories")))
note("B cross-check", l)
note("C summary", [json.dumps(doc["summary"]), "stored isins: " + json.dumps({s: v.get("isin") for s, v in doc["stocks"].items()})])
