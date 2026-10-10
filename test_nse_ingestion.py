"""Tests for the raw NSE file ingestion in nse_updater.py (misdated files and repeated (SYMBOL, DATE1) rows).
Run from the repository root:  python3 -m unittest test_nse_ingestion -v"""
import contextlib
import datetime as dt
import hashlib
import inspect
import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import nse_updater as nu

HEAD = "SYMBOL, SERIES, DATE1, PREV_CLOSE, HIGH_PRICE, CLOSE_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, DELIV_PER"


def line(sym, day, close, qty=1000, series="EQ", turnover=500, prev=None):
    return "%s, %s, %s, %s, %s, %s, %s, %s, 50.0" % (sym, series, day.strftime("%d-%b-%Y"), close if prev is None else prev, close + 1, close, qty, turnover)


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)
        self.old_raw = nu.RAW
        nu.RAW = self.raw
        self.addCleanup(self.restore)

    def restore(self):
        nu.RAW = self.old_raw
        self.tmp.cleanup()

    def put(self, filename_day, lines, name=None):
        (self.raw / (name or "bhav_%s.csv" % filename_day.strftime("%Y%m%d"))).write_text("\n".join([HEAD] + lines) + "\n")

    def put_day(self, day, base=100.0, syms=("AAA", "BBB", "CCC"), extra=()):
        self.put(day, [line(s, day, base + i) for i, s in enumerate(syms)] + list(extra))

    def load(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            df = nu.load_prices()
        return df, buf.getvalue()


class AcceptedAndRejected(Base):
    def test_matching_file_is_accepted_and_values_are_intact(self):
        d = dt.date(2026, 3, 4)
        self.put(d, [line("AAA", d, 101.5, qty=12345, turnover=777, prev=100.25), line("BBB", d, 55.0)])
        df, _ = self.load()
        self.assertEqual(len(df), 2)
        r = df[df.SYMBOL == "AAA"].iloc[0]
        self.assertEqual((r.CLOSE_PRICE, r.PREV_CLOSE, r.HIGH_PRICE, r.TTL_TRD_QNTY, r.TURNOVER_LACS), (101.5, 100.25, 102.5, 12345, 777))
        self.assertEqual(r.DATE, pd.Timestamp(d))
        self.assertEqual(nu.LAST_LOAD_REPORT["files_rejected"], [])

    def test_misdated_file_is_rejected_and_not_a_trading_day(self):
        real = dt.date(2026, 1, 14)
        self.put_day(real)
        self.put(dt.date(2026, 1, 15), [line(s, real, 100.0 + i) for i, s in enumerate(("AAA", "BBB", "CCC"))])   # a holiday served the 14th again
        df, log = self.load()
        self.assertEqual(sorted(df.DATE.dt.date.unique()), [real])
        self.assertEqual(len(df), 3)
        rej = nu.LAST_LOAD_REPORT["files_rejected"]
        self.assertEqual([r["file"] for r in rej], ["bhav_20260115.csv"])
        self.assertIn("not dated 2026-01-15", rej[0]["reason"])
        self.assertIn("14-Jan-2026", rej[0]["reason"])
        self.assertIn("REJECTED raw file bhav_20260115.csv", log)
        self.assertEqual((nu.LAST_LOAD_REPORT["files_found"], nu.LAST_LOAD_REPORT["files_used"]), (2, 1))

    def test_one_stray_row_rejects_the_whole_file(self):
        d, other = dt.date(2026, 2, 10), dt.date(2026, 2, 9)
        self.put(d, [line("AAA", d, 100.0), line("BBB", d, 101.0), line("CCC", other, 102.0)])
        self.put_day(dt.date(2026, 2, 11))
        df, _ = self.load()
        self.assertEqual(sorted(df.DATE.dt.date.unique()), [dt.date(2026, 2, 11)])
        self.assertEqual(nu.LAST_LOAD_REPORT["files_rejected"][0]["file"], "bhav_20260210.csv")

    def test_unreadable_date_rejects_the_file(self):
        d = dt.date(2026, 2, 10)
        self.put(d, [line("AAA", d, 100.0).replace(d.strftime("%d-%b-%Y"), "garbage")])
        self.put_day(dt.date(2026, 2, 11))
        df, _ = self.load()
        self.assertEqual(len(df), 3)
        self.assertEqual(len(nu.LAST_LOAD_REPORT["files_rejected"]), 1)

    def test_non_matching_file_name_is_rejected_not_guessed(self):
        self.put_day(dt.date(2026, 2, 11))
        self.put(dt.date(2026, 2, 12), [line("AAA", dt.date(2026, 2, 12), 1.0)], name="bhav_latest.csv")
        df, _ = self.load()
        self.assertEqual(len(df), 3)
        self.assertEqual(nu.LAST_LOAD_REPORT["files_rejected"][0]["file"], "bhav_latest.csv")

    def test_all_files_rejected_stops_the_run(self):
        self.put(dt.date(2026, 1, 15), [line("AAA", dt.date(2026, 1, 14), 1.0)])
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            nu.load_prices()
        self.assertIn("DATA QUALITY GATE FAILED", str(cm.exception))

    def test_no_files_still_stops_as_before(self):
        with self.assertRaises(SystemExit) as cm:
            nu.load_prices()
        self.assertIn("No data files yet", str(cm.exception))


class Duplicates(Base):
    def test_identical_repeated_rows_are_removed_before_any_rolling(self):
        d = dt.date(2026, 3, 4)
        self.put(d, [line("AAA", d, 100.0), line("AAA", d, 100.0), line("BBB", d, 50.0)])
        df, _ = self.load()
        self.assertEqual(len(df), 2)
        self.assertFalse(df.duplicated(["SYMBOL", "DATE"]).any())
        self.assertEqual(nu.LAST_LOAD_REPORT["identical_repeat_rows_removed"], 1)

    def test_conflicting_repeated_rows_fail_the_gate(self):
        d = dt.date(2026, 3, 4)
        self.put(d, [line("AAA", d, 100.0), line("AAA", d, 100.5), line("BBB", d, 50.0)])
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            nu.load_prices()
        msg = str(cm.exception)
        self.assertIn("DATA QUALITY GATE FAILED", msg)
        self.assertIn("DIFFERENT values", msg)
        self.assertIn("AAA", msg)

    def test_conflict_differing_only_in_volume_is_also_a_conflict(self):
        d = dt.date(2026, 3, 4)
        self.put(d, [line("AAA", d, 100.0, qty=1), line("AAA", d, 100.0, qty=2)])
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            nu.load_prices()

    def test_a_misdated_copy_cannot_reach_the_rolling_windows(self):
        days = weekdays(dt.date(2025, 9, 1), 70)
        for i, d in enumerate(days):
            self.put(d, [line("AAA", d, 100.0 + i, qty=1000 + i), line("BBB", d, 200.0 + 2 * i, qty=500 + i)])
        clean, _ = self.load()
        clean = nu.add_indicators(clean)
        # three "holiday" files (weekends are not asked for, so use weekday dates that are not in the series) each carry a copy of an earlier day
        for k, holiday in enumerate(weekdays(days[-1] + dt.timedelta(days=1), 3)):
            src = days[10 + 20 * k]
            self.put(holiday, [line("AAA", src, 100.0 + 10 + 20 * k, qty=1000 + 10 + 20 * k), line("BBB", src, 200.0 + 2 * (10 + 20 * k), qty=500 + 10 + 20 * k)])
        dirty, _ = self.load()
        dirty = nu.add_indicators(dirty)
        self.assertEqual(len(nu.LAST_LOAD_REPORT["files_rejected"]), 3)
        self.assertFalse(dirty.duplicated(["SYMBOL", "DATE"]).any())
        for col in ("dma50", "vol20", "ret63", "high252", "CLOSE_PRICE"):
            pd.testing.assert_series_equal(clean[col], dirty[col], check_names=False)
        last = dirty[dirty.SYMBOL == "AAA"].iloc[-1]
        self.assertAlmostEqual(last.ret63, ((100.0 + 69) / (100.0 + 69 - 63) - 1) * 100, places=9)   # exactly 63 real trading days back
        self.assertAlmostEqual(last.dma50, sum(100.0 + i for i in range(20, 70)) / 50, places=9)

    def test_without_the_guard_the_same_copy_would_have_distorted_ret63(self):
        """Proves the test above is meaningful: stack the copied day in by hand (what the old code did) and the 63-row return moves."""
        days = weekdays(dt.date(2025, 9, 1), 70)
        rows = [dict(SYMBOL="AAA", DATE=pd.Timestamp(d), CLOSE_PRICE=100.0 + i, HIGH_PRICE=101.0 + i, TTL_TRD_QNTY=1000.0, DELIV_PER=50.0) for i, d in enumerate(days)]
        base = pd.DataFrame(rows)
        dup = pd.concat([base, base.iloc[[30]]]).sort_values(["SYMBOL", "DATE"], kind="stable").reset_index(drop=True)
        self.assertNotAlmostEqual(nu.add_indicators(base.copy()).ret63.iloc[-1], nu.add_indicators(dup).ret63.iloc[-1])


class KeptAsBefore(Base):
    def test_multiple_valid_trading_days_are_all_preserved(self):
        days = weekdays(dt.date(2026, 4, 1), 8)
        for i, d in enumerate(days):
            self.put_day(d, base=100 + i)
        df, _ = self.load()
        self.assertEqual(sorted(df.DATE.dt.date.unique()), days)
        self.assertEqual(len(df), 8 * 3)
        self.assertEqual(df.groupby("SYMBOL").size().to_dict(), {"AAA": 8, "BBB": 8, "CCC": 8})
        self.assertEqual(nu.LAST_LOAD_REPORT["files_rejected"], [])

    def test_non_eq_rows_are_still_excluded(self):
        d = dt.date(2026, 4, 1)
        self.put(d, [line("AAA", d, 100.0), line("AAA", d, 90.0, series="BE"), line("ETF1", d, 10.0, series="N1"), line("BBB", d, 50.0, series=" EQ ")])
        df, _ = self.load()
        self.assertEqual(sorted(df.SYMBOL), ["AAA", "BBB"])
        self.assertEqual(df[df.SYMBOL == "AAA"].CLOSE_PRICE.iloc[0], 100.0)

    def test_eq_and_be_of_the_same_symbol_are_not_a_duplicate(self):
        d = dt.date(2026, 4, 1)
        self.put(d, [line("AAA", d, 100.0), line("AAA", d, 90.0, series="BE")])
        df, _ = self.load()
        self.assertEqual(len(df), 1)

    def test_scan_definitions_are_unchanged(self):
        self.assertEqual(nu.MIN_TURNOVER_LACS, 100)
        src = {n: inspect.getsource(getattr(nu, n)) for n in ("add_indicators", "dma_status", "build_scans")}
        digest = {n: hashlib.sha256(s.encode()).hexdigest()[:16] for n, s in src.items()}
        self.assertEqual(digest, PINNED)
        for frag in ("rolling(50)", "rolling(200)", "shift(1).rolling(20)", "rolling(252, min_periods=150)", "pct_change(63)"):
            self.assertIn(frag, src["add_indicators"])


PINNED = {"add_indicators": "389126209167ed21", "dma_status": "ca887ace25309867", "build_scans": "eac60c8072c02a47"}   # hashes of the functions as they were before this fix

if __name__ == "__main__":
    unittest.main()
