"""Offline tests for live/decoder_full.py and live/scanner.py (the live market scanner engine). No network, no clock, no token."""
import datetime as dt
import json
import struct
import unittest
from pathlib import Path

from live import decoder, decoder_full as df, scanner as sc
from live.instruments import Instruments

ROOT = Path(__file__).resolve().parent
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SD = "2026-10-13"
T0 = int(dt.datetime(2026, 10, 13, 12, 0, tzinfo=IST).timestamp() * 1000)      # 12:00 IST on the session date


# ---- a tiny protobuf ENCODER, only for the tests (so the decoder is tested against bytes it did not produce)
def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def fld(fn, wt, payload):
    t = varint((fn << 3) | wt)
    return t + (varint(len(payload)) + payload if wt == 2 else payload)


def dbl(fn, x):
    return fld(fn, 1, struct.pack("<d", x))


def vi(fn, n):
    return fld(fn, 0, varint(n))


def ltpc(ltp, ltt, ltq, cp):
    return dbl(1, ltp) + vi(2, ltt) + vi(3, ltq) + dbl(4, cp)


def ohlc(interval, o, h, l, c, vol, ts):
    return fld(1, 2, interval.encode()) + dbl(2, o) + dbl(3, h) + dbl(4, l) + dbl(5, c) + vi(6, vol) + vi(7, ts)


def market_ff(lt, atp=None, vtt=None, day=None, extra_interval_first=False):
    b = fld(1, 2, lt)
    if day is not None:
        items = (fld(1, 2, ohlc("1m", 1, 2, 1, 2, 5, 0)) if extra_interval_first else b"") + fld(1, 2, day)
        b += fld(4, 2, items)
    if atp is not None:
        b += dbl(5, atp)
    if vtt is not None:
        b += vi(6, vtt)
    return b


def feed_full(mff):
    return fld(2, 2, fld(1, 2, mff))


def frame(entries, typ=1):
    b = vi(1, typ) + vi(3, T0)
    for key, feed in entries.items():
        b += fld(2, 2, fld(1, 2, key.encode()) + fld(2, 2, feed))
    return b


class DecoderFullTests(unittest.TestCase):
    def test_full_feed_extras_are_read_next_to_the_unchanged_ltpc(self):
        mff = market_ff(ltpc(101.5, T0, 7, 100.0), atp=100.9, vtt=123456, day=ohlc("1d", 99.0, 102.0, 98.5, 101.5, 123456, T0), extra_interval_first=True)
        f = df.decode_frame_full(frame({"NSE_EQ|INE000000001": feed_full(mff)}))
        e = f["feeds"]["NSE_EQ|INE000000001"]
        self.assertEqual(e["ltpc"], {"ltp": 101.5, "ltt": T0, "ltq": 7, "cp": 100.0})
        self.assertEqual((e["extra"]["vtt"], e["extra"]["atp"]), (123456, 100.9))
        self.assertEqual({k: e["extra"]["day"][k] for k in ("open", "high", "low")}, {"open": 99.0, "high": 102.0, "low": 98.5})
        self.assertEqual(f["feeds"]["NSE_EQ|INE000000001"]["ltpc"], decoder.decode_frame(frame({"NSE_EQ|INE000000001": feed_full(mff)}))["feeds"]["NSE_EQ|INE000000001"]["ltpc"])

    def test_absent_fields_stay_none_never_zero(self):
        f = df.decode_frame_full(frame({"K": feed_full(market_ff(ltpc(10.0, T0, 1, 9.0)))}))
        self.assertEqual(f["feeds"]["K"]["extra"], {"atp": None, "vtt": None, "tbq": None, "tsq": None, "day": None})

    def test_only_the_whole_session_candle_is_the_day_candle(self):
        mff = market_ff(ltpc(10.0, T0, 1, 9.0), day=ohlc("30m", 1, 2, 1, 2, 5, 0))
        self.assertIsNone(df.decode_frame_full(frame({"K": feed_full(mff)}))["feeds"]["K"]["extra"]["day"])

    def test_ltpc_mode_feed_has_no_extras_and_garbage_raises_decode_error(self):
        f = df.decode_frame_full(frame({"K": fld(1, 2, ltpc(10.0, T0, 1, 9.0))}))
        self.assertIsNone(f["feeds"]["K"]["extra"])
        for bad in (b"\x0a\xff\xff", b"\xff\xff\xff", b"\x12\x05ab"):
            with self.assertRaises(decoder.DecodeError):
                df.decode_frame_full(bad)
        with self.assertRaises(decoder.DecodeError):
            df.decode_frame_full("text")


