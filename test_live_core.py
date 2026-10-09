"""Phase 1 of the live relay - the OFFLINE core. No network, no token, no clock: everything is fed bytes and fake times."""
import ast
import datetime as dt
import gzip
import json
import os
import random
import re
import tempfile
import time
import unittest
from pathlib import Path

from live import decoder, instruments, market_hours, replay, snapshot, state, ticks
from live.replay import enc_feed, enc_frame, enc_ltpc

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T0 = int(dt.datetime(2026, 10, 8, 10, 0, 0, tzinfo=IST).timestamp() * 1000)     # a Thursday, 10:00 IST
DAY = "2026-10-08"


def make_ins(n=20, n_idx=3):
    return instruments.Instruments.from_rows(replay.synthetic_rows(n, n_idx))


def eq_key(i):
    return "NSE_EQ|SYN%05d" % i


def sym(i):
    return "TST%05d" % i


def idx_key(name):
    return "NSE_INDEX|" + name.title()


def tick(ins, store, key, ltp, cp, t, ltt=None, ltq=5):
    f = decoder.decode_frame(enc_frame({key: enc_feed(enc_ltpc(ltp, t - 100 if ltt is None else ltt, ltq, cp))}, 1, t))
    return store.apply_frame(f, t)


def full_market(ins, store, now, move=0.01, cp=100.0):
    """Every instrument ticks once at `now`: equities move by `move` (alternating up/down), indices up."""
    feeds = {}
    for i, k in enumerate(ins.equities):
        feeds[k] = enc_feed(enc_ltpc(cp * (1 + move * (1 if i % 2 == 0 else -1)), now - 200, 1, cp))
    for k in ins.indices:
        feeds[k] = enc_feed(enc_ltpc(20000 * (1 + move), now - 200, 1, 20000))
    store.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, now)), now)


