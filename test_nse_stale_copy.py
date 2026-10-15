"""Tests for the NSE stale-copy handling: nse_dates.py, nse_updater.download_missing and the gap reporting in market_data_updater.py.
No network: the NSE session is a stub. Run from the repository root:  python3 -m unittest test_nse_stale_copy -v

Rules under test: a response is saved under the requested day only if EVERY row inside is dated that day; a stale copy (all rows dated one earlier day) or any other
mismatch is never saved, never relabelled and never used; a holiday is reported as such only when NSE really answered with an earlier day's file and the day is old enough;
a genuinely missing weekday stays visible; the market figures and the trading-day count do not change."""
import contextlib
import datetime as dt
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import market_data_updater as mu
import nse_dates as nd
import nse_updater as nu
import test_market_data as TM
from test_nse_ingestion import HEAD, line

TODAY = dt.date(2026, 10, 14)          # a Wednesday
D = dt.date


def csv_bytes(day_rows):
    """day_rows: list of (symbol, day) -> the bytes of a daily file whose rows carry those dates."""
    return ("\n".join([HEAD] + [line(s, d, 100.0 + i) for i, (s, d) in enumerate(day_rows)]) + "\n").encode()


def file_for(day, syms=("AAA", "BBB", "CCC")):
    return csv_bytes([(s, day) for s in syms])


class Resp:
    def __init__(self, status=200, content=b""):
        self.status_code, self.content = status, content


class Session:
    """Stands in for the NSE session: replies by the requested day (DDMMYYYY in the URL); anything not listed is a 404."""

    def __init__(self, replies):
        self.replies, self.asked = replies, []

    def get(self, url, timeout=None):
        key = url.rsplit("_", 1)[1].split(".")[0]
        day = dt.datetime.strptime(key, "%d%m%Y").date()
        self.asked.append(day)
        r = self.replies.get(day)
        return r if r is not None else Resp(404, b"")


