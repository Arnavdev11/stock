"""Tests for sensex_data_updater.py (no network, no token, no real clock). Run: python3 -m unittest test_sensex_data -v

Every request is a fake: requests.get is replaced wherever the module could reach it, and the Upstox client is a stand-in that counts its calls.
"""
import contextlib
import copy
import datetime as dt
import gzip
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sensex_data_updater as sx

ROOT = Path(__file__).resolve().parent
IST = sx.mu.IST
NOW = dt.datetime(2026, 10, 6, 21, 0, tzinfo=IST)          # Tuesday evening
END = dt.date(2026, 10, 6)
KEY = "BSE_INDEX|SENSEX"
INST = {"instrument_key": KEY, "instrument_name": "SENSEX"}
TOKEN = "SECRET-TOKEN-abc123"


def row(**kw):
    base = {"instrument_key": KEY, "name": "SENSEX", "trading_symbol": "SENSEX", "exchange": "BSE", "segment": "BSE_INDEX", "instrument_type": "INDEX", "exchange_token": "1"}
    base.update(kw)
    return base


def candles(frm, to, base=80000.0, step=10.0):
    out, d, i = [], frm, 0
    while d <= to:
        if d.weekday() < 5:
            c = round(base + step * i, 2)
            out.append({"date": d.isoformat(), "open": c, "high": c + 50, "low": c - 50, "close": c, "volume": 0})
            i += 1
        d += dt.timedelta(days=1)
    return out


class FakeClient:
    """Stands in for historical_updater.Client: records every call, returns newest-first like Upstox does."""
    def __init__(self, data=None, fail_with=None, token=None):
        self.data, self.fail_with, self.calls, self.token = data or [], fail_with, [], token

    def candles(self, key, frm, to):
        self.calls.append((key, frm, to))
        if self.fail_with:
            return None, self.fail_with
        return sorted([c for c in self.data if frm <= c["date"] <= to], key=lambda c: c["date"], reverse=True), None


def no_network(testcase):
    """Any attempt to use requests inside the test fails it."""
    def boom(*a, **k):
        raise AssertionError("a network request was attempted")
    p1, p2 = mock.patch.object(sx.requests, "get", boom), mock.patch.object(sx.requests, "Session", boom)
    p1.start(), p2.start()
    testcase.addCleanup(p1.stop)
    testcase.addCleanup(p2.stop)


class Resolve(unittest.TestCase):
    def test_exact_unique_bse_index_match(self):
        inst, why = sx.resolve([row(), row(instrument_key="BSE_INDEX|BSE500", name="BSE500", trading_symbol="BSE500")])
        self.assertIsNone(why)
        self.assertEqual(inst, {"instrument_key": KEY, "instrument_name": "SENSEX"})

    def test_the_same_row_listed_twice_is_one_instrument(self):
        inst, why = sx.resolve([row(), row()])
        self.assertIsNone(why)
        self.assertEqual(inst["instrument_key"], KEY)

    def test_matches_on_the_trading_symbol_when_the_name_differs(self):
        inst, why = sx.resolve([row(name="S&P BSE SENSEX")])
        self.assertIsNone(why)

    def test_missing(self):
        inst, why = sx.resolve([row(instrument_key="BSE_INDEX|BSE500", name="BSE500", trading_symbol="BSE500")])
        self.assertIsNone(inst)
        self.assertIn("no BSE_INDEX instrument", why)
        self.assertIsNone(sx.resolve([])[0])

    def test_ambiguous_two_keys_is_refused(self):
        inst, why = sx.resolve([row(), row(instrument_key="BSE_INDEX|SENSEX_ALT")])
        self.assertIsNone(inst)
        self.assertIn("2 distinct", why)

    def test_similar_names_are_not_accepted(self):
        for name in ("SENSEX 50", "BSE SENSEX", "S&P BSE SENSEX", "SENSEX NEXT 30", "BSE Sensex 50"):
            inst, why = sx.resolve([row(instrument_key="BSE_INDEX|X", name=name, trading_symbol=name)])
            self.assertIsNone(inst, name)

    def test_other_segments_are_ignored(self):
        self.assertIsNone(sx.resolve([row(segment="BSE_EQ"), row(segment="BSE_FO", instrument_key="BSE_FO|1")])[0])

    def test_wrong_exchange_or_type_is_refused(self):
        self.assertIn("not a BSE INDEX row", sx.resolve([row(exchange="NSE")])[1])
        self.assertIn("not a BSE INDEX row", sx.resolve([row(instrument_type="EQ")])[1])
        self.assertIn("not a BSE INDEX row", sx.resolve([row(), row(exchange=None)])[1])

    def test_malformed_key_is_refused(self):
        self.assertIsNone(sx.resolve([row(instrument_key="SENSEX")])[0])
        self.assertIsNone(sx.resolve([row(instrument_key="NSE_INDEX|SENSEX")])[0])
        self.assertIsNone(sx.resolve([row(instrument_key=None)])[0])
        self.assertIsNone(sx.resolve([row(instrument_key=123)])[0])

    def test_a_key_other_than_the_verified_one_stops_the_run_and_is_not_used(self):
        inst, why = sx.resolve([row(instrument_key="BSE_INDEX|Sensex")])
        self.assertIsNone(inst)
        self.assertIn("different key", why)

    def test_junk_rows_do_not_crash(self):
        inst, why = sx.resolve([None, 5, "x", [], {}, row()])
        self.assertEqual(inst["instrument_key"], KEY)