# ------------------------------------------------------------------------------------------------------------------ decoder
class DecoderTests(unittest.TestCase):
    def test_ltpc_frame_roundtrip(self):
        b = enc_frame({"NSE_INDEX|Nifty 50": enc_feed(enc_ltpc(24500.5, 1700000000123, 75, 24400.25))}, 1, 1700000000999)
        f = decoder.decode_frame(b)
        self.assertEqual(f["type"], "live_feed")
        self.assertEqual(f["current_ts"], 1700000000999)
        self.assertEqual(f["feeds"]["NSE_INDEX|Nifty 50"]["ltpc"], {"ltp": 24500.5, "ltt": 1700000000123, "ltq": 75, "cp": 24400.25})

    def test_known_bytes(self):
        # type=1 ; feeds{ key "A" ; feed{ ltpc{ ltp=1.5 ltt=2 ltq=3 cp=4.0 } } }
        ltpc = bytes([0x09]) + bytes.fromhex("000000000000F83F") + bytes([0x10, 2, 0x18, 3, 0x21]) + bytes.fromhex("0000000000001040")
        feed = bytes([0x0A, len(ltpc)]) + ltpc
        entry = bytes([0x0A, 1, 0x41, 0x12, len(feed)]) + feed
        frame = bytes([0x08, 1, 0x12, len(entry)]) + entry
        self.assertEqual(decoder.decode_frame(frame)["feeds"]["A"]["ltpc"], {"ltp": 1.5, "ltt": 2, "ltq": 3, "cp": 4.0})

    def test_full_feed_market_and_index_expose_the_same_ltpc_key(self):
        for kind in ("market", "index"):
            b = enc_frame({"K": enc_feed(enc_ltpc(10, 5, 1, 9), full=kind)}, 0)
            feed = decoder.decode_frame(b)["feeds"]["K"]
            self.assertEqual(feed["full"], kind)
            self.assertEqual(feed["ltpc"]["ltp"], 10.0)

    def test_market_info_and_types(self):
        b = enc_frame({}, 2, 5, {"NSE_EQ": 2, "NSE_FO": 3})
        f = decoder.decode_frame(b)
        self.assertEqual(f["type"], "market_info")
        self.assertEqual(f["market_info"], {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_CLOSE"})
        self.assertEqual(decoder.decode_frame(enc_frame({}, 0))["type"], "initial_feed")

    def test_missing_fields_are_none_not_zero(self):
        f = decoder.decode_frame(enc_frame({"K": enc_feed(enc_ltpc(ltp=10.0))}, 1))
        self.assertEqual(f["feeds"]["K"]["ltpc"], {"ltp": 10.0, "ltt": None, "ltq": None, "cp": None})

    def test_non_finite_doubles_become_none(self):
        for bad in (float("nan"), float("inf")):
            f = decoder.decode_frame(enc_frame({"K": enc_feed(enc_ltpc(bad, 1, 1, 5.0))}, 1))
            self.assertIsNone(f["feeds"]["K"]["ltpc"]["ltp"])

    def test_every_truncated_prefix_raises_only_decodeerror_or_decodes(self):
        b = enc_frame({"NSE_EQ|X": enc_feed(enc_ltpc(1.5, 2, 3, 4.0)), "NSE_INDEX|Y": enc_feed(enc_ltpc(9.5, 2, 3, 4.0), full="index")}, 1, 77, {"NSE_EQ": 2})
        for n in range(len(b)):
            try:
                decoder.decode_frame(b[:n])
            except decoder.DecodeError:
                pass

    def test_fuzz_never_raises_anything_but_decodeerror(self):
        rng = random.Random(7)
        base = enc_frame({"NSE_EQ|X": enc_feed(enc_ltpc(1.5, 2, 3, 4.0))}, 1, 77, {"NSE_EQ": 2})
        for _ in range(3000):
            b = bytearray(base if rng.random() < 0.6 else rng.randbytes(rng.randint(0, 60)))
            for _ in range(rng.randint(0, 4)):
                if b:
                    b[rng.randrange(len(b))] = rng.randrange(256)
            try:
                decoder.decode_frame(bytes(b))
            except decoder.DecodeError:
                pass

    def test_rejects_non_binary_oversized_and_field_zero(self):
        for bad in ("text", None, 5, ["a"]):
            with self.assertRaises(decoder.DecodeError):
                decoder.decode_frame(bad)
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_frame(b"\x00" * (decoder.MAX_FRAME_BYTES + 1))
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_frame(b"\x00\x01")                       # field number 0
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_frame(b"\x0B")                           # group wire type
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_frame(b"\x80" * 12)                      # varint too long

    def test_too_many_feeds_is_refused(self):
        old = decoder.MAX_FEEDS_PER_FRAME
        decoder.MAX_FEEDS_PER_FRAME = 3
        try:
            b = enc_frame({"K%d" % i: enc_feed(enc_ltpc(1.0, 1, 1, 1.0)) for i in range(5)}, 1)
            with self.assertRaises(decoder.DecodeError):
                decoder.decode_frame(b)
        finally:
            decoder.MAX_FEEDS_PER_FRAME = old

    def test_agrees_with_an_independent_protobuf_description(self):
        """The wire layout is described again here with the protobuf library's own descriptor machinery (not our parser), then both read the same bytes."""
        try:
            from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
        except Exception:
            self.skipTest("protobuf library not installed")
        T = descriptor_pb2.FieldDescriptorProto
        fd = descriptor_pb2.FileDescriptorProto(name="t_feed.proto", package="t", syntax="proto3")

        def msg(name, fields):
            m = fd.message_type.add(name=name)
            for n, num, typ, tn in fields:
                f = m.field.add(name=n, number=num, type=typ, label=T.LABEL_OPTIONAL)
                if tn:
                    f.type_name = tn
            return m
        msg("LTPC", [("ltp", 1, T.TYPE_DOUBLE, None), ("ltt", 2, T.TYPE_INT64, None), ("ltq", 3, T.TYPE_INT64, None), ("cp", 4, T.TYPE_DOUBLE, None)])
        msg("Feed", [("ltpc", 1, T.TYPE_MESSAGE, ".t.LTPC"), ("requestMode", 4, T.TYPE_INT32, None)])
        fr = msg("FeedResponse", [("type", 1, T.TYPE_INT32, None), ("currentTs", 3, T.TYPE_INT64, None)])
        ent = fr.nested_type.add(name="FeedsEntry")
        ent.field.add(name="key", number=1, type=T.TYPE_STRING, label=T.LABEL_OPTIONAL)
        ent.field.add(name="value", number=2, type=T.TYPE_MESSAGE, type_name=".t.Feed", label=T.LABEL_OPTIONAL)
        ent.options.map_entry = True
        fr.field.add(name="feeds", number=2, type=T.TYPE_MESSAGE, type_name=".t.FeedResponse.FeedsEntry", label=T.LABEL_REPEATED)
        pool = descriptor_pool.DescriptorPool()
        pool.Add(fd)
        cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("t.FeedResponse"))
        m = cls()
        m.type = 1
        m.currentTs = 1700000000999
        for k, (p, t, q, c) in {"NSE_EQ|AAA": (101.25, 1700000000100, 7, 100.0), "NSE_INDEX|Nifty 50": (24500.5, 1700000000200, 0, 24400.0)}.items():
            m.feeds[k].ltpc.ltp, m.feeds[k].ltpc.ltt, m.feeds[k].ltpc.ltq, m.feeds[k].ltpc.cp = p, t, q, c
        mine = decoder.decode_frame(m.SerializeToString())
        self.assertEqual(mine["current_ts"], 1700000000999)
        self.assertEqual(mine["feeds"]["NSE_EQ|AAA"]["ltpc"], {"ltp": 101.25, "ltt": 1700000000100, "ltq": 7, "cp": 100.0})
        self.assertEqual(mine["feeds"]["NSE_INDEX|Nifty 50"]["ltpc"]["cp"], 24400.0)
        # and our encoder's bytes parse correctly with the library
        n = cls()
        n.ParseFromString(enc_frame({"K": enc_feed(enc_ltpc(1.5, 2, 3, 4.0))}, 1, 9))
        self.assertEqual((n.feeds["K"].ltpc.ltp, n.feeds["K"].ltpc.cp, n.currentTs), (1.5, 4.0, 9))

    def test_sdk_crosscheck_is_optional(self):
        r = decoder.crosscheck_with_sdk(enc_frame({}, 1))
        self.assertTrue(r is None or isinstance(r, dict))


# ------------------------------------------------------------------------------------------------------------------ instruments
class InstrumentTests(unittest.TestCase):
    def test_from_rows_and_keys(self):
        ins = make_ins(5, 3)
        self.assertEqual(len(ins.equities), 5)
        self.assertEqual(list(ins.indices.values()), ["NIFTY 50", "BANK NIFTY", "NIFTY NEXT 50"])
        self.assertEqual(ins.kind_of(eq_key(0)), "equity")
        self.assertEqual(ins.kind_of(idx_key("nifty 50")), "index")
        self.assertIsNone(ins.kind_of("NSE_EQ|nope"))
        ks = ins.keys_for()
        self.assertEqual(ks[:3], list(ins.indices))
        self.assertEqual(len(ks), 8)
        self.assertEqual(ins.keys_for(["tst00001", "TST00003", "ZZZ"], with_indices=False), [eq_key(1), eq_key(3)])

    def test_duplicate_symbol_drops_both(self):
        rows = replay.synthetic_rows(2, 0) + [{"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|OTHER", "trading_symbol": "TST00000"}]
        ins = instruments.Instruments.from_rows(rows)
        self.assertNotIn(eq_key(0), ins.equities)
        self.assertNotIn("NSE_EQ|OTHER", ins.equities)
        self.assertEqual(len(ins.equities), 1)

    def test_ambiguous_or_missing_index_is_unresolved_not_guessed(self):
        rows = replay.synthetic_rows(1, 1) + [{"segment": "NSE_INDEX", "instrument_key": "NSE_INDEX|dup", "name": "NIFTY 50"}]
        ins = instruments.Instruments.from_rows(rows)
        self.assertNotIn("NIFTY 50", ins.indices.values())
        self.assertIn("NIFTY 50", ins.unresolved)
        self.assertIn("NIFTY IT", ins.unresolved)

    def test_non_eq_rows_and_junk_ignored(self):
        rows = [{"segment": "NSE_EQ", "instrument_type": "BE", "instrument_key": "k1", "trading_symbol": "AAA"}, "junk", {"segment": "NSE_EQ"}, None]
        self.assertEqual(instruments.Instruments.from_rows(rows).equities, {})

    def test_chunks(self):
        self.assertEqual([len(c) for c in instruments.chunks(list(range(1201)))], [500, 500, 201])
        self.assertEqual(instruments.chunks([]), [])

    def test_liquid_list(self):
        doc = {"stocks": [{"symbol": "A", "liquid": True}, {"symbol": "B", "liquid": False}, {"symbol": "C"}, {"symbol": "D", "liquid": True}, "x"]}
        self.assertEqual(instruments.liquid_symbols_from_dma(doc), {"A", "D"})
        self.assertEqual(instruments.liquid_symbols_from_dma({}), set())
        self.assertEqual(instruments.liquid_symbols_from_dma(None), set())
        self.assertEqual(instruments.load_liquid("/nonexistent/file.json"), set())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text(json.dumps(doc))
            self.assertEqual(instruments.load_liquid(str(p)), {"A", "D"})
            p.write_text("{not json")
            self.assertEqual(instruments.load_liquid(str(p)), set())


# ------------------------------------------------------------------------------------------------------------------ market hours
def at(h, m, s=0, day=(2026, 10, 8)):
    return dt.datetime(*day, h, m, s, tzinfo=IST)


class MarketHoursTests(unittest.TestCase):
    def test_clock_boundaries(self):
        H = set()
        cases = [((8, 59), "closed"), ((9, 0), "pre_open"), ((9, 14, 59), "pre_open"), ((9, 15), "open"), ((15, 29, 59), "open"), ((15, 30), "closed"), ((20, 0), "closed")]
        for t, want in cases:
            self.assertEqual(market_hours.status(at(*t), H), want, t)

    def test_weekend_and_holiday_closed_even_if_feed_says_open(self):
        sat = at(10, 0, day=(2026, 10, 10))
        self.assertEqual(market_hours.status(sat, set()), "closed")
        self.assertEqual(market_hours.status(sat, set(), "NORMAL_OPEN"), "closed")
        self.assertEqual(market_hours.status(at(10, 0), {DAY}, "NORMAL_OPEN"), "closed")

    def test_feed_status_wins_on_a_trading_day(self):
        self.assertEqual(market_hours.status(at(10, 0), set(), "NORMAL_CLOSE"), "closed")
        self.assertEqual(market_hours.status(at(18, 0), set(), "NORMAL_OPEN"), "open")
        self.assertEqual(market_hours.status(at(10, 0), set(), "PRE_OPEN_END"), "pre_open")
        self.assertEqual(market_hours.status(at(10, 0), set(), "SOMETHING_NEW"), "open")     # unknown status: the clock decides

    def test_naive_datetime_refused(self):
        with self.assertRaises(ValueError):
            market_hours.status(dt.datetime(2026, 10, 8, 10, 0), set())

    def test_other_timezone_is_converted(self):
        utc = dt.datetime(2026, 10, 8, 4, 30, tzinfo=dt.timezone.utc)                         # 10:00 IST
        self.assertEqual(market_hours.status(utc, set()), "open")

    def test_connection_window(self):
        self.assertFalse(market_hours.in_connection_window(at(8, 54, 59), set()))
        self.assertTrue(market_hours.in_connection_window(at(8, 55), set()))
        self.assertTrue(market_hours.in_connection_window(at(15, 44, 59), set()))
        self.assertFalse(market_hours.in_connection_window(at(15, 45), set()))
        self.assertFalse(market_hours.in_connection_window(at(10, 0), {DAY}))

    def test_holiday_parsing(self):
        self.assertEqual(market_hours.parse_holidays("2026-01-26\n2026-03-03 # x  junk 2026-13-45\n20260101"), {"2026-01-26", "2026-03-03"})
        self.assertEqual(market_hours.parse_holidays(None), set())

    def test_next_window_start_skips_weekend_and_holidays(self):
        n = market_hours.next_window_start(at(16, 0, day=(2026, 10, 9)), set())               # Friday after close -> Monday
        self.assertEqual(n.date().isoformat(), "2026-10-12")
        self.assertEqual((n.hour, n.minute), (8, 55))
        n = market_hours.next_window_start(at(16, 0, day=(2026, 10, 9)), {"2026-10-12"})
        self.assertEqual(n.date().isoformat(), "2026-10-13")
        n = market_hours.next_window_start(at(7, 0), set())
        self.assertEqual(n.date().isoformat(), DAY)
        self.assertEqual(market_hours.session_date(at(10, 0)), DAY)


# ------------------------------------------------------------------------------------------------------------------ tick store
class TickStoreTests(unittest.TestCase):
    def setUp(self):
        self.ins = make_ins(10)
        self.st = ticks.TickStore(self.ins, DAY)
        self.k = eq_key(0)

    def test_accepts_a_good_tick(self):
        r = tick(self.ins, self.st, self.k, 101.0, 100.0, T0)
        self.assertEqual(r["accepted"], 1)
        rec = self.st.get(self.k)
        self.assertEqual((rec["ltp"], rec["cp"], rec["kind"], rec["traded_today"]), (101.0, 100.0, "equity", True))

    def test_unknown_key(self):
        r = tick(self.ins, self.st, "NSE_EQ|ghost", 10.0, 9.0, T0)
        self.assertEqual(r["rejected"], {"unknown_key": 1})
        self.assertEqual(self.st.ticks, {})

    def test_bad_ltp_values(self):
        for bad in (0.0, -1.0, float("nan"), float("inf"), 2e7, None):
            r = tick(self.ins, self.st, self.k, bad, 100.0, T0)
            self.assertEqual(r["accepted"], 0, bad)
        self.assertEqual(self.st.ticks, {})
        self.assertGreaterEqual(self.st.rejected["bad_ltp"], 5)

    def test_future_ltt_rejected_small_skew_allowed(self):
        self.assertEqual(tick(self.ins, self.st, self.k, 100.0, 100.0, T0, ltt=T0 + 120_000)["rejected"], {"future_ltt": 1})
        self.assertEqual(tick(self.ins, self.st, self.k, 100.0, 100.0, T0, ltt=T0 + 30_000)["accepted"], 1)

    def test_out_of_order_rejected_equal_allowed(self):
        tick(self.ins, self.st, self.k, 100.0, 100.0, T0, ltt=T0 - 1000)
        self.assertEqual(tick(self.ins, self.st, self.k, 100.5, 100.0, T0 + 1, ltt=T0 - 2000)["rejected"], {"out_of_order": 1})
        self.assertEqual(tick(self.ins, self.st, self.k, 100.5, 100.0, T0 + 1, ltt=T0 - 1000)["accepted"], 1)
        self.assertEqual(self.st.get(self.k)["ltp"], 100.5)

    def test_single_tick_jump_is_held_and_not_shown(self):
        tick(self.ins, self.st, self.k, 100.0, 100.0, T0)
        r = tick(self.ins, self.st, self.k, 150.0, 100.0, T0 + 1000)
        self.assertEqual(r["rejected"], {"jump_pending": 1})
        self.assertEqual(self.st.get(self.k)["ltp"], 100.0)
        self.assertEqual(self.st.stats()["held_jumps"], 1)

    def test_jump_confirmed_by_agreeing_next_tick(self):
        tick(self.ins, self.st, self.k, 100.0, 100.0, T0)
        tick(self.ins, self.st, self.k, 150.0, 100.0, T0 + 1000)
        r = tick(self.ins, self.st, self.k, 151.0, 100.0, T0 + 2000)
        self.assertEqual(r["accepted"], 1)
        self.assertEqual(self.st.get(self.k)["ltp"], 151.0)
        self.assertEqual(self.st.stats()["held_jumps"], 0)

    def test_jump_not_confirmed_when_next_tick_disagrees_and_normal_tick_clears_hold(self):
        tick(self.ins, self.st, self.k, 100.0, 100.0, T0)
        tick(self.ins, self.st, self.k, 150.0, 100.0, T0 + 1000)
        self.assertEqual(tick(self.ins, self.st, self.k, 100.5, 100.0, T0 + 2000)["accepted"], 1)     # back to normal: the spike was an error
        self.assertEqual(self.st.stats()["held_jumps"], 0)
        self.assertEqual(self.st.get(self.k)["ltp"], 100.5)
        tick(self.ins, self.st, self.k, 150.0, 100.0, T0 + 3000)
        r = tick(self.ins, self.st, self.k, 200.0, 100.0, T0 + 4000)                                    # a second, different wild value: still held
        self.assertEqual(r["rejected"], {"jump_pending": 1})
        self.assertEqual(self.st.get(self.k)["ltp"], 100.5)

    def test_missing_cp_counted_and_earlier_cp_kept(self):
        r = tick(self.ins, self.st, self.k, 100.0, None, T0)
        self.assertEqual(r["accepted"], 1)
        self.assertEqual(r["rejected"], {"no_prev_close": 1})
        self.assertIsNone(self.st.get(self.k)["cp"])
        tick(self.ins, self.st, self.k, 101.0, 100.0, T0 + 1000)
        tick(self.ins, self.st, self.k, 102.0, None, T0 + 2000)
        self.assertEqual(self.st.get(self.k)["cp"], 100.0)
        for bad in (0.0, -5.0, float("nan")):
            tick(self.ins, self.st, self.k, 102.0, bad, T0 + 3000)
            self.assertEqual(self.st.get(self.k)["cp"], 100.0)

    def test_traded_today_false_for_yesterdays_tick(self):
        tick(self.ins, self.st, self.k, 100.0, 100.0, T0, ltt=T0 - 86_400_000)
        self.assertFalse(self.st.get(self.k)["traded_today"])

    def test_market_info_recorded(self):
        self.st.apply_frame(decoder.decode_frame(enc_frame({}, 2, 5, {"NSE_EQ": 2})), T0)
        self.assertEqual(self.st.segment_status, {"NSE_EQ": "NORMAL_OPEN"})

    def test_empty_and_odd_frames_do_not_crash(self):
        self.st.apply_frame({}, T0)
        self.st.apply_frame({"feeds": {"k": None}}, T0)
        self.st.apply_frame({"feeds": {eq_key(0): {"ltpc": None}}}, T0)
        self.assertEqual(self.st.ticks, {})
        self.assertEqual(self.st.frames, 3)

    def test_begin_connection_resets_coverage_but_keeps_prices(self):
        full_market(self.ins, self.st, T0)
        self.assertEqual(self.st.covered_equities(), 10)
        self.st.begin_connection(T0 + 5000)
        self.assertEqual(self.st.covered_equities(), 0)
        self.assertEqual(self.st.covered_indices(), 0)
        self.assertEqual(len(self.st.ticks), 13)
        tick(self.ins, self.st, eq_key(1), 100.0, 100.0, T0 + 6000)
        self.assertEqual(self.st.covered_equities(), 1)

    def test_index_and_equity_tick_times(self):
        tick(self.ins, self.st, self.k, 100.0, 100.0, T0)
        self.assertIsNone(self.st.last_index_tick_ms)
        tick(self.ins, self.st, idx_key("nifty 50"), 24000.0, 23900.0, T0 + 10)
        self.assertEqual((self.st.last_equity_tick_ms, self.st.last_index_tick_ms), (T0, T0 + 10))


# ------------------------------------------------------------------------------------------------------------------ snapshot
def build(store, ins, now, liquid=None, **kw):
    liquid = set(sym(i) for i in range(len(ins.equities))) if liquid is None else liquid
    kw.setdefault("seq", 1)
    kw.setdefault("market_status", "open")
    kw.setdefault("subscribed_equities", len(ins.equities))
    return snapshot.build_snapshot(store, ins, now, liquid=liquid, **kw)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.ins = make_ins(20)
        self.st = ticks.TickStore(self.ins, DAY)
        self.st.begin_connection(T0 - 1000)

    def healthy(self, now=T0):
        full_market(self.ins, self.st, now)
        return build(self.st, self.ins, now + 1000)

    def test_healthy_snapshot_is_valid_and_ok(self):
        d = self.healthy()
        self.assertEqual(snapshot.validate_snapshot(d), [])
        self.assertTrue(d["quality"]["ok"], d["quality"])
        self.assertEqual(d["breadth"], {"advances": 10, "declines": 10, "unchanged": 0, "counted": 20})
        self.assertEqual(d["scope"]["coverage"], 1.0)
        self.assertEqual([i["name"] for i in d["indices"]], ["NIFTY 50", "BANK NIFTY", "NIFTY NEXT 50"])
        self.assertEqual(d["indices"][0]["change_pct"], 1.0)
        self.assertEqual(d["generated_at"][-6:], "+05:30")

    def test_gainers_losers_order_ties_and_limit(self):
        moves = {i: (i - 10) * 0.5 for i in range(20)}            # -5.0 .. +4.5 ; index 10 is unchanged
        feeds = {eq_key(i): enc_feed(enc_ltpc(100 * (1 + m / 100), T0 - 100, 1, 100.0)) for i, m in moves.items()}
        for k in self.ins.indices:
            feeds[k] = enc_feed(enc_ltpc(20100, T0 - 100, 1, 20000))
        self.st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, T0)), T0)
        d = build(self.st, self.ins, T0 + 500, cfg={"movers": 3})
        self.assertEqual([r["symbol"] for r in d["gainers"]], [sym(19), sym(18), sym(17)])
        self.assertEqual([r["symbol"] for r in d["losers"]], [sym(0), sym(1), sym(2)])
        self.assertEqual(d["breadth"]["unchanged"], 1)
        self.assertEqual(snapshot.validate_snapshot(d), [])
        d2 = build(self.st, self.ins, T0 + 500)
        self.assertEqual((len(d2["gainers"]), len(d2["losers"])), (9, 10))            # only real gainers / losers, never padded with zeros

    def test_equal_moves_tie_break_by_symbol(self):
        feeds = {eq_key(i): enc_feed(enc_ltpc(102.0, T0 - 100, 1, 100.0)) for i in (5, 3, 9)}
        self.st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, T0)), T0)
        d = build(self.st, self.ins, T0 + 500)
        self.assertEqual([r["symbol"] for r in d["gainers"]], [sym(3), sym(5), sym(9)])

    def test_only_liquid_stocks_in_movers_but_all_in_breadth(self):
        full_market(self.ins, self.st, T0)
        d = build(self.st, self.ins, T0 + 500, liquid={sym(0), sym(2)})
        self.assertEqual({r["symbol"] for r in d["gainers"]}, {sym(0), sym(2)})
        self.assertEqual(d["breadth"]["counted"], 20)

    def test_no_liquid_list_means_null_movers_and_a_warning_not_an_unfiltered_list(self):
        full_market(self.ins, self.st, T0)
        d = build(self.st, self.ins, T0 + 500, liquid=set())
        self.assertIsNone(d["gainers"])
        self.assertIsNone(d["losers"])
        self.assertIn("liquid_list_unavailable", d["quality"]["warnings"])
        self.assertEqual(snapshot.validate_snapshot(d), [])
        ok = snapshot.client_accepts(d, T0 + 600)
        self.assertTrue(ok["indices"][0] and ok["breadth"][0])
        self.assertFalse(ok["movers"][0])

    def test_extreme_moves_excluded_from_movers_but_counted(self):
        feeds = {eq_key(0): enc_feed(enc_ltpc(160.0, T0 - 100, 1, 100.0))}
        self.st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, T0)), T0)       # +60%: held as a jump first
        self.st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, T0 + 10)), T0 + 10)
        self.st.apply_frame(decoder.decode_frame(enc_frame({eq_key(0): enc_feed(enc_ltpc(160.0, T0 + 50, 1, 100.0))}, 1, T0 + 20)), T0 + 20)
        self.assertEqual(self.st.get(eq_key(0))["ltp"], 160.0)
        d = build(self.st, self.ins, T0 + 500)
        self.assertEqual(d["gainers"], [])
        self.assertTrue(any(w.startswith("suspect_extreme_moves:") for w in d["quality"]["warnings"]))
        self.assertEqual(d["breadth"]["advances"], 1)

    def test_instrument_without_cp_is_left_out_of_breadth_and_movers_but_priced(self):
        feeds = {eq_key(0): enc_feed(enc_ltpc(100.0, T0 - 100, 1, None)), eq_key(1): enc_feed(enc_ltpc(105.0, T0 - 100, 1, 100.0))}
        self.st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, T0)), T0)
        d = build(self.st, self.ins, T0 + 500, price_symbols=[sym(0), sym(1)])
        self.assertEqual(d["breadth"]["counted"], 1)
        self.assertEqual(d["prices"][sym(0)]["change_pct"], None)
        self.assertEqual(d["prices"][sym(1)]["change_pct"], 5.0)
        self.assertEqual([r["symbol"] for r in d["gainers"]], [sym(1)])
        self.assertEqual(snapshot.validate_snapshot(d), [])

    def test_prices_only_for_requested_symbols(self):
        full_market(self.ins, self.st, T0)
        d = build(self.st, self.ins, T0 + 500, price_symbols=["tst00002", "NOPE"])
        self.assertEqual(list(d["prices"]), [sym(2)])

    def test_stale_ticks_from_before_this_connection_are_not_counted(self):
        full_market(self.ins, self.st, T0)
        self.st.begin_connection(T0 + 3000)
        d = build(self.st, self.ins, T0 + 3500)
        self.assertEqual((d["scope"]["ticked"], d["breadth"]["counted"], d["indices"]), (0, 0, []))
        self.assertIn("low_coverage", d["quality"]["reasons"])
        self.assertIn("no_indices", d["quality"]["reasons"])

    # ---- quality
    def test_quality_not_streaming(self):
        full_market(self.ins, self.st, T0)
        d = build(self.st, self.ins, T0 + 500, relay_state="backoff")
        self.assertIn("not_streaming", d["quality"]["reasons"])
        self.assertFalse(d["quality"]["ok"])

    def test_quality_feed_silent(self):
        full_market(self.ins, self.st, T0)
        d = build(self.st, self.ins, T0 + 10_001)
        self.assertIn("feed_silent", d["quality"]["reasons"])
        self.assertNotIn("feed_silent", build(self.st, self.ins, T0 + 9_999)["quality"]["reasons"])
        self.assertIn("feed_silent", build(ticks.TickStore(self.ins, DAY), self.ins, T0)["quality"]["reasons"])

    def test_quality_index_ticks_stale_only_matters_while_open(self):
        full_market(self.ins, self.st, T0)
        self.st.apply_frame(decoder.decode_frame(enc_frame({}, 1, T0 + 25_000)), T0 + 25_000)        # frames flow, but no index tick
        d = build(self.st, self.ins, T0 + 25_500)
        self.assertEqual(d["quality"]["reasons"], ["index_ticks_stale"])
        self.assertNotIn("index_ticks_stale", build(self.st, self.ins, T0 + 25_500, market_status="closed")["quality"]["reasons"])

    def test_quality_low_coverage_threshold(self):
        for n, ok in ((18, True), (17, False)):
            st = ticks.TickStore(self.ins, DAY)
            st.begin_connection(T0 - 1)
            feeds = {eq_key(i): enc_feed(enc_ltpc(101.0, T0 - 5, 1, 100.0)) for i in range(n)}
            feeds.update({k: enc_feed(enc_ltpc(20100, T0 - 5, 1, 20000)) for k in self.ins.indices})
            st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, T0)), T0)
            d = build(st, self.ins, T0 + 100)
            self.assertEqual("low_coverage" not in d["quality"]["reasons"], ok, n)

    def test_unresolved_indices_warning(self):
        ins = instruments.Instruments.from_rows(replay.synthetic_rows(3, 2))
        st = ticks.TickStore(ins, DAY)
        d = build(st, ins, T0)
        self.assertTrue(any(w.startswith("indices_unresolved:") for w in d["quality"]["warnings"]))

    # ---- encoding
    def test_encode_strict_json_deterministic_and_etag(self):
        d = self.healthy()
        raw, gz, etag = snapshot.encode_snapshot(d)
        self.assertEqual(json.loads(raw), d)
        self.assertEqual(gzip.decompress(gz), raw)
        self.assertEqual(snapshot.encode_snapshot(d), (raw, gz, etag))
        self.assertTrue(re.fullmatch(r'"1-[0-9a-f]{12}"', etag))
        d2 = dict(d, seq=2)
        self.assertNotEqual(snapshot.encode_snapshot(d2)[2], etag)
        bad = dict(d, scope=dict(d["scope"], coverage=float("nan")))
        with self.assertRaises(ValueError):
            snapshot.encode_snapshot(bad)

    def test_size_for_about_2300_stocks(self):
        ins = make_ins(2300, 7)
        st = ticks.TickStore(ins, DAY)
        st.begin_connection(T0 - 1)
        full_market(ins, st, T0)
        d = build(st, ins, T0 + 100, price_symbols=[sym(i) for i in range(10)])
        raw, gz, _ = snapshot.encode_snapshot(d)
        self.assertEqual(snapshot.validate_snapshot(d), [])
        self.assertLess(len(raw), 16_000)
        self.assertLess(len(gz), 6_000)
        self.assertEqual(d["breadth"]["counted"], 2300)

    # ---- validator
    def test_validator_rejects_damage(self):
        good = self.healthy()
        self.assertEqual(snapshot.validate_snapshot(good), [])
        mutations = {
            "extra key": lambda d: d.update(token="x"),
            "missing key": lambda d: d.pop("prices"),
            "bad kind": lambda d: d.update(kind="other"),
            "bad schema": lambda d: d.update(schema=2),
            "negative seq": lambda d: d.update(seq=-1),
            "float seq": lambda d: d.update(seq=1.5),
            "generated_at mismatch": lambda d: d.update(generated_at="2026-01-01T00:00:00.000+05:30"),
            "bad status": lambda d: d["market"].update(status="maybe"),
            "coverage >1": lambda d: d["scope"].update(coverage=1.5),
            "ticked > subscribed": lambda d: d["scope"].update(ticked=99),
            "bad index": lambda d: d["indices"][0].update(ltp=-1),
            "index pct mismatch": lambda d: d["indices"][0].update(change_pct=9.9),
            "breadth sum": lambda d: d["breadth"].update(counted=99),
            "gainer sign": lambda d: d["gainers"][0].update(change_pct=-1),
            "gainer pct mismatch": lambda d: d["gainers"][0].update(change_pct=3.14159),
            "gainer order": lambda d: d["gainers"].reverse(),
            "unsafe symbol": lambda d: d["gainers"][0].update(symbol="../x"),
            "one null": lambda d: d.update(gainers=None),
            "quality inconsistent": lambda d: d["quality"].update(ok=False),
            "bad source": lambda d: d.update(source="x"),
            "nan": lambda d: d["indices"][0].update(ltp=float("nan")),
            "not dict prices": lambda d: d.update(prices=[]),
        }
        for name, mut in mutations.items():
            d = json.loads(json.dumps(good))
            mut(d)
            self.assertTrue(snapshot.validate_snapshot(d), name)
        self.assertEqual(snapshot.validate_snapshot("x"), ["not an object"])

    # ---- the browser's accept rules
    def test_client_accepts_fresh_healthy_snapshot(self):
        d = self.healthy()
        r = snapshot.client_accepts(d, d["server_ts_ms"] + 3000)
        self.assertTrue(r["indices"][0] and r["breadth"][0] and r["movers"][0], r)
        self.assertEqual(r["prices"], (False, "no prices"))                  # none were requested

    def test_client_refuses_stale_future_closed_unhealthy_invalid(self):
        d = self.healthy()
        t = d["server_ts_ms"]
        all_refused = lambda r, frag: all((not v[0]) and frag in v[1] for v in r.values())
        self.assertTrue(all_refused(snapshot.client_accepts(d, t + 20_001), "old"))
        self.assertTrue(snapshot.client_accepts(d, t + 20_000)["indices"][0])
        self.assertTrue(all_refused(snapshot.client_accepts(d, t - 5_001), "future"))
        self.assertTrue(snapshot.client_accepts(d, t - 5_000)["indices"][0])
        closed = json.loads(json.dumps(d))
        closed["market"]["status"] = "closed"
        self.assertTrue(all_refused(snapshot.client_accepts(closed, t), "not open"))
        pre = json.loads(json.dumps(d))
        pre["market"]["status"] = "pre_open"
        self.assertTrue(all_refused(snapshot.client_accepts(pre, t), "not open"))
        sick = json.loads(json.dumps(d))
        sick["quality"].update(ok=False, reasons=["feed_silent"])
        self.assertTrue(all_refused(snapshot.client_accepts(sick, t), "feed_silent"))
        self.assertTrue(all_refused(snapshot.client_accepts({"x": 1}, t), "invalid"))
        self.assertTrue(all_refused(snapshot.client_accepts(None, t), "invalid"))

    def test_client_blocks_degrade_independently(self):
        # 19 of 20 covered (95%) but one index missing: indices refused, breadth/movers fine
        st = ticks.TickStore(self.ins, DAY)
        st.begin_connection(T0 - 1)
        feeds = {eq_key(i): enc_feed(enc_ltpc(101.0, T0 - 5, 1, 100.0)) for i in range(19)}
        ks = list(self.ins.indices)
        feeds.update({k: enc_feed(enc_ltpc(20100, T0 - 5, 1, 20000)) for k in ks[:2]})
        st.apply_frame(decoder.decode_frame(enc_frame(feeds, 1, T0)), T0)
        d = build(st, self.ins, T0 + 100)
        self.assertTrue(d["quality"]["ok"], d["quality"])
        r = snapshot.client_accepts(d, T0 + 200)
        self.assertFalse(r["indices"][0])
        self.assertTrue(r["breadth"][0] and r["movers"][0])
        self.assertFalse(r["prices"][0])                              # none requested

    def test_client_refuses_breadth_below_coverage_even_if_server_said_ok(self):
        d = json.loads(json.dumps(self.healthy()))
        d["scope"]["coverage"] = 0.5
        d["scope"]["ticked"] = 10
        r = snapshot.client_accepts(d, d["server_ts_ms"])
        self.assertFalse(r["breadth"][0])
        self.assertFalse(r["movers"][0])


