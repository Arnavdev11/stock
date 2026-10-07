"""
Tests for Phase 5K: market_derive.py, market_data_updater.py and validate_market_outputs.py.
Run:  python3 -m unittest test_market_data -v      (no network; every file is built in a temporary folder)

The tests document the output schema: the shape checks in SchemaTests are the contract the front end can rely on.
"""
import copy
import csv
import datetime as dt
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import market_data_updater as mu
import market_derive as md
import validate_market_outputs as vmo

IST = mu.IST
END = dt.date(2026, 10, 5)                       # a Monday
NOW = dt.datetime(2026, 10, 6, 9, 0, tzinfo=IST)  # Tuesday morning: expected latest trading day = Monday 2026-10-05
HEADER = ["SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE", "AVG_PRICE", "TTL_TRD_QNTY",
          "TURNOVER_LACS", "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER"]
SMALL = dict(mu.GATE, min_universe=10, min_eq_rows_per_file=10)     # the real gate wants well over 1,000 stocks; the test world is small


def weekdays(end, n):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out[::-1]


def r2(x):
    return round(x + 1e-9, 2)


class World:
    """A small, fully known market: closes, volumes and previous closes are computed here, independently of the code under test."""

    def __init__(self, n_days=300, fillers=30, end=END):
        self.dates = weekdays(end, n_days)
        self.n = n_days
        self.close, self.vol, self.prev, self.deliv = {}, {}, {}, {}
        self.rows_override = {}
        for k in range(fillers):
            s = "F%03d" % k
            self.close[s] = [r2(100 + k + (0.01 * i if k % 2 == 0 else -0.01 * i)) for i in range(n_days)]
            self.vol[s] = [200000 + 1000 * k] * n_days
        self.close["UP"] = [r2(100 * 1.002 ** i) for i in range(n_days)]
        self.close["UP"][-1] = r2(self.close["UP"][-2] * 1.06)                       # +6% on the last day: crosses its 52-week high
        self.vol["UP"] = [200000] * (n_days - 1) + [1000000]                           # 5x its own average
        self.close["DOWN"] = [r2(300 * 0.998 ** i) for i in range(n_days)]
        self.close["DOWN"][-1] = r2(self.close["DOWN"][-2] * 0.94)                   # -6%: crosses its 52-week low
        self.vol["DOWN"] = [200000] * (n_days - 1) + [800000]                          # 4x
        self.close["UNCH"] = [150.0] * n_days
        self.vol["UNCH"] = [300000] * n_days
        self.close["ZVOL"] = [r2(120 + 0.01 * i) for i in range(n_days)]
        self.vol["ZVOL"] = [100000] * (n_days - 1) + [0]
        self.close["TINY"] = [r2(10 + 0.01 * i) for i in range(n_days)]                  # an illiquid stock: well under Rs 1 crore a day
        self.close["TINY"][-1] = r2(self.close["TINY"][-2] * 1.15)                      # +15%, but not liquid
        self.vol["TINY"] = [500] * n_days
        self.start = {}                                                                   # first day index on which a symbol exists
        self.close["SHORT"] = [r2(80 + 0.1 * i) for i in range(n_days)]
        self.vol["SHORT"] = [150000] * n_days
        self.start["SHORT"] = max(0, n_days - 30)                                                 # listed 30 trading days ago
        self.close["SPLIT"] = [r2(400 + 0.05 * i) for i in range(n_days)]
        self.split_at = n_days - 100 if n_days > 120 else None
        for i in range(self.split_at or n_days, n_days):
            self.close["SPLIT"][i] = r2(self.close["SPLIT"][i] / 2)                       # 1:2 split 100 days ago
        self.vol["SPLIT"] = [200000] * n_days
        self.refresh_prev()

    def refresh_prev(self):
        for s, c in self.close.items():
            p = [None] + c[:-1]
            if s == "SPLIT" and self.split_at is not None:
                p[self.split_at] = r2(c[self.split_at - 1] / 2)                           # NSE adjusts the reference price on the ex-date
            self.prev[s] = p

    def symbols(self):
        return list(self.close)

    def row(self, s, i):
        c = self.close[s][i]
        v = self.vol[s][i]
        p = self.prev[s][i] if self.start.get(s, 0) < i else None
        if self.start.get(s, 0) == i:
            p = r2(c * 0.99)
        d = self.deliv.get(s, 45.5)
        return [s, "EQ", self.dates[i].strftime("%d-%b-%Y"), "" if p is None else p, c, r2(c * 1.01), r2(c * 0.99), c, c, c, v, r2(c * v / 1e5), 1000, int(v * 0.4), d]

    def write(self, root, only=None, header=HEADER):
        raw = Path(root) / "data" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        for i, d in enumerate(self.dates):
            if only is not None and i not in only:
                continue
            rows = [self.row(s, i) for s in self.symbols() if i >= self.start.get(s, 0)]
            rows = self.rows_override.get(i, rows)
            with open(raw / ("bhav_%s.csv" % d.strftime("%Y%m%d")), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(header)
                for r in rows:
                    w.writerow(r)
        return raw


class Tmp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def pipeline(self, world, now=NOW, cfg=SMALL):
        world.write(self.root)
        days, reports, counts = mu.load_days(self.root / "data" / "raw", now.date(), cfg)
        dq, latest = mu.assess(days, reports, counts, now, set(), cfg)
        return days, reports, counts, dq, latest

    def docs(self, world=None, now=NOW):
        world = world or World()
        days, reports, counts, dq, latest = self.pipeline(world, now)
        self.assertEqual(dq["status"], "pass", dq["errors"])
        return mu.compute(days, latest, dq, now, md.DEFAULTS, set()), world


def series_of(world, s, upto=None):
    out = []
    for i in range(world.start.get(s, 0), (upto if upto is not None else world.n)):
        r = world.row(s, i)
        out.append({"symbol": s, "date": world.dates[i].isoformat(), "prev_close": r[3] or None, "open": r[4], "high": r[5], "low": r[6], "close": r[8], "volume": r[10],
                    "turnover": r[11], "deliv": r[14]})
    return out


# =====================================================================================================================================
class ChangeAndBreadth(unittest.TestCase):
    def test_change(self):
        self.assertEqual(md.change_of({"close": 110, "prev_close": 100}), (10.0, 10.0))
        self.assertEqual(md.change_of({"close": 90, "prev_close": 100}), (-10.0, -10.0))

    def test_change_missing_or_invalid_previous_close(self):
        for p in (None, 0, -5):
            self.assertEqual(md.change_of({"close": 10, "prev_close": p}), (None, None))
        self.assertEqual(md.change_of({"close": None, "prev_close": 10}), (None, None))
        self.assertEqual(md.change_of({"close": 0, "prev_close": 10}), (None, None))

    def test_breadth_counts(self):
        rows = [{"close": 11, "prev_close": 10}, {"close": 9, "prev_close": 10}, {"close": 10, "prev_close": 10}, {"close": 12, "prev_close": 10}, {"close": 5, "prev_close": None}]
        b = md.breadth(rows)
        self.assertEqual((b["advancing"], b["declining"], b["unchanged"], b["counted"]), (2, 1, 1, 4))
        self.assertEqual(b["ad_ratio"], 2.0)

    def test_ad_ratio_never_divides_by_zero(self):
        b = md.breadth([{"close": 11, "prev_close": 10}, {"close": 10, "prev_close": 10}])
        self.assertIsNone(b["ad_ratio"])
        self.assertIn("no declining", b["ad_ratio_note"])

    def test_breadth_empty(self):
        b = md.breadth([])
        self.assertEqual((b["advancing"], b["declining"], b["unchanged"], b["counted"], b["ad_ratio"]), (0, 0, 0, 0, None))
        self.assertIn("note", "note" if b.get("ad_ratio_note") else "")

    def test_breadth_ignores_rows_without_valid_previous_close(self):
        self.assertEqual(md.breadth([{"close": 5, "prev_close": None}])["counted"], 0)


class Dma(unittest.TestCase):
    def S(self, closes, start="2026-01-01"):
        d0 = dt.date.fromisoformat(start)
        return [{"symbol": "X", "date": (d0 + dt.timedelta(days=i)).isoformat(), "close": c, "prev_close": closes[i - 1] if i else None} for i, c in enumerate(closes)]

    def test_average_value_and_observations(self):
        s = self.S([float(i) for i in range(1, 31)])
        d = md.dma_for(s, 20, [], md.DEFAULTS)
        self.assertEqual(d["status"], "safe")
        self.assertEqual(d["observations"], 20)
        self.assertEqual(d["value"], round(sum(range(11, 31)) / 20, 2))
        self.assertEqual(d["close"], 30.0)
        self.assertEqual(d["calc_date"], s[-1]["date"])
        self.assertEqual(d["position"], "above")

    def test_fewer_than_period_is_insufficient_not_a_short_average(self):
        for period, n in ((20, 19), (50, 49), (200, 199)):
            d = md.dma_for(self.S([10.0] * n), period, [], md.DEFAULTS)
            self.assertEqual(d["status"], "insufficient_history", period)
            self.assertIsNone(d["value"])
            self.assertEqual(d["observations"], n)

    def test_exact_period_is_enough(self):
        for period in (20, 50, 200):
            self.assertEqual(md.dma_for(self.S([10.0] * period), period, [], md.DEFAULTS)["status"], "safe")

    def test_observations_too_far_apart(self):
        d0 = dt.date(2025, 1, 1)
        s = [{"symbol": "X", "date": (d0 + dt.timedelta(days=30 * i)).isoformat(), "close": 10.0, "prev_close": 10.0} for i in range(20)]
        self.assertEqual(md.dma_for(s, 20, [], md.DEFAULTS)["status"], "insufficient_history")

    def test_event_inside_window_requires_adjustment(self):
        s = self.S([10.0] * 30)
        ev = [{"date": s[20]["date"], "ratio": 0.5}]
        self.assertEqual(md.dma_for(s, 20, ev, md.DEFAULTS)["status"], "requires_adjustment")
        self.assertEqual(md.dma_for(s, 5, ev, md.DEFAULTS)["status"], "safe")          # the 5-day window starts after the event

    def test_event_on_first_day_of_window_is_safe(self):
        s = self.S([10.0] * 30)
        self.assertEqual(md.dma_for(s, 10, [{"date": s[20]["date"], "ratio": 0.5}], md.DEFAULTS)["status"], "safe")

    def test_position(self):
        s = self.S([10.0] * 19 + [5.0])
        self.assertEqual(md.dma_for(s, 20, [], md.DEFAULTS)["position"], "below")
        self.assertEqual(md.dma_for(self.S([10.0] * 20), 20, [], md.DEFAULTS)["position"], "equal")

    def test_breadth_denominator_excludes_missing_history(self):
        per = {"A": {20: {"status": "safe", "position": "above"}}, "B": {20: {"status": "safe", "position": "below"}},
               "C": {20: {"status": "insufficient_history", "position": None}}, "D": {20: {"status": "requires_adjustment", "position": None}}, "E": {}}
        b = md.dma_breadth(per, 20, ["A", "B", "C", "D", "E"])
        self.assertEqual((b["numerator"], b["denominator"], b["pct"]), (1, 2, 50.0))
        self.assertEqual(b["excluded"], {"insufficient_history": 2, "requires_adjustment": 1})
        self.assertEqual(b["status"], "safe")

    def test_breadth_all_insufficient(self):
        b = md.dma_breadth({"C": {200: {"status": "insufficient_history"}}}, 200, ["C"])
        self.assertEqual((b["denominator"], b["pct"], b["status"]), (0, None, "insufficient_history"))

    def test_breadth_percentage_in_range(self):
        per = {s: {20: {"status": "safe", "position": "above"}} for s in "ABC"}
        self.assertEqual(md.dma_breadth(per, 20, list("ABC"))["pct"], 100.0)


class HighLow(unittest.TestCase):
    def S(self, n, close=lambda i: 100.0, hi=lambda i: 101.0, lo=lambda i: 99.0):
        d0 = dt.date(2025, 1, 1)
        return [{"symbol": "X", "date": (d0 + dt.timedelta(days=i)).isoformat(), "close": close(i), "high": hi(i), "low": lo(i), "prev_close": close(i - 1) if i else None} for i in range(n)]

    def test_new_high_and_prior_date(self):
        s = self.S(300, close=lambda i: 105.0 if i == 299 else 100.0, hi=lambda i: 110.0 if i == 150 else 101.0)
        s[-1]["close"] = 111.0
        r = md.high_low_52w(s, [], md.DEFAULTS)
        self.assertEqual(r["status"], "safe")
        self.assertEqual(r["high"]["state"], "new_high")
        self.assertEqual(r["high"]["prior_extreme"], 110.0)
        self.assertEqual(r["high"]["prior_extreme_date"], s[150]["date"])
        self.assertEqual(r["high"]["distance_pct"], round((111 / 110 - 1) * 100, 2))

    def test_equal_to_prior_high_is_not_new(self):
        s = self.S(300, hi=lambda i: 110.0 if i == 150 else 101.0)
        s[-1]["close"] = 110.0
        self.assertEqual(md.high_low_52w(s, [], md.DEFAULTS)["high"]["state"], "near_high")

    def test_near_and_away(self):
        s = self.S(300, hi=lambda i: 110.0 if i == 150 else 101.0)
        s[-1]["close"] = 108.5          # within 2% below 110
        self.assertEqual(md.high_low_52w(s, [], md.DEFAULTS)["high"]["state"], "near_high")
        s[-1]["close"] = 100.0
        self.assertEqual(md.high_low_52w(s, [], md.DEFAULTS)["high"]["state"], "away")

    def test_new_low_and_near_low(self):
        s = self.S(300, lo=lambda i: 90.0 if i == 120 else 99.0)
        s[-1]["close"] = 89.0
        r = md.high_low_52w(s, [], md.DEFAULTS)
        self.assertEqual((r["low"]["state"], r["low"]["prior_extreme"], r["low"]["prior_extreme_date"]), ("new_low", 90.0, s[120]["date"]))
        s[-1]["close"] = 91.0
        self.assertEqual(md.high_low_52w(s, [], md.DEFAULTS)["low"]["state"], "near_low")

    def test_equal_to_prior_low_is_not_new(self):
        s = self.S(300, lo=lambda i: 90.0 if i == 120 else 99.0)
        s[-1]["close"] = 90.0
        r = md.high_low_52w(s, [], md.DEFAULTS)
        self.assertEqual(r["low"]["state"], "near_low")
        self.assertEqual(r["low"]["distance_pct"], 0.0)

    def test_latest_day_is_not_in_its_own_window(self):
        s = self.S(300)
        s[-1]["high"], s[-1]["close"] = 200.0, 150.0
        r = md.high_low_52w(s, [], md.DEFAULTS)
        self.assertEqual(r["high"]["prior_extreme"], 101.0)
        self.assertEqual(r["high"]["state"], "new_high")

    def test_exactly_252_prior_days_needed(self):
        self.assertEqual(md.high_low_52w(self.S(252), [], md.DEFAULTS)["status"], "insufficient_history")
        self.assertEqual(md.high_low_52w(self.S(253), [], md.DEFAULTS)["status"], "safe")

    def test_missing_history(self):
        self.assertEqual(md.high_low_52w(self.S(30), [], md.DEFAULTS)["status"], "insufficient_history")
        r = md.high_low_52w(self.S(1), [], md.DEFAULTS)
        self.assertEqual((r["status"], r["high"], r["low"]), ("insufficient_history", None, None))

    def test_window_must_fit_in_calendar_span(self):
        d0 = dt.date(2020, 1, 1)
        s = [{"symbol": "X", "date": (d0 + dt.timedelta(days=5 * i)).isoformat(), "close": 100.0, "high": 101.0, "low": 99.0, "prev_close": 100.0} for i in range(260)]
        self.assertEqual(md.high_low_52w(s, [], md.DEFAULTS)["status"], "insufficient_history")

    def test_event_in_window(self):
        s = self.S(300)
        self.assertEqual(md.high_low_52w(s, [{"date": s[200]["date"], "ratio": 0.5}], md.DEFAULTS)["status"], "requires_adjustment")
        self.assertEqual(md.high_low_52w(s, [{"date": s[10]["date"], "ratio": 0.5}], md.DEFAULTS)["status"], "safe")   # long before the window

    def test_missing_low_column_is_unavailable_not_guessed(self):
        s = self.S(300, lo=lambda i: None)
        r = md.high_low_52w(s, [], md.DEFAULTS)
        self.assertEqual(r["low"]["status"], "unavailable")
        self.assertEqual(r["high"]["status"], "safe")


class VolumeStats(unittest.TestCase):
    def S(self, vols, start=dt.date(2026, 1, 1)):
        return [{"symbol": "X", "date": (start + dt.timedelta(days=i)).isoformat(), "volume": v, "close": 10.0, "prev_close": 10.0} for i, v in enumerate(vols)]

    def test_multiple_against_previous_20_days(self):
        s = self.S([100] * 20 + [350])
        v = md.volume_stats(s, [], md.DEFAULTS)
        self.assertEqual((v["status"], v["avg_volume"], v["multiple"], v["volume"]), ("safe", 100, 3.5, 350))

    def test_latest_day_not_in_its_own_baseline(self):
        s = self.S([100] * 20 + [10000])
        self.assertEqual(md.volume_stats(s, [], md.DEFAULTS)["avg_volume"], 100)

    def test_fewer_than_20_prior_days(self):
        self.assertEqual(md.volume_stats(self.S([100] * 20), [], md.DEFAULTS)["status"], "insufficient_history")

    def test_zero_baseline(self):
        v = md.volume_stats(self.S([0] * 20 + [500]), [], md.DEFAULTS)
        self.assertEqual((v["status"], v["multiple"]), ("unavailable", None))

    def test_zero_volume_today(self):
        v = md.volume_stats(self.S([100] * 20 + [0]), [], md.DEFAULTS)
        self.assertEqual((v["status"], v["multiple"]), ("safe", 0.0))

    def test_missing_volume_in_baseline(self):
        s = self.S([100] * 20 + [500])
        s[5]["volume"] = None
        self.assertEqual(md.volume_stats(s, [], md.DEFAULTS)["status"], "unavailable")

    def test_corporate_action_inside_baseline(self):
        s = self.S([100] * 21)
        self.assertEqual(md.volume_stats(s, [{"date": s[10]["date"], "ratio": 0.5}], md.DEFAULTS)["status"], "requires_adjustment")

    def test_lists_and_thresholds(self):
        rows, vol = [], {}
        spec = {"A": (2.0, 3.0, 500), "B": (1.99, 9.0, 500), "C": (5.0, 2.99, 500), "D": (4.0, -3.0, 500), "E": (3.0, -2.9, 500), "F": (9.0, 5.0, 50), "G": (6.0, 4.0, 500)}
        for s, (m, pct, tov) in spec.items():
            rows.append({"symbol": s, "close": 100 * (1 + pct / 100), "prev_close": 100.0, "volume": 1000, "turnover": tov, "deliv": 50.0})
            vol[s] = {"status": "safe", "multiple": m, "avg_volume": 500}
        vol["G"] = {"status": "insufficient_history", "multiple": None, "avg_volume": None}
        out = md.volume_lists(rows, vol, md.DEFAULTS)
        self.assertEqual([r["symbol"] for r in out["shockers"]], ["C", "D", "E", "A"])       # by multiple; B below 2x, F illiquid, G no baseline
        self.assertEqual([r["symbol"] for r in out["high_volume_high_gain"]], ["A"])         # gain >= 3%: A exactly 3.0 qualifies, C 2.99 does not
        self.assertEqual([r["symbol"] for r in out["high_volume_top_losers"]], ["D"])        # loss >= 3%: D exactly -3.0 qualifies, E -2.9 does not
        self.assertEqual([r["rank"] for r in out["shockers"]], [1, 2, 3, 4])


class Rankings(unittest.TestCase):
    ROWS = [{"symbol": "A", "close": 10, "prev_close": 9, "volume": 500, "turnover": 5.0, "deliv": 40.0}, {"symbol": "B", "close": 20, "prev_close": 21, "volume": 100, "turnover": 90.0, "deliv": None},
            {"symbol": "C", "close": 30, "prev_close": 30, "volume": 900, "turnover": 70.0, "deliv": 80.0}, {"symbol": "D", "close": 40, "prev_close": 36, "volume": 0, "turnover": 0.0, "deliv": 10.0},
            {"symbol": "E", "close": 50, "prev_close": 45, "volume": None, "turnover": None, "deliv": None}]

    def test_most_active_value_and_volume_are_different_rankings(self):
        m = md.most_active(self.ROWS, md.DEFAULTS)
        self.assertEqual([r["symbol"] for r in m["by_value"]], ["B", "C", "A"])
        self.assertEqual([r["symbol"] for r in m["by_volume"]], ["C", "A", "B"])
        self.assertEqual([r["rank"] for r in m["by_value"]], [1, 2, 3])
        self.assertEqual(m["by_value"][0]["turnover_lakhs"], 90.0)
        self.assertEqual(m["by_volume"][0]["volume"], 900)

    def test_most_active_zero_and_missing_excluded(self):
        syms = {r["symbol"] for k in md.most_active(self.ROWS, md.DEFAULTS).values() for r in k}
        self.assertNotIn("D", syms)
        self.assertNotIn("E", syms)

    def test_most_active_tie_broken_by_symbol_and_capped(self):
        rows = [{"symbol": s, "close": 1, "prev_close": 1, "volume": 10, "turnover": 5.0} for s in "CBA"]
        m = md.most_active(rows, dict(md.DEFAULTS, rows_active=2))
        self.assertEqual([r["symbol"] for r in m["by_value"]], ["A", "B"])

    def test_most_active_has_no_liquidity_filter(self):
        m = md.most_active([{"symbol": "T", "close": 1, "prev_close": 1, "volume": 10, "turnover": 0.5}], md.DEFAULTS)
        self.assertEqual(len(m["by_value"]), 1)

    def test_movers_liquid_only_and_ranked(self):
        rows = [{"symbol": "G1", "close": 110, "prev_close": 100, "volume": 5, "turnover": 150.0, "deliv": 30.0}, {"symbol": "G2", "close": 105, "prev_close": 100, "volume": 5, "turnover": 150.0},
                {"symbol": "ILL", "close": 150, "prev_close": 100, "volume": 5, "turnover": 10.0}, {"symbol": "L1", "close": 80, "prev_close": 100, "volume": 5, "turnover": 150.0},
                {"symbol": "L2", "close": 95, "prev_close": 100, "volume": 5, "turnover": 100.0}, {"symbol": "NOPREV", "close": 95, "prev_close": None, "volume": 5, "turnover": 500.0},
                {"symbol": "FLAT", "close": 100, "prev_close": 100, "volume": 5, "turnover": 500.0}]
        m = md.movers(rows, md.DEFAULTS)
        self.assertEqual([r["symbol"] for r in m["gainers"]], ["G1", "G2"])
        self.assertEqual([r["symbol"] for r in m["losers"]], ["L1", "L2"])      # L2 has turnover exactly at the cut-off: included
        g = m["gainers"][0]
        self.assertEqual((g["close"], g["prev_close"], g["change"], g["change_pct"], g["volume"], g["turnover_lakhs"], g["delivery_pct"], g["rank"]), (110, 100, 10.0, 10.0, 5, 150.0, 30.0, 1))
        self.assertEqual(m["candidates"], 5)

    def test_movers_capped(self):
        rows = [{"symbol": "S%03d" % i, "close": 100 + i + 1, "prev_close": 100, "volume": 1, "turnover": 500.0} for i in range(80)]
        self.assertEqual(len(md.movers(rows, md.DEFAULTS)["gainers"]), 50)


class Corporate(unittest.TestCase):
    def test_events(self):
        d0 = dt.date(2026, 1, 1)
        s = [{"symbol": "X", "date": (d0 + dt.timedelta(days=i)).isoformat(), "close": c, "prev_close": p} for i, (c, p) in enumerate([(100, None), (101, 100), (50.5, 50.5), (51, 50.5), (52, 100)])]
        ev = md.adjustment_events(s, md.DEFAULTS)
        self.assertEqual([(e["date"], e["ratio"]) for e in ev], [(s[2]["date"], 0.5), (s[4]["date"], 1.9608)])

    def test_small_dividend_adjustment_is_not_flagged(self):
        s = [{"symbol": "X", "date": "2026-01-01", "close": 100.0, "prev_close": None}, {"symbol": "X", "date": "2026-01-02", "close": 99.0, "prev_close": 98.0}]
        self.assertEqual(md.adjustment_events(s, md.DEFAULTS), [])


# =====================================================================================================================================
class Parsing(Tmp):
    def raw(self, text, name="bhav_20261005.csv"):
        p = self.root / name
        p.write_text(text)
        return p

    def test_numbers(self):
        for bad in (None, "", " ", "-", "--", "abc", "nan", "NaN", "inf", "-inf", "Infinity", "1e999"):
            self.assertIsNone(mu.fnum(bad), bad)
        self.assertEqual(mu.fnum(" 1,234.50 "), 1234.5)
        self.assertEqual(mu.fnum("0"), 0.0)

    def test_dates(self):
        self.assertEqual(mu.parse_nse_date("03-Oct-2025"), "2025-10-03")
        for bad in ("2025-10-03", "31-Feb-2025", "x", "", None, "03-Foo-2025"):
            self.assertIsNone(mu.parse_nse_date(bad), bad)

    def test_empty_file(self):
        raw, rep = mu.parse_bhav_file(self.raw(""), "2026-10-05")
        self.assertIsNone(raw)
        self.assertEqual(rep["status"], "empty")

    def test_header_only_file(self):
        raw, rep = mu.parse_bhav_file(self.raw(",".join(HEADER) + "\n"), "2026-10-05")
        self.assertEqual((raw, rep["status"]), (None, "empty"))

    def test_missing_columns(self):
        h = [c for c in HEADER if c != "TTL_TRD_QNTY"]
        raw, rep = mu.parse_bhav_file(self.raw(",".join(h) + "\nA,EQ,05-Oct-2026,1,1,1,1,1,1,1,1,1,1,1\n"), "2026-10-05")
        self.assertEqual(rep["status"], "missing_columns")
        self.assertIn("TTL_TRD_QNTY", rep["problem"])

    def test_missing_optional_columns_still_load(self):
        h = [c for c in HEADER if c not in ("LOW_PRICE", "DELIV_PER", "OPEN_PRICE")]
        text = ",".join(h) + "\n" + ",".join(["A", "EQ", "05-Oct-2026", "10", "11", "10", "10.5", "1000", "5", "1", "1", "1"][:len(h)]) + "\n"
        raw, rep = mu.parse_bhav_file(self.raw(text), "2026-10-05")
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(sorted(rep["missing_optional"]), ["DELIV_PER", "LOW_PRICE", "OPEN_PRICE"])

    def test_malformed_and_partial_rows(self):
        rows = "\n".join("A%d,EQ,05-Oct-2026,1,1,1,1,1,1,1,1,1,1,1,1" % i for i in range(200))
        broken = rows + "\nB,EQ,05-Oct-2026,1,1"      # one short row: dropped, file kept
        raw, rep = mu.parse_bhav_file(self.raw(",".join(HEADER) + "\n" + broken + "\n"), "2026-10-05")
        self.assertEqual((rep["status"], len(raw)), ("ok", 200))
        many = "\n".join("A%d,EQ,05-Oct-2026,1" % i for i in range(20))
        raw, rep = mu.parse_bhav_file(self.raw(",".join(HEADER) + "\n" + many + "\n"), "2026-10-05")
        self.assertEqual(rep["status"], "malformed")

    def test_nse_style_spacing_and_dashes(self):
        # the real file puts a space after every comma and uses '-' where a value does not exist
        head = ", ".join(HEADER)
        row = "TCS, EQ, 05-Oct-2026, 100.00, 101.00, 105.00, 99.00, 104.00, 103.50, 102.20, 12345, 126.15, 800, 5000, 40.50"
        dash = "ABC, EQ, 05-Oct-2026, 10.00, 10.00, 10.50, 9.90, 10.20, 10.10, 10.10, 500, 0.50, 20, -, -"
        raw, rep = mu.parse_bhav_file(self.raw(head + "\n" + row + "\n" + dash + "\n"), "2026-10-05")
        self.assertEqual(rep["status"], "ok")
        rows, c = mu.clean_day(raw)
        by = {r["symbol"]: r for r in rows}
        self.assertEqual((by["TCS"]["close"], by["TCS"]["low"], by["TCS"]["volume"], by["TCS"]["turnover"], by["TCS"]["deliv"]), (103.5, 99.0, 12345, 126.15, 40.5))
        self.assertIsNone(by["ABC"]["deliv"])
        self.assertEqual(c["invalid_delivery_pct"], 0)

    def test_binary_garbage(self):
        p = self.root / "bhav_20261005.csv"
        p.write_bytes(b"\xff\xfe\x00\x81" * 50)
        raw, rep = mu.parse_bhav_file(p, "2026-10-05")
        self.assertIsNone(raw)
        self.assertIn(rep["status"], ("malformed", "missing_columns"))

    def test_row_dated_for_another_day_rejects_the_file(self):
        raw, rep = mu.parse_bhav_file(self.raw(",".join(HEADER) + "\nA,EQ,04-Oct-2026,1,1,1,1,1,1,1,1,1,1,1,1\n"), "2026-10-05")
        self.assertEqual(rep["status"], "date_mismatch")

    def test_invalid_date_inside_file(self):
        raw, rep = mu.parse_bhav_file(self.raw(",".join(HEADER) + "\nA,EQ,not-a-date,1,1,1,1,1,1,1,1,1,1,1,1\n"), "2026-10-05")
        self.assertEqual(rep["status"], "date_mismatch")

    def test_no_raw_folder(self):
        self.assertEqual(mu.load_days(self.root / "nothing", NOW.date(), SMALL), ({}, [], {}))

    def test_weekend_and_future_files_rejected(self):
        for name in ("bhav_20261004.csv", "bhav_20261007.csv"):                 # a Sunday; a day after 'today'
            d = name[5:13]
            p = self.root / "data" / "raw" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            iso = dt.datetime.strptime(d, "%Y%m%d").strftime("%d-%b-%Y")
            p.write_text(",".join(HEADER) + "\n" + "\n".join("S%d,EQ,%s,1,1,1,1,1,1,1,1,1,1,1,1" % (i, iso) for i in range(20)) + "\n")
        days, reports, counts = mu.load_days(self.root / "data" / "raw", NOW.date(), SMALL)
        self.assertEqual(days, {})
        self.assertEqual(sorted(r["status"] for r in reports), ["future_date", "weekend_file"])

    def test_bad_file_name(self):
        p = self.root / "data" / "raw" / "bhav_notadate.csv"
        p.parent.mkdir(parents=True)
        p.write_text("x")
        self.assertEqual(mu.load_days(p.parent, NOW.date(), SMALL)[1][0]["status"], "bad_file_name")


class CleanDay(unittest.TestCase):
    def row(self, **kw):
        base = {"SYMBOL": "A", "SERIES": "EQ", "DATE1": "05-Oct-2026", "PREV_CLOSE": "100", "OPEN_PRICE": "100", "HIGH_PRICE": "105", "LOW_PRICE": "95", "CLOSE_PRICE": "102",
                "TTL_TRD_QNTY": "1000", "TURNOVER_LACS": "10", "DELIV_PER": "50"}
        base.update(kw)
        return base

    def test_series_filter(self):
        rows, c = mu.clean_day([self.row(), self.row(SYMBOL="B", SERIES="BE"), self.row(SYMBOL="C", SERIES=" EQ "), self.row(SYMBOL="D", SERIES="N1")])
        self.assertEqual([r["symbol"] for r in rows], ["A", "C"])
        self.assertEqual((c["other_series"], c["eq_rows"]), (2, 2))

    def test_identical_duplicates_collapse(self):
        rows, c = mu.clean_day([self.row(), self.row()])
        self.assertEqual((len(rows), c["duplicate_identical"], c["conflicting_duplicate_symbols"]), (1, 1, 0))

    def test_conflicting_duplicates_dropped(self):
        rows, c = mu.clean_day([self.row(), self.row(CLOSE_PRICE="150")])
        self.assertEqual((rows, c["conflicting_duplicate_symbols"]), ([], 1))

    def test_invalid_close(self):
        for bad in ("", "-", "0", "-5", "abc", "nan"):
            rows, c = mu.clean_day([self.row(CLOSE_PRICE=bad)])
            self.assertEqual((rows, c["invalid_close"]), ([], 1), bad)

    def test_missing_or_bad_previous_close_kept_without_change(self):
        for bad in ("", "-", "0", "-1"):
            rows, c = mu.clean_day([self.row(PREV_CLOSE=bad)])
            self.assertEqual((len(rows), rows[0]["prev_close"], c["missing_prev_close"]), (1, None, 1), bad)
            self.assertEqual(md.change_of(rows[0]), (None, None))

    def test_negative_volume_and_turnover_nulled(self):
        rows, c = mu.clean_day([self.row(TTL_TRD_QNTY="-5", TURNOVER_LACS="-1")])
        self.assertEqual((rows[0]["volume"], rows[0]["turnover"], c["invalid_volume"], c["invalid_turnover"]), (None, None, 1, 1))

    def test_zero_volume_is_legitimate(self):
        rows, c = mu.clean_day([self.row(TTL_TRD_QNTY="0", TURNOVER_LACS="0")])
        self.assertEqual((rows[0]["volume"], rows[0]["turnover"], c["invalid_volume"]), (0, 0.0, 0))

    def test_delivery_percentage_range(self):
        for bad, expect in (("101", None), ("-1", None), ("100", 100.0), ("0", 0.0), ("-", None), ("", None)):
            rows, c = mu.clean_day([self.row(DELIV_PER=bad)])
            self.assertEqual(rows[0]["deliv"], expect, bad)
        self.assertEqual(mu.clean_day([self.row(DELIV_PER="150")])[1]["invalid_delivery_pct"], 1)

    def test_high_below_low_and_non_positive_prices(self):
        rows, c = mu.clean_day([self.row(HIGH_PRICE="90", LOW_PRICE="95")])
        self.assertEqual((rows[0]["high"], rows[0]["low"], c["invalid_price_field"]), (None, None, 1))
        rows, c = mu.clean_day([self.row(OPEN_PRICE="0", LOW_PRICE="-3")])
        self.assertEqual((rows[0]["open"], rows[0]["low"], c["invalid_price_field"]), (None, None, 2))

    def test_extreme_change_counted_not_removed(self):
        rows, c = mu.clean_day([self.row(CLOSE_PRICE="400")])
        self.assertEqual((len(rows), c["extreme_change"]), (1, 1))

    def test_empty_input(self):
        self.assertEqual(mu.clean_day([])[0], [])

    def test_symbol_blank(self):
        rows, c = mu.clean_day([self.row(SYMBOL="")])
        self.assertEqual(rows, [])


# =====================================================================================================================================
class Gate(Tmp):
    def test_good_world_passes(self):
        days, reports, counts, dq, latest = self.pipeline(World())
        self.assertEqual(dq["status"], "pass", dq["errors"])
        self.assertEqual(latest, "2026-10-05")
        self.assertTrue(dq["source_is_latest_trading_day"])
        self.assertEqual(dq["trading_days_loaded"], 300)
        self.assertEqual(dq["expected_latest_trading_day"], "2026-10-05")

    def test_no_files(self):
        days, reports, counts = mu.load_days(self.root / "data" / "raw", NOW.date(), SMALL)
        dq, latest = mu.assess(days, reports, counts, NOW, set(), SMALL)
        self.assertEqual((dq["status"], latest), ("fail", None))
        self.assertTrue(any("no raw bhav files" in e for e in dq["errors"]))

    def test_all_files_rejected(self):
        raw = self.root / "data" / "raw"
        raw.mkdir(parents=True)
        (raw / "bhav_20261005.csv").write_text("")
        days, reports, counts = mu.load_days(raw, NOW.date(), SMALL)
        dq, latest = mu.assess(days, reports, counts, NOW, set(), SMALL)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any("rejected" in e for e in dq["errors"]))

    def test_empty_latest_file_fails_even_with_older_history(self):
        w = World()
        w.write(self.root)
        (self.root / "data" / "raw" / "bhav_20261005.csv").write_text("")
        days, reports, counts = mu.load_days(self.root / "data" / "raw", NOW.date(), SMALL)
        dq, latest = mu.assess(days, reports, counts, NOW, set(), SMALL)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any("newer file was rejected" in e for e in dq["errors"]))

    def test_partial_latest_file_fails(self):
        w = World()
        w.rows_override[w.n - 1] = [w.row(s, w.n - 1) for s in w.symbols()[:5]]
        days, reports, counts, dq, latest = self.pipeline(w)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any(r["status"] == "partial" for r in reports))

    def test_partial_older_file_is_only_a_warning(self):
        w = World()
        w.rows_override[100] = [w.row(s, 100) for s in w.symbols()[:5]]
        days, reports, counts, dq, latest = self.pipeline(w)
        self.assertEqual(dq["status"], "pass", dq["errors"])
        self.assertTrue(any("rejected" in x for x in dq["warnings"]))
        self.assertEqual(dq["trading_days_loaded"], 299)

    def test_universe_drop(self):
        w = World(fillers=30)
        keep = w.symbols()[:-8]
        w.rows_override[w.n - 1] = [w.row(s, w.n - 1) for s in keep]
        days, reports, counts, dq, latest = self.pipeline(w)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any("universe changed" in e for e in dq["errors"]))

    def test_universe_jump(self):
        w = World(fillers=30)
        extra = []
        for k in range(15):
            r = list(w.row("F000", w.n - 1))
            r[0] = "NEW%02d" % k
            extra.append(r)
        w.rows_override[w.n - 1] = [w.row(s, w.n - 1) for s in w.symbols()] + extra
        days, reports, counts, dq, latest = self.pipeline(w)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any("universe changed" in e for e in dq["errors"]))

    def test_universe_below_minimum(self):
        days, reports, counts, dq, latest = self.pipeline(World(fillers=3, n_days=300), cfg=dict(SMALL, min_universe=500, min_eq_rows_per_file=1))
        days, reports, counts = mu.load_days(self.root / "data" / "raw", NOW.date(), dict(SMALL, min_eq_rows_per_file=1))
        dq, latest = mu.assess(days, reports, counts, NOW, set(), dict(SMALL, min_universe=500, min_eq_rows_per_file=1))
        self.assertTrue(any("only" in e and "EQ stocks" in e for e in dq["errors"]))

    def test_stale_data_fails(self):
        w = World()
        w.write(self.root)
        later = dt.datetime(2026, 10, 20, 9, 0, tzinfo=IST)
        days, reports, counts = mu.load_days(self.root / "data" / "raw", later.date(), SMALL)
        dq, latest = mu.assess(days, reports, counts, later, set(), SMALL)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any("stale" in e for e in dq["errors"]))

    def test_one_day_behind_is_published_but_flagged(self):
        w = World()
        w.write(self.root)
        evening = dt.datetime(2026, 10, 6, 21, 0, tzinfo=IST)          # Tuesday evening: Tuesday's file is expected but not there yet
        days, reports, counts = mu.load_days(self.root / "data" / "raw", evening.date(), SMALL)
        dq, latest = mu.assess(days, reports, counts, evening, set(), SMALL)
        self.assertEqual(dq["status"], "pass")
        self.assertFalse(dq["source_is_latest_trading_day"])
        self.assertEqual(dq["expected_latest_trading_day"], "2026-10-06")
        self.assertTrue(any("not yet the expected trading day" in x for x in dq["warnings"]))

    def test_flag_flows_into_every_file(self):
        w = World()
        w.write(self.root)
        evening = dt.datetime(2026, 10, 6, 21, 0, tzinfo=IST)
        days, reports, counts = mu.load_days(self.root / "data" / "raw", evening.date(), SMALL)
        dq, latest = mu.assess(days, reports, counts, evening, set(), SMALL)
        docs = mu.compute(days, latest, dq, evening, md.DEFAULTS, set())
        for k in vmo.KINDS:
            self.assertFalse(docs[k]["freshness_detail"]["source_is_latest_trading_day"], k)
            self.assertEqual(docs[k]["as_of"], "2026-10-05")
        self.assertEqual(vmo.validate(docs, today=evening.date()), [])

    def test_holiday_is_skipped_when_expecting(self):
        self.assertEqual(mu.expected_latest_trading_day(dt.datetime(2026, 10, 6, 21, 0, tzinfo=IST), {"2026-10-06"}), "2026-10-05")
        self.assertEqual(mu.expected_latest_trading_day(dt.datetime(2026, 10, 10, 12, 0, tzinfo=IST), set()), "2026-10-09")   # Saturday -> Friday
        self.assertEqual(mu.expected_latest_trading_day(dt.datetime(2026, 10, 12, 9, 0, tzinfo=IST), set()), "2026-10-09")    # Monday morning -> Friday
        self.assertEqual(mu.expected_latest_trading_day(dt.datetime(2026, 10, 5, 20, 30, tzinfo=IST), set()), "2026-10-05")

    def test_calendar_gaps_ignore_known_holidays_and_weekends(self):
        dates = ["2026-10-01", "2026-10-02", "2026-10-06"]
        self.assertEqual(mu.calendar_gaps(dates, set()), ["2026-10-05"])
        self.assertEqual(mu.calendar_gaps(dates, {"2026-10-05"}), [])
        self.assertEqual(mu.calendar_gaps([], set()), [])

    def test_market_session_open_flag(self):
        self.assertTrue(mu.market_session_open(dt.datetime(2026, 10, 5, 11, 0, tzinfo=IST), set()))
        self.assertFalse(mu.market_session_open(dt.datetime(2026, 10, 5, 16, 0, tzinfo=IST), set()))
        self.assertFalse(mu.market_session_open(dt.datetime(2026, 10, 4, 11, 0, tzinfo=IST), set()))
        self.assertFalse(mu.market_session_open(dt.datetime(2026, 10, 5, 11, 0, tzinfo=IST), {"2026-10-05"}))

    def test_open_market_never_implies_live(self):
        w = World()
        w.write(self.root)
        mid = dt.datetime(2026, 10, 6, 11, 0, tzinfo=IST)               # the market is open: the data is still yesterday's close
        days, reports, counts = mu.load_days(self.root / "data" / "raw", mid.date(), SMALL)
        dq, latest = mu.assess(days, reports, counts, mid, set(), SMALL)
        docs = mu.compute(days, latest, dq, mid, md.DEFAULTS, set())
        fd = docs["breadth"]["freshness_detail"]
        self.assertEqual((docs["breadth"]["freshness"], fd["label"], fd["market_session_open_at_generation"]), ("EOD", "EOD as of 2026-10-05", True))
        self.assertEqual(vmo.validate(docs, today=mid.date()), [])

    def test_conflicting_duplicates_on_latest_day_over_limit(self):
        w = World()
        extra = []
        for k in range(5):
            r = list(w.row("F000", w.n - 1))
            r[0] = "F00%d" % (k + 1)
            r[8] = r[8] + 1                         # same symbol twice, different close
            extra.append(r)
        w.rows_override[w.n - 1] = [w.row(s, w.n - 1) for s in w.symbols()] + extra
        days, reports, counts, dq, latest = self.pipeline(w)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any("repeated with different numbers" in e for e in dq["errors"]))

    def test_many_invalid_closes_fail(self):
        w = World()
        rows = [w.row(s, w.n - 1) for s in w.symbols()]
        for r in rows[:8]:
            r[8] = 0
        w.rows_override[w.n - 1] = rows
        days, reports, counts, dq, latest = self.pipeline(w)
        self.assertEqual(dq["status"], "fail")
        self.assertTrue(any("no usable close" in e for e in dq["errors"]))

    def test_gate_reports_negative_volume_as_warning(self):
        w = World()
        rows = [w.row(s, w.n - 1) for s in w.symbols()]
        rows[0][10] = -5
        w.rows_override[w.n - 1] = rows
        days, reports, counts, dq, latest = self.pipeline(w)
        self.assertEqual(dq["status"], "pass")
        self.assertTrue(any("negative volume" in x for x in dq["warnings"]))