class LoadMetadata(unittest.TestCase):
    def resp(self, status=200, body=None, gz=True):
        raw = json.dumps(body if body is not None else [row(), {"segment": "BSE_EQ", "name": "X"}, "junk"]).encode()
        r = mock.Mock()
        r.status_code, r.content = status, (gzip.compress(raw) if gz else raw)
        return r

    def test_returns_only_bse_index_dict_rows_and_does_not_follow_redirects(self):
        with mock.patch.object(sx.requests, "get", return_value=self.resp()) as g:
            rows = sx.load_bse_index_rows("https://example.invalid/BSE.json.gz")
        self.assertEqual(rows, [row()])
        self.assertEqual(g.call_count, 1)
        self.assertIs(g.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(g.call_args.kwargs["timeout"], 90)
        self.assertNotIn("headers", g.call_args.kwargs)                         # no credentials of any kind on this request

    def test_plain_json_is_accepted_too(self):
        with mock.patch.object(sx.requests, "get", return_value=self.resp(gz=False)):
            self.assertEqual(sx.load_bse_index_rows("https://example.invalid/x"), [row()])

    def test_not_200_stops(self):
        for status in (301, 403, 404, 500):
            with mock.patch.object(sx.requests, "get", return_value=self.resp(status)), contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as cm:
                    sx.load_bse_index_rows("https://example.invalid/x")
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("HTTP %d" % status, out.getvalue())

    def test_network_error_stops_without_leaking_its_text(self):
        with mock.patch.object(sx.requests, "get", side_effect=sx.requests.ConnectionError("boom " + TOKEN)), contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                sx.load_bse_index_rows("https://example.invalid/x")
        self.assertIn("ConnectionError", out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())

    def test_bad_payloads_stop(self):
        bad = mock.Mock(status_code=200, content=b"not json")
        notlist = self.resp(body={"a": 1})
        for r in (bad, notlist):
            with mock.patch.object(sx.requests, "get", return_value=r), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    sx.load_bse_index_rows("https://example.invalid/x")

    def test_oversize_stops(self):
        with mock.patch.object(sx, "MAX_FILE_BYTES", 10), mock.patch.object(sx.requests, "get", return_value=self.resp()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                sx.load_bse_index_rows("https://example.invalid/x")


class Candles(unittest.TestCase):
    def good(self, **kw):
        c = {"date": "2026-10-05", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 0}
        c.update(kw)
        return c

    def test_tidy_accepts_a_valid_candle_and_nulls_a_zero_volume(self):
        self.assertEqual(sx.tidy(self.good())["volume"], None)
        self.assertEqual(sx.tidy(self.good(volume=5))["volume"], 5)
        self.assertEqual(sx.tidy(self.good(volume=True))["volume"], None)

    def test_tidy_rejects_malformed_and_impossible_candles(self):
        bads = [None, [], "x", {}, self.good(date="2026-13-40"), self.good(date="20261005"), self.good(date=5), self.good(date="2026-10-5"),
                self.good(open=0), self.good(close=-1), self.good(high=float("nan")), self.good(low=float("inf")), self.good(open=None), self.good(open="1"),
                self.good(open=True), self.good(high=1.0, low=2.0)]
        for b in bads:
            self.assertIsNone(sx.tidy(b), repr(b))
        c = self.good()
        del c["close"]
        self.assertIsNone(sx.tidy(c))

    def test_check_candles(self):
        ok = [sx.tidy(self.good(date="2026-10-02")), sx.tidy(self.good(date="2026-10-05"))]
        p = []
        sx.check_candles(ok, "t", p)
        self.assertEqual(p, [])
        for bad, word in ((ok + [ok[1]], "duplicate"), (ok[::-1], "ascending"), (ok + [{"date": "2026-10-06", "open": 0}], "malformed")):
            p = []
            sx.check_candles(bad, "t", p)
            self.assertTrue(p and word in p[0], (word, p))


class Run(unittest.TestCase):
    def setUp(self):
        no_network(self)

    def full(self):
        return candles(dt.date(2024, 9, 1), END)

    def test_first_run_fetches_two_years_in_three_requests(self):
        cl = FakeClient(self.full())
        doc, problems = sx.run(INST, cl, None, NOW, END, expected="2026-10-06")
        self.assertEqual(problems, [])
        self.assertEqual(len(cl.calls), 3)
        self.assertTrue(all(c[0] == KEY for c in cl.calls))
        self.assertEqual(cl.calls[0][1], (END - dt.timedelta(days=730)).isoformat())
        self.assertEqual(cl.calls[-1][2], END.isoformat())
        self.assertEqual(doc["last_date"], "2026-10-06")
        self.assertEqual(doc["first_date"], "2024-10-07")                      # the first weekday inside the two-year window
        dates = [c["date"] for c in doc["candles"]]
        self.assertEqual(dates, sorted(set(dates)))                             # newest-first responses come out ascending
        self.assertEqual(doc["candle_count"], len(doc["candles"]))

    def test_incremental_run_makes_one_request_from_shortly_before_the_last_date(self):
        data = self.full()
        old, _ = sx.run(INST, FakeClient([c for c in data if c["date"] <= "2026-10-01"]), None, NOW, dt.date(2026, 10, 1))
        cl = FakeClient(data)
        doc, problems = sx.run(INST, cl, old, NOW, END, expected="2026-10-06")
        self.assertEqual(problems, [])
        self.assertEqual(len(cl.calls), 1)
        self.assertEqual(cl.calls[0][1], (dt.date.fromisoformat(old["last_date"]) - dt.timedelta(days=sx.hu.OVERLAP_DAYS)).isoformat())
        self.assertEqual(doc["last_date"], "2026-10-06")
        self.assertGreater(doc["candle_count"], old["candle_count"])

    def test_the_request_cap_is_enforced_before_any_request(self):
        cl = FakeClient(self.full())
        with mock.patch.object(sx, "INDEX_YEARS", 5):
            doc, problems = sx.run(INST, cl, None, NOW, END)
        self.assertIsNone(doc)
        self.assertEqual(cl.calls, [])
        self.assertIn("candle requests", problems[0])

    def test_a_fetched_candle_replaces_the_older_copy_of_the_same_date(self):
        data = self.full()
        old, _ = sx.run(INST, FakeClient(data), None, NOW, END, expected="2026-10-06")
        changed = copy.deepcopy(data)
        changed[-1]["close"] = changed[-1]["high"]                              # a late correction of the last day
        doc, problems = sx.run(INST, FakeClient(changed), old, NOW, END, expected="2026-10-06")
        self.assertEqual(problems, [])
        self.assertEqual(doc["candles"][-1]["close"], changed[-1]["close"])

    def test_failed_fetch_returns_no_document(self):
        for err in ("HTTP 500", "network error: ConnectionError", "call limit for this run reached"):
            doc, problems = sx.run(INST, FakeClient(fail_with=err), None, NOW, END)
            self.assertIsNone(doc)
            self.assertIn(err, problems[0])

    def test_no_candles_returned(self):
        doc, problems = sx.run(INST, FakeClient([]), None, NOW, END)
        self.assertIsNone(doc)
        self.assertIn("no valid candles", problems[0])

    def test_malformed_or_impossible_candles_are_refused(self):
        for bad in (dict(open=-1), dict(high=1.0, low=2.0), dict(close=float("nan")), dict(date="2026-02-30")):
            data = self.full()
            data[-1].update(bad)
            doc, problems = sx.run(INST, FakeClient(data), None, NOW, END)
            self.assertIsNone(doc, bad)
            self.assertTrue(problems, bad)

    def test_duplicate_dates_in_a_response_are_refused(self):
        data = self.full() + [dict(self.full()[-1])]
        doc, problems = sx.run(INST, FakeClient(data), None, NOW, END)
        self.assertIsNone(doc)
        self.assertIn("repeats the date", problems[0])

    def test_a_future_dated_candle_is_refused(self):
        data = self.full() + candles(dt.date(2026, 10, 7), dt.date(2026, 10, 7), base=90000)
        doc, problems = sx.run(INST, FakeClient(data), None, NOW, dt.date(2026, 10, 8), expected="2026-10-06")
        self.assertIsNone(doc)
        self.assertTrue(any("future" in x for x in problems), problems)

    def test_stale_latest_data_is_refused(self):
        data = candles(dt.date(2024, 9, 1), dt.date(2026, 9, 25))
        doc, problems = sx.run(INST, FakeClient(data), None, NOW, END)
        self.assertIsNone(doc)
        self.assertTrue(any("more than 7 days old" in x for x in problems), problems)

    def test_backwards_movement_is_refused(self):
        old, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        tampered = copy.deepcopy(old)
        tampered["as_of"] = "2026-10-08"                                         # the published file claims a later date than the new data reaches
        doc, problems = sx.run(INST, FakeClient(self.full()), tampered, NOW, END, expected="2026-10-06")
        self.assertIsNone(doc)
        self.assertTrue(any("back in time" in x for x in problems), problems)

    def test_older_fetch_never_shortens_the_published_history(self):
        old, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        doc, problems = sx.run(INST, FakeClient([c for c in self.full() if c["date"] <= "2026-10-02"]), old, NOW, END, expected="2026-10-06")
        self.assertEqual(problems, [])
        self.assertEqual(doc["last_date"], "2026-10-06")                         # the union keeps every published date
        self.assertEqual({c["date"] for c in old["candles"]} - {c["date"] for c in doc["candles"]}, set())

    def test_dropped_published_dates_are_refused(self):
        old, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        new = copy.deepcopy(old)
        gone = new["candles"].pop(len(new["candles"]) // 2)["date"]
        p = sx.check_doc(new, old, "2024-01-01", NOW.date())
        self.assertTrue(any("dropped" in x and gone in x for x in p), p)
        self.assertEqual(sx.check_doc(new, old, "2999-01-01", NOW.date()), [])   # dates older than the keep limit may go

    def test_old_dates_beyond_the_keep_limit_are_trimmed_but_nothing_newer_is_lost(self):
        with mock.patch.object(sx, "KEEP_DAYS", 100):
            doc, problems = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        self.assertEqual(problems, [])
        self.assertGreaterEqual(doc["first_date"], (END - dt.timedelta(days=100)).isoformat())

    def test_instrument_key_change_is_refused_and_nothing_is_requested(self):
        old, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        old["instrument"]["instrument_key"] = "BSE_INDEX|SENSEX_OLD"
        cl = FakeClient(self.full())
        doc, problems = sx.run(INST, cl, old, NOW, END)
        self.assertIsNone(doc)
        self.assertEqual(cl.calls, [])
        self.assertIn("instrument key changed", problems[0])

    def test_check_doc_itself_refuses_a_key_change(self):
        old, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        other = copy.deepcopy(old)
        other["instrument"]["instrument_key"] = "BSE_INDEX|SENSEX_OLD"
        p = sx.check_doc(old, other, "2024-01-01", NOW.date())
        self.assertTrue(any("instrument key changed" in x for x in p), p)
        self.assertEqual(sx.check_doc(old, old, "2024-01-01", NOW.date()), [])

    def test_an_invalid_existing_file_is_never_overwritten(self):
        good, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        broken = [{}, {"kind": "other"}, dict(good, schema_version=2), dict(good, candles="x"), dict(good, candles=list(reversed(good["candles"]))),
                  dict(good, candles=good["candles"] + [good["candles"][-1]])]
        for b in broken:
            cl = FakeClient(self.full())
            doc, problems = sx.run(INST, cl, b, NOW, END)
            self.assertIsNone(doc)
            self.assertEqual(cl.calls, [])
            self.assertTrue(problems)

    def test_expected_latest_trading_day_flag(self):
        doc, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-06")
        self.assertTrue(doc["freshness_detail"]["source_is_latest_trading_day"])
        doc, _ = sx.run(INST, FakeClient(self.full()), None, NOW, END, expected="2026-10-07")
        self.assertFalse(doc["freshness_detail"]["source_is_latest_trading_day"])


class Schema(unittest.TestCase):
    def setUp(self):
        no_network(self)
        self.doc, p = sx.run(INST, FakeClient(candles(dt.date(2024, 9, 1), END)), None, NOW, END, expected="2026-10-06")
        self.assertEqual(p, [])

    def test_exact_top_level_keys_and_types(self):
        d = self.doc
        self.assertEqual(sorted(d), sorted(["schema_version", "kind", "as_of", "source", "metadata_source", "freshness", "freshness_detail", "generated_at", "interval",
                                            "instrument", "first_date", "last_date", "candle_count", "latest", "data_rights_note", "public_display_approved", "candles"]))
        self.assertEqual((d["schema_version"], d["kind"], d["freshness"], d["interval"]), (1, "sensex_history", "EOD", "1day"))
        self.assertEqual(d["instrument"], {"label": "SENSEX", "instrument_key": KEY, "instrument_name": "SENSEX", "exchange": "BSE", "segment": "BSE_INDEX", "instrument_type": "INDEX"})
        self.assertEqual(d["as_of"], d["last_date"])
        self.assertEqual(d["freshness_detail"]["label"], "EOD as of " + d["as_of"])
        self.assertEqual(sorted(d["latest"]), ["change_pct", "close", "date", "prev_close"])
        for c in d["candles"]:
            self.assertEqual(sorted(c), ["close", "date", "high", "low", "open", "volume"])
            self.assertEqual(sx.tidy(c), c)

    def test_strict_json_roundtrip(self):
        text = json.dumps(self.doc, allow_nan=False)
        def bad(c):
            raise ValueError(c)
        self.assertEqual(json.loads(text, parse_constant=bad), self.doc)

    def test_rights_note_and_unverified_url_are_stated(self):
        self.assertIn("unconfirmed", self.doc["data_rights_note"])
        self.assertIn("BSE SENSEX", self.doc["data_rights_note"])
        self.assertIn("public", self.doc["data_rights_note"])
        self.assertIn("unverified", self.doc["metadata_source"]["url_status"])
        self.assertEqual(self.doc["metadata_source"]["url"], sx.BSE_INSTRUMENTS_URL)

    def test_no_text_calls_the_data_real_time(self):
        def strings(x):
            if isinstance(x, str):
                yield x
            elif isinstance(x, dict):
                for v in x.values():
                    yield from strings(v)
            elif isinstance(x, list):
                for v in x:
                    yield from strings(v)
        for s in strings(self.doc):
            self.assertIsNone(re.search(r"\blive\b", s, re.I), s)

    def test_latest_summary_uses_the_previous_trading_day_only_when_close_enough(self):
        c = [sx.tidy({"date": "2026-09-01", "open": 1, "high": 2, "low": 1, "close": 100, "volume": 0}),
             sx.tidy({"date": "2026-10-05", "open": 1, "high": 2, "low": 1, "close": 110, "volume": 0})]
        self.assertIsNone(sx.latest_summary(c)["change_pct"])                    # a month apart: no change is claimed
        c[0]["date"] = "2026-10-02"
        self.assertEqual(sx.latest_summary(c)["change_pct"], 10.0)


class Gate(unittest.TestCase):
    def test_default_is_not_approved_and_only_the_exact_text_true_approves(self):
        self.assertFalse(sx.PUBLIC_DISPLAY_DEFAULT)
        self.assertFalse(sx.public_display_approved({}))
        for v in ("", "false", "0", "yes", "1", "approved", "tru", "true!", None, 1, True):
            self.assertFalse(sx.public_display_approved({sx.ENV_FLAG: v}), repr(v))
        for v in ("true", "TRUE", " True "):
            self.assertTrue(sx.public_display_approved({sx.ENV_FLAG: v}), repr(v))

    def test_reads_the_real_environment_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(sx.ENV_FLAG, None)
            self.assertFalse(sx.public_display_approved())
        with mock.patch.dict(os.environ, {sx.ENV_FLAG: "true"}):
            self.assertTrue(sx.public_display_approved())

    def test_the_written_file_carries_the_flag(self):
        for approved in (False, True):
            doc, _ = sx.run(INST, FakeClient(candles(dt.date(2024, 9, 1), END)), None, NOW, END, expected="2026-10-06", approved=approved)
            self.assertIs(doc["public_display_approved"], approved)
        doc, _ = sx.run(INST, FakeClient(candles(dt.date(2024, 9, 1), END)), None, NOW, END, expected="2026-10-06")
        self.assertIs(doc["public_display_approved"], False)                     # the default when nothing says otherwise


class Main(unittest.TestCase):
    """The whole job against a temporary out/ folder, with every network door replaced."""
    def setUp(self):
        no_network(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"
        self.out.mkdir()
        (self.out / "historical.json").write_text('{"benchmark": "untouched"}')
        (self.out / "index_history.json").write_text('{"indices": "untouched"}')
        self.before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        self.summary = Path(self.tmp.name) / "summary.md"
        self.patches = [mock.patch.object(sx, "OUT_FILE", self.out / "sensex_history.json"),
                        mock.patch.object(sx, "get_token", lambda: TOKEN),
                        mock.patch.object(sx, "load_bse_index_rows", lambda: [row()]),
                        mock.patch.object(sx.hu, "last_complete_day", lambda: END),
                        mock.patch.object(sx.mu, "read_holidays", lambda: set()),
                        mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary)})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(sx.ENV_FLAG, None)
        self.clients = []

    def go(self, client_factory, now=NOW):
        class Dt(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        def factory(token):
            c = client_factory(token)
            self.clients.append(c)
            return c
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sx.hu, "Client", factory), mock.patch.object(sx.dt, "datetime", Dt), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                sx.main()
                code = 0
            except SystemExit as e:
                code = e.code
        text = out.getvalue() + err.getvalue() + (self.summary.read_text() if self.summary.exists() else "")
        return code, text

    def files_other_than_ours(self):
        return {p.name: p.read_bytes() for p in self.out.iterdir() if p.name != "sensex_history.json"}

    def test_success_writes_only_sensex_history_and_leaves_the_other_files_byte_identical(self):
        code, text = self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), END)))
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["historical.json", "index_history.json", "sensex_history.json"])   # no stray .tmp file
        self.assertEqual(self.files_other_than_ours(), self.before)
        doc = json.loads((self.out / "sensex_history.json").read_text())
        self.assertEqual(doc["kind"], "sensex_history")
        self.assertIs(doc["public_display_approved"], False)
        self.assertEqual(len(self.clients[0].calls), 3)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(TOKEN, (self.out / "sensex_history.json").read_text())

    def test_public_display_flag_follows_the_environment(self):
        with mock.patch.dict(os.environ, {sx.ENV_FLAG: "true"}):
            self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), END)))
        self.assertIs(json.loads((self.out / "sensex_history.json").read_text())["public_display_approved"], True)

    def test_a_failed_fetch_leaves_the_published_file_exactly_as_it_was(self):
        self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), dt.date(2026, 10, 1))), now=NOW)
        path = self.out / "sensex_history.json"
        published = path.read_bytes()
        for err in ("HTTP 500", "network error: ConnectionError", "HTTP 429: too many requests " + TOKEN):
            code, text = self.go(lambda t, e=err: FakeClient(fail_with=e))
            self.assertEqual(code, 1)
            self.assertEqual(path.read_bytes(), published, err)
            self.assertEqual(self.files_other_than_ours(), self.before)
            self.assertNotIn(TOKEN, text)                                           # even an error text that contains the token is scrubbed
            self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["historical.json", "index_history.json", "sensex_history.json"])

    def test_a_failed_first_run_writes_no_file_at_all(self):
        code, text = self.go(lambda t: FakeClient(fail_with="HTTP 500"))
        self.assertEqual(code, 1)
        self.assertFalse((self.out / "sensex_history.json").exists())
        self.assertEqual(self.files_other_than_ours(), self.before)

    def test_unverified_instrument_stops_before_any_candle_request(self):
        for rows in ([], [row(), row(instrument_key="BSE_INDEX|OTHER")], [row(instrument_key="BSE_INDEX|Sensex")], [row(exchange="NSE")]):
            with mock.patch.object(sx, "load_bse_index_rows", lambda r=rows: r):
                self.clients.clear()
                code, text = self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), END)))
            self.assertEqual(code, 1)
            self.assertEqual(self.clients, [], "no client was even created")
            self.assertFalse((self.out / "sensex_history.json").exists())

    def test_an_unreadable_existing_file_is_left_alone(self):
        path = self.out / "sensex_history.json"
        path.write_text("{ this is not json")
        code, text = self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), END)))
        self.assertEqual(code, 1)
        self.assertEqual(path.read_text(), "{ this is not json")
        self.assertEqual(self.clients[0].calls if self.clients else [], [])

    def test_stale_data_leaves_the_published_file_alone(self):
        self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), END)))
        path = self.out / "sensex_history.json"
        published = path.read_bytes()
        later = dt.datetime(2026, 10, 30, 21, 0, tzinfo=IST)
        with mock.patch.object(sx.hu, "last_complete_day", lambda: later.date()):
            code, text = self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), END)), now=later)
        self.assertEqual(code, 1)
        self.assertEqual(path.read_bytes(), published)

    def test_no_token_means_no_work(self):
        with mock.patch.object(sx, "get_token", side_effect=SystemExit(1)):
            code, text = self.go(lambda t: FakeClient())
        self.assertEqual(code, 1)
        self.assertEqual(self.clients, [])

    def test_the_step_summary_has_no_prices_and_no_token(self):
        self.go(lambda t: FakeClient(candles(dt.date(2024, 9, 1), END)))
        s = self.summary.read_text()
        self.assertIn("SENSEX data: OK", s)
        self.assertIn(KEY, s)
        self.assertNotIn(TOKEN, s)
        self.assertIsNone(re.search(r"\d{5}\.\d", s), "no price in the summary")