# ------------------------------------------------------------------------------------------------------------------ supervisor
def drive(sup, events, start=0):
    t, acts = start, []
    for ev in events:
        name, kw = (ev, {}) if isinstance(ev, str) else ev
        t = kw.pop("at", t)
        acts.append((name, sup.handle(name, t, **kw)))
    return acts


class SupervisorTests(unittest.TestCase):
    def mk(self, r=0.0, cfg=None):
        return state.Supervisor(cfg, rng=lambda: r)

    def to_streaming(self, sup, t=0):
        sup.handle("window_open", t)
        sup.handle("authorized", t)
        sup.handle("connected", t)
        sup.handle("subscribed", t)
        self.assertEqual(sup.state, state.STREAMING)

    def test_happy_path_actions(self):
        s = self.mk()
        self.assertEqual(s.handle("window_open", 0), [("authorize",)])
        self.assertEqual(s.handle("authorized", 1), [("connect",)])
        self.assertEqual(s.handle("connected", 2), [("subscribe",)])
        self.assertEqual(s.handle("subscribed", 3), [])
        self.assertEqual((s.state, s.relay_state()), (state.STREAMING, "streaming"))

    def test_relay_state_is_streaming_only_while_streaming(self):
        s = self.mk()
        s.handle("window_open", 0)
        self.assertEqual(s.relay_state(), "authorizing")
        self.assertNotEqual(s.relay_state(), "streaming")

    def test_events_in_the_wrong_state_are_ignored(self):
        s = self.mk()
        for ev in ("authorized", "connected", "subscribed", "frame", "closed", "tick", "auth_failed"):
            self.assertEqual(s.handle(ev, 0), [])
        self.assertEqual(s.state, state.OFF_HOURS)
        s.handle("window_open", 0)
        self.assertEqual(s.handle("connected", 1), [])
        self.assertEqual(s.state, state.AUTHORIZING)
        self.assertEqual(s.handle("window_open", 2), [])

    def test_backoff_bounds_double_and_cap(self):
        for r, lo_factor in ((0.0, 0.5), (0.999999, 1.0)):
            s = self.mk(r)
            waits = []
            t = 0
            s.handle("window_open", t)
            for _ in range(10):
                before = t
                s.handle("auth_failed", t, http=500)
                waits.append((s.retry_at_ms - before) / 1000)
                t = s.retry_at_ms
                self.assertEqual(s.handle("tick", t), [("authorize",)])
            expected = [min(60, 2 ** n) for n in range(1, 11)]
            for w, e in zip(waits, expected):
                self.assertAlmostEqual(w, e * lo_factor, delta=0.01 + e * 0.001)
            self.assertLessEqual(max(waits), 60)

    def test_no_retry_before_the_time(self):
        s = self.mk(0.5)
        s.handle("window_open", 0)
        s.handle("auth_failed", 0, http=500)
        self.assertEqual(s.handle("tick", s.retry_at_ms - 1), [])
        self.assertEqual(s.state, state.BACKOFF)
        self.assertEqual(s.handle("tick", s.retry_at_ms), [("authorize",)])

    def test_every_reconnect_authorizes_again(self):
        s = self.mk()
        self.to_streaming(s)
        acts = s.handle("closed", 1000)
        self.assertIn(("disconnect",), acts)
        self.assertEqual(s.state, state.BACKOFF)
        self.assertEqual(s.handle("tick", s.retry_at_ms), [("authorize",)])
        self.assertEqual(s.handle("authorized", s.retry_at_ms), [("connect",)])

    def test_rate_limit_waits_at_least_30s(self):
        s = self.mk(0.0)
        s.handle("window_open", 0)
        s.handle("auth_failed", 0, http=429)
        self.assertGreaterEqual(s.retry_at_ms, 30_000)

    def test_two_consecutive_auth_rejections_stop_with_one_alert(self):
        s = self.mk()
        s.handle("window_open", 0)
        a1 = s.handle("auth_failed", 0, http=401)
        self.assertEqual(s.state, state.BACKOFF)
        self.assertFalse([a for a in a1 if a[0] == "alert"])
        s.handle("tick", s.retry_at_ms)
        a2 = s.handle("auth_failed", s.retry_at_ms, http=403)
        self.assertEqual(s.state, state.AUTH_STOPPED)
        alerts = [a for a in a2 if a[0] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][1], "auth_rejected")
        self.assertNotIn("token", alerts[0][2].lower().replace("the token", ""))        # the text never includes a secret, only the HTTP status
        for ev in ("tick", "window_open", "authorized", "connected", "closed"):
            self.assertEqual(s.handle(ev, 10 ** 9), [])
        self.assertEqual(s.state, state.AUTH_STOPPED)

    def test_auth_stopped_survives_window_close_and_only_reset_restarts(self):
        s = self.mk()
        s.handle("window_open", 0)
        s.handle("auth_failed", 0, http=401)
        s.handle("tick", s.retry_at_ms)
        s.handle("auth_failed", s.retry_at_ms, http=401)
        s.handle("window_close", 10 ** 8)
        self.assertEqual(s.state, state.AUTH_STOPPED)
        s.handle("reset", 10 ** 8)
        self.assertEqual(s.state, state.OFF_HOURS)
        self.assertEqual(s.handle("window_open", 10 ** 8), [("authorize",)])

    def test_handshake_401_counts_as_auth_rejection(self):
        s = self.mk()
        s.handle("window_open", 0)
        s.handle("authorized", 0)
        s.handle("connect_failed", 0, http=403)
        self.assertEqual(s.auth_failures, 1)
        s.handle("tick", s.retry_at_ms)
        s.handle("authorized", s.retry_at_ms)
        s.handle("connect_failed", s.retry_at_ms, http=401)
        self.assertEqual(s.state, state.AUTH_STOPPED)

    def test_other_http_errors_never_stop_the_relay(self):
        s = self.mk()
        s.handle("window_open", 0)
        t = 0
        for code in (500, 502, 503, 429, None, 500, 504, 500):
            s.handle("auth_failed", t, http=code)
            self.assertEqual(s.state, state.BACKOFF)
            t = s.retry_at_ms
            s.handle("tick", t)
        self.assertNotEqual(s.state, state.AUTH_STOPPED)

    def test_subscribe_failure_backs_off(self):
        s = self.mk()
        s.handle("window_open", 0)
        s.handle("authorized", 0)
        s.handle("connected", 0)
        acts = s.handle("subscribe_failed", 0)
        self.assertIn(("disconnect",), acts)
        self.assertEqual(s.state, state.BACKOFF)

    def test_watchdog(self):
        s = self.mk()
        self.to_streaming(s, 0)
        s.handle("frame", 5000)
        self.assertEqual(s.handle("tick", 19_999, expect_frames=True), [])
        self.assertEqual(s.handle("tick", 20_001, expect_frames=True), [("disconnect",)])
        self.assertEqual(s.state, state.BACKOFF)

    def test_watchdog_quiet_when_frames_not_expected(self):
        s = self.mk()
        self.to_streaming(s, 0)
        self.assertEqual(s.handle("tick", 10 ** 7, expect_frames=False), [])
        self.assertEqual(s.state, state.STREAMING)

    def test_watchdog_counts_from_subscription_when_no_frame_ever_arrives(self):
        s = self.mk()
        self.to_streaming(s, 1000)
        self.assertEqual(s.handle("tick", 17_000, expect_frames=True), [("disconnect",)])

    def test_stable_stream_clears_failure_counters(self):
        s = self.mk()
        s.handle("window_open", 0)
        s.handle("auth_failed", 0, http=500)
        s.handle("tick", s.retry_at_ms)
        t = s.retry_at_ms
        s.handle("authorized", t)
        s.handle("connected", t)
        s.handle("subscribed", t)
        self.assertEqual(s.failures, 1)
        s.handle("frame", t + 30_000)
        self.assertEqual(s.failures, 1)
        s.handle("frame", t + 60_000)
        self.assertEqual((s.failures, s.auth_failures), (0, 0))

    def test_short_lived_connections_do_not_reset_the_counter(self):
        s = self.mk()
        s.handle("window_open", 0)
        t = 0
        for _ in range(4):
            s.handle("authorized", t)
            s.handle("connected", t)
            s.handle("subscribed", t)
            s.handle("frame", t + 5000)
            s.handle("closed", t + 5000)
            t = s.retry_at_ms
            s.handle("tick", t)
        self.assertEqual(s.failures, 4)

    def test_reconnect_storm_alert_once_and_wait_at_least_60s(self):
        s = self.mk(0.0, {"max_reconnects_per_hour": 5})
        s.handle("window_open", 0)
        t, alerts = 0, []
        for _ in range(12):
            for a in s.handle("auth_failed", t, http=500) + (s.handle("authorized", t) if False else []):
                if a[0] == "alert":
                    alerts.append(a)
            wait = (s.retry_at_ms - t) / 1000
            t = s.retry_at_ms
            s.handle("tick", t)
        self.assertEqual([a[1] for a in alerts], ["reconnect_storm"])
        self.assertGreaterEqual(wait, 60)

    def test_window_close_disconnects_and_publishes_final_snapshot(self):
        s = self.mk()
        self.to_streaming(s)
        self.assertEqual(s.handle("window_close", 100), [("disconnect",), ("final_snapshot",)])
        self.assertEqual(s.state, state.OFF_HOURS)
        self.assertEqual(s.handle("window_close", 200), [])
        self.assertEqual(s.handle("tick", 10 ** 9), [])
        self.assertEqual(s.handle("closed", 300), [])
        self.assertEqual(s.handle("window_open", 400), [("authorize",)])

    def test_window_close_while_in_backoff_cancels_retry(self):
        s = self.mk()
        s.handle("window_open", 0)
        s.handle("auth_failed", 0, http=500)
        s.handle("window_close", 5)
        self.assertEqual(s.state, state.OFF_HOURS)
        self.assertIsNone(s.retry_at_ms)
        self.assertEqual(s.handle("tick", 10 ** 9), [])

    def test_a_whole_day_script(self):
        s = self.mk(0.3)
        log = drive(s, ["window_open", "authorized", "connected", "subscribed", ("frame", {"at": 1000}), ("closed", {"at": 2000}), ("tick", {"at": 100_000}),
                        ("authorized", {"at": 100_000}), "connected", "subscribed", ("frame", {"at": 101_000}), ("window_close", {"at": 200_000})])
        self.assertEqual(s.state, state.OFF_HOURS)
        self.assertEqual(s.total_connects, 2)
        self.assertEqual(log[-1][1], [("disconnect",), ("final_snapshot",)])


