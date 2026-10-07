"""TEMPORARY read-only diagnostic (Phase 5K). Reads data/raw/bhav_*.csv and data/holidays.txt, prints a report, writes NO data files."""
import datetime as dt
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import nse_updater as nu

RAW, HOL = nu.RAW, nu.HOLIDAYS
LINES = []


def out(s=""):
    print(s, flush=True)
    LINES.append(s)


def notice(title, lines):
    """Annotations are readable through the checks API; chunk to stay far under the size limit."""
    chunk, size, n = [], 0, 1
    for ln in lines:
        if size + len(ln) > 3500 and chunk:
            print("::notice title=%s (%d)::%s" % (title, n, "%0A".join(chunk)))
            chunk, size, n = [], 0, n + 1
        chunk.append(ln.replace("%", "%25").replace("\r", ""))
        size += len(ln) + 3
    if chunk:
        print("::notice title=%s (%d)::%s" % (title, n, "%0A".join(chunk)))


files = sorted(RAW.glob("bhav_*.csv"))
if not files:
    print("::error::cache has no data/raw/bhav_*.csv - stopping")
    sys.exit(1)
out("files in cache: %d  (%s .. %s)" % (len(files), files[0].name, files[-1].name))
hol = set(HOL.read_text().split()) if HOL.exists() else set()
out("holidays.txt entries: %d" % len(hol))

info, frames = {}, []
for f in files:
    fd = dt.datetime.strptime(re.search(r"(\d{8})", f.name).group(1), "%Y%m%d").date()
    d = pd.read_csv(f, skipinitialspace=True)
    d.columns = [c.strip() for c in d.columns]
    d["SYMBOL"] = d["SYMBOL"].astype(str).str.strip()
    d["SERIES"] = d["SERIES"].astype(str).str.strip()
    d["DATE1"] = d["DATE1"].astype(str).str.strip()
    d["_file"] = f.name
    dates_all = sorted({dt.datetime.strptime(x, "%d-%b-%Y").date() for x in d.DATE1.unique()})
    eq = d[d.SERIES == "EQ"]
    dates_eq = sorted({dt.datetime.strptime(x, "%d-%b-%Y").date() for x in eq.DATE1.unique()})
    info[f.name] = dict(fd=fd, rows=len(d), eq=len(eq), dates_all=dates_all, dates_eq=dates_eq, ok=(dates_all == [fd]))
    frames.append(d)

mis = {k: v for k, v in info.items() if not v["ok"]}
out("")
out("ITEM 1/2: files whose DATE1 set is exactly the filename date: %d ; NOT exactly the filename date: %d" % (len(info) - len(mis), len(mis)))
lines = ["%s eq_rows=%d DATE1(all)=%s" % (k, v["eq"], ",".join(x.isoformat() for x in v["dates_all"])) for k, v in mis.items()]
for l in lines:
    out("  MISDATED " + l)
notice("ITEM 2 misdated files", lines or ["none"])
compact = [k[5:13] + ("" if v["ok"] else "*") for k, v in info.items()]
notice("ITEM 1 all filenames (* = DATE1 differs)", [" ".join(compact[i:i + 12]) for i in range(0, len(compact), 12)])
out("ALL: " + " ".join(compact))

