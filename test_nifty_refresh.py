"""Tests for the automatic NIFTY 50 benchmark refresh: the weekday schedule of "Update historical price data", its timing, and what the updater keeps."""
import datetime as dt
import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path

import guard_update_yml as G
import historical_updater as hu

ROOT = Path(__file__).parent
WF = ROOT / ".github" / "workflows"
H = (WF / "historical.yml").read_text(encoding="utf-8")
U = (WF / "update.yml").read_text(encoding="utf-8")


def cron_of(text):
    m = re.findall(r'^\s+- cron: "([^"]+)"', text, re.M)
    return m


class Schedule(unittest.TestCase):
    def test_there_is_one_weekday_schedule_after_the_close_and_before_the_site_build(self):
        c = cron_of(H)
        self.assertEqual(len(c), 1)
        minute, hour, dom, mon, dow = c[0].split()
        self.assertEqual((dom, mon, dow), ("*", "*", "1-5"))                         # weekdays only
        utc = int(hour) * 60 + int(minute)
        ist = utc + 330
        self.assertGreaterEqual(ist, 16 * 60 + 30)                                   # after the updater's 16:00 IST "completed trading day" cut-off, with margin
        site = cron_of(U)
        self.assertEqual(len(site), 1)
        s_minute, s_hour = site[0].split()[:2]
        self.assertLess(utc + 60, int(s_hour) * 60 + int(s_minute))                  # at least an hour before "Update data and publish site" copies the saved files

    def test_the_updater_cut_off_matches_the_schedule(self):
        src = (ROOT / "historical_updater.py").read_text(encoding="utf-8")
        self.assertIn("now.hour >= 16", src)                                         # the hour the schedule is measured against

    def test_a_scheduled_run_is_the_default_manual_run_not_a_batch_and_not_a_wider_universe(self):
        self.assertIn('HISTORICAL_SYMBOLS: "${{ env.CHOSEN_SYMBOLS || (github.event_name == \'push\' && \'TCS\' || inputs.symbols || \'TCS\') }}"', H)
        for name in ("Choose the next batch of stocks", "Record which stocks gained candles"):
            seg = H.split("- name: " + name)[1].split("\n      - name:")[0]
            self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.batch_size != ''", seg)       # a schedule can never pick a batch

    def test_a_push_still_saves_nothing_and_only_the_save_job_can_write(self):
        self.assertEqual(H.count("contents: write"), 1)
        self.assertNotIn("UPSTOX", H.split("  save-historical:")[1])
        for name in ("Check the result and prepare the hand-over", "Hand the result to the save job"):
            seg = H.split("- name: " + name)[1].split("\n      - name:")[0].split("\n  save-historical")[0]
            self.assertIn("github.event_name == 'schedule'", seg)
            self.assertNotIn("== 'push'", seg)
        self.assertEqual(H.count("push:\n    branches:\n      - phase1"), 1)

    def test_the_site_build_is_untouched_by_this_change(self):
        self.assertIn("cp ledger-branch/historical/historical.json _site/out/historical.json", U)
        r = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", ".github/workflows/update.yml"], cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode == 0:
            self.assertEqual([f for f in r.stdout.split() if not G.is_approved(ROOT, f)], [])

    def test_the_changed_workflow_is_exactly_the_pinned_version(self):
        pin = json.loads((ROOT / "tests" / "approved_workflow_changes.json").read_text(encoding="utf-8"))["pinned_files"][".github/workflows/historical.yml"]
        self.assertEqual(hashlib.sha256((WF / "historical.yml").read_bytes()).hexdigest(), pin)
        self.assertTrue(G.is_approved(ROOT, ".github/workflows/historical.yml"))
        self.assertTrue(G.is_approved(ROOT, ".github/workflows/save_historical.yml"))       # unchanged


