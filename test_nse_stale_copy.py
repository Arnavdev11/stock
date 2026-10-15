"""Tests for the NSE stale-copy handling: nse_dates.py, nse_updater.download_missing and the gap reporting in market_data_updater.py.
No network: the NSE session is a stub. Run from the repository root:  python3 -m unittest test_nse_stale_copy -v

Rules under test
  * A response is saved under the requested day ONLY if every row inside is dated that day. A stale copy (all rows one earlier day) or any other mismatch is never saved,
    never relabelled and never used.
  * A stale copy is an unconfirmed OBSERVATION of what NSE sent, not a holiday: it is re-checked after 7 days, left alone after 3 identical answers, dropped when the real file
    appears, validated when read back, and in the market-data report it never removes a weekday from the unexplained gaps and is never called confirmed.
  * A file in another (decodable) encoding is not discarded for that reason alone; undecodable bytes fail closed.
  * The market figures and the trading-day count do not change."""
import contextlib
import datetime as dt
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import market_data_updater as mu
import nse_dates as nd
import nse_updater as nu
import test_market_data as TM
from test_nse_ingestion import HEAD, line

TODAY = dt.date(2026, 10, 14)          # a Wednesday
D = dt.date


def csv_text(day_rows):
    return "\n".join([HEAD] + [line(s, d, 100.0 + i) for i, (s, d) in enumerate(day_rows)]) + "\n"


def csv_bytes(day_rows):
    return csv_text(day_rows).encode()


def file_for(day, syms=("AAA", "BBB", "CCC")):
    return csv_bytes([(s, day) for s in syms])


def obs_line(req, srv, first, last, checks):
    return "%s %s %s %s %d" % (req, srv, first, last, checks)


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


# ----------------------------------------------------------------------------- A. encoding
class Decoding(unittest.TestCase):
    D1 = D(2026, 10, 1)

    def test_utf8_and_utf8_with_bom(self):
        t = csv_text([("AAA", self.D1), ("BBB", self.D1)])
        self.assertEqual(nd.decode_text(t.encode())[1], "utf-8")
        self.assertEqual(nd.decode_text(b"\xef\xbb\xbf" + t.encode()), (t, "utf-8-sig"))
        self.assertEqual(nd.response_dates(b"\xef\xbb\xbf" + t.encode()), ["2026-10-01"] * 2)       # the BOM must not hide the first column name

    def test_utf16_and_utf32_with_a_bom(self):
        t = csv_text([("AAA", self.D1)])
        for codec in ("utf-16", "utf-16-le", "utf-16-be", "utf-32"):
            raw = t.encode(codec)
            if codec in ("utf-16-le", "utf-16-be"):
                raw = (b"\xff\xfe" if codec.endswith("le") else b"\xfe\xff") + raw
            self.assertEqual(nd.response_dates(raw), ["2026-10-01"], codec)

    def test_windows_1252_is_decoded_and_its_dates_are_read(self):
        t = csv_text([("AAA", self.D1)]).replace("AAA", "A’A")                # a typographic apostrophe: byte 0x92 in cp1252, invalid UTF-8
        raw = t.encode("cp1252")
        self.assertRaises(UnicodeDecodeError, raw.decode, "utf-8")
        self.assertEqual(nd.decode_text(raw)[1], "cp1252")
        self.assertEqual(nd.response_dates(raw), ["2026-10-01"])

    def test_bytes_no_supported_encoding_can_decode_fail_closed(self):
        for bad in (csv_text([("AAA", self.D1)]).encode() + b"\x81", b"\x81\x8d\x8f\x90\x9d", b"SYMBOL,DATE1\nA,\x81\n"):
            self.assertEqual(nd.decode_text(bad), (None, None), bad[:20])
            self.assertEqual(nd.response_dates(bad), [None], bad[:20])
            self.assertEqual(nd.classify(nd.response_dates(bad), self.D1)[0], "mismatch")

    def test_nul_bytes_binary_and_truncated_utf16_fail_closed(self):
        self.assertEqual(nd.response_dates(b"SYMBOL,DATE1\x00\nA,01-Oct-2026\n"), [None])
        self.assertEqual(nd.response_dates(bytes(range(256)) * 4), [None])
        self.assertEqual(nd.response_dates(csv_text([("AAA", self.D1)]).encode("utf-16")[:-1]), [None])        # an odd number of bytes
        self.assertEqual(nd.response_dates("SYMBOL, SERIES, DATE1\nA\x00A, EQ, 01-Oct-2026\n"), [None])      # a NUL inside a data field: not text we trust
        self.assertEqual(nd.response_dates(b"SYMBOL, SERIES, DATE1\nA\x00A, EQ, 01-Oct-2026\n"), [None])
        self.assertEqual(nd.response_dates(""), [None])
        self.assertEqual(nd.response_dates(b""), [None])

    def test_invalid_date_values_are_unreadable_not_guessed(self):
        head = "SYMBOL, SERIES, DATE1\n"
        for bad in ("32-Oct-2026", "2026-10-01", "01/10/2026", "", "Oct-01-2026", "01-Foo-2026", "00-Oct-2026"):
            self.assertEqual(nd.response_dates(head + "AAA, EQ, %s\n" % bad), [None], bad)
        self.assertEqual(nd.response_dates(head + "AAA, EQ, 01-Oct-2026\nBBB, EQ, 31-Feb-2026\n"), ["2026-10-01", None])
        self.assertEqual(nd.classify(["2026-10-01", None], D(2026, 10, 1))[0], "mismatch")
        self.assertEqual(nd.response_dates("SYMBOL, SERIES\nAAA, EQ\n"), [None])                  # no DATE1 column
        self.assertEqual(nd.response_dates(head + "AAA, EQ\n"), [None])                           # a row too short to have DATE1
        self.assertEqual(nd.response_dates(head), [])                                             # header only

    def test_the_two_existing_readers_accept_utf8_with_and_without_bom_and_nothing_else(self):
        """The reason a file in another encoding is re-encoded when saved: this is what the readers can use."""
        base = csv_text([("AAA", self.D1)])
        with tempfile.TemporaryDirectory() as t:
            ok = Path(t) / "ok.csv"
            for raw in (base.encode(), b"\xef\xbb\xbf" + base.encode()):
                ok.write_bytes(raw)
                self.assertEqual(list(pd.read_csv(ok, skipinitialspace=True).columns)[0], "SYMBOL")
                self.assertEqual(mu.parse_bhav_file(ok, "2026-10-01")[1]["status"], "ok")
            for raw in (base.replace("AAA", "A’A").encode("cp1252"), base.encode("utf-16")):
                ok.write_bytes(raw)
                self.assertRaises(UnicodeDecodeError, pd.read_csv, ok, skipinitialspace=True)