# ITEM 9: is each misdated file's actual date present under its own filename, and are the rows identical?
raw = pd.concat(frames, ignore_index=True)
raw_eq = raw[raw.SERIES == "EQ"].copy()
raw_eq["DATE"] = pd.to_datetime(raw_eq.DATE1, format="%d-%b-%Y")
for c in ("PREV_CLOSE", "HIGH_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "DELIV_PER"):
    raw_eq[c] = pd.to_numeric(raw_eq[c], errors="coerce")
item9 = []
files_by_date = {}
for k, v in info.items():
    for x in v["dates_all"]:
        files_by_date.setdefault(x, []).append(k)
for k, v in mis.items():
    for x in v["dates_all"]:
        own = "bhav_%s.csv" % x.strftime("%Y%m%d")
        holders = [h for h in files_by_date[x] if h != k]
        wd = x.weekday() >= 5
        s = "%s -> DATE1 %s (%s%s): own file %s; other files carrying this DATE1: %s; actual date in holidays.txt: %s" % (
            k, x, x.strftime("%a"), ", WEEKEND" if wd else "", "EXISTS" if own in info else "ABSENT", holders or "NONE", x.isoformat() in hol)
        if holders:
            a = raw_eq[(raw_eq._file == k) & (raw_eq.DATE == pd.Timestamp(x))].drop_duplicates("SYMBOL").set_index("SYMBOL")
            b = raw_eq[(raw_eq._file == holders[0]) & (raw_eq.DATE == pd.Timestamp(x))].drop_duplicates("SYMBOL").set_index("SYMBOL")
            j = a.join(b, lsuffix="_a", rsuffix="_b", how="inner")
            same = int(((j.CLOSE_PRICE_a == j.CLOSE_PRICE_b) & (j.TTL_TRD_QNTY_a == j.TTL_TRD_QNTY_b)).sum())
            s += "; common EQ symbols %d, identical close+qty %d" % (len(j), same)
        item9.append(s)
        out("  " + s)
notice("ITEM 9 misdated file vs real date", item9 or ["none"])

# ITEM 4/5: duplicates in what nse_updater.load_prices returns
df = nu.load_prices()
dup_mask = df.duplicated(["SYMBOL", "DATE"], keep=False)
dups = df[dup_mask]
extra = int(df.duplicated(["SYMBOL", "DATE"], keep="first").sum())
cols = ["CLOSE_PRICE", "HIGH_PRICE", "TTL_TRD_QNTY", "PREV_CLOSE", "TURNOVER_LACS"]
exact = int(df.duplicated(["SYMBOL", "DATE"] + cols, keep="first").sum())
out("")
out("ITEM 4: EQ rows consumed by scans: %d ; (SYMBOL,DATE) rows involved in duplication: %d ; surplus duplicate rows: %d ; of which identical on close/high/qty/prevclose/turnover: %d ; conflicting: %d"
    % (len(df), len(dups), extra, exact, extra - exact))
byd = dups.groupby("DATE").SYMBOL.nunique()
l5 = ["%s : %d symbols duplicated (%s)" % (d.date(), n, d.strftime("%a")) for d, n in byd.items()]
out("ITEM 5: affected dates = %d" % len(byd))
for l in l5:
    out("  " + l)
notice("ITEM 4/5 duplicates", ["rows=%d involved=%d surplus=%d identical=%d conflicting=%d dates=%d" % (len(df), len(dups), extra, exact, extra - exact, len(byd))] + l5)

# ITEM 6-8: scans calculation with vs without duplicates
A = nu.add_indicators(df.copy())
dd = df.drop_duplicates(["SYMBOL", "DATE"], keep="last").reset_index(drop=True)
B = nu.add_indicators(dd.copy())
last = df.DATE.max()
out("")
out("latest DATE in consumed data: %s ; rows on that date with duplicates: %d" % (last.date(), int((df.DATE == last).sum())))
a = A[A.DATE == last].drop_duplicates("SYMBOL", keep="last").set_index("SYMBOL")
b = B[B.DATE == last].set_index("SYMBOL")
res = []
for universe, mask in (("scan universe (turnover>=100 lakh, as build_scans)", b.TURNOVER_LACS >= nu.MIN_TURNOVER_LACS), ("all EQ", b.TURNOVER_LACS.notna() | True)):
    idx = b.index[mask]
    res.append("[%s] stocks=%d" % (universe, len(idx)))
    for m in ("dma50", "dma200", "vol20", "high252", "ret63"):
        x, y = a.loc[idx, m], b.loc[idx, m]
        both = x.notna() & y.notna()
        nan_diff = int((x.notna() != y.notna()).sum())
        diff = (x - y).abs()[both]
        chg = int((diff > 1e-9).sum()) + nan_diff
        mx = diff.max() if len(diff) else 0
        sym = diff.idxmax() if len(diff) else "-"
        rel = ((x - y).abs() / y.abs().replace(0, np.nan))[both].max() if len(diff) else 0
        res.append("  %-8s changed=%d (of %d defined in both; NaN-status differs: %d) max_abs_diff=%.6f (%s) max_rel_diff=%.4f%%" % (m, chg, int(both.sum()), nan_diff, mx, sym, (rel or 0) * 100))
    sa = a.loc[idx].apply(nu.dma_status, axis=1)
    sb = b.loc[idx].apply(nu.dma_status, axis=1)
    res.append("  dma status label changed for %d stocks" % int((sa != sb).sum()))
    ra = (a.loc[idx, "ret63"].rank(pct=True) * 98 + 1).round()
    rb = (b.loc[idx, "ret63"].rank(pct=True) * 98 + 1).round()
    res.append("  RS rating (from ret63) changed for %d stocks" % int((ra.fillna(-1) != rb.fillna(-1)).sum()))
    pa = (a.loc[idx, "CLOSE_PRICE"] > a.loc[idx, "high252"]) != (b.loc[idx, "CLOSE_PRICE"] > b.loc[idx, "high252"])
    res.append("  52W-high flag (close>high252) changed for %d stocks" % int(pa.sum()))
    va = (a.loc[idx, "TTL_TRD_QNTY"] / a.loc[idx, "vol20"] >= 2) != (b.loc[idx, "TTL_TRD_QNTY"] / b.loc[idx, "vol20"] >= 2)
    res.append("  volume>=2x flag changed for %d stocks" % int(va.sum()))
    break  # the second universe adds little beyond the first; keep the report focused
for l in res:
    out(l)
notice("ITEM 6-8 scans metrics with vs without duplicates", res)

# ITEM 10: weekday gaps
out("")
first, lastf = info[files[0].name]["fd"], info[files[-1].name]["fd"]
have_fn = {v["fd"] for v in info.values()}
valid_dates = {d.date() for d in df.DATE.unique()}
gaps = []
d = first
while d <= lastf:
    if d.weekday() < 5 and d not in have_fn and d.isoformat() not in hol:
        carried = files_by_date.get(d, [])
        gaps.append("%s %s: no file, not in holidays.txt; DATE1 data for this date exists in: %s" % (d, d.strftime("%a"), carried or "NO FILE"))
    d += dt.timedelta(days=1)
out("ITEM 10a: weekdays between first and last filename with NO file and NOT in holidays.txt: %d" % len(gaps))
for g in gaps:
    out("  " + g)
nofile_hol = [x for x in sorted(hol) if first.isoformat() <= x <= lastf.isoformat()]
out("holidays.txt entries in range: %d" % len(nofile_hol))
nodata = []
d = first
while d <= lastf:
    if d.weekday() < 5 and d not in valid_dates:
        nodata.append("%s %s%s" % (d, d.strftime("%a"), " [in holidays.txt]" if d.isoformat() in hol else " [NOT in holidays.txt]"))
    d += dt.timedelta(days=1)
out("ITEM 10b: weekdays with NO valid EQ DATE1 row at all in the consumed data: %d" % len(nodata))
for g in nodata:
    out("  " + g)
notice("ITEM 10a gaps (no file, not in holidays.txt)", gaps or ["none"])
notice("ITEM 10b weekdays with no data at all", nodata or ["none"])
notice("holidays.txt in range", [" ".join(nofile_hol)])

sm = os.environ.get("GITHUB_STEP_SUMMARY")
if sm:
    with open(sm, "a", encoding="utf-8") as fh:
        fh.write("### Cache diagnostic (read-only)\n\n```\n" + "\n".join(LINES) + "\n```\n")
