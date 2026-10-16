"""
Why the site was stuck on 9 October, and the fix: tests. OFFLINE and SYNTHETIC: invented prices, a fake HTTP session, no network, no token, no workflow.

Proven cause (see the report): Upstox serves a trading day's daily candle only from about 07:00 IST the NEXT day, but history was fetched on the evening of the day (and, before 12 Oct,
only by hand), so the candle was never there when asked for; nothing asked again once it was, and only TCS was refreshed. These tests pin:
  * which session a run may expect (published_through), including weekends, the 07:30 boundary and the real 12/13 October times
  * NIFTY 50 and stock histories advancing incrementally (not by a full backfill) and a scheduled run refreshing every saved stock
  * failed, empty and partial downloads never replacing good data with less
  * a long-stale benchmark being a red ::error:: and a STALE headline, never a quiet "OK"
  * the publishing workflow publishing the saved files and the morning run that follows the history run
"""
import contextlib
import datetime as dt
import io
import json
import re
import unittest
from pathlib import Path
from unittest import mock

import historical_updater as U
import shards
import test_benchmark_freshness as T
from test_benchmark_freshness import MON, FRI, candle, raw, weekdays, Session

ROOT = Path(__file__).parent
IST = U.IST
TUE, WED = dt.date(2026, 10, 13), dt.date(2026, 10, 14)


def at(d, hh, mm):
    return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


class PublishedThrough(unittest.TestCase):
    def latest(self, now):
        with mock.patch.object(U, "now_ist", return_value=now):
            end = U.last_complete_day()
            return end, U.published_through(end, now), U.expected_latest_day(U.published_through(end, now))

    def test_the_real_times_of_the_incident(self):
        # Tue 13 Oct 19:19 IST: the scheduled run found 12 Oct but not 13 Oct. 13 Oct is not due yet, so nothing is stale.
        self.assertEqual(self.latest(at(TUE, 19, 19)), (TUE, MON, MON))
        # Mon 12 Oct 23:32 IST: the manual run got 9 Oct. 12 Oct was not due yet (Sunday falls back to Friday).
        self.assertEqual(self.latest(at(MON, 23, 32)), (MON, dt.date(2026, 10, 11), FRI))
        # Wed 14 Oct 08:00 IST: the morning run can expect 13 Oct.
        self.assertEqual(self.latest(at(WED, 8, 0)), (TUE, TUE, TUE))

    def test_a_days_candle_is_due_from_0730_the_next_morning_and_not_a_minute_before(self):
        self.assertEqual(self.latest(at(TUE, 7, 29))[2], FRI)       # end=Mon 12: due from Tue 07:30; before that the newest due is Friday's
        self.assertEqual(self.latest(at(TUE, 7, 30))[2], MON)
        self.assertEqual(self.latest(at(TUE, 15, 59))[2], MON)      # still the morning's end (hour < 16)
        self.assertEqual(self.latest(at(TUE, 16, 0))[2], MON)       # the run now asks for Tuesday, which is not due: Monday stays the expectation

    def test_weekends_and_mondays(self):
        sat, sun = dt.date(2026, 10, 10), dt.date(2026, 10, 11)
        self.assertEqual(self.latest(at(sat, 8, 0))[2], FRI)        # Friday's candle is due on Saturday morning
        self.assertEqual(self.latest(at(sat, 19, 0))[2], FRI)
        self.assertEqual(self.latest(at(sun, 12, 0))[2], FRI)
        self.assertEqual(self.latest(at(MON, 8, 0))[2], FRI)        # Monday morning: Monday's own candle is not due, Friday's is the newest

    def test_it_never_expects_more_than_was_asked_for(self):
        for hh in range(24):
            for d in (FRI, dt.date(2026, 10, 10), MON, TUE):
                end, pt, _ = self.latest(at(d, hh, 5))
                self.assertLessEqual(pt, end)
                self.assertGreaterEqual(pt, end - dt.timedelta(days=1))


