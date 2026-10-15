"""
nse_dates.py - what the dates INSIDE an NSE daily file say, compared with the date that was asked for. Standard library only (no network, no pandas).

NSE sometimes answers a request for a market-holiday file (sec_bhavdata_full_DDMMYYYY.csv) with a copy of the PREVIOUS trading day's file, so a file saved under that
day's name would hold rows dated the earlier day. Three outcomes, and nothing else is ever accepted:

    match       every row is dated the requested day                         -> the file may be saved under that day
    stale_copy  every row is dated ONE earlier day (a copy of that day)      -> never saved as the requested day; kept as an OBSERVATION (see below)
    mismatch    anything else (mixed dates, a later date, unreadable dates,
                no rows, no DATE1 column, undecodable bytes)                 -> never saved; kept visible as a problem

Nothing here relabels a file or changes a date.

A stale copy proves what NSE ANSWERED on the day it was asked. It does not prove that the requested day was a market holiday (the file may also not have been published,
or NSE may have answered wrongly). So it is stored as an unconfirmed OBSERVATION in data/nse_stale_copy_days.txt, never as a holiday:
    - it is asked for again after REVALIDATE_AFTER_DAYS (a real file that appears later is then saved normally and the observation is dropped);
    - after MAX_STALE_CHECKS consistent answers it is no longer asked for automatically, but it is still reported as unconfirmed;
    - a "confirmed holiday" is only what data/holidays.txt says; no code in this module or in the reports promotes an observation to one.

Encoding: both existing readers (pandas' default and the market loader's utf-8-sig) expect UTF-8, with or without a BOM. decode_text() accepts UTF-8 (+BOM), UTF-16/UTF-32 with a
BOM and strict Windows-1252 (the usual legacy encoding of CSV exports) so that a valid file is not discarded only because of how its bytes are encoded. Anything else, or bytes that
contain NUL, fails closed. Dates are plain ASCII (dd-Mon-yyyy), so the encoding cannot change a date; a file in another encoding is saved by nse_updater as UTF-8 so the readers can use it.
"""
import codecs
import csv
import datetime as dt
import io
import re
from pathlib import Path