# ---- fixtures for the book and the scan
def ins(n=8):
    eq = {"NSE_EQ|INE%03d" % i: "S%d" % i for i in range(1, n + 1)}
    return Instruments(eq, {"NSE_INDEX|Nifty 50": "NIFTY 50"}, {})


KEY = lambda i: "NSE_EQ|INE%03d" % i


def ff(ltp, cp, vtt=None, atp=None, day=None, ltt=T0 - 60_000, ltq=1):
    ex = {"atp": atp, "vtt": vtt, "tbq": None, "tsq": None, "day": day}
    return {"ltpc": {"ltp": ltp, "ltt": ltt, "ltq": ltq, "cp": cp}, "extra": ex}


def fr(feeds, ts=T0):
    return {"type": "live_feed", "current_ts": ts, "feeds": feeds, "market_info": None}


def book_with(feeds, n=8, now=T0):
    b = sc.QuoteBook(ins(n), SD)
    b.begin_connection(now - 5000)
    b.apply_frame(fr(feeds), now)
    return b


class QuoteBookTests(unittest.TestCase):
    def test_volume_never_goes_down_and_atp_must_be_positive(self):
        b = sc.QuoteBook(ins(), SD)
        b.apply_frame(fr({KEY(1): ff(101, 100, vtt=1000, atp=100.5)}), T0)
        b.apply_frame(fr({KEY(1): ff(102, 100, vtt=900, atp=-1, ltt=T0 - 30_000)}), T0 + 1)
        q = b.quote(KEY(1))
        self.assertEqual((q["extra"]["vtt"], q["extra"]["atp"]), (1000, 100.5))
        self.assertEqual(b.rejected, {"volume_decreased": 1, "bad_atp": 1})
        b.apply_frame(fr({KEY(1): ff(103, 100, vtt=1500, ltt=T0 - 20_000)}), T0 + 2)
        self.assertEqual(b.quote(KEY(1))["extra"]["vtt"], 1500)

    def test_extras_of_a_rejected_tick_are_not_used(self):
        b = sc.QuoteBook(ins(), SD)
        b.apply_frame(fr({KEY(1): ff(0, 100, vtt=5000)}), T0)                    # bad price
        self.assertIsNone(b.quote(KEY(1)))
        self.assertEqual(b.field_counts["feeds_with_extra"], 0)

    def test_a_session_range_that_does_not_contain_the_price_is_dropped(self):
        b = sc.QuoteBook(ins(), SD)
        b.apply_frame(fr({KEY(1): ff(101, 100, day={"open": 99, "high": 100.0, "low": 98, "close": 100, "vol": 1, "ts": 0})}), T0)
        self.assertIsNone(b.quote(KEY(1))["extra"]["day"])
        self.assertEqual(b.rejected, {"day_ohlc_inconsistent": 1})
        b.apply_frame(fr({KEY(1): ff(101, 100, day={"open": 99, "high": 101.2, "low": 98, "close": 100, "vol": 1, "ts": 0}, ltt=T0 - 1000)}), T0 + 1)
        self.assertEqual(b.quote(KEY(1))["extra"]["day"], {"open": 99.0, "high": 101.2, "low": 98.0})


def candles(n, start=None, price=100.0, vol=100_000, end="2026-10-12", hi_pad=2.0, lo_pad=2.0):
    """n weekday candles ending on `end` (ascending). `start` is accepted for readability and ignored."""
    days, d = [], dt.date.fromisoformat(end)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return [{"date": x, "open": price, "high": price + hi_pad, "low": price - lo_pad, "close": price, "volume": vol} for x in reversed(days)]