class TwoStocks(T.Base):
    """The freshness harness, plus INFY (in the single file) and DOMS (own file only), and a clock."""

    def setUp(self):
        super().setUp()
        self.now = None
        eq = {s: {"isin": "INE%09d" % i, "name": s, "instrument_key": "NSE_EQ|" + s} for i, s in enumerate(("TCS", "INFY", "DOMS"))}
        for p in (mock.patch.object(U, "load_instrument_file", return_value=(eq, [{"instrument_key": "NSE_INDEX|Nifty 50", "name": "Nifty 50"}])),
                  mock.patch.object(U, "now_ist", side_effect=lambda: self.now or at(self.end + dt.timedelta(days=1), 9, 0))):
            p.start()
            self.addCleanup(p.stop)

    def seed_all(self, last, doms_last=None, infy_last=None):
        """Benchmark, TCS and INFY in the single file; DOMS only as its own file. Candles up to the given weekdays."""
        self.seed(last)
        frm = last - dt.timedelta(days=60)
        self.infy_old = [candle(d, 300.0) for d in weekdays(frm, infy_last or last)]
        self.doms_old = [candle(d, 700.0) for d in weekdays(frm, doms_last or last)]
        path = self.t / "out" / "historical.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["stocks"]["INFY"] = {"symbol": "INFY", "isin": "INE000000001", "instrument_key": "NSE_EQ|INFY", "candles": self.infy_old}
        path.write_text(json.dumps(doc), encoding="utf-8")
        shards.write_shard(self.t, "historical", "DOMS", {"updated": "2026-10-09", "source": "Upstox", "interval": "1day", "notes": {}},
                           {"symbol": "DOMS", "isin": "INE000000002", "instrument_key": "NSE_EQ|DOMS", "candles": self.doms_old})

    def answers(self, upto, **status):
        """Upstox serves candles up to `upto` for everything, unless a fragment is given another (status, rows)."""
        rows = lambda base: [raw(d, base) for d in weekdays(upto - dt.timedelta(days=25), upto)]
        Session.answers = {"Nifty%2050": (200, rows(500.0)), "NSE_EQ%7CTCS": (200, rows(100.0)), "NSE_EQ%7CINFY": (200, rows(300.0)), "NSE_EQ%7CDOMS": (200, rows(700.0))}
        Session.answers.update(status)

    def shard(self, sym):
        return shards.read_shard(self.t, "historical", sym)["stocks"][sym]["candles"]

    def dates(self, candles):
        return [c["date"] for c in candles]

    def run_refresh(self, flag=True):
        with mock.patch.dict(U.os.environ, {"HISTORICAL_REFRESH_SAVED": "true" if flag else ""}):
            return self.run_updater()


