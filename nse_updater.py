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

import nse_dates as nd

ROOT = Path(os.environ.get("DATA_DIR", "."))
RAW, OUT = ROOT / "data" / "raw", ROOT / "out"
HOLIDAYS = ROOT / "data" / "holidays.txt"
FAILED_REQUESTS = ROOT / "data" / nd.FAILURE_FILE_NAME   # days on which NSE gave no usable answer (blocked, rate limited, server error, HTML page ...): never a holiday, retried a limited number of times
STALE_COPIES = ROOT / "data" / nd.STALE_FILE_NAME      # unconfirmed OBSERVATIONS of days on which NSE answered with an earlier day's file (never saved, never used, never a holiday)
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
def download_missing(s, today=None):
    """Download every missing weekday file. A response is saved under the requested day ONLY if every row inside it is dated that day: a stale copy (NSE's answer for a
    holiday: the previous trading day's file) or any other mismatch is never saved, never relabelled and never used (see nse_dates.py).

    What each kind of answer means (nse_dates.classify_response), for a day more than 3 days old:
      404 (not_found)         the project's existing signal that there is no file -> the day is added to data/holidays.txt, the repository's authoritative holiday list.
                              This is the ONLY way this function adds a holiday.
      stale copy              an unconfirmed OBSERVATION (data/nse_stale_copy_days.txt), asked again after 7 days, left alone after 3 identical answers. Never a holiday.
      403 / 429 / 5xx / other status / a 200 that is not a bhavcopy CSV (an HTML or error page) / undecodable bytes / a file dated otherwise than asked for
                              a FAILED REQUEST (data/nse_failed_requests.txt): not a holiday, the weekday stays an unexplained gap, asked again at most once a day and only a limited
                              number of times (4; 8 for rate limits and server errors), then left alone and reported.
    A connection error says nothing about the day and is not recorded (the next run asks again).
    A day more than 3 days old is judged this way; a more recent one may simply not be published yet and is asked for again on the next run without being recorded.
    A file that exists is never requested again, and a file saved by a later request clears the day's records."""
    RAW.mkdir(parents=True, exist_ok=True)
    known_holidays = set(HOLIDAYS.read_text().split()) if HOLIDAYS.exists() else set()
    today = today or dt.date.today()
    seen, problems = nd.read_observations(STALE_COPIES, today)
    for n, why in problems:
        log("ignored line %d of %s: %s" % (n, STALE_COPIES.name, why))
    changed = bool(problems)          # a rewrite drops the invalid lines (they were logged above)
    failed, fproblems = nd.read_failures(FAILED_REQUESTS, today)
    for n, why in fproblems:
        log("ignored line %d of %s: %s" % (n, FAILED_REQUESTS.name, why))
    fchanged = bool(fproblems)
    new = 0
    for i in range(DAYS_BACK, -1, -1):
        d = today - dt.timedelta(days=i)
        iso = d.isoformat()
        f = RAW / f"bhav_{d:%Y%m%d}.csv"
        if d.weekday() >= 5 or f.exists() or iso in known_holidays:
            continue
        if iso in seen and not nd.due_for_recheck(seen[iso], today):
            continue                  # an observation that was checked recently, or already seen 3 times
        if iso in failed and not nd.request_due(failed[iso], today):
            continue                  # a failed request that was tried today, or has used up its attempts
        try:
            r = s.get(BHAV_URL.format(d), timeout=30)
        except requests.RequestException as e:
            log("network problem on", d, e)
            time.sleep(5)
            continue
        kind, text, enc = nd.classify_response(r.status_code, r.content)
        old = nd.old_enough(iso, today)
        if kind == "data":
            verdict, served = nd.classify(nd.dates_of_text(text), d)
            if verdict == "match":
                if enc in ("utf-8", "utf-8-sig"):
                    f.write_bytes(r.content)             # byte for byte
                else:
                    f.write_bytes(text.encode("utf-8"))  # the readers expect UTF-8: same text, same dates, other encoding
                    log("re-encoded", f.name, "from", enc, "to UTF-8")
                new += 1
                log("saved", f.name)
                if iso in seen:
                    del seen[iso]
                    changed = True
                    log("the file for", iso, "is now available: its earlier stale-copy observation is dropped")
                if iso in failed:
                    del failed[iso]
                    fchanged = True
                    log("the file for", iso, "is now available: its earlier failed-request record is dropped")
            elif verdict == "stale_copy":
                if old:
                    rec = nd.observe(seen, iso, served, today)
                    changed = True
                    log("NOT saved", f.name, "- NSE answered with the file dated", served, "(check %d of %d; an unconfirmed observation, not a confirmed holiday)" % (rec["checks"], nd.MAX_STALE_CHECKS))
                    if iso in failed:
                        del failed[iso]       # a usable (if stale) answer replaces the earlier failure
                        fchanged = True
                else:
                    log("NOT saved", f.name, "- NSE answered with the file dated", served, "; the day may not be published yet, will ask again")
            else:
                kind = "mismatch"
        if kind == "not_found":
            if old:                   # older than 3 days and missing = market holiday (the project's existing rule, unchanged for a 404)
                known_holidays.add(iso)
                if iso in failed:
                    del failed[iso]
                    fchanged = True
        elif kind != "data":
            if kind == "mismatch":
                log("NOT saved", f.name, "- the dates inside the response do not match the day asked for (or are not a believable earlier trading day, or could not be read); not used")
            else:
                log("NOT saved", f.name, "- no usable answer (%s, HTTP %s); not a holiday" % (kind, r.status_code))
            if os.environ.get("GITHUB_ACTIONS"):
                print("::warning title=NSE response not saved::%s: %s" % (f.name, kind), flush=True)
            if old:
                rec = nd.record_failure(failed, iso, kind, today)
                fchanged = True
                log("failed request recorded: %s attempt %d of %d%s" % (kind, rec["attempts"], nd.max_attempts(kind), "" if nd.request_due(rec, today + dt.timedelta(days=1)) else " (no more automatic retries)"))
        time.sleep(WAIT)
    HOLIDAYS.parent.mkdir(parents=True, exist_ok=True)
    HOLIDAYS.write_text("\n".join(sorted(known_holidays)))
    if changed:
        nd.write_observations(STALE_COPIES, seen)
    if fchanged:
        nd.write_failures(FAILED_REQUESTS, failed)
    log("new daily files:", new, "| stale-copy observations on record (unconfirmed, not used):", len(seen), "| failed requests on record (not holidays):", len(failed))