# =====================================================================================================================================
class Compute(Tmp):
    def setUp(self):
        super().setUp()
        self.docs_, self.w = self.docs()
        self.b, self.m, self.a, self.d = (self.docs_[k] for k in ("breadth", "movers", "activity", "dma"))

    def sym(self, rows, s):
        return next((r for r in rows if r["symbol"] == s), None)

    def test_universe(self):
        self.assertEqual(self.b["universe_count"], len(self.w.symbols()))
        self.assertEqual(len(self.d["stocks"]), len(self.w.symbols()))

    def test_breadth_matches_an_independent_count(self):
        adv = dec = unch = 0
        for s in self.w.symbols():
            i = self.w.n - 1
            if self.w.start.get(s, 0) >= i:
                continue
            c, p = self.w.close[s][i], self.w.prev[s][i]
            adv += c > p
            dec += c < p
            unch += c == p
        got = self.b["breadth"]["all_eq"]
        self.assertEqual((got["advancing"], got["declining"], got["unchanged"]), (adv, dec, unch))
        self.assertEqual(got["ad_ratio"], round(adv / dec, 2))
        self.assertGreater(unch, 0)

    def test_liquid_universe_is_separate(self):
        liquid, everything = self.b["breadth"]["liquid"], self.b["breadth"]["all_eq"]
        self.assertEqual(everything["counted"] - liquid["counted"], 2)                  # TINY (illiquid) and ZVOL (no trades today) are left out of the liquid universe
        self.assertEqual(everything["advancing"] - liquid["advancing"], 2)
        flags = {r["symbol"]: r["liquid"] for r in self.d["stocks"]}
        self.assertFalse(flags["TINY"])
        self.assertTrue(flags["UP"])
        for p in ("20", "50", "200"):
            blk = self.b["dma_breadth"][p]
            self.assertEqual(blk["all_eq"]["denominator"] - blk["liquid"]["denominator"], 2, p)

    def test_illiquid_stock_is_in_most_active_universe_but_never_a_mover(self):
        self.assertIsNotNone(self.sym(self.a["most_active"]["by_value"]["rows"], "TINY"))     # no liquidity filter on most active
        self.assertIsNone(self.sym(self.m["gainers"], "TINY"))                                  # despite +15%
        self.assertEqual(self.m["liquid_universe_count"], self.b["universe_count"] - 2)

    def test_dma_breadth_denominator_excludes_short_and_split(self):
        for p, n_ins, n_adj in ((20, 0, 0), (50, 0, 0), (200, 1, 1)):
            blk = self.b["dma_breadth"][str(p)]["all_eq"]
            self.assertEqual(blk["excluded"]["insufficient_history"], 1 if p > 30 else 0, p)           # SHORT has only 30 observations
            self.assertEqual(blk["excluded"]["requires_adjustment"], n_adj, p)                          # SPLIT's split is 100 days back: inside the 200-day window only
            self.assertEqual(blk["denominator"], self.b["universe_count"] - blk["excluded"]["insufficient_history"] - blk["excluded"]["requires_adjustment"], p)

    def test_dma_values_checked_by_hand(self):
        up = self.sym(self.d["stocks"], "UP")
        for p in (20, 50, 200):
            hand = round(sum(self.w.close["UP"][-p:]) / p, 2)
            self.assertEqual(up["dma"][str(p)]["value"], hand)
            self.assertEqual(up["dma"][str(p)]["observations"], p)
            self.assertEqual(up["dma"][str(p)]["position"], "above")
        dn = self.sym(self.d["stocks"], "DOWN")
        self.assertEqual(dn["dma"]["20"]["position"], "below")
        self.assertEqual(self.sym(self.d["stocks"], "UNCH")["dma"]["20"]["position"], "equal")

    def test_short_history_has_only_the_20_day_average(self):
        sh = self.sym(self.d["stocks"], "SHORT")["dma"]
        self.assertEqual((sh["20"]["status"], sh["50"]["status"], sh["200"]["status"]), ("safe", "insufficient_history", "insufficient_history"))
        self.assertEqual((sh["50"]["value"], sh["200"]["value"]), (None, None))
        self.assertEqual(sh["50"]["observations"], 30)

    def test_split_stock_flagged_where_the_window_contains_it(self):
        sp = self.sym(self.d["stocks"], "SPLIT")["dma"]
        self.assertEqual((sp["20"]["status"], sp["50"]["status"], sp["200"]["status"]), ("safe", "safe", "requires_adjustment"))
        flags = [e for e in self.a["corporate_action_flags"]["events"] if e["symbol"] == "SPLIT"]
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["date"], self.w.dates[self.w.split_at].isoformat())
        self.assertEqual(flags[0]["ratio"], 0.5)

    def test_52w_new_high_and_low_with_explanation(self):
        hl = self.a["high_low_52w"]
        up = self.sym(hl["new_high"]["rows"], "UP")
        self.assertIsNotNone(up)
        self.assertEqual(up["close"], self.w.close["UP"][-1])
        hand = max(self.w.row("UP", i)[5] for i in range(self.w.n - 253, self.w.n - 1))
        self.assertEqual(up["prior_high_52w"], hand)
        self.assertEqual(up["state"], "new_high")
        self.assertEqual(up["distance_pct"], round((up["close"] / hand - 1) * 100, 2))
        self.assertRegex(up["prior_high_date"], r"^\d{4}-\d{2}-\d{2}$")
        dn = self.sym(hl["new_low"]["rows"], "DOWN")
        self.assertEqual(dn["prior_low_52w"], min(self.w.row("DOWN", i)[6] for i in range(self.w.n - 253, self.w.n - 1)))
        self.assertLess(dn["distance_pct"], 0)
        self.assertIsNone(self.sym(hl["new_high"]["rows"], "DOWN"))

    def test_52w_excludes_short_history_and_split(self):
        for lst in ("new_high", "near_high", "new_low", "near_low"):
            for s in ("SHORT", "SPLIT"):
                self.assertIsNone(self.sym(self.a["high_low_52w"][lst]["rows"], s), (lst, s))
        c = self.b["high_low_52w_counts"]["new_52w_high"]
        self.assertEqual(c["excluded"]["insufficient_history"], 1)
        self.assertEqual(c["excluded"]["requires_adjustment"], 1)
        self.assertEqual(c["count"], self.a["high_low_52w"]["new_high"]["count"])

    def test_volume_multiple_checked_by_hand(self):
        sh = self.sym(self.a["volume"]["shockers"], "UP")
        self.assertEqual((sh["volume"], sh["avg_volume_20d"], sh["volume_multiple"]), (1000000, 200000, 5.0))
        self.assertEqual(self.sym(self.a["volume"]["shockers"], "DOWN")["volume_multiple"], 4.0)
        self.assertEqual([r["symbol"] for r in self.a["volume"]["shockers"]], ["UP", "DOWN"])
        self.assertEqual([r["symbol"] for r in self.a["volume"]["high_volume_high_gain"]], ["UP"])
        self.assertEqual([r["symbol"] for r in self.a["volume"]["high_volume_top_losers"]], ["DOWN"])

    def test_zero_volume_stock_not_a_shocker_and_not_most_active(self):
        self.assertIsNone(self.sym(self.a["volume"]["shockers"], "ZVOL"))
        self.assertIsNone(self.sym(self.a["most_active"]["by_volume"]["rows"], "ZVOL"))

    def test_split_stock_excluded_from_volume_baseline_only_if_event_inside(self):
        self.assertIsNone(self.sym(self.a["volume"]["shockers"], "SPLIT"))      # volume unchanged, so not a shocker; baseline is 'safe' (event 100 days back)

    def test_most_active_checked_by_hand(self):
        rows = self.a["most_active"]["by_value"]["rows"]
        hand = sorted(((self.w.row(s, self.w.n - 1)[11], s) for s in self.w.symbols() if self.w.row(s, self.w.n - 1)[11] > 0), key=lambda x: (-x[0], x[1]))
        self.assertEqual([r["symbol"] for r in rows], [s for _, s in hand][:100])
        self.assertEqual(rows[0]["rank"], 1)
        byvol = self.a["most_active"]["by_volume"]["rows"]
        self.assertEqual(byvol[0]["symbol"], "UP")
        self.assertEqual(byvol[0]["volume"], 1000000)
        self.assertNotEqual([r["symbol"] for r in rows], [r["symbol"] for r in byvol])

    def test_movers(self):
        g, l = self.m["gainers"], self.m["losers"]
        self.assertEqual(g[0]["symbol"], "UP")
        self.assertEqual(l[0]["symbol"], "DOWN")
        self.assertEqual(g[0]["change_pct"], round((self.w.close["UP"][-1] / self.w.close["UP"][-2] - 1) * 100, 2))
        self.assertEqual(g[0]["delivery_pct"], 45.5)
        self.assertTrue(all(r["change_pct"] > 0 for r in g) and all(r["change_pct"] < 0 for r in l))
        self.assertEqual(g[0]["prev_close"], self.w.close["UP"][-2])
        self.assertEqual(g[0]["change"], round(g[0]["close"] - g[0]["prev_close"], 2))

    def test_metric_statuses(self):
        ms = self.b["metric_status"]
        self.assertEqual(ms["performance_30d_90d_all_market"], "requires_adjustment")
        self.assertEqual(ms["above_20_dma"], "safe")
        self.assertEqual(ms["new_52w_high"], "safe")
        self.assertTrue(set(ms.values()) <= vmo.STATUSES)

    def test_no_multi_day_market_performance_published(self):
        text = json.dumps(self.docs_["breadth"]) + json.dumps(self.a) + json.dumps(self.m)
        for key in ("return_30d", "return_90d", "perf_30d", "perf_90d", "change_30d", "change_90d"):
            self.assertNotIn(key, text)


