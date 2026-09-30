"""
nse_updater.py - daily end-of-day data updater for a swing-trading screener.

WHAT IT DOES (run once each evening, after about 8:30 PM IST)
  1. Downloads NSE's daily file "sec_bhavdata_full" (price, volume, delivery %) for any missing day.
  2. Downloads the latest bulk and block deals and keeps a running history.
  3. Downloads FII / DII daily figures and keeps a running history.
  4. Calculates: gainers, losers, volume gainers, 52-week highs, 50/200 DMA status,
     delivery, and a relative strength rating (1-99).
  5. Writes JSON files into ./out that your website reads.

SETUP:   pip install pandas requests
RUN:     python nse_updater.py            (first run downloads ~1 year, takes a while)
SCHEDULE: Linux cron   30 20 * * 1-5  cd /path && python nse_updater.py
          or a free GitHub Actions schedule.

IMPORTANT
  * NSE changes file locations and blocks aggressive scripts. If a download fails, check the
    URL on nseindia.com (Market Data > Reports) and update the constants below.
  * Be gentle: this script waits between downloads. Do not lower the wait.
  * These files are for your own analysis. Before you PUBLISH NSE data on a paid website,
    get written permission or buy data from a licensed vendor.
"""
import io
import json
import os
import sys
import time
import datetime as dt
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(os.environ.get("DATA_DIR", "."))
RAW, OUT = ROOT / "data" / "raw", ROOT / "out"
HOLIDAYS = ROOT / "data" / "holidays.txt"
ARCHIVE = "https://archives.nseindia.com"
BHAV_URL = ARCHIVE + "/products/content/sec_bhavdata_full_{:%d%m%Y}.csv"
BULK_URL = ARCHIVE + "/content/equities/bulk.csv"
BLOCK_URL = ARCHIVE + "/content/equities/block.csv"
FIIDII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
DAYS_BACK = 400          # about 1 year of trading days plus buffer (needed for 200 DMA and 52-week high)
MIN_TURNOVER_LACS = 100  # ignore stocks trading less than Rs 1 crore a day
WAIT = 1.5               # seconds between downloads

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def log(*a):
    print(dt.datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    try:  # visiting the home page first gives us the cookies NSE expects
        s.get("https://www.nseindia.com", timeout=30)
    except requests.RequestException as e:
        log("warning: could not open nseindia.com:", e)
    return s


# ---------------------------------------------------------------- 1. download daily files
def download_missing(s):
    RAW.mkdir(parents=True, exist_ok=True)
    known_holidays = set(HOLIDAYS.read_text().split()) if HOLIDAYS.exists() else set()
    today = dt.date.today()
    new = 0
    for i in range(DAYS_BACK, -1, -1):
        d = today - dt.timedelta(days=i)
        f = RAW / f"bhav_{d:%Y%m%d}.csv"
        if d.weekday() >= 5 or f.exists() or d.isoformat() in known_holidays:
            continue
        try:
            r = s.get(BHAV_URL.format(d), timeout=30)
            ok = r.status_code == 200 and b"SYMBOL" in r.content[:300]
        except requests.RequestException as e:
            log("network problem on", d, e)
            ok = False
            time.sleep(5)
            continue
        if ok:
            f.write_bytes(r.content)
            new += 1
            log("saved", f.name)
        elif (today - d).days > 3:  # older than 3 days and missing = market holiday
            known_holidays.add(d.isoformat())
        time.sleep(WAIT)
    HOLIDAYS.parent.mkdir(parents=True, exist_ok=True)
    HOLIDAYS.write_text("\n".join(sorted(known_holidays)))
    log("new daily files:", new)


# ---------------------------------------------------------------- 2. calculations
def load_prices():
    files = sorted(RAW.glob("bhav_*.csv"))
    if not files:
        sys.exit("No data files yet. Check your internet connection and the URL constants.")
    df = pd.concat((pd.read_csv(f, skipinitialspace=True) for f in files), ignore_index=True)
    df.columns = [c.strip() for c in df.columns]
    for c in ("SYMBOL", "SERIES", "DATE1"):
        df[c] = df[c].astype(str).str.strip()
    df = df[df.SERIES == "EQ"].copy()  # normal equity only (skips BE series, ETFs etc.)
    df["DATE"] = pd.to_datetime(df.DATE1, format="%d-%b-%Y")
    for c in ("PREV_CLOSE", "HIGH_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "DELIV_PER"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["SYMBOL", "DATE"]).reset_index(drop=True)


def add_indicators(df):
    g = df.groupby("SYMBOL", sort=False)
    df["dma50"] = g.CLOSE_PRICE.transform(lambda x: x.rolling(50).mean())
    df["dma200"] = g.CLOSE_PRICE.transform(lambda x: x.rolling(200).mean())
    df["prev50"] = df.groupby("SYMBOL", sort=False).dma50.shift(1)
    df["prev200"] = df.groupby("SYMBOL", sort=False).dma200.shift(1)
    df["vol20"] = g.TTL_TRD_QNTY.transform(lambda x: x.shift(1).rolling(20).mean())
    df["deliv20"] = g.DELIV_PER.transform(lambda x: x.shift(1).rolling(20, min_periods=10).mean())
    df["high252"] = g.HIGH_PRICE.transform(lambda x: x.shift(1).rolling(252, min_periods=150).max())
    df["ret63"] = g.CLOSE_PRICE.pct_change(63) * 100  # about 3 months
    return df


def dma_status(r):
    if pd.isna(r.dma200):
        return "Not enough history"
    if r.dma50 > r.dma200 and r.prev50 <= r.prev200:
        return "Golden cross today"
    if r.dma50 < r.dma200 and r.prev50 >= r.prev200:
        return "Death cross today"
    if r.CLOSE_PRICE > r.dma50 and r.CLOSE_PRICE > r.dma200:
        return "Above both"
    if r.CLOSE_PRICE < r.dma50 and r.CLOSE_PRICE < r.dma200:
        return "Below both"
    return "Between the two"


def build_scans(df):
    last_day = df.DATE.max()
    t = df[(df.DATE == last_day) & (df.TURNOVER_LACS >= MIN_TURNOVER_LACS)].copy()
    t["chg"] = (t.CLOSE_PRICE / t.PREV_CLOSE - 1) * 100
    t["volx"] = t.TTL_TRD_QNTY / t.vol20
    t["rs"] = (t.ret63.rank(pct=True) * 98 + 1).round()  # 99 = strongest of all stocks
    t["status"] = t.apply(dma_status, axis=1)

    def rows(x, cols, n=25):
        x = x[list(cols.keys())].rename(columns=cols).head(n).round(2)
        return x.astype(object).where(pd.notna(x), None).to_dict("records")

    base = {"SYMBOL": "symbol", "CLOSE_PRICE": "price", "chg": "change_pct", "volx": "volume_x_avg"}
    scans = {
        "as_of": last_day.strftime("%Y-%m-%d"),
        "gainers": rows(t.sort_values("chg", ascending=False), base),
        "losers": rows(t.sort_values("chg"), base),
        "volume_gainers": rows(
            t[(t.chg > 0) & (t.volx >= 2)].sort_values("volx", ascending=False),
            {**base, "TTL_TRD_QNTY": "volume"}),
        "high_52w": rows(
            t[t.CLOSE_PRICE > t.high252].sort_values("chg", ascending=False),
            {**base, "high252": "old_52w_high"}),
        "delivery": rows(
            t[(t.DELIV_PER > t.deliv20 * 1.3) & (t.chg > 0)].sort_values("DELIV_PER", ascending=False),
            {**base, "DELIV_PER": "delivery_pct", "deliv20": "usual_delivery_pct"}),
        "dma": rows(
            t[t.status != "Not enough history"].sort_values("rs", ascending=False),
            {"SYMBOL": "symbol", "CLOSE_PRICE": "price", "dma50": "dma50", "dma200": "dma200", "status": "status"}, 500),
        "relative_strength": rows(
            t.sort_values("rs", ascending=False), {"SYMBOL": "symbol", "ret63": "return_3m_pct", "rs": "rs_rating"}, 100),
    }
    breadth = {
        "advancing": int((t.chg > 0).sum()), "declining": int((t.chg < 0).sum()),
        "unchanged": int((t.chg == 0).sum()),
        "above_50dma_pct": round(float((t.CLOSE_PRICE > t.dma50).mean() * 100), 1),
        "above_200dma_pct": round(float((t.CLOSE_PRICE > t.dma200).mean() * 100), 1),
        "new_52w_highs": int((t.CLOSE_PRICE > t.high252).sum()),
    }
    scans["breadth"] = breadth
    return scans


# ---------------------------------------------------------------- 3. big deals and FII/DII
def update_big_deals(s):
    hist = OUT / "bigdeals_history.csv"
    frames = []
    for kind, url in (("Bulk", BULK_URL), ("Block", BLOCK_URL)):
        try:
            r = s.get(url, timeout=30)
            r.raise_for_status()
            x = pd.read_csv(io.StringIO(r.text), skipinitialspace=True)
            x.columns = [c.strip() for c in x.columns]
            x["Type"] = kind
            frames.append(x)
        except Exception as e:
            log("big deals", kind, "failed:", e)
        time.sleep(WAIT)
    if not frames:
        return
    new = pd.concat(frames, ignore_index=True)
    if hist.exists():
        new = pd.concat([pd.read_csv(hist), new], ignore_index=True)
    new = new.drop_duplicates().tail(2000)
    new.to_csv(hist, index=False)
    (OUT / "bigdeals.json").write_text(new.tail(200).fillna("").to_json(orient="records"))
    log("big deals rows:", len(new))


def update_fii_dii(s):
    hist = OUT / "fiidii_history.json"
    try:
        r = s.get(FIIDII_URL, timeout=30)
        r.raise_for_status()
        latest = r.json()
    except Exception as e:
        log("FII/DII failed (NSE may have changed this address):", e)
        return
    old = json.loads(hist.read_text()) if hist.exists() else []
    seen = {(x.get("date"), x.get("category")) for x in old}
    old += [x for x in latest if (x.get("date"), x.get("category")) not in seen]
    hist.write_text(json.dumps(old[-400:], indent=1))
    log("FII/DII rows:", len(old))


# ---------------------------------------------------------------- main
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    s = make_session()
    download_missing(s)
    scans = build_scans(add_indicators(load_prices()))
    (OUT / "scans.json").write_text(json.dumps(scans, indent=1))
    log("scans written for", scans["as_of"])
    update_big_deals(s)
    update_fii_dii(s)
    (OUT / "updated.json").write_text(json.dumps({"updated_at": dt.datetime.now().isoformat(timespec="seconds")}))
    log("done")


if __name__ == "__main__":
    main()