class Benchmark(unittest.TestCase):
    def candles(self, start, n):
        d = dt.date.fromisoformat(start)
        out = []
        for i in range(n):
            out.append({"date": (d + dt.timedelta(days=i)).isoformat(), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0 + i, "volume": 0})
        return out

    def test_a_refresh_adds_the_new_session_and_keeps_every_old_candle(self):
        old = self.candles("2026-09-01", 39)                                          # ends 2026-10-09
        new = [dict(self.candles("2026-10-09", 1)[0], close=22520.45), {"date": "2026-10-12", "open": 22500.0, "high": 22600.0, "low": 22400.0, "close": 22555.5, "volume": 0}]
        merged = hu.merge(old, new)
        self.assertEqual(merged[-1]["date"], "2026-10-12")
        self.assertEqual(merged[-1]["close"], 22555.5)
        self.assertEqual(len(merged), len(old) + 1)                                   # nothing dropped, only 12 Oct added
        self.assertEqual([c["date"] for c in merged], sorted(c["date"] for c in merged))
        self.assertEqual({c["date"]: c for c in merged if c["date"] < "2026-10-09"}, {c["date"]: c for c in old if c["date"] < "2026-10-09"})

    def test_a_failed_refresh_keeps_the_old_benchmark_and_no_equity_price_is_substituted(self):
        src = (ROOT / "historical_updater.py").read_text(encoding="utf-8")
        self.assertIn("keep the last good benchmark", src)
        self.assertIn("NSE_INDEX", src)                                               # the benchmark key comes from the index segment, never from an equity
        self.assertEqual(hu.BENCHMARK_SYMBOL, "NIFTY 50")

    def test_an_incomplete_day_is_not_fetched_before_the_close(self):
        class FakeDT(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 12, 15, 0, tzinfo=tz)
        real = hu.dt.datetime
        hu.dt.datetime = FakeDT
        try:
            self.assertEqual(hu.last_complete_day(), dt.date(2026, 10, 11))
            FakeDT.now = classmethod(lambda cls, tz=None: cls(2026, 10, 12, 19, 0, tzinfo=tz))
            self.assertEqual(hu.last_complete_day(), dt.date(2026, 10, 12))          # at 19:00 IST the day is complete
        finally:
            hu.dt.datetime = real


class StaleNoticeGuard(unittest.TestCase):
    NOW = (ROOT / "index.html").read_text(encoding="utf-8")

    def head(self):
        return subprocess.run(["git", "show", "HEAD:index.html"], cwd=str(ROOT), capture_output=True).stdout.decode("utf-8")

    def test_round_trip_to_the_committed_page_and_no_op_without_the_notice(self):
        h = self.head()
        if not h:
            self.skipTest("not a git checkout")
        self.assertIn("Stale: this NIFTY 50 value is from", self.NOW)
        r = G.without_nifty_stale_notice(self.NOW)
        self.assertNotIn("Stale: this NIFTY 50 value is from", r)
        self.assertEqual(G.without_nifty_stale_notice(r), r)                          # a page without the edit is returned unchanged
        self.assertEqual(G.without_market_cap(self.NOW), G.without_market_cap(r))     # the whole guard chain accepts the edit

    def test_a_partial_or_duplicated_edit_fails_loudly(self):
        with self.assertRaises(ValueError):
            G.without_nifty_stale_notice(self.NOW.replace("is not current.</p>", "is current.</p>", 1))
        with self.assertRaises(ValueError):
            G.without_nifty_stale_notice(self.NOW + G._STALE_NEW)
        with self.assertRaises(ValueError):
            G.without_nifty_stale_notice(self.NOW.replace("Stale: this NIFTY 50 value is from", "Stale: this NIFTY 50 value was from", 1))

    def test_any_other_page_change_is_still_detected(self):
        h = self.head()
        if not h:
            self.skipTest("not a git checkout")
        mutated = self.NOW.replace("NIFTY 50 history is not loaded", "NIFTY 50 history is gone", 1)
        self.assertNotEqual(G.without_market_cap(mutated), G.without_market_cap(h))


if __name__ == "__main__":
    unittest.main()