class BaselineTests(unittest.TestCase):
    def test_52_week_range_and_average_volume_use_completed_sessions_only(self):
        cs = candles(250)
        cs[100]["high"] = 150.0
        cs[120]["low"] = 60.0
        b = sc.baseline_from_candles(cs, "2026-10-13")
        self.assertEqual((b["hi52"], b["lo52"], b["avg_vol"], b["withheld"]), (150.0, 60.0, 100000.0, []))
        # a candle ON the session date or later is never part of its own baseline
        cs2 = cs + [{"date": "2026-10-13", "open": 1, "high": 9999, "low": 0.5, "close": 5, "volume": 10 ** 9}]
        b2 = sc.baseline_from_candles(cs2, "2026-10-13")
        self.assertEqual((b2["hi52"], b2["lo52"], b2["avg_vol"]), (150.0, 60.0, 100000.0))

    def test_too_little_history_withholds_instead_of_inventing_a_range(self):
        b = sc.baseline_from_candles(candles(60), "2026-10-13")
        self.assertIsNone(b["hi52"])
        self.assertIn("52w_too_few_sessions", b["withheld"])
        self.assertIsNotNone(b["avg_vol"])
        b = sc.baseline_from_candles(candles(10), "2026-10-13")
        self.assertIsNone(b["avg_vol"])
        self.assertIn("volume_too_few_sessions", b["withheld"])

    def test_a_suspected_unadjusted_split_withholds_the_52_week_range(self):
        cs = candles(250)
        for c in cs[150:]:
            for k in ("open", "high", "low", "close"):
                c[k] = c[k] / 2
        self.assertIn("52w_unadjusted_jump_suspected", sc.baseline_from_candles(cs, "2026-10-13")["withheld"])
        self.assertIsNone(sc.baseline_from_candles(cs, "2026-10-13")["hi52"])

    def test_old_or_missing_history_is_not_a_baseline(self):
        old = candles(250, end="2026-06-02")
        self.assertIn("history_too_old", sc.baseline_from_candles(old, "2026-10-13")["withheld"])
        self.assertIn("no_history", sc.baseline_from_candles([], "2026-10-13")["withheld"])


NAMES = {"S%d" % i: "Company %d Ltd" % i for i in range(1, 9)}