class Advancing(TwoStocks):
    def test_morning_run_advances_the_benchmark_and_every_saved_stock_to_the_newest_due_session(self):
        self.end = TUE                                  # Wed 08:00 IST: Tuesday's candle is due
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        self.answers(TUE)
        code, text, doc, summary = self.run_refresh()
        self.assertEqual(code, 0)
        self.assertEqual(doc["benchmark"]["candles"][-1]["date"], "2026-10-13")                         # NIFTY 50 advanced
        self.assertEqual(doc["stocks"]["TCS"]["candles"][-1]["date"], "2026-10-13")                     # the listed stock
        self.assertEqual(doc["stocks"]["INFY"]["candles"][-1]["date"], "2026-10-13")                    # a saved stock that was NOT listed
        self.assertEqual(self.shard("DOMS")[-1]["date"], "2026-10-13")                                  # a stock that only has its own file
        self.assertNotIn("freshness", text + summary)
        self.assertIn("### Historical data: OK", summary)
        self.assertEqual(doc["benchmark"]["candles"][:-1], self.bench_old)                              # everything older is untouched
        self.assertEqual(doc["errors"], [])

    def test_the_evening_run_is_not_called_stale_when_the_newest_candle_is_the_previous_day(self):
        self.end = TUE                                  # Tue 19:19 IST, the real scheduled run: Upstox has Monday, not Tuesday
        self.now = at(TUE, 19, 19)
        self.seed_all(FRI)
        self.answers(MON)
        code, text, doc, summary = self.run_refresh()
        self.assertEqual(doc["benchmark"]["candles"][-1]["date"], "2026-10-12")
        self.assertNotIn("freshness", text + summary)                                                   # this used to be a false warning every evening
        self.assertIn("### Historical data: OK", summary)

    def test_a_morning_run_that_finds_nothing_new_is_a_real_warning_naming_both_dates(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        self.answers(MON)                                                                               # Upstox has not published Tuesday yet
        code, text, doc, summary = self.run_refresh()
        self.assertEqual(code, 0)
        line = [x for x in text.splitlines() if "NIFTY 50 freshness" in x]
        self.assertEqual(len(line), 1)
        self.assertTrue(line[0].startswith("::warning::"))                                              # one session behind: a warning, not yet an error
        self.assertIn("2026-10-12", line[0])
        self.assertIn("2026-10-13", line[0])
        self.assertEqual(doc["benchmark"]["candles"], self.bench_old)                                   # nothing invented

    def test_updates_are_incremental_one_request_per_series_from_shortly_before_the_last_candle(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        self.answers(TUE)
        self.run_refresh()
        self.assertEqual(len(Session.log), 4)                                                           # NIFTY 50, TCS, INFY, DOMS: no full backfill
        for url in Session.log:
            frm = dt.date.fromisoformat(url.rstrip("/").rsplit("/", 1)[1])
            self.assertEqual(frm, MON - dt.timedelta(days=U.OVERLAP_DAYS))                              # resumes from the last saved candle minus the overlap

    def test_a_listed_run_without_the_flag_still_updates_only_what_it_listed(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        self.answers(TUE)
        code, text, doc, summary = self.run_refresh(flag=False)
        self.assertEqual(doc["stocks"]["TCS"]["candles"][-1]["date"], "2026-10-13")
        self.assertEqual(doc["stocks"]["INFY"]["candles"][-1]["date"], "2026-10-12")
        self.assertEqual(self.shard("DOMS")[-1]["date"], "2026-10-12")
        self.assertFalse([u for u in Session.log if "INFY" in u or "DOMS" in u])

    def test_stocks_that_were_never_saved_are_not_filled_by_a_scheduled_refresh(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        self.answers(TUE)
        with mock.patch.dict(U.os.environ, {"HISTORICAL_REFRESH_SAVED": "true"}):
            self.assertEqual(U.saved_symbols(), {"TCS", "INFY", "DOMS"})                                # exactly what is on file: the benchmark is not a stock, nothing else appears


class FailuresKeepGoodData(TwoStocks):
    def snapshot(self):
        """Every saved candle of every series (the single file is rewritten with its old content and an errors list, so bytes are compared only for the stock's own file)."""
        doc = json.loads((self.t / "out" / "historical.json").read_text(encoding="utf-8"))
        return {"bench": doc["benchmark"]["candles"], "stocks": {k: v["candles"] for k, v in doc["stocks"].items()},
                "doms": shards.shard_path(self.t, "historical", "DOMS").read_bytes()}

    def test_http_errors_and_empty_answers_never_replace_saved_candles(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        self.answers(TUE, **{"NSE_EQ%7CINFY": (500, None), "NSE_EQ%7CDOMS": (200, [])})                  # INFY fails; DOMS answers 200 with nothing at all
        code, text, doc, summary = self.run_refresh()
        self.assertEqual(code, 0)                                                                       # TCS and NIFTY 50 still saved
        self.assertEqual(doc["stocks"]["TCS"]["candles"][-1]["date"], "2026-10-13")
        self.assertEqual(doc["stocks"]["INFY"]["candles"], self.infy_old)                               # the failed stock keeps every candle
        self.assertEqual(self.shard("DOMS"), self.doms_old)                                             # the empty answer changed nothing
        self.assertEqual([e["symbol"] for e in doc["errors"]], ["INFY"])
        self.assertIn("PARTIAL - 1 problem(s)", summary)                                                # and the run says so

    def test_when_everything_fails_the_run_fails_and_no_saved_candle_changes(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        before = self.snapshot()
        self.answers(TUE, **{k: (500, None) for k in ("Nifty%2050", "NSE_EQ%7CTCS", "NSE_EQ%7CINFY", "NSE_EQ%7CDOMS")})
        with mock.patch.object(U.time, "sleep"):
            code, text, doc, summary = self.run_refresh()
        self.assertNotEqual(code, 0)                                                                    # a total failure is a failed step, not a green one
        self.assertEqual(self.snapshot(), before)

    def test_an_invalid_candle_is_dropped_and_a_zero_row_cannot_shrink_a_series(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(MON)
        bad = [raw(TUE, 500.0, open=0.0), raw(TUE, 500.0, high=1.0, low=9.0)]
        self.answers(TUE, **{"Nifty%2050": (200, bad)})
        code, text, doc, summary = self.run_refresh()
        self.assertEqual(doc["benchmark"]["candles"], self.bench_old)                                  # both rows rejected, the series is exactly as it was
        self.assertEqual(doc["stocks"]["TCS"]["candles"][-1]["date"], "2026-10-13")


class StaleIsNeverQuiet(TwoStocks):
    def test_a_benchmark_three_or_more_weekdays_behind_is_an_error_annotation_and_a_stale_headline(self):
        self.end = TUE
        self.now = at(WED, 8, 0)
        self.seed_all(dt.date(2026, 10, 7))                                                             # Wednesday 7 Oct: Thu, Fri, Mon, Tue are due
        self.answers(dt.date(2026, 10, 7))                                                              # and Upstox returns nothing newer
        code, text, doc, summary = self.run_refresh()
        self.assertEqual(code, 0)                                                                       # the good data is still saved (a failing step would discard the hand-over)
        self.assertTrue([x for x in text.splitlines() if x.startswith("::error::NIFTY 50 freshness")])
        self.assertIn("### Historical data: STALE", summary)
        self.assertNotIn("### Historical data: OK", summary)
        self.assertEqual(doc["benchmark"]["candles"], self.bench_old)

    def test_weekdays_behind_counts_weekdays_only(self):
        self.assertEqual(U.weekdays_behind([candle(FRI)], MON), 1)
        self.assertEqual(U.weekdays_behind([candle(FRI)], dt.date(2026, 10, 14)), 3)
        self.assertEqual(U.weekdays_behind([candle(MON)], MON), 0)
        self.assertEqual(U.weekdays_behind([candle(MON + dt.timedelta(days=1))], MON), 0)
        self.assertEqual(U.weekdays_behind([], MON), 0)
        self.assertEqual(U.weekdays_behind([candle(FRI)], dt.date(2026, 10, 11)), 0)                    # Sunday: Friday is current


class Publication(unittest.TestCase):
    U = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
    H = (ROOT / ".github" / "workflows" / "historical.yml").read_text(encoding="utf-8")

    def test_the_site_is_built_from_the_saved_history_on_the_data_branch_and_the_chart_files_are_the_ones_copied(self):
        build = self.U.split("- name: Build site folder")[1].split("- name: Upload Pages artifact")[0]
        self.assertIn("cp ledger-branch/historical/historical.json _site/out/historical.json", build)          # the single file the NIFTY chart and the dashboard read
        self.assertIn("cp ledger-branch/historical/shards/*.json _site/out/by_symbol/historical/", build)      # the per-stock files the detail charts read
        self.assertLess(build.index("cp out/*.json _site/out/"), build.index("ledger-branch/historical/historical.json"))   # the saved history is copied AFTER the cache copy, so it wins
        self.assertIn("ref: stocklens-data", self.U)                                                           # and is read from the data branch at the start of the build

    def test_the_morning_publish_exists_and_uses_the_same_build(self):
        self.assertEqual(re.findall(r'^\s+- cron: "([^"]+)"', self.U, re.M), ["15 15 * * 1-5", "30 4 * * 2-6"])
        self.assertEqual(self.U.count("name: Build site folder"), 1)                                           # one build, two triggers: the morning run cannot publish anything different
        self.assertIn("uses: actions/deploy-pages@v4", self.U)

    def test_history_is_saved_only_through_the_checked_hand_over_and_only_to_historical(self):
        self.assertIn("uses: ./.github/workflows/save_historical.yml", self.H)
        self.assertIn("if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'", self.H)
        self.assertEqual(self.H.count("contents: write"), 1)


class Untouched(unittest.TestCase):
    def test_the_gates_and_the_unrelated_files_are_not_part_of_this_fix(self):
        import subprocess
        r = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("not a git checkout")
        import guard_update_yml
        changed = {f for f in r.stdout.split() if not guard_update_yml.is_approved(ROOT, f)}      # a later approved change (tests/approved_workflow_changes.json) is not part of this fix
        for protected in (".gitignore", ".github/workflows/market_data.yml", "render.yaml", "live_public/__main__.py", "live_public/config.py", "index_data_updater.py",
                          "quarterly_results_updater.py", "sensex_publish.py", "sector_publish.py", "nse_updater.py"):
            self.assertNotIn(protected, changed, protected)
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        if "PUBLIC_LIVE" in html:      # present only where the live-data work has landed: it must stay switched off
            self.assertIn('var PUBLIC_LIVE={enabled:false,url:""}', html)


if __name__ == "__main__":
    unittest.main()
