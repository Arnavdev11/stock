"""Tests for coverage_probe.py: the read-only Upstox coverage probe. No network: sessions and endpoints are stubs.  Run: python3 -m unittest test_coverage_probe"""
import datetime as dt
import io
import contextlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import coverage_probe as cp
import historical_updater as hist
import quarterly_results_updater as qru
import test_quarterly_results as TQ
import upstox_common as uc

HERE = Path(__file__).resolve().parent
SRC = (HERE / "coverage_probe.py").read_text(encoding="utf-8")
WF = (HERE / ".github" / "workflows" / "coverage_probe.yml").read_text(encoding="utf-8")


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._b = status, body

    def json(self):
        if self._b is None:
            raise ValueError("no json")
        return self._b


class Session:
    """Stands in for requests.Session: replies from a list (the last reply repeats) and counts the requests."""

    def __init__(self, replies):
        self.replies, self.n, self.headers = list(replies), 0, {}

    def get(self, url, params=None, timeout=None):
        r = self.replies[min(self.n, len(self.replies) - 1)]
        self.n += 1
        return r


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class ConfigTests(unittest.TestCase):
    def test_the_default_symbols_are_twelve_distinct_plain_symbols(self):
        s = cp.DEFAULT_SYMBOLS
        self.assertEqual(len(s), 12)
        self.assertEqual(len(set(s)), 12)
        self.assertLessEqual(len(s), cp.MAX_SYMBOLS)
        for x in s:
            self.assertRegex(x, cp.SYMBOL_RE.pattern)

    def test_choose_symbols_validates(self):
        self.assertEqual(cp.choose_symbols("TCS, INFY"), ["TCS", "INFY"])
        self.assertEqual(cp.choose_symbols(""), list(cp.DEFAULT_SYMBOLS))
        for bad in ("tcs", "TCS,TCS", ",".join("S%d" % i for i in range(13)), "A B", "../x"):
            with quiet(), self.assertRaises(SystemExit):
                cp.choose_symbols(bad)

    def test_the_call_count_fits_the_caps(self):
        n = len(cp.DEFAULT_SYMBOLS)
        base = n * len(cp.ENDPOINTS)                              # 9 requests per stock when consolidated data exists
        worst = n * (1 + 1 + 2 + 2 + 2 + 2 + 1 + 1)               # profile, ratios, three statements and quarterly each retried once on standalone; corporate actions, share holdings
        self.assertEqual(base, 108)
        self.assertLessEqual(worst, cp.FUNDAMENTALS_CALL_CAP)
        self.assertLessEqual(n, cp.CANDLE_CALL_CAP)
        self.assertEqual(len(cp.ENDPOINTS), 9)


class WritePathTests(unittest.TestCase):
    def test_nothing_can_be_written_outside_the_probe_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "probe_output"
            p = cp.write_artifact("raw/x.json", {"a": 1}, root)
            self.assertEqual(p.parent.parent, root.resolve())
            for bad in ("../escape.json", "/etc/escape.json", "raw/../../escape.json"):
                with self.assertRaises(ValueError):
                    cp.write_artifact(bad, {}, root)
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()), ["probe_output"])

    def test_the_source_calls_no_production_writer_and_has_a_single_write_call(self):
        for banned in ("write_json(", "write_outputs(", "shards.", "write_shard", "CACHE_FILE", "_ledger", "ledger_storage", "open(", "os.remove", "unlink", "shutil", "subprocess"):
            self.assertNotIn(banned, SRC, banned)
        self.assertEqual(SRC.count(".write_text("), 1)             # inside write_artifact only
        mods = sorted(m.group(1) for m in re.finditer(r"^(?:import|from) ([a-z_]+)", SRC, re.M))
        self.assertEqual(mods, ["datetime", "financials_updater", "fundamentals_extra_updater", "fundamentals_updater", "historical_updater", "json", "os", "pathlib", "quarterly_results_updater", "re", "stock_directory", "sys", "upstox_common"])
        self.assertIn("import fundamentals_extra_updater as fx", SRC)       # its parsers only: fetch_one and ttm_dividend, never write_outputs or the cache

    def test_the_probe_never_logs_or_stores_the_token(self):
        for line in SRC.splitlines():
            if "log(" in line or "print(" in line:
                self.assertNotIn("token", line.lower().replace("get_token", ""), line)
        self.assertNotIn("Authorization", SRC)