# ---------------------------------------------------------------- 2. calculations
LAST_LOAD_REPORT = {}   # what the last load_prices() accepted and rejected (read by tests and printed in the log)


def file_date(f):
    """The date a saved file claims to be, from its name bhav_YYYYMMDD.csv (None if the name does not fit)."""
    try:
        return dt.datetime.strptime(Path(f).stem.replace("bhav_", "", 1), "%Y%m%d").date()
    except ValueError:
        return None


def read_raw_file(f):
    """Read one saved NSE daily file. Returns (frame, None) or (None, reason).
    DATA QUALITY RULE: a file is used only if EVERY row inside it is dated the date in its filename. NSE sometimes answers a request for a
    market holiday with a copy of the previous trading day's file; that copy must not be counted as a second trading day."""
    name_date = file_date(f)
    if name_date is None:
        return None, "the file name is not bhav_YYYYMMDD.csv"
    d = pd.read_csv(f, skipinitialspace=True)
    d.columns = [c.strip() for c in d.columns]
    for c in ("SYMBOL", "SERIES", "DATE1"):
        d[c] = d[c].astype(str).str.strip()
    days = pd.to_datetime(d.DATE1, format="%d-%b-%Y", errors="coerce")
    off = days != pd.Timestamp(name_date)       # also true for a DATE1 that cannot be read
    if off.any():
        found = sorted({x for x in d.DATE1[off].unique()})[:3]
        return None, "%d of %d row(s) are not dated %s (found: %s)" % (int(off.sum()), len(d), name_date.isoformat(), ", ".join(found))
    return d, None


def load_prices():
    files = sorted(RAW.glob("bhav_*.csv"))
    if not files:
        sys.exit("No data files yet. Check your internet connection and the URL constants.")
    frames, rejected = [], []
    for f in files:
        d, why = read_raw_file(f)
        if why:
            rejected.append({"file": f.name, "reason": why})
            log("REJECTED raw file", f.name, "-", why, "- not used as a trading day")
            if os.environ.get("GITHUB_ACTIONS"):
                print("::warning title=Raw NSE file rejected::%s: %s" % (f.name, why), flush=True)
        else:
            frames.append(d)
    if not frames:
        sys.exit("DATA QUALITY GATE FAILED: every saved NSE file was rejected (%d files)." % len(files))
    df = pd.concat(frames, ignore_index=True)
    df = df[df.SERIES == "EQ"].copy()  # normal equity only (skips BE series, ETFs etc.)
    df["DATE"] = pd.to_datetime(df.DATE1, format="%d-%b-%Y")
    for c in ("PREV_CLOSE", "HIGH_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "DELIV_PER"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # One row per (SYMBOL, DATE): the rolling windows below count rows, so a repeated day would be counted twice.
    # An identical repeat is removed (nothing is chosen); a repeat with different values is never resolved by guessing: the run stops.
    before = len(df)
    df = df.drop_duplicates()
    same = before - len(df)
    clash = df[df.duplicated(["SYMBOL", "DATE"], keep=False)]
    if len(clash):
        ex = clash.sort_values(["SYMBOL", "DATE"]).head(3)
        sys.exit("DATA QUALITY GATE FAILED: %d row(s) repeat a (SYMBOL, DATE1) with DIFFERENT values, e.g. %s. Nothing was written."
                 % (len(clash), "; ".join("%s %s" % (r.SYMBOL, r.DATE1) for r in ex.itertuples())))
    LAST_LOAD_REPORT.clear()
    LAST_LOAD_REPORT.update({"files_found": len(files), "files_used": len(frames), "files_rejected": rejected, "identical_repeat_rows_removed": same})
    log("raw files: %d found, %d used, %d rejected; identical repeated rows removed: %d" % (len(files), len(frames), len(rejected), same))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and rejected:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("### Raw NSE files rejected (filename date does not match the data inside)\n\n" + "\n".join("- `%s`: %s" % (r["file"], r["reason"]) for r in rejected) + "\n")
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
