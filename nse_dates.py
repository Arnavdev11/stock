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
HISTORY_DAYS = 400                                 # the project's download window: nse_updater.DAYS_BACK (a test keeps the two equal). A record for a day older than this is outside the supported history
FAILURE_FILE_NAME = "nse_failed_requests.txt"      # data/nse_failed_requests.txt: days whose request failed or returned something unusable (see read_failures)
REQUEST_RETRY_DAYS = 1                             # a failed request is repeated at most once per calendar day ...
MAX_ATTEMPTS_TRANSIENT, MAX_ATTEMPTS_OTHER = 8, 4  # ... and only this many times (transient: rate limit / server error), after which it is left alone and reported
FAILURE_KINDS = ("access_denied", "rate_limited", "server_error", "http_error", "invalid_response", "undecodable", "mismatch")
TRANSIENT_KINDS = ("rate_limited", "server_error")
_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # ASCII digits only (str.isdigit / \d also accept other scripts); used with fullmatch
_COUNT = re.compile(r"[1-9][0-9]?")                # a plain positive integer: no sign, no underscore, no leading zero, no non-ASCII digits


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


def _as_date(v):
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(v)


def stale_problem(requested, served):
    """The ONE policy for whether `served` can be the earlier trading day NSE answered `requested` with. None when believable, else the reason. Used when a response is judged
    (classify), when it is recorded, and when the record is read back, so what is written is always what the reader accepts."""
    req, srv = _as_date(requested), _as_date(served)
    if req.weekday() >= 5:
        return "the requested day is a weekend"
    if srv.weekday() >= 5:
        return "the served day is a weekend (NSE does not publish a trading file for it)"
    if not srv < req:
        return "the served day is not earlier than the requested day"
    if (req - srv).days > MAX_GAP_DAYS:
        return "the served day is more than %d days before the requested day" % MAX_GAP_DAYS
    return None


def classify(found, requested):
    """(verdict, served_day). `found` = ISO dates (or None) read from the rows; `requested` = the day asked for, a date or an ISO string.
    stale_copy only when every row is dated ONE earlier day that stale_problem() accepts; an earlier day it does not accept (a weekend, too far back) is a mismatch."""
    req = requested if isinstance(requested, str) else requested.isoformat()
    found = list(found)
    if not found or any(f is None for f in found):
        return "mismatch", None
    days = set(found)
    if days == {req}:
        return "match", None
    if len(days) == 1 and next(iter(days)) < req and stale_problem(req, next(iter(days))) is None:
        return "stale_copy", next(iter(days))
    return "mismatch", None


def old_enough(requested_iso, today):
    """True when the requested day is more than RECENT_DAYS before `today` (a date): too old for 'not published yet' to be the only explanation."""
    return (today - dt.date.fromisoformat(requested_iso)).days > RECENT_DAYS


# ------------------------------------------------------------------ what one HTTP answer is
def has_symbol_header(text):
    """True when the first non-empty line of the text is a CSV header containing a SYMBOL column (an HTML or error page is not)."""
    for line in text.splitlines():
        if line.strip():
            return "SYMBOL" in [c.strip() for c in line.split(",")]
    return False


def classify_response(status, content):
    """(kind, text, encoding) for one HTTP answer. kind is one of:
         data              status 200 and a decodable text whose first line is a CSV header with a SYMBOL column (its dates are judged by classify)
         not_found         404 - NSE's way of saying the file does not exist (the project's existing signal for a market holiday, see nse_updater)
         access_denied     401 / 403          rate_limited  429           server_error  5xx          http_error  any other status
         undecodable       200 but the bytes are not text in a supported encoding
         invalid_response  200 but not a bhavcopy CSV (an HTML or error page, an empty body)
       Only not_found can ever lead to a holiday; every other kind is a failure to get an answer about the day, not an answer about the day."""
    if status == 404:
        return "not_found", None, None
    if status in (401, 403):
        return "access_denied", None, None
    if status == 429:
        return "rate_limited", None, None
    if 500 <= status <= 599:
        return "server_error", None, None
    if status != 200:
        return "http_error", None, None
    text, enc = decode_text(content)
    if text is None:
        return "undecodable", None, None
    if not has_symbol_header(text):
        return "invalid_response", None, None
    return "data", text, enc


# ------------------------------------------------------------------ shared record rules
def _iso(text):
    if not _ISO.fullmatch(text):
        raise ValueError("not YYYY-MM-DD")
    return dt.date.fromisoformat(text)