class CapTests(unittest.TestCase):
    """The probe relies on the production clients' call budget: retries count against it and a rejected token stops the run."""

    def setUp(self):
        p = mock.patch.object(uc.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def test_fundamentals_budget_is_enforced_before_each_call_and_retries_count(self):
        api = uc.Upstox("tok-123456", 5)
        api.s = Session([Resp(500, {})])
        body, err = api.get("INE0/profile")                       # 3 attempts, all 500
        self.assertIsNone(body)
        self.assertEqual(api.calls, 3)
        api.get("INE0/profile")                                   # starts under the cap, so it may finish its own 3 attempts: 6 in all (at most RETRIES-1 over the cap)
        self.assertLessEqual(api.calls, 5 + uc.RETRIES - 1)
        n = api.s.n
        body, err = api.get("INE0/key-ratios")
        self.assertEqual(err, "call limit for this run reached")
        self.assertEqual(api.s.n, n)                              # no request is started once the cap is reached

    def test_the_probe_api_never_exceeds_its_strict_cap(self):
        for cap in (10, 11, 12, 130):
            for replies in ([Resp(500, {})], [Resp(200, {"status": "success", "data": {}})], [Resp(429, {})], [Resp(404, {})]):
                api = cp.make_fundamentals_api("tok-123456", cap)
                api.s = Session(replies)
                for _ in range(cap * 2):
                    api.get("INE0/profile")
                self.assertLessEqual(api.s.n, cap, (cap, replies[0].status_code))
                self.assertEqual(api.calls, api.s.n)
        # and the strict cap is the documented one
        self.assertEqual(cp.FUNDAMENTALS_CALL_CAP + cp.CANDLE_CALL_CAP, 166)

    def test_without_the_compensation_the_production_client_would_overshoot(self):
        api = uc.Upstox("tok-123456", 10)                         # the plain budget: documents why make_fundamentals_api exists
        api.s = Session([Resp(500, {})])
        for _ in range(20):
            api.get("INE0/profile")
        self.assertGreater(api.s.n, 10)

    def test_a_rejected_token_stops_the_probe_but_the_report_is_still_written(self):
        api = uc.Upstox("tok-123456", 10)
        api.s = Session([Resp(401, {})])
        client = mock.Mock(calls=0)
        with tempfile.TemporaryDirectory() as d, quiet(), self.assertRaises(SystemExit):
            cp.run(["AAA"], {"AAA": {"isin": "INE000A01011", "name": "A"}}, api, client, dt.date(2026, 10, 14), root=Path(d) / "probe_output")
        # the report was written by the finally block even though the run stopped
        with tempfile.TemporaryDirectory() as d, quiet():
            root = Path(d) / "probe_output"
            try:
                cp.run(["AAA"], {"AAA": {"isin": "INE000A01011", "name": "A"}}, api, client, dt.date(2026, 10, 14), root=root)
            except SystemExit:
                pass
            rep = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(rep["kind"], "coverage_probe")
            self.assertEqual(rep["results"]["AAA"]["isin"], "INE000A01011")

    def test_the_candle_client_respects_its_cap(self):
        with mock.patch.object(hist, "MAX_CALLS_PER_RUN", 2):
            c = hist.Client("tok-123456")
            c.s = Session([Resp(500, {})])
            rows, err = c.candles("NSE_EQ|INE000A01011", "2026-01-01", "2026-06-30")
            self.assertIsNone(rows)
            self.assertEqual(c.s.n, 2)


class EndpointTests(unittest.TestCase):
    def api(self, *replies):
        a = mock.Mock(calls=0)
        a.get.side_effect = list(replies)
        return a

    def test_profile_and_ratios(self):
        self.assertEqual(cp.probe_profile(self.api(({"data": {"sector": "IT"}}, None)), "I")["status"], "ok")
        self.assertEqual(cp.probe_profile(self.api(({"data": {"sector": ""}}, None)), "I"), {"status": "no_data", "missing": ["sector"]})
        self.assertEqual(cp.probe_profile(self.api((None, "HTTP 404")), "I")["status"], "error")
        self.assertEqual(cp.probe_profile(self.api((None, "call limit for this run reached")), "I")["status"], "skipped_call_cap")
        full = {"data": [{"name": n, "company_value": 1.5} for n in ("P/E", "P/B", "ROA", "ROE", "ROCE", "EV/EBITDA")]}
        self.assertEqual(cp.probe_ratios(self.api((full, None)), "I")["status"], "ok")
        part = {"data": [{"name": "P/E", "company_value": 1.5}]}
        r = cp.probe_ratios(self.api((part, None)), "I")
        self.assertEqual((r["status"], r["missing"]), ("partial", ["ev_ebitda", "pb", "roa", "roce", "roe"]))
        self.assertEqual(cp.probe_ratios(self.api(({"data": []}, None)), "I")["status"], "no_data")

    def test_sections_use_the_production_fetch_and_report_missing_fields(self):
        sec = ("income", "income-statement", {}, None)
        with mock.patch.object(cp.fin, "fetch_section", return_value=({"period": "Mar 2026", "revenue": 1.0, "profit_after_tax": 2.0}, "consolidated", None)):
            self.assertEqual(cp.probe_section(None, "I", *sec)["status"], "ok")
        with mock.patch.object(cp.fin, "fetch_section", return_value=({"period": "Mar 2026", "revenue": None, "profit_after_tax": 2.0}, "standalone", None)):
            r = cp.probe_section(None, "I", *sec)
            self.assertEqual((r["status"], r["missing"], r["basis"]), ("partial", ["revenue"], "standalone"))
        with mock.patch.object(cp.fin, "fetch_section", return_value=(None, None, "no usable data (standalone)")):
            self.assertEqual(cp.probe_section(None, "I", *sec)["status"], "no_data")
        with mock.patch.object(cp.fin, "fetch_section", return_value=(None, None, "HTTP 500")):
            self.assertEqual(cp.probe_section(None, "I", *sec)["status"], "error")

    def test_quarterly_reports_depth_and_whether_the_year_ago_quarter_exists(self):
        r = cp.probe_quarterly(self.api((TQ.REAL, None)), "I")
        self.assertEqual((r["status"], r["quarters"], r["year_ago_quarter_present"], r["basis"]), ("ok", 5, True, "consolidated"))
        four = TQ.real_shape(TQ.R5[1:], [1100, 1200, 1300, 1500], [210, 230, 240, 300], [110, 120, 130, 150])
        r4 = cp.probe_quarterly(self.api((four, None)), "I")
        self.assertEqual((r4["quarters"], r4["year_ago_quarter_present"]), (4, False))
        self.assertEqual(r4["fields_present"]["eps_basic"], 0)
        self.assertEqual(r4["missing"], [])

    def test_quarterly_falls_back_to_standalone_only_when_consolidated_has_no_data(self):
        empty = {"status": "success", "data": {"type": "consolidated", "time_period": "quarterly", "income_statement": [], "full_statement": None}}
        a = self.api((empty, None), (TQ.REAL, None))
        r = cp.probe_quarterly(a, "I")
        self.assertEqual((r["status"], r["basis"]), ("ok", "standalone"))
        self.assertEqual(a.get.call_count, 2)
        a2 = self.api((TQ.REAL, None))
        cp.probe_quarterly(a2, "I")
        self.assertEqual(a2.get.call_count, 1)
        self.assertEqual(cp.probe_quarterly(self.api((None, "HTTP 500")), "I")["status"], "error")

    def test_candles(self):
        row = lambda i: {"date": "2026-01-%02d" % (i % 28 + 1)}
        c = mock.Mock()
        c.candles.return_value = ([row(i) for i in range(120)], None)
        r = cp.probe_candles(c, "INE000A01011", dt.date(2026, 10, 14))
        self.assertEqual((r["status"], r["candles"]), ("ok", 120))
        c.candles.assert_called_once_with("NSE_EQ|INE000A01011", "2026-04-17", "2026-10-14")
        c.candles.return_value = ([row(0)], None)
        self.assertEqual(cp.probe_candles(c, "I", dt.date(2026, 10, 14))["status"], "partial")
        c.candles.return_value = ([], None)
        self.assertEqual(cp.probe_candles(c, "I", dt.date(2026, 10, 14))["status"], "no_data")
        c.candles.return_value = (None, "HTTP 400")
        self.assertEqual(cp.probe_candles(c, "I", dt.date(2026, 10, 14))["status"], "error")


class ExtraEndpointTests(unittest.TestCase):
    """probe_extra on the documented response shapes (invented values, from test_fundamentals_extra)."""
    def api(self, body):
        a = mock.Mock(calls=0)
        a.get.return_value = body
        return a

    def test_corporate_actions(self):
        import test_fundamentals_extra as tf
        a = self.api((tf.ACTIONS_BODY, None))
        r = cp.probe_extra(a, "INE000A01011", "corporate_actions")
        a.get.assert_called_once_with("INE000A01011/corporate-actions")
        self.assertEqual((r["status"], r["events"], r["types"], r["first"], r["last"]), ("ok", 5, {"dividend": 3, "bonus": 1, "split": 1}, "2023-01-02", "2026-08-14"))
        gap = {"data": [{"name": "Dividend", "expiry_date": "01 Oct 2026", "amount": None}]}
        r = cp.probe_extra(self.api((gap, None)), "I", "corporate_actions")
        self.assertEqual((r["status"], r["gaps"]["dividend_without_amount"]), ("partial", 1))
        self.assertEqual(cp.probe_extra(self.api(({"data": []}, None)), "I", "corporate_actions")["status"], "no_data")
        self.assertEqual(cp.probe_extra(self.api(({"data": {}}, None)), "I", "corporate_actions")["status"], "rejected")
        self.assertEqual(cp.probe_extra(self.api((None, "HTTP 500")), "I", "corporate_actions")["status"], "error")
        self.assertEqual(cp.probe_extra(self.api((None, "call limit for this run reached")), "I", "corporate_actions")["status"], "skipped_call_cap")

    def test_share_holdings(self):
        import test_fundamentals_extra as tf
        a = self.api((tf.HOLDINGS_BODY, None))
        r = cp.probe_extra(a, "INE000A01011", "share_holdings")
        a.get.assert_called_once_with("INE000A01011/share-holdings")
        self.assertEqual((r["status"], r["quarters"], r["latest"], r["missing"]), ("ok", 2, "2026-09-30", []))
        part = {"data": [x for x in tf.HOLDINGS_BODY["data"] if x["category"] != "fii"]}
        r = cp.probe_extra(self.api((part, None)), "I", "share_holdings")
        self.assertEqual((r["status"], r["missing"]), ("partial", ["fii"]))


class RunTests(unittest.TestCase):
    D = {"AAA": {"symbol": "AAA", "isin": "INE000A01011", "name": "A"}, "BBB": {"symbol": "BBB", "isin": "INE000B01011", "name": "B"}}

    def patches(self, bad=None):
        ok = lambda *a, **k: cp.result("ok")
        def boom(*a, **k):
            return cp.result("error", error="HTTP 500")
        return [mock.patch.object(cp, n, bad if (bad and n == "probe_ratios") else ok) for n in ("probe_profile", "probe_ratios", "probe_quarterly", "probe_candles", "probe_extra")] + \
               [mock.patch.object(cp, "probe_section", ok)]

    def run_probe(self, symbols, bad=None):
        with tempfile.TemporaryDirectory() as d, quiet():
            root = Path(d) / "probe_output"
            ps = self.patches(bad)
            for p in ps:
                p.start()
            try:
                api, client = mock.Mock(calls=7), mock.Mock(calls=2)
                rep = cp.run(symbols, self.D, api, client, dt.date(2026, 10, 14), identity={"AAA": "match"}, root=root)
                disk = json.loads((root / "report.json").read_text(encoding="utf-8"))
                files = sorted(str(x.relative_to(Path(d))) for x in Path(d).rglob("*") if x.is_file())
            finally:
                for p in ps:
                    p.stop()
        return rep, disk, files

    def test_report_shape_counts_and_only_the_report_is_written(self):
        rep, disk, files = self.run_probe(["AAA", "BBB"])
        self.assertEqual(rep, disk)
        self.assertEqual(files, ["probe_output/report.json"])
        self.assertEqual(rep["calls"], {"fundamentals": 7, "candles": 2})
        self.assertTrue(rep["within_caps"])
        self.assertEqual(rep["summary"]["income"]["ok"], 2)
        self.assertEqual(rep["summary"]["candles"]["error"], 0)
        self.assertIn("NSE filings not probed", rep["shareholding"])
        self.assertEqual(set(rep["results"]["AAA"]) - {"isin", "name"}, set(cp.ENDPOINTS))

    def test_a_symbol_missing_from_the_directory_makes_no_request(self):
        rep, _, _ = self.run_probe(["AAA", "ZZZ"])
        self.assertEqual(rep["results"]["ZZZ"], {"directory": "not_in_directory"})

    def test_one_failing_endpoint_is_recorded_and_does_not_stop_the_rest(self):
        rep, _, _ = self.run_probe(["AAA", "BBB"], bad=lambda *a, **k: cp.result("error", error="HTTP 500"))
        self.assertEqual(rep["summary"]["key_ratios"]["error"], 2)
        self.assertEqual(rep["summary"]["profile"]["ok"], 2)

    def test_identity_check(self):
        d = {"AAA": {"isin": "INE000A01011"}, "BBB": {"isin": "INE000B01011"}, "CCC": {"isin": "INE000C01011"}}
        inst = {"AAA": {"isin": "INE000A01011"}, "BBB": {"isin": "INE999B01011"}}
        with mock.patch.object(uc, "load_instruments", return_value=inst):
            self.assertEqual(cp.identity_check(d, ["AAA", "BBB", "CCC"]), {"AAA": "match", "BBB": "isin_mismatch", "CCC": "missing_in_instruments"})
        with mock.patch.object(uc, "load_instruments", side_effect=SystemExit(1)):
            self.assertEqual(cp.identity_check(d, ["AAA"]), {"AAA": "instrument_file_unavailable"})

    def test_recorder_saves_only_the_response_body_under_the_probe_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "probe_output"
            api = mock.Mock(calls=1)
            api.get.return_value = ({"status": "success", "data": {"x": 1}}, None)
            rec = cp.Recorder(api, root)
            self.assertEqual(rec.get("INE000A01011/income-statement", {"type": "consolidated", "fs": "true"})[1], None)
            saved = [x for x in root.rglob("*") if x.is_file()]
            self.assertEqual(len(saved), 1)
            self.assertTrue(str(saved[0].resolve()).startswith(str(root.resolve())))
            self.assertNotIn("tok", saved[0].read_text())
            api.get.return_value = (None, "HTTP 500")
            rec.get("INE000A01011/profile")
            self.assertEqual(len([x for x in root.rglob("*") if x.is_file()]), 1)      # an error stores nothing


class SummaryTests(unittest.TestCase):
    REPORT = {
        "kind": "coverage_probe", "generated_on": "2026-10-14", "symbols": ["AAA", "BBB"], "shareholding": "not probed",
        "caps": {"fundamentals": 130, "candles": 16}, "calls": {"fundamentals": 15, "candles": 3}, "within_caps": True,
        "retries": {"fundamentals": {"logical_calls": 13, "retries": 2, "retried_calls": [{"call": "income-statement", "requests": 2}, {"call": "profile", "requests": 2}]},
                    "candles": {"logical_calls": 2, "retries": 1, "retried_calls": [{"call": "candles", "requests": 2}]}},
        "identity": {"AAA": "match", "BBB": "isin_mismatch"},
        "results": {
            "AAA": {"isin": "INE000A01011", "name": "A", "profile": {"status": "ok"}, "key_ratios": {"status": "partial", "missing": ["roce", "ev_ebitda"]},
                    "income": {"status": "ok"}, "balance_sheet": {"status": "ok"}, "cash_flow": {"status": "ok"},
                    "quarterly": {"status": "ok", "quarters": 4, "first": "2025-12-31", "latest": "2026-09-30", "year_ago_quarter_present": False, "basis": "consolidated"},
                    "candles": {"status": "ok", "candles": 123, "first": "2026-04-01", "last": "2026-10-13"}},
            "BBB": {"directory": "not_in_directory"}},
        "summary": {"key_ratios": {"ok": 0, "partial": 1, "no_data": 0, "rejected": 0, "error": 0, "skipped_call_cap": 0}},
    }

    def test_the_summary_shows_every_stock_status_identity_missing_fields_requests_retries_and_cap(self):
        md = cp.summary_markdown(self.REPORT)
        for needle in ("| AAA |", "| BBB |", "isin_mismatch", "roce, ev_ebitda", "WITHIN CAPS", "total 18 of 146", "| fundamentals | 15 | 130 | 2 | 13 |", "| candles | 3 | 16 | 1 | 2 |",
                       "retried fundamentals call: income-statement: 2 requests", "retried candles call: candles: 2 requests", "same-quarter-last-year=False", "candles=123", "not_in_directory"):
            self.assertIn(needle, md)

    def test_a_cap_breach_or_missing_flag_is_never_shown_as_compliant(self):
        self.assertIn("CAP EXCEEDED", cp.summary_markdown(dict(self.REPORT, within_caps=False)))
        bad = dict(self.REPORT)
        del bad["within_caps"]
        self.assertNotIn("WITHIN CAPS", cp.summary_markdown(bad))

    def test_markdown_cannot_be_broken_or_injected_by_report_text(self):
        r = json.loads(json.dumps(self.REPORT))
        r["results"]["AAA"]["income"] = {"status": "error", "error": "a|b\n<script>`x`"}
        row = [l for l in cp.summary_markdown(r).splitlines() if "script" in l or "a b" in l][0]
        self.assertNotIn("<", row)
        self.assertEqual(row.count("|"), 8)

    def test_an_unreadable_or_empty_report_still_gives_a_message(self):
        self.assertIn("Coverage probe", cp.summary_markdown({}))
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cp.summary_main(Path(d) / "nope.json"), 0)
        self.assertIn("No readable report.json", out.getvalue())

    def test_the_summary_never_contains_the_token(self):
        tok = "tok-SECRET-123456"
        with mock.patch.dict("os.environ", {"UPSTOX_ANALYTICS_TOKEN": tok}):
            self.assertNotIn(tok, cp.summary_markdown(self.REPORT))
        self.assertNotIn("environ", SRC[SRC.index("def summary_markdown"):SRC.index("def main")])

    def test_retries_are_counted_from_real_attempts(self):
        api = cp.make_fundamentals_api("tok-123456", 30)
        api.s = Session([Resp(500, {}), Resp(200, {"status": "success", "data": {}})])
        rec = cp.Recorder(api, None)
        with mock.patch("time.sleep"), tempfile.TemporaryDirectory() as d:
            rec.root = Path(d) / "probe_output"
            body, err = rec.get("INE0/profile")
        self.assertIsNone(err)
        self.assertEqual(rec.tally.as_dict(), {"logical_calls": 1, "retries": 1, "retried_calls": [{"call": "profile", "requests": 2}]})
        self.assertEqual(api.calls, 2)

    def test_a_call_refused_by_the_cap_counts_no_request_and_no_retry(self):
        api = uc.Upstox("tok-123456", 0)
        rec = cp.Recorder(api, None)
        rec.get("INE0/profile")
        self.assertEqual(rec.tally.as_dict(), {"logical_calls": 0, "retries": 0, "retried_calls": []})

    def test_the_run_report_carries_the_retry_tallies(self):
        rep, _, _ = RunTests().run_probe(["AAA"])
        self.assertEqual(rep["retries"]["fundamentals"], {"logical_calls": 0, "retries": 0, "retried_calls": []})
        self.assertEqual(rep["retries"]["candles"], {"logical_calls": 0, "retries": 0, "retried_calls": []})

    def test_workflow_writes_the_summary_from_the_report_only(self):
        self.assertIn('python coverage_probe.py summary >> "$GITHUB_STEP_SUMMARY"', WF)
        self.assertNotIn("::notice", WF)
        step = WF[WF.index("Show the full report"):WF.index("Save the report")]
        self.assertNotIn("secrets.", step)
        self.assertIn("if: always()", step)