def scan(feeds, baselines=None, mode="live", now=T0, cfg=None, n=8, names=None, subscribed=None):
    b = book_with(feeds, n)
    subs = subscribed if subscribed is not None else [KEY(i) for i in range(1, n + 1)]
    return sc.build_scan(b, names or NAMES, subs, baselines or {}, now, "open" if mode == "live" else "closed", mode, cfg), b


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.feeds = {KEY(1): ff(110, 100, vtt=500), KEY(2): ff(95, 100, vtt=900), KEY(3): ff(100, 100, vtt=100), KEY(4): ff(103, 100, vtt=2_000_000, atp=102.0),
                      KEY(5): ff(90, 100, vtt=300, atp=95.0)}

    def test_gainers_and_losers_are_ranked_by_percentage_with_the_required_fields(self):
        s, _ = scan(self.feeds)
        self.assertEqual([r["symbol"] for r in s["gainers"]], ["S1", "S4"])
        self.assertEqual([r["symbol"] for r in s["losers"]], ["S5", "S2"])
        g = s["gainers"][0]
        self.assertEqual((g["name"], g["ltp"], g["change"], g["change_pct"], g["prev_close"]), ("Company 1 Ltd", 110, 10.0, 10.0, 100))
        self.assertTrue(g["quote_time"].startswith("2026-10-13T11:59:00"))
        self.assertNotIn("S3", [r["symbol"] for r in s["gainers"] + s["losers"]])           # unchanged is neither

    def test_top_n_is_enforced(self):
        feeds = {KEY(i): ff(100 + i, 100) for i in range(1, 9)}
        s, _ = scan(feeds, cfg={"top_n": 3})
        self.assertEqual([r["symbol"] for r in s["gainers"]], ["S8", "S7", "S6"])

    def test_no_previous_close_extreme_and_stale_quotes_are_not_ranked_and_are_counted(self):
        feeds = {KEY(1): ff(110, None), KEY(2): ff(200, 100), KEY(3): ff(105, 100, ltt=T0 - 20 * 60_000), KEY(4): ff(103, 100)}
        feeds[KEY(1)]["ltpc"]["cp"] = None
        s, _ = scan(feeds)
        self.assertEqual([r["symbol"] for r in s["gainers"]], ["S4"])
        c = s["coverage"]
        self.assertEqual((c["no_prev_close"], c["extreme_withheld"], c["stale"], c["fresh"], c["quoted"], c["unavailable"]), (1, 1, 1, 3, 4, 4))

    def test_yesterdays_quote_is_never_fresh(self):
        yday = int(dt.datetime(2026, 10, 12, 15, 29, tzinfo=IST).timestamp() * 1000)
        s, _ = scan({KEY(1): ff(110, 100, ltt=yday)})
        self.assertEqual((s["gainers"], s["coverage"]["fresh"], s["coverage"]["stale"]), ([], 0, 1))
        s, _ = scan({KEY(1): ff(110, 100, ltt=yday)}, mode="closing")
        self.assertEqual(s["gainers"], [])                                                  # closing mode still needs the session date

    def test_closing_mode_keeps_the_days_last_quotes_after_the_close(self):
        late = int(dt.datetime(2026, 10, 13, 18, 0, tzinfo=IST).timestamp() * 1000)
        feeds = {KEY(1): ff(110, 100, vtt=10)}
        live, _ = scan(feeds, now=late, mode="live")
        self.assertEqual(live["gainers"], [])
        closed, _ = scan(feeds, now=late, mode="closing")
        self.assertEqual([r["symbol"] for r in closed["gainers"]], ["S1"])
        self.assertEqual(closed["label"], "Closing snapshot of 2026-10-13")
        self.assertEqual(live["label"], "Live")

    def test_big_movers_respect_the_thresholds_and_rank_by_absolute_move(self):
        s, _ = scan(self.feeds, cfg={"mover_min_pct": 5.0})
        self.assertEqual([r["symbol"] for r in s["movers"]], ["S1", "S5", "S2"])
        s, _ = scan(self.feeds, cfg={"mover_min_pct": 5.0, "mover_min_abs": 8.0})
        self.assertEqual([r["symbol"] for r in s["movers"]], ["S1", "S5"])
        s, _ = scan(self.feeds, cfg={"mover_min_pct": 1.0, "mover_min_price": 100.0})
        self.assertEqual([r["symbol"] for r in s["movers"]], ["S1", "S4"])

    def test_day_high_low_appear_only_when_the_feed_supplied_consistent_values(self):
        feeds = {KEY(1): ff(110, 100, day={"open": 101, "high": 111, "low": 99, "close": 110, "vol": 1, "ts": 0}), KEY(2): ff(95, 100)}
        s, _ = scan(feeds, cfg={"mover_min_pct": 1.0})
        by = {r["symbol"]: r for r in s["movers"]}
        self.assertEqual((by["S1"]["day_high"], by["S1"]["day_low"], by["S1"]["day_open"]), (111.0, 99.0, 101.0))
        self.assertNotIn("day_high", by["S2"])

    def test_most_active_by_volume_uses_cumulative_volume_not_the_last_quantity(self):
        feeds = {KEY(1): ff(101, 100, vtt=500, ltq=9_999_999), KEY(2): ff(101, 100, vtt=900, ltq=1), KEY(3): ff(101, 100, ltq=5_000_000)}
        s, _ = scan(feeds)
        self.assertEqual([(r["symbol"], r["volume"]) for r in s["most_active_volume"]], [("S2", 900), ("S1", 500)])        # S3 has no volume: absent, not zero

    def test_turnover_is_approximate_traded_value_and_needs_both_factors(self):
        s, _ = scan(self.feeds)
        self.assertEqual([(r["symbol"], r["approx_traded_value"]) for r in s["most_active_value"]], [("S4", 204_000_000.0), ("S5", 28_500.0)])
        self.assertIn("approximate traded value", s["rules"]["turnover"])
        self.assertNotIn("turnover", json.dumps(s["most_active_volume"]))

    def test_unusual_volume_needs_a_baseline_and_is_not_just_high_volume(self):
        feeds = {KEY(1): ff(101, 100, vtt=300_000), KEY(2): ff(101, 100, vtt=900_000)}
        bl = {"S1": {"avg_vol": 100_000.0}}
        s, _ = scan(feeds, bl)                                         # 12:00 = 2h45m of 6h15m = 44% of the session
        self.assertEqual([r["symbol"] for r in s["unusual_volume"]], ["S1"])           # S2 has the larger volume but no baseline: absent
        self.assertAlmostEqual(s["unusual_volume"][0]["volume_ratio"], round(300_000 / (100_000 * 165 / 375), 2))
        s, _ = scan({KEY(1): ff(101, 100, vtt=30_000)}, bl)
        self.assertEqual(s["unusual_volume"], [])                       # below pace

    def test_unusual_volume_is_not_judged_before_enough_of_the_session_has_passed(self):
        early = int(dt.datetime(2026, 10, 13, 9, 30, tzinfo=IST).timestamp() * 1000)
        s, _ = scan({KEY(1): ff(101, 100, vtt=900_000, ltt=early - 1000)}, {"S1": {"avg_vol": 100_000.0}}, now=early)
        self.assertEqual(s["unusual_volume"], [])
        self.assertLess(s["rules"]["session_fraction"], 0.2)

    def test_a_thinly_traded_baseline_does_not_produce_unusual_volume(self):
        s, _ = scan({KEY(1): ff(101, 100, vtt=900)}, {"S1": {"avg_vol": 100.0}})
        self.assertEqual(s["unusual_volume"], [])

    def test_closing_unusual_volume_compares_the_whole_day(self):
        s, _ = scan({KEY(1): ff(101, 100, vtt=250_000)}, {"S1": {"avg_vol": 100_000.0}}, mode="closing")
        self.assertEqual(s["unusual_volume"][0]["volume_ratio"], 2.5)


