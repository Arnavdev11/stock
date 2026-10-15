"""
nse_dates.py - what the dates INSIDE an NSE daily file say, compared with the date that was asked for. Standard library only (no network, no pandas).

NSE answers a request for a market-holiday file (sec_bhavdata_full_DDMMYYYY.csv) with a copy of the PREVIOUS trading day's file, so a file saved under the holiday's
name holds rows dated the earlier day. Three outcomes, and nothing else is ever accepted:

    match       every row is dated the requested day                         -> the file may be saved under that day
    stale_copy  every row is dated ONE earlier day (a copy of that day)      -> never saved as the requested day; the day is reported, not hidden
    mismatch    anything else (mixed dates, a later date, unreadable dates,
                no rows, no DATE1 column)                                    -> never saved; kept visible as a problem

Nothing here relabels a file or changes a date. A stale copy is evidence about the REQUESTED day (no session, or not published yet), not data for it.
"""
import csv
import datetime as dt
import io
from pathlib import Path

STALE_FILE_NAME = "nse_stale_copy_days.txt"      # data/nse_stale_copy_days.txt: one "REQUESTED_DAY SERVED_DAY" pair per line, both YYYY-MM-DD
RECENT_DAYS = 3                                    # a day this recent may simply not be published yet; older than this, a stale copy means no session


def parse_nse_date(text):
    """'03-Oct-2025' -> '2025-10-03', or None when it cannot be read."""
    try:
        return dt.datetime.strptime(str(text).strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def response_dates(content):
    """The DATE1 of every data row of a downloaded file (bytes or text), as ISO dates; None for a value that cannot be read. [] when there are no data rows,
    [None] when the file has no readable DATE1 column."""
    try:
        text = content.decode("utf-8-sig") if isinstance(content, (bytes, bytearray)) else str(content)
        reader = csv.reader(io.StringIO(text))
        header = [h.strip() for h in next(reader)]
        if "DATE1" not in header:
            return [None]
        i = header.index("DATE1")
        return [parse_nse_date(r[i]) if len(r) > i else None for r in reader if any(c.strip() for c in r)]
    except (UnicodeDecodeError, csv.Error, StopIteration):
        return [None]


def classify(found, requested):
    """(verdict, served_day). `found` = ISO dates (or None) read from the rows; `requested` = the day asked for, a date or an ISO string."""
    req = requested if isinstance(requested, str) else requested.isoformat()
    found = list(found)
    if not found or any(f is None for f in found):
        return "mismatch", None
    days = set(found)
    if days == {req}:
        return "match", None
    if len(days) == 1 and next(iter(days)) < req:
        return "stale_copy", next(iter(days))
    return "mismatch", None


def old_enough(requested_iso, today):
    """True when the requested day is more than RECENT_DAYS before `today` (a date): too old for 'not published yet' to be the explanation."""
    return (today - dt.date.fromisoformat(requested_iso)).days > RECENT_DAYS


def read_stale(path):
    """{requested_day: served_day} from the list file. A line that is not two valid ISO dates (served earlier than requested) is ignored; a missing file is empty."""
    out = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            req, srv = (dt.date.fromisoformat(p).isoformat() for p in line.split())      # not exactly two valid dates -> ValueError -> line ignored
        except ValueError:
            continue
        if srv < req:
            out[req] = srv
    return out


def write_stale(path, stale):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join("%s %s\n" % (r, stale[r]) for r in sorted(stale)), encoding="utf-8")