class FakeResp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self.body, self.bad_json = status, body, bad_json

    def json(self):
        if self.bad_json:
            raise ValueError("not json")
        return self.body


def raw_rows(frm, to):
    """Candles in the shape the Upstox v2 API returns them: [timestamp, o, h, l, c, volume, oi], newest first."""
    return [[c["date"] + "T00:00:00+05:30", c["open"], c["high"], c["low"], c["close"], 0, 0] for c in reversed(candles(frm, to))]


def ok_body(rows):
    return {"status": "success", "data": {"candles": rows}}


WINDOW = re.compile(r"/day/(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$")


def serve(last=END, extra=None):
    """A handler that answers every candle request from a synthetic series that ends at `last`."""
    def handler(url, n):
        m = WINDOW.search(url)
        to, frm = dt.date.fromisoformat(m.group(1)), dt.date.fromisoformat(m.group(2))     # the v2 URL is .../day/<to>/<from>
        rows = raw_rows(max(frm, dt.date(2024, 9, 1)), min(to, last)) if frm <= last else []
        return FakeResp(200, ok_body(rows + (extra or [])))
    return handler


class Http:
    """Replaces requests.Session as the shared client sees it. Records every request and the headers set on the session; answers from handler(url, n).
    A handler may raise to simulate a network error. No socket is ever opened: requests.get is also replaced by a function that fails the test."""
    def __init__(self, testcase, handler):
        self.handler, self.requests, self.headers, self.sleeps = handler, [], [], []
        outer = self

        class Session:
            def __init__(self):
                self.headers = {}

            def get(self, url, timeout=None):
                outer.headers.append(dict(self.headers))
                outer.requests.append((url, timeout))
                return outer.handler(url, len(outer.requests))
        for p in (mock.patch.object(sx.hu.requests, "Session", Session), mock.patch.object(sx.hu.time, "sleep", lambda s: outer.sleeps.append(s)),
                  mock.patch.object(sx.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a direct network request was attempted")))):
            p.start()
            testcase.addCleanup(p.stop)


class RealClient(unittest.TestCase):
    """sensex_data_updater driven by the ACTUAL shared Upstox client (historical_updater.Client) over faked HTTP. No token is real, no socket opens."""
    def run_job(self, handler, old=None, now=NOW, end=END, **kw):
        http = Http(self, handler)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                res = sx.run(INST, sx.hu.Client(TOKEN), old, now, end, expected="2026-10-06", **kw)
            except SystemExit as e:
                res = e
        return http, res, out.getvalue()

    def test_first_run_request_shape_headers_encoding_and_pacing(self):
        http, (doc, problems), out = self.run_job(serve())
        self.assertEqual(problems, [])
        self.assertEqual(len(http.requests), 3)
        for url, timeout in http.requests:
            self.assertTrue(url.startswith("https://api.upstox.com/v2/historical-candle/BSE_INDEX%7CSENSEX/day/"), url)      # the "|" is percent-encoded
            self.assertNotIn("|", url)
            self.assertEqual(timeout, 30)
        windows = [WINDOW.search(u).groups() for u, _ in http.requests]
        self.assertEqual(windows[-1][0], END.isoformat())                              # the first window asked for ends today
        self.assertEqual(sorted({w[1] for w in windows})[0], (END - dt.timedelta(days=730)).isoformat())
        for h in http.headers:                                                          # compared with == inside assertTrue so a failure never prints the token
            self.assertTrue(h.get("Authorization") == "Bearer " + TOKEN)
            self.assertEqual(h.get("Accept"), "application/json")
        self.assertEqual(http.sleeps, [sx.hu.DELAY_SECONDS] * 3)                        # the shared pacing after every call
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN, json.dumps(doc))

    def test_response_parsing_and_the_shared_cleaner_drop_unusable_rows(self):
        bad = [["2026-10-05T00:00:00+05:30", -1, 2, 1, 1, 0, 0],          # negative price
               ["not a date", 1, 2, 1, 1, 0, 0], ["2026-10-04T00:00:00+05:30", 1, 1],      # bad date, short row
               ["2026-10-03T00:00:00+05:30", 5, 1, 9, 5, 0, 0],            # high below low
               "garbage", None, 7]
        http, (doc, problems), _ = self.run_job(serve(extra=bad))
        self.assertEqual(problems, [])
        dates = [c["date"] for c in doc["candles"]]
        self.assertEqual(dates, sorted(set(dates)))                                     # newest-first input, strictly ascending output
        self.assertTrue(all(sx.tidy(c) == c for c in doc["candles"]))
        self.assertEqual(doc["last_date"], "2026-10-06")
        newest = serve()(http.requests[-1][0], 0).body["data"]["candles"][0]              # the newest row of the last window, as Upstox sent it
        self.assertEqual((doc["candles"][-1]["date"], doc["candles"][-1]["close"]), (newest[0][:10], round(newest[4], 2)))
        self.assertIsNone(doc["candles"][-1]["volume"])                                 # a reported volume of 0 is stored as null

    def test_a_server_error_is_retried_and_then_succeeds(self):
        def handler(url, n):
            return FakeResp(500, {"status": "error"}) if n == 1 else serve()(url, n)
        http, (doc, problems), _ = self.run_job(handler)
        self.assertEqual(problems, [])
        self.assertEqual(len(http.requests), 4)                                         # 3 windows + 1 retry
        self.assertEqual(http.requests[0][0], http.requests[1][0])                      # the retry repeats the same window
        self.assertIn(10, http.sleeps)                                                  # the shared back-off after a 5xx

    def test_rate_limit_429_is_retried_with_backoff(self):
        def handler(url, n):
            return FakeResp(429, {"errors": [{"message": "slow down"}]}) if n <= 2 else serve()(url, n)
        http, (doc, problems), _ = self.run_job(handler)
        self.assertEqual(problems, [])
        self.assertEqual(len(http.requests), 5)
        self.assertIn(10, http.sleeps)
        self.assertIn(20, http.sleeps)

    def test_a_network_error_is_retried(self):
        def handler(url, n):
            if n == 1:
                raise sx.requests.ConnectionError("connection reset " + TOKEN)
            return serve()(url, n)
        http, (doc, problems), out = self.run_job(handler)
        self.assertEqual(problems, [])
        self.assertEqual(len(http.requests), 4)
        self.assertNotIn(TOKEN, out)

    def test_retries_are_bounded_and_the_run_fails(self):
        http, (doc, problems), _ = self.run_job(lambda u, n: FakeResp(500, {"status": "error"}))
        self.assertIsNone(doc)
        self.assertEqual(len(http.requests), sx.hu.RETRIES)                             # three attempts, then it gives up on the first window
        self.assertIn("HTTP 500", problems[0])

    def test_auth_failure_stops_at_once_without_retries_and_without_the_token(self):
        for status in (401, 403):
            http, res, out = self.run_job(lambda u, n, s=status: FakeResp(s, {"errors": [{"message": "rejected " + TOKEN}]}))
            self.assertIsInstance(res, SystemExit)
            self.assertEqual(res.code, 1)
            self.assertEqual(len(http.requests), 1)

    def test_bad_bodies_are_errors_not_data(self):
        for resp in (FakeResp(200, bad_json=True), FakeResp(200, {"status": "error", "data": {}}), FakeResp(200, {"status": "success", "data": {}}),
                     FakeResp(200, {"status": "success", "data": {"candles": "x"}}), FakeResp(200, ["not", "a", "dict"]), FakeResp(404, {})):
            http, (doc, problems), _ = self.run_job(lambda u, n, r=resp: r)
            self.assertIsNone(doc)
            self.assertTrue(problems)

    def test_an_unexpected_client_error_is_an_ordinary_failure_and_never_a_crash(self):
        class Boom:
            calls = 0
            def candles(self, key, frm, to):
                raise KeyError("secret " + TOKEN)
        doc, problems = sx.run(INST, Boom(), None, NOW, END)
        self.assertIsNone(doc)
        self.assertIn("KeyError", problems[0])
        self.assertNotIn(TOKEN, problems[0])                                        # only the exception type is reported
        http, (doc, problems), _ = self.run_job(lambda u, n: FakeResp(200, ["a", "list"]))   # the shared client itself cannot cope with a JSON list body
        self.assertIsNone(doc)
        self.assertIn("AttributeError", problems[0])

    def test_an_empty_answer_is_not_data(self):
        http, (doc, problems), _ = self.run_job(lambda u, n: FakeResp(200, ok_body([])))
        self.assertIsNone(doc)
        self.assertIn("no valid candles", problems[0])

    def test_the_shared_call_budget_stops_the_run(self):
        with mock.patch.object(sx.hu, "MAX_CALLS_PER_RUN", 2):
            http, (doc, problems), _ = self.run_job(serve())
        self.assertIsNone(doc)
        self.assertEqual(len(http.requests), 2)
        self.assertIn("call limit", problems[0])


class RealClientMain(unittest.TestCase):
    """main() end to end: real shared client, faked HTTP, a temporary out/ folder. Nothing about the existing file may change on any failure."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"
        self.out.mkdir()
        (self.out / "historical.json").write_text('{"benchmark": "untouched"}')
        (self.out / "index_history.json").write_text('{"indices": "untouched"}')
        self.summary = Path(self.tmp.name) / "summary.md"
        for p in (mock.patch.object(sx, "OUT_FILE", self.out / "sensex_history.json"), mock.patch.object(sx, "get_token", lambda: TOKEN),
                  mock.patch.object(sx, "load_bse_index_rows", lambda: [row()]), mock.patch.object(sx.hu, "last_complete_day", lambda: END),
                  mock.patch.object(sx.mu, "read_holidays", lambda: set()), mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary)})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(sx.ENV_FLAG, None)

    def go(self, handler):
        http = Http(self, handler)

        class Dt(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sx.dt, "datetime", Dt), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                sx.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return http, code, out.getvalue() + err.getvalue() + (self.summary.read_text() if self.summary.exists() else "")

    def tree(self):
        return {p.name: p.read_bytes() for p in sorted(self.out.iterdir())}

    def test_success_through_the_real_client(self):
        http, code, text = self.go(serve())
        self.assertEqual(code, 0, text)
        self.assertEqual(len(http.requests), 3)
        files = self.tree()
        self.assertEqual(sorted(files), ["historical.json", "index_history.json", "sensex_history.json"])
        self.assertEqual(files["historical.json"], b'{"benchmark": "untouched"}')
        self.assertEqual(files["index_history.json"], b'{"indices": "untouched"}')
        self.assertNotIn(TOKEN.encode(), files["sensex_history.json"])
        self.assertNotIn(TOKEN, text)
        doc = json.loads(files["sensex_history.json"])
        self.assertIs(doc["public_display_approved"], False)
        self.assertIn("unconfirmed", doc["data_rights_note"])

    def test_every_failure_leaves_the_existing_file_byte_identical_and_leaks_nothing(self):
        self.go(serve(last=dt.date(2026, 10, 1)))                                       # an earlier good run publishes a file
        before = self.tree()
        self.assertIn("sensex_history.json", before)
        echo = {"errors": [{"message": "token was " + TOKEN}]}
        failures = {"server error": lambda u, n: FakeResp(500, echo),
                    "rate limited": lambda u, n: FakeResp(429, echo),
                    "auth rejected": lambda u, n: FakeResp(401, echo),
                    "forbidden": lambda u, n: FakeResp(403, echo),
                    "network": lambda u, n: (_ for _ in ()).throw(sx.requests.ConnectionError("down " + TOKEN)),
                    "bad json": lambda u, n: FakeResp(200, bad_json=True),
                    "empty": lambda u, n: FakeResp(200, ok_body([])),
                    "malformed candle": lambda u, n: FakeResp(200, ok_body([["2026-10-06T00:00:00+05:30", 0, 0, 0, 0, 0, 0]]))}
        for name, handler in failures.items():
            http, code, text = self.go(handler)
            self.assertEqual(code, 1, name)
            self.assertEqual(self.tree(), before, name)                                  # every file in out/, byte for byte, and no stray .tmp
            self.assertNotIn(TOKEN, text, name)

    def test_an_older_answer_never_overwrites_a_newer_file(self):
        self.go(serve())
        before = self.tree()
        http, code, text = self.go(serve(last=dt.date(2026, 9, 1)))                    # the API answers with old data only: stale, refused
        self.assertEqual(code, 1)
        self.assertEqual(self.tree(), before)

    def test_unverified_instrument_makes_no_http_request_at_all(self):
        with mock.patch.object(sx, "load_bse_index_rows", lambda: [row(instrument_key="BSE_INDEX|OTHER"), row()]):
            http, code, text = self.go(serve())
        self.assertEqual(code, 1)
        self.assertEqual(http.requests, [])
        self.assertFalse((self.out / "sensex_history.json").exists())


class Source(unittest.TestCase):
    """Static checks on the module's own source."""
    code = (ROOT / "sensex_data_updater.py").read_text(encoding="utf-8")
    body = re.sub(r'""".*?"""', "", code, flags=re.S)

    def test_it_writes_only_its_own_file(self):
        self.assertEqual(len(re.findall(r"\bwrite_json\(", self.body)), 1)
        self.assertIn("write_json(OUT_FILE,", self.body)
        self.assertEqual(sx.OUT_FILE.name, "sensex_history.json")
        for other in ("historical.json", "index_history.json", "market_breadth", "shareholding", "by_symbol"):
            self.assertNotIn(other, self.body)
        self.assertEqual(re.findall(r"\bopen\(([^)]*)\)", self.body), ['path, "a", encoding="utf-8"'])      # the only file handle: the optional GitHub step summary, append mode

    def test_imports_are_the_shared_helpers_only(self):
        mods = sorted(set(re.findall(r"^(?:import|from) ([a-z_]+)", self.body, re.M)))
        self.assertEqual(mods, ["contextlib", "datetime", "gzip", "historical_updater", "io", "json", "market_data_updater", "math", "os", "re", "requests", "sys", "upstox_common"])

    def test_the_token_is_never_printed_or_formatted_into_text(self):
        for line in self.body.splitlines():
            if re.search(r"\b(print|log|summary)\(", line) and "token" in line.lower():
                self.assertIn("scrub(", line)                                      # any output line that mentions the token goes through scrub()
        self.assertEqual(len(re.findall(r"get_token\(\)", self.body)), 1)
        self.assertNotRegex(self.body, r"Authorization|Bearer")                    # the shared client adds the header; this module never builds one

    def test_the_notes_are_documented_in_the_module(self):
        for needle in ("UNVERIFIED CANDIDATE", "UNCONFIRMED", "fail closed", "VERIFIED_KEY"):
            self.assertIn(needle, self.code)
        self.assertNotIn("SECTOR_PUBLIC_DISPLAY_APPROVED", self.code)              # a different gate, never mixed with the sector one

    def test_importing_the_module_does_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            import importlib
            importlib.reload(sx)
        self.assertEqual(out.getvalue(), "")

    def test_the_sector_gate_files_are_untouched_by_this_stage(self):
        self.assertIn("DEFAULT_APPROVED = False", (ROOT / "sector_publish.py").read_text())
        self.assertIn('SECTOR_PUBLIC_DISPLAY_APPROVED: "false"', (ROOT / ".github" / "workflows" / "sector_data.yml").read_text())
        # no workflow RUNS the updater or publishes the file; the only mentions are the Stage 4 publication guard in the two site-building workflows (see test_sensex_publish.py)
        for p in (ROOT / ".github" / "workflows").glob("*.yml"):
            t = p.read_text().lower()
            self.assertNotIn("sensex_data_updater", t, p.name)
            self.assertNotIn("sensex_history", t, p.name)
            self.assertEqual("sensex" in t, p.name in ("update.yml", "market_data.yml"), p.name)


if __name__ == "__main__":
    unittest.main()