class FiftyTwoWeekTests(unittest.TestCase):
    BL = {"S%d" % i: {"hi52": 100.0, "lo52": 50.0, "avg_vol": None} for i in range(1, 8)}

    def test_new_at_and_near_levels_with_the_documented_thresholds(self):
        feeds = {KEY(1): ff(101, 98), KEY(2): ff(99.8, 98), KEY(3): ff(97.5, 96), KEY(4): ff(96.9, 96),
                 KEY(5): ff(49, 52), KEY(6): ff(50.1, 52), KEY(7): ff(51.4, 52)}
        s, _ = scan(feeds, self.BL)
        hi = {r["symbol"]: (r["status"], r["from_high_pct"]) for r in s["highs_52w"]}
        self.assertEqual(hi, {"S1": ("new_high", 1.0), "S2": ("at_high", -0.2), "S3": ("near_high", -2.5)})          # 96.9 is 3.1% below: not near
        self.assertEqual([r["symbol"] for r in s["highs_52w"]], ["S1", "S2", "S3"])
        lo = {r["symbol"]: r["status"] for r in s["lows_52w"]}
        self.assertEqual(lo, {"S5": "new_low", "S6": "at_low", "S7": "near_low"})
        self.assertEqual(s["counts_52w"], {"new_high": 1, "at_high": 1, "near_high": 1, "new_low": 1, "at_low": 1, "near_low": 1})

    def test_touching_the_level_exactly_is_at_the_level_not_a_new_high_or_low(self):
        s, _ = scan({KEY(1): ff(100.0, 98), KEY(2): ff(50.0, 52)}, self.BL)
        self.assertEqual(([r["status"] for r in s["highs_52w"]], [r["status"] for r in s["lows_52w"]]), (["at_high"], ["at_low"]))

    def test_missing_levels_are_never_treated_as_zero(self):
        s, _ = scan({KEY(1): ff(101, 98), KEY(2): ff(1, 2)}, {"S1": {"hi52": None, "lo52": None}, "S2": {}})
        self.assertEqual((s["highs_52w"], s["lows_52w"]), ([], []))
        self.assertEqual(s["coverage"]["with_baseline_52w"], 0)