class FewDays(Tmp):
    def run_with(self, n):
        w = World(n_days=n)
        docs, _ = self.docs(w)
        return docs

    def test_under_20_days(self):
        d = self.run_with(19)
        for p in ("20", "50", "200"):
            blk = d["breadth"]["dma_breadth"][p]["all_eq"]
            self.assertEqual((blk["denominator"], blk["pct"], blk["status"]), (0, None, "insufficient_history"))
        self.assertEqual(d["breadth"]["metric_status"]["above_20_dma"], "insufficient_history")
        self.assertEqual(d["breadth"]["metric_status"]["new_52w_high"], "insufficient_history")
        self.assertIsNone(d["breadth"]["high_low_52w_counts"]["new_52w_high"]["count"])
        self.assertEqual(d["activity"]["volume"]["shockers"], [])
        self.assertEqual(vmo.validate(d), [])

    def test_between_20_and_50(self):
        d = self.run_with(40)["breadth"]["dma_breadth"]
        self.assertGreater(d["20"]["all_eq"]["denominator"], 0)
        self.assertEqual(d["50"]["all_eq"]["denominator"], 0)
        self.assertEqual(d["200"]["all_eq"]["denominator"], 0)

    def test_between_50_and_200(self):
        d = self.run_with(120)["breadth"]["dma_breadth"]
        self.assertGreater(d["50"]["all_eq"]["denominator"], 0)
        self.assertEqual(d["200"]["all_eq"]["pct"], None)

    def test_just_over_200(self):
        d = self.run_with(210)["breadth"]["dma_breadth"]
        self.assertGreater(d["200"]["all_eq"]["denominator"], 0)

    def test_52w_needs_253_observations(self):
        self.assertIsNone(self.run_with(252)["breadth"]["high_low_52w_counts"]["new_52w_high"]["count"])
        self.assertIsNotNone(self.run_with(253)["breadth"]["high_low_52w_counts"]["new_52w_high"]["count"])