# ------------------------------------------------------------------------------------------------------------------ replay harness
def session_items(ins, seed=3, minutes=2, step_ms=1000):
    mk = replay.SyntheticMarket(ins, seed)
    items = [(T0, mk.initial_frame(T0))]
    for i in range(1, minutes * 60 + 1):
        t = T0 + i * step_ms
        items.append((t, mk.live_frame(t, 80)))
    return items


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.ins = make_ins(300, 3)
        self.liquid = {sym(i) for i in range(0, 300, 2)}

    def test_synthetic_market_is_deterministic_and_seed_sensitive(self):
        a = session_items(self.ins, 3, 1)
        self.assertEqual(a, session_items(self.ins, 3, 1))
        self.assertNotEqual(a, session_items(self.ins, 4, 1))

    def test_full_session_replay_gives_valid_healthy_snapshots(self):
        r = replay.run(session_items(self.ins), self.ins, liquid=self.liquid, session_date=DAY)
        self.assertEqual(r["decode_errors"], 0)
        snaps = r["snapshots"]
        self.assertGreater(len(snaps), 20)
        for d in snaps:
            self.assertEqual(snapshot.validate_snapshot(d), [])
            self.assertTrue(d["quality"]["ok"], (d["seq"], d["quality"]))
            self.assertEqual(snapshot.client_accepts(d, d["server_ts_ms"] + 1000)["breadth"][0], True)
        self.assertEqual([d["seq"] for d in snaps], list(range(1, len(snaps) + 1)))
        self.assertEqual(snaps[-1]["scope"]["coverage"], 1.0)
        self.assertEqual({r_["symbol"] for r_ in snaps[-1]["gainers"]} - self.liquid, set())

    def test_record_and_replay_round_trip_gives_identical_snapshots(self):
        items = session_items(self.ins)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "rec.jsonl")
            replay.record(p, items)
            back = replay.load_recording(p)
        self.assertEqual(back, items)
        a = replay.run(items, self.ins, liquid=self.liquid, session_date=DAY)["snapshots"]
        b = replay.run(back, self.ins, liquid=self.liquid, session_date=DAY)["snapshots"]
        self.assertEqual([snapshot.encode_snapshot(x)[2] for x in a], [snapshot.encode_snapshot(x)[2] for x in b])

    def test_stale_feed_goes_unhealthy_and_the_browser_refuses_it(self):
        items = session_items(self.ins, minutes=1)
        last = items[-1][0]
        r = replay.run(items, self.ins, liquid=self.liquid, session_date=DAY, end_ms=last + 30_000)
        final = r["snapshots"][-1]
        self.assertIn("feed_silent", final["quality"]["reasons"])
        self.assertIn("index_ticks_stale", final["quality"]["reasons"])
        self.assertTrue(all(not v[0] for v in snapshot.client_accepts(final, final["server_ts_ms"] + 100).values()))
        health = [d["quality"]["ok"] for d in r["snapshots"]]
        self.assertEqual(health[-1], False)
        first_bad = next(i for i, ok in enumerate(health) if not ok)
        self.assertTrue(all(not ok for ok in health[first_bad:]))                      # once silent it stays unhealthy until ticks return

    def test_feed_resumes_after_a_gap(self):
        mk = replay.SyntheticMarket(self.ins, 5)
        items = [(T0, mk.initial_frame(T0))]
        for i in range(1, 11):
            items.append((T0 + i * 1000, mk.live_frame(T0 + i * 1000, 300)))
        for i in range(60, 80):
            items.append((T0 + i * 1000, mk.live_frame(T0 + i * 1000, 300)))
        snaps = replay.run(items, self.ins, liquid=self.liquid, session_date=DAY)["snapshots"]
        oks = [d["quality"]["ok"] for d in snaps]
        self.assertFalse(all(oks))
        self.assertTrue(oks[-1])

    def test_garbage_frames_are_counted_and_skipped(self):
        items = session_items(self.ins, minutes=1)
        items.insert(5, (items[5][0], b"\xff\xff\xff"))
        items.insert(9, (items[9][0], b"\x08"))
        r = replay.run(items, self.ins, liquid=self.liquid, session_date=DAY)
        self.assertEqual(r["decode_errors"], 2)
        self.assertTrue(r["snapshots"][-1]["quality"]["ok"])

    def test_bad_ticks_in_a_stream_never_reach_the_snapshot(self):
        items = session_items(self.ins, minutes=1)
        t = items[-1][0] + 500
        k = eq_key(1)
        wild = enc_frame({k: enc_feed(enc_ltpc(1e9, t - 10, 1, 100.0)), eq_key(2): enc_feed(enc_ltpc(float("nan"), t - 10, 1, 100.0)),
                          eq_key(3): enc_feed(enc_ltpc(50.0, t + 10 ** 7, 1, 100.0)), "NSE_EQ|ghost": enc_feed(enc_ltpc(5.0, t, 1, 5.0))}, 1, t)
        before = {sym(i): replay.run(items, self.ins, liquid=self.liquid, session_date=DAY)["store"].get(eq_key(i))["ltp"] for i in (1, 2, 3)}
        r = replay.run(items + [(t, wild)], self.ins, liquid=self.liquid, session_date=DAY)
        for i in (1, 2, 3):
            self.assertEqual(r["store"].get(eq_key(i))["ltp"], before[sym(i)])
        rej = r["store"].stats()["rejected"]
        self.assertTrue({"bad_ltp", "future_ltt", "unknown_key"} <= set(rej), rej)
        self.assertEqual(snapshot.validate_snapshot(r["snapshots"][-1]), [])

    def test_reconnect_in_the_middle_counts_coverage_from_the_new_connection(self):
        mk = replay.SyntheticMarket(self.ins, 9)
        store = ticks.TickStore(self.ins, DAY)
        store.begin_connection(T0)
        store.apply_frame(decoder.decode_frame(mk.initial_frame(T0)), T0)
        d1 = snapshot.build_snapshot(store, self.ins, T0 + 500, seq=1, market_status="open", liquid=self.liquid, subscribed_equities=300)
        self.assertEqual(d1["scope"]["ticked"], 300)
        store.begin_connection(T0 + 5000)
        d2 = snapshot.build_snapshot(store, self.ins, T0 + 5100, seq=2, market_status="open", liquid=self.liquid, subscribed_equities=300, relay_state="subscribing")
        self.assertEqual(d2["scope"]["ticked"], 0)
        self.assertFalse(d2["quality"]["ok"])
        store.apply_frame(decoder.decode_frame(mk.initial_frame(T0 + 5200)), T0 + 5200)
        d3 = snapshot.build_snapshot(store, self.ins, T0 + 5300, seq=3, market_status="open", liquid=self.liquid, subscribed_equities=300)
        self.assertTrue(d3["quality"]["ok"], d3["quality"])

    def test_supervisor_and_replay_together_follow_a_drop(self):
        """The supervisor says 'not streaming' while it reconnects; the snapshot built in that time is refused by the browser."""
        sup = state.Supervisor(rng=lambda: 0.0)
        sup.handle("window_open", T0)
        sup.handle("authorized", T0)
        sup.handle("connected", T0)
        sup.handle("subscribed", T0)
        mk = replay.SyntheticMarket(self.ins, 2)
        store = ticks.TickStore(self.ins, DAY)
        store.begin_connection(T0)
        store.apply_frame(decoder.decode_frame(mk.initial_frame(T0)), T0)
        ok = snapshot.build_snapshot(store, self.ins, T0 + 1000, seq=1, market_status="open", liquid=self.liquid, subscribed_equities=300, relay_state=sup.relay_state())
        self.assertTrue(snapshot.client_accepts(ok, T0 + 1500)["indices"][0])
        sup.handle("closed", T0 + 2000)
        down = snapshot.build_snapshot(store, self.ins, T0 + 3000, seq=2, market_status="open", liquid=self.liquid, subscribed_equities=300, relay_state=sup.relay_state())
        self.assertIn("not_streaming", down["quality"]["reasons"])
        self.assertFalse(any(v[0] for v in snapshot.client_accepts(down, T0 + 3100).values()))

    def test_throughput_sanity(self):
        ins = make_ins(2300, 7)
        mk = replay.SyntheticMarket(ins, 1)
        items = [(T0, mk.initial_frame(T0))] + [(T0 + i * 100, mk.live_frame(T0 + i * 100, 500)) for i in range(1, 101)]     # 50k ticks
        t0 = time.perf_counter()
        r = replay.run(items, ins, liquid={sym(i) for i in range(2300)}, session_date=DAY, snapshot_every_ms=5000)
        took = time.perf_counter() - t0
        self.assertGreaterEqual(r["store"].stats()["accepted"], 2300 + 40_000)
        self.assertLess(took, 20.0, "50k ticks and 3 snapshots took %.1fs" % took)
        t1 = time.perf_counter()
        snapshot.encode_snapshot(snapshot.build_snapshot(r["store"], ins, T0 + 10_000, seq=9, market_status="open", liquid={sym(i) for i in range(2300)}, subscribed_equities=2300))
        self.assertLess(time.perf_counter() - t1, 1.0)