class WorkflowTests(unittest.TestCase):
    def test_manual_only_read_only_and_isolated(self):
        self.assertIn("workflow_dispatch:", WF)
        for banned in ("schedule", "push:", "pull_request", "actions/cache", "upload-pages-artifact", "deploy-pages", "configure-pages", "contents: write", "pages: write", "id-token",
                       "git push", "git commit", "git add", "stocklens-ledger", "STOCKLENS_UNIVERSE_FILE", "save_ledger", "_MAX_CALLS", "gh api", "workflow_run"):
            self.assertNotIn(banned, WF, banned)
        self.assertEqual(WF.count("contents: read"), 1)
        self.assertIn("group: stocklens-coverage-probe", WF)
        self.assertRegex(WF, r"timeout-minutes: \d+")

    def test_the_token_reaches_only_the_probe_step_and_is_never_echoed(self):
        self.assertEqual(WF.count("secrets."), 1)
        self.assertEqual(len(re.findall(r"UPSTOX_ANALYTICS_TOKEN", WF)), 2)       # the env name and the secret it is read from, in one step
        step = WF[WF.index("- name: Run the coverage probe"):WF.index("- name: Save the report")]
        self.assertIn("secrets.UPSTOX_ANALYTICS_TOKEN", step)
        self.assertNotRegex(WF, r"echo[^\n]*(TOKEN|secrets)")

    def test_the_data_branch_is_checked_out_read_only_and_only_the_directory_is_used(self):
        self.assertIn("ref: stocklens-data", WF)
        self.assertIn("persist-credentials: false", WF)
        self.assertIn("cp ledger-branch/stock_directory.json out/stock_directory.json", WF)
        self.assertEqual(WF.count("ledger-branch/"), 1)

    def test_the_only_upload_is_the_probe_folder_for_seven_days(self):
        self.assertEqual(WF.count("actions/upload-artifact@v4"), 1)
        self.assertIn("path: probe_output", WF)
        self.assertIn("retention-days: 7", WF)

    def test_no_other_workflow_calls_the_probe_and_the_site_does_not_publish_it(self):
        for f in (HERE / ".github" / "workflows").glob("*.yml"):
            if f.name != "coverage_probe.yml":
                t = f.read_text(encoding="utf-8")
                self.assertNotIn("coverage_probe", t, f.name)
                self.assertNotIn("coverage-probe", t, f.name)


if __name__ == "__main__":
    unittest.main()