STALE_FILE_NAME = "nse_stale_copy_days.txt"      # data/nse_stale_copy_days.txt: one observation per line (see read_observations)
RECENT_DAYS = 3                                    # a day this recent may simply not be published yet; older than this, a stale copy is recorded as an observation
REVALIDATE_AFTER_DAYS = 7                          # an observation is asked for again once its last check is this old ...
MAX_STALE_CHECKS = 3                               # ... until it has been seen this many times; then it is no longer asked for automatically (and is still only an observation)
MAX_GAP_DAYS = 10                                  # served day more than this many calendar days before the requested day is not believable
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_nse_date(text):
    """'03-Oct-2025' -> '2025-10-03', or None when it cannot be read."""
    try:
        return dt.datetime.strptime(str(text).strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


# ------------------------------------------------------------------ decoding and reading one response
def decode_text(content):
    """(text, encoding) or (None, None). Fails closed: never guesses past the encodings listed in the module docstring."""
    if isinstance(content, str):
        return (content, "str") if "\x00" not in content else (None, None)
    b = bytes(content)
    try:
        if b.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            text, enc = b.decode("utf-32"), "utf-32"
        elif b.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text, enc = b.decode("utf-16"), "utf-16"
        elif b.startswith(codecs.BOM_UTF8):
            text, enc = b[len(codecs.BOM_UTF8):].decode("utf-8"), "utf-8-sig"
        else:
            try:
                text, enc = b.decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                text, enc = b.decode("cp1252"), "cp1252"          # strict: the five bytes cp1252 leaves undefined raise, so garbage is not "decoded"
    except UnicodeDecodeError:
        return None, None
    return (text, enc) if "\x00" not in text else (None, None)


def dates_of_text(text):
    """The DATE1 of every data row, as ISO dates; None for a value that cannot be read. [None] when the text has no readable DATE1 column or is not text; [] when there are no data rows."""
    if text is None:
        return [None]
    try:
        reader = csv.reader(io.StringIO(text))
        header = [h.strip() for h in next(reader)]
        if "DATE1" not in header:
            return [None]
        i = header.index("DATE1")
        return [parse_nse_date(r[i]) if len(r) > i else None for r in reader if any(c.strip() for c in r)]
    except (csv.Error, StopIteration):
        return [None]


def response_dates(content):
    """The DATE1 of every data row of a downloaded file (bytes or text). See dates_of_text and decode_text."""
    return dates_of_text(decode_text(content)[0])


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
    """True when the requested day is more than RECENT_DAYS before `today` (a date): too old for 'not published yet' to be the only explanation."""
    return (today - dt.date.fromisoformat(requested_iso)).days > RECENT_DAYS


# ------------------------------------------------------------------ the persisted observations
def _iso(text):
    if not _ISO.match(text):
        raise ValueError("not YYYY-MM-DD")
    return dt.date.fromisoformat(text)


def read_observations(path, today=None):
    """({requested_day: record}, [(line_number, reason)]). Each line is  REQUESTED SERVED FIRST_SEEN LAST_CHECKED CHECKS  (ISO dates, integer). A line that is not exactly that,
    or is impossible or inconsistent, is NOT used and is returned in the problem list (never silently dropped); '#' lines and blank lines are comments. A valid line is an
    unconfirmed observation of what NSE answered, not a holiday. `today` (a date), when given, also rejects a last check in the future."""
    records, problems = {}, []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return records, problems
    except UnicodeDecodeError:
        return records, [(0, "the file is not readable text; none of it was used")]
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 5:
            problems.append((n, "expected 5 fields: requested served first_seen last_checked checks"))
            continue
        try:
            req, srv, first, last = (_iso(p) for p in parts[:4])
            checks = int(parts[4])
        except ValueError:
            problems.append((n, "not four YYYY-MM-DD dates and an integer"))
            continue
        why = None
        if not 1 <= checks <= 99:
            why = "checks must be 1 to 99"
        elif req.weekday() >= 5:
            why = "the requested day is a weekend"
        elif srv.weekday() >= 5:
            why = "the served day is a weekend (NSE does not publish a trading file for it)"
        elif not srv < req:
            why = "the served day is not earlier than the requested day"
        elif (req - srv).days > MAX_GAP_DAYS:
            why = "the served day is more than %d days before the requested day" % MAX_GAP_DAYS
        elif not first <= last:
            why = "first_seen is after last_checked"
        elif (first - req).days <= RECENT_DAYS:
            why = "first seen too soon after the requested day (it should have been retried, not recorded)"
        elif today is not None and last > today:
            why = "last_checked is in the future"
        elif req.isoformat() in records:
            why = "the requested day is listed twice"
        if why:
            problems.append((n, why))
            continue
        records[req.isoformat()] = {"requested": req.isoformat(), "served": srv.isoformat(), "first_seen": first.isoformat(), "last_checked": last.isoformat(), "checks": checks}
    return records, problems


def write_observations(path, records):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = ("# Days on which NSE answered a request with an EARLIER trading day's file. These are unconfirmed observations of what NSE sent, not a holiday list.\n"
            "# requested served first_seen last_checked checks\n")
    p.write_text(head + "".join("%s %s %s %s %d\n" % (r["requested"], r["served"], r["first_seen"], r["last_checked"], r["checks"]) for _, r in sorted(records.items())), encoding="utf-8")


def observe(records, requested_iso, served_iso, today):
    """Record one more stale answer for the requested day. Same served day as before -> one more check; a different served day (NSE is not consistent) starts again."""
    t = today.isoformat()
    old = records.get(requested_iso)
    if old and old["served"] == served_iso:
        records[requested_iso] = dict(old, last_checked=t, checks=min(99, old["checks"] + 1))
    else:
        records[requested_iso] = {"requested": requested_iso, "served": served_iso, "first_seen": t, "last_checked": t, "checks": 1}
    return records[requested_iso]


def due_for_recheck(record, today):
    """True when the observation has been seen fewer than MAX_STALE_CHECKS times and its last check is at least REVALIDATE_AFTER_DAYS old."""
    return record["checks"] < MAX_STALE_CHECKS and (today - dt.date.fromisoformat(record["last_checked"])).days >= REVALIDATE_AFTER_DAYS


def state_of(record):
    """'observed' (will be asked for again) or 'settled' (seen MAX_STALE_CHECKS times: no longer asked for automatically). Both are unconfirmed."""
    return "observed" if record["checks"] < MAX_STALE_CHECKS else "settled"