class NoDecliners(Tmp):
    def test_zero_declining_stocks(self):
        w = World(n_days=60, fillers=12)
        for s in list(w.close):
            if s not in ("UP", "UNCH"):
                w.close[s] = [r2(100 + 0.01 * i) for i in range(w.n)]
        w.close["DOWN"] = [r2(100 + 0.01 * i) for i in range(w.n)]
        w.close["SPLIT"] = [r2(100 + 0.01 * i) for i in range(w.n)]
        w.split_at = 5
        w.close["ZVOL"] = [r2(100 + 0.01 * i) for i in range(w.n)]
        w.close["SHORT"] = [r2(100 + 0.01 * i) for i in range(w.n)]
        w.refresh_prev()
        w.prev["SPLIT"] = [None] + w.close["SPLIT"][:-1]
        docs, _ = self.docs(w)
        b = docs["breadth"]["breadth"]["all_eq"]
        self.assertEqual(b["declining"], 0)
        self.assertIsNone(b["ad_ratio"])
        self.assertIn("no declining", b["ad_ratio_note"])
        self.assertEqual(vmo.validate(docs), [])
        json.loads(mu.dumps(docs["breadth"]))


class Serialization(Tmp):
    def test_strict_json_no_nan_or_infinity(self):
        docs, _ = self.docs()
        for k in mu.FILES:
            text = mu.dumps(docs[k])
            self.assertNotIn("NaN", text)
            self.assertNotIn("Infinity", text)
            vmo.strict_loads(text)

    def test_sanitize_replaces_non_finite(self):
        out = mu.sanitize({"a": float("nan"), "b": [float("inf"), 1.5, {"c": float("-inf")}], "d": (1, 2)})
        self.assertEqual(out, {"a": None, "b": [None, 1.5, {"c": None}], "d": [1, 2]})
        text = mu.dumps({"x": float("nan")})
        self.assertEqual(json.loads(text), {"x": None})

    def test_strict_loader_rejects_nan(self):
        for bad in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.assertRaises(ValueError):
                vmo.strict_loads(bad)

    def test_round_trip(self):
        docs, _ = self.docs()
        for k in mu.FILES:
            self.assertEqual(json.loads(mu.dumps(docs[k])), mu.sanitize(docs[k]))

    def test_no_non_finite_float_anywhere(self):
        docs, _ = self.docs()

        def walk(x):
            if isinstance(x, float):
                self.assertTrue(math.isfinite(x))
            elif isinstance(x, dict):
                [walk(v) for v in x.values()]
            elif isinstance(x, list):
                [walk(v) for v in x]
        for k in mu.FILES:
            walk(docs[k])


