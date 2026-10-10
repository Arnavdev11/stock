"""
live/market_hours.py - is the market open? Pure functions: the caller supplies an aware datetime in IST, the holiday set, and (optionally) the status the
feed itself reported for the equity segment.

Rules (conservative: when in doubt the page keeps showing end-of-day data):
  * weekend or listed holiday                       -> closed
  * the feed's own equity-segment status, when present:  NORMAL_OPEN -> open;  PRE_OPEN_* -> pre_open;  NORMAL_CLOSE / CLOSING_* -> closed
    (the feed wins over the clock, so a special session is followed; but the feed can never make a weekend or a listed holiday "open")
  * otherwise the clock:  09:00-09:15 pre_open,  09:15-15:30 open,  anything else closed
The relay's connection window (when the process holds the socket) is 08:55-15:45 on trading days.
"""
import datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PRE_OPEN, OPEN, CLOSE = dt.time(9, 0), dt.time(9, 15), dt.time(15, 30)
WINDOW_START, WINDOW_END = dt.time(8, 55), dt.time(15, 45)


def parse_holidays(text):
    """Whitespace-separated YYYY-MM-DD tokens (the format of data/holidays.txt); anything else is ignored."""
    out = set()
    for t in str(text or "").split():
        if len(t) == 10:
            try:
                dt.date.fromisoformat(t)
                out.add(t)
            except ValueError:
                pass
    return out


def is_trading_day(d, holidays):
    return d.weekday() < 5 and d.isoformat() not in holidays


def status(now, holidays, segment_status=None):
    """-> 'open' | 'pre_open' | 'closed'"""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(IST)
    if not is_trading_day(now.date(), holidays):
        return "closed"
    if segment_status:
        if segment_status == "NORMAL_OPEN":
            return "open"
        if segment_status in ("PRE_OPEN_START", "PRE_OPEN_END"):
            return "pre_open"
        if segment_status in ("NORMAL_CLOSE", "CLOSING_START", "CLOSING_END"):
            return "closed"
    t = now.time()
    if OPEN <= t < CLOSE:
        return "open"
    if PRE_OPEN <= t < OPEN:
        return "pre_open"
    return "closed"


def in_connection_window(now, holidays):
    now = now.astimezone(IST)
    return is_trading_day(now.date(), holidays) and WINDOW_START <= now.time() < WINDOW_END


def session_date(now):
    return now.astimezone(IST).date().isoformat()


def next_window_start(now, holidays):
    """The next moment the connection window opens (aware datetime, IST)."""
    now = now.astimezone(IST)
    d = now.date()
    cand = dt.datetime.combine(d, WINDOW_START, IST)
    if cand <= now or not is_trading_day(d, holidays):
        d += dt.timedelta(days=1)
        while not is_trading_day(d, holidays):
            d += dt.timedelta(days=1)
        cand = dt.datetime.combine(d, WINDOW_START, IST)
    return cand
