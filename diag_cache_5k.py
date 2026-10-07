"""TEMPORARY read-only verification of the ingestion fix against the real cache. Prints a report, writes no data files."""
import datetime as dt
import re
import sys

import numpy as np
import pandas as pd

import nse_updater as nu


def notice(title, lines):
    print("::notice title=%s::%s" % (title, "%0A".join(l.replace("%", "%25") for l in lines)))


files = sorted(nu.RAW.glob("bhav_*.csv"))
if not files:
    print("::error::cache has no data/raw/bhav_*.csv - stopping")
    sys.exit(1)

# independent view of the raw files: which files carry a DATE1 different from their filename?
truth_bad, legacy_frames = [], []
for f in files:
    d = pd.read_csv(f, skipinitialspace=True)
    d.columns = [c.strip() for c in d.columns]
    for c in ("SYMBOL", "SERIES", "DATE1"):
        d[c] = d[c].astype(str).str.strip()
    fd = dt.datetime.strptime(re.search(r"(\d{8})", f.name).group(1), "%Y%m%d").date()
    if {dt.datetime.strptime(x, "%d-%b-%Y").date() for x in d.DATE1.unique()} != {fd}:
        truth_bad.append(f.name)
    legacy_frames.append(d)

fixed = nu.load_prices()                      # the code under test
rep = nu.LAST_LOAD_REPORT
rej = sorted(r["file"] for r in rep["files_rejected"])

# old behaviour, reproduced here only for comparison
L = pd.concat(legacy_frames, ignore_index=True)
L = L[L.SERIES == "EQ"].copy()
L["DATE"] = pd.to_datetime(L.DATE1, format="%d-%b-%Y")
for c in ("PREV_CLOSE", "HIGH_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "DELIV_PER"):
    L[c] = pd.to_numeric(L[c], errors="coerce")
L = L.sort_values(["SYMBOL", "DATE"]).reset_index(drop=True)
oldded = L.drop_duplicates(["SYMBOL", "DATE"], keep="last").reset_index(drop=True)

out = []
out.append("files in cache: %d ; files used: %d ; files rejected: %d" % (rep["files_found"], rep["files_used"], len(rej)))
out.append("rejected == the independently found misdated files: %s (%d vs %d)" % (rej == sorted(truth_bad), len(rej), len(truth_bad)))
out.append("rejected files: " + ", ".join(rej))
out.append("reasons: " + " | ".join("%s: %s" % (r["file"], r["reason"]) for r in rep["files_rejected"][:3]) + " ...")
out.append("EQ rows: old path %d -> fixed path %d (removed %d ; expected = surplus duplicates)" % (len(L), len(fixed), len(L) - len(fixed)))
out.append("duplicate (SYMBOL,DATE) rows remaining in fixed data: %d" % int(fixed.duplicated(["SYMBOL", "DATE"]).sum()))
out.append("identical repeated rows removed inside accepted files: %d" % rep["identical_repeat_rows_removed"])
out.append("distinct trading dates: old %d, fixed %d ; fixed identical to old-path-deduplicated data: %s" % (L.DATE.nunique(), fixed.DATE.nunique(),
           bool(fixed.drop(columns=[c for c in fixed.columns if c not in oldded.columns]).reset_index(drop=True)[["SYMBOL", "DATE", "CLOSE_PRICE", "TTL_TRD_QNTY", "HIGH_PRICE"]].equals(oldded[["SYMBOL", "DATE", "CLOSE_PRICE", "TTL_TRD_QNTY", "HIGH_PRICE"]]))))

A = nu.add_indicators(L.copy())
B = nu.add_indicators(fixed.copy())
last = fixed.DATE.max()
a = A[A.DATE == last].drop_duplicates("SYMBOL", keep="last").set_index("SYMBOL")
b = B[B.DATE == last].set_index("SYMBOL")
out.append("latest date: %s ; stocks: %d" % (last.date(), len(b)))
for m in ("dma50", "dma200", "vol20", "high252", "ret63"):
    x, y = a[m].reindex(b.index), b[m]
    both = x.notna() & y.notna()
    out.append("  %-8s old-vs-fixed: changed=%d  NaN-status differs=%d  max_abs_diff=%.6f" % (m, int(((x - y).abs()[both] > 1e-9).sum()), int((x.notna() != y.notna()).sum()), float((x - y).abs()[both].max())))
# the same indicators on the independently de-duplicated data must equal the fixed ones exactly
C = nu.add_indicators(oldded.copy())
c = C[C.DATE == last].set_index("SYMBOL")
same = all(((b[m] == c[m].reindex(b.index)) | (b[m].isna() & c[m].reindex(b.index).isna())).all() for m in ("dma50", "dma200", "vol20", "high252", "ret63"))
out.append("fixed indicators == indicators on independently de-duplicated data (all 5 metrics, every stock): %s" % same)
sc = nu.build_scans(B.copy())
out.append("build_scans runs on fixed data: as_of=%s, keys=%s" % (sc["as_of"], ",".join(sorted(sc))))
for l in out:
    print(l)
notice("FIX VERIFICATION", out)