# ----------------------------------------------------------------------------- classify and the persisted observations
class Classify(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(nd.classify(["2026-10-02"] * 3, D(2026, 10, 2)), ("match", None))
        self.assertEqual(nd.classify(["2026-10-01"] * 3, D(2026, 10, 2)), ("stale_copy", "2026-10-01"))
        self.assertEqual(nd.classify(["2026-10-01", "2026-10-02"], D(2026, 10, 2))[0], "mismatch")
        self.assertEqual(nd.classify(["2026-10-01", "2026-09-30"], D(2026, 10, 2))[0], "mismatch")       # two different earlier days
        self.assertEqual(nd.classify(["2026-10-03"], D(2026, 10, 2))[0], "mismatch")
        self.assertEqual(nd.classify([], D(2026, 10, 2))[0], "mismatch")
        self.assertEqual(nd.classify(["2026-10-01"], "2026-10-02"), ("stale_copy", "2026-10-01"))

    def test_the_three_day_boundary(self):
        self.assertFalse(nd.old_enough("2026-10-11", D(2026, 10, 14)))     # 3 days: may not be published yet
        self.assertTrue(nd.old_enough("2026-10-10", D(2026, 10, 14)))      # 4 days

    def test_the_helper_module_uses_only_the_standard_library(self):
        src = (Path(__file__).parent / "nse_dates.py").read_text()
        for name in ("pandas", "requests", "numpy"):
            self.assertNotIn("import " + name, src)


class ObservationFile(unittest.TestCase):
    GOOD = obs_line("2026-10-02", "2026-10-01", "2026-10-09", "2026-10-09", 1)

    def read(self, *lines, today=D(2026, 10, 14)):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "x.txt"
            p.write_text("\n".join(lines) + "\n")
            return nd.read_observations(p, today)

    def reasons(self, *lines, **kw):
        recs, problems = self.read(*lines, **kw)
        self.assertEqual(recs, {}, lines)
        self.assertEqual(len(problems), len(lines), lines)
        return [w for _, w in problems]

    def test_a_valid_line_is_an_observation_with_all_its_fields(self):
        recs, problems = self.read("# a comment", "", self.GOOD)
        self.assertEqual(problems, [])
        self.assertEqual(recs, {"2026-10-02": {"requested": "2026-10-02", "served": "2026-10-01", "first_seen": "2026-10-09", "last_checked": "2026-10-09", "checks": 1}})

    def test_every_kind_of_bad_record_is_rejected_with_a_reason_and_none_is_used(self):
        cases = {
            "expected 5 fields": ["2026-10-02 2026-10-01", self.GOOD + " extra", "junk"],
            "not four YYYY-MM-DD dates and an integer": ["20261002 2026-10-01 2026-10-09 2026-10-09 1", "2026-13-01 2026-10-01 2026-10-09 2026-10-09 1",
                                                        "2026-10-02 2026-10-01 2026-10-09 2026-10-09 x"],
            "checks must be 1 to 99": [obs_line("2026-10-02", "2026-10-01", "2026-10-09", "2026-10-09", 0), obs_line("2026-10-02", "2026-10-01", "2026-10-09", "2026-10-09", 100)],
            "the requested day is a weekend": [obs_line("2026-10-03", "2026-10-02", "2026-10-09", "2026-10-09", 1)],
            "the served day is a weekend": [obs_line("2026-10-05", "2026-10-04", "2026-10-09", "2026-10-09", 1)],
            "the served day is not earlier than the requested day": [obs_line("2026-10-02", "2026-10-02", "2026-10-09", "2026-10-09", 1), obs_line("2026-10-02", "2026-10-05", "2026-10-09", "2026-10-09", 1)],
            "the served day is more than 10 days before the requested day": [obs_line("2026-10-02", "2026-09-18", "2026-10-09", "2026-10-09", 1)],
            "first_seen is after last_checked": [obs_line("2026-10-02", "2026-10-01", "2026-10-10", "2026-10-09", 1)],
            "first seen too soon after the requested day": [obs_line("2026-10-02", "2026-10-01", "2026-10-05", "2026-10-09", 1)],
            "last_checked is in the future": [obs_line("2026-10-02", "2026-10-01", "2026-10-09", "2026-10-20", 1)],
        }
        for why, lines in cases.items():
            for ln in lines:
                got = self.reasons(ln)
                self.assertTrue(got[0].startswith(why[:30]), (why, got))

    def test_a_requested_day_listed_twice_keeps_the_first_and_reports_the_second(self):
        recs, problems = self.read(self.GOOD, obs_line("2026-10-02", "2026-10-01", "2026-10-09", "2026-10-10", 2))
        self.assertEqual(recs["2026-10-02"]["checks"], 1)
        self.assertEqual([w for _, w in problems], ["the requested day is listed twice"])

    def test_one_bad_line_does_not_discard_the_good_ones(self):
        recs, problems = self.read("garbage", self.GOOD)
        self.assertEqual(list(recs), ["2026-10-02"])
        self.assertEqual([n for n, _ in problems], [1])

    def test_missing_or_unreadable_file_is_empty_not_an_error(self):
        self.assertEqual(nd.read_observations(Path("/nonexistent/none.txt")), ({}, []))
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "bin.txt"
            p.write_bytes(b"\xff\xfe\x00bad")
            recs, problems = nd.read_observations(p)
            self.assertEqual(recs, {})
            self.assertEqual(len(problems), 1)                                          # reported, not silently empty

    def test_write_then_read_round_trips(self):
        recs = {"2026-10-02": {"requested": "2026-10-02", "served": "2026-10-01", "first_seen": "2026-10-09", "last_checked": "2026-10-13", "checks": 2}}
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "d" / "x.txt"
            nd.write_observations(p, recs)
            self.assertEqual(nd.read_observations(p, TODAY), (recs, []))
            self.assertIn("not a holiday list", p.read_text())

    def test_due_settled_and_restart(self):
        rec = {"requested": "2026-10-02", "served": "2026-10-01", "first_seen": "2026-10-09", "last_checked": "2026-10-09", "checks": 1}
        self.assertFalse(nd.due_for_recheck(rec, D(2026, 10, 15)))          # 6 days after the last check
        self.assertTrue(nd.due_for_recheck(rec, D(2026, 10, 16)))           # 7 days
        self.assertFalse(nd.due_for_recheck(dict(rec, checks=3), D(2027, 1, 1)))      # seen 3 times: left alone however old
        self.assertEqual((nd.state_of(rec), nd.state_of(dict(rec, checks=3))), ("observed", "settled"))
        r = dict(rec)
        recs = {"2026-10-02": r}
        again = nd.observe(recs, "2026-10-02", "2026-10-01", D(2026, 10, 16))
        self.assertEqual((again["checks"], again["first_seen"], again["last_checked"]), (2, "2026-10-09", "2026-10-16"))
        diff = nd.observe(recs, "2026-10-02", "2026-09-30", D(2026, 10, 23))
        self.assertEqual((diff["checks"], diff["served"], diff["first_seen"]), (1, "2026-09-30", "2026-10-23"))        # NSE answered differently: start again


# ----------------------------------------------------------------------------- the downloader
class Dl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.patches = [mock.patch.object(nu, "RAW", self.tmp / "raw"), mock.patch.object(nu, "HOLIDAYS", self.tmp / "holidays.txt"),
                        mock.patch.object(nu, "STALE_COPIES", self.tmp / nd.STALE_FILE_NAME), mock.patch.object(nu, "DAYS_BACK", 40),
                        mock.patch.object(nu.time, "sleep")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.ledger = self.tmp / nd.STALE_FILE_NAME

    def run_dl(self, replies, today=TODAY):
        s = Session(replies)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nu.download_missing(s, today=today)
        return s, buf.getvalue()

    def saved(self):
        return sorted(p.name for p in (self.tmp / "raw").glob("bhav_*.csv"))

    def records(self):
        return nd.read_observations(self.ledger)[0]


class DownloadDecision(Dl):
    def test_a_matching_file_is_saved_byte_for_byte_and_nothing_is_recorded(self):
        d = D(2026, 10, 13)
        body = file_for(d)
        self.run_dl({d: Resp(200, body)})
        self.assertEqual((self.tmp / "raw" / "bhav_20261013.csv").read_bytes(), body)
        self.assertFalse(self.ledger.exists())

    def test_a_bom_file_is_saved_byte_for_byte_and_the_readers_can_load_it(self):
        d = D(2026, 10, 13)
        body = b"\xef\xbb\xbf" + file_for(d)
        self.run_dl({d: Resp(200, body)})
        f = self.tmp / "raw" / "bhav_20261013.csv"
        self.assertEqual(f.read_bytes(), body)
        df, why = nu.read_raw_file(f)
        self.assertIsNone(why)
        self.assertEqual(list(df.SYMBOL), ["AAA", "BBB", "CCC"])
        self.assertEqual(mu.parse_bhav_file(f, "2026-10-13")[1]["status"], "ok")

    def test_matching_files_in_other_encodings_are_kept_and_saved_as_utf8_so_the_readers_work(self):
        cases = {D(2026, 10, 7): "utf-16", D(2026, 10, 8): "cp1252", D(2026, 10, 9): "utf-32"}
        texts = {d: csv_text([("AAA", d), ("B\u2019B", d)]) for d in cases}
        _, out = self.run_dl({d: Resp(200, texts[d].encode(codec)) for d, codec in cases.items()})
        for d, codec in cases.items():
            f = self.tmp / "raw" / ("bhav_%s.csv" % d.strftime("%Y%m%d"))
            self.assertTrue(f.exists(), codec)
            self.assertEqual(f.read_bytes().decode("utf-8"), texts[d], codec)       # the same text, the same dates
            df, why = nu.read_raw_file(f)
            self.assertIsNone(why, codec)
            self.assertEqual(mu.parse_bhav_file(f, d.isoformat())[1]["status"], "ok", codec)
        self.assertIn("re-encoded", out)
        for d in cases:
            self.assertNotIn(d.isoformat(), (self.tmp / "holidays.txt").read_text())      # a readable file is never taken for a missing one

    def test_undecodable_bytes_are_not_saved_not_recorded_and_not_taken_for_a_holiday(self):
        recent, old = D(2026, 10, 13), D(2026, 10, 1)
        _, out = self.run_dl({recent: Resp(200, file_for(recent) + b"\x81"), old: Resp(200, b"\x81\x8d\x8f\x90\x9d")})
        self.assertEqual(self.saved(), [])
        self.assertFalse(self.ledger.exists())
        self.assertIn("could not be decoded", out)
        known = (self.tmp / "holidays.txt").read_text().split()
        self.assertNotIn(recent.isoformat(), known)
        self.assertNotIn(old.isoformat(), known)              # the 404 rule is for "not found"; garbage that did arrive is a problem to see

    def test_an_old_stale_copy_is_not_saved_and_is_recorded_only_as_an_observation(self):
        hol, prev = D(2026, 10, 2), D(2026, 10, 1)
        _, out = self.run_dl({hol: Resp(200, file_for(prev))})
        self.assertNotIn("bhav_20261002.csv", self.saved())
        self.assertEqual(self.records()["2026-10-02"], {"requested": "2026-10-02", "served": "2026-10-01", "first_seen": "2026-10-14", "last_checked": "2026-10-14", "checks": 1})
        self.assertNotIn("2026-10-02", (self.tmp / "holidays.txt").read_text())          # never promoted to a known holiday
        self.assertIn("unconfirmed observation, not a confirmed holiday", out)

    def test_a_recent_stale_copy_is_neither_saved_nor_recorded_and_is_asked_again(self):
        day = D(2026, 10, 13)
        self.run_dl({day: Resp(200, file_for(D(2026, 10, 12)))})
        self.assertEqual(self.saved(), [])
        self.assertFalse(self.ledger.exists())
        s, _ = self.run_dl({day: Resp(200, file_for(day))})
        self.assertIn(day, s.asked)
        self.assertIn("bhav_20261013.csv", self.saved())

    def test_mixed_later_unreadable_and_empty_responses_are_never_saved_or_recorded(self):
        d = D(2026, 10, 7)
        for body in (csv_bytes([("AAA", d), ("BBB", D(2026, 10, 6)), ("CCC", d)]), file_for(D(2026, 10, 8)),
                     (HEAD + "\nAAA, EQ, not-a-date, 1, 2, 1, 1, 1, 50\n").encode(), (HEAD + "\n").encode(), b"SYMBOL, SERIES, CLOSE_PRICE\nAAA, EQ, 1\n"):
            _, out = self.run_dl({d: Resp(200, body)})
            self.assertEqual(self.saved(), [], body[:40])
            self.assertFalse(self.ledger.exists(), body[:40])
            self.assertIn("do not match", out)

    def test_a_404_on_an_old_day_is_still_a_known_holiday_and_a_recent_404_is_not(self):
        self.run_dl({})
        known = (self.tmp / "holidays.txt").read_text().split()
        self.assertIn("2026-10-01", known)
        self.assertNotIn("2026-10-13", known)

    def test_every_saved_file_holds_only_rows_dated_its_own_name(self):
        replies = {D(2026, 10, 1): Resp(200, file_for(D(2026, 10, 1))), D(2026, 10, 2): Resp(200, file_for(D(2026, 10, 1))),
                   D(2026, 10, 5): Resp(200, file_for(D(2026, 10, 5))), D(2026, 10, 6): Resp(200, csv_bytes([("AAA", D(2026, 10, 6)), ("BBB", D(2026, 10, 5))])),
                   D(2026, 10, 7): Resp(200, file_for(D(2026, 10, 7)))}
        self.run_dl(replies)
        self.assertEqual(self.saved(), ["bhav_20261001.csv", "bhav_20261005.csv", "bhav_20261007.csv"])
        for p in (self.tmp / "raw").glob("bhav_*.csv"):
            iso = dt.datetime.strptime(p.stem[5:], "%Y%m%d").date().isoformat()
            self.assertEqual(set(nd.response_dates(p.read_bytes())), {iso}, p.name)


class Revalidation(Dl):
    HOL, PREV = D(2026, 10, 2), D(2026, 10, 1)

    def seed(self, last_checked, checks=1, first="2026-10-07"):
        first = min(first, last_checked)
        nd.write_observations(self.ledger, {"2026-10-02": {"requested": "2026-10-02", "served": "2026-10-01", "first_seen": first, "last_checked": last_checked, "checks": checks}})

    def test_a_recently_checked_observation_is_not_asked_for_again(self):
        self.seed("2026-10-09")                                       # 5 days before TODAY
        s, _ = self.run_dl({self.HOL: Resp(200, file_for(self.PREV))})
        self.assertNotIn(self.HOL, s.asked)
        self.assertEqual(self.records()["2026-10-02"]["checks"], 1)

    def test_an_observation_is_asked_for_again_once_it_is_a_week_old_and_a_consistent_answer_adds_a_check(self):
        self.seed("2026-10-07")                                       # exactly 7 days before TODAY
        s, _ = self.run_dl({self.HOL: Resp(200, file_for(self.PREV))})
        self.assertIn(self.HOL, s.asked)
        r = self.records()["2026-10-02"]
        self.assertEqual((r["checks"], r["first_seen"], r["last_checked"]), (2, "2026-10-07", "2026-10-14"))
        self.assertEqual(self.saved(), [])

    def test_after_three_answers_it_is_left_alone_but_stays_an_observation(self):
        self.seed("2026-10-07", checks=2)
        self.run_dl({self.HOL: Resp(200, file_for(self.PREV))})
        self.assertEqual(self.records()["2026-10-02"]["checks"], 3)
        s, _ = self.run_dl({self.HOL: Resp(200, file_for(self.PREV))}, today=D(2026, 12, 1))      # much later
        self.assertNotIn(self.HOL, s.asked)                                                      # no endless retry
        self.assertEqual(self.records()["2026-10-02"]["checks"], 3)
        self.assertNotIn("2026-10-02", (self.tmp / "holidays.txt").read_text())                  # and still not a confirmed holiday

    def test_if_the_real_file_appears_it_is_saved_and_the_observation_is_dropped(self):
        self.seed("2026-10-07")
        body = file_for(self.HOL)
        _, out = self.run_dl({self.HOL: Resp(200, body)})
        self.assertEqual((self.tmp / "raw" / "bhav_20261002.csv").read_bytes(), body)
        self.assertNotIn("2026-10-02", self.records())
        self.assertIn("now available", out)

    def test_a_different_stale_answer_starts_the_count_again(self):
        self.seed("2026-10-07", checks=2)
        self.run_dl({self.HOL: Resp(200, file_for(D(2026, 9, 30)))})
        r = self.records()["2026-10-02"]
        self.assertEqual((r["checks"], r["served"], r["first_seen"]), (1, "2026-09-30", "2026-10-14"))

    def test_a_mismatched_answer_during_revalidation_changes_nothing(self):
        self.seed("2026-10-07")
        before = self.ledger.read_text()
        self.run_dl({self.HOL: Resp(200, csv_bytes([("AAA", self.HOL), ("BBB", self.PREV)]))})
        self.assertEqual(self.ledger.read_text(), before)
        self.assertEqual(self.saved(), [])

    def test_a_valid_file_is_never_overwritten_or_replaced_because_of_a_record(self):
        self.seed("2026-10-01")                                       # due
        (self.tmp / "raw").mkdir(parents=True)
        mine = file_for(self.HOL, ("ZZZ",))
        (self.tmp / "raw" / "bhav_20261002.csv").write_bytes(mine)
        s, _ = self.run_dl({self.HOL: Resp(200, file_for(self.PREV))})
        self.assertNotIn(self.HOL, s.asked)
        self.assertEqual((self.tmp / "raw" / "bhav_20261002.csv").read_bytes(), mine)

    def test_invalid_lines_are_logged_and_dropped_when_the_file_is_rewritten_and_valid_ones_survive(self):
        self.ledger.write_text("garbage\n" + obs_line("2026-10-02", "2026-10-01", "2026-10-09", "2026-10-09", 1) + "\n" + obs_line("2026-10-03", "2026-10-02", "2026-10-09", "2026-10-09", 1) + "\n")
        _, out = self.run_dl({})
        self.assertIn("ignored line 1", out)
        self.assertIn("weekend", out)
        recs, problems = nd.read_observations(self.ledger, TODAY)
        self.assertEqual((list(recs), problems), (["2026-10-02"], []))

    def test_a_future_dated_record_is_ignored_not_trusted(self):
        self.ledger.write_text(obs_line("2026-10-02", "2026-10-01", "2026-10-09", "2026-12-31", 1) + "\n")
        s, out = self.run_dl({self.HOL: Resp(200, file_for(self.PREV))})
        self.assertIn("future", out)
        self.assertIn(self.HOL, s.asked)                      # ignored record -> the day is treated as unseen and asked for


# ----------------------------------------------------------------------------- the market data side
class Gaps(TM.Tmp):
    NOW = TM.NOW

    def world(self):
        return TM.World()

    def build(self, w, root, skip=(), stale=(), ledger=None):
        """Write the world without the `skip` day indexes; for each index in `stale` write a byte-for-byte copy of the PREVIOUS trading day's file under that day's name;
        `ledger` = {index: (checks, last_checked)} writes recorded observations."""
        keep = set(range(w.n)) - set(skip) - set(stale)
        w.write(root, only=keep)
        raw = Path(root) / "data" / "raw"
        for i in stale:
            shutil.copy(raw / ("bhav_%s.csv" % w.dates[i - 1].strftime("%Y%m%d")), raw / ("bhav_%s.csv" % w.dates[i].strftime("%Y%m%d")))
        if ledger:
            nd.write_observations(Path(root) / "data" / nd.STALE_FILE_NAME, {
                w.dates[i].isoformat(): {"requested": w.dates[i].isoformat(), "served": w.dates[i - 1].isoformat(), "first_seen": "2026-10-01", "last_checked": last, "checks": c}
                for i, (c, last) in ledger.items()})

    def go(self, root, now=None):
        code, msgs = mu.run(root, now or self.NOW, TM.SMALL)
        self.assertEqual(code, 0, msgs)
        return {n: json.loads((Path(root) / "out" / n).read_text()) for n in mu.FILES.values()}, msgs

    def other_root(self):
        r = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, r, ignore_errors=True)
        return r

    @staticmethod
    def figures(docs):
        return {k: {kk: vv for kk, vv in v.items() if kk != "data_quality"} for k, v in docs.items()}

    @staticmethod
    def dq(docs):
        return docs["market_breadth.json"]["data_quality"]

    def test_market_figures_and_trading_day_counts_are_unchanged_by_stale_copies(self):
        w = self.world()
        holidays = [100, 180]
        a, b = self.other_root(), self.root
        self.build(w, a, skip=holidays)
        self.build(w, b, stale=holidays)
        da, _ = self.go(a)
        db, _ = self.go(b)
        self.assertEqual(self.figures(da), self.figures(db))
        qa, qb = self.dq(da), self.dq(db)
        self.assertEqual((qa["trading_days_loaded"], qb["trading_days_loaded"]), (w.n - 2, w.n - 2))
        self.assertEqual((qa["files_used"], qb["files_used"]), (w.n - 2, w.n - 2))
        self.assertEqual(qb["files_seen"], qa["files_seen"] + 2)
        self.assertEqual(len(qb["files_rejected"]), 2)                                  # rejection details are still there ...
        self.assertEqual(set(qb["files_rejected"][0]), {"file", "status", "problem"})
        self.assertEqual(qb["files_rejected"][0]["status"], "date_mismatch")            # ... unchanged

    def test_a_stale_copy_does_not_remove_a_weekday_from_the_unexplained_gaps_and_is_never_called_confirmed(self):
        w = self.world()
        self.build(w, self.root, stale=[100, 180])
        db, msgs = self.go(self.root)
        q = self.dq(db)
        self.assertEqual(q["unexplained_weekday_gaps"], 2)                              # exactly what it was before this work
        self.assertEqual((q["weekday_gaps_with_stale_copy_observation"], q["weekday_gaps_without_observation"]), (2, 0))
        obs = q["nse_stale_copy_observations"]
        self.assertEqual([o["date"] for o in obs], sorted(w.dates[i].isoformat() for i in (100, 180)))
        for o in obs:
            self.assertEqual(o["status"], "unconfirmed")
            self.assertEqual(o["sources"], ["rejected_cache_file"])
            self.assertTrue(o["evidence"]["served_day_file_loaded"])
            self.assertTrue(o["evidence"]["row_count_equals_served_day"])               # a byte copy of the served day's file
        text = json.dumps(db)
        for forbidden in ("stale_copy_days", "confirmed_holiday", "holiday_confirmed"):
            self.assertNotIn(forbidden, text)
        self.assertTrue(any("2 weekday(s) have no file and are not known holidays" in m for m in msgs))
        note = [m for m in msgs if "answered by NSE with an earlier trading day's file" in m]
        self.assertEqual(len(note), 1)
        self.assertIn("unconfirmed observation", note[0])
        self.assertIn("not proof of a market holiday", note[0])
        self.assertEqual(sum("rejected" in m for m in msgs), 2)

    def test_a_genuinely_missing_weekday_stays_visible_and_is_told_apart_from_an_observed_one(self):
        w = self.world()
        self.build(w, self.root, skip=[220], stale=[100])
        q = self.dq(self.go(self.root)[0])
        self.assertEqual(q["unexplained_weekday_gaps"], 2)
        self.assertEqual((q["weekday_gaps_with_stale_copy_observation"], q["weekday_gaps_without_observation"]), (1, 1))
        self.assertEqual([o["date"] for o in q["nse_stale_copy_observations"]], [w.dates[100].isoformat()])

    def test_without_any_stale_copy_the_gap_report_is_what_it_was(self):
        w = self.world()
        self.build(w, self.root, skip=[100, 180])
        db, msgs = self.go(self.root)
        q = self.dq(db)
        self.assertEqual((q["unexplained_weekday_gaps"], q["weekday_gaps_with_stale_copy_observation"], q["weekday_gaps_without_observation"], q["nse_stale_copy_observations"]), (2, 0, 2, []))
        self.assertFalse(any("answered by NSE" in m for m in msgs))

    def test_only_the_known_holidays_file_confirms_a_holiday_and_then_an_observation_is_ignored_not_double_counted(self):
        w = self.world()
        self.build(w, self.root, stale=[100, 180])
        (self.root / "data" / "holidays.txt").write_text(w.dates[100].isoformat() + "\n")
        q = self.dq(self.go(self.root)[0])
        self.assertEqual(q["unexplained_weekday_gaps"], 1)                              # the confirmed one is gone from the gaps
        self.assertEqual([o["date"] for o in q["nse_stale_copy_observations"]], [w.dates[180].isoformat()])
        self.assertEqual(q["nse_stale_copy_records_ignored"]["known_holiday"], 1)

    def test_a_recorded_observation_with_no_file_is_shown_with_its_checks_and_is_still_a_gap(self):
        w = self.world()
        self.build(w, self.root, skip=[100], ledger={100: (2, "2026-10-05")})
        q = self.dq(self.go(self.root)[0])
        self.assertEqual(q["unexplained_weekday_gaps"], 1)
        o = q["nse_stale_copy_observations"][0]
        self.assertEqual((o["date"], o["sources"], o["checks"], o["recording"], o["status"]), (w.dates[100].isoformat(), ["recorded_response"], 2, "observed", "unconfirmed"))
        self.assertEqual((o["first_seen"], o["last_checked"], o["served_day"]), ("2026-10-01", "2026-10-05", w.dates[99].isoformat()))
        self.assertIsNone(o["evidence"]["row_count_equals_served_day"])                 # there is no file to compare

    def test_the_evidence_says_so_when_the_served_day_has_no_file_loaded(self):
        w = self.world()
        self.build(w, self.root, skip=[99, 100], ledger={100: (1, "2026-10-05")})          # NSE said "I sent day 99's file" but day 99 itself is not in the cache
        q = self.dq(self.go(self.root)[0])
        o = [x for x in q["nse_stale_copy_observations"] if x["date"] == w.dates[100].isoformat()][0]
        self.assertIs(o["evidence"]["served_day_file_loaded"], False)
        self.assertEqual(o["status"], "unconfirmed")                                        # weaker evidence, still reported, still never confirmed
        self.assertEqual(q["unexplained_weekday_gaps"], 2)

    def test_a_settled_observation_is_labelled_settled_not_confirmed(self):
        w = self.world()
        self.build(w, self.root, skip=[100], ledger={100: (3, "2026-10-05")})
        o = self.dq(self.go(self.root)[0])["nse_stale_copy_observations"][0]
        self.assertEqual((o["recording"], o["status"]), ("settled", "unconfirmed"))

    def test_a_file_and_a_recorded_observation_for_the_same_day_are_one_entry_with_both_sources(self):
        w = self.world()
        self.build(w, self.root, stale=[100], ledger={100: (1, "2026-10-02")})
        obs = self.dq(self.go(self.root)[0])["nse_stale_copy_observations"]
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["sources"], ["rejected_cache_file", "recorded_response"])

    def test_a_record_for_a_day_that_has_a_valid_file_or_is_outside_the_data_is_ignored_and_counted(self):
        w = self.world()
        self.build(w, self.root, ledger={100: (1, "2026-10-02")})                      # day 100 HAS a good file
        before = self.dq(self.go(self.root)[0])
        self.assertEqual(before["nse_stale_copy_observations"], [])
        self.assertEqual(before["nse_stale_copy_records_ignored"]["has_a_valid_file"], 1)
        self.assertEqual(before["trading_days_loaded"], w.n)
        far = self.other_root()
        self.build(w, far, skip=[0], ledger={1: (1, "2026-10-02")})                    # day 1's neighbour 0 is missing; day 1 itself exists -> use a record before the data starts
        nd.write_observations(far / "data" / nd.STALE_FILE_NAME, {"2020-01-02": {"requested": "2020-01-02", "served": "2020-01-01", "first_seen": "2020-01-09", "last_checked": "2020-01-09", "checks": 1}})
        ignored = self.dq(self.go(far)[0])["nse_stale_copy_records_ignored"]
        self.assertEqual(ignored["outside_loaded_range"], 1)

    def test_invalid_record_lines_are_ignored_reported_and_do_not_change_the_figures(self):
        w = self.world()
        a, b = self.other_root(), self.root
        self.build(w, a, skip=[100])
        self.build(w, b, skip=[100])
        (b / "data" / nd.STALE_FILE_NAME).write_text("garbage\n%s\n" % obs_line("2026-10-03", "2026-10-02", "2026-10-01", "2026-10-01", 1))
        da, _ = self.go(a)
        db, msgs = self.go(b)
        self.assertEqual(self.figures(da), self.figures(db))
        self.assertEqual(self.dq(db)["nse_stale_copy_records_ignored"]["invalid_lines"], 2)
        self.assertTrue(any("2 invalid line(s) in nse_stale_copy_days.txt were ignored" in m for m in msgs))
        self.assertEqual(self.dq(db)["nse_stale_copy_observations"], [])

    def test_a_copy_with_a_different_row_count_is_flagged_by_the_evidence_not_hidden(self):
        w = self.world()
        self.build(w, self.root, skip=[100])
        raw = self.root / "data" / "raw"
        src = (raw / ("bhav_%s.csv" % w.dates[99].strftime("%Y%m%d"))).read_text().splitlines()
        (raw / ("bhav_%s.csv" % w.dates[100].strftime("%Y%m%d"))).write_text("\n".join(src[:-3]) + "\n")        # a copy missing its last 3 rows
        o = self.dq(self.go(self.root)[0])["nse_stale_copy_observations"][0]
        self.assertFalse(o["evidence"]["row_count_equals_served_day"])
        self.assertEqual(o["status"], "unconfirmed")

    def test_a_stale_copy_only_3_days_old_is_labelled_too_recent_to_judge_and_a_day_later_unconfirmed(self):
        w = self.world()
        now = dt.datetime(2026, 10, 5, 22, 0, tzinfo=mu.IST)          # 2026-10-05 is the newest day; 2026-10-02 is exactly 3 days before it
        i = w.n - 2
        self.assertEqual(w.dates[i], D(2026, 10, 2))
        self.build(w, self.root, stale=[i])
        days, reports, counts = mu.load_days(self.root / "data" / "raw", now.date(), TM.SMALL)
        dq, _ = mu.assess(days, reports, counts, now, set(), TM.SMALL)
        self.assertEqual(dq["unexplained_weekday_gaps"], 1)
        self.assertEqual(dq["nse_stale_copy_observations"][0]["status"], "unconfirmed_too_recent_to_judge")
        later = dt.datetime(2026, 10, 6, 22, 0, tzinfo=mu.IST)
        dq2, _ = mu.assess(days, reports, counts, later, set(), TM.SMALL)
        self.assertEqual(dq2["nse_stale_copy_observations"][0]["status"], "unconfirmed")
        self.assertEqual(dq2["unexplained_weekday_gaps"], 1)

    def test_a_stale_copy_of_the_newest_day_still_fails_the_run_as_before(self):
        w = self.world()
        now = dt.datetime(2026, 10, 5, 22, 0, tzinfo=mu.IST)
        self.build(w, self.root, stale=[w.n - 1])
        code, msgs = mu.run(self.root, now, TM.SMALL)
        self.assertEqual(code, 1)
        self.assertTrue(any("a newer file was rejected" in m for m in msgs))
        self.assertFalse((self.root / "out" / "market_breadth.json").exists())

    def test_the_other_safety_gates_are_unchanged_an_empty_latest_file_still_fails(self):
        w = self.world()
        self.build(w, self.root, stale=[100])
        (self.root / "data" / "raw" / "bhav_20261005.csv").write_text("")
        code, msgs = mu.run(self.root, self.NOW, TM.SMALL)
        self.assertEqual(code, 1)

    def test_a_stale_copy_is_never_counted_as_a_trading_day(self):
        w = self.world()
        self.build(w, self.root, stale=[100])
        days, reports, counts = mu.load_days(self.root / "data" / "raw", self.NOW.date(), TM.SMALL)
        self.assertNotIn(w.dates[100].isoformat(), days)
        rep = [r for r in reports if r["date"] == w.dates[100].isoformat()][0]
        self.assertEqual((rep["status"], rep["stale_copy_of"]), ("date_mismatch", w.dates[99].isoformat()))

    def test_a_mixed_dated_file_is_rejected_and_is_not_an_observation(self):
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
        q = self.dq(self.go(self.root)[0])
        self.assertEqual((q["unexplained_weekday_gaps"], q["weekday_gaps_without_observation"], q["nse_stale_copy_observations"]), (1, 1, []))


if __name__ == "__main__":
    unittest.main()