class BreadthAndCoverageTests(unittest.TestCase):
    def test_breadth_counts_only_fresh_valid_stocks_and_coverage_reconciles(self):
        feeds = {KEY(1): ff(101, 100), KEY(2): ff(99, 100), KEY(3): ff(100, 100), KEY(4): ff(104, 100), KEY(5): ff(101, 100, ltt=T0 - 3_600_000)}
        feeds[KEY(4)]["ltpc"]["cp"] = None
        s, _ = scan(feeds, subscribed=[KEY(i) for i in range(1, 8)])
        self.assertEqual(s["breadth"], {"advancing": 1, "declining": 1, "unchanged": 1, "counted": 3})
        c = s["coverage"]
        self.assertEqual((c["universe"], c["subscribed"], c["quoted"], c["unavailable"], c["fresh"], c["stale"], c["no_prev_close"]), (8, 7, 5, 2, 4, 1, 1))
        self.assertEqual(c["no_instrument_key"], 0)

    def test_directory_symbols_without_an_instrument_key_are_counted_not_hidden(self):
        names = dict(NAMES, GHOST="Ghost Ltd")
        s, _ = scan({KEY(1): ff(101, 100)}, names=names)
        self.assertEqual((s["coverage"]["universe"], s["coverage"]["no_instrument_key"]), (9, 1))

    def test_indices_show_value_and_change_when_fresh_and_nothing_when_not(self):
        b = book_with({KEY(1): ff(101, 100), "NSE_INDEX|Nifty 50": {"ltpc": {"ltp": 22700.0, "ltt": T0 - 5000, "ltq": 0, "cp": 22363.35}, "extra": None}})
        s = sc.build_scan(b, NAMES, [KEY(1)], {}, T0, "open", "live")
        n = s["indices"][0]
        self.assertEqual((n["label"], n["fresh"], n["value"], n["change"], n["change_pct"]), ("NIFTY 50", True, 22700.0, 336.65, 1.51))
        s = sc.build_scan(b, NAMES, [KEY(1)], {}, T0 + 10 * 60_000, "open", "live")
        self.assertEqual((s["indices"][0]["fresh"], s["indices"][0]["value"]), (False, None))

    def test_the_document_is_deterministic_and_strict_json(self):
        feeds = {KEY(i): ff(100 + i, 100, vtt=i * 1000, atp=100.0) for i in range(1, 9)}
        a, _ = scan(feeds)
        b, _ = scan(dict(reversed(list(feeds.items()))))
        self.assertEqual(json.dumps(a, sort_keys=True, allow_nan=False), json.dumps(b, sort_keys=True, allow_nan=False))
        self.assertEqual((a["schema_version"], a["kind"]), (1, "live_scan"))

    def test_session_fraction(self):
        f = lambda h, m: sc.session_fraction(int(dt.datetime(2026, 10, 13, h, m, tzinfo=IST).timestamp() * 1000), SD)
        self.assertEqual((f(9, 0), f(9, 15), f(15, 30), f(18, 0)), (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(round(f(12, 22), 2), 0.5)


class SeparationTests(unittest.TestCase):
    def test_the_engine_has_no_network_clock_or_end_of_day_dependency(self):
        for name in ("live/scanner.py", "live/decoder_full.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            code = "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))
            for bad in ("import requests", "import socket", "import websockets", "import asyncio", "time.time(", "datetime.now(", "historical_updater", "os.environ", "open("):
                self.assertNotIn(bad, code, (name, bad))


if __name__ == "__main__":
    unittest.main()
