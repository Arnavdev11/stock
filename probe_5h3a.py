"""TEMPORARY (probe-5h3a only): shows the real structure of the Upstox share-holdings response and RELIANCE's NSE index ISIN fields. No token is printed."""
import json, sys
import shareholding_updater as su
from upstox_common import Upstox, load_instruments, get_token, SYMBOLS

def note(t, lines): print("::notice title=%s::%s" % (t, "%0A".join(lines)[:3800]))

ins = load_instruments()
api = Upstox(get_token(), 30)
out = []
for sym in SYMBOLS:
    m = ins.get(sym); isin = (m or {}).get("isin")
    body, err = api.get(isin + "/share-holdings") if isin else (None, "not in instruments")
    if err:
        out.append("%s isin=%s ERR %s" % (sym, isin, err)); continue
    d = body.get("data")
    l = ["%s isin=%s top=%s datatype=%s" % (sym, isin, list(body), type(d).__name__)]
    if isinstance(d, dict): l.append("  datakeys=%s" % list(d))
    items = d if isinstance(d, list) else (d.get("shareholdings") if isinstance(d, dict) else None)
    out.append("\n".join(l)); 
    out.append("  sample=" + json.dumps(d)[:700])
note("upstox structure A", out[:10])
note("upstox structure B", out[10:20])
# periods per first stock with data
for sym in ("TCS", "INFY"):
    isin = ins[sym]["isin"]; body, err = api.get(isin + "/share-holdings")
    note("upstox full %s" % sym, [json.dumps(body)[:3700] if not err else err])
n = su.Nse(); recs, err = n.index("RELIANCE")
l = ["err=%s n=%s" % (err, len(recs or []))]
for r in (recs or [])[:3]: l.append(json.dumps({k: r.get(k) for k in r if k in ("isin", "ISIN", "symbol", "name", "date", "recordId")}))
l.append("isin values across records: %s" % sorted({str(r.get("isin")) for r in (recs or [])}))
l.append("keys=%s" % sorted((recs or [{}])[0].keys()))
note("RELIANCE index", l)