class SchemaTests(Tmp):
    """The documented schema: what every file must carry."""

    def setUp(self):
        super().setUp()
        self.docs_, self.w = self.docs()

    def test_envelope(self):
        for k, kind in vmo.KINDS.items():
            d = self.docs_[k]
            self.assertEqual((d["schema_version"], d["kind"], d["as_of"], d["freshness"], d["universe"]), (1, kind, "2026-10-05", "EOD", "NSE EQ series"))
            self.assertIn("NSE EOD bhav", d["source"])
            for key in ("generated_at", "universe_count", "filters", "data_quality", "freshness_detail", "metric_status"):
                self.assertIn(key, d)
            self.assertEqual(d["freshness_detail"]["label"], "EOD as of 2026-10-05")
            self.assertTrue(d["freshness_detail"]["source_is_latest_trading_day"])
            self.assertEqual(d["data_quality"]["status"], "pass")

    def test_filters_are_documented(self):
        f = self.docs_["movers"]["filters"]
        self.assertEqual(f["liquidity_filter"]["min_turnover_lakhs"], 100.0)
        self.assertIn("most_active", f["liquidity_filter"]["does_not_apply_to"])
        self.assertIn("none", f["universe_filter"])

    def test_breadth_shape(self):
        b = self.docs_["breadth"]
        self.assertEqual(sorted(b["breadth"]), ["all_eq", "liquid"])
        self.assertEqual(sorted(b["breadth"]["all_eq"]), ["ad_ratio", "advancing", "counted", "declining", "unchanged"])
        self.assertEqual(sorted(b["dma_breadth"]), ["20", "200", "50"])
        self.assertEqual(sorted(b["dma_breadth"]["20"]["all_eq"]), ["denominator", "excluded", "numerator", "pct", "period", "status"])
        self.assertEqual(sorted(b["high_low_52w_counts"]), ["new_52w_high", "new_52w_low"])

    def test_row_shapes(self):
        g = self.docs_["movers"]["gainers"][0]
        self.assertEqual(sorted(g), ["change", "change_pct", "close", "delivery_pct", "prev_close", "rank", "symbol", "turnover_lakhs", "volume"])
        v = self.docs_["activity"]["volume"]["shockers"][0]
        self.assertEqual(sorted(v), ["avg_volume_20d", "change_pct", "close", "delivery_pct", "rank", "symbol", "turnover_lakhs", "volume", "volume_multiple"])
        h = self.docs_["activity"]["high_low_52w"]["new_high"]["rows"][0]
        self.assertEqual(sorted(h), ["close", "distance_pct", "prior_high_52w", "prior_high_date", "rank", "state", "symbol"])
        l = self.docs_["activity"]["high_low_52w"]["new_low"]["rows"][0]
        self.assertEqual(sorted(l), ["close", "distance_pct", "prior_low_52w", "prior_low_date", "rank", "state", "symbol"])
        d = self.docs_["dma"]["stocks"][0]
        self.assertEqual(sorted(d), ["close", "dma", "liquid", "symbol"])
        self.assertEqual(sorted(d["dma"]["20"]), ["observations", "position", "status", "value"])

    def test_valid_documents_pass(self):
        self.assertEqual(vmo.validate(self.docs_), [])

    def mutate(self, fn):
        d = copy.deepcopy(self.docs_)
        fn(d)
        return vmo.validate(d)

    def test_validator_catches_problems(self):
        cases = {
            "freshness": lambda d: d["breadth"].__setitem__("freshness", "LIVE"),
            "freshness not EOD": lambda d: d["breadth"].__setitem__("freshness", "INTRADAY"),
            "live wording": lambda d: d["breadth"]["freshness_detail"].__setitem__("note", "live prices"),
            "future": lambda d: [d[k].__setitem__("as_of", "2999-01-01") for k in vmo.KINDS],
            "weekend": lambda d: [d[k].update({"as_of": "2026-10-04", "freshness_detail": dict(d[k]["freshness_detail"], source_date="2026-10-04", label="EOD as of 2026-10-04")}) for k in vmo.KINDS],
            "mixed dates": lambda d: d["movers"].__setitem__("as_of", "2026-10-02"),
            "dq fail": lambda d: d["breadth"]["data_quality"].__setitem__("status", "fail"),
            "pct range": lambda d: d["breadth"]["dma_breadth"]["20"]["all_eq"].__setitem__("pct", 120.0),
            "pct vs counts": lambda d: d["breadth"]["dma_breadth"]["20"]["all_eq"].__setitem__("pct", 1.0),
            "ratio with no decliners": lambda d: d["breadth"]["breadth"]["all_eq"].update({"declining": 0, "ad_ratio": 3.0}),
            "bad rank": lambda d: d["movers"]["gainers"][0].__setitem__("rank", 7),
            "unsorted": lambda d: d["movers"]["gainers"].reverse(),
            "loser in gainers": lambda d: d["movers"]["gainers"][0].__setitem__("change_pct", -1.0),
            "negative volume": lambda d: d["movers"]["gainers"][0].__setitem__("volume", -1),
            "delivery range": lambda d: d["movers"]["gainers"][0].__setitem__("delivery_pct", 140.0),
            "nan": lambda d: d["movers"]["gainers"][0].__setitem__("close", float("nan")),
            "infinity": lambda d: d["movers"]["gainers"][0].__setitem__("close", float("inf")),
            "status": lambda d: d["breadth"]["metric_status"].__setitem__("above_20_dma", "great"),
            "30d safe": lambda d: d["breadth"]["metric_status"].__setitem__("performance_30d_90d_all_market", "safe"),
            "universe": lambda d: d["dma"].__setitem__("universe_count", 5),
            "dma without value": lambda d: d["dma"]["stocks"][0]["dma"]["20"].update({"status": "safe", "value": None}),
            "dma value without safe": lambda d: d["dma"]["stocks"][0]["dma"]["20"].update({"status": "insufficient_history"}),
            "table vs breadth": lambda d: d["breadth"]["dma_breadth"]["20"]["all_eq"].update({"numerator": 0, "pct": 0.0}),
            "not crossed": lambda d: d["activity"]["high_low_52w"]["new_high"]["rows"][0].__setitem__("close", 1.0),
            "multiple": lambda d: d["activity"]["volume"]["shockers"][0].__setitem__("volume_multiple", -1),
            "missing doc": lambda d: d.pop("dma"),
            "count unsafe": lambda d: d["breadth"]["high_low_52w_counts"]["new_52w_high"].update({"status": "insufficient_history", "count": 3}),
            "duplicate symbol": lambda d: d["movers"]["gainers"].append(dict(d["movers"]["gainers"][0], rank=len(d["movers"]["gainers"]) + 1)),
            "counted": lambda d: d["breadth"]["breadth"]["all_eq"].__setitem__("counted", 1),
            "freshness detail": lambda d: d["activity"]["freshness_detail"].__setitem__("source_date", "2026-01-01"),
            "universe count": lambda d: [d[k].__setitem__("universe_count", 0) for k in vmo.KINDS],
        }
        for name, fn in cases.items():
            self.assertTrue(self.mutate(fn), "validator missed: " + name)