# ------------------------------------------------------------------------------------------------------------------ guards on the package itself
LIVE = Path(__file__).parent / "live"
FORBIDDEN_IMPORTS = {"requests", "websockets", "websocket", "socket", "ssl", "http", "urllib", "aiohttp", "asyncio", "subprocess", "upstox_client", "httpx", "ftplib", "smtplib"}


class PackageGuardTests(unittest.TestCase):
    def sources(self):
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(LIVE.glob("*.py"))}

    def test_no_network_or_process_modules_imported(self):
        for name, src in self.sources().items():
            for node in ast.walk(ast.parse(src)):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    mods = [node.module or ""]
                for m in mods:
                    if name == "decoder.py" and m.startswith("upstox_client"):
                        continue                                                      # the one optional, guarded import: decoder.crosscheck_with_sdk (offline decode only)
                    self.assertNotIn(m.split(".")[0], FORBIDDEN_IMPORTS, "%s imports %s" % (name, m))

    def test_never_reads_the_environment_or_mentions_a_secret(self):
        for name, src in self.sources().items():
            self.assertNotRegex(src, r"os\.environ|getenv|UPSTOX_|ANALYTICS_TOKEN|access_token|Bearer|authorized_redirect_uri|wss://", name)

    def test_no_files_written_outside_the_replay_recorder(self):
        for name, src in self.sources().items():
            if name != "replay.py":
                self.assertNotRegex(src, r"open\([^)]*[\"'][wa]b?[\"']", name)

    def test_imports_no_eod_updater_or_site_code(self):
        for name, src in self.sources().items():
            self.assertNotRegex(src, r"import\s+(nse_updater|fundamentals_updater|financials_updater|financial_history_updater|shareholding_updater|historical_updater|universe|shards)\b", name)

    def test_no_hard_coded_stock_symbols_and_no_advice_words(self):
        symbols = re.compile(r"\b(RELIANCE|TCS|INFY|HDFCBANK|ICICIBANK|SBIN|ITC|LT|BHARTIARTL|WIPRO)\b")
        for name, src in self.sources().items():
            self.assertIsNone(symbols.search(src), name)
            self.assertNotRegex(src.lower(), r"\b(buy|sell|target price|rating|recommend)\b", name)

    def test_the_other_files_were_not_touched_by_this_phase(self):
        # the package must be self-contained: nothing outside live/ and this test file is part of Phase 1
        self.assertTrue((LIVE / "__init__.py").exists())
        import shutil
        import subprocess
        if shutil.which("git") is None:
            self.skipTest("git is not installed here: this check reads `git status`")
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(LIVE.parent), capture_output=True, text=True).stdout.splitlines()
        protected = ("index.html", "update.yml", "nse_updater.py", "fundamentals_updater.py", "financials_updater.py", "financial_history_updater.py", "shareholding_updater.py", "historical_updater.py")
        for line in out:
            path = line[3:].strip()
            self.assertFalse(path.endswith(protected) or path.startswith(".github/"), "unexpected change: " + line)


if __name__ == "__main__":
    unittest.main()