class Dl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.patches = [mock.patch.object(nu, "RAW", self.tmp / "raw"), mock.patch.object(nu, "HOLIDAYS", self.tmp / "holidays.txt"),
                        mock.patch.object(nu, "STALE_COPIES", self.tmp / nd.STALE_FILE_NAME), mock.patch.object(nu, "DAYS_BACK", 14),
                        mock.patch.object(nu.time, "sleep")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def run_dl(self, replies, today=TODAY):
        s = Session(replies)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nu.download_missing(s, today=today)
        return s, buf.getvalue()

    def saved(self):
        return sorted(p.name for p in (self.tmp / "raw").glob("bhav_*.csv"))


class DownloadDecision(Dl):
    def test_a_matching_file_is_saved_byte_for_byte(self):
        d = D(2026, 10, 13)
        body = file_for(d)
        self.run_dl({d: Resp(200, body)})
        self.assertEqual((self.tmp / "raw" / "bhav_20261013.csv").read_bytes(), body)
        self.assertFalse((self.tmp / nd.STALE_FILE_NAME).exists())

    def test_an_old_stale_copy_is_not_saved_and_is_recorded_with_the_day_actually_served(self):
        hol, prev = D(2026, 10, 2), D(2026, 10, 1)
        _, out = self.run_dl({hol: Resp(200, file_for(prev))})
        self.assertNotIn("bhav_20261002.csv", self.saved())
        self.assertEqual(nd.read_stale(self.tmp / nd.STALE_FILE_NAME), {"2026-10-02": "2026-10-01"})
        self.assertIn("NOT saved", out)
        self.assertNotIn("2026-10-02", (self.tmp / "holidays.txt").read_text())        # it is not silently turned into an ordinary known holiday either

    def test_a_recorded_stale_day_is_not_asked_for_again(self):
        hol, prev = D(2026, 10, 2), D(2026, 10, 1)
        self.run_dl({hol: Resp(200, file_for(prev))})
        s, _ = self.run_dl({hol: Resp(200, file_for(prev))})
        self.assertNotIn(hol, s.asked)

    def test_a_recent_stale_copy_is_not_saved_and_not_recorded_so_it_is_asked_again(self):
        today_stale = D(2026, 10, 13)             # yesterday: NSE may simply not have published it yet
        self.run_dl({today_stale: Resp(200, file_for(D(2026, 10, 12)))})
        self.assertNotIn("bhav_20261013.csv", self.saved())
        self.assertFalse((self.tmp / nd.STALE_FILE_NAME).exists())
        s, _ = self.run_dl({today_stale: Resp(200, file_for(today_stale))})        # published later: now it is saved
        self.assertIn(today_stale, s.asked)
        self.assertIn("bhav_20261013.csv", self.saved())

    def test_the_three_day_boundary(self):
        self.assertFalse(nd.old_enough("2026-10-11", D(2026, 10, 14)))     # 3 days: still "may not be published"
        self.assertTrue(nd.old_enough("2026-10-10", D(2026, 10, 14)))      # 4 days: an old day

    def test_mixed_dates_are_never_saved_never_relabelled_and_stay_visible(self):
        d = D(2026, 10, 7)
        mixed = csv_bytes([("AAA", d), ("BBB", D(2026, 10, 6)), ("CCC", d)])
        _, out = self.run_dl({d: Resp(200, mixed)})
        self.assertNotIn("bhav_20261007.csv", self.saved())
        self.assertFalse((self.tmp / nd.STALE_FILE_NAME).exists())           # not a clean copy of one earlier day: not recorded as a holiday
        self.assertIn("do not match", out)

    def test_a_later_dated_response_is_not_saved(self):
        d = D(2026, 10, 7)
        self.run_dl({d: Resp(200, file_for(D(2026, 10, 8)))})
        self.assertEqual(self.saved(), [])
        self.assertFalse((self.tmp / nd.STALE_FILE_NAME).exists())

    def test_unreadable_dates_no_rows_and_no_date_column_are_not_saved(self):
        d = D(2026, 10, 7)
        bad_dates = (HEAD + "\nAAA, EQ, not-a-date, 1, 2, 1, 1, 1, 50\n").encode()
        no_rows = (HEAD + "\n").encode()
        no_col = b"SYMBOL, SERIES, CLOSE_PRICE\nAAA, EQ, 1\n"
        for body in (bad_dates, no_rows, no_col):
            self.run_dl({d: Resp(200, body)})
            self.assertEqual(self.saved(), [], body[:30])
        self.assertFalse((self.tmp / nd.STALE_FILE_NAME).exists())

    def test_a_404_on_an_old_day_is_still_a_known_holiday_and_a_recent_404_is_not(self):
        self.run_dl({})
        known = (self.tmp / "holidays.txt").read_text().split()
        self.assertIn("2026-10-01", known)
        self.assertNotIn("2026-10-13", known)
        self.assertNotIn("2026-10-14", known)

    def test_an_existing_file_is_not_asked_for_again(self):
        d = D(2026, 10, 13)
        (self.tmp / "raw").mkdir(parents=True)
        (self.tmp / "raw" / "bhav_20261013.csv").write_bytes(file_for(d))
        s, _ = self.run_dl({d: Resp(200, file_for(d))})
        self.assertNotIn(d, s.asked)

    def test_every_saved_file_holds_only_rows_dated_its_own_name(self):
        replies = {D(2026, 10, 1): Resp(200, file_for(D(2026, 10, 1))), D(2026, 10, 2): Resp(200, file_for(D(2026, 10, 1))),
                   D(2026, 10, 5): Resp(200, file_for(D(2026, 10, 5))), D(2026, 10, 6): Resp(200, csv_bytes([("AAA", D(2026, 10, 6)), ("BBB", D(2026, 10, 5))])),
                   D(2026, 10, 7): Resp(200, file_for(D(2026, 10, 7)))}
        self.run_dl(replies)
        self.assertEqual(self.saved(), ["bhav_20261001.csv", "bhav_20261005.csv", "bhav_20261007.csv"])
        for p in (self.tmp / "raw").glob("bhav_*.csv"):
            iso = dt.datetime.strptime(p.stem[5:], "%Y%m%d").date().isoformat()
            self.assertEqual(set(nd.response_dates(p.read_bytes())), {iso}, p.name)


class Helpers(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(nd.classify(["2026-10-02"] * 3, D(2026, 10, 2)), ("match", None))
        self.assertEqual(nd.classify(["2026-10-01"] * 3, D(2026, 10, 2)), ("stale_copy", "2026-10-01"))
        self.assertEqual(nd.classify(["2026-10-01", "2026-10-02"], D(2026, 10, 2))[0], "mismatch")
        self.assertEqual(nd.classify(["2026-10-01", "2026-09-30"], D(2026, 10, 2))[0], "mismatch")       # two different earlier days
        self.assertEqual(nd.classify(["2026-10-03"], D(2026, 10, 2))[0], "mismatch")
        self.assertEqual(nd.classify(["2026-10-01", None], D(2026, 10, 2))[0], "mismatch")
        self.assertEqual(nd.classify([], D(2026, 10, 2))[0], "mismatch")
        self.assertEqual(nd.classify(["2026-10-01"], "2026-10-02"), ("stale_copy", "2026-10-01"))

    def test_response_dates(self):
        self.assertEqual(nd.response_dates(file_for(D(2026, 10, 1))), ["2026-10-01"] * 3)
        self.assertEqual(nd.response_dates(b"\xff\xfe\x00bad"), [None])
        self.assertEqual(nd.response_dates(""), [None])

    def test_the_list_file_ignores_bad_lines_and_a_served_day_that_is_not_earlier(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "x.txt"
            p.write_text("2026-10-02 2026-10-01\njunk\n2026-10-03 2026-10-03\n2026-10-05 2026-10-01 extra\n2026-10-06\n2026-10-04 2026-10-09\n2026-13-01 2026-01-01\n\n")
            self.assertEqual(nd.read_stale(p), {"2026-10-02": "2026-10-01"})
        self.assertEqual(nd.read_stale(Path("/nonexistent/none.txt")), {})

    def test_the_helper_module_uses_only_the_standard_library(self):
        src = (Path(__file__).parent / "nse_dates.py").read_text()
        for name in ("pandas", "requests", "numpy"):
            self.assertNotIn("import " + name, src)


# ----------------------------------------------------------------------------- the market data side
class Gaps(TM.Tmp):
    NOW = TM.NOW

    def world(self):
        return TM.World()

    def build(self, w, root, skip=(), stale=(), listed=None):
        """Write the world without the `skip` day indexes; for each index in `stale` write a byte-for-byte copy of the PREVIOUS trading day's file under that day's name."""
        keep = set(range(w.n)) - set(skip) - set(stale)
        w.write(root, only=keep)
        raw = Path(root) / "data" / "raw"
        for i in stale:
            shutil.copy(raw / ("bhav_%s.csv" % w.dates[i - 1].strftime("%Y%m%d")), raw / ("bhav_%s.csv" % w.dates[i].strftime("%Y%m%d")))
        if listed:
            nd.write_stale(Path(root) / "data" / nd.STALE_FILE_NAME, listed)

    def go(self, root):
        code, msgs = mu.run(root, self.NOW, TM.SMALL)
        self.assertEqual(code, 0, msgs)
        return {n: json.loads((Path(root) / "out" / n).read_text()) for n in mu.FILES.values()}, msgs

    def other_root(self):
        r = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, r, ignore_errors=True)
        return r

    @staticmethod
    def figures(docs):
        return {k: {kk: vv for kk, vv in v.items() if kk != "data_quality"} for k, v in docs.items()}

    def test_market_figures_and_trading_day_counts_are_unchanged_by_stale_copies(self):
        w = self.world()
        holidays = [100, 180]
        a, b = self.other_root(), self.root
        self.build(w, a, skip=holidays)
        self.build(w, b, stale=holidays)
        da, _ = self.go(a)
        db, msgs = self.go(b)
        self.assertEqual(self.figures(da), self.figures(db))
        qa, qb = da["market_breadth.json"]["data_quality"], db["market_breadth.json"]["data_quality"]
        self.assertEqual(qa["trading_days_loaded"], qb["trading_days_loaded"])
        self.assertEqual((qa["files_used"], qb["files_used"]), (w.n - 2, w.n - 2))
        self.assertEqual(qb["files_seen"], qa["files_seen"] + 2)                        # the two copies are still seen ...
        self.assertEqual(len(qb["files_rejected"]), 2)                                  # ... and still rejected, not used
        self.assertEqual(db["market_breadth.json"]["as_of"], da["market_breadth.json"]["as_of"])

    def test_before_the_fix_the_two_holidays_were_unexplained_gaps_now_they_are_listed_as_holidays(self):
        w = self.world()
        self.build(w, self.root, stale=[100, 180])
        db, msgs = self.go(self.root)
        q = db["market_breadth.json"]["data_quality"]
        self.assertEqual(q["unexplained_weekday_gaps"], 0)
        self.assertEqual(q["stale_copy_days"], sorted(w.dates[i].isoformat() for i in (100, 180)))
        self.assertTrue(any("market holidays on which NSE answered with the previous trading day's file" in m for m in msgs))
        self.assertFalse(any("have no file and are not known holidays" in m for m in msgs))
        self.assertEqual(sum("rejected" in m for m in msgs), 2)

    def test_a_genuinely_missing_weekday_stays_visible_next_to_a_stale_copy_day(self):
        w = self.world()
        self.build(w, self.root, skip=[220], stale=[100])
        db, msgs = self.go(self.root)
        q = db["market_breadth.json"]["data_quality"]
        self.assertEqual(q["unexplained_weekday_gaps"], 1)
        self.assertEqual(q["stale_copy_days"], [w.dates[100].isoformat()])
        self.assertTrue(any("1 weekday(s) have no file and are not known holidays" in m for m in msgs))

    def test_without_any_stale_copy_the_gap_report_is_what_it_was(self):
        w = self.world()
        self.build(w, self.root, skip=[100, 180])
        db, msgs = self.go(self.root)
        q = db["market_breadth.json"]["data_quality"]
        self.assertEqual((q["unexplained_weekday_gaps"], q["stale_copy_days"]), (2, []))
        self.assertTrue(any("2 weekday(s) have no file" in m for m in msgs))
        self.assertFalse(any("market holidays on which NSE answered" in m for m in msgs))

    def test_the_recorded_list_explains_a_day_that_has_no_file_at_all(self):
        w = self.world()
        self.build(w, self.root, skip=[100], listed={w.dates[100].isoformat(): w.dates[99].isoformat()})
        q = self.go(self.root)[0]["market_breadth.json"]["data_quality"]
        self.assertEqual((q["unexplained_weekday_gaps"], q["stale_copy_days"]), (0, [w.dates[100].isoformat()]))

    def test_a_listed_day_that_has_a_good_file_is_never_called_a_holiday(self):
        w = self.world()
        self.build(w, self.root, listed={w.dates[100].isoformat(): w.dates[99].isoformat()})
        q = self.go(self.root)[0]["market_breadth.json"]["data_quality"]
        self.assertEqual(q["stale_copy_days"], [])
        self.assertEqual(q["trading_days_loaded"], w.n)

    def test_a_stale_copy_only_3_days_old_is_not_called_a_holiday(self):
        w = self.world()
        now = dt.datetime(2026, 10, 5, 22, 0, tzinfo=mu.IST)          # 2026-10-05 is the newest day; 2026-10-02 is exactly 3 days before it
        i = w.n - 2
        self.assertEqual(w.dates[i], D(2026, 10, 2))
        self.build(w, self.root, stale=[i])
        days, reports, counts = mu.load_days(self.root / "data" / "raw", now.date(), TM.SMALL)
        dq, _ = mu.assess(days, reports, counts, now, set(), TM.SMALL)
        self.assertEqual(dq["stale_copy_days"], [])
        self.assertEqual(dq["unexplained_weekday_gaps"], 1)            # visible: it may simply not have been published yet
        later = dt.datetime(2026, 10, 6, 22, 0, tzinfo=mu.IST)         # a day later it is 4 days old: now it counts as a holiday
        dq2, _ = mu.assess(days, reports, counts, later, set(), TM.SMALL)
        self.assertEqual(dq2["stale_copy_days"], ["2026-10-02"])
        self.assertEqual(dq2["unexplained_weekday_gaps"], 0)

    def test_a_stale_copy_of_the_newest_day_still_fails_the_run_as_before(self):
        w = self.world()
        now = dt.datetime(2026, 10, 5, 22, 0, tzinfo=mu.IST)
        self.build(w, self.root, stale=[w.n - 1])
        code, msgs = mu.run(self.root, now, TM.SMALL)
        self.assertEqual(code, 1)
        self.assertTrue(any("a newer file was rejected" in m for m in msgs))
        self.assertFalse((self.root / "out" / "market_breadth.json").exists())

    def test_a_stale_copy_is_never_counted_as_a_trading_day(self):
        w = self.world()
        self.build(w, self.root, stale=[100])
        days, reports, counts = mu.load_days(self.root / "data" / "raw", self.NOW.date(), TM.SMALL)
        self.assertNotIn(w.dates[100].isoformat(), days)
        rep = [r for r in reports if r["date"] == w.dates[100].isoformat()][0]
        self.assertEqual((rep["status"], rep["stale_copy_of"]), ("date_mismatch", w.dates[99].isoformat()))

    def test_a_mixed_dated_file_is_rejected_and_is_not_a_stale_copy(self):
        w = self.world()
        self.build(w, self.root, skip=[100])
        raw = self.root / "data" / "raw"
        name = lambda i: raw / ("bhav_%s.csv" % w.dates[i].strftime("%Y%m%d"))
        before, after = name(99).read_text().splitlines(), name(101).read_text().splitlines()
        name(100).write_text("\n".join(before[:20] + after[1:20]) + "\n")          # rows of two different days under one day's name
        days, reports, _ = mu.load_days(raw, self.NOW.date(), TM.SMALL)
        rep = [r for r in reports if r["date"] == w.dates[100].isoformat()][0]
        self.assertEqual(rep["status"], "date_mismatch")
        self.assertIsNone(rep["stale_copy_of"])
        q = self.go(self.root)[0]["market_breadth.json"]["data_quality"]
        self.assertEqual(q["unexplained_weekday_gaps"], 1)            # still visible as a gap
        self.assertEqual(q["stale_copy_days"], [])


if __name__ == "__main__":
    unittest.main()
