"""
NIFTY 50 freshness warning in historical_updater.py. OFFLINE and SYNTHETIC: invented prices, a fake HTTP session, no network, no token, no workflow.
The warning only reports that the newest stored benchmark candle is older than the newest expected weekday. It is not a fetch error, it adds nothing,
it retries nothing, and it never invents a price.
"""
import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import historical_updater as U

TOKEN = "dummy-not-a-real-token-value"
MON, FRI = dt.date(2026, 10, 12), dt.date(2026, 10, 9)
SAT, SUN = dt.date(2026, 10, 10), dt.date(2026, 10, 11)


def candle(d, base=100.0):
    o = base + d.toordinal() % 40
    return {"date": d.isoformat(), "open": o, "high": o + 2.0, "low": o - 1.0, "close": o + 1.0, "volume": 1000}


def raw(d, base=100.0, **over):
    c = dict(candle(d, base), **over)
    return [d.isoformat() + "T00:00:00+05:30", c["open"], c["high"], c["low"], c["close"], c["volume"], 0]


def weekdays(frm, to):
    d = frm
    while d <= to:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


class Resp:
    def __init__(self, status, rows=None):
        self.status_code = status
        self._rows = rows

    def json(self):
        return {"status": "success", "data": {"candles": self._rows}}


class Session:
    """What requests.Session would be: answers each URL from `answers` (key fragment -> status, raw rows) and counts the requests."""
    log = []
    answers = {}

    def __init__(self):
        self.headers = {}

    def get(self, url, timeout=0):
        Session.log.append(url)
        for frag, (status, rows) in Session.answers.items():
            if frag in url:
                return Resp(status, rows)
        return Resp(200, [])


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.t = Path(self._t.name)
        (self.t / "out").mkdir()
        self.summary = self.t / "summary.md"
        Session.log, Session.answers = [], {}
        eq = {"TCS": {"isin": "INE000000000", "name": "TCS", "instrument_key": "NSE_EQ|TCS"}}
        self.end = MON
        self.patches = [mock.patch.object(U, "OUT_FILE", self.t / "out" / "historical.json"),
                        mock.patch.object(U.requests, "Session", Session),
                        mock.patch.object(U, "load_instrument_file", return_value=(eq, [{"instrument_key": "NSE_INDEX|Nifty 50", "name": "Nifty 50"}])),
                        mock.patch.object(U, "last_complete_day", side_effect=lambda: self.end),      # the clock is mocked: no test depends on today's date
                        mock.patch.object(U.time, "sleep"),
                        mock.patch.object(U, "MAX_CALLS_PER_RUN", 150),
                        mock.patch.dict(os.environ, {"UPSTOX_ANALYTICS_TOKEN": TOKEN, "HISTORICAL_SYMBOLS": "TCS", "GITHUB_STEP_SUMMARY": str(self.summary)})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def seed(self, bench_last, tcs_last=None):
        """A saved single file: the benchmark and TCS, weekday candles up to the given dates."""
        frm = bench_last - dt.timedelta(days=60)
        self.bench_old = [candle(d, 500.0) for d in weekdays(frm, bench_last)]
        tcs_last = tcs_last or bench_last
        self.tcs_old = [candle(d, 100.0) for d in weekdays(frm, tcs_last)]
        doc = {"updated": "2026-10-09", "source": "Upstox", "interval": "1day", "notes": {},
               "benchmark": {"symbol": "NIFTY 50", "instrument_key": "NSE_INDEX|Nifty 50", "candles": self.bench_old},
               "stocks": {"TCS": {"symbol": "TCS", "isin": "INE000000000", "instrument_key": "NSE_EQ|TCS", "candles": self.tcs_old}}, "errors": []}
        (self.t / "out" / "historical.json").write_text(json.dumps(doc), encoding="utf-8")

    def answer(self, bench_rows, status=200):
        Session.answers = {"Nifty%2050": (status, bench_rows), "NSE_EQ%7CTCS": (200, [raw(d) for d in weekdays(self.end - dt.timedelta(days=15), self.end)])}

    def run_updater(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                U.main()
                code = 0
            except SystemExit as e:
                code = e.code
        doc = json.loads((self.t / "out" / "historical.json").read_text(encoding="utf-8"))
        summary = self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""
        return code, out.getvalue(), doc, summary

    def bench_requests(self):
        return [u for u in Session.log if "Nifty%2050" in u]


class ExpectedDay(unittest.TestCase):
    def test_a_weekday_is_its_own_expected_day_and_a_weekend_falls_back_to_friday(self):
        for d in weekdays(dt.date(2026, 10, 5), dt.date(2026, 10, 9)):
            self.assertEqual(U.expected_latest_day(d), d)
        self.assertEqual(U.expected_latest_day(SAT), FRI)
        self.assertEqual(U.expected_latest_day(SUN), FRI)
        self.assertEqual(U.expected_latest_day(MON), MON)

    def test_freshness_warning_is_pure_and_names_both_dates(self):
        self.assertIsNone(U.freshness_warning([candle(MON)], MON))
        self.assertIsNone(U.freshness_warning([candle(FRI)], SAT))
        self.assertIsNone(U.freshness_warning([candle(FRI)], SUN))
        msg = U.freshness_warning([candle(FRI)], MON)
        self.assertIn("2026-10-09", msg)
        self.assertIn("2026-10-12", msg)
        self.assertIn("no price was invented", msg)
        self.assertIn("none", U.freshness_warning([], MON))                       # nothing stored at all: also reported, in words
        self.assertIsNone(U.freshness_warning([candle(MON + dt.timedelta(days=1))], MON))      # never "stale" for a newer candle


class Run(Base):
    def test_current_data_gives_no_freshness_warning_and_the_same_output_as_before(self):
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), MON)])
        code, text, doc, summary = self.run_updater()
        self.assertEqual(code, 0)
        self.assertEqual(doc["benchmark"]["candles"][-1]["date"], "2026-10-12")
        self.assertNotIn("freshness", text)
        self.assertNotIn("freshness", summary)
        self.assertIn("### Historical data: OK", summary)
        self.assertEqual(doc["errors"], [])

    def test_a_stale_benchmark_warns_in_the_log_and_the_summary_with_both_dates(self):
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)])        # HTTP 200, but no row for Monday
        code, text, doc, summary = self.run_updater()
        self.assertEqual(code, 0)
        line = [x for x in text.splitlines() if x.startswith("::warning::NIFTY 50 freshness")]
        self.assertEqual(len(line), 1)
        self.assertIn("2026-10-09", line[0])
        self.assertIn("2026-10-12", line[0])
        self.assertIn("- NIFTY 50 freshness", summary)
        self.assertIn("2026-10-09", summary)
        self.assertIn("2026-10-12", summary)

    def test_the_warning_is_not_a_fetch_error_and_changes_no_status_label_or_exit_code(self):
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)])
        code, text, doc, summary = self.run_updater()
        self.assertEqual(code, 0)
        self.assertEqual(doc["errors"], [])                                       # the existing error structure is untouched
        self.assertIn("### Historical data: OK", summary)
        self.assertNotIn("PARTIAL", summary)
        self.assertNotIn("problem(s)", summary)
        self.assertIn("problems: 0", text)

    def test_stale_data_keeps_every_candle_and_invents_none(self):
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)])
        code, text, doc, summary = self.run_updater()
        got = doc["benchmark"]["candles"]
        self.assertEqual(got, self.bench_old)                                      # identical: nothing added, changed or removed
        self.assertNotIn("2026-10-12", [c["date"] for c in got])
        tcs = doc["stocks"]["TCS"]["candles"]
        self.assertEqual([c for c in tcs if c["date"] <= "2026-10-09"], self.tcs_old)

    def test_there_is_no_retry_one_benchmark_request_even_when_stale(self):
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)])
        self.run_updater()
        self.assertEqual(len(self.bench_requests()), 1)
        self.assertEqual(U.time.sleep.call_args_list, [mock.call(U.DELAY_SECONDS)] * len(Session.log))      # only the existing 1 s pause after each call

    def test_weekend_references_do_not_flag_a_friday_candle(self):
        for end in (SAT, SUN):
            with self.subTest(end=end.isoformat()):
                self.end = end
                self.seed(FRI)
                self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)])
                code, text, doc, summary = self.run_updater()
                self.assertEqual(code, 0)
                self.assertNotIn("freshness", text)
                self.assertNotIn("freshness", summary)

    def test_a_monday_reference_flags_a_friday_candle(self):
        self.end = MON
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)])
        self.assertIn("freshness", self.run_updater()[1])

    def test_invalid_candles_are_still_dropped_and_the_benchmark_is_still_reported_stale(self):
        self.seed(FRI)
        rows = [raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)]
        rows.append(raw(MON, 500.0, open=0.0))                                      # a zero price: the existing rule drops it
        rows.append(raw(MON, 500.0, high=1.0, low=9.0))                             # high below low: dropped too
        self.answer(rows)
        code, text, doc, summary = self.run_updater()
        self.assertEqual(code, 0)
        self.assertNotIn("2026-10-12", [c["date"] for c in doc["benchmark"]["candles"]])
        self.assertIn("::warning::NIFTY 50 freshness", text)
        self.assertEqual(doc["benchmark"]["candles"], self.bench_old)

    def test_a_valid_late_row_is_kept_and_clears_the_warning(self):
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), MON)])
        code, text, doc, summary = self.run_updater()
        self.assertEqual(doc["benchmark"]["candles"][-1]["date"], "2026-10-12")
        self.assertNotIn("freshness", text)

    def test_a_failed_benchmark_request_is_still_an_error_and_the_old_series_is_kept_with_the_warning_separate(self):
        self.seed(FRI)
        self.answer(None, status=500)
        code, text, doc, summary = self.run_updater()
        self.assertEqual(code, 0)                                                   # TCS still saved: the exit-code rule is unchanged
        self.assertEqual(len(doc["errors"]), 1)
        self.assertEqual(doc["errors"][0]["symbol"], "NIFTY 50")
        self.assertIn("HTTP 500", doc["errors"][0]["error"])
        self.assertEqual(set(doc["errors"][0]), {"symbol", "request", "error"})
        self.assertIn("PARTIAL - 1 problem(s)", summary)                            # one problem: the fetch error, not the freshness warning
        self.assertEqual(doc["benchmark"]["candles"], self.bench_old)
        self.assertIn("freshness", text)                                            # and the stale series is still reported, separately

    def test_no_token_or_header_reaches_the_log_or_the_summary(self):
        self.seed(FRI)
        self.answer([raw(d, 500.0) for d in weekdays(FRI - dt.timedelta(days=15), FRI)])
        code, text, doc, summary = self.run_updater()
        for bad in (TOKEN, "Bearer", "Authorization"):
            self.assertNotIn(bad, text)
            self.assertNotIn(bad, summary)


class Scope(unittest.TestCase):
    def test_no_retry_wait_or_new_dependency_was_added(self):
        src = (Path(__file__).parent / "historical_updater.py").read_text(encoding="utf-8")
        body = src[src.index("def expected_latest_day"):src.index("def merge(")]
        self.assertNotIn("sleep", body)
        self.assertNotIn("RETRIES", body)
        self.assertEqual(src.count("RETRIES = 3"), 1)                               # the existing network-error retry is unchanged


if __name__ == "__main__":
    unittest.main()