# =====================================================================================================================================
class EndToEnd(Tmp):
    def test_run_writes_four_files_and_they_validate(self):
        World().write(self.root)
        code, msgs = mu.run(self.root, NOW, SMALL)
        self.assertEqual(code, 0, msgs)
        for name in mu.FILES.values():
            self.assertTrue((self.root / "out" / name).exists(), name)
        self.assertEqual(vmo.validate_dir(self.root / "out"), [])
        self.assertEqual(list((self.root / "out").glob("*.tmp")), [])

    def test_failed_gate_writes_nothing_and_keeps_old_files(self):
        World().write(self.root)
        self.assertEqual(mu.run(self.root, NOW, SMALL)[0], 0)
        before = {n: (self.root / "out" / n).read_bytes() for n in mu.FILES.values()}
        (self.root / "data" / "raw" / "bhav_20261005.csv").write_text("")
        code, msgs = mu.run(self.root, NOW, SMALL)
        self.assertEqual(code, 1)
        self.assertTrue(any("FAILED" in m for m in msgs))
        self.assertEqual({n: (self.root / "out" / n).read_bytes() for n in mu.FILES.values()}, before)

    def test_missing_raw_data_fails_and_writes_nothing(self):
        code, msgs = mu.run(self.root, NOW, SMALL)
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "out").exists() and any((self.root / "out").iterdir()))

    def test_no_loss_refuses_older_data(self):
        w = World()
        w.write(self.root)
        self.assertEqual(mu.run(self.root, NOW, SMALL)[0], 0)
        out = self.root / "out" / "market_breadth.json"
        doc = json.loads(out.read_text())
        doc["as_of"] = "2026-10-09"
        out.write_text(json.dumps(doc))
        code, msgs = mu.run(self.root, NOW, SMALL)
        self.assertEqual(code, 1)
        self.assertTrue(any("back in time" in m for m in msgs))

    def test_no_loss_refuses_much_smaller_universe(self):
        w = World()
        w.write(self.root)
        self.assertEqual(mu.run(self.root, NOW, SMALL)[0], 0)
        out = self.root / "out" / "market_breadth.json"
        doc = json.loads(out.read_text())
        doc["universe_count"] = 1000
        out.write_text(json.dumps(doc))
        code, msgs = mu.run(self.root, NOW, SMALL)
        self.assertEqual(code, 1)
        self.assertTrue(any("smaller than the published" in m for m in msgs))

    def test_run_refuses_documents_the_validator_rejects(self):
        World().write(self.root)
        real = mu.compute

        def bad(*a, **k):
            d = real(*a, **k)
            d["breadth"]["breadth"]["all_eq"]["ad_ratio"] = 99.0       # not advancing / declining
            return d
        mu.compute = bad
        try:
            code, msgs = mu.run(self.root, NOW, SMALL)
        finally:
            mu.compute = real
        self.assertEqual(code, 1)
        self.assertTrue(any("output check FAILED" in m for m in msgs))
        self.assertFalse((self.root / "out").exists() and any((self.root / "out").iterdir()))

    def test_same_day_rerun_is_allowed(self):
        World().write(self.root)
        self.assertEqual(mu.run(self.root, NOW, SMALL)[0], 0)
        self.assertEqual(mu.run(self.root, NOW, SMALL)[0], 0)

    def test_no_loss_ignores_missing_or_broken_old_file(self):
        doc = {"as_of": "2026-10-05", "universe_count": 40}
        self.assertEqual(mu.check_no_loss(doc, self.root / "none.json"), [])
        bad = self.root / "bad.json"
        bad.write_text("{not json")
        self.assertEqual(mu.check_no_loss(doc, bad), [])

    def test_atomic_write_leaves_no_partial_set(self):
        docs, _ = Tmp.docs(self)
        (self.root / "out").mkdir(exist_ok=True)
        bad = dict(docs)
        bad["dma"] = {"x": object()}                       # cannot be serialised
        with self.assertRaises(TypeError):
            mu.write_all(self.root / "out", bad)
        self.assertEqual([p.name for p in (self.root / "out").iterdir()], [])

    def test_validate_dir_reports_garbage(self):
        out = self.root / "out"
        out.mkdir()
        (out / "market_breadth.json").write_text('{"a": NaN}')
        self.assertTrue(vmo.validate_dir(out))
        self.assertTrue(vmo.validate_dir(self.root / "empty"))

    def test_no_nse_download_and_no_token_in_the_new_modules(self):
        for f in ("market_derive.py", "market_data_updater.py", "validate_market_outputs.py"):
            text = Path(f).read_text()
            for bad in ("requests", "urllib", "UPSTOX", "Authorization", "Bearer", "nseindia.com", "socket"):
                self.assertNotIn(bad, text, f + " must not use " + bad)

    def test_protected_files_are_not_imported_or_touched(self):
        text = Path("market_data_updater.py").read_text()
        for f in ("nse_updater", "fundamentals_updater", "financials_updater", "financial_history_updater", "shareholding_updater", "historical_updater"):
            self.assertNotIn("import " + f, text)


if __name__ == "__main__":
    unittest.main()