def _count(text, cap):
    if not _COUNT.fullmatch(text):
        raise ValueError("not a plain positive integer")
    n = int(text)
    if n > cap:
        raise ValueError("above the limit")
    return n


def history_problem(count, first, last, step_days, noun="check"):
    """The relationship between a count of checks (or attempts) and the dates they happened on, as the writer produces it: one -> first and last are the same day; every further one
    is at least `step_days` after the one before, so last - first >= (count - 1) * step_days. A record that says otherwise cannot have been written by this code."""
    if first > last:
        return "first_seen is after the last date"
    span = (last - first).days
    if count == 1 and span != 0:
        return "one %s, but the first and last dates differ" % noun
    if span < (count - 1) * step_days:
        return "%d %ss need at least %d days between the first and last dates (found %d)" % (count, noun, (count - 1) * step_days, span)
    return None


def _window_problem(req, today):
    if today is None:
        return None
    if req > today:
        return "the requested day is in the future"
    if (today - req).days > HISTORY_DAYS:
        return "the requested day is outside the supported history (older than %d days, the download window)" % HISTORY_DAYS
    return None


def _read_lines(path):
    """([(line_number, fields)], problems). '#' and blank lines are comments. An unreadable (undecodable) file gives no lines and one problem."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], []
    except UnicodeDecodeError:
        return [], [(0, "the file is not readable text; none of it was used")]
    return [(n, raw.split()) for n, raw in enumerate(lines, 1) if raw.strip() and not raw.strip().startswith("#")], []


# ------------------------------------------------------------------ the persisted stale-copy observations
def read_observations(path, today=None):
    """({requested_day: record}, [(line_number, reason)]). Each line is  REQUESTED SERVED FIRST_SEEN LAST_CHECKED CHECKS  (ISO dates, integer 1..MAX_STALE_CHECKS, ASCII only).
    A line that is not exactly that, or is impossible or inconsistent, is NOT used and is returned in the problem list (never silently dropped). A valid line is an unconfirmed
    observation of what NSE answered, not a holiday. Rules, all shared with the writer: stale_problem(); history_problem() with REVALIDATE_AFTER_DAYS (so 'settled' = MAX_STALE_CHECKS
    checks needs at least (MAX_STALE_CHECKS - 1) * REVALIDATE_AFTER_DAYS days); first seen more than RECENT_DAYS after the requested day. `today` (a date), when given, also rejects
    a requested or checked day in the future and a requested day outside the supported history."""
    rows, problems = _read_lines(path)
    records = {}
    for n, parts in rows:
        if len(parts) != 5:
            problems.append((n, "expected 5 fields: requested served first_seen last_checked checks"))
            continue
        try:
            req, srv, first, last = (_iso(p) for p in parts[:4])
            checks = _count(parts[4], MAX_STALE_CHECKS)
        except ValueError:
            problems.append((n, "not four YYYY-MM-DD dates and a check count of 1 to %d written as plain digits" % MAX_STALE_CHECKS))
            continue
        why = stale_problem(req, srv) or history_problem(checks, first, last, REVALIDATE_AFTER_DAYS)
        if not why and (first - req).days <= RECENT_DAYS:
            why = "first seen too soon after the requested day (it should have been retried, not recorded)"
        if not why:
            why = _window_problem(req, today)
        if not why and today is not None and last > today:
            why = "last_checked is in the future"
        if not why and req.isoformat() in records:
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
    """Record one more stale answer for the requested day. Same served day as before -> one more check; a different served day (NSE is not consistent) starts again.
    Refuses (ValueError) what read_observations would refuse, so nothing is ever written that the reader would then reject."""
    why = stale_problem(requested_iso, served_iso)
    if why:
        raise ValueError(why)
    t = today.isoformat()
    old = records.get(requested_iso)
    if old and old["served"] == served_iso:
        records[requested_iso] = dict(old, last_checked=t, checks=min(MAX_STALE_CHECKS, old["checks"] + 1))
    else:
        records[requested_iso] = {"requested": requested_iso, "served": served_iso, "first_seen": t, "last_checked": t, "checks": 1}
    return records[requested_iso]


def due_for_recheck(record, today):
    """True when the observation has been seen fewer than MAX_STALE_CHECKS times and its last check is at least REVALIDATE_AFTER_DAYS old."""
    return record["checks"] < MAX_STALE_CHECKS and (today - dt.date.fromisoformat(record["last_checked"])).days >= REVALIDATE_AFTER_DAYS


def state_of(record):
    """'observed' (will be asked for again) or 'settled' (seen MAX_STALE_CHECKS times, at least (MAX_STALE_CHECKS - 1) * REVALIDATE_AFTER_DAYS days apart: no longer asked for
    automatically). Both are unconfirmed."""
    return "observed" if record["checks"] < MAX_STALE_CHECKS else "settled"


# ------------------------------------------------------------------ the persisted failed requests
def max_attempts(kind):
    return MAX_ATTEMPTS_TRANSIENT if kind in TRANSIENT_KINDS else MAX_ATTEMPTS_OTHER


def read_failures(path, today=None):
    """({requested_day: record}, [(line_number, reason)]). Each line is  REQUESTED KIND FIRST_SEEN LAST_ATTEMPT ATTEMPTS  where KIND is one of FAILURE_KINDS. These are days on which NSE did
    not give a usable answer (blocked, rate limited, server error, an HTML/error page, undecodable bytes, a file dated otherwise than asked for). They say nothing about whether the
    day was a market holiday. Same strictness as read_observations: syntax, kind, weekday, supported history, first seen more than RECENT_DAYS after the requested day,
    ATTEMPTS 1..max_attempts(kind), and history_problem() with REQUEST_RETRY_DAYS."""
    rows, problems = _read_lines(path)
    records = {}
    for n, parts in rows:
        if len(parts) != 5:
            problems.append((n, "expected 5 fields: requested kind first_seen last_attempt attempts"))
            continue
        kind = parts[1]
        try:
            req, first, last = _iso(parts[0]), _iso(parts[2]), _iso(parts[3])
            attempts = _count(parts[4], max_attempts(kind) if kind in FAILURE_KINDS else MAX_ATTEMPTS_TRANSIENT)
        except ValueError:
            problems.append((n, "not three YYYY-MM-DD dates and an attempt count written as plain digits within the limit for the kind"))
            continue
        why = None
        if kind not in FAILURE_KINDS:
            why = "unknown kind (one of: %s)" % ", ".join(FAILURE_KINDS)
        elif req.weekday() >= 5:
            why = "the requested day is a weekend"
        else:
            why = history_problem(attempts, first, last, REQUEST_RETRY_DAYS, "attempt")
        if not why and (first - req).days <= RECENT_DAYS:
            why = "first seen too soon after the requested day (it should have been retried, not recorded)"
        if not why:
            why = _window_problem(req, today)
        if not why and today is not None and last > today:
            why = "last_attempt is in the future"
        if not why and req.isoformat() in records:
            why = "the requested day is listed twice"
        if why:
            problems.append((n, why))
            continue
        records[req.isoformat()] = {"requested": req.isoformat(), "kind": kind, "first_seen": first.isoformat(), "last_attempt": last.isoformat(), "attempts": attempts}
    return records, problems


def write_failures(path, records):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = ("# Weekdays for which NSE gave no usable answer (blocked, rate limited, server error, an HTML/error page, bytes that are not text, or a file dated otherwise than asked for).\n"
            "# These say nothing about whether the day was a market holiday, and they are never treated as one. Asked again at most once a day, a limited number of times.\n"
            "# requested kind first_seen last_attempt attempts\n")
    p.write_text(head + "".join("%s %s %s %s %d\n" % (r["requested"], r["kind"], r["first_seen"], r["last_attempt"], r["attempts"]) for _, r in sorted(records.items())), encoding="utf-8")


def record_failure(records, requested_iso, kind, today):
    """One more failed attempt for the day. The same kind as before -> one more attempt; a different kind starts again."""
    if kind not in FAILURE_KINDS:
        raise ValueError("unknown kind %r" % kind)
    t = today.isoformat()
    old = records.get(requested_iso)
    if old and old["kind"] == kind:
        records[requested_iso] = dict(old, last_attempt=t, attempts=min(max_attempts(kind), old["attempts"] + 1))
    else:
        records[requested_iso] = {"requested": requested_iso, "kind": kind, "first_seen": t, "last_attempt": t, "attempts": 1}
    return records[requested_iso]


def request_due(record, today):
    """True when the failed request may be repeated: fewer than max_attempts(kind) attempts so far and the last one was at least REQUEST_RETRY_DAYS ago."""
    return record["attempts"] < max_attempts(record["kind"]) and (today - dt.date.fromisoformat(record["last_attempt"])).days >= REQUEST_RETRY_DAYS


def request_state(record):
    """'retrying' (will be asked for again) or 'given_up' (max_attempts reached: no longer asked for automatically, still reported). Neither is a holiday."""
    return "retrying" if record["attempts"] < max_attempts(record["kind"]) else "given_up"
